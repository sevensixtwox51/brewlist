"""Turn Clock: a goldfish of how fast a Commander deck can kill ONE opponent who does nothing --
no blockers, no removal, no life gain -- starting from 40 life. It shuffles and plays the deck over
and over on top of the mana goldfish (mana_sim.Table: land drops, ramp, mulligans), casts the
creatures and damage effects it can afford each turn, attacks with everything, and records the turn
the opponent's life runs out.

It reads rules text with a handful of patterns, not a full rules engine, so it is a ruler for comparing
builds, not a prediction of a real table. What it understands:
  - creatures: printed power (Scryfall), summoning sickness, haste, double strike, and the tokens a
    creature or spell makes when it enters or at a beginning-of-turn trigger
  - static team boosts ("creatures you control get +1/+1", "each other Vampire you control gets +1/+1")
    and one-turn team pumps cast on the attack
  - direct damage and life loss to each opponent (ETB, spells, and beginning-of-turn triggers)
  - a commander's own attack trigger: "whenever ~ attacks, create a token" and the "put a creature card
    from your hand onto the battlefield attacking" cheat (Kaalia)
What it ignores: equipment and auras, damage that scales with board state (Gray Merchant, Blasphemous Act),
repeatable activated abilities, combos, extra combats and planeswalkers. A deck that wins by combo
or drain engines it can't read shows a slow or missing clock -- the panel says so when most games never kill.
"""
from __future__ import annotations

import json
import os
import random
import re
import urllib.error
import urllib.request

from brewlist_core import CardEntry, categorize, gameplay_data_in_index, normalize_name
from deck_builder import _color_pips
from mana_sim import SEED, Table, _classify, _opening_hand  # noqa: F401 -- _opening_hand re-exported for tests

_CORE_DIR = os.path.dirname(os.path.abspath(__file__))
CARD_STATS_PATH = os.path.join(_CORE_DIR, "data", "card_stats.json")

GAMES_DEFAULT = 1500
MAX_TURN = 12
LIFE = 40

_NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}
_COUNT = r"(a|an|one|two|three|four|five|six|seven|eight|\d+)"
_TOKEN_RE = re.compile(_COUNT + r" (?:tapped )?(?:and attacking )?(\d+)/(\d+) [^.\n]*?creature tokens?", re.I)
_DRAIN_RE = re.compile(r"each opponent loses (\d+) life|deals (\d+) damage to each opponent|deals (\d+) damage to each of your opponents", re.I)
_ANTHEM_RE = re.compile(r"creatures you control get \+(\d+)/\+(\d+)", re.I)
_TRIBE_ANTHEM_RE = re.compile(r"(?:each )?other ([a-z]+?)s? you control gets? \+(\d+)/\+(\d+)", re.I)
_CHEAT_RE = re.compile(r"put (?:an? )?([a-z, ]+?) (?:creature )?cards? from your hand onto the battlefield tapped and attacking", re.I)
_KEYWORDS = {"flying", "first strike", "double strike", "haste", "trample", "vigilance", "menace", "lifelink", "deathtouch",
             "reach", "indestructible", "hexproof", "flash", "defender", "infect", "wither", "prowess", "skulk", "fear", "intimidate"}
_OPPONENT_TOKEN_RE = re.compile(r"(?:its|that|their) controller creates|(?:target|that) (?:player|opponent) creates|each (?:opponent|other player) creates|defending player creates|enchanted [a-z]+'s controller", re.I)
_ABILITY_WORD_RE = re.compile(r"^[a-z' ]{3,24} — ")
_EACH_TURN_RE = re.compile(r"^at the beginning of (?:your|each) (?:upkeep|end step|combat|precombat main phase|postcombat main phase)", re.I)


def _tokens_in(ln: str) -> tuple[int, int] | None:
    """(count, power) of the creature tokens a line makes for ITS controller, or None."""
    if _OPPONENT_TOKEN_RE.search(ln):
        return None
    tok = _TOKEN_RE.search(ln)
    if not tok or int(tok.group(2)) <= 0:  # a 0/0 token (amass, counters) isn't a real attacker here
        return None
    return _num(tok.group(1)), int(tok.group(2))


def _num(word: str) -> int:
    return int(word) if word.isdigit() else _NUMBER_WORDS.get(word.lower(), 1)


# -- creature power from Scryfall (MTGJSON-backed gameplay index has none) -------------------------------------------

def _read_stats() -> dict:
    try:
        with open(CARD_STATS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def ensure_card_stats(names: list[str]) -> dict[str, dict]:
    """{normalized name: {"p", "t"}} for creatures, fetched once from Scryfall's collection endpoint and
    cached in data/card_stats.json (a card's printed power never changes). Network failures leave a
    name out; callers estimate a power for it."""
    cache = _read_stats()
    need = [n for n in dict.fromkeys(names) if normalize_name(n) not in cache]
    changed = False
    for i in range(0, len(need), 75):
        chunk = need[i:i + 75]
        try:
            req = urllib.request.Request(
                "https://api.scryfall.com/cards/collection",
                data=json.dumps({"identifiers": [{"name": n.split(" // ")[0]} for n in chunk]}).encode(),
                headers={"User-Agent": "brewlist/1.0 (personal use)", "Accept": "application/json", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                found = json.loads(resp.read().decode("utf-8")).get("data") or []
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            break
        for card in found:
            face = card
            if card.get("power") is None and card.get("card_faces"):
                face = card["card_faces"][0]
            cache[normalize_name(card.get("name", ""))] = {"p": face.get("power"), "t": face.get("toughness")}
            cache[normalize_name((card.get("name") or "").split(" // ")[0])] = {"p": face.get("power"), "t": face.get("toughness")}
        for n in chunk:
            cache.setdefault(normalize_name(n), {"p": None, "t": None})
        changed = True
    if changed:
        try:
            os.makedirs(os.path.dirname(CARD_STATS_PATH), exist_ok=True)
            tmp = f"{CARD_STATS_PATH}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cache, f)
            os.replace(tmp, CARD_STATS_PATH)
        except OSError:
            pass
    return cache


def _power(raw, mv: int) -> tuple[int, bool]:
    """(power, estimated): printed power, or a guess for */variable power and for stats we couldn't fetch."""
    if raw is not None and str(raw).lstrip("-").isdigit():
        return int(raw), False
    return max(1, round(mv * 0.8)), True


# -- reading a card's damage plan from its text ----------------------------------------------------------------------

def _profile(entry: CardEntry, info: dict, stats: dict) -> dict | None:
    """What the card adds to the clock, or None when it adds nothing the engine understands."""
    text = info.get("oracle_text") or ""
    type_line = entry.type_line or info.get("type_line") or ""
    mv = int(entry.cmc or info.get("cmc") or 0)
    name = entry.name
    low = text.lower()
    # legendary cards refer to themselves by a short name: "Kaalia", "Edgar Markov" for "Edgar Markov"
    short = name.split(",")[0]
    aliases = {name, short, short.split(" of ")[0]} | ({short.split()[0]} if "Legendary" in type_line else set())
    for alias in sorted(aliases, key=len, reverse=True):
        low = low.replace(alias.lower(), "~")
    lines = [ln.strip() for ln in low.split("\n") if ln.strip()]
    is_creature = "Creature" in type_line
    is_permanent = categorize(type_line) not in ("Instants", "Sorceries")
    profile: dict = {"creature": is_creature, "tokens": [], "drain": 0, "each_turn_tokens": [], "each_turn_drain": 0,
                     "anthem": 0, "tribe_anthem": None, "pump": 0, "global_haste": False, "haste": False, "double_strike": False,
                     "attack_tokens": [], "cheat": None, "estimated": False, "subtypes": set(), "power": 0}
    if is_creature:
        raw = (stats.get(normalize_name(name)) or {}).get("p")
        profile["power"], profile["estimated"] = _power(raw, mv)
        if "—" in type_line:
            profile["subtypes"] = set(type_line.split("—", 1)[1].split())
        keyword_lines = [
            {k.strip() for k in ln.split(" (")[0].split(",")} for ln in lines
            if ln.split(" (")[0].split(",")[0].strip().split(" ")[0] in {w.split(" ")[0] for w in _KEYWORDS}
        ]
        keywords = set().union(*[ks for ks in keyword_lines if ks <= _KEYWORDS]) if keyword_lines else set()
        profile["haste"] = "haste" in keywords
        profile["double_strike"] = "double strike" in keywords
    for ln in lines:
        ln = _ABILITY_WORD_RE.sub("", ln)
        triggered = ln.startswith(("when", "whenever", "at the beginning"))
        activated = ":" in ln.split(".")[0] and not triggered
        if activated:
            continue
        if "creatures you control have haste" in ln or "creatures you control gain haste" in ln and not triggered:
            profile["global_haste"] = True
        if ln.startswith(("whenever ~ attacks", "whenever ~ and at least")):
            tok = _tokens_in(ln)
            if tok:
                profile["attack_tokens"].append(tok)
            cheat = _CHEAT_RE.search(ln)
            if cheat:
                profile["cheat"] = {w.strip() for w in re.split(r",| or | and ", cheat.group(1)) if w.strip()}
            continue
        if _EACH_TURN_RE.match(ln):
            tok = _tokens_in(ln)
            if tok:
                profile["each_turn_tokens"].append(tok)
            drain = _DRAIN_RE.search(ln)
            if drain:
                profile["each_turn_drain"] += int(next(g for g in drain.groups() if g))
            continue
        if triggered and "enters" not in ln.split(",")[0]:
            continue
        # a spell's own effect line, or an enters-the-battlefield trigger
        tok = _tokens_in(ln)
        if tok and "until end of turn" not in ln:
            profile["tokens"].append(tok)
        drain = _DRAIN_RE.search(ln)
        if drain and not triggered or (drain and "enters" in ln.split(",")[0]):
            profile["drain"] += int(next(g for g in drain.groups() if g))
        anthem = _ANTHEM_RE.search(ln)
        if anthem and not triggered and is_permanent:
            profile["anthem"] += int(anthem.group(1))
        elif anthem and "until end of turn" in ln and not is_permanent:
            profile["pump"] += int(anthem.group(1))
        tribe = _TRIBE_ANTHEM_RE.search(ln)
        if tribe and not triggered and is_permanent:
            profile["tribe_anthem"] = (tribe.group(1).capitalize(), int(tribe.group(2)))
    useful = (is_creature and profile["power"] > 0) or profile["tokens"] or profile["drain"] or profile["each_turn_tokens"] \
        or profile["each_turn_drain"] or profile["anthem"] or profile["tribe_anthem"] or profile["pump"] or profile["cheat"]
    return profile if useful else None


# -- paying costs ----------------------------------------------------------------------------------------------------

def _pay(sources: list[set], pips: dict[str, int], mv: int) -> list[set] | None:
    """Mana sources left after paying a spell (colored pips from distinct sources that make the color, the rest
    generic from the least flexible sources), or None when it can't be paid."""
    if mv > len(sources):
        return None
    needed = [col for col, n in pips.items() for _ in range(n)]
    order = sorted(range(len(sources)), key=lambda j: len(sources[j]))
    used: set[int] = set()

    def assign(i: int) -> bool:
        if i == len(needed):
            return True
        for j in order:
            if j not in used and needed[i] in sources[j]:
                used.add(j)
                if assign(i + 1):
                    return True
                used.discard(j)
        return False

    if not assign(0):
        return None
    rest = [sources[j] for j in order if j not in used]
    generic = mv - len(needed)
    return rest[generic:] if generic > 0 else rest


# -- one game --------------------------------------------------------------------------------------------------------

def _play(deck: list[dict], commander: dict | None, deck_colors: set[str], rng: random.Random) -> dict:
    table = Table(deck, deck_colors, rng)
    creatures: list[dict] = []     # {"power", "subtypes", "ready", "ds"}
    anthem = 0
    tribe_anthems: list[tuple[str, int]] = []
    global_haste = False
    each_turn_drain = 0
    each_turn_tokens: list[tuple[int, int]] = []
    attack_tokens: list[tuple[int, int]] = []
    cheat = None
    damage = 0
    kill_turn = None
    commander_cast = None
    cast_count = 0

    def enter_creature(profile: dict, turn: int, attacking_now: bool = False) -> dict:
        c = {"power": profile["power"], "subtypes": profile["subtypes"], "ds": profile["double_strike"],
             "ready": turn if (profile["haste"] or global_haste or attacking_now) else turn + 1}
        creatures.append(c)
        return c

    def resolve(profile: dict, turn: int) -> int:
        """Apply a cast card's effects; returns damage dealt right now."""
        nonlocal anthem, global_haste, each_turn_drain, cheat
        if profile["creature"]:
            enter_creature(profile, turn)
        for count, power in profile["tokens"]:
            for _ in range(min(count, 12)):
                creatures.append({"power": power, "subtypes": set(), "ds": False, "ready": turn if global_haste else turn + 1})
        anthem += profile["anthem"]
        if profile["tribe_anthem"]:
            tribe_anthems.append(profile["tribe_anthem"])
        global_haste = global_haste or profile["global_haste"]
        each_turn_drain += profile["each_turn_drain"]
        each_turn_tokens.extend(profile["each_turn_tokens"])
        attack_tokens.extend(profile["attack_tokens"])
        if profile["cheat"]:
            cheat = profile["cheat"]
        return profile["drain"]

    def power_of(c: dict) -> int:
        bonus = anthem + sum(n for tribe, n in tribe_anthems if tribe in c["subtypes"])
        return max(0, c["power"] + bonus) * (2 if c["ds"] else 1)

    for turn in range(1, MAX_TURN + 1):
        table.begin_turn(turn)
        sources = table.mana_sources(turn)
        for count, power in each_turn_tokens:
            for _ in range(count):
                creatures.append({"power": power, "subtypes": set(), "ds": False, "ready": turn if global_haste else turn + 1})
        # commander first, then the best damage per mana
        if commander and commander_cast is None and commander["profile"]:
            paid = _pay(sources, commander["pips"], commander["mv"])
            if paid is not None:
                sources, commander_cast = paid, turn
                damage += resolve(commander["profile"], turn)
        castable = [c for c in table.hand if c.get("profile")]
        castable.sort(key=lambda c: -((c["profile"]["power"] + sum(p * n for n, p in c["profile"]["tokens"]) + c["profile"]["drain"]
                                       + 3 * (c["profile"]["anthem"] + (c["profile"]["tribe_anthem"] or ("", 0))[1])) / max(1, c["mv"])))
        pump = 0
        for c in castable:
            if c["profile"]["pump"] and len([x for x in creatures if x["ready"] <= turn]) < 2:
                continue
            paid = _pay(sources, c["pips"], c["mv"])
            if paid is None:
                continue
            sources = paid
            table.hand.remove(c)
            cast_count += 1
            if c["profile"]["pump"]:
                pump += c["profile"]["pump"]
            damage += resolve(c["profile"], turn)
        attackers = [c for c in creatures if c["ready"] <= turn]
        attack_damage = sum(power_of(c) + pump * (2 if c["ds"] else 1) for c in attackers)
        if attackers:
            for count, power in attack_tokens:
                for _ in range(count):
                    creatures.append({"power": power, "subtypes": set(), "ds": False, "ready": turn + 1})
            if cheat:
                options = [c for c in table.hand if c.get("profile") and c["profile"]["creature"] and ({t.lower() for t in c["profile"]["subtypes"]} & cheat)]
                if options:
                    best = max(options, key=lambda c: c["profile"]["power"])
                    table.hand.remove(best)
                    entering = enter_creature(best["profile"], turn, attacking_now=True)
                    attack_damage += power_of(entering)
                    damage += resolve({**best["profile"], "creature": False}, turn)
        damage += attack_damage + each_turn_drain
        if damage >= LIFE and kill_turn is None:
            kill_turn = turn
            break
    return {"kill_turn": kill_turn, "commander_turn": commander_cast, "cast": cast_count, "damage": damage}


def simulate_turn_clock(entries: list[CardEntry], games: int = GAMES_DEFAULT) -> dict | None:
    """Runs the Turn Clock goldfish on a Commander deck; None when there's no library to shuffle."""
    gameplay = gameplay_data_in_index()
    creature_names = [e.name for e in entries if "Creature" in (e.type_line or "")]
    stats = ensure_card_stats(creature_names)
    deck: list[dict] = []
    commander = None
    deck_colors: set[str] = set()
    estimated = 0
    for e in entries:
        info = gameplay.get(normalize_name(e.name)) or {}
        cost = e.mana_cost or info.get("mana_cost") or ""
        deck_colors |= set(_color_pips(cost))
        profile = _profile(e, info, stats)
        if profile and profile["estimated"]:
            estimated += 1
        pips = {col: max(1, int(-(-n // 1))) for col, n in _color_pips(cost).items()}
        if e.section == "commander":
            if commander is None:
                commander = {"profile": profile, "pips": pips, "mv": int(e.cmc or info.get("cmc") or 0), "name": e.name}
            continue
        card = _classify(e, info)
        card["profile"], card["pips"] = (profile if card["kind"] == "spell" else None), pips
        deck.extend([dict(card) for _ in range(e.quantity)])
    if len(deck) < 40:
        return None
    rng = random.Random(SEED)
    runs = [_play(deck, commander, deck_colors, rng) for _ in range(games)]
    kills = sorted(r["kill_turn"] for r in runs if r["kill_turn"] is not None)
    by_turn = {t: round(100.0 * sum(1 for k in kills if k <= t) / games, 1) for t in range(3, MAX_TURN + 1)}

    def percentile(p: float):
        index = int(games * p)
        return kills[index] if index < len(kills) else None

    return {
        "games": games,
        "kill_rate": round(100.0 * len(kills) / games, 1),
        "median_turn": percentile(0.5),
        "fast_turn": percentile(0.1),
        "slow_turn": percentile(0.9),
        "by_turn": by_turn,
        "estimated_power_cards": estimated,
        "commander_cast_rate": round(100.0 * sum(1 for r in runs if r["commander_turn"] is not None) / games, 1),
        "life": LIFE,
        "max_turn": MAX_TURN,
    }
