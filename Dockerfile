# Brewlist web app, containerized. The CLI (brewlist_cli.py) isn't wired up
# here -- this image runs the Flask web app (app.py) only.
#
# Build:
#   docker build -t brewlist .
# Run (data/ holds your ManaBox collection + the local MTGJSON price index,
# so it needs a volume or it's lost when the container is removed):
#   docker run -p 5050:5050 -v brewlist-data:/app/data brewlist
# Or just: docker compose up -d

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# HOST=0.0.0.0: 127.0.0.1 (the app's own default, since it's normally a
# local double-clicked app) isn't reachable from outside the container.
# NO_BROWSER: no browser to auto-open inside a container.
# BREWLIST_DOCKER: .dockerignore excludes .git from the image on purpose
# (see its own comment), so update_from_git() always fails its "is this a
# git checkout" check in here -- this lets it tell a Docker user the real
# fix (update the host + rebuild) instead of the generic "you downloaded a
# ZIP" message meant for a plain, non-Docker install.
ENV HOST=0.0.0.0 \
    PORT=5050 \
    NO_BROWSER=1 \
    BREWLIST_DOCKER=1

EXPOSE 5050
VOLUME ["/app/data"]

CMD ["python3", "app.py"]
