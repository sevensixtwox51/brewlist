"""Collection insights: where a ManaBox collection is deep and where it is thin.

A read-only look at what you own, separate from the Deck Builder and the compare flow (they never import this).
Everything comes from data the app already has locally -- the ManaBox export, the card database's per-card
gameplay data, and Scryfall's Oracle Tags -- so it works offline and costs no network calls.

  - snapshot:  how many cards, copies, market value, foils, sets.
  - colors:    unique nonland cards per color identity.
  - roles:     a role x color grid (ramp, draw, removal, wipes, counterspells, tutors, recursion, ...): how many
               cards you own for each job in each color, and how many such cards exist in the game. The page shades
               each row on its own scale, red to green.
  - themes:    graveyard, sacrifice, tokens, counters... with how much of each theme in the whole game you own,
               compared with your other themes (every theme reads high against the game as a whole, since themed
               cards are the better-known ones).
  - types:     card types, mana curve, and your deepest creature types.

Oracle Tags are crowd-made and uneven, so treat the counts as "roughly how deep", not exact numbers.
"""

from __future__ import annotations

import re
import threading
import time

from brewlist_core import (
    gameplay_data_in_index,
    budget_alt_data_in_index,
    normalize_name,
    categorize,
    price_for_printing,
    active_price_stores,
    best_offer,
    prices_data_in_index,
    scryfall_prices_in_index,
    sets_data_in_index,
)
from deck_builder import _NON_LIBRARY_TYPE_RE, _ROLE_TAG_LABELS, _role_member_names, land_searcher_names

COLOR_ORDER = ["W", "U", "B", "R", "G"]
COLOR_NAMES = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green", "M": "Multicolor", "C": "Colorless"}
COLUMNS = COLOR_ORDER + ["M", "C"]

# Tag labels that are Tagger housekeeping or one-off jokes (same idea as tag_synergy._JUNK).
_JUNK = re.compile(r"achievement|flowstone|inscryption|set.s mechanic|set mechanic|conjure|^sneak-|vanilla|auraify|more expensive")

_INTERACTION = _ROLE_TAG_LABELS["Interaction"]
# Ramp, Card draw and the three Interaction rows start from the builder's own cleaned role lists (deck_builder.
# _role_member_names), so this page never disagrees with the builder: that cleanup drops "end the turn" cards like
# Sundial of the Infinite from Interaction, "pay life" cards that draw nothing from Draw, land-fetch removal that
# ramps the opponent from Ramp, and so on. "within" narrows the cleaned Interaction list to one kind by tag.
_ROLES: list[tuple[str, dict]] = [
    ("Ramp", {"clean": "Ramp"}),
    ("Card draw", {"clean": "Draw"}),
    ("Spot removal", {"within": "Interaction", "labels": {l for l in _INTERACTION if not l.startswith(("sweeper", "counterspell"))}}),
    ("Board wipes", {"within": "Interaction", "labels": {l for l in _INTERACTION if l.startswith("sweeper")}}),
    ("Counterspells", {"within": "Interaction", "labels": {l for l in _INTERACTION if l.startswith("counterspell")}}),
    ("Tutors", {"regex": r"^tutor-(?!land)", "tutors": True}),
    ("Land search", {"landsearch": True}),  # landcycling, Pilgrim's Eye, Cultivate...: fixing and land drops, not tutors
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
    # "counters matter" alone is every kind of counter (burden, charge, oil, shield...), so only the +1/+1 ("pp") tags count
    ("+1/+1 counters", {"regex": r"^pp counters matter|gives pp counters|proliferate", "oracle": r"\+1/\+1 counters?"}),
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
_cache_lock = threading.Lock()  # the page asks for the insights and the card browser at once; compute them once


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


def _expand(names) -> set[str]:
    out: set[str] = set()
    for n in names:
        out |= _aliases(n)
    return out


def _pool_members(defn: dict, pool: list[dict], groups: dict, labels: dict, cleaned: dict | None = None) -> set[str]:
    """Normalized names (front face included) of pool cards that fit a role/theme definition."""
    members: set[str] = set()
    if defn.get("clean") and cleaned:
        return _expand(cleaned.get(defn["clean"]) or ())
    if defn.get("labels") or defn.get("regex"):
        members |= _tag_members(_definition_filter(defn), groups, labels)
    if defn.get("landsearch"):
        return _expand(land_searcher_names(yours_only=True))
    if defn.get("tutors"):  # land-only searchers (landcycling, Cultivate...) carry the generic tutor tags too: not tutors
        lands = _tag_members(lambda l: l.startswith("tutor-land"), groups, labels)
        specific = _tag_members(lambda l: l.startswith("tutor-") and not l.startswith("tutor-land")
                                and l not in ("tutor-to-hand", "tutor-card", "tutor-to-top"), groups, labels)
        members -= (lands | _expand(land_searcher_names())) - specific
    if defn.get("within") and cleaned:
        members &= _expand(cleaned.get(defn["within"]) or ())
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


MISSING_PER_KEY = 60


def _rank(g: dict) -> int:
    try:
        return int(g.get("edhrec_rank") or 10**9)
    except (TypeError, ValueError):
        return 10**9


def _missing_card(g: dict, price_data: dict) -> dict:
    """A card you don't own, for the "popular to add" view: its EDHREC rank and the cheapest nonfoil price found."""
    entry = (price_data.get(normalize_name(g["name"])) or price_data.get(normalize_name(g["name"]).split(" // ")[0]) or {})
    best = best_offer(entry)  # cheapest among the stores picked on the Decks page (never Cardmarket: EUR)
    return {"name": g["name"], "scryfall_id": g.get("scryfall_id"), "type_line": g.get("type_line"), "mana_cost": g.get("mana_cost"),
            "layout": g.get("layout"), "rank": _rank(g) if _rank(g) < 10**9 else None, "price": best[1] if best else None,
            "store": best[0] if best else None, "url": best[2] if best else None}


def _one_per_card(cards: list[dict]) -> list[dict]:
    """The card database can hold the same card more than once: a double-faced card as "A // B" and as "A", or a
    reversible printing as "Sol Ring // Sol Ring" next to "Sol Ring". Count each once, preferring the plain
    front-face entry, so game-wide totals aren't inflated and a card isn't suggested twice."""
    best: dict[str, tuple[bool, dict]] = {}
    for g in cards:
        full = normalize_name(g["name"])
        front = full.split(" // ")[0]
        cur = best.get(front)
        if cur is None or (full == front and cur[0]):
            best[front] = (full != front, g)
    return [g for _, g in best.values()]


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
    pool = _one_per_card([g for g in gameplay.values()
                          if _legal(g) and not _is_land(g.get("type_line")) and not _NON_LIBRARY_TYPE_RE.search(g.get("type_line") or "")])
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
    # Each cell is a count of cards you own, with the number that exist in the whole game for that role and color so
    # the page can also show the share you own. How it is shaded is decided on the page, across each row.
    cleaned = _role_member_names(groups, labels)
    role_members: dict[str, set[str]] = {}
    role_rows = []
    for role, defn in _ROLES:
        members = _pool_members(defn, pool, groups, labels, cleaned)
        role_members[role] = members
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
        cells = {}
        for c in COLUMNS:
            n = len(hits[c])
            pool_n = pool_hits[c]
            top = sorted(hits[c], key=lambda r: (r.get("edhrec_rank") or 10**9))[:12]
            lists[f"role:{role}:{c}"] = _sorted_cards(hits[c])
            cells[c] = {"n": n, "pool": pool_n, "pct": round(100 * n / pool_n, 1) if pool_n else None,
                        "names": [r["name"] for r in top],
                        "top": [{"name": r["name"], "id": r.get("scryfall_id")} for r in top[:5]]}
        role_rows.append({"role": role, "total": total, "cells": cells})
    roles = {"columns": [{"key": c, "label": COLOR_NAMES[c], "total": col_totals[c]} for c in COLUMNS], "rows": role_rows}

    # ---- themes -----------------------------------------------------------------------------------------------
    themes = []
    theme_members: dict[str, set[str]] = {}
    for name, defn in _THEMES:
        members = _pool_members(defn, pool, groups, labels)
        theme_members[name] = members
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
                       "index": round(index, 2), "level": level, "examples": [r["name"] for r in top],
                       "top": [{"name": r["name"], "id": r.get("scryfall_id")} for r in top[:5]]})
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

    # ---- the most popular cards you don't own, behind every number (the "popular to add" view) ----------------
    price_data = prices_data_in_index()
    missing: dict[str, list[dict]] = {}
    pool_aliases = {id(g): _aliases(g["name"]) for g in pool}
    not_owned = sorted((g for g in pool if not (pool_aliases[id(g)] & my_names.keys())), key=_rank)
    wanted_tribes = {k.split(":", 1)[1] for k in lists if k.startswith("tribe:")}

    def add(key: str, g: dict) -> None:
        bucket_list = missing.setdefault(key, [])
        if len(bucket_list) < MISSING_PER_KEY:
            bucket_list.append(_missing_card(g, price_data))

    for g in not_owned:
        if _rank(g) >= 10**9:
            break  # unranked cards come last, and aren't "popular"
        al, b = pool_aliases[id(g)], _color_bucket(g.get("color_identity"))
        add(f"color:{b}", g)
        for role, members in role_members.items():
            if al & members:
                add(f"role:{role}:{b}", g)
        for name, members in theme_members.items():
            if al & members:
                add(f"theme:{name}", g)
        add(f"type:{categorize(g.get('type_line') or '')}", g)
        add(f"cmc:{min(int(g.get('cmc') or 0), 7)}", g)
        for t in _creature_types(g.get("type_line")):
            if t in wanted_tribes:
                add(f"tribe:{t}", g)

    browse = _browse_payload(owned, owned_rows, prices, sets, role_members, theme_members, [r["role"] for r in role_rows], [t["name"] for t in themes], snapshot["value"])
    return {"snapshot": snapshot, "colors": colors, "roles": roles, "themes": themes, "types": types,
            "baseline_pct": round(baseline * 100, 1), "pool_size": len(pool), "_lists": lists, "_missing": missing, "_browse": browse}


def _browse_payload(owned: dict, owned_rows: list[dict], prices: dict, sets: dict, role_members: dict, theme_members: dict,
                    role_names: list[str], theme_names: list[str], total_value: float) -> dict:
    """Every card you own (lands too) with what the card browser filters and totals on: type, color, mana value, the sets
    of the printings you own, which roles and themes it fills (as indexes into the two name lists), and what it is worth.
    Worth is the market price of the exact printings you hold (the same price the Market value tile adds up); top/low are
    the priciest and cheapest single copy."""
    role_idx = {n: i for i, n in enumerate(role_names)}
    theme_idx = {n: i for i, n in enumerate(theme_names)}
    cards = []
    set_counts: dict[str, int] = {}
    for r in owned_rows:
        key = normalize_name(r["name"])
        oc = owned.get(key) or owned.get(key.split(" // ")[0])
        value = 0.0
        copies_priced = 0
        copy_prices: list[float] = []  # one entry per printing you hold; printings of one card can differ a lot (foil vs not)
        codes: list[str] = []
        if oc:
            for p in oc.printings:
                sp = prices.get(p.scryfall_id)
                price = price_for_printing(sp, p) if sp else None
                if price is not None:
                    value += price * p.quantity
                    copies_priced += p.quantity
                    copy_prices.append(price)
                code = (p.set_code or "").upper()
                if code and code not in codes:
                    codes.append(code)
        for c in codes:
            set_counts[c] = set_counts.get(c, 0) + 1
        al = _aliases(r["name"])
        cards.append({
            "name": r["name"], "id": r.get("scryfall_id"), "layout": r.get("layout"), "type_line": r.get("type_line"),
            "category": r.get("category"), "mana_cost": r.get("mana_cost"), "cmc": r.get("cmc") or 0,
            "ci": r.get("color_identity") or [], "qty": r.get("quantity") or 1, "foil": (oc.foil if oc else 0),
            "value": round(value, 2) if copies_priced else None,
            # the priciest and cheapest single copy: a card held in two printings is not worth their average
            "top": round(max(copy_prices), 2) if copy_prices else None,
            "low": round(min(copy_prices), 2) if copy_prices else None,
            "unpriced": (r.get("quantity") or 1) - copies_priced if copies_priced else (r.get("quantity") or 1),
            "sets": codes, "rank": r.get("edhrec_rank"),
            "roles": [role_idx[n] for n, m in role_members.items() if n in role_idx and al & m],
            "themes": [theme_idx[n] for n, m in theme_members.items() if n in theme_idx and al & m],
        })
    set_list = sorted(({"code": c, "name": (sets.get(c) or {}).get("name") or c, "release": (sets.get(c) or {}).get("release_date") or "", "n": n}
                       for c, n in set_counts.items()), key=lambda s: s["release"], reverse=True)
    cats = sorted({c["category"] for c in cards if c["category"]})
    return {"cards": cards, "roles": role_names, "themes": theme_names, "sets": set_list, "categories": cats, "total_value": total_value}


def get_insights(collection_path: str, collection_mtime: float, load_owned, owned_view) -> dict:
    """build_insights with an in-memory cache keyed on the collection file and the card database's build time."""
    gp = gameplay_data_in_index()
    key = (collection_mtime, len(gp), id(gp), tuple(active_price_stores()))  # prices follow the picked stores
    with _cache_lock:
        if _cache["key"] == key and _cache["data"] is not None:
            return _cache["data"]
        t = time.time()
        owned = load_owned(collection_path)
        data = build_insights(owned, owned_view(owned, gp))
        data["seconds"] = round(time.time() - t, 1)
        _cache.update(key=key, lists=data.pop("_lists"), missing=data.pop("_missing"), browse=data.pop("_browse"), data=data)
        return data


def browse_cards() -> dict | None:
    """Every owned card with its filterable facts (see _browse_payload). Only valid after get_insights has run."""
    return _cache.get("browse")


def missing_for(key: str) -> list[dict] | None:
    """The most popular Commander-legal cards behind a number that you DON'T own, most popular first."""
    return (_cache.get("missing") or {}).get(key)


def cards_for(key: str) -> list[dict] | None:
    """The cards behind a number on the page ("role:Tutors:R", "theme:Tokens", "color:B", "type:Creatures", "cmc:3",
    "tribe:Elf"), or None for an unknown key. Only valid after get_insights has run."""
    return (_cache.get("lists") or {}).get(key)
