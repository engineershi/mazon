#!/bin/sh
# Mazon dev server. Env: MAZON_TAG, MAZON_MARKET, PORT.
cd "$(dirname "$0")/app" || exit 1
exec python3 server.py