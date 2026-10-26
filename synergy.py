"""Synergy packages: what a Commander deck is actually built around, as pairs of enablers (cards that
make or do the thing) and payoffs (cards that reward it). Aristocrats is sacrifice outlets plus "whenever a
creature dies" payoffs; tokens is token makers plus "for each token" and team boosts; and so on.

Each package is read from rules text with patterns, the same way the Turn Clock reads cards, so every count
can be explained by pointing at the card. (The Oracle Tags the rest of the app uses are too loose for this: Path
to Exile is tagged "land ramp" and Marshal's Anthem "reanimate".) A card counts once per side of a package,
and can be an enabler in one package and a payoff in another.

What the panel reports:
  - the packages the deck leans on, with enabler and payoff counts and which side is short
  - payoffs that have almost nothing to enable them (they will sit in hand)
  - cards that belong to none of the deck's main packages, among the cards that are neither lands, ramp,
    card draw nor interaction -- the ones most likely to be filler
"""
from __future__ import annotations

import collections
import re

from brewlist_core import CardEntry, categorize, gameplay_data_in_index, normalize_name
from deck_builder import _card_role, _role_member_names, budget_alt_data_in_index

# (name, enabler patterns, payoff patterns, enabler card types, one-line description)
_PACKAGES = [
    ("Tokens",
     [r"\bcreates? [^.]*\btokens?\b", r"\bpopulate\b", r"\bamass\b"],
     [r"for each (?:creature )?tokens?", r"whenever (?:one or more |a |another )?(?:creature )?tokens?", r"tokens? you control (?:get|have)",
      r"creatures you control get \+", r"other creatures you control get \+", r"whenever a creature you control enters", r"(?:twice|double) (?:that many|the number of) [^.]*tokens?",
      r"\bconvoke\b", r"\bsacrifice (?:a|an|another|two|three)\b[^.:]*:"],
     (), "token makers and the cards that multiply or reward a wide board"),
    ("Sacrifice and death triggers",
     [r"^[^:\n]*\bsacrifice (?:a|an|another|two|three|x|any number of|up to)\b[^:\n]*:"],
     [r"whenever (?:a|another|one or more)[^.,]*\b(?:creatures?|permanents?|artifacts?|nontoken creatures?)\b[^.,]*\b(?:you control )?dies?\b",
      r"whenever you sacrifice", r"whenever [^.,]*\bis put into a graveyard from the battlefield", r"whenever (?:a|another) (?:creature|permanent) you control dies"],
     (), "sacrifice outlets and the cards that pay you when things die"),
    ("Graveyard recursion",
     [r"\bmills? (?:\w+ )?cards?", r"put the top [^.]*cards? of your library into your graveyard", r"\bsurveil\b", r"\bdredge\b", r"discard (?:a|up to|two|your)",
      r"\bconnive\b", r"\bloot\b", r"cycling \{"],
     [r"return (?:target |up to \w+ target |all |each )?[^.]*cards? from your graveyard", r"from your graveyard (?:to the battlefield|to your hand)",
      r"(?:cast|play) [^.]*from your graveyard", r"for each (?:creature )?cards? in your graveyard", r"cards? in your graveyard (?:with|that|have)", r"\bescape\b", r"\bflashback\b", r"\bunearth\b", r"\bembalm\b|\beternalize\b"],
     (), "ways to fill the graveyard and ways to use it"),
    ("+1/+1 counters",
     [r"put (?:a|an|two|three|four|x|\w+) \+1/\+1 counters? on", r"\bproliferate\b", r"enters with [^.]*\+1/\+1 counters?", r"\bbolster\b|\badapt\b|\bsupport\b|\bevolve\b|\bmodular\b|\boutlast\b|\bmentor\b|\btraining\b|\bmonstrosity\b"],
     [r"for each \+1/\+1 counter", r"(?:twice|double) (?:that many|the number of)[^.]*counters?", r"whenever [^.]*\+1/\+1 counters? (?:is|are) put", r"number of \+1/\+1 counters",
      r"remove (?:a|an|\w+) \+1/\+1 counters?", r"if [^.]*\+1/\+1 counters? on it", r"with [^.]*\+1/\+1 counters? on (?:it|them)"],
     (), "cards that add counters and cards that grow, double or spend them"),
    ("Lifegain",
     [r"you gain (?:\d+|x|\w+) life", r"\blifelink\b", r"gains? lifelink"],
     [r"whenever you gain life", r"if you gained life", r"for each (?:point of )?life you gained", r"life you(?:'ve)? gained", r"whenever you gain"],
     (), "lifegain sources and lifegain payoffs"),
    ("Landfall and extra lands",
     [r"search your library for [^.]*\bland\b[^.]*onto the battlefield", r"put (?:a|up to \w+) lands? cards? from your hand onto the battlefield", r"you may play (?:an )?additional lands?",
      r"play (?:two|an additional|additional) lands?", r"\bexplore\b"],
     [r"\blandfall\b", r"whenever a land (?:you control )?enters", r"for each land you control", r"number of lands you control", r"lands? you control (?:get|have)"],
     (), "extra land drops and ramp, plus cards that care when a land enters"),
    ("Artifacts",
     [r"\bcreates? [^.]*(?:treasure|clue|food|blood|thopter|servo|construct)[^.]*tokens?", r"\bcreates? [^.]*artifact[^.]*tokens?"],
     [r"whenever (?:an|another) artifact", r"for each artifact", r"artifacts you control", r"number of artifacts", r"artifact spells?", r"\baffinity for artifacts\b", r"\bmetalcraft\b", r"\bimprovise\b"],
     ("Artifact",), "artifact permanents and token makers, plus artifact-count payoffs"),
    ("Enchantments",
     [r"\bcreates? [^.]*enchantment"],
     [r"\bconstellation\b", r"whenever an enchantment", r"enchantments you control", r"for each enchantment", r"number of enchantments"],
     ("Enchantment",), "enchantment permanents plus enchantment payoffs"),
    ("Blink and enters-the-battlefield value",
     [r"exile (?:target|another|up to one target|up to \w+ target) [^.]*(?:creature|permanent)[^.]*\.?[^.]*return (?:it|that card|them|those cards) to the battlefield",
      r"\bblink\b|\bflicker\b", r"return (?:target |up to \w+ target )?(?:creature|permanent)s? you control to (?:its|their) owner'?s hands?", r"\bpopulate\b|\bclone\b|copy of (?:target|another) creature"],
     [r"whenever (?:a|another) (?:nontoken )?creature (?:you control )?enters", r"\bcreatures? entering\b|enters the battlefield under your control"],
     (), "blink effects and creatures whose enters trigger is the point"),
    ("Spells matter",
     [],
     [r"whenever you cast (?:an? )?(?:instant|sorcery|noncreature|spell|spell with|your (?:first|second|third))", r"\bmagecraft\b", r"\bprowess\b", r"copy (?:target|that|it) (?:instant|sorcery|spell)",
      r"\bstorm\b", r"for each (?:instant|sorcery)", r"instant and sorcery cards? in your graveyard", r"\bcast trigger\b"],
     ("Instant", "Sorcery"), "instants and sorceries plus cards that reward casting them"),
    ("Equipment and auras",
     [],
     [r"whenever [^.]*(?:becomes? attached|equipped creature|enchanted creature)", r"\battach (?:target|an|it|this)", r"for each (?:equipment|aura)", r"(?:equipment|auras?) you control", r"\breconfigure\b", r"equip(?:ment)? (?:costs?|abilities)"],
     ("Equipment", "Aura"), "equipment and auras plus cards that attach or reward them"),
    ("Combat tricks and extra combats",
     [r"additional combat phase", r"\bextra combat\b", r"untap all creatures you control"],
     [r"attacking creatures you control", r"\bexalted\b", r"\bbattle cry\b", r"\braid\b", r"for each attacking creature", r"whenever you attack", r"whenever (?:a|another|one or more) [^.,]*creatures? you control attacks?"],
     (), "extra combats and the cards that trigger on attacking"),
]

_COMPILED = [
    (name, [re.compile(p, re.M) for p in enablers], [re.compile(p, re.M) for p in payoffs], types, blurb)
    for name, enablers, payoffs, types, blurb in _PACKAGES
]
_MIN_PACKAGE_SIZE = 6      # enablers + payoffs for a package to count as something the deck is doing
_PAYOFF_SHORT_RATIO = 0.3  # enablers below this share of the package: payoffs outnumber what feeds them


def _normalise(entry: CardEntry, info: dict) -> str:
    """Lowercased rules text with the card's own name (and short name) replaced by "~"."""
    text = (info.get("oracle_text") or "").lower()
    name = entry.name
    short = name.split(",")[0]
    for alias in sorted({name, short, short.split(" of ")[0]}, key=len, reverse=True):
        text = text.replace(alias.lower(), "~")
    return re.sub(r"\bthis (?:creature|permanent|artifact|enchantment|equipment|aura|spell|vehicle)\b", "~", text)


def _matches(rxs, text: str) -> bool:
    return any(rx.search(text) for rx in rxs)


_GENERIC_TYPES = {"Human", "Wizard", "Warrior", "Soldier", "Shaman", "Rogue", "Cleric", "Knight"}  # too common to be a plan alone
_ALL_TYPES: set[str] | None = None


def _creature_types(type_line: str) -> set[str]:
    """Creature subtypes on a type line, across both faces of a two-faced card."""
    out: set[str] = set()
    for face in type_line.split("//"):
        if "Creature" in face and "—" in face:
            out |= {w for w in face.split("—", 1)[1].split() if w.isalpha() and len(w) > 2 and w[0].isupper()}
    return out


def _tribal_packages(entries: list[CardEntry], spells: list[CardEntry], gameplay: dict) -> list[dict]:
    """A tribal package for each creature type the deck is built around: the most common types among its
    creatures plus any types the commander's text names ("an Angel, Demon, or Dragon"). Enablers are creatures
    of the type (changelings too); payoffs are cards whose text rewards the type."""
    global _ALL_TYPES
    if _ALL_TYPES is None:
        seen_on = collections.Counter()
        for card in gameplay.values():
            seen_on.update(_creature_types(card.get("type_line") or ""))
        _ALL_TYPES = {t for t, n in seen_on.items() if n >= 5 and t not in {"The", "And", "Of", "Or"}}  # real creature types appear on many cards
    commander = next((e for e in entries if e.section == "commander"), None)
    commander_text = ""
    if commander:
        commander_text = _normalise(commander, gameplay.get(normalize_name(commander.name)) or {})
    commander_text = commander_text.replace("~", " ")
    named = {t for t in _ALL_TYPES if re.search(r"\b" + re.escape(t.lower()) + r"s?\b", commander_text)}
    seen = collections.Counter()
    for e in spells:
        for t in _creature_types(e.type_line):
            seen[t] += e.quantity
    top = [t for t, n in seen.most_common(4) if n >= 7 and t not in _GENERIC_TYPES][:2]
    groups = []
    if named and sum(1 for e in spells if _creature_types(e.type_line) & named) >= 5:
        groups.append(sorted(named))
    for t in top:
        if not any(t in g for g in groups):
            groups.append([t])
    out = []
    for types in groups:
        rx = re.compile("|".join(r"\b" + re.escape(t.lower()) + r"s?\b" for t in types))
        enablers, payoffs = [], []
        for e in spells:
            info = gameplay.get(normalize_name(e.name)) or {}
            text = _normalise(e, info)
            if _creature_types(e.type_line) & set(types) or "changeling" in text:
                enablers.append((e.name, e.quantity))
            if rx.search(text):  # a member can also reward the type ("whenever you cast another Vampire spell")
                payoffs.append((e.name, e.quantity))
        en, pay = sum(q for _, q in enablers), sum(q for _, q in payoffs)
        if en >= 6:
            plural = [t + "s" for t in types]
            label = plural[0] if len(plural) == 1 else ", ".join(plural[:-1]) + " and " + plural[-1]
            out.append({"name": f"{label} (tribal)", "blurb": "creatures of the type and the cards that reward them", "enablers": en, "payoffs": pay,
                        "enabler_cards": [n for n, _ in enablers][:14], "payoff_cards": [n for n, _ in payoffs][:14],
                        "members": [n for n, _ in enablers] + [n for n, _ in payoffs], "verdict": "short on payoffs" if pay < 3 else "balanced", "score": en + pay + 2 * min(en, pay), "tribal": True})
    return out


def deck_synergy_packages(entries: list[CardEntry]) -> dict | None:
    """The deck's synergy packages: {"packages": [{name, blurb, enablers, payoffs, verdict, enabler_cards,
    payoff_cards}], "thin": [...], "off_plan": [{name, role}], "tribe": {...}|None}. None for an empty deck."""
    gameplay = gameplay_data_in_index()
    budget_alt = budget_alt_data_in_index()
    role_members = _role_member_names(budget_alt["groups"], budget_alt["tag_labels"])
    spells = [e for e in entries if categorize(e.type_line) not in ("Lands", "Basic Lands")]
    if not spells:
        return None
    members: dict[str, dict[str, list[str]]] = {name: {"enablers": [], "payoffs": []} for name, *_ in _COMPILED}
    per_card: dict[str, set[str]] = collections.defaultdict(set)
    counts: dict[str, dict[str, int]] = {name: {"enablers": 0, "payoffs": 0} for name, *_ in _COMPILED}
    for e in spells:
        info = gameplay.get(normalize_name(e.name)) or {}
        text = _normalise(e, info)
        type_line = e.type_line or info.get("type_line") or ""
        for name, enablers, payoffs, types, _ in _COMPILED:
            is_enabler = (bool(types) and any(t in type_line for t in types)) or _matches(enablers, text)
            is_payoff = _matches(payoffs, text)
            # an Equipment or Aura is the enabler; its own "equipped creature" text isn't a payoff
            if name == "Equipment and auras" and any(t in type_line for t in ("Equipment", "Aura")):
                is_payoff = False
            if is_enabler:
                counts[name]["enablers"] += e.quantity
                members[name]["enablers"].append(e.name)
                per_card[e.name].add(name)
            if is_payoff:
                counts[name]["payoffs"] += e.quantity
                members[name]["payoffs"].append(e.name)
                per_card[e.name].add(name)
    packages, thin = [], []
    for name, _, _, _, blurb in _COMPILED:
        en, pay = counts[name]["enablers"], counts[name]["payoffs"]
        total = en + pay
        entry = {"name": name, "blurb": blurb, "enablers": en, "payoffs": pay,
                 "enabler_cards": members[name]["enablers"][:14], "payoff_cards": members[name]["payoffs"][:14]}
        if total >= _MIN_PACKAGE_SIZE and en >= 2 and pay >= 3:
            share = en / total
            entry["verdict"] = ("short on enablers" if share < _PAYOFF_SHORT_RATIO
                                else "short on payoffs" if share > 1 - _PAYOFF_SHORT_RATIO / 1.5 else "balanced")
            entry["score"] = total + 2 * min(en, pay)
            packages.append(entry)
        elif pay >= 3 and en <= 1:
            entry["verdict"] = "payoffs with almost nothing to enable them"
            thin.append(entry)
    packages += _tribal_packages(entries, spells, gameplay)
    packages.sort(key=lambda p: -p["score"])
    main = packages[:5]
    main_names = {p["name"] for p in main}
    for p in main:
        for member in p.get("members", ()):
            per_card[member].add(p["name"])
    off_plan = []
    for e in spells:
        if e.section == "commander":
            continue
        role = _card_role(e.name, categorize(e.type_line), role_members)
        if role != "Synergy" or per_card.get(e.name, set()) & main_names:
            continue
        off_plan.append({"name": e.name, "role": role})
    for p in packages:
        p.pop("members", None)
    return {"packages": main, "other_packages": packages[5:], "thin": thin, "off_plan": off_plan[:16], "off_plan_total": len(off_plan),
            "checked": sum(e.quantity for e in spells if e.section != "commander")}
