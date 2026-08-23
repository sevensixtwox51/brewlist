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
    WOTC_BRACKET_GAME_CHANGER_MAX,
    budget_alt_data_in_index,
    categorize,
    find_deck_combos,
    game_changers_in_index,
    normalize_name,
)

# WOTC_BRACKET_GAME_CHANGER_MAX is keyed 1/2/3 (brackets 1-2 share a cap
# of 0 per WotC's own rules; 4-5 have no cap at all -- see its definition
# in brewlist_core.py). "1-2"/"3"/"4+" here match the same three buckets
# estimate_wotc_bracket() itself reports, since 1-vs-2 and 4-vs-5 aren't
# distinguishable from a decklist alone.
_INTENDED_BRACKET_GC_CAP = {"1-2": WOTC_BRACKET_GAME_CHANGER_MAX[2], "3": WOTC_BRACKET_GAME_CHANGER_MAX[3]}

# Rough target shape used only to steer which category the fill-the-gaps
# suggester reaches for next -- not a hard rule, just a heuristic so
# suggestions don't pile up entirely in one category.
CONSTRUCTED_LAND_FRACTION = 0.40

# Named EDH archetypes/themes (Voltron, Stax, Reanimator, ...) mapped to
# the underlying Scryfall Oracle Tags label(s) that represent them, per
# card the user picked (https://commandertheory.com/post/94098579282/
# archetypes-and-themes) and cross-checked against EDHREC's own theme
# list (edhrec.com/themes) -- both use a much broader vocabulary than
# this covers (EDHREC alone lists 250+), but these are the names that are
# both genuinely well-known EDH deckbuilding identities *and* have a
# real, verified Oracle Tags match in this app's own tag universe (see
# budget_alt_data_in_index) -- every label below was checked against the
# live tag set before being added, not guessed (a few obvious names from
# that article -- Aggro, Control, Combo, Chaos, Goodstuff -- have no
# entry here on purpose: those describe what a *whole 100-card deck* is
# trying to do, not something any single card can be tagged with, so
# there's no way to steer Suggest toward them the way a per-card theme
# tag lets it steer toward, say, Reanimator). "Ramp" is deliberately
# excluded even though the tag exists -- it's already its own dedicated
# Deck mix targets role, not a flavor theme, so listing it here too would
# just be a confusing duplicate control for the same thing.
#
# This is a curated *shortcut* layer on top of the full raw Oracle Tags
# list list_theme_options() already offered (every tag with 2+ owned
# candidates) -- not a replacement for it. A card whose own representative
# tag isn't covered by any curated theme below still shows up under its
# raw tag label, same as before.
CURATED_THEMES: dict[str, list[str]] = {
    # "affinity for auras" doesn't exist in Scryfall's tag set (checked --
    # unlike equipment/artifacts/tokens, there's no dedicated "auras
    # matter" tag; "affinity for enchantments" is the nearest real one but
    # is too broad, catching general enchantment synergy that has nothing
    # to do with stacking auras onto a creature) -- Voltron is genuinely
    # equipment-only coverage here, not the gap it looks like.
    "Voltron": ["affinity for equipment"],
    "Stax": ["tax", "tax attack"],
    "Sacrifice / Aristocrats": [
        "free sacrifice outlet", "mutual sacrifice", "opponent sacrifice matters", "opponent sacrifices",
    ],
    "Reanimator": ["reanimate", "reanimate matters", "mass reanimation", "hunger reanimation"],
    "Tokens": [
        "affinity for tokens", "repeatable creature tokens", "repeatable artifact tokens",
        "repeatable enchantment tokens", "repeatable noncreature tokens",
    ],
    "Group Hug": ["group hug", "selective group hug"],
    "Group Slug": ["group slug"],
    "Artifacts": ["affinity for artifacts", "artifact matters"],
    "Enchantress": ["creature type enchantress"],
    "Mass Land Denial": ["mass land denial"],
    "Blink / Flicker": [
        "flicker", "flicker-creature", "flicker-artifact", "flicker-enchantment",
        "flicker-land", "flicker-nonenchantment",
    ],
    "Wheels": ["wheel", "wheel-symmetrical", "wheel-symmetrical-optional", "wheel-one-sided", "miniwheel"],
    "Extra Turns": ["extra turn"],
    "Landfall": ["landfall", "landfall other"],
    "Lifegain": ["lifegain", "lifegain matters", "lifegain increaser"],
    "Storm": ["storm-like", "gives storm", "storm count matters"],
    "Mill": ["mill-any", "mill-each", "mill-opponent", "mill-self"],
    "Discard": ["discard", "discard matters"],
    "Proliferate": ["repeatable-proliferate", "synergy-proliferate", "pseudo-proliferate"],
}

# The well-known community-standard EDH deck shape (Command Zone-style
# ratios: ~38 lands, ~10 ramp, ~10 card draw, ~10-12 interaction/removal,
# the remaining ~30 "Synergy" slots being the deck's actual creatures/
# win-cons/theme pieces) -- raw counts since a Commander deck is always
# 100 cards (partner/background commanders aren't supported yet, see
# deck_builder.py's module docstring scope). User-overridable per brew
# (see suggest_builder_cards's mix_targets param and the builder UI) --
# this is our default assumption, not a rule anyone has to follow.
DEFAULT_COMMANDER_MIX = {"Lands": 38, "Ramp": 10, "Draw": 10, "Interaction": 11}

# Maps each non-land role bucket to the Oracle Tag label(s) that indicate
# it -- reuses the exact same tag_by_name selection already computed for
# budget-alternative suggestions (see _compute_budget_alt_groups in
# brewlist_core.py), which already prioritizes picking one of these exact
# labels as a card's "role" tag when applicable (BUDGET_ALT_PREFERRED_TAGS
# there overlaps by design). Anything not matching one of these buckets
# falls into "Synergy" -- the deck's actual engine pieces/win-cons.
_ROLE_TAG_LABELS = {
    "Ramp": {"mana rock", "ramp"},
    "Draw": {"draw", "pure draw"},
    "Interaction": {"removal-exile", "spot removal", "sweeper", "counterspell", "counterspell-soft"},
}


def _card_role(name: str, category: str, tag_by_name: dict, tag_labels: dict) -> str:
    """Buckets a card into the standard EDH deck-shape roles (Lands/Ramp/
    Draw/Interaction, else "Synergy" for everything else -- creatures,
    other spells, win conditions, theme pieces). Not a full archetype
    classifier, just enough to keep Suggest roughly on-shape for
    DEFAULT_COMMANDER_MIX."""
    if category in ("Lands", "Basic Lands"):
        return "Lands"
    tag_id = tag_by_name.get(normalize_name(name))
    label = tag_labels.get(tag_id) if tag_id else None
    for role, labels in _ROLE_TAG_LABELS.items():
        if label in labels:
            return role
    return "Synergy"


def _is_basic_land(type_line: str) -> bool:
    return "Basic" in type_line and "Land" in type_line


def owned_collection_gameplay_view(owned: dict[str, OwnedCard], gameplay: dict[str, dict]) -> list[dict]:
    """Merges load_collection()'s owned-card/pricing data with
    gameplay_data_in_index()'s type/color/legality data into flat,
    JSON-ready dicts for the builder's collection browser: {name,
    quantity, type_line, cmc, mana_cost, color_identity, category,
    scryfall_id, set_code, collector_number, legalities}. Owned cards with
    no gameplay match (tokens, Un-cards, anything MTGJSON doesn't carry)
    are skipped -- there's nothing to build a real deck with for those
    anyway. set_code/collector_number identify the *exact* printing you
    own (from the ManaBox export itself, see OwnedPrinting) -- carried
    through so an exported decklist can request that exact printing back
    on import instead of whatever a site defaults to."""
    view = []
    for name, owned_card in owned.items():
        gp = gameplay.get(name)
        if not gp:
            continue
        printing = owned_card.printings[0] if owned_card.printings else None
        view.append({
            "name": gp.get("name") or name,
            "quantity": owned_card.total,
            "type_line": gp.get("type_line") or "",
            "mana_cost": gp.get("mana_cost") or "",
            "cmc": gp.get("cmc") or 0,
            "color_identity": gp.get("color_identity") or [],
            "category": categorize(gp.get("type_line") or ""),
            "scryfall_id": printing.scryfall_id if printing else None,
            "set_code": printing.set_code if printing else "",
            "collector_number": printing.collector_number if printing else "",
            "legalities": gp.get("legalities") or {},
        })
    view.sort(key=lambda c: c["name"])
    return view


def owned_set_options(owned: dict[str, OwnedCard], sets_data: dict[str, dict]) -> list[dict]:
    """Every set code that's some card's *representative* printing (the
    first one, same `printings[0]` pick owned_collection_gameplay_view
    makes -- not every set the card happens to be owned in). This has to
    match that convention exactly: _filter_candidates only ever checks a
    candidate's single representative set_code, so a set that only shows
    up via a card's second/third owned printing would be functionally
    meaningless to exclude/include here -- toggling it wouldn't change
    which cards Suggest/the grid actually see, and it would silently
    render as an empty "0 owned" row. Labeled via sets_data_in_index and
    sorted chronologically by release date (oldest first) -- the pool the
    builder's Set filters (autobuilder + collection grid) offer,
    defaulting to all-on. A code with no match in sets_data (a stale
    pre-this-field index, or an unrecognized code) still shows up, using
    the raw code as its own name and sorting last (empty release date)."""
    counts: dict[str, int] = {}
    for card in owned.values():
        if not card.printings:
            continue
        code = (card.printings[0].set_code or "").upper()
        if code:
            counts[code] = counts.get(code, 0) + 1
    options = []
    for code, count in counts.items():
        info = sets_data.get(code) or {}
        options.append({
            "set_code": code,
            "set_name": info.get("name") or code,
            "release_date": info.get("release_date") or "",
            "count": count,
            # baseSetSize from MTGJSON -- None (not 0) on a stale index
            # that predates this field, so the client can tell "unknown"
            # apart from "a real 0-card set" and just omit the "/ M" part.
            "total_in_set": info.get("card_count"),
        })
    options.sort(key=lambda o: (o["release_date"] or "9999-99-99", o["set_name"]))
    return options


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
            set_code=commander.get("set_code", ""), collector_number=commander.get("collector_number", ""),
        ))
    for c in brew.get("cards") or []:
        entries.append(CardEntry(
            name=c["name"], quantity=c.get("quantity", 1), type_line=c.get("type_line", ""),
            is_foil=False, section="mainboard", scryfall_id=c.get("scryfall_id"),
            color_identity=c.get("color_identity") or [],
            set_code=c.get("set_code", ""), collector_number=c.get("collector_number", ""),
        ))
    return entries


def _filter_candidates(
    wip_entries: list[CardEntry],
    owned_view: list[dict],
    deck_format: str,
    target_format: str | None,
    commander_color_identity: list[str] | None,
    intended_bracket: str | None,
    excluded_set_codes: set[str] | None = None,
) -> list[dict]:
    """Owned cards that are legal, color-correct, and not already in the
    WIP deck -- the same filtering suggest_builder_cards has always done,
    pulled out so list_theme_options/suggest_replacements can compute
    their own candidate pools without duplicating the legality/color-
    identity/Game-Changer-cap/Set-Selection rules.

    `excluded_set_codes`, if given, drops any candidate whose (single,
    representative -- see owned_collection_gameplay_view) owned printing
    is from one of those sets. Empty/None means no restriction, matching
    the Set Selection filter's "everything on by default" behavior."""
    used_names = {normalize_name(e.name) for e in wip_entries}
    legality_key = "commander" if deck_format == "commander" else (target_format or "")
    colors_allowed = set(commander_color_identity) if deck_format == "commander" and commander_color_identity is not None else None
    if colors_allowed is None and deck_format != "commander":
        used_colors: set[str] = set()
        for e in wip_entries:
            used_colors.update(e.color_identity or [])
        colors_allowed = used_colors or None  # no colors committed yet -> no color filter

    game_changers = game_changers_in_index() if deck_format == "commander" else set()
    gc_cap = _INTENDED_BRACKET_GC_CAP.get(intended_bracket or "")
    gc_at_cap = False
    if deck_format == "commander" and gc_cap is not None:
        current_gc_count = sum(e.quantity for e in wip_entries if normalize_name(e.name) in game_changers)
        gc_at_cap = current_gc_count >= gc_cap

    candidates = []
    for c in owned_view:
        if normalize_name(c["name"]) in used_names:
            continue
        if excluded_set_codes and (c.get("set_code") or "").upper() in excluded_set_codes:
            continue
        if gc_at_cap and normalize_name(c["name"]) in game_changers:
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
    return candidates


def list_theme_options(
    wip_entries: list[CardEntry],
    owned_view: list[dict],
    deck_format: str,
    target_format: str | None,
    commander_color_identity: list[str] | None,
    excluded_set_codes: set[str] | None = None,
) -> list[dict]:
    """CURATED_THEMES (named EDH archetypes like Voltron or Reanimator,
    each backed by one or more underlying Oracle Tags -- see
    budget_alt_data_in_index, the same community-curated tags used for
    budget-alternative suggestions and suggest_builder_cards's own
    theme-sharing callouts) with at least 2 owned/legal/color-correct/
    not-yet-in-deck candidates across its underlying tag(s), so every
    option is guaranteed to actually add something if chosen. Each entry:
    {"tag_ids": [...] (a theme's full underlying tag list, so the client
    and suggest_builder_cards's preferred_theme_tag_ids can just treat it
    as an opaque set to match against), "label", "count"}. Sorted by how
    many owned cards carry it, most first."""
    candidates = _filter_candidates(wip_entries, owned_view, deck_format, target_format, commander_color_identity, None, excluded_set_codes)
    budget_alt = budget_alt_data_in_index()
    tag_by_name = budget_alt.get("tag_by_name") or {}
    tag_labels = budget_alt.get("tag_labels") or {}
    label_to_tag_id = {label: tag_id for tag_id, label in tag_labels.items()}

    counts: dict[str, int] = {}
    for c in candidates:
        tag_id = tag_by_name.get(normalize_name(c["name"]))
        if tag_id:
            counts[tag_id] = counts.get(tag_id, 0) + 1

    options = []
    for theme_name, labels in CURATED_THEMES.items():
        tag_ids = [label_to_tag_id[label] for label in labels if label in label_to_tag_id]
        total = sum(counts.get(tid, 0) for tid in tag_ids)
        if total >= 2:
            options.append({"tag_ids": tag_ids, "label": theme_name, "count": total})
    options.sort(key=lambda o: (-o["count"], o["label"]))
    return options


def suggest_replacements(
    target_name: str,
    target_category: str,
    wip_entries: list[CardEntry],
    owned_view: list[dict],
    deck_format: str,
    target_format: str | None,
    commander_color_identity: list[str] | None,
    limit: int = 6,
    excluded_set_codes: set[str] | None = None,
) -> list[dict]:
    """Owned, legal, color-correct cards that could swap in for
    target_name -- restricted to the same role it fills (see _card_role:
    Lands/Ramp/Draw/Interaction/Synergy for Commander, just Lands/other
    for constructed) so a land only gets replaced with lands, a removal
    spell with removal, etc. Ranked by whether the candidate shares
    target_name's own Oracle Tag first (the same "what is this card's
    actual job" signal suggest_builder_cards's theme callouts use), then
    Game Changers as a power tiebreak. Deliberately skips the live
    Commander Spellbook combo check suggest_builder_cards makes -- this
    runs from a quick per-card popup, not a full re-suggest, so it stays
    local/instant."""
    candidates = _filter_candidates(wip_entries, owned_view, deck_format, target_format, commander_color_identity, None, excluded_set_codes)
    if not candidates:
        return []
    budget_alt = budget_alt_data_in_index()
    tag_by_name = budget_alt.get("tag_by_name") or {}
    tag_labels = budget_alt.get("tag_labels") or {}
    game_changers = game_changers_in_index() if deck_format == "commander" else set()

    def role_of(name: str, category: str) -> str:
        if deck_format != "commander":
            return "Lands" if category in ("Lands", "Basic Lands") else "Synergy"
        return _card_role(name, category, tag_by_name, tag_labels)

    target_role = role_of(target_name, target_category)
    target_tag = tag_by_name.get(normalize_name(target_name))
    same_role = [c for c in candidates if role_of(c["name"], c["category"]) == target_role]

    def rank_key(c: dict):
        nm = normalize_name(c["name"])
        shares_tag = target_tag is not None and tag_by_name.get(nm) == target_tag
        return (not shares_tag, nm not in game_changers, c["name"])

    same_role.sort(key=rank_key)
    tag_label = tag_labels.get(target_tag) if target_tag else None
    results = []
    for c in same_role[:limit]:
        nm = normalize_name(c["name"])
        shares_tag = target_tag is not None and tag_by_name.get(nm) == target_tag
        reason = (
            f'shares the "{tag_label}" role with {target_name}' if shares_tag and tag_label
            else f'fills the same {target_role} role as {target_name}'
        )
        results.append({
            "name": c["name"], "scryfall_id": c["scryfall_id"], "category": c["category"],
            "type_line": c["type_line"], "color_identity": c["color_identity"],
            "cmc": c["cmc"], "mana_cost": c["mana_cost"],
            "set_code": c["set_code"], "collector_number": c["collector_number"],
            "reason": reason,
        })
    return results


def suggest_builder_cards(
    wip_entries: list[CardEntry],
    owned_view: list[dict],
    deck_format: str,
    target_format: str | None,
    target_size: int,
    commander_color_identity: list[str] | None,
    max_suggestions: int = 15,
    mix_targets: dict[str, int] | None = None,
    intended_bracket: str | None = None,
    preferred_theme_tag_ids: list[str] | None = None,
    preferred_theme_label: str | None = None,
    excluded_set_codes: set[str] | None = None,
) -> list[dict]:
    """Fill-the-gaps auto-suggest: proposes owned, legal, color-correct
    cards to fill the remaining slots in a work-in-progress deck. This is
    a heuristic ranking (combo pieces first, then a shared-theme signal,
    then whichever category is most under a rough target shape, then Game
    Changers/price as a power tiebreak) -- not an AI guess, same "no
    AI-generated guesses" approach the existing budget-alternative
    suggestions use (in fact the exact same Scryfall Oracle Tags data,
    see budget_alt_data_in_index).

    `preferred_theme_tag_ids`/`preferred_theme_label` (the "tag_ids"/
    "label" of one option from list_theme_options -- a curated theme's
    tag_ids can be several underlying Oracle Tags, a raw tag's is just
    itself in a 1-item list), if given, is treated as this deck's theme
    from the very first Suggest click --
    same priority-ordering boost the commander's own tag and any organic
    2+-card overlap already get -- rather than only ever detecting a
    theme after it emerges on its own.

    `intended_bracket` ("1-2"/"3"/"4+"/None), if given, is purely a self-
    declared target -- if the WIP deck's Game Changers count is already
    at or above what WotC's own published bracket rules allow for that
    bracket (see WOTC_BRACKET_GAME_CHANGER_MAX), Game Changer candidates
    are excluded outright rather than suggested and then flagged later.
    None (no preference) suggests freely, same as before this existed."""
    # target_size is the *whole* deck (100 for Commander, matching WotC's
    # own rules -- 99 library + 1 commander); the commander itself never
    # counts toward the library, so the actual library target is one
    # less. Comparing the library count straight against target_size
    # instead would let Suggest keep filling until the library alone hit
    # 100 (101 cards total) and skew every role's numeric target by the
    # same one card.
    library_target = target_size - 1 if deck_format == "commander" else target_size
    used_names = {normalize_name(e.name) for e in wip_entries}
    remaining = max(0, library_target - sum(e.quantity for e in wip_entries if e.section != "commander"))
    if remaining <= 0:
        return []
    # Cap the batch itself to what's actually left, not just gate on
    # remaining>0 -- a deck 3 cards from done shouldn't get handed back a
    # full 15-card batch (every caller so far just appends everything
    # returned, e.g. "Add All" or a repeated build-to-completion loop, so
    # without this a deck can overshoot its target by a full batch).
    max_suggestions = min(max_suggestions, remaining)

    candidates = _filter_candidates(wip_entries, owned_view, deck_format, target_format, commander_color_identity, intended_bracket, excluded_set_codes)
    if not candidates:
        return []
    game_changers = game_changers_in_index() if deck_format == "commander" else set()
    # _filter_candidates already excludes every Game Changer once the WIP
    # deck is AT its cap *before* this call -- but that's a one-time gate,
    # not a running count, so it doesn't stop a single large batch (Suggest
    # now returns up to the whole remaining deck in one click) from adding
    # several Game Changers in a row before anything rechecks: rank_key
    # below actively sorts them toward the front of their role's pool as a
    # power tiebreak, so a deck that starts under the cap can end several
    # over it by the time one batch finishes. running_gc_count, updated as
    # candidates are actually chosen in the round-robin near the bottom of
    # this function, closes that gap.
    gc_cap = _INTENDED_BRACKET_GC_CAP.get(intended_bracket or "")
    running_gc_count = 0
    if deck_format == "commander" and gc_cap is not None:
        running_gc_count = sum(e.quantity for e in wip_entries if normalize_name(e.name) in game_changers)

    reason_by_name: dict[str, str] = {}
    if deck_format == "commander" and wip_entries:
        combos = find_deck_combos(wip_entries)
        if combos:
            for combo in combos.get("almost_included") or []:
                for missing_name in combo.get("missing") or []:
                    nm = normalize_name(missing_name)
                    reason_by_name.setdefault(nm, f"completes a combo with {', '.join(combo['uses'][:2])}")

    # Theme/synergy signal -- reuses the exact same Oracle Tags data the
    # budget-alternative suggestions already use (one representative
    # "role" tag per card, e.g. "mana rock"/"ramp"/"tokens matter"; see
    # _compute_budget_alt_groups). Whichever tags are already well-
    # represented in the WIP deck (2+ cards sharing one) are treated as
    # this deck's emerging theme, and owned candidates carrying that same
    # tag get called out -- not a full archetype/EDHREC-style detector,
    # just "what is this deck already doing, and what else you own does
    # the same thing."
    theme_reason_by_name: dict[str, str] = {}
    budget_alt = budget_alt_data_in_index()
    tag_by_name = budget_alt.get("tag_by_name") or {}
    tag_labels = budget_alt.get("tag_labels") or {}
    if tag_by_name:
        wip_tag_counts: dict[str, int] = {}
        commander_tag_id = None
        for e in wip_entries:
            tag_id = tag_by_name.get(normalize_name(e.name))
            if not tag_id:
                continue
            if e.section == "commander":
                commander_tag_id = tag_id
                continue
            wip_tag_counts[tag_id] = wip_tag_counts.get(tag_id, 0) + e.quantity
        deck_theme_tags = {tag_id: n for tag_id, n in wip_tag_counts.items() if n >= 2}
        # The commander embodies the deck's theme by definition -- surface
        # its own tag as an emerging theme right away (distinct wording
        # below), rather than waiting for two *other* cards to happen to
        # share it. Without this, a brand-new deck with only a commander
        # picked can never clear the >=2 threshold above on the very first
        # Suggest click, since one card can only ever contribute 1.
        if commander_tag_id and commander_tag_id not in deck_theme_tags:
            deck_theme_tags[commander_tag_id] = 0  # sentinel: commander-only, no other cards yet
        # A curated theme (see CURATED_THEMES/list_theme_options) can back
        # onto several underlying tag_ids at once -- every one of them
        # counts as "chosen", and all get the *same* preferred_theme_label
        # in the reason text below (a card tagged "flicker-artifact"
        # should read as matching "Blink / Flicker", not its own narrower
        # raw tag label) rather than each rendering under its own raw tag.
        preferred_label_override: dict[str, str] = {}
        for tag_id in preferred_theme_tag_ids or []:
            if tag_id not in deck_theme_tags:
                deck_theme_tags[tag_id] = -1  # sentinel: explicitly chosen, not (yet) organic
            preferred_label_override[tag_id] = preferred_theme_label or tag_labels.get(tag_id, tag_id)
        if deck_theme_tags:
            for c in candidates:
                tag_id = tag_by_name.get(normalize_name(c["name"]))
                if tag_id in deck_theme_tags:
                    label = preferred_label_override.get(tag_id) or tag_labels.get(tag_id, tag_id)
                    count = deck_theme_tags[tag_id]
                    if count == -1:
                        reason = f'matches your chosen "{label}" theme'
                    elif count == 0:
                        reason = f'shares the "{label}" theme with your commander'
                    else:
                        reason = f'shares the "{label}" theme with {count} card(s) already in your deck'
                    theme_reason_by_name[normalize_name(c["name"])] = reason

    def rank_key(c: dict):
        nm = normalize_name(c["name"])
        return (nm not in reason_by_name, nm not in theme_reason_by_name, nm not in game_changers, c["name"])

    # Deck-shape roles: Commander gets the full Lands/Ramp/Draw/
    # Interaction/Synergy breakdown (see DEFAULT_COMMANDER_MIX and its
    # user-supplied override, mix_targets); constructed keeps the
    # simpler land-only target it always had, since there's no single
    # community-standard ramp/draw/removal ratio across constructed
    # formats/archetypes the way there is for EDH. "Synergy" (the deck's
    # actual creatures/win-cons/theme pieces) is always whatever's left of
    # library_target (the 99-card library, not counting the commander)
    # after the tracked roles -- the single biggest bucket in the standard
    # EDH shape (~30/99), so it's sized like every other role below, never
    # treated as a mere leftover.
    if deck_format == "commander":
        role_targets = dict(DEFAULT_COMMANDER_MIX)
        if mix_targets:
            for role in role_targets:
                if role in mix_targets and mix_targets[role] is not None:
                    role_targets[role] = max(0, int(mix_targets[role]))
    else:
        role_targets = {"Lands": round(library_target * CONSTRUCTED_LAND_FRACTION)}
    role_targets["Synergy"] = max(0, library_target - sum(role_targets.values()))

    def role_of(name: str, category: str) -> str:
        if deck_format != "commander":
            return "Lands" if category in ("Lands", "Basic Lands") else "Synergy"
        return _card_role(name, category, tag_by_name, tag_labels)

    current_role_counts: dict[str, int] = {}
    for e in wip_entries:
        r = role_of(e.name, categorize(e.type_line))
        current_role_counts[r] = current_role_counts.get(r, 0) + e.quantity
    role_needed = {role: max(0, target - current_role_counts.get(role, 0)) for role, target in role_targets.items()}

    role_candidates: dict[str, list[dict]] = {}
    for c in candidates:
        role_candidates.setdefault(role_of(c["name"], c["category"]), []).append(c)
    for pool in role_candidates.values():
        pool.sort(key=rank_key)

    # Slot allocation: split the batch across roles proportional to how
    # much each still needs, so a Suggest click always reflects the
    # target deck shape instead of whichever single category happens to
    # be most short by raw count (the bug that made an early-build
    # Suggest click return nothing but lands -- 0/38 lands always beats
    # 0/10 ramp on raw need, so a plain "most needed first" sort starved
    # every other role until lands alone hit target). Uses a largest-
    # remainder apportionment (floor the proportional share per role,
    # then hand out the few leftover slots to whichever roles had the
    # biggest fractional remainder) rather than rounding each role
    # independently -- independent rounding can overshoot max_suggestions
    # and silently starve whichever role happens to be computed last
    # (this cost Synergy -- the actual creatures/win-cons -- its entire
    # share the first time this was tried).
    eligible = {
        role: needed for role, needed in role_needed.items()
        if needed > 0 and role_candidates.get(role)
    }
    if not eligible:
        # Every tracked role either hit its target already or has no
        # owned/legal/unused candidates left of that specific role (e.g.
        # this deck's colors just don't have many Oracle-tagged "draw" or
        # "interaction" cards in the collection) -- targets are a shape to
        # aim for, not a hard cap once the narrower roles are tapped out.
        # Without this fallback, Suggest stalls well short of a full deck
        # even with hundreds of untouched, perfectly legal Synergy cards
        # still sitting in the collection, because nothing is technically
        # "under target" anymore. Fall back to whichever roles still have
        # any candidates at all, weighted by how many are available.
        fallback_pools = {role: len(pool) for role, pool in role_candidates.items() if pool}
        total_pool = sum(fallback_pools.values())
        if total_pool:
            eligible = {role: max(1, round(max_suggestions * size / total_pool)) for role, size in fallback_pools.items()}
    role_slots: dict[str, int] = {}
    if eligible:
        total_needed = sum(eligible.values())
        raw_shares = {role: max_suggestions * needed / total_needed for role, needed in eligible.items()}
        for role, share in raw_shares.items():
            role_slots[role] = min(int(share), len(role_candidates[role]), eligible[role])
        leftover = max_suggestions - sum(role_slots.values())
        for role in sorted(eligible, key=lambda r: raw_shares[r] - int(raw_shares[r]), reverse=True):
            if leftover <= 0:
                break
            cap = min(len(role_candidates[role]), eligible[role])
            if role_slots[role] < cap:
                role_slots[role] += 1
                leftover -= 1
        if leftover > 0:
            # A role's own target genuinely can't be met (e.g. this
            # color pair has zero owned "Draw" candidates) -- the loop
            # above only ever tops a role up to its OWN eligible[role]
            # target, so that role's unfillable slots would otherwise
            # just be lost even with hundreds of untouched Synergy
            # candidates sitting right there. Reallocate the remainder to
            # any role with spare *pool* capacity beyond its own target,
            # biggest pool first, repeating until nothing more fits.
            changed = True
            while leftover > 0 and changed:
                changed = False
                for role in sorted(role_candidates, key=lambda r: len(role_candidates[r]), reverse=True):
                    if leftover <= 0:
                        break
                    if not role_candidates[role]:
                        continue
                    if role_slots.get(role, 0) < len(role_candidates[role]):
                        role_slots[role] = role_slots.get(role, 0) + 1
                        leftover -= 1
                        changed = True

    # Round-robin across roles rather than role-by-role so even a short
    # list reads as a mix, not a wall of one role followed by another.
    # indices[role] walks the role's whole sorted pool (not just its
    # role_slots[role] budget) so a Game-Changer candidate blocked by the
    # running cap check below can be skipped in favor of the next
    # candidate in that same pool, instead of just shrinking the role's
    # contribution -- added_counts[role] (bounded by role_slots[role]) is
    # what actually limits how many get taken from each role.
    ordered: list[dict] = []
    indices = {role: 0 for role in role_slots}
    added_counts = {role: 0 for role in role_slots}
    active_roles = [r for r in role_slots if role_slots[r] > 0]
    while len(ordered) < max_suggestions and active_roles:
        for role in list(active_roles):
            if len(ordered) >= max_suggestions:
                break
            pool = role_candidates.get(role) or []
            picked = None
            while added_counts[role] < role_slots[role] and indices[role] < len(pool):
                candidate = pool[indices[role]]
                indices[role] += 1
                is_gc = normalize_name(candidate["name"]) in game_changers
                if gc_cap is not None and is_gc and running_gc_count >= gc_cap:
                    continue  # over the intended bracket's cap -- try this role's next candidate instead
                picked = candidate
                break
            if picked is None:
                active_roles.remove(role)
                continue
            ordered.append(picked)
            added_counts[role] += 1
            if normalize_name(picked["name"]) in game_changers:
                running_gc_count += 1

    suggestions = []
    for c in ordered:
        nm = normalize_name(c["name"])
        role = role_of(c["name"], c["category"])
        fallback_reason = f"fills out {role}" if deck_format == "commander" else f"fills out {c['category']}"
        suggestions.append({
            "name": c["name"],
            "scryfall_id": c["scryfall_id"],
            "category": c["category"],
            "type_line": c["type_line"],
            "color_identity": c["color_identity"],
            "cmc": c["cmc"],
            "mana_cost": c["mana_cost"],
            "set_code": c["set_code"],
            "collector_number": c["collector_number"],
            "reason": reason_by_name.get(nm) or theme_reason_by_name.get(nm) or fallback_reason,
        })
    return suggestions
