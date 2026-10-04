# rediff

Testing

Read diff: a human reading the agent's changes. A Nix-packaged Neovim setup
for modal editing and agent-assisted code review.

The self-contained [`nix/`](nix/) directory contains the flake, Home Manager
module, pinned plugins, configuration, and tests. Copy that directory into
your dotfiles or consume it as a flake input.

```sh
nix run path:./nix
```

To install the Nix-built editor as `nvim` through Home Manager:

```sh
./install.sh           # Build only; no activation or shell changes
./install.sh --switch  # Install, then open a new terminal and type nvim
```

The installer bootstraps Home Manager using this repository's pinned inputs.
It refuses to replace an existing Home Manager setup; use the module in the
[installation guide](nix/README.md#home-manager) for that case. It preserves
your existing Neovim configuration. The theme is **Rosé Pine (main)**, packaged
by Nix along with the editor and plugins.

The `rediff` command retains `NVIM_APPNAME=myeditor` for existing settings,
outboxes and harness bindings; `myeditor` remains a compatibility alias. Your normal `nvim`
configuration is untouched. Interactive launches inside a Git worktree open
Review automatically. Press **i** to comment and **:w** in a diff pane to send
saved feedback. **Space q** returns to ordinary editing; **Space r** re-enters
Review. Git staging is an explicit, separate action.

Supports **Amp**, **Claude Code**, and custom harness receivers. Select a
thread/session per repository with `:ReviewHarness amp T-…`; configuration
and credentials stay separate from your portable editor setup.

For live Amp steering, this repository owns the **anthrodiff** plugin and
installer in [`nix/amp/`](nix/amp/). Enable
`programs.rediff.ampPlugin.enable = true` in Home Manager, or run
`:harness install amp` in the editor (shell: `bash nix/amp/install.sh`), then
restart/reload Amp and use `:harness connect amp`.
The revdiff plugin is no longer required or supported; see the guide for replacing it.

Built on [Codediff](https://github.com/esmuellert/codediff.nvim)'s diff renderer,
with compact inline annotations and
[Neo-tree](https://github.com/nvim-neo-tree/neo-tree.nvim) for ordinary editing.

See the [installation, workflow, and agent integration guide](nix/README.md).
Review refreshes automatically; **Space R** refreshes immediately. The bottom
Review bar shows the branch or detached commit. Use `:harness use amp`, then
`:worktree new` to create a checkout with its own harness process.
`:worktree list` / `:worktree switch` navigates existing checkouts. See the
[feature list](FEATURES.md) for implemented behavior and remaining gaps.
