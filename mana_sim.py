"""Mana goldfish: shuffles a Commander deck's 99 cards over and over and plays out only the
mana game -- land drops, mana rocks and dorks, land-fetching ramp spells -- to answer "does this
deck's mana work?" with numbers: opening-hand quality, mulligans, mana available on each turn,
how often it misses land drops, and how often the commander comes down on curve.

It is deliberately a mana model, not a combat or interaction simulator: every other spell is
dead weight in hand. Simplifications (all biased the same way for any deck, so they compare
decks fairly rather than predicting exact table results):
  - the player is on the draw on turn 1 (draws a card), as Karsten's tables assume
  - the first mulligan is free (multiplayer Commander); London mulligans after that
  - a land is "keepable" in a 7-card hand at 2-5 lands (ramp that costs 2 or less counts as half a land)
  - lands that enter tapped only when unconditional ("enters tapped"); conditional ones count as untapped
  - ramp spells are paid with mana from any source; colors only matter for the commander and land choice
"""
from __future__ import annotations

import random
import re

from brewlist_core import CardEntry, categorize, gameplay_data_in_index, normalize_name
from deck_builder import _BASIC_FOR_COLOR, _color_pips, _land_produces

_ADD_RE = re.compile(r"\{T\}[^.\n]*: Add ([^.\n]*)", re.I)
_SYMBOL_RE = re.compile(r"\{([^}]+)\}")
_ENTERS_TAPPED_RE = re.compile(r"enters(?: the battlefield)? tapped", re.I)
_CONDITIONAL_TAPPED_RE = re.compile(r"unless|if you control|if you don't|you may pay|if an opponent|if you have|if you reveal", re.I)
_LAND_RAMP_RE = re.compile(r"search your library for [^.]*\bland\b[^.]*(?:put|onto)[^.]*onto the battlefield", re.I)
_LANDS_IN_HAND_RE = re.compile(r"(?:put|puts) (?:the other|one of them|it|that card|them)[^.]*into your hand|and the other into your hand", re.I)

GAMES_DEFAULT = 2000
TURNS = 8
SEED = 20260519


def _classify(entry: CardEntry, info: dict) -> dict:
    """What one card does for the mana game: {kind: land|rock|dork|landramp|spell, mv, makes: set of
    colors, amount (mana per tap), tapped (enters tapped)}."""
    category = categorize(entry.type_line)
    text = info.get("oracle_text") or ""
    mv = int(entry.cmc or info.get("cmc") or 0)
    base = {"kind": "spell", "mv": mv, "makes": set(), "amount": 0, "tapped": False}
    if category in ("Lands", "Basic Lands"):
        tapped = bool(_ENTERS_TAPPED_RE.search(text)) and not _CONDITIONAL_TAPPED_RE.search(text)
        makes = _land_produces({"type_line": entry.type_line, "oracle_text": text})
        # "Add {C}" lands and utility lands still make mana even with no colors
        amount = 1
        return {**base, "kind": "land", "makes": makes, "amount": amount, "tapped": tapped}
    if mv <= 3:
        match = _ADD_RE.search(text)
        if match and category in ("Artifacts", "Creatures", "Enchantments"):
            symbols = _SYMBOL_RE.findall(match.group(1))
            amount = sum(1 for s in symbols if s in "WUBRGC" or "/" in s)
            if "any color" in match.group(1).lower() or "one mana of any" in match.group(1).lower():
                amount, makes = max(amount, 1), set(_BASIC_FOR_COLOR)
            else:
                makes = {s for s in symbols if s in _BASIC_FOR_COLOR}
            if amount:
                tapped = bool(_ENTERS_TAPPED_RE.search(text)) and not _CONDITIONAL_TAPPED_RE.search(text)
                kind = "dork" if "Creature" in entry.type_line else "rock"
                return {**base, "kind": kind, "makes": makes, "amount": amount, "tapped": tapped}
    if mv <= 4 and _LAND_RAMP_RE.search(text) and "Land" not in entry.type_line:
        return {**base, "kind": "landramp", "amount": 1, "extra_land_to_hand": bool(_LANDS_IN_HAND_RE.search(text))}
    return base


def _can_pay_pips(pips: dict[str, int], sources: list[set[str]]) -> bool:
    """Whether each colored pip can be paid by a distinct source (colorless sources can't pay a pip)."""
    needed = [col for col, n in pips.items() for _ in range(n)]
    if not needed:
        return True
    taken = [False] * len(sources)

    def assign(i: int) -> bool:
        if i == len(needed):
            return True
        for j, makes in enumerate(sources):
            if not taken[j] and needed[i] in makes:
                taken[j] = True
                if assign(i + 1):
                    return True
                taken[j] = False
        return False

    return assign(0)


def _opening_hand(deck: list[dict], rng: random.Random) -> tuple[list[dict], list[dict], int]:
    """(hand, library, mulligans) with a free first mulligan and London mulligans after."""
    mulligans = 0
    while True:
        shuffled = deck[:]
        rng.shuffle(shuffled)
        hand, library = shuffled[:7], shuffled[7:]
        score = sum(1 if c["kind"] == "land" else 0.5 if c["kind"] in ("rock", "dork", "landramp") and c["mv"] <= 2 else 0 for c in hand)
        lands = sum(1 for c in hand if c["kind"] == "land")
        keepable = 2 <= lands <= 5 and 2 <= score <= 5.5
        if keepable or mulligans >= 3:
            for _ in range(max(0, mulligans - 1)):  # free first mulligan: bottom one card per mulligan after it
                lands_in = [c for c in hand if c["kind"] == "land"]
                bottom = (lands_in[-1] if len(lands_in) > 3 else max(hand, key=lambda c: (c["kind"] == "spell", c["mv"])))
                hand.remove(bottom)
                library.append(bottom)
            return hand, library, mulligans
        mulligans += 1


class Table:
    """One game's mana state: hand, library and the permanents that make mana. `begin_turn` does the
    draw, the land drop and the ramp casting for a turn; the Turn Clock then spends what's left."""

    def __init__(self, deck: list[dict], deck_colors: set[str], rng: random.Random):
        self.deck_colors = deck_colors
        self.hand, self.library, self.mulligans = _opening_hand(deck, rng)
        self.opening_lands = sum(1 for c in self.hand if c["kind"] == "land")
        self.battlefield: list[dict] = []  # {"makes", "amount", "ready_turn", "land"}
        self.played: set[str] = set()  # normalized names of lands and ramp already on the battlefield
        self.produced = 0   # mana the table can make this turn
        self.pool = 0       # ...of which is still unspent after ramp

    def _mark_played(self, card: dict) -> None:
        name = card.get("name")
        if name:
            self.played.update({normalize_name(name), normalize_name(name.split(" // ")[0])})

    def begin_turn(self, turn: int) -> int:
        hand, battlefield, deck_colors = self.hand, self.battlefield, self.deck_colors
        if self.library:
            hand.append(self.library.pop(0))
        lands_in_hand = [c for c in hand if c["kind"] == "land"]
        if lands_in_hand:
            have_colors = {col for p in battlefield for col in p["makes"]}
            # prefer untapped lands, and lands that add a color the deck is still missing
            lands_in_hand.sort(key=lambda c: (c["tapped"], -len((c["makes"] & deck_colors) - have_colors), -len(c["makes"])))
            land = lands_in_hand[0]
            hand.remove(land)
            self._mark_played(land)
            battlefield.append({"makes": land["makes"], "amount": 1, "ready_turn": turn + (1 if land["tapped"] else 0), "land": True})
        produced = sum(p["amount"] for p in battlefield if p["ready_turn"] <= turn)
        pool = produced
        ramp = sorted((c for c in hand if c["kind"] in ("rock", "dork", "landramp")), key=lambda c: c["mv"])
        for c in ramp:
            if c["mv"] > pool:
                continue
            pool -= c["mv"]
            hand.remove(c)
            self._mark_played(c)
            if c["kind"] == "landramp":
                missing = sorted(deck_colors - {col for p in battlefield for col in p["makes"]})
                makes = {missing[0]} if missing else (set(sorted(deck_colors)[:1]) if deck_colors else set())
                battlefield.append({"makes": makes, "amount": 1, "ready_turn": turn + 1, "land": True})
                if c.get("extra_land_to_hand") and self.library:
                    hand.append({"kind": "land", "mv": 0, "makes": set(deck_colors), "amount": 1, "tapped": True})
            else:
                ready = turn + (1 if (c["tapped"] or c["kind"] == "dork") else 0)
                battlefield.append({"makes": c["makes"], "amount": c["amount"], "ready_turn": ready, "land": False})
                if ready <= turn:
                    pool += c["amount"]
                    produced += c["amount"]
        self.produced, self.pool = produced, pool
        return produced

    def mana_sources(self, turn: int) -> list[set[str]]:
        """The colors of each unspent mana this turn (the ramp's cost taken from the least colorful first)."""
        sources = sorted(
            (set(p["makes"]) for p in self.battlefield if p["ready_turn"] <= turn for _ in range(p["amount"])),
            key=len,
        )
        return sources[self.produced - self.pool:]

    def colors_ready(self, turn: int) -> set[str]:
        return {col for p in self.battlefield if p["ready_turn"] <= turn for col in p["makes"]}


def _play(deck: list[dict], commander: dict | None, deck_colors: set[str], rng: random.Random) -> dict:
    table = Table(deck, deck_colors, rng)
    mana_by_turn: list[int] = []
    colors_by_turn: list[set[str]] = []
    commander_cast_turn = None
    cmd_pips = {col: int(round(n)) for col, n in _color_pips(commander["mana_cost"]).items()} if commander else {}
    for turn in range(1, TURNS + 1):
        produced = table.begin_turn(turn)
        mana_by_turn.append(produced)
        colors_by_turn.append(table.colors_ready(turn))
        if commander and commander_cast_turn is None and produced >= commander["mv"]:
            sources = [p["makes"] for p in table.battlefield if p["ready_turn"] <= turn for _ in range(p["amount"])]
            if _can_pay_pips(cmd_pips, sources):
                commander_cast_turn = turn
    return {
        "mulligans": table.mulligans, "opening_lands": table.opening_lands, "mana": mana_by_turn, "colors": colors_by_turn,
        "commander_turn": commander_cast_turn,
    }


def simulate_mana(entries: list[CardEntry], games: int = GAMES_DEFAULT) -> dict | None:
    """Runs the mana goldfish on a Commander deck; None when there's no 99-card library to shuffle."""
    gameplay = gameplay_data_in_index()
    deck: list[dict] = []
    commander = None
    deck_colors: set[str] = set()
    for e in entries:
        info = gameplay.get(normalize_name(e.name)) or {}
        if e.section == "commander":
            if commander is None:
                commander = {"mv": int(e.cmc or info.get("cmc") or 0), "mana_cost": e.mana_cost or info.get("mana_cost") or "", "name": e.name}
            deck_colors |= set(_color_pips(e.mana_cost or info.get("mana_cost")))
            continue
        card = _classify(e, info)
        deck_colors |= set(_color_pips(e.mana_cost or info.get("mana_cost")))
        deck.extend([card] * e.quantity)
    if len(deck) < 40:
        return None
    rng = random.Random(SEED)
    runs = [_play(deck, commander, deck_colors, rng) for _ in range(games)]

    def pct(n: int) -> float:
        return round(100.0 * n / games, 1)

    on_curve = {t: pct(sum(1 for r in runs if r["mana"][t - 1] >= t)) for t in range(2, 7)}
    avg_mana = {t: round(sum(r["mana"][t - 1] for r in runs) / games, 2) for t in range(1, 7)}
    out = {
        "games": games,
        "lands": sum(1 for c in deck if c["kind"] == "land"),
        "ramp": sum(1 for c in deck if c["kind"] in ("rock", "dork", "landramp")),
        "mulligan_rate": pct(sum(1 for r in runs if r["mulligans"] > 0)),
        "avg_opening_lands": round(sum(r["opening_lands"] for r in runs) / games, 2),
        "on_curve": on_curve,
        "avg_mana": avg_mana,
        "four_mana_by_turn_4": pct(sum(1 for r in runs if r["mana"][3] >= 4)),
        "stuck_on_two_turn_4": pct(sum(1 for r in runs if r["mana"][3] <= 2)),
        "color_by_turn_4": {
            col: pct(sum(1 for r in runs if col in r["colors"][3])) for col in sorted(deck_colors)
        },
    }
    if commander and commander["mv"]:
        mv = commander["mv"]
        out["commander"] = {
            "name": commander["name"], "mv": mv,
            "on_curve": pct(sum(1 for r in runs if r["commander_turn"] is not None and r["commander_turn"] <= mv)),
            "within_two": pct(sum(1 for r in runs if r["commander_turn"] is not None and r["commander_turn"] <= mv + 2)),
        }
    return out
