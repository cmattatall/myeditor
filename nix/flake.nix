{
  description = "An isolated Neovim editor with an agent review workspace";

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
          editor = pkgs.callPackage ./package.nix { inherit inputs; };
        in
        {
          default = editor;
          myeditor = editor;
        }
      );
      apps = eachSystem (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/myeditor";
          meta.description = "Launch myeditor";
        };
      });
      checks = eachSystem (
        system:
        let
          pkgs = import nixpkgs { inherit system; };
          standalone = import ./standalone-home.nix {
            flake = self;
            inherit system;
            username = "review-test";
            homeDirectory =
              if pkgs.stdenv.hostPlatform.isDarwin then "/Users/review-test" else "/home/review-test";
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
                  programs.myeditor.enable = true;
                  programs.myeditor.harness = "amp";
                }
              ];
            }).activationPackage;
          home-manager-install =
            pkgs.runCommand "myeditor-home-manager-install-tests"
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
                python3 -B ${./tests/test_install_home_manager.py} ${self}/install-home-manager.sh
                unset __HM_SESS_VARS_SOURCED
                . ${standalone}/home-path/etc/profile.d/hm-session-vars.sh
                test "$(readlink "$(command -v nvim)")" = "${self.packages.${system}.default}/bin/myeditor"
                nvim --headless -i NONE -c 'lua if vim.env.NVIM_APPNAME ~= "myeditor" or vim.api.nvim_get_hl(0, {name="Normal"}).bg ~= 0x191724 then vim.cmd("cquit 1") end' -c 'qa!'
                test "$(cat ${standalone}/home-files/.config/myeditor/standalone-owner)" = myeditor-standalone-v1
                test ! -e ${standalone}/home-files/.config/amp/plugins/anthrodiff.ts
                touch "$out"
              '';
          anthrodiff =
            pkgs.runCommand "anthrodiff-tests"
              {
                nativeBuildInputs = [
                  pkgs.nodejs
                  pkgs.python3
                ];
              }
              ''
                export HOME="$TMPDIR/home"
                export ANTHRODIFF_TEST_BRIDGE=${./amp_live.py}
                mkdir -p "$HOME"
                node --test ${./amp}/tests/anthrodiff.test.ts
                bash ${./amp}/tests/install.test.sh
                touch "$out"
              '';
          review =
            pkgs.runCommand "myeditor-tests"
              {
                nativeBuildInputs = [
                  self.packages.${system}.default
                  pkgs.git
                  pkgs.python3
                ];
              }
              ''
                export HOME="$TMPDIR/home"
                mkdir -p "$HOME"
                myeditor --headless -l ${./tests}/run.lua
                python3 -B ${./tests/test_harness.py} ${./harness.py}
                python3 -B ${./tests/test_amp_live.py} ${./amp_live.py}
                touch "$out"
              '';
        }
      );
      formatter = eachSystem (system: nixpkgs.legacyPackages.${system}.nixfmt);
      homeManagerModules.default = import ./home-manager.nix { inherit self; };
    };
}
