"""What a finished deck is weak to and strong against -- Analyze's "Matchups" section.

Read-only and local: it uses the deck's own cards, the card database (Oracle text, types, EDHREC rank) and Oracle Tags,
and makes no network calls, so it works offline. Nothing imports this module except the one route in app.py.

How it works, in three steps:
  1. Profile: what the deck depends on, counted from its own cards (death triggers, enter triggers, tokens, the
     graveyard, artifacts, enchantments, equipment/auras, instants and sorceries, nonbasic lands), plus what the
     COMMANDER itself depends on, read from its Oracle text (Teysa Karlov only works while creatures die).
  2. Weak to: for each dependency that matters, the kinds of cards that punish it, with named examples (a curated
     list of well-known cards, each checked against the card database) and what the deck already holds in answer.
  3. Strong against: rule-of-thumb reads per archetype from the deck's own counts. Not a simulation and not a
     prediction about any real table; the page says so.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from brewlist_core import PRICE_INDEX_PATH, commander_eligible, gameplay_data_in_index, normalize_name, prices_data_in_index
import edhrec_data
from tag_synergy import _index as _tag_index
from turn_clock import _PROTECT_RE, _REANIMATE_RE

# ---- what each dependency looks like in a card's Oracle text ------------------------------------------------
_DIES_RE = re.compile(r"(?:whenever|when)[^.\n]*\bdies\b|\bdies,|is put into a graveyard from the battlefield", re.I)
_ETB_RE = re.compile(r"(?:whenever|when)[^.\n]*\benters\b", re.I)
_SAC_RE = re.compile(r"\bsacrifice\b", re.I)
_TOKEN_RE = re.compile(r"\bcreates?\b[^.\n]*\btokens?\b", re.I)
_UTILITY_TOKEN_RE = re.compile(r"\b(?:treasure|clue|food|blood|map|gold|powerstone|junk|shard|lander|incubator)\b", re.I)


def _makes_creature_tokens(text: str) -> bool:
    """Creature tokens (the thing a sweeper punishes), not Treasure, Clue, Food and the like, which nearly every deck makes."""
    for sentence in re.split(r"(?<=[.\n])", text):
        if _TOKEN_RE.search(sentence) and (not _UTILITY_TOKEN_RE.search(sentence) or re.search(r"creature token|\d+/\d+", sentence, re.I)):
            return True
    return False
_GRAVE_RE = re.compile(r"from your graveyard|in your graveyard|from a graveyard|your graveyard|\b(?:flashback|escape|unearth|embalm|eternalize|disturb|dredge|delve|jump-start)\b", re.I)
_CAST_RE = re.compile(r"whenever you cast", re.I)
_FREE_SAC_RE = re.compile(r"(?:^|\n)sacrifice (?:a|an|another|two|three|x)\b[^:\n{]*:", re.I)

# Dependencies: key -> (label, heavy at this many cards, moderate at this many). Counted over the deck's unique nonland cards.
_DEPS = {
    "dies": ("Death triggers", 9, 5),
    "etb": ("Enter-the-battlefield triggers", 15, 10),
    "tokens": ("Tokens and a wide board", 9, 6),
    "graveyard": ("The graveyard", 10, 7),
    "artifacts": ("Artifacts", 18, 13),
    "enchantments": ("Enchantments", 12, 8),
    "voltron": ("Equipment and Auras", 8, 5),
    "spells": ("Instants and sorceries", 0, 0),  # share-based, below
    "lands": ("Nonbasic lands", 24, 99),  # never above "moderate" (see below): nearly every deck runs many
}

# What the commander's own text says it depends on.
_CMDR_DEPS = {
    "dies": re.compile(r"\bdies\b|dying|put into a graveyard from the battlefield", re.I),
    "etb": re.compile(r"(?:whenever|when)[^.\n]*\benters\b", re.I),
    # creature tokens only: a commander that makes Treasure or Clue doesn't depend on a wide board
    "tokens": re.compile(r"\bcreature tokens?\b|\b\d+/\d+\b[^.\n]*\btokens?\b|\btokens? you control\b", re.I),
    "graveyard": re.compile(r"graveyard", re.I),
    "artifacts": re.compile(r"\bartifacts?\b", re.I),
    "enchantments": re.compile(r"\benchantments?\b", re.I),
    "voltron": re.compile(r"\b(?:equipped|enchanted|equipment|auras?)\b", re.I),
    "spells": re.compile(r"whenever you cast|instant or sorcery", re.I),
    "lands": re.compile(r"\blandfall\b|land enters|play(?:ed)? a land|lands? you control", re.I),
}
_CMDR_PHRASE = {
    "dies": "creatures dying", "etb": "things entering the battlefield", "tokens": "tokens", "graveyard": "the graveyard",
    "artifacts": "artifacts", "enchantments": "enchantments", "voltron": "equipment and Auras",
    "spells": "casting spells", "lands": "lands",
}

# ---- curated disruption: weakness id -> list of (title, why, [card names]) -----------------------------------
# Only well-known cards; names are checked against the card database at load time and dropped if missing.
_HATE = {
    "dies": [
        ("Removal that doesn't kill", "Exile, bounce and tuck all take a creature away without it dying, so no death trigger fires.",
         ["Swords to Plowshares", "Path to Exile", "Chaos Warp", "Cyclonic Rift", "Settle the Wreckage",
          "Oblivion Ring", "Banishing Light", "Anguished Unmaking", "Vanishing Verse", "Unexpectedly Absent"]),
        ("Dies-becomes-exile effects", "These exile a creature instead of letting it die, which shuts off death triggers even though it never reaches the graveyard. (Kalitas only exiles nontoken creatures. Stone of Erech is colorless, so any deck can run it, and it only affects creatures an opponent controls.)",
         ["Rest in Peace", "Leyline of the Void", "Kalitas, Traitor of Ghet", "Dauthi Voidwalker", "Stone of Erech"]),
        ("Cards that stop the trigger itself", "Hushbringer is the one card that says dying doesn't cause abilities to trigger (it blanks enter triggers too). Stifle-style spells counter one triggered ability at a time, which is weaker against a commander that doubles triggers.",
         ["Hushbringer", "Stifle", "Trickbind", "Repudiate", "Consign to Memory", "Sublime Epiphany"]),
    ],
    "etb": [
        ("Trigger shutoffs", "Creatures entering no longer trigger abilities, which blanks enter-the-battlefield value.",
         ["Torpor Orb", "Hushbringer", "Elesh Norn, Mother of Machines", "Doorkeeper Thrull", "Tocatli Honor Guard", "Strict Proctor"]),
    ],
    "tokens": [
        ("Board wipes", "A single sweeper clears a wide board of tokens and small creatures.",
         ["Wrath of God", "Damnation", "Toxic Deluge", "Blasphemous Act", "Austere Command", "Farewell", "Crux of Fate", "Massacre Girl"]),
    ],
    "graveyard": [
        ("Graveyard hate", "Exiles or locks the graveyard, which cuts off recursion, flashback and graveyard payoffs.",
         ["Rest in Peace", "Bojuka Bog", "Tormod's Crypt", "Relic of Progenitus", "Scavenger Grounds", "Leyline of the Void",
          "Dauthi Voidwalker", "Soul-Guide Lantern", "Cremate", "Deathrite Shaman"]),
    ],
    "artifacts": [
        ("Artifact hate", "Turns off or destroys artifacts, including their activated abilities.",
         ["Collector Ouphe", "Stony Silence", "Null Rod", "Bane of Progress", "Vandalblast", "Farewell", "Kataki, War's Wage",
          "Reclamation Sage", "Thrashing Brontodon", "Manglehorn"]),
    ],
    "enchantments": [
        ("Enchantment removal", "Destroys or exiles enchantments, and some of it hits artifacts as well.",
         ["Bane of Progress", "Reclamation Sage", "Aura Shards", "Disenchant", "Return to Nature", "Farewell", "Thrashing Brontodon"]),
    ],
    "voltron": [
        ("Single-target removal", "A voltron plan puts everything on one creature, so one removal spell undoes the investment.",
         ["Swords to Plowshares", "Path to Exile", "Chaos Warp", "Generous Gift", "Beast Within", "Pongify", "Rapid Hybridization"]),
        ("Attack deterrents", "Taxes or ambushes that make attacking with the one big creature costly.",
         ["Settle the Wreckage", "Propaganda", "Ghostly Prison", "Maze of Ith", "Archangel of Tithes"]),
    ],
    "spells": [
        ("Spell taxes and counters", "Makes each instant and sorcery cost more or stops it outright.",
         ["Rule of Law", "Eidolon of the Great Revel", "Drannith Magistrate", "Grand Abolisher", "Deafening Silence", "Trinisphere",
          "Counterspell", "Swan Song", "Force of Will", "Mana Drain"]),
    ],
    "lands": [
        ("Nonbasic land punishers", "Blank or destroy nonbasic lands, which hurts the colors they provide.",
         ["Blood Moon", "Magus of the Moon", "Back to Basics", "Wasteland", "Tectonic Edge", "Field of Ruin", "Ruination", "Price of Progress"]),
    ],
}

# Commanders that go after a dependency, found by scanning legal commanders' Oracle text (tight patterns only).
_WATCH = {
    "dies": ("Commanders that stop creatures dying",
             re.compile(r"(?:if|whenever) (?:a|an|another|any) (?:nontoken )?(?:creature|card)[^.]{0,50}would (?:die|be put into [^.]{0,30}graveyard)[^.]*\bexile\b[^.]*instead", re.I)),
    "etb": ("Commanders that stop enter triggers", re.compile(r"entering(?: the battlefield)? (?:doesn't|don't) cause", re.I)),
    "tokens": ("Commanders that sweep the board", re.compile(r"(?:destroy|exile) all (?:other )?creatures|each creature gets -\d+/-\d+|damage to each creature", re.I)),
    "graveyard": ("Commanders that exile graveyards", re.compile(r"exile (?:all|each|target) cards? from (?:all |each |a |target |your opponents' )?graveyards?|cards in graveyards? can't|can't (?:cast|play|activate)[^.]*graveyard", re.I)),
    "spells": ("Commanders that tax or stop spells", re.compile(r"(?:opponents|players) (?:can't cast|spells cost)|spells (?:your opponents cast |opponents cast )?cost \{\d\} more|counter target spell", re.I)),
}

# Groups whose whole point is that the creature does NOT die: a card that says "destroy" must never be listed in
# them (Beast Within and Generous Gift were, wrongly, until caught). Enforced from the card's own Oracle text.
_MUST_NOT_DESTROY = {"Removal that doesn't kill"}
_DESTROY_RE = re.compile(r"\bdestroy\b", re.I)
# Groups that claim to hit one kind of thing: every card listed must say so in its own text. (Strip Mine, which destroys
# ANY land, was listed as a "nonbasic land punisher" until caught.)
_MUST_MENTION = {"Nonbasic land punishers": re.compile(r"nonbasic", re.I),
                 "Cards that stop the trigger itself": re.compile(r"triggered ability|abilities to trigger", re.I)}

_cache: dict = {}

# ---- which printing's image to show ----
# The card database keeps one arbitrary printing per card (the first one it saw, often decades old). A card pill
# shows, in order: the printing you own, else Scryfall's current default printing for the card (looked up once for
# the whole batch, then kept in data/matchup_prints.json so it works offline afterwards), else the database's own.
_PRINTS_PATH = os.path.join(os.path.dirname(PRICE_INDEX_PATH), "matchup_prints.json")
_prints_missed: set[str] = set()  # asked for this session and not found / unreachable: don't ask again


def _load_prints() -> dict:
    try:
        with open(_PRINTS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _fetch_default_prints(names: list[str]) -> dict[str, str]:
    """{normalized front-face name: scryfall id} for Scryfall's default printing of each name. Best effort: any
    failure (offline, rate limit, bad reply) returns what it has so far."""
    found: dict[str, str] = {}
    for i in range(0, len(names), 70):
        batch = names[i:i + 70]
        body = json.dumps({"identifiers": [{"name": n} for n in batch]}).encode("utf-8")
        req = urllib.request.Request("https://api.scryfall.com/cards/collection", data=body, method="POST", headers={
            "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "brewlist/1.0 (personal use)"})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            break
        for card in data.get("data") or []:
            if card.get("id") and card.get("name"):
                found[_front(card["name"])] = card["id"]
    return found


def _use_best_printings(result: dict, owned_ids: dict[str, str]) -> None:
    """Rewrites every {"name", "id"} card reference in the result in place to the best printing."""
    refs: list[dict] = []

    def walk(x):
        if isinstance(x, dict):
            if "name" in x and "id" in x and isinstance(x.get("name"), str):
                refs.append(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(result)
    prints = _load_prints()
    wanted = sorted({_front(r["name"]) for r in refs if normalize_name(r["name"]) not in owned_ids and _front(r["name"]) not in owned_ids}
                    - set(prints) - _prints_missed)
    if wanted:
        fetched = _fetch_default_prints(wanted)
        _prints_missed.update(set(wanted) - set(fetched))
        if fetched:
            prints.update(fetched)
            try:
                tmp = _PRINTS_PATH + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(prints, f)
                os.replace(tmp, _PRINTS_PATH)
            except OSError:
                pass
    price_data = prices_data_in_index() if any(_front(r["name"]) not in owned_ids for r in refs) else {}
    for r in refs:
        owned_id = owned_ids.get(normalize_name(r["name"])) or owned_ids.get(_front(r["name"]))
        r["id"] = owned_id or prints.get(_front(r["name"])) or r.get("id")
        r["owned"] = bool(owned_id)
        if not owned_id:
            r["price"] = _cheapest(price_data, r["name"])


def _cheapest(price_data: dict, name: str) -> float | None:
    """Cheapest nonfoil USD price found (Cardmarket is EUR and never mixed in), or None."""
    entry = price_data.get(normalize_name(name)) or price_data.get(_front(name)) or {}
    prices = [o[1] for o in (entry.get("nonfoil") or []) if o and o[0] != "CM" and o[1] is not None]
    return min(prices) if prices else None


def commander_ranks() -> dict[str, dict[str, int]]:
    """{window: {front-face normalized name: EDHREC's rank among commanders actually played that way in that window}}
    for "2years", "month" and "week". How popular a card is AS A COMMANDER (the builder's "popular commander" ranking),
    not the card's overall popularity. Read from the saved EDHREC rankings, rebuilt only when that file changes."""
    path = os.path.join(os.path.dirname(os.path.abspath(edhrec_data.__file__)), "data", "edhrec_cache.json")
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        stamp = None
    cached = _cache.get("ranks")
    if cached and cached[0] == stamp:
        return cached[1]
    out: dict[str, dict[str, int]] = {w: {} for w in ("2years", "month", "week")}
    for window, entry in (edhrec_data._load_cache().get("top") or {}).items():
        for c in entry.get("cards", []):
            if window in out and c.get("name") and isinstance(c.get("rank"), int):
                out[window].setdefault(_front(c["name"]), c["rank"])
    _cache["ranks"] = (stamp, out)
    return out


# Which popular commanders lean on each deck type, read from the commander's own Oracle text. Used to show, under each
# "strong"/"weak" verdict, real commanders of that kind. (Combo has no reliable text signal, so it gets no list.)
_ARCH_COMMANDERS = {
    "Go-wide tokens and aggro": re.compile(r"\bcreates?\b[^.\n]*(?:\b\d+/\d+\b|\bcreature\b)[^.\n]*\btokens?\b|\bpopulate\b|\bamass\b|\bcreature tokens? you control\b", re.I),
    "Voltron and one big threat": re.compile(r"\bequip(?:ped|ment)\b|\bauras?\b|\battach(?:ed)?\b|\bdouble strike\b", re.I),
    "Graveyard and reanimator": re.compile(r"from (?:your|a|any) graveyard|graveyard (?:to|onto) the battlefield|\breanimat|\bunearth\b|\bescape\b|\bdredge\b|\bdelve\b|cards? in your graveyard", re.I),
    "Artifact and enchantment stax": re.compile(r"(?:your opponents?|each opponent|opponents|players|each player|creatures your opponents control) (?:can't|cannot|don't|doesn't)\b|(?:opponents|players)[^.\n]* cast[^.\n]* cost \{\d\} more|can't attack you", re.I),
    "Control and board wipes": re.compile(r"(?:destroy|exile) all (?:other )?(?:creatures|nonland permanents|permanents)|each creature gets -\d+/-\d+|counter target spell|each (?:player|opponent) sacrifices", re.I),
}


def _popular_commanders(pool: dict, label: str, exclude_front: str, limit: int = 6) -> list[dict]:
    """The most popular commanders (past 2 years, as commanders) whose text leans on this deck type."""
    key = ("archcmd", label, id(pool), _cache.get("ranks", (None,))[0])
    full = _cache.get(key)
    if full is None:
        rx = _ARCH_COMMANDERS[label]
        ranks = commander_ranks()["2years"]
        seen, rows = set(), []
        for r in pool.values():
            if not commander_eligible(r.get("type_line"), r.get("oracle_text")):
                continue
            if str((r.get("legalities") or {}).get("commander")).lower() != "legal":
                continue
            f = _front(r["name"])
            rank = ranks.get(f)
            if rank is None or f in seen or not rx.search(r.get("oracle_text") or ""):
                continue
            seen.add(f)
            rows.append((rank, r))
        rows.sort(key=lambda t: t[0])
        full = [r for _, r in rows[:40]]
        _cache[key] = full
    return [_card_ref(r) for r in full if _front(r["name"]) != exclude_front][:limit]


def _pool() -> dict:
    return gameplay_data_in_index()


def _front(name: str) -> str:
    return normalize_name(name).split(" // ")[0]


def _lookup(pool: dict, name: str) -> dict | None:
    return pool.get(normalize_name(name)) or pool.get(_front(name))


def _tag_labels_for(name: str) -> set[str]:
    ix = _tag_index()
    ids = ix["cards"].get(normalize_name(name)) or ix["cards"].get(_front(name)) or ()
    return {ix["tags"].get(t, "") for t in ids}


def _card_ref(row: dict) -> dict:
    return {"name": row["name"], "id": row.get("scryfall_id")}


def _named(pool: dict, names: list[str]) -> list[dict]:
    """The curated names that actually exist in the card database, most played first."""
    rows = [r for r in (_lookup(pool, n) for n in names) if r]
    rows.sort(key=lambda r: int(r.get("edhrec_rank") or 10**9))
    return [_card_ref(r) for r in rows]


def _watch_commanders(pool: dict, key: str, limit: int = 5) -> list[dict]:
    cached = _cache.get(("watch", key, id(pool)))
    if cached is not None:
        return cached
    label_rx = _WATCH[key][1]
    out = []
    for r in pool.values():
        if not commander_eligible(r.get("type_line"), r.get("oracle_text")):
            continue
        if str((r.get("legalities") or {}).get("commander")).lower() != "legal":
            continue
        if label_rx.search(r.get("oracle_text") or ""):
            out.append(r)
    out.sort(key=lambda r: int(r.get("edhrec_rank") or 10**9))
    uniq, seen_front = [], set()
    for r in out:  # a double-faced commander is in the database under "A // B" and as "A"
        f = _front(r["name"])
        if f not in seen_front:
            seen_front.add(f)
            uniq.append(r)
    refs = [_card_ref(r) for r in uniq[:limit]]
    _cache[("watch", key, id(pool))] = refs
    return refs


def analyze_matchups(cards: list[dict], commander: dict | None, owned_ids: dict[str, str] | None = None) -> dict:
    pool = _pool()
    names = []
    seen = set()
    for c in cards or []:
        n = (c.get("name") or "").strip()
        if n and normalize_name(n) not in seen:
            seen.add(normalize_name(n))
            names.append(n)
    rows = [r for r in (_lookup(pool, n) for n in names) if r]
    cmd_row = _lookup(pool, (commander or {}).get("name") or "") if commander else None
    nonland = [r for r in rows if "Land" not in (r.get("type_line") or "")]
    lands = [r for r in rows if "Land" in (r.get("type_line") or "")]
    n_nonland = max(1, len(nonland))

    def text(r): return r.get("oracle_text") or ""
    def tl(r): return r.get("type_line") or ""

    members = {
        "dies": [r for r in nonland if _DIES_RE.search(text(r))],
        "etb": [r for r in nonland if _ETB_RE.search(text(r))],
        "tokens": [r for r in nonland if _makes_creature_tokens(text(r))],
        "graveyard": [r for r in nonland if _GRAVE_RE.search(text(r))],
        "artifacts": [r for r in nonland if "Artifact" in tl(r) and "Equipment" not in tl(r)],
        "enchantments": [r for r in nonland if "Enchantment" in tl(r) and "Aura" not in tl(r)],
        "voltron": [r for r in nonland if "Equipment" in tl(r) or "Aura" in tl(r)],
        "spells": [r for r in nonland if re.search(r"Instant|Sorcery", tl(r))],
        "lands": [r for r in lands if "Basic" not in tl(r)],
    }
    sacrifice = [r for r in nonland if _SAC_RE.search(text(r))]

    cmd_text = text(cmd_row) if cmd_row else ""
    cmd_relies = {k: bool(cmd_text and rx.search(cmd_text)) for k, rx in _CMDR_DEPS.items()}

    profile = []
    for key, (label, heavy, moderate) in _DEPS.items():
        count = len(members[key])
        if key == "spells":
            payoffs = len([r for r in nonland if _CAST_RE.search(text(r)) or re.search(r"instant or sorcery", text(r), re.I)])
            share = count / n_nonland
            count = max(count, payoffs)
            level = "heavy" if (payoffs >= 6 or share >= 0.5) else ("moderate" if (payoffs >= 3 or share >= 0.4) else "light")
        else:
            level = "heavy" if count >= heavy else ("moderate" if count >= moderate else "light")
        if key == "lands" and level == "heavy":
            level = "moderate"
        if cmd_relies.get(key) and level != "heavy":
            level = "heavy" if key in ("dies", "etb", "tokens", "graveyard") else "moderate"
        profile.append({"key": key, "label": label, "count": count, "level": level, "commander": cmd_relies.get(key, False)})

    # ---- what the deck already holds in answer ----
    free_sac = [r for r in nonland if _FREE_SAC_RE.search(text(r))] + [r for r in nonland if "free sacrifice outlet" in _tag_labels_for(r["name"])]
    free_sac = list({r["name"]: r for r in free_sac}.values())
    protection = [r for r in nonland if "Instant" in tl(r) and _PROTECT_RE.search(text(r).lower()) and "you control" in text(r).lower()]
    recursion = [r for r in nonland if "Creature" not in tl(r) and _REANIMATE_RE.search(text(r).lower())]
    counters = [r for r in nonland if "counterspell" in _tag_labels_for(r["name"])]
    gy_hate_in_deck = [r for r in nonland if re.search(r"exile (?:all|target|each) cards? from (?:a |target |all |each )?graveyards?|cards in graveyards? can't|exile [^.]*graveyard", text(r), re.I) and "Creature" not in tl(r)]
    sweepers = [r for r in nonland if "sweeper" in _tag_labels_for(r["name"]) or "sweeper-one-sided" in _tag_labels_for(r["name"])]
    exile_removal = [r for r in nonland if "removal-exile" in _tag_labels_for(r["name"])]
    art_ench_removal = [r for r in nonland if _tag_labels_for(r["name"]) & {"removal-artifact", "removal-enchantment"}]
    spot_removal = [r for r in nonland if _tag_labels_for(r["name"]) & {"removal-exile", "removal-creature", "removal-toughness", "removal-sacrifice", "removal-bounce", "removal-tuck", "removal-nonland", "removal-permanent"}]
    instant_interaction = [r for r in spot_removal + counters if "Instant" in tl(r) or "Flash" in text(r)]
    token_makers = members["tokens"]

    def refs(rs, limit=6): return [_card_ref(r) for r in rs[:limit]]

    mitigation = {
        "dies": ("Free sacrifice outlets", free_sac,
                 "A free outlet lets you sacrifice in response to exile or bounce, turning it back into a death."),
        "tokens": ("Protection and recursion", protection + recursion,
                   "Protection spells save the board from a wipe and reanimation brings it back."),
        "artifacts": ("Protection spells", protection, ""),
        "enchantments": ("Protection spells", protection, ""),
        "voltron": ("Protection spells", protection, "Hexproof or indestructible in response beats single-target removal."),
        "spells": ("Counterspells", counters, "Counters can answer a tax piece or the spell that stops you."),
        "graveyard": ("Recursion that avoids the graveyard", [], ""),
    }

    weaknesses = []
    for dep in profile:
        key = dep["key"]
        if key not in _HATE:
            continue
        if dep["level"] == "light":
            # Not a big exposure, but don't hide it: a deck with 8 enter-trigger cards still loses real value to Torpor
            # Orb or Elesh Norn. Shown under "smaller exposures", only when it's close to the moderate cutoff.
            # (Spells and lands are left out: nearly every deck has both, so they'd always appear.)
            floor = max(4, round(0.5 * _DEPS[key][2]))
            if key in ("spells", "lands") or dep["count"] < floor:
                continue
        mit = mitigation.get(key)
        mit_out = None
        if mit and mit[0] != "Recursion that avoids the graveyard":
            mit_out = {"label": mit[0], "count": len(mit[1]), "cards": refs(mit[1]), "note": mit[2]}
        groups = []
        for title, why, hate_names in _HATE[key]:
            if title in _MUST_NOT_DESTROY:
                hate_names = [n for n in hate_names if not _DESTROY_RE.search((_lookup(pool, n) or {}).get("oracle_text") or "")]
            if title in _MUST_MENTION:
                hate_names = [n for n in hate_names if _MUST_MENTION[title].search((_lookup(pool, n) or {}).get("oracle_text") or "")]
            ex = _named(pool, hate_names)
            if ex:
                groups.append({"title": title, "why": why, "examples": ex[:8]})
        if not groups:
            continue
        reason_bits = []
        if dep["count"]:
            reason_bits.append(f"{dep['count']} card{'s' if dep['count'] != 1 else ''} in the deck")
        if dep["commander"]:
            reason_bits.append(f"your commander ({cmd_row['name']}) relies on {_CMDR_PHRASE[key]}")
        entry = {"key": key, "title": dep["label"], "level": dep["level"], "because": "; ".join(reason_bits),
                 "groups": groups, "mitigation": mit_out}
        if key in _WATCH:
            watch = _watch_commanders(pool, key)
            if watch:
                entry["commanders"] = {"title": _WATCH[key][0], "cards": watch}
        weaknesses.append(entry)
    weaknesses.sort(key=lambda w: ({"heavy": 0, "moderate": 1, "light": 2}[w["level"]], -next(p["count"] for p in profile if p["key"] == w["key"])))

    # ---- strong against / weak against: archetype rules of thumb from the deck's own counts ----
    def verdict(label, good, bad, good_why, bad_why, even_why):
        if good: return {"archetype": label, "verdict": "strong", "why": good_why}
        if bad: return {"archetype": label, "verdict": "weak", "why": bad_why}
        return {"archetype": label, "verdict": "even", "why": even_why}

    def only(n, noun):
        """"No spot-removal pieces" / "Only 1 spot-removal piece" / "Only 3 spot-removal pieces"."""
        return f"No {noun}s" if n == 0 else f"Only {n} {noun}{'s' if n != 1 else ''}"

    def names_of(rs, limit=3):
        return ", ".join(r["name"] for r in rs[:limit])

    def verdict(label, strong, weak, strong_why, weak_why, even_why, cards=()):
        v = "strong" if strong else ("weak" if weak else "even")
        return {"archetype": label, "verdict": v, "why": strong_why if strong else (weak_why if weak else even_why), "cards": refs(list(cards), 4)}

    n_inter = len(spot_removal)
    n_protect = len(protection) + len(recursion)
    strengths = [
        verdict("Go-wide tokens and aggro", len(sweepers) >= 3 or (len(sweepers) >= 2 and len(token_makers) >= 8), len(sweepers) == 0 and len(token_makers) < 3,
                f"{len(sweepers)} sweeper{'s' if len(sweepers) != 1 else ''} ({names_of(sweepers)})" + (f" and {len(token_makers)} token makers" if token_makers else "") + " to blunt a wide board.",
                "No sweepers and few token makers, so a wide board can overrun it.",
                f"{len(sweepers)} sweeper{'s' if len(sweepers) != 1 else ''} and {len(token_makers)} token makers: some answers, nothing decisive.", sweepers),
        verdict("Voltron and one big threat", n_inter >= 8 or (n_inter >= 6 and len(free_sac) >= 2), n_inter <= 3,
                f"{n_inter} spot-removal pieces" + (f" and {len(free_sac)} free sacrifice outlets" if free_sac else "") + " to deal with one big threat.",
                f"{only(n_inter, 'spot-removal piece')}, so a lone huge threat is hard to stop.",
                f"{n_inter} spot-removal pieces: workable but not a sure answer.", spot_removal),
        verdict("Graveyard and reanimator", len(gy_hate_in_deck) >= 2, len(gy_hate_in_deck) == 0,
                f"{len(gy_hate_in_deck)} graveyard-hate pieces ({names_of(gy_hate_in_deck)}).",
                "No graveyard hate, so a reanimator deck gets to do its thing.", f"One graveyard-hate piece ({names_of(gy_hate_in_deck)}).", gy_hate_in_deck),
        verdict("Combo", len(instant_interaction) >= 6, len(instant_interaction) <= 2,
                f"{len(instant_interaction)} instant-speed answers to interrupt a combo.",
                f"{only(len(instant_interaction), 'instant-speed answer')}, so a combo can go off uncontested.",
                f"{len(instant_interaction)} instant-speed answers: some chance to interrupt.", instant_interaction),
        verdict("Artifact and enchantment stax", len(art_ench_removal) >= 4, len(art_ench_removal) <= 1 and (len(members["artifacts"]) + len(members["enchantments"])) >= 8,
                f"{len(art_ench_removal)} artifact or enchantment removal pieces to break a lock.",
                f"{only(len(art_ench_removal), 'artifact or enchantment removal piece')} while the deck leans on both.",
                f"{len(art_ench_removal)} artifact or enchantment removal pieces.", art_ench_removal),
        verdict("Control and board wipes", n_protect >= 5, n_protect <= 1 and len(members["tokens"]) + len([r for r in nonland if "Creature" in tl(r)]) >= 20,
                f"{len(protection)} protection and {len(recursion)} recursion spells to recover from wipes.",
                "A creature-heavy board with almost no protection or recursion, so one wipe sets it far back.",
                f"{len(protection)} protection and {len(recursion)} recursion spells.", protection + recursion),
    ]

    # under each verdict that isn't "even", real popular commanders of that kind (what this build tends to beat, or lose to)
    for st in strengths:
        if st["verdict"] != "even" and st["archetype"] in _ARCH_COMMANDERS:
            st["commanders"] = _popular_commanders(pool, st["archetype"], _front(cmd_row["name"]) if cmd_row else "")

    cmd_note = None
    if cmd_row:
        deps = [_CMDR_PHRASE[k] for k, v in cmd_relies.items() if v]
        if deps:
            cmd_note = f"{cmd_row['name']} depends on " + (", ".join(deps[:-1]) + " and " + deps[-1] if len(deps) > 1 else deps[0]) + "."
    result = {"profile": profile, "commander_note": cmd_note, "weaknesses": weaknesses, "strengths": strengths,
              "cards_checked": len(rows), "cards_in_deck": len(names),
              "disclaimer": "These are rules of thumb from your deck's own cards, not a simulation and not a prediction about any real table."}
    _use_best_printings(result, owned_ids or {})
    return result
