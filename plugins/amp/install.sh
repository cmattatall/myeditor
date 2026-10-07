#!/usr/bin/env bash
# Install only the Amp plugin. The editor is installed with Nix/Home Manager.
set -euo pipefail

case "${1:-}" in
    --help|-h)
        echo "Usage: $0"
        echo "Installs readiff for this user; backs up the old rediff plugin; never reloads Amp."
        exit 0 ;;
esac
if [ "$#" -ne 0 ]; then echo "Unexpected argument: $1" >&2; exit 2; fi

source_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
destination_dir="${HOME:?HOME must be set}/.config/amp/plugins"
destination="$destination_dir/readiff.ts"
legacy="$destination_dir/rediff.ts"

for path in "$destination" "$legacy"; do
    if [ -L "$path" ] || { [ -e "$path" ] && [ ! -f "$path" ]; }; then
        echo "Refusing to replace a symlink or non-file: $path (use Home Manager if it owns this file)." >&2
        exit 1
    fi
done

mkdir -p "$destination_dir"
temporary=$(mktemp "$destination_dir/.readiff-install.XXXXXX")
trap 'rm -f "$temporary"' EXIT
cp "$source_dir/readiff.ts" "$temporary"
chmod 644 "$temporary"
if [ -f "$legacy" ]; then
    backup_dir="$HOME/.config/amp/plugin-backups"
    mkdir -p "$backup_dir"
    backup=$(mktemp -d "$backup_dir/readiff-migration.XXXXXX")
    mv "$legacy" "$backup/rediff.ts"
    printf 'Backed up old plugin to %s\n' "$backup/rediff.ts"
fi
mv -f "$temporary" "$destination"
printf 'Installed %s\n' "$destination"
printf 'Restart Amp or reload its plugins, then run :harness connect amp in rediff.\n'
printf 'No other plugins were changed. No reload was performed.\n'
