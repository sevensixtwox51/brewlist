"""The full deck analysis for the compare report: the same checks the Deck Builder's Analyze has (Rule 0 summary and bracket,
land and color-source check, mana simulation, Turn Clock, Resilience, synergy packages, Matchups), drawn into the compare report.

Pure presentation. The numbers come from the same /builder/* endpoints the builder's Analyze uses, called with the compared
deck's cards, so the two can't drift apart. brewlist_core.render_html embeds ANALYSIS_CSS, ANALYSIS_JS and the deck payload; the
report page is served by the web app, so the endpoints are there. (A report saved as a plain file has no server, so the panel
says so instead of failing quietly.)
"""
from matchups_ui import MATCHUPS_CSS, MATCHUPS_JS

ANALYSIS_CSS = MATCHUPS_CSS + """
/* ---- Deck Analysis: laid out like the Deck Builder's Analyze (full-width Rule 0 panel, then two columns of titled panels) ---- */
.deck-analysis-body { display: block; padding: 4px 16px 16px; }
.deck-analysis-body > .ai-summary-note { margin: 0 0 14px; }
.an-grid { columns: 2 440px; column-gap: 16px; }
.an-grid > .an-panel { break-inside: avoid; }
.deck-analysis-body .an-panel { margin-bottom: 16px; }
.deck-analysis-body .an-panel > h4.analysis-heading { margin: 0 0 12px; padding-bottom: 7px; border-bottom: 1px solid var(--card-border); font-family: var(--serif); font-size: 1.1rem; font-weight: 600; color: var(--accent); letter-spacing: 0.01em; }
.deck-analysis-body .sample-hand.an-panel { grid-column: auto; }
@media (max-width: 900px) { .an-grid { columns: 1; } }
.deck-analysis-body .hint { color: var(--text-dim); font-size: 0.8rem; margin: 6px 0 8px; }
.deck-analysis-body .analysis-stat-line { color: var(--text-dim); font-size: 0.78rem; margin: 0 0 8px; }
.deck-analysis-body .curve-chart { gap: 4px; height: 90px; }
.deck-analysis-body .curve-label { font-size: 0.68rem; margin-top: 3px; }
.deck-analysis-body .chart-legend { gap: 12px; font-size: 0.72rem; margin: 6px 0 8px; }
.deck-analysis-body .chart-legend .swatch { width: 9px; height: 9px; margin-right: 4px; }
.deck-analysis-body .color-breakdown { display: flex; flex-direction: column; gap: 6px; margin-bottom: 8px; }
.deck-analysis-body .color-row { gap: 8px; }
.deck-analysis-body .color-row .mana-icon { width: 16px; height: 16px; }
.deck-analysis-body .color-bar-track { height: 7px; }
/* the builder's own bars, tiles and badges */
.deck-analysis-body .shape-row { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; font-size: 0.82rem; }
.deck-analysis-body .shape-row .shape-label { width: 80px; flex-shrink: 0; }
.deck-analysis-body .shape-bar-track { flex: 1; height: 9px; border-radius: 5px; background: var(--card-border); overflow: hidden; }
.deck-analysis-body .shape-bar { height: 100%; border-radius: 5px; }
.deck-analysis-body .shape-bar.under { background: var(--missing); }
.deck-analysis-body .shape-bar.near { background: var(--owned); }
.deck-analysis-body .shape-bar.over { background: var(--gold); }
.deck-analysis-body .shape-row .shape-count { width: 80px; text-align: right; flex-shrink: 0; color: var(--text-dim); }
.deck-analysis-body .stat-tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(132px, 1fr)); gap: 10px; margin: 0 0 14px; }
.deck-analysis-body .stat-tile { display: block; background: var(--bg-elevated); border: 1px solid var(--card-border); border-radius: 10px; padding: 10px 12px 11px; min-width: 0; box-shadow: none; }
.deck-analysis-body .stat-value { font-family: var(--serif); font-size: 1.9rem; font-weight: 600; line-height: 1.05; color: var(--accent); font-variant-numeric: tabular-nums; }
.deck-analysis-body .stat-tile.good .stat-value, .deck-analysis-body .stat-colors b.good { color: var(--owned); }
.deck-analysis-body .stat-tile.ok .stat-value, .deck-analysis-body .stat-colors b.ok { color: var(--gold); }
.deck-analysis-body .stat-tile.bad .stat-value, .deck-analysis-body .stat-colors b.bad { color: var(--missing); }
.deck-analysis-body .stat-label { margin-top: 5px; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.07em; color: var(--text-dim); }
.deck-analysis-body .stat-sub { margin-top: 2px; font-size: 0.72rem; color: var(--text-dim); line-height: 1.3; }
.deck-analysis-body .stat-colors { display: flex; flex-wrap: wrap; gap: 6px 12px; margin-top: 4px; }
.deck-analysis-body .stat-color { display: inline-flex; align-items: center; gap: 5px; }
.deck-analysis-body .stat-color .mana-icon { width: 18px; height: 18px; }
.deck-analysis-body .stat-color b { font-family: var(--serif); font-size: 1.05rem; font-weight: 600; }
.deck-analysis-body .stat-tile.span2 { grid-column: span 2; }
.rule0-list { margin: 0 0 4px; padding-left: 20px; font-size: 0.85rem; color: var(--text); columns: 2 340px; column-gap: 24px; }
.rule0-list li { margin-bottom: 5px; break-inside: avoid; }
.fa-badges { margin: 0 0 12px; }
.bv-badge { display: inline-flex; align-items: center; gap: 4px; padding: 4px 10px; margin: 0 6px 6px 0; border-radius: 999px; background: var(--bg); border: 1px solid var(--card-border); font-size: 0.78rem; color: var(--text); cursor: help; }
.bv-badge.warn { border-color: color-mix(in srgb, var(--missing) 45%, var(--card-border)); color: var(--missing); }
.bv-badge.good { border-color: color-mix(in srgb, var(--owned) 45%, var(--card-border)); color: var(--owned); }
.bv-badge.highlight { border-color: color-mix(in srgb, var(--gold) 45%, var(--card-border)); color: var(--gold); }
table.fa-land { width: 100%; font-size: 13px; border-collapse: collapse; }
table.fa-land th { text-align: left; font-weight: 600; color: var(--text-dim); }
table.fa-land td, table.fa-land th { padding: 3px 8px 3px 0; }
.fa-busy { display: none; align-items: center; justify-content: center; gap: 10px; margin: 0 0 14px; padding: 8px 14px; border: 1px solid var(--card-border); border-radius: 999px; background: var(--bg-elevated); font-size: 0.82rem; color: var(--text-dim); }
.fa-busy.on { display: flex; }
.fa-spinner { width: 15px; height: 15px; border-radius: 50%; border: 2px solid var(--card-border); border-top-color: var(--accent); animation: fa-spin 0.8s linear infinite; }
@keyframes fa-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .fa-spinner { animation: none; } }
.fa-note { color: var(--text-dim); font-size: 0.82rem; margin: 0 0 14px; }
#fa-hover { position: fixed; pointer-events: none; z-index: 400; display: none; width: 240px; border-radius: 4.75% / 3.5%; box-shadow: 0 12px 32px rgba(0,0,0,0.5), 0 0 0 1px var(--card-border); }
"""

def panel(pid, emoji, title):
    """An empty titled panel the page fills in once the numbers are in (hidden until then)."""
    return f'<div class="an-panel" id="{pid}" hidden><h4 class="analysis-heading">{emoji} {title}</h4><div class="fa-content"></div></div>'


ANALYSIS_STATUS_HTML = '<div class="fa-busy" id="fa-status" role="status" aria-live="polite"><span class="fa-spinner"></span><span id="fa-status-text"></span></div><div class="fa-note" id="fa-note" hidden></div>'
ANALYSIS_JS = r"""
// ---- Full analysis (analysis_ui.py): the builder's Analyze checks on the compared deck ----
function escapeHtml(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
function muImg(id) { return id ? 'https://cards.scryfall.io/normal/front/' + id[0] + '/' + id[1] + '/' + id + '.jpg' : ''; }
""" + MATCHUPS_JS + r"""
(function () {
  const deck = window.ANALYSIS_DECK;
  if (!deck) return;
  const $ = (id) => document.getElementById(id);
  const WUBRG = ['W', 'U', 'B', 'R', 'G'];
  const COLOR_NAMES = { W: 'White', U: 'Blue', B: 'Black', R: 'Red', G: 'Green' };
  const SOURCE_COLOR_HEX = { W: '#e9dfae', U: '#6fa8e0', B: '#8a7a8f', R: '#e0735a', G: '#6fb07a' };
  const BRACKET_TAG_LABELS = { E: 'Exhibition', C: 'Core', P: 'Powerful', O: 'Oddball', S: 'Spicy', R: 'Ruthless', B: 'Banned' };
  const MANA_SIM_GOOD = { 2: 95, 3: 88, 4: 78, 5: 66, 6: 55 };
  const ROLE_HELP = {
    Lands: 'Land cards in the 99-card library: nonbasic lands plus basics.',
    Ramp: 'Cards that make or fetch mana: mana rocks, mana creatures, and spells that put lands onto the battlefield.',
    Draw: 'Card advantage: spells and permanents that draw cards or let you play cards from the top of the library.',
    Interaction: 'Answers to what opponents do: targeted removal, board wipes, counterspells, bounce and tuck effects, and edicts.',
    Synergy: 'Everything else: creatures, win conditions, and cards that support the commander’s plan.',
  };
  const tile = (value, label, sub, cls, tip) => '<div class="stat-tile ' + (cls || '') + '"' + (tip ? ' title="' + escapeHtml(tip) + '"' : '') + '><div class="stat-value">' + value + '</div><div class="stat-label">' + label + '</div>' + (sub ? '<div class="stat-sub">' + sub + '</div>' : '') + '</div>';
  const tiles = (arr) => '<div class="stat-tiles">' + arr.filter(Boolean).join('') + '</div>';
  const row = (label, cls, pct, count, tip, labelW, countW) => '<div class="shape-row" title="' + escapeHtml(tip || '') + '"><span class="shape-label"' + (labelW ? ' style="min-width:' + labelW + 'px"' : '') + '>' + label + '</span><div class="shape-bar-track"><div class="shape-bar ' + cls + '" style="width:' + pct + '%"></div></div><span class="shape-count"' + (countW ? ' style="white-space:nowrap;min-width:' + countW + 'px"' : '') + '>' + count + '</span></div>';
  const fill = (id, html) => { const s = $(id); if (!s) return; s.hidden = false; s.querySelector('.fa-content').innerHTML = html; };
  const post = (url, body) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then((r) => r.json());
  const base = { cards: deck.cards, commander: deck.commander, format: 'commander' };

  function colorsLabel() {
    const set = new Set(deck.commander ? deck.commander.color_identity || [] : []);
    deck.cards.forEach((c) => (c.color_identity || []).forEach((x) => set.add(x)));
    const ordered = WUBRG.filter((c) => set.has(c));
    return ordered.length ? ordered.map((c) => COLOR_NAMES[c]).join(' / ') : 'Colorless';
  }
  function badgesHtml(d) {
    const b = [{ cls: 'highlight', label: '&#128176; $' + d.deck_value.toFixed(2), tip: "Total deck value at today's market price" }];
    b.push({ cls: d.game_changers ? 'highlight' : '', label: '&#9889; ' + d.game_changers + ' Game Changer' + (d.game_changers === 1 ? '' : 's'), tip: d.game_changers_names.length ? 'Game Changers in this deck: ' + d.game_changers_names.join(', ') : "On WotC's official Commander Game Changers list -- none found in this deck" });
    b.push(d.banned_count ? { cls: 'warn', label: '&#9940; ' + d.banned_count + ' not legal', tip: 'Cards not legal in Commander (banned/restricted)' } : { cls: 'good', label: '&#10003; Commander legal', tip: 'No Commander-banned or restricted cards found' });
    if (d.bracket_tag) b.push({ label: BRACKET_TAG_LABELS[d.bracket_tag] || d.bracket_tag, tip: "Commander Spellbook's own community power/style rating for this deck -- not the official WotC bracket system" });
    if (d.wotc_bracket) b.push({ label: 'Bracket ' + d.wotc_bracket[0], tip: "Estimated from WotC's published Bracket rules (Game Changers, mass land denial, combos, extra turns): " + d.wotc_bracket[1] });
    b.push(d.spellbook_unavailable ? { label: '&#128279; combos: lookup unavailable', tip: "Commander Spellbook couldn't be reached, so combos, mass land denial and extra turns are unknown, not zero." }
      : { label: '&#128279; ' + d.combos_included + ' combo' + (d.combos_included === 1 ? '' : 's') + ', ' + d.combos_almost + ' one away', tip: d.combos_included + ' known combo(s) already in this deck, and ' + d.combos_almost + ' more exactly one card away (via Commander Spellbook)' });
    return '<div class="fa-badges">' + b.map((x) => '<span class="bv-badge ' + (x.cls || '') + '" title="' + escapeHtml(x.tip) + '">' + x.label + '</span>').join('') + '</div>';
  }
  function rule0Html(d) {
    const items = ['<b>Colors:</b> ' + colorsLabel()];
    if (d.wotc_bracket) items.push('<b>Estimated Bracket ' + d.wotc_bracket[0] + '</b> (' + d.wotc_bracket[1] + ") -- WotC's published rules, from this deck's Game Changers, combos, extra turns and land denial");
    if (d.spellbook_unavailable) items.push("<b>Combo data unavailable:</b> Commander Spellbook couldn't be reached, so mass land denial, extra turns and combos are unknown (not zero)");
    items.push('<b>Game Changers (' + d.game_changers + '):</b> ' + (d.game_changers_names.length ? d.game_changers_names.map(escapeHtml).join(', ') : 'none'));
    if (!d.spellbook_unavailable) {
      const mld = d.mass_land_denial_names || [], xt = d.extra_turn_names || [];
      items.push('<b>Mass land denial:</b> ' + (d.mass_land_denial ? 'Yes' + (mld.length ? ' (' + mld.map(escapeHtml).join(', ') + ')' : '') : 'No'));
      items.push('<b>Extra-turn effects:</b> ' + d.extra_turn_count + (xt.length ? ' (' + xt.map(escapeHtml).join(', ') + ')' : ''));
      items.push('<b>Combos:</b> ' + d.combos_included + ' already assembled, ' + d.combos_almost + ' one card away');
    }
    items.push('<b>Deck value:</b> $' + d.deck_value.toFixed(2) + " (today's market)");
    return '<ul class="rule0-list">' + items.map((t) => '<li>' + t + '</li>').join('') + '</ul>';
  }
  function shapeHtml(d) {
    const counts = d.role_counts || {}, targets = d.role_targets || {};
    let html = ['Lands', 'Ramp', 'Draw', 'Interaction', 'Synergy'].map((role) => {
      const actual = counts[role] || 0, target = targets[role];
      if (target === null || target === undefined) return row(role, 'near', 0, actual + ' card(s)', ROLE_HELP[role]);
      const ratio = target ? actual / target : (actual ? 2 : 0);
      const cls = ratio < 0.8 ? 'under' : (ratio > 1.3 ? 'over' : 'near');
      return row(role, cls, Math.min(100, ratio * 100), actual + ' / ~' + target, actual + ' of a ~' + target + ' target. ' + (ROLE_HELP[role] || ''));
    }).join('');
    const k = d.karsten;
    if (k) {
      const diff = k.lands_actual - k.lands_formula;
      html += '<div class="hint"><b>Karsten’s land formula</b> gives ' + k.lands_formula + ' lands for this deck’s average mana value (' + k.avg_mv.toFixed(2) + ') and ' + k.cheap + ' cheap ramp or draw cards; it has ' + k.lands_actual + ' (' + (Math.abs(diff) < 1.5 ? 'about right' : (diff < 0 ? 'light on lands' : 'heavy on lands')) + ').</div>';
    }
    const cs = d.color_sources;
    if (cs && cs.length) {
      html += '<div class="hint"><b>Colored sources</b> against Karsten’s requirement (90–96% on-curve odds). A dual or tri-land counts toward every color it makes:</div>' + cs.map((r) => {
        const gap = r.need - r.have, cls = gap <= 0 ? 'near' : (gap <= 5 ? 'over' : 'under');
        const verdict = gap <= 0 ? 'enough' : (gap <= 5 ? 'a little short' : 'add ' + gap + ' more');
        const tip = r.have + ' ' + r.name + ' sources (lands that make it, plus mana rocks and dorks costing 2 or less). Karsten’s requirement is ~' + r.need + '; the hardest card is ' + r.hardest.card + ' at ~' + r.hardest.need + '.';
        return row('<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:' + SOURCE_COLOR_HEX[r.color] + ';margin-right:6px;"></span>' + r.name[0].toUpperCase() + r.name.slice(1), cls, Math.min(100, r.have / Math.max(1, r.need) * 100), r.have + ' / ~' + r.need + ' &middot; ' + verdict, tip, 0, 150);
      }).join('');
    }
    const fixes = d.mana_fixes || [];
    if (fixes.length) {
      html += '<div class="hint"><b>Land swaps from your collection</b> that would close those gaps:</div><ul class="rule0-list">' + fixes.map((f) => '<li>Swap a <b>' + escapeHtml(f.remove) + '</b> for <b>' + escapeHtml(f.add.name) + '</b> (' + f.makes.map((c) => '+' + c).join(' ') + (f.tapped ? ', enters tapped' : '') + ')</li>').join('') + '</ul>';
    }
    return html;
  }
  function manaSimHtml(sim) {
    const good = (v, hi, mid) => v >= hi ? 'good' : (v >= mid ? 'ok' : 'bad');
    const curve = [2, 3, 4, 5, 6].map((t) => {
      const pct = sim.on_curve[t], g = MANA_SIM_GOOD[t];
      return row('Turn ' + t, pct >= g ? 'near' : (pct >= g - 8 ? 'over' : 'under'), pct, pct + '%', 'In ' + pct + '% of ' + sim.games + ' games you have at least ' + t + ' mana on turn ' + t + ' (average ' + sim.avg_mana[t] + ').');
    }).join('');
    const colorTile = '<div class="stat-tile span2" title="Chance each color is available by turn 4"><div class="stat-colors">' + Object.entries(sim.color_by_turn_4).map(([c, p]) => SOURCE_COLOR_HEX[c] ? '<span class="stat-color"><img class="mana-icon" src="/static/mana/' + c + '.svg" alt="' + c + '"><b class="' + good(p, 94, 85) + '">' + p + '%</b></span>' : '').join('') + '</div><div class="stat-label">Color available by turn 4</div></div>';
    return '<div class="hint">' + sim.games.toLocaleString() + ' simulated games of just the mana game (' + sim.lands + ' lands, ' + sim.ramp + ' mana rocks, dorks and land-fetching spells, plus card draw): the chance of having at least as much mana as the turn number.</div>'
      + tiles([
        sim.commander ? tile(sim.commander.on_curve + '%', 'Commander on curve', 'mana value ' + sim.commander.mv + ' &middot; within two turns of that: ' + sim.commander.within_two + '%', good(sim.commander.on_curve, 80, 65), sim.commander.name + ' can be cast on curve in ' + sim.commander.on_curve + '% of games.') : '',
        tile(sim.avg_opening_lands, 'Opening lands', 'average &middot; ' + sim.mulligan_rate + '% mulligan', '', ''),
        tile(sim.stuck_on_two_turn_4 + '%', 'Stuck on turn 4', 'with two mana or less', sim.stuck_on_two_turn_4 <= 3 ? 'good' : (sim.stuck_on_two_turn_4 <= 8 ? 'ok' : 'bad'), ''),
        colorTile,
      ]) + curve + '<div class="hint">Mana only: no spells are cast but ramp.</div>';
  }
  function landCurveHtml(curve) {
    const wanted = new Set([curve.current - 4, curve.current - 2, curve.current, curve.current + 2, curve.current + 4]);
    const rows = curve.rows.filter((r) => wanted.has(r.lands)).map((r) => '<tr><td>' + r.lands + ' lands' + (r.lands === curve.current ? ' <span class="hint" style="margin:0">(yours)</span>' : '') + '</td><td>' + r.four_on_four + '%</td><td>' + r.five_on_five + '%</td><td>' + r.flood + '%</td></tr>').join('');
    const lo = curve.rows.find((r) => r.lands === curve.current - 4) || curve.rows[0], hi = curve.rows.find((r) => r.lands === curve.current + 4) || curve.rows[curve.rows.length - 1];
    const span = (hi.lands - lo.lands) || 1;
    return '<div class="hint" style="margin-top:12px"><b>Land count:</b> the same deck with more or fewer lands (' + curve.games.toLocaleString() + ' games each). Around ' + curve.current + ' lands, each extra land adds about <b>' + ((hi.four_on_four - lo.four_on_four) / span).toFixed(1) + '</b> points of four-mana-on-turn-4 odds and about <b>' + ((hi.flood - lo.flood) / span).toFixed(1) + '</b> points of flood odds.</div>'
      + '<table class="fa-land"><thead><tr><th>Lands</th><th title="Chance of at least four mana on turn 4">4 on T4</th><th title="Chance of at least five mana on turn 5">5 on T5</th><th title="Chance of 8 or more lands in the first 14 cards">Flood</th></tr></thead><tbody>' + rows + '</tbody></table>';
  }
  function synergyHtml(d) {
    const syn = d.synergy;
    if (!syn || !(syn.packages.length || syn.thin.length || syn.off_plan_total)) return '';
    const rows = syn.packages.map((p) => {
      const total = p.enablers + p.payoffs, share = Math.round(p.enablers / total * 100);
      const tip = p.blurb + '. Enablers (' + p.enablers + '): ' + p.enabler_cards.join(', ') + (p.enablers > p.enabler_cards.length ? '...' : '') + '. Payoffs (' + p.payoffs + '): ' + (p.payoff_cards.join(', ') || 'none') + (p.payoffs > p.payoff_cards.length ? '...' : '');
      return '<div class="shape-row" title="' + escapeHtml(tip) + '"><span class="shape-label" style="width:150px">' + escapeHtml(p.name) + '</span><div class="shape-bar-track" style="display:flex"><div class="shape-bar ' + (p.verdict === 'balanced' ? 'near' : 'over') + '" style="width:' + share + '%;border-radius:0"></div><div class="shape-bar near" style="width:' + (100 - share) + '%;border-radius:0;opacity:.55"></div></div><span class="shape-count" style="min-width:200px">' + p.enablers + ' enabler' + (p.enablers === 1 ? '' : 's') + ' &middot; ' + p.payoffs + ' payoff' + (p.payoffs === 1 ? '' : 's') + (p.verdict === 'balanced' ? '' : ' &middot; ' + p.verdict) + '</span></div>';
    }).join('');
    const thin = syn.thin.length ? '<div class="hint"><b>Payoffs with little to enable them:</b> ' + syn.thin.map((p) => escapeHtml(p.name) + ' (' + p.payoffs + ' payoffs, ' + p.enablers + ' enabler' + (p.enablers === 1 ? '' : 's') + ')').join('; ') + '. Those cards may sit idle in hand.</div>' : '';
    const off = syn.off_plan_total ? '<div class="hint"><b>Outside every package above (' + syn.off_plan_total + '):</b> ' + syn.off_plan.map((c) => escapeHtml(c.name)).join(', ') + (syn.off_plan_total > syn.off_plan.length ? '...' : '') + '. These are the likeliest cuts.</div>' : '';
    return (syn.packages.length ? '<div class="hint">What the deck is built around, from card text: enablers (left, bright) against payoffs (right). Hover a bar for the cards.</div>' + rows : '<div class="hint">No package has both enablers and payoffs in this deck yet.</div>') + thin + off;
  }
  function turnClockHtml(clock) {
    const bars = [];
    for (let t = 5; t <= clock.max_turn; t++) { const pct = clock.by_turn[t]; bars.push(row('By turn ' + t, 'near', pct, pct + '% &middot; table ' + clock.table_by_turn[t] + '%', 'In ' + pct + '% of ' + clock.games + ' simulated games the deck has dealt ' + clock.life + ' damage to one do-nothing opponent by turn ' + t + '. The table figure is the chance it has killed all ' + clock.opponents + ' opponents.', 0, 150)); }
    return '<div class="hint">' + clock.games.toLocaleString() + ' simulated games against opponents at ' + clock.life + ' life who block nothing and remove nothing. Commander damage (21) counts as a kill.</div>'
      + tiles([
        tile(clock.median_turn ? 'Turn ' + clock.median_turn : '&mdash;', 'Kills one opponent', clock.median_turn ? 'in half of games' + (clock.fast_turn ? ' &middot; fastest tenth by turn ' + clock.fast_turn : '') + (clock.slow_turn ? ', slowest tenth by turn ' + clock.slow_turn : '') : 'fewer than half of games by turn ' + clock.max_turn, '', ''),
        tile(clock.table_median_turn ? 'Turn ' + clock.table_median_turn : clock.table_kill_rate + '%', 'Whole table dead', clock.table_median_turn ? 'all ' + clock.opponents + ' opponents, in half of games' : 'of games by turn ' + clock.max_turn, '', ''),
        tile(clock.kill_rate + '%', 'Games that kill', 'one opponent by turn ' + clock.max_turn, '', ''),
        clock.combos ? tile(clock.combo_kill_rate + '%', 'End by combo', clock.combos + ' assembled game-ending combo' + (clock.combos === 1 ? '' : 's'), '', '') : '',
      ]) + bars.join('')
      + (clock.kill_rate < 60 ? '<div class="hint"><b>Most games never kill.</b> The clock reads combat, tokens, equipment and auras, team boosts, extra combats, damage that scales with the board, direct life loss and assembled combos; a deck that wins another way will read slow.</div>' : '')
      + '<div class="hint">A ruler for comparing builds, not a prediction.</div>';
  }
  function resilienceHtml(res) {
    const baseS = res.baseline, turnText = (s) => s.median_turn ? 'turn ' + s.median_turn : 'rarely';
    const delta = (s) => (s.median_turn && baseS.median_turn) ? (s.median_turn - baseS.median_turn) : null;
    const rrow = (label, s, tip) => { const d = delta(s), cls = d === null ? 'under' : (d <= 1 ? 'near' : (d <= 2 ? 'over' : 'under')); return row(label, cls, Math.max(4, Math.min(100, s.by_10)), 'kills on ' + turnText(s) + (d ? ' (+' + d + ')' : '') + ' &middot; ' + s.by_10 + '% by turn 10', tip, 210, 220); };
    const delayTile = (label, s, tip) => { const d = delta(s); return tile(d === null ? 'Rarely' : (d <= 0 ? 'No delay' : '+' + d + (d === 1 ? ' turn' : ' turns')), label, s.by_10 + '% still kill by turn 10', d === null ? 'bad' : (d <= 1 ? 'good' : (d <= 2 ? 'ok' : 'bad')), tip); };
    const wipeDelay = delta(res.wipe);
    const advice = (res.protection + res.recursion === 0)
      ? 'Nothing in the deck protects the board or brings it back, so a wipe on turn ' + res.wipe_turn + ' costs ' + (wipeDelay === null ? 'the game' : wipeDelay + ' turn' + (wipeDelay === 1 ? '' : 's')) + '.'
      : res.protection + ' protection spell' + (res.protection === 1 ? '' : 's') + ' and ' + res.recursion + ' reanimation spell' + (res.recursion === 1 ? '' : 's') + ' soften a wipe: it costs ' + (wipeDelay === null ? 'the game' : wipeDelay + ' turn' + (wipeDelay === 1 ? '' : 's')) + '.';
    return '<div class="hint">The same Turn Clock when opponents interact (' + res.games.toLocaleString() + ' games each): how many turns later the kill comes than with no interaction (turn ' + (baseS.median_turn || '?') + ').</div>'
      + tiles([delayTile('Spot removal', res.spot, 'Each turn from turn 3 there is a ' + Math.round(res.spot_chance * 100) + '% chance an opponent removes your best creature.'), delayTile('Board wipe T' + res.wipe_turn, res.wipe, 'Every creature and token dies at the start of turn ' + res.wipe_turn + ' unless protected.'), delayTile('Wipe + spot', res.both, 'Both in the same game.'), delayTile('Commander removed', res.commander, 'Removed each time it is cast (twice), and recasting costs {2} more.')])
      + rrow('No interaction', baseS, 'The baseline Turn Clock.') + rrow('Spot removal (' + Math.round(res.spot_chance * 100) + '% each turn)', res.spot, 'From turn 3, each turn an opponent may remove your best creature.')
      + rrow('Board wipe on turn ' + res.wipe_turn, res.wipe, 'Every creature and token dies on that turn unless a protection spell is in hand; reanimation brings creatures back.') + rrow('Wipe and spot removal', res.both, 'Both in the same game, the closest to a real table.')
      + rrow('Commander removed when cast (twice)', res.commander, 'Removed each time it is cast, and recasting costs {2} more.')
      + '<div class="hint">' + advice + ' Protection ' + res.protection + ' &middot; reanimation ' + res.recursion + ' &middot; card draw ' + res.draw + ' &middot; threats ' + res.threats + '.</div>';
  }

  // hover a named card in the Matchups panel to see it
  const hover = document.createElement('img'); hover.id = 'fa-hover'; hover.alt = ''; document.body.appendChild(hover);
  document.addEventListener('mousemove', (e) => {
    const c = e.target.closest ? e.target.closest('.deck-analysis-body .mu-card[data-full]') : null;
    if (!c) { hover.style.display = 'none'; return; }
    if (hover.dataset.src !== c.dataset.full) { hover.dataset.src = c.dataset.full; hover.src = c.dataset.full; }
    hover.style.display = 'block';
    const pad = 18, w = 240, h = Math.round(w * 1.4);
    let x = e.clientX + pad, y = e.clientY + pad;
    if (x + w > window.innerWidth) x = e.clientX - w - pad;
    if (y + h > window.innerHeight) y = window.innerHeight - h - pad;
    hover.style.left = x + 'px'; hover.style.top = Math.max(0, y) + 'px';
  });

  const statusBox = $('fa-status'), statusText = $('fa-status-text'), note = $('fa-note');
  if (!/^https?:$/.test(location.protocol)) {
    // a report saved as a plain file has no app to ask
    note.hidden = false; note.textContent = 'The deeper checks (Rule 0 summary, mana simulation, Turn Clock, Resilience, Matchups) run when this report is opened from the Brewlist app.';
    return;
  }
  const stages = { stats: 'deck stats and combos', mana: 'mana simulation', clock: 'Turn Clock', resilience: 'resilience', matchups: 'matchups' };
  const pending = new Set(Object.keys(stages));
  const progress = () => {
    const left = pending.size, total = Object.keys(stages).length;
    statusBox.classList.toggle('on', left > 0);
    statusText.textContent = left ? 'Analyzing\u2026 ' + (total - left) + ' of ' + total + ' done \u00b7 working on ' + [...pending].map((k) => stages[k]).join(', ') : '';
  };
  const done = (k) => { pending.delete(k); progress(); };
  const fail = (msg) => { pending.clear(); progress(); note.hidden = false; note.textContent = msg; };
  // The section starts collapsed, so the checks only run (once) the first time it is opened.
  let started = false;
  const start = () => {
    if (started) return;
    started = true;
    progress();
    post('/builder/stats', base).then((d) => {
      done('stats');
      if (d.error) { fail(d.error); return; }
      fill('fa-rule0', badgesHtml(d) + rule0Html(d));
      fill('fa-shape', shapeHtml(d));
      const syn = synergyHtml(d); if (syn) fill('fa-synergy', syn);
      const combos = d.combos_included_list || [];
      post('/builder/mana-sim', { cards: deck.cards, commander: deck.commander }).then((sim) => {
        if (sim && !sim.error) {
          fill('fa-mana', manaSimHtml(sim));
          post('/builder/land-curve', { cards: deck.cards, commander: deck.commander }).then((c) => { if (c && !c.error) $('fa-mana').querySelector('.fa-content').insertAdjacentHTML('beforeend', landCurveHtml(c)); }).catch(() => {});
        }
        done('mana');
      }).catch(() => done('mana'));
      post('/builder/turn-clock', { cards: deck.cards, commander: deck.commander, combos }).then((clock) => {
        if (clock && !clock.error) {
          fill('fa-clock', turnClockHtml(clock));
          post('/builder/resilience', { cards: deck.cards, commander: deck.commander, combos }).then((res) => { if (res && !res.error) fill('fa-resilience', resilienceHtml(res)); done('resilience'); }).catch(() => done('resilience'));
        } else done('resilience');
        done('clock');
      }).catch(() => { done('clock'); done('resilience'); });
      post('/builder/matchups', { cards: deck.cards, commander: deck.commander }).then((res) => { if (res && !res.error) fill('fa-matchups', muRender(res, { intro: 'Cards an opponent could use against this deck.' })); done('matchups'); }).catch(() => done('matchups'));
    }).catch(() => fail('Could not reach the Brewlist app, so the deeper checks were skipped.'));
  };
  const details = document.querySelector('.deck-analysis-body').closest('details');
  if (!details || details.open) start(); else details.addEventListener('toggle', () => { if (details.open) start(); });
})();
"""
