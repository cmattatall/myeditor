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
