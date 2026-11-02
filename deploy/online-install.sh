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
configure_command_path() {
    # A successful/no-op updater may not have installed any shell PATH block.
    # The public installer must repair that too, without changing user settings.
    local commands="$1" platform="$2" shell_name="${SHELL:-/bin/bash}"
    shell_name="${shell_name##*/}"
    local configs=() directories=("$commands") profile candidate config target parent staged hops quoted prefix index
    if [[ "$platform" == Darwin ]]; then
        for prefix in "${HOMEBREW_PREFIX:-}" /opt/homebrew /usr/local; do
            if [[ -n "$prefix" && -f "$prefix/bin/brew" ]]; then
                directories+=("$prefix/bin" "$prefix/sbin"); break
            fi
        done
    fi
    if [[ "$platform" == Darwin || "$shell_name" == bash ]]; then
        profile="$HOME/.bash_profile"
        for candidate in .bash_profile .bash_login .profile; do
            if [[ -f "$HOME/$candidate" ]]; then profile="$HOME/$candidate"; break; fi
        done
        configs+=("$profile" "$HOME/.bashrc")
    fi
    if [[ "$platform" == Darwin || "$shell_name" == zsh ]]; then
        configs+=("${ZDOTDIR:-$HOME}/.zprofile" "${ZDOTDIR:-$HOME}/.zshrc")
    fi
    if [[ "$shell_name" == fish ]]; then configs+=("${XDG_CONFIG_HOME:-$HOME/.config}/fish/conf.d/xcpc-path.fish"); fi
    if (( ${#configs[@]} == 0 )); then
        printf 'Add the XCPC command directory to your shell PATH: %s\n' "$commands" >&2
        return 1
    fi
    for config in "${configs[@]}"; do
        target="$config"; hops=0
        while [[ -L "$target" ]]; do
            (( hops += 1 )); [[ "$hops" -le 40 ]] || return 1
            parent="$(dirname -- "$target")"
            target="$(readlink -- "$target")"
            [[ "$target" == /* ]] || target="$parent/$target"
        done
        parent="$(dirname -- "$target")"; mkdir -p -- "$parent"
        staged="$(mktemp "$parent/.xcpc-path.XXXXXXXX")"
        if [[ -f "$target" ]]; then
            cp -p -- "$target" "$staged"
            awk '$0=="# >>> XCPC command path >>>"{skip=1;next} $0=="# <<< XCPC command path <<<"{skip=0;next} !skip{if($0==""){blank++;next} while(blank-->0)print ""; blank=0; print}' "$target" > "$staged"
        fi
        printf '\n# >>> XCPC command path >>>\n' >> "$staged"
        for (( index=${#directories[@]}-1; index>=0; index-- )); do
            if [[ "$config" == *.fish ]]; then
                quoted="${directories[index]//\\/\\\\}"; quoted="${quoted//\'/\\\'}"
                printf "if test \"\$PATH[1]\" != '%s'\n  set -gx PATH '%s' \$PATH\nend\n" "$quoted" "$quoted" >> "$staged"
            else
                printf -v quoted '%q' "${directories[index]}"
                printf 'case "$PATH" in\n  %s|%s:*) ;;\n  *) export PATH=%s:"$PATH" ;;\nesac\n' "$quoted" "$quoted" "$quoted" >> "$staged"
            fi
        done
        printf '# <<< XCPC command path <<<\n' >> "$staged"
        mv -f -- "$staged" "$target"
    done
    printf 'Command directory: %s\nPATH configured for new terminals.\n' "$commands"
    printf 'For this terminal, run:\n  export PATH=%q:"$PATH"\n' "$commands"
}
platform="$(uname -s)"
shared_command="/usr/local/bin/xcpc"
existing_command=
# A legacy per-user updater only updates that private installation. The online
# installer now migrates those users to a shared application instead.
if [[ -x "$shared_command" ]]; then
    if [[ "$platform" == Linux && -L "$shared_command" && "$(readlink -- "$shared_command")" == "/opt/vp-helper/current/venv/bin/xcpc" ]]; then
        existing_command="$shared_command"
    elif [[ "$platform" == Darwin && -f "/usr/local/bin/.xcpc-native-commands.json" ]] &&
         awk '/"global"[[:space:]]*:[[:space:]]*true/{found=1} END{exit !found}' "/usr/local/bin/.xcpc-native-commands.json"; then
        existing_command="$shared_command"
    fi
fi
if [[ -n "$existing_command" ]]; then
    if interactive "$existing_command" update --cloud; then
        configure_command_path "/usr/local/bin" "$platform"
        export PATH="/usr/local/bin:$PATH"
        "$existing_command" --help >/dev/null
        printf '\n%s\n' 'Installation complete for all computer users. Open a new terminal and run: xcpc login'
        exit 0
    fi
    echo 'The existing shared command could not update. Preparing the standard installer.' >&2
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
if [[ "$platform" == Darwin ]]; then interactive sh "$source/install-macos.sh"
else interactive bash "$source/install.sh"; fi
configure_command_path "/usr/local/bin" "$platform"
export PATH="/usr/local/bin:$PATH"
"$shared_command" --help >/dev/null
printf '\n%s\n' 'Installation complete for all computer users. Open a new terminal and run: xcpc login'
