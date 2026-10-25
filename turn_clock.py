"""Turn Clock: a goldfish of how fast a Commander deck can kill ONE opponent who does nothing --
no blockers, no removal, no life gain -- starting from 40 life. It shuffles and plays the deck over
and over on top of the mana goldfish (mana_sim.Table: land drops, ramp, mulligans), casts the
creatures and damage effects it can afford each turn, attacks with everything, and records the turn
the opponent's life runs out.

It reads rules text with a set of patterns, not a full rules engine, so it is a ruler for comparing
builds, not a prediction of a real table. What it understands:
  - creatures: printed power (Scryfall), summoning sickness, haste, double strike, and the tokens a
    creature or spell makes when it enters or at a beginning-of-turn trigger
  - equipment (equip costs, +N/+N, haste, double strike, living weapon) and auras (+N/+N, haste)
  - static team boosts ("creatures you control get +1/+1", "each other Vampire you control gets +1/+1")
    and one-turn team pumps cast on the attack
  - direct damage and life loss to each opponent (ETB, spells, beginning-of-turn triggers), including
    ones that scale with the board (devotion, number of creatures, greatest or total power) and
    pingers (Hellrider's "whenever a creature you control attacks", Impact Tremors)
  - extra combat phases, one-shot and repeatable ("{3}{R}{R}: untap all creatures... additional combat")
  - a commander's own attack trigger: "whenever ~ attacks, create a token" and the "put a creature card
    from your hand onto the battlefield attacking" cheat (Kaalia)
  - combos the deck has already assembled (Commander Spellbook's "included" list) that win the game --
    infinite damage or life loss, an outright win, infinite combat -- once every piece is castable
What it ignores: repeatable activated abilities, planeswalkers, tutors, choices and targeting, and combos
that need a card the deck can't find on its own. A deck that wins in a way it can't read shows a slow or
missing clock -- the panel says so when most games never kill.
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
_EQUIP_RE = re.compile(r"^equip(?: [a-z ]+?)? ((?:\{[^}]+\})+)")
_EQUIP_BONUS_RE = re.compile(r"(?:equipped|enchanted) creature gets \+(\d+)/\+(\d+)")
_SCALING_RES = (
    re.compile(r"(?:each opponent|target opponent|target player) loses x life[^.]*?where x is ([^.]*)"),
    re.compile(r"(?:each opponent|target opponent) loses life equal to ([^.]*)"),
    re.compile(r"deals x damage to (?:each opponent|target opponent|target player)[^.]*?where x is ([^.]*)"),
    re.compile(r"deals damage equal to ([^.]*?) to (?:each opponent|target opponent|target player)"),
)
_COLOR_WORDS = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G"}
_ADDITIONAL_COMBAT_RE = re.compile(r"additional combat phase")
_PING_RE = re.compile(r"^whenever a creature you control attacks, ~ deals (\d+) damage to (?:the player|defending player|that player)")
_ETB_PING_RE = re.compile(r"^whenever (?:a|another) (?:other )?creature (?:you control )?enters(?: the battlefield)?(?: under your control)?, ~ (?:deals (\d+) damage to each opponent|causes each opponent to lose (\d+) life)")
_WIN_FEATURES = ("win the game", "loses the game", "infinite damage", "infinite lifeloss", "infinite combat")
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

def _scaling(descriptor: str):
    """What a "where X is ..." clause counts, as ("devotion", colors) | ("creatures", 0) | ("greatest_power", 0)
    | ("total_power", 0), or None when it isn't a board count we can read."""
    descriptor = descriptor.strip()
    devotion = re.search(r"devotion to ([a-z]+)(?: and ([a-z]+))?", descriptor)
    if devotion:
        return ("devotion", [_COLOR_WORDS[w] for w in devotion.groups() if w in _COLOR_WORDS])
    if "number of other creatures you control" in descriptor:
        return ("creatures", -1)
    if "number of creatures you control" in descriptor:
        return ("creatures", 0)
    if "greatest power among creatures you control" in descriptor:
        return ("greatest_power", 0)
    if "total power of creatures you control" in descriptor:
        return ("total_power", 0)
    return None


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
    low = re.sub(r"\bthis (?:creature|permanent|artifact|enchantment|equipment|aura|spell|vehicle)\b", "~", low)
    lines = [ln.strip() for ln in low.split("\n") if ln.strip()]
    is_creature = "Creature" in type_line
    is_permanent = categorize(type_line) not in ("Instants", "Sorceries")
    profile: dict = {"creature": is_creature, "tokens": [], "drain": 0, "each_turn_tokens": [], "each_turn_drain": 0,
                     "anthem": 0, "tribe_anthem": None, "pump": 0, "global_haste": False, "haste": False, "double_strike": False,
                     "attack_tokens": [], "cheat": None, "estimated": False, "subtypes": set(), "power": 0,
                     "scaling": None, "extra_combat": None, "ping": 0, "etb_ping": 0, "equipment": None, "aura": None}
    is_equipment, is_aura = "Equipment" in type_line, "Aura" in type_line
    equip_bonus = equip_haste = equip_cost = 0
    equip_double = False
    living_weapon = False
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
        if _ADDITIONAL_COMBAT_RE.search(ln):
            cost = 0
            if activated:  # "{3}{R}{R}: untap all creatures you control. After this phase, there is an additional combat phase"
                cost = sum(int(sym) if sym.isdigit() else 1 for sym in re.findall(r"\{([^}]+)\}", ln.split(":")[0]))
            if not (triggered and "attacks" in ln):
                profile["extra_combat"] = {"cost": cost, "repeat": activated}
        etb = _ETB_PING_RE.match(ln)
        if etb:
            profile["etb_ping"] += int(etb.group(1) or etb.group(2))
        ping = _PING_RE.match(ln)
        if ping:
            profile["ping"] += int(ping.group(1))
        if is_equipment or is_aura:
            equip = _EQUIP_RE.match(ln)
            if equip:
                equip_cost = sum(int(sym) if sym.isdigit() else 1 for sym in re.findall(r"\{([^}]+)\}", equip.group(1)))
            elif ln.startswith("equip"):
                equip_cost = equip_cost or 3
            bonus = _EQUIP_BONUS_RE.search(ln)
            if bonus:
                equip_bonus += int(bonus.group(1))
            if re.search(r"(?:equipped|enchanted) creature [^.]*\bhaste\b", ln):
                equip_haste = 1
            if re.search(r"(?:equipped|enchanted) creature [^.]*double strike", ln):
                equip_double = True
        if ln.startswith("living weapon"):
            living_weapon = True
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
        if triggered and not re.match(r"when(?:ever)? ~ enters", ln):  # not "whenever a creature enters", which happens many times
            continue
        # a spell's own effect line, or an enters-the-battlefield trigger
        tok = _tokens_in(ln)
        if tok and "until end of turn" not in ln:
            profile["tokens"].append(tok)
        drain = _DRAIN_RE.search(ln)
        if drain and (not triggered or re.match(r"when(?:ever)? ~ enters", ln)):
            profile["drain"] += int(next(g for g in drain.groups() if g))
        for rx in _SCALING_RES:
            scaled = rx.search(ln)
            if scaled and _scaling(scaled.group(1)):
                profile["scaling"] = _scaling(scaled.group(1))
                break
        anthem = _ANTHEM_RE.search(ln)
        if anthem and not triggered and is_permanent:
            profile["anthem"] += int(anthem.group(1))
        elif anthem and "until end of turn" in ln and not is_permanent:
            profile["pump"] += int(anthem.group(1))
        tribe = _TRIBE_ANTHEM_RE.search(ln)
        if tribe and not triggered and is_permanent:
            profile["tribe_anthem"] = (tribe.group(1).capitalize(), int(tribe.group(2)))
    if is_equipment and (equip_bonus or equip_haste or equip_double or living_weapon):
        profile["equipment"] = {"bonus": equip_bonus, "haste": bool(equip_haste), "double": equip_double,
                                "equip": equip_cost or 3, "living_weapon": living_weapon}
    if is_aura and (equip_bonus or equip_haste or equip_double):
        profile["aura"] = {"bonus": equip_bonus, "haste": bool(equip_haste), "double": equip_double}
    useful = profile["scaling"] or profile["extra_combat"] or profile["ping"] or profile["etb_ping"] or profile["equipment"] or profile["aura"] \
        or (is_creature and profile["power"] > 0) or profile["tokens"] or profile["drain"] or profile["each_turn_tokens"] \
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

def _name_keys(name: str) -> set[str]:
    return {normalize_name(name), normalize_name(name.split(" // ")[0])}


def _play(deck: list[dict], commander: dict | None, deck_colors: set[str], combos: list[dict], rng: random.Random) -> dict:
    table = Table(deck, deck_colors, rng)
    creatures: list[dict] = []     # {"power", "subtypes", "ready", "ds", "bonus", "haste_granted"}
    equipment: list[dict] = []     # {"bonus", "haste", "double", "equip", "on"}
    on_battlefield: set[str] = set()
    devotion = {col: 0.0 for col in "WUBRG"}
    anthem = 0
    tribe_anthems: list[tuple[str, int]] = []
    global_haste = False
    each_turn_drain = 0
    each_turn_tokens: list[tuple[int, int]] = []
    attack_tokens: list[tuple[int, int]] = []
    attack_ping = 0
    etb_ping = 0
    extra_combat_once = 0
    extra_combat_costs: list[int] = []
    cheat = None
    damage = 0
    kill_turn = None
    combo_turn = None
    combo_fired = False
    commander_cast = None
    cast_count = 0

    def new_creature(power: int, subtypes: set, ds: bool, ready: int) -> dict:
        nonlocal damage
        c = {"power": power, "subtypes": subtypes, "ds": ds, "ready": ready, "bonus": 0}
        creatures.append(c)
        damage += etb_ping  # Impact Tremors / Purphoros: each creature entering hurts
        return c

    def token_ready(turn: int) -> int:
        return turn if global_haste else turn + 1

    def power_of(c: dict) -> int:
        bonus = anthem + c["bonus"] + sum(n for tribe, n in tribe_anthems if tribe in c["subtypes"])
        return max(0, c["power"] + bonus) * (2 if c["ds"] else 1)

    def attach(item: dict, creature: dict, turn: int) -> None:
        if item.get("on") is creature:
            return
        creature["bonus"] += item["bonus"]
        item["on"] = creature
        creature["ds"] = creature["ds"] or item["double"]
        if item["haste"]:
            creature["ready"] = min(creature["ready"], turn)

    def best_carrier(turn: int):
        able = [c for c in creatures if c["ready"] <= turn]
        pool = able or creatures
        return max(pool, key=power_of) if pool else None

    def scaling_value(kind: tuple) -> int:
        what, arg = kind
        if what == "devotion":
            return int(sum(devotion[col] for col in arg))
        if what == "creatures":
            return max(0, len(creatures) + arg)
        if what == "greatest_power":
            return max((power_of(c) for c in creatures), default=0)
        return sum(power_of(c) for c in creatures)

    def resolve(card: dict, turn: int) -> int:
        """Apply a cast card's effects; returns damage dealt right now."""
        nonlocal anthem, global_haste, each_turn_drain, cheat, attack_ping, etb_ping, extra_combat_once
        profile = card["profile"]
        on_battlefield.update(_name_keys(card.get("name", "")))
        if card["permanent"]:
            for col, n in card["devo"].items():
                devotion[col] += n
        gain = profile["drain"]
        if profile["creature"]:
            new_creature(profile["power"], profile["subtypes"], profile["double_strike"],
                         turn if (profile["haste"] or global_haste) else turn + 1)
        for count, power in profile["tokens"]:
            for _ in range(min(count, 12)):
                new_creature(power, set(), False, token_ready(turn))
        anthem += profile["anthem"]
        if profile["tribe_anthem"]:
            tribe_anthems.append(profile["tribe_anthem"])
        global_haste = global_haste or profile["global_haste"]
        each_turn_drain += profile["each_turn_drain"]
        each_turn_tokens.extend(profile["each_turn_tokens"])
        attack_tokens.extend(profile["attack_tokens"])
        attack_ping += profile["ping"]
        etb_ping += profile["etb_ping"]
        if profile["cheat"]:
            cheat = profile["cheat"]
        ec = profile["extra_combat"]
        if ec:
            if ec["repeat"]:
                extra_combat_costs.append(ec["cost"])
            else:
                extra_combat_once += 1
        eq = profile["equipment"]
        if eq:
            item = {**eq, "on": None, "bonus": eq["bonus"]}
            equipment.append(item)
            if eq["living_weapon"]:
                germ = new_creature(0, set(), False, token_ready(turn))
                attach(item, germ, turn)
        aura = profile["aura"]
        if aura:
            carrier = best_carrier(turn)
            if carrier is not None:
                attach({**aura, "on": None, "equip": 0}, carrier, turn)
        if profile["scaling"]:
            gain += scaling_value(profile["scaling"])
        return gain

    def castable_value(card: dict) -> float:
        p = card["profile"]
        eq = p["equipment"] or p["aura"] or {}
        value = p["power"] + sum(pw * n for n, pw in p["tokens"]) + p["drain"] + 3 * (p["anthem"] + (p["tribe_anthem"] or ("", 0))[1])
        value += 1.5 * eq.get("bonus", 0) + (2 if eq.get("haste") else 0) + (3 if p["extra_combat"] else 0) + 4 * p["ping"] + 4 * p["etb_ping"]
        value += 5 if p["scaling"] else 0
        return value / max(1, card["mv"])

    def combo_pieces(combo: dict, turn: int, sources: list):
        """(cards to cast, sources left) when every piece is on the battlefield or castable this turn, else None."""
        casts: list[dict] = []
        left = sources
        for piece in combo["pieces"]:
            if piece in on_battlefield or piece in table.played:
                continue
            if commander and commander_cast is None and piece in commander["keys"]:
                paid = _pay(left, commander["pips"], commander["mv"])
                if paid is None:
                    return None
                left = paid
                casts.append(commander)
                continue
            card = next((c for c in table.hand if piece in _name_keys(c.get("name", ""))), None)
            if card is None:
                return None
            paid = _pay(left, card["pips"], card["mv"]) if card["kind"] != "land" else left
            if paid is None:
                return None
            left = paid
            casts.append(card)
        return casts, left

    for turn in range(1, MAX_TURN + 1):
        table.begin_turn(turn)
        sources = table.mana_sources(turn)
        if combo_turn == turn:
            kill_turn = turn
            break
        for count, power in each_turn_tokens:
            for _ in range(count):
                new_creature(power, set(), False, token_ready(turn))
        # a known combo that wins the game: cast whatever pieces are missing and it goes off (next turn if a
        # creature piece needs to lose summoning sickness first)
        for combo in combos:
            found = combo_pieces(combo, turn, sources)
            if found is None:
                continue
            casts, sources = found
            sick = False
            for card in casts:
                if card is commander:
                    commander_cast = turn
                else:
                    table.hand.remove(card)
                    if card["kind"] == "land":
                        table.played.update(_name_keys(card.get("name", "")))
                on_battlefield.update(_name_keys(card.get("name", "") if card is not commander else commander["name"]))
                sick = sick or (card.get("creature") and not (card.get("profile") or {}).get("haste") and not global_haste)
                if card.get("profile"):
                    damage += resolve(card, turn)
                cast_count += 1
            combo_fired = True
            if sick:
                combo_turn = turn + 1
            else:
                kill_turn = turn
            break
        if kill_turn:
            break
        # commander first, then the best damage per mana
        if commander and commander_cast is None and commander["profile"]:
            paid = _pay(sources, commander["pips"], commander["mv"])
            if paid is not None:
                sources, commander_cast = paid, turn
                damage += resolve(commander, turn)
        castable = [c for c in table.hand if c.get("profile")]
        castable.sort(key=lambda c: -castable_value(c))
        pump = 0
        for c in castable:
            p = c["profile"]
            if p["pump"] and len([x for x in creatures if x["ready"] <= turn]) < 2:
                continue
            if (p["aura"]) and not creatures:
                continue
            paid = _pay(sources, c["pips"], c["mv"])
            if paid is None:
                continue
            sources = paid
            table.hand.remove(c)
            cast_count += 1
            if p["pump"]:
                pump += p["pump"]
            damage += resolve(c, turn)
        # equip: move each unattached piece of equipment onto the best creature we can pay for
        for item in equipment:
            if item["on"] is None and creatures:
                paid = _pay(sources, {}, item["equip"])
                if paid is not None:
                    sources = paid
                    attach(item, best_carrier(turn), turn)
        attackers = [c for c in creatures if c["ready"] <= turn]
        attack_damage = sum(power_of(c) + pump * (2 if c["ds"] else 1) for c in attackers)
        if attackers:
            for count, power in attack_tokens:
                for _ in range(count):
                    new_creature(power, set(), False, turn + 1)
            if cheat:
                options = [c for c in table.hand if c.get("profile") and c["profile"]["creature"]
                           and ({t.lower() for t in c["profile"]["subtypes"]} & cheat)]
                if options:
                    best = max(options, key=lambda c: c["profile"]["power"])
                    table.hand.remove(best)
                    card = {**best, "profile": {**best["profile"], "haste": True}}
                    entering = new_creature(best["profile"]["power"], best["profile"]["subtypes"], best["profile"]["double_strike"], turn)
                    attack_damage += power_of(entering)
                    damage += resolve({**card, "profile": {**card["profile"], "creature": False}}, turn)
            attack_damage += attack_ping * len(attackers)
            extra = extra_combat_once
            for cost in extra_combat_costs:  # Aggravated Assault style: pay again and again
                while extra < 3 + extra_combat_once:
                    paid = _pay(sources, {}, cost)
                    if paid is None:
                        break
                    sources = paid
                    extra += 1
            attack_damage *= 1 + min(extra, 4)
        damage += attack_damage + each_turn_drain
        if damage >= LIFE:
            kill_turn = turn
            break
    return {"kill_turn": kill_turn, "commander_turn": commander_cast, "cast": cast_count, "damage": damage,
            "combo_kill": combo_fired and kill_turn is not None}


def _combos_for(combos_included: list[dict] | None) -> list[dict]:
    """The deck's already-assembled combos that end the game (infinite damage or life loss, an outright
    win, infinite combat), as {"pieces": {normalized names}} sorted fewest pieces first."""
    out = []
    for combo in combos_included or []:
        produces = " ".join(combo.get("produces") or []).lower()
        if any(feature in produces for feature in _WIN_FEATURES) and combo.get("uses"):
            out.append({"pieces": [normalize_name(n) for n in combo["uses"]]})
    return sorted(out, key=lambda c: len(c["pieces"]))


def simulate_turn_clock(entries: list[CardEntry], games: int = GAMES_DEFAULT, combos_included: list[dict] | None = None) -> dict | None:
    """Runs the Turn Clock goldfish on a Commander deck; None when there's no library to shuffle.
    `combos_included`: the deck's assembled combos as the Analyze stats report them (uses/produces)."""
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
        type_line = e.type_line or info.get("type_line") or ""
        base = {"name": e.name, "pips": pips, "devo": _color_pips(cost), "creature": "Creature" in type_line,
                "permanent": categorize(type_line) not in ("Instants", "Sorceries")}
        if e.section == "commander":
            if commander is None:
                commander = {**base, "profile": profile, "mv": int(e.cmc or info.get("cmc") or 0), "kind": "spell", "keys": _name_keys(e.name)}
            continue
        card = _classify(e, info)
        card.update(base)
        card["profile"] = profile if card["kind"] == "spell" else None
        deck.extend([dict(card) for _ in range(e.quantity)])
    if len(deck) < 40:
        return None
    combos = _combos_for(combos_included)
    rng = random.Random(SEED)
    runs = [_play(deck, commander, deck_colors, combos, rng) for _ in range(games)]
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
        "combos": len(combos),
        "combo_kill_rate": round(100.0 * sum(1 for r in runs if r["combo_kill"]) / games, 1),
        "life": LIFE,
        "max_turn": MAX_TURN,
    }
