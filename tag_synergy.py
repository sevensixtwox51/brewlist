"""Search and synergy from Scryfall's Oracle Tags (the data behind otag: searches), read from the local card
database (brewlist_core.budget_alt_data_in_index) so it works offline.

Two things are built on it:
  - tag_listing: every tag that applies to a card you own, with counts, plus each owned card's tags -- the
    builder's tag search and the tag chips on tiles.
  - related_cards: for one card, owned cards that "work with it" (a small curated list of tag pairs: sacrifice
    outlets with death payoffs, landfall with land ramp...) and owned cards that are simply similar (sharing
    rare tags, weighted so a broad tag like "attacking matters" counts for little).

The tags are crowd-made and uneven (Skullclamp carries "flowstone"), so a few kinds of tag are hidden, and the
curated pairs -- not shared tags alone -- decide what counts as complementary: a sacrifice outlet and a death
payoff share no tag at all.
"""

import math
import re

from brewlist_core import budget_alt_data_in_index, normalize_name

# Tags that say nothing useful about how a card plays: Tagger housekeeping, set-specific mechanics, art notes,
# "hate" tags (what a card is bad against), and one-off jokes.
_JUNK = re.compile(
    r"achievement|flowstone|inscryption|set.s mechanic|set mechanic|conjure|^sneak-|vanilla|auraify|more expensive"
    r"|^hate-|single-minded|^donate|swap removal|aesthetic|out of color|mana ability with extra|gives trample|gains trample"
    r"|^combat ramp|rampage|^scales with"
)

# Complementary tag pairs: (name of side A, patterns for A, name of side B, patterns for B). A card on one side
# "works with" owned cards on the other. Patterns match the tag labels above.
PAIRS = [
    ("Sacrifice outlets", [r"sacrifice outlet"], "Death payoffs and fodder", [r"^death trigger$", r"blood artist ability", r"^multiple bodies$"]),
    ("Landfall", [r"^landfall$"], "Land ramp", [r"land ramp", r"tutor-land-to-battlefield"]),
    ("Counters matter", [r"counters matter"], "Counter makers", [r"gives pp counters", r"proliferate"]),
    ("Reanimation", [r"^reanimate-", r"castable from graveyard", r"cards in graveyard matter", r"activate from graveyard"],
     "Ways to fill the graveyard", [r"^mill-self", r"discard outlet", r"synergy-mill", r"tutor-to-graveyard"]),
    ("Lifegain", [r"^lifegain$"], "Lifegain payoffs", [r"lifegain matters", r"blood artist ability"]),
    ("Treasure and artifact makers", [r"repeatable treasures", r"repeatable artifact tokens"], "Artifact payoffs", [r"synergy-artifact", r"sacrifice outlet-artifact"]),
    ("Ramp", [r"^ramp$", r"mana dork", r"mana rock", r"land ramp"], "Mana sinks", [r"mana sink"]),
]

_cache: dict = {"key": None}


def _index() -> dict:
    """{"tags": {tag_id: label}, "cards": {norm_name: frozenset(tag_id)}, "size": {tag_id: members}, "n": cards}."""
    data = budget_alt_data_in_index()
    groups = data.get("groups") or {}
    key = (id(groups), len(groups))
    if _cache["key"] == key:
        return _cache
    labels = data.get("tag_labels") or {}
    tags = {tid: labels.get(tid, tid) for tid in groups if not _JUNK.search((labels.get(tid) or "").lower())}
    cards: dict[str, set] = {}
    for tid in tags:
        for entry in groups[tid]:
            if not entry:
                continue
            name = entry[0]
            for alias in {name, name.split(" // ")[0]}:
                cards.setdefault(alias, set()).add(tid)
    size = {tid: len(groups[tid]) for tid in tags}
    _cache.update(key=key, tags=tags, cards={k: frozenset(v) for k, v in cards.items()}, size=size, n=max(1, len(cards)))
    return _cache


def card_tag_ids(name: str) -> frozenset:
    idx = _index()
    norm = normalize_name(name)
    return idx["cards"].get(norm) or idx["cards"].get(norm.split(" // ")[0]) or frozenset()


def _weight(tid: str, idx: dict) -> float:
    """How telling a shared tag is: rare tags count for much more than broad ones."""
    size = idx["size"].get(tid, 2)
    w = min(6.0, math.log(idx["n"] / max(2, size)))
    return w * 0.5 if size < 20 else w  # a tag on a handful of cards is usually one set's mechanic, not a way to play


def tag_listing(owned_names: list[str]) -> dict:
    """{"tags": [{"i", "label", "owned", "total"}], "cards": {norm_name: [i, ...]}} for the cards you own -- only
    tags that apply to at least one of them. `i` indexes the list so the per-card map stays small."""
    idx = _index()
    per_card = {normalize_name(n): sorted(card_tag_ids(n)) for n in owned_names}
    counts: dict[str, int] = {}
    for tids in per_card.values():
        for tid in tids:
            counts[tid] = counts.get(tid, 0) + 1
    order = sorted(counts, key=lambda t: (-counts[t], idx["tags"][t]))
    position = {tid: i for i, tid in enumerate(order)}
    return {
        "tags": [{"i": position[t], "label": idx["tags"][t], "owned": counts[t], "total": idx["size"][t]} for t in order],
        "cards": {n: [position[t] for t in tids] for n, tids in per_card.items() if tids},
    }


def _side_tags(patterns: list[str], idx: dict) -> set:
    regs = [re.compile(p) for p in patterns]
    return {tid for tid, label in idx["tags"].items() if any(r.search(label.lower()) for r in regs)}


def related_cards(name: str, owned_rows: list[dict], identity: set | None, deck_names: set, limit: int = 18) -> dict:
    """What works with `name` among the cards you own. `owned_rows` are owned_collection_gameplay_view rows;
    `identity` (a set of colour letters) limits results to a commander's colours; `deck_names` are normalized
    names already in the deck (kept in the results but flagged)."""
    idx = _index()
    mine = card_tag_ids(name)
    me = normalize_name(name)
    pool = []
    for row in owned_rows:
        nm = normalize_name(row["name"])
        if nm == me:
            continue
        if identity is not None and not set(row.get("color_identity") or []) <= identity:
            continue
        if (row.get("legalities") or {}).get("commander") not in (None, "Legal"):
            continue
        pool.append((row, card_tag_ids(row["name"])))

    def entry(row, reason):
        return {k: row.get(k) for k in ("name", "scryfall_id", "type_line", "mana_cost", "cmc", "color_identity", "category", "set_code", "collector_number", "layout", "quantity")} | {
            "reason": reason, "in_deck": normalize_name(row["name"]) in deck_names}

    groups = []
    used: set[str] = set()
    for a_name, a_pats, b_name, b_pats in PAIRS:
        a, b = _side_tags(a_pats, idx), _side_tags(b_pats, idx)
        on_a, on_b = bool(mine & a), bool(mine & b)
        if not (on_a or on_b):
            continue
        want, want_name, have_name = (b, b_name, a_name) if on_a else (a, a_name, b_name)
        scored = []
        for row, tids in pool:
            hit = tids & want
            if hit:
                scored.append((sum(_weight(t, idx) for t in hit) + 0.5 * len(hit), row, hit))
        scored.sort(key=lambda x: -x[0])
        if scored:
            cards = [entry(row, ", ".join(sorted(idx["tags"][t] for t in hit)[:3])) for _, row, hit in scored[:limit]]
            used.update(normalize_name(c["name"]) for c in cards)
            groups.append({"title": want_name, "why": f"{name} is one of your {have_name.lower()}; these are {want_name.lower()}.", "total": len(scored), "cards": cards})

    similar = []
    for row, tids in pool:
        shared = tids & mine
        if not shared:
            continue
        score = sum(_weight(t, idx) for t in shared)
        if score >= 4.5:
            similar.append((score, row, shared))
    similar.sort(key=lambda x: -x[0])
    similar_cards = [entry(row, ", ".join(sorted(idx["tags"][t] for t in shared)[:3])) for _, row, shared in similar if normalize_name(row["name"]) not in used][:limit]
    return {
        "card": {"name": name, "tags": sorted(idx["tags"][t] for t in mine)},
        "groups": groups,
        "similar": similar_cards,
        "similar_total": len(similar),
    }
