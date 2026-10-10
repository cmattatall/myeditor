{ pkgs, flake }:
let
  editor = flake.packages.${pkgs.stdenv.hostPlatform.system}.default;
  homeDirectory = if pkgs.stdenv.hostPlatform.isDarwin then "/Users/consumer" else "/home/consumer";
  extraPackage = pkgs.writeShellScriptBin "consumer-command" "echo consumer-package";
  customEditor = pkgs.writeShellScriptBin "rediff" "echo custom-editor";
  evaluate =
    settings:
    (flake.inputs.home-manager.lib.homeManagerConfiguration {
      inherit pkgs;
      modules = [
        flake.homeManagerModules.default
        {
          home = {
            username = "consumer";
            inherit homeDirectory;
            stateVersion = "24.11";
            packages = [ extraPackage ];
            sessionVariables.EDITOR = "nvim";
          };
          programs.bash.enable = true;
          programs.bash.initExtra = "export CONSUMER_SHELL=owned-by-consumer";
          xdg.configFile."nvim/init.lua".text = "vim.cmd('cquit 42')";
          programs.rediff = settings;
        }
      ];
    }).config;
  disabled = evaluate { };
  enabled = evaluate { enable = true; };
  ampEnabled = evaluate {
    enable = true;
    ampPlugin.enable = true;
  };
  aliased = evaluate {
    enable = true;
    nvimAlias = true;
    reviewRefreshInterval = 17;
  };
  vimOnly = evaluate {
    enable = true;
    vimAlias = true;
    manageSettings = false;
  };
  overridden = evaluate {
    enable = true;
    nvimAlias = true;
    vimAlias = true;
    package = customEditor;
  };
  paths = packages: map toString packages;
in
assert !(builtins.elem (toString editor) (paths disabled.home.packages));
assert !(disabled.xdg.configFile ? "rediff/settings.json");
assert !(vimOnly.xdg.configFile ? "rediff/settings.json");
assert builtins.elem (toString editor) (paths enabled.home.packages);
assert enabled.home.sessionPath == disabled.home.sessionPath;
assert aliased.home.username == "consumer";
assert aliased.home.homeDirectory == homeDirectory;
assert aliased.home.stateVersion == "24.11";
assert aliased.home.sessionVariables.EDITOR == "nvim";
assert aliased.programs.bash.initExtra == disabled.programs.bash.initExtra;
assert aliased.xdg.configFile."nvim/init.lua".text == disabled.xdg.configFile."nvim/init.lua".text;
assert builtins.elem (toString extraPackage) (paths aliased.home.packages);
assert builtins.elem "${aliased.home.profileDirectory}/bin" aliased.home.sessionPath;
assert !(aliased.home.file ? ".config/rediff/standalone-owner");
assert !(aliased.home.file ? ".config/amp/plugins/rediff.ts");
assert !(aliased.home.file ? ".config/amp/plugins/readiff.ts");
assert !(ampEnabled.home.file ? ".config/amp/plugins/rediff.ts");
assert
  builtins.readFile ampEnabled.home.file.".config/amp/plugins/readiff.ts".source
  == builtins.readFile ../../plugins/amp/readiff.ts;
assert !(aliased.home.file ? ".omp/agent/extensions/rediff.ts");
assert
  builtins.fromJSON aliased.xdg.configFile."rediff/settings.json".text == {
    harness = "none";
    feedback_command = [ ];
    review_refresh_interval = 17;
  };
assert !(builtins.elem (toString editor) (paths overridden.home.packages));
assert builtins.elem (toString customEditor) (paths overridden.home.packages);
pkgs.runCommand "rediff-home-manager-composition-tests" { } ''
  export HOME="$TMPDIR/home"
  mkdir -p "$HOME/.config/nvim"
  cp ${aliased.xdg.configFile."nvim/init.lua".source} "$HOME/.config/nvim/init.lua"
  test ! -e ${enabled.home.path}/bin/nvim
  test "$(realpath ${aliased.home.path}/bin/nvim)" = "${editor}/bin/rediff"
  test ! -e ${aliased.home.path}/bin/vim
  test ! -e ${vimOnly.home.path}/bin/nvim
  test "$(realpath ${vimOnly.home.path}/bin/vim)" = "${editor}/bin/rediff"
  test "$(${aliased.home.path}/bin/consumer-command)" = consumer-package
  test ! -e ${aliased.home.path}/bin/amp
  test ! -e ${aliased.home.path}/bin/omp
  ${aliased.home.path}/bin/nvim --headless -i NONE \
    -c 'lua if vim.env.NVIM_APPNAME ~= "rediff" or vim.api.nvim_get_hl(0, {name="Normal"}).bg ~= 0x191724 then vim.cmd("cquit 1") end' \
    -c 'qa!'
  test "$(${overridden.home.path}/bin/nvim)" = custom-editor
  test "$(${overridden.home.path}/bin/vim)" = custom-editor
  touch "$out"
''
