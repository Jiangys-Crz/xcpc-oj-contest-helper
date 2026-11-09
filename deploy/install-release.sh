#!/usr/bin/env bash
# Installs a LOCALLY built artifact. No npm install or build runs on this server.
set -euo pipefail
root=/home/paa/xcpc-vp-system
artifact="${1:?Usage: install-release.sh ARTIFACT SHA256}"
expected="${2:?Artifact SHA256 is required}"
[[ $EUID == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
[[ "$expected" =~ ^[0-9a-f]{64}$ ]] || { echo 'Invalid checksum.' >&2; exit 1; }
[[ "$(sha256sum "$artifact" | cut -d' ' -f1)" == "$expected" ]] || { echo 'Artifact checksum mismatch.' >&2; exit 1; }
[[ -f "$root/shared/env.json" ]] || { echo 'Configure runtime credentials first.' >&2; exit 1; }
stamp="$(date -u +%Y%m%dT%H%M%S)-$$"
stage="$root/.incoming-$stamp"
release="$root/releases/$stamp"
previous="$(readlink "$root/current" || true)"
previous_runtime="$(readlink "$root/runtime" || true)"
caddy=/root/.hydro/Caddyfile
caddy_binary=/usr/local/bin/caddy-l4
[[ -x "$caddy_binary" && -f "$caddy" ]] || { echo 'Expected Caddy installation not found; existing services preserved.' >&2; exit 1; }
[[ -z "$previous" || "$previous" == releases/* ]] || { echo 'Unmanaged current link; preserved.' >&2; exit 1; }
mkdir "$stage"
rollback_directory="$root/deployment-backups/release.$stamp"
install -d -m 700 "$rollback_directory"
managed=("$root/ecosystem.config.cjs" "$root/Caddyfile" "$root/backup.mjs" /etc/systemd/system/xcpc-vp-pm2.service /etc/systemd/system/xcpc-vp-backup.service /etc/systemd/system/xcpc-vp-backup.timer)
for index in "${!managed[@]}"; do
    if [[ -f "${managed[$index]}" ]]; then cp -p "${managed[$index]}" "$rollback_directory/$index"; fi
done
caddy_changed=false
caddy_reloaded=false
deployed=false
cleanup() {
    result=$?
    trap - EXIT
    if [[ "$deployed" != true ]]; then
        set +e
        for index in "${!managed[@]}"; do
            if [[ -f "$rollback_directory/$index" ]]; then cp -p "$rollback_directory/$index" "${managed[$index]}"; else rm -f -- "${managed[$index]}"; fi
        done
        if [[ -n "$previous_runtime" ]]; then ln -s "$previous_runtime" "$root/.runtime-rollback-$stamp"; mv -Tf "$root/.runtime-rollback-$stamp" "$root/runtime"; else rm -f "$root/runtime"; fi
        if [[ -n "$previous" ]]; then
            ln -s "$previous" "$root/.current-rollback-$stamp"; mv -Tf "$root/.current-rollback-$stamp" "$root/current"
            systemctl daemon-reload; systemctl reload xcpc-vp-pm2.service
        else
            systemctl stop xcpc-vp-pm2.service
            rm -f "$root/current"
            systemctl disable xcpc-vp-pm2.service >/dev/null 2>&1
        fi
        if [[ "$caddy_changed" == true ]]; then
            python3 - "$caddy" "$root/Caddyfile" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]); line='import '+sys.argv[2]
p.write_text(''.join(row for row in p.read_text().splitlines(keepends=True) if row.strip()!=line))
PY
        fi
        if [[ "$caddy_changed" == true || "$caddy_reloaded" == true ]]; then "$caddy_binary" reload --config "$caddy" --adapter caddyfile; fi
    fi
    rm -rf -- "$stage"
    exit "$result"
}
trap cleanup EXIT
# Reject path escapes before extracting this checksum-verified application artifact.
python3 - "$artifact" <<'PY'
import sys,tarfile,pathlib
with tarfile.open(sys.argv[1]) as t:
    for m in t.getmembers():
        p=pathlib.PurePosixPath(m.name)
        if p.is_absolute() or '..' in p.parts: raise SystemExit('Unsafe archive entry')
        if m.issym() or m.islnk():
            link=pathlib.PurePosixPath(m.linkname)
            parts=[]
            for piece in ((p.parent/link).parts if m.issym() else link.parts):
                if piece in ('','.'): continue
                if piece=='..':
                    if not parts: raise SystemExit('Unsafe archive link')
                    parts.pop()
                else: parts.append(piece)
            if link.is_absolute(): raise SystemExit('Unsafe archive link')
        elif not (m.isfile() or m.isdir()): raise SystemExit('Unsupported archive entry')
PY
tar --extract --gzip --file "$artifact" --directory "$stage" --no-same-owner
[[ -f "$stage/release/server.js" && -x "$stage/runtime/node" ]] || { echo 'Incomplete artifact.' >&2; exit 1; }
PYTHONPATH="$stage/release/board-runtime" PYTHONDONTWRITEBYTECODE=1 python3 -c 'import vp_helper.board_export, vp_helper.rank_xcpcio, vp_helper.rank_ccpc'
"$stage/runtime/node" -e 'require("node:sqlite"); if (+process.versions.node.split(".")[0]<24) process.exit(1)'
version="$(tr -d '\r\n' < "$stage/runtime/NODE_VERSION")"
[[ "$version" =~ ^v24\.[0-9]+\.[0-9]+$ ]] || { echo 'Unexpected Node runtime.' >&2; exit 1; }
runtime="runtimes/$version-pm2-7.1.1"
if [[ ! -d "$root/$runtime" ]]; then mv "$stage/runtime" "$root/$runtime"; fi
ln -s "$runtime" "$root/.runtime-new-$stamp"
mv -Tf "$root/.runtime-new-$stamp" "$root/runtime"
mv "$stage/release" "$release"
install -d -o xcpc-vp -g xcpc-vp -m 750 "$release/.next/cache"
cp -a "$stage/source" "$release/source"
for f in ecosystem.config.cjs Caddyfile backup.mjs; do install -o root -g root -m 644 "$stage/deploy/$f" "$root/$f"; done
install -d -o root -g root -m 755 "$root/shared/public/downloads/terminal"
install -o root -g root -m 644 "$stage/deploy/online-install.sh" "$root/shared/public/install.sh"
install -o root -g root -m 644 "$stage/deploy/online-install.ps1" "$root/shared/public/install.ps1"
install -o root -g root -m 644 "$stage/deploy/xcpc-vp-pm2.service" /etc/systemd/system/xcpc-vp-pm2.service
install -o root -g root -m 644 "$stage/deploy/xcpc-vp-backup.service" /etc/systemd/system/xcpc-vp-backup.service
install -o root -g root -m 644 "$stage/deploy/xcpc-vp-backup.timer" /etc/systemd/system/xcpc-vp-backup.timer
ln -s "releases/$stamp" "$root/.current-new-$stamp"
mv -Tf "$root/.current-new-$stamp" "$root/current"
systemctl daemon-reload
systemctl enable xcpc-vp-pm2.service xcpc-vp-backup.timer >/dev/null
if systemctl is-active --quiet xcpc-vp-pm2.service; then
    systemctl reload xcpc-vp-pm2.service
else
    systemctl start xcpc-vp-pm2.service
fi
healthy=false
for check in {1..45}; do
    if curl --fail --silent --max-time 2 http://127.0.0.1:3100/api/health >/dev/null; then healthy=true; break; fi
    sleep 1
done
if ! "$healthy"; then
    echo 'VP health check failed. Caddy and other services were not changed.' >&2
    exit 1
fi
# Append one isolated site import; preserve every byte of the old service configuration.
import_line="import $root/Caddyfile"
if ! grep -Fxq "$import_line" "$caddy"; then
    if grep -Eq '^[[:space:]]*vp\.cwnupaa\.com[[:space:]]*\{' "$caddy"; then
        echo 'An existing unmanaged VP site was preserved.' >&2; exit 1
    fi
    backup="$root/deployment-backups/Caddyfile.$stamp"
    cp -p "$caddy" "$backup"
    original="$(sha256sum "$caddy" | cut -d' ' -f1)"
    candidate="/root/.hydro/Caddyfile.vp-candidate.$stamp"
    cat "$caddy" > "$candidate"
    printf '\n# XCPC VP independent site\n%s\n' "$import_line" >> "$candidate"
    "$caddy_binary" validate --config "$candidate" --adapter caddyfile
    [[ "$(sha256sum "$caddy" | cut -d' ' -f1)" == "$original" ]] || { echo 'Caddy changed concurrently; no configuration was overwritten.' >&2; exit 1; }
    cp "$candidate" "$caddy.vp-new"
    chmod --reference="$caddy" "$caddy.vp-new"
    mv -f "$caddy.vp-new" "$caddy"
    caddy_changed=true
    if ! "$caddy_binary" reload --config "$caddy" --adapter caddyfile; then
        cp -p "$backup" "$caddy"
        "$caddy_binary" reload --config "$caddy" --adapter caddyfile
        echo 'Caddy reload failed; original configuration restored.' >&2
        exit 1
    fi
else
    "$caddy_binary" validate --config "$caddy" --adapter caddyfile
    "$caddy_binary" reload --config "$caddy" --adapter caddyfile
fi
caddy_reloaded=true
systemctl start xcpc-vp-backup.timer
runuser -u xcpc-vp -- env PM2_HOME="$root/shared/pm2" "$root/runtime/node" "$root/runtime/pm2/node_modules/pm2/bin/pm2" save >/dev/null
deployed=true
printf 'VP deployed at https://vp.cwnupaa.com (release %s).\n' "$stamp"
