{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.harnesses;
in
{
  options.programs.harnesses = {
    amp = {
      enable = lib.mkEnableOption "the Amp CLI (without editor plugins or authentication)";
      package = lib.mkPackageOption pkgs "amp-cli" { };
    };
    pi = {
      enable = lib.mkEnableOption "the pi coding agent CLI (without editor plugins or authentication)";
      package = lib.mkPackageOption pkgs "pi-coding-agent" { };
    };
  };

  config.home.packages =
    lib.optional cfg.amp.enable cfg.amp.package ++ lib.optional cfg.pi.enable cfg.pi.package;
}
