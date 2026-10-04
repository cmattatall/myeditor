#!/usr/bin/env bash
# Install only the Amp plugin. The editor is installed with Nix/Home Manager.
set -euo pipefail

case "${1:-}" in
    --help|-h)
        echo "Usage: $0"
        echo "Installs rediff for this user; never reloads Amp."
        exit 0 ;;
esac
if [ "$#" -ne 0 ]; then echo "Unexpected argument: $1" >&2; exit 2; fi

source_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
destination_dir="${HOME:?HOME must be set}/.config/amp/plugins"
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
printf 'Restart Amp or reload its plugins, then run :harness connect amp in rediff.\n'
printf 'No other plugins were changed. No reload was performed.\n'
