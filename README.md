# myeditor

A portable Neovim setup for modal editing and agent-assisted code review.

## Planned foundation

- Neovim for Normal, Insert, and Visual editing.
- [codediff.nvim](https://github.com/esmuellert/codediff.nvim) for Git diffs,
  the changed-file sidebar, and staging.
- [review.nvim](https://github.com/georgeguimaraes/review.nvim) for inline
  feedback on code and selected ranges.
- A custom Review workspace and agent adapter for submitting feedback with
  `:w` without writing the reviewed source files.

## Distribution plan

Keep the configuration and a version-controlled `lazy-lock.json` in this
repository. Use `NVIM_APPNAME=myeditor` to keep its configuration, plugins,
and state separate from an existing Neovim setup. Agent credentials and
machine-specific state stay outside this repository.

## Status

Repository scaffold only. The Neovim configuration, installer, and custom
Review workflow have not been implemented yet.
