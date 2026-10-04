{
  lib,
  stdenv,
  vimUtils,
  vimPlugins,
  neovim,
  makeWrapper,
  git,
  ripgrep,
  fzf,
  difftastic,
  python3,
  bash,
  coreutils,
  inputs,
}:
let
  codediff = vimUtils.buildVimPlugin {
    pname = "codediff.nvim";
    version = inputs.codediff.shortRev or "pinned";
    src = inputs.codediff;
    buildPhase = ''
      runHook preBuild
      bash build.sh
      runHook postBuild
    '';
    doCheck = false;
    doInstallCheck = false;
  };
  review = vimUtils.buildVimPlugin {
    pname = "review.nvim";
    version = inputs.review.shortRev or "pinned";
    src = inputs.review;
    doCheck = false;
    doInstallCheck = false;
  };
  plugins = [
    codediff
    review
    vimPlugins.nui-nvim
    vimPlugins.neo-tree-nvim
    vimPlugins.fzf-lua
    vimPlugins.plenary-nvim
    vimPlugins.nvim-web-devicons
    vimPlugins.rose-pine
  ];
  runtime = lib.concatMapStringsSep "," toString plugins;
in
stdenv.mkDerivation {
  pname = "myeditor";
  version = "0.1.0";
  src = ./config;
  nativeBuildInputs = [ makeWrapper ];
  dontBuild = true;
  installPhase = ''
    mkdir -p "$out/share/myeditor" "$out/bin"
    cp -R . "$out/share/myeditor/"
    ${neovim}/bin/nvim --headless -u NONE -i NONE -n \
      -c "helptags $out/share/myeditor/doc" -c 'qa!'
    makeWrapper ${neovim}/bin/nvim "$out/bin/myeditor" \
      --set NVIM_APPNAME myeditor \
      --set MYEDITOR_RUNTIME "$out/share/myeditor" \
      --set MYEDITOR_PLUGINS '${runtime}' \
      --set VSCODE_DIFF_NO_AUTO_INSTALL 1 \
      --prefix PATH : ${
        lib.makeBinPath [
          git
          ripgrep
          fzf
          difftastic
        ]
      } \
      --prefix PATH : "$out/bin" \
      --add-flags '-u' --add-flags "$out/share/myeditor/init.lua"
    cp ${./harness.py} "$out/share/myeditor/harness.py"
    makeWrapper ${python3}/bin/python3 "$out/bin/myeditor-harness" \
      --add-flags "$out/share/myeditor/harness.py"
    cp ${./amp_live.py} "$out/share/myeditor/amp_live.py"
    makeWrapper ${python3}/bin/python3 "$out/bin/myeditor-amp-live" \
      --add-flags "$out/share/myeditor/amp_live.py"
    mkdir -p "$out/share/myeditor/amp"
    cp ${./amp/anthrodiff.ts} "$out/share/myeditor/amp/anthrodiff.ts"
    cp ${./amp/install.sh} "$out/share/myeditor/amp/install.sh"
    makeWrapper ${bash}/bin/bash "$out/bin/myeditor-install-amp-plugin" \
      --prefix PATH : ${lib.makeBinPath [ coreutils ]} \
      --add-flags "$out/share/myeditor/amp/install.sh"
  '';
  meta = {
    description = "Portable modal editor with snapshot-based agent review";
    mainProgram = "myeditor";
    platforms = lib.platforms.linux ++ lib.platforms.darwin;
  };
}
