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
printf 'another plugin\n' > "$plugins/other.ts"
printf 'old plugin with local edits\n' > "$plugins/rediff.ts"
bash "$installer"
cmp "$source_dir/readiff.ts" "$plugins/readiff.ts"
test ! -e "$plugins/rediff.ts"
backups=("$HOME"/.config/amp/plugin-backups/readiff-migration.*/rediff.ts)
test "${#backups[@]}" -eq 1
test "$(cat "${backups[0]}")" = 'old plugin with local edits'
bash "$installer"
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
echo 'PASS: readiff installer'
