#!/bin/bash
# Run this to launch Brewlist and open it in your browser.
# app.py itself finds a free port and opens the browser -- this is just a
# convenient entry point. Closing this terminal (or the "Shut Down" button
# in the page itself) stops the server.
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
  python3 app.py
else
  python app.py
fi
