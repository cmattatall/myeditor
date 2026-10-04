{ self }:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.myeditor;
  nvim = pkgs.runCommand "myeditor-nvim" { } ''
    mkdir -p "$out/bin"
    ln -s ${cfg.package}/bin/myeditor "$out/bin/nvim"
  '';
in
{
  options.programs.myeditor = {
    enable = lib.mkEnableOption "the isolated myeditor Neovim profile";
    nvimAlias = lib.mkEnableOption "installing myeditor as nvim ahead of other editors on PATH";
    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      description = "The myeditor package to install.";
    };
    ampPlugin.enable = lib.mkOption {
      type = lib.types.bool;
      default = cfg.harness == "amp";
      description = "Install the anthrodiff Amp plugin from this flake. Does not install or authenticate Amp.";
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
    home.packages = [ cfg.package ] ++ lib.optional cfg.nvimAlias nvim;
    # A store path here pins existing shells to an obsolete editor after switch.
    home.sessionPath = lib.optional cfg.nvimAlias "${config.home.profileDirectory}/bin";
    home.file.".config/amp/plugins/anthrodiff.ts" = lib.mkIf cfg.ampPlugin.enable {
      source = ./amp/anthrodiff.ts;
    };
    home.activation.checkAnthrodiffPlugin = lib.mkIf cfg.ampPlugin.enable (
      lib.hm.dag.entryBefore [ "checkLinkTargets" ] ''
        for legacy in "$HOME/.config/amp/plugins/revdiff.ts" "$HOME/.config/amp/plugins/revdiff"; do
          if [ -e "$legacy" ] || [ -L "$legacy" ]; then
            echo "Disable the old revdiff plugin before enabling anthrodiff: $legacy" >&2
            exit 1
          fi
        done
      ''
    );
    xdg.configFile."myeditor/settings.json".text = builtins.toJSON {
      harness = cfg.harness;
      feedback_command = cfg.feedbackCommand;
    };
  };
}
