"""Collection insights: where a ManaBox collection is deep and where it is thin.

A read-only look at what you own, separate from the Deck Builder and the compare flow (they never import this).
Everything comes from data the app already has locally -- the ManaBox export, the card database's per-card
gameplay data, and Scryfall's Oracle Tags -- so it works offline and costs no network calls.

  - snapshot:  how many cards, copies, rough value, foils, sets.
  - colors:    unique nonland cards per color identity.
  - roles:     a role x color grid (ramp, draw, removal, wipes, counterspells, tutors, recursion, ...), each cell
               flagged "thin" / "deep" against what the collection's own size per color would predict, so a big
               color doesn't look strong just for being big.
  - themes:    graveyard, sacrifice, tokens, counters... with how much of each theme in the whole game you own,
               compared with your other themes (every theme reads high against the game as a whole, since themed
               cards are the better-known ones).
  - types:     card types, mana curve, and your deepest creature types.

Oracle Tags are crowd-made and uneven, so treat the counts as "roughly how deep", not exact numbers.
"""

from __future__ import annotations

import re
import time

from brewlist_core import (
    gameplay_data_in_index,
    budget_alt_data_in_index,
    normalize_name,
    price_for_printing,
    scryfall_prices_in_index,
    sets_data_in_index,
)
from deck_builder import _NON_LIBRARY_TYPE_RE, _ROLE_TAG_LABELS

COLOR_ORDER = ["W", "U", "B", "R", "G"]
COLOR_NAMES = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green", "M": "Multicolor", "C": "Colorless"}
COLUMNS = COLOR_ORDER + ["M", "C"]

# Tag labels that are Tagger housekeeping or one-off jokes (same idea as tag_synergy._JUNK).
_JUNK = re.compile(r"achievement|flowstone|inscryption|set.s mechanic|set mechanic|conjure|^sneak-|vanilla|auraify|more expensive")

_INTERACTION = _ROLE_TAG_LABELS["Interaction"]
_ROLES: list[tuple[str, dict]] = [
    ("Ramp", {"labels": _ROLE_TAG_LABELS["Ramp"]}),
    ("Card draw", {"labels": _ROLE_TAG_LABELS["Draw"]}),
    ("Spot removal", {"labels": {l for l in _INTERACTION if not l.startswith(("sweeper", "counterspell"))}}),
    ("Board wipes", {"labels": {l for l in _INTERACTION if l.startswith("sweeper")}}),
    ("Counterspells", {"labels": {l for l in _INTERACTION if l.startswith("counterspell")}}),
    ("Tutors", {"regex": r"^tutor-(?!land)"}),
    ("Recursion", {"regex": r"^reanimate|castable from graveyard|^regrowth|activate from graveyard"}),
    ("Sacrifice outlets", {"regex": r"sacrifice outlet"}),
    ("Token makers", {"oracle": r"\bcreates? [^.]*\btokens?\b"}),
    ("Lifegain", {"regex": r"^lifegain$"}),
]

# Themes: a player-recognizable plan, defined by Oracle Tag labels and/or an Oracle-text pattern.
_THEMES: list[tuple[str, dict]] = [
    ("Graveyard and reanimation", {"regex": r"^reanimate|castable from graveyard|activate from graveyard|cards in graveyard matter|graveyard fuel|trigger from graveyard|^mill-self|^regrowth"}),
    ("Sacrifice and death triggers", {"regex": r"sacrifice outlet|^death trigger|blood artist ability|^multiple bodies"}),
    ("Tokens", {"regex": r"repeatable .*tokens?|^synergy-token|^tokenfall", "oracle": r"\bcreates? [^.]*\btokens?\b"}),
    ("+1/+1 counters", {"regex": r"counters matter|gives pp counters|proliferate", "oracle": r"\+1/\+1 counters?"}),
    ("Lifegain", {"regex": r"^lifegain|lifegain matters|blood artist ability"}),
    ("Landfall and extra lands", {"regex": r"^landfall$|^land ramp|tutor-land-to-battlefield", "oracle": r"\bland(?:s)? (?:you control )?enters?\b|play an additional land"}),
    ("Treasure and artifacts", {"regex": r"repeatable treasures|repeatable artifact tokens|synergy-artifact|sacrifice outlet-artifact", "oracle": r"\btreasure\b|whenever an artifact"}),
    ("Spellslinger", {"regex": r"^synergy-instant|^synergy-sorcery|^synergy-noncreature", "oracle": r"whenever you cast (?:an? )?(?:instant|sorcery|noncreature)"}),
    ("Equipment and auras", {"regex": r"^synergy-equipment|^synergy-aura|^tutor-artifact-equipment", "oracle": r"\bequip\b|equipped creature|enchanted creature gets"}),
    ("Enchantments matter", {"regex": r"^synergy-enchantment|^enchantmentfall", "oracle": r"whenever (?:an? )?enchantment|enchantments? you control|constellation"}),
    ("Drain and burn", {"regex": r"group slug|opponent loses life|^burn player|^burn any"}),
    ("Blink and flicker", {"regex": r"^flicker-"}),
    ("Copy and theft", {"regex": r"^theft-|^copy-(?:creature|spell|instant|sorcery)|^clone"}),
    ("Wheels and discard", {"regex": r"^wheel|^miniwheel|discard outlet|self-discard matters"}),
    ("Combat and aggression", {"regex": r"attacking matters|^gives haste|combat trick|power boost to all|extra combat"}),
    ("Mill and surveil", {"regex": r"^mill-opponent|^mill-any|^mill-each|synergy-mill|^surveil"}),
]

_cache: dict = {"key": None, "data": None}


def _aliases(name: str) -> set[str]:
    n = normalize_name(name)
    return {n, n.split(" // ")[0]}


def _color_bucket(identity: list[str]) -> str:
    ci = [c for c in identity or [] if c in COLOR_ORDER]
    if not ci:
        return "C"
    return ci[0] if len(ci) == 1 else "M"


def _is_land(type_line: str) -> bool:
    return "Land" in (type_line or "")


def _legal(row: dict) -> bool:
    return ((row.get("legalities") or {}).get("commander")) in (None, "Legal")


def _tag_members(label_filter, groups: dict, labels: dict) -> set[str]:
    out: set[str] = set()
    for tid, label in labels.items():
        if _JUNK.search(label or ""):
            continue
        if label_filter(label):
            for entry in groups.get(tid) or []:
                if entry:
                    out |= _aliases(entry[0])
    return out


def _definition_filter(defn: dict):
    labels = defn.get("labels")
    rx = re.compile(defn["regex"]) if defn.get("regex") else None
    return lambda label: bool((labels and label in labels) or (rx and rx.search(label)))


def _pool_members(defn: dict, pool: list[dict], groups: dict, labels: dict) -> set[str]:
    """Normalized names (front face included) of pool cards that fit a role/theme definition."""
    members: set[str] = set()
    if defn.get("labels") or defn.get("regex"):
        members |= _tag_members(_definition_filter(defn), groups, labels)
    if defn.get("oracle"):
        rx = re.compile(defn["oracle"], re.I)
        for g in pool:
            if rx.search(g.get("oracle_text") or ""):
                members |= _aliases(g["name"])
    return members


def _card(r: dict) -> dict:
    return {"name": r["name"], "scryfall_id": r.get("scryfall_id"), "type_line": r.get("type_line"), "mana_cost": r.get("mana_cost"),
            "quantity": r.get("quantity") or 1, "set_code": r.get("set_code"), "foil": bool(r.get("foil")), "layout": r.get("layout")}


def _sorted_cards(rows: list[dict]) -> list[dict]:
    return [_card(r) for r in sorted(rows, key=lambda r: (r.get("edhrec_rank") or 10**9, r["name"]))]


def _creature_types(type_line: str) -> list[str]:
    if "Creature" not in (type_line or "") or "—" not in type_line:
        return []
    return [t for t in type_line.split("—", 1)[1].split() if t not in ("//",) and t.isalpha()]


def build_insights(owned: dict, owned_rows: list[dict]) -> dict:
    """`owned` is load_collection's {name: OwnedCard}; `owned_rows` are owned_collection_gameplay_view rows."""
    gameplay = gameplay_data_in_index()
    tag_data = budget_alt_data_in_index()
    groups, labels = tag_data.get("groups") or {}, tag_data.get("tag_labels") or {}
    prices = scryfall_prices_in_index()
    sets = sets_data_in_index()

    # ---- snapshot -------------------------------------------------------------------------------------------
    copies = foil_copies = 0
    value = 0.0
    priced = 0
    set_codes: set[str] = set()
    for card in owned.values():
        copies += card.total
        foil_copies += card.foil
        for p in card.printings:
            if p.set_code:
                set_codes.add(p.set_code.upper())
            sp = prices.get(p.scryfall_id)
            price = price_for_printing(sp, p) if sp else None
            if price is not None:
                value += price * p.quantity
                priced += p.quantity
    snapshot = {
        "unique": len(owned_rows),
        "copies": copies,
        "lands": sum(1 for r in owned_rows if _is_land(r.get("type_line"))),
        "value": round(value, 2),
        "value_covers": round(priced / copies, 3) if copies else 0,
        "foil_pct": round(100 * foil_copies / copies, 1) if copies else 0,
        "sets": len(set_codes),
    }

    # ---- the pool (every Commander-legal nonland card in the game) and what you own of it -------------------
    pool = [g for g in gameplay.values()
            if _legal(g) and not _is_land(g.get("type_line")) and not _NON_LIBRARY_TYPE_RE.search(g.get("type_line") or "")]
    mine = [r for r in owned_rows if not _is_land(r.get("type_line")) and _legal(r) and not _NON_LIBRARY_TYPE_RE.search(r.get("type_line") or "")]
    my_names = {}
    for r in mine:
        for a in _aliases(r["name"]):
            my_names[a] = r
    baseline = len(mine) / max(1, len(pool))

    # ---- colors ---------------------------------------------------------------------------------------------
    lists: dict[str, list[dict]] = {}  # the cards behind every number, looked up by key when something is clicked
    col_totals = {c: 0 for c in COLUMNS}
    by_color: dict[str, list[dict]] = {c: [] for c in COLUMNS}
    for r in mine:
        b = _color_bucket(r.get("color_identity"))
        col_totals[b] += 1
        by_color[b].append(r)
    for c in COLUMNS:
        lists[f"color:{c}"] = _sorted_cards(by_color[c])
    colors = {"columns": [{"key": c, "label": COLOR_NAMES[c], "unique": col_totals[c]} for c in COLUMNS], "nonland_total": len(mine)}

    # ---- roles x colors ---------------------------------------------------------------------------------------
    # A cell is judged against how many such cards exist in the whole game for that color, and against how much of
    # that color you own overall -- so blue counterspells aren't "deep" and black ones a "gap" just because
    # counterspells are blue, and a big color doesn't look strong only for being big.
    pool_col_totals = {c: 0 for c in COLUMNS}
    for g in pool:
        pool_col_totals[_color_bucket(g.get("color_identity"))] += 1
    col_baseline = {c: (col_totals[c] / pool_col_totals[c]) if pool_col_totals[c] else 0 for c in COLUMNS}
    role_rows = []
    for role, defn in _ROLES:
        members = _pool_members(defn, pool, groups, labels)
        pool_hits = {c: 0 for c in COLUMNS}
        for g in pool:
            if _aliases(g["name"]) & members:
                pool_hits[_color_bucket(g.get("color_identity"))] += 1
        hits: dict[str, list[dict]] = {c: [] for c in COLUMNS}
        seen = set()
        for r in mine:
            if normalize_name(r["name"]) in seen:
                continue
            if _aliases(r["name"]) & members:
                seen.add(normalize_name(r["name"]))
                hits[_color_bucket(r.get("color_identity"))].append(r)
        total = sum(len(v) for v in hits.values())
        # Role-tagged cards are the good, popular ones, so you own a bigger share of them than of the average card in
        # every color. "role_lift" removes that, so a cell reads relative to the rest of its own row.
        pool_total = sum(pool_hits.values())
        expected_cov = sum(pool_hits[c] * col_baseline[c] for c in COLUMNS) / pool_total if pool_total else 0
        role_lift = ((total / pool_total) / expected_cov) if pool_total and expected_cov else 1
        cells = {}
        for c in COLUMNS:
            n = len(hits[c])
            pool_n = pool_hits[c]
            index = (n / pool_n) / (col_baseline[c] * role_lift) if pool_n and col_baseline[c] and role_lift else None
            flag = None
            # Only the five colors are flagged: Multicolor and Colorless each mix many kinds of cards, and their small
            # pools made the flags noisy (a "strong" colorless cell next to a bigger count in black read as a bug).
            if index is not None and pool_n >= 30 and c in COLOR_ORDER:  # a role that barely exists in a color isn't a gap
                if index < 0.35:
                    flag = "gap"
                elif index < 0.7:
                    flag = "thin"
                elif index > 1.4 and n >= 6:
                    flag = "deep"
            top = sorted(hits[c], key=lambda r: (r.get("edhrec_rank") or 10**9))[:12]
            lists[f"role:{role}:{c}"] = _sorted_cards(hits[c])
            cells[c] = {"n": n, "pool": pool_n, "pct": round(100 * n / pool_n, 1) if pool_n else None,
                        "index": round(index, 2) if index is not None else None, "flag": flag,
                        "names": [r["name"] for r in top]}
        role_rows.append({"role": role, "total": total, "cells": cells})
    roles = {"columns": [{"key": c, "label": COLOR_NAMES[c], "total": col_totals[c]} for c in COLUMNS], "rows": role_rows}

    # ---- themes -----------------------------------------------------------------------------------------------
    themes = []
    for name, defn in _THEMES:
        members = _pool_members(defn, pool, groups, labels)
        pool_n = sum(1 for g in pool if _aliases(g["name"]) & members)
        owned_hits = [r for r in mine if _aliases(r["name"]) & members]
        if pool_n < 40:
            continue
        share = len(owned_hits) / pool_n
        index = share / baseline if baseline else 0
        level = "deep" if index >= 1.25 else ("thin" if index < 0.75 else "average")
        top = sorted(owned_hits, key=lambda r: (r.get("edhrec_rank") or 10**9))[:8]
        lists[f"theme:{name}"] = _sorted_cards(owned_hits)
        themes.append({"name": name, "owned": len(owned_hits), "pool": pool_n, "share": round(share * 100, 1),
                       "index": round(index, 2), "level": level, "examples": [r["name"] for r in top]})
    # Theme cards are also the better-known ones, so every theme tends to read "deep"; judge each against the middle theme.
    if themes:
        mid = sorted(t["index"] for t in themes)[len(themes) // 2] or 1
        for t in themes:
            t["index"] = round(t["index"] / mid, 2)
            t["level"] = "deep" if t["index"] >= 1.3 else ("thin" if t["index"] < 0.75 else "average")
    themes.sort(key=lambda t: -t["index"])

    # ---- types, curve, tribes ---------------------------------------------------------------------------------
    by_cat: dict[str, dict] = {}
    for r in owned_rows:
        d = by_cat.setdefault(r.get("category") or "Other", {"unique": 0, "copies": 0})
        d["unique"] += 1
        d["copies"] += r.get("quantity") or 1
    curve = [0] * 8
    cmc_sum = cmc_n = 0
    tribes: dict[str, int] = {}
    curve_rows: dict[int, list[dict]] = {i: [] for i in range(8)}
    tribe_rows: dict[str, list[dict]] = {}
    for r in mine:
        c = int(r.get("cmc") or 0)
        curve[min(c, 7)] += 1
        curve_rows[min(c, 7)].append(r)
        cmc_sum += float(r.get("cmc") or 0)
        cmc_n += 1
        for t in _creature_types(r.get("type_line")):
            tribes[t] = tribes.get(t, 0) + 1
            tribe_rows.setdefault(t, []).append(r)
    cat_rows: dict[str, list[dict]] = {}
    for r in owned_rows:
        cat_rows.setdefault(r.get("category") or "Other", []).append(r)
    for k, rows in cat_rows.items():
        lists[f"type:{k}"] = _sorted_cards(rows)
    for i, rows in curve_rows.items():
        lists[f"cmc:{i}"] = _sorted_cards(rows)
    for k, rows in tribe_rows.items():
        if tribes[k] >= 3:
            lists[f"tribe:{k}"] = _sorted_cards(rows)
    types = {
        "by_category": [{"type": k, **v} for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1]["unique"])],
        "curve": curve,
        "avg_cmc": round(cmc_sum / cmc_n, 2) if cmc_n else 0,
        "tribes": [{"type": k, "n": v} for k, v in sorted(tribes.items(), key=lambda kv: -kv[1])[:14]],
    }

    return {"snapshot": snapshot, "colors": colors, "roles": roles, "themes": themes, "types": types,
            "baseline_pct": round(baseline * 100, 1), "pool_size": len(pool), "_lists": lists}


def get_insights(collection_path: str, collection_mtime: float, load_owned, owned_view) -> dict:
    """build_insights with an in-memory cache keyed on the collection file and the card database's build time."""
    gp = gameplay_data_in_index()
    key = (collection_mtime, len(gp), id(gp))
    if _cache["key"] == key and _cache["data"] is not None:
        return _cache["data"]
    t = time.time()
    owned = load_owned(collection_path)
    data = build_insights(owned, owned_view(owned, gp))
    data["seconds"] = round(time.time() - t, 1)
    _cache.update(key=key, lists=data.pop("_lists"), data=data)
    return data


def cards_for(key: str) -> list[dict] | None:
    """The cards behind a number on the page ("role:Tutors:R", "theme:Tokens", "color:B", "type:Creatures", "cmc:3",
    "tribe:Elf"), or None for an unknown key. Only valid after get_insights has run."""
    return (_cache.get("lists") or {}).get(key)
