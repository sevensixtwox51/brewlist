"""Build with AI -- an agentic deck-building loop using Claude's tool use,
layered on top of deck_builder.py's existing legality/color/ownership
filtering rather than replacing it.

Why this exists: Suggest/Optimize (deck_builder.py) are entirely
Oracle-Tag/combo-database heuristics -- they have zero understanding of
what a commander's own rules text actually says. A commander whose power
comes from a specific, narrow interaction (e.g. "activated abilities of
Elf cards in your graveyard") has no matching Oracle Tag, so the
tag-based engine structurally cannot steer toward it. This module hands
Claude real tools over a real candidate pool -- never a free-form card
list, every add is validated against real data, same "don't trust it
blindly" discipline every other AI-adjacent feature in this app follows
-- and lets it read the commander's real oracle text, search for whatever
it decides is relevant, and build a deck across multiple turns.

Three modes, one loop (`run_ai_build()`'s `wip_entries`/`scope` params):
- Fresh build: no `wip_entries` given -- starts from just the commander.
- Improve an existing deck: `wip_entries` seeded with the WIP deck's own
  cards -- the loop keeps/builds on them instead of starting over.
- Import & improve (`scope="any"`): the candidate pool is every real
  paper card MTGJSON knows about (see _full_card_pool), not just owned
  ones -- for improving an imported decklist that may need new purchases.
  `scope="owned"` (the default) keeps every add scoped to the real owned
  collection, same guarantee the original single-mode version had.

No UI dependencies -- imported by app.py, same relationship
deck_builder.py/brewlist_core.py already have to app.py.
"""

from __future__ import annotations

import json
import os
import time
from typing import Callable

import anthropic

from brewlist_core import (
    CardEntry,
    find_deck_combos,
    game_changers_in_index,
    gameplay_data_in_index,
    normalize_name,
    prices_data_in_index,
)
from deck_builder import _filter_candidates, _INTENDED_BRACKET_GC_CAP, categorize

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
AI_CONFIG_PATH = os.path.join(_MODULE_DIR, "data", "ai_config.json")

_MODEL = "claude-sonnet-5"
_MAX_TURNS = 60
_MAX_SECONDS = 420
_MAX_TOKENS_PER_TURN = 4096


def load_api_key() -> str | None:
    """ANTHROPIC_API_KEY env var wins if set (same precedence as this
    app's existing PORT/HOST/NO_BROWSER env-var reads in app.py) --
    otherwise falls back to the locally-saved key. Never logged, never
    returned to a browser response."""
    env_key = os.environ.get("ANTHROPIC_API_KEY")
    if env_key:
        return env_key
    if not os.path.isfile(AI_CONFIG_PATH):
        return None
    try:
        with open(AI_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("anthropic_api_key") or None
    except (OSError, ValueError):
        return None


def key_source() -> str | None:
    """"env" | "file" | None, for the /ai/status endpoint -- tells the UI
    whether a key is configured without ever exposing the key itself."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "env"
    if os.path.isfile(AI_CONFIG_PATH) and load_api_key():
        return "file"
    return None


def save_api_key(key: str) -> None:
    key = (key or "").strip()
    if not key:
        raise ValueError("API key is empty.")
    os.makedirs(os.path.dirname(AI_CONFIG_PATH), exist_ok=True)
    with open(AI_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"anthropic_api_key": key}, f)
    # Belt-and-suspenders on a multi-user machine -- readable/writable by
    # the owner only, not the world-readable default umask would give it.
    os.chmod(AI_CONFIG_PATH, 0o600)


def clear_api_key() -> None:
    if os.path.isfile(AI_CONFIG_PATH):
        os.remove(AI_CONFIG_PATH)


def validate_api_key(key: str) -> str | None:
    """Cheap live call to confirm a key actually works before saving it --
    returns None on success, an error message string on failure. Costs
    effectively nothing (max_tokens=1)."""
    try:
        client = anthropic.Anthropic(api_key=key)
        client.messages.create(model=_MODEL, max_tokens=1, messages=[{"role": "user", "content": "hi"}])
        return None
    except anthropic.AuthenticationError:
        return "That key was rejected by Anthropic -- double check it was copied correctly."
    except Exception as e:
        return f"Could not reach Anthropic to validate the key: {e}"


def _full_card_pool(
    wip_entries: list[CardEntry],
    deck_format: str,
    target_format: str | None,
    commander_color_identity: list[str] | None,
    intended_bracket: str | None,
) -> list[dict]:
    """Every real, paper-legal, color-correct, not-already-in-deck card
    MTGJSON knows about -- the scope="any" equivalent of deck_builder.py's
    _filter_candidates, sourced from the full gameplay_data_in_index()
    instead of one player's owned collection. Mirrors _filter_candidates'
    legality/color-identity/Game-Changer-cap rules exactly, so a
    scope="any" build respects the same constraints a scope="owned" one
    does -- just without the ownership gate."""
    gameplay = gameplay_data_in_index()
    used_names = {normalize_name(e.name) for e in wip_entries}
    legality_key = "commander" if deck_format == "commander" else (target_format or "")
    colors_allowed = set(commander_color_identity) if deck_format == "commander" and commander_color_identity is not None else None
    if colors_allowed is None and deck_format != "commander":
        used_colors: set[str] = set()
        for e in wip_entries:
            used_colors.update(e.color_identity or [])
        colors_allowed = used_colors or None

    game_changers = game_changers_in_index() if deck_format == "commander" else set()
    gc_cap = _INTENDED_BRACKET_GC_CAP.get(intended_bracket or "")
    gc_at_cap = False
    if deck_format == "commander" and gc_cap is not None:
        current_gc_count = sum(e.quantity for e in wip_entries if normalize_name(e.name) in game_changers)
        gc_at_cap = current_gc_count >= gc_cap

    # MTGJSON indexes a multi-faced card (split/DFC/Adventure) under BOTH
    # its full "Front // Back" name and each individual face name, all
    # sharing one scryfall_id -- iterating the whole dict as-is would
    # surface the same physical card up to 3x under different-looking
    # names (confirmed live: "Thranduil, Sindarin Liege", "... // Silvan
    # Rally", and "Silvan Rally" all appeared as separate search results
    # for one owned card, and the bare-face-name variant got a false
    # owned=False annotation since owned_view records it under the full
    # name). Dedupe by scryfall_id first, preferring whichever variant
    # contains " // " -- the full-name convention this app already uses
    # everywhere else (owned_view, Moxfield/Archidekt import/export).
    deduped: dict[str, dict] = {}
    for nm, gp in gameplay.items():
        key = gp.get("scryfall_id") or f"noid:{nm}"
        existing = deduped.get(key)
        name = gp.get("name") or nm
        if existing is None or ("//" in name and "//" not in (existing.get("name") or "")):
            deduped[key] = gp

    candidates = []
    for gp in deduped.values():
        name = gp.get("name") or ""
        if normalize_name(name) in used_names:
            continue
        nm = normalize_name(name)
        if gc_at_cap and nm in game_changers:
            continue
        color_identity = gp.get("color_identity") or []
        if colors_allowed is not None and not set(color_identity).issubset(colors_allowed):
            continue
        if legality_key:
            legality = (gp.get("legalities") or {}).get(legality_key)
            if legality_key == "commander":
                if legality and legality != "Legal":
                    continue
            elif legality != "Legal":
                continue
        candidates.append({
            "name": name,
            "type_line": gp.get("type_line") or "",
            "mana_cost": gp.get("mana_cost") or "",
            "cmc": gp.get("cmc") or 0,
            "color_identity": color_identity,
            "oracle_text": gp.get("oracle_text") or "",
            "scryfall_id": gp.get("scryfall_id") or None,
            "category": categorize(gp.get("type_line") or ""),
            "set_code": "",
            "collector_number": "",
        })
    return candidates


def _build_tools(scope: str) -> list[dict]:
    if scope == "owned":
        search_name = "search_owned_collection"
        search_desc = (
            "Search the player's owned, format-legal, color-identity-correct cards that aren't "
            "already in the deck. Matches your query against each card's name, type line, and "
            "full oracle (rules) text -- use this to look for anything relevant: a creature type "
            "(e.g. \"Elf\"), a mechanic or keyword (e.g. \"graveyard\", \"proliferate\", \"mill\"), "
            "a card's own name, or a broad category (e.g. \"land\", \"removal\"). Returns up to 25 "
            "matches with full oracle text so you can judge fit yourself."
        )
        add_desc = "Adds one owned, legal, color-correct card to the deck library. Must be a card name returned by search_owned_collection."
    else:
        search_name = "search_cards"
        search_desc = (
            "Search EVERY real, format-legal, color-identity-correct Magic card, owned or not, that "
            "isn't already in the deck. Matches your query against each card's name, type line, and "
            "full oracle (rules) text. Each result includes \"owned\": true/false and, when not "
            "owned, a rough \"price_usd\" -- prefer an owned card when it's reasonably close in "
            "quality to an unowned alternative, and mention price whenever you suggest buying "
            "something new. Returns up to 25 matches."
        )
        add_desc = "Adds one real, legal, color-correct card to the deck library, owned or not. Must be a card name returned by search_cards."
    return [
        {
            "name": search_name,
            "description": search_desc,
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "A word or short phrase to search for."}},
                "required": ["query"],
            },
        },
        {
            "name": "get_deck_state",
            "description": "Returns every card currently in the deck (commander + library), with counts.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "add_card",
            "description": add_desc,
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": f"Exact card name, as returned by {search_name}."},
                    "reason": {"type": "string", "description": "One sentence on why this card belongs in the deck."},
                },
                "required": ["name", "reason"],
            },
        },
        {
            "name": "remove_card",
            "description": "Removes one card from the deck library (not the commander).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "reason": {"type": "string", "description": "One sentence on why this card is being cut."},
                },
                "required": ["name", "reason"],
            },
        },
        {
            "name": "check_combos",
            "description": "Checks the current deck against Commander Spellbook's live combo database -- returns combos already fully assembled, and notable combos the deck is exactly one card away from.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "finish_deck",
            "description": "Call this once the deck is complete at exactly the target library size. Fails with the exact count still needed/over if the size doesn't match yet.",
            "input_schema": {
                "type": "object",
                "properties": {"summary": {"type": "string", "description": "A short overall strategy summary for this deck, shown to the player."}},
                "required": ["summary"],
            },
        },
    ]


def _card_summary(c: dict, scope: str, owned_names: set[str], prices: dict[str, dict]) -> dict:
    text = c.get("oracle_text") or ""
    summary = {
        "name": c["name"],
        "type_line": c.get("type_line") or "",
        "mana_cost": c.get("mana_cost") or "",
        "cmc": c.get("cmc") or 0,
        "oracle_text": text[:400],
    }
    if scope == "any":
        nm = normalize_name(c["name"])
        summary["owned"] = nm in owned_names
        nonfoil = (prices.get(nm) or {}).get("nonfoil") or []
        summary["price_usd"] = nonfoil[0][1] if nonfoil else None
    return summary


def run_ai_build(
    commander: dict,
    deck_format: str,
    target_format: str | None,
    target_size: int,
    commander_color_identity: list[str] | None,
    intended_bracket: str | None,
    user_notes: str,
    owned_view: list[dict],
    api_key: str,
    on_progress: Callable[[int, int, str, list[str], list[dict]], None],
    wip_entries: list[CardEntry] | None = None,
    scope: str = "owned",
) -> dict:
    """Runs the agentic build loop. Returns {"suggestions": [...] (same
    shape suggest_builder_cards()/optimize_builder_combos() already
    return, so the existing suggestions-panel JS works unmodified),
    "removed": [names of cards the AI cut from a pre-existing deck],
    "log": [...], "finished": bool, "summary": str, "error": str|None}.

    `wip_entries`, if given, seeds the loop with an existing deck
    (commander + library) instead of starting from just the commander --
    this is what makes "improve the current deck" and "import & improve a
    decklist" both just different callers of the same loop rather than
    separate code paths. `scope="any"` widens every search/add tool to the
    full card database (see _full_card_pool) instead of the owned
    collection -- used for the import/improve mode, where the point is
    often to find cards worth *buying*, not just what's already owned.

    Never applies anything to a real saved brew -- this only ever builds
    up an in-memory wip_entries list and hands back suggestions for the
    caller to let the user review/apply, same "never auto-applied"
    convention Suggest/Optimize already use."""
    library_target = target_size - 1 if deck_format == "commander" else target_size
    if wip_entries is None:
        wip_entries = [CardEntry(
            name=commander["name"], quantity=1, type_line=commander.get("type_line", ""),
            is_foil=False, section="commander", scryfall_id=commander.get("scryfall_id"),
            color_identity=commander.get("color_identity") or [],
            set_code=commander.get("set_code", ""), collector_number=commander.get("collector_number", ""),
        )]
    else:
        wip_entries = list(wip_entries)  # own copy -- dispatch() mutates this in place

    starting_library_count = sum(e.quantity for e in wip_entries if e.section != "commander")
    added_by_name: dict[str, dict] = {}
    removed_names: list[str] = []
    log: list[str] = []
    client = anthropic.Anthropic(api_key=api_key)
    owned_names = {normalize_name(c["name"]) for c in owned_view}
    prices = prices_data_in_index() if scope == "any" else {}
    tools = _build_tools(scope)
    search_tool_name = tools[0]["name"]
    pool_label = "owned collection" if scope == "owned" else "full card database"

    # emit() can log several tool calls within one API turn (Claude often
    # batches multiple tool_use blocks in a single response) -- done/total
    # need to reflect actual API turns against _MAX_TURNS, not len(log)
    # tool-call entries, or the count can run past the stated total and
    # look broken (e.g. "step 87/60") even though nothing is actually
    # wrong. turn_ref is a mutable holder so the for-loop below can update
    # it and this closure can read the current value.
    turn_ref = {"value": 0}

    def emit(stage: str):
        log.append(stage)
        on_progress(turn_ref["value"], _MAX_TURNS, stage, log, _deck_state_view(wip_entries))

    def _deck_state_view(entries: list[CardEntry]) -> list[dict]:
        return [{"name": e.name, "quantity": e.quantity, "section": e.section, "category": categorize(e.type_line)} for e in entries]

    def library_count() -> int:
        return sum(e.quantity for e in wip_entries if e.section != "commander")

    def current_pool() -> list[dict]:
        if scope == "owned":
            return _filter_candidates(wip_entries, owned_view, deck_format, target_format, commander_color_identity, intended_bracket, None)
        return _full_card_pool(wip_entries, deck_format, target_format, commander_color_identity, intended_bracket)

    def dispatch(tool_name: str, tool_input: dict) -> str:
        if tool_name in ("search_owned_collection", "search_cards"):
            query = (tool_input.get("query") or "").strip().lower()
            matches = [
                c for c in current_pool()
                if query in c["name"].lower() or query in (c.get("type_line") or "").lower() or query in (c.get("oracle_text") or "").lower()
            ][:25]
            emit(f'Searched {pool_label} for "{tool_input.get("query")}" -- {len(matches)} match(es)')
            return json.dumps([_card_summary(c, scope, owned_names, prices) for c in matches])

        if tool_name == "get_deck_state":
            return json.dumps(_deck_state_view(wip_entries))

        if tool_name == "add_card":
            name = (tool_input.get("name") or "").strip()
            reason = (tool_input.get("reason") or "").strip()
            nm = normalize_name(name)
            existing = next((e for e in wip_entries if e.section != "commander" and normalize_name(e.name) == nm), None)
            if deck_format == "commander" and existing:
                return json.dumps({"ok": False, "error": f"{name} is already in the deck (singleton format -- only one copy allowed)."})
            if existing:
                owned_qty = next((c["quantity"] for c in owned_view if normalize_name(c["name"]) == nm), 0)
                max_copies = min(4, owned_qty) if scope == "owned" else 4
                if existing.quantity >= max_copies:
                    return json.dumps({"ok": False, "error": f"Already at the max {max_copies} cop{'y' if max_copies == 1 else 'ies'} of {name}."})
                existing.quantity += 1
                added_by_name.setdefault(nm, {
                    "name": existing.name, "scryfall_id": existing.scryfall_id, "category": categorize(existing.type_line),
                    "type_line": existing.type_line, "color_identity": existing.color_identity,
                    "cmc": 0, "mana_cost": "", "set_code": existing.set_code, "collector_number": existing.collector_number,
                })["reason"] = reason
                emit(f"Added another copy of {existing.name} ({existing.quantity} total) -- {reason}")
                return json.dumps({"ok": True, "library_count": library_count(), "library_target": library_target})
            match = next((c for c in current_pool() if normalize_name(c["name"]) == nm), None)
            if not match:
                return json.dumps({"ok": False, "error": f"{name} isn't in the {pool_label} pool -- {search_tool_name} first to confirm the exact name."})
            wip_entries.append(CardEntry(
                name=match["name"], quantity=1, type_line=match["type_line"], is_foil=False, section="mainboard",
                scryfall_id=match["scryfall_id"], color_identity=match["color_identity"],
                set_code=match.get("set_code", ""), collector_number=match.get("collector_number", ""),
            ))
            added_by_name[nm] = {**match, "reason": reason}
            emit(f"Added {match['name']} -- {reason}")
            return json.dumps({"ok": True, "library_count": library_count(), "library_target": library_target})

        if tool_name == "remove_card":
            name = (tool_input.get("name") or "").strip()
            reason = (tool_input.get("reason") or "").strip()
            nm = normalize_name(name)
            match = next((e for e in wip_entries if e.section != "commander" and normalize_name(e.name) == nm), None)
            if not match:
                return json.dumps({"ok": False, "error": f"{name} isn't in the deck."})
            if match.quantity > 1:
                match.quantity -= 1
            else:
                wip_entries.remove(match)
                added_by_name.pop(nm, None)
                if nm not in {normalize_name(x) for x in removed_names}:
                    removed_names.append(match.name)
            emit(f"Removed {match.name} -- {reason}")
            return json.dumps({"ok": True, "library_count": library_count(), "library_target": library_target})

        if tool_name == "check_combos":
            emit("Checking Commander Spellbook for combos in the current deck...")
            combos = find_deck_combos(wip_entries)
            if combos is None:
                return json.dumps({"error": "Combo lookup unavailable right now."})
            return json.dumps({
                "included": [{"uses": c["uses"], "produces": c["produces"]} for c in combos["included"]],
                "almost_included": [{"uses": c["uses"], "missing": c["missing"], "produces": c["produces"]} for c in combos["almost_included"]],
            })

        if tool_name == "finish_deck":
            count = library_count()
            if count != library_target:
                diff = library_target - count
                verb = "add" if diff > 0 else "remove"
                return json.dumps({"ok": False, "error": f"Deck has {count} library cards, needs exactly {library_target} -- {verb} {abs(diff)} more before finishing."})
            emit(f"Deck complete at {count} cards.")
            return json.dumps({"ok": True})

        return json.dumps({"error": f"Unknown tool {tool_name}"})

    pool_desc = "the player's owned cards" if scope == "owned" else "any real Magic card, owned or not (each search result says whether it's owned and, if not, a rough price)"
    deck_state_note = (
        f"The deck library already has {starting_library_count} card(s) chosen -- keep them unless one is "
        "clearly wrong for the plan, and focus on filling the remaining slots and making targeted "
        "improvements rather than rebuilding from scratch.\n"
        if starting_library_count > 0 else
        "The deck library is currently empty -- build it from scratch.\n"
    )
    system_prompt = (
        f"You are building/improving a real, legal Magic: The Gathering deck using {pool_desc}.\n\n"
        f"Commander: {commander['name']}\n"
        f"Type line: {commander.get('type_line', '')}\n"
        f"Oracle text: {commander.get('oracle_text', '') or '(not available)'}\n"
        f"Color identity: {', '.join(commander_color_identity or []) or 'colorless'}\n"
        f"Format: {deck_format}" + (f" (target legality: {target_format})" if target_format else "") + "\n"
        f"Target library size: exactly {library_target} cards (plus the commander already in the deck).\n"
        + deck_state_note
        + (f"Intended power bracket: {intended_bracket}\n" if intended_bracket else "")
        + (f"Player's notes on what they want: {user_notes}\n" if user_notes else "")
        + f"\nRead the commander's own oracle text carefully -- build around what it ACTUALLY does, not just "
        f"its colors. Use {search_tool_name} to look for anything relevant (creature types, keywords, "
        f"mechanics mentioned in the commander's text) rather than only generic staples. You may only add cards "
        f"returned by {search_tool_name} -- never invent a card name.\n\n"
        f"WORKFLOW -- this matters, and your turn budget is limited: searching does not build the deck, add_card "
        "does. After every search (or every 1-2 searches at most), call add_card for the best matches you just "
        "found before searching again -- and when a search returns several good matches, add 3-5 of them in the "
        "same batch, not just one at a time. Do not run more than two searches in a row without adding anything.\n\n"
        "NEVER search for a specific famous card's exact name on a guess (\"Sol Ring\", \"Cultivate\", \"Rhystic "
        "Study\", a named legendary creature, etc.) unless a broader search or the deck state already gave you "
        "real reason to think it's a good match -- most name guesses return zero matches and burn a turn for "
        "nothing. For a staple EFFECT you want (ramp, a board wipe, a counterspell, card draw), search for the "
        "effect or role instead of guessing which specific card provides it (\"ramp\", \"destroy all creatures\", "
        "\"counter target spell\", \"draw a card\") -- this surfaces every real option in one call instead of "
        "guessing one name at a time. If two searches in a row return zero matches, that's a signal to change "
        "your whole angle (a different mechanic, a different need) rather than try another specific guess in the "
        "same vein.\n\n"
        "Your only goal is reaching exactly {target} library cards; time spent searching without adding makes "
        "zero progress toward that no matter how thorough it feels, and you will run out of turns before "
        "finishing if search calls aren't converting into add_card calls at a good rate. Call check_combos every "
        "10-15 cards or so, not constantly. Call finish_deck only once the library is at the exact target size, "
        "and give a short summary of the deck's strategy.".format(target=library_target)
    )

    messages: list[dict] = [{"role": "user", "content": "Build the deck."}]
    finished = False
    summary = ""
    error = None
    start = time.monotonic()
    emit(f"Reading {commander['name']}'s card text...")

    for turn in range(_MAX_TURNS):
        turn_ref["value"] = turn + 1
        if time.monotonic() - start > _MAX_SECONDS:
            emit("Time limit reached -- stopping with whatever's been built so far.")
            break
        try:
            response = client.messages.create(
                model=_MODEL, max_tokens=_MAX_TOKENS_PER_TURN, system=system_prompt,
                messages=messages, tools=tools,
            )
        except Exception as e:
            error = str(e)
            emit(f"Error calling Claude: {e}")
            break

        messages.append({"role": "assistant", "content": response.content})
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            break

        tool_results = []
        stop = False
        for block in tool_uses:
            result = dispatch(block.name, block.input)
            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})
            if block.name == "finish_deck":
                parsed = json.loads(result)
                if parsed.get("ok"):
                    finished = True
                    summary = block.input.get("summary", "")
                    stop = True
        messages.append({"role": "user", "content": tool_results})
        if stop:
            break
    else:
        emit(f"Reached the {_MAX_TURNS}-turn limit -- stopping with whatever's been built so far.")

    suggestions = []
    for c in added_by_name.values():
        suggestions.append({
            "name": c["name"], "scryfall_id": c["scryfall_id"], "category": c["category"],
            "type_line": c["type_line"], "color_identity": c["color_identity"],
            "cmc": c["cmc"], "mana_cost": c["mana_cost"],
            "set_code": c.get("set_code", ""), "collector_number": c.get("collector_number", ""),
            "reason": c["reason"],
        })
    return {"suggestions": suggestions, "removed": removed_names, "log": log, "finished": finished, "summary": summary, "error": error}
