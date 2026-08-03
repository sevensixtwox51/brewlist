#!/usr/bin/env python3
"""
Flask web app for comparing a Moxfield or Archidekt decklist against your
ManaBox collection -- paste the deck URL, upload your ManaBox export once,
and it remembers both your collection and any "reserved for another deck"
overrides per project (deck) between visits. No terminal required.

Run it with:
    python3 app.py

Then open http://localhost:5000
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone

from flask import Flask, Response, abort, jsonify, request

from brewlist_core import (
    PICKABLE_STORE_LABELS,
    STORE_DISPLAY_NAMES,
    build_comparison,
    deck_is_commander_format,
    deck_key,
    ensure_price_index,
    extract_entries,
    fetch_deck,
    load_collection,
    load_store_prefs,
    normalize_name,
    parse_deck_ref,
    price_index_built_at,
    render_html,
    save_store_prefs,
)

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
main { max-width: 640px; margin: 0 auto; padding: 40px 24px 80px; }
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
  background: transparent; color: var(--text-dim);
  border: 1px solid var(--card-border); font-weight: 500;
}
.btn.danger:hover { color: var(--missing); border-color: var(--missing); filter: none; }
.btn.ghost {
  background: transparent; color: var(--text);
  border: 1px solid var(--card-border); font-weight: 500;
}
.btn.ghost:hover { color: var(--accent); border-color: var(--accent); filter: none; }
.page-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
.kofi-float {
  position: fixed; top: 70px; right: 16px; z-index: 40;
  display: block; border-radius: 8px; overflow: hidden;
  box-shadow: var(--shadow); transition: transform 0.15s ease;
}
.kofi-float:hover { transform: scale(1.05); }
.kofi-float img { display: block; height: 36px; width: auto; }
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
.project-list .when { color: var(--text-dim); font-size: 0.8rem; white-space: nowrap; }
.error { color: var(--missing); background: color-mix(in srgb, var(--missing) 12%, var(--bg-elevated));
  border: 1px solid var(--missing); border-radius: 8px; padding: 12px 16px; margin-bottom: 20px; }
#progress-wrap { display: none; margin-top: 14px; }
.progress-track { height: 8px; background: var(--card-border); border-radius: 4px; overflow: hidden; }
.progress-fill { height: 100%; width: 0%; background: linear-gradient(90deg, var(--owned), var(--accent)); transition: width 0.2s ease; }
#progress-label { color: var(--text-dim); font-size: 0.85rem; margin-top: 8px; }
"""


def render_home_page(error: str | None = None, prefill_url: str = "") -> str:
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

    projects = list_projects()
    if projects:
        items = "".join(
            f'<li><a href="/?deck={_esc(p.get("deck_url", p.get("deck_id", "")))}">'
            f'{_esc(p.get("deck_name", p.get("deck_id", "unknown")))}</a>'
            f'<span class="when">{_esc((p.get("updated") or "")[:16].replace("T", " "))}</span></li>'
            for p in projects[:15]
        )
        projects_html = f'<div class="card"><label>Recent decks</label><ul class="project-list">{items}</ul></div>'
    else:
        projects_html = ""

    error_html = f'<div class="error">{_esc(error)}</div>' if error else ""

    index_built_at = price_index_built_at()
    if index_built_at is None:
        index_status = 'Not built yet — first use downloads it (~325MB from MTGJSON, one-time, refreshed weekly after).'
    else:
        index_status = f'Updated {index_built_at.strftime("%Y-%m-%d %H:%M UTC")}.'

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

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/svg+xml" href="https://svgs.scryfall.io/card-symbols/PW.svg">
<title>Brewlist</title>
<style>{PAGE_STYLE}</style>
</head>
<body>
<a class="kofi-float" href="https://ko-fi.com/imtotallymeh" target="_blank" rel="noopener noreferrer" title="Support Brewlist on Ko-fi">
  <img src="https://storage.ko-fi.com/cdn/kofi5.png?v=3" alt="Support me on Ko-fi" loading="lazy">
</a>
<main>
  <div class="page-header">
    <div>
      <h1>Brewlist</h1>
      <p class="subtitle">Compare a decklist against your collection, with live pricing.</p>
    </div>
    <button type="button" class="btn small danger" id="shutdown-btn" title="Stops the local server">&#9209; Shut Down</button>
  </div>
  <div id="error-box" class="error" style="display:{"block" if error else "none"};">{_esc(error) if error else ""}</div>
  {projects_html}
  <form class="card" id="compare-form">
    <label for="moxfield_url">Moxfield or Archidekt deck URL</label>
    <input type="url" id="moxfield_url" name="moxfield_url" placeholder="https://moxfield.com/decks/... or https://archidekt.com/decks/..." value="{_esc(prefill_url)}" required>

    {collection_html}
    <label for="manabox_csv">ManaBox collection export (.csv)</label>
    <input type="file" id="manabox_csv" name="manabox_csv" accept=".csv">
    <div class="hint">Leave empty to reuse the collection already on file.</div>

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
  <div class="card" id="price-index-card">
    <label>Card database</label>
    <div class="hint" style="margin-top:-8px;margin-bottom:8px;">Pricing, price trends, Game Changers, and Commander legality all come from this local MTGJSON-based database.</div>
    <div class="collection-status" id="price-index-status">{_esc(index_status)}</div>
    <button type="button" class="btn ghost small" id="refresh-index-btn">&#128260; Refresh Database</button>
    <div id="refresh-index-label" class="hint" style="display:none;margin-top:8px;"></div>
  </div>
</main>
<script>
const form = document.getElementById('compare-form');
const submitBtn = document.getElementById('submit-btn');
const progressWrap = document.getElementById('progress-wrap');
const progressFill = document.getElementById('progress-fill');
const progressLabel = document.getElementById('progress-label');
const errorBox = document.getElementById('error-box');

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

document.getElementById('shutdown-btn').addEventListener('click', () => {{
  if (!confirm('Shut down the server? You will need to relaunch it to use this again.')) return;
  fetch('/shutdown', {{ method: 'POST' }}).catch(() => {{}});
  document.body.innerHTML =
    '<main><p style="color:var(--text-dim);padding-top:40px;">Server stopped. You can close this tab.</p></main>';
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
        refreshIndexLabel.textContent = data.total > 0
          ? 'Downloading\\u2026 (' + fmtIndexProgress(data.done, data.total) + ')'
          : 'Starting\\u2026';
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
</script>
</body>
</html>
"""


def _esc(s) -> str:
    import html as _html
    return _html.escape(str(s or ""))


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def home():
    prefill = request.args.get("deck", "")
    return render_home_page(prefill_url=prefill)


@app.route("/price-index/refresh", methods=["POST"])
def refresh_price_index():
    """Force-rebuilds the local card database (see ensure_price_index
    in brewlist_core.py) regardless of how fresh it already is -- the home
    page's "Refresh Database" button. Runs in the same background-job +
    polling machinery as a deck comparison, but has no follow-up /compare/result
    step (see compare_progress's self-cleanup for jobs with no "html")."""
    job_id = uuid.uuid4().hex
    with _JOBS_LOCK:
        JOBS[job_id] = {"status": "running", "done": 0, "total": 0, "html": None, "error": None}

    def _run():
        def _on_progress(done, total):
            with _JOBS_LOCK:
                job = JOBS.get(job_id)
                if job:
                    job["done"] = done
                    job["total"] = total
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

    upload = request.files.get("manabox_csv")
    if upload and upload.filename:
        upload.save(COLLECTION_PATH)
        with open(COLLECTION_META_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "filename": upload.filename,
                "uploaded": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            }, f)
    elif not os.path.isfile(COLLECTION_PATH):
        return jsonify(error="No ManaBox collection on file yet -- upload your export first."), 400

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
        return render_home_page(error="That comparison has expired or wasn't found -- try again.")
    if job["status"] != "done":
        return render_home_page(error=job.get("error") or "That comparison hasn't finished yet -- try again.")
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # use_reloader=False: the reloader runs the app in a child process and
    # respawns it on exit, which would silently undo the /shutdown route above.
    # debug=True is kept for friendly in-browser tracebacks if something breaks.
    # threaded=True: the progress-polling requests need to be served while a
    # background thread is doing the actual (slow) Scryfall pricing work.
    app.run(debug=True, use_reloader=False, threaded=True, port=port)
