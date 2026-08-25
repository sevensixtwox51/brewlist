#!/usr/bin/env python3
"""
Flask web app for comparing a Moxfield or Archidekt decklist against your
ManaBox collection -- paste the deck URL, upload your ManaBox export once,
and it remembers both your collection and any "reserved for another deck"
overrides per project (deck) between visits. No terminal required.

Run it with:
    python3 app.py

It finds a free port automatically and opens your browser to it -- same
behavior on macOS, Windows, and Linux (see _find_free_port/_open_browser_when_ready
below). Set PORT to force a specific one instead.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from datetime import datetime, timezone

from flask import Flask, Response, abort, jsonify, request

from brewlist_core import (
    BUCKET_ORDER,
    PICKABLE_STORE_LABELS,
    STORE_DISPLAY_NAMES,
    CardEntry,
    build_comparison,
    deck_is_commander_format,
    deck_key,
    ensure_price_index,
    extract_entries,
    fetch_deck,
    gameplay_data_in_index,
    load_collection,
    load_store_prefs,
    normalize_name,
    parse_deck_ref,
    parse_pasted_decklist,
    price_index_built_at,
    render_html,
    save_store_prefs,
    sets_data_in_index,
    update_from_git,
)
from deck_builder import (
    brew_to_card_entries,
    list_theme_options,
    optimize_builder_combos,
    owned_collection_gameplay_view,
    owned_set_options,
    suggest_builder_cards,
    suggest_replacements,
)
from ai_builder import clear_api_key, key_source, load_api_key, run_ai_build, save_api_key, validate_api_key

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, "data")
PROJECTS_DIR = os.path.join(DATA_DIR, "projects")
COLLECTION_PATH = os.path.join(DATA_DIR, "collection.csv")
COLLECTION_META_PATH = os.path.join(DATA_DIR, "collection_meta.json")

os.makedirs(PROJECTS_DIR, exist_ok=True)

app = Flask(__name__)


# --------------------------------------------------------------------------
# Project persistence -- one JSON file per deck, remembering the
# reserved-card overrides and last-used options so you never have to redo
# either after the first visit.
# --------------------------------------------------------------------------

def _project_path(deck_id: str) -> str:
    safe = "".join(c for c in deck_id if c.isalnum() or c in "_-")
    return os.path.join(PROJECTS_DIR, f"{safe}.json")


def load_project(deck_id: str) -> dict:
    path = _project_path(deck_id)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_project(deck_id: str, **fields) -> None:
    project = load_project(deck_id)
    project.update(fields)
    project["deck_id"] = deck_id
    project["updated"] = datetime.now(timezone.utc).isoformat()
    with open(_project_path(deck_id), "w", encoding="utf-8") as f:
        json.dump(project, f, indent=2)


def list_projects() -> list[dict]:
    projects = []
    for name in os.listdir(PROJECTS_DIR):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(PROJECTS_DIR, name), encoding="utf-8") as f:
                projects.append(json.load(f))
        except (OSError, ValueError):
            continue
    projects.sort(key=lambda p: p.get("updated", ""), reverse=True)
    return projects


def delete_project(deck_id: str) -> bool:
    """Removes a saved deck's JSON file (the Recent decks list entry and,
    for a brew, the deck itself). Returns whether a file was actually
    there to remove -- deleting an already-gone/unknown id is not an
    error, just a no-op, since the UI's own list is the only thing that
    could ever hand back a stale id."""
    path = _project_path(deck_id)
    if not os.path.isfile(path):
        return False
    os.remove(path)
    return True


def collection_meta() -> dict | None:
    if not os.path.isfile(COLLECTION_PATH):
        return None
    if os.path.isfile(COLLECTION_META_PATH):
        try:
            with open(COLLECTION_META_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    return {"filename": "collection.csv", "uploaded": None}


# --------------------------------------------------------------------------
# Comparison jobs -- pricing owned cards via Scryfall is a series of
# rate-limited network calls that can take several seconds for a full deck,
# so it runs in a background thread while the page polls for live progress
# instead of blocking on one long request with no feedback.
# --------------------------------------------------------------------------

_JOBS_LOCK = threading.Lock()
JOBS: dict[str, dict] = {}

# Populated once by a background check kicked off at server startup (see
# __main__) -- the home page reads this to show a banner if an update was
# actually pulled. Left at "checked": False if the check hasn't finished
# yet (or the process wasn't started via `python3 app.py`, e.g. tests
# importing this module directly).
_STARTUP_UPDATE_LOCK = threading.Lock()
STARTUP_UPDATE: dict = {"checked": False, "ok": None, "updated": None, "message": None}


def _check_for_updates_on_launch() -> None:
    result = update_from_git(APP_DIR)
    with _STARTUP_UPDATE_LOCK:
        STARTUP_UPDATE.update(checked=True, **result)


def _run_compare_job(job_id, entries, owned, break_out_basics, reserved,
                      deck_id, deck_name, deck_url, options, is_commander_format=False, stores=None):
    def _on_progress(done, total, stage=None):
        with _JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                job["done"] = done
                job["total"] = total
                job["stage"] = stage

    try:
        bucket_names, buckets, totals = build_comparison(
            entries, owned, ignore_basics=not break_out_basics, overrides=reserved,
            on_progress=_on_progress, is_commander_format=is_commander_format, stores=stores,
        )
        save_project(deck_id, deck_name=deck_name, deck_url=deck_url, options=options, reserved=reserved)
        html_report = render_html(
            deck_name, deck_url, deck_id, bucket_names, buckets, totals,
            overrides_endpoint=f"/api/overrides/{deck_id}",
            is_commander_format=is_commander_format,
        )
        with _JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                job["status"] = "done"
                job["html"] = html_report
    except Exception as e:  # noqa: BLE001 -- surface any failure to the polling client
        with _JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                job["status"] = "error"
                job["error"] = str(e)


def _run_brew_report_job(job_id, entries, owned, deck_id, deck_name, is_commander_format, ai_summary="", further_optimizations=None, maybeboard=None):
    """Same job shape as _run_compare_job, for the deck builder's "View
    full report" button -- reuses the exact same /compare/progress and
    /compare/result polling routes, so a brew's report is a saved decklist
    with 100% ownership (every card in `entries` is, by construction, one
    the builder only let you add because you own it)."""
    def _on_progress(done, total, stage=None):
        with _JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                job["done"] = done
                job["total"] = total
                job["stage"] = stage

    try:
        bucket_names, buckets, totals = build_comparison(
            entries, owned, ignore_basics=False, on_progress=_on_progress,
            is_commander_format=is_commander_format,
        )
        html_report = render_html(
            deck_name, "", deck_id, bucket_names, buckets, totals,
            overrides_endpoint=f"/api/overrides/{deck_id}",
            is_commander_format=is_commander_format, ai_summary=ai_summary,
            further_optimizations=further_optimizations, maybeboard=maybeboard,
        )
        with _JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                job["status"] = "done"
                job["html"] = html_report
    except Exception as e:  # noqa: BLE001 -- surface any failure to the polling client
        with _JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                job["status"] = "error"
                job["error"] = str(e)


def _run_ai_build_job(job_id, commander, deck_format, target_format, target_size,
                       commander_color_identity, intended_bracket, user_notes, owned_view, api_key,
                       wip_entries=None, scope="owned"):
    """Same JOBS/threading shape as _run_compare_job -- runs
    ai_builder.run_ai_build() (a multi-round-trip Claude tool-use loop, so
    genuinely slow) in a background thread. Unlike the compare/report jobs
    this also tracks a running `log` (list of one-line tool-call
    summaries) and `deck_state` (current WIP cards) so the client can
    render live progress, not just a done/total counter -- see
    /builder/ai-build/progress, a new route rather than widening
    /compare/progress's existing response shape.

    `wip_entries`/`scope` are just threaded through to run_ai_build() --
    see its own docstring for what "improve the current deck" (wip_entries
    seeded from the WIP deck) and "import & improve" (scope="any", full
    card database) actually change."""
    def _on_progress(done, total, stage, log, deck_state):
        with _JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                job["done"] = done
                job["total"] = total
                job["stage"] = stage
                job["log"] = log
                job["deck_state"] = deck_state

    try:
        result = run_ai_build(
            commander, deck_format, target_format, target_size, commander_color_identity,
            intended_bracket, user_notes, owned_view, api_key, _on_progress,
            wip_entries=wip_entries, scope=scope,
        )
        with _JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                if result.get("error"):
                    job["status"] = "error"
                    job["error"] = result["error"]
                else:
                    job["status"] = "done"
                    job["suggestions"] = result["suggestions"]
                    job["removed"] = result["removed"]
                    job["final_entries"] = result["final_entries"]
                    job["maybeboard"] = result.get("maybeboard") or []
                    job["finished"] = result["finished"]
                    job["summary"] = result["summary"]
    except Exception as e:  # noqa: BLE001 -- surface any failure to the polling client
        with _JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                job["status"] = "error"
                job["error"] = str(e)


# --------------------------------------------------------------------------
# Home page
# --------------------------------------------------------------------------

PAGE_STYLE = """
:root {
  --bg: #0f1117; --bg-elevated: #171a23; --card-bg: #1c1f2a; --card-border: #2a2f3d;
  --text: #e8e9ee; --text-dim: #8b90a3; --accent: #7dd3fc; --owned: #4ade80;
  --missing: #fb7185; --gold: #facc15; --shadow: 0 4px 16px rgba(0,0,0,0.35);
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #f4f5f8; --bg-elevated: #ffffff; --card-bg: #ffffff; --card-border: #e2e4ea;
    --text: #1a1c23; --text-dim: #63677a; --accent: #0284c7; --owned: #16a34a;
    --missing: #e11d48; --gold: #ca8a04; --shadow: 0 2px 10px rgba(20,20,30,0.08);
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  line-height: 1.5;
}
main { max-width: 640px; margin: 0 auto; padding: 0 24px 80px; }
h1 { font-size: 1.5rem; margin-bottom: 4px; }
p.subtitle { color: var(--text-dim); margin-top: 0; }
.card {
  background: var(--bg-elevated); border: 1px solid var(--card-border);
  border-radius: 12px; padding: 20px; margin-bottom: 20px; box-shadow: var(--shadow);
}
label { display: block; font-weight: 600; font-size: 0.9rem; margin-bottom: 6px; }
input[type="text"], input[type="url"], input[type="file"] {
  width: 100%; padding: 10px 12px; border-radius: 8px; border: 1px solid var(--card-border);
  background: var(--bg); color: var(--text); font-size: 0.95rem; margin-bottom: 16px;
}
.checkbox-row { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; font-size: 0.9rem; color: var(--text-dim); }
.checkbox-row input { width: auto; margin: 0; }
.store-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 16px; }
.store-grid .checkbox-row { margin-bottom: 4px; }
.hint-inline { color: var(--text-dim); font-size: 0.75rem; }
.hint { color: var(--text-dim); font-size: 0.8rem; margin-top: -10px; margin-bottom: 16px; }
.btn {
  cursor: pointer; border: none; border-radius: 8px; padding: 10px 18px;
  font-size: 0.95rem; font-weight: 600; font-family: inherit;
  background: var(--gold); color: #241f00;
}
.btn:hover { filter: brightness(1.08); }
.btn:disabled { opacity: 0.6; cursor: default; }
.btn.small { padding: 7px 14px; font-size: 0.85rem; }
.btn.danger {
  background: transparent; color: color-mix(in srgb, var(--missing) 55%, var(--text-dim));
  border: 1px solid color-mix(in srgb, var(--missing) 45%, var(--card-border)); font-weight: 500;
}
.btn.danger:hover { color: var(--missing); border-color: var(--missing); filter: none; }
.btn.ghost {
  background: transparent; color: var(--text);
  border: 1px solid var(--card-border); font-weight: 500;
}
.btn.ghost:hover { color: var(--accent); border-color: var(--accent); filter: none; }
.page-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
.sticky-top {
  position: sticky; top: 0; z-index: 50; background: var(--bg);
  margin: 0 -24px; padding: 40px 24px 4px;
}
.header-actions { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.kofi-link { display: flex; align-items: center; border-radius: 8px; overflow: hidden; }
.kofi-link img { display: block; height: 30px; width: auto; }
.kofi-link:hover { opacity: 0.85; }
.collection-status { font-size: 0.85rem; color: var(--text-dim); margin-bottom: 12px; }
.collection-status b { color: var(--owned); }
.project-list { list-style: none; padding: 0; margin: 0; }
.project-list li {
  padding: 10px 0; border-bottom: 1px solid var(--card-border);
  display: flex; justify-content: space-between; align-items: center; gap: 10px;
}
.project-list li:last-child { border-bottom: none; }
.project-list a { color: var(--text); text-decoration: none; font-weight: 600; }
.project-list a:hover { color: var(--accent); }
.project-list .deck-meta { display: flex; align-items: center; gap: 10px; }
.project-list .when { color: var(--text-dim); font-size: 0.8rem; white-space: nowrap; text-align: right; }
.error { color: var(--missing); background: color-mix(in srgb, var(--missing) 12%, var(--bg-elevated));
  border: 1px solid var(--missing); border-radius: 8px; padding: 12px 16px; margin-bottom: 20px; }
.update-banner { color: var(--owned); background: color-mix(in srgb, var(--owned) 12%, var(--bg-elevated));
  border: 1px solid var(--owned); border-radius: 8px; padding: 12px 16px; margin-bottom: 20px; }
#progress-wrap { display: none; margin-top: 14px; }
.progress-track { height: 8px; background: var(--card-border); border-radius: 4px; overflow: hidden; }
.progress-fill { height: 100%; width: 0%; background: linear-gradient(90deg, var(--owned), var(--accent)); transition: width 0.2s ease; }
#progress-label { color: var(--text-dim); font-size: 0.85rem; margin-top: 8px; }
.ai-disclosure { display: flex; justify-content: center; align-items: center; gap: 14px; margin-top: 32px; }
.ai-disclosure img { height: 24px; width: auto; display: block; }
"""


def _esc(s) -> str:
    import html as _html
    return _html.escape(str(s or ""))


# --------------------------------------------------------------------------
# Two-step home flow: Setup (/, collection upload + card database + app
# updates) then Decks (/decks, Recent decks + Compare-a-decklist). Split
# this way (rather than one page with everything) so uploading a fresh
# collection is its own immediate action with its own submit button --
# it used to live inside the Compare form, sharing its submit, so
# selecting a new file and then clicking "+ New deck" (a plain link, not
# that form's submit) silently abandoned the upload: the file was chosen
# but never actually sent to the server, and the Deck Builder went on
# using whatever collection was already on file. Header (title/ko-fi/
# shutdown/error-box/update-banner) and the AI-disclosure footer are
# identical chrome on both pages -- see _page_chrome -- so ko-fi/shutdown/
# update-checking stay reachable no matter which page you're on, even
# though collection/price-index setup itself only lives on Setup now.
# --------------------------------------------------------------------------

def _update_banner_html() -> str:
    with _STARTUP_UPDATE_LOCK:
        startup_update = dict(STARTUP_UPDATE)
    if startup_update.get("checked") and startup_update.get("ok") and startup_update.get("updated"):
        return (
            '<div class="update-banner">&#9889; Brewlist was just updated to the latest version -- '
            'restart the app to use it.</div>'
        )
    return ""


def _page_chrome(subtitle: str, error: str | None) -> tuple[str, str]:
    """(header_html, footer_html) shared by the Setup and Decks pages."""
    header = f"""
  <div class="sticky-top">
    <div class="page-header">
      <div>
        <h1>Brewlist</h1>
        <p class="subtitle">{subtitle}</p>
      </div>
      <div class="header-actions">
        <a class="kofi-link" href="https://ko-fi.com/imtotallymeh" target="_blank" rel="noopener noreferrer" title="Support Brewlist on Ko-fi">
          <img src="https://storage.ko-fi.com/cdn/kofi5.png?v=3" alt="Support me on Ko-fi" loading="lazy">
        </a>
        <button type="button" class="btn small danger" id="shutdown-btn" title="Stops the local server">&#9209; Shut Down</button>
      </div>
    </div>
    {_update_banner_html()}
    <div id="error-box" class="error" style="display:{"block" if error else "none"};">{_esc(error) if error else ""}</div>
  </div>"""
    footer = """
  <div class="ai-disclosure">
    <a href="https://github.com/sevensixtwox51/brewlist/blob/main/AI-DECLARATION.md" target="_blank" rel="noopener noreferrer" title="Brewlist is built almost entirely by AI (Claude Code) -- see AI-DECLARATION.md">
      <img src="https://img.shields.io/badge/%E4%B7%BC%20AI--DECLARATION-auto-ede9fe?labelColor=ede9fe" alt="AI-DECLARATION: auto">
    </a>
    <a href="https://www.realgoodai.org/real-rating" target="_blank" rel="noopener noreferrer" title="REAL Rating: Level 5, Full AI">
      <img src="https://images.squarespace-cdn.com/content/v1/677c1269fe60517a0976d6fc/fbf2122d-5b3f-474d-8466-bc18298c15e2/5+REAL+rating%404x.png" alt="REAL Rating: Level 5, Full AI">
    </a>
  </div>"""
    return header, footer


SHUTDOWN_BTN_SCRIPT = """
document.getElementById('shutdown-btn').addEventListener('click', () => {
  if (!confirm('Shut down the server? You will need to relaunch it to use this again.')) return;
  fetch('/shutdown', { method: 'POST' }).catch(() => {});
  document.body.innerHTML =
    '<main><p style="color:var(--text-dim);padding-top:40px;">Server stopped. You can close this tab.</p></main>';
});
"""


def render_setup_page(error: str | None = None) -> str:
    meta = collection_meta()
    if meta and meta.get("uploaded"):
        collection_html = (
            f'<div class="collection-status">Using collection <b>{_esc(meta["filename"])}</b> '
            f'(uploaded {_esc(meta["uploaded"])}) &mdash; upload a new export below to replace it.</div>'
        )
    elif meta:
        collection_html = '<div class="collection-status">Using a previously uploaded collection.</div>'
    else:
        collection_html = '<div class="collection-status">No collection uploaded yet &mdash; required the first time.</div>'

    index_built_at = price_index_built_at()
    if index_built_at is None:
        index_status = 'Not built yet — first use downloads it (~325MB from MTGJSON, one-time, refreshed weekly after).'
    else:
        index_status = f'Updated {index_built_at.strftime("%Y-%m-%d %H:%M UTC")}.'

    header_html, footer_html = _page_chrome(
        "Set up your ManaBox collection and card database, then head to Decks to compare or build.", error
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/svg+xml" href="https://svgs.scryfall.io/card-symbols/PW.svg">
<title>Brewlist -- Setup</title>
<style>{PAGE_STYLE}</style>
</head>
<body>
<main>
  {header_html}
  <form class="card" id="collection-form">
    <label for="manabox_csv">ManaBox collection export (.csv)</label>
    {collection_html}
    <input type="file" id="manabox_csv" name="manabox_csv" accept=".csv" required>
    <button type="submit" class="btn" id="collection-submit-btn">Upload Collection</button>
    <div id="collection-upload-label" class="hint" style="display:none;margin-top:8px;"></div>
  </form>
  <div class="card" id="price-index-card">
    <label>Card database</label>
    <div class="hint" style="margin-top:-8px;margin-bottom:8px;">Pricing, price trends, Game Changers, and Commander legality all come from this local MTGJSON-based database.</div>
    <div class="collection-status" id="price-index-status">{_esc(index_status)}</div>
    <button type="button" class="btn ghost small" id="refresh-index-btn">&#128260; Refresh Database</button>
    <div id="refresh-index-label" class="hint" style="display:none;margin-top:8px;"></div>
  </div>
  <div class="card" style="text-align:center;">
    <a href="/decks" class="btn" style="display:block;">Continue to Decks &rarr;</a>
  </div>
  <div class="card" id="update-card">
    <label>App updates</label>
    <div class="hint" style="margin-top:-8px;margin-bottom:8px;">Pulls the latest Brewlist code from GitHub -- only works if this was installed with 'git clone'.</div>
    <button type="button" class="btn ghost small" id="check-updates-btn">&#8635; Check for Updates</button>
    <div id="update-label" class="hint" style="margin-top:8px;"></div>
  </div>
  {footer_html}
</main>
<script>
const errorBox = document.getElementById('error-box');

const collectionForm = document.getElementById('collection-form');
const collectionSubmitBtn = document.getElementById('collection-submit-btn');
const collectionUploadLabel = document.getElementById('collection-upload-label');
collectionForm.addEventListener('submit', (e) => {{
  e.preventDefault();
  collectionSubmitBtn.disabled = true;
  collectionUploadLabel.style.display = 'block';
  collectionUploadLabel.textContent = 'Uploading\\u2026';
  fetch('/collection/upload', {{ method: 'POST', body: new FormData(collectionForm) }})
    .then(r => r.json())
    .then(data => {{
      collectionSubmitBtn.disabled = false;
      if (data.error) {{ collectionUploadLabel.textContent = data.error; return; }}
      collectionUploadLabel.textContent = `Uploaded ${{data.filename}} (${{data.uploaded}}).`;
      const statusEl = collectionForm.querySelector('.collection-status');
      if (statusEl) {{
        statusEl.innerHTML = `Using collection <b>${{data.filename}}</b> (uploaded ${{data.uploaded}}) &mdash; upload a new export above to replace it.`;
      }}
    }})
    .catch(() => {{ collectionSubmitBtn.disabled = false; collectionUploadLabel.textContent = 'Could not reach the server.'; }});
}});

const refreshIndexBtn = document.getElementById('refresh-index-btn');
const refreshIndexLabel = document.getElementById('refresh-index-label');
const refreshIndexStatus = document.getElementById('price-index-status');

function fmtIndexProgress(done, total) {{
  if (total > 100000) {{
    return (done / 1048576).toFixed(1) + ' / ' + (total / 1048576).toFixed(1) + ' MB';
  }}
  return done + '/' + total;
}}

function pollIndexRefresh(jobId) {{
  fetch('/compare/progress/' + jobId)
    .then(r => r.json())
    .then(data => {{
      if (data.status === 'running') {{
        if (data.stage === 'processing') {{
          refreshIndexLabel.textContent = data.total > 0
            ? 'Processing downloaded card data\\u2026 (' + data.done.toLocaleString() + ' / ' + data.total.toLocaleString() + ' cards)'
            : 'Processing downloaded card data\\u2026';
        }} else if (data.stage === 'tags') {{
          refreshIndexLabel.textContent = data.total > 0
            ? 'Finding budget-alternative tags\\u2026 (' + fmtIndexProgress(data.done, data.total) + ')'
            : 'Finding budget-alternative tags\\u2026';
        }} else {{
          refreshIndexLabel.textContent = data.total > 0
            ? 'Downloading\\u2026 (' + fmtIndexProgress(data.done, data.total) + ')'
            : 'Starting\\u2026';
        }}
        setTimeout(() => pollIndexRefresh(jobId), 400);
      }} else if (data.status === 'done') {{
        refreshIndexLabel.textContent = 'Done!';
        const now = new Date();
        const pad = n => String(n).padStart(2, '0');
        const stamp = now.getUTCFullYear() + '-' + pad(now.getUTCMonth() + 1) + '-' + pad(now.getUTCDate())
          + ' ' + pad(now.getUTCHours()) + ':' + pad(now.getUTCMinutes()) + ' UTC';
        refreshIndexStatus.textContent = 'Updated ' + stamp + '.';
        refreshIndexBtn.disabled = false;
        setTimeout(() => {{ refreshIndexLabel.style.display = 'none'; }}, 2000);
      }} else {{
        refreshIndexLabel.textContent = data.error || 'Something went wrong.';
        refreshIndexBtn.disabled = false;
      }}
    }})
    .catch(() => {{
      refreshIndexLabel.textContent = 'Lost contact with the server.';
      refreshIndexBtn.disabled = false;
    }});
}}

refreshIndexBtn.addEventListener('click', () => {{
  refreshIndexBtn.disabled = true;
  refreshIndexLabel.style.display = 'block';
  refreshIndexLabel.textContent = 'Starting\\u2026';
  fetch('/price-index/refresh', {{ method: 'POST' }})
    .then(r => r.json())
    .then(data => {{
      if (data.error) {{ refreshIndexLabel.textContent = data.error; refreshIndexBtn.disabled = false; return; }}
      pollIndexRefresh(data.job_id);
    }})
    .catch(() => {{
      refreshIndexLabel.textContent = 'Could not reach the server.';
      refreshIndexBtn.disabled = false;
    }});
}});

const checkUpdatesBtn = document.getElementById('check-updates-btn');
const updateLabel = document.getElementById('update-label');
checkUpdatesBtn.addEventListener('click', () => {{
  checkUpdatesBtn.disabled = true;
  updateLabel.textContent = 'Checking\\u2026';
  fetch('/update/check', {{ method: 'POST' }})
    .then(r => r.json())
    .then(data => {{
      updateLabel.textContent = data.message;
      checkUpdatesBtn.disabled = false;
    }})
    .catch(() => {{
      updateLabel.textContent = 'Could not reach the server.';
      checkUpdatesBtn.disabled = false;
    }});
}});

{SHUTDOWN_BTN_SCRIPT}
</script>
</body>
</html>
"""


def render_decks_page(error: str | None = None, prefill_url: str = "") -> str:
    projects = list_projects()

    def _project_item(p: dict) -> str:
        is_brew = p.get("type") == "brew"
        deck_id = p.get("deck_id", "")
        raw_name = p.get("deck_name") or deck_id or "unknown"
        label = _esc(raw_name)
        if is_brew:
            href = f'/builder?id={_esc(deck_id)}'
            data_attrs = 'data-type="brew"'
            label += ' <span class="hint-inline">(brew)</span>'
        else:
            href = "#"
            data_attrs = f'data-type="compare" data-url="{_esc(p.get("deck_url", deck_id))}"'
        return (
            f'<li><a href="{href}" class="recent-deck-link" {data_attrs}>{label}</a>'
            '<span class="deck-meta">'
            f'<span class="when">{_esc((p.get("updated") or "")[:16].replace("T", " "))}</span>'
            f'<button type="button" class="btn danger small delete-deck-btn" '
            f'data-deck-id="{_esc(deck_id)}" data-deck-name="{_esc(raw_name)}" title="Delete this deck">&times;</button>'
            '</span>'
            f'</li>'
        )

    items = "".join(_project_item(p) for p in projects[:15])
    projects_list_html = f'<ul class="project-list">{items}</ul>' if items else '<div class="hint" style="margin:0;">No decks yet.</div>'
    projects_html = (
        '<div class="card"><div class="page-header" style="margin-bottom:12px;">'
        '<label style="margin:0;">Recent decks</label>'
        '<a href="/builder" class="btn ghost small">&#43; New deck</a>'
        f'</div>{projects_list_html}</div>'
    )

    selected_stores = set(load_store_prefs())

    def _store_checkbox(label: str) -> str:
        name = STORE_DISPLAY_NAMES[label]
        if label == "CM":
            name_html = f'{_esc(name)} <span class="hint-inline">(EUR, not in totals)</span>'
            title_attr = (
                ' title="Cardmarket prices are in EUR, not USD -- shown for reference only, '
                'never used to pick the cheapest store or in any dollar total"'
            )
        else:
            name_html = _esc(name)
            title_attr = ""
        return (
            f'<div class="checkbox-row"><input type="checkbox" id="store_{label.lower()}" name="stores" '
            f'value="{label}"{" checked" if label in selected_stores else ""}>'
            f'<label for="store_{label.lower()}" style="margin:0;font-weight:400;"{title_attr}>{name_html}</label></div>'
        )

    store_checkboxes_html = "".join(_store_checkbox(label) for label in PICKABLE_STORE_LABELS)

    header_html, footer_html = _page_chrome(
        "Compare a decklist against your collection, with live pricing.", error
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/svg+xml" href="https://svgs.scryfall.io/card-symbols/PW.svg">
<title>Brewlist -- Decks</title>
<style>{PAGE_STYLE}</style>
</head>
<body>
<main>
  {header_html}
  <div style="margin-bottom:16px;">
    <a href="/" class="btn ghost small">&#9881; Collection &amp; Settings</a>
  </div>
  {projects_html}
  <form class="card" id="compare-form">
    <label for="moxfield_url">Moxfield or Archidekt deck URL</label>
    <input type="url" id="moxfield_url" name="moxfield_url" placeholder="https://moxfield.com/decks/... or https://archidekt.com/decks/..." value="{_esc(prefill_url)}" required>

    <div class="checkbox-row"><input type="checkbox" id="include_sideboard" name="include_sideboard"><label for="include_sideboard" style="margin:0;font-weight:400;">Include sideboard cards</label></div>
    <div class="checkbox-row"><input type="checkbox" id="include_maybeboard" name="include_maybeboard"><label for="include_maybeboard" style="margin:0;font-weight:400;">Include maybeboard cards</label></div>
    <div class="checkbox-row"><input type="checkbox" id="break_out_basics" name="break_out_basics" checked><label for="break_out_basics" style="margin:0;font-weight:400;">Break out basic lands separately</label></div>

    <label>Store pricing</label>
    <div class="store-grid">{store_checkboxes_html}</div>
    <div class="hint">Which stores to show prices from -- saved for next time.</div>

    <button type="submit" class="btn" id="submit-btn">Compare</button>
    <div id="progress-wrap">
      <div class="progress-track"><div class="progress-fill" id="progress-fill"></div></div>
      <div id="progress-label">Fetching deck from Moxfield&hellip;</div>
    </div>
  </form>
  {footer_html}
</main>
<script>
function fmtIndexProgress(done, total) {{
  if (total > 100000) {{
    return (done / 1048576).toFixed(1) + ' / ' + (total / 1048576).toFixed(1) + ' MB';
  }}
  return done + '/' + total;
}}

const form = document.getElementById('compare-form');
const submitBtn = document.getElementById('submit-btn');
const progressWrap = document.getElementById('progress-wrap');
const progressFill = document.getElementById('progress-fill');
const progressLabel = document.getElementById('progress-label');
const errorBox = document.getElementById('error-box');
const moxfieldUrlInput = document.getElementById('moxfield_url');

document.querySelectorAll('.recent-deck-link').forEach(link => {{
  link.addEventListener('click', (e) => {{
    if (link.dataset.type === 'brew') return; // real navigation to /builder?id=...
    e.preventDefault();
    moxfieldUrlInput.value = link.dataset.url;
    moxfieldUrlInput.focus();
  }});
}});

document.querySelectorAll('.delete-deck-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    if (!confirm(`Delete "${{btn.dataset.deckName}}"? This cannot be undone.`)) return;
    btn.disabled = true;
    fetch('/project/delete', {{
      method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ deck_id: btn.dataset.deckId }}),
    }})
      .then(r => r.json())
      .then(data => {{
        if (data.error) {{ showError(data.error); btn.disabled = false; return; }}
        const li = btn.closest('li');
        const list = li.parentElement;
        li.remove();
        if (!list.children.length) {{
          list.outerHTML = '<div class="hint" style="margin:0;">No decks yet.</div>';
        }}
      }})
      .catch(() => {{ showError('Could not reach the server.'); btn.disabled = false; }});
  }});
}});

function showError(message) {{
  errorBox.textContent = message;
  errorBox.style.display = 'block';
}}

function resetForm() {{
  submitBtn.disabled = false;
  submitBtn.textContent = 'Compare';
  progressWrap.style.display = 'none';
  progressFill.style.width = '0%';
}}

function pollProgress(jobId) {{
  fetch('/compare/progress/' + jobId)
    .then(r => r.json())
    .then(data => {{
      if (data.status === 'running') {{
        if (data.stage === 'combos') {{
          progressFill.style.width = '100%';
          progressLabel.textContent = 'Checking combos & bracket rating (Commander Spellbook)\\u2026';
        }} else if (data.stage === 'processing') {{
          if (data.total > 0) {{
            progressFill.style.width = Math.round((data.done / data.total) * 100) + '%';
            progressLabel.textContent = 'Processing downloaded card data\\u2026 (' + data.done.toLocaleString() + ' / ' + data.total.toLocaleString() + ' cards)';
          }} else {{
            progressFill.style.width = '100%';
            progressLabel.textContent = 'Processing downloaded card data\\u2026';
          }}
        }} else if (data.stage === 'tags') {{
          if (data.total > 0) {{
            progressFill.style.width = Math.round((data.done / data.total) * 100) + '%';
            progressLabel.textContent = 'Finding budget-alternative tags\\u2026 (' + fmtIndexProgress(data.done, data.total) + ')';
          }} else {{
            progressFill.style.width = '100%';
            progressLabel.textContent = 'Finding budget-alternative tags\\u2026';
          }}
        }} else if (data.total > 0) {{
          const pct = Math.round((data.done / data.total) * 100);
          progressFill.style.width = pct + '%';
          progressLabel.textContent = 'Preparing price data... (' + pct + '%)';
        }} else {{
          progressLabel.textContent = 'Fetching deck and preparing comparison\\u2026';
        }}
        setTimeout(() => pollProgress(jobId), 400);
      }} else if (data.status === 'done') {{
        progressFill.style.width = '100%';
        progressLabel.textContent = 'Done!';
        window.location.href = '/compare/result/' + jobId;
      }} else if (data.status === 'error') {{
        showError(data.error || 'Something went wrong.');
        resetForm();
      }} else {{
        showError('That comparison could not be found -- try again.');
        resetForm();
      }}
    }})
    .catch(() => {{
      showError('Lost contact with the server while checking progress.');
      resetForm();
    }});
}}

form.addEventListener('submit', (e) => {{
  e.preventDefault();
  errorBox.style.display = 'none';
  if (!form.querySelector('input[name="stores"]:checked')) {{
    showError('Pick at least one store for pricing.');
    return;
  }}
  submitBtn.disabled = true;
  submitBtn.textContent = 'Working...';
  progressWrap.style.display = 'block';
  progressFill.style.width = '0%';
  progressLabel.textContent = 'Fetching deck from Moxfield\\u2026';

  fetch('/compare/start', {{ method: 'POST', body: new FormData(form) }})
    .then(r => r.json())
    .then(data => {{
      if (data.error) {{ showError(data.error); resetForm(); return; }}
      pollProgress(data.job_id);
    }})
    .catch(() => {{
      showError('Could not reach the server.');
      resetForm();
    }});
}});

{SHUTDOWN_BTN_SCRIPT}
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# Deck builder page -- build a brand-new deck (Commander or generic 60-card
# constructed) using only cards already in the ManaBox collection. See
# deck_builder.py for the non-UI logic (gameplay-data merge, fill-the-gaps
# suggestions, brew -> CardEntry conversion). A saved brew is just another
# project file (see save_project/load_project above), distinguished by
# "type": "brew" -- the home page's Recent decks list already picks it up
# for free.
# --------------------------------------------------------------------------

BUILDER_STYLE = """
main.builder { max-width: 1400px; }
body::before {
  content: ""; position: fixed; inset: -40px;
  background-image: var(--commander-bg-url, none);
  background-size: cover; background-position: center top; background-repeat: no-repeat;
  filter: blur(16px); z-index: -2; pointer-events: none;
}
body::after {
  content: ""; position: fixed; inset: 0;
  background: color-mix(in srgb, var(--bg) 68%, transparent);
  z-index: -1; pointer-events: none;
}
.builder-top { display:flex; flex-direction:column; gap:14px; }
.builder-top-row { display:flex; flex-wrap:wrap; gap:12px; align-items:flex-end; }
.builder-top-row.builder-settings { justify-content:space-between; }
.builder-top-row.builder-actions { align-items:center; }
.builder-utility-actions { align-self:flex-start; }
.builder-top .field { display:flex; flex-direction:column; gap:4px; }
.builder-top label { margin:0; }
.builder-top input[type=text], .builder-top select {
  padding:8px 10px; border-radius:8px; border:1px solid var(--card-border);
  background:var(--bg); color:var(--text); font-size:0.9rem; margin:0; min-width:160px;
}
.action-group { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.builder-layout { display:grid; grid-template-columns: 1.8fr 1fr; gap:20px; align-items:start; margin-top:20px; }
.builder-filters { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:28px; }
.builder-filters input[type=text] { flex:1; min-width:160px; margin:0; }
.builder-filters select {
  padding:8px 10px; border-radius:8px; border:1px solid var(--card-border);
  background:var(--bg); color:var(--text); font-size:0.85rem;
}
.exact-color-toggle {
  display:flex; align-items:center; gap:5px; font-size:0.8rem; color:var(--text-dim);
  white-space:nowrap; cursor:pointer; user-select:none;
}
.exact-color-toggle input { margin:0; cursor:pointer; }
.collection-grid {
  display:grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap:10px; max-height:75vh; overflow-y:auto; padding-right:4px;
}
.builder-tile { background:var(--bg-elevated); border:1px solid var(--card-border); border-radius:10px; padding:10px; font-size:0.82rem; display:flex; gap:10px; position:relative; }
.builder-tile .tile-body { flex:1; min-width:0; }
.builder-tile .name { font-weight:600; margin-bottom:2px; padding-right:60px; }
.builder-tile .meta { color:var(--text-dim); font-size:0.75rem; }
.builder-tile .warn { color: var(--missing); font-size:0.72rem; margin-top:4px; }
.builder-tile.hidden { display:none; }
.tile-corner-actions { position:absolute; top:6px; right:6px; display:flex; align-items:flex-end; gap:4px; }
.tile-icon-stack { display:flex; flex-direction:column; gap:3px; }
.tile-icon-btn {
  width:22px; height:22px; padding:0; display:flex; align-items:center; justify-content:center;
  border-radius:50%; font-size:0.8rem; line-height:1; flex-shrink:0;
}
.card-thumb {
  width: 48px; height: 67px; border-radius: 5px; object-fit: cover;
  flex-shrink: 0; background: var(--card-border); cursor: zoom-in;
}
.card-thumb.small { width: 32px; height: 44px; border-radius: 3.5px; }
.color-icons { display: inline-flex; gap: 2px; align-items: center; flex-shrink: 0; }
.mana-icon { width: 14px; height: 14px; display: block; }
.mana-cost-pips { display: inline-flex; align-items: center; gap: 1px; flex-shrink: 0; }
.mana-cost-pips .mana-icon { width: 13px; height: 13px; }
#hover-preview {
  /* Above .modal-overlay's (200) and .replace-popup's (260) z-index --
     this needs to outrank whatever overlay/popup is currently open, not
     just the base page content, since it can be triggered from inside
     either one (sample-hand-cards in the modal, replace-popup's items). */
  position: fixed; pointer-events: none; z-index: 300; display: none;
  width: 240px; border-radius: 4.75% / 3.5%;
  box-shadow: 0 12px 32px rgba(0,0,0,0.5), 0 0 0 1px var(--card-border);
}
#hover-preview.show { display: block; }
.deck-panel { position:sticky; top:20px; padding:14px 16px; }
.deck-stats { display:flex; gap:14px; flex-wrap:wrap; color:var(--text-dim); font-size:0.85rem; margin-bottom:10px; }
.deck-stats b { color: var(--text); }
.commander-slot {
  border:1px dashed var(--card-border); border-radius:10px; padding:8px 10px;
  margin-bottom:10px; font-size:0.85rem; display:flex; justify-content:space-between; align-items:center; gap:8px;
}
.commander-slot .commander-info { display:flex; align-items:center; gap:8px; }
.deck-group { margin-bottom:10px; }
.deck-group h4 { margin: 0 0 4px; font-size:0.75rem; color:var(--text-dim); text-transform:uppercase; letter-spacing:0.03em; }
.deck-row { display:flex; justify-content:space-between; align-items:center; gap:8px; padding:3px 0; border-bottom:1px solid var(--card-border); font-size:0.85rem; }
.deck-row:last-child { border-bottom:none; }
.deck-row .row-name { display:flex; align-items:center; gap:8px; min-width:0; }
.deck-row .row-name span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.deck-row .qty-controls { display:flex; align-items:center; gap:6px; flex-shrink:0; }
.deck-row .qty-controls .qty-btn { width:22px; height:22px; padding:0; line-height:1; }
.replace-popup {
  display:none; position:fixed; z-index:260; width:260px; max-height:320px; overflow-y:auto;
  background:var(--bg-elevated); border:1px solid var(--card-border); border-radius:10px;
  box-shadow:var(--shadow); padding:8px;
}
.replace-popup.show { display:block; }
.replace-popup h5 { margin:0 0 6px; padding:0 4px; font-size:0.72rem; color:var(--text-dim); text-transform:uppercase; letter-spacing:0.03em; }
.replace-popup .replace-item { display:flex; align-items:center; gap:8px; padding:6px 8px; border-radius:6px; cursor:pointer; font-size:0.82rem; }
.replace-popup .replace-item:hover { background:var(--bg); }
.replace-popup .replace-item .reason { color:var(--text-dim); font-size:0.72rem; margin-top:1px; }
.replace-popup .hint { margin:4px; }
#deck-list { max-height:38vh; overflow-y:auto; padding-right:4px; }
#suggestions-panel { margin-top:10px; max-height:38vh; overflow-y:auto; padding-right:4px; }
#suggestions-panel .suggestion-row { display:flex; justify-content:space-between; align-items:center; gap:8px; padding:6px 0; border-bottom:1px solid var(--card-border); font-size:0.82rem; }
#suggestions-panel .row-name { display:flex; align-items:center; gap:8px; min-width:0; }
#suggestions-panel .reason { color:var(--text-dim); font-size:0.72rem; }
.segmented { display:flex; border:1px solid var(--card-border); border-radius:8px; overflow:hidden; }
.segmented .seg-btn {
  border:none; background:var(--bg); color:var(--text-dim); padding:8px 14px;
  font-size:0.85rem; font-family:inherit; cursor:pointer;
}
.segmented .seg-btn:first-child { border-right:1px solid var(--card-border); }
.segmented .seg-btn.active { background:var(--gold); color:#241f00; font-weight:600; }
.analysis-heading { margin:16px 0 8px; font-size:0.85rem; }
.analysis-heading:first-child { margin-top:0; }
.analysis-stat-line { color:var(--text-dim); font-size:0.78rem; margin:0 0 8px; }
.curve-chart { display:flex; align-items:flex-end; gap:4px; height:90px; }
.curve-col { flex:1; display:flex; flex-direction:column; align-items:center; height:100%; }
.curve-bar-wrap { flex:1; width:100%; display:flex; flex-direction:column-reverse; align-items:stretch; min-height:0; }
.curve-bar { width:100%; }
.curve-bar.permanents { background:var(--accent); }
.curve-bar.spells { background:var(--gold); }
.curve-label { font-size:0.68rem; color:var(--text-dim); margin-top:3px; }
.chart-legend { display:flex; gap:12px; font-size:0.72rem; color:var(--text-dim); margin:6px 0 8px; flex-wrap:wrap; }
.chart-legend .swatch { width:9px; height:9px; border-radius:2px; display:inline-block; margin-right:4px; }
.chart-legend .swatch.permanents { background:var(--accent); }
.chart-legend .swatch.spells { background:var(--gold); }
.chart-legend .swatch.pips { background:var(--accent); }
.color-breakdown { display:flex; flex-direction:column; gap:6px; margin-bottom:8px; }
.color-row { display:flex; align-items:center; gap:8px; }
.color-row .mana-icon { width:16px; height:16px; flex-shrink:0; }
.color-bar-track { flex:1; height:7px; border-radius:4px; background:var(--card-border); overflow:hidden; }
.color-bar { height:100%; border-radius:4px; background:var(--accent); }
.sample-hand-cards { display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }
.sample-hand-thumb { width:64px; height:89px; border-radius:5px; object-fit:cover; background:var(--card-border); cursor:zoom-in; }
body.compact .builder-tile { padding:4px 10px; min-height:36px; align-items:center; }
body.compact .builder-tile .card-thumb { display:none; }
body.compact .builder-tile .meta { display:none; }
body.compact .builder-tile .name { margin-bottom:0; padding-right:0; }
body.compact .builder-tile .name-text { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
body.compact .tile-corner-actions { position:static; flex-shrink:0; }
body.compact .collection-grid { grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); }
body.compact .deck-row .card-thumb { display:none; }
.theme-picker { position:relative; }
.theme-picker input[type=text] { width:200px; margin:0; padding:8px 12px; font-size:0.85rem; }
.theme-dropdown {
  display:none; position:absolute; top:100%; left:0; margin-top:4px; width:240px; max-height:240px;
  overflow-y:auto; background:var(--bg-elevated); border:1px solid var(--card-border); border-radius:8px;
  box-shadow:var(--shadow); z-index:30;
}
.theme-dropdown.show { display:block; }
.theme-dropdown-item { display:flex; justify-content:space-between; gap:8px; padding:7px 12px; font-size:0.82rem; cursor:pointer; }
.theme-dropdown-item:hover, .theme-dropdown-item.active { background:var(--bg); }
.theme-dropdown-item .count { color:var(--text-dim); font-size:0.72rem; flex-shrink:0; }
.theme-dropdown-empty { padding:8px 12px; font-size:0.78rem; color:var(--text-dim); }
.mix-targets-field { border:1px solid var(--card-border); border-radius:8px; padding:2px 12px; }
.mix-targets-field summary { cursor:pointer; padding:6px 10px; font-size:0.85rem; color:var(--text-dim); white-space:nowrap; }
.mix-targets-field[open] { position:relative; z-index:5; }
.mix-targets-grid { display:flex; gap:16px; flex-wrap:wrap; align-items:flex-end; padding:2px 0 12px; }
.mix-targets-grid label { display:flex; flex-direction:column; gap:3px; font-size:0.78rem; color:var(--text-dim); }
.mix-targets-grid input[type=number] {
  width:60px; padding:6px 8px; border-radius:6px; border:1px solid var(--card-border);
  background:var(--bg); color:var(--text); font-size:0.85rem; margin:0;
}
.set-selection-field { position:relative; }
.set-selection-popup {
  /* Select All/Clear All and Cancel/OK are pinned outside the scrolling
     list (flex column, only .set-selection-list scrolls) so they're
     always reachable without scrolling through however many sets are in
     the list first -- OK/Cancel especially shouldn't require hunting for. */
  display:none; position:absolute; top:100%; left:0; margin-top:4px; width:280px; max-height:340px;
  background:var(--bg-elevated); border:1px solid var(--card-border); border-radius:8px;
  box-shadow:var(--shadow); z-index:30; padding:8px; flex-direction:column;
}
.set-selection-popup.show { display:flex; }
.set-selection-list { flex:1; min-height:0; overflow-y:auto; display:flex; flex-direction:column; gap:2px; margin:6px 0; }
.set-selection-list label { display:flex; align-items:center; gap:6px; font-size:0.82rem; padding:3px 4px; border-radius:4px; cursor:pointer; }
.set-selection-list label:hover { background:var(--bg); }
.set-selection-list input { margin:0; cursor:pointer; }
.set-selection-quick { display:flex; gap:8px; flex-shrink:0; }
.set-selection-quick .btn { flex:1; }
.set-selection-actions { display:flex; gap:8px; padding-top:6px; border-top:1px solid var(--card-border); flex-shrink:0; }
.set-selection-actions .btn { flex:1; }
.bv-badge {
  display:inline-flex; align-items:center; gap:4px; padding:4px 10px; margin:0 6px 6px 0;
  border-radius:999px; background:var(--bg); border:1px solid var(--card-border);
  font-size:0.78rem; color:var(--text); position:relative; cursor:help;
}
.bv-badge b { font-weight:600; }
.bv-badge.warn { border-color:color-mix(in srgb, var(--missing) 45%, var(--card-border)); color:var(--missing); }
.bv-badge.good { border-color:color-mix(in srgb, var(--owned) 45%, var(--card-border)); color:var(--owned); }
.bv-badge.highlight { border-color:color-mix(in srgb, var(--gold) 45%, var(--card-border)); color:var(--gold); }
.bv-badge .tooltip-popup {
  /* Opens downward, not upward -- these badges sit right at the top of
     the Analyze modal's scrollable body, and .modal-body's overflow-y:auto
     makes the X axis clip too (an element with only one non-visible
     overflow axis gets the other computed as auto as well), so an upward
     popup got clipped by the modal header above it. Horizontal position
     is NOT solved with a fixed CSS anchor (left:0 clips at the modal's
     right edge for a late badge, centering clips at the left edge for the
     first badge) -- JS clamps the actual left offset per badge on hover
     instead, see positionBadgeTooltip below; this left:0 is just the
     unshifted baseline it measures from.
  */
  display:none; position:absolute; top:100%; left:0; margin-top:6px;
  background:var(--bg-elevated); border:1px solid var(--card-border); border-radius:8px;
  padding:8px 10px; font-size:0.78rem; font-weight:400; color:var(--text); white-space:normal;
  width:max-content; max-width:220px; box-shadow:var(--shadow); z-index:20;
}
.modal-overlay {
  display:none; position:fixed; inset:0; background:rgba(0,0,0,0.6);
  z-index:200; align-items:center; justify-content:center; padding:20px;
}
.modal-overlay.show { display:flex; }
.modal-box {
  background:var(--bg-elevated); border:1px solid var(--card-border); border-radius:12px;
  width:100%; max-width:640px; max-height:85vh; display:flex; flex-direction:column; overflow:hidden;
}
.modal-header { display:flex; align-items:center; justify-content:space-between; padding:14px 18px; border-bottom:1px solid var(--card-border); flex-shrink:0; }
.modal-header h3 { margin:0; font-size:1.05rem; }
.modal-close { background:none; border:none; color:var(--text-dim); font-size:1.4rem; line-height:1; cursor:pointer; padding:2px 6px; }
.modal-close:hover { color:var(--text); }
.modal-body { padding:16px 18px; overflow-y:auto; flex:1; }
.modal-footer { padding:12px 18px; border-top:1px solid var(--card-border); display:flex; justify-content:flex-end; flex-shrink:0; }
.rule0-list { margin:0 0 4px; padding-left:20px; font-size:0.85rem; color:var(--text); }
.rule0-list li { margin-bottom:5px; }
.combo-group { margin-bottom:10px; }
.combo-group h5 { margin:0 0 6px; font-size:0.75rem; color:var(--text-dim); text-transform:uppercase; letter-spacing:0.03em; }
.combo-item { border:1px solid var(--card-border); border-radius:8px; padding:8px 10px; margin-bottom:6px; font-size:0.82rem; }
.combo-item .combo-uses { font-weight:600; }
.combo-item .combo-arrow { color:var(--text-dim); margin:0 6px; }
.combo-item .combo-missing { color:var(--missing); font-size:0.78rem; margin-top:2px; }
.combo-item a { color:var(--accent); }
#battle-card { display:none; }
@media (max-width: 900px) { .builder-layout { grid-template-columns: 1fr; } .deck-panel { position:static; } }
@media print {
  body * { visibility:hidden; }
  body::before, body::after { display:none !important; }
  #battle-card, #battle-card * { visibility:visible; }
  #battle-card.printing {
    display:block; position:absolute; top:0; left:0; width:100%;
    font-family: system-ui, sans-serif; color:#111; background:#fff; padding:24px;
  }
  #battle-card h1 { margin:0 0 4px; font-size:1.4rem; }
  #battle-card .bc-sub { color:#555; font-size:0.9rem; margin-bottom:14px; }
  #battle-card h2 { font-size:0.95rem; margin:16px 0 6px; border-bottom:1px solid #ccc; padding-bottom:3px; }
  #battle-card ul { margin:0; padding-left:20px; font-size:0.85rem; }
  #battle-card li { margin-bottom:4px; }
  #battle-card .bc-combo { font-size:0.82rem; margin-bottom:5px; }
  #battle-card .bc-badges span { display:inline-block; border:1px solid #999; border-radius:999px; padding:2px 9px; margin:0 6px 6px 0; font-size:0.78rem; }
}
"""

COLOR_LABELS = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}

# Wizards' own guild/shard/wedge flavor names for 2- and 3-color
# combinations, plus the community-standard "Nephilim" names for the
# 4-color combos (from the Time Spiral Nephilim cycle, reused ever since
# as the accepted shorthand -- e.g. by Scryfall's own color-combination
# search terms). Letter order in each tuple matches each name's
# conventional spoken order (not always WUBRG order), same as
# https://boardgames.stackexchange.com/questions/11550. Filtering by one
# of these is a subset match against a card's color identity (same
# "legal to include" rule the Suggest engine already uses for a
# commander's color identity) -- so e.g. "Sultai" also surfaces mono-
# black/blue/green and colorless cards, not just true 3-color ones.
COLOR_FAMILIES = {
    "Two-Color (Guilds)": [
        ("WU", "Azorius"), ("UB", "Dimir"), ("BR", "Rakdos"), ("RG", "Gruul"), ("GW", "Selesnya"),
        ("WB", "Orzhov"), ("UR", "Izzet"), ("BG", "Golgari"), ("RW", "Boros"), ("GU", "Simic"),
    ],
    "Three-Color (Shards)": [
        ("GWU", "Bant"), ("WUB", "Esper"), ("UBR", "Grixis"), ("BRG", "Jund"), ("RGW", "Naya"),
    ],
    "Three-Color (Wedges)": [
        ("WBG", "Abzan"), ("URW", "Jeskai"), ("BGU", "Sultai"), ("RWB", "Mardu"), ("GUR", "Temur"),
    ],
    "Four-Color": [
        ("WUBR", "Yore-Tiller"), ("UBRG", "Glint-Eye"), ("WBRG", "Dune-Brood"),
        ("WURG", "Ink-Treader"), ("WUBG", "Witch-Maw"),
    ],
    "Five-Color": [
        ("WUBRG", "Five-Color"),
    ],
}


def render_builder_page(deck_id: str | None = None) -> str:
    brew = load_project(deck_id) if deck_id else {}
    if deck_id and brew.get("type") != "brew":
        brew = {}
        deck_id = None
    default_mix = {"Lands": 38, "Ramp": 10, "Draw": 10, "Interaction": 11}
    saved_mix = brew.get("mix_targets") or {}
    brew_state = {
        "deck_name": brew.get("deck_name") or "",
        "format": brew.get("format") or "commander",
        "target_format": brew.get("target_format") or "standard",
        "commander": brew.get("commander"),
        "cards": brew.get("cards") or [],
        "mix_targets": {role: saved_mix.get(role, default_mix[role]) for role in default_mix},
        "intended_bracket": brew.get("intended_bracket") or "",
        "preferred_theme_tag_ids": brew.get("preferred_theme_tag_ids") or [],
        "preferred_theme_label": brew.get("preferred_theme_label") or "",
        "excluded_set_codes": brew.get("excluded_set_codes") or [],
        "ai_summary": brew.get("ai_summary") or "",
        "further_optimizations": brew.get("further_optimizations") or [],
        "maybeboard": brew.get("maybeboard") or [],
    }

    category_options = "".join(f'<option value="{_esc(b)}">{_esc(b)}</option>' for b in BUCKET_ORDER)
    color_options = "".join(f'<option value="{code}">{_esc(label)}</option>' for code, label in COLOR_LABELS.items())
    color_family_optgroups = "".join(
        f'<optgroup label="{_esc(group)}">' + "".join(
            f'<option value="{colors}">{_esc(name)} ({colors})</option>' for colors, name in combos
        ) + "</optgroup>"
        for group, combos in COLOR_FAMILIES.items()
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/svg+xml" href="https://svgs.scryfall.io/card-symbols/PW.svg">
<title>Brewlist -- Deck Builder</title>
<style>{PAGE_STYLE}{BUILDER_STYLE}</style>
</head>
<body>
<img id="hover-preview" alt="">
<main class="builder">
  <div class="page-header">
    <div>
      <h1>Deck Builder</h1>
      <p class="subtitle">Build a new deck using only cards you already own.</p>
    </div>
    <div class="header-actions">
      <a href="/decks" class="btn ghost small">&larr; Decks</a>
    </div>
  </div>
  <div id="error-box" class="error" style="display:none;"></div>

  <div class="card builder-top">
    <div class="builder-top-row builder-settings">
      <div class="action-group">
        <div class="field"><label for="deck-name">Deck name</label>
          <input type="text" id="deck-name" placeholder="Untitled brew">
        </div>
        <div class="field"><label for="deck-format">Format</label>
          <select id="deck-format">
            <option value="commander">Commander</option>
            <option value="constructed">60-card constructed</option>
          </select>
        </div>
        <div class="field" id="target-format-field">
          <label for="target-format">Target format (legality)</label>
          <select id="target-format">
            <option value="standard">Standard</option>
            <option value="pioneer">Pioneer</option>
            <option value="modern">Modern</option>
            <option value="legacy">Legacy</option>
            <option value="vintage">Vintage</option>
            <option value="pauper">Pauper</option>
          </select>
        </div>
        <div class="field" id="intended-bracket-field">
          <label for="intended-bracket" title="Optional -- if set, Suggest avoids Game Changers beyond what WotC's own bracket rules allow. Leave as No preference to build freely; the estimated bracket is always shown either way.">Intended bracket</label>
          <select id="intended-bracket">
            <option value="">No preference (build freely)</option>
            <option value="1-2">1-2 (Exhibition / Core)</option>
            <option value="3">3 (Upgraded)</option>
            <option value="4+">4+ (Optimized / cEDH)</option>
          </select>
        </div>
      </div>
      <div class="action-group builder-utility-actions">
        <button type="button" class="btn" id="save-btn">Save</button>
        <span id="save-label" class="hint" style="margin:0;display:none;"></span>
        <button type="button" class="btn ghost small" id="report-btn">View full report</button>
        <button type="button" class="btn ghost small" id="copy-decklist-btn" title="Copies a plain-text decklist (with your exact printings) you can paste into Moxfield or Archidekt to import">&#128203; Copy Decklist</button>
        <button type="button" class="btn ghost small" id="export-csv-btn" title="Downloads a CSV grouped like a physical store's binders, so you can find these cards in your own collection">&#128190; Export CSV</button>
      </div>
    </div>
    <div class="builder-top-row builder-actions">
      <div class="action-group">
        <button type="button" class="btn ghost" id="suggest-btn">Suggest cards</button>
        <button type="button" class="btn ghost" id="ai-build-btn" title="Claude reads your commander's actual card text and searches your collection for whatever it decides is relevant -- not limited to the fixed tag list Suggest uses. Uses your own Anthropic API key.">&#10024; Build with AI</button>
        <div class="theme-picker">
          <input type="text" id="theme-input" placeholder="Preferred theme (optional)" autocomplete="off">
          <div class="theme-dropdown" id="theme-dropdown"></div>
        </div>
        <details class="mix-targets-field" id="mix-targets-field">
          <summary>Deck mix targets</summary>
          <div class="mix-targets-grid">
            <label>Lands <input type="number" id="mix-lands" min="0" max="100"></label>
            <label>Ramp <input type="number" id="mix-ramp" min="0" max="100"></label>
            <label>Draw <input type="number" id="mix-draw" min="0" max="100"></label>
            <label>Interaction <input type="number" id="mix-interaction" min="0" max="100"></label>
            <span class="hint" id="mix-synergy-readout" style="margin:0;"></span>
          </div>
        </details>
        <div class="set-selection-field">
          <button type="button" class="btn ghost small" id="set-selection-btn">Set Selection</button>
          <div class="set-selection-popup" id="set-selection-popup"></div>
        </div>
      </div>
    </div>
  </div>

  <div class="builder-layout">
    <div>
      <div class="builder-filters">
        <input type="text" id="search" placeholder="Search your collection...">
        <select id="filter-category"><option value="">All types</option><option value="Commander">Commander</option>{category_options}</select>
        <select id="filter-color"><option value="">Any color</option><option value="C">Colorless</option><optgroup label="One-Color">{color_options}</optgroup>{color_family_optgroups}</select>
        <label class="exact-color-toggle" title="Only show cards whose color identity is exactly the selected color(s) -- e.g. true Grixis cards, not also mono-colored or two-color ones that merely fit within Grixis">
          <input type="checkbox" id="filter-color-exact"> Exact colors
        </label>
        <div class="set-selection-field">
          <button type="button" class="btn ghost small" id="grid-set-filter-btn">Set</button>
          <div class="set-selection-popup" id="grid-set-filter-popup"></div>
        </div>
        <div class="segmented" id="view-density-toggle" title="Switches between full tiles and a compact text list with mana-cost pips">
          <button type="button" class="seg-btn active" data-value="cards">Cards</button>
          <button type="button" class="seg-btn" data-value="compact">Compact</button>
        </div>
      </div>
      <div class="collection-grid" id="collection-grid"><div class="hint">Loading your collection&hellip;</div></div>
    </div>
    <div class="card deck-panel">
      <div class="deck-stats" id="deck-stats"></div>
      <button type="button" class="btn ghost small" id="analyze-btn" style="margin-bottom:10px;">&#128269; Analyze Deck</button>
      <button type="button" class="btn ghost small" id="optimize-btn" style="margin-bottom:10px;" title="Looks for real, owned combos you're exactly one card away from completing, and proposes swaps to add them -- useful after Suggest cards has already filled the deck, since a full batch can't always see a combo piece it's about to add in that same batch">&#9889; Optimize Deck</button>
      <div id="commander-slot-wrap"></div>
      <button type="button" class="btn danger small" id="clear-cards-btn" style="display:none;margin-bottom:6px;">Clear All Cards</button>
      <div id="deck-list"></div>
      <div id="suggestions-panel"></div>
    </div>
  </div>
</main>

<div class="modal-overlay" id="analyze-modal">
  <div class="modal-box">
    <div class="modal-header">
      <h3 id="analyze-modal-title">Analyze Deck</h3>
      <button type="button" class="modal-close" id="analyze-modal-close" aria-label="Close">&times;</button>
    </div>
    <div class="modal-body">
      <div id="analyze-badges"></div>
      <div id="ai-summary-block" style="display:none;">
        <h4 class="analysis-heading">&#10024; AI Summary</h4>
        <p class="hint" id="ai-summary-text" style="margin:0 0 12px;"></p>
      </div>
      <div id="further-optimizations-block" style="display:none;">
        <h4 class="analysis-heading">&#9889; Further Optimizations</h4>
        <ul class="rule0-list" id="further-optimizations-list"></ul>
      </div>
      <div id="maybeboard-block" style="display:none;">
        <h4 class="analysis-heading">&#128064; Maybeboard</h4>
        <p class="hint" style="margin:0 0 8px;">Candidates the AI found but wasn't confident enough to commit as a direct swap -- not part of the deck.</p>
        <div id="maybeboard-list"></div>
      </div>
      <div class="hint" id="analyze-loading" style="margin:0 0 12px;">Checking&hellip; (calls Commander Spellbook live, may take a few seconds)</div>

      <h4 class="analysis-heading">Rule 0 summary</h4>
      <ul class="rule0-list" id="rule0-list"></ul>

      <h4 class="analysis-heading">&#128202; Mana Curve &amp; Colors</h4>
      <div class="analysis-stat-line" id="analysis-stat-line"></div>
      <div class="curve-chart" id="curve-chart"></div>
      <div class="chart-legend">
        <span><span class="swatch permanents"></span>Permanents</span>
        <span><span class="swatch spells"></span>Instants/Sorceries</span>
      </div>
      <div class="color-breakdown" id="color-breakdown"></div>

      <h4 class="analysis-heading">&#127183; Sample Opening Hand</h4>
      <div class="analysis-stat-line" id="sample-hand-stat"></div>
      <button type="button" class="btn ghost small" id="draw-hand-btn">Draw / Deal Another Hand</button>
      <div class="sample-hand-cards" id="sample-hand-cards"></div>

      <h4 class="analysis-heading">&#128279; Combo Reference</h4>
      <div id="combo-reference"></div>
    </div>
    <div class="modal-footer">
      <button type="button" class="btn ghost small" id="print-battle-card-btn">&#128424; Print Battle Card</button>
    </div>
  </div>
</div>

<div class="modal-overlay" id="ai-modal">
  <div class="modal-box">
    <div class="modal-header">
      <h3>&#10024; Build with AI</h3>
      <button type="button" class="modal-close" id="ai-modal-close" aria-label="Close">&times;</button>
    </div>
    <div class="modal-body">
      <div class="error" id="ai-modal-error" style="display:none;"></div>

      <div id="ai-mode-phase">
        <p class="hint" style="margin:0 0 10px;">Claude reads your commander's actual card text and searches for whatever it decides is relevant -- not limited to Suggest's fixed tag list.</p>
        <button type="button" class="btn" id="ai-mode-fresh-btn" style="display:block;width:100%;text-align:left;margin-bottom:8px;">
          <strong>Build deck from chosen commander</strong><br><span class="hint" style="margin:0;">Starts fresh from just your commander.</span>
        </button>
        <button type="button" class="btn" id="ai-mode-improve-btn" style="display:block;width:100%;text-align:left;margin-bottom:8px;">
          <strong>Improve/optimize current deck</strong><br><span class="hint" style="margin:0;">Keeps what you've already chosen and builds on it.</span>
        </button>
        <button type="button" class="btn" id="ai-mode-import-btn" style="display:block;width:100%;text-align:left;">
          <strong>Improve deck from import</strong><br><span class="hint" style="margin:0;">Paste a decklist or a Moxfield/Archidekt URL -- may include cards you don't own. Get a report of what to buy afterward.</span>
        </button>
      </div>

      <div id="ai-import-phase" style="display:none;">
        <p class="hint" style="margin:0 0 8px;">Fill in either one -- not both.</p>
        <label for="ai-import-url-input" style="font-size:0.85rem;font-weight:600;">Moxfield or Archidekt URL</label>
        <input type="text" id="ai-import-url-input" placeholder="https://moxfield.com/decks/..." style="width:100%;box-sizing:border-box;margin:6px 0 10px;">
        <label for="ai-import-text-input" style="font-size:0.85rem;font-weight:600;">Or paste a decklist</label>
        <textarea id="ai-import-text-input" rows="6" placeholder="1 Sol Ring&#10;1 Command Tower&#10;..." style="width:100%;box-sizing:border-box;margin:6px 0 10px;font-family:monospace;font-size:0.82rem;"></textarea>
        <p class="hint" style="margin:0 0 12px;">A pasted list doesn't carry a commander -- your already-chosen commander is used. A Commander-format URL import uses its own commander automatically.</p>
        <button type="button" class="btn" id="ai-import-continue-btn">Continue</button>
        <button type="button" class="btn ghost small" id="ai-import-back-btn">&larr; Back</button>
      </div>

      <div id="ai-setup-phase" style="display:none;">
        <p class="hint" style="margin:0 0 10px;">This calls the Anthropic API using your own key, which needs its own billing set up and costs a small amount per build (typically well under a dollar).</p>
        <ol style="margin:0 0 12px;padding-left:20px;font-size:0.85rem;">
          <li>Go to <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noopener">console.anthropic.com/settings/keys</a> and sign in (or create an account).</li>
          <li>Add billing under Settings &rarr; Billing if you haven't already.</li>
          <li>Click "Create Key", give it any name, then copy the key it shows you.</li>
          <li>Paste it below. It's saved locally on your machine only -- never sent anywhere but Anthropic, and never included if you share this app's code.</li>
        </ol>
        <input type="password" id="ai-key-input" placeholder="sk-ant-..." autocomplete="off" style="width:100%;box-sizing:border-box;margin-bottom:8px;">
        <button type="button" class="btn" id="ai-key-save-btn">Save Key</button>
        <span class="hint" id="ai-key-status" style="margin-left:8px;"></span>
      </div>

      <div id="ai-confirm-phase" style="display:none;">
        <p class="hint" style="margin:0 0 8px;" id="ai-key-configured-note"></p>
        <label for="ai-notes-input" style="font-size:0.85rem;font-weight:600;">Anything specific you want this deck to do? (optional)</label>
        <textarea id="ai-notes-input" rows="3" placeholder="e.g. lean into graveyard recursion, keep it low to the ground, prioritize card draw" style="width:100%;box-sizing:border-box;margin:6px 0 10px;font-family:inherit;"></textarea>
        <p class="hint" style="margin:0 0 12px;" id="ai-confirm-note">This can take several minutes (up to 7) and makes many real API calls on your account. The result won't be applied until you review and approve it.</p>
        <button type="button" class="btn" id="ai-start-btn">Start Build</button>
        <button type="button" class="btn ghost small" id="ai-change-key-btn">Change / Remove Key</button>
      </div>

      <div id="ai-progress-phase" style="display:none;">
        <p class="hint" id="ai-progress-count" style="margin:0 0 8px;"></p>
        <div id="ai-log" style="max-height:320px;overflow-y:auto;font-size:0.82rem;font-family:monospace;background:var(--bg);border:1px solid var(--card-border);border-radius:8px;padding:10px;"></div>
      </div>
    </div>
  </div>
</div>

<div id="battle-card"></div>
<div id="replace-popup" class="replace-popup"></div>
<script>
let deckId = {json.dumps(deck_id)};
let brew = {json.dumps(brew_state)};
let collection = [];
let themeLabelToIds = {{}};
let themeRequestId = 0;

const errorBox = document.getElementById('error-box');
function showError(message) {{ errorBox.textContent = message; errorBox.style.display = 'block'; }}

document.getElementById('deck-name').value = brew.deck_name;
document.getElementById('deck-format').value = brew.format;
document.getElementById('target-format').value = brew.target_format;
document.getElementById('intended-bracket').value = brew.intended_bracket || '';
document.getElementById('mix-lands').value = brew.mix_targets.Lands;
document.getElementById('mix-ramp').value = brew.mix_targets.Ramp;
document.getElementById('mix-draw').value = brew.mix_targets.Draw;
document.getElementById('mix-interaction').value = brew.mix_targets.Interaction;
document.getElementById('theme-input').value = brew.preferred_theme_label || '';

function targetSize() {{ return brew.format === 'commander' ? 100 : 60; }}

function updateSynergyReadout() {{
  const t = brew.mix_targets;
  const synergy = Math.max(0, 100 - (t.Lands + t.Ramp + t.Draw + t.Interaction));
  document.getElementById('mix-synergy-readout').textContent = `Synergy (remainder): ${{synergy}}`;
}}
updateSynergyReadout();

function updateFormatUI() {{
  const isCommander = brew.format === 'commander';
  document.getElementById('target-format-field').style.display = isCommander ? 'none' : 'flex';
  document.getElementById('intended-bracket-field').style.display = isCommander ? 'flex' : 'none';
  document.getElementById('mix-targets-field').style.display = isCommander ? 'block' : 'none';
}}
updateFormatUI();
loadThemeOptions();

document.getElementById('deck-format').addEventListener('change', (e) => {{
  brew.format = e.target.value;
  if (brew.format !== 'commander') brew.commander = null;
  updateFormatUI();
  renderAll();
  loadThemeOptions();
}});
document.getElementById('target-format').addEventListener('change', (e) => {{ brew.target_format = e.target.value; }});
document.getElementById('intended-bracket').addEventListener('change', (e) => {{ brew.intended_bracket = e.target.value; }});
document.getElementById('deck-name').addEventListener('input', (e) => {{ brew.deck_name = e.target.value; }});
['lands', 'ramp', 'draw', 'interaction'].forEach(role => {{
  document.getElementById('mix-' + role).addEventListener('input', (e) => {{
    const key = role.charAt(0).toUpperCase() + role.slice(1);
    brew.mix_targets[key] = Math.max(0, parseInt(e.target.value, 10) || 0);
    updateSynergyReadout();
  }});
}});

function normalizeName(name) {{ return name.trim().toLowerCase(); }}

// Same direct Scryfall CDN hotlink pattern as scryfall_image_url() in
// brewlist_core.py -- no API call needed, just the card's own Scryfall ID.
function scryfallImg(scryfallId, size) {{
  if (!scryfallId) return null;
  return `https://cards.scryfall.io/${{size}}/front/${{scryfallId[0]}}/${{scryfallId[1]}}/${{scryfallId}}.jpg`;
}}

// Same convention as the compare report's card tiles: one icon per color-
// identity letter (not a full parse of the mana cost string's pip counts).
const WUBRG = ['W', 'U', 'B', 'R', 'G'];
function colorIconsHtml(colorIdentity) {{
  const colors = WUBRG.filter(c => (colorIdentity || []).includes(c));
  if (!colors.length) return '';
  return '<span class="color-icons">' + colors.map(c =>
    `<img class="mana-icon" src="https://svgs.scryfall.io/card-symbols/${{c}}.svg" alt="${{c}}" loading="lazy">`
  ).join('') + '</span>';
}}

// Full ordered mana-cost pip sequence (not just color identity) -- hidden
// in Cards view (colorIconsHtml above already shows color there), shown
// instead of it in Compact view, same convention as the compare report's
// card tiles (see mana_cost_pips_html in brewlist_core.py's render_html).
function manaCostPipsHtml(manaCost) {{
  const pips = parseManaPips(manaCost);
  if (!pips.length) return '';
  return '<span class="mana-cost-pips">' + pips.map(p =>
    `<img class="mana-icon" src="${{manaPipSymbolUrl(p)}}" alt="${{p}}" loading="lazy">`
  ).join('') + '</span>';
}}

function thumbHtml(scryfallId, cssClass) {{
  const small = scryfallImg(scryfallId, 'small');
  const full = scryfallImg(scryfallId, 'normal');
  if (!small) return `<div class="${{cssClass}}"></div>`;
  return `<img class="${{cssClass}}" src="${{small}}" data-full="${{full}}" alt="" loading="lazy" decoding="async">`;
}}

const hoverPreview = document.getElementById('hover-preview');
function setupHoverPreview(container) {{
  container.addEventListener('mousemove', (e) => {{
    // Only the card image itself triggers the preview -- hovering the
    // name/meta text next to it shouldn't. The one exception is Compact
    // view, where .card-thumb is display:none (so the mouse can never
    // actually be "over" it -- hidden elements don't receive pointer
    // events at all) and there's nothing else to hover, so the row/tile
    // container's own data-full (a duplicate of the hidden image's) is
    // used there instead.
    const thumb = e.target.closest('.card-thumb[data-full]');
    let full = thumb ? thumb.dataset.full : null;
    if (!full) {{
      const row = e.target.closest('[data-full]');
      const rowThumb = row && row.querySelector('.card-thumb');
      if (row && rowThumb && getComputedStyle(rowThumb).display === 'none') {{
        full = row.dataset.full;
      }}
    }}
    if (!full) {{ hoverPreview.classList.remove('show'); return; }}
    hoverPreview.src = full;
    hoverPreview.classList.add('show');
    const pad = 18, w = 240, h = Math.round(w * 1.4);
    let x = e.clientX + pad;
    let y = e.clientY + pad;
    if (x + w > window.innerWidth) x = e.clientX - w - pad;
    if (y + h > window.innerHeight) y = window.innerHeight - h - pad;
    hoverPreview.style.left = x + 'px';
    hoverPreview.style.top = Math.max(0, y) + 'px';
  }});
  container.addEventListener('mouseleave', () => hoverPreview.classList.remove('show'));
}}
setupHoverPreview(document.getElementById('collection-grid'));
setupHoverPreview(document.getElementById('commander-slot-wrap'));
setupHoverPreview(document.getElementById('deck-list'));
setupHoverPreview(document.getElementById('suggestions-panel'));
setupHoverPreview(document.getElementById('sample-hand-cards'));
setupHoverPreview(document.getElementById('replace-popup'));

document.getElementById('draw-hand-btn').addEventListener('click', drawSampleHand);

const viewDensityBtns = document.querySelectorAll('#view-density-toggle .seg-btn');
viewDensityBtns.forEach(btn => {{
  btn.addEventListener('click', () => {{
    viewDensityBtns.forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    document.body.classList.toggle('compact', btn.dataset.value === 'compact');
  }});
}});

function findCard(name) {{
  const nm = normalizeName(name);
  return brew.cards.find(c => normalizeName(c.name) === nm);
}}

function isCommanderEligible(card) {{
  return card.type_line.includes('Legendary') && card.type_line.includes('Creature');
}}

function setCommander(card) {{
  brew.commander = {{
    name: card.name, scryfall_id: card.scryfall_id, type_line: card.type_line, color_identity: card.color_identity,
    cmc: card.cmc, mana_cost: card.mana_cost, category: card.category, quantity: 1,
    set_code: card.set_code, collector_number: card.collector_number,
  }};
  renderAll();
  loadThemeOptions();
}}

function clearCommander() {{ brew.commander = null; renderAll(); loadThemeOptions(); }}

function addCard(card) {{
  const existing = findCard(card.name);
  if (brew.format === 'commander') {{
    if (existing) return;
    brew.cards.push({{
      name: card.name, quantity: 1, scryfall_id: card.scryfall_id, cmc: card.cmc, mana_cost: card.mana_cost,
      type_line: card.type_line, color_identity: card.color_identity, category: card.category,
      set_code: card.set_code, collector_number: card.collector_number,
    }});
  }} else {{
    const ownedQty = card.quantity || 4;
    if (existing) {{
      if (existing.quantity < Math.min(4, ownedQty)) existing.quantity += 1;
    }} else {{
      brew.cards.push({{
        name: card.name, quantity: 1, scryfall_id: card.scryfall_id, cmc: card.cmc, mana_cost: card.mana_cost,
        type_line: card.type_line, color_identity: card.color_identity, category: card.category,
        set_code: card.set_code, collector_number: card.collector_number,
      }});
    }}
  }}
  renderAll();
}}

function removeCard(name) {{
  brew.cards = brew.cards.filter(c => normalizeName(c.name) !== normalizeName(name));
  renderAll();
}}

document.getElementById('clear-cards-btn').addEventListener('click', () => {{
  if (!brew.cards.length) return;
  if (!confirm(`Remove all ${{brew.cards.length}} card(s) from this deck? Your commander is kept -- use its own Clear button to remove that separately.`)) return;
  brew.cards = [];
  renderAll();
  loadThemeOptions();
}});

function adjustQty(name, delta) {{
  const c = findCard(name);
  if (!c) return;
  c.quantity = Math.max(1, c.quantity + delta);
  if (c.quantity <= 0) removeCard(name);
  renderAll();
}}

function legalityFor(card) {{
  // MTGJSON omits a format entirely when a card was never printed into
  // that format's pool (e.g. Sol Ring has no "standard" key at all)
  // rather than saying "Not Legal" -- so a missing entry means "not
  // legal" for a target constructed format. Commander is the exception:
  // it's explicitly "Legal" for essentially every real paper card, so a
  // missing entry there just means untracked, not banned.
  if (brew.format === 'commander') return (card.legalities || {{}})['commander'] || null;
  return (card.legalities || {{}})[brew.target_format] || 'Not Legal';
}}

// Appends a "(count)" to each type-filter option based on the loaded
// collection, so e.g. "Planeswalkers (0)" is visible before clicking into
// an empty grid, instead of only "Planeswalkers" with no hint it's empty.
function annotateFilterCounts() {{
  const counts = {{}};
  let commanderCount = 0;
  collection.forEach(card => {{
    counts[card.category] = (counts[card.category] || 0) + 1;
    if (isCommanderEligible(card)) commanderCount++;
  }});
  document.querySelectorAll('#filter-category option[value]').forEach(opt => {{
    if (!opt.value) return;
    const n = opt.value === 'Commander' ? commanderCount : (counts[opt.value] || 0);
    opt.textContent = `${{opt.value}} (${{n}})`;
  }});
}}

// Populates the "Preferred theme" picker with every curated named EDH
// archetype (Voltron, Reanimator, ... -- see CURATED_THEMES in
// deck_builder.py) that has enough owned, legal, color-correct candidates
// to be worth picking (see list_theme_options) -- recomputed whenever the
// commander or format changes, since that's what the color/legality
// filter behind it depends on. A hand-rolled dropdown (not a native
// <datalist>) -- datalist's suggestion popup is positioned by the browser
// itself and, in this embedded layout, was rendering nowhere near the
// input; a plain absolutely-positioned div anchored to .theme-picker
// (position:relative) is fully within our own control instead.
let themeOptionsList = [];
function loadThemeOptions() {{
  const requestId = ++themeRequestId;
  fetch('/builder/themes', {{
    method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ format: brew.format, target_format: brew.target_format, commander: brew.commander, cards: brew.cards, excluded_set_codes: brew.excluded_set_codes }}),
  }})
    .then(r => r.json())
    .then(data => {{
      // Guards against a race where this call's response arrives after a
      // newer one (e.g. clicking a different commander quickly) -- without
      // this, a slow/broader-scoped response landing last could silently
      // overwrite the correct, more-recent theme list.
      if (requestId !== themeRequestId) return;
      if (data.error) return;
      themeOptionsList = data.themes || [];
      themeLabelToIds = {{}};
      themeOptionsList.forEach(t => {{ themeLabelToIds[t.label] = t.tag_ids; }});
      // A previously-chosen theme that no longer has any owned candidates
      // in the current colors (e.g. after switching commanders) can't be
      // resolved back to its tag_ids -- drop it rather than silently
      // sending a stale/meaningless selection with the next Suggest
      // request.
      if (brew.preferred_theme_label && !(brew.preferred_theme_label in themeLabelToIds)) {{
        brew.preferred_theme_tag_ids = [];
        brew.preferred_theme_label = '';
        document.getElementById('theme-input').value = '';
      }}
    }})
    .catch(() => {{}});
}}

const themeInput = document.getElementById('theme-input');
const themeDropdown = document.getElementById('theme-dropdown');
function renderThemeDropdown() {{
  const filter = themeInput.value.trim().toLowerCase();
  const matches = filter ? themeOptionsList.filter(t => t.label.toLowerCase().includes(filter)) : themeOptionsList;
  if (!matches.length) {{
    themeDropdown.innerHTML = '<div class="theme-dropdown-empty">No matching themes among your owned cards</div>';
  }} else {{
    themeDropdown.innerHTML = matches.map(t =>
      `<div class="theme-dropdown-item" data-label="${{t.label.replace(/"/g, '&quot;')}}"><span>${{t.label}}</span><span class="count">${{t.count}} owned</span></div>`
    ).join('');
  }}
  themeDropdown.classList.add('show');
}}
themeInput.addEventListener('focus', renderThemeDropdown);
themeInput.addEventListener('input', (e) => {{
  const label = e.target.value.trim();
  if (label in themeLabelToIds) {{
    brew.preferred_theme_tag_ids = themeLabelToIds[label];
    brew.preferred_theme_label = label;
  }} else {{
    brew.preferred_theme_tag_ids = [];
    brew.preferred_theme_label = '';
  }}
  renderThemeDropdown();
}});
themeDropdown.addEventListener('mousedown', (e) => {{
  // mousedown (not click) fires before the input's blur, so the
  // dropdown is still in the DOM to read from when this runs.
  const item = e.target.closest('.theme-dropdown-item[data-label]');
  if (!item) return;
  const label = item.dataset.label;
  themeInput.value = label;
  brew.preferred_theme_tag_ids = themeLabelToIds[label] || [];
  brew.preferred_theme_label = label;
  themeDropdown.classList.remove('show');
}});
themeInput.addEventListener('blur', () => {{ setTimeout(() => themeDropdown.classList.remove('show'), 150); }});

// Set filtering: two independent multiselect popups sharing the same
// owned-set list and the same UI pattern (Select All/Clear All/Cancel/OK,
// staged -- nothing applies until OK). "Set Selection" restricts which
// sets Suggest/Preferred-theme/Replace pull owned candidates from (see
// excluded_set_codes threaded through deck_builder.py's
// _filter_candidates); the grid's "Set" button separately restricts what
// the manual collection browser shows -- deliberately independent state,
// since browsing everything while restricting the autobuilder (or vice
// versa) is a reasonable thing to want. allSetOptions itself is loaded
// once at page init, independent of the brew.
let allSetOptions = [];
function loadSetOptions() {{
  fetch('/builder/sets')
    .then(r => r.json())
    .then(data => {{
      if (data.error) return;
      allSetOptions = data.sets || [];
      setSelectionPopupCtl.refreshLabel();
      gridSetFilterPopupCtl.refreshLabel();
    }})
    .catch(() => {{}});
}}

// Returns {{refreshLabel}}. `opts`: label (button text prefix), getExcluded/
// setExcluded (read/write the excluded-codes list this instance owns),
// onApply (called after OK commits a new selection).
function setupSetFilterPopup(btn, popup, opts) {{
  function refreshLabel() {{
    const excluded = new Set(opts.getExcluded());
    const total = allSetOptions.length;
    const includedCount = allSetOptions.filter(s => !excluded.has(s.set_code)).length;
    btn.textContent = (total === 0 || includedCount === total) ? opts.label : `${{opts.label}} (${{includedCount}}/${{total}})`;
  }}
  function render() {{
    const excluded = new Set(opts.getExcluded());
    const rows = allSetOptions.map(s => {{
      const year = s.release_date ? s.release_date.slice(0, 4) : '?';
      const ownedFrac = s.total_in_set ? `${{s.count}} / ${{s.total_in_set}}` : `${{s.count}}`;
      return `<label><input type="checkbox" value="${{s.set_code}}"${{excluded.has(s.set_code) ? '' : ' checked'}}> ${{s.set_name}} (${{year}}) &middot; ${{ownedFrac}}</label>`;
    }}).join('');
    popup.innerHTML = `
      <div class="set-selection-quick">
        <button type="button" class="btn ghost small" data-action="all">Select All</button>
        <button type="button" class="btn ghost small" data-action="none">Clear All</button>
      </div>
      <div class="set-selection-list">${{rows || '<div class="hint" style="margin:0;">No sets found in your collection.</div>'}}</div>
      <div class="set-selection-actions">
        <button type="button" class="btn ghost small" data-action="cancel">Cancel</button>
        <button type="button" class="btn small" data-action="ok">OK</button>
      </div>
    `;
    popup.querySelector('[data-action="all"]').addEventListener('click', () => {{
      popup.querySelectorAll('input[type=checkbox]').forEach(cb => {{ cb.checked = true; }});
    }});
    popup.querySelector('[data-action="none"]').addEventListener('click', () => {{
      popup.querySelectorAll('input[type=checkbox]').forEach(cb => {{ cb.checked = false; }});
    }});
    popup.querySelector('[data-action="cancel"]').addEventListener('click', () => {{
      popup.classList.remove('show');
    }});
    popup.querySelector('[data-action="ok"]').addEventListener('click', () => {{
      const excludedCodes = Array.from(popup.querySelectorAll('input[type=checkbox]'))
        .filter(cb => !cb.checked).map(cb => cb.value);
      opts.setExcluded(excludedCodes);
      refreshLabel();
      popup.classList.remove('show');
      opts.onApply();
    }});
  }}
  btn.addEventListener('click', () => {{
    if (popup.classList.contains('show')) {{ popup.classList.remove('show'); return; }}
    render();
    popup.classList.add('show');
  }});
  document.addEventListener('click', (e) => {{
    if (popup.classList.contains('show') && !popup.contains(e.target) && e.target !== btn) {{
      popup.classList.remove('show');
    }}
  }});
  document.addEventListener('keydown', (e) => {{ if (e.key === 'Escape') popup.classList.remove('show'); }});
  return {{ refreshLabel }};
}}

const setSelectionPopupCtl = setupSetFilterPopup(
  document.getElementById('set-selection-btn'), document.getElementById('set-selection-popup'), {{
    label: 'Set Selection',
    getExcluded: () => brew.excluded_set_codes || [],
    setExcluded: (codes) => {{ brew.excluded_set_codes = codes; }},
    onApply: () => loadThemeOptions(),
  }}
);

let gridExcludedSetCodes = [];
const gridSetFilterPopupCtl = setupSetFilterPopup(
  document.getElementById('grid-set-filter-btn'), document.getElementById('grid-set-filter-popup'), {{
    label: 'Set',
    getExcluded: () => gridExcludedSetCodes,
    setExcluded: (codes) => {{ gridExcludedSetCodes = codes; }},
    onApply: () => renderGrid(),
  }}
);

loadSetOptions();

// '' -> no filter. 'C' -> exactly colorless. With `exact` off (the
// default): a single WUBRG letter matches any card whose identity
// contains it ("uses this color"); two or more letters (a guild/shard/
// wedge/etc name) match by *subset* ("legal to include in a deck of
// these colors"), same rule the Suggest engine uses for a commander's
// color identity -- so e.g. "Sultai (BGU)" also surfaces mono-color and
// colorless cards, not just true 3-color ones. With `exact` on (the
// "Exact colors" checkbox), a card's identity must equal the filter's
// colors precisely -- e.g. "Grixis (UBR)" + exact finds only true
// 3-color UBR cards, which is what actually narrows down a Grixis-
// *only* commander instead of drowning it in every mono/2-color UBR
// card that also happens to fit inside Grixis.
function matchesColorFilter(colorIdentity, filterValue, exact) {{
  if (!filterValue) return true;
  if (filterValue === 'C') return colorIdentity.length === 0;
  if (exact) {{
    const filterColors = filterValue.split('');
    return colorIdentity.length === filterColors.length && filterColors.every(c => colorIdentity.includes(c));
  }}
  if (filterValue.length === 1) return colorIdentity.includes(filterValue);
  return colorIdentity.every(c => filterValue.includes(c));
}}

function renderGrid() {{
  const search = document.getElementById('search').value.trim().toLowerCase();
  const category = document.getElementById('filter-category').value;
  const color = document.getElementById('filter-color').value;
  const exactColor = document.getElementById('filter-color-exact').checked;
  const grid = document.getElementById('collection-grid');
  grid.innerHTML = '';
  const frag = document.createDocumentFragment();
  collection.forEach(card => {{
    if (search && !card.name.toLowerCase().includes(search)) return;
    if (category === 'Commander' && !isCommanderEligible(card)) return;
    if (category && category !== 'Commander' && card.category !== category) return;
    if (!matchesColorFilter(card.color_identity, color, exactColor)) return;
    if (gridExcludedSetCodes.length && gridExcludedSetCodes.includes(card.set_code)) return;

    const tile = document.createElement('div');
    tile.className = 'builder-tile';
    tile.dataset.full = scryfallImg(card.scryfall_id, 'normal') || '';
    const legality = legalityFor(card);
    const warnHtml = (legality && legality !== 'Legal') ? `<div class="warn">&#9888; ${{legality}} in ${{brew.format === 'commander' ? 'Commander' : brew.target_format}}</div>` : '';
    const inDeck = findCard(card.name);
    tile.innerHTML = `
      ${{thumbHtml(card.scryfall_id, 'card-thumb')}}
      <div class="tile-body">
        <div class="name"><span class="name-text">${{card.name}}</span></div>
        <div class="meta">${{card.type_line || 'Unknown type'}} &middot; CMC ${{card.cmc}} &middot; own ${{card.quantity}}</div>
        ${{warnHtml}}
      </div>
      <div class="tile-corner-actions">
        ${{manaCostPipsHtml(card.mana_cost)}}
        <div class="tile-icon-stack"></div>
      </div>
    `;
    const actions = tile.querySelector('.tile-icon-stack');
    if (brew.format === 'commander' && isCommanderEligible(card)) {{
      const cmdBtn = document.createElement('button');
      cmdBtn.type = 'button'; cmdBtn.className = 'btn ghost tile-icon-btn';
      cmdBtn.textContent = '\\u2605';
      cmdBtn.title = 'Set as Commander';
      cmdBtn.addEventListener('click', () => setCommander(card));
      actions.appendChild(cmdBtn);
    }}
    const addBtn = document.createElement('button');
    addBtn.type = 'button'; addBtn.className = 'btn ghost tile-icon-btn';
    addBtn.textContent = (brew.format === 'commander' && inDeck) ? '\\u2713' : '+';
    addBtn.title = (brew.format === 'commander' && inDeck) ? 'Already in deck' : 'Add to deck';
    addBtn.disabled = brew.format === 'commander' && !!inDeck;
    addBtn.addEventListener('click', () => addCard(card));
    actions.appendChild(addBtn);
    frag.appendChild(tile);
  }});
  grid.appendChild(frag);
}}

function updateCommanderBackdrop() {{
  const bg = (brew.format === 'commander' && brew.commander) ? scryfallImg(brew.commander.scryfall_id, 'large') : null;
  if (bg) {{
    document.body.style.setProperty('--commander-bg-url', `url('${{bg}}')`);
  }} else {{
    document.body.style.removeProperty('--commander-bg-url');
  }}
}}

function renderCommanderSlot() {{
  updateCommanderBackdrop();
  const wrap = document.getElementById('commander-slot-wrap');
  if (brew.format !== 'commander') {{ wrap.innerHTML = ''; return; }}
  if (!brew.commander) {{
    wrap.innerHTML = '<div class="commander-slot"><span class="hint" style="margin:0;">No commander chosen -- click &#9733; Commander on an eligible card.</span></div>';
    return;
  }}
  wrap.innerHTML = `<div class="commander-slot" data-full="${{scryfallImg(brew.commander.scryfall_id, 'normal') || ''}}"><span class="commander-info">${{thumbHtml(brew.commander.scryfall_id, 'card-thumb small')}}<span><b>Commander:</b> ${{brew.commander.name}} ${{colorIconsHtml(brew.commander.color_identity)}}</span></span></div>`;
  wrap.querySelector('.commander-slot').appendChild(Object.assign(document.createElement('button'), {{
    type: 'button', className: 'btn ghost small', textContent: 'Clear',
    onclick: clearCommander,
  }}));
}}

function renderDeckList() {{
  const list = document.getElementById('deck-list');
  list.innerHTML = '';
  document.getElementById('clear-cards-btn').style.display = brew.cards.length ? '' : 'none';
  const groups = {{}};
  brew.cards.forEach(c => {{
    const g = c.category || 'Other';
    (groups[g] = groups[g] || []).push(c);
  }});
  Object.keys(groups).sort().forEach(g => {{
    const div = document.createElement('div');
    div.className = 'deck-group';
    div.innerHTML = `<h4>${{g}} (${{groups[g].reduce((s, c) => s + c.quantity, 0)}})</h4>`;
    groups[g].sort((a, b) => a.name.localeCompare(b.name)).forEach(c => {{
      const row = document.createElement('div');
      row.className = 'deck-row';
      row.dataset.full = scryfallImg(c.scryfall_id, 'normal') || '';
      row.innerHTML = `<span class="row-name">${{thumbHtml(c.scryfall_id, 'card-thumb small')}}<span>${{c.name}}</span>${{colorIconsHtml(c.color_identity)}}</span>`;
      const controls = document.createElement('div');
      controls.className = 'qty-controls';
      if (brew.format !== 'commander') {{
        const minus = Object.assign(document.createElement('button'), {{ className: 'btn ghost small qty-btn', textContent: '\\u2212', onclick: () => adjustQty(c.name, -1) }});
        const qty = Object.assign(document.createElement('span'), {{ textContent: c.quantity }});
        const plus = Object.assign(document.createElement('button'), {{ className: 'btn ghost small qty-btn', textContent: '+', onclick: () => adjustQty(c.name, 1) }});
        controls.append(minus, qty, plus);
      }}
      const replaceBtn = Object.assign(document.createElement('button'), {{
        className: 'btn ghost tile-icon-btn replace-btn', textContent: '\\u21c4', title: 'Suggest a replacement',
        onclick: (e) => openReplacePopup(e.currentTarget, c),
      }});
      const removeBtn = Object.assign(document.createElement('button'), {{ className: 'btn danger small', textContent: 'Remove', onclick: () => removeCard(c.name) }});
      controls.append(replaceBtn, removeBtn);
      row.appendChild(controls);
      div.appendChild(row);
    }});
    list.appendChild(div);
  }});
}}

// A single shared popup (like #hover-preview/#battle-card), not one per
// deck-row -- position:fixed so it escapes #deck-list's own
// overflow-y:auto (which would otherwise clip it, same issue the
// Analyze modal's badge tooltips had), repositioned/repopulated on each
// swap-button click instead.
const replacePopup = document.getElementById('replace-popup');
function closeReplacePopup() {{ replacePopup.classList.remove('show'); }}
function openReplacePopup(btn, card) {{
  const rect = btn.getBoundingClientRect();
  replacePopup.innerHTML = '<h5>Suggested replacements</h5><div class="hint">Loading&hellip;</div>';
  replacePopup.style.top = (rect.bottom + 6) + 'px';
  replacePopup.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - 268)) + 'px';
  replacePopup.classList.add('show');
  fetch('/builder/replace', {{
    method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{
      card_name: card.name, category: card.category, cards: brew.cards,
      commander: brew.commander, format: brew.format, target_format: brew.target_format,
      excluded_set_codes: brew.excluded_set_codes,
    }}),
  }})
    .then(r => r.json())
    .then(data => {{
      if (!replacePopup.classList.contains('show')) return; // closed while the request was in flight
      if (data.error) {{ replacePopup.innerHTML = `<h5>Suggested replacements</h5><div class="hint">${{data.error}}</div>`; return; }}
      if (!data.replacements.length) {{
        replacePopup.innerHTML = '<h5>Suggested replacements</h5><div class="hint">Nothing else owned fills this role.</div>';
        return;
      }}
      replacePopup.innerHTML = '<h5>Suggested replacements</h5>' +
        data.replacements.map((r, i) => `
          <div class="replace-item" data-i="${{i}}" data-full="${{scryfallImg(r.scryfall_id, 'normal') || ''}}">
            ${{thumbHtml(r.scryfall_id, 'card-thumb small')}}
            <div><div>${{r.name}}</div><div class="reason">${{r.reason}}</div></div>
          </div>`).join('');
      replacePopup.querySelectorAll('.replace-item').forEach(item => {{
        const rep = data.replacements[Number(item.dataset.i)];
        item.addEventListener('click', () => {{
          removeCard(card.name);
          addCard(rep);
          closeReplacePopup();
        }});
      }});
    }})
    .catch(() => {{ replacePopup.innerHTML = '<h5>Suggested replacements</h5><div class="hint">Could not reach the server.</div>'; }});
}}
document.addEventListener('click', (e) => {{
  if (replacePopup.classList.contains('show') && !replacePopup.contains(e.target) && !e.target.closest('.replace-btn')) closeReplacePopup();
}});
document.addEventListener('keydown', (e) => {{ if (e.key === 'Escape') closeReplacePopup(); }});

function renderStats() {{
  // Matches WotC's own 99-library + 1-commander = 100-card rule -- the
  // commander counts toward the displayed total (so a full deck reads
  // "100/100"), even though it's tracked separately from brew.cards.
  const total = brew.cards.reduce((s, c) => s + c.quantity, 0) + (brew.format === 'commander' && brew.commander ? 1 : 0);
  const lands = brew.cards.filter(c => (c.category || '').includes('Land')).reduce((s, c) => s + c.quantity, 0);
  const avgCmc = brew.cards.length
    ? (brew.cards.reduce((s, c) => s + (c.cmc || 0) * c.quantity, 0) / Math.max(1, total)).toFixed(2)
    : '0.00';
  document.getElementById('deck-stats').innerHTML =
    `<span><b>${{total}}</b> / ${{targetSize()}} cards</span><span><b>${{lands}}</b> lands</span><span>avg CMC <b>${{avgCmc}}</b></span>`;
}}

function parseManaPips(manaCost) {{
  const matches = (manaCost || '').match(/\\{{[^}}]+\\}}/g) || [];
  return matches.map(m => m.slice(1, -1));
}}

function manaPipSymbolUrl(token) {{
  return `https://svgs.scryfall.io/card-symbols/${{token.replace('/', '').toUpperCase()}}.svg`;
}}

function libraryCards() {{
  // The commander never gets drawn/shuffled (starts in the command zone) --
  // curve/pips/sample-hand should reflect what's actually in the 99/60-card
  // library, same reasoning as brewlist_core.py's render_html.
  return brew.cards;
}}

function manaCurveData(cards) {{
  const buckets = {{}};
  for (let i = 0; i <= 7; i++) buckets[i] = {{ permanents: 0, spells: 0 }};
  cards.forEach(c => {{
    if (c.category === 'Lands' || c.category === 'Basic Lands') return;
    const b = Math.min(Math.round(c.cmc || 0), 7);
    if (c.category === 'Instants' || c.category === 'Sorceries') buckets[b].spells += c.quantity;
    else buckets[b].permanents += c.quantity;
  }});
  return Object.keys(buckets).map(k => ({{
    label: k === '7' ? '7+' : k, permanents: buckets[k].permanents, spells: buckets[k].spells,
  }}));
}}

function colorPipCounts(cards) {{
  const counts = {{ W: 0, U: 0, B: 0, R: 0, G: 0 }};
  cards.forEach(c => {{
    if (c.category === 'Lands' || c.category === 'Basic Lands') return;
    parseManaPips(c.mana_cost).forEach(p => {{
      WUBRG.forEach(col => {{ if (p.includes(col)) counts[col] += c.quantity; }});
    }});
  }});
  return counts;
}}

function renderDeckAnalysis() {{
  const cards = libraryCards();
  const total = cards.reduce((s, c) => s + c.quantity, 0);
  if (!total) {{
    document.getElementById('analysis-stat-line').textContent = 'Add some cards first.';
    document.getElementById('curve-chart').innerHTML = '';
    document.getElementById('color-breakdown').innerHTML = '';
    document.getElementById('sample-hand-stat').textContent = '';
    document.getElementById('sample-hand-cards').innerHTML = '';
    return;
  }}

  const allCmcs = cards.flatMap(c => Array(c.quantity).fill(c.cmc || 0));
  const avgCmc = allCmcs.reduce((s, v) => s + v, 0) / allCmcs.length;
  document.getElementById('analysis-stat-line').textContent = `Average mana value: ${{avgCmc.toFixed(2)}}`;

  const curve = manaCurveData(cards);
  const maxCount = Math.max(1, ...curve.map(b => b.permanents + b.spells));
  document.getElementById('curve-chart').innerHTML = curve.map(b => `
    <div class="curve-col" title="Mana value ${{b.label}}: ${{b.permanents + b.spells}} card(s)">
      <div class="curve-bar-wrap">
        <div class="curve-bar spells" style="height:${{b.spells / maxCount * 100}}%"></div>
        <div class="curve-bar permanents" style="height:${{b.permanents / maxCount * 100}}%"></div>
      </div>
      <div class="curve-label">${{b.label}}</div>
    </div>`).join('');

  const pips = colorPipCounts(cards);
  const totalPips = Math.max(1, Object.values(pips).reduce((s, v) => s + v, 0));
  document.getElementById('color-breakdown').innerHTML = WUBRG.map(c => `
    <div class="color-row">
      <img class="mana-icon" src="https://svgs.scryfall.io/card-symbols/${{c}}.svg" alt="${{c}}" loading="lazy">
      <div class="color-bar-track" title="${{pips[c]}} pip(s) &middot; ${{Math.round(pips[c] / totalPips * 100)}}% of all symbols">
        <div class="color-bar" style="width:${{pips[c] / totalPips * 100}}%"></div>
      </div>
    </div>`).join('');

  const lands = cards.filter(c => c.category === 'Lands' || c.category === 'Basic Lands').reduce((s, c) => s + c.quantity, 0);
  const avgLandsInHand = 7 * lands / total;
  document.getElementById('sample-hand-stat').textContent = `Average number of lands in opening hand: ${{avgLandsInHand.toFixed(2)}}`;
}}

function drawSampleHand() {{
  const pool = [];
  libraryCards().forEach(c => {{ for (let i = 0; i < c.quantity; i++) pool.push(c); }});
  for (let i = pool.length - 1; i > 0; i--) {{
    const j = Math.floor(Math.random() * (i + 1));
    const tmp = pool[i]; pool[i] = pool[j]; pool[j] = tmp;
  }}
  document.getElementById('sample-hand-cards').innerHTML =
    pool.slice(0, 7).map(c => thumbHtml(c.scryfall_id, 'sample-hand-thumb card-thumb')).join('');
}}

function renderAll() {{
  renderGrid();
  renderCommanderSlot();
  renderDeckList();
  renderStats();
}}

fetch('/builder/collection-data')
  .then(r => r.json())
  .then(data => {{
    if (data.error) {{ showError(data.error); return; }}
    collection = data.cards;
    annotateFilterCounts();
    renderAll();
  }})
  .catch(() => showError('Could not load your collection.'));

document.getElementById('search').addEventListener('input', renderGrid);
document.getElementById('filter-category').addEventListener('change', renderGrid);
document.getElementById('filter-color').addEventListener('change', renderGrid);
document.getElementById('filter-color-exact').addEventListener('change', renderGrid);

const saveBtn = document.getElementById('save-btn');
const saveLabel = document.getElementById('save-label');
saveBtn.addEventListener('click', () => {{
  saveBtn.disabled = true;
  fetch('/builder/save', {{
    method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ deck_id: deckId, ...brew }}),
  }})
    .then(r => r.json())
    .then(data => {{
      if (data.error) {{ showError(data.error); return; }}
      deckId = data.deck_id;
      window.history.replaceState(null, '', '/builder?id=' + encodeURIComponent(deckId));
      saveLabel.textContent = 'Saved.';
      saveLabel.style.display = 'block';
      setTimeout(() => {{ saveLabel.style.display = 'none'; }}, 2000);
    }})
    .catch(() => showError('Could not reach the server.'))
    .finally(() => {{ saveBtn.disabled = false; }});
}});

const suggestBtn = document.getElementById('suggest-btn');
suggestBtn.addEventListener('click', () => {{
  suggestBtn.disabled = true;
  fetch('/builder/suggest', {{
    method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{
      cards: brew.cards, commander: brew.commander, format: brew.format, target_format: brew.target_format,
      mix_targets: brew.mix_targets, intended_bracket: brew.intended_bracket,
      preferred_theme_tag_ids: brew.preferred_theme_tag_ids, preferred_theme_label: brew.preferred_theme_label,
      excluded_set_codes: brew.excluded_set_codes,
    }}),
  }})
    .then(r => r.json())
    .then(data => {{
      if (data.error) {{ showError(data.error); return; }}
      const panel = document.getElementById('suggestions-panel');
      panel.innerHTML = '<h4 style="font-size:0.8rem;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.03em;">Suggestions</h4>';
      if (!data.suggestions.length) {{
        panel.innerHTML += '<div class="hint" style="margin:0;">No suggestions -- your deck may already be full, or nothing left fits the color/legality filters.</div>';
        return;
      }}
      // Tracks which suggestions haven't been individually added yet, so
      // "+ Add All" reflects reality instead of a stale snapshot -- without
      // this, adding every suggestion one-by-one via its own "+ Add" left
      // "+ Add All" sitting there with the original count, and clicking it
      // would try to re-add cards already in the deck.
      let remaining = data.suggestions.slice();
      const addAllBtn = Object.assign(document.createElement('button'), {{
        className: 'btn ghost small', style: 'margin-bottom:8px;margin-right:8px;',
        onclick: () => {{ remaining.forEach(addCard); panel.innerHTML = ''; }},
      }});
      const dismissAllBtn = Object.assign(document.createElement('button'), {{
        className: 'btn ghost small', textContent: 'Dismiss All', style: 'margin-bottom:8px;',
        title: 'Discard this batch of suggestions without adding any of them',
        onclick: () => {{ panel.innerHTML = ''; }},
      }});
      function refreshAddAllBtn() {{
        addAllBtn.textContent = `+ Add All (${{remaining.length}})`;
        addAllBtn.style.display = remaining.length ? '' : 'none';
        dismissAllBtn.style.display = remaining.length ? '' : 'none';
      }}
      refreshAddAllBtn();
      panel.append(addAllBtn, dismissAllBtn);
      data.suggestions.forEach(s => {{
        const row = document.createElement('div');
        row.className = 'suggestion-row';
        row.dataset.full = scryfallImg(s.scryfall_id, 'normal') || '';
        row.innerHTML = `<span class="row-name">${{thumbHtml(s.scryfall_id, 'card-thumb small')}}<span>${{s.name}} ${{colorIconsHtml(s.color_identity)}}<div class="reason">${{s.reason}}</div></span></span>`;
        const addBtn = Object.assign(document.createElement('button'), {{
          className: 'btn ghost small', textContent: '+ Add',
          onclick: () => {{
            addCard(s);
            row.remove();
            remaining = remaining.filter(x => x !== s);
            refreshAddAllBtn();
          }},
        }});
        row.appendChild(addBtn);
        panel.appendChild(row);
      }});
    }})
    .catch(() => showError('Could not reach the server.'))
    .finally(() => {{ suggestBtn.disabled = false; }});
}});

const optimizeBtn = document.getElementById('optimize-btn');
optimizeBtn.addEventListener('click', () => {{
  optimizeBtn.disabled = true;
  fetch('/builder/optimize', {{
    method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{
      cards: brew.cards, commander: brew.commander, format: brew.format, target_format: brew.target_format,
      intended_bracket: brew.intended_bracket, excluded_set_codes: brew.excluded_set_codes,
    }}),
  }})
    .then(r => r.json())
    .then(data => {{
      if (data.error) {{ showError(data.error); return; }}
      const panel = document.getElementById('suggestions-panel');
      panel.innerHTML = '<h4 style="font-size:0.8rem;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.03em;">Combo Optimizations</h4>';
      if (!data.proposals.length) {{
        panel.innerHTML += '<div class="hint" style="margin:0;">No one-card-away combos found with a safe card to swap out -- your deck may already have every completable combo, or nothing left fits without bumping something already earning its slot.</div>';
        return;
      }}
      // Same "remaining reflects reality" pattern as Suggest's Add All --
      // applying one swap can change what a *later* swap in this same
      // batch would cut (two proposals could target the same filler
      // card), so re-fetching after Apply All is the honest way to
      // confirm the deck's actual state rather than trusting this batch.
      let remaining = data.proposals.slice();
      function applySwap(p) {{
        removeCard(p.remove.name);
        addCard(p.add);
        // Recorded onto the brew (not just applied) so Analyze Deck and
        // the full report can show a "Further Optimizations" trail below
        // the AI summary -- otherwise a deterministic Optimize swap made
        // after an AI build leaves the AI's own narrative silently
        // out of sync with what's actually in the deck now.
        brew.further_optimizations.push(`+ ${{p.add.name}} (${{p.reason}}) — cut ${{p.remove.name}}`);
      }}
      const applyAllBtn = Object.assign(document.createElement('button'), {{
        className: 'btn ghost small', style: 'margin-bottom:8px;margin-right:8px;',
        onclick: () => {{ remaining.forEach(applySwap); panel.innerHTML = ''; }},
      }});
      const dismissAllBtn = Object.assign(document.createElement('button'), {{
        className: 'btn ghost small', textContent: 'Dismiss All', style: 'margin-bottom:8px;',
        title: 'Discard these swap proposals without applying any of them',
        onclick: () => {{ panel.innerHTML = ''; }},
      }});
      function refreshApplyAllBtn() {{
        applyAllBtn.textContent = `Apply All (${{remaining.length}})`;
        applyAllBtn.style.display = remaining.length ? '' : 'none';
        dismissAllBtn.style.display = remaining.length ? '' : 'none';
      }}
      refreshApplyAllBtn();
      panel.append(applyAllBtn, dismissAllBtn);
      data.proposals.forEach(p => {{
        const row = document.createElement('div');
        row.className = 'suggestion-row';
        row.dataset.full = scryfallImg(p.add.scryfall_id, 'normal') || '';
        row.innerHTML = `<span class="row-name">${{thumbHtml(p.add.scryfall_id, 'card-thumb small')}}<span>+ ${{p.add.name}} ${{colorIconsHtml(p.add.color_identity)}}<div class="reason">${{p.reason}} &mdash; cuts ${{p.remove.name}}</div></span></span>`;
        const applyBtn = Object.assign(document.createElement('button'), {{
          className: 'btn ghost small', textContent: 'Apply',
          onclick: () => {{
            applySwap(p);
            row.remove();
            remaining = remaining.filter(x => x !== p);
            refreshApplyAllBtn();
          }},
        }});
        row.appendChild(applyBtn);
        panel.appendChild(row);
      }});
    }})
    .catch(() => showError('Could not reach the server.'))
    .finally(() => {{ optimizeBtn.disabled = false; }});
}});

// Unlike every other "reason" string in this page (all template-generated
// from a bounded set of phrases), AI-build log lines/reasons/summary are
// genuinely free-form model output -- escape before any innerHTML use.
function escapeHtml(s) {{
  const div = document.createElement('div');
  div.textContent = s == null ? '' : String(s);
  return div.innerHTML;
}}

const aiModal = document.getElementById('ai-modal');
const aiModalError = document.getElementById('ai-modal-error');
const aiModePhase = document.getElementById('ai-mode-phase');
const aiImportPhase = document.getElementById('ai-import-phase');
const aiSetupPhase = document.getElementById('ai-setup-phase');
const aiConfirmPhase = document.getElementById('ai-confirm-phase');
const aiProgressPhase = document.getElementById('ai-progress-phase');
const aiKeyInput = document.getElementById('ai-key-input');
const aiKeyStatus = document.getElementById('ai-key-status');
const aiKeyConfiguredNote = document.getElementById('ai-key-configured-note');
let aiMode = 'fresh';  // 'fresh' | 'improve' | 'import'

function closeAiModal() {{ aiModal.classList.remove('show'); }}
document.getElementById('ai-modal-close').addEventListener('click', closeAiModal);
aiModal.addEventListener('click', (e) => {{ if (e.target === aiModal) closeAiModal(); }});
document.addEventListener('keydown', (e) => {{ if (e.key === 'Escape' && aiModal.classList.contains('show')) closeAiModal(); }});

function showAiError(msg) {{ aiModalError.textContent = msg; aiModalError.style.display = 'block'; }}
function hideAiError() {{ aiModalError.style.display = 'none'; }}
function showAiPhase(phase) {{
  aiModePhase.style.display = phase === 'mode' ? 'block' : 'none';
  aiImportPhase.style.display = phase === 'import' ? 'block' : 'none';
  aiSetupPhase.style.display = phase === 'setup' ? 'block' : 'none';
  aiConfirmPhase.style.display = phase === 'confirm' ? 'block' : 'none';
  aiProgressPhase.style.display = phase === 'progress' ? 'block' : 'none';
}}

document.getElementById('ai-build-btn').addEventListener('click', () => {{
  hideAiError();
  aiKeyInput.value = '';
  aiKeyStatus.textContent = '';
  document.getElementById('ai-import-url-input').value = '';
  document.getElementById('ai-import-text-input').value = '';
  document.getElementById('ai-mode-improve-btn').style.display = brew.cards.length ? '' : 'none';
  aiModal.classList.add('show');
  showAiPhase('mode');
}});

function aiProceedAfterMode() {{
  if (aiMode !== 'import' && brew.format === 'commander' && !brew.commander) {{ closeAiModal(); showError('Choose a commander first.'); return; }}
  fetch('/ai/status')
    .then(r => r.json())
    .then(data => {{
      if (data.configured) {{
        aiKeyConfiguredNote.textContent = data.source === 'env'
          ? 'Using the ANTHROPIC_API_KEY environment variable.' : 'Using your saved API key.';
        const notes = {{
          fresh: 'This can take several minutes (up to 7) and makes many real API calls on your account. The result won’t be applied until you review and approve it.',
          improve: `Your existing ${{brew.cards.length}} card(s) are kept unless there’s a clear reason to change one. Can take several minutes and makes many real API calls on your account. Nothing changes until you review and approve it.`,
          import: 'May suggest cards you don’t own -- nothing is applied until you review it, and once applied you can use Save + View full report to see what’s already owned vs. what to buy. Can take several minutes and makes many real API calls on your account.',
        }};
        document.getElementById('ai-confirm-note').textContent = notes[aiMode] || notes.fresh;
        showAiPhase('confirm');
      }} else {{
        showAiPhase('setup');
      }}
    }})
    .catch(() => showAiError('Could not reach the server.'));
}}

document.getElementById('ai-mode-fresh-btn').addEventListener('click', () => {{ aiMode = 'fresh'; aiProceedAfterMode(); }});
document.getElementById('ai-mode-improve-btn').addEventListener('click', () => {{ aiMode = 'improve'; aiProceedAfterMode(); }});
document.getElementById('ai-mode-import-btn').addEventListener('click', () => {{ aiMode = 'import'; showAiPhase('import'); }});
document.getElementById('ai-import-back-btn').addEventListener('click', () => {{ showAiPhase('mode'); }});
document.getElementById('ai-import-continue-btn').addEventListener('click', () => {{
  const url = document.getElementById('ai-import-url-input').value.trim();
  const text = document.getElementById('ai-import-text-input').value.trim();
  if (!url && !text) {{ showAiError('Paste a decklist or provide a URL first.'); return; }}
  hideAiError();
  aiMode = 'import';
  aiProceedAfterMode();
}});

document.getElementById('ai-key-save-btn').addEventListener('click', () => {{
  const key = aiKeyInput.value.trim();
  if (!key) {{ aiKeyStatus.textContent = 'Paste a key first.'; return; }}
  aiKeyStatus.textContent = 'Checking…';
  fetch('/ai/key', {{
    method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ api_key: key }}),
  }})
    .then(r => r.json())
    .then(data => {{
      if (data.error) {{ aiKeyStatus.textContent = data.error; return; }}
      aiKeyInput.value = '';
      aiProceedAfterMode();
    }})
    .catch(() => {{ aiKeyStatus.textContent = 'Could not reach the server.'; }});
}});

document.getElementById('ai-change-key-btn').addEventListener('click', () => {{
  if (!confirm('Remove the saved API key? You can enter a new one right after.')) return;
  fetch('/ai/key/clear', {{ method: 'POST' }})
    .then(() => {{ aiKeyInput.value = ''; aiKeyStatus.textContent = ''; showAiPhase('setup'); }})
    .catch(() => showAiError('Could not reach the server.'));
}});

let aiPollTimer = null;
document.getElementById('ai-start-btn').addEventListener('click', () => {{
  hideAiError();
  showAiPhase('progress');
  document.getElementById('ai-log').innerHTML = '';
  document.getElementById('ai-progress-count').textContent = 'Starting…';
  fetch('/builder/ai-build/start', {{
    method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{
      cards: brew.cards, commander: brew.commander, format: brew.format, target_format: brew.target_format,
      intended_bracket: brew.intended_bracket, user_notes: document.getElementById('ai-notes-input').value,
      mode: aiMode,
      import_url: document.getElementById('ai-import-url-input').value.trim(),
      import_text: document.getElementById('ai-import-text-input').value.trim(),
    }}),
  }})
    .then(r => r.json())
    .then(data => {{
      if (data.error) {{ showAiError(data.error); showAiPhase('confirm'); return; }}
      pollAiBuild(data.job_id);
    }})
    .catch(() => {{ showAiError('Could not reach the server.'); showAiPhase('confirm'); }});
}});

function pollAiBuild(jobId) {{
  clearTimeout(aiPollTimer);
  fetch('/builder/ai-build/progress/' + jobId)
    .then(r => r.json())
    .then(data => {{
      if (data.status === 'not_found') {{ showAiError('That build expired.'); showAiPhase('confirm'); return; }}
      const logEl = document.getElementById('ai-log');
      logEl.innerHTML = (data.log || []).map(l => `<div>${{escapeHtml(l)}}</div>`).join('');
      logEl.scrollTop = logEl.scrollHeight;
      const deckCount = (data.deck_state || []).filter(c => c.section !== 'commander').reduce((s, c) => s + c.quantity, 0);
      document.getElementById('ai-progress-count').textContent = `${{deckCount}} card(s) so far — turn ${{data.done}}/${{data.total}}`;
      if (data.status === 'done' || data.status === 'error') {{ fetchAiResult(jobId); return; }}
      aiPollTimer = setTimeout(() => pollAiBuild(jobId), 1500);
    }})
    .catch(() => {{ aiPollTimer = setTimeout(() => pollAiBuild(jobId), 1500); }});
}}

function fetchAiResult(jobId) {{
  fetch('/builder/ai-build/result/' + jobId)
    .then(r => r.json())
    .then(data => {{
      if (data.error) {{ showAiError(data.error); showAiPhase('confirm'); return; }}
      closeAiModal();
      if (data.mode === 'import') {{
        applyAiImportResult(data);
      }} else {{
        renderAiSuggestions(data);
      }}
    }})
    .catch(() => {{ showAiError('Could not reach the server.'); showAiPhase('confirm'); }});
}}

// Import & improve is a different shape of result than fresh/improve:
// the point was never to keep hand-picking individual suggestions in the
// builder (there's nothing to "do" with a bare list there) -- it's to
// end up with a real deck you can immediately see owned-vs-missing for.
// So this replaces the WIP deck outright with the AI's full final list
// (commander + everything it kept or added -- see run_ai_build's
// final_entries, which exists specifically because the originally
// imported/pasted cards were otherwise never sent back to the client at
// all), names it, saves it, and jumps straight into the existing
// compare/report view -- same one-click flow "View full report" already
// uses, just triggered automatically instead of requiring Save then
// View full report by hand afterward.
function applyAiImportResult(data) {{
  if (data.summary) {{ brew.ai_summary = data.summary; }}
  brew.further_optimizations = [];  // fresh AI-built deck -- no stale trail from whatever was here before
  brew.maybeboard = data.maybeboard || [];
  const commanderEntry = (data.final_entries || []).find(c => c.section === 'commander');
  const libraryEntries = (data.final_entries || []).filter(c => c.section !== 'commander');
  brew.commander = commanderEntry ? {{
    name: commanderEntry.name, scryfall_id: commanderEntry.scryfall_id, type_line: commanderEntry.type_line,
    color_identity: commanderEntry.color_identity, cmc: commanderEntry.cmc, mana_cost: commanderEntry.mana_cost,
    category: commanderEntry.category, quantity: 1, set_code: commanderEntry.set_code,
    collector_number: commanderEntry.collector_number,
  }} : null;
  brew.cards = libraryEntries.map(c => ({{
    name: c.name, quantity: c.quantity, scryfall_id: c.scryfall_id, cmc: c.cmc, mana_cost: c.mana_cost,
    type_line: c.type_line, color_identity: c.color_identity, category: c.category,
    set_code: c.set_code, collector_number: c.collector_number,
  }}));
  if (data.guessed_deck_name) {{
    brew.deck_name = data.guessed_deck_name;
    document.getElementById('deck-name').value = data.guessed_deck_name;
  }}
  renderAll();
  if (data.import_unresolved && data.import_unresolved.length) {{
    showError(`Couldn't recognize ${{data.import_unresolved.length}} line(s) from your import: ${{data.import_unresolved.slice(0, 5).join('; ')}}`);
  }}
  saveThenViewReport();
}}

// Maybeboard entries are informational + actionable (unlike the static
// Further Optimizations trail) -- each gets its own "+ Add" since the
// whole point is the player deciding, not the AI committing a swap it
// wasn't confident about. Re-run after adding one so the list reflects
// what's left, same "remaining reflects reality" pattern used elsewhere.
function renderMaybeboard() {{
  const list = brew.maybeboard || [];
  document.getElementById('maybeboard-block').style.display = list.length ? 'block' : 'none';
  const container = document.getElementById('maybeboard-list');
  container.innerHTML = '';
  list.forEach(c => {{
    const row = document.createElement('div');
    row.className = 'suggestion-row';
    row.dataset.full = scryfallImg(c.scryfall_id, 'normal') || '';
    const replacesNote = c.replaces ? ` &mdash; could replace ${{escapeHtml(c.replaces)}}` : '';
    row.innerHTML = `<span class="row-name">${{thumbHtml(c.scryfall_id, 'card-thumb small')}}<span>${{escapeHtml(c.name)}} ${{colorIconsHtml(c.color_identity)}}<div class="reason">${{escapeHtml(c.reason)}}${{replacesNote}}</div></span></span>`;
    const addBtn = Object.assign(document.createElement('button'), {{
      className: 'btn ghost small', textContent: '+ Add',
      onclick: () => {{
        addCard(c);
        brew.maybeboard = brew.maybeboard.filter(x => x !== c);
        renderMaybeboard();
      }},
    }});
    row.appendChild(addBtn);
    container.appendChild(row);
  }});
}}

function renderAiSuggestions(data) {{
  // Persisted onto the brew (not just shown here) so it survives past
  // this transient panel -- Analyze Deck and the full report's Deck
  // Analysis section both surface it once the deck is saved, instead of
  // the AI's own reasoning being lost the moment this panel is dismissed.
  if (data.summary) {{ brew.ai_summary = data.summary; brew.further_optimizations = []; }}
  const panel = document.getElementById('suggestions-panel');
  panel.innerHTML = '<h4 style="font-size:0.8rem;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.03em;">&#10024; AI-Built Deck</h4>';
  if (data.summary) {{
    panel.innerHTML += `<p class="hint" style="margin:0 0 8px;">${{escapeHtml(data.summary)}}</p>`;
  }}
  if (!data.finished) {{
    panel.innerHTML += '<p class="hint" style="margin:0 0 8px;color:var(--missing);">Claude hit the time/turn limit before calling finish -- this list may be short of a full deck.</p>';
  }}
  if (data.import_unresolved && data.import_unresolved.length) {{
    panel.innerHTML += `<p class="hint" style="margin:0 0 8px;color:var(--missing);">Couldn’t recognize ${{data.import_unresolved.length}} line(s) from your import: ${{escapeHtml(data.import_unresolved.slice(0, 5).join('; '))}}</p>`;
  }}
  if (!data.suggestions.length && !(data.removed || []).length) {{
    panel.innerHTML += '<div class="hint" style="margin:0;">Claude didn’t change anything -- try again, or add a note about what you want.</div>';
    return;
  }}

  if ((data.removed || []).length) {{
    const removeHeader = document.createElement('div');
    removeHeader.className = 'hint';
    removeHeader.style.margin = '0 0 6px';
    removeHeader.textContent = 'Suggested cuts:';
    panel.appendChild(removeHeader);
    data.removed.forEach(name => {{
      const row = document.createElement('div');
      row.className = 'suggestion-row';
      row.innerHTML = `<span class="row-name"><span>${{escapeHtml(name)}}</span></span>`;
      const cutBtn = Object.assign(document.createElement('button'), {{
        className: 'btn ghost small', textContent: 'Remove',
        onclick: () => {{ removeCard(name); row.remove(); }},
      }});
      row.appendChild(cutBtn);
      panel.appendChild(row);
    }});
  }}

  if (!data.suggestions.length) return;
  let remaining = data.suggestions.slice();
  const addAllBtn = Object.assign(document.createElement('button'), {{
    className: 'btn ghost small', style: 'margin-bottom:8px;margin-right:8px;',
    onclick: () => {{ remaining.forEach(addCard); panel.innerHTML = ''; }},
  }});
  const dismissAllBtn = Object.assign(document.createElement('button'), {{
    className: 'btn ghost small', textContent: 'Dismiss All', style: 'margin-bottom:8px;',
    onclick: () => {{ panel.innerHTML = ''; }},
  }});
  function refreshAddAllBtn() {{
    addAllBtn.textContent = `+ Add All (${{remaining.length}})`;
    addAllBtn.style.display = remaining.length ? '' : 'none';
    dismissAllBtn.style.display = remaining.length ? '' : 'none';
  }}
  refreshAddAllBtn();
  panel.append(addAllBtn, dismissAllBtn);
  data.suggestions.forEach(s => {{
    const row = document.createElement('div');
    row.className = 'suggestion-row';
    row.dataset.full = scryfallImg(s.scryfall_id, 'normal') || '';
    row.innerHTML = `<span class="row-name">${{thumbHtml(s.scryfall_id, 'card-thumb small')}}<span>${{escapeHtml(s.name)}} ${{colorIconsHtml(s.color_identity)}}<div class="reason">${{escapeHtml(s.reason)}}</div></span></span>`;
    const addBtn = Object.assign(document.createElement('button'), {{
      className: 'btn ghost small', textContent: '+ Add',
      onclick: () => {{
        addCard(s);
        row.remove();
        remaining = remaining.filter(x => x !== s);
        refreshAddAllBtn();
      }},
    }});
    row.appendChild(addBtn);
    panel.appendChild(row);
  }});
}}

// Confirmed live against Moxfield's and Archidekt's own decklist-import
// UI: both accept plain "qty Name" lines, and both accept an optional
// "(SET) CollectorNumber" suffix to pin the exact printing rather than
// defaulting to whichever one the site prefers. Neither needs the
// commander marked in the pasted text -- Moxfield sets it via its own
// separate "Commander" search field on the new-deck form, and Archidekt
// lets you flag it after import -- so this is one plain list, commander
// included, with a callout telling you its name to set manually.
function decklistLine(c) {{
  const printing = (c.set_code && c.collector_number) ? ` (${{c.set_code.toUpperCase()}}) ${{c.collector_number}}` : '';
  return `${{c.quantity}} ${{c.name}}${{printing}}`;
}}

function buildDecklistText() {{
  const lines = [];
  if (brew.commander) lines.push(decklistLine(brew.commander));
  brew.cards.forEach(c => lines.push(decklistLine(c)));
  return lines.join('\\n');
}}

const copyDecklistBtn = document.getElementById('copy-decklist-btn');
copyDecklistBtn.addEventListener('click', () => {{
  if (!brew.cards.length && !brew.commander) {{ showError('Add some cards first.'); return; }}
  const text = buildDecklistText();
  const label = document.getElementById('save-label');
  const finish = () => {{
    label.textContent = brew.commander
      ? `Copied ${{brew.cards.length + 1}} cards. Paste into Moxfield/Archidekt, then set your commander to: ${{brew.commander.name}}.`
      : `Copied ${{brew.cards.length}} cards. Paste into Moxfield/Archidekt to import.`;
    label.style.display = 'block';
    setTimeout(() => {{ label.style.display = 'none'; }}, 6000);
  }};
  if (navigator.clipboard && navigator.clipboard.writeText) {{
    navigator.clipboard.writeText(text).then(finish).catch(() => showError('Could not copy to clipboard.'));
  }} else {{
    showError('Clipboard access is not available in this browser.');
  }}
}});

// Same "Group, Subgroup" binning as the compare report's In-Store CSV
// (see shopping_group() in brewlist_core.py -- ported here since this
// runs entirely client-side off brew.cards, no server round trip), but
// listing what you *own* (set/collector number) rather than what to buy.
function shoppingGroupOf(c) {{
  if ((c.type_line || '').includes('Land')) return ['Lands', ''];
  if ((c.type_line || '').includes('Artifact')) return ['Artifacts', ''];
  const names = {{ W: 'White', U: 'Blue', B: 'Black', R: 'Red', G: 'Green' }};
  const colors = WUBRG.filter(col => (c.color_identity || []).includes(col));
  const subgroup = colors.length === 0 ? 'Colorless' : colors.length === 1 ? names[colors[0]] : 'Multicolor';
  return ['Colors', subgroup];
}}

function csvEscape(value) {{
  const s = String(value ?? '');
  if (/["\\n,]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
  return s;
}}

const exportCsvBtn = document.getElementById('export-csv-btn');
exportCsvBtn.addEventListener('click', () => {{
  const all = brew.commander ? [brew.commander, ...brew.cards] : brew.cards;
  if (!all.length) {{ showError('Add some cards first.'); return; }}
  const groupRank = {{ Colors: 0, Artifacts: 1, Lands: 2 }};
  const subgroupRank = {{ Multicolor: 0, White: 1, Blue: 2, Black: 3, Red: 4, Green: 5, Colorless: 99 }};
  const rows = all.map(c => {{
    const [group, subgroup] = shoppingGroupOf(c);
    return {{
      group, subgroup, qty: c.quantity, name: c.name, type: c.type_line || '',
      setCode: (c.set_code || '').toUpperCase(), cn: c.collector_number || '',
    }};
  }});
  rows.sort((a, b) => {{
    const ra = (groupRank[a.group] ?? 9) * 1000 + (subgroupRank[a.subgroup] ?? 50);
    const rb = (groupRank[b.group] ?? 9) * 1000 + (subgroupRank[b.subgroup] ?? 50);
    return ra - rb || a.name.localeCompare(b.name);
  }});
  const header = ['Group', 'Subgroup', 'Qty', 'Name', 'Type', 'Set Code', 'Collector #'];
  const lines = [header.map(csvEscape).join(',')];
  rows.forEach(r => lines.push([r.group, r.subgroup, r.qty, r.name, r.type, r.setCode, r.cn].map(csvEscape).join(',')));
  const csv = lines.join('\\r\\n');
  const blob = new Blob([csv], {{ type: 'text/csv;charset=utf-8;' }});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = (brew.deck_name || 'deck').replace(/[^A-Za-z0-9_-]+/g, '_') + '_cards.csv';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}});

const BRACKET_TAG_LABELS = {{ E: 'Exhibition', C: 'Core', P: 'Powerful', O: 'Oddball', S: 'Spicy', R: 'Ruthless', B: 'Banned' }};

const COLOR_NAMES = {{ W: 'White', U: 'Blue', B: 'Black', R: 'Red', G: 'Green' }};
function deckColorIdentity() {{
  const colors = new Set();
  if (brew.commander) (brew.commander.color_identity || []).forEach(c => colors.add(c));
  brew.cards.forEach(c => (c.color_identity || []).forEach(x => colors.add(x)));
  const ordered = WUBRG.filter(c => colors.has(c));
  return ordered.length ? ordered : ['Colorless'];
}}

function statBadges(data) {{
  const badges = [
    {{ cls: 'highlight', label: `&#128176; $${{data.deck_value.toFixed(2)}}`, tip: "Total deck value at today's market price" }},
  ];
  if (data.is_commander_format) {{
    const gcTip = data.game_changers_names.length
      ? 'Game Changers in this deck: ' + data.game_changers_names.join(', ')
      : 'On WotC\\'s official Commander Game Changers list -- none found in this deck';
    badges.push({{
      cls: data.game_changers ? 'highlight' : '',
      label: `&#9889; ${{data.game_changers}} Game Changer${{data.game_changers === 1 ? '' : 's'}}`,
      tip: gcTip,
    }});
    badges.push(data.banned_count
      ? {{ cls: 'warn', label: `&#9940; ${{data.banned_count}} not legal`, tip: 'Cards not legal in Commander (banned/restricted)' }}
      : {{ cls: 'good', label: '&#10003; Commander legal', tip: 'No Commander-banned or restricted cards found' }});
    if (data.bracket_tag) {{
      badges.push({{
        label: BRACKET_TAG_LABELS[data.bracket_tag] || data.bracket_tag,
        tip: "Commander Spellbook's own community power/style rating for this deck -- not the official WotC bracket system",
      }});
    }}
    if (data.wotc_bracket) {{
      badges.push({{
        label: `Bracket ${{data.wotc_bracket[0]}}`,
        tip: `Estimated from WotC's own published Bracket rules (Game Changers, mass land denial, combos, extra turns): ${{data.wotc_bracket[1]}}`,
      }});
    }}
    badges.push({{
      label: `&#128279; ${{data.combos_included}} combo${{data.combos_included === 1 ? '' : 's'}}, ${{data.combos_almost}} one away`,
      tip: `${{data.combos_included}} known combo(s) already in this deck, and ${{data.combos_almost}} more you're exactly one card away from completing (via Commander Spellbook)`,
    }});
  }}
  return badges;
}}

// Plain computed facts, not generated prose -- the same "no AI-generated
// guesses" rule as everywhere else in this app (Suggest's theme signal,
// budget alternatives). Just the objective numbers a real Rule 0 chat
// covers, laid out as statements instead of badges.
function rule0Items(data) {{
  const items = [`<b>Colors:</b> ${{deckColorIdentity().map(c => COLOR_NAMES[c] || c).join(' / ')}}`];
  if (data.is_commander_format) {{
    if (data.wotc_bracket) {{
      items.push(`<b>Estimated Bracket ${{data.wotc_bracket[0]}}</b> (${{data.wotc_bracket[1]}}) -- WotC's own published rules, from this deck's Game Changers/combos/extra-turns/land-denial`);
    }}
    items.push(`<b>Game Changers (${{data.game_changers}}):</b> ${{data.game_changers_names.length ? data.game_changers_names.join(', ') : 'none'}}`);
    items.push(`<b>Mass land denial:</b> ${{data.mass_land_denial ? 'Yes' : 'No'}}`);
    items.push(`<b>Extra-turn effects:</b> ${{data.extra_turn_count}}`);
    items.push(`<b>Combos:</b> ${{data.combos_included}} already assembled, ${{data.combos_almost}} one card away`);
  }}
  items.push(`<b>Deck value:</b> $${{data.deck_value.toFixed(2)}} (today's market)`);
  return items;
}}

function renderComboReference(data) {{
  const included = data.combos_included_list || [];
  const almost = data.combos_almost_list || [];
  let html = '';
  const comboLine = c => `${{c.uses.join(' + ')}}<span class="combo-arrow">&rarr;</span>${{c.produces.join(', ')}}`;
  if (included.length) {{
    html += '<div class="combo-group"><h5>Already in your deck</h5>' + included.map(c => `
      <div class="combo-item"><span class="combo-uses">${{comboLine(c)}}</span>${{c.url ? ` <a href="${{c.url}}" target="_blank" rel="noopener">(details)</a>` : ''}}</div>
    `).join('') + '</div>';
  }}
  if (almost.length) {{
    html += '<div class="combo-group"><h5>One card away</h5>' + almost.map(c => `
      <div class="combo-item">
        <span class="combo-uses">${{comboLine(c)}}</span>
        <div class="combo-missing">Missing: ${{(c.missing || []).join(', ') || '?'}}</div>
      </div>
    `).join('') + '</div>';
  }}
  document.getElementById('combo-reference').innerHTML = html || '<div class="hint" style="margin:0;">No known combos detected (via Commander Spellbook).</div>';
}}

let lastAnalyzeData = null;
const analyzeModal = document.getElementById('analyze-modal');
const analyzeBtn = document.getElementById('analyze-btn');
const printBattleCardBtn = document.getElementById('print-battle-card-btn');

function closeAnalyzeModal() {{ analyzeModal.classList.remove('show'); }}
document.getElementById('analyze-modal-close').addEventListener('click', closeAnalyzeModal);
analyzeModal.addEventListener('click', (e) => {{ if (e.target === analyzeModal) closeAnalyzeModal(); }});
document.addEventListener('keydown', (e) => {{ if (e.key === 'Escape' && analyzeModal.classList.contains('show')) closeAnalyzeModal(); }});

analyzeBtn.addEventListener('click', () => {{
  if (!brew.cards.length && !brew.commander) {{ showError('Add some cards first.'); return; }}
  lastAnalyzeData = null;
  analyzeModal.classList.add('show');
  document.getElementById('analyze-modal-title').textContent = brew.deck_name || document.getElementById('deck-name').value || 'Analyze Deck';
  document.getElementById('ai-summary-block').style.display = brew.ai_summary ? 'block' : 'none';
  document.getElementById('ai-summary-text').textContent = brew.ai_summary || '';
  const furtherOpts = brew.further_optimizations || [];
  document.getElementById('further-optimizations-block').style.display = furtherOpts.length ? 'block' : 'none';
  document.getElementById('further-optimizations-list').innerHTML = furtherOpts.map(t => `<li>${{escapeHtml(t)}}</li>`).join('');
  renderMaybeboard();
  renderDeckAnalysis();
  drawSampleHand();
  document.getElementById('analyze-badges').innerHTML = '';
  document.getElementById('rule0-list').innerHTML = '';
  document.getElementById('combo-reference').innerHTML = '';
  document.getElementById('analyze-loading').style.display = 'block';
  printBattleCardBtn.disabled = true;
  fetch('/builder/stats', {{
    method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ cards: brew.cards, commander: brew.commander, format: brew.format }}),
  }})
    .then(r => r.json())
    .then(data => {{
      document.getElementById('analyze-loading').style.display = 'none';
      if (data.error) {{ showError(data.error); return; }}
      lastAnalyzeData = data;
      document.getElementById('analyze-badges').innerHTML = statBadges(data).map(b =>
        `<span class="bv-badge ${{b.cls || ''}}">${{b.label}}<span class="tooltip-popup">${{b.tip}}</span></span>`
      ).join('');
      document.getElementById('rule0-list').innerHTML = rule0Items(data).map(t => `<li>${{t}}</li>`).join('');
      renderComboReference(data);
      printBattleCardBtn.disabled = false;
    }})
    .catch(() => {{ document.getElementById('analyze-loading').style.display = 'none'; showError('Could not reach the server.'); }});
}});

// Delegated (badges are re-rendered wholesale on every Analyze click, so
// per-element listeners would leak/duplicate) hover-tooltip positioning
// for .bv-badge, same clamped-to-container idea as #hover-preview's own
// viewport clamping above. CSS alone can't do this: the badges live in a
// modal whose overflow-y:auto clips the X axis too, and no single fixed
// anchor (left:0, centered, right:0) avoids clipping for every badge --
// a badge near the row's start needs to open right-ish, one near the end
// needs to open left-ish. This measures each badge against .modal-box's
// actual bounds on hover and shifts the tooltip only as much as needed.
function positionBadgeTooltip(badge) {{
  const tip = badge.querySelector('.tooltip-popup');
  if (!tip) return;
  tip.style.left = '0px';
  tip.style.display = 'block';
  const pad = 10;
  const boxRect = document.querySelector('.modal-box').getBoundingClientRect();
  const tipRect = tip.getBoundingClientRect();
  let shift = 0;
  if (tipRect.right > boxRect.right - pad) shift -= (tipRect.right - (boxRect.right - pad));
  if (tipRect.left + shift < boxRect.left + pad) shift += (boxRect.left + pad - (tipRect.left + shift));
  tip.style.left = shift + 'px';
}}
const analyzeBadgesEl = document.getElementById('analyze-badges');
analyzeBadgesEl.addEventListener('mouseover', (e) => {{
  const badge = e.target.closest('.bv-badge');
  if (badge) positionBadgeTooltip(badge);
}});
analyzeBadgesEl.addEventListener('mouseout', (e) => {{
  const badge = e.target.closest('.bv-badge');
  if (!badge || badge.contains(e.relatedTarget)) return;
  const tip = badge.querySelector('.tooltip-popup');
  if (tip) tip.style.display = 'none';
}});

function buildBattleCardHtml(data) {{
  const name = brew.deck_name || document.getElementById('deck-name').value || 'Untitled brew';
  const commanderLine = brew.commander ? `Commander: ${{brew.commander.name}}` : (brew.format === 'commander' ? 'No commander chosen' : '60-card constructed');
  const badges = [];
  badges.push(`${{deckColorIdentity().map(c => COLOR_NAMES[c] || c).join(' / ')}}`);
  if (data.is_commander_format && data.wotc_bracket) badges.push(`Bracket ${{data.wotc_bracket[0]}} (${{data.wotc_bracket[1]}})`);
  if (data.bracket_tag) badges.push(BRACKET_TAG_LABELS[data.bracket_tag] || data.bracket_tag);
  badges.push(`$${{data.deck_value.toFixed(2)}}`);
  const included = data.combos_included_list || [];
  const almost = data.combos_almost_list || [];
  const comboLine = c => `${{c.uses.join(' + ')}} \\u2192 ${{c.produces.join(', ')}}`;
  return `
    <h1>${{name}}</h1>
    <div class="bc-sub">${{commanderLine}}</div>
    <div class="bc-badges">${{badges.map(b => `<span>${{b}}</span>`).join('')}}</div>
    <h2>Rule 0 Summary</h2>
    <ul>${{rule0Items(data).map(t => `<li>${{t}}</li>`).join('')}}</ul>
    <h2>Game Changers</h2>
    <ul>${{data.is_commander_format && data.game_changers_names.length ? data.game_changers_names.map(n => `<li>${{n}}</li>`).join('') : '<li>None</li>'}}</ul>
    <h2>Combo Reference</h2>
    ${{included.length ? included.map(c => `<div class="bc-combo">&#10003; ${{comboLine(c)}}</div>`).join('') : ''}}
    ${{almost.length ? almost.map(c => `<div class="bc-combo">&#9675; ${{comboLine(c)}} (needs: ${{(c.missing || []).join(', ')}})</div>`).join('') : ''}}
    ${{!included.length && !almost.length ? '<div class="bc-combo">No known combos detected.</div>' : ''}}
  `;
}}

printBattleCardBtn.addEventListener('click', () => {{
  if (!lastAnalyzeData) return;
  const card = document.getElementById('battle-card');
  card.innerHTML = buildBattleCardHtml(lastAnalyzeData);
  card.classList.add('printing');
  window.print();
  card.classList.remove('printing');
}});

function pollReportProgress(jobId) {{
  fetch('/compare/progress/' + jobId)
    .then(r => r.json())
    .then(data => {{
      if (data.status === 'running') {{
        setTimeout(() => pollReportProgress(jobId), 400);
      }} else if (data.status === 'done') {{
        window.location.href = '/compare/result/' + jobId;
      }} else {{
        showError(data.error || 'Something went wrong building the report.');
        document.getElementById('report-btn').disabled = false;
      }}
    }})
    .catch(() => {{
      showError('Lost contact with the server.');
      document.getElementById('report-btn').disabled = false;
    }});
}}

const reportBtn = document.getElementById('report-btn');
function saveThenViewReport() {{
  if (!brew.cards.length) {{ showError('Add some cards first.'); return; }}
  reportBtn.disabled = true;
  fetch('/builder/save', {{
    method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ deck_id: deckId, ...brew }}),
  }})
    .then(r => r.json())
    .then(data => {{
      if (data.error) {{ throw new Error(data.error); }}
      deckId = data.deck_id;
      window.history.replaceState(null, '', '/builder?id=' + encodeURIComponent(deckId));
      return fetch('/builder/report/start', {{
        method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ deck_id: deckId }}),
      }});
    }})
    .then(r => r.json())
    .then(data => {{
      if (data.error) {{ throw new Error(data.error); }}
      pollReportProgress(data.job_id);
    }})
    .catch((e) => {{
      showError(e.message || 'Could not reach the server.');
      reportBtn.disabled = false;
    }});
}}
reportBtn.addEventListener('click', saveThenViewReport);
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def home():
    return render_setup_page()


@app.route("/decks", methods=["GET"])
def decks():
    prefill = request.args.get("deck", "")
    return render_decks_page(prefill_url=prefill)


@app.route("/collection/upload", methods=["POST"])
def collection_upload():
    """Standalone collection upload -- its own submit, decoupled from the
    Compare form (which used to bundle the file input, so choosing a new
    file and then navigating to /decks or /builder without also filling
    in a deck URL and clicking Compare silently dropped the upload)."""
    upload = request.files.get("manabox_csv")
    if not upload or not upload.filename:
        return jsonify(error="Choose a .csv file first."), 400
    upload.save(COLLECTION_PATH)
    meta = {
        "filename": upload.filename,
        "uploaded": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    with open(COLLECTION_META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f)
    return jsonify(ok=True, **meta)


@app.route("/project/delete", methods=["POST"])
def project_delete():
    body = request.get_json(silent=True) or {}
    deck_id = body.get("deck_id")
    if not deck_id:
        return jsonify(error="Missing deck_id."), 400
    delete_project(deck_id)
    return jsonify(ok=True)


@app.route("/builder", methods=["GET"])
def builder():
    return render_builder_page(deck_id=request.args.get("id"))


@app.route("/builder/collection-data", methods=["GET"])
def builder_collection_data():
    if not os.path.isfile(COLLECTION_PATH):
        return jsonify(error="No ManaBox collection on file yet -- upload one from the home page first."), 400
    try:
        owned = load_collection(COLLECTION_PATH)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    cards = owned_collection_gameplay_view(owned, gameplay_data_in_index())
    return jsonify(cards=cards)


@app.route("/builder/sets", methods=["GET"])
def builder_sets():
    """Every set the owned collection has cards from, name + release date
    labeled, sorted oldest-first -- backs the builder's Set Selection
    filter. Independent of any particular brew (unlike /builder/themes),
    so this is a plain GET with no request body."""
    if not os.path.isfile(COLLECTION_PATH):
        return jsonify(error="No ManaBox collection on file yet -- upload one from the home page first."), 400
    try:
        owned = load_collection(COLLECTION_PATH)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    sets = owned_set_options(owned, sets_data_in_index())
    return jsonify(sets=sets)


@app.route("/builder/save", methods=["POST"])
def builder_save():
    body = request.get_json(silent=True) or {}
    cards = body.get("cards")
    if not isinstance(cards, list):
        abort(400)
    deck_id = body.get("deck_id") or f"brew-{uuid.uuid4().hex[:12]}"
    save_project(
        deck_id,
        type="brew",
        deck_name=body.get("deck_name") or "Untitled brew",
        format=body.get("format") if body.get("format") in ("commander", "constructed") else "commander",
        target_format=body.get("target_format") or "standard",
        commander=body.get("commander"),
        cards=cards,
        mix_targets=body.get("mix_targets") or {},
        intended_bracket=body.get("intended_bracket") or "",
        preferred_theme_tag_ids=body.get("preferred_theme_tag_ids") or [],
        preferred_theme_label=body.get("preferred_theme_label") or "",
        excluded_set_codes=body.get("excluded_set_codes") or [],
        ai_summary=body.get("ai_summary") or "",
        further_optimizations=body.get("further_optimizations") or [],
        maybeboard=body.get("maybeboard") or [],
    )
    return jsonify(deck_id=deck_id)


def _excluded_set_codes(body: dict) -> set[str] | None:
    """Parses the Set Selection filter's excluded_set_codes list out of a
    /builder/suggest, /builder/themes, or /builder/replace request body --
    shared so the three routes stay in sync. None (not an empty set) when
    nothing's excluded, matching _filter_candidates' own "falsy = no
    restriction" convention."""
    codes = {str(c).upper() for c in (body.get("excluded_set_codes") or []) if c}
    return codes or None


@app.route("/builder/suggest", methods=["POST"])
def builder_suggest():
    body = request.get_json(silent=True) or {}
    deck_format = body.get("format") if body.get("format") in ("commander", "constructed") else "commander"
    if not os.path.isfile(COLLECTION_PATH):
        return jsonify(error="No ManaBox collection on file yet -- upload one from the home page first."), 400
    try:
        owned = load_collection(COLLECTION_PATH)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    owned_view = owned_collection_gameplay_view(owned, gameplay_data_in_index())
    wip_entries = brew_to_card_entries({"commander": body.get("commander"), "cards": body.get("cards") or []})
    commander = body.get("commander") or {}
    raw_mix = body.get("mix_targets") or {}
    mix_targets = {
        role: int(raw_mix[role]) for role in ("Lands", "Ramp", "Draw", "Interaction")
        if role in raw_mix and isinstance(raw_mix[role], (int, float))
    } or None
    target_size = 100 if deck_format == "commander" else 60
    suggestions = suggest_builder_cards(
        wip_entries, owned_view, deck_format, body.get("target_format"),
        target_size=target_size,
        commander_color_identity=commander.get("color_identity") if deck_format == "commander" else None,
        # Request everything still needed in one go (suggest_builder_cards
        # caps this to however many slots are actually left) rather than
        # the function's own small per-call default -- a fresh deck should
        # get one big reviewable batch, not require clicking Suggest ~7
        # times to reach a full 99/100 or 60/60.
        max_suggestions=target_size,
        mix_targets=mix_targets,
        intended_bracket=body.get("intended_bracket") or None,
        preferred_theme_tag_ids=body.get("preferred_theme_tag_ids") or None,
        preferred_theme_label=body.get("preferred_theme_label") or None,
        excluded_set_codes=_excluded_set_codes(body),
    )
    return jsonify(suggestions=suggestions)


@app.route("/builder/optimize", methods=["POST"])
def builder_optimize():
    """Second-pass combo optimizer for a deck that's already built (see
    optimize_builder_combos) -- proposed add/remove swaps for real, owned,
    one-card-away combos that a plain Suggest batch can miss entirely,
    since Suggest's own combo-completion signal only ever sees combos as
    they stood at the *start* of that call. Same request shape as
    /builder/suggest; not auto-applied client-side."""
    body = request.get_json(silent=True) or {}
    deck_format = body.get("format") if body.get("format") in ("commander", "constructed") else "commander"
    if not os.path.isfile(COLLECTION_PATH):
        return jsonify(error="No ManaBox collection on file yet -- upload one from the home page first."), 400
    try:
        owned = load_collection(COLLECTION_PATH)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    owned_view = owned_collection_gameplay_view(owned, gameplay_data_in_index())
    wip_entries = brew_to_card_entries({"commander": body.get("commander"), "cards": body.get("cards") or []})
    commander = body.get("commander") or {}
    proposals = optimize_builder_combos(
        wip_entries, owned_view, deck_format, body.get("target_format"),
        commander_color_identity=commander.get("color_identity") if deck_format == "commander" else None,
        intended_bracket=body.get("intended_bracket") or None,
        excluded_set_codes=_excluded_set_codes(body),
    )
    return jsonify(proposals=proposals)


@app.route("/ai/status", methods=["GET"])
def ai_status():
    """Whether an Anthropic API key is configured, and where it came
    from -- never the key itself. env always wins over a locally-saved
    file (see ai_builder.load_api_key)."""
    source = key_source()
    return jsonify(configured=source is not None, source=source)


@app.route("/ai/key", methods=["POST"])
def ai_key_save():
    body = request.get_json(silent=True) or {}
    api_key = (body.get("api_key") or "").strip()
    if not api_key:
        return jsonify(error="Paste your Anthropic API key first."), 400
    validation_error = validate_api_key(api_key)
    if validation_error:
        return jsonify(error=validation_error), 400
    save_api_key(api_key)
    return jsonify(ok=True)


@app.route("/ai/key/clear", methods=["POST"])
def ai_key_clear():
    clear_api_key()
    return jsonify(ok=True)


@app.route("/builder/ai-build/start", methods=["POST"])
def builder_ai_build_start():
    """Kicks off the agentic "Build with AI" loop (see ai_builder.py) in a
    background thread -- same JOBS/threading/poll shape /compare/start
    already established, since a multi-round-trip Claude tool-use build
    genuinely takes a while and shouldn't block one long request.

    `mode` ("fresh" | "improve" | "import", default "fresh") picks which
    of the three modal options fired this call:
    - "fresh": today's original behavior -- no wip_entries, starts from
      just the commander.
    - "improve": seeds wip_entries from the WIP deck's own cards
      (`body["cards"]`) via brew_to_card_entries(), same helper
      /builder/suggest and /builder/optimize already use -- so the loop
      builds on what's already chosen instead of discarding it.
    - "import": seeds wip_entries from a Moxfield/Archidekt URL
      (`import_url`, reusing parse_deck_ref/fetch_deck/extract_entries
      exactly like /compare does) or a pasted plain-text decklist
      (`import_text`, via parse_pasted_decklist()) and runs with
      scope="any" -- the candidate pool becomes every real, legal card,
      not just owned ones, since the point is often to find what's worth
      *buying*. A Commander-format import's own commander (tagged via
      extract_entries' section=="commander") is used directly; otherwise
      falls back to whatever commander the builder already has set."""
    body = request.get_json(silent=True) or {}
    deck_format = body.get("format") if body.get("format") in ("commander", "constructed") else "commander"
    mode = body.get("mode") if body.get("mode") in ("fresh", "improve", "import") else "fresh"
    commander = body.get("commander") or {}

    api_key = load_api_key()
    if not api_key:
        return jsonify(error="No Anthropic API key configured yet."), 400
    if not os.path.isfile(COLLECTION_PATH):
        return jsonify(error="No ManaBox collection on file yet -- upload one from the home page first."), 400
    try:
        owned = load_collection(COLLECTION_PATH)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    owned_view = owned_collection_gameplay_view(owned, gameplay_data_in_index())

    wip_entries = None
    scope = "owned"
    import_unresolved: list[str] = []
    guessed_deck_name = None

    if mode == "improve":
        wip_entries = brew_to_card_entries({"commander": commander, "cards": body.get("cards") or []})
    elif mode == "import":
        import_url = (body.get("import_url") or "").strip()
        import_text = (body.get("import_text") or "").strip()
        if import_url:
            try:
                source, deck_id = parse_deck_ref(import_url)
                deck = fetch_deck(source, deck_id)
            except ValueError as e:
                return jsonify(error=str(e)), 400
            wip_entries = extract_entries(source, deck, include_sideboard=False, include_maybeboard=False)
            guessed_deck_name = deck.get("name") or deck_id
        elif import_text:
            parsed = parse_pasted_decklist(import_text)
            import_unresolved = parsed["unresolved"]
            commander_entry = CardEntry(
                name=commander.get("name", ""), quantity=1, type_line=commander.get("type_line", ""),
                is_foil=False, section="commander", scryfall_id=commander.get("scryfall_id"),
                color_identity=commander.get("color_identity") or [],
                set_code=commander.get("set_code", ""), collector_number=commander.get("collector_number", ""),
            )
            wip_entries = [commander_entry] + parsed["entries"] if commander.get("name") else parsed["entries"]
        else:
            return jsonify(error="Paste a decklist or provide a Moxfield/Archidekt URL first."), 400

        imported_commander = next((e for e in wip_entries if e.section == "commander"), None)
        if imported_commander:
            commander = {
                "name": imported_commander.name, "type_line": imported_commander.type_line,
                "color_identity": imported_commander.color_identity, "scryfall_id": imported_commander.scryfall_id,
                "set_code": imported_commander.set_code, "collector_number": imported_commander.collector_number,
            }
            deck_format = "commander"
        if not guessed_deck_name and commander.get("name"):
            guessed_deck_name = f"{commander['name']} (AI-improved)"
        scope = "any"

    if deck_format == "commander" and not commander.get("name"):
        return jsonify(error="Choose a commander first." if mode != "import" else "This import has no commander -- choose one in the builder first, or import a Commander deck/list."), 400

    # The commander's own oracle text is what ai_builder actually reasons
    # over (see its system prompt) -- look it up fresh from the gameplay
    # index rather than trusting whatever the client happened to have
    # cached, since that's the one payload this whole feature depends on.
    gp = gameplay_data_in_index().get(normalize_name(commander["name"])) if commander.get("name") else {}
    commander_full = {**commander, "oracle_text": (gp or {}).get("oracle_text", "")}
    target_size = 100 if deck_format == "commander" else 60

    job_id = uuid.uuid4().hex
    with _JOBS_LOCK:
        JOBS[job_id] = {
            "status": "running", "done": 0, "total": 0, "stage": None,
            "log": [], "deck_state": [], "suggestions": None, "removed": [], "final_entries": [], "maybeboard": [],
            "finished": False, "summary": "", "error": None, "import_unresolved": import_unresolved, "mode": mode,
            "guessed_deck_name": guessed_deck_name,
        }

    threading.Thread(
        target=_run_ai_build_job,
        args=(
            job_id, commander_full, deck_format, body.get("target_format"), target_size,
            commander.get("color_identity") if deck_format == "commander" else None,
            body.get("intended_bracket") or None, (body.get("user_notes") or "").strip(),
            owned_view, api_key,
        ),
        kwargs={"wip_entries": wip_entries, "scope": scope},
        daemon=True,
    ).start()

    return jsonify(job_id=job_id)


@app.route("/builder/ai-build/progress/<job_id>", methods=["GET"])
def builder_ai_build_progress(job_id):
    """Separate from /compare/progress on purpose -- that route's response
    shape is depended on by the compare/report flows, and this job tracks
    more (a running tool-call log + live deck state) than a plain
    done/total counter."""
    with _JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify(status="not_found"), 404
        status, done, total, error = job["status"], job["done"], job["total"], job["error"]
        stage, log, deck_state = job.get("stage"), job.get("log") or [], job.get("deck_state") or []
    return jsonify(status=status, done=done, total=total, stage=stage, log=log, deck_state=deck_state, error=error)


@app.route("/builder/ai-build/result/<job_id>", methods=["GET"])
def builder_ai_build_result(job_id):
    """Single-use, same convention as /compare/result -- deleted from JOBS
    once fetched, whether it finished or errored."""
    with _JOBS_LOCK:
        job = JOBS.get(job_id)
        if job and job["status"] in ("done", "error"):
            del JOBS[job_id]
    if not job:
        return jsonify(error="That build has expired or wasn't found -- try again."), 404
    if job["status"] == "error":
        return jsonify(error=job.get("error") or "The AI build failed."), 400
    if job["status"] != "done":
        return jsonify(error="That build hasn't finished yet."), 409
    return jsonify(
        suggestions=job["suggestions"], removed=job.get("removed") or [], finished=job["finished"],
        summary=job["summary"], import_unresolved=job.get("import_unresolved") or [],
        final_entries=job.get("final_entries") or [], mode=job.get("mode") or "fresh",
        guessed_deck_name=job.get("guessed_deck_name"), maybeboard=job.get("maybeboard") or [],
    )


@app.route("/builder/themes", methods=["POST"])
def builder_themes():
    """Oracle Tags themes (see list_theme_options) with enough owned,
    legal, color-correct candidates to be worth offering in the builder's
    "Preferred theme" picker -- recomputed whenever the commander/colors
    change client-side, same request shape as /builder/suggest."""
    body = request.get_json(silent=True) or {}
    deck_format = body.get("format") if body.get("format") in ("commander", "constructed") else "commander"
    if not os.path.isfile(COLLECTION_PATH):
        return jsonify(error="No ManaBox collection on file yet -- upload one from the home page first."), 400
    try:
        owned = load_collection(COLLECTION_PATH)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    owned_view = owned_collection_gameplay_view(owned, gameplay_data_in_index())
    wip_entries = brew_to_card_entries({"commander": body.get("commander"), "cards": body.get("cards") or []})
    commander = body.get("commander") or {}
    themes = list_theme_options(
        wip_entries, owned_view, deck_format, body.get("target_format"),
        commander_color_identity=commander.get("color_identity") if deck_format == "commander" else None,
        excluded_set_codes=_excluded_set_codes(body),
    )
    return jsonify(themes=themes)


@app.route("/builder/replace", methods=["POST"])
def builder_replace():
    """Suggested swap-ins for one specific card already in the deck (see
    suggest_replacements) -- same role, ranked by shared Oracle Tag then
    Game Changers, no live Commander Spellbook call since this backs a
    quick per-card popup, not a full re-suggest."""
    body = request.get_json(silent=True) or {}
    deck_format = body.get("format") if body.get("format") in ("commander", "constructed") else "commander"
    card_name = body.get("card_name")
    if not card_name:
        return jsonify(error="Missing card_name."), 400
    if not os.path.isfile(COLLECTION_PATH):
        return jsonify(error="No ManaBox collection on file yet -- upload one from the home page first."), 400
    try:
        owned = load_collection(COLLECTION_PATH)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    owned_view = owned_collection_gameplay_view(owned, gameplay_data_in_index())
    wip_entries = brew_to_card_entries({"commander": body.get("commander"), "cards": body.get("cards") or []})
    commander = body.get("commander") or {}
    replacements = suggest_replacements(
        card_name, body.get("category") or "", wip_entries, owned_view, deck_format, body.get("target_format"),
        commander_color_identity=commander.get("color_identity") if deck_format == "commander" else None,
        excluded_set_codes=_excluded_set_codes(body),
    )
    return jsonify(replacements=replacements)


@app.route("/builder/stats", methods=["POST"])
def builder_stats():
    """Live Game Changers/legality/bracket/combos/deck-value summary for
    the current work-in-progress brew, reusing build_comparison() exactly
    like a saved brew's full report does (see _run_brew_report_job) --
    every card in a brew is owned by construction, so this is really just
    "what would the compare-flow header stats say about this list right
    now," without paying for the full HTML render. On-demand (a button),
    not run on every card add/remove, since it makes a live Commander
    Spellbook call (combos/bracket) each time."""
    body = request.get_json(silent=True) or {}
    deck_format = body.get("format") if body.get("format") in ("commander", "constructed") else "commander"
    entries = brew_to_card_entries({"commander": body.get("commander"), "cards": body.get("cards") or []})
    if not entries:
        return jsonify(error="Add some cards first."), 400
    try:
        owned = load_collection(COLLECTION_PATH)
    except ValueError as e:
        return jsonify(error=str(e)), 400

    is_commander_format = deck_format == "commander"
    _, _, totals = build_comparison(entries, owned, ignore_basics=False, is_commander_format=is_commander_format)
    combos = totals.get("combos") or {}
    return jsonify(
        deck_value=totals["deck_value"],
        game_changers=totals["game_changers"],
        game_changers_names=totals["game_changers_names"],
        banned_count=totals["banned_count"],
        bracket_tag=totals.get("bracket_tag"),
        wotc_bracket=totals.get("wotc_bracket"),
        combos_included=len(combos.get("included") or []),
        combos_almost=combos.get("almost_total") or 0,
        # Full combo detail (uses/produces/url for "included"; uses/missing
        # for "almost") and the two other rule-0-relevant flags WotC's own
        # bracket rules key off of -- all for the Analyze modal's combo
        # reference section and rule-0 summary, not just the badge counts
        # the old Bracket & Value panel showed.
        combos_included_list=combos.get("included") or [],
        combos_almost_list=combos.get("almost_included") or [],
        mass_land_denial=totals.get("mass_land_denial") or False,
        extra_turn_count=totals.get("extra_turn_count") or 0,
        is_commander_format=is_commander_format,
    )


@app.route("/builder/report/start", methods=["POST"])
def builder_report_start():
    body = request.get_json(silent=True) or {}
    deck_id = body.get("deck_id")
    if not deck_id:
        return jsonify(error="Save the deck first."), 400
    brew = load_project(deck_id)
    if not brew or brew.get("type") != "brew":
        return jsonify(error="That brew could not be found."), 404
    entries = brew_to_card_entries(brew)
    if not entries:
        return jsonify(error="Add some cards first."), 400
    try:
        owned = load_collection(COLLECTION_PATH)
    except ValueError as e:
        return jsonify(error=str(e)), 400

    is_commander_format = brew.get("format") == "commander"
    job_id = uuid.uuid4().hex
    with _JOBS_LOCK:
        JOBS[job_id] = {"status": "running", "done": 0, "total": 0, "stage": None, "html": None, "error": None}

    threading.Thread(
        target=_run_brew_report_job,
        args=(job_id, entries, owned, deck_id, brew.get("deck_name") or "Brew", is_commander_format),
        kwargs={
            "ai_summary": brew.get("ai_summary") or "", "further_optimizations": brew.get("further_optimizations") or [],
            "maybeboard": brew.get("maybeboard") or [],
        },
        daemon=True,
    ).start()

    return jsonify(job_id=job_id)


@app.route("/price-index/refresh", methods=["POST"])
def refresh_price_index():
    """Force-rebuilds the local card database (see ensure_price_index
    in brewlist_core.py) regardless of how fresh it already is -- the home
    page's "Refresh Database" button. Runs in the same background-job +
    polling machinery as a deck comparison, but has no follow-up /compare/result
    step (see compare_progress's self-cleanup for jobs with no "html")."""
    job_id = uuid.uuid4().hex
    with _JOBS_LOCK:
        JOBS[job_id] = {"status": "running", "done": 0, "total": 0, "stage": None, "html": None, "error": None}

    def _run():
        def _on_progress(done, total, stage=None):
            with _JOBS_LOCK:
                job = JOBS.get(job_id)
                if job:
                    job["done"] = done
                    job["total"] = total
                    job["stage"] = stage
        try:
            ensure_price_index(on_progress=_on_progress, force_refresh=True)
            with _JOBS_LOCK:
                job = JOBS.get(job_id)
                if job:
                    job["status"] = "done"
        except Exception as e:  # noqa: BLE001 -- surface any failure to the polling client
            with _JOBS_LOCK:
                job = JOBS.get(job_id)
                if job:
                    job["status"] = "error"
                    job["error"] = str(e)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify(job_id=job_id)


@app.route("/update/check", methods=["POST"])
def check_for_updates():
    """Runs `git pull` in the app's own directory -- the home page's "Check
    for Updates" button. Synchronous (a git pull on a small text repo is
    fast, no need for the job-polling machinery the price index download
    uses). See update_from_git in brewlist_core.py for what each field of
    the response means."""
    return jsonify(update_from_git(APP_DIR))


@app.route("/compare/start", methods=["POST"])
def compare_start():
    deck_url_input = (request.form.get("moxfield_url") or "").strip()
    if not deck_url_input:
        return jsonify(error="A Moxfield or Archidekt deck URL is required."), 400

    source, deck_id = parse_deck_ref(deck_url_input)
    if not deck_id:
        return jsonify(error=f"Couldn't figure out a deck ID from '{deck_url_input}'."), 400
    project_key = deck_key(source, deck_id)

    include_sideboard = "include_sideboard" in request.form
    include_maybeboard = "include_maybeboard" in request.form
    break_out_basics = "break_out_basics" in request.form

    selected_stores = [s for s in request.form.getlist("stores") if s in PICKABLE_STORE_LABELS]
    save_store_prefs(selected_stores)

    # Collection upload is its own standalone action now (see
    # /collection/upload) -- Compare only ever reads whatever's already on
    # file, matching the two-step Setup-then-Decks flow.
    if not os.path.isfile(COLLECTION_PATH):
        return jsonify(error="No ManaBox collection on file yet -- upload your export on the Collection & Settings page first."), 400

    try:
        deck = fetch_deck(source, deck_id)
    except ValueError as e:
        return jsonify(error=str(e)), 400

    deck_name = deck.get("name", deck_id)
    if source == "archidekt":
        deck_url = f"https://archidekt.com/decks/{deck_id}"
    else:
        deck_url = deck.get("publicUrl", f"https://moxfield.com/decks/{deck_id}")
    entries = extract_entries(source, deck, include_sideboard, include_maybeboard)
    is_commander_format = deck_is_commander_format(source, deck)

    try:
        owned = load_collection(COLLECTION_PATH)
    except ValueError as e:
        return jsonify(error=str(e)), 400

    project = load_project(project_key)
    reserved = project.get("reserved") or {}
    options = {
        "include_sideboard": include_sideboard,
        "include_maybeboard": include_maybeboard,
        "break_out_basics": break_out_basics,
    }

    job_id = uuid.uuid4().hex
    with _JOBS_LOCK:
        JOBS[job_id] = {"status": "running", "done": 0, "total": 0, "stage": None, "html": None, "error": None}

    threading.Thread(
        target=_run_compare_job,
        args=(job_id, entries, owned, break_out_basics, reserved, project_key, deck_name, deck_url, options),
        kwargs={"is_commander_format": is_commander_format, "stores": selected_stores or None},
        daemon=True,
    ).start()

    return jsonify(job_id=job_id)


@app.route("/compare/progress/<job_id>", methods=["GET"])
def compare_progress(job_id):
    with _JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify(status="not_found"), 404
        status, done, total, error = job["status"], job["done"], job["total"], job["error"]
        stage = job.get("stage")
        # Deck-comparison jobs keep their finished HTML around for the
        # follow-up GET /compare/result/<job_id> to serve and clean up. Jobs
        # with no such follow-up (e.g. a bare price-index refresh) have
        # nothing to fetch afterward, so they're self-cleaning here instead.
        if status in ("done", "error") and not job.get("html"):
            del JOBS[job_id]
    return jsonify(status=status, done=done, total=total, stage=stage, error=error)


@app.route("/compare/result/<job_id>", methods=["GET"])
def compare_result(job_id):
    with _JOBS_LOCK:
        job = JOBS.get(job_id)
        if job and job["status"] == "done":
            del JOBS[job_id]  # single-use: the report is now fully in the client's hands
    if not job:
        return render_decks_page(error="That comparison has expired or wasn't found -- try again.")
    if job["status"] != "done":
        return render_decks_page(error=job.get("error") or "That comparison hasn't finished yet -- try again.")
    return Response(job["html"], mimetype="text/html")


@app.route("/api/overrides/<deck_id>", methods=["POST"])
def save_overrides(deck_id):
    body = request.get_json(silent=True) or {}
    reserved = body.get("reserved") or {}
    if not isinstance(reserved, dict):
        abort(400)
    # normalize keys/values defensively -- the browser already sends normalized
    # names, but don't trust the client for what ends up on disk.
    clean = {}
    for k, v in reserved.items():
        try:
            clean[normalize_name(str(k))] = int(v)
        except (ValueError, TypeError):
            continue

    project = load_project(deck_id)
    save_project(
        deck_id,
        deck_name=project.get("deck_name", deck_id),
        deck_url=project.get("deck_url", f"https://moxfield.com/decks/{deck_id}"),
        options=project.get("options", {}),
        reserved=clean,
    )
    return {"ok": True, "reserved_count": len(clean)}


@app.route("/shutdown", methods=["POST"])
def shutdown():
    # os._exit() rather than a graceful stop: this is a personal local dev
    # server, not something with in-flight work worth waiting on, and it
    # guarantees the process actually dies instead of lingering.
    import threading

    def _die():
        os._exit(0)

    threading.Timer(0.2, _die).start()
    return {"ok": True}


def _find_free_port(start: int = 5050, attempts: int = 50) -> int:
    """First port >= start that isn't already bound -- macOS's AirPlay
    Receiver often squats on 5000, so 5050 is a friendlier default. Pure
    stdlib (socket), so this behaves identically on macOS/Windows/Linux."""
    port = start
    for _ in range(attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
        port += 1
    return start  # give up scanning -- let Flask's own bind error surface


def _open_browser_when_ready(url: str, timeout: float = 15.0) -> None:
    """Polls `url` until the server actually answers, then opens the user's
    default browser -- webbrowser.open() picks the right mechanism per OS
    on its own (macOS 'open', Windows os.startfile, Linux xdg-open/$BROWSER),
    so this needs no OS-specific code. Runs in a background thread since
    app.run() below blocks the main one."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=0.5)
            webbrowser.open(url)
            return
        except (urllib.error.URLError, OSError):
            time.sleep(0.3)


if __name__ == "__main__":
    explicit_port = os.environ.get("PORT")
    port = int(explicit_port) if explicit_port else _find_free_port()
    # NO_BROWSER: set by the Docker image (no browser to open in a
    # container) -- webbrowser.open() can raise on a truly headless system
    # with no known browser command, which would otherwise print a scary
    # traceback to the container logs on every launch for no benefit.
    if not os.environ.get("NO_BROWSER"):
        threading.Thread(target=_open_browser_when_ready, args=(f"http://localhost:{port}",), daemon=True).start()
    threading.Thread(target=_check_for_updates_on_launch, daemon=True).start()
    # use_reloader=False: the reloader runs the app in a child process and
    # respawns it on exit, which would silently undo the /shutdown route above.
    # debug=True is kept for friendly in-browser tracebacks if something breaks.
    # threaded=True: the progress-polling requests need to be served while a
    # background thread is doing the actual (slow) Scryfall pricing work.
    # HOST: defaults to loopback-only (matches this being a personal local
    # app you double-click, not something meant reachable on the network by
    # default) -- the Docker image sets HOST=0.0.0.0 so the container's
    # published port actually works, since 127.0.0.1 inside a container
    # isn't reachable from outside it.
    app.run(debug=True, use_reloader=False, threaded=True, port=port, host=os.environ.get("HOST", "127.0.0.1"))
