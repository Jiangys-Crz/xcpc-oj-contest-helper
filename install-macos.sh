#!/bin/sh
set -eu
xcpc_source=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$(uname -s)" != Darwin ]; then
    echo 'macOS only; Ubuntu uses bash install.sh.' >&2
    exit 1
fi

xcpc_offline=false
for xcpc_argument in "$@"; do
    case "$xcpc_argument" in
        --offline) xcpc_offline=true ;;
        --help|-h)
            printf '%s\n' \
                'Usage: sh install-macos.sh [options]' \
                '' \
                'Install XCPC and any missing Python 3.12+, Git and tmux prerequisites.' \
                'Python 3.13 is preferred. Homebrew is installed when it is needed.' \
                'Homebrew may ask for your macOS password and confirmation.' \
                '' \
                '  --target-root PATH  Choose the application directory.' \
                '  --target-bin PATH   Choose the command directory.' \
                '  --offline           Use existing prerequisites and bundled wheels only.' \
                '  -h, --help          Show this help without installing anything.'
            exit 0
            ;;
    esac
done

xcpc_find_brew() {
    xcpc_brew=$(command -v brew 2>/dev/null || true)
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
            printf '%s\n' "$xcpc_candidate"
            return 0
        fi
    done
    return 1
}

xcpc_check_prerequisites() {
    xcpc_python=$(xcpc_find_python) || xcpc_python=
    xcpc_missing=
    if [ -z "$xcpc_python" ]; then
        xcpc_missing=' Python 3.12+ (with venv and ensurepip)'
    fi
    xcpc_have_git=false
    if command -v git >/dev/null 2>&1 && git --version >/dev/null 2>&1; then
        xcpc_have_git=true
    else
        xcpc_missing="$xcpc_missing Git"
    fi
    xcpc_have_tmux=false
    if command -v tmux >/dev/null 2>&1 && tmux -V >/dev/null 2>&1; then
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
        if ! /bin/bash "$xcpc_bootstrap"; then
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
            "$xcpc_brew" install "$@"
        fi
    }
    xcpc_install_prerequisites
    xcpc_check_prerequisites
    if [ -n "$xcpc_missing" ]; then
        echo "Prerequisite setup did not make these tools usable:$xcpc_missing." >&2
        exit 1
    fi
fi
exec "$xcpc_python" "$xcpc_source/install.py" "$@"
