"""The Matchups panel's look and its HTML builder, shared by Analyze (app.py) and the Commander research page.

Plain strings so each page can drop them in without escaping braces: MATCHUPS_CSS goes in a <style>, MATCHUPS_JS in a
<script> next to the page's own escapeHtml(text) and muImg(scryfallId).

Layout: two clearly separate blocks, WEAKNESSES (red) and STRENGTHS (green). Each weakness is a collapsible card (the big
ones open, the rest closed) so the page is a short list of headlines first and detail on demand. Deck types with no clear
edge are folded into one quiet line.
"""

MATCHUPS_CSS = r"""
.mu-note { margin:0 0 12px; font-size:0.85rem; }
.mu-block { border:1px solid var(--card-border); border-radius:14px; margin:0 0 16px; overflow:hidden; background:var(--bg-elevated); }
.mu-block-head { display:flex; flex-wrap:wrap; align-items:baseline; gap:2px 12px; padding:11px 16px; border-bottom:1px solid var(--card-border); }
.mu-block.weak .mu-block-head { background:color-mix(in srgb, var(--missing) 15%, var(--bg-elevated)); }
.mu-block.strong .mu-block-head { background:color-mix(in srgb, var(--owned) 15%, var(--bg-elevated)); }
.mu-block-title { font-family:var(--serif); font-size:1.25rem; font-weight:600; margin:0; }
.mu-block.weak .mu-block-title { color:var(--missing); }
.mu-block.strong .mu-block-title { color:var(--owned); }
.mu-block-sub { font-size:0.8rem; color:var(--text-dim); }
.mu-block-body { padding:12px 16px 6px; }
.mu-key { font-size:0.74rem; color:var(--text-dim); margin:0 0 10px; }
.mu-sub { margin:14px 0 6px; font-size:0.72rem; text-transform:uppercase; letter-spacing:0.08em; color:var(--text-dim); }
.mu-empty { color:var(--text-dim); font-size:0.85rem; margin:0 0 10px; }

details.mu-weak { border:1px solid var(--card-border); border-left:3px solid var(--gold); border-radius:10px; margin:0 0 8px; background:var(--bg); }
details.mu-weak.heavy { border-left-color:var(--missing); }
details.mu-weak.light { border-left-color:var(--card-border); }
details.mu-weak.light .mu-level { color:var(--text-dim); }
details.mu-minor { margin:2px 0 10px; }
details.mu-minor > summary { font-size:0.82rem; }
details.mu-minor > details.mu-weak { margin-top:6px; }
details.mu-weak > summary { list-style:none; cursor:pointer; display:flex; flex-wrap:wrap; align-items:baseline; gap:3px 10px; padding:9px 12px 9px 28px; position:relative; }
details.mu-weak > summary::-webkit-details-marker { display:none; }
details.mu-weak > summary::before { content:"\25B8"; position:absolute; left:11px; top:9px; color:var(--text-dim); transition:transform .12s; }
details.mu-weak[open] > summary::before { transform:rotate(90deg); }
.mu-level { font-size:0.64rem; text-transform:uppercase; letter-spacing:0.07em; padding:1px 7px; border-radius:999px; border:1px solid currentColor; color:var(--gold); white-space:nowrap; }
details.mu-weak.heavy .mu-level { color:var(--missing); }
.mu-title { font-weight:700; }
.mu-because { font-size:0.78rem; color:var(--text-dim); }
.mu-body { padding:0 12px 6px 28px; }
.mu-group { margin:0 0 9px; }
.mu-group-title { font-size:0.82rem; font-weight:600; }
.mu-why { font-size:0.76rem; color:var(--text-dim); line-height:1.35; margin:1px 0 4px; }
.mu-cards { display:flex; flex-wrap:wrap; gap:5px; }
.mu-card { font-size:0.76rem; padding:2px 8px; border-radius:999px; border:1px solid var(--card-border); background:var(--card-bg); cursor:default; }
.mu-card[data-full]:hover { border-color:var(--accent); color:var(--accent); }
.mu-card.own { border-color:color-mix(in srgb, var(--owned) 55%, var(--card-border)); background:color-mix(in srgb, var(--owned) 12%, var(--card-bg)); }
.mu-card.buy { border-style:dashed; color:var(--text-dim); }
.mu-card.buy i { font-style:normal; color:var(--gold); margin-left:3px; }
.mu-card.cmdr { border-color:color-mix(in srgb, var(--gold) 55%, var(--card-border)); color:var(--gold); text-decoration:none; cursor:pointer; }
.mu-card.cmdr.own { border-color:color-mix(in srgb, var(--owned) 60%, var(--card-border)); color:var(--owned); background:color-mix(in srgb, var(--owned) 12%, var(--card-bg)); }
.mu-card.cmdr.buy { border-style:dashed; }
.mu-card.cmdr.buy i { font-style:normal; color:var(--text-dim); margin-left:3px; }
.mu-card.cmdr:hover { background:color-mix(in srgb, var(--gold) 14%, var(--card-bg)); }
.mu-have { font-size:0.8rem; margin:2px 0 9px; padding:6px 9px; border-radius:8px; background:color-mix(in srgb, var(--owned) 10%, transparent); }
.mu-have.none { background:color-mix(in srgb, var(--missing) 12%, transparent); }

details.mu-more { margin:4px 0 8px; }
details.mu-more > summary { cursor:pointer; font-size:0.78rem; color:var(--text-dim); list-style:none; padding:2px 0; }
details.mu-more > summary::-webkit-details-marker { display:none; }
details.mu-more > summary::before { content:"\25B8  "; }
details.mu-more[open] > summary::before { content:"\25BE  "; }
details.mu-more > summary:hover { color:var(--accent); }
details.mu-more > .mu-why { margin-top:4px; }

.mu-type { padding:9px 0; border-top:1px solid var(--card-border); }
.mu-type:first-child { border-top:0; padding-top:2px; }
.mu-type-head { display:grid; grid-template-columns:minmax(150px, 215px) 1fr; gap:3px 14px; font-size:0.84rem; }
.mu-type-head b { font-weight:700; }
.mu-type-head span { color:var(--text-dim); }
.mu-neutral { font-size:0.8rem; color:var(--text-dim); margin:0 0 8px; padding:0 2px; }
@media (max-width: 700px) { .mu-type-head { grid-template-columns:1fr; } }
"""

MATCHUPS_JS = r"""
// Needs the page to provide escapeHtml(text) and muImg(scryfallId) (the card image URL for hover previews).
// marks: show whether you own each card (a check) or what the cheapest copy costs. Off for opposing commanders,
// where owning a copy isn't the point.
function muChips(cards, marks) {
  return (cards || []).map(c => {
    const full = c.id ? muImg(c.id) : '';
    let cls = 'mu-card', label = escapeHtml(c.name);
    if (marks) {
      if (c.owned) { cls += ' own'; label = '&#10003; ' + label; }
      else { cls += ' buy'; if (c.price != null) label += ' <i>$' + Number(c.price).toFixed(2) + '</i>'; }
    }
    return `<span class="${cls}"${full ? ` data-full="${full}"` : ''}>${label}</span>`;
  }).join('');
}
// Opposing commanders: not cards to add, but decks to watch out for. Crowned pills that open that commander in
// Commander research (in a new tab, so an unsaved deck in the builder is never lost).
function muCommanderChips(cards) {
  return (cards || []).map(c => {
    const mark = c.owned ? ' own' : ' buy';
    const label = c.owned ? '&#10003; ' + escapeHtml(c.name) : escapeHtml(c.name) + (c.price != null ? ' <i>$' + Number(c.price).toFixed(2) + '</i>' : '');
    return `<a class="mu-card cmdr${mark}" href="/research?commander=${encodeURIComponent(c.name)}" target="_blank" rel="noopener"${c.id ? ` data-full="${muImg(c.id)}"` : ''}>&#9813; ${label}</a>`;
  }).join('');
}
// One thing the deck is exposed to: a collapsible card. The big exposures start open, the rest closed.
function muExposure(w, deckWord) {
  const groups = w.groups.map(g => `<div class="mu-group"><div class="mu-group-title">${escapeHtml(g.title)}</div><div class="mu-why">${escapeHtml(g.why)}</div><div class="mu-cards">${muChips(g.examples, true)}</div></div>`).join('');
  let mit = '';
  if (w.mitigation) {
    const m = w.mitigation;
    mit = `<div class="mu-have ${m.count ? 'ok' : 'none'}"><b>${escapeHtml(m.label)} in ${deckWord}: ${m.count}</b>${m.count ? ' &mdash; ' + m.cards.map(c => escapeHtml(c.name)).join(', ') : ''}${m.note ? '<div class="mu-why">' + escapeHtml(m.note) + '</div>' : ''}</div>`;
  }
  const cmd = w.commanders ? `<details class="mu-more"><summary>Opposing commanders that already do this (${w.commanders.cards.length})</summary><div class="mu-why">If someone leads with one of these, this exposure comes built into their deck. They're decks to watch out for, not cards to add. A check means you own that commander; a price is the cheapest copy if you don\u2019t. Click one to research it.</div><div class="mu-cards">${muCommanderChips(w.commanders.cards)}</div></details>` : '';
  return `<details class="mu-weak ${w.level}"${w.level === 'heavy' ? ' open' : ''}><summary><span class="mu-level">${w.level === 'heavy' ? 'Big exposure' : w.level === 'light' ? 'Minor' : 'Some exposure'}</span><span class="mu-title">${escapeHtml(w.title)}</span><span class="mu-because">${escapeHtml(w.because)}</span></summary><div class="mu-body">${groups}${mit}${cmd}</div></details>`;
}
// A deck type this build beats, or loses to, with the popular commanders of that kind tucked behind a toggle.
function muType(s) {
  const lead = s.verdict === 'strong' ? 'Popular commanders like this that this build should handle' : 'Popular commanders like this that tend to give this build trouble';
  const cmds = (s.commanders && s.commanders.length) ? `<details class="mu-more"><summary>${lead} (${s.commanders.length})</summary><div class="mu-cards">${muCommanderChips(s.commanders)}</div></details>` : '';
  return `<div class="mu-type"><div class="mu-type-head"><b>${escapeHtml(s.archetype)}</b><span>${escapeHtml(s.why)}</span></div>${cmds}</div>`;
}
// The Matchups panel's HTML for the server's analysis. opts.deckWord: how the deck is named in "answers in ..." lines
// ("your deck", "this build").
function muRender(res, opts) {
  opts = opts || {};
  const deckWord = opts.deckWord || 'your deck';
  const exposures = res.weaknesses || [];
  const types = res.strengths || [];
  const lose = types.filter(s => s.verdict === 'weak'), win = types.filter(s => s.verdict === 'strong'), even = types.filter(s => s.verdict === 'even');
  const parts = [];
  if (res.commander_note) parts.push(`<p class="hint mu-note"><b>${escapeHtml(res.commander_note)}</b> Anything that stops that stops the commander too.</p>`);

  // ---- weaknesses ----
  let weak = '';
  if (exposures.length) {
    weak += `<p class="mu-key"><span class="mu-card own" style="cursor:default">&#10003; Name</span> you own a copy &nbsp;&middot;&nbsp; <span class="mu-card buy" style="cursor:default">Name <i>$1.00</i></span> you don’t (cheapest copy found) &nbsp;&middot;&nbsp; the cards below are ones an opponent could use against this deck, or that you could add to answer them. Click a heading to open it.</p>`;
    const main = exposures.filter(w => w.level !== 'light'), minor = exposures.filter(w => w.level === 'light');
    weak += main.map(w => muExposure(w, deckWord)).join('');
    if (minor.length) weak += `<details class="mu-more mu-minor"><summary>Smaller exposures (${minor.length}): ${minor.map(w => escapeHtml(w.title)).join(', ')}</summary><div class="mu-why">The deck leans on these a little, not a lot. Open one to see what would hurt it.</div>${minor.map(w => muExposure(w, deckWord)).join('')}</details>`;
  } else {
    weak += '<p class="mu-empty">Nothing in this deck leans hard enough on one thing for a specific hate card to stand out.</p>';
  }
  if (lose.length) weak += '<h5 class="mu-sub">Deck types that tend to beat it</h5>' + lose.map(muType).join('');
  parts.push(`<section class="mu-block weak"><div class="mu-block-head"><h3 class="mu-block-title">&#9888; Weaknesses</h3><span class="mu-block-sub">what could hurt this deck</span></div><div class="mu-block-body">${weak}</div></section>`);

  // ---- strengths ----
  const strong = win.length ? win.map(muType).join('') : '<p class="mu-empty">No clear edge against any one deck type.</p>';
  parts.push(`<section class="mu-block strong"><div class="mu-block-head"><h3 class="mu-block-title">&#10003; Strengths</h3><span class="mu-block-sub">what this deck handles well</span></div><div class="mu-block-body">${strong}</div></section>`);

  if (even.length) parts.push(`<p class="mu-neutral">No clear edge either way against: ${even.map(s => escapeHtml(s.archetype)).join(', ')}.</p>`);
  parts.push(`<p class="hint" style="margin:6px 0 0;">${escapeHtml(res.disclaimer || '')}</p>`);
  return parts.join('');
}
"""
