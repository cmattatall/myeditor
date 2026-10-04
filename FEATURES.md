# Feature list

myeditor is a portable, Nix-packaged Neovim configuration with ordinary
editing and a protected, side-by-side Git Review workspace. Review supports
exact-range comments, local drafts/outbox, explicit staging, and Amp, Claude,
or custom feedback receivers. It intentionally does not claim complete
revdiff parity.

## Navigation and review progress

- Interactive launches inside Git open Review automatically; **Space q** returns
  to ordinary editing. Outside Git, startup opens the filesystem tree instead.
  Review starts focused on the file tree, with the first diff previewed;
  **Tab** moves into the diff for hunk navigation and staging.
  Headless runs open neither. Startup does not send feedback or change the index.
- **:view split** / **:view merged** selects side-by-side or unified presentation;
  **:view** toggles. Merged deletions are display-only; use split to select old text.
  Difftastic supplies syntax-aware token highlights, with green additions and red
  deletions. Native text alignment and Git staging stay independent; tool failures
  or oversized files retain ordinary text highlights.
- The sidebar has **STAGED** and **UNSTAGED** sections. Untracked files appear in
  UNSTAGED with `U`; `M`, `R`, `A`, and `D` identify modified, Git-detected renamed,
  added, and deleted paths. Staging a sidebar file advances to the next unstaged
  entry, or the previous one at the end; an exhausted section keeps header focus.
  Moving onto a file row immediately displays its diff while retaining tree focus.
  The full-row highlight follows that file; normal cursor styling returns on exit.
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
- **s** in a Review source pane toggles staging for the current hunk; **S** toggles
  staging for the entire file from either the sidebar or a source pane.
  Staging a hunk advances to the next remaining UNSTAGED hunk, wrapping across
  files. When none remain, focus rests on the UNSTAGED header, not STAGED.
  Inside annotations and ordinary files, native `s`/`S` editing is unchanged.
- **]** / **[** cycle through hunks across visible files within the current
  STAGED or UNSTAGED group, including untracked files, and accept counts.
  **:fs** / **:focus staged** selects the first visible staged file;
  **:fm** / **:focus modified** selects the first visible unstaged file.
  Both preserve pane focus; an empty group leaves the selection unchanged.
  **Space j/k** select the next/previous changed file across groups.
- **Space f** / `:Files` fuzzy-finds nonignored project files while editing;
  in Review it finds visible changed entries, keeping staged and unstaged
  instances distinct. **Space /** / `:Search` searches saved project contents
  while editing and old plus new lines (including deletions) in Review.
  **Enter** chooses and **Esc** cancels. Native `/`, `n`, and `N` remain
  current-buffer search.
- **Space p** / `:Commands` fuzzy-searches native and plugin commands in Editing
  and Review. `?` opens help; `:help myeditor-commands` lists editor commands
  with their arguments and meanings.
- **Space m** / `:ReviewMark` toggles reviewed `✓` / unreviewed `○` for the selected tree row
  or displayed diff. **Space u** / `:ReviewUnreviewed` filters to unreviewed
  entries for the sidebar, Review pickers, and navigation.
- Reviewed marks are private per worktree and fingerprint changed content,
  independently for staged and unstaged entries. They survive context-only
  shifts and reopen/refresh, but are cleared when changed content changes.
  Review checks saved files and the index about once a second, preserving file
  selection and pane focus. Refresh pauses during composition, selections,
  command input, pickers, and delivery. **Space R** also refreshes immediately.
- The bottom Review bar shows the current branch, or `@short-SHA` for detached
  HEAD. It updates with manual/live refresh and worktree switching.
- **:worktree list** / **:worktree switch** opens a worktree chooser;
  **:worktree switch branch-or-path** switches directly. **:worktree new** asks
  for a new branch, creates a sibling checkout, and starts the selected harness
  in a terminal tab there. **:harness use amp** / **:harness use claude** persists
  the launch type per worktree. New worktrees inherit that type, not a feedback
  connection or drafts. Existing dirty buffers and saved annotations stay with
  their original checkout. The CLI must already be installed/authenticated.

## Revdiff parity inventory

Implemented: tree/diff focus and pane switching; cross-file counted hunk and
file navigation; fuzzy file/content search; staged/unstaged groups and status badges;
hunk/file staging; reviewed marks and unreviewed filtering; protected diffs;
line/range feedback drafts; help; live/manual refresh; and worktree switching
and creation with a selected harness process.

Partial: annotations are strong native Neovim editing and exact-range review
comments, but do not yet provide revdiff's file/hunk annotation scopes or
next/previous annotation navigation. Filters cover unreviewed state, not the
broader filter set. Display and command-palette parity is selective.

Pending: LSP symbol inspection/definition/references; blame views; collapsed
and compact diff presentation; file/hunk-level annotations and annotation navigation; and
the remaining view toggles, filters, and general commands in the revdiff fork.

## Live harness steering

Live Amp feedback sends annotations, file/line references, snapshot status, and
at most 1,000 selected-text characters per note. Full old/new files and patches
stay in the local outbox, referenced by archive path rather than pasted into Amp.

General-message drafts clear after accepted/completed delivery, including the
open `:harness send` window. Success updates the status bar quietly, without a
receipt-path notification or Enter prompt. Failed/pending/local-only messages
and newer edits remain intact within this editor process only. A fresh editor opens an
empty composer, including when an older version saved draft text to disk.
Review annotations are separate: annotation `:w` saves the note
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
plugin. With a selected live Amp target, it then asks that thread to call
`reload_plugins`; queued acknowledgment is not proof the reload completed.
Without a live target, reload manually once and use `:harness connect amp`.
It preserves Home Manager symlinks, does not install the Amp CLI, and never
sends annotations or composer text as part of installation.

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
Home Manager can install it; Home Manager and shell installs require a manual
reload. The registry is
`~/.cache/anthrodiff/amp` with no legacy discovery. Feedback uses the neutral
"Review feedback" prefix. Harness discovery is manual, no real sends
occur merely by connecting, and credentials never belong in Nix or Git.
