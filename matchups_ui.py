"""The Matchups panel's look and its HTML builder, shared by Analyze (app.py) and the Commander research page.

Plain strings so each page can drop them in without escaping braces: MATCHUPS_CSS goes in a <style>, MATCHUPS_JS in a
<script> next to the page's own escapeHtml(text) and muImg(scryfallId).
"""

MATCHUPS_CSS = r"""
.mu-sub { margin:14px 0 8px; font-size:0.74rem; text-transform:uppercase; letter-spacing:0.07em; color:var(--text-dim); }
.mu-weak { border:1px solid var(--card-border); border-left:3px solid var(--gold); border-radius:10px; padding:10px 12px 8px; margin-bottom:10px; background:var(--bg-elevated); }
.mu-weak.heavy { border-left-color:var(--missing); }
.mu-head { display:flex; flex-wrap:wrap; align-items:baseline; gap:4px 10px; margin-bottom:6px; }
.mu-level { font-size:0.66rem; text-transform:uppercase; letter-spacing:0.07em; padding:1px 7px; border-radius:999px; border:1px solid currentColor; color:var(--gold); }
.mu-weak.heavy .mu-level { color:var(--missing); }
.mu-because { font-size:0.78rem; color:var(--text-dim); }
.mu-group { margin:0 0 8px; }
.mu-group-title { font-size:0.82rem; font-weight:600; }
.mu-why { font-size:0.76rem; color:var(--text-dim); line-height:1.35; margin:1px 0 4px; }
.mu-cards { display:flex; flex-wrap:wrap; gap:5px; }
.mu-card { font-size:0.76rem; padding:2px 8px; border-radius:999px; border:1px solid var(--card-border); background:var(--card-bg); cursor:default; }
.mu-card[data-full]:hover { border-color:var(--accent); color:var(--accent); }
.mu-card.own { border-color:color-mix(in srgb, var(--owned) 55%, var(--card-border)); background:color-mix(in srgb, var(--owned) 12%, var(--card-bg)); }
.mu-card.buy { border-style:dashed; color:var(--text-dim); }
.mu-card.buy i { font-style:normal; color:var(--gold); margin-left:3px; }
.mu-have { font-size:0.8rem; margin:2px 0 8px; padding:6px 9px; border-radius:8px; background:color-mix(in srgb, var(--owned) 10%, transparent); }
.mu-have.none { background:color-mix(in srgb, var(--missing) 12%, transparent); }
.mu-arch { display:flex; flex-direction:column; gap:5px; }
.mu-row { display:grid; grid-template-columns:104px minmax(130px, 190px) 1fr; gap:4px 10px; align-items:baseline; font-size:0.8rem; padding:5px 8px; border-radius:8px; border:1px solid var(--card-border); }
.mu-verdict { font-size:0.66rem; text-transform:uppercase; letter-spacing:0.06em; font-weight:700; }
.mu-row.strong .mu-verdict { color:var(--owned); } .mu-row.weak .mu-verdict { color:var(--missing); } .mu-row.even .mu-verdict { color:var(--gold); }
.mu-name { font-weight:600; } .mu-text { color:var(--text-dim); }
@media (max-width: 700px) { .mu-row { grid-template-columns:1fr; } }
"""

MATCHUPS_JS = r"""
const MU_VERDICT = { strong: 'Strong against', weak: 'Weak against', even: 'Even' };
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
// The Matchups panel's HTML for the server's analysis. opts.intro: the sentence above the pills; opts.deckWord: how
// the deck is named in "answers in ..." lines ("your deck", "this build").
function muRender(res, opts) {
  opts = opts || {};
  const deckWord = opts.deckWord || 'your deck';
  const weak = res.weaknesses || [];
  const parts = [];
  if (res.commander_note) parts.push(`<p class="hint" style="margin:0 0 10px;"><b>${escapeHtml(res.commander_note)}</b> Anything that stops that stops the commander too.</p>`);
  parts.push('<h5 class="mu-sub">Weak to</h5>');
  if (weak.length) parts.push(`<p class="hint" style="margin:0 0 8px;">${escapeHtml(opts.intro || 'Cards an opponent could use against this deck.')} <span class="mu-card own" style="cursor:default">&#10003; Name</span> means you own a copy; a dashed pill with a price is one you don’t own (cheapest copy found), useful if you want to run it yourself.</p>`);
  if (!weak.length) parts.push('<p class="hint" style="margin:0 0 10px;">Nothing in this deck leans hard enough on one thing for a specific hate card to stand out.</p>');
  weak.forEach(w => {
    const groups = w.groups.map(g => `<div class="mu-group"><div class="mu-group-title">${escapeHtml(g.title)}</div><div class="mu-why">${escapeHtml(g.why)}</div><div class="mu-cards">${muChips(g.examples, true)}</div></div>`).join('');
    let mit = '';
    if (w.mitigation) {
      const m = w.mitigation;
      mit = `<div class="mu-have ${m.count ? 'ok' : 'none'}"><b>${escapeHtml(m.label)} in ${deckWord}: ${m.count}</b>${m.count ? ' &mdash; ' + m.cards.map(c => escapeHtml(c.name)).join(', ') : ''}${m.note ? '<div class="mu-why">' + escapeHtml(m.note) + '</div>' : ''}</div>`;
    }
    const cmd = w.commanders ? `<div class="mu-group"><div class="mu-group-title">${escapeHtml(w.commanders.title)}</div><div class="mu-cards">${muChips(w.commanders.cards)}</div></div>` : '';
    parts.push(`<div class="mu-weak ${w.level}"><div class="mu-head"><span class="mu-level">${w.level === 'heavy' ? 'Big exposure' : 'Some exposure'}</span><b>${escapeHtml(w.title)}</b><span class="mu-because">${escapeHtml(w.because)}</span></div>${groups}${mit}${cmd}</div>`);
  });
  parts.push('<h5 class="mu-sub">Strong or weak against, by deck type</h5>');
  parts.push('<div class="mu-arch">' + (res.strengths || []).map(s =>
    `<div class="mu-row ${s.verdict}"><span class="mu-verdict">${MU_VERDICT[s.verdict]}</span><span class="mu-name">${escapeHtml(s.archetype)}</span><span class="mu-text">${escapeHtml(s.why)}</span></div>`
  ).join('') + '</div>');
  parts.push(`<p class="hint" style="margin:10px 0 0;">${escapeHtml(res.disclaimer || '')}</p>`);
  return parts.join('');
}
"""
