#!/bin/bash
# Double-click this file in Finder to launch Brewlist and open it in your browser.
# app.py itself finds a free port and opens the browser -- this is just a
# convenient double-click entry point. Closing this Terminal window (or the
# "Shut Down" button in the page itself) stops the server.
cd "$(dirname "$0")"
python3 app.py
