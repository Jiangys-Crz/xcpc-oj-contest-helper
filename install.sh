#!/bin/bash
set -euo pipefail

vp_source="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
vp_local=false
vp_offline=false
vp_python=python3
vp_python_supplied=false
vp_submit_path=
vp_target_root=
vp_target_bin=
vp_args=("$@")
while (( $# )); do
    case "$1" in
        --local) vp_local=true ;;
        --offline) vp_offline=true ;;
        --python)
            if (( $# < 2 )); then printf '%s\n' '--python requires an interpreter path.' >&2; exit 2; fi
            vp_python="$2"; vp_python_supplied=true; shift ;;
        --target-root|--target-bin)
            if (( $# < 2 )) || [[ "$2" != /* ]]; then printf '%s\n' 'Update targets require absolute paths.' >&2; exit 2; fi
            if [[ "$1" == --target-root ]]; then vp_target_root="$2"; else vp_target_bin="$2"; fi
            shift ;;
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
    if [[ "${XCPC_SOURCE_REVISION:-}" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]]; then
        exec sudo -- env XCPC_SOURCE_REVISION="$XCPC_SOURCE_REVISION" bash "$vp_source/install.sh" "${vp_args[@]}"
    fi
    exec sudo -- bash "$vp_source/install.sh" "${vp_args[@]}"
fi
# The public bootstrap deliberately downloads with umask 077. Global program
# files must be usable by the invoking user after sudo finishes.
if ! $vp_local; then umask 022; fi
vp_prefix=/opt/vp-helper
vp_bin=/usr/local/bin
if $vp_local; then
    vp_prefix="$vp_source/.vp-install"
    vp_bin="$vp_source/bin"
fi
if [[ -n "$vp_target_root" || -n "$vp_target_bin" ]]; then
    if [[ -z "$vp_target_root" || -z "$vp_target_bin" ]]; then
        printf '%s\n' 'Both update target paths are required.' >&2
        exit 2
    fi
    vp_prefix="$vp_target_root"
    vp_bin="$vp_target_bin"
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
if [[ -e "$vp_bin/live" || -L "$vp_bin/live" ]]; then
    if [[ ! -L "$vp_bin/live" || "$(readlink -- "$vp_bin/live")" != "$vp_prefix/current/venv/bin/live" ]]; then
        printf '%s already exists and is not managed by this installation.\n' "$vp_bin/live" >&2
        exit 1
    fi
fi
if [[ -e "$vp_bin/bt" || -L "$vp_bin/bt" ]]; then
    if [[ ! -L "$vp_bin/bt" || "$(readlink -- "$vp_bin/bt")" != "$vp_prefix/current/venv/bin/bt" ]]; then
        printf '%s already exists and is not managed by this installation.\n' "$vp_bin/bt" >&2
        exit 1
    fi
fi
if [[ -e "$vp_bin/xcpc" || -L "$vp_bin/xcpc" ]]; then
    if [[ ! -L "$vp_bin/xcpc" || "$(readlink -- "$vp_bin/xcpc")" != "$vp_prefix/current/venv/bin/xcpc" ]]; then
        printf '%s already exists and is not managed by this installation.\n' "$vp_bin/xcpc" >&2
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
    if $vp_python_supplied; then
        printf 'Python interpreter not found: %s\n' "$vp_python" >&2
        exit 1
    fi
    vp_apt update -qq
    vp_apt install -y --no-install-recommends python3 python3-venv ca-certificates
fi
# Prefer the native interpreter, including Ubuntu 26.04's Python 3.14.
# The bundled CPython is only a fallback for older x86_64 Ubuntu systems.
if ! "$vp_python" -c 'import sys; assert sys.version_info >= (3, 12)' 2>/dev/null; then
    if $vp_python_supplied; then
        printf 'The supplied interpreter must be a usable Python 3.12 or newer: %s\n' "$vp_python" >&2
        exit 1
    fi
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
if ! command -v tmux >/dev/null || ! tmux -V >/dev/null 2>&1; then vp_apt_packages+=(tmux); fi
if ! command -v git >/dev/null || ! git --version >/dev/null 2>&1; then vp_apt_packages+=(git); fi
if ! "$vp_python" -c 'import venv, ensurepip; ensurepip.version()' 2>/dev/null; then
    vp_python_version="$("$vp_python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    vp_apt_packages+=("python${vp_python_version}-venv")
fi
if [[ ! -s /etc/ssl/certs/ca-certificates.crt ]]; then vp_apt_packages+=(ca-certificates); fi
if (( ${#vp_apt_packages[@]} )); then
    vp_apt update -qq
    vp_apt install -y --no-install-recommends "${vp_apt_packages[@]}"
fi
if ! tmux -V >/dev/null 2>&1 || ! git --version >/dev/null 2>&1; then
    printf '%s\n' 'Git or tmux is still unusable after prerequisite setup. Check the apt errors and retry.' >&2
    exit 1
fi
if ! "$vp_python" -c 'import venv, ensurepip; ensurepip.version()' 2>/dev/null; then
    printf 'Python venv support is still unusable after prerequisite setup: %s\n' "$vp_python" >&2
    exit 1
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
"$vp_release/venv/bin/xcpc" --help
"$vp_release/venv/bin/vp-cf-submit" --help
"$vp_release/venv/bin/vp-hydro-submit" --help
"$vp_release/venv/bin/vp-nowcoder-submit" --help
"$vp_release/venv/bin/vp-submit" --help
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
            ln -sfn "$vp_prefix/current/venv/bin/vp-submit" "$vp_selected" ;;
        /opt/vp-helper/*/third_party/cf_submit/submit|*/.vp-install/*/third_party/cf_submit/submit)
            ln -sfn "$vp_prefix/current/venv/bin/vp-submit" "$vp_selected" ;;
        *) printf 'Preserved external submit link: %s -> %s\n' "$vp_selected" "$vp_previous" ;;
    esac
fi
if [[ -z "$vp_submit_path" && -d "$vp_release/third_party" && ! -e "$vp_selected" && ! -L "$vp_selected" ]]; then
    # Create missing per-user ancestors as that user, not as root. Otherwise a
    # fresh ~/.local/state would be root-owned after a global installation.
    "$vp_python" - "$vp_owner" "$(dirname -- "$vp_selected")" <<'PY_USER_STATE'
import os
from pathlib import Path
import pwd
import sys
account = pwd.getpwnam(sys.argv[1])
if os.geteuid() == 0 and account.pw_uid != 0:
    os.initgroups(account.pw_name, account.pw_gid)
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
if os.geteuid() != account.pw_uid:
    raise PermissionError("Cannot create another user's state directory.")
Path(sys.argv[2]).mkdir(parents=True, exist_ok=True, mode=0o700)
PY_USER_STATE
    ln -s "$vp_prefix/current/venv/bin/vp-submit" "$vp_selected"
    if [[ $EUID -eq 0 ]]; then
        chown "$vp_owner:$vp_owner_group" "$(dirname -- "$vp_selected")"
        chown -h "$vp_owner:$vp_owner_group" "$vp_selected"
    fi
fi
if [[ -z "$vp_submit_path" && -L "$vp_selected" ]]; then
    case "$(readlink -- "$vp_selected")" in
        "$vp_prefix"/*/third_party/qoj/submit|"$vp_prefix"/*/third_party/cf_submit/submit|"$vp_prefix"/*/venv/bin/vp-*)
            ln -sfn "$vp_prefix/current/venv/bin/vp-submit" "$vp_selected" ;;
    esac
fi
# Record the install target independently from the source checkout. The updater
# can then install a clean cached checkout back into the same local/global path.
"$vp_release/venv/bin/python" - "$vp_release" "$vp_source" "$vp_prefix" "$vp_bin" "$vp_local" "$vp_python" "$vp_submit_path" "${vp_runtime_root:-}" <<'PY_INSTALLATION'
import importlib.util, json, os, re, shlex, shutil, subprocess, sys
from pathlib import Path
release, source, prefix, commands, local, python, submit, runtime = sys.argv[1:]
revision = ""
if shutil.which("git"):
    try:
        # Global installation runs under sudo while the explicit source folder
        # belongs to the invoking user. Trust only this installer source for
        # these read-only revision queries, without changing Git configuration.
        git = ["git", "-c", "safe.directory=" + str(Path(source).resolve()), "-C", source]
        top = subprocess.check_output([*git, "rev-parse", "--show-toplevel"], stderr=subprocess.DEVNULL, text=True, timeout=5).strip()
        if Path(top).resolve() == Path(source).resolve():
            revision = subprocess.check_output([*git, "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True, timeout=5).strip()
    except (subprocess.SubprocessError, OSError):
        pass
if re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", os.environ.get("XCPC_SOURCE_REVISION", "")):
    revision = os.environ["XCPC_SOURCE_REVISION"]
path = Path(release) / "installation.json"
path.write_text(json.dumps({"install_root": str(Path(prefix).absolute()), "bin_directory": str(Path(commands).absolute()),
                           "source_directory": str(Path(source).resolve()), "source_revision": revision,
                           "local": local == "true", "global": local != "true", "python": shutil.which(python) or python,
                           "submit_path": submit}, ensure_ascii=False, indent=2))
path.chmod(0o644)
if local != 'true':
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location('xcpc_runtime_permissions', Path(source)/'install.py')
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    installer.make_runtime_readable(release)
    if runtime:
        installer.make_runtime_readable(runtime)
    profile = Path('/etc/profile.d/xcpc-vp-system.sh')
    profile.parent.mkdir(parents=True, exist_ok=True)
    quoted = shlex.quote(commands)
    installer.replace_file(profile, ('# XCPC shared command directory\ncase "$PATH" in\n'
        f'  {quoted}|{quoted}:*) ;;\n  *) export PATH={quoted}:"$PATH" ;;\nesac\n').encode('utf8'),mode=0o644)
PY_INSTALLATION
# Publish only after the new environment and both submitters have passed checks.
ln -s "$vp_release" "$vp_prefix/.current-new-$$"
mv -Tf "$vp_prefix/.current-new-$$" "$vp_prefix/current"
vp_installed=true
ln -sfn "$vp_prefix/current/venv/bin/vp" "$vp_bin/vp"
ln -sfn "$vp_prefix/current/venv/bin/live" "$vp_bin/live"
ln -sfn "$vp_prefix/current/venv/bin/bt" "$vp_bin/bt"
ln -sfn "$vp_prefix/current/venv/bin/xcpc" "$vp_bin/xcpc"
if [[ ! -e "$vp_bin/printfile" && ! -L "$vp_bin/printfile" ]]; then
    ln -s -- "$vp_prefix/current/venv/bin/printfile" "$vp_bin/printfile"
elif [[ "$(readlink -- "$vp_bin/printfile" || true)" == "$vp_prefix/current/venv/bin/printfile" ]]; then
    ln -sfn "$vp_prefix/current/venv/bin/printfile" "$vp_bin/printfile"
else
    printf 'Preserved existing printfile command: %s\n' "$vp_bin/printfile"
fi
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
printf 'Commands: %s/xcpc, %s/vp, %s/live, %s/bt and %s/submit\n' "$vp_bin" "$vp_bin" "$vp_bin" "$vp_bin" "$vp_bin"
# Configure the invoking user's shell, even when the global installer was
# elevated with sudo. Load the helper before dropping privileges so a source
# checkout inside a private parent directory remains usable.
if ! "$vp_python" - "$vp_source/install.py" "$vp_bin" "$vp_owner" <<'PY_PATH'
import importlib.util
import os
from pathlib import Path
import pwd
import sys

source, commands, owner = sys.argv[1:]
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('xcpc_path_configuration', source)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
account = pwd.getpwnam(owner)
if os.geteuid() == 0 and account.pw_uid != 0:
    os.initgroups(account.pw_name, account.pw_gid)
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
if os.geteuid() != account.pw_uid:
    raise PermissionError('Cannot safely update another user\'s shell configuration.')
files = installer.configure_macos_path(Path(commands), home=account.pw_dir,
                                       shell=account.pw_shell or '/bin/bash')
if files:
    print('Configured PATH for new terminals: ' + ', '.join(map(str, files)))
else:
    print('Shell PATH could not be configured automatically for ' + account.pw_shell)
PY_PATH
then
    printf '%s\n' 'Could not configure shell PATH automatically; add the export below to your shell configuration.' >&2
fi
printf 'For this terminal, run:\n  export PATH=%q:"$PATH"\n' "$vp_bin"
