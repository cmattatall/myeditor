#!/usr/bin/env bash
# Install the Amp plugin and its interrupt key. The editor is installed with Nix.
set -euo pipefail

case "${1:-}" in
    --help|-h)
        echo "Usage: $0"
        echo "Installs readiff for this user; backs up the old rediff plugin; never reloads Amp."
        echo "Sets amp.keymap thread.interrupt to ctrl+l, preserving other settings. Requires Python 3."
        exit 0 ;;
esac
if [ "$#" -ne 0 ]; then echo "Unexpected argument: $1" >&2; exit 2; fi

source_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
destination_dir="${HOME:?HOME must be set}/.config/amp/plugins"
destination="$destination_dir/readiff.ts"
legacy="$destination_dir/rediff.ts"
settings="$HOME/.config/amp/settings.json"

for path in "$destination" "$legacy" "$settings"; do
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
# Validate and merge settings before replacing either plugin. Never discard an
# unreadable settings file or other user keybindings to install this shortcut.
python3 - "$settings" <<'PY'
import json
import os
import sys
import tempfile
from pathlib import Path

path = Path(sys.argv[1])
try:
    settings = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(settings, dict):
        raise ValueError("settings must be a JSON object")
    keymap = settings.setdefault("amp.keymap", {})
    if not isinstance(keymap, dict):
        raise ValueError("amp.keymap must be a JSON object")
    if keymap.get("thread.interrupt") != "ctrl+l":
        keymap["thread.interrupt"] = "ctrl+l"
        fd, temporary = tempfile.mkstemp(prefix=".readiff-keymap-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as output:
                json.dump(settings, output, indent=2)
                output.write("\n")
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
except (OSError, ValueError) as error:
    sys.exit(f"Cannot configure Amp interrupt key in {path}: {error}")
PY
if [ -f "$legacy" ]; then
    backup_dir="$HOME/.config/amp/plugin-backups"
    mkdir -p "$backup_dir"
    backup=$(mktemp -d "$backup_dir/readiff-migration.XXXXXX")
    mv "$legacy" "$backup/rediff.ts"
    printf 'Backed up old plugin to %s\n' "$backup/rediff.ts"
fi
mv -f "$temporary" "$destination"
printf 'Installed %s\n' "$destination"
printf 'Configured Ctrl+L to interrupt Amp (replaces Esc Esc); other settings and keybindings preserved.\n'
printf 'Restart Amp or reload its plugins, then run :harness connect amp in rediff.\n'
printf 'No other plugins were changed. No reload was performed.\n'
