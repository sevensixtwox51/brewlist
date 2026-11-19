"""The Collection Insights page (/collection): where your collection is deep and where it's thin.

Pure presentation for collection_insights.py's numbers. app.py supplies the shared page shell (style, header, footer,
scripts) so this module doesn't import it; the page fetches /collection/data once it has loaded.
"""

COLLECTION_STYLE = """
.stat-tiles { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:10px; margin:0 0 18px; }
.stat-tile { background:var(--bg-elevated); border:1px solid var(--card-border); border-radius:10px; padding:12px 14px 13px; min-width:0; }
.stat-value { font-family:var(--serif); font-size:2rem; font-weight:600; line-height:1.05; color:var(--accent); font-variant-numeric:tabular-nums; }
.stat-label { margin-top:5px; font-size:0.7rem; text-transform:uppercase; letter-spacing:0.07em; color:var(--text-dim); }
.stat-sub { margin-top:2px; font-size:0.74rem; color:var(--text-dim); line-height:1.3; }
.ins-back { margin: 8px 0 16px; }
.ins-loading { color: var(--text-dim); padding: 40px 0; text-align: center; }
.ins-section { margin-bottom: 22px; }
.ins-section h2 { font-family: var(--serif); font-weight: 600; font-size: 1.35rem; color: var(--accent); margin: 0 0 4px; }
.ins-section .ins-sub { color: var(--text-dim); font-size: 0.85rem; margin: 0 0 12px; max-width: 70ch; }
.ins-colorbar { display: flex; height: 34px; border-radius: 9px; overflow: hidden; border: 1px solid var(--card-border); }
.ins-colorbar > div { display: flex; align-items: center; justify-content: center; min-width: 2px; font-size: 0.78rem; font-weight: 700; color: #1b1712; }
.ins-legend { display: flex; flex-wrap: wrap; gap: 8px 18px; margin-top: 10px; font-size: 0.85rem; color: var(--text-dim); }
.ins-legend span { display: inline-flex; align-items: center; gap: 6px; }
.ins-legend img { width: 18px; height: 18px; }
.ins-legend b { color: var(--text); font-variant-numeric: tabular-nums; }
.ins-chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 12px; align-items: center; }
.ins-chips .lbl { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.07em; color: var(--text-dim); margin-right: 4px; }
.ins-chip { padding: 3px 10px; border-radius: 999px; border: 1px solid var(--card-border); font-size: 0.82rem; background: var(--bg); }
.ins-chip.bad { border-color: color-mix(in srgb, var(--missing) 60%, var(--card-border)); color: var(--missing); }
.ins-chip.good { border-color: color-mix(in srgb, var(--owned) 55%, var(--card-border)); color: var(--owned); }
.ins-table-wrap { overflow-x: auto; }
table.ins-grid { border-collapse: separate; border-spacing: 4px; width: 100%; min-width: 640px; table-layout: fixed; }
.ins-grid th.role { width: 225px; }
.ins-grid th { font-weight: 600; font-size: 0.8rem; color: var(--text-dim); text-align: center; padding: 2px 4px 6px; }
.ins-grid th img { width: 20px; height: 20px; display: block; margin: 0 auto 2px; }
.ins-grid th.role { text-align: left; color: var(--text); font-size: 0.9rem; white-space: nowrap; padding-right: 10px; }
.ins-grid th.role small { color: var(--text-dim); font-weight: 400; margin-left: 4px; }
.ins-grid td { text-align: center; padding: 9px 4px; border-radius: 7px; background: var(--bg); border: 1px solid var(--card-border);
  font-variant-numeric: tabular-nums; font-weight: 600; font-size: 0.95rem; cursor: default; }
.ins-grid td.gap { background: color-mix(in srgb, var(--missing) 26%, var(--bg)); border-color: color-mix(in srgb, var(--missing) 60%, var(--card-border)); color: var(--missing); }
.ins-grid td.thin { background: color-mix(in srgb, var(--gold) 18%, var(--bg)); border-color: color-mix(in srgb, var(--gold) 50%, var(--card-border)); color: var(--gold); }
.ins-grid td.deep { background: color-mix(in srgb, var(--owned) 20%, var(--bg)); border-color: color-mix(in srgb, var(--owned) 50%, var(--card-border)); color: var(--owned); }
.ins-key { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 10px; font-size: 0.78rem; color: var(--text-dim); }
.ins-key i { display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin-right: 5px; vertical-align: -1px; border: 1px solid var(--card-border); }
.ins-theme { display: grid; grid-template-columns: minmax(150px, 230px) 1fr 150px; gap: 12px; align-items: center; padding: 7px 0; border-bottom: 1px solid var(--card-border); }
.ins-theme:last-child { border-bottom: none; }
.ins-theme .nm { font-weight: 600; }
.ins-theme .nm small { display: block; color: var(--text-dim); font-weight: 400; font-size: 0.75rem; }
.ins-meter { position: relative; height: 12px; border-radius: 6px; background: var(--bg); border: 1px solid var(--card-border); overflow: visible; }
.ins-meter .fill { height: 100%; border-radius: 6px; background: var(--accent); }
.ins-meter .mid { position: absolute; top: -3px; bottom: -3px; width: 2px; background: var(--text-dim); opacity: 0.7; }
.ins-theme.deep .fill { background: var(--owned); }
.ins-theme.thin .fill { background: var(--missing); }
.ins-pill { justify-self: end; font-size: 0.72rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; padding: 3px 10px; border-radius: 999px; border: 1px solid var(--card-border); color: var(--text-dim); }
.ins-theme.deep .ins-pill { color: var(--owned); border-color: color-mix(in srgb, var(--owned) 55%, var(--card-border)); }
.ins-theme.thin .ins-pill { color: var(--missing); border-color: color-mix(in srgb, var(--missing) 55%, var(--card-border)); }
.ins-two { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.ins-bars .row { display: grid; grid-template-columns: 110px 1fr 80px; gap: 10px; align-items: center; margin: 5px 0; font-size: 0.88rem; }
.ins-bars .bar { height: 12px; border-radius: 6px; background: var(--accent); min-width: 2px; }
.ins-bars .num { text-align: right; color: var(--text-dim); font-variant-numeric: tabular-nums; }
.ins-curve { display: flex; align-items: flex-end; gap: 8px; height: 140px; margin-top: 6px; }
.ins-curve .col { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; height: 100%; font-size: 0.78rem; color: var(--text-dim); }
.ins-curve .col .bar { width: 100%; background: var(--accent); border-radius: 5px 5px 0 0; min-height: 2px; }
.ins-curve .col b { color: var(--text); font-weight: 600; font-variant-numeric: tabular-nums; margin-bottom: 3px; }
.ins-tribes { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
.ins-note { color: var(--text-dim); font-size: 0.78rem; margin-top: 18px; max-width: 80ch; }
@media (max-width: 800px) { .ins-two { grid-template-columns: 1fr; } .ins-theme { grid-template-columns: 1fr; gap: 4px; } .ins-pill { justify-self: start; } }
"""

COLLECTION_BODY = """
  <div class="ins-back"><a href="/decks" class="btn ghost small">&larr; Decks</a></div>
  <div id="insights"><div class="ins-loading">Reading your collection&hellip;</div></div>
"""

COLLECTION_SCRIPT = r"""
const MANA_HEX = { W: '#e8dfb8', U: '#4a90c8', B: '#8d7aa0', R: '#d1553a', G: '#4f9a5a', M: '#e9c46a', C: '#9b9689' };
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (n) => Number(n || 0).toLocaleString();
const icon = (k) => (k === 'M' || k === 'C') ? '' : '<img src="/static/mana/' + k + '.svg" alt="' + k + '">';
const showError = (m) => { const b = document.getElementById('error-box'); if (b) { b.textContent = m; b.style.display = 'block'; } };

function snapshotHtml(s) {
  const tile = (value, label, sub) => '<div class="stat-tile"><div class="stat-value">' + value + '</div><div class="stat-label">' + label + '</div>' + (sub ? '<div class="stat-sub">' + sub + '</div>' : '') + '</div>';
  return '<div class="stat-tiles">'
    + tile(fmt(s.unique), 'Unique cards', fmt(s.lands) + ' lands')
    + tile(fmt(s.copies), 'Copies', '')
    + tile('$' + fmt(Math.round(s.value)), 'Rough value', 'at today’s market price')
    + tile(s.foil_pct + '%', 'Foils', 'of your copies')
    + tile(fmt(s.sets), 'Sets', 'you own cards from')
    + '</div>';
}

function colorsHtml(c) {
  const total = c.nonland_total || 1;
  const segs = c.columns.filter((x) => x.unique > 0).map((x) =>
    '<div style="flex:' + x.unique + ';background:' + MANA_HEX[x.key] + ';" title="' + x.label + ': ' + fmt(x.unique) + '">' + (x.unique / total >= 0.06 ? Math.round(100 * x.unique / total) + '%' : '') + '</div>').join('');
  const legend = c.columns.map((x) => '<span>' + (icon(x.key) || '<i style="display:inline-block;width:14px;height:14px;border-radius:50%;background:' + MANA_HEX[x.key] + ';"></i>') + x.label + ' <b>' + fmt(x.unique) + '</b></span>').join('');
  return '<div class="card ins-section"><h2>Colors</h2><p class="ins-sub">Unique nonland cards by color identity.</p><div class="ins-colorbar">' + segs + '</div><div class="ins-legend">' + legend + '</div></div>';
}

function rolesHtml(r) {
  const cols = r.columns;
  const colName = Object.fromEntries(cols.map((c) => [c.key, c.label]));
  const flagged = [];
  r.rows.forEach((row) => cols.forEach((c) => {
    const cell = row.cells[c.key];
    if (cell.flag && 'WUBRG'.includes(c.key)) flagged.push({ role: row.role, key: c.key, n: cell.n, idx: cell.index, flag: cell.flag });
  }));
  const thin = flagged.filter((f) => f.flag === 'gap' || f.flag === 'thin').sort((a, b) => a.idx - b.idx).slice(0, 6);
  const deep = flagged.filter((f) => f.flag === 'deep').sort((a, b) => b.idx - a.idx).slice(0, 6);
  const chip = (f, cls) => '<span class="ins-chip ' + cls + '">' + colName[f.key] + ' ' + esc(f.role.toLowerCase()) + ' (' + f.n + ')</span>';
  const head = '<tr><th class="role"></th>' + cols.map((c) => '<th>' + icon(c.key) + (c.key === 'M' || c.key === 'C' ? c.label : '') + '<div style="font-weight:400;">' + fmt(c.total) + '</div></th>').join('') + '</tr>';
  const body = r.rows.map((row) => '<tr><th class="role">' + esc(row.role) + '<small>' + fmt(row.total) + '</small></th>' + cols.map((c) => {
    const cell = row.cells[c.key];
    const tip = colName[c.key] + ' ' + row.role.toLowerCase() + ': you own ' + cell.n + ' of ' + fmt(cell.pool) + ' Commander-legal cards'
      + (cell.index != null ? ' (' + cell.index.toFixed(2) + '× your usual share for this color)' : '') + (cell.names.length ? '. Most played: ' + cell.names.slice(0, 8).join(', ') : '');
    return '<td class="' + (cell.flag || '') + '" title="' + esc(tip) + '">' + cell.n + '</td>';
  }).join('') + '</tr>').join('');
  return '<div class="card ins-section"><h2>Roles by color</h2>'
    + '<p class="ins-sub">How many cards you own for each job, by color. Each cell is judged against how many such cards exist in that color and how much of that color you own, so a big color doesn’t look strong just for being big. Hover a cell for details.</p>'
    + (thin.length ? '<div class="ins-chips"><span class="lbl">Thin spots</span>' + thin.map((f) => chip(f, 'bad')).join('') + '</div>' : '')
    + (deep.length ? '<div class="ins-chips"><span class="lbl">Strong spots</span>' + deep.map((f) => chip(f, 'good')).join('') + '</div>' : '')
    + '<div class="ins-table-wrap"><table class="ins-grid">' + head + body + '</table></div>'
    + '<div class="ins-key"><span><i style="background:color-mix(in srgb, var(--missing) 40%, var(--bg));"></i>Gap</span><span><i style="background:color-mix(in srgb, var(--gold) 35%, var(--bg));"></i>Thin</span><span><i style="background:color-mix(in srgb, var(--owned) 35%, var(--bg));"></i>Strong</span><span>Counts are unique cards; a role that barely exists in a color (blue creature recursion, say) is never flagged.</span></div></div>';
}

function themesHtml(themes) {
  const rows = themes.map((t) => {
    const pct = Math.min(100, (t.index / 2) * 100);
    const label = t.level === 'deep' ? 'Deep' : (t.level === 'thin' ? 'Thin' : 'Typical');
    return '<div class="ins-theme ' + t.level + '" title="' + esc(t.examples.length ? 'Most played of yours: ' + t.examples.join(', ') : '') + '">'
      + '<div class="nm">' + esc(t.name) + '<small>' + fmt(t.owned) + ' of ' + fmt(t.pool) + ' (' + t.share + '%)</small></div>'
      + '<div class="ins-meter"><div class="fill" style="width:' + pct + '%;"></div><div class="mid" style="left:50%;" title="A typical theme for you"></div></div>'
      + '<div class="ins-pill">' + label + ' &middot; ' + t.index.toFixed(1) + '×</div></div>';
  }).join('');
  return '<div class="card ins-section"><h2>Themes</h2>'
    + '<p class="ins-sub">How much of each plan’s cards you own, next to your other plans. The tick marks a typical theme for you; bars past it are where you’re deepest. Hover a theme to see your most-played cards for it. Oracle Tags are crowd-made, so read this as “roughly how deep”.</p>'
    + rows + '</div>';
}

function typesHtml(t) {
  const maxU = Math.max(...t.by_category.map((x) => x.unique), 1);
  const cats = t.by_category.map((x) => '<div class="row"><span>' + esc(x.type) + '</span><div><div class="bar" style="width:' + (100 * x.unique / maxU) + '%;"></div></div><span class="num">' + fmt(x.unique) + '</span></div>').join('');
  const maxC = Math.max(...t.curve, 1);
  const curve = t.curve.map((n, i) => '<div class="col"><b>' + n + '</b><div class="bar" style="height:' + Math.max(2, 100 * n / maxC * 0.78) + '%;"></div>' + (i === 7 ? '7+' : i) + '</div>').join('');
  const tribes = t.tribes.map((x) => '<span class="ins-chip">' + esc(x.type) + ' <b>' + x.n + '</b></span>').join('');
  return '<div class="ins-two"><div class="card ins-section"><h2>Card types</h2><p class="ins-sub">Unique cards of each type.</p><div class="ins-bars">' + cats + '</div></div>'
    + '<div class="card ins-section"><h2>Mana curve</h2><p class="ins-sub">Nonland cards by mana value (average ' + t.avg_cmc + ').</p><div class="ins-curve">' + curve + '</div></div></div>'
    + '<div class="card ins-section"><h2>Creature types</h2><p class="ins-sub">Your deepest tribes, by unique creatures.</p><div class="ins-tribes">' + tribes + '</div></div>';
}

fetch('/collection/data').then((r) => r.json()).then((d) => {
  const el = document.getElementById('insights');
  if (d.error) { el.innerHTML = '<div class="card"><p>' + esc(d.error) + '</p><a class="btn small" href="/">Go to Setup</a></div>'; return; }
  el.innerHTML = snapshotHtml(d.snapshot) + colorsHtml(d.colors) + rolesHtml(d.roles) + themesHtml(d.themes) + typesHtml(d.types)
    + '<p class="ins-note">All of this is worked out on your computer from your collection and the local card database (' + fmt(d.pool_size) + ' Commander-legal nonland cards; you own ' + d.baseline_pct + '% of them). Nothing is sent anywhere.</p>';
}).catch(() => { showError('Could not reach the server.'); document.getElementById('insights').innerHTML = ''; });
"""


def render_collection_page(page_style: str, header_html: str, footer_html: str, shared_scripts: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/svg+xml" href="https://svgs.scryfall.io/card-symbols/PW.svg">
<title>Brewlist -- Collection</title>
<style>{page_style}</style>
<style>{COLLECTION_STYLE}</style>
</head>
<body>
<main class="wide">
  {header_html}
  {COLLECTION_BODY}
  {footer_html}
</main>
<script>
{COLLECTION_SCRIPT}
{shared_scripts}
</script>
</body>
</html>
"""
