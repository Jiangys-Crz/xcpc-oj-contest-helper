#!/bin/sh
set -eu
xcpc_source=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$(uname -s)" != Darwin ]; then
    echo 'macOS only; Ubuntu uses bash install.sh.' >&2
    exit 1
fi
xcpc_python=$(command -v python3.13 || command -v python3)
exec "$xcpc_python" "$xcpc_source/install.py" "$@"
