# myeditor

A Nix-packaged Neovim setup for modal editing and agent-assisted code review.

The self-contained [`nix/`](nix/) directory contains the flake, Home Manager
module, pinned plugins, configuration, and tests. Copy that directory into
your dotfiles or consume it as a flake input.

```sh
nix run path:./nix
```

The `myeditor` command uses `NVIM_APPNAME=myeditor`; your normal `nvim`
configuration is untouched. Press **Space r** for Review, **i** to comment,
and **:w** to queue/send feedback instead of saving source. Git staging is
an explicit, separate action.

Supports **Amp**, **Claude Code**, and custom harness receivers. Select a
thread/session per repository with `:ReviewHarness amp T-…`; configuration
and credentials stay separate from your portable editor setup.

Built on [Codediff](https://github.com/esmuellert/codediff.nvim)'s diff renderer,
[review.nvim](https://github.com/georgeguimaraes/review.nvim)'s comment renderer,
and [Neo-tree](https://github.com/nvim-neo-tree/neo-tree.nvim) for ordinary editing.

See the [installation, workflow, and agent integration guide](nix/README.md).
Planned worktree switching and live refresh are tracked in the [feature list](FEATURES.md).
