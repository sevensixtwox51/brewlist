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
.ins-card .pic { position: relative; }
.ins-card .qty { position: absolute; bottom: 6px; left: 6px; background: rgba(0,0,0,0.78); color: var(--accent); font-weight: 700; font-size: 0.75rem; padding: 1px 7px; border-radius: 999px; }
#ins-tip { position: fixed; z-index: 400; display: none; pointer-events: none; max-width: 470px; padding: 10px 12px 11px; background: var(--bg-elevated); border: 1px solid var(--accent);
  border-radius: 10px; box-shadow: 0 8px 28px rgba(0,0,0,0.6); font-size: 0.82rem; line-height: 1.35; }
#ins-tip.show { display: block; }
#ins-tip b { color: var(--accent); }
#ins-tip .tt { font-family: var(--serif); font-size: 1.05rem; color: var(--accent); margin-bottom: 2px; }
#ins-tip .foot { color: var(--text-dim); font-size: 0.74rem; margin-top: 7px; }
#ins-tip .thumbs { display: flex; gap: 6px; margin-top: 8px; }
#ins-tip .thumbs img { width: 84px; aspect-ratio: 5 / 7; border-radius: 5px; background: var(--bg); display: block; }
#ins-zoom { position: fixed; z-index: 500; display: none; pointer-events: none; width: 300px; border-radius: 14px; box-shadow: 0 10px 34px rgba(0,0,0,0.75); background: #000; }
#ins-zoom.show { display: block; }
#ins-zoom img { display: block; width: 100%; border-radius: 14px; }
.ins-card[data-id] .img-hover { cursor: zoom-in; }
.ins-empty { padding: 30px 18px; color: var(--text-dim); text-align: center; }
.ins-tabs { display: inline-flex; }
.ins-tabs button { background: var(--bg); color: var(--text-dim); border: 1px solid var(--card-border); padding: 5px 12px; cursor: pointer; font: inherit; font-size: 0.82rem; }
.ins-tabs button:first-child { border-radius: 8px 0 0 8px; }
.ins-tabs button:last-child { border-radius: 0 8px 8px 0; border-left: none; }
.ins-tabs button.on { color: var(--accent); border-color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, var(--bg)); }
.ins-modal-hint { padding: 12px 18px 0; color: var(--text-dim); font-size: 0.8rem; }
.ins-card a.img { display: block; }
.ins-card .flip { position: absolute; top: 6px; right: 6px; z-index: 2; width: 26px; height: 26px; border-radius: 999px; border: 1px solid var(--card-border); background: rgba(0,0,0,0.78); color: var(--text); font-size: 0.95rem; line-height: 1; cursor: pointer; padding: 0; }
.ins-card .flip:hover { color: var(--accent); border-color: var(--accent); }
.ins-card .rank { position: absolute; bottom: 6px; left: 6px; background: rgba(0,0,0,0.78); color: var(--text); font-weight: 600; font-size: 0.7rem; padding: 1px 7px; border-radius: 999px; }
.ins-card .price { display: inline-block; margin-top: 4px; font-size: 0.74rem; font-weight: 600; color: var(--owned); text-decoration: none; border: 1px solid color-mix(in srgb, var(--owned) 50%, var(--card-border)); border-radius: 999px; padding: 1px 8px; }
.ins-card .price:hover { background: color-mix(in srgb, var(--owned) 16%, transparent); }
.ins-view-tabs { display: inline-flex; margin: 0 0 16px 12px; vertical-align: middle; }
.ins-view-tabs button { background: var(--bg); color: var(--text-dim); border: 1px solid var(--card-border); padding: 7px 16px; cursor: pointer; font: inherit; font-size: 0.9rem; }
.ins-view-tabs button:first-child { border-radius: 9px 0 0 9px; }
.ins-view-tabs button:last-child { border-radius: 0 9px 9px 0; border-left: none; }
.ins-view-tabs button.on { color: var(--accent); border-color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, var(--bg)); }
.br-filters { display: flex; flex-wrap: wrap; gap: 8px 10px; align-items: center; margin: 0 0 10px; }
.br-filters input[type=text], .br-filters input[type=number], .br-filters select { margin: 0; padding: 7px 10px; width: auto; }
.br-filters input[type=text] { width: 220px; }
.br-filters input[type=number] { width: 86px; }
.br-filters .br-sep { color: var(--text-dim); font-size: 0.82rem; }
.br-pips { display: inline-flex; align-items: center; gap: 3px; }
.br-pip { width: 30px; height: 30px; padding: 0; border-radius: 50%; border: 2px solid transparent; background: none; cursor: pointer; opacity: 0.45; transition: opacity .12s ease, border-color .12s ease; display: flex; align-items: center; justify-content: center; position: relative; }
.br-pip img { width: 24px; height: 24px; display: block; }
.br-pip:hover { opacity: 0.85; }
.br-pip.on { opacity: 1; border-color: var(--accent); border-style: dashed; }
.br-pip.must { opacity: 1; border-style: solid; border-width: 3px; box-shadow: 0 0 12px 0 var(--accent); }
.br-pip.must::after { content: "\u2713"; position: absolute; top: -6px; right: -6px; width: 15px; height: 15px; border-radius: 50%; background: var(--accent); color: var(--bg); font-size: 0.62rem; font-weight: 700; line-height: 15px; text-align: center; }
.br-check { display: inline-flex; align-items: center; gap: 6px; font-size: 0.85rem; color: var(--text-dim); cursor: pointer; }
.br-check input { margin: 0; }
.br-setwrap { position: relative; }
.br-setpop { display: none; position: absolute; z-index: 60; top: calc(100% + 6px); left: 0; width: 330px; max-height: 380px; background: var(--bg-elevated); border: 1px solid var(--card-border); border-radius: 10px; box-shadow: var(--shadow); padding: 10px; }
.br-setpop.show { display: block; }
.br-setpop input[type=text] { width: 100%; margin: 0 0 8px; box-sizing: border-box; }
.br-setlist { overflow-y: auto; max-height: 280px; }
.br-setlist label { display: flex; align-items: center; gap: 8px; padding: 3px 2px; font-size: 0.84rem; cursor: pointer; }
.br-setlist label small { margin-left: auto; color: var(--text-dim); white-space: nowrap; }
.br-setlist input { margin: 0; }
.br-setactions { display: flex; justify-content: space-between; margin-top: 8px; font-size: 0.82rem; }
.br-summary { margin: 4px 0 14px; }
.br-note { color: var(--text-dim); font-size: 0.78rem; margin: 0 0 14px; max-width: 90ch; }
.br-more { text-align: center; padding: 6px 0 24px; }
table.br-list { width: 100%; border-collapse: collapse; font-size: 0.86rem; }
.br-list th { text-align: left; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-dim); padding: 6px 8px; border-bottom: 1px solid var(--card-border); }
.br-list td { padding: 6px 8px; border-bottom: 1px solid var(--card-border); vertical-align: top; }
.br-list .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.br-list tr:hover td { background: color-mix(in srgb, var(--accent) 7%, transparent); }
.br-list .ty { color: var(--text-dim); }
.ins-card .worth { display: block; margin-top: 3px; font-size: 0.74rem; color: var(--owned); font-weight: 600; font-variant-numeric: tabular-nums; }
.ins-card .worth small { color: var(--text-dim); font-weight: 400; }
.br-lb { display: none; position: fixed; inset: 0; z-index: 350; background: rgba(8, 6, 4, 0.86); align-items: center; justify-content: center; padding: 3vh 16px; }
.br-lb.show { display: flex; }
.br-lb-panel { position: relative; display: flex; gap: 26px; align-items: flex-start; max-width: min(960px, 100%); max-height: 94vh; overflow: auto; background: var(--bg-elevated); border: 1px solid var(--card-border); border-radius: 16px; padding: 36px 40px; box-shadow: var(--shadow); }
.br-lb-art { position: relative; flex: 0 0 auto; width: min(380px, 70vw); }
/* The card is held in the hand: it tilts toward the pointer in 3D, its shadow slides the other way, and a glare sweeps
   across it (a rainbow band too when the printing is a foil) as the angle changes. */
.br-lb-art { perspective: 1300px; }
.br-tilt { position: relative; border-radius: 4.8%; transform-style: preserve-3d; transform: rotateX(var(--rx, 0deg)) rotateY(var(--ry, 0deg)) scale(var(--sc, 1));
  box-shadow: var(--sh, 0 6px 26px rgba(0,0,0,0.6)); transition: transform 0.55s cubic-bezier(.2,.8,.2,1), box-shadow 0.55s cubic-bezier(.2,.8,.2,1); will-change: transform; }
.br-tilt.live { transition: transform 0.07s linear, box-shadow 0.07s linear; }
.br-tilt img { display: block; width: 100%; aspect-ratio: 5 / 7; border-radius: 4.8%; background: var(--bg); }
.br-glare { position: absolute; inset: 0; border-radius: 4.8%; overflow: hidden; pointer-events: none; opacity: var(--go, 0); transition: opacity 0.4s ease; }
.br-glare::before { content: ""; position: absolute; inset: 0; background: radial-gradient(farthest-corner circle at var(--gx, 50%) var(--gy, 50%), rgba(255,255,255,0.5) 0%, rgba(255,255,255,0.14) 30%, transparent 60%); mix-blend-mode: soft-light; }
.br-glare::after { content: ""; position: absolute; inset: 0; opacity: 0; mix-blend-mode: color-dodge;
  background: linear-gradient(115deg, transparent 18%, rgba(255,0,150,0.55) 32%, rgba(255,220,0,0.5) 42%, rgba(0,255,190,0.5) 52%, rgba(0,150,255,0.55) 62%, transparent 78%);
  background-size: 280% 280%; background-position: var(--gx, 50%) var(--gy, 50%); }
.br-tilt.holo .br-glare::after { opacity: 0.55; }
@media (prefers-reduced-motion: reduce) { .br-tilt, .br-tilt.live { transition: none; } }
.br-lb-art .flip { position: absolute; top: 10px; right: 10px; z-index: 2; width: 32px; height: 32px; border-radius: 999px; border: 1px solid var(--card-border); background: rgba(0,0,0,0.7); color: var(--text); cursor: pointer; font-size: 1rem; }
.br-lb-art .flip:hover { color: var(--accent); border-color: var(--accent); }
.br-lb-info { flex: 1 1 280px; min-width: 260px; }
.br-lb-info h3 { font-family: var(--serif); font-weight: 600; font-size: 1.6rem; color: var(--accent); margin: 0 0 2px; }
.br-lb-info .ty { color: var(--text-dim); font-size: 0.9rem; }
.br-lb-info .sum { margin: 10px 0 14px; font-size: 0.95rem; }
.br-lb-info .sum b { color: var(--owned); font-variant-numeric: tabular-nums; }
.br-lb-info h4 { margin: 0 0 8px; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.07em; color: var(--text-dim); font-weight: 600; }
.br-pr { display: grid; grid-template-columns: 46px 1fr auto; gap: 4px 12px; align-items: center; width: 100%; text-align: left; font: inherit; color: var(--text); background: var(--bg); border: 1px solid var(--card-border); border-radius: 10px; padding: 7px 10px; margin: 0 0 7px; cursor: pointer; }
.br-pr:hover { border-color: var(--accent); }
.br-pr.on { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 10%, var(--bg)); }
.br-pr img { width: 46px; aspect-ratio: 5 / 7; border-radius: 4px; background: var(--bg-elevated); display: block; grid-column: 1; grid-row: 1 / span 2; }
.br-pr .nm { grid-column: 2; grid-row: 1; align-self: end; font-weight: 600; font-size: 0.88rem; }
.br-pr .sub { grid-column: 2; grid-row: 2; align-self: start; color: var(--text-dim); font-size: 0.78rem; }
.br-pr .px { grid-column: 3; grid-row: 1 / span 2; text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; color: var(--owned); font-size: 0.92rem; }
.br-pr .px small { display: block; color: var(--text-dim); font-weight: 400; font-size: 0.72rem; }
.br-pr .tag { display: inline-block; margin-left: 6px; padding: 0 6px; border-radius: 999px; border: 1px solid var(--accent); color: var(--accent); font-size: 0.68rem; font-weight: 700; }
.br-lb-close { position: absolute; top: 8px; right: 12px; background: none; border: none; color: var(--text-dim); font-size: 1.7rem; line-height: 1; cursor: pointer; z-index: 3; }
.br-lb-close:hover { color: var(--accent); }
.br-lb-nav { position: fixed; top: 50%; transform: translateY(-50%); width: 44px; height: 64px; border-radius: 12px; border: 1px solid var(--card-border); background: rgba(0,0,0,0.55); color: var(--text); font-size: 1.8rem; cursor: pointer; z-index: 351; }
.br-lb-nav:hover { color: var(--accent); border-color: var(--accent); }
.br-lb-nav.prev { left: 14px; } .br-lb-nav.next { right: 14px; }
#br-results .ins-card[data-i], #br-results tr[data-i] { cursor: pointer; }
/* Foil printings get the same hover shimmer as the compare report's tiles: a soft rainbow glow that follows the pointer. */
.pic.foil { --mx: 50%; --my: 50%; isolation: isolate; }
.pic.foil::before { content: ""; position: absolute; inset: 0; z-index: 1; pointer-events: none; border-radius: 6.5%; background: radial-gradient(circle at var(--mx) var(--my), rgba(255,255,255,0.22) 0%, rgba(120,200,255,0.14) 25%, rgba(255,80,220,0.12) 45%, transparent 70%); mix-blend-mode: soft-light; opacity: 0; transition: opacity 0.7s ease; }
.pic.foil:hover::before { opacity: 1; }
.pic.foil .qty, .pic.foil .rank, .pic.foil .flip { z-index: 2; }
@media (max-width: 800px) { .ins-two { grid-template-columns: 1fr; } .ins-theme { grid-template-columns: 1fr; gap: 4px; } .ins-pill { justify-self: start; } }
"""

COLLECTION_BODY = """
  <div class="ins-back"><a href="/decks" class="btn ghost small">&larr; Decks</a></div>
  <div class="ins-view-tabs" id="ins-view-tabs" role="tablist"><button type="button" data-view="insights" class="on">Insights</button><button type="button" data-view="browse" title="See, filter and add up the cards you own">Browse cards</button></div>
  <div id="insights"><div class="ins-loading">Reading your collection&hellip;</div></div>
  <div id="browse" hidden>
    <div class="card ins-section">
      <h2>Browse your cards</h2>
      <p class="ins-sub">Every card you own. Filter it any way you like and the totals follow.</p>
      <div class="br-filters">
        <input type="text" id="br-q" placeholder="Search name or type&hellip;" autocomplete="off">
        <select id="br-cat"><option value="">All types</option></select>
        <span class="br-pips" id="br-pips" title="Click a color to allow it, click again to require it (every card shown has it), a third time to clear"></span>
        <label class="br-check" title="Only cards whose color identity is exactly the selected colors, not also smaller ones that fit inside them"><input type="checkbox" id="br-exact" checked> Exact colors</label>
        <div class="br-setwrap"><button type="button" class="btn ghost small" id="br-setbtn">Sets</button>
          <div class="br-setpop" id="br-setpop"><input type="text" id="br-setq" placeholder="Find a set&hellip;" autocomplete="off"><div class="br-setlist" id="br-setlist"></div>
            <div class="br-setactions"><a href="#" id="br-setclear">Clear</a><a href="#" id="br-setdone">Done</a></div></div></div>
      </div>
      <div class="br-filters">
        <select id="br-role"><option value="">Any role</option></select>
        <select id="br-theme"><option value="">Any theme</option></select>
        <span class="br-sep" title="Judged by the priciest copy you own of each card">Price of a copy $</span><input type="number" id="br-pmin" min="0" step="0.01" placeholder="min"><span class="br-sep">to</span><input type="number" id="br-pmax" min="0" step="0.01" placeholder="max">
        <label class="br-check"><input type="checkbox" id="br-foil"> Has a foil</label>
        <select id="br-sort" title="How to order the cards">
          <option value="name">Sort: name</option><option value="each">Sort: priciest copy</option><option value="value">Sort: total value</option>
          <option value="cmc">Sort: mana value</option><option value="rank">Sort: popularity</option><option value="qty">Sort: copies owned</option>
        </select>
        <button type="button" class="btn ghost small" id="br-dir" title="Reverse the order">&#8593;</button>
        <div class="ins-tabs" id="br-viewmode"><button type="button" data-v="cards" class="on">Cards</button><button type="button" data-v="list">List</button></div>
        <a href="#" id="br-reset" style="font-size:0.85rem;">Reset filters</a>
      </div>
      <div class="stat-tiles br-summary" id="br-summary"></div>
      <p class="br-note" id="br-note"></p>
      <div id="br-results"><div class="ins-loading">Loading your cards&hellip;</div></div>
    </div>
  </div>
  <div id="ins-tip" role="tooltip"></div>
  <div id="ins-zoom"><img alt=""></div>
  <div class="br-lb" id="br-lb" role="dialog" aria-modal="true" aria-label="Card">
    <button type="button" class="br-lb-nav prev" id="br-lb-prev" aria-label="Previous card">&lsaquo;</button>
    <button type="button" class="br-lb-nav next" id="br-lb-next" aria-label="Next card">&rsaquo;</button>
    <div class="br-lb-panel">
      <button type="button" class="br-lb-close" id="br-lb-close" aria-label="Close">&times;</button>
      <div class="br-lb-art"><div class="br-tilt" id="br-tilt"><img id="br-lb-img" alt=""><div class="br-glare"></div></div><button type="button" class="flip" id="br-lb-flip" title="Show the other face" hidden>&#8635;</button></div>
      <div class="br-lb-info" id="br-lb-info"></div>
    </div>
  </div>
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
    + tile('$' + fmt(Math.round(s.value)), 'Market value', 'at today’s market price')
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
let priceStores = 'your chosen stores';
let modalCards = [], modalSeq = 0, modalKey = '', modalMode = 'owned', modalCache = {};
const cardImg = (id, face) => id ? 'https://cards.scryfall.io/normal/' + (face || 'front') + '/' + id[0] + '/' + id[1] + '/' + id + '.jpg' : '';
// only these layouts have a second image on Scryfall (adventure/split cards 404 on /back/)
const FLIPS = ['transform', 'modal_dfc'];
const flipBtn = (c) => (c.scryfall_id && FLIPS.includes(c.layout)) ? '<button type="button" class="flip" title="Show the other face">\u21bb</button>' : '';
const STORES = { MP: 'ManaPool', TCGP: 'TCGplayer', CK: 'Card Kingdom' };
const scryUrl = (name) => 'https://scryfall.com/search?q=' + encodeURIComponent('!"' + name + '"');
function renderModalCards() {
  const q = modalFilter.value.trim().toLowerCase();
  const shown = q ? modalCards.filter((c) => (c.name + ' ' + (c.type_line || '')).toLowerCase().includes(q)) : modalCards;
  const missing = modalMode === 'missing';
  const copies = shown.reduce((n, c) => n + (c.quantity || 1), 0);
  document.getElementById('ins-modal-count').textContent = shown.length + (shown.length === 1 ? ' card' : ' cards') + (!missing && copies !== shown.length ? ' · ' + copies + ' copies' : '');
  const noneOwned = modalCache.owned && modalCache.owned.length === 0;
  const hint = missing ? '<div class="ins-modal-hint">' + (noneOwned ? 'You don\u2019t own any of these yet. ' : '') + 'The most popular Commander-legal cards like this that aren’t in your collection, most popular first (EDHREC rank). Prices are the cheapest nonfoil from ' + esc(priceStores) + ' (change the stores on the Decks page); click a price to open that listing, or a card for its Scryfall page.</div>' : '';
  const tile = (c) => {
    const img = c.scryfall_id ? '<img loading="lazy" alt="' + esc(c.name) + '" src="' + cardImg(c.scryfall_id) + '">' : '<div style="aspect-ratio:5/7;border-radius:6%;background:var(--bg);"></div>';
    if (!missing) {
      return '<div class="ins-card" data-id="' + esc(c.scryfall_id || '') + '"><div class="pic' + (c.foil ? ' foil' : '') + '">' + img + flipBtn(c) + ((c.quantity || 1) > 1 ? '<span class="qty">×' + c.quantity + '</span>' : '') + '</div>'
        + '<div class="nm">' + esc(c.name) + (c.foil ? ' ✦' : '') + '</div><div class="ty">' + esc(c.type_line || '') + '</div></div>';
    }
    const price = c.price != null ? '<a class="price" target="_blank" rel="noopener noreferrer" href="' + esc(c.url || scryUrl(c.name)) + '" title="Cheapest nonfoil listing">$' + Number(c.price).toFixed(2) + ' · ' + esc(STORES[c.store] || c.store || '') + '</a>' : '';
    return '<div class="ins-card" data-id="' + esc(c.scryfall_id || '') + '"><div class="pic"><a class="img" target="_blank" rel="noopener noreferrer" href="' + scryUrl(c.name) + '">' + img + '</a>' + flipBtn(c)
      + (c.rank ? '<span class="rank">#' + fmt(c.rank) + '</span>' : '') + '</div>'
      + '<div class="nm">' + esc(c.name) + '</div><div class="ty">' + esc(c.type_line || '') + '</div>' + price + '</div>';
  };
  modalBody.innerHTML = hint + (shown.length ? '<div class="ins-cards">' + shown.map(tile).join('') + '</div>' : '<div class="ins-empty">' + (missing && !modalCards.length ? 'Nothing popular left to add here.' : 'No cards match.') + '</div>');
}
function loadModalMode() {
  const mine = ++modalSeq;
  document.querySelectorAll('.ins-modal-head .ins-tabs button').forEach((b) => b.classList.toggle('on', b.dataset.mode === modalMode));
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
document.querySelector('.ins-modal-head .ins-tabs').addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (b && b.dataset.mode !== modalMode) { modalMode = b.dataset.mode; loadModalMode(); }
});
document.getElementById('insights').addEventListener('click', (e) => {
  const mode = e.target.closest('.ins-mode button');
  if (mode) { heatMode = mode.dataset.mode; paintHeat(); return; }
  const el = e.target.closest('[data-key]');
  if (el) openList(el.dataset.key, el.dataset.title || 'Cards');
});

// ---- zoom a card in the card view on hover ----
const zoomEl = document.getElementById('ins-zoom');
const zoomImg = zoomEl.querySelector('img');
let zoomId = null;
const faceOf = (card) => card.dataset.face === 'back' ? 'back' : 'front';
const hideZoom = () => { zoomId = null; zoomEl.classList.remove('show'); };
// hover-enlarge and the flip button, for any grid of .ins-card tiles (the modal and the Browse view)
function attachZoom(box) {
  box.addEventListener('mousemove', (e) => {
    const card = e.target.closest ? e.target.closest('.ins-card[data-id]') : null;
    const id = card && card.dataset.id;
    if (!id) { if (zoomId) hideZoom(); return; }
    const key = id + faceOf(card);
    if (key !== zoomId) {
      zoomId = key;
      zoomImg.src = cardImg(id, faceOf(card));
      zoomEl.classList.add('show');
    }
    const pad = 22, w = zoomEl.offsetWidth, h = zoomEl.offsetHeight || 420;
    let x = e.clientX + pad, y = e.clientY - h / 2;
    if (x + w > window.innerWidth - 8) x = e.clientX - w - pad;
    y = Math.max(8, Math.min(y, window.innerHeight - h - 8));
    zoomEl.style.left = Math.max(8, x) + 'px'; zoomEl.style.top = y + 'px';
  });
  // the flip button turns a double-faced card (tile and zoom) to its other face
  box.addEventListener('click', (e) => {
    const btn = e.target.closest ? e.target.closest('.flip') : null;
    if (!btn) return;
    e.preventDefault(); e.stopPropagation();
    const card = btn.closest('.ins-card');
    const face = faceOf(card) === 'back' ? 'front' : 'back';
    card.dataset.face = face;
    card.querySelector('img').src = cardImg(card.dataset.id, face);
    if (zoomId) { zoomId = card.dataset.id + face; zoomImg.src = cardImg(card.dataset.id, face); }
  }, true);
  box.addEventListener('mouseleave', hideZoom);
  box.addEventListener('scroll', hideZoom, true);
}
attachZoom(modalBody);
// the foil shimmer follows the pointer across a foil card's picture
function attachFoil(box) {
  box.addEventListener('mousemove', (e) => {
    const t = e.target.closest ? e.target.closest('.pic.foil') : null;
    if (!t) return;
    const r = t.getBoundingClientRect();
    t.style.setProperty('--mx', ((e.clientX - r.left) / r.width * 100) + '%');
    t.style.setProperty('--my', ((e.clientY - r.top) / r.height * 100) + '%');
  });
}
attachFoil(modalBody);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hideZoom(); });
document.getElementById('ins-modal-close').addEventListener('click', hideZoom);

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

// ---- Browse cards: every card you own, filtered and added up ----
const brEl = (id) => document.getElementById(id);
const BR = { data: null, loaded: false, loading: false, q: '', cat: '', role: '', theme: '', pips: { W: 0, U: 0, B: 0, R: 0, G: 0 }, colorless: false, exact: true,
  sets: new Set(), pmin: null, pmax: null, foil: false, sort: 'name', desc: false, view: 'cards', shown: 96 };
const money = (n) => '$' + Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const PAGE = 96;

// what one copy is worth: a single figure, or the range when the printings you hold differ (a foil and a regular copy)
const eachTxt = (c) => c.top == null ? '—' : (c.top === c.low ? money(c.top) : money(c.low) + '–' + money(c.top));
// the tile shows one printing (the best one you own); it shimmers when that printing is a foil
const shownIsFoil = (c) => { const x = (c.pr || []).find((p) => p.id === c.id); return x ? x.f > 0 : c.foil > 0; };
function brMatchesColor(ci) {
  const must = 'WUBRG'.split('').filter((c) => BR.pips[c] === 2), allowed = 'WUBRG'.split('').filter((c) => BR.pips[c] >= 1);
  if (must.length) return must.every((c) => ci.includes(c)) && ci.every((c) => allowed.includes(c));
  if (allowed.length) {
    if (BR.exact) return (ci.length === allowed.length && allowed.every((c) => ci.includes(c))) || (BR.colorless && ci.length === 0);
    if (allowed.length === 1) return ci.includes(allowed[0]) || (BR.colorless && ci.length === 0);
    return (ci.length > 0 && ci.every((c) => allowed.includes(c))) || (BR.colorless && ci.length === 0);
  }
  return BR.colorless ? ci.length === 0 : true;
}
function brFiltered() {
  const q = BR.q.trim().toLowerCase();
  const role = BR.role === '' ? -1 : Number(BR.role), theme = BR.theme === '' ? -1 : Number(BR.theme);
  const priceOn = BR.pmin != null || BR.pmax != null;
  const rows = BR.data.cards.filter((c) => {
    if (q && !(c.name + ' ' + (c.type_line || '')).toLowerCase().includes(q)) return false;
    if (BR.cat && c.category !== BR.cat) return false;
    if (!brMatchesColor(c.ci || [])) return false;
    if (role >= 0 && !c.roles.includes(role)) return false;
    if (theme >= 0 && !c.themes.includes(theme)) return false;
    if (BR.sets.size && !c.sets.some((s) => BR.sets.has(s))) return false;
    if (BR.foil && !(c.foil > 0)) return false;
    if (priceOn) {
      if (c.top == null) return false;
      if (BR.pmin != null && c.top < BR.pmin) return false;
      if (BR.pmax != null && c.top > BR.pmax) return false;
    }
    return true;
  });
  // Ascending is the natural order of what is sorted; cards with nothing to sort on (no price, unranked) always go last.
  const dir = BR.desc ? -1 : 1, key = BR.sort;
  const val = { name: (c) => c.name.toLowerCase(), each: (c) => c.top, value: (c) => c.value, cmc: (c) => c.cmc, rank: (c) => c.rank, qty: (c) => c.qty }[key];
  rows.sort((a, b) => {
    const x = val(a), y = val(b);
    if (x == null && y == null) return a.name.localeCompare(b.name);
    if (x == null) return 1;
    if (y == null) return -1;
    return (x < y ? -1 : x > y ? 1 : 0) * dir || a.name.localeCompare(b.name);
  });
  return rows;
}
function brRender() {
  if (!BR.data) return;
  const rows = brFiltered();
  BR.rows = rows;  // the lightbox steps through exactly what is listed
  const copies = rows.reduce((n, c) => n + c.qty, 0);
  const worth = rows.reduce((n, c) => n + (c.value || 0), 0);
  const unpriced = rows.reduce((n, c) => n + c.unpriced, 0);
  const best = rows.reduce((m, c) => (c.top != null && (!m || c.top > m.top) ? c : m), null);
  const tile = (value, label, sub) => '<div class="stat-tile"><div class="stat-value">' + value + '</div><div class="stat-label">' + label + '</div>' + (sub ? '<div class="stat-sub">' + sub + '</div>' : '') + '</div>';
  const filtering = rows.length !== BR.data.cards.length;
  brEl('br-summary').innerHTML = tile(fmt(rows.length), 'Unique cards', filtering ? 'of ' + fmt(BR.data.cards.length) + ' you own' : 'you own')
    + tile(fmt(copies), 'Copies', '')
    + tile(money(worth), 'Total value', filtering ? 'of ' + money(BR.data.total_value) + ' overall' : 'at today’s market price')
    + tile(rows.length ? money(worth / rows.length) : '—', 'Average per card', rows.length ? 'per unique card' : '')
    + tile(best ? money(best.top) : '—', 'Priciest copy', best ? esc(best.name) : '');
  brEl('br-note').textContent = 'Value is today’s market price of the exact printing you own (the same prices as the Market value tile; not your store setting, which only applies to cards you’d buy).'
    + (unpriced ? ' ' + fmt(unpriced) + ' ' + (unpriced === 1 ? 'copy has' : 'copies have') + ' no price on file and count as $0.' : '')
    + ((BR.pmin != null || BR.pmax != null) ? ' With a price filter on, cards with no price are left out.' : '');
  const shownRows = rows.slice(0, BR.shown);
  let html;
  if (!rows.length) html = '<div class="ins-empty">No cards match these filters.</div>';
  else if (BR.view === 'list') {
    html = '<table class="br-list"><thead><tr><th>Card</th><th>Type</th><th>Sets</th><th class="num">Copies</th><th class="num">Each copy</th><th class="num">Value</th></tr></thead><tbody>'
      + shownRows.map((c, i) => '<tr class="ins-card" data-i="' + i + '" data-id="' + esc(c.id || '') + '"><td>' + esc(c.name) + (c.foil ? ' ✦' : '') + '</td><td class="ty">' + esc(c.type_line || '') + '</td><td>' + esc(c.sets.join(', ')) + '</td><td class="num">' + c.qty + '</td><td class="num">' + eachTxt(c) + '</td><td class="num">' + (c.value != null ? money(c.value) : '—') + '</td></tr>').join('')
      + '</tbody></table>';
  } else {
    html = '<div class="ins-cards" style="padding:0;">' + shownRows.map((c, i) => {
      const img = c.id ? '<img loading="lazy" alt="' + esc(c.name) + '" src="' + cardImg(c.id) + '">' : '<div style="aspect-ratio:5/7;border-radius:6%;background:var(--bg);"></div>';
      const worthTxt = c.value != null ? '<span class="worth">' + money(c.value) + (c.qty > 1 ? ' <small>(' + eachTxt(c) + (c.top === c.low ? ' each' : '') + ')</small>' : '') + '</span>' : '<span class="worth"><small>no price</small></span>';
      return '<div class="ins-card" data-i="' + i + '" data-id="' + esc(c.id || '') + '"><div class="pic' + (shownIsFoil(c) ? ' foil' : '') + '">' + img + flipBtn({ scryfall_id: c.id, layout: c.layout }) + (c.qty > 1 ? '<span class="qty">×' + c.qty + '</span>' : '') + '</div>'
        + '<div class="nm">' + esc(c.name) + (c.foil ? ' ✦' : '') + '</div><div class="ty">' + esc(c.type_line || '') + '</div>' + worthTxt + '</div>';
    }).join('') + '</div>';
  }
  const left = rows.length - shownRows.length;
  if (left > 0) html += '<div class="br-more"><button type="button" class="btn ghost" id="br-more">Show ' + Math.min(PAGE, left) + ' more (' + fmt(left) + ' not shown)</button></div>';
  brEl('br-results').innerHTML = html;
  hideZoom();
}
function brChanged() { BR.shown = PAGE; brRender(); }

function brSyncPips() {
  brEl('br-pips').querySelectorAll('.br-pip').forEach((b) => {
    const c = b.dataset.c;
    const st = c === 'C' ? (BR.colorless ? 1 : 0) : BR.pips[c];
    b.classList.toggle('on', st === 1); b.classList.toggle('must', st === 2);
  });
  const anyMust = 'WUBRG'.split('').some((c) => BR.pips[c] === 2);
  brEl('br-exact').disabled = anyMust;
  brEl('br-exact').closest('label').style.opacity = anyMust ? '0.45' : '';
}
function brRenderSets() {
  const q = brEl('br-setq').value.trim().toLowerCase();
  brEl('br-setlist').innerHTML = BR.data.sets.filter((s) => !q || (s.name + ' ' + s.code).toLowerCase().includes(q)).map((s) =>
    '<label><input type="checkbox" value="' + esc(s.code) + '"' + (BR.sets.has(s.code) ? ' checked' : '') + '>' + esc(s.name) + '<small>' + esc(s.code) + ' · ' + s.n + '</small></label>').join('')
    || '<div class="ins-empty" style="padding:10px;">No sets match.</div>';
  brEl('br-setbtn').textContent = BR.sets.size ? 'Sets (' + BR.sets.size + ')' : 'Sets';
}
function brInit(d) {
  BR.data = d; BR.loaded = true;
  brEl('br-cat').innerHTML += d.categories.map((c) => '<option value="' + esc(c) + '">' + esc(c) + '</option>').join('');
  brEl('br-role').innerHTML += d.roles.map((r, i) => '<option value="' + i + '">' + esc(r) + '</option>').join('');
  brEl('br-theme').innerHTML += d.themes.map((t, i) => '<option value="' + i + '">' + esc(t) + '</option>').join('');
  const names = { W: 'White', U: 'Blue', B: 'Black', R: 'Red', G: 'Green', C: 'Colorless' };
  brEl('br-pips').innerHTML = 'WUBRGC'.split('').map((c) => '<button type="button" class="br-pip" data-c="' + c + '" aria-label="' + names[c] + '"><img src="/static/mana/' + c + '.svg" alt=""></button>').join('');
  brSyncPips(); brRenderSets(); brRender();
}
function brLoad() {
  if (BR.loaded || BR.loading) return;
  BR.loading = true;
  fetch('/collection/browse').then((r) => r.json()).then((d) => {
    BR.loading = false;
    if (d.error) { brEl('br-results').innerHTML = '<div class="ins-empty">' + esc(d.error) + '</div>'; return; }
    brInit(d);
  }).catch(() => { BR.loading = false; brEl('br-results').innerHTML = '<div class="ins-empty">Could not reach the server.</div>'; });
}
function setView(v) {
  document.querySelectorAll('#ins-view-tabs button').forEach((b) => b.classList.toggle('on', b.dataset.view === v));
  brEl('insights').hidden = v !== 'insights'; brEl('browse').hidden = v !== 'browse';
  try { history.replaceState(null, '', v === 'browse' ? '#browse' : location.pathname); } catch (e) {}
  if (v === 'browse') brLoad();
}
brEl('ins-view-tabs').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) setView(b.dataset.view); });
brEl('br-q').addEventListener('input', (e) => { BR.q = e.target.value; brChanged(); });
brEl('br-cat').addEventListener('change', (e) => { BR.cat = e.target.value; brChanged(); });
brEl('br-role').addEventListener('change', (e) => { BR.role = e.target.value; brChanged(); });
brEl('br-theme').addEventListener('change', (e) => { BR.theme = e.target.value; brChanged(); });
brEl('br-exact').addEventListener('change', (e) => { BR.exact = e.target.checked; brChanged(); });
brEl('br-foil').addEventListener('change', (e) => { BR.foil = e.target.checked; brChanged(); });
const numOrNull = (v) => (v === '' || isNaN(Number(v))) ? null : Number(v);
brEl('br-pmin').addEventListener('input', (e) => { BR.pmin = numOrNull(e.target.value); brChanged(); });
brEl('br-pmax').addEventListener('input', (e) => { BR.pmax = numOrNull(e.target.value); brChanged(); });
brEl('br-sort').addEventListener('change', (e) => {
  BR.sort = e.target.value;
  BR.desc = ['each', 'value', 'qty'].includes(BR.sort);  // money and copies read best biggest first; the rest start at the small end
  brEl('br-dir').innerHTML = BR.desc ? '&#8595;' : '&#8593;'; brChanged();
});
brEl('br-dir').addEventListener('click', () => { BR.desc = !BR.desc; brEl('br-dir').innerHTML = BR.desc ? '&#8595;' : '&#8593;'; brChanged(); });
brEl('br-viewmode').addEventListener('click', (e) => {
  const b = e.target.closest('button'); if (!b) return;
  BR.view = b.dataset.v; brEl('br-viewmode').querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b)); brChanged();
});
brEl('br-pips').addEventListener('click', (e) => {
  const b = e.target.closest('.br-pip'); if (!b) return;
  const c = b.dataset.c;
  if (c === 'C') { if ('WUBRG'.split('').some((x) => BR.pips[x] === 2)) return; BR.colorless = !BR.colorless; }
  else { BR.pips[c] = (BR.pips[c] + 1) % 3; if (BR.pips[c] === 2) BR.colorless = false; }
  brSyncPips(); brChanged();
});
brEl('br-setbtn').addEventListener('click', (e) => { e.stopPropagation(); brEl('br-setpop').classList.toggle('show'); if (brEl('br-setpop').classList.contains('show')) brEl('br-setq').focus(); });
brEl('br-setpop').addEventListener('click', (e) => e.stopPropagation());
document.addEventListener('click', () => brEl('br-setpop').classList.remove('show'));
brEl('br-setq').addEventListener('input', brRenderSets);
brEl('br-setlist').addEventListener('change', (e) => {
  const cb = e.target; if (!cb.value) return;
  if (cb.checked) BR.sets.add(cb.value); else BR.sets.delete(cb.value);
  brEl('br-setbtn').textContent = BR.sets.size ? 'Sets (' + BR.sets.size + ')' : 'Sets'; brChanged();
});
brEl('br-setclear').addEventListener('click', (e) => { e.preventDefault(); BR.sets.clear(); brRenderSets(); brChanged(); });
brEl('br-setdone').addEventListener('click', (e) => { e.preventDefault(); brEl('br-setpop').classList.remove('show'); });
brEl('br-reset').addEventListener('click', (e) => {
  e.preventDefault();
  Object.assign(BR, { q: '', cat: '', role: '', theme: '', colorless: false, exact: true, pmin: null, pmax: null, foil: false, sort: 'name', desc: false });
  BR.sets.clear(); 'WUBRG'.split('').forEach((c) => { BR.pips[c] = 0; });
  ['br-q', 'br-pmin', 'br-pmax'].forEach((id) => { brEl(id).value = ''; });
  ['br-cat', 'br-role', 'br-theme'].forEach((id) => { brEl(id).value = ''; });
  brEl('br-sort').value = 'name'; brEl('br-exact').checked = true; brEl('br-foil').checked = false; brEl('br-dir').innerHTML = '&#8593;';
  brSyncPips(); brRenderSets(); brChanged();
});
brEl('br-results').addEventListener('click', (e) => { if (e.target.closest('#br-more')) { BR.shown += PAGE; brRender(); } });
attachZoom(brEl('br-results'));
attachFoil(brEl('br-results'));
// the card follows the pointer like it is held in a hand: tilt, a sliding shadow and a moving glare
const lbArt = brEl('br-lb').querySelector('.br-lb-art'), lbTilt = brEl('br-tilt');
const noMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
lbArt.addEventListener('pointermove', (e) => {
  if (noMotion) return;
  const r = lbArt.getBoundingClientRect();
  const nx = Math.max(-1, Math.min(1, (e.clientX - r.left) / r.width * 2 - 1)), ny = Math.max(-1, Math.min(1, (e.clientY - r.top) / r.height * 2 - 1));
  lbTilt.classList.add('live');
  const st = lbTilt.style;
  st.setProperty('--ry', (nx * 14).toFixed(2) + 'deg'); st.setProperty('--rx', (-ny * 14).toFixed(2) + 'deg'); st.setProperty('--sc', '1.02');
  st.setProperty('--sh', (-nx * 22).toFixed(1) + 'px ' + (-ny * 22 + 14).toFixed(1) + 'px 38px rgba(0,0,0,0.65)');
  st.setProperty('--gx', ((nx + 1) * 50).toFixed(1) + '%'); st.setProperty('--gy', ((ny + 1) * 50).toFixed(1) + '%'); st.setProperty('--go', '1');
});
lbArt.addEventListener('pointerleave', () => {
  lbTilt.classList.remove('live');
  ['--rx', '--ry', '--sc', '--sh', '--go'].forEach((k) => lbTilt.style.removeProperty(k));
});

// ---- click a card: a lightbox with every printing of it you own ----
const LB = { i: -1, p: 0, face: 'front' };
const finishName = (f) => f === 2 ? 'Etched' : (f === 1 ? 'Foil' : 'Nonfoil');
function lbRender() {
  const c = (BR.rows || [])[LB.i]; if (!c) return;
  const pr = c.pr && c.pr.length ? c.pr : [{ id: c.id, set: (c.sets || [])[0] || '', n: '', f: c.foil ? 1 : 0, q: c.qty, p: c.each }];
  if (LB.p >= pr.length) LB.p = 0;
  const cur = pr[LB.p];
  const img = brEl('br-lb-img');
  img.src = cardImg(cur.id, LB.face); img.alt = c.name;
  brEl('br-tilt').classList.toggle('holo', cur.f > 0);
  const canFlip = FLIPS.includes(c.layout);
  brEl('br-lb-flip').hidden = !canFlip;
  const names = (BR.data && BR.data.set_names) || {};
  const total = c.value != null ? '<b>' + money(c.value) + '</b>' : '<b>no price</b>';
  brEl('br-lb-info').innerHTML = '<h3>' + esc(c.name) + '</h3><div class="ty">' + esc(c.type_line || '') + '</div>'
    + '<div class="sum">' + c.qty + (c.qty === 1 ? ' copy' : ' copies') + ' · ' + total + (c.unpriced ? ' <span class="ty">(' + c.unpriced + ' unpriced)</span>' : '') + '</div>'
    + '<h4>' + (pr.length > 1 ? 'Your ' + pr.length + ' printings' : 'Your printing') + '</h4>'
    + pr.map((x, k) => '<button type="button" class="br-pr' + (k === LB.p ? ' on' : '') + '" data-k="' + k + '">'
      + '<img loading="lazy" alt="" src="' + smallImg(x.id) + '">'
      + '<span class="nm">' + esc(names[x.set] || x.set || 'Unknown set') + (x.f ? '<span class="tag">' + finishName(x.f) + '</span>' : '') + '</span>'
      + '<span class="px">' + (x.p != null ? money(x.p * x.q) + (x.q > 1 ? '<small>' + money(x.p) + ' each</small>' : '') : '—') + '</span>'
      + '<span class="sub">' + esc(x.set) + (x.n ? ' #' + esc(x.n) : '') + ' · ×' + x.q + '</span></button>').join('');
  const many = (BR.rows || []).length > 1;
  brEl('br-lb-prev').style.display = brEl('br-lb-next').style.display = many ? '' : 'none';
}
function lbOpen(i) { hideZoom(); LB.i = i; LB.p = 0; LB.face = 'front'; brEl('br-lb').classList.add('show'); document.body.style.overflow = 'hidden'; lbRender(); }
function lbClose() { brEl('br-lb').classList.remove('show'); document.body.style.overflow = ''; LB.i = -1; }
function lbStep(d) { const n = (BR.rows || []).length; if (!n) return; LB.i = (LB.i + d + n) % n; LB.p = 0; LB.face = 'front'; lbRender(); }
brEl('br-results').addEventListener('click', (e) => {
  if (e.target.closest('.flip') || e.target.closest('#br-more')) return;
  const t = e.target.closest('[data-i]'); if (t) lbOpen(Number(t.dataset.i));
});
brEl('br-lb').addEventListener('click', (e) => { if (e.target === brEl('br-lb')) lbClose(); });
brEl('br-lb-close').addEventListener('click', lbClose);
brEl('br-lb-prev').addEventListener('click', () => lbStep(-1));
brEl('br-lb-next').addEventListener('click', () => lbStep(1));
brEl('br-lb-flip').addEventListener('click', () => { LB.face = LB.face === 'back' ? 'front' : 'back'; lbRender(); });
brEl('br-lb-info').addEventListener('click', (e) => { const b = e.target.closest('.br-pr'); if (b) { LB.p = Number(b.dataset.k); LB.face = 'front'; lbRender(); } });
document.addEventListener('keydown', (e) => {
  if (!brEl('br-lb').classList.contains('show')) return;
  if (e.key === 'Escape') lbClose(); else if (e.key === 'ArrowLeft') lbStep(-1); else if (e.key === 'ArrowRight') lbStep(1);
});
if (location.hash === '#browse') setView('browse');

fetch('/collection/data').then((r) => r.json()).then((d) => {
  const el = document.getElementById('insights');
  if (d.error) { el.innerHTML = '<div class="card"><p>' + esc(d.error) + '</p><a class="btn small" href="/">Go to Setup</a></div>'; return; }
  if (d.price_stores && d.price_stores.length) priceStores = d.price_stores.join(', ');
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
