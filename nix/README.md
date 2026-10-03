# Portable Review editor

This directory is a self-contained flake. It packages Neovim, Git, ripgrep,
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

The filesystem tree opens on the left at startup, with focus in the editing
pane. **Space e** focuses it, **Space d** returns to the editor, and **Space E**
toggles it. Headless runs do not open the tree.

The package defines `myeditor`, not `nvim`. It uses `NVIM_APPNAME=myeditor`
and a configuration in the Nix store. Existing Neovim configurations and
plugins are not modified. Targets are Apple Silicon macOS and ARM64/x86-64
Linux; a build on one architecture does not verify the other targets.
Intel macOS is not supported by the pinned unstable nixpkgs release.

## Home Manager

Add the flake to your existing flake's inputs:

```nix
inputs.myeditor.url = "github:cmattatall/myeditor?dir=nix";
```

Add its module and enable it in your Home Manager configuration:

```nix
{
  imports = [ inputs.myeditor.homeManagerModules.default ];
  programs.myeditor.enable = true;
}
```

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

1. Save your ordinary file edits and pause any agent writing to this worktree.
2. Press **Space r** (`:Review`). A new tab replaces the editing layout with
   a Git changes sidebar and protected old/new snapshot panes. Your original
   tab, sidebar, buffers, and unsaved edits are retained.
3. Press **i**, **a**, **o**, or **O** on code to compose a comment. Use
   **v**, **V**, or **Ctrl-v**, then **i**, for character/line/block feedback.
4. Edit the annotation with normal Vim bindings, including operators, counts,
   registers, macros, and undo/redo. **Esc**, then **:w** submits the current
   batch and keeps the annotation open. **:wq** submits and closes it. Neither
   writes the reviewed source file. **:q** (or **:q!**) collects the annotation
   locally and closes it without sending. The next annotation starts empty,
   even on the same hunk; previously collected comments remain in the batch.
5. **:w** also submits from the diff panes; **:WriteFeedback** is an alias.
   With no receiver configured, feedback is queued locally only. Use
   **:ReviewComments** to list/remove collected notes. **q** retains its normal
   macro-recording behavior in annotations.
6. After reviewing an agent's response, use **:ReviewArchive** to archive the
   round and start empty, then **Space R** to refresh the changes.

The sidebar separates **STAGED**, **UNSTAGED**, and **UNTRACKED** with colored
header rows, counts, and spacing; empty sections remain visible. A partially
staged file appears in both comparisons. **] / [** jump between changes,
crossing files and groups in sidebar order without wrapping. They work from
either source pane or the sidebar, retain focus, and accept counts (e.g.
**3]**). A gutter arrow/bar marks the selected hunk in both source panes.

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
| Space e / `:Explorer` | Focus Git sidebar in Review; focus Neo-tree while editing |
| Space d / `:FocusDiff` | Focus diff in Review; focus editor while editing |
| Space E | Toggle ordinary Neo-tree while editing; focus-only in Review |
| Space r / `:Review` | Enter/focus Review |
| Enter in sidebar | Open selected changed file |
| Tab | Toggle tree/diff focus in Review |
| Space j / Space k | Next/previous changed file |
| `]` / `[` | Next/previous hunk across files/groups; retain pane focus |
| Space f / `:Files` | Fuzzy project files, or visible Review entries |
| Space / / `:Search` | Fuzzy saved contents, or old/new Review lines |
| Space m / `:ReviewMark` | Toggle reviewed mark |
| Space u / `:ReviewUnreviewed` | Toggle unreviewed-only filter |
| i/a/o/O/I/A/c/d/s/S/r/R/x/p… | Edit annotation rather than source |
| Visual selection, then i or Space c | Comment on exact selection |
| Space c / `:ReviewComments` | List/remove draft comments |
| Space s in a source pane | Stage/unstage the Git hunk containing the cursor |
| Space S in sidebar or source pane | Stage/unstage selected file or entire displayed diff |
| `:ReviewStage hunk` / `:ReviewStage file` | Explicitly stage; rejects STAGED entries |
| `:ReviewUnstage hunk` / `:ReviewUnstage file` | Explicitly unstage; requires STAGED entry |
| Space R / `:ReviewRefresh` | Refresh snapshots and changed-file list |
| `:w` / `:WriteFeedback` | Queue/send feedback; never stage or save source |
| `:wq` in annotation | Submit feedback and close only annotation |
| `?` / `:help` | Help overlay; ?/q/Esc closes it |
| `:ReviewHarness [name session]` | Show or change this repository's harness binding |
| `:ReviewRetry` | Explicit retry after checking an uncertain/failed delivery |
| `:ReviewOutbox` | Browse persisted feedback and delivery receipts |
| `:ReviewArchive` | Archive all drafts locally and begin a new round |
| Space q / `:ReviewLeave` (also q in sidebar) | Return to editing, retaining drafts |

Git hunks include Git's context lines and can group nearby edits differently
from Codediff's highlighted ranges. New/deleted files require **Space S**. Renames
are shown as deletion/addition pairs. Stage before commenting if possible:
staging changes the index comparison and can make existing anchors stale.

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
repository is required, and installation does not bind a session or send feedback.
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
only the plugin; use Nix for the editor. Neither installation method reloads Amp.
Restart Amp or reload its plugins after installation. In Amp's command palette,
**anthrodiff: connect** shows connection instructions; **anthrodiff: disconnect**
stops this thread's endpoint. Session/agent start registers silently by default.

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
discovery or live refresh. Connecting sends nothing. `:Harness send` opens a
general-message `acwrite` buffer with normal Vim editing: `:w` submits and
stays open, `:wq` submits and closes, and `:q` (or `:q!`) closes while retaining
the draft locally without sending. Hidden message drafts do not block quitting;
ordinary unsaved files remain protected. An `accepted` ACK means the steering
message was queued, not that the agent completed a turn. Disconnect retains
local drafts and does not stop the agent.

Accepted/completed messages clear from the open composer and its saved draft,
so the next `:harness send` opens empty. Pending, failed/uncertain, and local-only
queued messages are retained. A late acknowledgment never clears different text
typed while sending. An empty `:w` sends nothing, and `:wq` can close a cleared
composer. **This does not clear individual review annotations**: `:w` in Review
still submits the accumulated annotation batch, retained until explicitly archived.

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

- Drafts/outbox live under `stdpath("state")/reviews/<repository-hash>/`,
  normally `~/.local/state/myeditor/reviews/`. Files are private (0600), with
  atomic replacement. Drafts, snapshots and agent results stay out of Git.
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
