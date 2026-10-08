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

import re

from brewlist_core import (
    CardEntry,
    OwnedCard,
    WOTC_BRACKET_GAME_CHANGER_MAX,
    budget_alt_data_in_index,
    categorize,
    find_deck_combos,
    game_changers_in_index,
    normalize_name,
    prices_data_in_index,
)
from edhrec_data import commander_profile, commander_synergy_cards

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
    # The reanimate-* family is where the real reanimation spells live
    # (reanimate-creature alone has ~500 cards; plain "reanimate" is tiny).
    # reanimate-self (a creature that recurs itself) and reanimate-cast
    # (cast from the graveyard) are different jobs, left out on purpose.
    "Reanimator": [
        "reanimate", "reanimate matters", "mass reanimation", "hunger reanimation",
        "reanimate-creature", "reanimate-artifact-creature", "reanimate-from-opponent",
        "reanimate-permanent", "reanimate-nonland", "reanimate-from-any",
    ],
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

# The default Lands/Ramp/Draw/Interaction/Synergy shape used to both build
# (suggest_builder_cards) and judge (the Deck Shape breakdown in Analyze)
# a Commander deck -- see commander_deck_shape_targets, below. User-
# overridable per brew (see suggest_builder_cards's mix_targets param and
# the builder UI). These two consumers used to carry separate, silently-
# drifted numbers (Suggest's own old default targeted 11 Interaction while
# Analyze displayed a 15 target) -- confirmed via role_counts on 4 real
# built decks, every one landing at 11-13 Interaction, "light on
# interaction" by the app's own displayed standard even though Suggest was
# hitting the number it was actually told to hit. Unified on one function
# so a future change to the ratio can't silently split the two again.

# Maps each non-land role bucket to the Oracle Tag label(s) that indicate
# it. Checked against real cards, not guessed -- and deliberately checked
# via each tag's FULL membership list (_role_member_names, below), not a
# card's single tag_by_name "representative" pick: a card can genuinely
# belong to several Oracle Tags at once, but tag_by_name only ever records
# one of them (whichever the underlying selection logic happened to
# prefer), which is very often NOT the role-relevant one. Confirmed live,
# a real bug this fixes -- these are each real staples' own tag_by_name
# pick, none of which match ANY label below even though every one of them
# is a genuine member of one of these tags' full list: Sign in Blood /
# Night's Whisper / Read the Bones -> "life for cards"; Rhystic Study ->
# "cast tax"; Phyrexian Arena -> "deal with the devil"; Arcane Signet ->
# "commander identity matters"; Mind Stone -> "utility mana rock";
# Cultivate -> "tutor-to-hand"; Rampant Growth -> "tutor-land-to-
# battlefield"; Birds of Paradise -> "real life animal name". "draw" and
# "spot removal" (the two tags this set used before) turned out to not
# be real, populated tags at all -- 0 members each -- explaining why
# "Draw" was silently *always* empty for every commander, not just a
# color-sparse one; "pure draw" (also previously listed) is a real tag
# ID but likewise has 0 members in the current data. Anything not
# matching one of these buckets falls into "Synergy" -- the deck's
# actual engine pieces/win-cons.
_ROLE_TAG_LABELS = {
    "Ramp": {
        "ramp", "mana rock", "utility mana rock", "mana rock with set's mechanic",
        "mana dork", "mana dork egg", "land ramp", "multi land ramp",
        "tutor-land-to-battlefield", "ramp with set's mechanic",
    },
    "Draw": {
        "burst draw", "life for cards", "deal with the devil", "repeatable draw",
        "drawlink", "gives drawlink", "impulsive draw", "repeatable impulsive draw",
        "long term impulsive draw",
    },
    "Interaction": {
        "removal-exile", "sweeper", "sweeper-one-sided", "sweeper-graveyard",
        "counterspell", "counterspell-soft", "counterspell-reusable",
        "multi removal", "removal-artifact", "removal-bounce", "removal-enchantment",
        "removal-land", "removal-nonland", "removal-permanent", "removal-planeswalker",
        "removal-sacrifice", "removal-tuck",
        # Added after a real, live-confirmed bug: these are each real,
        # heavily-populated tags (burn creature alone has 1018 members)
        # that were previously falling through to "Synergy" entirely,
        # silently starving Interaction's quota across every commander --
        # confirmed via role_counts on 4 real saved decks, all landing at
        # 11-13 against a 15 target despite every other role hitting its
        # number. "removal-toughness"/"burn *"/"*-fight" are -X/-X and
        # damage-based removal; the rest are counterspell subtypes (only
        # 3 of ~18 real ones were previously listed).
        "removal-toughness", "removal-fight", "one-sided fight", "removal-aura",
        "removal-battle", "removal-equipment", "removal-vehicle", "removal-spacecraft",
        "swap removal", "burn creature", "burn any", "burn planeswalker",
        "counterspell-ability", "counterspell-artifact", "counterspell-automatic",
        "counterspell-bounce", "counterspell-creature", "counterspell-enchantment",
        "counterspell-exile", "counterspell-free", "counterspell-instant",
        "counterspell-noncreature", "counterspell-planeswalker", "counterspell-sacrifice",
        "counterspell-sorcery", "counterspell-sweeper", "counterspell with set mechanic",
    },
}

# Maps each real basic land type name to the WUBRG letter it produces --
# used only by _dead_fetch_land_colors below.
_BASIC_LAND_TYPE_COLORS = {"Plains": "W", "Island": "U", "Swamp": "B", "Mountain": "R", "Forest": "G"}
_FETCH_LAND_RE = re.compile(r"[Ss]earch your library for an?\s+([^.]*?)\s+card")

# Supertypes that belong to a separate special deck (Planechase/Archenemy/
# Vanguard), never a normal 100-card Commander library -- see
# _filter_candidates' own usage for the real bug this closes. Word-
# boundaried so "Plane" doesn't also match "Planeswalker".
_NON_LIBRARY_TYPE_RE = re.compile(r"\b(?:Plane|Phenomenon|Scheme|Vanguard)\b")


def _dead_fetch_land_colors(oracle_text: str, colors_allowed: set[str]) -> bool:
    """True if oracle_text is a classic fetchland ("Search your library
    for a Mountain or Plains card, ...") whose named basic land types are
    ALL outside colors_allowed -- a real, user-caught bug: a card's own
    color_identity is empty for a fetchland like this (it has no colored
    mana symbols itself), so it passes the ordinary color-identity filter
    in ANY deck regardless of what it can actually find, even one with no
    matching basics at all. Confirmed live: for Zhulodok, Void Gorger
    (colorless, color_identity []), Suggest offered Arid Mesa (Mountain
    or Plains) alongside 6 other single-pair-colored fetches, none able
    to ever find a target in a deck that would run Wastes, not a colored
    basic -- 7 of 37 suggested lands were simply dead cards.

    An UNCOLORED fetch ("search your library for a basic land card", no
    specific type named -- Evolving Wilds/Terramorphic Expanse/Fabled
    Passage) doesn't match _FETCH_LAND_RE's named-type check and is
    always left alone here, since it can find any basic regardless of
    color (Wastes included)."""
    m = _FETCH_LAND_RE.search(oracle_text or "")
    if not m:
        return False
    named_colors = {color for name, color in _BASIC_LAND_TYPE_COLORS.items() if name in m.group(1)}
    if not named_colors:
        return False  # names something else (e.g. a specific nonbasic), not a plain basic-type fetch
    return not (named_colors & colors_allowed)


_COMMANDER_IDENTITY_MANA_RE = re.compile(r"[Aa]dd (?:one mana of )?any color in your commander'?s color identity")
_PLAIN_COLORLESS_MANA_RE = re.compile(r"\{T\}[^.\n]*:\s*Add \{C\}")


def _dead_commander_identity_mana(oracle_text: str, colors_allowed: set[str]) -> bool:
    """True if oracle_text's ONLY mana ability is the templated "Add one
    mana of any color in your commander's color identity" (Arcane Signet,
    Commander's Sphere, Command Tower, Hidden Hideout, Path of Ancestry,
    and a handful of others all share this exact wording) and
    colors_allowed -- the commander's actual color identity -- is empty,
    meaning "any color in your commander's color identity" is asking for
    one of zero colors: the ability produces nothing at all. Real,
    user-caught bug, same shape as _dead_fetch_land_colors above:
    confirmed live for Zhulodok, Void Gorger (colorless) -- Command
    Tower, one of the most automatic includes in Commander, was being
    suggested as a land despite being completely nonfunctional for this
    specific commander.

    Never excludes a card that ALSO has an unconditional colorless
    ability elsewhere in its text (Opal Palace's own separate "{T}: Add
    {C}." keeps it useful even though its bonus color-identity-gated
    ability is equally dead here) -- only the cards whose color-identity
    ability is their sole means of producing mana at all."""
    if colors_allowed:
        return False  # a real commander color exists, so "any color in identity" produces something
    if not _COMMANDER_IDENTITY_MANA_RE.search(oracle_text or ""):
        return False
    return not _PLAIN_COLORLESS_MANA_RE.search(oracle_text or "")


_LAND_TYPE_GRANT_RE = re.compile(r"is an?\s+(Plains|Island|Swamp|Mountain|Forest)\b")


def _useless_type_granting_land(oracle_text: str, colors_allowed: set[str]) -> bool:
    """True if oracle_text's only effect is granting a single basic land
    type (Urborg, Tomb of Yawgmoth: "Each land is a Swamp in addition to
    its other land types.") whose color isn't in colors_allowed, and the
    land has no other guaranteed mana ability of its own.

    A real, user-caught distinction from the two checks above: this
    doesn't add any mana a land wouldn't already have -- it only changes
    what color is available (Urborg itself has no mana ability printed on
    it at all; its only output is the {T}: Add {B} it gains BY becoming a
    Swamp). For a deck that can never spend that color -- confirmed for
    Zhulodok, Void Gorger (colorless): black mana can pay a generic cost
    but never Zhulodok's own {C} pip, so trading a land's natural output
    for Urborg's black is pure downside, never a reason to include it --
    this is exactly as useless in practice as a land with no ability at
    all, even though it technically still taps for something.

    Note Urborg's own color_identity is empty (the grant is plain English,
    "is a Swamp", not a printed {B} symbol), so it passes the ordinary
    color-identity filter in ANY deck regardless of whether that deck can
    use black -- the same gap _dead_fetch_land_colors closes for
    fetchlands, here for a type-granting land instead."""
    m = _LAND_TYPE_GRANT_RE.search(oracle_text or "")
    if not m:
        return False
    color = _BASIC_LAND_TYPE_COLORS.get(m.group(1))
    if not color or color in colors_allowed:
        return False
    return not _PLAIN_COLORLESS_MANA_RE.search(oracle_text or "")


# Deliberately matches only a bare "any color"/"any one color"/"the chosen
# color" ability -- NOT "...in your commander's color identity" (that's
# _dead_commander_identity_mana's own, differently-gated case) and NOT
# "...of any TYPE a land you control could produce" (Reflecting Pool: real
# MTG rules distinguish "type" -- which includes colorless -- from "color",
# which never does, so that one genuinely can mirror a {C} source and stays
# fine). The negative lookahead keeps the two functions' concerns separate.
_ARBITRARY_COLOR_MANA_RE = re.compile(
    r"[Aa]dd [a-z]+ mana of (?:any one color|any color|the chosen color)\b(?! in your commander)"
)


def _useless_arbitrary_color_mana(oracle_text: str, colors_allowed: set[str]) -> bool:
    """True if oracle_text's mana ability only ever produces a real WUBRG
    color -- player-chosen (Gilded Lotus, Command Bridge, Crossroads
    Village, Room of Refuge) or dependent on what a land you/an opponent
    controls could produce (Fellwar Stone, Exotic Orchard) -- with no
    colorless fallback of its own, and colors_allowed is empty.

    This is explicitly NOT the same as the two checks above: a card like
    this is excellent, completely normal fixing for any real colored
    commander (that IS exactly the flexible mana such a deck wants), so
    this only ever excludes it for a colorless commander specifically --
    where "any color" can never include the one thing the deck actually
    needs ({C}), and every other mana source already suggested covers
    both generic costs AND {C} costs, making this strictly the worse
    pick regardless of which specific color it happens to produce."""
    if colors_allowed:
        return False
    if not _ARBITRARY_COLOR_MANA_RE.search(oracle_text or ""):
        return False
    return not _PLAIN_COLORLESS_MANA_RE.search(oracle_text or "")


# Interaction tags that, on a *permanent*, describe incidental damage rather
# than removal: Terror of the Peaks, Orcish Bowmasters, Siege-Gang Commander
# and Sword of Fire and Ice are all tagged "burn any" but are threats/
# engines, not the answers the Interaction slots are budgeted for (user-
# reported: Terror of the Peaks counted as Interaction). Instants/sorceries
# with these tags (Lightning Bolt) are real interaction and keep the role.
_INCIDENTAL_BURN_LABELS = {"burn any", "burn with set's mechanic"}
_INCIDENTAL_BURN_KEY = "_incidental_burn"
_PERMANENT_CATEGORIES = ("Creatures", "Artifacts", "Enchantments")


def _role_member_names(groups: dict, tag_labels: dict) -> dict[str, frozenset[str]]:
    """{role: {normalized_card_name, ...}} -- the real, full membership of
    every _ROLE_TAG_LABELS tag for each role, from budget_alt_data_in_
    index()'s `groups` (every card tagged with a given Oracle Tag, not
    just each card's own single "representative" pick -- see
    _ROLE_TAG_LABELS' own docstring for why that distinction is the whole
    fix). `groups[tag_id]` entries are `[normalized_name, display_name,
    ...]`; only the normalized name (index 0) is needed here."""
    label_to_id = {label: tag_id for tag_id, label in tag_labels.items()}
    result: dict[str, frozenset[str]] = {}
    for role, labels in _ROLE_TAG_LABELS.items():
        names: set[str] = set()
        for label in labels:
            tag_id = label_to_id.get(label)
            if not tag_id:
                continue
            for entry in groups.get(tag_id) or []:
                if entry:
                    names.add(entry[0])
        result[role] = frozenset(names)
    # Cards whose ONLY Interaction tags are the incidental-burn ones above
    # (anything also tagged with a real removal/sweeper/counter label stays
    # Interaction even as a permanent -- e.g. Goblin Cratermaker).
    soft: set[str] = set()
    hard: set[str] = set()
    for label in _ROLE_TAG_LABELS["Interaction"]:
        tag_id = label_to_id.get(label)
        if not tag_id:
            continue
        target = soft if label in _INCIDENTAL_BURN_LABELS else hard
        for entry in groups.get(tag_id) or []:
            if entry:
                target.add(entry[0])
    result[_INCIDENTAL_BURN_KEY] = frozenset(soft - hard)
    return result


def _card_role(name: str, category: str, role_members: dict[str, frozenset[str]]) -> str:
    """Buckets a card into the standard EDH deck-shape roles (Lands/Ramp/
    Draw/Interaction, else "Synergy" for everything else -- creatures,
    other spells, win conditions, theme pieces). Not a full archetype
    classifier, just enough to keep Suggest roughly on-shape for
    commander_deck_shape_targets. `role_members` -- see _role_member_names
    -- checks a card's full real Oracle Tag membership per role, not just
    its single tag_by_name pick."""
    if category in ("Lands", "Basic Lands"):
        return "Lands"
    nm = normalize_name(name)
    incidental_burn = role_members.get(_INCIDENTAL_BURN_KEY) or frozenset()
    for role, names in role_members.items():
        if role == _INCIDENTAL_BURN_KEY:
            continue
        if nm in names:
            if role == "Interaction" and category in _PERMANENT_CATEGORIES and nm in incidental_burn:
                continue
            return role
    return "Synergy"


# Below this, an EDHREC synergy score is noise (see _synergy_reason): the card
# is simply played everywhere, not distinctively with this commander.
_MEANINGFUL_SYNERGY = 0.08


def _synergy_reason(score: float | None) -> str | None:
    """Wording for an EDHREC synergy score, tiered so the label never
    overstates a weak number -- real, user-reported bug: Sol Ring (a
    universal staple played regardless of commander, so its synergy with
    any *specific* one is naturally near zero) was showing up as "high
    synergy with your commander (per EDHREC, 1%)", which reads as a
    contradiction -- 1% isn't "high" by any reasonable standard, it's
    just barely positive. Calibrated against real observed scores (a
    genuine standout pick for a commander is typically >=20%; the ~5-15%
    range is a real but modest edge; anything under that is closer to
    noise -- a card that's simply fine everywhere, not on the commander's
    own theme). None below the noise floor -- callers fall through to
    their own next-best reason (a role/tag match, or a generic fallback)
    rather than mentioning EDHREC at all for a score this weak."""
    if score is None or score < _MEANINGFUL_SYNERGY:
        return None
    if score >= 0.20:
        return f"high synergy with your commander (per EDHREC, {score:.0%})"
    return f"synergizes with your commander (per EDHREC, {score:.0%})"


def role_counts_for_entries(
    entries: list[CardEntry],
    role_members: dict[str, frozenset[str]] | None = None,
    exclude_commander: bool = True,
) -> dict[str, int]:
    """Buckets a decklist into the standard EDH deck-shape roles (see
    _card_role) and sums quantities per role -- {"Lands", "Ramp", "Draw",
    "Interaction", "Synergy"}, always all 5 keys even if a role has 0.
    Backs the Deck Builder's Analyze modal (showing the finished deck's
    shape against commander_deck_shape_targets, below).

    `role_members` (from _role_member_names) can be passed in by a caller
    already holding it, to avoid re-reading the price index file on every
    call; a one-off caller (a single Analyze click) can omit it and this
    loads it fresh."""
    if role_members is None:
        budget_alt = budget_alt_data_in_index()
        role_members = _role_member_names(budget_alt["groups"], budget_alt["tag_labels"])
    counts = {"Lands": 0, "Ramp": 0, "Draw": 0, "Interaction": 0, "Synergy": 0}
    for e in entries:
        if exclude_commander and e.section == "commander":
            continue
        role = _card_role(e.name, categorize(e.type_line), role_members)
        counts[role] = counts.get(role, 0) + e.quantity
    return counts


def commander_deck_shape_targets(deck_format: str, target_size: int) -> dict[str, int | None]:
    """Rough per-role target counts for a deck of this size/format -- the
    single shared source both suggest_builder_cards (what Suggest actually
    builds toward) and the Deck Shape breakdown in Analyze (what a built
    deck is judged against) use, so the two can't drift apart again --
    they silently did for a while (Suggest targeting an old, separate
    11-Interaction default while Analyze displayed 15), which is exactly
    why every auto-built deck looked "light on interaction": Suggest was
    hitting the number it was told to hit, just not the number shown next
    to it. For a 100-card Commander deck: ~37 lands, ~10 ramp, ~10 card
    draw, ~15 removal + board wipes combined (the player's own 10 removal
    + 5 wipes), and the rest as win conditions/synergy -- scaled
    proportionally for a different target size. Non-Commander just gets a
    lands target (CONSTRUCTED_LAND_FRACTION) -- constructed archetypes
    vary too much for a single generic ramp/draw/interaction split to mean
    anything."""
    if deck_format == "commander":
        lands = round(target_size * 0.37)
        ramp = round(target_size * 0.10)
        draw = round(target_size * 0.10)
        interaction = round(target_size * 0.15)
        return {"Lands": lands, "Ramp": ramp, "Draw": draw, "Interaction": interaction, "Synergy": target_size - lands - ramp - draw - interaction}
    lands = round(target_size * CONSTRUCTED_LAND_FRACTION)
    return {"Lands": lands, "Ramp": None, "Draw": None, "Interaction": None, "Synergy": target_size - lands}


def _is_basic_land(type_line: str) -> bool:
    return "Basic" in type_line and "Land" in type_line


# Oracle Tags that mark a commander as equipment/aura-based Voltron: the win
# condition is one big, protected creature, so symmetric board wipes (which
# kill that creature too) are the wrong kind of Interaction -- Voltron wants
# selective or one-sided wipes that clear the opponents' blockers instead.
# Cloud, Ex-SOLDIER is tagged synergy-equipment + quick equip.
_VOLTRON_COMMANDER_TAGS = {
    "synergy-equipment", "synergy-equipment-legendary", "affinity for equipment",
    "quick equip", "auto equip", "synergy-aura", "transferrable aura",
    "cost-reducer-equip-ability", "cost-reducer-equipment",
}


# EDHREC's theme labels (tag_counts "value", lowercased) -> the CURATED_THEMES
# entry whose Oracle Tags describe the same plan. Only themes whose names
# line up unambiguously; anything else EDHREC says is simply ignored.
_EDHREC_THEME_TO_CURATED = {
    "reanimator": "Reanimator", "tokens": "Tokens", "mill": "Mill", "artifacts": "Artifacts",
    "lifegain": "Lifegain", "landfall": "Landfall", "proliferate": "Proliferate",
    "sacrifice": "Sacrifice / Aristocrats", "aristocrats": "Sacrifice / Aristocrats",
    "blink": "Blink / Flicker", "flicker": "Blink / Flicker", "wheels": "Wheels",
    "storm": "Storm", "discard": "Discard", "extra turns": "Extra Turns", "stax": "Stax",
    "group hug": "Group Hug", "group slug": "Group Slug", "enchantress": "Enchantress",
    "voltron": "Voltron",
}


def _edhrec_theme_names(profile: dict, top: int = 3, min_share: float = 0.5) -> list[str]:
    """CURATED_THEMES names for the commander's top-`top` EDHREC themes that
    at least `min_share` as many decks play as its most-played theme
    (Teval: Reanimator 2893, Lands Matter 1696, Mill 1668 -> Reanimator,
    Mill; Y'shtola's Lifegain at 1262 vs Control's 3272 is too minor and
    would only pull in fluff)."""
    names: list[str] = []
    themes = profile.get("themes") or []
    biggest = (themes[0].get("count") or 0) if themes else 0
    for t in themes[:top]:
        if biggest and (t.get("count") or 0) < biggest * min_share:
            continue
        curated = _EDHREC_THEME_TO_CURATED.get((t.get("value") or "").strip().lower())
        if curated and curated not in names:
            names.append(curated)
    return names


# How a commander's EDHREC themes say it feels about symmetric board wipes
# (Wrath of God and friends, which hit your own board too). A theme only
# counts if it's among the commander's top 3. "friendly" wins over "averse":
# Talrand (Spellslinger, Tokens, Control) wants wipes; Cloud (Equipment,
# Voltron), Kaalia and Krenko (Aggro) don't.
_WIPE_AVERSE_THEMES = {"voltron", "aggro", "tokens", "equipment", "extra combats"}
_WIPE_FRIENDLY_THEMES = {
    "control", "spellslinger", "reanimator", "mill", "planeswalkers", "superfriends",
    "wheels", "group slug",
}
# Most symmetric wipes a deck should hold, by stance. ~3 is the usual count in
# a creature deck that still wants an emergency button; control/graveyard/
# superfriends decks run more. Averse decks run none (and rank them last).
_WIPE_CAPS = {"averse": 0, "neutral": 3, "friendly": 5}


def _edhrec_wipe_stance(profile: dict) -> str | None:
    """"averse" / "friendly" / "neutral" from EDHREC's top-3 themes for the
    commander, or None when EDHREC has no themes for it."""
    themes = profile.get("themes") or []
    if not themes:
        return None
    top = {(t.get("value") or "").strip().lower() for t in themes[:3]}
    if top & _WIPE_FRIENDLY_THEMES:
        return "friendly"
    if top & _WIPE_AVERSE_THEMES:
        return "averse"
    return "neutral"


def _wipe_policy(
    commander_name: str | None, groups: dict, tag_labels: dict, preferred_theme_label: str | None = None,
    edhrec_profile: dict | None = None,
) -> tuple[str, frozenset[str], frozenset[str]]:
    """(stance, symmetric_wipe_names, one_sided_wipe_names). stance is
    "averse" when the player explicitly picked the Voltron theme, else what
    EDHREC's themes say (_edhrec_wipe_stance), else -- only when EDHREC has
    nothing -- "averse" if the commander carries one of
    _VOLTRON_COMMANDER_TAGS and "neutral" otherwise. symmetric = tagged
    "sweeper" but not "sweeper-one-sided" (Wrath of God, Blasphemous Act);
    one_sided = "sweeper-one-sided" (Elspeth, Sun's Champion; Cyclonic
    Rift). Callers still run _hits_own_board over both, since the one-sided
    tag is loose."""
    label_to_id = {label: tag_id for tag_id, label in tag_labels.items()}

    def members(label: str) -> set[str]:
        return {e[0] for e in (groups.get(label_to_id.get(label)) or []) if e}

    one_sided = members("sweeper-one-sided")
    symmetric = members("sweeper") - one_sided
    if preferred_theme_label == "Voltron":
        stance = "averse"
    else:
        stance = _edhrec_wipe_stance(edhrec_profile or {})
        if stance is None:
            nm = normalize_name(commander_name) if commander_name else None
            voltron = bool(nm) and any(nm in members(label) for label in _VOLTRON_COMMANDER_TAGS)
            stance = "averse" if voltron else "neutral"
    return stance, frozenset(symmetric), frozenset(one_sided)


_ALL_CREATURES_RE = re.compile(r"\b(?:each|all) (?:non-?\w+ )?creatures?\b", re.I)
_OPPONENT_ONLY_RE = re.compile(
    r"you don't control|your opponents? controls?|opponents? controls?|"
    r"target (?:player|opponent) controls?|defending player controls?|attacking creatures?",
    re.I,
)


def _hits_own_board(c: dict, nm: str, symmetric: frozenset[str], one_sided: frozenset[str]) -> bool:
    """True if this wipe would also hit the player's own creatures. The
    "sweeper" tag set is the baseline, but "sweeper-one-sided" is loose
    (Desolation of Smaug -- 3 damage to each non-Dragon creature -- and
    Elspeth, Sun's Champion -- destroy all creatures with power 4 or
    greater -- both carry it), so a one-sided-tagged card whose text says
    "each/all creatures" with no opponent-only qualifier still counts."""
    if nm in symmetric:
        return True
    if nm in one_sided:
        text = c.get("oracle_text") or ""
        return bool(_ALL_CREATURES_RE.search(text)) and not _OPPONENT_ONLY_RE.search(text)
    return False


def _costly_filler(c: dict, role: str) -> bool:
    """True for a 6+ mana card in a role that's supposed to be cheap
    support (Ramp/Draw/Interaction). Only used among cards with no
    commander-specific signal: a tiebreak on overall popularity alone
    happily fills a Draw slot with a 7-mana "draw half your library"
    sorcery (real case: Peer into the Abyss in a Kaalia deck), or an
    Interaction slot with 6-mana planeswalkers (Elspeth, Sun's Champion and
    Ugin, the Ineffable in a Cloud, Ex-SOLDIER deck, with cheaper owned
    removal left on the bench). Synergy and Lands are exempt -- big
    finishers are legitimately Synergy cards."""
    return role in ("Ramp", "Draw", "Interaction") and (c.get("cmc") or 0) >= 6


def _overall_rank(c: dict) -> int:
    """EDHREC's overall popularity rank (1 = most played), or a huge number
    when unknown so unranked cards sort after ranked ones. The last
    tiebreak before the name in the rank_keys below: with nothing
    commander-specific to go on, "cards people actually play" beats A-Z
    (which used to hand every deck whichever owned cards start with 'A',
    e.g. A.I.M. Synthoids and Abundant Maw)."""
    return c.get("edhrec_rank") or 10**9


def owned_collection_gameplay_view(owned: dict[str, OwnedCard], gameplay: dict[str, dict]) -> list[dict]:
    """Merges load_collection()'s owned-card/pricing data with
    gameplay_data_in_index()'s type/color/legality data into flat,
    JSON-ready dicts for the builder's collection browser: {name,
    quantity, type_line, cmc, mana_cost, color_identity, category,
    scryfall_id, set_code, collector_number, legalities, oracle_text}.
    oracle_text is carried here at no extra cost (gameplay already has
    it) since nothing currently reads it, but it's available for any
    future free-text search over the owned collection. Owned cards with
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
            "oracle_text": gp.get("oracle_text") or "",
            "edhrec_rank": gp.get("edhrec_rank"),
        })
    view.sort(key=lambda c: c["name"])
    return view


def full_card_pool_gameplay_view(gameplay: dict[str, dict], exclude_names: set[str]) -> list[dict]:
    """Every real paper card gameplay_data_in_index() knows about (the
    full MTGJSON pool, not just owned ones), shaped identically to
    owned_collection_gameplay_view's own per-card dicts so it can be run
    through the exact same _filter_candidates legality/color-identity/
    dead-mana-source checks suggest_builder_cards already applies to
    owned cards -- see its own purchase-suggestion fallback. quantity is
    fixed at 1 (a single copy is always enough to suggest buying one);
    there's no real owned printing to report set_code/collector_number
    from. `exclude_names` (normalized) drops anything already owned --
    there's nothing to suggest buying for those."""
    view = []
    for name, gp in gameplay.items():
        if name in exclude_names:
            continue
        view.append({
            "name": gp.get("name") or name,
            "quantity": 1,
            "type_line": gp.get("type_line") or "",
            "mana_cost": gp.get("mana_cost") or "",
            "cmc": gp.get("cmc") or 0,
            "color_identity": gp.get("color_identity") or [],
            "category": categorize(gp.get("type_line") or ""),
            "scryfall_id": gp.get("scryfall_id"),
            "set_code": "",
            "collector_number": "",
            "legalities": gp.get("legalities") or {},
            "oracle_text": gp.get("oracle_text") or "",
            "edhrec_rank": gp.get("edhrec_rank"),
        })
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
    excluded_card_names: set[str] | None = None,
) -> list[dict]:
    """Owned cards that are legal, color-correct, and not already in the
    WIP deck -- the same filtering suggest_builder_cards has always done,
    pulled out so list_theme_options/suggest_replacements can compute
    their own candidate pools without duplicating the legality/color-
    identity/Game-Changer-cap/Set-Selection rules.

    `excluded_set_codes`, if given, drops any candidate whose (single,
    representative -- see owned_collection_gameplay_view) owned printing
    is from one of those sets. Empty/None means no restriction, matching
    the Set Selection filter's "everything on by default" behavior.

    `excluded_card_names` (normalized names), if given, drops any
    candidate the player has explicitly said never to suggest again for
    this brew -- the fix for a real, confirmed problem: a card with no
    EDHREC synergy data for the commander (neutral, not negative, in
    rank_key below) can still win a role purely on generic theme-overlap
    with cards already in the deck. Cutting it from the deck alone doesn't
    stop it from being re-suggested right back into the same now-open
    slot on the very next call, since nothing remembers that cut -- this
    is that memory, same "everything on by default" convention as
    excluded_set_codes."""
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
        # Real, user-caught bug: a Plane card ("Artist Alley", type_line
        # "Plane — MagicCon") got suggested as a library card entirely.
        # These supertypes belong to a separate special deck (Planechase/
        # Archenemy/Vanguard), never the main 100-card Commander library,
        # but MTGJSON's bulk data carries an EMPTY legalities dict for
        # them -- not an explicit "not_legal" -- so the legality check
        # below (whose "missing = allowed" convention exists specifically
        # for ordinary untracked cards in Commander) let it straight
        # through. This is a correctness check, not a color/format one --
        # applies unconditionally, independent of colors_allowed/
        # legality_key, and would equally apply to an owned copy of one
        # of these, not just a purchase suggestion.
        if _NON_LIBRARY_TYPE_RE.search(c.get("type_line") or ""):
            continue
        if excluded_set_codes and (c.get("set_code") or "").upper() in excluded_set_codes:
            continue
        if excluded_card_names and normalize_name(c["name"]) in excluded_card_names:
            continue
        if gc_at_cap and normalize_name(c["name"]) in game_changers:
            continue
        if colors_allowed is not None and not set(c["color_identity"]).issubset(colors_allowed):
            continue
        # A classic fetchland's own color_identity is empty (no colored
        # mana symbols in its text), so it passes the check above in ANY
        # deck regardless of what it can actually find -- this catches
        # the real, user-confirmed gap that check misses: a fetch whose
        # named basic types (e.g. Arid Mesa's Mountain/Plains) don't
        # overlap this deck's colors at all can never resolve to a real
        # card, unlike an unrestricted "search for a basic land card"
        # fetch (Evolving Wilds and friends), which is left alone here.
        if colors_allowed is not None and _dead_fetch_land_colors(c.get("oracle_text") or "", colors_allowed):
            continue
        # Same empty-color_identity-but-functionally-useless gap, two more
        # shapes: a mana ability that only works for colors "in your
        # commander's color identity" (Command Tower, Arcane Signet, ...)
        # produces nothing when that identity is empty; a land that only
        # grants a single off-identity basic land type (Urborg) changes
        # what color is available rather than adding any -- see each
        # function's own docstring for the full reasoning.
        if colors_allowed is not None and _dead_commander_identity_mana(c.get("oracle_text") or "", colors_allowed):
            continue
        if colors_allowed is not None and _useless_type_granting_land(c.get("oracle_text") or "", colors_allowed):
            continue
        # A third, softer shape, only for a truly colorless commander:
        # Command Bridge/Crossroads Village/Room of Refuge/Gilded Lotus/
        # Fellwar Stone all produce a REAL, usable color -- fine fixing
        # for any colored deck -- but with no {C} fallback, so for a
        # colorless commander they can pay a generic cost and nothing
        # else, while every other suggested mana source already does
        # that AND covers {C}. User-requested exclusion, not a "legal
        # but zero function" bug like the two checks above.
        if colors_allowed is not None and _useless_arbitrary_color_mana(c.get("oracle_text") or "", colors_allowed):
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
    excluded_card_names: set[str] | None = None,
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
    candidates = _filter_candidates(wip_entries, owned_view, deck_format, target_format, commander_color_identity, None, excluded_set_codes, excluded_card_names)
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
    excluded_card_names: set[str] | None = None,
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
    candidates = _filter_candidates(wip_entries, owned_view, deck_format, target_format, commander_color_identity, None, excluded_set_codes, excluded_card_names)
    if not candidates:
        return []
    budget_alt = budget_alt_data_in_index()
    tag_by_name = budget_alt.get("tag_by_name") or {}
    tag_labels = budget_alt.get("tag_labels") or {}
    role_members = _role_member_names(budget_alt.get("groups") or {}, tag_labels)
    game_changers = game_changers_in_index() if deck_format == "commander" else set()

    # Same real, per-commander EDHREC synergy signal suggest_builder_cards
    # uses -- a replacement that's independently popular with this exact
    # commander is worth ranking above one that merely shares a generic
    # role tag with the card being swapped out. edhrec_tracked_names is
    # every name EDHREC returns at all (not just the positive-synergy
    # subset in synergy_by_name) -- same fix as suggest_builder_cards's
    # own rank_key: a card EDHREC has never seen paired with this
    # commander shouldn't rank level with one 7000+ real decks play
    # alongside it just because the latter's synergy score happens to be
    # at or below zero (a strong generic staple often IS slightly
    # negative -- played everywhere, not distinctively so with THIS
    # commander -- which isn't the same thing as never played with it).
    synergy_by_name: dict[str, float] = {}
    edhrec_tracked_names: set[str] = set()
    commander_entry = next((e for e in wip_entries if e.section == "commander"), None)
    if deck_format == "commander" and commander_entry:
        synergy_data = commander_synergy_cards(commander_entry.name)
        edhrec_tracked_names = set(synergy_data.keys())
        for nm, info in synergy_data.items():
            if info["synergy"] > 0:
                synergy_by_name[nm] = info["synergy"]

    def role_of(name: str, category: str) -> str:
        if deck_format != "commander":
            return "Lands" if category in ("Lands", "Basic Lands") else "Synergy"
        return _card_role(name, category, role_members)

    target_role = role_of(target_name, target_category)
    target_tag = tag_by_name.get(normalize_name(target_name))
    same_role = [c for c in candidates if role_of(c["name"], c["category"]) == target_role]

    edhrec_profile = commander_profile(commander_entry.name) if (deck_format == "commander" and commander_entry) else {}
    wipe_stance, symmetric_wipes, one_sided_wipes = _wipe_policy(
        commander_entry.name if commander_entry else None,
        budget_alt.get("groups") or {}, tag_labels, None, edhrec_profile,
    ) if deck_format == "commander" else ("neutral", frozenset(), frozenset())
    voltron_commander = wipe_stance == "averse"

    def rank_key(c: dict):
        nm = normalize_name(c["name"])
        shares_tag = target_tag is not None and tag_by_name.get(nm) == target_tag
        return (voltron_commander and _hits_own_board(c, nm, symmetric_wipes, one_sided_wipes), -synergy_by_name.get(nm, 0.0), nm not in edhrec_tracked_names, not shares_tag, nm not in game_changers, _costly_filler(c, target_role), _overall_rank(c), c["name"])

    same_role.sort(key=rank_key)
    tag_label = tag_labels.get(target_tag) if target_tag else None
    results = []
    for c in same_role[:limit]:
        nm = normalize_name(c["name"])
        shares_tag = target_tag is not None and tag_by_name.get(nm) == target_tag
        synergy_reason = _synergy_reason(synergy_by_name.get(nm))
        reason = (
            synergy_reason
            or (f'shares the "{tag_label}" role with {target_name}' if shares_tag and tag_label else None)
            or f'fills the same {target_role} role as {target_name}'
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
    excluded_card_names: set[str] | None = None,
    gameplay: dict[str, dict] | None = None,
    pools_out: dict | None = None,
    alternates_per_role: int = 30,
    unowned_alternates_per_role: int = 12,
) -> list[dict]:
    """Fill-the-gaps auto-suggest: proposes owned, legal, color-correct
    cards to fill the remaining slots in a work-in-progress deck. This is
    a heuristic ranking (combo pieces first, then a shared-theme signal,
    then whichever category is most under a rough target shape, then Game
    Changers/price as a power tiebreak) -- not an AI guess, same "no
    AI-generated guesses" approach the existing budget-alternative
    suggestions use (in fact the exact same Scryfall Oracle Tags data,
    see budget_alt_data_in_index).

    `gameplay` (gameplay_data_in_index()'s full MTGJSON pool), if given,
    backs a purchase-suggestion fallback: once owned candidates are
    exhausted, any role still short of its own target (e.g. a colorless
    commander whose owned collection only has 24 of the ~37 lands a
    real build wants) gets filled from the full card pool instead of
    just coming back short, each result marked owned: False with a real
    price (prices_data_in_index -- the same free, already-loaded local
    data /compare's shopping list already uses, no live API call). None
    (the default) skips this entirely and preserves the exact prior
    behavior -- every existing caller that doesn't pass it still only
    ever suggests owned cards.

    `pools_out`, if a dict is passed, is filled IN PLACE with what the
    guided "pick your own cards" flow needs (the return value is
    unchanged either way, so every existing caller is unaffected):
    {"steps": [{"role", "have", "needed", "target", "picks",
    "alternates", "unowned_alternates"}, ...], "gc_cap",
    "library_target", "library_count"}. `picks` is exactly what this
    function chose for that role (so a UI can pre-select them);
    `alternates` are the next-best owned candidates the ranking passed
    over (up to `alternates_per_role`); `unowned_alternates` are the
    best cards NOT owned for that role (up to
    `unowned_alternates_per_role`), restricted to cards EDHREC actually
    tracks for this commander -- ranking the whole 35k-card pool with no
    real-world signal would just surface alphabetical junk. Needs
    `gameplay`, like the purchase fallback.

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
    None (no preference) suggests freely, same as before this existed.

    `excluded_card_names` (normalized names), if given, are never
    suggested -- see _filter_candidates' own docstring for why this
    exists: a card with no EDHREC synergy data can still win a role on
    generic theme-overlap alone, so simply cutting it from the deck isn't
    enough to stop it coming right back the next time this is called."""
    # target_size is the *whole* deck (100 for Commander, matching WotC's
    # own rules -- 99 library + 1 commander); the commander itself never
    # counts toward the library, so the actual library target is one
    # less. Comparing the library count straight against target_size
    # instead would let Suggest keep filling until the library alone hit
    # 100 (101 cards total) and skew every role's numeric target by the
    # same one card.
    library_target = target_size - 1 if deck_format == "commander" else target_size
    used_names = {normalize_name(e.name) for e in wip_entries}
    library_count = sum(e.quantity for e in wip_entries if e.section != "commander")
    remaining = max(0, library_target - library_count)
    if pools_out is not None:
        # Always a valid (possibly empty) shape, even on the early returns
        # below, so a caller never has to special-case "nothing to suggest".
        pools_out.clear()
        pools_out.update({"steps": [], "gc_cap": None, "library_target": library_target, "library_count": library_count})
    if remaining <= 0:
        return []
    # Cap the batch itself to what's actually left, not just gate on
    # remaining>0 -- a deck 3 cards from done shouldn't get handed back a
    # full 15-card batch (every caller so far just appends everything
    # returned, e.g. "Add All" or a repeated build-to-completion loop, so
    # without this a deck can overshoot its target by a full batch).
    max_suggestions = min(max_suggestions, remaining)

    candidates = _filter_candidates(wip_entries, owned_view, deck_format, target_format, commander_color_identity, intended_bracket, excluded_set_codes, excluded_card_names)
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

    # Commander-specific synergy, straight from EDHREC's own real-decklist
    # statistics -- not a guess, not a generic Oracle-Tag category match.
    # This is the free, non-AI replacement for what the (now-removed) AI
    # builder existed to do: understand what actually goes well with THIS
    # specific commander's own text, not just "ramp"/"removal" categories.
    # Positive synergy means over-represented in real decks running this
    # commander versus the overall population -- see commander_synergy_cards'
    # own docstring for a real confirmed example. Best-effort: an unplayed/
    # brand-new commander or a network hiccup just yields {}, and every
    # candidate falls through to the existing theme/GC signals below.
    # edhrec_tracked_names is deliberately EVERY name EDHREC returns for
    # this commander, not just the positive-synergy subset in
    # synergy_by_name below -- a real, user-caught design gap: a card with
    # no entry at all (never appears on the commander's own EDHREC page,
    # in ANY of its cardlists) used to be scored identically to one EDHREC
    # tracks but scored at/below zero synergy for this specific commander
    # (both landed at the same synergy_by_name.get(nm, 0.0) == 0.0), so
    # generic theme-overlap alone could push a genuinely-never-played
    # card (confirmed live: Aether Snap, absent from Atraxa's ~291-card
    # EDHREC page entirely, has zero real players pairing it with her)
    # ahead of real, on-role removal just because it happened to share a
    # tag with cards already kept. "Just cut the bad pick and remember
    # not to suggest it again" (excluded_card_names, above) only ever
    # catches problems after a human already noticed one -- this instead
    # makes real-world EDHREC presence itself outrank a same-tag guess,
    # which is what actually being "EDHREC-driven" requires: prefer a
    # card real Atraxa decks are known to play at all, even at neutral or
    # slightly negative synergy, over one they're never seen playing.
    synergy_by_name: dict[str, float] = {}
    edhrec_tracked_names: set[str] = set()
    commander_entry = next((e for e in wip_entries if e.section == "commander"), None)
    if deck_format == "commander" and commander_entry:
        synergy_data = commander_synergy_cards(commander_entry.name)
        edhrec_tracked_names = set(synergy_data.keys())
        for nm, info in synergy_data.items():
            if info["synergy"] > 0:
                synergy_by_name[nm] = info["synergy"]

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
    role_members = _role_member_names(budget_alt.get("groups") or {}, tag_labels)
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

    edhrec_profile = commander_profile(commander_entry.name) if (deck_format == "commander" and commander_entry) else {}
    wipe_stance, symmetric_wipes, one_sided_wipes = _wipe_policy(
        commander_entry.name if commander_entry else None,
        budget_alt.get("groups") or {}, tag_labels, preferred_theme_label, edhrec_profile,
    ) if deck_format == "commander" else ("neutral", frozenset(), frozenset())
    voltron_commander = wipe_stance == "averse"
    wipe_cap = _WIPE_CAPS[wipe_stance] if deck_format == "commander" else None
    owned_by_name = {normalize_name(c["name"]): c for c in owned_view}

    def is_own_board_wipe(c: dict) -> bool:
        return _hits_own_board(c, normalize_name(c["name"]), symmetric_wipes, one_sided_wipes)

    # Wipes already in the deck count against the cap (a partial deck).
    running_wipe_count = sum(
        e.quantity for e in wip_entries
        if e.section != "commander"
        and is_own_board_wipe(owned_by_name.get(normalize_name(e.name)) or {"name": e.name})
    )
    # EDHREC's average planeswalker count in this commander's decks (Cloud 0,
    # Atraxa 5). Under 1 means players essentially never run them, so a
    # planeswalker with no commander-specific signal shouldn't fill a slot.
    avg_pw = edhrec_profile.get("avg_planeswalkers")
    pw_unwanted = avg_pw is not None and avg_pw < 1

    # EDHREC says what this commander is played as (Teval: Reanimator, Mill).
    # Its per-commander card lists only run ~50 deep per category, so owned
    # cards that clearly serve that plan but sit below the cut (Necromancy,
    # Zombify, Unearth in a Teval deck: 34 owned reanimation cards, 6 picked)
    # got nothing. Give every owned card carrying one of the theme's Oracle
    # Tags the same "fits the theme" standing the deck's own emerging themes
    # already get in rank_key -- after anything EDHREC itself tracks.
    edhrec_theme_boost: set[str] = set()
    if deck_format == "commander" and tag_labels:
        groups = budget_alt.get("groups") or {}
        label_to_id = {label: tag_id for tag_id, label in tag_labels.items()}
        for theme in _edhrec_theme_names(edhrec_profile):
            theme_names: set[str] = set()
            for label in CURATED_THEMES.get(theme, []):
                for entry in groups.get(label_to_id.get(label)) or []:
                    if entry:
                        theme_names.add(entry[0])
            for c in candidates:
                nm = normalize_name(c["name"])
                if nm in theme_names and nm not in theme_reason_by_name:
                    # Synergy only: a theme tag on a removal spell or mana
                    # creature (e.g. a mill creature in Ramp) says nothing
                    # about whether it's the right Ramp/Interaction pick --
                    # those roles keep choosing on their own signals.
                    if _card_role(c["name"], c["category"], role_members) == "Synergy":
                        theme_reason_by_name[nm] = f"fits this commander's {theme} theme (per EDHREC)"
                        edhrec_theme_boost.add(nm)

    def rank_key(c: dict):
        nm = normalize_name(c["name"])
        # EDHREC synergy sits right after combo-completion and ahead of the
        # generic theme signal -- a real, commander-specific number beats a
        # generic tag-category match. Negated so a HIGHER synergy score
        # sorts EARLIER (ascending sort, same convention every other field
        # here already uses via `not in`). Real EDHREC presence (tracked at
        # all for this commander, regardless of synergy sign -- see
        # edhrec_tracked_names above) outranks a pure theme-tag guess next,
        # so "actually played with this commander" beats "shares a generic
        # tag with cards already kept" whenever EDHREC has an opinion at
        # all; theme-matching only decides among cards EDHREC is silent on.
        return (
            nm not in reason_by_name,
            # Voltron: a symmetric wipe kills the commander the deck is
            # built around, so it sorts behind everything else -- even a
            # card EDHREC tracks for this commander (it's popular as a
            # generic red/white staple, not because it suits the plan).
            voltron_commander and _hits_own_board(c, nm, symmetric_wipes, one_sided_wipes),
            # Meaningful EDHREC synergy first; then cards that fit the
            # commander's EDHREC theme (Synergy role only), ahead of cards
            # EDHREC merely lists at noise-level synergy (Lightning Greaves
            # at +0.01 shouldn't beat Necromancy in a reanimator deck).
            synergy_by_name.get(nm, 0.0) < _MEANINGFUL_SYNERGY,
            -synergy_by_name.get(nm, 0.0) if synergy_by_name.get(nm, 0.0) >= _MEANINGFUL_SYNERGY else 0.0,
            nm not in edhrec_theme_boost,
            -synergy_by_name.get(nm, 0.0),
            nm not in edhrec_tracked_names,
            nm not in theme_reason_by_name,
            nm not in game_changers,
            pw_unwanted and c["category"] == "Planeswalkers",
            _costly_filler(c, role_of(c["name"], c["category"])),
            # Voltron: among equally cheap no-signal cards, a one-sided
            # wipe (clears blockers, spares the commander) goes first. Kept
            # behind the cost check on purpose: the one-sided tag is loose
            # (Elspeth, Sun's Champion carries it, but her -3 destroys the
            # Voltron commander too) and shouldn't override "no 6-drops".
            not (voltron_commander and nm in one_sided_wipes and not _hits_own_board(c, nm, symmetric_wipes, one_sided_wipes)),
            _overall_rank(c),
            c["name"],
        )

    # Combo-completing candidates are pulled in up front, ahead of the
    # role-shape apportionment below -- rank_key only sorts them to the
    # front of their OWN role's pool, which does nothing if that role
    # already hit its numeric target (e.g. Synergy's default 30-card
    # target, the single biggest bucket, so also the one most likely to
    # already be "full" by the time a combo piece becomes findable). That
    # let a real, owned, legal, one-card-away combo (Commander Spellbook
    # confirmed) get silently skipped every single Suggest call once its
    # role filled up, even though nothing else in the deck could ever
    # complete it. Still respects the intended-bracket Game Changer cap,
    # same check the round-robin below uses.
    combo_first: list[dict] = []
    for c in sorted(
        (c for c in candidates if normalize_name(c["name"]) in reason_by_name),
        key=lambda c: c["name"],
    ):
        if len(combo_first) >= max_suggestions:
            break
        is_gc = normalize_name(c["name"]) in game_changers
        if gc_cap is not None and is_gc and running_gc_count >= gc_cap:
            continue
        combo_first.append(c)
        if is_gc:
            running_gc_count += 1
        if is_own_board_wipe(c):
            running_wipe_count += 1  # a real combo piece wins, but it still counts
    combo_first_names = {normalize_name(c["name"]) for c in combo_first}
    role_pool_candidates = [c for c in candidates if normalize_name(c["name"]) not in combo_first_names]

    # Deck-shape roles: Commander gets the full Lands/Ramp/Draw/
    # Interaction/Synergy breakdown (see commander_deck_shape_targets and
    # its user-supplied override, mix_targets); constructed keeps the
    # simpler land-only target it always had, since there's no single
    # community-standard ramp/draw/removal ratio across constructed
    # formats/archetypes the way there is for EDH. "Synergy" (the deck's
    # actual creatures/win-cons/theme pieces) is always whatever's left of
    # library_target (the 99-card library, not counting the commander)
    # after the tracked roles -- the single biggest bucket in the standard
    # EDH shape (~30/99), so it's sized like every other role below, never
    # treated as a mere leftover. Sourced from commander_deck_shape_targets
    # rather than a separate local default so Suggest always builds toward
    # the exact same numbers Analyze's Deck Shape breakdown judges it
    # against (its own Synergy figure is dropped here and recomputed below
    # against library_target, not target_size, to stay exact).
    if deck_format == "commander":
        role_targets = {k: v for k, v in commander_deck_shape_targets(deck_format, target_size).items() if k != "Synergy"}
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
        return _card_role(name, category, role_members)

    current_role_counts: dict[str, int] = {}
    for e in wip_entries:
        r = role_of(e.name, categorize(e.type_line))
        current_role_counts[r] = current_role_counts.get(r, 0) + e.quantity
    role_needed = {role: max(0, target - current_role_counts.get(role, 0)) for role, target in role_targets.items()}

    role_candidates: dict[str, list[dict]] = {}
    for c in role_pool_candidates:
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
            #
            # Every role is capped at its OWN role_targets value here --
            # originally this only capped "Lands" (a single Suggest click
            # for Thranduil, the Elvenking landed 41 lands instead of the
            # ~38 target, back when a color pair with zero "Draw"
            # candidates dumped its whole shortfall into whichever role
            # had the single biggest raw pool), on the reasoning that "a
            # few extra Ramp/Interaction/Synergy picks is just more good
            # cards." That reasoning breaks down at scale: confirmed live
            # for Zhulodok, Void Gorger (colorless) -- once today's dead-
            # mana-source fixes correctly shrank the real Lands pool to
            # 24 (this collection's actual count of GOOD colorless lands,
            # not a bug) and Draw's pool was already thin, this exact loop
            # dumped all 19 leftover slots into Ramp/Synergy uncapped,
            # landing Ramp at 20 -- double its 10-card target -- while
            # Lands and Draw stayed starved. A lopsided role is a lopsided
            # role regardless of which one floods; capping every role the
            # same way Lands already was just means a batch can come back
            # short of the full count when the collection genuinely lacks
            # enough good cards for the ideal shape, which is the honest
            # outcome -- better than silently cramming extra Ramp in to
            # hit a number.
            changed = True
            while leftover > 0 and changed:
                changed = False
                for role in sorted(role_candidates, key=lambda r: len(role_candidates[r]), reverse=True):
                    if leftover <= 0:
                        break
                    if not role_candidates[role]:
                        continue
                    role_cap = role_targets.get(role, len(role_candidates[role]))
                    if role_cap is not None and role_slots.get(role, 0) < min(role_cap, len(role_candidates[role])):
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
    ordered: list[dict] = list(combo_first)
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
                if wipe_cap is not None and running_wipe_count >= wipe_cap and is_own_board_wipe(candidate):
                    continue  # enough symmetric wipes for this kind of deck already
                picked = candidate
                break
            if picked is None:
                active_roles.remove(role)
                continue
            ordered.append(picked)
            added_counts[role] += 1
            if normalize_name(picked["name"]) in game_changers:
                running_gc_count += 1
            if is_own_board_wipe(picked):
                running_wipe_count += 1

    # What the owned round-robin (plus the combo-first carve-out) already
    # placed per role. The purchase fallback below needs the real remaining
    # gap -- role_needed (target minus cards ALREADY in the deck) minus what
    # was just placed -- not role_targets minus placed. The latter was a
    # real bug for any partially-built deck: cards the user had already
    # added never counted, so a role that was actually full could still
    # look short and attract purchase picks while a genuinely short role
    # didn't.
    placed_per_role: dict[str, int] = dict(added_counts)
    for c in combo_first:
        r = role_of(c["name"], c["category"])
        placed_per_role[r] = placed_per_role.get(r, 0) + 1

    # Purchase-suggestion fallback: a role whose OWNED pool simply isn't
    # big enough to reach its own target (role_slots[role] was already
    # capped at len(role_candidates[role]) above) would otherwise just
    # come back short -- real, confirmed case: Zhulodok, Void Gorger
    # (colorless) has only 24 owned lands worth suggesting against a ~37
    # target. Rather than silently returning fewer cards than asked for,
    # fill the remainder from the full card pool when the caller passed
    # one, clearly marked as a purchase (owned: False) rather than mixed
    # in indistinguishably.
    unowned_picks: list[tuple[dict, str]] = []
    shortfall = {role: max(0, role_needed.get(role, 0) - placed_per_role.get(role, 0)) for role in role_targets}
    has_gap = len(ordered) < max_suggestions and any(shortfall.values())
    unowned_by_role: dict[str, list[dict]] = {}
    if gameplay and (has_gap or pools_out is not None):
        owned_or_used_names = {normalize_name(c["name"]) for c in owned_view} | used_names
        full_pool = full_card_pool_gameplay_view(gameplay, owned_or_used_names)
        unowned_candidates = _filter_candidates(
            wip_entries, full_pool, deck_format, target_format, commander_color_identity,
            intended_bracket, excluded_set_codes, excluded_card_names,
        )
        for c in unowned_candidates:
            unowned_by_role.setdefault(role_of(c["name"], c["category"]), []).append(c)
        for role, pool in unowned_by_role.items():
            pool.sort(key=rank_key)
    if gameplay and has_gap:
        for role, need in shortfall.items():
            pool = unowned_by_role.get(role) or []
            i = 0
            taken = 0
            while taken < need and i < len(pool) and len(ordered) + len(unowned_picks) < max_suggestions:
                candidate = pool[i]
                i += 1
                is_gc = normalize_name(candidate["name"]) in game_changers
                if gc_cap is not None and is_gc and running_gc_count >= gc_cap:
                    continue
                if wipe_cap is not None and running_wipe_count >= wipe_cap and is_own_board_wipe(candidate):
                    continue
                unowned_picks.append((candidate, role))
                taken += 1
                if is_gc:
                    running_gc_count += 1
                if is_own_board_wipe(candidate):
                    running_wipe_count += 1

    prices = prices_data_in_index() if (unowned_picks or (pools_out is not None and unowned_by_role)) else {}

    def _cheapest_price(nm: str) -> tuple[float, str] | tuple[None, None]:
        """(price, purchase_url) for the cheapest listed nonfoil (falling
        back to foil), cheapest-store-first per prices_data_in_index's own
        convention -- a real, user-caught gap: a bare price with no link
        to actually buy the card isn't a purchase suggestion, just a
        number."""
        entry = prices.get(nm) or {}
        for finish in ("nonfoil", "foil"):
            rows = entry.get(finish)
            if rows:
                return rows[0][1], rows[0][2]
        return None, None

    def suggestion_basis(nm: str) -> str:
        """Why this card was picked: "combo" (completes a real combo),
        "synergy" (meaningful EDHREC synergy with this commander), "theme"
        (fits the commander's/deck's theme), "edhrec" (EDHREC lists it with
        this commander but with no distinctive synergy), "filler" (none of
        the above -- chosen purely to fill a slot, ranked by overall
        popularity), or "other" when EDHREC has no data for the commander at
        all, in which case nothing can fairly be called filler."""
        if nm in reason_by_name:
            return "combo"
        if synergy_by_name.get(nm, 0.0) >= _MEANINGFUL_SYNERGY:
            return "synergy"
        if nm in theme_reason_by_name:
            return "theme"
        if nm in edhrec_tracked_names:
            return "edhrec"
        return "filler" if (deck_format == "commander" and edhrec_tracked_names) else "other"

    def make_suggestion(c: dict, role: str, owned: bool) -> dict:
        nm = normalize_name(c["name"])
        synergy_reason = _synergy_reason(synergy_by_name.get(nm))
        basis = suggestion_basis(nm)
        out = {
            "name": c["name"],
            "scryfall_id": c["scryfall_id"],
            "category": c["category"],
            "type_line": c["type_line"],
            "color_identity": c["color_identity"],
            "cmc": c["cmc"],
            "mana_cost": c["mana_cost"],
            "set_code": c["set_code"],
            "collector_number": c["collector_number"],
            "owned": owned,
            "role": role,
            "game_changer": nm in game_changers,
            "basis": basis,
        }
        if owned:
            fallback_reason = f"fills out {role}" if deck_format == "commander" else f"fills out {c['category']}"
            rank = c.get("edhrec_rank")
            if basis == "filler":
                fallback_reason = f"no EDHREC data for this card with your commander; picked to fill {role}" + (f" (overall popularity #{rank})" if rank else "")
            elif basis == "edhrec":
                fallback_reason = f"played in EDHREC decks for this commander (no distinctive synergy); fills out {role}"
            out["reason"] = reason_by_name.get(nm) or synergy_reason or theme_reason_by_name.get(nm) or fallback_reason
        else:
            price, price_url = _cheapest_price(nm)
            out["reason"] = reason_by_name.get(nm) or synergy_reason or f"not owned -- would fill out {role}"
            out["price"] = price
            out["price_url"] = price_url
            # Whether there's any real-world evidence behind this purchase
            # (EDHREC tracks it for this commander, or it completes a combo)
            # vs. it only being next in line when a role came up short. A
            # guided UI shouldn't pre-select the latter -- real, user-caught
            # smell: "Artist Alley", a Plane, got suggested purely as filler.
            out["supported"] = nm in edhrec_tracked_names or nm in reason_by_name
        return out

    suggestions = [make_suggestion(c, role_of(c["name"], c["category"]), True) for c in ordered]
    suggestions += [make_suggestion(c, role, False) for c, role in unowned_picks]

    if pools_out is not None:
        picked_names = {normalize_name(s["name"]) for s in suggestions}
        # current_role_counts includes the commander entry itself (it's in
        # wip_entries), but role targets are LIBRARY-only -- a commander
        # shouldn't count toward "cards you already have" for its role, or
        # a step opens reading "27 selected / 26 needed" before the user
        # has touched anything.
        commander_role = role_of(commander_entry.name, categorize(commander_entry.type_line)) if commander_entry else None
        steps = []
        for role in role_targets:
            have = current_role_counts.get(role, 0) - (1 if role == commander_role else 0)
            role_picks = [s for s in suggestions if s["role"] == role]
            owned_alts = [
                make_suggestion(c, role, True)
                for c in (role_candidates.get(role) or [])
                if normalize_name(c["name"]) not in picked_names
            ][:alternates_per_role]
            # Only cards EDHREC tracks for this commander: with no real-
            # world signal at all, rank_key's last tiebreak is alphabetical,
            # which across the whole ~35k-card pool would surface junk.
            unowned_alts = []
            if edhrec_tracked_names:
                unowned_alts = [
                    make_suggestion(c, role, False)
                    for c in (unowned_by_role.get(role) or [])
                    if normalize_name(c["name"]) in edhrec_tracked_names and normalize_name(c["name"]) not in picked_names
                ][:unowned_alternates_per_role]
            steps.append({
                "role": role,
                "have": have,
                "needed": max(0, role_targets.get(role, 0) - have),
                "target": role_targets.get(role, 0),
                "picks": role_picks,
                "alternates": owned_alts,
                "unowned_alternates": unowned_alts,
            })
        pools_out["steps"] = steps
        pools_out["gc_cap"] = gc_cap
        # Game Changers already in the deck count against the cap too, so
        # a UI warning on the picks can't be accurate without this.
        pools_out["gc_existing"] = sum(e.quantity for e in wip_entries if normalize_name(e.name) in game_changers)
    return suggestions


def optimize_builder_combos(
    wip_entries: list[CardEntry],
    owned_view: list[dict],
    deck_format: str,
    target_format: str | None,
    commander_color_identity: list[str] | None,
    intended_bracket: str | None = None,
    excluded_set_codes: set[str] | None = None,
    excluded_card_names: set[str] | None = None,
) -> list[dict]:
    """Second-pass optimizer for a deck that's already built (typically to
    its full target size). suggest_builder_cards()'s own combo-completion
    carve-out only ever sees combos as they stand at the *start* of one
    Suggest call -- so a combo piece added in that very same call (e.g.
    the common "Add All" single-batch flow) is invisible to it, and a
    deck already sitting at its target size never calls it again at all
    (suggest_builder_cards returns [] immediately once nothing's left to
    fill). This looks for real, owned, one-card-away combos in the
    *finished* deck and proposes concrete swaps instead: add the missing
    card, cut the lowest-priority card sharing its role.

    Commander Spellbook's own "almost included" results are always
    exactly one card away (verified directly against the live API, not
    assumed from its name) -- find_deck_combos() already relies on this,
    and so does this function: a combo missing 2+ owned cards just won't
    appear here, since there's no single-card swap that completes it.

    A candidate "cut" card is only ever pulled from the SAME role as the
    card being added, and is never a Game Changer, never itself a piece
    of any other included/almost-included combo, and never the deck's
    own theme match -- so a swap can never bump something that already
    earned its slot deliberately. If a combo's role has no safe cut
    candidate, that combo is simply skipped this round rather than
    forcing a cross-role cut.

    Calling this again after applying some swaps can surface combos that
    weren't visible before -- e.g. a 3-card combo the deck had 1 of, 2
    missing, doesn't show up until some other swap happens to add one of
    its other pieces, at which point it's genuinely one away and this
    picks it up on the next call. That's not special-cased here; it just
    falls out of re-querying find_deck_combos() fresh each call.

    Return shape: [{"add": {...card fields...}, "remove": {...card
    fields...}, "produces": [...], "reason": "..."}, ...]. Never applied
    automatically -- same "the user picks, we don't touch the deck
    ourselves" pattern suggest_replacements already uses for its swap
    popup."""
    if deck_format != "commander" or not wip_entries:
        return []
    combos = find_deck_combos(wip_entries)
    if not combos:
        return []
    # Real, user-caught bug: a combo needing a generic template slot (e.g.
    # "Permanent Castable for {C}") in ADDITION to its named cards isn't
    # genuinely "one card away" just because only one *named* card is
    # missing -- the template slot is a real, separate card the deck may
    # or may not actually have. Confirmed concretely, not assumed: for a
    # real Hullbreaker Horror + Sol Ring proposal, the only card in the
    # whole 99 that satisfied "Permanent Castable for {C}" was Sol Ring
    # itself -- which can't count twice, since the combo needs it on the
    # battlefield at the same time a *different* card is cast from hand.
    # There's no general, reliable way to evaluate an arbitrary Scryfall-
    # style template against this app's own card data (that's a real
    # query-language interpreter, not a lookup), so rather than propose
    # something that might quietly still be missing a piece, combos with
    # any such requirement are excluded from Optimize entirely -- only
    # combos that are genuinely complete with named cards alone (no
    # `requires`) get proposed as a clean, trustworthy swap.
    almost = [
        c for c in (combos.get("almost_included") or [])
        if len(c.get("missing") or []) == 1 and not c.get("requires")
    ]
    if not almost:
        return []

    candidates = _filter_candidates(wip_entries, owned_view, deck_format, target_format, commander_color_identity, intended_bracket, excluded_set_codes, excluded_card_names)
    candidates_by_name = {normalize_name(c["name"]): c for c in candidates}

    budget_alt = budget_alt_data_in_index()
    tag_by_name = budget_alt.get("tag_by_name") or {}
    tag_labels = budget_alt.get("tag_labels") or {}
    role_members = _role_member_names(budget_alt.get("groups") or {}, tag_labels)
    game_changers = game_changers_in_index()

    # Same real per-commander EDHREC synergy data suggest_builder_cards/
    # suggest_replacements use -- here it picks which card to CUT, not add:
    # among several same-role candidates safe to cut, prefer cutting the
    # one EDHREC says is least synergistic with this exact commander,
    # rather than an arbitrary alphabetical pick.
    synergy_by_name: dict[str, float] = {}
    commander_entry = next((e for e in wip_entries if e.section == "commander"), None)
    if commander_entry:
        for nm, info in commander_synergy_cards(commander_entry.name).items():
            synergy_by_name[nm] = info["synergy"]

    def role_of(name: str, category: str) -> str:
        return _card_role(name, category, role_members)

    # Cards to protect from ever being cut: any combo piece already
    # contributing to an included combo or to any almost-included combo
    # (cutting one would either break a real combo or destroy a *different*
    # one-away opportunity), and the commander's own theme tag / anything
    # sharing a 2+-represented tag in the deck (the same organic-theme
    # signal suggest_builder_cards uses, just read in reverse here).
    protected_names: set[str] = set()
    for combo in combos.get("included") or []:
        protected_names.update(normalize_name(n) for n in combo.get("uses") or [])
    for combo in combos.get("almost_included") or []:
        protected_names.update(normalize_name(n) for n in combo.get("uses") or [])
    wip_tag_counts: dict[str, int] = {}
    commander_tag_id = None
    for e in wip_entries:
        tag_id = tag_by_name.get(normalize_name(e.name))
        if not tag_id:
            continue
        if e.section == "commander":
            commander_tag_id = tag_id
        else:
            wip_tag_counts[tag_id] = wip_tag_counts.get(tag_id, 0) + e.quantity
    theme_tags = {tag_id for tag_id, n in wip_tag_counts.items() if n >= 2}
    if commander_tag_id:
        theme_tags.add(commander_tag_id)
    for e in wip_entries:
        if e.section == "commander":
            continue
        tag_id = tag_by_name.get(normalize_name(e.name))
        if tag_id in theme_tags:
            protected_names.add(normalize_name(e.name))

    used_add_names: set[str] = set()
    used_remove_names: set[str] = set()
    proposals = []
    for combo in almost:
        missing_name = combo["missing"][0]
        nm = normalize_name(missing_name)
        if nm in used_add_names:
            continue
        add_card = candidates_by_name.get(nm)
        if not add_card:
            continue  # not owned, not legal, or wrong colors -- nothing to propose
        add_role = role_of(add_card["name"], add_card["category"])
        cut_pool = sorted(
            (
                e for e in wip_entries
                if e.section != "commander"
                and normalize_name(e.name) not in used_remove_names
                and normalize_name(e.name) not in protected_names
                and normalize_name(e.name) not in game_changers
                and role_of(e.name, categorize(e.type_line)) == add_role
            ),
            key=lambda e: (synergy_by_name.get(normalize_name(e.name), 0.0), e.name),
        )
        if not cut_pool:
            continue  # no safe filler to cut in this role -- skip, don't force a cross-role cut
        cut = cut_pool[0]
        used_add_names.add(nm)
        used_remove_names.add(normalize_name(cut.name))
        proposals.append({
            "add": {
                "name": add_card["name"], "scryfall_id": add_card["scryfall_id"], "category": add_card["category"],
                "type_line": add_card["type_line"], "color_identity": add_card["color_identity"],
                "cmc": add_card["cmc"], "mana_cost": add_card["mana_cost"],
                "set_code": add_card["set_code"], "collector_number": add_card["collector_number"],
            },
            "remove": {"name": cut.name, "scryfall_id": cut.scryfall_id, "category": categorize(cut.type_line)},
            "produces": combo.get("produces") or [],
            # `almost` (above) already excludes any combo with a `requires`
            # template slot, so every proposal reaching this point is a
            # clean, fully-named-cards combo -- naming just the uses is
            # accurate here, not an oversimplification.
            "reason": f"completes a combo with {', '.join(combo['uses'][:2])}",
            "url": combo.get("url"),
        })
    return proposals
