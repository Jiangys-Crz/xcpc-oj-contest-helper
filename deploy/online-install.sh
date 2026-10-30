#!/usr/bin/env bash
set -euo pipefail
umask 077
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
    printf '%s\n' 'CWNU XCPC Vp System online installer' 'Ubuntu / macOS: curl -fsSL https://vp.cwnupaa.com/install.sh | bash' 'Installs the verified terminal release and missing prerequisites.'
    exit 0
fi
case "$(uname -s)" in Linux|Darwin) ;; *) echo 'Windows: use https://vp.cwnupaa.com/install.ps1 in PowerShell.' >&2; exit 1;; esac
base=https://vp.cwnupaa.com
fetch() {
    if command -v curl >/dev/null 2>&1; then curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 --connect-timeout 15 --retry 2 "$1" -o "$2"
    elif command -v wget >/dev/null 2>&1; then wget --https-only -q "$1" -O "$2"
    else echo 'curl or wget is required to download this installer.' >&2; return 1; fi
}
interactive() { if ( : </dev/tty ) >/dev/null 2>&1; then "$@" </dev/tty; else "$@"; fi; }
# Prefer an existing installation's own updater, retaining its custom directories.
if command -v xcpc >/dev/null 2>&1; then
    if interactive xcpc update; then exit 0; fi
    echo 'The existing command could not update. Preparing the standard installer.' >&2
fi
temporary="$(mktemp -d "${TMPDIR:-/tmp}/xcpc-online.XXXXXXXX")"
trap 'rm -rf -- "$temporary"' EXIT
fetch "$base/api/client/releases/latest" "$temporary/manifest.json"
revision="$(sed -nE 's/.*"revision"[[:space:]]*:[[:space:]]*"([0-9a-f]{40,64})".*/\1/p' "$temporary/manifest.json")"
checksum="$(sed -nE 's/.*"sha256"[[:space:]]*:[[:space:]]*"([0-9a-f]{64})".*/\1/p' "$temporary/manifest.json")"
[[ "$revision" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ && "$checksum" =~ ^[0-9a-f]{64}$ ]] || { echo 'Invalid release manifest.' >&2; exit 1; }
fetch "$base/downloads/terminal/$revision.tar.gz" "$temporary/source.tar.gz"
if command -v sha256sum >/dev/null 2>&1; then actual="$(sha256sum "$temporary/source.tar.gz" | cut -d' ' -f1)"
else actual="$(shasum -a 256 "$temporary/source.tar.gz" | cut -d' ' -f1)"; fi
[[ "$actual" == "$checksum" ]] || { echo 'Release SHA256 mismatch; nothing was installed.' >&2; exit 1; }
tar -tzf "$temporary/source.tar.gz" | awk 'BEGIN{ok=1} /^\// || /(^|\/)\.\.(\/|$)/ || /\\/ || /:/{ok=0} END{exit !ok}' || { echo 'Unsafe source path.' >&2; exit 1; }
tar -tvzf "$temporary/source.tar.gz" | awk 'substr($0,1,1)!="-" && substr($0,1,1)!="d"{bad=1} END{exit bad}' || { echo 'Source archive links are not allowed.' >&2; exit 1; }
mkdir "$temporary/source"
tar -xzf "$temporary/source.tar.gz" -C "$temporary/source"
source="$temporary/source/VP"
[[ -f "$source/install.py" && -f "$source/install.sh" ]] || { echo 'Incomplete source archive.' >&2; exit 1; }
export XCPC_SOURCE_REVISION="$revision"
if [[ "$(uname -s)" == Darwin ]]; then interactive sh "$source/install-macos.sh"
else interactive bash "$source/install.sh"; fi
printf '\n%s\n' 'Installation complete. Open a new terminal and run: xcpc login'
