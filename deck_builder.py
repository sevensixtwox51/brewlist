"""Deck builder: build a brand-new deck (Commander singleton, or generic
60-card constructed) using only cards already in the ManaBox collection.

Complements brewlist_core.py's compare-a-decklist-against-a-collection
flow with the opposite direction -- start from nothing and pick owned
cards. Reuses brewlist_core's CardEntry/categorize/pricing/legality/
combo machinery throughout rather than duplicating any of it: a finished
brew is just converted into a list[CardEntry] and run through the exact
same build_comparison()/render_html() pipeline a compared deck uses (see
brew_to_card_entries), so it gets the same rich report for free.

No UI dependencies -- imported by app.py, same relationship brewlist_core
has to app.py/brewlist_cli.py.
"""

from __future__ import annotations

from brewlist_core import (
    CardEntry,
    OwnedCard,
    categorize,
    find_deck_combos,
    game_changers_in_index,
    normalize_name,
)

# Rough target shape used only to steer which category the fill-the-gaps
# suggester reaches for next -- not a hard rule, just a heuristic so
# suggestions don't pile up entirely in one category. Fractions of the
# non-commander deck size.
COMMANDER_LAND_FRACTION = 0.37
CONSTRUCTED_LAND_FRACTION = 0.40


def _is_basic_land(type_line: str) -> bool:
    return "Basic" in type_line and "Land" in type_line


def owned_collection_gameplay_view(owned: dict[str, OwnedCard], gameplay: dict[str, dict]) -> list[dict]:
    """Merges load_collection()'s owned-card/pricing data with
    gameplay_data_in_index()'s type/color/legality data into flat,
    JSON-ready dicts for the builder's collection browser: {name,
    quantity, type_line, cmc, mana_cost, color_identity, category,
    scryfall_id, legalities}. Owned cards with no gameplay match (tokens,
    Un-cards, anything MTGJSON doesn't carry) are skipped -- there's
    nothing to build a real deck with for those anyway."""
    view = []
    for name, owned_card in owned.items():
        gp = gameplay.get(name)
        if not gp:
            continue
        scryfall_id = owned_card.printings[0].scryfall_id if owned_card.printings else None
        view.append({
            "name": gp.get("name") or name,
            "quantity": owned_card.total,
            "type_line": gp.get("type_line") or "",
            "mana_cost": gp.get("mana_cost") or "",
            "cmc": gp.get("cmc") or 0,
            "color_identity": gp.get("color_identity") or [],
            "category": categorize(gp.get("type_line") or ""),
            "scryfall_id": scryfall_id,
            "legalities": gp.get("legalities") or {},
        })
    view.sort(key=lambda c: c["name"])
    return view


def brew_to_card_entries(brew: dict) -> list[CardEntry]:
    """Converts a saved brew ({"format", "commander", "cards": [{"name",
    "quantity", "scryfall_id", "type_line", "color_identity"}, ...]}) into
    the CardEntry list every other part of the app already knows how to
    price/categorize/check-legality-on/find-combos-in."""
    entries: list[CardEntry] = []
    commander = brew.get("commander")
    if commander:
        entries.append(CardEntry(
            name=commander["name"], quantity=1, type_line=commander.get("type_line", ""),
            is_foil=False, section="commander", scryfall_id=commander.get("scryfall_id"),
            color_identity=commander.get("color_identity") or [],
        ))
    for c in brew.get("cards") or []:
        entries.append(CardEntry(
            name=c["name"], quantity=c.get("quantity", 1), type_line=c.get("type_line", ""),
            is_foil=False, section="mainboard", scryfall_id=c.get("scryfall_id"),
            color_identity=c.get("color_identity") or [],
        ))
    return entries


def suggest_builder_cards(
    wip_entries: list[CardEntry],
    owned_view: list[dict],
    deck_format: str,
    target_format: str | None,
    target_size: int,
    commander_color_identity: list[str] | None,
    max_suggestions: int = 15,
) -> list[dict]:
    """Fill-the-gaps auto-suggest: proposes owned, legal, color-correct
    cards to fill the remaining slots in a work-in-progress deck. This is
    a heuristic ranking (combo pieces first, then whichever category is
    most under a rough target shape, then Game Changers/price as a power
    tiebreak) -- not an AI guess, same "no AI-generated guesses" approach
    the existing budget-alternative suggestions use."""
    used_names = {normalize_name(e.name) for e in wip_entries}
    remaining = max(0, target_size - sum(e.quantity for e in wip_entries if e.section != "commander"))
    if remaining <= 0:
        return []

    legality_key = "commander" if deck_format == "commander" else (target_format or "")
    colors_allowed = set(commander_color_identity) if deck_format == "commander" and commander_color_identity is not None else None
    if colors_allowed is None and deck_format != "commander":
        used_colors: set[str] = set()
        for e in wip_entries:
            used_colors.update(e.color_identity or [])
        colors_allowed = used_colors or None  # no colors committed yet -> no color filter

    candidates = []
    for c in owned_view:
        if normalize_name(c["name"]) in used_names:
            continue
        if colors_allowed is not None and not set(c["color_identity"]).issubset(colors_allowed):
            continue
        if legality_key:
            legality = (c.get("legalities") or {}).get(legality_key)
            # MTGJSON's legalities dict omits a format entirely when a card
            # was simply never printed into that format's pool (the common
            # case for e.g. Sol Ring in Standard) rather than saying "Not
            # Legal" -- so missing means "not legal" here for a *target*
            # constructed format. Commander is the exception: it's a near-
            # universal-legal format where MTGJSON does explicitly mark
            # "Legal" for essentially every real paper card, so a missing
            # entry there (some untracked oddity) shouldn't be treated as
            # banned -- same "missing = not flagged" convention already
            # used by commander_legality elsewhere in this app.
            if legality_key == "commander":
                if legality and legality != "Legal":
                    continue
            elif legality != "Legal":
                continue
        if c["quantity"] < 1:
            continue
        candidates.append(c)

    if not candidates:
        return []

    reason_by_name: dict[str, str] = {}
    if deck_format == "commander" and wip_entries:
        combos = find_deck_combos(wip_entries)
        if combos:
            for combo in combos.get("almost_included") or []:
                for missing_name in combo.get("missing") or []:
                    nm = normalize_name(missing_name)
                    reason_by_name.setdefault(nm, f"completes a combo with {', '.join(combo['uses'][:2])}")

    land_fraction = COMMANDER_LAND_FRACTION if deck_format == "commander" else CONSTRUCTED_LAND_FRACTION
    target_lands = round((target_size) * land_fraction)
    current_lands = sum(e.quantity for e in wip_entries if categorize(e.type_line) in ("Lands", "Basic Lands"))
    want_lands = current_lands < target_lands

    game_changers = game_changers_in_index() if deck_format == "commander" else set()

    def sort_key(c: dict):
        nm = normalize_name(c["name"])
        has_combo_reason = nm in reason_by_name
        is_land = c["category"] in ("Lands", "Basic Lands")
        matches_need = is_land == want_lands
        is_game_changer = nm in game_changers
        return (not has_combo_reason, not matches_need, not is_game_changer, c["name"])

    candidates.sort(key=sort_key)

    suggestions = []
    for c in candidates[:max_suggestions]:
        nm = normalize_name(c["name"])
        suggestions.append({
            "name": c["name"],
            "scryfall_id": c["scryfall_id"],
            "category": c["category"],
            "type_line": c["type_line"],
            "color_identity": c["color_identity"],
            "cmc": c["cmc"],
            "reason": reason_by_name.get(nm, f"fills out {c['category']}"),
        })
    return suggestions
