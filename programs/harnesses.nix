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
    omp = {
      enable = lib.mkEnableOption "the oh-my-pi CLI (without editor extensions or authentication)";
      package = lib.mkPackageOption pkgs "omp" { };
    };
  };

  config.home.packages =
    lib.optional cfg.amp.enable cfg.amp.package ++ lib.optional cfg.omp.enable cfg.omp.package;
}
