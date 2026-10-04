#!/usr/bin/env bash
set -euo pipefail
source_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
export HOME="$temporary/home with spaces"
plugins="$HOME/.config/amp/plugins"
installer="$source_dir/install.sh"

bash "$installer"
cmp "$source_dir/rediff.ts" "$plugins/rediff.ts"
printf 'another plugin\n' > "$plugins/other.ts"
bash "$installer"
cmp "$source_dir/rediff.ts" "$plugins/rediff.ts"
test "$(cat "$plugins/other.ts")" = 'another plugin'
rm "$plugins/rediff.ts"
ln -s "$source_dir/rediff.ts" "$plugins/rediff.ts"
if bash "$installer"; then echo 'Unexpected Home Manager symlink replacement'; exit 1; fi
test -L "$plugins/rediff.ts"
rm "$plugins/rediff.ts"
ln -s "$temporary/missing" "$plugins/rediff.ts"
if bash "$installer"; then echo 'Unexpected dangling symlink replacement'; exit 1; fi
test -L "$plugins/rediff.ts"
rm "$plugins/rediff.ts"
mkdir "$plugins/rediff.ts"
if bash "$installer"; then echo 'Unexpected directory replacement'; exit 1; fi
test -d "$plugins/rediff.ts"
echo 'PASS: rediff installer'
