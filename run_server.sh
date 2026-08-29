#!/bin/sh
# pstore dev server. Env: PSTORE_TAG, PSTORE_MARKET, PORT.
cd "$(dirname "$0")/app" || exit 1
exec python3 server.py