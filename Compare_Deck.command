#!/bin/bash
# Double-click this file in Finder to launch the web app and open it in your browser.
cd "$(dirname "$0")"

# Find a free port, starting at 5050 (macOS's AirPlay Receiver often occupies 5000).
PORT=5050
while lsof -i ":$PORT" >/dev/null 2>&1; do
  PORT=$((PORT + 1))
done

echo "Starting the app on port $PORT..."
PORT=$PORT python3 app.py &
APP_PID=$!

# Make sure the server actually dies with this window, however it closes.
# pkill -P is a defensive extra in case app.py ever forks a child again.
cleanup() {
  echo
  echo "Stopping server..."
  pkill -P "$APP_PID" 2>/dev/null
  kill "$APP_PID" 2>/dev/null
  wait "$APP_PID" 2>/dev/null
}
trap cleanup EXIT INT TERM HUP

# Wait for the server to come up, then open it in your default browser.
for i in $(seq 1 30); do
  if curl -s -o /dev/null "http://localhost:$PORT/"; then
    open "http://localhost:$PORT/"
    break
  fi
  sleep 0.5
done

echo
echo "Server is running (PID $APP_PID) at http://localhost:$PORT"
echo "Close this window or press Ctrl+C to stop it."
wait $APP_PID
