import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

INSTALLER = Path(sys.argv.pop(1)).resolve()


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rediff install ")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        self.generation = self.home / "generation"
        self.generation.mkdir()
        self.rc = self.home / ".zshrc"
        self.rc.write_text("# Existing shell configuration\nexport KEEP_ME=yes\n")
        self.original = self.rc.read_text()
        self.env = dict(os.environ, HOME=str(self.home), SHELL="/bin/zsh",
                        XDG_STATE_HOME=str(self.home / ".local/state"),
                        XDG_CONFIG_HOME=str(self.home / ".config"), ZDOTDIR=str(self.home),
                        PATH=str(self.bin) + os.pathsep + os.environ["PATH"])
        self.executable(self.bin / "id", 'echo rediff-install-test\n')
        self.executable(self.bin / "nix", 'printf "%s\\n" "$@" > "$HOME/build-args"\n'
                        'printf "%s\\n" "$REDIFF_FLAKE" > "$HOME/flake-path"\n'
                        'printf "%s\\n" "$HOME/generation"\n')
        self.executable(self.generation / "activate", 'touch "$HOME/activated"\n')

    def executable(self, path, body):
        path.write_text("#!/bin/sh\nset -eu\n" + body)
        path.chmod(0o755)

    def run_installer(self, *args):
        return subprocess.run(["bash", str(INSTALLER), *args], cwd=self.home,
                              env=self.env, capture_output=True, text=True)

    def test_default_build_never_activates_or_edits_shell(self):
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.home / "activated").exists())
        self.assertEqual(self.rc.read_text(), self.original)
        self.assertEqual((self.home / "build-args").read_text().splitlines(), [
            "build", "--impure", "--file", str(INSTALLER.parent.parent / "examples/home-manager.nix"),
            "--no-link", "--print-out-paths"])
        self.assertEqual((self.home / "flake-path").read_text().strip(),
                         "path:" + str(INSTALLER.parent.parent))

    def test_switch_preserves_shell_and_is_repeatable(self):
        profile = self.home / ".local/state/nix/profiles/home-manager"
        marker = self.generation / "home-files/.config/rediff/standalone-owner"
        marker.parent.mkdir(parents=True)
        marker.write_text("rediff-standalone-v1\n")
        for _ in range(2):
            result = self.run_installer("--switch")
            self.assertEqual(result.returncode, 0, result.stderr)
            if not profile.exists():
                profile.parent.mkdir(parents=True)
                profile.symlink_to(self.generation)
        self.assertTrue((self.home / "activated").exists())
        self.assertTrue(self.rc.read_text().startswith(self.original))
        self.assertEqual(self.rc.read_text().count('hm-session-vars.sh'), 1)
        backups = list(self.home.glob(".zshrc.rediff-backup.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), self.original)

    def test_old_standalone_marker_allows_safe_upgrade(self):
        profile = self.home / ".local/state/nix/profiles/home-manager"
        marker = self.generation / "home-files/.config/myeditor/standalone-owner"
        marker.parent.mkdir(parents=True)
        marker.write_text("myeditor-standalone-v1\n")
        profile.parent.mkdir(parents=True)
        profile.symlink_to(self.generation)
        result = self.run_installer("--switch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.home / "activated").exists())

    def test_wrong_old_marker_is_not_accepted(self):
        profile = self.home / ".local/state/nix/profiles/home-manager"
        marker = self.generation / "home-files/.config/myeditor/standalone-owner"
        marker.parent.mkdir(parents=True)
        marker.write_text("some-other-owner\n")
        profile.parent.mkdir(parents=True)
        profile.symlink_to(self.generation)
        result = self.run_installer("--switch")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.home / "activated").exists())

    def test_unrelated_generation_is_not_replaced(self):
        profile = self.home / ".local/state/nix/profiles/home-manager"
        profile.parent.mkdir(parents=True)
        profile.symlink_to(self.generation)
        result = self.run_installer("--switch")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to replace", result.stderr)
        self.assertFalse((self.home / "build-args").exists())
        self.assertEqual(self.rc.read_text(), self.original)

    def test_existing_config_is_not_replaced(self):
        config = self.home / ".config/home-manager"
        config.mkdir(parents=True)
        result = self.run_installer("--switch")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Existing Home Manager configuration", result.stderr)
        self.assertFalse((self.home / "activated").exists())

    def test_managed_shell_config_is_not_modified(self):
        target = self.home / "managed-zshrc"
        self.rc.rename(target)
        self.rc.symlink_to(target)
        result = self.run_installer("--switch")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to modify", result.stderr)
        self.assertEqual(target.read_text(), self.original)
        self.assertFalse((self.home / "activated").exists())

    def test_failed_activation_does_not_edit_shell(self):
        self.executable(self.generation / "activate", "exit 1\n")
        result = self.run_installer("--switch")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.rc.read_text(), self.original)

    def test_unknown_argument_has_no_side_effects(self):
        result = self.run_installer("--force")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.home / "build-args").exists())


if __name__ == "__main__":
    unittest.main()
