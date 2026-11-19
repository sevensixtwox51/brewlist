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
.ins-grid td .pc { font-weight: 400; font-size: 0.68rem; color: var(--text-dim); margin-top: 1px; }
.ins-grid td[style*="color"] .pc { color: inherit; opacity: 0.8; }
.ins-mode { display: inline-flex; align-items: center; gap: 0; margin: 0 0 12px; font-size: 0.82rem; color: var(--text-dim); }
.ins-mode span { margin-right: 10px; }
.ins-mode button { background: var(--bg); color: var(--text-dim); border: 1px solid var(--card-border); padding: 5px 12px; cursor: pointer; font: inherit; }
.ins-mode button:first-of-type { border-radius: 8px 0 0 8px; }
.ins-mode button:last-of-type { border-radius: 0 8px 8px 0; border-left: none; }
.ins-mode button.on { color: var(--accent); border-color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, var(--bg)); }
.ins-key .ins-scale i { display: inline-block; width: 110px; height: 10px; border-radius: 5px; vertical-align: -1px; border: none; margin: 0; background: linear-gradient(90deg, hsl(2 52% 36%), hsl(48 52% 36%), hsl(132 52% 36%)); }
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
[data-key] { cursor: pointer; }
.ins-grid td[data-key]:hover, .ins-chip[data-key]:hover { border-color: var(--accent); filter: brightness(1.25); }
.ins-theme[data-key]:hover .nm { color: var(--accent); }
.ins-bars .row[data-key]:hover > span:first-child, .ins-curve .col[data-key]:hover { color: var(--accent); }
.ins-colorbar > div[data-key]:hover, .ins-legend span[data-key]:hover { filter: brightness(1.15); }
.ins-legend span[data-key]:hover { color: var(--accent); }
.ins-modal { display: none; position: fixed; inset: 0; z-index: 300; background: rgba(8, 6, 4, 0.78); align-items: flex-start; justify-content: center; padding: 4vh 16px; overflow-y: auto; }
.ins-modal.show { display: flex; }
.ins-modal-panel { background: var(--bg-elevated); border: 1px solid var(--card-border); border-radius: 14px; width: min(1100px, 100%); box-shadow: var(--shadow); }
.ins-modal-head { display: flex; align-items: center; gap: 12px; padding: 14px 18px; border-bottom: 1px solid var(--card-border); position: sticky; top: 0; background: var(--bg-elevated); border-radius: 14px 14px 0 0; z-index: 2; }
.ins-modal-head h3 { font-family: var(--serif); font-weight: 600; font-size: 1.3rem; color: var(--accent); margin: 0; flex: 1; }
.ins-modal-head .count { color: var(--text-dim); font-size: 0.85rem; white-space: nowrap; }
.ins-modal-head input { width: 190px; margin: 0; padding: 6px 10px; }
.ins-modal-close { background: none; border: none; color: var(--text-dim); font-size: 1.6rem; line-height: 1; cursor: pointer; }
.ins-modal-close:hover { color: var(--accent); }
.ins-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 14px; padding: 16px 18px 20px; }
.ins-card { position: relative; min-width: 0; }
.ins-card img { width: 100%; aspect-ratio: 5 / 7; border-radius: 6.5%; background: var(--bg); display: block; box-shadow: 0 2px 10px rgba(0,0,0,0.5); }
.ins-card .nm { font-size: 0.8rem; font-weight: 600; margin-top: 6px; line-height: 1.2; }
.ins-card .ty { font-size: 0.7rem; color: var(--text-dim); line-height: 1.25; }
.ins-card .qty { position: absolute; top: 6px; right: 6px; background: rgba(0,0,0,0.78); color: var(--accent); font-weight: 700; font-size: 0.75rem; padding: 1px 7px; border-radius: 999px; }
#ins-tip { position: fixed; z-index: 400; display: none; pointer-events: none; max-width: 470px; padding: 10px 12px 11px; background: var(--bg-elevated); border: 1px solid var(--accent);
  border-radius: 10px; box-shadow: 0 8px 28px rgba(0,0,0,0.6); font-size: 0.82rem; line-height: 1.35; }
#ins-tip.show { display: block; }
#ins-tip b { color: var(--accent); }
#ins-tip .tt { font-family: var(--serif); font-size: 1.05rem; color: var(--accent); margin-bottom: 2px; }
#ins-tip .foot { color: var(--text-dim); font-size: 0.74rem; margin-top: 7px; }
#ins-tip .thumbs { display: flex; gap: 6px; margin-top: 8px; }
#ins-tip .thumbs img { width: 84px; aspect-ratio: 5 / 7; border-radius: 5px; background: var(--bg); display: block; }
.ins-empty { padding: 30px 18px; color: var(--text-dim); text-align: center; }
.ins-tabs { display: inline-flex; }
.ins-tabs button { background: var(--bg); color: var(--text-dim); border: 1px solid var(--card-border); padding: 5px 12px; cursor: pointer; font: inherit; font-size: 0.82rem; }
.ins-tabs button:first-child { border-radius: 8px 0 0 8px; }
.ins-tabs button:last-child { border-radius: 0 8px 8px 0; border-left: none; }
.ins-tabs button.on { color: var(--accent); border-color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, var(--bg)); }
.ins-modal-hint { padding: 12px 18px 0; color: var(--text-dim); font-size: 0.8rem; }
.ins-card a.img { display: block; }
.ins-card .rank { position: absolute; top: 6px; left: 6px; background: rgba(0,0,0,0.78); color: var(--text); font-weight: 600; font-size: 0.7rem; padding: 1px 7px; border-radius: 999px; }
.ins-card .price { display: inline-block; margin-top: 4px; font-size: 0.74rem; font-weight: 600; color: var(--owned); text-decoration: none; border: 1px solid color-mix(in srgb, var(--owned) 50%, var(--card-border)); border-radius: 999px; padding: 1px 8px; }
.ins-card .price:hover { background: color-mix(in srgb, var(--owned) 16%, transparent); }
@media (max-width: 800px) { .ins-two { grid-template-columns: 1fr; } .ins-theme { grid-template-columns: 1fr; gap: 4px; } .ins-pill { justify-self: start; } }
"""

COLLECTION_BODY = """
  <div class="ins-back"><a href="/decks" class="btn ghost small">&larr; Decks</a></div>
  <div id="insights"><div class="ins-loading">Reading your collection&hellip;</div></div>
  <div id="ins-tip" role="tooltip"></div>
  <div class="ins-modal" id="ins-modal" role="dialog" aria-modal="true" aria-label="Cards">
    <div class="ins-modal-panel">
      <div class="ins-modal-head">
        <h3 id="ins-modal-title"></h3>
        <div class="ins-tabs"><button type="button" data-mode="owned">Your cards</button><button type="button" data-mode="missing" title="The most popular cards like these that you don't own yet">Popular to add</button></div>
        <span class="count" id="ins-modal-count"></span>
        <input type="text" id="ins-modal-filter" placeholder="Filter these cards&hellip;" autocomplete="off">
        <button type="button" class="ins-modal-close" id="ins-modal-close" aria-label="Close">&times;</button>
      </div>
      <div id="ins-modal-body"></div>
    </div>
  </div>
"""

COLLECTION_SCRIPT = r"""
const MANA_HEX = { W: '#e8dfb8', U: '#4a90c8', B: '#8d7aa0', R: '#d1553a', G: '#4f9a5a', M: '#e9c46a', C: '#9b9689' };
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (n) => Number(n || 0).toLocaleString();
const icon = (k) => (k === 'M' || k === 'C') ? '' : '<img src="/static/mana/' + k + '.svg" alt="' + k + '">';
const smallImg = (id) => 'https://cards.scryfall.io/small/front/' + id[0] + '/' + id[1] + '/' + id + '.jpg';
// The hover card used by every clickable element: one instant tooltip (the browser's own title tooltips are slow and unreliable here).
function tipHtml(title, lines, top, foot) {
  const thumbs = (top || []).filter((t) => t.id).map((t) => '<img src="' + smallImg(t.id) + '" alt="' + esc(t.name) + '">').join('');
  const names = (top || []).length ? '<div style="margin-top:6px;">Most played: ' + (top || []).map((t) => esc(t.name)).join(', ') + '</div>' : '';
  return '<div class="tt">' + esc(title) + '</div>' + lines.map((l) => '<div>' + l + '</div>').join('') + names + (thumbs ? '<div class="thumbs">' + thumbs + '</div>' : '') + (foot ? '<div class="foot">' + esc(foot) + '</div>' : '');
}
const tipAttr = (html) => ' data-tip="' + esc(html) + '"';
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
    '<div data-key="color:' + x.key + '" data-title="' + x.label + ' cards" style="flex:' + x.unique + ';background:' + MANA_HEX[x.key] + ';" ' + tipAttr(tipHtml(x.label + ' cards', ['<b>' + fmt(x.unique) + '</b> unique nonland cards (' + Math.round(100 * x.unique / total) + '% of your collection)'], null, 'Click to see them, or for popular cards to add')) + '>' + (x.unique / total >= 0.06 ? Math.round(100 * x.unique / total) + '%' : '') + '</div>').join('');
  const legend = c.columns.map((x) => '<span data-key="color:' + x.key + '" data-title="' + x.label + ' cards"' + tipAttr(tipHtml(x.label + ' cards', ['<b>' + fmt(x.unique) + '</b> unique nonland cards'], null, 'Click to see them, or for popular cards to add')) + '>' + (icon(x.key) || '<i style="display:inline-block;width:14px;height:14px;border-radius:50%;background:' + MANA_HEX[x.key] + ';"></i>') + x.label + ' <b>' + fmt(x.unique) + '</b></span>').join('');
  return '<div class="card ins-section"><h2>Colors</h2><p class="ins-sub">Unique nonland cards by color identity.</p><div class="ins-colorbar">' + segs + '</div><div class="ins-legend">' + legend + '</div></div>';
}

// ---- Roles by color: each row is shaded on its own scale across the five colors, red (lowest) to green (highest) ----
let heatMode = 'n';  // 'n' = how many cards you own, 'pct' = the share of all such cards that exist
const heatColor = (t) => {  // t in [0,1] -> a dark-theme cell: red, through amber, to green
  const hue = t < 0.5 ? 2 + t * 2 * 46 : 48 + (t - 0.5) * 2 * 84;
  return { bg: 'hsl(' + hue + ' 48% 19%)', border: 'hsl(' + hue + ' 52% 36%)', fg: 'hsl(' + hue + ' 72% 68%)' };
};
function paintHeat() {
  const chips = { low: [], high: [] };
  document.querySelectorAll('.ins-grid tr[data-role]').forEach((tr) => {
    const cells = [...tr.querySelectorAll('td')].map((td) => ({ td, v: Number(td.dataset[heatMode]), ok: td.dataset.ok === '1', n: Number(td.dataset.n), color: td.dataset.color, real: Number(td.dataset.pool) >= 30 }));
    const valid = cells.filter((c) => c.ok);
    const vals = valid.map((c) => c.v);
    const lo = Math.min(...vals), hi = Math.max(...vals), mean = vals.reduce((a, b) => a + b, 0) / (vals.length || 1);
    cells.forEach((c) => { c.td.style.background = ''; c.td.style.borderColor = ''; c.td.style.color = ''; });
    if (hi === lo) return;  // every color the same: nothing to compare across this row
    valid.forEach((c) => {
      // Each row on its own scale across the five colors: its lowest cell is red and its highest is green.
      const col = heatColor((c.v - lo) / (hi - lo));
      c.td.style.background = col.bg; c.td.style.borderColor = col.border; c.td.style.color = col.fg;
    });
    // The chips skip colors where the job barely exists (black counterspells, blue lifegain): that isn't a weakness.
    const real = valid.filter((c) => c.real);
    if (real.length < 3) return;
    const rlo = Math.min(...real.map((c) => c.v)), rhi = Math.max(...real.map((c) => c.v));
    const rmean = real.reduce((a, c) => a + c.v, 0) / real.length;
    const lowest = real.find((c) => c.v === rlo), highest = real.find((c) => c.v === rhi);
    if (rmean && rlo <= rmean * 0.5) chips.low.push({ role: tr.dataset.role, color: lowest.color, n: lowest.n, ratio: rlo / rmean });
    if (rmean && rhi >= rmean * 1.5 && highest.n >= 6) chips.high.push({ role: tr.dataset.role, color: highest.color, n: highest.n, ratio: rhi / rmean });
  });
  const colName = { W: 'White', U: 'Blue', B: 'Black', R: 'Red', G: 'Green' };
  const chip = (f, cls) => '<span class="ins-chip ' + cls + '" data-key="role:' + esc(f.role) + ':' + f.color + '" data-title="' + esc(colName[f.color] + ' ' + f.role.toLowerCase()) + '"' + tipAttr(tipHtml(colName[f.color] + ' ' + f.role.toLowerCase(), ['You own <b>' + f.n + '</b>; click to see them or popular cards to add'], null, '')) + '>' + colName[f.color] + ' ' + esc(f.role.toLowerCase()) + ' (' + f.n + ')</span>';
  chips.low.sort((a, b) => a.ratio - b.ratio); chips.high.sort((a, b) => b.ratio - a.ratio);
  document.getElementById('ins-chips-low').innerHTML = chips.low.length ? '<span class="lbl">Weakest in their row</span>' + chips.low.slice(0, 6).map((f) => chip(f, 'bad')).join('') : '';
  document.getElementById('ins-chips-high').innerHTML = chips.high.length ? '<span class="lbl">Strongest in their row</span>' + chips.high.slice(0, 6).map((f) => chip(f, 'good')).join('') : '';
  document.querySelectorAll('.ins-mode button').forEach((b) => b.classList.toggle('on', b.dataset.mode === heatMode));
}

function rolesHtml(r) {
  const cols = r.columns;
  const colName = Object.fromEntries(cols.map((c) => [c.key, c.label]));
  const head = '<tr><th class="role"></th>' + cols.map((c) => '<th>' + icon(c.key) + (c.key === 'M' || c.key === 'C' ? c.label : '') + '<div style="font-weight:400;">' + fmt(c.total) + '</div></th>').join('') + '</tr>';
  const body = r.rows.map((row) => '<tr data-role="' + esc(row.role) + '"><th class="role">' + esc(row.role) + '<small>' + fmt(row.total) + '</small></th>' + cols.map((c) => {
    const cell = row.cells[c.key];
    const ok = 'WUBRG'.includes(c.key);
    const tip = tipHtml(colName[c.key] + ' ' + row.role.toLowerCase(),
      ['You own <b>' + cell.n + '</b> of the ' + fmt(cell.pool) + ' Commander-legal cards that exist' + (cell.pct != null ? ' (' + cell.pct + '%)' : '')],
      cell.top, cell.n ? 'Click to see all ' + cell.n + ', or for popular cards to add' : 'None owned yet. Click for popular cards to add');
    const key = ' data-key="role:' + esc(row.role) + ':' + c.key + '" data-title="' + esc(colName[c.key] + ' ' + row.role.toLowerCase()) + '"';
    const pc = cell.pct == null ? '' : '<div class="pc">' + (cell.pct >= 10 ? Math.round(cell.pct) : cell.pct.toFixed(1)) + '%</div>';
    return '<td' + key + ' data-ok="' + (ok ? 1 : 0) + '" data-pool="' + cell.pool + '" data-n="' + cell.n + '" data-pct="' + (cell.pct == null ? 0 : cell.pct) + '" data-color="' + c.key + '"' + tipAttr(tip) + '>' + cell.n + pc + '</td>';
  }).join('') + '</tr>').join('');
  return '<div class="card ins-section"><h2>Roles by color</h2>'
    + '<p class="ins-sub">How many cards you own for each job, by color. Every row is shaded on its own scale across the five colors: the lowest color in each row is red and the highest is green. The small percentage is how much of everything that exists for that job and color you own. Click a cell to see the cards.</p>'
    + '<div class="ins-mode"><span>Shade by</span><button type="button" data-mode="n">Cards owned</button><button type="button" data-mode="pct">Share of all such cards</button></div>'
    + '<div class="ins-chips" id="ins-chips-low"></div><div class="ins-chips" id="ins-chips-high"></div>'
    + '<div class="ins-table-wrap"><table class="ins-grid">' + head + body + '</table></div>'
    + '<div class="ins-key"><span class="ins-scale"><i></i></span><span>Lowest in its row &rarr; highest</span><span>Counts are unique cards. Multicolor and Colorless are shown but not shaded.</span></div></div>';
}

function themesHtml(themes) {
  const rows = themes.map((t) => {
    const pct = Math.min(100, (t.index / 2) * 100);
    const label = t.level === 'deep' ? 'Deep' : (t.level === 'thin' ? 'Thin' : 'Typical');
    return '<div class="ins-theme ' + t.level + '" data-key="theme:' + esc(t.name) + '" data-title="' + esc(t.name) + '"' + tipAttr(tipHtml(t.name, ['You own <b>' + fmt(t.owned) + '</b> of the ' + fmt(t.pool) + ' cards in this theme (' + t.share + '%), ' + t.index.toFixed(1) + '\u00d7 a typical theme for you'], t.top, 'Click to see them, or for popular cards to add')) + '>'
      + '<div class="nm">' + esc(t.name) + '<small>' + fmt(t.owned) + ' of ' + fmt(t.pool) + ' (' + t.share + '%)</small></div>'
      + '<div class="ins-meter"><div class="fill" style="width:' + pct + '%;"></div><div class="mid" style="left:50%;"></div></div>'
      + '<div class="ins-pill">' + label + ' &middot; ' + t.index.toFixed(1) + '×</div></div>';
  }).join('');
  return '<div class="card ins-section"><h2>Themes</h2>'
    + '<p class="ins-sub">How much of each plan’s cards you own, next to your other plans. The tick marks a typical theme for you; bars past it are where you’re deepest. Hover a theme to see your most-played cards for it. Oracle Tags are crowd-made, so read this as “roughly how deep”.</p>'
    + rows + '</div>';
}

function typesHtml(t) {
  const maxU = Math.max(...t.by_category.map((x) => x.unique), 1);
  const cats = t.by_category.map((x) => '<div class="row" data-key="type:' + esc(x.type) + '" data-title="' + esc(x.type) + '"' + tipAttr(tipHtml(x.type, ['<b>' + fmt(x.unique) + '</b> unique cards, ' + fmt(x.copies) + ' copies'], null, 'Click to see them, or for popular cards to add')) + '><span>' + esc(x.type) + '</span><div><div class="bar" style="width:' + (100 * x.unique / maxU) + '%;"></div></div><span class="num">' + fmt(x.unique) + '</span></div>').join('');
  const maxC = Math.max(...t.curve, 1);
  const curve = t.curve.map((n, i) => '<div class="col" data-key="cmc:' + i + '" data-title="Mana value ' + (i === 7 ? '7+' : i) + '"' + tipAttr(tipHtml('Mana value ' + (i === 7 ? '7+' : i), ['<b>' + n + '</b> nonland cards'], null, 'Click to see them, or for popular cards to add')) + '><b>' + n + '</b><div class="bar" style="height:' + Math.max(2, 100 * n / maxC * 0.78) + '%;"></div>' + (i === 7 ? '7+' : i) + '</div>').join('');
  const tribes = t.tribes.map((x) => '<span class="ins-chip" data-key="tribe:' + esc(x.type) + '" data-title="' + esc(x.type) + ' creatures"' + tipAttr(tipHtml(x.type + ' creatures', ['<b>' + x.n + '</b> unique creatures'], null, 'Click to see them, or for popular cards to add')) + '>' + esc(x.type) + ' <b>' + x.n + '</b></span>').join('');
  return '<div class="ins-two"><div class="card ins-section"><h2>Card types</h2><p class="ins-sub">Unique cards of each type.</p><div class="ins-bars">' + cats + '</div></div>'
    + '<div class="card ins-section"><h2>Mana curve</h2><p class="ins-sub">Nonland cards by mana value (average ' + t.avg_cmc + ').</p><div class="ins-curve">' + curve + '</div></div></div>'
    + '<div class="card ins-section"><h2>Creature types</h2><p class="ins-sub">Your deepest tribes, by unique creatures.</p><div class="ins-tribes">' + tribes + '</div></div>';
}

// ---- the cards behind a number ----
const modal = document.getElementById('ins-modal');
const modalBody = document.getElementById('ins-modal-body');
const modalFilter = document.getElementById('ins-modal-filter');
let modalCards = [], modalSeq = 0, modalKey = '', modalMode = 'owned', modalCache = {};
const cardImg = (id) => id ? 'https://cards.scryfall.io/normal/front/' + id[0] + '/' + id[1] + '/' + id + '.jpg' : '';
const STORES = { MP: 'ManaPool', TCGP: 'TCGplayer', CK: 'Card Kingdom' };
const scryUrl = (name) => 'https://scryfall.com/search?q=' + encodeURIComponent('!"' + name + '"');
function renderModalCards() {
  const q = modalFilter.value.trim().toLowerCase();
  const shown = q ? modalCards.filter((c) => (c.name + ' ' + (c.type_line || '')).toLowerCase().includes(q)) : modalCards;
  const missing = modalMode === 'missing';
  const copies = shown.reduce((n, c) => n + (c.quantity || 1), 0);
  document.getElementById('ins-modal-count').textContent = shown.length + (shown.length === 1 ? ' card' : ' cards') + (!missing && copies !== shown.length ? ' · ' + copies + ' copies' : '');
  const noneOwned = modalCache.owned && modalCache.owned.length === 0;
  const hint = missing ? '<div class="ins-modal-hint">' + (noneOwned ? 'You don\u2019t own any of these yet. ' : '') + 'The most popular Commander-legal cards like this that aren’t in your collection, most popular first (EDHREC rank). Prices are the cheapest nonfoil from TCGplayer, Card Kingdom and ManaPool; click a price to open that listing, or a card for its Scryfall page.</div>' : '';
  const tile = (c) => {
    const img = c.scryfall_id ? '<img loading="lazy" alt="' + esc(c.name) + '" src="' + cardImg(c.scryfall_id) + '">' : '<div style="aspect-ratio:5/7;border-radius:6%;background:var(--bg);"></div>';
    if (!missing) {
      return '<div class="ins-card" title="' + esc(c.name) + '">' + img + ((c.quantity || 1) > 1 ? '<span class="qty">×' + c.quantity + '</span>' : '')
        + '<div class="nm">' + esc(c.name) + (c.foil ? ' ✦' : '') + '</div><div class="ty">' + esc(c.type_line || '') + '</div></div>';
    }
    const price = c.price != null ? '<a class="price" target="_blank" rel="noopener noreferrer" href="' + esc(c.url || scryUrl(c.name)) + '" title="Cheapest nonfoil listing">$' + Number(c.price).toFixed(2) + ' · ' + esc(STORES[c.store] || c.store || '') + '</a>' : '';
    return '<div class="ins-card" title="' + esc(c.name) + '"><a class="img" target="_blank" rel="noopener noreferrer" href="' + scryUrl(c.name) + '">' + img + '</a>'
      + (c.rank ? '<span class="rank">#' + fmt(c.rank) + '</span>' : '')
      + '<div class="nm">' + esc(c.name) + '</div><div class="ty">' + esc(c.type_line || '') + '</div>' + price + '</div>';
  };
  modalBody.innerHTML = hint + (shown.length ? '<div class="ins-cards">' + shown.map(tile).join('') + '</div>' : '<div class="ins-empty">' + (missing && !modalCards.length ? 'Nothing popular left to add here.' : 'No cards match.') + '</div>');
}
function loadModalMode() {
  const mine = ++modalSeq;
  document.querySelectorAll('.ins-tabs button').forEach((b) => b.classList.toggle('on', b.dataset.mode === modalMode));
  modalFilter.value = '';
  if (modalCache[modalMode]) { modalCards = modalCache[modalMode]; renderModalCards(); return; }
  document.getElementById('ins-modal-count').textContent = '';
  modalBody.innerHTML = '<div class="ins-empty">Loading…</div>';
  fetch('/collection/cards?key=' + encodeURIComponent(modalKey) + (modalMode === 'missing' ? '&missing=1' : '')).then((r) => r.json()).then((d) => {
    if (mine !== modalSeq) return;
    if (d.error) { modalBody.innerHTML = '<div class="ins-empty">' + esc(d.error) + '</div>'; return; }
    modalCache[modalMode] = d.cards;
    modalCards = d.cards;
    if (modalMode === 'owned' && !d.cards.length) { modalMode = 'missing'; loadModalMode(); return; }  // nothing owned: show what to add
    renderModalCards();
  }).catch(() => { if (mine === modalSeq) modalBody.innerHTML = '<div class="ins-empty">Could not reach the server.</div>'; });
}
function openList(key, title) {
  modalKey = key; modalMode = 'owned'; modalCache = {};
  document.getElementById('ins-modal-title').textContent = title;
  modal.classList.add('show');
  document.body.style.overflow = 'hidden';
  loadModalMode();
}
function closeList() { modalSeq++; modal.classList.remove('show'); document.body.style.overflow = ''; }
document.getElementById('ins-modal-close').addEventListener('click', closeList);
modal.addEventListener('click', (e) => { if (e.target === modal) closeList(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && modal.classList.contains('show')) closeList(); });
modalFilter.addEventListener('input', renderModalCards);
document.querySelector('.ins-tabs').addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (b && b.dataset.mode !== modalMode) { modalMode = b.dataset.mode; loadModalMode(); }
});
document.getElementById('insights').addEventListener('click', (e) => {
  const mode = e.target.closest('.ins-mode button');
  if (mode) { heatMode = mode.dataset.mode; paintHeat(); return; }
  const el = e.target.closest('[data-key]');
  if (el) openList(el.dataset.key, el.dataset.title || 'Cards');
});

// ---- hover cards ----
const tipEl = document.getElementById('ins-tip');
let tipOwner = null;
const hideTip = () => { tipOwner = null; tipEl.classList.remove('show'); };
document.addEventListener('mousemove', (e) => {
  const el = modal.classList.contains('show') ? null : (e.target.closest ? e.target.closest('[data-tip]') : null);
  if (!el) { if (tipOwner) hideTip(); return; }
  if (el !== tipOwner) { tipOwner = el; tipEl.innerHTML = el.dataset.tip; tipEl.classList.add('show'); }
  const pad = 16, w = tipEl.offsetWidth, h = tipEl.offsetHeight;
  let x = e.clientX + pad, y = e.clientY + pad;
  if (x + w > window.innerWidth - 8) x = e.clientX - w - pad;
  if (y + h > window.innerHeight - 8) y = e.clientY - h - pad;
  tipEl.style.left = Math.max(8, x) + 'px'; tipEl.style.top = Math.max(8, y) + 'px';
});
document.addEventListener('mouseleave', hideTip);
document.addEventListener('scroll', hideTip, true);
document.addEventListener('click', hideTip, true);

fetch('/collection/data').then((r) => r.json()).then((d) => {
  const el = document.getElementById('insights');
  if (d.error) { el.innerHTML = '<div class="card"><p>' + esc(d.error) + '</p><a class="btn small" href="/">Go to Setup</a></div>'; return; }
  el.innerHTML = snapshotHtml(d.snapshot) + colorsHtml(d.colors) + rolesHtml(d.roles) + themesHtml(d.themes) + typesHtml(d.types)
    + '<p class="ins-note">All of this is worked out on your computer from your collection and the local card database (' + fmt(d.pool_size) + ' Commander-legal nonland cards; you own ' + d.baseline_pct + '% of them). Nothing is sent anywhere.</p>';
  paintHeat();
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
