#!/bin/bash
# Double-click this file in Finder to launch the interactive deck comparison tool.
cd "$(dirname "$0")"
python3 moxfield_vs_collection.py
echo
read -p "Press Enter to close..."
