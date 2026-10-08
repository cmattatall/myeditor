# rediff: read agent diffs

The public editor distribution is [cmattatall/readiff](https://github.com/cmattatall/readiff).
In this guide, "repository root" means the editor directory containing the
standalone flake. The bootstrap below is optional; Home Manager is not required.

The repository-root flake packages Neovim, Git, ripgrep, difftastic,
Neo-tree, Codediff (including its compiled native library), review.nvim, and
the custom Review workspace. Nix owns dependency versions; there is no
runtime plugin manager and no first-launch plugin/library download.
Nix modules and editor configuration live here; harness plugins live under
`plugins/<harness>/` at the repository root.

`package.nix` is the reusable editor derivation. `home-manager.nix` is an optional
module for your own home configuration. The install script's opinionated profile
lives separately in [`examples/home-manager.nix`](../examples/home-manager.nix);
it is not required to build or run the editor.

## Try it without changing your configuration

Requires Nix with `nix-command` and `flakes` enabled. From the repository:

```sh
nix run path:.
```

Or run the public package directly:

```sh
nix run github:cmattatall/readiff
```

Run from a Git worktree to review its changes. Arguments are passed to Neovim:

```sh
nix run path:/path/to/rediff -- src/main.lua
```

Interactive launches with no file arguments inside a Git worktree open Review,
focused on the tree with the first diff previewed. File arguments open directly
for editing. Outside Git, startup stays in ordinary editing without a sidebar.
**Space r** toggles Review; **Space q** also leaves it. **Space R** refreshes snapshots.
**Space e** or **:ft** opens/focuses the tree, **Space d** returns to the editor, and
**Space E** toggles it. Headless runs open neither Review nor the tree.
If Review is locked by another editor or cannot open, startup reports the
reason and leaves ordinary editing available. Startup never sends feedback.

**Ctrl-A / Ctrl-E** move to the start/end of the line in Insert mode (files,
annotations, and harness messages) and in command/search input. Normal-mode
Vim bindings remain unchanged. Typed **:ft** expands to `:Explorer` and focuses
the Git sidebar in Review; scripts should use `:Explorer` directly.

The package defines `rediff`; Home Manager can also expose it as `nvim`.
It uses `NVIM_APPNAME=rediff` and a configuration in the Nix store.
The renamed profile starts fresh: old settings, outboxes, and bindings are not
migrated or deleted. Reconfigure your harness and reconnect after upgrading.
The theme is **Rosé Pine (main)**, bundled through the pinned nixpkgs.
Existing Neovim configurations and plugins are not modified.
Targets are Apple Silicon macOS and ARM64/x86-64
Linux; a build on one architecture does not verify the other targets.
Intel macOS is not supported by the pinned unstable nixpkgs release.

## Home Manager

### Bootstrap without an existing Home Manager setup

From the repository root:

```sh
./install.sh           # Build the Home Manager generation only
./install.sh --switch  # Build and activate it; then open a new shell
command -v nvim       # Should resolve to a Nix store path, not Homebrew
```

The root installer delegates to `nix/install-home-manager.sh`, which uses the
flake and lockfile at the repository root. Both scripts accept the same flags.
Nix and flakes must already be available. No separate Home Manager CLI is needed.
`examples/home-manager.nix` uses this flake's locked Home Manager/nixpkgs and reads
only the host system, username, and home directory through impure evaluation.
Repeat `--switch` after changing the checkout to rebuild and install updates.

Activation installs rediff, its `nvim` wrapper, Amp (`amp`), and oh-my-pi (`omp`) through
Home Manager. CLI versions come from the pinned `amp-cli` and `omp`
nixpkgs packages; no curl/npm installer runs. Only `amp-cli` is allowed as an
unfree package. Log in to each CLI separately. Agent extensions are separate
opt-ins; the bootstrap profile does not install them.
The installer backs up an existing regular shell rc and appends the standard
`hm-session-vars.sh` source line once: `.zshrc` (respecting `ZDOTDIR`), `.bashrc`
on Linux, or `.bash_profile` for macOS Bash login shells. Home Manager puts
its stable profile `bin` directory ahead of Homebrew on PATH, not a
version-specific Nix store path. It does not replace
your Neovim config, install/reload Amp plugins, or send feedback.

For the first install, or when migrating from the older version-specific PATH,
refresh your existing shell once (a new terminal is not required):

```sh
unset __HM_SESS_VARS_SOURCED
. "$HOME/.nix-profile/etc/profile.d/hm-session-vars.sh"
hash -r
```

Later `--switch` updates take effect when you quit and reopen Neovim in the same
terminal. An already-running Neovim retains its original Nix build. To bypass
a stale shell PATH at any time, launch `~/.nix-profile/bin/nvim` directly.

The installer refuses unrelated Home Manager generations/configurations and
managed shell-rc symlinks. For other shells or existing Home Manager/nix-darwin
setups, use the module below instead. Do not activate this minimal standalone
configuration over a larger home configuration: Home Manager manages one profile.

### Add to an existing configuration

Add the flake to your existing flake's inputs:

```nix
inputs.rediff.url = "github:cmattatall/readiff";
```

Add its module and enable it in your Home Manager configuration:

```nix
{
  imports = [ inputs.rediff.homeManagerModules.default ];
  programs.rediff.enable = true;
  programs.rediff.nvimAlias = true;
}
```

The consuming configuration owns the Home Manager instance, username, home
directory, state version, shell, and activation. This module supplies only the
editor and its settings, plus explicitly enabled agent plugins. Do not import
`examples/home-manager.nix` or run `install.sh --switch` for this setup.
To select this editor for other commands, optionally set
`home.sessionVariables.EDITOR = "nvim"` and
`home.sessionVariables.VISUAL = "nvim"` in your own configuration.

The editor module does not install harness CLIs. Import the independent
`inputs.rediff.homeManagerModules.harnesses` module to install either or both:

```nix
programs.harnesses.amp.enable = true;
programs.harnesses.omp.enable = true;
nixpkgs.config.allowUnfreePredicate = pkg: lib.getName pkg == "amp-cli";
```

The harness module lives in `programs/harnesses.nix` and works without the editor
module. Both options default to false; `.amp.package` and `.omp.package` can
override the nixpkgs packages. It does not install plugins or manage credentials.
If Home Manager uses an externally configured `pkgs` (such as nix-darwin's
global packages), set the unfree predicate on that nixpkgs instance instead.

Use `programs.rediff` and `inputs.rediff` when upgrading an existing configuration.
Remove older `?dir=nix` or `?dir=rediff` suffixes when using the public repository;
its flake lives at the root.
`nvimAlias` defaults to false, so existing users keep their normal `nvim` unless
they opt in. With the alias enabled, disable `programs.neovim.enable` and remove
any separate `pkgs.neovim` from `home.packages` to avoid a profile collision.
Existing `~/.config/nvim` files are not overwritten or loaded by rediff.
If Home Manager does not manage your shell, source
`~/.nix-profile/etc/profile.d/hm-session-vars.sh` after other PATH setup so the
Nix-built wrapper takes precedence. Its stable profile path follows updates;
subsequent switches require reopening Neovim, not restarting your shell.

Here `inputs` is the inputs argument from your flake's `outputs` function;
pass it through `extraSpecialArgs` if your home module is a separate file.
Then run your usual `home-manager switch --flake ...` (or your existing
NixOS/nix-darwin rebuild command if Home Manager is integrated there).

To vendor this instead, copy the root `flake.nix`, `flake.lock`, `install.sh`, and
`LICENSE` alongside the complete `nix/`, `plugins/`, `programs/`, and `examples/` directories
into your dotfiles as `rediff/`, preserving their layout, and use:

```nix
inputs.rediff.url = "path:./rediff";
```

Keep copied files tracked if the enclosing flake is a Git repository.
Copy the full layout, not just `nix/`. By default
the editor uses its own locked nixpkgs rather than following your system's,
so changing your system's packages does not silently change this editor.
Set `inputs.rediff.inputs.nixpkgs.follows = "nixpkgs"` only if you want to share
your nixpkgs, including its Neovim and plugin versions. Update the editor with
`nix flake update rediff` in the consuming configuration and rebuild there.

## Review workflow

1. Save ordinary file edits; Review reads saved files and the Git index.
2. Review opens automatically inside Git when no file arguments are given. Otherwise,
   press **Space r** (`:Review`) to enter it. A separate tab contains the Git
   changes sidebar and protected old/new snapshots. The original editing tab,
   sidebar, buffers, and unsaved edits are retained.
   Focus starts on the file tree with the first diff previewed. Press **Tab**
   to focus the diff, then **[ / ]** to select a hunk and **s** to stage it.
3. Press **i**, **a**, **o**, or **O** on code to compose a comment. Use
   **v**, **V**, or **Ctrl-v**, then **i**, for character/line/block feedback.
4. Edit the annotation with normal Vim bindings, including operators, counts,
   registers, macros, and undo/redo. **Esc**, then **:w** saves the note locally
   and closes the annotation. **:wq** does the same. Neither sends
   feedback or writes the source file. **:q** (or **:q!**) discards edits since
   the last write and closes. **i** on an annotated line edits the existing note;
   **:annotations new** starts a separate note on the same line.
5. **:w** from a diff pane or Git sidebar submits the saved batch;
   **:WriteFeedback** is an alias. Unsaved annotation text is never included.
   With no receiver configured, feedback is queued locally only. Use
   **d** on an annotated source line to delete its note (choose from a picker
   if several overlap). Notes use a small orange icon and orange text below
   the source range, without a label or border. **@**, **:annotations list** or **:al** opens a
   fuzzy picker over filenames and note text; Enter jumps to the note, then
   **i** edits it. **} / {** or **:annotations next** / **prev** cycle through
   saved notes from tree/diff panes; counts work (**3}**). Ordinary files and
   annotation text keep native paragraph motions.
   Jumps show the original snapshot; **Space R** returns to current files.
   **:ReviewComments** also opens the picker. **q**
   retains its normal macro-recording behavior in annotations.
6. Accepted/completed delivery removes the sent notes, preserving newer notes
   and edits. Pending, failed, and local-only batches remain in this editor
   process; a fresh launch starts without annotations. Submitted payloads and
   receipts remain in **:ReviewOutbox**. Review refreshes automatically as the
   agent edits files; **Space R** refreshes immediately. **:ReviewArchive**
   archives any remaining notes.

Connected Amp tool-result hooks trigger Review refreshes after reported file edits
in this worktree. Git determines which hunks changed since the last accepted send.
Polling every three seconds catches other edits and index changes. Refresh runs
with Normal-mode tree, diff, or harness-message focus and keeps the selected
file, pane focus, and cursor/scroll positions where possible. It pauses for
annotation editing, typing, selections, commands/pickers, and feedback delivery.
Typing in the harness send pane is allowed without interrupting the draft.
Annotation snapshots stay unchanged. Refresh never saves buffers, stages files,
or sends feedback.
The bottom Review bar shows the branch or `@short-SHA` for detached HEAD.
Use **Space R** / **:ReviewRefresh** for these snapshot buffers, not `:bufdo e`.
Outside Review, native `:checktime` checks ordinary buffers for external changes.

Configure fallback polling in Home Manager (seconds, `0` disables polling):

```nix
programs.rediff.reviewRefreshInterval = 10;
```

Without Home Manager, merge `"review_refresh_interval": 10` into
`~/.config/rediff/settings.json` (respecting `XDG_CONFIG_HOME`). The value must be
a non-negative whole number; the default is `3`. Apply Home Manager changes or
save the unmanaged JSON file, then leave and re-enter Review to use the new value.
Amp file events and **Space R** / **:ReviewRefresh** still work with polling disabled.

The sidebar separates **STAGED** and **UNSTAGED** with colored header rows and
counts; empty sections remain visible. Untracked files are included in UNSTAGED.
Badges show `M` modified, `U` untracked, `R` Git-detected rename, `A` added, and
`D` deleted. Staging/unstaging from the sidebar selects the next file in the
same group, or the previous one at the end. With none left, focus stays on that
group's header, including after refresh; further **S** presses change nothing.
Focused tree navigation highlights a full row instead of a character cursor;
**h/Left** collapses a group (**▸**); **l/Right** or **Enter** expands it (**▾**)
and restores its selected file. **Enter** never collapses or leaves the tree.
**j/k** or **Up/Down** visits visible files and collapsed
or empty group headers, skipping blank rows. The selected header is highlighted.
Refresh preserves collapsed groups; **:fs/:fu**
and hunk navigation reveal their target file. Moving onto a file immediately
displays its diff without leaving the tree.
Tab focuses that diff. Normal cursor styling returns when focus leaves the tree.
A partially staged file appears in both comparisons. **] / [** jump between changes,
cycling across visible files within the current STAGED or UNSTAGED group.
Untracked files belong to the UNSTAGED cycle. They work from
either source pane or the sidebar, retain focus, and accept counts (e.g.
**3]**). A gutter arrow/bar marks the selected hunk in both source panes.

Use **:fs** / **:focus staged** or **:fu** / **:focus unstaged** to select the
first visible file/hunk in that group without changing pane focus. If the group
is empty, selection stays unchanged. Scripts use `:Focus staged` or
`:Focus unstaged`; lowercase aliases expand only while in Review.

Use **:view split** for side-by-side panes, **:view merged** for a unified view,
or **:view** to toggle. The selected file/hunk and saved notes survive switching.
Merged deletions are virtual display lines: use split for exact old-side
selections or annotations. Scripts use `:View`; native lowercase `:view` remains
unchanged outside Review.

Additions are green and deletions red, with stronger token highlights from the
Nix-pinned **difftastic** engine. Codediff retains text alignment/navigation and
Git retains staging boundaries. Unsupported languages use difftastic's text
comparison; missing/failed tools, a two-second timeout, or files over 1 MB retain
the ordinary text renderer. The bottom bar shows `difftastic` or `text`.

**?** in Normal mode (or **:help**) opens a centered overlay from editing,
either tree, Review, annotations, or harness messages. Press **?**, **q**, or
**Esc** to dismiss it without changing the underlying splits. Insert-mode `?`
still types normally; backward search is available via `:?pattern` or `/` then
`N`. The guide includes navigation, annotations, harnesses, Git actions, view
controls, a complete editor-command index, and revdiff equivalents/gaps.
Normal help navigation and topics such as `:help motion` still work.

Command names show dim inline suggestions (`:h` → `help`, `:ha` → `harness`).
Tab accepts the hint; Enter executes only typed/accepted text. Harness
subcommands also have hints; other arguments keep native Tab completion.

| Key/command | Action |
| --- | --- |
| Space e / `:ft` / `:Explorer` | Focus Git sidebar in Review; focus Neo-tree while editing |
| Space d / `:FocusDiff` | Focus diff in Review; focus editor while editing |
| Space E | Toggle ordinary Neo-tree while editing; focus-only in Review |
| Space r | Toggle Review |
| `:Review` | Enter/focus Review |
| `:view [split\|merged]` / `:View` | Select a diff layout; no argument toggles |
| Tab | Toggle tree/diff focus in Review |
| Space j / Space k | Next/previous changed file |
| `]` / `[` | Cycle hunks across files within the current Git group; retain pane focus |
| `:fs` / `:focus staged` | Select first visible STAGED file/hunk |
| `:fu` / `:focus unstaged` | Select first visible UNSTAGED file/hunk, including untracked |
| Space f / `:Files` | Fuzzy project files, or visible Review entries |
| Space / / `:Search` | Fuzzy saved contents, or old/new Review lines |
| Space p / `:Commands` | Search native and plugin commands |
| i/a/o/O/I/A/c/r/R/x/p… | Edit annotation rather than source |
| d in source pane | Delete annotation covering cursor; picker if several overlap |
| Visual selection, then a (also i or Space c) | Comment on the entire character/line/block selection |
| @ / `:annotations list` / `:al` | Fuzzy-search annotations; Enter jumps, i edits |
| } / { or `:annotations next` / `prev` | Next/previous saved annotation; accepts counts |
| `:annotations new` | Create a separate note at the cursor |
| s in a source pane | Stage/unstage the Git hunk containing the cursor |
| S in sidebar or source pane | Stage/unstage selected file or entire displayed diff |
| `:ReviewStage hunk` / `:ReviewStage file` | Explicitly stage; rejects STAGED entries |
| `:ReviewUnstage hunk` / `:ReviewUnstage file` | Explicitly unstage; requires STAGED entry |
| Space R / `:ReviewRefresh` | Refresh snapshots and changed-file list |
| `:w` in diff/sidebar / `:WriteFeedback` | Queue/send saved batch; never stage or save source |
| `:w` / `:wq` in annotation | Save note locally and close annotation |
| `:q` / `:q!` in annotation | Discard edits since last write and close |
| `?` / `:help` | Help overlay; ?/q/Esc closes it |
| `:ReviewHarness [name session]` | Show or change this repository's harness binding |
| `:ReviewRetry` | Explicit retry after checking an uncertain/failed delivery |
| `:ReviewOutbox` | Browse persisted feedback and delivery receipts |
| `:ReviewArchive` | Archive all drafts locally and begin a new round |
| Space q / `:ReviewLeave` (also q in sidebar) | Return to editing, retaining drafts |

Git hunks include Git's context lines and can group nearby edits differently
from Codediff's highlighted ranges. An all-green new file is one hunk, so **s**
stages it and advances to the next unstaged hunk. Empty/deleted files require **S**. Renames
are shown as deletion/addition pairs. Stage before commenting if possible:
staging changes the index comparison and can make existing anchors stale.

**Option+Left/Up/h/k** moves backward by a word; **Option+Right/Down/l/j** moves
forward in Normal, Visual, Insert, and command/search input, including annotations
and harness messages. Configure the terminal to send Option as Alt/Meta rather
than special characters; Esc-b/Esc-f word-key sequences are also supported.
Bare **s/S** retains native substitution inside annotations and ordinary files.

## Worktrees and new harness sessions

```vim
:harness use amp
:worktree list
:worktree switch
:worktree switch feature-branch
:worktree new
:worktree new feature-branch ../rediff-feature
:worktree delete feature-branch
```

`list` and `switch` open a pane showing paths and branches. Use **j/k** to select,
**Enter** to switch, **n** to create a worktree and launch its harness, **dd** to
confirm deletion, **R** to refresh, and **q/Esc** to close. The current checkout
is marked `*`. `switch` also accepts a branch or path directly.
`new` asks for a branch when omitted, otherwise
uses `new branch [path]`. Its default directory is `<current-root>-<branch>` beside
the current checkout (branch slashes become hyphens). It runs `git worktree add -b`
from the current HEAD, preserving the original index and working files. It never
forces an existing path/branch, commits, or pushes.

`delete branch|path` removes a linked worktree and stops its terminal harness jobs
launched by this Neovim instance. It keeps the Git branch and saved review metadata.
It refuses the main checkout, locked/unavailable worktrees, live reviews in another
editor, unsaved file buffers, and staged/unstaged/untracked changes. Git removal is
never forced; ignored files follow Git's normal removal behavior. Deleting the
current checkout switches to the main checkout first. Separately started or merely
connected harnesses are not killed. If Git refuses removal after a harness stops
(for example, because it wrote a final change), the worktree is retained.

`:harness use amp` or `:harness use claude` persists the launch type per worktree;
it does not start a process or send feedback. `use amp` also discovers live
sessions in the current checkout, connecting to one match or opening a picker
for several. No matches or cancelling keeps any compatible binding. Claude has
no live discovery adapter; use `:ReviewHarness claude SESSION_ID` for feedback.
`new` checks that CLI is on PATH,
creates and switches to the new Review, then starts a fresh interactive `amp` or
`claude` process in a bottom terminal split with that worktree as its working directory.
Install/authenticate the CLI separately. Exit Terminal mode with **Ctrl-\\ Ctrl-N**,
then **:hide** hides the pane without stopping the process. **Ctrl-W Ctrl-W**
returns directly to the previous editor window, leaving the harness running.
**:harness open** / **:ho** reopens a terminal and offers a chooser when multiple
sessions are connected or running in the editor. Selecting another worktree's
TUI does not change the review's annotation recipient. With no available/selected
session, it starts the selected CLI in the current worktree.
For an external Amp/Claude session, opening it (or **:harness resume**) asks before
starting a new TUI for that session; stop using its other terminal first. This is not process
attachment. OMP resumption is unsupported; live feedback may need reconnecting.
Editor exit stops its terminal processes.
If a launch fails, the new worktree is retained and an error is reported.

New worktrees inherit only the selected harness type, not a thread ID, feedback
connection, or drafts. In the new Review, use `:harness connect amp` to select its
live Amp session; for Claude, use `:ReviewHarness claude SESSION_ID`. Changing
the selected type clears an incompatible feedback binding; `:harness status`
shows both. Switching worktrees does not spawn additional agents. Dirty editing
buffers and saved notes remain with their original checkout; save or close an
open annotation before switching. Worktrees owned by another editor are refused.
Scripts use uppercase `:Worktree` / `:Harness`, not the typed abbreviations.

## Connect an agent: Amp, Claude Code, or a custom harness

The editor is harness-independent. Built-in adapters support **Amp**, **oh-my-pi**,
and **Claude Code**; other harnesses can implement the receiver protocol below.
Install and authenticate the chosen agent CLI separately. The Nix package
includes the adapters, not the agent CLIs or credentials.

### Steer a live Amp process

This repository owns the **readiff** Amp plugin in `plugins/amp/readiff.ts`,
its installer, and tests. It publishes a private live registry at
`~/.cache/rediff/amp`. It has no runtime dependency on the revdiff fork
and does not discover older registries. The plugin forwards the editor's
content unchanged, without adding instructions.

Built-in Amp and Claude delivery uses JSON: `rules` first, `repository` and
`snapshot_archive` context next, then `annotations`. General messages use
`rules`, `repository`, and `message`, without review context. Rules appear once;
there is no prose prefix or epilogue. Ask-first defaults explicitly permit actions
the user authorizes in the feedback, including commits, pushes, and pull requests.
Annotations retain absolute file paths, old/new side, line ranges, Git group,
snapshot identity/status, and exact selection spans. Stale/unverified, old-side,
and character/block annotations also carry `selected_text`: up to five lines
and 400 Unicode characters from the saved selection. `selected_text_truncated`
is true when either limit omits text; the exact range/spans remain unchanged.
Current new-side line annotations omit excerpts. Full files, patches, and complete
selections stay in the local outbox, referenced by archive path. That path is
local to rediff, not automatically accessible to a remote agent; the rules ask
for needed historical context if it cannot be read. Custom receivers still get
the full original payload.

To upgrade from the old prose format, update the editor and bundled Amp plugin,
reload Amp's plugins, then reconnect. Older plugins still prepend the old guidance.

For Home Manager, enable the plugin alongside the editor:

```nix
programs.rediff = {
  enable = true;
  ampPlugin.enable = true;
};
```

`ampPlugin.enable` also defaults to true when `harness = "amp"`. The plugin
is installed at `~/.config/amp/plugins/readiff.ts`; Amp itself and its
authentication remain separate. Other plugin files are left untouched.
Do not enable another copy in a project or global plugin scope.

Without Home Manager, install directly from the editor:

```vim
:harness install amp
```

This asks for confirmation before installing the bundled **readiff plugin**
(not the Amp CLI). Cancel is the default. No Git
repository is required. With a selected live Amp target, the confirmation also
authorizes a small request to that thread to call `reload_plugins` after a
successful installation. Amp's plugin API has no direct reload method, so this
is agent-mediated: queued does not mean reloaded. It never sends annotations or
composer text, changes the binding, or replaces the last feedback retry target.
Without a live target, reload manually once, then `:harness connect amp`.
Home Manager-owned symlinks are never overwritten; update those through Home Manager.

The same installer can be run from this repository's root:

```sh
bash plugins/amp/install.sh
```

The installer writes `readiff.ts` and moves a legacy `rediff.ts` to a unique backup
under `~/.config/amp/plugin-backups/`, outside Amp's active plugin directory.
It refuses symlinks (including Home Manager-owned files) or non-files at either
name. Update Home Manager-managed plugins through Home Manager instead; it removes
the old managed filename when switching to the new one. The installer installs
only the plugin; use Nix for the editor. Shell and Home Manager installations
still require a manual plugin reload. Reload invalidates the live connection;
use `:harness connect amp` again once Amp has registered the new endpoint.
In Amp's command palette, **readiff: connect** shows connection instructions;
**readiff: disconnect** stops this thread's endpoint. Session/agent start
registers silently by default.

Relaunch rediff using the updated package, then connect in the same checkout:

```vim
:Harness install amp
:Harness connect amp
:Harness send
:Harness status
:Harness retry
:Harness disconnect
```

`connect amp` and `list` open the same searchable panel across worktrees. `/`
filters aliases, titles, providers, IDs, directories, and activity. Enter toggles
the selected harness's connection without stopping its agent or discarding drafts;
`r` assigns a local alias, `d` disconnects, `c` shows connected sessions, and `R`
rediscovers. `use amp` selects the launch type and connects to the only live match
in this checkout, or opens the panel for multiple matches. While open, the panel
discovers sessions every second without overlapping probes or restarting healthy
activity streams. Closing it stops polling. Connecting sends nothing and does
not start an agent.

Amp can run in another terminal window, emulator, or tmux session on the same
machine and user account. Each Amp instance must load the readiff plugin; discovery
uses `~/.cache/rediff/amp`, not terminal environment variables. Remote machines
and orbs do not share this local registry or loopback connection.

Multiple harnesses can remain connected. `:Harness send` asks which recipient
when there is more than one, or `:Harness send ALIAS` selects directly. Drafts
are separate per sender worktree and recipient. Selecting a local harness also
sets the worktree's annotation target; selecting an external harness never does.
Annotations cannot be sent across worktrees. General messages include sender
directory, editor instance/PID, and recipient identity. External recipients get
an explicit rule not to edit the sender's worktree. This is agent policy, not a
filesystem sandbox: an existing agent retains its own filesystem permissions.

The panel receives authenticated Amp activity snapshots asynchronously: running,
idle, awaiting approval, error, and active tool name, without tool inputs/outputs.
A running agent has a spinner. Multiple editor instances can subscribe to the
same agent. Lost connections show offline; panel polling or `R` reconnects their
streams when available, without resending feedback. Providers without live activity show unknown.
Aliases persist locally; live subscriptions end when the editor exits.

On startup, a saved live Amp binding is checked asynchronously against the live
registry. The same thread in the same worktree can acquire a new endpoint after
a plugin reload. If it is gone, a single live session in the current worktree
connects automatically. With multiple matches (or none), `:harness send` opens
the connection picker. Sessions in other worktrees are never selected as an
automatic fallback. Failed feedback is never resent by reconnecting.

`:Harness send` opens a general-message `acwrite` buffer with normal Vim editing: `:w` submits and
stays open, `:wq` submits and closes, and `:q` (or `:q!`) closes while retaining
the draft locally without sending. Hidden message drafts do not block quitting;
ordinary unsaved files remain protected. An `accepted` ACK means the steering
message was queued, not that the agent completed a turn. Disconnect retains
local drafts in this editor process and does not stop the agent. Every fresh
editor starts with an empty composer, even if an older version saved a draft.

Accepted/completed messages clear from the open composer and its session draft,
so the next `:harness send` opens empty. Pending, failed/uncertain, and local-only
queued messages are retained only until the editor exits; submitted payloads
and receipts remain in the outbox for retry. A late acknowledgment never clears
different text typed while sending. An empty `:w` sends nothing, and `:wq` can close a cleared
composer. **General-message sends do not clear review annotations**: `:w` in a
Review diff pane or sidebar sends the saved annotation batch, and its successful
acknowledgment clears only those sent notes.

The bottom status bar shows the harness, session suffix, and delivery state in
both Editing and Review. Use `:harness status` for the full session ID and last
receipt status. This shows the selected binding and feedback delivery, not a
live agent health check. `local` means feedback stays in the local outbox.

Message writes never include pending review comments. Repeated writes of the
same text to the same target reuse the existing submission. `:Harness retry`
retries the current composer's last immutable submission (or the worktree's last
annotation submission outside a composer), not newly edited text, and refuses a
changed target. Live uncertain retries are allowed only on the same connection
generation; reconnecting does not make an uncertain delivery safe to resend.

For interactive typing, lowercase `:harness` and `:hs` abbreviate `:Harness`
and `:Harness send`. Plugins/scripts must use uppercase because Neovim custom
commands are uppercase. Credentials never belong in Nix or Git.

### Steer a live oh-my-pi process

The `omp` provider targets **oh-my-pi 18.4.4**, the version in the pinned nixpkgs,
not upstream pi. Import the harness module to install its CLI independently:

```nix
programs.harnesses.omp.enable = true;
programs.rediff.ompPlugin.enable = true; # Requires the editor module; optional
```

The editor option manages `~/.omp/agent/extensions/rediff.ts` for the default
profile. It also defaults to true with `programs.rediff.harness = "omp"`.
Without Home Manager, `:harness install omp` confirms installation of only the
bundled extension. That installer honors OMP's `OMP_PROFILE`/`PI_PROFILE`,
`PI_CONFIG_DIR`, and `PI_CODING_AGENT_DIR` settings inherited by the editor;
it refuses Home Manager-owned symlinks. Neither method starts or authenticates OMP.

Start `omp`, or run **`/restart`** in an existing OMP session to load the extension.
Then run `:harness connect omp` in rediff. `:harness use omp` also selects OMP for
new worktrees. `:hl` shows both Amp and OMP sessions; its search, aliases, activity,
disconnect, and one-second discovery refresh work for both providers.

Feedback goes to the live session without launching another agent process.
`accepted` means admitted: an idle agent starts a turn, a running agent receives
steering, and any later model error is reported by OMP. It does not mean the turn
finished. Tool activity invalidates changed files so Review can refresh unseen
markers; shell/subagent tools invalidate conservatively, with Git checking the diff.

Session changes (`/new`, `/resume`, `/fork`, `/branch`) replace the connection;
select the new session in rediff. If another extension cancels a session switch,
run `/rediff-connect` in OMP to resume delivery. `/rediff-disconnect` stops the
bridge without stopping OMP. Connections use a private loopback registry under
`~/.cache/rediff/omp`; they work across terminals on the same machine/user.
Annotations remain local to their worktree, including with mixed Amp/OMP connections.

### Continue an Amp CLI thread

For Amp, set the default in Home Manager:

```nix
programs.rediff = {
  enable = true;
  harness = "amp";
};
```

In Review, bind an existing thread belonging to this local checkout:

```vim
:ReviewHarness amp T-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

Replace the placeholder with the thread's actual ID. The binding is persisted
privately per repository/worktree, outside your Nix configuration and Git.
`:ReviewHarness` displays it; selecting it sends nothing. `:w` sends feedback
through `amp threads continue THREAD_ID --execute --stream-json --no-ide`,
with the prompt on stdin. It requires a successful final result for the same
thread before acknowledging completion. See Amp's
[streaming JSON documentation](https://ampcode.com/docs/cli/streaming-json).

This adapter targets **local CLI threads in the reviewed checkout**, and is
not the live `:Harness connect amp` transport above. Orb and
remote runner workspaces are not synchronized by this editor. Do not continue
a thread that is currently being driven by another agent process. Pause/stop
that process first; this integration does not inject feedback into its TUI.

Switch harnesses without rebuilding:

```vim
:ReviewHarness claude YOUR-CLAUDE-SESSION-ID
:ReviewHarness none
```

Claude uses `claude --print --resume SESSION_ID --output-format json`. `none`
returns to the local outbox. With `harness = "amp"` or `"claude"`, submitting
before selecting a session fails rather than guessing the most recent session.
With the default `harness = "none"`, feedback is queued locally only.

Adapters do not pause agents or bypass their permission configuration. Keep
the editor running until delivery completes. `completed` means the agent turn
finished, not that every comment was resolved; review its result and any
`permission_denials` in the outbox receipt. Agent credentials never belong in
Nix configuration, command arguments, or this repository.

Without Home Manager, create `~/.config/rediff/settings.json` (respecting
`XDG_CONFIG_HOME`):

```json
{"harness":"amp"}
```

### Custom receiver protocol

For Aider, LangGraph, or another harness, configure your own receiver:

```nix
programs.rediff = {
  enable = true;
  harness = "custom";
  feedbackCommand = [ "/absolute/path/to/receiver" "optional-argument" ];
};
```

If the repository already has a binding, select `:ReviewHarness custom`.
`feedbackCommand` is an argv list, not a shell command. The editor appends one
absolute submission JSON path and launches it asynchronously in the repository.
Without Home Manager, use `{"harness":"custom","feedback_command":["/path/to/receiver"]}`.

Every payload contains `protocol_version`, `submission_id`, `repository`, and
`target`. Review payloads contain `comments` and `snapshots`; general-message
payloads contain `message` instead. Each comment includes old/new side,
file, line range, snapshot ID, exact selected text, and selection spans.
Snapshots include both file versions and the Git patch. `snapshot_status`
maps snapshot IDs to `current`, `changed`, or `unverified`, as observed when
the submission was first created. It does not change the batch identity;
retries preserve the original payload. Agents must check current files before
applying feedback; line ranges refer to the supplied snapshots. Nothing uploads until
you configure a receiver and submit; payloads can contain sensitive source.

Selections use `coordinates = "nvim-getregionpos-v1"`: 1-based line and byte
columns, with a span per source line. `start_offset` counts display cells into
a tab/wide character; nonzero `end_offset` is its first excluded cell. When
`end_offset` is zero, the end character is included. This preserves Visual
block geometry instead of pretending it is a contiguous line range.

On success, a receiver exits zero and prints exactly one JSON acknowledgment:

```json
{"submission_id":"the-received-id","status":"completed","result":"What changed and what was tested"}
```

`status = "accepted"` is also supported for receivers that durably queue work.
Other output, a mismatched ID, or nonzero exit means failed/uncertain delivery.
Receivers must deduplicate `submission_id`. Repeated writes of an unchanged
batch to the same target reuse its ID; completed/accepted batches are not resent.
Changing the receiver/session deliberately creates a different submission.

Both built-in adapters cache completed receipts. After a crash or agent error
they refuse automatic replay. Inspect the named session and receipt before
removing an uncertain `.amp-receipt.json` or `.claude-receipt.json` file and
explicitly retrying. Changing targets deliberately allows the same feedback
to be sent to another agent; verify the binding before submitting.

## Persistence and boundaries

- Outbox payloads/receipts live under `stdpath("state")/reviews/<repository-hash>/`,
  normally `~/.local/state/rediff/reviews/`. Files are private (0600), with
  atomic replacement. Annotation and message drafts live only in this editor
  process; closing and reopening their views retains them, but quitting discards
  them. Reviewed marks and harness bindings persist. Drafts, snapshots and agent
  results stay out of Git.
- One editor owns a repository's draft at a time. A process lock prevents
  concurrent editors from overwriting each other's drafts.
- Review reads saved disk/index content, not unsaved normal buffers. Snapshots
  are immutable until refresh. Feedback on changed/unverifiable content is
  submitted with a warning and the original reviewed context; it is never
  silently re-anchored. Staging still rejects changed
  content. Drafts remain available through `:ReviewComments` and the outbox.
- Regular text files up to 2 MiB are supported. Merge conflicts, binary files,
  and working-tree symlinks/submodules are not an editing/review target here.
- Alternate/range/append/filter writes from review buffers are rejected.
  Ordinary editing buffers retain normal Neovim write behavior. This is not
  a sandbox against deliberate Lua execution or `:noautocmd` commands.
- This first implementation reviews worktree/index changes side by side.
  Automatic agent pausing, unapplied proposals, automatic cross-round anchor
  remapping, and a native Neovim core mode enum are not implemented.

## Development and updates

```sh
nix build path:.
nix flake check path:.
```

Run these commands from the repository root; from `nix/`, use `path:..` instead.
Format Nix files from the root with `nix fmt -- flake.nix nix/*.nix`.
`flake check` builds a sample
Home Manager generation without activating it, runs Neovim integration tests
against a disposable Git repository, tests the harness adapters, and exercises
the readiff plugin, installer, and Python-to-plugin transport with a fake Amp thread.
It does not contact a live LLM or alter your Home Manager profile.

Update nixpkgs/Home Manager with `nix flake update --flake path:.` and rerun
checks. Codediff and review.nvim use explicit tested revisions in `flake.nix`;
change those URLs deliberately before refreshing the lock. The custom workspace
uses internal rendering APIs, so plugin updates require integration testing.
