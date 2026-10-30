#!/usr/bin/env bash
set -euo pipefail
root=/home/paa/xcpc-vp-system
if [[ $EUID != 0 ]]; then echo 'Run this installer as root.' >&2; exit 1; fi
if getent passwd xcpc-vp >/dev/null; then
    [[ "$(getent passwd xcpc-vp | cut -d: -f6)" == "$root" ]] || { echo 'Existing xcpc-vp account has a different home; preserved.' >&2; exit 1; }
else
    useradd --system --user-group --home-dir "$root" --shell /usr/sbin/nologin xcpc-vp
fi
install -d -o root -g root -m 755 "$root" "$root/releases" "$root/runtimes" "$root/shared/public/downloads/terminal"
install -d -o root -g xcpc-vp -m 750 "$root/shared"
install -d -o xcpc-vp -g xcpc-vp -m 700 "$root/shared/data" "$root/shared/pm2"
install -d -o xcpc-vp -g xcpc-vp -m 750 "$root/shared/logs"
install -d -o root -g root -m 700 "$root/deployment-backups"
printf 'Isolated application directories ready.\n'
