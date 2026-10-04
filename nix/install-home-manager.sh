#!/usr/bin/env bash
set -euo pipefail

case "${1:---build}" in
    --build|--switch) mode=${1:---build} ;;
    --help|-h)
        echo "Usage: ./install.sh [--build | --switch]"
        echo "Or, from a vendored nix directory: bash install-home-manager.sh [--build | --switch]"
        echo "Default: build only. --switch activates Home Manager and adds its session setup to your shell rc."
        echo "For an existing Home Manager setup, import home-manager.nix instead; see nix/README.md."
        exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
esac
if [ "$#" -gt 1 ]; then echo "Expected at most one argument" >&2; exit 2; fi

source_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
export MYEDITOR_FLAKE="path:$source_dir"
export USER
USER=$(id -un)
: "${HOME:?HOME must be set}"

if [ "$mode" = --switch ]; then
    # Never replace an unrelated standalone or nix-darwin-managed generation.
    for profile in "${XDG_STATE_HOME:-$HOME/.local/state}/nix/profiles/home-manager" \
        "/nix/var/nix/profiles/per-user/$USER/home-manager" \
        "/etc/profiles/per-user/$USER"; do
        if [ -e "$profile" ] || [ -L "$profile" ]; then
            marker="$profile/home-files/.config/myeditor/standalone-owner"
            if [ ! -f "$marker" ] || [ "$(cat "$marker")" != myeditor-standalone-v1 ]; then
                echo "Refusing to replace an existing Home Manager/system profile: $profile" >&2
                echo "Import this repository's Home Manager module into your existing configuration instead." >&2
                exit 1
            fi
        fi
    done
    for config in "${XDG_CONFIG_HOME:-$HOME/.config}/home-manager" \
        "${XDG_CONFIG_HOME:-$HOME/.config}/nixpkgs/home.nix"; do
        if [ -e "$config" ] || [ -L "$config" ]; then
            echo "Existing Home Manager configuration found: $config. Use its switch command instead." >&2
            exit 1
        fi
    done
    case "${SHELL:-}" in
        */zsh|zsh) shell_rc="${ZDOTDIR:-$HOME}/.zshrc" ;;
        */bash|bash)
            if [ "$(uname -s)" = Darwin ]; then shell_rc="$HOME/.bash_profile"; else shell_rc="$HOME/.bashrc"; fi ;;
        *) echo "Unsupported shell: ${SHELL:-unset}; source Home Manager's hm-session-vars.sh manually." >&2; exit 1 ;;
    esac
    session_line='. "$HOME/.nix-profile/etc/profile.d/hm-session-vars.sh"'
    if ! grep -Fxq "$session_line" "$shell_rc" 2>/dev/null; then
        if [ -L "$shell_rc" ] || { [ -e "$shell_rc" ] && [ ! -f "$shell_rc" ]; }; then
            echo "Refusing to modify managed/non-regular shell configuration: $shell_rc" >&2
            exit 1
        fi
    fi
fi

generation=$(nix build --impure --file "$source_dir/standalone-home.nix" --no-link --print-out-paths)
printf 'Built Home Manager generation: %s\n' "$generation"
if [ "$mode" != --switch ]; then
    echo "Nothing activated. Run this script with --switch to install nvim."
    exit 0
fi

# The activation script's default driver updates the Home Manager profile itself.
"$generation/activate"
if ! grep -Fxq "$session_line" "$shell_rc" 2>/dev/null; then
    if [ -f "$shell_rc" ]; then
        backup=$(mktemp "$shell_rc.myeditor-backup.XXXXXX")
        cp -p "$shell_rc" "$backup"
        printf 'Backed up shell configuration: %s\n' "$backup"
    fi
    printf '\n# Nix/Home Manager: make the packaged nvim take precedence over Homebrew.\n%s\n' "$session_line" >> "$shell_rc"
fi
echo 'Installed. On first install or migration from an older installer, refresh this shell once:'
echo 'unset __HM_SESS_VARS_SOURCED; . "$HOME/.nix-profile/etc/profile.d/hm-session-vars.sh"; hash -r'
echo 'Later updates only require quitting and reopening Neovim, not a new terminal.'
echo 'Then run: command -v nvim'
echo 'Your ~/.config/nvim and Amp plugins were not modified.'
