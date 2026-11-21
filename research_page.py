"""The Commander research page (/research): pick any commander, see how people build it, how much of that you own, and
what that build is weak to.

Pure presentation for research.py's numbers. app.py supplies the shared page shell (style, header, footer, scripts) and
the routes the page calls; the Matchups panel is drawn with the code shared with Analyze (matchups_ui.py).
"""
from matchups_ui import MATCHUPS_CSS, MATCHUPS_JS

RESEARCH_STYLE = """
.stat-tiles { display:grid; grid-template-columns:repeat(auto-fit, minmax(170px, 1fr)); gap:10px; margin:0 0 16px; }
.stat-tile { background:var(--bg-elevated); border:1px solid var(--card-border); border-radius:10px; padding:12px 14px 13px; min-width:0; }
.stat-value { font-family:var(--serif); font-size:1.9rem; font-weight:600; line-height:1.05; color:var(--accent); font-variant-numeric:tabular-nums; }
.stat-tile.good .stat-value { color:var(--owned); } .stat-tile.ok .stat-value { color:var(--gold); } .stat-tile.bad .stat-value { color:var(--missing); }
.stat-label { margin-top:5px; font-size:0.7rem; text-transform:uppercase; letter-spacing:0.07em; color:var(--text-dim); }
.stat-sub { margin-top:2px; font-size:0.74rem; color:var(--text-dim); line-height:1.3; }
.ins-back { margin: 8px 0 16px; display:flex; gap:8px; flex-wrap:wrap; }
.rs-pick { background:var(--bg-elevated); border:1px solid var(--card-border); border-radius:12px; padding:16px 18px 18px; margin-bottom:18px; }
.rs-pick h2, .rs-section h2 { font-family:var(--serif); font-weight:600; font-size:1.3rem; color:var(--accent); margin:0 0 4px; }
.rs-sub { color:var(--text-dim); font-size:0.85rem; margin:0 0 12px; max-width:75ch; }
.rs-controls { display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin:0 0 14px; }
.rs-controls input[type=text] { flex:1 1 280px; min-width:0; padding:9px 12px; border-radius:9px; border:1px solid var(--card-border); background:var(--bg); color:var(--text); font-size:0.95rem; }
.rs-controls select { padding:9px 10px; border-radius:9px; border:1px solid var(--card-border); background:var(--bg); color:var(--text); }
.rs-results { display:grid; grid-template-columns:repeat(auto-fill, minmax(150px, 1fr)); gap:14px; }
.rs-tile { cursor:pointer; position:relative; min-width:0; border:2px solid transparent; border-radius:12px; padding:5px 5px 7px; }
.rs-tile:hover { border-color:var(--accent); }
.rs-tile img { width:100%; aspect-ratio:5/7; display:block; border-radius:6.5%; background:var(--bg); box-shadow:0 2px 10px rgba(0,0,0,0.5); }
.rs-tile .nm { font-size:0.82rem; font-weight:600; margin-top:6px; line-height:1.2; }
.rs-tile .meta { font-size:0.72rem; color:var(--text-dim); display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.rs-tile .meta img { width:14px; height:14px; aspect-ratio:auto; box-shadow:none; display:inline-block; }
.rs-badge { font-size:0.68rem; font-weight:700; padding:1px 7px; border-radius:999px; border:1px solid currentColor; }
.rs-badge.own { color:var(--owned); } .rs-badge.no { color:var(--text-dim); }
.rs-banner { display:flex; gap:20px; align-items:flex-start; flex-wrap:wrap; margin:0 0 18px; }
.rs-banner img.big { width:220px; border-radius:7%; box-shadow:0 6px 22px rgba(0,0,0,0.6); background:var(--bg); }
.rs-banner .info { flex:1 1 320px; min-width:0; }
.rs-banner h1 { font-family:var(--serif); font-size:1.9rem; margin:0 0 4px; }
.rs-banner .meta { color:var(--text-dim); font-size:0.9rem; display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin-bottom:10px; }
.rs-banner .meta img { width:18px; height:18px; }
.rs-archs { display:flex; flex-wrap:wrap; gap:8px; margin:6px 0 4px; }
.rs-arch { cursor:pointer; padding:7px 14px; border-radius:999px; border:1px solid var(--card-border); background:var(--bg); color:var(--text); font-size:0.9rem; }
.rs-arch small { color:var(--text-dim); margin-left:5px; }
.rs-arch:hover { border-color:var(--accent); }
.rs-arch.on { border-color:var(--accent); background:color-mix(in srgb, var(--accent) 16%, var(--bg)); color:var(--accent); font-weight:600; }
.rs-section { margin:0 0 24px; }
.rs-tabs { display:inline-flex; border:1px solid var(--card-border); border-radius:9px; overflow:hidden; margin:0 0 12px; }
.rs-tabs button { background:var(--bg); color:var(--text-dim); border:0; padding:7px 14px; cursor:pointer; font-size:0.85rem; }
.rs-tabs button.on { background:color-mix(in srgb, var(--accent) 16%, var(--bg)); color:var(--accent); font-weight:600; }
.rs-cards { display:grid; grid-template-columns:repeat(auto-fill, minmax(128px, 1fr)); gap:12px; }
.rs-card { position:relative; min-width:0; }
.rs-card img { width:100%; aspect-ratio:5/7; display:block; border-radius:6.5%; background:var(--bg); box-shadow:0 2px 8px rgba(0,0,0,0.5); }
.rs-card.miss img { opacity:0.8; }
.rs-card .nm { font-size:0.74rem; font-weight:600; margin-top:5px; line-height:1.2; }
.rs-card .sub { font-size:0.68rem; color:var(--text-dim); }
.rs-card .pic { position:relative; }
.rs-card .pill { position:absolute; left:6px; bottom:6px; background:rgba(0,0,0,0.78); color:var(--text); font-weight:600; font-size:0.68rem; padding:1px 7px; border-radius:999px; }
.rs-card .price { display:inline-block; margin-top:2px; font-size:0.72rem; font-weight:600; color:var(--gold); }
.rs-loading { color:var(--text-dim); padding:26px 0; text-align:center; }
#rs-zoom { position:fixed; z-index:500; display:none; pointer-events:none; width:300px; border-radius:14px; box-shadow:0 10px 34px rgba(0,0,0,0.75); background:#000; }
#rs-zoom.show { display:block; }
#rs-zoom img { display:block; width:100%; border-radius:14px; }
"""

RESEARCH_BODY = """
  <div class="ins-back"><a href="/decks" class="btn ghost small">&larr; Decks</a><button type="button" class="btn ghost small" id="rs-change" style="display:none;">&#8634; Pick a different commander</button></div>
  <div id="rs-pick" class="rs-pick">
    <h2>Find a commander</h2>
    <p class="rs-sub">Any Commander-legal commander, owned or not, most popular first. Pick one to see how people build it, how much of that you own, and what that build is weak to.</p>
    <div class="rs-controls">
      <input type="text" id="rs-search" placeholder="Search by name, e.g. Teysa Karlov" autocomplete="off">
      <select id="rs-window" aria-label="Popularity window" title="How popular each commander is, counted over this period (the same choice the builder offers)">
        <option value="2years">Popularity: past 2 years</option>
        <option value="month">Popularity: past month</option>
        <option value="week">Popularity: past week</option>
      </select>
      <select id="rs-scope" aria-label="Which commanders">
        <option value="all">All commanders</option>
        <option value="owned">Ones I own</option>
        <option value="unowned">Ones I don't own</option>
      </select>
    </div>
    <div id="rs-results" class="rs-results"></div>
  </div>
  <div id="rs-detail"></div>
  <div id="rs-zoom"><img alt=""></div>
"""

RESEARCH_SCRIPT = r"""
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const escapeHtml = esc;
const fmt = (n) => Number(n).toLocaleString();
const imgUrl = (id, size, face) => id ? 'https://cards.scryfall.io/' + (size || 'normal') + '/' + (face || 'front') + '/' + id[0] + '/' + id[1] + '/' + id + '.jpg' : '';
const muImg = (id) => imgUrl(id, 'normal');
const icons = (ci) => (ci || []).map((c) => '<img src="/static/mana/' + c + '.svg" alt="' + c + '">').join('');
const money = (n) => '$' + Number(n).toFixed(2);

__MATCHUPS_JS__

const pickEl = document.getElementById('rs-pick'), resultsEl = document.getElementById('rs-results');
const detailEl = document.getElementById('rs-detail'), changeBtn = document.getElementById('rs-change');
const searchEl = document.getElementById('rs-search'), scopeEl = document.getElementById('rs-scope'), windowEl = document.getElementById('rs-window');
const WINDOW_LABEL = { '2years': 'past 2 years', month: 'past month', week: 'past week' };
let searchSeq = 0, selected = null, archetype = null, deckSeq = 0;
const deckCache = {};

// ---------- finding a commander ----------
function tileHtml(c) {
  return '<div class="rs-tile" data-name="' + esc(c.name) + '" data-full="' + esc(imgUrl(c.id, 'normal')) + '">'
    + '<img loading="lazy" alt="' + esc(c.name) + '" src="' + imgUrl(c.id, 'normal') + '">'
    + '<div class="nm">' + esc(c.name) + '</div>'
    + '<div class="meta">' + icons(c.color_identity) + (c.rank ? '<span title="Rank as a commander, ' + WINDOW_LABEL[windowEl.value] + '">#' + fmt(c.rank) + '</span>' : '<span title="Not played as a commander in this period">unranked</span>')
    + '<span class="rs-badge ' + (c.owned ? 'own' : 'no') + '">' + (c.owned ? '&#10003; Owned' : 'Not owned') + '</span></div></div>';
}
function runSearch() {
  const mine = ++searchSeq;
  resultsEl.innerHTML = '<div class="rs-loading">Searching&hellip;</div>';
  fetch('/research/search?q=' + encodeURIComponent(searchEl.value.trim()) + '&scope=' + scopeEl.value + '&window=' + windowEl.value).then((r) => r.json()).then((d) => {
    if (mine !== searchSeq) return;
    if (d.error) { resultsEl.innerHTML = '<div class="rs-loading">' + esc(d.error) + '</div>'; return; }
    resultsEl.innerHTML = d.results.length ? d.results.map(tileHtml).join('') : '<div class="rs-loading">No commanders match.</div>';
  }).catch(() => { if (mine === searchSeq) resultsEl.innerHTML = '<div class="rs-loading">Could not reach the server.</div>'; });
}
let searchTimer = null;
searchEl.addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 200); });
scopeEl.addEventListener('change', runSearch);
windowEl.addEventListener('change', runSearch);
resultsEl.addEventListener('click', (e) => { const t = e.target.closest('.rs-tile'); if (t) chooseCommander(t.dataset.name); });
changeBtn.addEventListener('click', () => { selected = null; deckSeq++; detailEl.innerHTML = ''; pickEl.style.display = ''; changeBtn.style.display = 'none'; hideZoom(); window.scrollTo({ top: 0, behavior: 'smooth' }); });

// ---------- one commander ----------
function chooseCommander(name) {
  const mine = ++deckSeq;
  pickEl.style.display = 'none'; changeBtn.style.display = ''; hideZoom();
  detailEl.innerHTML = '<div class="rs-loading">Looking up ' + esc(name) + '&hellip; (the first look at a commander that isn’t saved yet needs the internet)</div>';
  window.scrollTo({ top: 0 });
  fetch('/research/commander?name=' + encodeURIComponent(name) + '&window=' + windowEl.value).then((r) => r.json()).then((d) => {
    if (mine !== deckSeq) return;
    if (d.error) { detailEl.innerHTML = '<div class="rs-pick"><p>' + esc(d.error) + '</p></div>'; return; }
    selected = d; archetype = null;
    renderCommander(d);
    if (d.archetypes.length) chooseArchetype(d.archetypes[0].slug);
  }).catch(() => { if (mine === deckSeq) detailEl.innerHTML = '<div class="rs-pick"><p>Could not reach the server.</p></div>'; });
}
function renderCommander(d) {
  const c = d.commander;
  const own = c.owned ? '<span class="rs-badge own">&#10003; You own this commander</span>' : '<span class="rs-badge no">You don’t own this commander</span>';
  const archs = d.archetypes.length
    ? '<div class="rs-archs">' + d.archetypes.map((a) => '<button type="button" class="rs-arch" data-slug="' + esc(a.slug) + '">' + esc(a.value) + '<small>' + fmt(a.count) + ' decks</small></button>').join('') + '</div>'
    : '<p class="rs-sub">' + esc(d.problem || 'EDHREC has no deck types for this commander.') + '</p>';
  detailEl.innerHTML =
    '<div class="rs-banner"><img class="big" data-full="' + esc(imgUrl(c.id, 'normal')) + '" alt="' + esc(c.name) + '" src="' + imgUrl(c.id, 'normal') + '">'
    + '<div class="info"><h1>' + esc(c.name) + '</h1>'
    + '<div class="meta">' + icons(c.color_identity) + '<span>' + esc(c.type_line || '') + '</span>' + (c.rank ? '<span>#' + fmt(c.rank) + ' commander on EDHREC (' + WINDOW_LABEL[windowEl.value] + ')</span>' : '<span>Not ranked as a commander in this period</span>') + own + '</div>'
    + '<div id="rs-buildable"></div>'
    + '<div class="rs-section" style="margin:6px 0 0;"><h2>How people build it</h2><p class="rs-sub">EDHREC’s deck types for this commander, most played first. Pick one.</p>' + archs + '</div></div></div>'
    + '<div id="rs-deck"></div>';
  detailEl.querySelectorAll('.rs-arch').forEach((b) => b.addEventListener('click', () => chooseArchetype(b.dataset.slug)));
  loadBuildable(c.name, d.score);
}
function loadBuildable(name, cached) {
  const el = document.getElementById('rs-buildable');
  const show = (s) => {
    if (!el || !s || s.status === 'no-data' || s.pct == null) { if (el) el.innerHTML = ''; return; }
    el.innerHTML = '<div class="stat-tiles" style="margin:0 0 6px;"><div class="stat-tile ' + (s.pct >= 80 ? 'good' : s.pct >= 50 ? 'ok' : 'bad') + '"><div class="stat-value">' + s.pct + '%</div>'
      + '<div class="stat-label">Buildable from your collection</div><div class="stat-sub">How much of the ' + esc(s.deck_type || 'standard') + ' build your own cards could fill with EDHREC-backed picks, not necessarily the same cards as the typical list below (the percentage on the commander tiles in the builder).</div></div></div>';
  };
  if (cached) { show(cached); return; }
  if (el) el.innerHTML = '<p class="rs-sub">Working out what your collection could build&hellip;</p>';
  fetch('/research/buildable?name=' + encodeURIComponent(name)).then((r) => r.json()).then((d) => { if (selected && selected.commander.name === name) show(d.score); }).catch(() => { if (el) el.innerHTML = ''; });
}

// ---------- one archetype ----------
function chooseArchetype(slug) {
  archetype = slug;
  detailEl.querySelectorAll('.rs-arch').forEach((b) => b.classList.toggle('on', b.dataset.slug === slug));
  const deckEl = document.getElementById('rs-deck');
  const key = selected.commander.name + '|' + slug;
  if (deckCache[key]) { renderDeck(deckCache[key]); return; }
  const mine = ++deckSeq;
  deckEl.innerHTML = '<div class="rs-loading">Building the typical list and checking it against your collection&hellip;</div>';
  fetch('/research/deck?name=' + encodeURIComponent(selected.commander.name) + '&theme=' + encodeURIComponent(slug)).then((r) => r.json()).then((d) => {
    if (mine !== deckSeq || archetype !== slug) return;
    if (d.error) { deckEl.innerHTML = '<div class="rs-pick"><p>' + esc(d.error) + '</p></div>'; return; }
    deckCache[key] = d; renderDeck(d);
  }).catch(() => { if (mine === deckSeq) deckEl.innerHTML = '<div class="rs-pick"><p>Could not reach the server.</p></div>'; });
}
function cardTile(c, missing) {
  return '<div class="rs-card' + (missing ? ' miss' : '') + '" data-full="' + esc(imgUrl(c.id, 'normal')) + '"><div class="pic"><img loading="lazy" alt="' + esc(c.name) + '" src="' + imgUrl(c.id, 'normal') + '">'
    + '<span class="pill">' + c.inclusion + '% of decks</span></div><div class="nm">' + esc(c.name) + '</div>'
    + (missing ? (c.price != null ? '<span class="price">' + money(c.price) + '</span>' : '<span class="sub">no price found</span>') : '<span class="sub">&#10003; you own it</span>') + '</div>';
}
function renderDeck(d) {
  const deckEl = document.getElementById('rs-deck');
  const c = d.commander;
  const miss = d.cards.filter((x) => !x.owned), have = d.cards.filter((x) => x.owned);
  const pctCls = d.owned_pct >= 80 ? 'good' : d.owned_pct >= 50 ? 'ok' : 'bad';
  // The banner already says whether you own the commander, so this tile only appears when there is something to add:
  // what it costs to get one.
  const cmdTile = c.owned ? '' : '<div class="stat-tile"><div class="stat-value">' + (c.price != null ? money(c.price) : '&mdash;') + '</div>'
    + '<div class="stat-label">To buy the commander</div><div class="stat-sub">' + (c.price != null ? 'Cheapest copy found.' : 'No price found.') + '</div></div>';
  deckEl.innerHTML =
    '<div class="rs-section"><h2>What you own of it</h2><p class="rs-sub">' + esc(d.note) + '</p>'
    + '<div class="stat-tiles">'
    + '<div class="stat-tile ' + pctCls + '"><div class="stat-value">' + d.owned_pct + '%</div><div class="stat-label">of the typical list you own</div><div class="stat-sub">' + d.owned_count + ' of ' + d.total_count + ' non-basic cards</div></div>'
    + '<div class="stat-tile"><div class="stat-value">' + money(d.missing_cost) + '</div><div class="stat-label">to complete it</div><div class="stat-sub">' + miss.length + ' cards, cheapest copies' + (d.missing_unpriced ? ' (' + d.missing_unpriced + ' have no price)' : '') + '</div></div>'
    + cmdTile + '</div>'
    + '<div class="rs-tabs"><button type="button" class="on" data-tab="miss">Missing (' + miss.length + ')</button><button type="button" data-tab="have">You own (' + have.length + ')</button></div>'
    + '<div class="rs-cards" id="rs-cardlist">' + (miss.length ? miss.map((x) => cardTile(x, true)).join('') : '<p class="rs-sub">You own every card in the typical list.</p>') + '</div></div>'
    + '<div class="rs-section"><h2>Weaknesses and strengths of this build</h2><p class="rs-sub">Worked out from the typical list above. Handy if someone keeps beating you with this commander: the pills marked &#10003; are cards you already own that you could add to a deck of yours. Hover any pill to see the card.</p>'
    + '<div id="rs-matchups">' + muRender(d.matchups, { deckWord: 'this build', intro: 'Cards that punish this build.' }) + '</div></div>';
  deckEl.querySelectorAll('.rs-tabs button').forEach((b) => b.addEventListener('click', () => {
    deckEl.querySelectorAll('.rs-tabs button').forEach((x) => x.classList.toggle('on', x === b));
    const showMiss = b.dataset.tab === 'miss';
    const list = showMiss ? miss : have;
    document.getElementById('rs-cardlist').innerHTML = list.length ? list.map((x) => cardTile(x, showMiss)).join('') : '<p class="rs-sub">' + (showMiss ? 'You own every card in the typical list.' : 'You own none of them yet.') + '</p>';
  }));
}

// ---------- hover: enlarge any card ----------
const zoomEl = document.getElementById('rs-zoom'), zoomImg = zoomEl.querySelector('img');
let zoomSrc = null;
function hideZoom() { zoomSrc = null; zoomEl.classList.remove('show'); }
document.addEventListener('mousemove', (e) => {
  const t = e.target.closest ? e.target.closest('[data-full]') : null;
  if (!t || !t.dataset.full) { if (zoomSrc) hideZoom(); return; }
  if (t.dataset.full !== zoomSrc) { zoomSrc = t.dataset.full; zoomImg.src = zoomSrc; zoomEl.classList.add('show'); }
  const pad = 22, w = 300, h = zoomEl.offsetHeight || 420;
  let x = e.clientX + pad, y = e.clientY - h / 2;
  if (x + w > window.innerWidth - 8) x = e.clientX - w - pad;
  y = Math.max(8, Math.min(y, window.innerHeight - h - 8));
  zoomEl.style.left = Math.max(8, x) + 'px'; zoomEl.style.top = y + 'px';
});
document.addEventListener('scroll', hideZoom, true);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hideZoom(); });

// An opposing-commander pill (here or in Analyze) opens that commander; here it switches in place.
detailEl.addEventListener('click', (e) => {
  const a = e.target.closest ? e.target.closest('a.mu-card.cmdr') : null;
  if (!a) return;
  e.preventDefault();
  chooseCommander(decodeURIComponent((a.getAttribute('href') || '').split('commander=')[1] || ''));
});
runSearch();
const wanted = new URLSearchParams(location.search).get('commander');
if (wanted) chooseCommander(wanted);
""".replace("__MATCHUPS_JS__", MATCHUPS_JS)


def render_research_page(page_style: str, header_html: str, footer_html: str, shared_scripts: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/svg+xml" href="https://svgs.scryfall.io/card-symbols/PW.svg">
<title>Brewlist -- Commander research</title>
<style>{page_style}</style>
<style>{RESEARCH_STYLE}{MATCHUPS_CSS}</style>
</head>
<body>
<main class="wide">
  {header_html}
  {RESEARCH_BODY}
  {footer_html}
</main>
<script>
{RESEARCH_SCRIPT}
{shared_scripts}
</script>
</body>
</html>
"""
