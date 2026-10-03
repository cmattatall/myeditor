# Feature list

myeditor is a portable, Nix-packaged Neovim configuration with ordinary
editing and a protected, side-by-side Git Review workspace. Review supports
exact-range comments, local drafts/outbox, explicit staging, and Amp, Claude,
or custom feedback receivers. It intentionally does not claim complete
revdiff parity.

## Navigation and review progress

- **Space e** / `:ft` / `:Explorer` focuses the Git sidebar in Review and Neo-tree
  while editing. **Space d** / `:FocusDiff` focuses the diff or editor.
  **Space E** toggles ordinary Neo-tree while editing and is focus-only in
  Review. **Tab** switches tree/diff focus in Review.
- **Ctrl-A / Ctrl-E** move to the start/end of the line in Insert mode and
  command/search input, including annotations and harness messages.
  Normal-mode Vim bindings remain unchanged.
- **Option+Left/Up/h/k** moves backward by a word; **Option+Right/Down/l/j**
  moves forward. Works in Normal, Visual, Insert, and command/search input.
  Terminals must send Option as Alt/Meta; Esc-b/Esc-f word-key sequences work too.
- **s / S** in a Review source pane stages/unstages the hunk under the cursor.
  Inside annotations and ordinary files, native `s`/`S` editing is unchanged.
  **Space s** still toggles hunk staging; **Space S** toggles file staging.
- **]** / **[** jump immediately to the next/previous cross-file hunk and
  accept counts. **Space j/k** select the next/previous changed file.
- **Space f** / `:Files` fuzzy-finds nonignored project files while editing;
  in Review it finds visible changed entries, keeping staged and unstaged
  instances distinct. **Space /** / `:Search` searches saved project contents
  while editing and old plus new lines (including deletions) in Review.
  **Enter** chooses and **Esc** cancels. Native `/`, `n`, and `N` remain
  current-buffer search.
- **Space m** / `:ReviewMark` toggles reviewed `✓` / unreviewed `○` for the selected tree row
  or displayed diff. **Space u** / `:ReviewUnreviewed` filters to unreviewed
  entries for the sidebar, Review pickers, and navigation.
- Reviewed marks are private per worktree and fingerprint changed content,
  independently for staged and unstaged entries. They survive context-only
  shifts and reopen/refresh, but are cleared when changed content changes.
  Refresh remains manual with **Space R**.

## Revdiff parity inventory

Implemented: tree/diff focus and pane switching; cross-file counted hunk and
file navigation; fuzzy file/content search; staged/unstaged/untracked groups;
hunk/file staging; reviewed marks and unreviewed filtering; protected diffs;
line/range feedback drafts; help; and explicit manual refresh.

Partial: annotations are strong native Neovim editing and exact-range review
comments, but do not yet provide revdiff's file/hunk annotation scopes or
next/previous annotation navigation. Filters cover unreviewed state, not the
broader filter set. Display and command-palette parity is selective.

Pending: worktree switching; automatic filesystem/live Review refresh; LSP
symbol inspection/definition/references; blame views; collapsed and compact
diff presentation; file/hunk-level annotations and annotation navigation; and
the remaining view toggles, filters, and general commands in the revdiff fork.

## Live harness steering

General-message drafts clear after accepted/completed delivery, including the
open `:harness send` window. Failed/pending/local-only messages and newer edits
remain intact. Review annotations are separate: annotation `:w` saves the note
locally and closes the panel without sending. `:w` from a diff pane or Git
sidebar sends only the saved batch. Accepted/completed delivery deletes the
sent notes, preserving newer notes and edits. Pending, failed, and local-only
batches remain in the current editor session. Annotations survive leaving and
re-entering Review, but a fresh editor process always starts without notes.
Submitted payloads and receipts remain in the outbox for inspection/retry.

Closing an annotation with `:q` discards edits since its last write; `:wq`
saves locally and closes. Starting another opens a fresh panel, even on the
same hunk. Saved notes remain in the batch. **d** in a source pane deletes a
note covering the cursor, with a picker if several overlap; it never deletes
source or retracts sent feedback. General-message `:q` retains its draft for
continued editing and does not block quitting, even with a hidden composer.

`:Harness install amp` installs the bundled anthrodiff bridge plugin after
confirmation, including explicit backup/replacement of the old user-local
plugin. It preserves Home Manager symlinks and does not install the Amp CLI,
reload Amp, select a session, or send feedback.

`:Harness connect amp` manually discovers live anthrodiff-plugin registrations
for this exact checkout: one live match is selected directly, otherwise a
fuzzy session picker is shown. `:Harness send` opens a normal Vim message
buffer (`:w` submits only, `:wq` submits and closes, `:q!` closes while
retaining the draft). `:Harness status`, `:Harness retry`, and
`:Harness disconnect` are supported; disconnect retains drafts and does not
stop the agent. An accepted ACK means queued steering, not a completed turn.

Typed `:harness` and `:hs` expand to `:Harness` and `:Harness send`; scripts
must use uppercase Neovim custom-command names. This live transport is separate
from `:ReviewHarness amp THREAD`, which starts an Amp CLI continuation. This
repository owns the anthrodiff plugin, installer, and tests under `nix/amp/`.
Home Manager can install it; installation never reloads Amp. The registry is
`~/.cache/anthrodiff/amp` with no legacy discovery. Feedback uses the neutral
"Review feedback" prefix. Discovery and refresh are manual, no real sends
occur merely by connecting, and credentials never belong in Nix or Git.
