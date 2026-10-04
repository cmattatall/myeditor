# rediff

Testing

Read diff: a human reading the agent's changes. A Nix-packaged Neovim setup
for modal editing and agent-assisted code review.

The root flake packages the Home Manager module, editor configuration, and tests
under [`nix/`](nix/), plus harness plugins under [`plugins/`](plugins/).
Consume this repository as a flake input or vendor both directories with the flake
and [`LICENSE`](LICENSE).

```sh
nix run path:.
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

The `rediff` command uses its own `NVIM_APPNAME=rediff` profile. Your normal
Neovim configuration is untouched. Interactive launches inside a Git worktree open
Review automatically. Press **i** to comment and **:w** in a diff pane to send
saved feedback. **Space q** returns to ordinary editing; **Space r** re-enters
Review. Git staging is an explicit, separate action.

Supports **Amp**, **Claude Code**, and custom harness receivers. Select a
thread/session per repository with `:ReviewHarness amp T-…`; configuration
and credentials stay separate from your portable editor setup.

For live Amp steering, this repository owns the **rediff** plugin and
installer in [`plugins/amp/`](plugins/amp/). Enable
`programs.rediff.ampPlugin.enable = true` in Home Manager, or run
`:harness install amp` in the editor (shell: `bash plugins/amp/install.sh`), then
restart/reload Amp and use `:harness connect amp`.
Other plugin files are left untouched.

Built on [Codediff](https://github.com/esmuellert/codediff.nvim)'s diff renderer,
with compact inline annotations and
[Neo-tree](https://github.com/nvim-neo-tree/neo-tree.nvim) for ordinary editing.

See the [installation, workflow, and agent integration guide](nix/README.md).
Review refreshes automatically; **Space R** refreshes immediately. The bottom
Review bar shows the branch or detached commit. Use `:harness use amp`, then
`:worktree new` to create a checkout with its own harness process.
`:worktree list` / `:worktree switch` navigates existing checkouts. See the
[feature list](FEATURES.md) for implemented behavior and remaining gaps.

Licensed under the [MIT License](LICENSE).

Third-party code retains its existing copyright notices and licenses.
