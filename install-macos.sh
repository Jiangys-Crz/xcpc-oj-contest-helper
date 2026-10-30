#!/bin/sh
set -eu
xcpc_source=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$(uname -s)" != Darwin ]; then
    echo 'macOS only; Ubuntu uses bash install.sh.' >&2
    exit 1
fi

xcpc_offline=false
xcpc_skip_browser=false
xcpc_skip_path=false
xcpc_target_root='/Library/Application Support/XCPC/app'
xcpc_target_bin='/usr/local/bin'
xcpc_root_supplied=false
xcpc_bin_supplied=false
xcpc_select_targets() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --target-root|--target-bin)
                [ "$#" -ge 2 ] || { echo "$1 requires a directory." >&2; exit 2; }
                if [ "$1" = --target-root ]; then xcpc_target_root=$2; xcpc_root_supplied=true
                else xcpc_target_bin=$2; xcpc_bin_supplied=true; fi
                shift
                ;;
        esac
        shift
    done
}
xcpc_select_targets "$@"
if "$xcpc_root_supplied" && "$xcpc_bin_supplied"; then
    if { [ "$xcpc_target_root" = '/Library/Application Support/XCPC/app' ] && [ "$xcpc_target_bin" != /usr/local/bin ]; } ||
       { [ "$xcpc_target_root" != '/Library/Application Support/XCPC/app' ] && [ "$xcpc_target_bin" = /usr/local/bin ]; }; then
        echo 'Shared installation requires both /Library/Application Support/XCPC/app and /usr/local/bin; mixed shared/private targets are not allowed.' >&2
        exit 2
    fi
elif "$xcpc_root_supplied" && [ "$xcpc_target_root" != '/Library/Application Support/XCPC/app' ]; then
    xcpc_target_bin="$HOME/.local/bin"
elif "$xcpc_bin_supplied" && [ "$xcpc_target_bin" != /usr/local/bin ]; then
    xcpc_target_root="$HOME/Library/Application Support/XCPC/app"
fi
xcpc_global=false
if [ "$xcpc_target_root" = '/Library/Application Support/XCPC/app' ] && [ "$xcpc_target_bin" = /usr/local/bin ]; then
    xcpc_global=true
fi
for xcpc_argument in "$@"; do
    case "$xcpc_argument" in
        --offline) xcpc_offline=true ;;
        --skip-browser-registration) xcpc_skip_browser=true ;;
        --skip-path-configuration) xcpc_skip_path=true ;;
        --help|-h)
            printf '%s\n' \
                'Usage: sh install-macos.sh [options]' \
                '' \
                'Install XCPC and any missing Python 3.12+, Git and tmux prerequisites.' \
                'Python 3.13 is preferred. Homebrew is installed when it is needed.' \
                'Homebrew may ask for your macOS password and confirmation.' \
                'Default: all users share /Library/Application Support/XCPC/app and /usr/local/bin.' \
                'Only application installation is elevated; browser accounts stay private.' \
                '' \
                '  --target-root PATH  Choose the application directory.' \
                '  --target-bin PATH   Choose the command directory.' \
                '  Custom private targets retain a previous user installation; do not mix them with shared defaults.' \
                '  --offline           Use existing prerequisites and bundled wheels only.' \
                '  -h, --help          Show this help without installing anything.'
            exit 0
            ;;
    esac
done

# Homebrew refuses root installations. Even when this wrapper was launched
# with sudo, prepare dependencies as the invoking administrator first.
if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then
    exec sudo -u "$SUDO_USER" -H -- /usr/bin/env \
        "XCPC_SOURCE_REVISION=${XCPC_SOURCE_REVISION:-}" \
        /bin/sh "$xcpc_source/install-macos.sh" "$@"
fi

xcpc_find_brew() {
    xcpc_brew=$(command -v brew 2>/dev/null || true)
    if "$xcpc_global"; then
        case "$xcpc_brew" in /Users/*|"$HOME"/*) xcpc_brew= ;; esac
    fi
    if [ -n "$xcpc_brew" ]; then
        return
    fi
    for xcpc_candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
        if [ -x "$xcpc_candidate" ]; then
            xcpc_brew=$xcpc_candidate
            return
        fi
    done
}

xcpc_brew_path() {
    if [ -n "$xcpc_brew" ]; then
        xcpc_prefix=$("$xcpc_brew" --prefix) || {
            echo 'Homebrew could not report its installation directory.' >&2
            exit 1
        }
        PATH="$xcpc_prefix/opt/python@3.13/bin:$xcpc_prefix/opt/python@3.13/libexec/bin:$xcpc_prefix/bin:$xcpc_prefix/sbin:$PATH"
        HOMEBREW_PREFIX=$xcpc_prefix
        export PATH HOMEBREW_PREFIX
    fi
}

xcpc_find_python() {
    for xcpc_name in python3.13 python3 python3.14 python3.12 python3.15 python; do
        xcpc_candidate=$(command -v "$xcpc_name" 2>/dev/null || true)
        if [ -n "$xcpc_candidate" ] && "$xcpc_candidate" -I -c \
            'import sys; sys.exit(1) if sys.version_info < (3, 12) else None; import venv, ensurepip; ensurepip.version()' \
            >/dev/null 2>&1; then
            if "$xcpc_global" && ! "$xcpc_candidate" -I -c '
# XCPC_GLOBAL_PYTHON_CHECK: a shared runtime must not depend on one user home.
import os, pathlib, stat, sys
paths = [pathlib.Path(getattr(sys, "_base_executable", sys.executable)).resolve(), pathlib.Path(sys.base_prefix).resolve()]
for path in paths:
    if path == pathlib.Path("/Users") or pathlib.Path("/Users") in path.parents:
        raise SystemExit(1)
    if not path.stat().st_mode & stat.S_IROTH or not path.stat().st_mode & stat.S_IXOTH:
        raise SystemExit(1)
    for parent in path.parents:
        if not parent.stat().st_mode & stat.S_IXOTH:
            raise SystemExit(1)
' >/dev/null 2>&1; then
                continue
            fi
            printf '%s\n' "$xcpc_candidate"
            return 0
        fi
    done
    return 1
}

xcpc_shared_tool() {
    if ! "$xcpc_global"; then return 0; fi
    case "$1" in /Users/*|"$HOME"/*) return 1 ;; esac
    if [ -n "$xcpc_python" ]; then
        "$xcpc_python" -I -c '
# XCPC_GLOBAL_TOOL_CHECK
import pathlib, stat, sys
path = pathlib.Path(sys.argv[1]).resolve()
if pathlib.Path("/Users") in path.parents:
    raise SystemExit(1)
if not path.stat().st_mode & stat.S_IROTH or not path.stat().st_mode & stat.S_IXOTH:
    raise SystemExit(1)
if any(not parent.stat().st_mode & stat.S_IXOTH for parent in path.parents):
    raise SystemExit(1)
' "$1" >/dev/null 2>&1
    fi
}

xcpc_check_prerequisites() {
    xcpc_python=$(xcpc_find_python) || xcpc_python=
    xcpc_missing=
    if [ -z "$xcpc_python" ]; then
        xcpc_missing=' Python 3.12+ (with venv and ensurepip)'
        if "$xcpc_global"; then xcpc_missing="$xcpc_missing in a shared system location"; fi
    fi
    xcpc_have_git=false
    if command -v git >/dev/null 2>&1 && git --version >/dev/null 2>&1 && xcpc_shared_tool "$(command -v git)"; then
        xcpc_have_git=true
    else
        xcpc_missing="$xcpc_missing Git"
    fi
    xcpc_have_tmux=false
    if command -v tmux >/dev/null 2>&1 && tmux -V >/dev/null 2>&1 && xcpc_shared_tool "$(command -v tmux)"; then
        xcpc_have_tmux=true
    else
        xcpc_missing="$xcpc_missing tmux"
    fi
}

xcpc_find_brew
xcpc_brew_path
xcpc_check_prerequisites
if [ -n "$xcpc_missing" ]; then
    if "$xcpc_offline"; then
        echo "Offline mode cannot install missing prerequisites:$xcpc_missing." >&2
        echo 'Re-run without --offline to set them up automatically, then retry offline.' >&2
        exit 1
    fi
    if [ "$(id -u)" -eq 0 ]; then
        echo 'Run install-macos.sh from your normal administrator account without sudo so Homebrew can prepare the shared dependencies.' >&2
        exit 1
    fi
    if [ -z "$xcpc_brew" ]; then
        echo 'Installing Homebrew to set up the missing prerequisites.'
        echo 'Follow the Homebrew prompts; macOS may ask for your password.'
        xcpc_bootstrap=$(mktemp "${TMPDIR:-/tmp}/xcpc-homebrew.XXXXXX")
        trap 'rm -f "$xcpc_bootstrap"' 0
        trap 'exit 130' INT
        trap 'exit 143' TERM
        if ! curl --fail --silent --show-error --location \
            https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh \
            --output "$xcpc_bootstrap"; then
            echo 'Could not download the official Homebrew installer. Check your connection and retry.' >&2
            exit 1
        fi
        if ! (umask 022; /bin/bash "$xcpc_bootstrap"); then
            echo 'Homebrew setup did not finish. Re-run this command to retry.' >&2
            exit 1
        fi
        rm -f "$xcpc_bootstrap"
        trap - 0 INT TERM
        xcpc_find_brew
        if [ -z "$xcpc_brew" ]; then
            echo 'Homebrew setup finished but brew was not found in PATH or its standard macOS locations.' >&2
            exit 1
        fi
        xcpc_brew_path
        # Homebrew setup may itself have supplied Git or an existing Python.
        xcpc_check_prerequisites
    fi
    # Function arguments keep the original installer arguments intact.
    xcpc_install_prerequisites() {
        set --
        if [ -z "$xcpc_python" ]; then set -- "$@" python@3.13; fi
        if ! "$xcpc_have_git"; then set -- "$@" git; fi
        if ! "$xcpc_have_tmux"; then set -- "$@" tmux; fi
        if [ "$#" -gt 0 ]; then
            (umask 022; "$xcpc_brew" install "$@")
        fi
    }
    xcpc_install_prerequisites
    # New Homebrew commands can precede a previously cached private command.
    hash -r 2>/dev/null || true
    xcpc_check_prerequisites
    if [ -n "$xcpc_missing" ]; then
        echo "Prerequisite setup did not make these tools usable:$xcpc_missing." >&2
        exit 1
    fi
fi
if ! "$xcpc_root_supplied"; then set -- --target-root "$xcpc_target_root" "$@"; fi
if ! "$xcpc_bin_supplied"; then set -- --target-bin "$xcpc_target_bin" "$@"; fi
if "$xcpc_global" && [ "$(id -u)" -ne 0 ]; then
    echo 'Installing shared XCPC commands for every user; macOS may request your administrator password.'
    # sudo resets the environment. Preserve only installer metadata and the
    # discovered public Homebrew runtime; user passwords and cookies are absent.
    sudo -- /usr/bin/env "XCPC_SOURCE_REVISION=${XCPC_SOURCE_REVISION:-}" \
        "HOMEBREW_PREFIX=${HOMEBREW_PREFIX:-}" "PATH=$PATH" \
        "$xcpc_python" "$xcpc_source/install.py" "$@"
    if ! "$xcpc_skip_path"; then
        if ! "$xcpc_python" -I -c '
import importlib.util, pathlib, sys
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("xcpc_install_paths", sys.argv[1])
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
installer.configure_macos_path(pathlib.Path(sys.argv[2]), all_shells=True)
' "$xcpc_source/install.py" "$xcpc_target_bin"; then
            echo '全局命令已安装；未能更新当前账号的终端配置，请重新打开终端。' >&2
        fi
    fi
    if ! "$xcpc_skip_browser"; then
        # Return to the ordinary account for extension registration. Never
        # create its browser manifests or account state as root.
        if ! "$xcpc_python" -I -c '
import json, os, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
release = pathlib.Path(json.loads((root/"current.json").read_text(encoding="utf8"))["release"]).resolve()
if not release.is_relative_to(root/"releases"):
    raise SystemExit("Invalid XCPC installation pointer")
python = str(release/"venv/bin/python")
os.execv(python, [python, "-I", "-m", "vp_helper.chromium_login"])
' "$xcpc_target_root"; then
            echo '全局命令已安装。请用普通账号运行 xcpc doctor cf 完成浏览器扩展初始化。' >&2
        fi
    fi
    exit 0
fi
if "$xcpc_global" && ! "$xcpc_skip_browser"; then
    echo '请在普通账号的终端运行 xcpc doctor cf 初始化该账号的浏览器扩展。'
fi
exec "$xcpc_python" "$xcpc_source/install.py" "$@"
