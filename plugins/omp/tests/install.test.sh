#!/usr/bin/env bash
set -euo pipefail
source_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
export HOME="$temporary/home with spaces"
unset PI_CODING_AGENT_DIR PI_CONFIG_DIR OMP_PROFILE PI_PROFILE
extensions="$HOME/.omp/agent/extensions"
installer="$source_dir/install.sh"
# Fail if the installer ever tries to start omp.
mkdir -p "$temporary/bin"
printf '#!/bin/sh\necho omp was started >&2\nexit 99\n' > "$temporary/bin/omp"
chmod +x "$temporary/bin/omp"
export PATH="$temporary/bin:$PATH"
installed() { cmp "$source_dir/rediff.ts" "$1/rediff.ts"; }

output=$(bash "$installer")
installed "$extensions"
python3 -c 'import os, sys; assert os.stat(sys.argv[1]).st_mode & 0o777 == 0o644' "$extensions/rediff.ts"
grep -q '/restart' <<<"$output"
grep -q ':harness connect omp' <<<"$output"
grep -q "Installed $extensions/rediff.ts" <<<"$output"
test "$(ls -A "$extensions")" = rediff.ts # No temporary file is left behind.
printf 'another extension\n' > "$extensions/other.ts"
bash "$installer" >/dev/null
installed "$extensions"
test "$(cat "$extensions/other.ts")" = 'another extension'

# PI_CODING_AGENT_DIR replaces the agent directory (default profile only), resolved like path.resolve.
PI_CODING_AGENT_DIR="$temporary/custom agent" bash "$installer" >/dev/null
installed "$temporary/custom agent/extensions"
(cd "$temporary" && PI_CODING_AGENT_DIR='relative agent' bash "$installer" >/dev/null)
installed "$temporary/relative agent/extensions"
(cd "$temporary" && PI_CODING_AGENT_DIR='~/tilde' bash "$installer" >/dev/null)
installed "$temporary/~/tilde/extensions" # omp does not expand ~ in PI_CODING_AGENT_DIR.
PI_CODING_AGENT_DIR='' bash "$installer" >/dev/null # Empty means the default, like omp.
test ! -e "$temporary/extensions"

# PI_CONFIG_DIR renames the config root under HOME.
PI_CONFIG_DIR='.omp-alt' bash "$installer" >/dev/null
installed "$HOME/.omp-alt/agent/extensions"

# Named profiles own their agent directory and ignore PI_CODING_AGENT_DIR; OMP_PROFILE beats PI_PROFILE.
OMP_PROFILE=work PI_CODING_AGENT_DIR="$temporary/ignored" bash "$installer" >/dev/null
installed "$HOME/.omp/profiles/work/agent/extensions"
test ! -e "$temporary/ignored"
PI_PROFILE=legacy PI_CONFIG_DIR='.omp-alt' bash "$installer" >/dev/null
installed "$HOME/.omp-alt/profiles/legacy/agent/extensions"
OMP_PROFILE='' PI_PROFILE=legacy PI_CODING_AGENT_DIR="$temporary/explicit default" bash "$installer" >/dev/null
installed "$temporary/explicit default/extensions" # An explicitly empty OMP_PROFILE selects the default profile.
OMP_PROFILE=' default ' bash "$installer" >/dev/null
for invalid in 'Work' '../x' 'x.' 'con' 'lpt1.txt'; do
    if OMP_PROFILE="$invalid" bash "$installer" 2>/dev/null; then echo "Unexpected profile accepted: $invalid"; exit 1; fi
done

rm "$extensions/rediff.ts"
ln -s "$source_dir/rediff.ts" "$extensions/rediff.ts"
if bash "$installer" 2>/dev/null; then echo 'Unexpected Home Manager symlink replacement'; exit 1; fi
test -L "$extensions/rediff.ts"
rm "$extensions/rediff.ts"
ln -s "$temporary/missing" "$extensions/rediff.ts"
if bash "$installer" 2>/dev/null; then echo 'Unexpected dangling symlink replacement'; exit 1; fi
test -L "$extensions/rediff.ts"
rm "$extensions/rediff.ts"
mkdir "$extensions/rediff.ts"
if bash "$installer" 2>/dev/null; then echo 'Unexpected directory replacement'; exit 1; fi
test -d "$extensions/rediff.ts"
if bash "$installer" extra 2>/dev/null; then echo 'Unexpected argument accepted'; exit 1; fi
bash "$installer" --help | grep -q 'Never starts or restarts omp'
echo 'PASS: rediff omp installer'
