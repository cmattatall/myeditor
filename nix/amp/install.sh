#!/usr/bin/env bash
# Install only the Amp plugin. The editor is installed with Nix/Home Manager.
set -euo pipefail

replace_revdiff=0
case "${1:-}" in
    --replace-revdiff) replace_revdiff=1; shift ;;
    --help|-h)
        echo "Usage: $0 [--replace-revdiff]"
        echo "Installs anthrodiff for this user; never reloads Amp."
        echo "--replace-revdiff backs up the old user-local plugin outside Amp's plugin directory."
        exit 0 ;;
esac
if [ "$#" -ne 0 ]; then echo "Unexpected argument: $1" >&2; exit 2; fi

source_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
destination_dir="${HOME:?HOME must be set}/.config/amp/plugins"
destination="$destination_dir/anthrodiff.ts"
legacy="$destination_dir/revdiff.ts"
backup="$HOME/.config/amp/plugin-backups/revdiff.ts"

if [ -L "$destination" ] || { [ -e "$destination" ] && [ ! -f "$destination" ]; }; then
    echo "Refusing to replace a symlink or non-file: $destination (use Home Manager if it owns this file)." >&2
    exit 1
fi
if [ -e "$destination_dir/revdiff" ] || [ -L "$destination_dir/revdiff" ]; then
    echo "Remove the revdiff directory plugin explicitly before installing anthrodiff." >&2
    exit 1
fi
if [ -e "$legacy" ] || [ -L "$legacy" ]; then
    if [ "$replace_revdiff" -ne 1 ]; then
        echo "revdiff is installed; rerun with --replace-revdiff to back it up and replace it." >&2
        exit 1
    fi
    if [ -L "$legacy" ] || [ ! -f "$legacy" ]; then
        echo "Refusing to move a symlink or non-file: $legacy. Remove it through its configuration owner." >&2
        exit 1
    fi
    if [ -e "$backup" ] || [ -L "$backup" ]; then
        echo "Refusing to overwrite existing backup: $backup" >&2
        exit 1
    fi
fi

mkdir -p "$destination_dir"
temporary=$(mktemp "$destination_dir/.anthrodiff-install.XXXXXX")
trap 'rm -f "$temporary"' EXIT
cp "$source_dir/anthrodiff.ts" "$temporary"
chmod 644 "$temporary"
if [ -f "$legacy" ]; then
    mkdir -p "$(dirname -- "$backup")"
    mv "$legacy" "$backup"
    printf 'Backed up %s\n' "$backup"
fi
mv -f "$temporary" "$destination"
printf 'Installed %s\n' "$destination"
printf 'Restart Amp or reload its plugins, then run :harness connect amp in myeditor.\n'
printf 'Also disable any project/global revdiff plugin before reloading. No reload was performed.\n'
