# xx-find-server for Synology Container Manager.
# Runs the stdlib-only server (plus PyNaCl) as a non-root user, listening
# on 127.0.0.1:8080 inside the container. Reachability over the owner's
# Tailscale tailnet is configured OUTSIDE this image, in Container
# Manager (bind the published port to the NAS Tailscale IP, or put the
# NAS Tailscale client in front) -- this image opens no public port and
# ships no deployment automation. Plain-Python fallback: run
# `python3 src/xx-find-server [store.json]` on any host with PyNaCl.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-nacl \
    && rm -rf /var/lib/apt/lists

RUN useradd --create-home --uid 927 xxfind
USER xxfind
WORKDIR /home/xxfind/app

COPY src/xx_find ./xx_find
COPY src/xx-find-server ./xx-find-server

VOLUME ["/home/xxfind/data"]
EXPOSE 8080

CMD ["python3", "./xx-find-server", "/home/xxfind/data/store.json"]
