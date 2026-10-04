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
                if editor.api.get_mode()["blocking"]:
                    editor.input("<C-c><CR>")
                editor.input("<Esc>")
                editor.command("qa!")
            except EOFError:
                pass
            finally:
                editor.close()
        os.environ.clear()
        os.environ.update(self.environment)
        self.directory.cleanup()

    def launch(self, directory, headless=False, file=None, columns=132):
        args = [
            EDITOR,
            "--embed",
            "-i",
            "NONE",
            "--cmd",
            (
                "lua _G.original_notify = vim.notify; "
                "vim.notify = function(message) vim.g.startup_notice = message end; "
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
            editor.ui_attach(columns, 32, rgb=True, ext_linegrid=True)
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

    def wait_for(self, editor, condition):
        deadline = time.monotonic() + 8
        while not self.lua(editor, "return " + condition):
            self.assertLess(time.monotonic(), deadline, condition)
            pump(editor)

    def test_startup_and_lock(self):
        index = subprocess.check_output(["git", "-C", self.root, "write-tree"])
        editor = self.launch(self.root, file="plan.md")
        self.assertTrue(self.lua(editor, "return r.active() ~= nil"))
        self.assertTrue(
            self.lua(
                editor,
                "return vim.api.nvim_get_current_win() == s.tree_win and not vim.bo[s.new_buf].modifiable",
            )
        )
        self.assertEqual("auth.lua", self.lua(editor, "return s.current.path"))
        self.assertEqual(
            Path(self.root, "auth.lua").read_text().splitlines(),
            self.lua(
                editor, "return vim.api.nvim_buf_get_lines(s.new_buf, 0, -1, false)"
            ),
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
        self.keys(editor, ":Review<CR>")
        self.assertTrue(
            self.lua(editor, "return vim.api.nvim_get_current_win() == s.tree_win")
        )
        self.keys(editor, ":ReviewLeave<CR>")
        headless = self.launch(self.root, headless=True)
        self.assertTrue(
            self.lua(headless, "return s == nil and #vim.api.nvim_list_wins() == 1")
        )
        outside = self.launch(self.directory.name)
        self.assertTrue(
            self.lua(outside, "return s == nil and #vim.api.nvim_list_wins() == 2")
        )

    def test_split_balances_on_resize(self):
        editor = self.launch(self.root, columns=80)
        self.keys(editor, "<CR>")
        self.keys(editor, "]")
        snapshot = self.lua(editor, "return s.current.id")

        def balanced():
            old, new, tree = self.lua(
                editor,
                "return {vim.api.nvim_win_get_width(s.old_win), vim.api.nvim_win_get_width(s.new_win), vim.api.nvim_win_get_width(s.tree_win)}",
            )
            self.assertLessEqual(
                abs(old - new), 1, "Diff panes must share the available width equally"
            )
            self.assertEqual(min(28, editor.options["columns"] // 5), tree)
            self.assertEqual(snapshot, self.lua(editor, "return s.current.id"))

        for width in (181, 79, 132):
            editor.ui_try_resize(width, 32)
            pump(editor)
            balanced()
            self.assertEqual(
                [True, 2],
                self.lua(
                    editor,
                    "return {vim.api.nvim_get_current_win() == s.new_win, s.selected_hunk}",
                ),
            )

        self.keys(editor, "iKeep my draft")
        height = editor.current.window.height
        editor.ui_try_resize(201, 32)
        pump(editor)
        balanced()
        self.assertTrue(
            self.lua(editor, "return vim.api.nvim_get_current_win() == s.composer_win")
        )
        self.assertEqual(height, editor.current.window.height)
        self.assertEqual(["Keep my draft"], editor.current.buffer[:])
        self.assertEqual("i", editor.api.get_mode()["mode"])
        self.keys(editor, "<Esc>:q<CR>:view merged<CR>")
        editor.ui_try_resize(100, 32)
        pump(editor)
        self.assertEqual("merged", self.lua(editor, "return s.layout"))
        self.assertEqual("", editor.eval("v:errmsg"))
        self.keys(editor, ":view split<CR>")
        balanced()
        # Resizing another tab must not steal focus back to Review.
        self.lua(editor, "vim.api.nvim_set_current_tabpage(s.previous_tab)")
        editor.ui_try_resize(180, 32)
        pump(editor)
        balanced()
        self.assertTrue(
            self.lua(
                editor, "return vim.api.nvim_get_current_tabpage() == s.previous_tab"
            )
        )

    def test_tree_row_selection(self):
        editor = self.launch(self.root)
        self.keys(editor, " d")
        cursor = editor.options["guicursor"]
        self.keys(editor, " e")
        self.assertTrue(self.lua(editor, "return vim.wo[s.tree_win].cursorline"))
        self.assertEqual(
            "line", self.lua(editor, "return vim.wo[s.tree_win].cursorlineopt")
        )
        self.assertIn(
            "ReviewTreeSelection",
            self.lua(editor, "return vim.wo[s.tree_win].winhighlight"),
        )
        self.assertIn("n:ver1-ReviewTreeCursor", editor.options["guicursor"])
        row = editor.current.window.cursor[0]
        self.keys(editor, "j")
        self.keys(editor, "4l")
        self.assertEqual((row + 1, 0), editor.current.window.cursor)
        self.assertEqual("removed.lua", self.lua(editor, "return s.current.path"))
        self.assertEqual(
            ["local obsolete = true", "return obsolete"],
            self.lua(
                editor, "return vim.api.nvim_buf_get_lines(s.old_buf, 0, -1, false)"
            ),
        )
        self.assertEqual(
            [""],
            self.lua(
                editor, "return vim.api.nvim_buf_get_lines(s.new_buf, 0, -1, false)"
            ),
        )
        self.keys(editor, ":view merged<CR>")
        self.keys(editor, "j")
        self.assertEqual("plan.md", self.lua(editor, "return s.current.path"))
        self.assertEqual(
            Path(self.root, "plan.md").read_text().splitlines(),
            self.lua(
                editor, "return vim.api.nvim_buf_get_lines(s.new_buf, 0, -1, false)"
            ),
        )
        self.assertEqual(
            [row + 1],
            self.lua(
                editor,
                'local rows = {}; for _, mark in ipairs(vim.api.nvim_buf_get_extmarks(s.tree_buf, vim.api.nvim_get_namespaces()["myeditor.active-file"], 0, -1, {})) do table.insert(rows, mark[2]) end; return rows',
            ),
        )
        self.assertTrue(
            self.lua(editor, "return vim.api.nvim_get_current_win() == s.tree_win")
        )
        # Headings are not files, and must not trigger a snapshot read or an error.
        self.keys(editor, "gg")
        self.assertEqual("plan.md", self.lua(editor, "return s.current.path"))
        self.keys(editor, f"{row}G")
        self.assertEqual("auth.lua", self.lua(editor, "return s.current.path"))
        self.keys(editor, ":view split<CR>")
        editor.command("colorscheme rose-pine")
        self.assertTrue(editor.api.get_hl(0, {"name": "ReviewTreeSelection"})["bold"])
        self.keys(editor, " d")
        self.assertEqual(cursor, editor.options["guicursor"])
        self.assertFalse(self.lua(editor, "return vim.wo[s.tree_win].cursorline"))
        self.keys(editor, "iDraft<Esc>")
        self.assertEqual(cursor, editor.options["guicursor"])
        self.keys(editor, ":q<CR>")
        # Empty guicursor is also a valid user setting; restore it exactly.
        editor.options["guicursor"] = ""
        self.keys(editor, " e")
        self.keys(editor, " q")
        self.assertEqual("", editor.options["guicursor"])
        self.assertTrue(self.lua(editor, "return s == nil"))

    def test_enter_focuses_diff_for_hunk_staging(self):
        editor = self.launch(self.root)
        baseline = subprocess.check_output(
            ["git", "-C", self.root, "show", "HEAD:auth.lua"]
        )
        changed = Path(self.root, "auth.lua").read_bytes()
        for layout in ("split", "merged"):
            self.keys(editor, f":view {layout}<CR>")
            self.keys(editor, "]")
            self.keys(editor, " e")
            self.keys(editor, "<CR>")
            self.assertEqual(
                [True, "table", 2],
                self.lua(
                    editor,
                    "return {vim.api.nvim_get_current_win() == s.new_win, type(s.current), s.selected_hunk}",
                ),
            )
            self.assertEqual("", editor.eval("v:errmsg"))
            self.assertTrue(
                self.lua(
                    editor,
                    "return s.composer == nil and not vim.bo[s.new_buf].modifiable",
                )
            )
            self.keys(editor, "s")
            self.assertEqual(
                baseline.replace(b"  return token\n", b"  return nil\n"),
                subprocess.check_output(["git", "-C", self.root, "show", ":auth.lua"]),
            )
            self.keys(editor, ":fs<CR>")
            self.keys(editor, " e")
            self.keys(editor, "<CR>")
            self.keys(editor, "s")
            self.assertEqual(
                baseline,
                subprocess.check_output(["git", "-C", self.root, "show", ":auth.lua"]),
            )
            self.assertEqual(changed, Path(self.root, "auth.lua").read_bytes())
        # Queued movement + Enter can run before CursorMoved previews that row.
        self.keys(editor, "<Tab>j<CR>")
        self.assertEqual(
            [True, "removed.lua"],
            self.lua(
                editor,
                "return {vim.api.nvim_get_current_win() == s.new_win, s.current.path}",
            ),
        )

    def test_lowercase_s_toggles_only_current_hunk(self):
        editor = self.launch(self.root)
        self.keys(editor, "<CR>")
        changed = Path(self.root, "auth.lua").read_bytes()
        self.keys(editor, "S")
        self.assertEqual("staged", self.lua(editor, "return s.current.group"))
        self.keys(editor, "s")
        # The first hunk returns to HEAD while the second stays staged.
        partial = changed.replace(b"    return true\n", b"    return false\n")
        self.assertEqual(
            partial,
            subprocess.check_output(["git", "-C", self.root, "show", ":auth.lua"]),
        )
        self.keys(editor, ":fm<CR>")
        self.keys(editor, "s")
        self.assertEqual(
            changed,
            subprocess.check_output(["git", "-C", self.root, "show", ":auth.lua"]),
        )
        self.assertEqual(changed, Path(self.root, "auth.lua").read_bytes())
        self.assertTrue(
            self.lua(
                editor, "return s.composer == nil and not vim.bo[s.new_buf].modifiable"
            )
        )

    def test_uppercase_s_toggles_entire_file(self):
        editor = self.launch(self.root)
        self.assertTrue(
            self.lua(
                editor,
                'for _, buf in ipairs({s.tree_buf, s.old_buf, s.new_buf}) do for _, map in ipairs(vim.api.nvim_buf_get_keymap(buf, "n")) do if map.lhs == " S" or map.lhs == " s" then return false end end end; return true',
            )
        )
        baseline = subprocess.check_output(
            ["git", "-C", self.root, "show", "HEAD:auth.lua"]
        )
        changed = Path(self.root, "auth.lua").read_bytes()
        self.assertEqual(2, self.lua(editor, "return #s.diff.changes"))
        # Both old/new panes, both layouts, and mappings after refresh.
        for layout, pane in (("split", "new"), ("split", "old"), ("merged", "new")):
            self.keys(editor, f":view {layout}<CR>")
            self.lua(editor, f"vim.api.nvim_set_current_win(s.{pane}_win)")
            self.keys(editor, " R")
            self.keys(editor, "S")
            self.assertEqual(
                changed,
                subprocess.check_output(["git", "-C", self.root, "show", ":auth.lua"]),
            )
            self.assertEqual("staged", self.lua(editor, "return s.current.group"))
            self.keys(editor, "S")
            self.assertEqual(
                baseline,
                subprocess.check_output(["git", "-C", self.root, "show", ":auth.lua"]),
            )
            self.assertEqual(changed, Path(self.root, "auth.lua").read_bytes())
            self.assertTrue(
                self.lua(
                    editor,
                    "return s.composer == nil and not vim.bo[s.new_buf].modifiable",
                )
            )
        # Switching Lua -> Markdown runs FileType handlers on the same buffer.
        self.lua(
            editor,
            'for i, entry in ipairs(s.entries) do if entry.path == "plan.md" then r.show(i); break end end',
        )
        self.keys(editor, "S")
        self.assertEqual(
            Path(self.root, "plan.md").read_bytes(),
            subprocess.check_output(["git", "-C", self.root, "show", ":plan.md"]),
        )
        self.keys(editor, ":fs<CR>")
        self.assertEqual("plan.md", self.lua(editor, "return s.current.path"))
        self.keys(editor, "S")
        self.assertEqual(
            b"",
            subprocess.check_output(
                ["git", "-C", self.root, "ls-files", "--", "plan.md"]
            ),
        )
        self.assertTrue(
            self.lua(
                editor, "return s.composer == nil and not vim.bo[s.new_buf].modifiable"
            )
        )

    def test_navigation_and_layouts(self):
        path = Path(self.root, "auth.lua")
        path.write_text(
            path.read_text().replace(
                "  return nil\nend", "  return nil\nend -- changed"
            )
        )
        editor = self.launch(self.root)
        self.keys(editor, "<CR>")
        # Layout changes themselves must use the current snapshot, not read disk.
        self.lua(editor, 'require("myeditor.live").stop(s)')
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

    def test_message_tab_returns_to_review_without_sending(self):
        editor = self.launch(self.root)
        self.keys(editor, ":harness send<CR>iDraft<Tab>message<Esc>")
        message = editor.current.buffer
        draft = message[:]
        self.assertRegex(draft[0], r"^Draft\s+message$")
        self.assertTrue(message.name.startswith("harness://"))
        self.keys(editor, "<Tab>")
        self.assertTrue(
            self.lua(editor, "return vim.api.nvim_get_current_win() == s.tree_win")
        )
        self.keys(editor, "<Tab>")
        self.assertTrue(
            self.lua(editor, "return vim.api.nvim_get_current_win() == s.new_win")
        )
        self.keys(editor, "<Tab>")
        self.assertTrue(
            self.lua(editor, "return vim.api.nvim_get_current_win() == s.tree_win")
        )
        self.keys(editor, ":harness send<CR>")
        self.assertEqual(message.number, editor.current.buffer.number)
        self.assertEqual(draft, message[:])
        self.assertTrue(
            self.lua(
                editor, 'return require("myeditor.harness").get(s.root).last == nil'
            )
        )
        self.keys(editor, "<Tab>")
        self.assertTrue(
            self.lua(editor, "return vim.api.nvim_get_current_win() == s.tree_win")
        )

    def test_successful_message_clears_without_enter_prompt(self):
        editor = self.launch(self.root)
        self.lua(
            editor,
            """
            local feedback = require('myeditor.feedback')
            feedback.settings = function() return {feedback_command={'fake-receiver'}} end
            require('myeditor.harness').select(s.root, {name='custom'})
            local system = vim.system
            vim.system = function(argv, opts, callback)
                if argv[1] ~= 'fake-receiver' then return system(argv, opts, callback) end
                local payload = feedback.read(argv[#argv])
                vim.schedule(function()
                    callback({code=0, stdout=vim.json.encode({
                        submission_id=payload.submission_id, status='accepted'
                    })})
                end)
            end
            vim.notify = original_notify
        """,
        )
        self.keys(editor, ":harness send<CR>iSteer the agent<Esc>:w<CR>")
        self.wait_for(
            editor, 'require("myeditor.harness").get(s.root).delivery == "accepted"'
        )
        self.assertEqual([""], editor.current.buffer[:])
        self.keys(editor, "iNext message<Esc>")
        self.assertEqual(["Next message"], editor.current.buffer[:])

    def test_branch_and_manual_refresh(self):
        editor = self.launch(self.root)
        self.assertIn(" REVIEW · main · ", self.lua(editor, "return r.statusline()"))
        # Branch identity updates even with no changed-file status transition.
        subprocess.run(
            ["git", "-C", self.root, "checkout", "-b", "topic%branch"],
            check=True,
            capture_output=True,
        )
        self.wait_for(editor, 's.reference == "topic%branch"')
        self.assertIn(
            "topic%branch",
            editor.api.eval_statusline(editor.options["statusline"], {})["str"],
        )
        subprocess.run(
            ["git", "-C", self.root, "checkout", "--detach"],
            check=True,
            capture_output=True,
        )
        sha = subprocess.check_output(
            ["git", "-C", self.root, "rev-parse", "--short", "HEAD"], text=True
        ).strip()
        self.wait_for(editor, 's.reference:sub(1,1) == "@"')
        self.assertIn(
            "@" + sha,
            editor.api.eval_statusline(editor.options["statusline"], {})["str"],
        )
        # The manual shortcut works independently of the polling timer.
        self.lua(editor, 'require("myeditor.live").stop(s)')
        path = Path(self.root, "auth.lua")
        path.write_text(path.read_text().replace("return nil", "return 321"))
        self.keys(editor, " R")
        self.assertIn("return 321", self.lua(editor, "return s.current.new"))

    def test_live_refresh_preserves_focus_and_annotations(self):
        editor = self.launch(self.root)
        index = subprocess.check_output(["git", "-C", self.root, "write-tree"])
        path = Path(self.root, "auth.lua")
        self.keys(editor, "]")
        self.keys(editor, " e")
        self.assertEqual(2, self.lua(editor, "return s.selected_hunk"))
        path.write_text(path.read_text().replace("return nil", "return 123"))
        self.wait_for(editor, 's.current.new:find("return 123", 1, true) ~= nil')
        self.assertEqual(
            [17, 2, True],
            self.lua(
                editor,
                "return {vim.api.nvim_win_get_cursor(s.new_win)[1], "
                "s.selected_hunk, vim.api.nvim_get_current_win() == s.tree_win}",
            ),
        )
        self.keys(editor, " diKeep the earlier snapshot<Esc>")
        snapshot = self.lua(editor, "return s.current.id")
        path.write_text(path.read_text().replace("return 123", "return 456"))
        time.sleep(1.2)
        pump(editor)
        self.assertEqual(snapshot, self.lua(editor, "return s.current.id"))
        self.keys(editor, ":w<CR>")
        self.wait_for(editor, 's.current.new:find("return 456", 1, true) ~= nil')
        self.assertEqual(
            [snapshot, "Keep the earlier snapshot", True],
            self.lua(
                editor,
                "return {s.comments[1].snapshot_id, s.comments[1].text, "
                "s.snapshots[s.comments[1].snapshot_id].new:find('return 123', 1, true) ~= nil}",
            ),
        )
        # An untracked filename with whitespace must not break status parsing.
        Path(self.root, "new file.txt").write_text("one\n")
        self.wait_for(editor, "#s.entries == 4")
        self.lua(
            editor,
            "for i,e in ipairs(s.entries) do if e.path == 'new file.txt' then r.show(i) end end",
        )
        Path(self.root, "new file.txt").write_text("two\n")
        self.wait_for(editor, 's.current.new == "two\\n"')
        Path(self.root, "new file.txt").unlink()
        self.wait_for(editor, "#s.entries == 3")
        self.assertEqual(
            index, subprocess.check_output(["git", "-C", self.root, "write-tree"])
        )

        # Index object IDs change even when the status letters stay the same.
        subprocess.run(["git", "-C", self.root, "add", "auth.lua"], check=True)
        self.wait_for(editor, 's.entries[1].group == "staged"')
        self.keys(editor, ":fs<CR>")
        path.write_text(path.read_text().replace("return 456", "return 789"))
        subprocess.run(["git", "-C", self.root, "add", "auth.lua"], check=True)
        self.wait_for(
            editor,
            's.current.group == "staged" and s.current.new:find("return 789", 1, true) ~= nil',
        )
        # Deterministically change selection during a Git read. Late refresh must
        # not overwrite a file the user opened while the read was running.
        self.assertEqual(
            [False, "plan.md"],
            self.lua(
                editor,
                """
                require('myeditor.live').stop(s)
                local git = require('myeditor.git')
                local snapshot = git.snapshot
                git.snapshot = function(...)
                    git.snapshot = snapshot
                    r.show(#s.entries)
                    return snapshot(...)
                end
                local applied = r.refresh_live()
                git.snapshot = snapshot
                return {applied, s.current.path}
            """,
            ),
        )
        self.lua(editor, "_G.left_review = s")
        self.keys(editor, " q")
        self.assertTrue(
            self.lua(editor, "return s == nil and left_review.live_refresh == nil")
        )

    def test_worktrees_and_selected_harness(self):
        binary = Path(self.directory.name, "bin")
        binary.mkdir()
        marker = Path(self.directory.name, "launch.log")
        for name in ("amp", "claude"):
            executable = binary / name
            executable.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$0" "$PWD" "$#" >> "$HARNESS_TEST_LOG"\n'
                'printf "Fake harness ready\\n"\n'
            )
            executable.chmod(0o755)
        os.environ["PATH"] = str(binary) + os.pathsep + os.environ["PATH"]
        os.environ["HARNESS_TEST_LOG"] = str(marker)
        editor = self.launch(self.root, file="auth.lua")
        self.keys(editor, "<CR>")
        index = subprocess.check_output(["git", "-C", self.root, "write-tree"])
        self.keys(editor, ":harness use amp<CR>")
        self.assertEqual(
            "amp",
            self.lua(editor, 'return require("myeditor.harness").get(s.root).provider'),
        )
        self.assertFalse(marker.exists(), "Selecting a type must not launch a process")
        self.lua(
            editor,
            """
            local h = require('myeditor.harness')
            h.select(s.root, {name='amp-live',session='T-parent',connection='/fake/parent.json'})
            _G.original = s
            _G.edit_buf = vim.api.nvim_win_get_buf(vim.api.nvim_tabpage_get_win(s.previous_tab))
            vim.api.nvim_buf_set_lines(edit_buf, 0, 1, false, {'-- unsaved original buffer'})
        """,
        )
        self.keys(editor, "iParent note<Esc>:w<CR>")
        self.keys(editor, ":worktree new topic<CR>")
        target = str(Path(self.root + "-topic").resolve())
        self.wait_for(editor, "vim.fn.filereadable(vim.env.HARNESS_TEST_LOG) == 1")
        self.assertEqual(
            [str(binary / "amp"), target, "0"], marker.read_text().splitlines()
        )
        self.assertEqual(
            [target, "amp", "none", 0, True],
            self.lua(
                editor,
                "local h=require('myeditor.harness').get(s.root); "
                "return {s.root,h.provider,h.target.name,#s.comments,original.live_refresh == nil}",
            ),
        )
        self.assertEqual("terminal", editor.current.buffer.options["buftype"])
        self.keys(editor, "<C-\\><C-n>:worktree switch main<CR>")
        self.assertEqual(
            [self.root, "Parent note", "T-parent", True],
            self.lua(
                editor,
                "return {s.root,s.comments[1].text,s.harness.session,vim.bo[edit_buf].modified}",
            ),
        )
        self.assertEqual(
            3,
            len(marker.read_text().splitlines()),
            "Switching does not launch a second agent",
        )
        self.assertEqual(
            index, subprocess.check_output(["git", "-C", self.root, "write-tree"])
        )
        self.keys(editor, ":worktree list<CR>")
        self.keys(editor, "2<CR>")
        self.assertEqual(target, self.lua(editor, "return s.root"))
        self.keys(editor, ":worktree switch<CR>")
        self.keys(editor, "1<CR>")
        self.assertEqual(self.root, self.lua(editor, "return s.root"))
        # Changing provider clears an incompatible feedback connection, not drafts.
        self.keys(editor, ":harness use claude<CR>")
        self.assertEqual(
            ["claude", "none", 1],
            self.lua(
                editor,
                "local h=require('myeditor.harness').get(s.root); return {h.provider,h.target.name,#s.comments}",
            ),
        )
        restarted = self.launch(self.root, headless=True)
        self.assertEqual(
            "claude",
            self.lua(
                restarted,
                'return require("myeditor.harness").get(require("myeditor.git").root()).provider',
            ),
        )
        # Missing CLI and invalid branch must fail before any worktree is created.
        self.assertFalse(
            self.lua(
                editor,
                """
            local original = vim.fn.exepath
            vim.fn.exepath = function() return '' end
            local ok = pcall(require('myeditor.worktree').new, 'missing-cli')
            vim.fn.exepath = original
            return ok
        """,
            )
        )
        self.assertFalse(Path(self.root + "-missing-cli").exists())
        self.assertFalse(
            self.lua(editor, "return pcall(require('myeditor.worktree').new, '--bad')")
        )
        self.assertEqual(
            2, self.lua(editor, "return #require('myeditor.worktree').list()")
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
            self.assertTrue(
                self.lua(
                    editor,
                    """
                    local row = vim.api.nvim_win_get_cursor(s.tree_win)[1]
                    local marks = vim.api.nvim_buf_get_extmarks(s.tree_buf,
                        vim.api.nvim_get_namespaces()['myeditor.active-file'], 0, -1, {})
                    return s.rows[row] == s.index and #marks == 1 and marks[1][2] == row - 1
                    """,
                ),
                "Sidebar cursor and sole file highlight must follow the selected hunk",
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
        self.keys(editor, ":focus st<Tab><CR>")
        position("staged", "auth.lua", 1, "old_win")
        self.lua(editor, "vim.api.nvim_set_current_win(s.new_win)")
        self.keys(editor, "2]")
        position("staged", "removed.lua", 1, "new_win")
        self.keys(editor, "[")
        position("staged", "auth.lua", 2, "new_win")

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
            self.keys(editor, "S")
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
        self.keys(editor, "S")
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
