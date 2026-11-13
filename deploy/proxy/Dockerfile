# M8-14: the pinned upstream Squid image is Ubuntu-based and ships no Python,
# while the public listener's auth helper (egress_auth.py) is a stdlib Python 3
# program. Add only the interpreter here; the helper itself is mounted at
# runtime so allowlist/secret changes never need an image rebuild.
FROM squid:6.10

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*
