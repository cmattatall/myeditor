# Optional installer profile, not the rediff package or reusable Home Manager module.
# For users without an existing home configuration; ./install.sh opts into this example.
{
  flake ? builtins.getFlake (builtins.getEnv "REDIFF_FLAKE"),
  system ? builtins.currentSystem,
  username ? builtins.getEnv "USER",
  homeDirectory ? builtins.getEnv "HOME",
}:
let
  pkgs = import flake.inputs.nixpkgs {
    inherit system;
    config.allowUnfreePredicate = pkg: flake.inputs.nixpkgs.lib.getName pkg == "amp-cli";
  };
in
(flake.inputs.home-manager.lib.homeManagerConfiguration {
  inherit pkgs;
  modules = [
    flake.homeManagerModules.default
    flake.homeManagerModules.harnesses
    {
      home = {
        inherit username homeDirectory;
        stateVersion = "26.05";
        file.".config/rediff/standalone-owner".text = "rediff-standalone-v1\n";
      };
      programs.harnesses.amp.enable = true;
      programs.harnesses.pi.enable = true;
      programs.rediff = {
        enable = true;
        nvimAlias = true;
        # Installing the editor must not replace/reload an existing Amp plugin.
        ampPlugin.enable = false;
      };
    }
  ];
}).activationPackage
