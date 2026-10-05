{ pkgs, module }:
let
  inherit (pkgs) lib;
  amp = pkgs.writeShellScriptBin "amp" "exit 0";
  omp = pkgs.writeShellScriptBin "omp" "exit 0";
  custom = pkgs.writeShellScriptBin "custom-amp" "exit 0";
  existing = pkgs.emptyDirectory;
  packages =
    settings:
    (lib.evalModules {
      specialArgs.pkgs = {
        amp-cli = amp;
        inherit omp;
      };
      modules = [
        module
        {
          options.home.packages = lib.mkOption {
            type = lib.types.listOf lib.types.package;
            default = [ ];
          };
          config.home.packages = [ existing ];
        }
        { programs.harnesses = settings; }
      ];
    }).config.home.packages;
  paths = packages: lib.sort builtins.lessThan (map toString packages);
in
assert paths (packages { }) == paths [ existing ];
assert
  paths (packages {
    amp.enable = true;
  }) == paths [
    existing
    amp
  ];
assert
  paths (packages {
    omp.enable = true;
  }) == paths [
    existing
    omp
  ];
assert
  paths (packages {
    amp.enable = true;
    omp.enable = true;
  }) == paths [
    existing
    amp
    omp
  ];
assert
  paths (packages {
    amp.enable = true;
    amp.package = custom;
  }) == paths [
    existing
    custom
  ];
assert
  paths (packages {
    omp.enable = true;
    omp.package = custom;
  }) == paths [
    existing
    custom
  ];
pkgs.runCommand "rediff-harness-module-tests" { } "touch $out"
