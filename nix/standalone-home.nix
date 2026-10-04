# Bootstrap for users without an existing Home Manager configuration.
{
  flake ? builtins.getFlake (builtins.getEnv "MYEDITOR_FLAKE"),
  system ? builtins.currentSystem,
  username ? builtins.getEnv "USER",
  homeDirectory ? builtins.getEnv "HOME",
}:
(flake.inputs.home-manager.lib.homeManagerConfiguration {
  pkgs = flake.inputs.nixpkgs.legacyPackages.${system};
  modules = [
    flake.homeManagerModules.default
    {
      home = {
        inherit username homeDirectory;
        stateVersion = "26.05";
        file.".config/myeditor/standalone-owner".text = "myeditor-standalone-v1\n";
      };
      programs.rediff = {
        enable = true;
        nvimAlias = true;
        # Installing the editor must not replace/reload an existing Amp plugin.
        ampPlugin.enable = false;
      };
    }
  ];
}).activationPackage
