#!/usr/bin/env bash
set -euo pipefail
source_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
export HOME="$temporary/home with spaces"
plugins="$HOME/.config/amp/plugins"
backup="$HOME/.config/amp/plugin-backups/revdiff.ts"
installer="$source_dir/install.sh"

bash "$installer"
cmp "$source_dir/anthrodiff.ts" "$plugins/anthrodiff.ts"
bash "$installer"
cmp "$source_dir/anthrodiff.ts" "$plugins/anthrodiff.ts"
printf 'old plugin\n' > "$plugins/revdiff.ts"
if bash "$installer"; then echo 'Unexpected replacement without opt-in'; exit 1; fi
test -f "$plugins/revdiff.ts"
bash "$installer" --replace-revdiff
test ! -e "$plugins/revdiff.ts"
test "$(cat "$backup")" = 'old plugin'
cmp "$source_dir/anthrodiff.ts" "$plugins/anthrodiff.ts"
printf 'new legacy contents\n' > "$plugins/revdiff.ts"
if bash "$installer" --replace-revdiff; then echo 'Unexpected backup overwrite'; exit 1; fi
test "$(cat "$plugins/revdiff.ts")" = 'new legacy contents'
rm "$plugins/revdiff.ts"
rm "$plugins/anthrodiff.ts"
ln -s "$source_dir/anthrodiff.ts" "$plugins/anthrodiff.ts"
if bash "$installer"; then echo 'Unexpected Home Manager symlink replacement'; exit 1; fi
test -L "$plugins/anthrodiff.ts"
rm "$plugins/anthrodiff.ts"
ln -s "$temporary/missing" "$plugins/revdiff.ts"
if bash "$installer" --replace-revdiff; then echo 'Unexpected legacy symlink replacement'; exit 1; fi
test -L "$plugins/revdiff.ts"
rm "$plugins/revdiff.ts"
mkdir "$plugins/revdiff"
if bash "$installer"; then echo 'Unexpected duplicate directory plugin'; exit 1; fi
test ! -e "$plugins/anthrodiff.ts"
echo 'PASS: anthrodiff installer'
