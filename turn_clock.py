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
  - card draw: spells cast with the mana the damage plays leave, engines drawing every turn, ETB draws on creatures
  - the whole table: three opponents at 40, attackers hit one at a time, each-opponent effects hit all three
  - commander damage: 21 from the commander alone ends one opponent
  - tutors: cast for their mana, fetching the one missing piece of the nearest combo, else the best damage card
Resilience (simulate_resilience) reruns the clock with a board wipe on turn 6 (survived if a protection spell is in hand,
reversed by reanimation), with spot removal taking the best creature 40% of turns, both together, and with the commander removed each
time it is cast, twice.
  - planeswalkers: cast, then one loyalty ability a turn (tokens, drain, damage, draw, an emblem for creatures), with their
    printed loyalty from Scryfall; they survive a creature wipe
  - repeatable abilities that cost only mana and {T}: token makers, pingers, firebreathing, card draw -- spending the mana the
    plays leave over, and tapping a creature only when that is worth more than its attack
What it ignores: abilities with other costs (sacrifice, discard, life), choices and targeting, and combos
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
from deck_builder import _card_tag_index, _color_pips
from brewlist_core import budget_alt_data_in_index
from mana_sim import SEED, Table, _classify, _opening_hand  # noqa: F401 -- _opening_hand re-exported for tests

_CORE_DIR = os.path.dirname(os.path.abspath(__file__))
CARD_STATS_PATH = os.path.join(_CORE_DIR, "data", "card_stats.json")

GAMES_DEFAULT = 2000
MAX_TURN = 12
LIFE = 40
OPPONENTS = 3
COMMANDER_DAMAGE = 21
WIPE_TURN = 6
SPOT_CHANCE = 0.4

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
_TUTOR_LABELS = {"tutor-to-hand", "tutor-card", "tutor-creature", "tutor-artifact", "tutor-mv", "tutor-to-top"}
_LOYALTY_RE = re.compile(r"^\[?([+\u2212\-]?\d+|[+\u2212\-]x)\]?: (.+)")
_DRAW_N_RE = re.compile(r"\bdraw (a|an|one|two|three|four|\d+) cards?")
_EMBLEM_RE = re.compile(r"you get an emblem with")
_PUMP_SELF_RE = re.compile(r"~ gets? \+(\d+)/\+0 until end of turn")
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


def ensure_card_stats(names: list[str], need_loyalty: set[str] | frozenset = frozenset()) -> dict[str, dict]:
    """{normalized name: {"p", "t"}} for creatures, fetched once from Scryfall's collection endpoint and
    cached in data/card_stats.json (a card's printed power never changes). Network failures leave a
    name out; callers estimate a power for it."""
    cache = _read_stats()
    need = [n for n in dict.fromkeys(names)
            if normalize_name(n) not in cache or (n in need_loyalty and "l" not in cache[normalize_name(n)])]
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
            stat = {"p": face.get("power"), "t": face.get("toughness"), "l": card.get("loyalty") or face.get("loyalty")}
            cache[normalize_name(card.get("name", ""))] = stat
            cache[normalize_name((card.get("name") or "").split(" // ")[0])] = stat
        for n in chunk:
            cache.setdefault(normalize_name(n), {"p": None, "t": None, "l": None})
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

def _effects_in(effect: str) -> dict:
    """What one ability's effect text does for the clock: tokens made, damage or life loss to opponents (each of them, or
    one), cards drawn, a power pump on itself, or an emblem that gives creatures +N."""
    out = {"tokens": [], "each": 0, "one": 0, "draw": 0, "pump": 0, "emblem": 0}
    tok = _tokens_in(effect)
    if tok:
        out["tokens"].append(tok)
    each = re.search(r"each opponent loses (\d+) life|deals (\d+) damage to each opponent", effect)
    if each:
        out["each"] = int(next(g for g in each.groups() if g))
    one = re.search(r"deals (\d+) damage to (?:any target|target (?:player|opponent)(?: or planeswalker)?)|target (?:player|opponent) loses (\d+) life", effect)
    if one:
        out["one"] = int(next(g for g in one.groups() if g))
    draw = _DRAW_N_RE.search(effect)
    if draw and "each player" not in effect and "target player" not in effect and "target opponent" not in effect:
        out["draw"] = _num(draw.group(1))
    pump = _PUMP_SELF_RE.search(effect)
    if pump:
        out["pump"] = int(pump.group(1))
    if _EMBLEM_RE.search(effect):
        anthem = _ANTHEM_RE.search(effect)
        if anthem:
            out["emblem"] = int(anthem.group(1))
    return out


def _has_effect(e: dict) -> bool:
    return bool(e["tokens"] or e["each"] or e["one"] or e["draw"] or e["pump"] or e["emblem"])


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
                     "scaling": None, "extra_combat": None, "ping": 0, "etb_ping": 0, "loyalty": None, "activated": [], "equipment": None, "aura": None}
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
    is_planeswalker = "Planeswalker" in type_line
    loyalty_abilities = []
    for ln in lines:
        loyal = _LOYALTY_RE.match(ln) if is_planeswalker else None
        if loyal:
            cost_text = loyal.group(1).replace("\u2212", "-")
            cost = 3 if cost_text.lstrip("+-") == "x" else int(cost_text.replace("+", ""))
            effects = _effects_in(loyal.group(2))
            if _has_effect(effects):
                loyalty_abilities.append({"cost": cost, **effects})
            continue
        ln = _ABILITY_WORD_RE.sub("", ln)
        if not is_planeswalker and ":" in ln and not ln.startswith(("when", "whenever", "at the beginning")) and not ln.startswith(("equip", "cycling", "channel")):
            cost_part, effect_part = ln.split(":", 1)
            if not effect_part.strip().startswith("add ") and not re.search(r"sacrifice|discard|exile|pay \d+ life|remove|\bx\b|\{x\}", cost_part):
                symbols = re.findall(r"\{([^}]+)\}", cost_part)
                mana = sum(int(s) if s.isdigit() else 1 for s in symbols if s not in ("t", "q"))
                effects = _effects_in(effect_part)
                if _has_effect(effects) and not re.search(r"\bx\b", effect_part):
                    profile["activated"].append({"mana": mana, "tap": "{t}" in cost_part, **effects})
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
    if loyalty_abilities:
        raw = (stats.get(normalize_name(name)) or {}).get("l")
        profile["loyalty"] = {"start": int(raw) if raw is not None and str(raw).isdigit() else 4, "abilities": loyalty_abilities}
    if is_equipment and (equip_bonus or equip_haste or equip_double or living_weapon):
        profile["equipment"] = {"bonus": equip_bonus, "haste": bool(equip_haste), "double": equip_double,
                                "equip": equip_cost or 3, "living_weapon": living_weapon}
    if is_aura and (equip_bonus or equip_haste or equip_double):
        profile["aura"] = {"bonus": equip_bonus, "haste": bool(equip_haste), "double": equip_double}
    useful = profile["loyalty"] or profile["activated"] or profile["scaling"] or profile["extra_combat"] or profile["ping"] or profile["etb_ping"] or profile["equipment"] or profile["aura"] \
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


def _play(deck: list[dict], commander: dict | None, deck_colors: set[str], combos: list[dict], rng: random.Random,
          scenario: str | dict | None = None) -> dict:
    """One goldfish game. `scenario` is None or a dict of what the opponents do: {"wipe": turn} (every creature dies at the
    start of that turn unless a protection spell is in hand), {"spot": p} (from turn 3, each turn with chance p your best
    creature is removed), {"cmd": n} (the commander is removed at the end of each of the first n turns it is cast).
    "wipe" and "commander" are shorthand for the first and third."""
    plan = {"wipe": WIPE_TURN} if scenario == "wipe" else {"cmd": 2} if scenario == "commander" else dict(scenario or {})
    wipe_turn, spot_chance = plan.get("wipe"), plan.get("spot", 0.0)
    table = Table(deck, deck_colors, rng)
    table.auto_draw = False  # the clock spends mana on damage first and card draw with what is left
    creatures: list[dict] = []     # {"power", "subtypes", "ready", "ds", "bonus", "haste_granted"}
    equipment: list[dict] = []     # {"bonus", "haste", "double", "equip", "on"}
    on_battlefield: set[str] = set()
    permanents: list[dict] = []    # {"card", "profile", "creature": its creature dict or None, "commander"}
    graveyard: list[dict] = []     # creature cards that died (for reanimation)
    commander_tax = 0
    emblem_bonus = 0
    pw_loyalty: dict[int, int] = {}
    wiped = False
    removals_left = plan.get("cmd", 0)
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
    damage = 0          # dealt to one opponent
    table_damage = 0    # dealt across all three (an each-opponent effect hits every one of them)
    cmd_damage = 0      # combat damage the commander has dealt to one opponent
    kill_turn = None
    table_turn = None
    combo_turn = None
    combo_fired = False
    commander_cast = None
    cast_count = 0

    def new_creature(power: int, subtypes: set, ds: bool, ready: int) -> dict:
        nonlocal damage, table_damage
        c = {"power": power, "subtypes": subtypes, "ds": ds, "ready": ready, "bonus": 0}
        creatures.append(c)
        damage += etb_ping  # Impact Tremors / Purphoros: each creature entering hurts
        table_damage += etb_ping * OPPONENTS
        return c

    def refresh() -> None:
        """Recompute every ongoing effect from the permanents on the battlefield (so destroying one removes its effects)."""
        nonlocal anthem, global_haste, each_turn_drain, cheat, attack_ping, etb_ping
        profiles = [p["profile"] for p in permanents]
        anthem = sum(p["anthem"] for p in profiles)
        tribe_anthems[:] = [p["tribe_anthem"] for p in profiles if p["tribe_anthem"]]
        global_haste = any(p["global_haste"] for p in profiles)
        each_turn_drain = sum(p["each_turn_drain"] for p in profiles)
        each_turn_tokens[:] = [tok for p in profiles for tok in p["each_turn_tokens"]]
        attack_tokens[:] = [tok for p in profiles for tok in p["attack_tokens"]]
        attack_ping = sum(p["ping"] for p in profiles)
        etb_ping = sum(p["etb_ping"] for p in profiles)
        extra_combat_costs[:] = [p["extra_combat"]["cost"] for p in profiles if p["extra_combat"] and p["extra_combat"]["repeat"]]
        cheat = next((p["cheat"] for p in profiles if p["cheat"]), None)

    def devotion_to(colors: list[str]) -> int:
        return int(sum(p["card"]["devo"].get(col, 0) for p in permanents for col in colors))

    def kill_permanent(perm: dict) -> None:
        nonlocal commander_cast, commander_tax
        permanents.remove(perm)
        dead = perm["creature"]
        if dead is not None and dead in creatures:
            creatures.remove(dead)
        for item in equipment:
            if item["on"] is dead:
                item["on"] = None
        on_battlefield.difference_update(_name_keys(perm["card"].get("name", "")))
        if perm["commander"]:
            commander_cast, commander_tax = None, commander_tax + 2  # back to the command zone; recasting costs {2} more
        elif perm["card"].get("creature"):
            graveyard.append(perm["card"])

    def wipe_board() -> None:
        for perm in [p for p in permanents if p["card"].get("creature") or p["profile"]["aura"]]:
            kill_permanent(perm)
        creatures.clear()  # tokens go too
        for item in equipment:
            item["on"] = None
        refresh()

    def token_ready(turn: int) -> int:
        return turn if global_haste else turn + 1

    def power_of(c: dict) -> int:
        bonus = anthem + emblem_bonus + c["bonus"] + sum(n for tribe, n in tribe_anthems if tribe in c["subtypes"])
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
            return devotion_to(arg)
        if what == "creatures":
            return max(0, len(creatures) + arg)
        if what == "greatest_power":
            return max((power_of(c) for c in creatures), default=0)
        return sum(power_of(c) for c in creatures)

    def resolve(card: dict, turn: int, creature: dict | None = None) -> int:
        """Apply a cast card's effects; returns damage dealt right now. `creature`: one already put onto the battlefield."""
        nonlocal extra_combat_once
        profile = card["profile"]
        on_battlefield.update(_name_keys(card.get("name", "")))
        if card.get("draw"):  # an enters-the-battlefield or spell draw on a card the clock casts for its damage
            table.draw(card["draw"]["net"])
            if card["draw"]["engine"]:
                table.engines += 1
        gain = profile["drain"]
        if card["permanent"]:
            permanents.append({"card": card, "profile": profile, "creature": creature, "commander": card is commander})
            refresh()  # before anything enters, so a haste or anthem permanent already counts for its own tokens
        if profile["creature"]:
            made = new_creature(profile["power"], profile["subtypes"], profile["double_strike"],
                                turn if (profile["haste"] or global_haste) else turn + 1)
            if card["permanent"]:
                permanents[-1]["creature"] = made
        for count, power in profile["tokens"]:
            for _ in range(min(count, 12)):
                new_creature(power, set(), False, token_ready(turn))
        ec = profile["extra_combat"]
        if ec and not ec["repeat"]:
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

    def deal_each(amount: int) -> None:
        """Damage or life loss to each opponent: it counts once against one opponent and three times across the table."""
        nonlocal damage, table_damage
        damage += amount
        table_damage += amount * OPPONENTS

    def effect_value(a: dict) -> float:
        """Rough worth of one ability use, in damage: tokens as future attackers, drain as table damage, an emblem as a lot."""
        return (sum(n * pw for n, pw in a["tokens"]) * 1.5 + a["each"] * 2 + a["one"] + a["draw"] * 1.5 + a["pump"] + a["emblem"] * 8)

    def apply_effects(a: dict, turn: int) -> None:
        nonlocal damage, table_damage, emblem_bonus
        for count, power in a["tokens"]:
            for _ in range(min(count, 12)):
                new_creature(power, set(), False, token_ready(turn))
        if a["each"]:
            deal_each(a["each"])
        if a["one"]:
            damage += a["one"]
            table_damage += a["one"]
        if a["draw"]:
            table.draw(a["draw"])
        emblem_bonus += a["emblem"]

    def castable_value(card: dict) -> float:
        p = card["profile"]
        eq = p["equipment"] or p["aura"] or {}
        value = p["power"] + sum(pw * n for n, pw in p["tokens"]) + p["drain"] + 3 * (p["anthem"] + (p["tribe_anthem"] or ("", 0))[1])
        value += 1.5 * eq.get("bonus", 0) + (2 if eq.get("haste") else 0) + (3 if p["extra_combat"] else 0) + 4 * p["ping"] + 4 * p["etb_ping"]
        value += 5 if p["scaling"] else 0
        if p["loyalty"]:
            value += max(effect_value(a) for a in p["loyalty"]["abilities"]) * 3
        value += sum(effect_value(a) for a in p["activated"]) * 2
        return value / max(1, card["mv"])

    def combo_pieces(combo: dict, turn: int, sources: list):
        """(cards to cast, sources left) when every piece is on the battlefield or castable this turn, else None."""
        casts: list[dict] = []
        left = sources
        for piece in combo["pieces"]:
            if piece in on_battlefield or piece in table.played:
                continue
            if commander and commander_cast is None and piece in commander["keys"]:
                paid = _pay(left, commander["pips"], commander["mv"] + commander_tax)
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
        if wipe_turn and turn == wipe_turn and not wiped:
            wiped = True
            guard = next((c for c in table.hand if c.get("protect")), None)
            paid = _pay(sources, guard["pips"], guard["mv"]) if guard and creatures else None
            if paid is not None:  # a protection spell in hand: the board survives
                sources = paid
                table.hand.remove(guard)
            else:
                wipe_board()
        if spot_chance and turn >= 3 and creatures and rng.random() < spot_chance:
            doomed = max(creatures, key=power_of)  # an opponent's removal spell takes your best creature
            perm = next((p for p in permanents if p["creature"] is doomed), None)
            if perm:
                kill_permanent(perm)
            else:
                creatures.remove(doomed)
                for item in equipment:
                    if item["on"] is doomed:
                        item["on"] = None
            refresh()
        if combo_turn == turn:
            kill_turn = kill_turn or turn
            table_turn = turn
            break
        for count, power in each_turn_tokens:
            for _ in range(count):
                new_creature(power, set(), False, token_ready(turn))
        # a known combo that wins the game: cast whatever pieces are missing and it goes off (next turn if a
        # creature piece needs to lose summoning sickness first)
        def try_combos() -> bool:
            nonlocal sources, commander_cast, cast_count, combo_fired, combo_turn, kill_turn, table_turn
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
                        deal_each(resolve(card, turn))
                    cast_count += 1
                combo_fired = True
                if sick:
                    combo_turn = turn + 1
                else:
                    kill_turn = kill_turn or turn
                    table_turn = turn
                return True
            return False

        def missing_pieces(combo: dict) -> list[str]:
            return [p for p in combo["pieces"] if p not in on_battlefield and p not in table.played
                    and not any(p in _name_keys(c.get("name", "")) for c in table.hand)
                    and not (commander and commander_cast is None and p in commander["keys"])]

        def tutor_target(card: dict):
            """What a tutor would fetch: the one missing piece of the nearest combo, else the best damage card."""
            allowed = card["tutor"]["types"]
            for combo in combos:
                missing = missing_pieces(combo)
                if len(missing) == 1:
                    hit = next((c for c in table.library if missing[0] in _name_keys(c.get("name", ""))), None)
                    if hit is not None:
                        return hit
            pool = [c for c in table.library if c.get("profile") and (allowed is None or any(t in c["type_line"] for t in allowed))]
            return max(pool, key=castable_value, default=None)

        def cast_tutors() -> None:
            nonlocal sources, cast_count
            for card in sorted((c for c in table.hand if c.get("tutor")), key=lambda c: c["mv"]):
                target = tutor_target(card)
                if target is None:
                    continue
                paid = _pay(sources, card["pips"], card["mv"])
                if paid is None:
                    continue
                sources = paid
                table.hand.remove(card)
                table.library.remove(target)
                table.hand.append(target)
                cast_count += 1

        try_combos()
        if table_turn:
            break
        if combos or any(c.get("tutor") for c in table.hand):
            cast_tutors()
            try_combos()
            if table_turn:
                break
        # bring creatures back from the graveyard (only ever matters after something has died)
        for spell in [c for c in table.hand if c.get("reanimate")]:
            if not graveyard:
                break
            paid = _pay(sources, spell["pips"], spell["mv"])
            if paid is None:
                continue
            sources = paid
            table.hand.remove(spell)
            cast_count += 1
            returning = list(graveyard) if spell["reanimate"] == "mass" else [max(graveyard, key=lambda c: c["profile"]["power"] if c.get("profile") else 0)]
            for back in returning:
                graveyard.remove(back)
                if back.get("profile"):
                    deal_each(resolve(back, turn))
        # commander first, then the best damage per mana
        if commander and commander_cast is None and commander["profile"]:
            paid = _pay(sources, commander["pips"], commander["mv"] + commander_tax)
            if paid is not None:
                sources, commander_cast = paid, turn
                before = len(creatures)
                deal_each(resolve(commander, turn))
                if commander["profile"]["creature"]:
                    creatures[before]["cmd"] = True
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
            deal_each(resolve(c, turn))
        # equip: move each unattached piece of equipment onto the best creature we can pay for
        for item in equipment:
            if item["on"] is None and creatures:
                paid = _pay(sources, {}, item["equip"])
                if paid is not None:
                    sources = paid
                    attach(item, best_carrier(turn), turn)
        # planeswalkers: one loyalty ability each, the most valuable one the loyalty can pay for
        for perm in [p for p in permanents if p["profile"]["loyalty"]]:
            lo = perm["profile"]["loyalty"]
            current = pw_loyalty.setdefault(id(perm), lo["start"])
            options = [a for a in lo["abilities"] if current + a["cost"] >= 0]
            if options:
                best = max(options, key=lambda a: (effect_value(a), a["cost"]))
                pw_loyalty[id(perm)] = current + best["cost"]
                apply_effects(best, turn)
        # repeatable activated abilities spend the mana the plays left over; a creature's {T} ability is used only
        # when it is worth more than the creature's attack
        pump_extra = 0
        uses = []
        for perm in permanents:
            for a in perm["profile"]["activated"]:
                uses.append((effect_value(a) / max(0.5, a["mana"]), perm, a))
        activations = 0
        for _, perm, a in sorted(uses, key=lambda u: -u[0]):
            body = perm["creature"]
            if a["tap"]:
                if body is not None and (body["ready"] > turn or effect_value(a) < power_of(body)):
                    continue
                repeats = 1
            else:
                repeats = 6
            for _ in range(repeats):
                if activations >= 12:
                    break
                paid = _pay(sources, {}, a["mana"]) if a["mana"] else sources
                if paid is None:
                    break
                sources = paid
                activations += 1
                if a["tap"] and body is not None:
                    body["tapped"] = turn
                if a["pump"]:
                    if body is None or body["ready"] > turn or body.get("tapped") == turn:
                        break
                    pump_extra += a["pump"] * (2 if body["ds"] else 1)
                apply_effects(a, turn)
        extra = 0
        spent = table.cast_draw_spells(len(sources))  # card draw with the mana the damage plays left over
        sources = sources[spent:]
        attackers = [c for c in creatures if c["ready"] <= turn and c.get("tapped") != turn]
        attack_damage = sum(power_of(c) + pump * (2 if c["ds"] else 1) for c in attackers) + pump_extra
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
                    deal_each(resolve({**card, "profile": {**card["profile"], "creature": False}}, turn, creature=entering))
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
        table_damage += attack_damage + each_turn_drain * OPPONENTS  # attackers hit one opponent at a time
        if attackers:
            cmd_damage += sum(power_of(c) + pump * (2 if c["ds"] else 1) for c in attackers if c.get("cmd")) * (1 + min(extra, 4))
        if kill_turn is None and (damage >= LIFE or cmd_damage >= COMMANDER_DAMAGE):
            kill_turn = turn
        if table_turn is None and table_damage >= LIFE * OPPONENTS:
            table_turn = turn
        if table_turn:
            break
        if removals_left and commander_cast == turn:
            cmd_perm = next((p for p in permanents if p["commander"]), None)
            if cmd_perm:
                kill_permanent(cmd_perm)
                refresh()
                removals_left -= 1
    return {"kill_turn": kill_turn, "table_turn": table_turn, "commander_turn": commander_cast, "cast": cast_count,
            "damage": damage, "combo_kill": combo_fired and kill_turn is not None}


def _combos_for(combos_included: list[dict] | None) -> list[dict]:
    """The deck's already-assembled combos that end the game (infinite damage or life loss, an outright
    win, infinite combat), as {"pieces": {normalized names}} sorted fewest pieces first."""
    out = []
    for combo in combos_included or []:
        produces = " ".join(combo.get("produces") or []).lower()
        if any(feature in produces for feature in _WIN_FEATURES) and combo.get("uses"):
            out.append({"pieces": [normalize_name(n) for n in combo["uses"]]})
    return sorted(out, key=lambda c: len(c["pieces"]))


_PROTECT_RE = re.compile(r"(?:gain|gains|get|gets|has|have)[^.]*(?:indestructible|hexproof|protection from|shroud)|phases? out|\bregenerate\b")
_REANIMATE_RE = re.compile(r"(?:return|put) (?:target |up to \w+ target |all |each )?(?:other )?creature cards? [^.]*from (?:your|a|any) graveyard[^.]*(?:to|onto) the battlefield")


def _prepare(entries: list[CardEntry], combos_included: list[dict] | None):
    """The shuffled-deck inputs both simulations share: (deck cards, commander, deck colors, combos, cards whose
    power had to be estimated)."""
    gameplay = gameplay_data_in_index()
    creature_names = [e.name for e in entries if "Creature" in (e.type_line or "") or "Planeswalker" in (e.type_line or "")]
    walkers = {e.name for e in entries if "Planeswalker" in (e.type_line or "")}
    stats = ensure_card_stats(creature_names, walkers)
    budget_alt = budget_alt_data_in_index()
    tags_by_name, _ = _card_tag_index(budget_alt["groups"])
    labels = budget_alt["tag_labels"]
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
        card["type_line"] = type_line
        card["profile"] = profile if card["kind"] == "spell" else None
        text = (info.get("oracle_text") or "").lower()
        card["protect"] = bool("Instant" in type_line and card["mv"] <= 3 and "you control" in text and _PROTECT_RE.search(text))
        reanimates = _REANIMATE_RE.search(text) if card["kind"] == "spell" and "Creature" not in type_line else None
        card["reanimate"] = (("mass" if re.search(r"\b(?:all|each) (?:other )?creature cards?", text) else "single") if reanimates else None)
        labels_here = {labels.get(t, t) for t in tags_by_name.get(normalize_name(e.name), ())} & _TUTOR_LABELS
        card["tutor"] = None
        if labels_here and card["kind"] == "spell" and "Creature" not in type_line:
            types = {"Creature"} if labels_here == {"tutor-creature"} else {"Artifact"} if labels_here == {"tutor-artifact"} else None
            card["tutor"] = {"types": types}
        deck.extend([dict(card) for _ in range(e.quantity)])
    return deck, commander, deck_colors, _combos_for(combos_included), estimated


def simulate_resilience(entries: list[CardEntry], games: int = 1200, combos_included: list[dict] | None = None) -> dict | None:
    """How the Turn Clock changes when the opponents interact: a board wipe on turn WIPE_TURN (survived if a
    protection spell is in hand; creatures come back if a reanimation spell is), and the commander removed each
    time it is cast (twice). Reports each scenario's median kill turn and kill rate by turn 12 next to the baseline,
    plus how many protection, recursion, draw and threat cards the deck runs."""
    deck, commander, deck_colors, combos, _ = _prepare(entries, combos_included)
    if len(deck) < 40:
        return None

    def run(scenario):
        rng = random.Random(SEED)
        runs = [_play(deck, commander, deck_colors, combos, rng, scenario) for _ in range(games)]
        kills = sorted(r["kill_turn"] for r in runs if r["kill_turn"] is not None)
        index = games // 2
        return {"median_turn": kills[index] if index < len(kills) else None, "kill_rate": round(100.0 * len(kills) / games, 1),
                "by_8": round(100.0 * sum(1 for k in kills if k <= 8) / games, 1),
                "by_10": round(100.0 * sum(1 for k in kills if k <= 10) / games, 1)}

    unique = {c["name"]: c for c in deck}.values()
    threats = [c for c in unique if c.get("profile") and ((c["profile"]["creature"] and c["profile"]["power"] >= 3) or c["profile"]["tokens"] or c["profile"]["drain"] or c["profile"]["scaling"])]
    return {
        "games": games, "wipe_turn": WIPE_TURN,
        "baseline": run(None), "spot": run({"spot": SPOT_CHANCE}), "wipe": run({"wipe": WIPE_TURN}),
        "both": run({"wipe": WIPE_TURN, "spot": SPOT_CHANCE}), "commander": run({"cmd": 2}), "spot_chance": SPOT_CHANCE,
        "protection": sum(1 for c in deck if c.get("protect")),
        "recursion": sum(1 for c in deck if c.get("reanimate")),
        "draw": sum(1 for c in deck if c.get("draw")),
        "threats": len(threats),
        "combos": len(combos),
    }


def simulate_turn_clock(entries: list[CardEntry], games: int = GAMES_DEFAULT, combos_included: list[dict] | None = None) -> dict | None:
    """Runs the Turn Clock goldfish on a Commander deck; None when there's no library to shuffle.
    `combos_included`: the deck's assembled combos as the Analyze stats report them (uses/produces)."""
    deck, commander, deck_colors, combos, estimated = _prepare(entries, combos_included)
    if len(deck) < 40:
        return None
    rng = random.Random(SEED)
    runs = [_play(deck, commander, deck_colors, combos, rng) for _ in range(games)]
    kills = sorted(r["kill_turn"] for r in runs if r["kill_turn"] is not None)
    table_kills = sorted(r["table_turn"] for r in runs if r["table_turn"] is not None)
    by_turn = {t: round(100.0 * sum(1 for k in kills if k <= t) / games, 1) for t in range(3, MAX_TURN + 1)}
    table_by_turn = {t: round(100.0 * sum(1 for k in table_kills if k <= t) / games, 1) for t in range(3, MAX_TURN + 1)}

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
        "table_by_turn": table_by_turn,
        "table_kill_rate": round(100.0 * len(table_kills) / games, 1),
        "table_median_turn": table_kills[games // 2] if games // 2 < len(table_kills) else None,
        "opponents": OPPONENTS,
        "estimated_power_cards": estimated,
        "commander_cast_rate": round(100.0 * sum(1 for r in runs if r["commander_turn"] is not None) / games, 1),
        "combos": len(combos),
        "combo_kill_rate": round(100.0 * sum(1 for r in runs if r["combo_kill"]) / games, 1),
        "life": LIFE,
        "max_turn": MAX_TURN,
    }
