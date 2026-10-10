{ self }:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.rediff;
  nvim = pkgs.runCommand "rediff-nvim" { } ''
    mkdir -p "$out/bin"
    ${lib.optionalString cfg.nvimAlias ''ln -s ${cfg.package}/bin/rediff "$out/bin/nvim"''}
    ${lib.optionalString cfg.vimAlias ''ln -s ${cfg.package}/bin/rediff "$out/bin/vim"''}
  '';
in
{
  options.programs.rediff = {
    enable = lib.mkEnableOption "the isolated rediff Neovim profile";
    nvimAlias = lib.mkEnableOption "installing rediff as nvim ahead of other editors on PATH";
    vimAlias = lib.mkEnableOption "installing rediff as vim ahead of other editors on PATH";
    manageSettings = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Manage rediff/settings.json with the harness, feedbackCommand and
        reviewRefreshInterval options. Disable to keep an existing user-managed
        settings file; those options then do not configure the editor.
        Plugin installation remains controlled by ampPlugin and ompPlugin.
      '';
    };
    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      description = "The rediff package to install.";
    };
    reviewRefreshInterval = lib.mkOption {
      type = lib.types.ints.unsigned;
      default = 3;
      example = 10;
      description = ''
        Seconds between fallback Review polling checks. Set to 0 to disable
        polling; Amp file events, Space R and :ReviewRefresh still work.
        Changes take effect when Review is next opened.
      '';
    };
    ampPlugin.enable = lib.mkOption {
      type = lib.types.bool;
      default = cfg.harness == "amp";
      description = "Install the readiff Amp plugin from this flake. Does not install or authenticate Amp.";
    };
    ompPlugin.enable = lib.mkOption {
      type = lib.types.bool;
      default = cfg.harness == "omp";
      description = "Install the rediff oh-my-pi extension for the default ~/.omp/agent profile. Does not install or authenticate OMP.";
    };
    harness = lib.mkOption {
      type = lib.types.enum [
        "none"
        "amp"
        "omp"
        "claude"
        "custom"
      ];
      default = "none";
      example = "amp";
      description = ''
        Default harness for repositories without a saved binding. Built-in
        live harnesses connect with :Harness connect amp|omp. CLI continuation
        uses :ReviewHarness. None keeps feedback in the local outbox.
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
    home.packages = [ cfg.package ] ++ lib.optional (cfg.nvimAlias || cfg.vimAlias) nvim;
    # A store path here pins existing shells to an obsolete editor after switch.
    home.sessionPath = lib.optional (
      cfg.nvimAlias || cfg.vimAlias
    ) "${config.home.profileDirectory}/bin";
    home.file.".config/amp/plugins/readiff.ts" = lib.mkIf cfg.ampPlugin.enable {
      source = ../plugins/amp/readiff.ts;
    };
    home.file.".omp/agent/extensions/rediff.ts" = lib.mkIf cfg.ompPlugin.enable {
      source = ../plugins/omp/rediff.ts;
    };
    xdg.configFile."rediff/settings.json" = lib.mkIf cfg.manageSettings {
      text = builtins.toJSON {
        harness = cfg.harness;
        feedback_command = cfg.feedbackCommand;
        review_refresh_interval = cfg.reviewRefreshInterval;
      };
    };
  };
}
