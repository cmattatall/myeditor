{
  description = "rediff: read agent diffs in a Nix-packaged Neovim workspace";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    codediff = {
      url = "github:esmuellert/codediff.nvim/09d9ebef2cc5a5c04db7a349cd6c61bdf84ecc8e";
      flake = false;
    };
    review = {
      url = "github:georgeguimaraes/review.nvim/f72a347538913ac558d2440dd3899426a2dd85ae";
      flake = false;
    };
  };

  outputs =
    inputs@{ self, nixpkgs, ... }:
    let
      systems = [
        "aarch64-darwin"
        "aarch64-linux"
        "x86_64-linux"
      ];
      eachSystem = nixpkgs.lib.genAttrs systems;
    in
    {
      packages = eachSystem (
        system:
        let
          pkgs = import nixpkgs { inherit system; };
          editor = pkgs.callPackage ./nix/package.nix { inherit inputs; };
        in
        {
          default = editor;
          rediff = editor;
        }
      );
      apps = eachSystem (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/rediff";
          meta.description = "Launch rediff";
        };
      });
      checks = eachSystem (
        system:
        let
          pkgs = import nixpkgs { inherit system; };
          testHome = if pkgs.stdenv.hostPlatform.isDarwin then "/Users/review-test" else "/home/review-test";
          standalone = import ./examples/home-manager.nix {
            flake = self;
            inherit system;
            username = "review-test";
            homeDirectory = testHome;
          };
        in
        {
          home-manager =
            (inputs.home-manager.lib.homeManagerConfiguration {
              inherit pkgs;
              modules = [
                self.homeManagerModules.default
                {
                  home.username = "review-test";
                  home.homeDirectory =
                    if pkgs.stdenv.hostPlatform.isDarwin then "/Users/review-test" else "/home/review-test";
                  home.stateVersion = "26.05";
                  programs.rediff.enable = true;
                  programs.rediff.harness = "amp";
                }
              ];
            }).activationPackage;
          home-manager-install =
            pkgs.runCommand "rediff-home-manager-install-tests"
              {
                nativeBuildInputs = [
                  pkgs.python3
                  pkgs.bash
                ];
              }
              ''
                export HOME="$TMPDIR/home"
                mkdir -p "$HOME/homebrew/bin"
                printf '#!/bin/sh\nexit 1\n' > "$HOME/homebrew/bin/nvim"
                chmod +x "$HOME/homebrew/bin/nvim"
                export PATH="$HOME/homebrew/bin:$PATH"
                python3 -B ${./nix/tests/test_install_home_manager.py} ${self}/nix/install-home-manager.sh
                ln -s ${standalone}/home-path "$HOME/.nix-profile"
                sed 's|${testHome}|'"$HOME"'|g' ${standalone}/home-path/etc/profile.d/hm-session-vars.sh > "$TMPDIR/session-vars.sh"
                unset __HM_SESS_VARS_SOURCED
                . "$TMPDIR/session-vars.sh"
                test "$(command -v nvim)" = "$HOME/.nix-profile/bin/nvim"
                test "$(realpath "$(command -v nvim)")" = "${self.packages.${system}.default}/bin/rediff"
                test "$(command -v amp)" = "$HOME/.nix-profile/bin/amp"
                test "$(command -v pi)" = "$HOME/.nix-profile/bin/pi"
                amp --version
                pi --version
                test ! -e "$HOME/.nix-profile/bin/myeditor"
                nvim --headless -i NONE -c 'lua if vim.env.NVIM_APPNAME ~= "rediff" or vim.api.nvim_get_hl(0, {name="Normal"}).bg ~= 0x191724 then vim.cmd("cquit 1") end' -c 'qa!'
                # A profile switch must update nvim in this same shell, without
                # sourcing session vars again or clearing its command cache.
                mkdir -p "$TMPDIR/next-generation/bin"
                printf '#!/bin/sh\necho updated-editor\n' > "$TMPDIR/next-generation/bin/nvim"
                chmod +x "$TMPDIR/next-generation/bin/nvim"
                ln -sfn "$TMPDIR/next-generation" "$HOME/.nix-profile"
                test "$(nvim)" = updated-editor
                test "$(cat ${standalone}/home-files/.config/rediff/standalone-owner)" = rediff-standalone-v1
                test ! -e ${standalone}/home-files/.config/amp/plugins/rediff.ts
                touch "$out"
              '';
          rediff-amp =
            pkgs.runCommand "rediff-amp-tests"
              {
                nativeBuildInputs = [
                  pkgs.nodejs
                  pkgs.python3
                ];
              }
              ''
                export HOME="$TMPDIR/home"
                export REDIFF_TEST_BRIDGE=${self}/nix/amp_live.py
                mkdir -p "$HOME"
                node --test ${./plugins/amp}/tests/rediff.test.ts
                bash ${./plugins/amp}/tests/install.test.sh
                touch "$out"
              '';
          review =
            pkgs.runCommand "rediff-tests"
              {
                nativeBuildInputs = [
                  self.packages.${system}.default
                  pkgs.git
                  (pkgs.python3.withPackages (ps: [ ps.pynvim ]))
                ];
              }
              ''
                export HOME="$TMPDIR/home"
                mkdir -p "$HOME"
                rediff --headless -l ${./nix/tests}/run.lua
                python3 -B ${./nix/tests/test_harness.py} ${./nix/harness.py}
                python3 -B ${./nix/tests/test_amp_live.py} ${self}/nix/amp_live.py
                python3 -B ${./nix/tests}/test_ui.py
                touch "$out"
              '';
        }
      );
      formatter = eachSystem (system: nixpkgs.legacyPackages.${system}.nixfmt);
      homeManagerModules.default = import ./nix/home-manager.nix { inherit self; };
    };
}
