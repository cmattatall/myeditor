"""Exercise real startup, key input and layouts through Neovim's UI protocol."""

import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

import pynvim

EDITOR = shutil.which("myeditor")
FIXTURE = str(Path(__file__).with_name("fixture.lua"))
STATE = 'local r = require("myeditor.review"); local s = r.state; '


def pump(editor):
    timer = threading.Timer(0.15, lambda: editor.async_call(editor.stop_loop))
    timer.start()
    editor.run_loop(None, lambda *_: None)
    timer.join()


class EditorUI(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = os.environ.copy()
        os.environ["HOME"] = self.directory.name
        for name in (
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "XDG_STATE_HOME",
            "XDG_CACHE_HOME",
        ):
            os.environ[name] = self.directory.name + "/" + name
        self.editors = []
        self.fixture = self.launch(self.directory.name, headless=True)
        self.root = self.fixture.exec_lua(
            "local root = dofile(...).create(); return root", FIXTURE
        )

    def tearDown(self):
        for editor in reversed(self.editors):
            try:
                editor.input("<Esc>")
                editor.command("qa!")
            except EOFError:
                pass
            finally:
                editor.close()
        os.environ.clear()
        os.environ.update(self.environment)
        self.directory.cleanup()

    def launch(self, directory, headless=False, file=None):
        args = [
            EDITOR,
            "--embed",
            "-i",
            "NONE",
            "--cmd",
            (
                "lua vim.notify = function(message) vim.g.startup_notice = message end; "
                'vim.api.nvim_create_autocmd("User", {pattern="ReviewEnter", '
                "callback=function() vim.g.test_review_ready=true end})"
            ),
        ]
        if headless:
            args.append("--headless")
        if file:
            args.append(file)
        previous = os.getcwd()
        try:
            os.chdir(directory)
            editor = pynvim.attach("child", argv=args)
        finally:
            os.chdir(previous)
        self.editors.append(editor)
        if not headless:
            editor.ui_attach(132, 32, rgb=True, ext_linegrid=True)
        pump(editor)
        if not headless and Path(directory, ".git").exists():
            deadline = time.monotonic() + 5
            while not editor.exec_lua(
                "return vim.g.test_review_ready or vim.g.startup_notice ~= nil"
            ):
                self.assertLess(time.monotonic(), deadline, "Review startup timed out")
                pump(editor)
        return editor

    def lua(self, editor, code):
        self.assertFalse(
            editor.api.get_mode()["blocking"], "Input must not wait on a hidden prefix"
        )
        return editor.exec_lua(STATE + code)

    def keys(self, editor, keys):
        editor.input(keys)
        pump(editor)

    def test_startup_and_lock(self):
        index = subprocess.check_output(["git", "-C", self.root, "write-tree"])
        editor = self.launch(self.root, file="plan.md")
        self.assertTrue(self.lua(editor, "return r.active() ~= nil"))
        self.assertTrue(
            self.lua(
                editor,
                "return vim.api.nvim_get_current_win() == s.new_win and not vim.bo[s.new_buf].modifiable",
            )
        )
        self.assertEqual(
            3, self.lua(editor, "return #vim.api.nvim_tabpage_list_wins(0)")
        )
        self.assertTrue(
            self.lua(
                editor, 'return require("myeditor.harness").get(s.root).last == nil'
            )
        )
        locked = self.launch(self.root)
        self.assertTrue(self.lua(locked, "return s == nil"))
        self.assertIn(
            "Another editor owns", self.lua(locked, "return vim.g.startup_notice")
        )
        self.keys(editor, " q")
        self.assertEqual(
            "plan.md",
            self.lua(
                editor, 'return vim.fn.fnamemodify(vim.api.nvim_buf_get_name(0), ":t")'
            ),
        )
        self.assertEqual(
            index, subprocess.check_output(["git", "-C", self.root, "write-tree"])
        )
        headless = self.launch(self.root, headless=True)
        self.assertTrue(
            self.lua(headless, "return s == nil and #vim.api.nvim_list_wins() == 1")
        )
        outside = self.launch(self.directory.name)
        self.assertTrue(
            self.lua(outside, "return s == nil and #vim.api.nvim_list_wins() == 2")
        )

    def test_navigation_and_layouts(self):
        path = Path(self.root, "auth.lua")
        path.write_text(
            path.read_text().replace(
                "  return nil\nend", "  return nil\nend -- changed"
            )
        )
        editor = self.launch(self.root)
        self.keys(editor, "]")
        self.assertEqual(2, self.lua(editor, "return s.selected_hunk"))
        self.keys(
            editor, "j["
        )  # Previous from inside a multi-line final hunk, not its first row.
        self.assertEqual(1, self.lua(editor, "return s.selected_hunk"))
        self.keys(editor, "3]")
        self.assertEqual("plan.md", self.lua(editor, "return s.current.path"))
        self.keys(editor, "]")
        self.assertEqual(
            ["auth.lua", 1],
            self.lua(editor, "return {s.current.path, s.selected_hunk}"),
        )
        self.keys(editor, "[")
        self.assertEqual("plan.md", self.lua(editor, "return s.current.path"))
        self.keys(editor, "[")
        self.assertEqual("removed.lua", self.lua(editor, "return s.current.path"))
        self.keys(editor, "[")
        self.assertEqual(
            ["auth.lua", 2],
            self.lua(editor, "return {s.current.path, s.selected_hunk}"),
        )
        snapshot = self.lua(editor, "return s.current.id")
        reviewed_lines = self.lua(
            editor, "return vim.api.nvim_buf_get_lines(s.new_buf,0,-1,false)"
        )
        path.write_text(path.read_text() + "-- newer worktree edit, not refreshed\n")
        index = subprocess.check_output(["git", "-C", self.root, "write-tree"])
        self.keys(editor, ":view merged<CR>")
        self.assertEqual(
            ["merged", 2, 2],
            self.lua(
                editor,
                "return {s.layout, #vim.api.nvim_tabpage_list_wins(0), s.selected_hunk}",
            ),
        )
        self.assertEqual(snapshot, self.lua(editor, "return s.current.id"))
        self.assertEqual(
            reviewed_lines,
            self.lua(editor, "return vim.api.nvim_buf_get_lines(s.new_buf,0,-1,false)"),
        )
        self.assertTrue(
            self.lua(
                editor,
                'return #vim.api.nvim_buf_get_extmarks(s.new_buf, vim.api.nvim_get_namespaces()["codediff-inline"], 0, -1, {}) > 0',
            )
        )
        self.keys(editor, "iKeep this note<Esc>:w<CR>")
        self.assertEqual(
            "Keep this note", self.lua(editor, "return s.comments[1].text")
        )
        self.keys(editor, ":view<CR>")
        self.assertEqual(
            ["split", 3, 1],
            self.lua(
                editor,
                "return {s.layout, #vim.api.nvim_tabpage_list_wins(0), #s.comments}",
            ),
        )
        self.assertTrue(
            self.lua(
                editor,
                'return #vim.api.nvim_buf_get_extmarks(s.new_buf, vim.api.nvim_get_namespaces()["codediff-inline"], 0, -1, {}) == 0',
            )
        )
        self.keys(editor, ":view merged<CR>:view split<CR>")
        self.assertEqual("split", self.lua(editor, "return s.layout"))
        self.assertEqual(
            index, subprocess.check_output(["git", "-C", self.root, "write-tree"])
        )

    def test_hunks_stay_in_git_group(self):
        # Only the disposable fixture is staged; auth has two hunks, removed one.
        subprocess.run(
            ["git", "-C", self.root, "add", "auth.lua", "removed.lua"], check=True
        )
        path = Path(self.root, "auth.lua")
        path.write_text(path.read_text().replace("M.timeout = 30", "M.timeout = 60"))
        index = subprocess.check_output(["git", "-C", self.root, "write-tree"])
        editor = self.launch(self.root)

        def position(group, path, hunk, pane="tree_win"):
            self.assertEqual(
                [group, path, hunk, True],
                self.lua(
                    editor,
                    "return {s.current.group, s.current.path, s.selected_hunk, "
                    f"vim.api.nvim_get_current_win() == s.{pane}}}",
                ),
            )

        self.keys(editor, " e:fs<CR>")
        position("staged", "auth.lua", 1)
        self.keys(editor, "[")
        position("staged", "removed.lua", 1)
        self.keys(editor, "]")
        position("staged", "auth.lua", 1)
        self.keys(editor, "4]")
        position("staged", "auth.lua", 2)
        self.keys(editor, "5[")
        position("staged", "removed.lua", 1)
        self.keys(editor, ":fm<CR>")
        position("unstaged", "auth.lua", 1)
        self.keys(editor, "[")
        position("untracked", "plan.md", 1)
        self.keys(editor, "3]")
        position("unstaged", "auth.lua", 1)

        self.lua(editor, "vim.api.nvim_set_current_win(s.old_win)")
        self.keys(editor, ":focus staged<CR>")
        position("staged", "auth.lua", 1, "old_win")
        self.keys(editor, "[")
        position("staged", "removed.lua", 1, "old_win")
        self.keys(editor, ":focus modified<CR>")
        position("unstaged", "auth.lua", 1, "old_win")
        self.assertEqual(["staged"], editor.funcs.getcompletion("Focus st", "cmdline"))

        # Hide every tracked entry: modified now means the single untracked file.
        self.lua(
            editor,
            "for i, entry in ipairs(s.entries) do "
            "if entry.group ~= 'untracked' then r.show(i); r.toggle_reviewed() end end; "
            "r.toggle_unreviewed(); vim.api.nvim_set_current_win(s.tree_win)",
        )
        self.keys(editor, ":fm<CR>4]3[")
        position("untracked", "plan.md", 1)
        self.keys(editor, ":fs<CR>")
        position("untracked", "plan.md", 1)
        self.assertEqual(
            "No visible STAGED files", self.lua(editor, "return vim.g.startup_notice")
        )
        self.keys(editor, ":view merged<CR>2]2[")
        position("untracked", "plan.md", 1)
        self.assertEqual(
            index, subprocess.check_output(["git", "-C", self.root, "write-tree"])
        )

    def test_empty_review_layouts(self):
        root = Path(self.directory.name, "empty")
        subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
        editor = self.launch(str(root))
        self.assertTrue(
            self.lua(editor, "return r.active() ~= nil and #s.entries == 0")
        )
        self.keys(editor, ":fs<CR>")
        self.assertEqual(
            "No visible STAGED files", self.lua(editor, "return vim.g.startup_notice")
        )
        self.keys(editor, ":focus modified<CR>")
        self.assertEqual(
            "No visible UNSTAGED files", self.lua(editor, "return vim.g.startup_notice")
        )
        self.keys(editor, " e][")
        self.assertEqual(
            "No hunks in this Git group",
            self.lua(editor, "return vim.g.startup_notice"),
        )
        self.assertTrue(
            self.lua(editor, "return vim.api.nvim_get_current_win() == s.tree_win")
        )
        self.keys(editor, ":view merged<CR>")
        self.assertEqual(
            2, self.lua(editor, "return #vim.api.nvim_tabpage_list_wins(0)")
        )
        self.keys(editor, ":view split<CR>")
        self.assertEqual(
            3, self.lua(editor, "return #vim.api.nvim_tabpage_list_wins(0)")
        )

    def test_tree_staging_advances_and_combines_untracked(self):
        editor = self.launch(self.root)
        self.keys(editor, " e")
        for expected in ("removed.lua", "plan.md"):
            self.keys(editor, " S")
            self.assertEqual(
                expected,
                self.lua(
                    editor,
                    "return s.entries[s.rows[vim.api.nvim_win_get_cursor(s.tree_win)[1]]].path",
                ),
            )
            self.assertTrue(
                self.lua(editor, "return vim.api.nvim_get_current_win() == s.tree_win")
            )
        self.keys(editor, " S")
        self.assertEqual(
            " UNSTAGED (0)", self.lua(editor, "return vim.api.nvim_get_current_line()")
        )
        self.assertTrue(
            self.lua(
                editor,
                'return table.concat(vim.api.nvim_buf_get_lines(s.tree_buf, 0, -1, false), "\\n"):find("UNTRACKED", 1, true) == nil',
            )
        )

    def test_bright_diff_foregrounds_survive_theme_reload(self):
        editor = self.launch(self.root)
        for reload_theme in (False, True):
            if reload_theme:
                editor.command("colorscheme rose-pine")
            for name, background, foreground in (
                ("CodeDiffLineInsert", 0x28683E, 0xFFFFFF),
                ("CodeDiffLineDelete", 0xC62828, 0xFFFFFF),
                ("CodeDiffCharInsert", 0x58A46B, 0x101010),
                ("CodeDiffCharDelete", 0xFF5252, 0x101010),
            ):
                highlight = editor.api.get_hl(0, {"name": name})
                self.assertEqual(
                    (background, foreground), (highlight["bg"], highlight["fg"])
                )
                self.assertTrue(highlight["nocombine"])
        self.keys(editor, ":view merged<CR>")
        self.assertTrue(
            self.lua(
                editor,
                'return vim.wait(5000, function() return s.diff_engine=="difftastic" end)',
            )
        )
        deleted_groups = self.lua(
            editor,
            """
            local groups = {}
            for _, mark in ipairs(vim.api.nvim_buf_get_extmarks(s.new_buf,
                vim.api.nvim_get_namespaces()["codediff-inline"], 0, -1, {details=true})) do
                for _, line in ipairs(mark[4].virt_lines or {}) do
                    for _, chunk in ipairs(line) do groups[chunk[2]] = true end
                end
            end
            return vim.tbl_keys(groups)
        """,
        )
        self.assertEqual(
            {"CodeDiffLineDelete", "CodeDiffCharDelete"}, set(deleted_groups)
        )

    def test_git_detected_renames_keep_both_snapshot_paths(self):
        original = subprocess.check_output(
            ["git", "-C", self.root, "show", "HEAD:removed.lua"]
        )
        Path(self.root, "renamed.lua").write_bytes(original)
        subprocess.run(
            ["git", "-C", self.root, "add", "--", "removed.lua", "renamed.lua"],
            check=True,
        )
        editor = self.launch(self.root)
        entries = self.lua(
            editor,
            'local found={}; for _,e in ipairs(s.entries) do if e.status=="R" then table.insert(found,e.path) end end; table.sort(found); return found',
        )
        self.assertEqual(["removed.lua", "renamed.lua"], entries)

    def test_difftastic_unicode_whitespace_and_merged(self):
        path = Path(self.root, "syntax.lua")
        prefix = 'local label = "🌲"; return '
        path.write_text(prefix + "17\n")
        subprocess.run(["git", "-C", self.root, "add", "--", "syntax.lua"], check=True)
        path.write_text(prefix + "29\n")
        editor = self.launch(self.root)
        self.lua(
            editor,
            'for i,e in ipairs(s.entries) do if e.path=="syntax.lua" and e.group=="unstaged" then r.show(i); break end end',
        )
        self.assertTrue(
            self.lua(
                editor,
                'return vim.wait(5000, function() return s.diff_engine=="difftastic" end)',
            )
        )
        marks = self.lua(
            editor,
            'return vim.api.nvim_buf_get_extmarks(s.new_buf, vim.api.nvim_get_namespaces()["myeditor.difftastic"], 0, -1, {details=true})',
        )
        self.assertEqual(1, len(marks))
        self.assertEqual([0, len(prefix.encode())], marks[0][1:3])
        self.assertEqual(len(prefix.encode()) + 2, marks[0][3]["end_col"])
        self.assertEqual("CodeDiffCharInsert", marks[0][3]["hl_group"])
        self.keys(editor, ":view merged<CR>")
        self.assertTrue(
            self.lua(
                editor,
                'return vim.wait(5000, function() return s.diff_engine=="difftastic" end)',
            )
        )
        self.assertEqual(
            prefix + "29",
            self.lua(
                editor, "return vim.api.nvim_buf_get_lines(s.new_buf,0,1,false)[1]"
            ),
        )
        path.write_text('local label="🌲";return 17\n')
        self.lua(editor, "r.refresh(s.current)")
        self.assertTrue(
            self.lua(
                editor,
                'return vim.wait(5000, function() return s.diff_engine=="difftastic" end)',
            )
        )
        self.assertEqual(
            [],
            self.lua(
                editor,
                'return vim.api.nvim_buf_get_extmarks(s.new_buf, vim.api.nvim_get_namespaces()["myeditor.difftastic"], 0, -1, {})',
            ),
        )
        # A failed tool must retain the ordinary text renderer, not blank the diff.
        self.lua(
            editor,
            """
            local system = vim.system
            vim.system = function(argv, opts, callback)
                if argv[1] == "difft" then
                    vim.schedule(function() callback({code=1,stdout="invalid JSON"}) end)
                    return {kill=function() end}
                end
                return system(argv,opts,callback)
            end
            r.show(s.index)
            vim.system = system
        """,
        )
        pump(editor)
        self.assertEqual("text", self.lua(editor, "return s.diff_engine"))
        self.assertGreater(self.lua(editor, "return #s.diff.changes"), 0)


if __name__ == "__main__":
    unittest.main()
