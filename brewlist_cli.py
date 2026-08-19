#!/usr/bin/env python3
"""
Compare a Moxfield or Archidekt decklist against your ManaBox card collection.

Interactive mode (menu-driven, no flags needed):
    python3 brewlist_cli.py

Direct / scriptable mode:
    python3 brewlist_cli.py <deck_url_or_id> [options]

Examples:
    python3 brewlist_cli.py https://moxfield.com/decks/PoWfAXDZdHy0n9GEa47ZQw
    python3 brewlist_cli.py PoWfAXDZdHy0n9GEa47ZQw --collection-dir ~/Downloads
    python3 brewlist_cli.py PoWfAXDZdHy0n9GEa47ZQw --collection "ManaBox_Collection 2.csv"

By default the script looks in ~/Downloads for the most recently modified
file whose name starts with "manabox" (case-insensitive) and ends in .csv,
so you can just re-export from ManaBox and re-run this without changing
any arguments.

Requires the 'rich' package for the interactive menu / terminal tables:
    pip3 install rich

Optionally uses 'pyfiglet' for a fun ASCII banner (auto-sized to your terminal
width) -- entirely cosmetic, the script works fine without it:
    pip3 install pyfiglet

The script will offer to pip install either of these for you the first time
it's run if they're missing.

Also available as a Flask web app (paste the deck URL, upload your ManaBox
export, no terminal needed) -- see app.py.
"""

from __future__ import annotations

import argparse
import datetime
import os
import random
import re
import subprocess
import sys
import webbrowser


def _pip_install(package: str) -> bool:
    """Best-effort `pip install <package>`, retrying with --break-system-packages
    if the interpreter's Python is externally managed. Returns success/failure."""
    pip_cmd = [sys.executable, "-m", "pip", "install", package]
    result = subprocess.run(pip_cmd, capture_output=True, text=True)

    if result.returncode != 0 and "externally-managed-environment" in result.stderr:
        print("This Python install is externally managed; retrying with --break-system-packages ...")
        result = subprocess.run(pip_cmd + ["--break-system-packages"], capture_output=True, text=True)

    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return False
    return True


def _preflight_rich() -> None:
    """Make sure 'rich' is importable -- required. Offers to pip install it if not."""
    try:
        import rich  # noqa: F401
        return
    except ImportError:
        pass

    print("This script needs the 'rich' package (for the interactive menu and tables), "
          "but it isn't installed.")
    try:
        answer = input("Install it now with pip? [Y/n]: ").strip().lower()
    except EOFError:
        answer = "n"
    if answer not in ("", "y", "yes"):
        raise SystemExit("Okay -- install it yourself with:\n\n    pip3 install rich\n")

    print(f"Installing rich via {sys.executable} -m pip ...")
    if not _pip_install("rich"):
        raise SystemExit(
            "\nAutomatic install failed. Install it yourself with:\n\n    pip3 install rich\n"
        )
    print("rich installed successfully.\n")


_preflight_rich()

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn
    from rich.prompt import Confirm, IntPrompt, Prompt
    from rich.table import Table
except ImportError:
    raise SystemExit(
        "rich was just installed but couldn't be imported -- try re-running the script.\n"
    )

console = Console()

HAS_PYFIGLET = False


def _preflight_pyfiglet() -> None:
    """Optional dependency for the fun ASCII banners -- degrades gracefully if skipped."""
    global HAS_PYFIGLET
    try:
        import pyfiglet  # noqa: F401
        HAS_PYFIGLET = True
        return
    except ImportError:
        pass

    if not console.is_terminal:
        return

    console.print(
        "[dim](optional) 'pyfiglet' isn't installed -- it draws the fun ASCII banners, "
        "nothing else depends on it.[/dim]"
    )
    if not Confirm.ask("Install it now with pip?", default=True):
        return

    console.print(f"[dim]Installing pyfiglet via {sys.executable} -m pip ...[/dim]")
    if _pip_install("pyfiglet"):
        try:
            import pyfiglet  # noqa: F401
            HAS_PYFIGLET = True
            console.print("[dim]pyfiglet installed successfully.[/dim]\n")
        except ImportError:
            console.print("[dim]Installed but still couldn't import it -- skipping the banner.[/dim]\n")
    else:
        console.print("[dim]Couldn't install pyfiglet -- continuing without the fun banner.[/dim]\n")


_preflight_pyfiglet()

from brewlist_core import (
    CardResult,
    build_comparison,
    deck_is_commander_format,
    deck_key,
    ensure_price_index,
    extract_entries,
    fetch_deck,
    find_collection_candidates,
    load_collection,
    load_overrides,
    parse_deck_ref,
    render_html,
    render_markdown,
    update_from_git,
    write_missing_csv,
)

MTG_FLAVOR_LINES = [
    "Tap. Attack. Win.",
    "Draw a card, take a turn.",
    "Shuffle up and play!",
    "May your top-deck be legendary.",
    "Untap, upkeep, draw...",
    "Sol Ring go brrr.",
    "It's morning, my dudes.",
    "One more land, I swear.",
    "Cast with confidence.",
    "Big mana, bigger plays.",
    "Attacks are declared.",
    "Tutor for victory.",
]

# Roughly biggest to smallest -- these are bundled with every pyfiglet install.
_FIGLET_FONTS_BY_SIZE = ["big", "standard", "small", "mini"]


def render_banner(text: str) -> str | None:
    """ASCII-art `text` sized to fit the current terminal width. Tries fonts from
    biggest to smallest and returns the first that fits without wrapping; None if
    pyfiglet isn't available or even the smallest font is too wide."""
    if not HAS_PYFIGLET:
        return None
    import pyfiglet

    width = max(console.size.width, 20)
    for font in _FIGLET_FONTS_BY_SIZE:
        try:
            # Render unconstrained (no width= here) so a too-wide result stays a
            # single wide block we can measure and reject, rather than pyfiglet's
            # own line-wrapping splitting it into several stacked banners.
            art = pyfiglet.Figlet(font=font, width=1000).renderText(text)
        except pyfiglet.FontNotFound:
            continue
        lines = [line for line in art.rstrip("\n").split("\n") if line.strip()]
        if lines and max(len(line) for line in lines) <= width:
            return art.rstrip("\n")
    return None


def print_banner(text: str, style: str = "bold cyan") -> None:
    if not console.is_terminal:
        return
    art = render_banner(text)
    if art:
        console.print(art, style=style, highlight=False)
    else:
        console.print(text, style=style, justify="center")


# --------------------------------------------------------------------------
# Rendering: terminal (rich)
# --------------------------------------------------------------------------

def render_console(deck_name: str, deck_url: str, bucket_names: list[str],
                    buckets: dict[str, list[CardResult]], totals: dict) -> None:
    console.print()
    unpriced_note = (
        f" [dim](no price found for {totals['unpriced_count']} card"
        f"{'s' if totals['unpriced_count'] != 1 else ''})[/dim]"
        if totals["unpriced_count"] else ""
    )
    console.print(Panel(
        f"[link={deck_url}]{deck_url}[/link]\n\n"
        f"[green]Owned: {totals['owned']}[/green]   "
        f"[red]Missing: {totals['missing']}[/red]   "
        f"[yellow]Est. cost to complete: ${totals['cost_nonfoil']:.2f} non-foil / "
        f"${totals['cost_foil']:.2f} foil[/yellow]\n"
        f"[cyan]Total deck value (today's market): ${totals['deck_value']:.2f}[/cyan]"
        f"   [dim](owned portion: ${totals['owned_value']:.2f})[/dim]{unpriced_note}",
        title=deck_name,
        border_style="cyan",
    ))

    for bucket in bucket_names:
        cards = buckets[bucket]
        table = Table(title=f"{bucket} ({len(cards)})", box=None, title_justify="left",
                      header_style="bold")
        table.add_column("", width=2)
        table.add_column("Card")
        table.add_column("Need", justify="right")
        table.add_column("Have", justify="right")
        table.add_column("Best price / store")

        for r in cards:
            commander_tag = " [dim](Commander)[/dim]" if r.entry.section == "commander" else ""
            foil_tag = " [dim](foil)[/dim]" if r.entry.is_foil else ""
            if r.shortfall == 0:
                table.add_row("[green]✓[/green]", f"{r.entry.name}{commander_tag}",
                              str(r.entry.quantity), str(r.have), "")
            else:
                if r.best:
                    label, price, url = r.best[0]
                    price_str = f"[link={url}]{label} ${price:.2f}[/link]"
                    if len(r.best) > 1:
                        price_str += "  [dim](" + ", ".join(
                            f"{lbl} ${p:.2f}" for lbl, p, _ in r.best[1:]
                        ) + ")[/dim]"
                else:
                    price_str = "[dim]no price found[/dim]"
                table.add_row("[red]✗[/red]", f"{r.entry.name}{commander_tag}{foil_tag}",
                              str(r.shortfall), str(r.have), price_str)

        console.print(table)
        console.print()


# --------------------------------------------------------------------------
# Interactive menu helpers
# --------------------------------------------------------------------------

def choose_collection_file(candidates: list[str]) -> str:
    if len(candidates) == 1:
        return candidates[0]

    console.print("\n[bold]Multiple ManaBox exports found:[/bold]")
    for i, path in enumerate(candidates, start=1):
        mtime = os.path.getmtime(path)
        stamp = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        newest = "  [green](newest)[/green]" if i == 1 else ""
        console.print(f"  [cyan]{i}[/cyan]. {os.path.basename(path)}  [dim]({stamp})[/dim]{newest}")

    choice = IntPrompt.ask("Which file?", default=1, choices=[str(i) for i in range(1, len(candidates) + 1)])
    return candidates[choice - 1]


def run_interactive():
    print_banner("BREWLIST")
    console.print(random.choice(MTG_FLAVOR_LINES), style="italic dim", justify="center")
    console.print()
    console.print(Panel("Moxfield/Archidekt decklist vs. ManaBox collection", border_style="cyan"))

    deck_input = ""
    while not deck_input.strip():
        deck_input = Prompt.ask("Moxfield or Archidekt deck URL")
        if not deck_input.strip():
            console.print("[red]A deck URL or ID is required.[/red]")

    console.print("\n[bold]What would you like to generate?[/bold]")
    console.print("  [cyan]1[/cyan]. Generate Deck Coverage HTML")
    console.print("  [cyan]2[/cyan]. Generate Deck Coverage CSV")
    console.print("  [cyan]3[/cyan]. Generate Deck Coverage Markdown")
    console.print("  [cyan]4[/cyan]. Generate All Deck Coverage Files")
    format_choice = IntPrompt.ask("Choose an option", default=1, choices=["1", "2", "3", "4"])
    want_html = format_choice in (1, 4)
    want_csv = format_choice in (2, 4)
    want_markdown = format_choice in (3, 4)

    collection_dir = Prompt.ask("Folder to search for ManaBox exports", default="~/Downloads")

    candidates = find_collection_candidates(collection_dir)
    if not candidates:
        explicit = Prompt.ask(
            f"No file starting with 'manabox' found in {collection_dir}. "
            f"Enter the full path to your CSV export"
        )
        collection_path = explicit
    else:
        collection_path = choose_collection_file(candidates)

    include_sideboard = Confirm.ask("Include sideboard cards?", default=False)
    include_maybeboard = Confirm.ask("Include maybeboard cards?", default=False)
    include_basics = not Confirm.ask("Break out basic lands separately?", default=True)

    run(
        deck_input=deck_input,
        collection_path=collection_path,
        collection_dir=collection_dir,
        include_sideboard=include_sideboard,
        include_maybeboard=include_maybeboard,
        include_basics=include_basics,
        output=None,
        missing_csv=None,
        html_output=None,
        open_html=False,
        interactive=True,
        want_markdown=want_markdown,
        want_csv=want_csv,
        want_html=want_html,
    )


def refresh_price_index_cli() -> None:
    """Force-downloads a fresh cheapest-price index and exits -- the
    --refresh-price-index flag. Same index build_comparison() reuses
    automatically once it's less than a week old."""
    progress = Progress(
        TextColumn("[dim]{task.description}[/dim]"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )
    with progress:
        task_id = progress.add_task("Downloading card database from MTGJSON...", total=None)

        def _on_progress(done, total, stage=None):
            if stage == "processing":
                progress.update(task_id, description="Processing downloaded card data...", total=None, completed=0)
            elif stage == "tags":
                progress.update(task_id, description="Finding budget-alternative tags...", total=None, completed=0)
            else:
                progress.update(task_id, description="Downloading card database from MTGJSON...", total=total, completed=done)

        try:
            index = ensure_price_index(on_progress=_on_progress, force_refresh=True)
        except ValueError as e:
            raise SystemExit(str(e))
    console.print(f"[green]Card database refreshed -- {len(index)} unique card names.[/green]")


def update_cli() -> None:
    """Pulls the latest Brewlist code from GitHub and exits -- the --update
    flag. Plain `git pull`, so it only works if this was installed with
    'git clone' (not a downloaded ZIP)."""
    with console.status("Checking for updates..."):
        result = update_from_git()
    if not result["ok"]:
        raise SystemExit(result["message"])
    style = "green" if result["updated"] else "dim"
    console.print(f"[{style}]{result['message']}[/{style}]")


def _check_for_updates_on_launch() -> None:
    """Runs on every normal launch (not for --update/--refresh-price-index,
    which already have their own explicit flow) -- silent unless an update
    was actually found and pulled, since most launches have nothing to
    report and a message every time would just be noise. Never raises: a
    network hiccup or a non-git install here shouldn't block using the
    app, only --update itself is allowed to fail loudly."""
    try:
        result = update_from_git()
    except Exception:  # noqa: BLE001 -- this must never block a normal run
        return
    if result.get("ok") and result.get("updated"):
        console.print(f"[green]⚡ {result['message']}[/green]\n")


# --------------------------------------------------------------------------
# Core run
# --------------------------------------------------------------------------

def run(deck_input, collection_path, collection_dir, include_sideboard,
        include_maybeboard, include_basics, output, missing_csv, html_output,
        open_html, interactive, want_markdown=False, want_csv=False, want_html=False):
    if not deck_input or not deck_input.strip():
        raise SystemExit("A Moxfield or Archidekt deck URL or ID is required.")

    source, deck_id = parse_deck_ref(deck_input)
    if not deck_id:
        raise SystemExit(f"Couldn't figure out a deck ID from '{deck_input}'.")
    project_key = deck_key(source, deck_id)
    source_label = "Archidekt" if source == "archidekt" else "Moxfield"
    with console.status(f"Fetching deck '{deck_id}' from {source_label}..."):
        try:
            deck = fetch_deck(source, deck_id)
        except ValueError as e:
            raise SystemExit(str(e))
    deck_name = deck.get("name", deck_id)
    if source == "archidekt":
        deck_url = f"https://archidekt.com/decks/{deck_id}"
    else:
        deck_url = deck.get("publicUrl", f"https://moxfield.com/decks/{deck_id}")

    entries = extract_entries(source, deck, include_sideboard, include_maybeboard)
    is_commander_format = deck_is_commander_format(source, deck)
    console.print(f"[dim]Deck '{deck_name}': {len(entries)} unique cards[/dim]")

    if not collection_path:
        candidates = find_collection_candidates(collection_dir)
        if not candidates:
            raise SystemExit(
                f"No file starting with 'manabox' found in {collection_dir}. "
                f"Pass --collection /path/to/file.csv to specify one directly."
            )
        collection_path = candidates[0]
    elif not os.path.isfile(collection_path):
        raise SystemExit(f"Collection file not found: {collection_path}")

    console.print(f"[dim]Using collection file: {collection_path}[/dim]")
    owned = load_collection(collection_path)
    console.print(f"[dim]Collection has {len(owned)} unique card names[/dim]")

    overrides, overrides_path = load_overrides(project_key, collection_dir)
    if overrides:
        console.print(
            f"[dim]Reserving {len(overrides)} card(s) for other decks "
            f"(from {overrides_path})[/dim]"
        )

    progress = Progress(
        TextColumn("[dim]{task.description}[/dim]"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )
    with progress:
        task_id = progress.add_task("Pricing cards via local index...", total=None)

        def _on_progress(done, total, stage=None):
            if stage == "combos":
                progress.update(
                    task_id, description="Checking combos & bracket rating (Commander Spellbook)...",
                    total=None, completed=0,
                )
            elif stage == "processing":
                progress.update(task_id, description="Processing downloaded card data...", total=None, completed=0)
            elif stage == "tags":
                progress.update(task_id, description="Finding budget-alternative tags...", total=None, completed=0)
            else:
                progress.update(task_id, description="Pricing cards via local index...", total=total, completed=done)

        try:
            bucket_names, buckets, totals = build_comparison(
                entries, owned, ignore_basics=not include_basics, overrides=overrides,
                on_progress=_on_progress, is_commander_format=is_commander_format,
            )
        except ValueError as e:
            raise SystemExit(str(e))
    render_console(deck_name, deck_url, bucket_names, buckets, totals)

    if totals["missing"] == 0:
        print_banner("DECK COMPLETE", style="bold green")

    markdown, missing_rows = render_markdown(deck_name, deck_url, bucket_names, buckets, totals)
    safe_name = re.sub(r'[^A-Za-z0-9_-]+', '_', deck_name)

    if not interactive and not output:
        output = f"{safe_name}_comparison.md"
    if not interactive and open_html and not html_output:
        html_output = f"{safe_name}_comparison.html"

    if interactive:
        if want_markdown:
            output = f"{safe_name}_comparison.md"
        if want_html:
            html_output = f"{safe_name}_comparison.html"
        if want_csv:
            missing_csv = f"{safe_name}_missing.csv"

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(markdown)
        console.print(f"[green]Report written to {output}[/green]")

    if html_output:
        html_report = render_html(
            deck_name, deck_url, project_key, bucket_names, buckets, totals,
            is_commander_format=is_commander_format,
        )
        with open(html_output, "w", encoding="utf-8") as f:
            f.write(html_report)
        console.print(f"[green]HTML report written to {html_output}[/green]")

        open_now = open_html
        if interactive and not open_html:
            open_now = Confirm.ask("Open it in your browser now?", default=True)
        if open_now:
            webbrowser.open(f"file://{os.path.abspath(html_output)}")

    if missing_csv:
        write_missing_csv(missing_csv, missing_rows)
        console.print(f"[green]Missing-cards CSV written to {missing_csv}[/green]")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("deck", nargs="?", default=None, help="Moxfield or Archidekt deck URL or deck id")
    parser.add_argument("--collection", help="Path to a specific ManaBox CSV export")
    parser.add_argument("--collection-dir", default="~/Downloads",
                         help="Directory to search for the newest manabox*.csv (default: ~/Downloads)")
    parser.add_argument("--include-sideboard", action="store_true")
    parser.add_argument("--include-maybeboard", action="store_true")
    parser.add_argument("--include-basics", action="store_true",
                         help="Don't split basic lands into their own bucket / treat them like any other card")
    parser.add_argument("-o", "--output", default=None,
                         help="Markdown report output path (default: <deckname>_comparison.md)")
    parser.add_argument("--html", dest="html_output", default=None,
                         help="Also write a standalone HTML report to this path")
    parser.add_argument("--open", action="store_true",
                         help="Open the HTML report in your browser once it's written (implies --html if not set, "
                              "using the default path)")
    parser.add_argument("--missing-csv", default=None,
                         help="Optional CSV path to dump just the missing cards with buy links")
    parser.add_argument("--refresh-price-index", action="store_true",
                         help="Force-download a fresh copy of the local card database (pricing across "
                              "TCGPlayer, Card Kingdom, and ManaPool, price trends, Game Changers, and "
                              "Commander legality all come from it). Normally this refreshes itself "
                              "automatically once a week -- use this to update it sooner.")
    parser.add_argument("--update", action="store_true",
                         help="Pull the latest Brewlist code from GitHub and exit. Only works if this was "
                              "installed with 'git clone' (not a downloaded ZIP).")
    args = parser.parse_args()

    if args.refresh_price_index:
        refresh_price_index_cli()
        return

    if args.update:
        update_cli()
        return

    _check_for_updates_on_launch()

    if args.deck is None:
        run_interactive()
        return

    run(
        deck_input=args.deck,
        collection_path=args.collection,
        collection_dir=args.collection_dir,
        include_sideboard=args.include_sideboard,
        include_maybeboard=args.include_maybeboard,
        include_basics=args.include_basics,
        output=args.output,
        missing_csv=args.missing_csv,
        html_output=args.html_output,
        open_html=args.open,
        interactive=False,
    )


if __name__ == "__main__":
    main()
