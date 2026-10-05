#!/usr/bin/env bash
# Install only the oh-my-pi (omp) extension. The editor is installed with Nix/Home Manager.
set -euo pipefail
export LC_ALL=C # Byte-wise [a-z] ranges in the profile check.

case "${1:-}" in
    --help|-h)
        echo "Usage: $0"
        echo "Installs rediff.ts into omp's user extensions directory (default ~/.omp/agent/extensions);"
        echo "honors OMP_PROFILE/PI_PROFILE, PI_CONFIG_DIR, and PI_CODING_AGENT_DIR like omp 18.4.4."
        echo "Never starts or restarts omp."
        exit 0 ;;
esac
if [ "$#" -ne 0 ]; then echo "Unexpected argument: $1" >&2; exit 2; fi

source_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
home="${HOME:?HOME must be set}"
# Mirror @oh-my-pi/pi-utils dirs.ts: the config root is $HOME/${PI_CONFIG_DIR:-.omp}; a named profile
# (OMP_PROFILE wins even when empty, else PI_PROFILE) owns <root>/profiles/<name>/agent and ignores
# PI_CODING_AGENT_DIR; otherwise a nonempty PI_CODING_AGENT_DIR is the agent dir (path.resolve, no ~).
config_root="$home/${PI_CONFIG_DIR:-.omp}"
if [ "${OMP_PROFILE+set}" = set ]; then profile="$OMP_PROFILE"; else profile="${PI_PROFILE:-}"; fi
profile="${profile#"${profile%%[![:space:]]*}"}"
profile="${profile%"${profile##*[![:space:]]}"}"
[ "$profile" = default ] && profile=''
if [ -n "$profile" ]; then
    # Same rules as normalizeProfileName (names are lowercase, so the reserved check needs no case folding).
    if ! [[ "$profile" =~ ^[a-z0-9][a-z0-9._-]{0,63}$ ]] || [[ "$profile" == *. ]] ||
        [[ "$profile" =~ ^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$ ]]; then
        echo "Invalid OMP profile \"$profile\"; omp would refuse it too." >&2
        exit 2
    fi
    agent_dir="$config_root/profiles/$profile/agent"
elif [ -n "${PI_CODING_AGENT_DIR:-}" ]; then
    case "$PI_CODING_AGENT_DIR" in
        /*) agent_dir="$PI_CODING_AGENT_DIR" ;;
        *) agent_dir="$PWD/$PI_CODING_AGENT_DIR" ;;
    esac
else
    agent_dir="$config_root/agent"
fi
destination_dir="$agent_dir/extensions"
destination="$destination_dir/rediff.ts"

if [ -L "$destination" ] || { [ -e "$destination" ] && [ ! -f "$destination" ]; }; then
    echo "Refusing to replace a symlink or non-file: $destination (use Home Manager if it owns this file)." >&2
    exit 1
fi

mkdir -p "$destination_dir"
temporary=$(mktemp "$destination_dir/.rediff-install.XXXXXX")
trap 'rm -f "$temporary"' EXIT
cp "$source_dir/rediff.ts" "$temporary"
chmod 644 "$temporary"
mv -f "$temporary" "$destination"
printf 'Installed %s\n' "$destination"
printf 'omp loads extensions at startup: run /restart in open omp sessions (or start omp),\n'
printf 'then run :harness connect omp in rediff.\n'
printf 'No other extensions were changed. omp was not started or restarted.\n'
