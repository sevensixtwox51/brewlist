"""Commander research -- pick ANY commander, see how people build it, how much of that build you own, and what it is weak to.

Read-only. It combines three things the app already has:
  * the card database (every Commander-legal commander, with EDHREC popularity rank),
  * EDHREC's own deck types for a commander ("archetypes") and the cards that define each one (edhrec_data.py; fetched
    once per commander, then cached, so only the first look at a commander not already cached needs the internet),
  * the Matchups analysis (matchups.py) run on the typical list for the chosen archetype.

"The typical list" is built from EDHREC's per-card inclusion counts for that archetype: the cards most often included,
filled to a 99-card deck using the average land split of real decks. It is a consensus of what people play, not any one
real decklist.
"""
from __future__ import annotations

from brewlist_core import gameplay_data_in_index, normalize_name
from edhrec_data import commander_data_problem, commander_profile, commander_theme_page
from matchups import _front, _lookup, _use_best_printings, analyze_matchups, commander_ranks

_cache: dict = {}


def _commander_rows() -> list[dict]:
    """Every Commander-legal commander in the card database (a legendary creature, or any card that says it can be your
    commander), one per card even when the database holds a double-faced card under two names."""
    pool = gameplay_data_in_index()
    cached = _cache.get("rows")
    if cached and cached[0] == id(pool):
        return cached[1]
    best: dict[str, dict] = {}
    for r in pool.values():
        tl = r.get("type_line") or ""
        if not ((("Legendary" in tl) and ("Creature" in tl)) or "can be your commander" in (r.get("oracle_text") or "")):
            continue
        if str((r.get("legalities") or {}).get("commander")).lower() != "legal":
            continue
        front = _front(r["name"])
        cur = best.get(front)
        if cur is None or (normalize_name(r["name"]) == front and normalize_name(cur["name"]) != front):
            best[front] = r
    rows = sorted(best.values(), key=lambda r: int(r.get("edhrec_rank") or 10**9))
    _cache["rows"] = (id(pool), rows)
    return rows


WINDOWS = ("2years", "month", "week")  # the same three time windows the builder's popularity sort offers
_UNRANKED = 10**9


_ranks = commander_ranks


def _rank_of(row: dict, window: str) -> int | None:
    r = _ranks().get(window if window in WINDOWS else "2years", {}).get(_front(row["name"]))
    return r if r is not None else None


def _card_for(row: dict, owned_ids: dict[str, str], window: str = "2years") -> dict:
    front = _front(row["name"])
    return {"name": row["name"], "id": owned_ids.get(front) or row.get("scryfall_id"), "color_identity": row.get("color_identity") or [],
            "rank": _rank_of(row, window), "owned": front in owned_ids}


def search_commanders(query: str, owned_ids: dict[str, str], limit: int = 24, window: str = "2years") -> list[dict]:
    """Commanders whose name contains the query, most popular AS A COMMANDER first (in the chosen time window);
    commanders nobody has played in that window come last. With no query: the most popular overall."""
    q = normalize_name(query or "")
    rows = _commander_rows()
    if q:
        words = q.split()
        starts = [r for r in rows if normalize_name(r["name"]).startswith(q)]
        has = [r for r in rows if r not in starts and all(w in normalize_name(r["name"]) for w in words)]
        rows = starts + has
    ranks = _ranks().get(window if window in WINDOWS else "2years", {})
    rows = sorted(rows, key=lambda r: (ranks.get(_front(r["name"]), _UNRANKED), int(r.get("edhrec_rank") or _UNRANKED)))
    return [_card_for(r, owned_ids, window) for r in rows[:limit]]


def commander_summary(name: str, owned_ids: dict[str, str], window: str = "2years") -> dict | None:
    row = _lookup(gameplay_data_in_index(), name)
    if not row:
        return None
    card = _card_for(row, owned_ids, window)
    card["type_line"] = row.get("type_line")
    return card


def archetypes(name: str) -> dict:
    """EDHREC's deck types for the commander, most played first: {"archetypes": [{slug, value, count, share}], "total",
    "problem"}. The first look at a commander EDHREC data isn't cached for needs the internet."""
    themes = (commander_profile(name) or {}).get("themes") or []
    total = max((t.get("count") or 0) for t in themes) if themes else 0
    out = [{"slug": t["slug"], "value": t.get("value") or t["slug"], "count": t.get("count") or 0} for t in themes[:12]]
    return {"archetypes": out, "total": total, "problem": None if out else commander_data_problem(name)}


def typical_deck(name: str, theme_slug: str, owned_ids: dict[str, str]) -> dict:
    """The consensus 99 for one archetype, how much of it you own, what is missing with prices, and the Matchups
    analysis of it."""
    pool = gameplay_data_in_index()
    cmd_row = _lookup(pool, name)
    if not cmd_row:
        return {"error": "That commander isn't in the card database."}
    page = commander_theme_page(name, theme_slug)
    if not page or not page.get("cards"):
        return {"error": commander_data_problem(name) or "EDHREC has no deck data for that archetype (or it can't be reached right now)."}
    cmd_ci = set(cmd_row.get("color_identity") or [])
    total_decks = page.get("num_decks") or max((c.get("num_decks") or 0) for c in page["cards"].values()) or 1

    nonland, lands = [], []
    for c in page["cards"].values():
        row = _lookup(pool, c["name"])
        if not row or _front(row["name"]) == _front(cmd_row["name"]):
            continue
        if str((row.get("legalities") or {}).get("commander")).lower() != "legal":
            continue
        if not set(row.get("color_identity") or []) <= cmd_ci:
            continue
        tl = row.get("type_line") or ""
        if "Basic" in tl:
            continue
        entry = {"name": row["name"], "id": row.get("scryfall_id"), "type_line": tl, "cmc": row.get("cmc") or 0,
                 "inclusion": round(100 * (c.get("num_decks") or 0) / total_decks), "decks": c.get("num_decks") or 0}
        (lands if "Land" in tl else nonland).append(entry)
    nonland.sort(key=lambda e: -e["decks"])
    lands.sort(key=lambda e: -e["decks"])

    avg_basic, avg_nonbasic = page.get("avg_basic"), page.get("avg_nonbasic")
    land_total = round(avg_basic + avg_nonbasic) if isinstance(avg_basic, (int, float)) and isinstance(avg_nonbasic, (int, float)) else 37
    land_total = max(30, min(42, land_total))
    nonbasic_n = max(0, min(25, round(avg_nonbasic))) if isinstance(avg_nonbasic, (int, float)) else 8
    deck = nonland[:99 - land_total] + lands[:nonbasic_n]
    basics = 99 - len(deck)

    result = {"commander": {"name": cmd_row["name"], "id": cmd_row.get("scryfall_id")},
              "cards": deck, "basics": max(0, basics), "total_decks": total_decks,
              "avg_creatures": page.get("avg_creatures"), "avg_planeswalkers": page.get("avg_planeswalkers"),
              "matchups": analyze_matchups([{"name": e["name"]} for e in deck], {"name": cmd_row["name"]}, owned_ids)}
    # printings, owned flags and cheapest prices for every card reference in the result (cards, commander, pills)
    _use_best_printings(result, owned_ids)
    missing = sorted((e for e in deck if not e.get("owned")), key=lambda e: -e["decks"])
    priced = [e["price"] for e in missing if e.get("price") is not None]
    result["owned_count"] = len(deck) - len(missing)
    result["total_count"] = len(deck)
    result["owned_pct"] = round(100 * result["owned_count"] / len(deck)) if deck else 0
    result["missing_cost"] = round(sum(priced), 2)
    result["missing_unpriced"] = len(missing) - len(priced)
    result["note"] = (f"The {len(deck)} most-played non-basic cards across {total_decks:,} {theme_slug.replace('-', ' ')} decks, "
                      f"plus about {max(0, basics)} basic lands. A consensus of what people play, not one real decklist.")
    return result
