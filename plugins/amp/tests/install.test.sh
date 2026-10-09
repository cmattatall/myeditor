#!/usr/bin/env bash
set -euo pipefail
source_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
export HOME="$temporary/home with spaces"
plugins="$HOME/.config/amp/plugins"
installer="$source_dir/install.sh"

bash "$installer"
cmp "$source_dir/readiff.ts" "$plugins/readiff.ts"
settings="$HOME/.config/amp/settings.json"
python3 - "$settings" <<'PY'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
assert json.loads(path.read_text()) == {"amp.keymap": {"thread.interrupt": "ctrl+l"}}
path.write_text(json.dumps({"amp.gauge": "cost", "amp.keymap": {
    "thread.interrupt": ["esc esc", "ctrl+k"], "thread.copyURL": "<leader> u"
}}))
PY
printf 'another plugin\n' > "$plugins/other.ts"
printf 'old plugin with local edits\n' > "$plugins/rediff.ts"
bash "$installer"
cmp "$source_dir/readiff.ts" "$plugins/readiff.ts"
test ! -e "$plugins/rediff.ts"
backups=("$HOME"/.config/amp/plugin-backups/readiff-migration.*/rediff.ts)
test "${#backups[@]}" -eq 1
test "$(cat "${backups[0]}")" = 'old plugin with local edits'
python3 - "$settings" <<'PY'
import json, sys
from pathlib import Path
assert json.loads(Path(sys.argv[1]).read_text()) == {
    "amp.gauge": "cost", "amp.keymap": {
        "thread.interrupt": "ctrl+l", "thread.copyURL": "<leader> u"
    }
}
PY
cp "$settings" "$temporary/expected-settings"
bash "$installer"
cmp "$settings" "$temporary/expected-settings"
backups=("$HOME"/.config/amp/plugin-backups/readiff-migration.*/rediff.ts)
test "${#backups[@]}" -eq 1
test "$(cat "$plugins/other.ts")" = 'another plugin'
rm "$plugins/readiff.ts"
for name in readiff rediff; do
    ln -s "$source_dir/readiff.ts" "$plugins/$name.ts"
    if bash "$installer"; then echo 'Unexpected Home Manager symlink replacement'; exit 1; fi
    test -L "$plugins/$name.ts"
    rm "$plugins/$name.ts"
    ln -s "$temporary/missing" "$plugins/$name.ts"
    if bash "$installer"; then echo 'Unexpected dangling symlink replacement'; exit 1; fi
    test -L "$plugins/$name.ts"
    rm "$plugins/$name.ts"
    mkdir "$plugins/$name.ts"
    if bash "$installer"; then echo 'Unexpected directory replacement'; exit 1; fi
    test -d "$plugins/$name.ts"
    rmdir "$plugins/$name.ts"
done
printf 'existing plugin\n' > "$plugins/readiff.ts"
for invalid in '{broken' '[]' '{"amp.keymap": []}'; do
    printf '%s' "$invalid" > "$settings"
    if bash "$installer"; then echo 'Unexpected invalid settings replacement'; exit 1; fi
    test "$(cat "$settings")" = "$invalid"
    test "$(cat "$plugins/readiff.ts")" = 'existing plugin'
done
rm "$settings"
ln -s "$temporary/expected-settings" "$settings"
if bash "$installer"; then echo 'Unexpected settings symlink replacement'; exit 1; fi
test -L "$settings"
test "$(cat "$plugins/readiff.ts")" = 'existing plugin'
echo 'PASS: readiff installer'
