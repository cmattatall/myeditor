{ self }:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.myeditor;
in
{
  options.programs.myeditor = {
    enable = lib.mkEnableOption "the isolated myeditor Neovim profile";
    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      description = "The myeditor package to install.";
    };
    harness = lib.mkOption {
      type = lib.types.enum [
        "none"
        "amp"
        "claude"
        "custom"
      ];
      default = "none";
      example = "amp";
      description = ''
        Default harness for repositories without a saved binding. Built-in
        harnesses require an explicit per-repository thread/session selected
        with :ReviewHarness. None keeps feedback in the local outbox.
      '';
    };
    feedbackCommand = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      example = [ "/path/to/my-langgraph-receiver" ];
      description = ''
        Receiver argv for the custom harness. The editor appends the JSON
        submission path and runs the command in the reviewed repository.
        Never put credentials here: this value is visible in the Nix store.
      '';
    };
  };
  config = lib.mkIf cfg.enable {
    home.packages = [ cfg.package ];
    xdg.configFile."myeditor/settings.json".text = builtins.toJSON {
      harness = cfg.harness;
      feedback_command = cfg.feedbackCommand;
    };
  };
}
