# M8-14: the public listener's auth helper (egress_auth.py) is a stdlib
# Python 3 program. Ubuntu's official Squid image is maintained, ships apt and
# has a resolvable tag (the upstream `squid:6.10` tag does not exist); only the
# interpreter is added here, the helper itself is mounted at runtime so
# allowlist/secret changes never need an image rebuild.
FROM ubuntu/squid:latest

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*
