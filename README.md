# rediff

The public distribution lives at [cmattatall/readiff](https://github.com/cmattatall/readiff).
This is a one-way snapshot mirror maintained from the author's development checkout.
Issues and pull requests are welcome; contributions are integrated upstream before
the next snapshot is published. You do not need access to the development checkout.

**Read the diff.** rediff is a Neovim-based editor for reviewing an AI coding
agent's changes. Read side-by-side or merged diffs, annotate lines and selections,
send feedback to an agent, and review what changed afterward. You control staging
and commits; sending feedback never stages files.

It also works as an ordinary editor. Opening a file starts in editing mode.
Launching without file arguments inside a Git worktree opens Review. **Space r**
toggles between Review and editing.

rediff is distributed as a **Nix package**, not a home configuration. Home Manager
is optional. The package bundles Neovim, its plugins, and the Rosé Pine theme under
`NVIM_APPNAME=rediff`; it does not replace `~/.config/nvim`, alias `nvim`, install
agent CLIs, or manage your shell. Supported targets are Apple Silicon macOS and
ARM64/x86-64 Linux.

## Run or install the package

Install [Nix](https://nixos.org/download/) first. Enable flakes in your Nix
configuration (`~/.config/nix/nix.conf` on a standalone installation):

```conf
experimental-features = nix-command flakes
```

From a checkout:

```sh
git clone https://github.com/cmattatall/readiff.git
cd readiff
nix run path:.                 # Review this worktree
nix run path:. -- src/file.lua # Edit a file directly
nix build path:.               # Build only: ./result/bin/rediff
nix profile add path:.         # Optional: install the rediff command
```

Or run the published version from the worktree you want to review:

```sh
nix run github:cmattatall/readiff
# Or install the command:
nix profile add github:cmattatall/readiff
```

The application and command are `rediff`; the GitHub repository retains the name `readiff`.
The package is defined in [`nix/package.nix`](nix/package.nix) and exported as
`packages.<system>.rediff` and `packages.<system>.default`. Building/running it
does not activate Home Manager.

## Use your own Home Manager configuration

If you already use Home Manager, add rediff to your existing configuration rather
than running the bootstrap installer below. This repository supplies the Neovim
editing configuration, plugins, and editor package. Your home configuration owns
the username, home directory, state version, shell, other packages, and activation.
Importing the module alone enables nothing.

Add this input to your `flake.nix`:

```nix
inputs.rediff.url = "github:cmattatall/readiff";
```

Include the module in your **Home Manager** module list. For a standalone flake,
the relevant output looks like this; retain your existing username, system,
inputs, and other modules:

```nix
outputs = inputs@{ nixpkgs, home-manager, ... }: {
  homeConfigurations."you" = home-manager.lib.homeManagerConfiguration {
    pkgs = nixpkgs.legacyPackages.aarch64-darwin;
    modules = [
      inputs.rediff.homeManagerModules.default
      ./home.nix
    ];
  };
};
```

Add these settings to `home.nix`:

```nix
programs.rediff = {
  enable = true;
  nvimAlias = true;           # Use this editing configuration when running nvim
  ampPlugin.enable = false;  # Opt in to managing the rediff Amp plugin
  reviewRefreshInterval = 3; # Seconds; 0 disables fallback polling, not events
};

# Optional: make it the editor used by other commands.
home.sessionVariables.EDITOR = "nvim";
home.sessionVariables.VISUAL = "nvim";
```

With `nvimAlias = true`, disable `programs.neovim.enable` and remove any separate
`pkgs.neovim` from `home.packages` to avoid two packages providing `bin/nvim`.
The bundled configuration stays isolated under `NVIM_APPNAME=rediff`; existing
`~/.config/nvim` files are neither overwritten nor loaded. Omit `nvimAlias` (it
defaults to false) to keep another `nvim` and launch this editor as `rediff` only.

The editor uses this repository's pinned dependencies by default. You may set
`inputs.rediff.inputs.nixpkgs.follows = "nixpkgs"` to share your nixpkgs instead,
but that also changes the editor's Neovim and plugin versions. The module uses
your Home Manager instance; this flake's Home Manager input is only for its own
checks and optional bootstrap profile. Update the editor with
`nix flake update rediff` from your home configuration, then rebuild as usual.

Apply with your usual `home-manager switch --flake ~/.config/home-manager`,
or your NixOS/nix-darwin rebuild if Home Manager is integrated there. The module
does not install agent CLIs. Import `inputs.rediff.homeManagerModules.harnesses`
in your module list to opt into the independent harness installer, then add:

```nix
programs.harnesses.amp.enable = true;
programs.harnesses.omp.enable = true;
nixpkgs.config.allowUnfreePredicate = pkg: lib.getName pkg == "amp-cli";
```

Both harnesses default to disabled and each accepts a `.package` override.
You can import only the harness module without installing rediff, Neovim, or the
Amp bridge plugin. Conversely, installing the editor does not require harnesses.

`lib` is a Home Manager module argument. If you supply an already configured/global
`pkgs`, allow Amp in that nixpkgs instance instead. Authenticate
the CLIs separately; credentials do not belong in the Nix configuration.
`omp` is [oh-my-pi](https://omp.sh), not upstream pi.

## Install Home Manager for the first time

Choose **one** of these paths after installing Nix and enabling flakes.

**Manage your own home configuration:** follow the
[Home Manager manual](https://nix-community.github.io/home-manager/). For an
unstable nixpkgs-based configuration, initialize it with:

```sh
nix run github:nix-community/home-manager/master -- init --switch
```

This creates `~/.config/home-manager/flake.nix` and `home.nix`. Then add rediff as
shown above. Use the matching Home Manager release branch if your nixpkgs is
pinned to a stable release. Do not initialize over an existing configuration.

**Use rediff's optional bootstrap profile:** if you do not have a Home Manager
configuration and want the editor plus harness CLIs, run from this checkout:

```sh
./install.sh          # Build the optional profile; change nothing in your home
./install.sh --switch # Activate it and add Home Manager session setup to your shell
```

This explicitly opts into [`examples/home-manager.nix`](examples/home-manager.nix).
It installs rediff as `nvim`, plus `amp` and `omp`, allowing only Amp's unfree
package. It leaves your Neovim configuration, agent credentials, and agent plugins
alone. No separate Home Manager CLI installation is required.

The script refuses to replace an unrelated Home Manager/NixOS/nix-darwin profile.
It backs up a regular shell rc before adding the session setup line. On first
installation, refresh your shell once:

```sh
unset __HM_SESS_VARS_SOURCED
. "$HOME/.nix-profile/etc/profile.d/hm-session-vars.sh"
hash -r
command -v nvim
```

Repeat `./install.sh --switch` to rebuild and activate later changes. Quit and
reopen Neovim afterward; an already-running editor retains its previous build.
See the [installation details](nix/README.md#home-manager) for profile safety,
PATH precedence, and integration with an existing home configuration.

## Review and send feedback

1. Launch `rediff` in a Git worktree, or press **Space r** while editing there.
2. Use **j/k** in the tree to preview files and **Tab** to enter the diff.
   **[ / ]** moves between hunks; `:view` toggles split/merged layout.
3. Press **i** to add/edit an annotation, or select text and press **a** to
   annotate a range. Save the note with `:w`. **@** lists/searches annotations.
4. Connect a harness, then `:w` in the tree/diff sends saved annotations.
   `:harness send` composes a general message.
5. Review the next edits: violet **◆** means changed/unseen, cyan **◇** means
   changed/seen. **s** stages a hunk; **S** stages a file. **Space R** refreshes.

For a live Amp session, install the rediff plugin with `:harness install amp`,
or opt into `programs.rediff.ampPlugin.enable` in Home Manager. Reload Amp's
plugins, then run `:harness connect amp`. `:harness list` shows available and
connected sessions, their worktrees, and activity. Multiple connections are
supported; annotations stay associated with their originating worktree.

For **oh-my-pi**, enable `programs.harnesses.omp.enable` to install the CLI.
Separately, enable `programs.rediff.ompPlugin.enable` or run
`:harness install omp` to install the rediff extension. Start OMP (or run
`/restart` in an existing session), then `:harness connect omp` in rediff.
Amp and OMP sessions can share the panel and stay connected simultaneously.

Amp, oh-my-pi, Claude Code, and custom feedback receivers are supported. Agent selection,
authentication, and sending feedback are explicit actions, not installation steps.
Read the [agent integration guide](nix/README.md#connect-an-agent-amp-claude-code-or-a-custom-harness),
[editor help](nix/config/doc/rediff.txt), or [feature list](FEATURES.md) for details.
Press **?** in the editor for help.

## Repository layout

- [`nix/package.nix`](nix/package.nix): reusable editor derivation.
- [`nix/config/`](nix/config/): Neovim configuration and Review implementation.
- [`nix/home-manager.nix`](nix/home-manager.nix): optional module, not a home profile.
- [`programs/harnesses.nix`](programs/harnesses.nix): independent opt-in Amp/OMP CLI module.
- [`examples/home-manager.nix`](examples/home-manager.nix): opinionated bootstrap
  profile used only by the install script and its tests.
- [`plugins/`](plugins/): harness plugins, including the Amp bridge.

Built on [Codediff](https://github.com/esmuellert/codediff.nvim),
[review.nvim](https://github.com/georgeguimaraes/review.nvim), and
[Neo-tree](https://github.com/nvim-neo-tree/neo-tree.nvim).
Licensed under the [MIT License](LICENSE); dependencies retain their own licenses.
