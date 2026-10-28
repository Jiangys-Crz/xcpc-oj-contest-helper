#!/bin/bash
set -euo pipefail

vp_source="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
vp_local=false
vp_offline=false
vp_python=python3
vp_submit_path=
vp_args=("$@")
while (( $# )); do
    case "$1" in
        --local) vp_local=true ;;
        --offline) vp_offline=true ;;
        --python)
            if (( $# < 2 )); then printf '%s\n' '--python requires an interpreter path.' >&2; exit 2; fi
            vp_python="$2"; shift ;;
        -h|--help)
            printf '%s\n' 'Usage: bash install.sh [--local] [--offline] [--python PYTHON] [EXISTING_SUBMIT]' \
                '  --local    Install inside this folder (.vp-install/ and bin/), without sudo except for apt.' \
                '  --offline  Use bundled wheels only; do not fall back to PyPI. Missing system packages still require apt.' \
                '  --python   Use a specific native Python 3.12 or newer interpreter.'
            exit 0 ;;
        --*) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
        *)
            if [[ -n "$vp_submit_path" ]]; then printf '%s\n' 'Only one existing submit path is allowed.' >&2; exit 2; fi
            vp_submit_path="$1" ;;
    esac
    shift
done
if [[ ! -f /etc/os-release ]]; then
    printf '%s\n' 'Run this installer after booting Ubuntu (not macOS).' >&2
    exit 1
fi
source /etc/os-release
if [[ "${ID:-}" != ubuntu ]]; then
    printf '%s\n' 'This installer supports Ubuntu only.' >&2
    exit 1
fi
case "$(uname -m)" in
    x86_64|aarch64|arm64) ;;
    *) printf '%s\n' 'Supported architectures: x86_64 and ARM64 (aarch64).' >&2; exit 1 ;;
esac
if ! $vp_local && [[ $EUID -ne 0 ]]; then
    exec sudo -- bash "$vp_source/install.sh" "${vp_args[@]}"
fi
vp_prefix=/opt/vp-helper
vp_bin=/usr/local/bin
if $vp_local; then
    vp_prefix="$vp_source/.vp-install"
    vp_bin="$vp_source/bin"
fi
if [[ -n "$vp_submit_path" ]]; then
    if [[ ! -f "$vp_submit_path" || ! -x "$vp_submit_path" ]]; then
        printf '%s\n' 'The existing submit path must be an executable file.' >&2
        exit 1
    fi
    vp_submit_path="$(readlink -f -- "$vp_submit_path")"
fi
if [[ -e "$vp_bin/vp" || -L "$vp_bin/vp" ]]; then
    if [[ ! -L "$vp_bin/vp" || "$(readlink -- "$vp_bin/vp")" != "$vp_prefix/current/venv/bin/vp" ]]; then
        printf '%s already exists and is not managed by this installation.\n' "$vp_bin/vp" >&2
        exit 1
    fi
fi
if [[ -n "$vp_submit_path" && ( -e "$vp_bin/submit" || -L "$vp_bin/submit" ) ]] && \
        [[ "$(readlink -f -- "$vp_bin/submit")" != "$vp_submit_path" ]]; then
    printf '%s\n' 'A different submit already exists. It was left in place.' >&2
    exit 1
fi
vp_apt() {
    if [[ $EUID -eq 0 ]]; then
        apt-get "$@"
    else
        sudo -- apt-get "$@"
    fi
}
if ! command -v "$vp_python" >/dev/null; then
    if [[ $vp_python != python3 ]]; then
        printf 'Python interpreter not found: %s\n' "$vp_python" >&2
        exit 1
    fi
    vp_apt update -qq
    vp_apt install -y --no-install-recommends python3 python3-venv ca-certificates
fi
# Prefer the native interpreter, including Ubuntu 26.04's Python 3.14.
# The bundled CPython is only a fallback for older x86_64 Ubuntu systems.
if ! "$vp_python" -c 'import sys; assert sys.version_info >= (3, 12)' 2>/dev/null; then
    if [[ $(uname -m) != x86_64 || ! -f "$vp_source/runtimes/manifest.json" ]]; then
        printf '%s\n' 'Native Python 3.12+ is required on ARM64. Use Ubuntu 24.04/26.04 or --python /path/to/python3.' >&2
        exit 1
    fi
    readarray -t vp_runtime < <("$vp_python" - "$vp_source/runtimes/manifest.json" <<'PY_RUNTIME'
import json, sys
from pathlib import Path
data = json.loads(Path(sys.argv[1]).read_text())
print(data['filename'])
print(data['sha256'])
PY_RUNTIME
)
    vp_archive="$vp_source/runtimes/${vp_runtime[0]}"
    printf '%s  %s\n' "${vp_runtime[1]}" "$vp_archive" | sha256sum -c -
    vp_runtime_root="$vp_prefix/runtime/${vp_runtime[1]:0:12}"
    if [[ ! -x "$vp_runtime_root/python/bin/python3" ]]; then
        install -d -m 755 "$vp_runtime_root"
        tar -xzf "$vp_archive" -C "$vp_runtime_root" --no-same-owner
    fi
    vp_python="$vp_runtime_root/python/bin/python3"
fi
vp_apt_packages=()
if ! command -v tmux >/dev/null; then vp_apt_packages+=(tmux); fi
if ! "$vp_python" -c 'import ensurepip' 2>/dev/null; then
    vp_python_version="$("$vp_python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    vp_apt_packages+=("python${vp_python_version}-venv")
fi
if [[ ! -s /etc/ssl/certs/ca-certificates.crt ]]; then vp_apt_packages+=(ca-certificates); fi
if (( ${#vp_apt_packages[@]} )); then
    vp_apt update -qq
    vp_apt install -y --no-install-recommends "${vp_apt_packages[@]}"
fi
install -d -m 755 "$vp_prefix/releases" "$vp_bin"
vp_release="$(mktemp -d "$vp_prefix/releases/$(date -u +%Y%m%dT%H%M%SZ).XXXXXX")"
vp_installed=false
trap 'if ! $vp_installed; then rm -rf -- "$vp_release"; fi' EXIT
chmod 755 "$vp_release"
install -d -m 755 "$vp_release/app"
cp -a "$vp_source/vp_helper" "$vp_source/pyproject.toml" "$vp_release/app/"
"$vp_python" -m venv "$vp_release/venv"
vp_pip=("$vp_release/venv/bin/python" -m pip)
vp_requirements=("$vp_release/app")
if [[ -d "$vp_source/third_party" ]]; then
    cp -a "$vp_source/third_party" "$vp_release/"
    vp_requirements+=(selectolax lxml robobrowser 'Werkzeug==0.16.1' 'prettytable<4')
fi
vp_wheel_args=()
if [[ -d "$vp_source/wheels" ]]; then vp_wheel_args+=(--find-links "$vp_source/wheels"); fi
# Probe the offline bundle first. pip rejects incompatible x86_64/cp312 wheels
# itself; ARM64 and newer Python can resolve native wheels from PyPI as needed.
if ! "${vp_pip[@]}" install --disable-pip-version-check --no-index "${vp_wheel_args[@]}" "${vp_requirements[@]}"; then
    if $vp_offline; then
        printf '%s\n' 'The wheel bundle is incomplete for this Python/CPU. Retry without --offline while online.' >&2
        exit 1
    fi
    printf '%s\n' 'Resolving compatible native dependencies from PyPI for this Python/CPU...'
    "${vp_pip[@]}" install --disable-pip-version-check --prefer-binary \
        --only-binary=cffi,curl-cffi,lxml,selectolax "${vp_wheel_args[@]}" "${vp_requirements[@]}"
fi
if [[ -d "$vp_release/third_party" ]]; then
    # A shell/Python polyglot resolves its own release, including paths with
    # spaces. Avoid the upstream scripts' hardcoded /opt interpreter shebang.
    "${vp_pip[0]}" - "$vp_release" <<'PY_LAUNCHER'
from pathlib import Path
import sys
root = Path(sys.argv[1])
header = '''#!/bin/sh
""":"
vp_script=$(readlink -f -- "$0")
exec "$(dirname -- "$vp_script")/../../venv/bin/python" "$vp_script" "$@"
":"""
'''
for platform in ('qoj', 'cf_submit'):
    path = root / 'third_party' / platform / 'submit'
    path.write_text(header + path.read_text().split('\n', 1)[1])
    path.chmod(0o755)
PY_LAUNCHER
fi
"${vp_pip[@]}" check
"$vp_release/venv/bin/vp" --help
if [[ -d "$vp_release/third_party" ]]; then
    "$vp_release/third_party/qoj/submit" --help
    "$vp_release/third_party/cf_submit/submit" --help >/dev/null
fi
vp_owner="${SUDO_USER:-$(id -un)}"
vp_owner_home="$(getent passwd "$vp_owner" | cut -d: -f6)"
vp_owner_group="$(id -gn "$vp_owner")"
vp_selected="$vp_owner_home/.local/state/vp/submit"
if $vp_local; then vp_selected="${XDG_STATE_HOME:-$HOME/.local/state}/vp/submit"; fi
if [[ -z "$vp_submit_path" && -L "$vp_selected" && ! -e "$vp_selected" ]]; then
    # Repair a dangling link from a previous VP installation, while preserving
    # links to unrelated user-provided submitters.
    vp_previous="$(readlink -- "$vp_selected")"
    case "$vp_previous" in
        /opt/vp-helper/*/third_party/qoj/submit|*/.vp-install/*/third_party/qoj/submit)
            ln -sfn "$vp_prefix/current/third_party/qoj/submit" "$vp_selected" ;;
        /opt/vp-helper/*/third_party/cf_submit/submit|*/.vp-install/*/third_party/cf_submit/submit)
            ln -sfn "$vp_prefix/current/third_party/cf_submit/submit" "$vp_selected" ;;
        *) printf 'Preserved external submit link: %s -> %s\n' "$vp_selected" "$vp_previous" ;;
    esac
fi
if [[ -z "$vp_submit_path" && -d "$vp_release/third_party" && ! -e "$vp_selected" && ! -L "$vp_selected" ]]; then
    install -d -m 700 "$(dirname -- "$vp_selected")"
    ln -s "$vp_prefix/current/third_party/qoj/submit" "$vp_selected"
    if [[ $EUID -eq 0 ]]; then
        chown "$vp_owner:$vp_owner_group" "$(dirname -- "$vp_selected")"
        chown -h "$vp_owner:$vp_owner_group" "$vp_selected"
    fi
fi
# Publish only after the new environment and both submitters have passed checks.
ln -s "$vp_release" "$vp_prefix/.current-new-$$"
mv -Tf "$vp_prefix/.current-new-$$" "$vp_prefix/current"
vp_installed=true
ln -sfn "$vp_prefix/current/venv/bin/vp" "$vp_bin/vp"
if [[ ! -e "$vp_bin/submit" && ! -L "$vp_bin/submit" ]]; then
    if [[ -n "$vp_submit_path" ]]; then
        ln -s -- "$vp_submit_path" "$vp_bin/submit"
    elif [[ -d "$vp_release/third_party" ]]; then
        ln -s -- "$vp_selected" "$vp_bin/submit"
    fi
elif [[ -z "$vp_submit_path" && "$(readlink -- "$vp_bin/submit" || true)" != "$vp_selected" ]]; then
    printf '%s\n' 'Preserved the previously installed submit.'
fi
printf 'Installed VP Helper using %s (%s).\n' "$("$vp_release/venv/bin/python" --version)" "$(uname -m)"
printf 'Commands: %s/vp and %s/submit\n' "$vp_bin" "$vp_bin"
if $vp_local; then
    printf 'For commands in any directory, add this to your shell configuration:\n  export PATH=%q:"$PATH"\n' "$vp_bin"
fi
