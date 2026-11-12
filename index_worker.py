"""Rebuilds the local card database in a process of its own.

The rebuild parses MTGJSON's whole card and price files with single blocking calls (json.load) that hold Python's
interpreter for many seconds. Run inside the web server, that starves every other request -- including the page's
health check, which then shows "Server not responding". Run here, the server stays responsive.

Progress goes to stdout, one JSON object per line: {"p": [done, total, stage]}, then {"ok": true} or {"error": "..."}.
Usage: python3 index_worker.py [path]  (see brewlist_core.rebuild_price_index_isolated)
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brewlist_core import PRICE_INDEX_PATH, rebuild_price_index  # noqa: E402


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else PRICE_INDEX_PATH
    last = {"t": 0.0, "stage": object()}

    def emit(done, total, stage=None):
        now = time.time()
        # A byte-progress tick per 64 KB chunk would be thousands of lines: pass one on every 0.25 s and on every stage change.
        if stage == last["stage"] and now - last["t"] < 0.25:
            return
        last["t"], last["stage"] = now, stage
        print(json.dumps({"p": [done, total, stage]}), flush=True)

    try:
        rebuild_price_index(path, on_progress=emit)
    except BaseException as e:  # noqa: BLE001 -- whatever went wrong is the parent's error message
        print(json.dumps({"error": str(e) or e.__class__.__name__}), flush=True)
        return 1
    print(json.dumps({"ok": True}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
