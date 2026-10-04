# Portable Review editor

This directory is a self-contained flake. It packages Neovim, Git, ripgrep, difftastic,
Neo-tree, Codediff (including its compiled native library), review.nvim, and
the custom Review workspace. Nix owns dependency versions; there is no
runtime plugin manager and no first-launch plugin/library download.

## Try it without changing your configuration

Requires Nix with `nix-command` and `flakes` enabled. From the repository:

```sh
nix run path:./nix
```

Or, after these changes have been published:

```sh
nix run 'github:cmattatall/myeditor?dir=nix'
```

Run from a Git worktree to review its changes. Arguments are passed to Neovim:

```sh
nix run path:/path/to/myeditor/nix -- src/main.lua
```

Interactive launches inside a Git worktree open Review automatically, focused
on the protected new-side pane. **Space q** returns to the editing tab, retaining
any file arguments; **Space r** re-enters Review. **Space R** refreshes snapshots.
Outside Git, the filesystem tree opens on the left with focus in the editor.
**Space e** or **:ft** focuses it, **Space d** returns to the editor, and
**Space E** toggles it. Headless runs open neither Review nor the tree.
If Review is locked by another editor or cannot open, startup reports the
reason and leaves ordinary editing available. Startup never sends feedback.

**Ctrl-A / Ctrl-E** move to the start/end of the line in Insert mode (files,
annotations, and harness messages) and in command/search input. Normal-mode
Vim bindings remain unchanged. Typed **:ft** expands to `:Explorer` and focuses
the Git sidebar in Review; scripts should use `:Explorer` directly.

The package defines `myeditor`; Home Manager can also expose it as `nvim`.
Both use `NVIM_APPNAME=myeditor` and a configuration in the Nix store.
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

The root installer delegates to `nix/install-home-manager.sh`; a vendored copy
of this directory can use `bash install-home-manager.sh` with the same flags.
Nix and flakes must already be available. No separate Home Manager CLI is needed.
`standalone-home.nix` uses this flake's locked Home Manager/nixpkgs and reads
only the host system, username, and home directory through impure evaluation.
Repeat `--switch` after changing the checkout to rebuild and install updates.

Activation installs the package and its `nvim` wrapper through Home Manager.
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
inputs.myeditor.url = "github:cmattatall/myeditor?dir=nix";
```

Add its module and enable it in your Home Manager configuration:

```nix
{
  imports = [ inputs.myeditor.homeManagerModules.default ];
  programs.myeditor.enable = true;
  programs.myeditor.nvimAlias = true;
}
```

`nvimAlias` defaults to false, so existing users keep their normal `nvim` unless
they opt in. Disable any other Home Manager `nvim` package to avoid a profile
collision. If Home Manager does not manage your shell, source
`~/.nix-profile/etc/profile.d/hm-session-vars.sh` after other PATH setup so the
Nix-built wrapper takes precedence. Its stable profile path follows updates;
subsequent switches require reopening Neovim, not restarting your shell.

Here `inputs` is the inputs argument from your flake's `outputs` function;
pass it through `extraSpecialArgs` if your home module is a separate file.
Then run your usual `home-manager switch --flake ...` (or your existing
NixOS/nix-darwin rebuild command if Home Manager is integrated there).

To vendor this instead, copy this entire directory, including `flake.lock`,
into your dotfiles as `myeditor/` and use:

```nix
inputs.myeditor.url = "path:./myeditor";
```

Keep copied files tracked if the enclosing flake is a Git repository.
The module has no dependency on files outside this directory. By default
the editor uses its own locked nixpkgs rather than following your system's,
so changing your system's packages does not silently change this editor.

## Review workflow

1. Save ordinary file edits; Review reads saved files and the Git index.
2. Review opens automatically on startup inside Git. From ordinary editing,
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
   the last write and closes. The next annotation starts empty, even on the
   same hunk; previously saved comments remain in the batch.
5. **:w** from a diff pane or Git sidebar submits the saved batch;
   **:WriteFeedback** is an alias. Unsaved annotation text is never included.
   With no receiver configured, feedback is queued locally only. Use
   **d** on an annotated source line to delete its note (choose from a picker
   if several overlap), or **:ReviewComments** to list/remove notes. **q**
   retains its normal macro-recording behavior in annotations.
6. Accepted/completed delivery removes the sent notes, preserving newer notes
   and edits. Pending, failed, and local-only batches remain in this editor
   process; a fresh launch starts without annotations. Submitted payloads and
   receipts remain in **:ReviewOutbox**. Review refreshes automatically as the
   agent edits files; **Space R** refreshes immediately. **:ReviewArchive**
   archives any remaining notes.

While a Review source pane or sidebar is focused in Normal mode, saved file and
index changes are checked about once a second. Refresh preserves the displayed
file and pane focus, retaining cursor/scroll positions where possible. It pauses
while composing annotations/messages, selecting text, using commands/pickers,
or delivering feedback. Original annotation snapshots are retained, never
silently re-anchored. Refresh does not save buffers, stage files, or send feedback.
The bottom Review bar shows the branch or `@short-SHA` for detached HEAD.
Use **Space R** / **:ReviewRefresh** for these snapshot buffers, not `:bufdo e`.
Outside Review, native `:checktime` checks ordinary buffers for external changes.

The sidebar separates **STAGED** and **UNSTAGED** with colored header rows and
counts; empty sections remain visible. Untracked files are included in UNSTAGED.
Badges show `M` modified, `U` untracked, `R` Git-detected rename, `A` added, and
`D` deleted. Staging a file from the sidebar selects the next unstaged file, or
the previous one at the end. With none left, focus stays on the UNSTAGED header.
Focused tree navigation highlights a full row instead of a character cursor;
moving onto a file immediately displays its diff without leaving the tree.
Tab focuses that diff. Normal cursor styling returns when focus leaves the tree.
A partially staged file appears in both comparisons. **] / [** jump between changes,
cycling across visible files within the current STAGED or UNSTAGED group.
Untracked files belong to the UNSTAGED cycle. They work from
either source pane or the sidebar, retain focus, and accept counts (e.g.
**3]**). A gutter arrow/bar marks the selected hunk in both source panes.

Use **:fs** / **:focus staged** or **:fm** / **:focus modified** to select the
first visible file/hunk in that group without changing pane focus. If the group
is empty, selection stays unchanged. Scripts use `:Focus staged` or
`:Focus modified`; lowercase aliases expand only while in Review.

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

**Space m** toggles reviewed (`✓`) / unreviewed (`○`); **Space u**
filters the sidebar, navigation, and Review pickers to unreviewed entries.
Marks are private per worktree, separately fingerprint staged/unstaged changed
content, survive context-only shifts, and clear when that content changes.

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
| Space r / `:Review` | Enter/focus Review |
| `:view [split\|merged]` / `:View` | Select a diff layout; no argument toggles |
| Tab | Toggle tree/diff focus in Review |
| Space j / Space k | Next/previous changed file |
| `]` / `[` | Cycle hunks across files within the current Git group; retain pane focus |
| `:fs` / `:focus staged` | Select first visible STAGED file/hunk |
| `:fm` / `:focus modified` | Select first visible UNSTAGED file/hunk, including untracked |
| Space f / `:Files` | Fuzzy project files, or visible Review entries |
| Space / / `:Search` | Fuzzy saved contents, or old/new Review lines |
| Space p / `:Commands` | Search native and plugin commands |
| Space m / `:ReviewMark` | Toggle reviewed mark |
| Space u / `:ReviewUnreviewed` | Toggle unreviewed-only filter |
| i/a/o/O/I/A/c/r/R/x/p… | Edit annotation rather than source |
| d in source pane | Delete annotation covering cursor; picker if several overlap |
| Visual selection, then i or Space c | Comment on exact selection |
| Space c / `:ReviewComments` | List/remove draft comments |
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
from Codediff's highlighted ranges. New/deleted files require **S**. Renames
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
:worktree new feature-branch ../myeditor-feature
```

`list` and `switch` open a chooser showing paths and branches; `switch` also
accepts a branch or path directly. `new` asks for a branch when omitted, otherwise
uses `new branch [path]`. Its default directory is `<current-root>-<branch>` beside
the current checkout (branch slashes become hyphens). It runs `git worktree add -b`
from the current HEAD, preserving the original index and working files. It never
forces an existing path/branch, commits, or pushes.

`:harness use amp` or `:harness use claude` persists the launch type per worktree;
it does not start a process or send feedback. `new` checks that CLI is on PATH,
creates and switches to the new Review, then starts a fresh interactive `amp` or
`claude` process in a terminal tab with that worktree as its working directory.
Install/authenticate the CLI separately. Exit Terminal mode with **Ctrl-\\ Ctrl-N**,
then **gT** returns to the Review tab. Editor exit stops its terminal processes.
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

The editor is harness-independent. Built-in adapters support **Amp** and
**Claude Code**; other harnesses can implement the receiver protocol below.
Install and authenticate the chosen agent CLI separately. The Nix package
includes the adapters, not the agent CLIs or credentials.

### Steer a live Amp process

This repository owns the **anthrodiff** Amp plugin in `amp/anthrodiff.ts`,
its installer, and tests. It publishes a private live registry at
`~/.cache/anthrodiff/amp`. It has no runtime dependency on the revdiff fork
and does not discover the old revdiff registry. Feedback starts with the
tool-neutral **Review feedback** prefix and preserves the Git safety guidance.

Annotation delivery sends the note, file/line reference, comparison side and
snapshot status, with at most 1,000 selected-text characters per note. It does
not paste complete old/new files or patches into Amp. Full immutable payloads
remain in the local outbox; the message includes their archive path for optional
historical lookup. This formatting happens in the editor bridge, not the plugin,
so updating the editor is sufficient; no plugin reload is needed for this change.

For Home Manager, enable the plugin alongside the editor:

```nix
programs.myeditor = {
  enable = true;
  ampPlugin.enable = true;
};
```

`ampPlugin.enable` also defaults to true when `harness = "amp"`. The plugin
is installed at `~/.config/amp/plugins/anthrodiff.ts`; Amp itself and its
authentication remain separate. Home Manager refuses activation while a
user-local revdiff plugin exists. Disable that old plugin first, and move
aside any manually installed anthrodiff copy before giving Home Manager
ownership. Do not enable another copy in a project or global plugin scope.

Without Home Manager, install directly from the editor:

```vim
:harness install amp
```

This asks for confirmation before installing the bundled **anthrodiff plugin**
(not the Amp CLI). If the old user-local revdiff plugin exists, the confirmation
explicitly offers to back it up and replace it. Cancel is the default. No Git
repository is required. With a selected live Amp target, the confirmation also
authorizes a small request to that thread to call `reload_plugins` after a
successful installation. Amp's plugin API has no direct reload method, so this
is agent-mediated: queued does not mean reloaded. It never sends annotations or
composer text, changes the binding, or replaces the last feedback retry target.
Without a live target, reload manually once, then `:harness connect amp`.
Home Manager-owned symlinks are never overwritten; update those through Home Manager.

The same installer can be run from this repository's root:

```sh
bash nix/amp/install.sh
# To explicitly replace an existing user-local revdiff.ts instead:
bash nix/amp/install.sh --replace-revdiff
```

Replacement moves the old regular file to
`~/.config/amp/plugin-backups/revdiff.ts` without overwriting existing backups.
The installer refuses symlinks (including Home Manager-owned files). It installs
only the plugin; use Nix for the editor. Shell and Home Manager installations
still require a manual plugin reload. Reload invalidates the live connection;
use `:harness connect amp` again once Amp has registered the new endpoint.
In Amp's command palette, **anthrodiff: connect** shows connection instructions;
**anthrodiff: disconnect** stops this thread's endpoint. Session/agent start
registers silently by default.

Relaunch myeditor using the updated package, then connect in the same checkout:

```vim
:Harness install amp
:Harness connect amp
:Harness send
:Harness status
:Harness retry
:Harness disconnect
```

Connect performs manual discovery for the exact checkout. It directly chooses
the only live match, or opens a fuzzy session picker. There is no automatic
session discovery; Review files refresh independently. Connecting sends nothing.
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
retries the last immutable submission, not newly edited text, and refuses a
changed target. Live uncertain retries are allowed only on the same connection
generation; reconnecting does not make an uncertain delivery safe to resend.

For interactive typing, lowercase `:harness` and `:hs` abbreviate `:Harness`
and `:Harness send`. Plugins/scripts must use uppercase because Neovim custom
commands are uppercase. Credentials never belong in Nix or Git.

### Continue an Amp CLI thread

For Amp, set the default in Home Manager:

```nix
programs.myeditor = {
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

Without Home Manager, create `~/.config/myeditor/settings.json` (respecting
`XDG_CONFIG_HOME`):

```json
{"harness":"amp"}
```

### Custom receiver protocol

For Aider, LangGraph, or another harness, configure your own receiver:

```nix
programs.myeditor = {
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
  normally `~/.local/state/myeditor/reviews/`. Files are private (0600), with
  atomic replacement. Annotation and message drafts live only in this editor
  process; closing and reopening their views retains them, but quitting discards
  them. Reviewed marks and harness bindings persist. Drafts, snapshots and agent
  results stay out of Git.
- One editor owns a repository's draft at a time. A process lock prevents
  concurrent editors from overwriting each other's drafts.
- Review reads saved disk/index content, not unsaved normal buffers. Snapshots
  are immutable until refresh. Feedback on changed/unverifiable content is
  submitted with a warning and the original reviewed context; it is never
  silently re-anchored. Staging and marking reviewed still reject changed
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
nix build path:./nix
nix flake check path:./nix
```

From inside this directory, use `path:.` instead; format Nix files there with
`nix fmt -- *.nix`. `flake check` builds a sample
Home Manager generation without activating it, runs Neovim integration tests
against a disposable Git repository, tests the harness adapters, and exercises
the anthrodiff plugin, installer, and Python-to-plugin transport with a fake Amp thread.
It does not contact a live LLM or alter your Home Manager profile.

Update nixpkgs/Home Manager with `nix flake update --flake path:./nix` and rerun
checks. Codediff and review.nvim use explicit tested revisions in `flake.nix`;
change those URLs deliberately before refreshing the lock. The custom workspace
uses internal rendering APIs, so plugin updates require integration testing.
