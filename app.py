#!/usr/bin/env python3
"""
Flask web app for comparing a Moxfield decklist against your ManaBox
collection -- paste the deck URL, upload your ManaBox export once, and it
remembers both your collection and any "reserved for another deck" overrides
per project (deck) between visits. No terminal required.

Run it with:
    python3 app.py

Then open http://localhost:5000
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from flask import Flask, Response, abort, request

from mtg_core import (
    build_comparison,
    extract_entries,
    fetch_deck,
    load_collection,
    normalize_name,
    parse_deck_id,
    render_html,
)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, "data")
PROJECTS_DIR = os.path.join(DATA_DIR, "projects")
COLLECTION_PATH = os.path.join(DATA_DIR, "collection.csv")
COLLECTION_META_PATH = os.path.join(DATA_DIR, "collection_meta.json")

os.makedirs(PROJECTS_DIR, exist_ok=True)

app = Flask(__name__)


# --------------------------------------------------------------------------
# Project persistence -- one JSON file per Moxfield deck, remembering the
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
.page-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
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
#processing { display: none; color: var(--text-dim); font-size: 0.9rem; margin-top: 10px; }
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

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Moxfield vs. ManaBox</title>
<style>{PAGE_STYLE}</style>
</head>
<body>
<main>
  <div class="page-header">
    <div>
      <h1>Moxfield vs. ManaBox</h1>
      <p class="subtitle">Compare a decklist against your collection, with live pricing.</p>
    </div>
    <button type="button" class="btn small danger" id="shutdown-btn" title="Stops the local server">&#9209; Shut Down</button>
  </div>
  {error_html}
  {projects_html}
  <form class="card" method="post" action="/compare" enctype="multipart/form-data" id="compare-form">
    <label for="moxfield_url">Moxfield deck URL</label>
    <input type="url" id="moxfield_url" name="moxfield_url" placeholder="https://moxfield.com/decks/..." value="{_esc(prefill_url)}" required>

    {collection_html}
    <label for="manabox_csv">ManaBox collection export (.csv)</label>
    <input type="file" id="manabox_csv" name="manabox_csv" accept=".csv">
    <div class="hint">Leave empty to reuse the collection already on file.</div>

    <div class="checkbox-row"><input type="checkbox" id="include_sideboard" name="include_sideboard"><label for="include_sideboard" style="margin:0;font-weight:400;">Include sideboard cards</label></div>
    <div class="checkbox-row"><input type="checkbox" id="include_maybeboard" name="include_maybeboard"><label for="include_maybeboard" style="margin:0;font-weight:400;">Include maybeboard cards</label></div>
    <div class="checkbox-row"><input type="checkbox" id="break_out_basics" name="break_out_basics" checked><label for="break_out_basics" style="margin:0;font-weight:400;">Break out basic lands separately</label></div>

    <button type="submit" class="btn" id="submit-btn">Compare</button>
    <div id="processing">Fetching the deck and pricing your collection&hellip; this can take up to 20 seconds for a full deck.</div>
  </form>
</main>
<script>
document.getElementById('compare-form').addEventListener('submit', () => {{
  document.getElementById('submit-btn').disabled = true;
  document.getElementById('submit-btn').textContent = 'Working...';
  document.getElementById('processing').style.display = 'block';
}});

document.getElementById('shutdown-btn').addEventListener('click', () => {{
  if (!confirm('Shut down the server? You will need to relaunch it to use this again.')) return;
  fetch('/shutdown', {{ method: 'POST' }}).catch(() => {{}});
  document.body.innerHTML =
    '<main><p style="color:var(--text-dim);padding-top:40px;">Server stopped. You can close this tab.</p></main>';
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


@app.route("/compare", methods=["POST"])
def compare():
    moxfield_url = (request.form.get("moxfield_url") or "").strip()
    if not moxfield_url:
        return render_home_page(error="A Moxfield deck URL is required.")

    deck_id = parse_deck_id(moxfield_url)
    if not deck_id:
        return render_home_page(error=f"Couldn't figure out a deck ID from '{moxfield_url}'.", prefill_url=moxfield_url)

    include_sideboard = "include_sideboard" in request.form
    include_maybeboard = "include_maybeboard" in request.form
    break_out_basics = "break_out_basics" in request.form

    upload = request.files.get("manabox_csv")
    if upload and upload.filename:
        upload.save(COLLECTION_PATH)
        with open(COLLECTION_META_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "filename": upload.filename,
                "uploaded": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            }, f)
    elif not os.path.isfile(COLLECTION_PATH):
        return render_home_page(
            error="No ManaBox collection on file yet -- upload your export first.",
            prefill_url=moxfield_url,
        )

    try:
        deck = fetch_deck(deck_id)
    except ValueError as e:
        return render_home_page(error=str(e), prefill_url=moxfield_url)

    deck_name = deck.get("name", deck_id)
    deck_url = deck.get("publicUrl", f"https://moxfield.com/decks/{deck_id}")
    entries = extract_entries(deck, include_sideboard, include_maybeboard)

    try:
        owned = load_collection(COLLECTION_PATH)
    except ValueError as e:
        return render_home_page(error=str(e), prefill_url=moxfield_url)

    project = load_project(deck_id)
    reserved = project.get("reserved") or {}

    def _log_progress(done, total):
        print(f"Pricing owned cards via Scryfall... ({done}/{total})", flush=True)

    bucket_names, buckets, totals = build_comparison(
        entries, owned, ignore_basics=not break_out_basics, overrides=reserved,
        on_progress=_log_progress,
    )

    save_project(
        deck_id,
        deck_name=deck_name,
        deck_url=deck_url,
        options={
            "include_sideboard": include_sideboard,
            "include_maybeboard": include_maybeboard,
            "break_out_basics": break_out_basics,
        },
        reserved=reserved,
    )

    html_report = render_html(
        deck_name, deck_url, deck_id, bucket_names, buckets, totals,
        overrides_endpoint=f"/api/overrides/{deck_id}",
    )
    return Response(html_report, mimetype="text/html")


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
    app.run(debug=True, use_reloader=False, port=port)
