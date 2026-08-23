"""Build with AI -- an agentic deck-building loop using Claude's tool use,
layered on top of deck_builder.py's existing legality/color/ownership
filtering rather than replacing it.

Why this exists: Suggest/Optimize (deck_builder.py) are entirely
Oracle-Tag/combo-database heuristics -- they have zero understanding of
what a commander's own rules text actually says. A commander whose power
comes from a specific, narrow interaction (e.g. "activated abilities of
Elf cards in your graveyard") has no matching Oracle Tag, so the
tag-based engine structurally cannot steer toward it. This module hands
Claude real tools over the *actual* owned/legal/color-correct candidate
pool (never a free-form card list -- every add is validated against real
data, same "don't trust it blindly" discipline every other AI-adjacent
feature in this app follows) and lets it read the commander's real
oracle text, search for whatever it decides is relevant, and build a
deck across multiple turns.

No UI dependencies -- imported by app.py, same relationship
deck_builder.py/brewlist_core.py already have to app.py.
"""

from __future__ import annotations

import json
import os
import time
from typing import Callable

import anthropic

from brewlist_core import CardEntry, find_deck_combos, normalize_name
from deck_builder import _filter_candidates, categorize

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


_TOOLS = [
    {
        "name": "search_owned_collection",
        "description": (
            "Search the player's owned, format-legal, color-identity-correct cards that aren't "
            "already in the deck. Matches your query against each card's name, type line, and "
            "full oracle (rules) text -- use this to look for anything relevant: a creature type "
            "(e.g. \"Elf\"), a mechanic or keyword (e.g. \"graveyard\", \"proliferate\", \"mill\"), "
            "a card's own name, or a broad category (e.g. \"land\", \"draw\"). Returns up to 25 "
            "matches with full oracle text so you can judge fit yourself."
        ),
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
        "description": "Adds one owned, legal, color-correct card to the deck library. Must be a card name returned by search_owned_collection.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Exact card name, as returned by search_owned_collection."},
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


def _card_summary(c: dict) -> dict:
    text = c.get("oracle_text") or ""
    return {
        "name": c["name"],
        "type_line": c.get("type_line") or "",
        "mana_cost": c.get("mana_cost") or "",
        "cmc": c.get("cmc") or 0,
        "oracle_text": text[:400],
    }


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
) -> dict:
    """Runs the agentic build loop. Returns {"suggestions": [...] (same
    shape suggest_builder_cards()/optimize_builder_combos() already
    return, so the existing suggestions-panel JS works unmodified),
    "log": [...], "finished": bool, "summary": str, "error": str|None}.

    Never applies anything to a real saved brew -- this only ever builds
    up an in-memory wip_entries list and hands back suggestions for the
    caller to let the user review/apply, same "never auto-applied"
    convention Suggest/Optimize already use."""
    library_target = target_size - 1 if deck_format == "commander" else target_size
    wip_entries = [CardEntry(
        name=commander["name"], quantity=1, type_line=commander.get("type_line", ""),
        is_foil=False, section="commander", scryfall_id=commander.get("scryfall_id"),
        color_identity=commander.get("color_identity") or [],
        set_code=commander.get("set_code", ""), collector_number=commander.get("collector_number", ""),
    )]
    added_by_name: dict[str, dict] = {}
    log: list[str] = []
    client = anthropic.Anthropic(api_key=api_key)
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

    def dispatch(tool_name: str, tool_input: dict) -> str:
        if tool_name == "search_owned_collection":
            query = (tool_input.get("query") or "").strip().lower()
            candidates = _filter_candidates(wip_entries, owned_view, deck_format, target_format, commander_color_identity, intended_bracket, None)
            matches = [
                c for c in candidates
                if query in c["name"].lower() or query in (c.get("type_line") or "").lower() or query in (c.get("oracle_text") or "").lower()
            ][:25]
            emit(f'Searched collection for "{tool_input.get("query")}" -- {len(matches)} match(es)')
            return json.dumps([_card_summary(c) for c in matches])

        if tool_name == "get_deck_state":
            return json.dumps(_deck_state_view(wip_entries))

        if tool_name == "add_card":
            name = (tool_input.get("name") or "").strip()
            reason = (tool_input.get("reason") or "").strip()
            nm = normalize_name(name)
            if nm in {normalize_name(e.name) for e in wip_entries}:
                return json.dumps({"ok": False, "error": f"{name} is already in the deck."})
            candidates = _filter_candidates(wip_entries, owned_view, deck_format, target_format, commander_color_identity, intended_bracket, None)
            match = next((c for c in candidates if normalize_name(c["name"]) == nm), None)
            if not match:
                return json.dumps({"ok": False, "error": f"{name} isn't in your owned/legal/color-correct pool -- search_owned_collection first to confirm the exact name."})
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
            wip_entries.remove(match)
            added_by_name.pop(nm, None)
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

    system_prompt = (
        f"You are building a real, legal Magic: The Gathering deck using only the player's owned cards.\n\n"
        f"Commander: {commander['name']}\n"
        f"Type line: {commander.get('type_line', '')}\n"
        f"Oracle text: {commander.get('oracle_text', '') or '(not available)'}\n"
        f"Color identity: {', '.join(commander_color_identity or []) or 'colorless'}\n"
        f"Format: {deck_format}" + (f" (target legality: {target_format})" if target_format else "") + "\n"
        f"Target library size: exactly {library_target} cards (plus the commander already in the deck).\n"
        + (f"Intended power bracket: {intended_bracket}\n" if intended_bracket else "")
        + (f"Player's notes on what they want: {user_notes}\n" if user_notes else "")
        + "\nRead the commander's own oracle text carefully -- build around what it ACTUALLY does, not just "
        "its colors. Use search_owned_collection to look for anything relevant (creature types, keywords, "
        "mechanics mentioned in the commander's text) rather than only generic staples. You may only add cards "
        "returned by search_owned_collection -- never invent a card name.\n\n"
        "WORKFLOW -- this matters, and your turn budget is limited: searching does not build the deck, add_card "
        "does. After every search (or every 1-2 searches at most), call add_card for the best matches you just "
        "found before searching again -- and when a search returns several good matches, add 3-5 of them in the "
        "same batch, not just one at a time. Do not run more than two searches in a row without adding anything.\n\n"
        "NEVER search for a specific famous card's exact name on a guess (\"Sol Ring\", \"Cultivate\", \"Rhystic "
        "Study\", a named legendary creature, etc.) unless a broader search or the deck state already gave you "
        "real reason to think it's owned -- most name guesses return zero matches and burn a turn for nothing. "
        "For a staple EFFECT you want (ramp, a board wipe, a counterspell, card draw), search for the effect or "
        "role instead of guessing which specific card provides it (\"ramp\", \"destroy all creatures\", \"counter "
        "target spell\", \"draw a card\") -- this surfaces every real, owned option in one call instead of "
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
                messages=messages, tools=_TOOLS,
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
    return {"suggestions": suggestions, "log": log, "finished": finished, "summary": summary, "error": error}
