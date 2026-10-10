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

EDITOR = shutil.which("rediff")
FIXTURE = str(Path(__file__).with_name("fixture.lua"))
STATE = 'local r = require("rediff.review"); local s = r.state; '


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

    def launch(
        self, directory, headless=False, file=None, columns=132, before_init=None
    ):
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
        if before_init:
            args.extend(["--cmd", "lua " + before_init])
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
        if not headless and not file and Path(directory, ".git").exists():
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

    def wait_for(self, editor, condition, timeout=8):
        deadline = time.monotonic() + timeout
        while not self.lua(editor, "return " + condition):
            self.assertLess(time.monotonic(), deadline, condition)
            pump(editor)

    def test_ctrl_shift_brackets_skip_seen_and_baseline_hunks(self):
        path = Path(self.root, "zeta.txt")
        lines = [f"line {i}\n" for i in range(1, 61)]
        path.write_text("".join(lines))
        gone = Path(self.root, "gone.txt")
        gone.write_text("delete this file\n")
        for args in (
            ["add", "zeta.txt", "gone.txt"],
            ["commit", "-m", "Navigation fixture"],
        ):
            subprocess.run(
                ["git", "-C", self.root, *args], check=True, capture_output=True
            )
        lines[44] = "baseline edit\n"
        path.write_text("".join(lines))
        editor = self.launch(self.root)
        editor.ui_try_resize(132, 14)
        self.wait_for(editor, "not require('rediff.awareness').get(s.root).scanning")
        self.lua(editor, "require('rediff.live').stop(s)")
        lines[4], lines[24] = "first new edit\n", "second new edit\n"
        path.write_text("".join(lines))
        gone.unlink()
        Path(self.root, "staged.txt").write_text("unseen in another Git group\n")
        Path(self.root, "unseen-new.txt").write_text("unseen untracked addition\n")
        subprocess.run(["git", "-C", self.root, "add", "staged.txt"], check=True)
        self.lua(editor, "r.refresh()")
        self.wait_for(editor, "not require('rediff.awareness').get(s.root).scanning")
        show_zeta = "for i,e in ipairs(s.entries) do if e.path=='zeta.txt' then r.show(i); break end end"
        self.lua(editor, show_zeta)
        self.wait_for(
            editor,
            "(function() local a=require('rediff.awareness'); local t=a.get(s.root); "
            "local f='unstaged\\0zeta.txt'; return a.hunk_state(t,f,t.current[f].hunks[1].id)=='seen' end)()",
        )
        # Freeze exposure after genuinely viewing the first hunk; navigation must
        # distinguish it from both the unseen second hunk and baseline third hunk.
        self.lua(editor, "require('rediff.awareness').stop()")
        for view, pane in (("split", "new"), ("split", "old"), ("merged", "tree")):
            self.lua(
                editor,
                f"r.view('{view}'); {show_zeta}; vim.api.nvim_set_current_win(s.{pane}_win)",
            )
            for keys, expected in (
                ("<C-S-]>", ["zeta.txt", 2]),
                ("<C-S-]>", ["unseen-new.txt", 1]),
                ("<C-S-]>", ["gone.txt", 1]),
                ("<C-S-[>", ["unseen-new.txt", 1]),
                ("<C-S-[>", ["zeta.txt", 2]),
                ("<C-S-[>", ["gone.txt", 1]),
                ("<C-S-]>", ["zeta.txt", 2]),
                ("3<C-S-[>", ["zeta.txt", 2]),
            ):
                with self.subTest(view=view, pane=pane, keys=keys, expected=expected):
                    self.keys(editor, keys)
                    self.assertEqual(
                        expected,
                        self.lua(editor, "return {s.current.path,s.selected_hunk}"),
                    )
                    self.assertTrue(
                        self.lua(
                            editor,
                            f"return vim.api.nvim_get_current_win()==s.{pane}_win",
                        )
                    )
        # A fully acknowledged baseline has no unseen targets and must not move.
        self.lua(
            editor,
            "local a=require('rediff.awareness'); a.accept(s.root,vim.deepcopy(a.get(s.root).current))",
        )
        self.wait_for(editor, "not require('rediff.awareness').get(s.root).scanning")
        before = self.lua(
            editor,
            "return {s.current.id,s.selected_hunk,vim.api.nvim_win_get_cursor(s.new_win)}",
        )
        self.keys(editor, "<C-S-]>")
        self.assertEqual(
            before,
            self.lua(
                editor,
                "return {s.current.id,s.selected_hunk,vim.api.nvim_win_get_cursor(s.new_win)}",
            ),
        )
        self.assertEqual(
            "No unseen hunks in this Git group",
            self.lua(editor, "return vim.g.startup_notice"),
        )
        # The original bracket keys still visit baseline hunks.
        self.keys(editor, "]")
        self.assertEqual(
            ["zeta.txt", 3], self.lua(editor, "return {s.current.path,s.selected_hunk}")
        )

    def test_change_awareness_tracks_visible_hunks_not_tree_previews(self):
        editor = self.launch(self.root)
        editor.ui_try_resize(132, 14)
        pump(editor)
        self.wait_for(editor, "not require('rediff.awareness').get(s.root).scanning")
        self.lua(editor, "require('rediff.live').stop(s)")
        path = Path(self.root, "auth.lua")
        original = path.read_text()
        # The second hunk is below this viewport; previewing the first is not enough.
        path.write_text(
            original.replace("return true", "return 'first edit'").replace(
                "  return nil", "  return 'second edit'"
            )
        )
        self.lua(editor, "r.refresh(); vim.api.nvim_set_current_win(s.tree_win)")
        self.wait_for(editor, "not require('rediff.awareness').get(s.root).scanning")
        state = "require('rediff.awareness').file_state(require('rediff.awareness').get(s.root), 'unstaged\\0auth.lua')"
        self.assertEqual("unseen", self.lua(editor, "return " + state))
        time.sleep(1.4)
        pump(editor)
        self.assertEqual(
            "unseen", self.lua(editor, "return " + state), "Preview is not exposure"
        )
        self.keys(editor, "<Tab>")
        self.wait_for(
            editor,
            "(function() local a=require('rediff.awareness'); local t=a.get(s.root); "
            "return a.hunk_state(t, 'unstaged\\0auth.lua', t.current['unstaged\\0auth.lua'].hunks[1].id)=='seen' end)()",
        )
        self.assertEqual(
            "unseen",
            self.lua(editor, "return " + state),
            "Offscreen hunk keeps file unseen",
        )
        self.keys(editor, "]")
        self.wait_for(editor, state + " == 'seen'")
        self.lua(editor, "r.view('merged')")
        self.assertEqual(
            "seen",
            self.lua(editor, "return " + state),
            "Layout changes retain exposure",
        )
        self.lua(editor, "vim.cmd('colorscheme rose-pine')")
        self.assertEqual(
            int("c4a7e7", 16),
            editor.api.get_hl(0, {"name": "ReviewUnseen"})["fg"],
            "Theme reload preserves violet indicators",
        )
        # A fresh edit resets only that hunk, and does not steal tree focus.
        self.lua(editor, "vim.api.nvim_set_current_win(s.tree_win)")
        path.write_text(path.read_text().replace("second edit", "third edit"))
        self.lua(editor, "r.refresh(); vim.api.nvim_set_current_win(s.tree_win)")
        self.wait_for(editor, "not require('rediff.awareness').get(s.root).scanning")
        self.assertEqual("unseen", self.lua(editor, "return " + state))
        marks = self.lua(
            editor,
            "return vim.api.nvim_buf_get_extmarks(s.new_buf, "
            "vim.api.nvim_create_namespace('rediff.awareness'), 0, -1, {details=true})",
        )
        self.assertTrue(
            any(m[3].get("number_hl_group") == "ReviewUnseen" for m in marks)
        )
        self.assertTrue(any(m[3].get("number_hl_group") == "ReviewSeen" for m in marks))
        # Both pulse phases render without editing text or moving the cursor.
        self.lua(
            editor,
            "local a=require('rediff.awareness'); a.get(s.root).pulses['unstaged\\0auth.lua']=vim.uv.now()-300; a.render()",
        )
        tree_marks = self.lua(
            editor,
            "return vim.api.nvim_buf_get_extmarks(s.tree_buf, "
            "vim.api.nvim_create_namespace('rediff.awareness'), 0, -1, {details=true})",
        )
        self.assertEqual("ReviewUnseenPulse", tree_marks[0][3]["virt_text"][0][1])
        self.assertTrue(
            self.lua(editor, "return vim.api.nvim_get_current_win()==s.tree_win")
        )
        self.lua(editor, "r.leave(); r.open()")
        self.wait_for(editor, "not require('rediff.awareness').get(s.root).scanning")
        self.assertEqual(
            "unseen",
            self.lua(editor, "return " + state),
            "Reopening preserves this session's baseline",
        )

    def test_staging_acknowledges_only_the_affected_changes(self):
        editor = self.launch(self.root)
        self.wait_for(editor, "not require('rediff.awareness').get(s.root).scanning")
        self.lua(
            editor, "require('rediff.live').stop(s); require('rediff.awareness').stop()"
        )
        path = Path(self.root, "auth.lua")
        path.write_text(
            path.read_text()
            .replace("return true", "return 'first edit'")
            .replace("  return nil", "  return 'second edit'")
        )

        def settled():
            self.wait_for(
                editor, "not require('rediff.awareness').get(s.root).scanning"
            )

        def states(group, name="auth.lua"):
            settled()
            return editor.exec_lua(
                STATE
                + """
                local group, name = ...
                local a = require('rediff.awareness')
                local model = a.get(s.root)
                local file = group .. '\\0' .. name
                local current = model.current[file]
                local result = {a.file_state(model, file) or 'quiet'}
                for _, hunk in ipairs(current and current.hunks or {}) do
                    table.insert(result, a.hunk_state(model, file, hunk.id) or 'quiet')
                end
                return result
                """,
                group,
                name,
            )

        self.lua(editor, "r.refresh(); vim.api.nvim_set_current_win(s.tree_win)")
        self.assertEqual(["unseen", "unseen", "unseen"], states("unstaged"))
        # A pre-stage capture arriving late must not erase acknowledgements.
        self.lua(
            editor,
            "local a=require('rediff.awareness'); local capture=a.capture; "
            "a.capture=function(_,callback) a.capture=capture; _G.late_capture=callback end; "
            "a.scan(s.root)",
        )
        # Await the operation, not a fixed key-input delay: slow Git must finish
        # before the next file edit or assertion. Key mappings are tested separately.
        self.lua(editor, "vim.api.nvim_set_current_win(s.new_win); r.stage(false)")
        self.lua(editor, "late_capture({}); _G.late_capture=nil")
        self.assertEqual(["quiet", "quiet"], states("staged"))
        self.assertEqual(["unseen", "unseen"], states("unstaged"))
        self.lua(editor, "r.focus('staged')")
        self.assertEqual(
            [],
            self.lua(
                editor,
                "return vim.api.nvim_buf_get_extmarks(s.new_buf, "
                "vim.api.nvim_create_namespace('rediff.awareness'), 0, -1, {})",
            ),
            "Staged hunk has no purple or cyan awareness marks",
        )
        self.lua(editor, "r.stage(false)")
        self.assertEqual(["unseen", "quiet", "unseen"], states("unstaged"))
        self.lua(editor, "r.focus('unstaged'); r.stage(true)")
        self.assertEqual(["quiet", "quiet", "quiet"], states("staged"))
        self.lua(editor, "r.focus('staged'); r.stage(true)")
        self.assertEqual(["quiet", "quiet", "quiet"], states("unstaged"))

        # Stage a second revision of a line already changed in the index. Its
        # HEAD-to-index hunk differs from the index-to-worktree hunk just acted on.
        self.lua(editor, "r.focus('unstaged'); r.stage(false)")
        path.write_text(path.read_text().replace("first edit", "third edit"))
        self.lua(editor, "r.refresh(); r.focus('unstaged')")
        self.assertEqual(["unseen", "unseen", "quiet"], states("unstaged"))
        self.lua(editor, "r.stage(false)")
        self.assertEqual(["quiet", "quiet"], states("staged"))
        self.lua(editor, "r.focus('staged'); r.stage(false)")
        self.assertEqual(["quiet", "quiet", "quiet"], states("unstaged"))

        # A later edit is unread, even when unstage folds it into the same hunk.
        self.lua(editor, "r.focus('unstaged'); r.stage(false)")
        path.write_text(path.read_text().replace("third edit", "fourth edit"))
        self.lua(editor, "r.refresh(); r.focus('staged')")
        self.lua(editor, "r.stage(false)")
        self.assertEqual(["unseen", "unseen", "quiet"], states("unstaged"))

        # Rejected stale staging must not acknowledge the displayed hunk.
        self.lua(editor, "r.focus('unstaged')")
        path.write_text(path.read_text().replace("fourth edit", "fifth edit"))
        self.assertFalse(self.lua(editor, "return pcall(r.stage, false)"))
        self.assertEqual(["unseen", "unseen", "quiet"], states("unstaged"))

        # Untracked -> staged -> untracked uses the same acknowledgement.
        Path(self.root, "fresh.lua").write_text("return 'new file'\n")
        self.lua(
            editor,
            "r.refresh(); for i,e in ipairs(s.entries) do "
            "if e.path=='fresh.lua' then r.show(i); break end end",
        )
        self.assertEqual(["unseen", "unseen"], states("untracked", "fresh.lua"))
        self.lua(editor, "r.stage(false); r.focus('staged'); r.stage(false)")
        self.assertEqual(["quiet", "quiet"], states("untracked", "fresh.lua"))

    def test_saved_amp_connection_is_rediscovered_before_feedback(self):
        for outcome in ("renewed", "replacement", "gone", "superseded"):
            with self.subTest(outcome=outcome):
                self.fixture.exec_lua(
                    """
                    local root = ...
                    local feedback = require('rediff.feedback')
                    feedback.write(feedback.directory(root)..'/harness.json', {
                        target={name='amp-live',session='T-saved',connection='/deleted/session/connection.json'},
                        provider='amp',
                    })
                    """,
                    self.root,
                )
                editor = self.launch(
                    self.root,
                    headless=True,
                    before_init="""
                    local root = vim.fn.getcwd()
                    _G.bridge_calls = {}
                    local system = vim.system
                    vim.system = function(argv, opts, callback)
                        if argv[1] ~= 'rediff-amp-live' then return system(argv,opts,callback) end
                        table.insert(bridge_calls,argv)
                        if argv[2] == 'discover' and argv[3] == root then
                            _G.restore_reply = callback
                        elseif argv[2] == 'discover' then
                            callback({code=0,stdout='[]'})
                        elseif argv[2] == 'send' then
                            local payload = require('rediff.feedback').read(argv[#argv])
                            callback({code=0,stdout=vim.json.encode({submission_id=payload.submission_id,status='accepted'})})
                        else error('Unexpected bridge command') end
                    end
                """,
                )
                editor.exec_lua(
                    "_G.saved_session = require('rediff.harness').get(...)", self.root
                )
                self.assertTrue(editor.exec_lua("return saved_session.restoring"))
                self.assertEqual(
                    0,
                    editor.exec_lua(
                        "return #require('rediff.connections').connected()"
                    ),
                )
                self.assertIn(
                    "Checking the saved harness session",
                    editor.exec_lua(
                        "return select(2, pcall(require('rediff.harness').deliver,saved_session.root,'early','/missing',{}))"
                    ),
                )
                if outcome == "superseded":
                    editor.exec_lua(
                        "require('rediff.harness').select(saved_session.root,{name='none'})"
                    )
                editor.exec_lua(
                    """
                    local outcome = ...
                    local rows = {
                        {root=saved_session.root,session='T-another',connection='/other/connection.json'},
                        {root=saved_session.root..'/other',session='T-saved',connection='/wrong-root/connection.json'},
                    }
                    if outcome == 'gone' then
                        table.insert(rows,{root=saved_session.root,session='T-second',connection='/second/connection.json'})
                    elseif outcome ~= 'replacement' then
                        table.insert(rows,{root=saved_session.root,session='T-saved',connection='/renewed/connection.json'})
                    end
                    restore_reply({code=0,stdout=vim.json.encode(rows)})
                """,
                    outcome,
                )
                pump(editor)
                if outcome in ("renewed", "replacement"):
                    expected_connection, expected_thread = (
                        ("/renewed/connection.json", "T-saved")
                        if outcome == "renewed"
                        else ("/other/connection.json", "T-another")
                    )
                    self.assertEqual(
                        expected_connection,
                        editor.exec_lua("return saved_session.target.connection"),
                    )
                    self.assertEqual(1, editor.exec_lua("return #bridge_calls"))
                    # Same steps as Review submission, without taking the
                    # Review lock that earlier subtests' editors still hold.
                    editor.exec_lua(
                        """
                        local feedback = require('rediff.feedback')
                        local argv = feedback.command(saved_session.target)
                        local id, path = feedback.enqueue(saved_session.root,
                            {{text='Send to the selected local thread'}}, {}, argv)
                        require('rediff.harness').deliver(saved_session.root, id, path, argv)
                        """
                    )
                    pump(editor)
                    self.assertEqual(
                        [
                            "rediff-amp-live",
                            "send",
                            expected_connection,
                            expected_thread,
                        ],
                        editor.exec_lua("return vim.list_slice(bridge_calls[2],1,4)"),
                    )
                    self.assertEqual(
                        "Send to the selected local thread",
                        editor.exec_lua(
                            "return require('rediff.feedback').read(bridge_calls[2][#bridge_calls[2]]).comments[1].text"
                        ),
                    )
                elif outcome == "gone":
                    self.assertEqual(
                        "disconnected", editor.exec_lua("return saved_session.delivery")
                    )
                    self.assertFalse(
                        editor.exec_lua(
                            "return pcall(require('rediff.harness').deliver,saved_session.root,'never','/missing',{})"
                        )
                    )
                    self.assertEqual(
                        0,
                        editor.exec_lua(
                            "return #require('rediff.connections').connected()"
                        ),
                    )
                    self.assertTrue(
                        editor.exec_lua(
                            "for _,argv in ipairs(bridge_calls) do if argv[2]~='discover' then return false end end return true"
                        )
                    )
                else:
                    self.assertEqual(
                        "none", editor.exec_lua("return saved_session.target.name")
                    )
                    self.assertEqual(
                        0,
                        editor.exec_lua(
                            "return #require('rediff.connections').connected()"
                        ),
                    )

    def test_resumed_harness_reconnects_before_flushing_annotations(self):
        binary = Path(self.directory.name, "bin")
        binary.mkdir()
        cli = binary / "amp"
        cli.write_text('#!/bin/sh\necho "Resumed harness ready"\nexec sleep 600\n')
        cli.chmod(0o755)
        os.environ["PATH"] = str(binary) + os.pathsep + os.environ["PATH"]
        self.fixture.exec_lua(
            """
            local root=...
            local f=require('rediff.feedback')
            f.write(f.directory(root)..'/harness.json', {
                provider='amp',target={name='amp-live',
                    session='T-11111111-2222-4333-8444-555555555555',connection='/stale/connection.json'}})
        """,
            self.root,
        )
        editor = self.launch(
            self.root,
            before_init="""
            local system=vim.system
            local initial=true
            vim.system=function(argv,opts,callback)
                if argv[1]~='rediff-amp-live' then return system(argv,opts,callback) end
                if argv[2]=='discover' then
                    if initial then initial=false; callback({code=0,stdout='[]'})
                    else _G.reconnect_reply=callback end
                elseif argv[2]=='startup' then
                    callback({code=0,stdout=vim.json.encode({
                        action='resume',session='T-11111111-2222-4333-8444-555555555555'})})
                elseif argv[2]=='send' then
                    vim.g.sent_argv=argv
                    _G.send_reply=callback
                else error('Unexpected bridge command') end
            end
        """,
        )
        self.wait_for(editor, "vim.bo.buftype == 'terminal'")
        self.wait_for(editor, "reconnect_reply ~= nil")
        self.keys(editor, "<Esc>")
        self.lua(
            editor,
            "vim.api.nvim_set_current_win(s.new_win); vim.api.nvim_win_set_cursor(0,{5,0}); r.compose()",
        )
        self.keys(editor, "<Esc>iKeep this annotation until accepted<Esc>:w<CR>")
        self.wait_for(editor, "s.composer == nil")
        self.keys(editor, ":w<CR>")
        self.assertFalse(editor.api.get_mode()["blocking"])
        self.assertIn("Waiting for the resumed amp", editor.vars["startup_notice"])
        self.assertEqual(1, self.lua(editor, "return #s.comments"))
        self.assertIsNone(self.lua(editor, "return s.last_submission"))
        # Neither the original endpoint nor another thread/worktree may win.
        self.lua(
            editor,
            """
            reconnect_reply({code=0,stdout=vim.json.encode({
                {root=s.root,session=s.harness.session,connection='/stale/connection.json'},
                {root=s.root,session='T-other',connection='/other/thread'},
                {root=s.root..'/other',session=s.harness.session,connection='/other/worktree'},
            })}); reconnect_reply=nil
        """,
        )
        self.wait_for(editor, "reconnect_reply ~= nil")
        self.assertEqual(
            "/stale/connection.json", self.lua(editor, "return s.harness.connection")
        )
        self.assertIsNone(editor.vars.get("sent_argv"))
        self.lua(
            editor,
            """
            reconnect_reply({code=0,stdout=vim.json.encode({{
                root=s.root,session=s.harness.session,connection='/fresh/connection.json'
            }})})
        """,
        )
        self.wait_for(editor, "s.harness.connection == '/fresh/connection.json'")
        self.keys(editor, ":ho<CR><Esc>:w<CR>")
        self.wait_for(editor, "send_reply ~= nil")
        self.assertEqual(1, self.lua(editor, "return #s.comments"))
        self.assertEqual(
            [
                "rediff-amp-live",
                "send",
                "/fresh/connection.json",
                "T-11111111-2222-4333-8444-555555555555",
            ],
            editor.vars["sent_argv"][:4],
        )
        self.lua(
            editor,
            """
            local payload=require('rediff.feedback').read(vim.g.sent_argv[#vim.g.sent_argv])
            assert(payload.comments[1].text=='Keep this annotation until accepted')
            send_reply({code=0,stdout=vim.json.encode({submission_id=payload.submission_id,status='accepted'})})
        """,
        )
        self.wait_for(editor, "s.delivery == 'accepted' and #s.comments == 0")
        self.assertIsNone(
            self.lua(
                editor, "return require('rediff.harness').get(s.root).connection_error"
            )
        )
        # A delayed registration must not undo an explicit disconnect.
        self.lua(
            editor,
            "require('rediff.harness').stop(s.root); reconnect_reply=nil; vim.fn.confirm=function() return 1 end",
        )
        self.keys(editor, "<Esc>:harness resume<CR>")
        self.wait_for(editor, "reconnect_reply ~= nil")
        self.keys(editor, "<Esc>:harness disconnect<CR>")
        self.lua(
            editor,
            """
            reconnect_reply({code=0,stdout=vim.json.encode({{
                root=s.root,session='T-11111111-2222-4333-8444-555555555555',connection='/late/connection.json'
            }})})
        """,
        )
        pump(editor)
        self.assertEqual("none", self.lua(editor, "return s.harness.name"))
        self.assertEqual(
            0, self.lua(editor, "return #require('rediff.connections').connected()")
        )

    def test_harness_delivery_keeps_editor_responsive(self):
        editor = self.launch(self.root)
        editor.exec_lua(
            STATE
            + """
            require('rediff.live').stop(s)
            local feedback = require('rediff.feedback')
            local receiver = ...
            feedback.settings = function()
                return {feedback_command={vim.v.progpath,'--headless','-u','NONE','-l',receiver,'wait'}}
            end
            r.select_harness('custom')
            local write = feedback.write
            vim.g.feedback_writes = 0
            feedback.write = function(...)
                vim.g.feedback_writes = vim.g.feedback_writes + 1
                return write(...)
            end
            """,
            str(Path(FIXTURE).with_name("receiver.lua")),
        )
        self.lua(
            editor,
            "vim.api.nvim_set_current_win(s.new_win); "
            "vim.api.nvim_win_set_cursor(0,{5,0}); r.compose()",
        )
        self.keys(editor, "<Esc>")
        writes = self.lua(editor, "return vim.g.feedback_writes")
        self.keys(editor, "iPending annotation<Esc>")
        self.assertEqual(writes, self.lua(editor, "return vim.g.feedback_writes"))
        self.keys(editor, ":w<CR>")  # Save locally first.
        self.wait_for(editor, "s.composer == nil")
        self.lua(
            editor,
            "vim.g.delivery_tick = false; "
            "vim.defer_fn(function() vim.g.delivery_tick = true end, 50)",
        )
        self.keys(editor, ":w<CR>")
        path = self.lua(
            editor, "return require('rediff.harness').get(s.root).last.path"
        )
        try:
            self.wait_for(
                editor,
                "vim.fn.filereadable(require('rediff.harness').get(s.root).last.path .. '.calls') == 1",
            )
            self.assertTrue(
                self.lua(editor, "return require('rediff.feedback').busy(s.root)")
            )
            self.assertTrue(self.lua(editor, "return vim.g.delivery_tick"))
            self.keys(editor, "gg3j")
            self.assertEqual(
                4, self.lua(editor, "return vim.api.nvim_win_get_cursor(0)[1]")
            )
            self.assertEqual(1, self.lua(editor, "return #s.comments"))
            self.assertTrue(
                self.lua(editor, "return require('rediff.feedback').busy(s.root)")
            )
        finally:
            Path(path + ".release").touch()
        self.wait_for(editor, "not require('rediff.feedback').busy(s.root)")
        self.assertEqual(
            "completed",
            self.lua(editor, "return require('rediff.harness').get(s.root).delivery"),
        )
        self.assertEqual(0, self.lua(editor, "return #s.comments"))

    def test_harness_pane_write_sends_only_its_active_review_annotations(self):
        editor = self.launch(self.root)
        editor.exec_lua(
            STATE
            + """
            local feedback = require('rediff.feedback')
            local receiver = ...
            feedback.settings = function()
                return {feedback_command={vim.v.progpath,'--headless','-u','NONE','-l',receiver,'ok'}}
            end
            r.select_harness('custom')
            vim.api.nvim_set_current_win(s.new_win)
            vim.api.nvim_win_set_cursor(0,{5,0})
            r.compose()
            """,
            str(Path(FIXTURE).with_name("receiver.lua")),
        )
        self.keys(editor, "<Esc>iSend from the harness pane<Esc>:w<CR>")
        self.wait_for(editor, "s.composer == nil")
        self.assertIsNone(self.lua(editor, "return s.last_submission"))
        self.lua(
            editor,
            "require('rediff.harness').launch(s.root,{'sh','-c','exec sleep 60'})",
        )
        buf = editor.current.buffer.number
        job = editor.current.buffer.vars["terminal_job_id"]
        self.keys(editor, "<Esc>:w<CR>")
        self.wait_for(editor, "s.delivery == 'completed'")
        self.assertEqual(0, self.lua(editor, "return #s.comments"))
        path = self.lua(editor, "return s.last_submission")
        self.assertEqual(
            "Send from the harness pane",
            self.lua(
                editor,
                "return require('rediff.feedback').read(s.last_submission).comments[1].text",
            ),
        )
        self.assertEqual(buf, editor.current.buffer.number)
        self.assertEqual(job, editor.current.buffer.vars["terminal_job_id"])
        self.assertEqual("terminal", editor.current.buffer.options["buftype"])
        self.keys(editor, ":write<CR>")
        self.assertEqual(
            "No agent notes to send to the harness", editor.vars["startup_notice"]
        )
        self.assertEqual("1", Path(path + ".calls").read_text().strip())
        # Outside Review the same terminal must not submit to the saved Review.
        self.keys(editor, "<Space>r:ho<CR><Esc>")
        self.assertTrue(self.lua(editor, "return r.active() == nil"))
        self.lua(editor, "vim.v.errmsg = ''")
        self.keys(editor, ":w<CR>")
        self.assertIn("E382", editor.vvars["errmsg"])
        self.assertEqual("1", Path(path + ".calls").read_text().strip())
        self.assertEqual(buf, editor.current.buffer.number)

    def test_harness_use_discovers_and_picks_live_session(self):
        editor = self.launch(self.root)
        self.lua(
            editor,
            """
            local system = vim.system
            vim.system = function(argv, opts, callback)
                if argv[1] ~= 'rediff-live' then return system(argv, opts, callback) end
                assert(argv[2] == 'discover' and argv[3] == '--all')
                callback({code=0, stdout=vim.json.encode({
                    {provider='amp',root=s.root, session='T-first', title='Alpha', connection='/fake/first.json'},
                    {provider='amp',root=s.root, session='T-second', title='Beta', connection='/fake/second.json'},
                })})
            end
        """,
        )
        for choose in (False, True):
            self.keys(editor, ":harness use amp<CR>")
            self.wait_for(editor, 'vim.bo.filetype == "rediff-harness-panel"')
            self.assertEqual("none", self.lua(editor, "return s.harness.name"))
            if choose:
                self.keys(editor, "/Beta<CR><CR>q")
            else:
                self.keys(editor, "<Esc>")
            self.wait_for(editor, 'require("rediff.harness_panel").state == nil')
            self.assertEqual(
                "amp-live" if choose else "none",
                self.lua(editor, "return s.harness.name"),
            )
        self.assertEqual("T-second", self.lua(editor, "return s.harness.session"))
        self.assertIsNone(
            self.lua(editor, 'return require("rediff.harness").get(s.root).last')
        )

    def test_worktree_switch_connects_only_an_unambiguous_local_harness(self):
        other = Path(self.directory.name, "other-worktree")
        subprocess.run(
            ["git", "-C", self.root, "worktree", "add", "-b", "other", str(other)],
            check=True,
            capture_output=True,
        )
        editor = self.launch(self.root)
        editor.exec_lua(
            """
            local system = vim.system
            local root = ...
            vim.system = function(argv, opts, callback)
                if argv[1] ~= 'rediff-live' then return system(argv, opts, callback) end
                local rows = {{provider='amp',root=root..'/nested',session='T-wrong',connection='/fake/wrong'}}
                for i=1,vim.g.local_matches do
                    table.insert(rows, {provider='amp',root=root,session='T-local-'..i,connection='/fake/'..i})
                end
                callback({code=0,stdout=vim.json.encode(rows)})
            end
            """,
            str(other),
        )
        for matches in (2, 0, 1):
            self.lua(editor, f"vim.g.local_matches={matches}")
            self.keys(editor, ":worktree switch other<CR>")
            self.assertEqual(str(other), self.lua(editor, "return s.root"))
            self.assertEqual("n", editor.api.get_mode()["mode"])
            self.assertIsNone(
                self.lua(editor, "return require('rediff.harness_panel').state")
            )
            if matches == 1:
                self.wait_for(editor, "s.harness.session == 'T-local-1'")
            else:
                self.assertEqual("none", self.lua(editor, "return s.harness.name"))
            self.keys(editor, ":worktree switch main<CR>")
        # A remembered selection remains the default even with new local candidates.
        self.lua(editor, "vim.g.local_matches=2")
        self.keys(editor, ":worktree switch other<CR>")
        self.assertEqual("T-local-1", self.lua(editor, "return s.harness.session"))

    def test_multiple_harnesses_panel_routing_and_activity(self):
        editor = self.launch(self.root)
        remote = str(Path(self.directory.name, "external-worktree"))
        Path(remote).mkdir()
        editor.exec_lua(
            STATE
            + """
            require('rediff.live').stop(s)
            vim.g.external_root = ...
            _G.harness_streams, _G.sent_feedback = {}, {}
            local system = vim.system
            vim.system = function(argv, opts, callback)
                if argv[1] ~= 'rediff-amp-live' and argv[1] ~= 'rediff-omp-live' and argv[1] ~= 'rediff-live' then return system(argv,opts,callback) end
                if argv[2] == 'discover' then
                    callback({code=0,stdout=vim.json.encode({
                        {provider='amp',root=s.root,session='T-local',title='Local review',connection='/fake/local',capabilities={'activity'}},
                        {provider='omp',root=vim.g.external_root,session='T-remote',title='Remote worker',connection='/fake/remote',capabilities={'activity'}},
                    })})
                elseif argv[2] == 'watch' then
                    _G.harness_streams[argv[4]] = {stdout=opts.stdout,exit=callback}
                    return {kill=function() end}
                else
                    assert(argv[2] == 'send')
                    local payload = require('rediff.feedback').read(argv[#argv])
                    table.insert(_G.sent_feedback,{argv=argv,payload=payload,cwd=opts.cwd})
                    callback({code=0,stderr='',stdout=vim.json.encode({submission_id=payload.submission_id,status='accepted'})})
                end
            end
            function _G.emit_activity(thread, root, sequence, state, tool)
                local line = vim.json.encode({version=1,thread=thread,root=root,sequence=sequence,
                    state=state,tool=tool or vim.NIL,title=thread=='T-local' and 'Local review' or 'Remote worker'}) .. '\\n'
                local out = _G.harness_streams[thread].stdout
                out(nil,line:sub(1,9)); out(nil,line:sub(10))
            end
            """,
            remote,
        )
        self.keys(editor, ":hc<CR>")
        self.wait_for(editor, "#require('rediff.harness_panel').state.entries == 2")
        rows = editor.current.buffer[:]
        self.assertIn(Path(self.root).name, rows[3])
        self.assertIn("external-worktree", rows[5])
        self.keys(editor, "q:hl<CR>")
        self.wait_for(editor, "require('rediff.harness_panel').state ~= nil")
        self.assertEqual("rediff-harness-panel", editor.current.buffer.options["filetype"])
        self.keys(editor, "<CR>")
        self.assertEqual("T-local", self.lua(editor, "return s.harness.session"))
        self.assertIn("Enter disconnect", "\n".join(editor.current.buffer[:]))
        self.keys(editor, "<CR>")
        self.assertEqual("none", self.lua(editor, "return s.harness.name"))
        self.assertEqual(
            0, self.lua(editor, "return #require('rediff.connections').connected()")
        )
        self.assertIn("Enter connect", "\n".join(editor.current.buffer[:]))
        self.keys(editor, "<CR>")
        self.assertEqual("T-local", self.lua(editor, "return s.harness.session"))
        self.keys(editor, "/Remote<CR><CR>")
        self.assertEqual(
            2, self.lua(editor, "return #require('rediff.connections').connected()")
        )
        self.assertEqual("T-local", self.lua(editor, "return s.harness.session"))
        self.lua(editor, "vim.ui.input=function(_,callback) callback('backend') end")
        self.keys(editor, "r")
        self.assertEqual(
            "backend",
            self.lua(
                editor, "return require('rediff.harness_panel').state.entries[1].alias"
            ),
        )
        self.lua(
            editor,
            "emit_activity('T-local',s.root,0,'idle'); emit_activity('T-remote',vim.g.external_root,0,'running','shell_command')",
        )
        pump(editor)
        panel_text = "\n".join(editor.current.buffer[:])
        self.assertIn("external worktree", panel_text)
        self.assertIn("shell_command", panel_text)
        frame = self.lua(editor, "return require('rediff.harness_panel').state.frame")
        self.wait_for(editor, f"require('rediff.harness_panel').state.frame ~= {frame}")
        editor.ui_try_resize(100, 28)
        pump(editor)
        self.assertTrue(
            self.lua(
                editor,
                "local p=require('rediff.harness_panel').state; return vim.api.nvim_win_get_width(p.win) <= 94",
            )
        )
        self.lua(
            editor,
            "emit_activity('T-remote',vim.g.external_root,1,'awaiting-approval')",
        )
        pump(editor)
        self.assertIn("awaiting-approval", "\n".join(editor.current.buffer[:]))
        self.keys(editor, "q")
        self.lua(
            editor,
            "r.add_comment({file=s.current.path,side='new',snapshot_id=s.current.id,selection=require('rediff.selection').line(s.new_buf,5)},'Local annotation')",
        )
        self.lua(editor, "vim.api.nvim_set_current_win(s.new_win)")
        self.keys(editor, ":w<CR>")
        self.wait_for(editor, "not require('rediff.feedback').busy(s.root)")
        self.assertEqual(1, self.lua(editor, "return #_G.sent_feedback"))
        annotation = self.lua(editor, "return _G.sent_feedback[1]")
        self.assertEqual("rediff-amp-live", annotation["argv"][0])
        self.assertEqual("T-local", annotation["argv"][3])
        self.assertEqual(
            "Local annotation", annotation["payload"]["comments"][0]["text"]
        )
        self.assertEqual(0, self.lua(editor, "return #s.comments"))
        self.assertFalse(
            self.lua(
                editor,
                "return pcall(require('rediff.harness').select,s.root,{name='omp-live',root=vim.g.external_root,session='T-remote',connection='/fake/remote'})",
            )
        )
        self.lua(editor, "_G.harness_streams['T-remote'].exit({code=1})")
        pump(editor)
        self.assertTrue(
            self.lua(
                editor,
                "for _,e in ipairs(require('rediff.connections').connected()) do if e.session=='T-remote' then return not e.online and e.activity.state=='unknown' end end",
            )
        )
        self.keys(editor, ":harness list<CR>")
        self.keys(editor, "/backend<CR><CR>")
        self.assertEqual(
            1, self.lua(editor, "return #require('rediff.connections').connected()")
        )
        self.assertEqual("T-local", self.lua(editor, "return s.harness.session"))
        self.assertIn("Enter connect", "\n".join(editor.current.buffer[:]))
        self.keys(editor, "<CR>q")
        self.assertTrue(
            self.lua(
                editor,
                "for _,e in ipairs(require('rediff.connections').connected()) do if e.alias=='backend' then return e.online end end",
            )
        )
        self.assertEqual("T-local", self.lua(editor, "return s.harness.session"))
        self.assertEqual(1, self.lua(editor, "return #_G.sent_feedback"))

    def test_harness_panel_polls_without_overlapping_or_losing_selection(self):
        editor = self.launch(self.root)
        self.lua(
            editor,
            """
            require('rediff.live').stop(s)
            _G.discoveries, _G.completed = 0, 0
            local system = vim.system
            vim.system = function(argv, opts, callback)
                if argv[1] ~= 'rediff-live' then return system(argv,opts,callback) end
                assert(argv[2] == 'discover' and argv[3] == '--all')
                discoveries = discoveries + 1
                _G.discovery_done = callback
            end
            function _G.respond(rows)
                discovery_done({code=0,stdout=vim.json.encode(rows)})
            end
            _G.rows = {
                {provider='amp',root=s.root,session='T-b',title='Beta',connection='/fake/b'},
                {provider='amp',root=s.root,session='T-c',title='Charlie',connection='/fake/c'},
            }
            """,
        )
        self.keys(editor, ":hl<CR>")
        self.assertEqual(1, self.lua(editor, "return discoveries"))
        self.assertEqual(
            1000,
            self.lua(
                editor,
                "return require('rediff.harness_panel').state.discovery_timer:get_repeat()",
            ),
        )
        # A slow probe must not be replaced every second (or it can never finish).
        self.lua(
            editor,
            "require('rediff.connections').discover(function() completed=completed+1 end)",
        )
        time.sleep(1.2)
        pump(editor)
        self.assertEqual(1, self.lua(editor, "return discoveries"))
        self.lua(editor, "respond(rows)")
        self.wait_for(
            editor,
            "completed == 1 and #require('rediff.harness_panel').state.entries == 2",
        )
        self.keys(editor, "j")
        self.wait_for(editor, "discoveries == 2")
        self.lua(
            editor,
            "table.insert(rows,1,{provider='amp',root=s.root,session='T-a',title='Alpha',connection='/fake/a'}); respond(rows)",
        )
        self.wait_for(editor, "#require('rediff.harness_panel').state.entries == 3")
        self.assertEqual(
            "T-c",
            self.lua(
                editor,
                "local p=require('rediff.harness_panel').state; return p.entries[p.selected].session",
            ),
        )
        self.keys(editor, "/Charlie")
        self.wait_for(editor, "discoveries == 3")
        self.lua(editor, "respond(rows)")
        self.wait_for(editor, "not require('rediff.harness_panel').state.refreshing")
        self.assertEqual("i", editor.api.get_mode()["mode"])
        self.assertEqual(["Charlie"], list(editor.current.buffer[:]))
        self.assertEqual(
            1, self.lua(editor, "return #require('rediff.harness_panel').state.entries")
        )
        # Removing a session takes effect without R; the filter remains intact.
        self.keys(editor, "<CR>")
        self.wait_for(editor, "discoveries == 4")
        self.lua(editor, "table.remove(rows,3); respond(rows)")
        self.wait_for(editor, "#require('rediff.harness_panel').state.entries == 0")
        self.wait_for(editor, "discoveries == 5")
        self.lua(
            editor,
            "_G.poll_timer=require('rediff.harness_panel').state.discovery_timer",
        )
        self.keys(editor, "q:hl<CR>")
        self.assertTrue(self.lua(editor, "return poll_timer:is_closing()"))
        self.assertEqual(
            5, self.lua(editor, "return discoveries"), "Reopen shares the pending probe"
        )
        self.lua(editor, "respond(rows)")
        self.wait_for(editor, "not require('rediff.harness_panel').state.refreshing")
        self.assertEqual(
            2, self.lua(editor, "return #require('rediff.harness_panel').state.entries")
        )
        self.keys(editor, "q")
        count = self.lua(editor, "return discoveries")
        time.sleep(1.2)
        pump(editor)
        self.assertEqual(
            count,
            self.lua(editor, "return discoveries"),
            "Closing stops discovery polling",
        )

    def test_harness_stream_lifecycle_and_alias_persistence(self):
        editor = self.launch(self.root)
        self.lua(
            editor,
            """
            require('rediff.live').stop(s)
            _G.streams = {}
            local system = vim.system
            vim.system = function(argv, opts, callback)
                if argv[1] ~= 'rediff-amp-live' and argv[1] ~= 'rediff-live' then return system(argv,opts,callback) end
                if argv[2] == 'discover' then
                    callback({code=0,stdout=vim.json.encode({target})})
                    return
                end
                assert(argv[2] == 'watch')
                local stream = {out=opts.stdout,exit=callback,killed=false}
                table.insert(streams,stream)
                return {kill=function() stream.killed=true end}
            end
            _G.registry = require('rediff.connections')
            _G.target = {provider='amp',name='amp-live',root=s.root,session='T-stream',connection='/fake/stream',capabilities={'activity'}}
            _G.key = registry.connect(target).key
            registry.rename(key,'test-worker')
            registry.disconnect(key)
            registry.connect(target)
            streams[1].out(nil,'{}\\n')
            streams[1].exit({code=1})
        """,
        )
        pump(editor)
        self.assertTrue(
            self.lua(editor, "return streams[1].killed and registry.get(key).online")
        )
        self.lua(
            editor,
            """
            local line = vim.json.encode({version=1,root=s.root,thread='T-stream',sequence=3,
                state='running',title='Working',tool=vim.NIL}) .. '\\n'
            streams[2].out(nil,line)
        """,
        )
        pump(editor)
        self.assertEqual(
            {"state": "running"}, self.lua(editor, "return registry.get(key).activity")
        )
        self.lua(
            editor,
            "target.title='Old descriptor title'; registry.discover(function() end)",
        )
        pump(editor)
        self.assertEqual(
            2,
            self.lua(editor, "return #streams"),
            "Discovery preserves the activity stream",
        )
        self.assertEqual("Working", self.lua(editor, "return registry.get(key).title"))
        self.assertFalse(self.lua(editor, "return streams[2].killed"))
        self.lua(editor, "streams[2].out(nil,'false\\n')")
        pump(editor)
        self.assertTrue(
            self.lua(
                editor, "return streams[2].killed and not registry.get(key).online"
            )
        )
        self.lua(
            editor,
            """
            registry.connect(target)
            local wrong = vim.json.encode({version=1,root=s.root..'/other',thread='T-stream',sequence=4,
                state='idle',title='Wrong worktree'}) .. '\\n'
            streams[3].out(nil,wrong)
        """,
        )
        pump(editor)
        self.assertFalse(self.lua(editor, "return registry.get(key).online"))
        self.lua(editor, "registry.discover(function() end)")
        pump(editor)
        self.assertEqual(
            4,
            self.lua(editor, "return #streams"),
            "Discovery reconnects a failed stream",
        )
        self.lua(
            editor,
            "target.connection='/fake/reloaded'; registry.discover(function() end)",
        )
        pump(editor)
        self.assertEqual(
            5,
            self.lua(editor, "return #streams"),
            "A changed descriptor starts a new stream",
        )
        self.assertTrue(self.lua(editor, "return streams[4].killed"))
        self.lua(editor, "streams[4].exit({code=1})")
        pump(editor)
        self.assertTrue(
            self.lua(editor, "return registry.get(key).online"),
            "Old stream exit cannot mark the new one offline",
        )
        fresh = self.launch(self.root, headless=True)
        self.assertEqual(
            "test-worker",
            fresh.exec_lua(
                """
            local registry = require('rediff.connections')
            assert(#registry.connected() == 0)
            return registry.connect({name='amp-live',root=...,session='T-stream',connection='/fake/stream'}).alias
        """,
                self.root,
            ),
        )

    def test_help_pages_like_man_without_changing_workspace_keys(self):
        editor = self.launch(self.root, file="auth.lua")
        index = subprocess.check_output(["git", "-C", self.root, "write-tree"])
        # Space must page immediately, not wait for a possible leader sequence.
        editor.options["timeoutlen"] = 5000
        for mode in ("editing", "review"):
            if mode == "review":
                self.keys(editor, " r")
            original = editor.current.window.handle
            self.keys(editor, "?")
            self.assertEqual("help", editor.current.buffer.options["filetype"])
            height = editor.current.window.height
            self.keys(editor, "g")
            self.assertEqual(1, editor.current.window.cursor[0])
            for forward in (" ", "f", "<PageDown>"):
                self.keys(editor, forward)
                self.assertGreater(editor.eval('line("w0")'), height // 2)
                self.keys(editor, "b")
                self.assertEqual(1, editor.eval('line("w0")'))
            self.keys(editor, "d")
            self.assertGreater(editor.eval('line("w0")'), 1)
            self.keys(editor, "u")
            self.assertEqual(1, editor.eval('line("w0")'))
            self.keys(editor, "G")
            self.assertEqual(
                len(editor.current.buffer), editor.current.window.cursor[0]
            )
            self.keys(editor, "g/REVIEW QUICK REFERENCE<CR>")
            self.assertEqual("REVIEW QUICK REFERENCE", editor.current.line)
            self.keys(editor, "q")
            self.assertEqual(original, editor.current.window.handle)
            self.keys(editor, " p")
            self.wait_for(editor, 'vim.bo.filetype == "fzf"')
            self.keys(editor, "<Esc>")
            self.wait_for(editor, f"vim.api.nvim_get_current_win() == {original}")
        self.assertEqual(
            index, subprocess.check_output(["git", "-C", self.root, "write-tree"])
        )

    def test_command_picker(self):
        editor = self.launch(self.root)
        index = subprocess.check_output(["git", "-C", self.root, "write-tree"])
        editor.exec_lua(
            'vim.api.nvim_create_user_command("PaletteProbe", function() '
            "vim.g.palette_window = vim.api.nvim_get_current_win() end, {})"
        )
        for pane in ("tree_win", "old_win", "new_win"):
            self.lua(editor, f"vim.api.nvim_set_current_win(s.{pane})")
            original = editor.current.window.handle
            self.keys(editor, "?")
            self.assertEqual("help", editor.current.buffer.options["filetype"])
            help_window = editor.current.window.handle
            self.keys(editor, ":Commands<CR>")
            self.wait_for(editor, 'vim.bo.filetype == "fzf"')
            self.assertFalse(editor.api.win_is_valid(help_window))
            self.keys(editor, "PaletteProbe")
            self.keys(editor, "<Esc>")
            self.wait_for(editor, f"vim.api.nvim_get_current_win() == {original}")
            self.assertIsNone(editor.vars.get("palette_window"))
        for opening in (":Commands<CR>", " p"):
            original = editor.current.window.handle
            self.keys(editor, opening)
            self.wait_for(editor, 'vim.bo.filetype == "fzf"')
            self.keys(editor, "PaletteProbe")
            self.keys(editor, "<CR>")
            self.wait_for(editor, 'vim.fn.getcmdline() == "PaletteProbe"')
            self.assertIsNone(editor.vars.get("palette_window"))
            self.keys(editor, "<CR>")
            self.wait_for(editor, f"vim.g.palette_window == {original}")
            editor.vars["palette_window"] = None
            if self.lua(editor, "return r.active() ~= nil"):
                self.keys(editor, " q")
        self.assertEqual(
            index, subprocess.check_output(["git", "-C", self.root, "write-tree"])
        )

    def test_review_symbol_definitions_use_current_worktree_without_leaving_review(
        self,
    ):
        source = Path(self.root, "auth.lua")
        source.write_text(
            "-- Lines moved in the worktree\n\n"
            + source.read_text().replace(
                "return token.expires_at > os.time()",
                'local label = "é😀"; return require("helper with spaces").lookup_target(token)',
            )
        )
        helper = Path(self.root, "helper with spaces.lua")
        helper.write_text(
            "local M = {}\n\nfunction M.lookup_target(token)\n  return true\nend\nreturn M\n"
        )
        # Same symbol name in a different module: semantic lookup must follow the import.
        Path(self.root, "other.lua").write_text(
            "local M = {}\nfunction M.lookup_target() end\nreturn M\n"
        )
        index = subprocess.check_output(["git", "-C", self.root, "write-tree"])
        original = source.read_bytes()
        editor = self.launch(self.root)
        self.lua(
            editor,
            "require('rediff.live').stop(s); for i,e in ipairs(s.entries) do if e.path=='auth.lua' then r.show(i); break end end",
        )
        self.lua(
            editor,
            "r.add_comment({file=s.current.path,side='new',snapshot_id=s.current.id,selection=require('rediff.selection').line(s.new_buf,5)},'Keep this note')",
        )
        review_tab = editor.current.tabpage.handle
        for layout, side, line, key, destination, destination_line in (
            ("split", "old", 3, "<M-CR>", source, 5),
            ("split", "new", 9, "<M-CR>", helper, 3),
            ("merged", "new", 9, "gd", helper, 3),
        ):
            with self.subTest(layout=layout, side=side):
                editor.command("View " + layout)
                self.lua(editor, f"vim.api.nvim_set_current_win(s.{side}_win)")
                column = (
                    12
                    if side == "old"
                    else len(
                        editor.current.buffer[line - 1]
                        .split("lookup_target")[0]
                        .encode()
                    )
                )
                editor.current.window.cursor = [line, column]
                panes = editor.eval("winlayout()")
                self.keys(editor, key)
                self.wait_for(
                    editor, f"vim.api.nvim_get_current_tabpage() ~= {review_tab}"
                )
                self.assertEqual(str(destination), editor.current.buffer.name)
                self.assertEqual(destination_line, editor.current.window.cursor[0])
                self.keys(editor, "gT")
                self.assertEqual(review_tab, editor.current.tabpage.handle)
                self.assertEqual(panes, editor.eval("winlayout()"))
                self.assertEqual(
                    "Keep this note", self.lua(editor, "return s.comments[1].text")
                )
                self.assertIsNone(self.lua(editor, "return s.last_submission"))
        editor.command("View split")
        self.lua(editor, "vim.api.nvim_set_current_win(s.old_win)")
        editor.current.window.cursor = [7, 12]
        self.keys(editor, "gd")
        self.assertEqual(review_tab, editor.current.tabpage.handle)
        self.assertIn("This line changed", editor.vars["startup_notice"])
        self.assertEqual(
            0, self.lua(editor, "return #vim.lsp.get_clients({bufnr=s.new_buf})")
        )
        self.assertEqual(
            0, self.lua(editor, "return #vim.lsp.get_clients({bufnr=s.old_buf})")
        )
        self.assertEqual(original, source.read_bytes())
        self.assertEqual(
            index, subprocess.check_output(["git", "-C", self.root, "write-tree"])
        )

    def test_bundled_language_servers_resolve_review_definitions(self):
        cases = (
            (
                "example.py",
                "def greeting():\n    return 'hi'\n\ngreeting()\n",
                4,
                0,
                1,
                "pyright",
            ),
            (
                "example.ts",
                "export function greeting() {}\ngreeting();\n",
                2,
                0,
                1,
                "ts_ls",
            ),
            (
                "example.nix",
                'let\n  greeting = name: name;\nin greeting "hi"\n',
                3,
                3,
                2,
                "nil_ls",
            ),
            (
                "example.go",
                'package fixture\n\nfunc greeting() string { return "hi" }\nfunc caller() string { return greeting() }\n',
                4,
                30,
                3,
                "gopls",
            ),
            (
                "example.rs",
                "pub fn greeting() -> u32 { 1 }\npub fn caller() -> u32 { greeting() }\n",
                2,
                25,
                1,
                "rust_analyzer",
            ),
        )
        Path(self.root, "go.mod").write_text("module example.com/fixture\n\ngo 1.20\n")
        Path(self.root, "Cargo.toml").write_text(
            '[package]\nname = "rediff_fixture"\nversion = "0.1.0"\nedition = "2021"\n[lib]\npath = "example.rs"\n'
        )
        for name, contents, *_ in cases:
            Path(self.root, name).write_text(contents)
        editor = self.launch(self.root)
        self.lua(editor, "require('rediff.live').stop(s)")
        review_tab = editor.current.tabpage.handle
        for name, _, row, col, target, server in cases:
            with self.subTest(language_server=server):
                self.lua(
                    editor,
                    f"for i,e in ipairs(s.entries) do if e.path=='{name}' then r.show(i); break end end; vim.api.nvim_set_current_win(s.new_win)",
                )
                editor.current.window.cursor = [row, col]
                if server == "rust_analyzer":
                    # Rust answers with no locations until its initial workspace indexing finishes.
                    self.lua(
                        editor,
                        """
                        vim.lsp.config('rust_analyzer', {handlers={
                            ['experimental/serverStatus']=function(_, result)
                                vim.g.rust_ready=result.quiescent
                            end,
                        }})
                        vim.fn.bufload(vim.fn.bufadd(s.root..'/example.rs'))
                    """,
                    )
                    self.wait_for(editor, "vim.g.rust_ready == true", timeout=60)
                self.keys(editor, "gd")
                self.wait_for(
                    editor,
                    f"vim.api.nvim_get_current_tabpage() ~= {review_tab}",
                    timeout=30,
                )
                self.assertEqual(str(Path(self.root, name)), editor.current.buffer.name)
                self.assertEqual(target, editor.current.window.cursor[0])
                self.assertEqual(
                    server,
                    self.lua(editor, "return vim.lsp.get_clients({bufnr=0})[1].name"),
                )
                self.keys(editor, "gT")

    def test_definition_lookup_is_async_and_does_not_jump_after_cursor_moves(self):
        editor = self.launch(self.root)
        self.lua(
            editor,
            """
            require('rediff.live').stop(s)
            vim.api.nvim_set_current_win(s.new_win)
            vim.api.nvim_win_set_cursor(0,{3,12})
            vim.lsp.enable({'lua_ls', 'nil_ls', 'pyright', 'ts_ls'}, false)
            local get_clients = vim.lsp.get_clients
            vim.lsp.get_clients = function(opts)
                if opts and opts.method == 'textDocument/definition' then return {{}} end
                return get_clients(opts)
            end
            vim.lsp.buf.definition = function(opts) _G.definition_result = opts.on_list end
        """,
        )
        tab = editor.current.tabpage.handle
        self.keys(editor, "<M-CR>")
        self.wait_for(editor, "definition_result ~= nil")
        self.keys(editor, "j")
        self.assertEqual(
            4, editor.current.window.cursor[0], "Lookup must not block navigation"
        )
        self.lua(
            editor,
            "definition_result({items={{filename=s.root..'/auth.lua',lnum=3,col=1}}})",
        )
        pump(editor)
        self.assertEqual(tab, editor.current.tabpage.handle)
        self.assertEqual(
            4, editor.current.window.cursor[0], "Stale lookup must not steal focus"
        )
        self.lua(
            editor,
            "vim.api.nvim_win_set_cursor(s.new_win,{3,12}); _G.definition_result=nil",
        )
        self.keys(editor, "gd")
        self.wait_for(editor, "definition_result ~= nil")
        self.lua(
            editor,
            "definition_result({items={{filename=s.root..'/auth.lua',lnum=3,col=1},{filename=s.root..'/auth.lua',lnum=16,col=1}}})",
        )
        pump(editor)
        self.assertNotEqual(tab, editor.current.tabpage.handle)
        self.assertEqual("quickfix", editor.current.buffer.options["buftype"])
        self.assertEqual([3, 16], [item["lnum"] for item in editor.funcs.getqflist()])

    def test_annotation_editor_is_anchored_below_source(self):
        editor = self.launch(self.root)
        index = subprocess.check_output(["git", "-C", self.root, "write-tree"])
        original = Path(self.root, "auth.lua").read_bytes()

        def below(side, last):
            geometry = self.lua(
                editor,
                f"""
                local win = s.{side}_win
                local cfg = vim.api.nvim_win_get_config(s.composer_win)
                local pos = vim.fn.screenpos(win, {last}, 1)
                return {{cfg.relative, cfg.win == win, cfg.bufpos,
                    vim.api.nvim_win_get_position(s.composer_win), pos.row, pos.col,
                    cfg.width, cfg.height, vim.api.nvim_win_get_position(win),
                    vim.api.nvim_win_get_width(win), vim.api.nvim_win_get_height(win)}}
            """,
            )
            relative, same_win, anchor, pos, row, col, width, height, source, sw, sh = (
                geometry
            )
            self.assertEqual("win", relative)
            self.assertTrue(same_win)
            self.assertEqual([last - 1, 0], anchor)
            self.assertGreater(row, 0, "The annotated line must remain visible")
            # screenpos is 1-based; float position is 0-based, including border.
            self.assertEqual(
                row, pos[0], "Top border is immediately below the annotation range"
            )
            self.assertEqual(col - 1, pos[1])
            self.assertLessEqual(pos[1] + width + 2, source[1] + sw)
            self.assertLessEqual(pos[0] + height + 2, source[0] + sh)

        for layout, side, line, opening, last in (
            ("split", "new", 5, "i", 5),
            ("split", "old", 8, "Vkka", 8),
            ("merged", "new", 17, "i", 17),
        ):
            with self.subTest(layout=layout, side=side):
                editor.command("View " + layout)
                self.lua(editor, f"vim.api.nvim_set_current_win(s.{side}_win)")
                editor.current.window.cursor = [line, 0]
                panes = editor.eval("winlayout()")
                height = editor.current.window.height
                self.keys(editor, opening + "Review this range<Esc>")
                below(side, last)
                self.assertEqual(panes, editor.eval("winlayout()"))
                self.assertEqual(
                    height,
                    self.lua(
                        editor, f"return vim.api.nvim_win_get_height(s.{side}_win)"
                    ),
                )
                self.assertEqual(["Review this range"], editor.current.buffer[:])
                if layout == "merged":
                    editor.ui_try_resize(79, 14)
                    pump(editor)
                    below(side, last)
                    self.assertEqual(["Review this range"], editor.current.buffer[:])
                    self.assertTrue(
                        self.lua(
                            editor,
                            "return vim.api.nvim_get_current_win() == s.composer_win",
                        )
                    )
                self.keys(editor, "<Esc>")
                self.wait_for(editor, "s.composer == nil")
                self.assertEqual(0, self.lua(editor, "return #s.comments"))
                self.assertTrue(
                    self.lua(
                        editor, f"return vim.api.nvim_get_current_win() == s.{side}_win"
                    )
                )
        self.keys(editor, "i<Esc>gg0cGSaved note<Esc>:w<CR>")
        self.wait_for(editor, "s.composer == nil")
        self.keys(editor, "iUnsaved edit<Esc>")
        self.assertIsNotNone(self.lua(editor, "return s.composer"))
        self.keys(editor, "<Esc>")
        self.wait_for(editor, "s.composer == nil")
        self.assertEqual("Saved note", self.lua(editor, "return s.comments[1].text"))
        self.assertIsNone(self.lua(editor, "return s.last_submission"))
        self.assertEqual(original, Path(self.root, "auth.lua").read_bytes())
        self.assertEqual(
            index, subprocess.check_output(["git", "-C", self.root, "write-tree"])
        )

    def test_empty_review_write_is_a_notice_without_delivery(self):
        editor = self.launch(self.root)
        self.lua(
            editor,
            """
            require('rediff.live').stop(s)
            require('rediff.feedback').enqueue = function() error('Unexpected enqueue') end
            vim.notify = function(message, level)
                vim.g.empty_notice = message
                vim.g.empty_level = level
                original_notify(message, level)
            end
        """,
        )
        for pane in ("tree_win", "old_win", "new_win"):
            self.lua(editor, f"vim.api.nvim_set_current_win(s.{pane})")
            self.keys(editor, ":w<CR>")
            self.assertEqual(
                "No agent notes to send to the harness", editor.vars["empty_notice"]
            )
            self.assertEqual(2, editor.vars["empty_level"])
            self.assertIsNone(self.lua(editor, "return s.last_submission"))
            self.assertEqual("n", editor.api.get_mode()["mode"])
        self.assertEqual("draft", self.lua(editor, "return s.delivery"))

    def test_closed_note_drafts_are_independent_and_not_sent(self):
        editor = self.launch(self.root)
        self.lua(editor, "require('rediff.live').stop(s)")
        cases = (
            ("new", 5, "First draft", ":q<CR>"),
            ("new", 17, "Second draft", ":q!<CR>"),
            ("old", 5, "Old-side draft", "<Esc>"),
        )
        for side, line, text, closing in cases:
            self.lua(editor, f"vim.api.nvim_set_current_win(s.{side}_win)")
            editor.current.window.cursor = [line, 0]
            self.keys(editor, "i" + text + "<Esc>" + closing)
            self.wait_for(editor, "s.composer == nil")
        self.assertEqual(0, self.lua(editor, "return #s.comments"))
        self.keys(editor, ":w<CR>")
        self.assertIsNone(self.lua(editor, "return s.last_submission"))
        self.lua(editor, "r.leave(); r.open(); require('rediff.live').stop(r.state)")
        for side, line, text, _ in cases:
            self.lua(editor, f"vim.api.nvim_set_current_win(s.{side}_win)")
            editor.current.window.cursor = [line, 0]
            self.keys(editor, "i<Esc>")
            self.assertEqual([text], list(editor.current.buffer[:]))
            self.keys(editor, ":q<CR>")
            self.wait_for(editor, "s.composer == nil")
        self.lua(editor, "vim.api.nvim_set_current_win(s.new_win)")
        editor.current.window.cursor = [5, 0]
        self.keys(editor, "i<Esc>:w<CR>")
        self.wait_for(editor, "s.composer == nil")
        self.keys(editor, "i<Esc>A with edits<Esc>:q<CR>")
        self.wait_for(editor, "s.composer == nil")
        self.keys(editor, ":w<CR>")
        self.assertEqual(
            ["First draft"],
            self.lua(
                editor,
                "return vim.tbl_map(function(c) return c.text end, require('rediff.feedback').read(s.last_submission).comments)",
            ),
        )
        self.lua(editor, "r.clear_sent(s.root, s.last_submission)")
        self.keys(editor, "i<Esc>")
        self.assertEqual(["First draft with edits"], list(editor.current.buffer[:]))
        self.keys(editor, ":w<CR>")
        self.wait_for(editor, "s.composer == nil")
        self.assertEqual(
            "First draft with edits", self.lua(editor, "return s.comments[1].text")
        )
        self.lua(editor, "r.archive()")
        self.keys(editor, "i<Esc>")
        self.assertEqual([""], list(editor.current.buffer[:]))

    def test_visual_a_preserves_annotation_ranges_through_submission(self):
        editor = self.launch(self.root)
        index = subprocess.check_output(["git", "-C", self.root, "write-tree"])
        original = Path(self.root, "auth.lua").read_bytes()
        cases = (
            (
                "old",
                [5, 0],
                "Vkka",
                "line",
                3,
                5,
                [
                    "function M.authorize(token)",
                    "  if token == nil then",
                    "    return false",
                ],
                1,
                17,
            ),
            (
                "new",
                [3, 9],
                "vj06la",
                "character",
                3,
                4,
                ["M.authorize(token)", "  if to"],
                10,
                7,
            ),
            ("new", [4, 5], "<C-v>j3la", "block", 4, 5, ["toke", "etur"], 6, 9),
        )
        expected_notes = []
        for side, cursor, keys, kind, first, last, text, start_byte, end_byte in cases:
            with self.subTest(kind=kind):
                self.lua(editor, f"vim.api.nvim_set_current_win(s.{side}_win)")
                editor.current.window.cursor = cursor
                self.keys(editor, keys + f"Review {kind} range<Esc>")
                self.wait_for(editor, "s.composer ~= nil")
                self.assertEqual([f"Review {kind} range"], editor.current.buffer[:])
                selection = self.lua(editor, "return s.draft.anchor.selection")
                self.assertEqual(kind, selection["kind"])
                self.assertEqual(text, selection["text"])
                self.assertEqual("nvim-getregionpos-v1", selection["coordinates"])
                spans = selection["spans"]
                self.assertEqual(
                    list(range(first, last + 1)), [s["line"] for s in spans]
                )
                self.assertEqual(start_byte, spans[0]["start_byte"])
                self.assertEqual(end_byte, spans[-1]["end_byte"])
                if kind == "block":
                    for span in spans:
                        self.assertEqual(
                            (6, 9, 0, 0),
                            (
                                span["start_byte"],
                                span["end_byte"],
                                span["start_offset"],
                                span["end_offset"],
                            ),
                        )
                self.keys(editor, ":w<CR>")
                self.wait_for(editor, "s.composer == nil")
                note = self.lua(editor, "return s.comments[#s.comments]")
                self.assertEqual(
                    ("auth.lua", side, first, last),
                    (
                        note["file"],
                        note["side"],
                        note["line"],
                        note["line_end"],
                    ),
                )
                self.assertEqual(selection, note["selection"])
                expected_notes.append(note)
        self.assertIsNone(self.lua(editor, "return s.last_submission"))
        # No receiver is connected: submission archives the batch locally only.
        self.keys(editor, ":w<CR>")
        payload = self.lua(
            editor, 'return require("rediff.feedback").read(s.last_submission)'
        )
        self.assertEqual(expected_notes, payload["comments"])
        self.assertEqual(original, Path(self.root, "auth.lua").read_bytes())
        self.assertEqual(
            index, subprocess.check_output(["git", "-C", self.root, "write-tree"])
        )

    def test_annotation_picker_jump_edit_and_saved_snapshots(self):
        editor = self.launch(self.root)
        index = subprocess.check_output(["git", "-C", self.root, "write-tree"])
        original = Path(self.root, "auth.lua").read_bytes()
        notes = self.lua(
            editor,
            r"""
            local selection = require('rediff.selection')
            local function note(side, line, text)
                local buf = side == 'old' and s.old_buf or s.new_buf
                return r.add_comment({file=s.current.path, side=side,
                    snapshot_id=s.current.id, selection=selection.line(buf,line)},text)
            end
            note('new',5,'Authentication boundary\nCheck lease timeout')
            note('new',5,'Overlapping candidate')
            note('old',17,'Old refresh contract')
            for i,entry in ipairs(s.entries) do
                if entry.path == 'plan.md' then r.show(i); break end
            end
            note('new',2,'Plan details')
            return s.comments
            """,
        )

        def choose(opening, query):
            self.keys(editor, opening)
            self.wait_for(editor, 'vim.bo.filetype == "fzf"')
            self.keys(editor, query)
            self.keys(editor, "<CR>")
            self.wait_for(editor, 'vim.bo.filetype ~= "fzf"')

        for pane in ("tree_win", "old_win", "new_win"):
            self.lua(editor, f"vim.api.nvim_set_current_win(s.{pane})")
            self.assertEqual(
                "", self.lua(editor, 'return vim.fn.maparg("<leader>c", "n")')
            )
            self.keys(editor, "@")
            self.wait_for(editor, 'vim.bo.filetype == "fzf"')
            self.keys(editor, "<Esc>")
        choose(":al<CR>", "lease timeout")
        self.assertEqual(
            ["auth.lua", "new", 5, notes[0]["id"]],
            self.lua(
                editor,
                "return {s.current.path, vim.api.nvim_get_current_win()==s.new_win and 'new' or 'old', "
                "vim.api.nvim_win_get_cursor(0)[1], s.annotation_id}",
            ),
        )
        self.keys(editor, "i<Esc>")
        self.assertEqual(
            ["Authentication boundary", "Check lease timeout"],
            list(editor.current.buffer[:]),
        )
        self.assertTrue(
            self.lua(
                editor,
                'return vim.fn.maparg("{", "n") == "" and vim.fn.maparg("}", "n") == "" and vim.fn.maparg("@", "n") == ""',
            )
        )
        self.keys(editor, "gg0CUpdated boundary<Esc>:w<CR>")
        self.wait_for(editor, "s.composer == nil")
        edited = self.lua(editor, "return s.comments")
        expected = [dict(note) for note in notes]
        expected[0]["text"] = "Updated boundary\nCheck lease timeout"
        self.assertEqual(expected, edited)
        self.keys(editor, "iDraft: <Esc>:q!<CR>")
        self.assertEqual(expected, self.lua(editor, "return s.comments"))

        # Direct i on overlapping anchors asks which note, rather than overwriting one.
        self.keys(editor, " R")
        choose("i", "candidate")
        self.assertEqual("Overlapping candidate", editor.current.buffer[0])
        self.keys(editor, "<Esc>:q<CR>")
        self.keys(editor, ":view merged<CR>}")
        self.assertEqual(
            ["split", True, 17, notes[2]["id"]],
            self.lua(
                editor,
                "return {s.layout, vim.api.nvim_get_current_win()==s.old_win, "
                "vim.api.nvim_win_get_cursor(0)[1], s.annotation_id}",
            ),
        )
        self.keys(editor, "2{")
        self.assertEqual(notes[0]["id"], self.lua(editor, "return s.annotation_id"))
        self.keys(editor, ":annotations prev<CR>")
        self.assertEqual(notes[3]["id"], self.lua(editor, "return s.annotation_id"))
        self.keys(editor, ":annotations next<CR>")
        self.assertEqual(notes[0]["id"], self.lua(editor, "return s.annotation_id"))
        self.keys(editor, " e{")
        self.assertEqual(notes[3]["id"], self.lua(editor, "return s.annotation_id"))
        self.keys(editor, " e}")
        self.assertEqual(notes[0]["id"], self.lua(editor, "return s.annotation_id"))
        choose(":annotations list<CR>", "Old refresh")
        self.assertEqual(notes[2]["id"], self.lua(editor, "return s.annotation_id"))
        choose("@", "Plan details")
        self.assertEqual("plan.md", self.lua(editor, "return s.current.path"))
        self.assertEqual(original, Path(self.root, "auth.lua").read_bytes())

        # The file can disappear from Git's changed-file list; notes keep exact snapshots.
        baseline = subprocess.check_output(
            ["git", "-C", self.root, "show", "HEAD:auth.lua"]
        )
        Path(self.root, "auth.lua").write_bytes(baseline)
        self.keys(editor, " R")
        choose(":al<CR>", "lease timeout")
        self.assertTrue(self.lua(editor, "return s.index == nil"))
        self.assertEqual(original.decode(), self.lua(editor, "return s.current.new"))
        self.assertFalse(self.lua(editor, "return r.refresh_live()"))
        self.keys(editor, "s")
        self.assertIn("Space R", self.lua(editor, "return vim.g.startup_notice"))
        self.keys(editor, "i<Esc>")
        self.assertEqual(
            ["Draft: Updated boundary", "Check lease timeout"],
            list(editor.current.buffer[:]),
        )
        self.keys(editor, ":q<CR> R")
        self.assertTrue(self.lua(editor, "return s.annotation_id == nil"))
        self.assertEqual(expected, self.lua(editor, "return s.comments"))
        self.assertIsNone(self.lua(editor, "return s.last_submission"))
        self.assertEqual(baseline, Path(self.root, "auth.lua").read_bytes())
        self.assertEqual(
            index, subprocess.check_output(["git", "-C", self.root, "write-tree"])
        )
        self.keys(editor, " q")
        self.assertTrue(
            self.lua(
                editor,
                'return vim.fn.maparg("{", "n") == "" and vim.fn.maparg("}", "n") == "" and vim.fn.maparg("@", "n") == ""',
            )
        )

    def test_tree_annotation_indicators(self):
        subprocess.run(["git", "-C", self.root, "add", "auth.lua"], check=True)
        path = Path(self.root, "auth.lua")
        path.write_text(path.read_text() + "-- unstaged edit\n")
        Path(self.root, "sub").mkdir()
        Path(self.root, "sub/auth.lua").write_text("return 'same basename'\n")
        editor = self.launch(self.root)
        self.lua(editor, "require('rediff.live').stop(s)")

        def marked_files(expected):
            marks = self.lua(
                editor,
                "local marks=vim.api.nvim_buf_get_extmarks(s.tree_buf, "
                "vim.api.nvim_get_namespaces()['rediff.annotations'],0,-1,{details=true}); "
                "local result={}; for _,m in ipairs(marks) do "
                "table.insert(result,{s.entries[s.rows[m[2]+1]].path,m[2]+1,m[4]}) end; return result",
            )
            self.assertCountEqual(expected, [path for path, _, _ in marks])
            for _, _, details in marks:
                self.assertEqual([["●", "ReviewAnnotation"]], details["virt_text"])
                self.assertEqual("right_align", details["virt_text_pos"])
            return marks

        marked_files([])
        self.lua(
            editor,
            """
            local selection=require('rediff.selection')
            for _,note in ipairs({{'new',5,'First note'},{'old',17,'Second note'}}) do
                r.add_comment({file=s.current.path,side=note[1],snapshot_id=s.current.id,
                    selection=selection.line(note[1]=='old' and s.old_buf or s.new_buf,note[2])},note[3])
            end
            """,
        )
        # One marker per row, not per comment; both Git groups share the file's notes.
        marked_files(["auth.lua", "auth.lua"])
        self.lua(
            editor,
            "for i,e in ipairs(s.entries) do if e.path=='plan.md' then r.show(i); break end end; "
            "r.add_comment({file=s.current.path,side='new',snapshot_id=s.current.id, "
            "selection=require('rediff.selection').line(s.new_buf,1)},'Plan note')",
        )
        expected = ["auth.lua", "auth.lua", "plan.md"]
        for columns in (132, 88):
            editor.ui_try_resize(columns, 32)
            editor.command("colorscheme rose-pine")
            pump(editor)
            for _, row, _ in marked_files(expected):
                self.keys(editor, f" e{row}G")
                for focus in (" e", " d"):
                    self.keys(editor, focus)
                    cell = self.lua(
                        editor,
                        f"local p=vim.fn.screenpos(s.tree_win,{row},1); "
                        "local col=vim.api.nvim_win_get_position(s.tree_win)[2] "
                        "+ vim.api.nvim_win_get_width(s.tree_win)-1; "
                        "return vim.api.nvim__inspect_cell(1,p.row-1,col)",
                    )
                    self.assertEqual("●", cell[0])
                    self.assertEqual(0xFF9E64, cell[1]["foreground"])
        # Notes still refer to their saved snapshots after staging and new edits.
        subprocess.run(["git", "-C", self.root, "add", "plan.md"], check=True)
        path.write_text(path.read_text() + "-- newer snapshot\n")
        self.lua(editor, "r.refresh()")
        marked_files(expected)
        self.lua(editor, "r.leave(); r.open(); require('rediff.live').stop(r.state)")
        marked_files(expected)
        self.lua(editor, "r.jump_comment(s.comments[1]); r.delete_comment()")
        marked_files(expected)
        self.lua(editor, "r.jump_comment(s.comments[1]); r.delete_comment()")
        marked_files(["plan.md"])
        self.lua(editor, "r.archive()")
        marked_files([])

    def test_compact_orange_annotations(self):
        path = Path(self.root, "auth.lua")
        path.write_text(
            "-- inserted line one\n-- inserted line two\n" + path.read_text()
        )
        original = path.read_bytes()
        editor = self.launch(self.root)
        self.lua(
            editor,
            """
            local selection = require('rediff.selection')
            local function note(side, line, text)
                r.add_comment({file=s.current.path, side=side, snapshot_id=s.current.id,
                    selection=selection.line(side=='old' and s.old_buf or s.new_buf,line)},text)
            end
            note('new',7,'Check token.\\nKeep this exact.')
            note('old',17,string.rep('界 boundary ',8))
            note('new',7,'Second note')
            """,
        )

        def marks(side):
            return self.lua(
                editor,
                f"return vim.api.nvim_buf_get_extmarks(s.{side}_buf, "
                'vim.api.nvim_get_namespaces()["rediff.annotations"], 0, -1, {details=true})',
            )

        for side, expected_rows in (
            ("old", {251: 4, 252: 16, 253: 4}),
            ("new", {251: 6, 252: 18, 253: 6}),
        ):
            rendered = marks(side)
            self.assertEqual(3, len(rendered))
            for _, row, _, details in rendered:
                self.assertEqual(expected_rows[details["priority"]], row)
                self.assertFalse(details.get("virt_lines_above", False))
                self.assertNotIn("sign_text", details)
        new_note = next(
            mark[3]["virt_lines"] for mark in marks("new") if mark[3]["priority"] == 251
        )
        self.assertEqual(
            [
                [["● Check token.", "ReviewAnnotation"]],
                [["  Keep this exact.", "ReviewAnnotation"]],
            ],
            new_note,
        )
        old_note = next(
            mark[3]["virt_lines"] for mark in marks("old") if mark[3]["priority"] == 252
        )
        self.assertGreater(len(old_note), 1)
        self.assertEqual(
            "界 boundary " * 8, "".join(line[0][0][2:] for line in old_note)
        )
        self.assertTrue(all(line[0][1] == "ReviewAnnotation" for line in old_note))
        padding = next(
            mark[3]["virt_lines"] for mark in marks("new") if mark[3]["priority"] == 252
        )
        self.assertEqual(len(old_note), len(padding))
        self.assertTrue(all(not line for line in padding))
        self.assertTrue(
            self.lua(
                editor,
                "return vim.fn.screenpos(s.old_win,8,1).row == vim.fn.screenpos(s.new_win,10,1).row",
            )
        )
        editor.command("colorscheme rose-pine")
        self.assertEqual(
            0xFF9E64, editor.api.get_hl(0, {"name": "ReviewAnnotation"})["fg"]
        )
        self.keys(editor, ":view merged<CR>")
        self.assertEqual(2, len(marks("new")))
        self.assertEqual([], marks("old"))
        self.keys(editor, ":view split<CR>")
        self.assertEqual(3, len(marks("new")))
        self.assertEqual(original, path.read_bytes())

    def test_annotation_cursor_gutter_marker(self):
        editor = self.launch(self.root)
        self.lua(
            editor,
            """
            local selection = require('rediff.selection')
            local range = selection.capture(s.new_buf, 'V', {s.new_buf,3,1,0}, {s.new_buf,5,1,0})
            r.add_comment({file=s.current.path, side='new', snapshot_id=s.current.id,
                selection=range}, 'Range note\\nSecond line')
            r.add_comment({file=s.current.path, side='new', snapshot_id=s.current.id,
                selection=selection.line(s.new_buf,3)}, 'Overlapping note')
            r.add_comment({file=s.current.path, side='old', snapshot_id=s.current.id,
                selection=selection.line(s.old_buf,17)}, 'Old note')
            """,
        )

        def marked(side):
            marks = self.lua(
                editor,
                f"return vim.api.nvim_buf_get_extmarks(s.{side}_buf, "
                'vim.api.nvim_get_namespaces()["rediff.annotations"], 0, -1, {details=true})',
            )
            selected = []
            gutter = self.lua(
                editor, f"return vim.fn.getwininfo(s.{side}_win)[1].textoff"
            )
            for _, _, _, details in marks:
                lines = details["virt_lines"]
                if not lines or not lines[0]:
                    continue
                if details.get("virt_lines_leftcol", False):
                    self.assertEqual(
                        [" " * (gutter - 3) + ">> ", "ReviewAnnotation"], lines[0][0]
                    )
                    self.assertTrue(lines[0][1][0].startswith("● "))
                    self.assertTrue(all(row[0][0] == " " * gutter for row in lines[1:]))
                    selected.append(details["priority"] - 250)
                else:
                    self.assertTrue(lines[0][0][0].startswith("● "))
            return sorted(selected)

        self.assertEqual([], marked("new"))  # Tree focus never selects a note.
        self.lua(editor, "vim.api.nvim_set_current_win(s.new_win)")
        for line, expected in ((2, []), (3, [1, 2]), (4, [1]), (5, [1]), (6, [])):
            self.keys(editor, f"{line}G")
            self.assertEqual(expected, marked("new"))
            self.assertEqual([], marked("old"))
            self.assertIsNone(self.lua(editor, "return s.annotation_id"))

        # Jumps to overlapping notes share a cursor position but select only one.
        self.lua(editor, "r.jump_comment(s.comments[1])")
        self.assertEqual([1], marked("new"))
        self.keys(editor, "}")
        self.assertEqual([2], marked("new"))
        self.keys(editor, ":view merged<CR>")
        self.assertEqual([2], marked("new"))
        self.keys(editor, "}")  # An old-side note reopens split view.
        self.assertEqual([3], marked("old"))
        self.assertEqual([], marked("new"))
        self.keys(editor, "<Tab>")
        self.assertEqual([], marked("old"))
        self.keys(editor, "<Tab>")
        self.assertEqual([], marked("old"))  # Tab returns to the new-side pane.
        self.lua(editor, "vim.api.nvim_set_current_win(s.old_win)")
        self.assertEqual([3], marked("old"))
        editor.command("colorscheme rose-pine")
        self.assertEqual(
            0xFF9E64, editor.api.get_hl(0, {"name": "ReviewAnnotation"})["fg"]
        )
        self.assertNotIn("bg", editor.api.get_hl(0, {"name": "ReviewAnnotation"}))

    def test_single_annotation_navigation_does_not_reopen_current_note(self):
        editor = self.launch(self.root)
        self.lua(
            editor,
            """
            -- Count navigation reloads independently of the background Git poll.
            require('rediff.live').stop(s)
            local selection = require('rediff.selection')
            r.add_comment({file=s.current.path, side='new', snapshot_id=s.current.id,
                selection=selection.capture(s.new_buf,'V',{s.new_buf,3,1,0},{s.new_buf,5,1,0})},
                'Only annotation')
            local show = r.show
            vim.g.annotation_show_count = 0
            r.show = function(...)
                vim.g.annotation_show_count = vim.g.annotation_show_count + 1
                return show(...)
            end
            vim.api.nvim_set_current_win(s.new_win)
            """,
        )
        self.keys(editor, "4Gzt")
        view = self.lua(editor, "return vim.fn.winsaveview()")
        for keys in ("}", "{", "3}", "3{"):
            self.keys(editor, keys)
            self.assertEqual(view, self.lua(editor, "return vim.fn.winsaveview()"))
            self.assertEqual(0, self.lua(editor, "return vim.g.annotation_show_count"))
            self.assertIsNone(self.lua(editor, "return s.annotation_id"))

        # A lone note remains reachable from elsewhere, including the tree.
        for origin in ("10G", "<Tab>"):
            self.keys(editor, origin + "}")
            self.assertTrue(
                self.lua(editor, "return vim.api.nvim_get_current_win() == s.new_win")
            )
            self.assertEqual(
                3, self.lua(editor, "return vim.api.nvim_win_get_cursor(0)[1]")
            )
            self.keys(editor, "zt")
            view = self.lua(editor, "return vim.fn.winsaveview()")
            shows = self.lua(editor, "return vim.g.annotation_show_count")
            self.keys(editor, "}{")
            self.assertEqual(view, self.lua(editor, "return vim.fn.winsaveview()"))
            self.assertEqual(
                shows, self.lua(editor, "return vim.g.annotation_show_count")
            )

    def test_startup_opens_configured_harness_once(self):
        binary = Path(self.directory.name, "bin")
        binary.mkdir()
        cli = binary / "amp"
        log = Path(self.directory.name, "startup-harness")
        cli.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$PWD" >> "$HARNESS_TEST_LOG"\n'
            'echo "Startup harness ready"\nexec sleep 600\n'
        )
        cli.chmod(0o755)
        os.environ["PATH"] = str(binary) + os.pathsep + os.environ["PATH"]
        os.environ["HARNESS_TEST_LOG"] = str(log)
        self.fixture.exec_lua(
            "local f=require('rediff.feedback'); local p=vim.fn.stdpath('config'); "
            "vim.fn.mkdir(p,'p'); f.write(p..'/settings.json',{harness='amp'})"
        )
        for directory, options in (
            (self.root, {"headless": True}),
            (self.root, {"file": "auth.lua"}),
            (self.directory.name, {"file": self.root}),
            (self.directory.name, {}),
        ):
            self.launch(directory, **options)
            self.assertFalse(
                log.exists(), "Only bare interactive Git startup opens a harness"
            )
        missing = self.launch(
            self.root,
            before_init="""
            local exepath=vim.fn.exepath
            vim.fn.exepath=function(name) return name=='amp' and '' or exepath(name) end
        """,
        )
        self.assertTrue(self.lua(missing, "return r.active() ~= nil"))
        self.wait_for(missing, "vim.g.startup_notice ~= nil")
        self.assertIn("Could not open harness", missing.vars["startup_notice"])
        self.assertFalse(log.exists())
        self.keys(missing, ":ReviewLeave<CR>")
        editor = self.launch(self.root)
        self.wait_for(editor, "vim.bo.buftype == 'terminal'")
        self.wait_for(editor, "vim.fn.filereadable(vim.env.HARNESS_TEST_LOG) == 1")
        self.assertEqual([self.root], log.read_text().splitlines())
        self.assertEqual("t", editor.api.get_mode()["mode"])
        self.assertEqual(4, len(editor.api.tabpage_list_wins(0)))
        job = editor.current.buffer.vars["terminal_job_id"]
        self.keys(editor, "<Esc>:hide<CR><Space>r<Space>r")
        self.assertEqual(3, len(editor.api.tabpage_list_wins(0)))
        self.keys(editor, ":ho<CR>")
        self.assertEqual(job, editor.current.buffer.vars["terminal_job_id"])
        self.assertEqual([self.root], log.read_text().splitlines())

    def test_startup_waits_for_restored_harness_without_stealing_focus(self):
        binary = Path(self.directory.name, "bin")
        binary.mkdir()
        cli = binary / "amp"
        log = Path(self.directory.name, "resumed-harness")
        cli.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$PWD" "$@" > "$HARNESS_TEST_LOG"\n'
            'if [ "$HARNESS_TEST_FAIL" = 1 ]; then echo "Thread is active somewhere else"; exit 1; fi\n'
            'echo "Resumed harness ready"\nexec sleep 600\n'
        )
        cli.chmod(0o755)
        os.environ["PATH"] = str(binary) + os.pathsep + os.environ["PATH"]
        os.environ["HARNESS_TEST_LOG"] = str(log)
        thread = "T-11111111-2222-4333-8444-555555555555"
        for outcome in (
            "connect", "leave", "superseded", "ambiguous", "resume", "new", "invalidated", "error", "exit-error",
        ):
            os.environ["HARNESS_TEST_FAIL"] = "1" if outcome == "exit-error" else "0"
            root = self.fixture.exec_lua(
                "local root=dofile(...).create(); return root", FIXTURE
            )
            self.fixture.exec_lua(
                """
                local root,thread=...
                local f=require('rediff.feedback')
                f.write(f.directory(root)..'/harness.json',{
                    provider='amp',target={name='amp',session=thread}})
            """,
                root,
                thread,
            )
            editor = self.launch(
                root,
                before_init="""
                local system=vim.system
                vim.system=function(argv,opts,callback)
                    if argv[1]=='rediff-amp-live' and argv[2]=='startup' then
                        _G.startup_restore=callback; return
                    end
                    if argv[1]=='rediff-amp-live' and argv[2]=='discover' then
                        callback({code=0,stdout='[]'}); return
                    end
                    return system(argv,opts,callback)
                end
                vim.fn.confirm=function(prompt) vim.g.resume_prompt=prompt; return 2 end
                vim.ui.select=function(items,opts,callback) vim.g.choices=items; callback(nil) end
            """,
            )
            self.assertIsNone(editor.vars.get("resume_prompt"))
            self.assertFalse(log.exists(), "Wait for discovery before resuming")
            if outcome == "leave":
                self.keys(editor, "<Space>r")
            if outcome == "superseded":
                self.lua(editor, "require('rediff.harness').select(s.root,{name='none'})")
            editor.exec_lua(
                """
                local outcome,thread=...
                local root=vim.fn.getcwd()
                local decision={action='connect',matches={{root=root,session=thread,connection='/fake/live'}}}
                if outcome=='ambiguous' then
                    table.insert(decision.matches,{root=root,session='T-second',connection='/fake/second'})
                elseif outcome=='resume' or outcome=='exit-error' then
                    decision={action='resume',session=thread}
                elseif outcome=='new' or outcome=='invalidated' then
                    decision={action='new',invalidated=outcome=='invalidated' and thread or nil}
                end
                startup_restore({code=outcome=='error' and 1 or 0,stdout=vim.json.encode(decision),stderr='Lookup unavailable'})
            """,
                outcome,
                thread,
            )
            pump(editor)
            if outcome in ("connect", "leave", "superseded", "ambiguous", "error"):
                self.assertIsNone(editor.vars.get("resume_prompt"))
                self.assertFalse(log.exists(), outcome + " must not launch a process")
                self.assertNotEqual(
                    "terminal", editor.current.buffer.options["buftype"]
                )
                if outcome == "connect":
                    self.assertEqual("/fake/live", self.lua(editor, "return s.harness.connection"))
                    self.assertIn("existing terminal", editor.vars["startup_notice"])
                elif outcome == "leave":
                    self.assertTrue(self.lua(editor, "return r.active() == nil"))
                elif outcome == "superseded":
                    self.assertEqual("none", self.lua(editor, "return s.harness.name"))
                elif outcome == "ambiguous":
                    self.assertEqual(2, len(editor.vars["choices"]))
                else:
                    self.assertIn("Lookup unavailable", editor.vars["startup_notice"])
                    self.assertEqual(thread, self.lua(editor, "return s.harness.session"))
            else:
                self.wait_for(
                    editor, "vim.fn.filereadable(vim.env.HARNESS_TEST_LOG)==1"
                )
                self.assertIsNone(editor.vars.get("resume_prompt"))
                argv = ["threads", "continue", thread] if outcome in ("resume", "exit-error") else []
                self.assertEqual(
                    [root, *argv], log.read_text().splitlines()
                )
                self.assertEqual("terminal", editor.current.buffer.options["buftype"])
                if outcome == "exit-error":
                    self.wait_for(editor, "require('rediff.harness').get(s.root).delivery == 'disconnected'")
                    self.assertEqual(thread, self.lua(editor, "return s.harness.session"))
                    self.assertIn("active somewhere else", "\n".join(editor.current.buffer[:]))
                else:
                    self.assertEqual("t", editor.api.get_mode()["mode"])
                if not argv:
                    self.assertIsNone(self.lua(editor, "return s.harness.session"))
                    self.assertFalse(editor.exec_lua(
                        "for _,entry in ipairs(require('rediff.connections').connected()) do "
                        "if entry.root == ... then return true end end; return false", root
                    ))
                    saved = editor.exec_lua("local f=require('rediff.feedback'); return f.read(f.directory(... )..'/harness.json')", root)
                    self.assertNotIn("session", saved["target"])
                self.lua(editor, "require('rediff.harness').stop(s.root)")
                log.unlink()

    def test_startup_and_lock(self):
        index = subprocess.check_output(["git", "-C", self.root, "write-tree"])
        editor = self.launch(self.root)
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
            self.lua(editor, 'return require("rediff.harness").get(s.root).last == nil')
        )
        locked = self.launch(self.root)
        self.assertTrue(self.lua(locked, "return s == nil"))
        self.assertIn(
            "Another editor owns", self.lua(locked, "return vim.g.startup_notice")
        )
        self.keys(editor, " q")
        self.wait_for(editor, 'vim.bo.filetype == "neo-tree"')
        self.assertEqual(
            self.root,
            self.lua(
                editor,
                "return require('neo-tree.sources.manager').get_state('filesystem').path",
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
            self.lua(outside, "return s == nil and #vim.api.nvim_list_wins() == 1")
        )

    def test_directory_argument_explorer_git_markers(self):
        root = Path(self.root)
        (root / "modified-dir").mkdir()
        (root / "modified-dir" / "changed.txt").write_text("before\n")
        (root / "modified-dir" / "deleted.txt").write_text("remove me\n")
        (root / "clean.txt").write_text("unchanged\n")
        for args in (
            ["add", "modified-dir", "clean.txt"],
            ["commit", "-m", "Explorer fixture"],
        ):
            subprocess.run(["git", "-C", self.root, *args], check=True, capture_output=True)
        (root / "modified-dir" / "changed.txt").write_text("after\n")
        (root / "modified-dir" / "deleted.txt").unlink()
        (root / "new-dir").mkdir()
        (root / "new-dir" / "brand-new.txt").write_text("new\n")
        (root / "added.txt").write_text("staged addition\n")
        subprocess.run(["git", "-C", self.root, "add", "added.txt", "auth.lua"], check=True)
        # Launch from outside the repository, as with `nvim cortex`.
        editor = self.launch(self.directory.name, file=self.root)
        self.wait_for(editor, "vim.bo.filetype == 'neo-tree'")
        self.assertTrue(self.lua(editor, "return s == nil"))
        self.wait_for(
            editor,
            "table.concat(vim.api.nvim_buf_get_lines(0,0,-1,false),'\\n'):match('auth%.lua[^\\n]*M') ~= nil",
        )
        expected = {
            "modified-dir": ("M", 0xFACC15),
            "new-dir": ("U", 0x4ADE80),
            "auth.lua": ("M", 0xFACC15),
            "added.txt": ("A", 0x4ADE80),
            "plan.md": ("U", 0x4ADE80),
        }
        for phase in ("collapsed", "expanded", "theme reload"):
            if phase == "expanded":
                for name in ("modified-dir", "new-dir"):
                    self.assertGreater(editor.funcs.search(name, "w"), 0)
                    self.keys(editor, "<CR>")
                expected.update(
                    {
                        "changed.txt": ("M", 0xFACC15),
                        "brand-new.txt": ("U", 0x4ADE80),
                    }
                )
            elif phase == "theme reload":
                editor.command("colorscheme rose-pine")
                pump(editor)
            lines = list(editor.current.buffer[:])
            self.assertFalse(
                any("deleted.txt" in line or "removed.lua" in line for line in lines)
            )
            for name, (marker, color) in expected.items():
                with self.subTest(phase=phase, name=name):
                    row = next(i for i, line in enumerate(lines, 1) if name in line)
                    self.assertRegex(lines[row - 1], rf"\s{marker}(?:\s|$)")
                    self.keys(editor, f"{row}G")
                    cell = self.lua(
                        editor,
                        "local line=vim.api.nvim_get_current_line(); "
                        "local col=line:find('%s[AMU]%s')+1; "
                        f"local p=vim.fn.screenpos(0,{row},col); "
                        "return vim.api.nvim__inspect_cell(1,p.row-1,p.col-1)",
                    )
                    self.assertEqual(marker, cell[0])
                    self.assertEqual(color, cell[1]["foreground"])
            clean = next(line for line in lines if "clean.txt" in line)
            self.assertNotRegex(clean, r"\s[AMU](?:\s|$)")
        self.assertGreater(editor.funcs.search("changed.txt", "w"), 0)
        self.keys(editor, "<CR>")
        self.wait_for(editor, "vim.bo.filetype ~= 'neo-tree'")
        self.assertEqual(
            (root / "modified-dir" / "changed.txt").resolve(),
            Path(editor.current.buffer.name).resolve(),
        )

    def test_leaving_startup_review_can_explore_worktree_subdirectories(self):
        worktree = Path(self.directory.name, "linked-worktree")
        subprocess.run(
            [
                "git",
                "-C",
                self.root,
                "worktree",
                "add",
                "--detach",
                str(worktree),
                "HEAD",
            ],
            check=True,
            capture_output=True,
        )
        folder = worktree / "nested"
        folder.mkdir()
        child = folder / "child.txt"
        child.write_text("Explore this file\n")
        editor = self.launch(str(worktree))
        self.assertTrue(self.lua(editor, "return r.active() ~= nil"))
        self.keys(editor, " r")
        self.wait_for(editor, 'vim.bo.filetype == "neo-tree"')
        self.assertEqual(
            str(worktree.resolve()),
            self.lua(
                editor,
                "return vim.uv.fs_realpath(require('neo-tree.sources.manager').get_state('filesystem').path)",
            ),
        )
        self.assertGreater(editor.funcs.search("nested", "w"), 0)
        self.keys(editor, "<CR>")
        self.wait_for(
            editor,
            "table.concat(vim.api.nvim_buf_get_lines(0,0,-1,false),'\\n'):find('child.txt',1,true) ~= nil",
        )
        self.keys(editor, "j<CR>")
        self.wait_for(editor, "vim.bo.filetype ~= 'neo-tree'")
        self.assertEqual(child.resolve(), Path(editor.current.buffer.name).resolve())
        self.assertEqual(["Explore this file"], list(editor.current.buffer[:]))

    def test_space_r_toggles_review(self):
        editor = self.launch(self.root, file="auth.lua")
        buffer = editor.current.buffer
        saved = Path(buffer.name).read_text()
        buffer[0] = "-- unsaved edit"
        index = subprocess.check_output(["git", "-C", self.root, "write-tree"])
        for focus in ("", "<Tab>"):
            self.keys(editor, " r")
            self.assertTrue(self.lua(editor, "return r.active() ~= nil"))
            # From the editing tab, focus the existing Review rather than closing it.
            self.lua(
                editor,
                "_G.review_tab=s.tab; vim.api.nvim_set_current_tabpage(s.previous_tab)",
            )
            self.keys(editor, " r")
            self.assertTrue(
                self.lua(editor, "return r.active() ~= nil and s.tab == review_tab")
            )
            if focus:
                self.keys(editor, focus)
            self.keys(editor, " r")
            self.assertTrue(self.lua(editor, "return s == nil"))
            self.assertEqual(1, len(editor.tabpages))
            self.assertEqual(buffer.number, editor.current.buffer.number)
            self.assertEqual("-- unsaved edit", buffer[0])
            self.assertTrue(buffer.options["modified"])
        self.assertEqual(saved, Path(buffer.name).read_text())
        self.assertEqual(
            index, subprocess.check_output(["git", "-C", self.root, "write-tree"])
        )

    def test_file_arguments_open_for_editing(self):
        outside_file = Path(self.directory.name, "plain file.txt")
        outside_file.write_text("Ordinary file outside Git\n")
        for directory, filename in (
            (self.root, "auth.lua"),
            (self.root, "new file.txt"),
            (self.directory.name, str(outside_file)),
            (self.directory.name, str(Path(self.root, "plan.md"))),
        ):
            with self.subTest(directory=directory, file=filename):
                path = Path(directory, filename).resolve()
                editor = self.launch(directory, file=filename)
                self.assertTrue(
                    self.lua(editor, "return s == nil and vim.g.startup_notice == nil")
                )
                self.assertEqual(str(path), editor.current.buffer.name)
                self.assertEqual(1, len(editor.windows))
                self.assertEqual(1, len(editor.tabpages))
                self.assertTrue(editor.current.buffer.options["modifiable"])
                self.assertEqual(
                    path.read_text().splitlines() if path.exists() else [""],
                    editor.current.buffer[:],
                )
                if filename == "auth.lua":
                    self.keys(editor, " r")
                    self.assertTrue(self.lua(editor, "return r.active() ~= nil"))
                    self.keys(editor, " q")
                    self.assertEqual(str(path), editor.current.buffer.name)

    def test_split_balances_on_resize(self):
        editor = self.launch(self.root, columns=80)
        self.keys(editor, "<Tab>")
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

    def test_tree_status_marker_colors(self):
        Path(self.root, "original.lua").write_text("return 'rename fixture'\n")
        for args in (
            ["add", "original.lua"],
            ["commit", "-m", "Add rename fixture"],
            ["mv", "original.lua", "renamed.lua"],
        ):
            subprocess.run(["git", "-C", self.root, *args], check=True, capture_output=True)
        Path(self.root, "added.lua").write_text("return 'new file'\n")
        subprocess.run(["git", "-C", self.root, "add", "added.lua"], check=True)
        editor = self.launch(self.root)
        self.lua(editor, "require('rediff.live').stop(s)")
        expected = {
            "A": ("ReviewAdded", 0x4ADE80),
            "M": ("ReviewModified", 0xFACC15),
            "U": ("ReviewUntracked", 0x4ADE80),
            "D": ("ReviewDeleted", 0xF87171),
            "R": ("ReviewRenamed", 0xA3A3A3),
        }
        for phase in ("startup", "theme reload", "staging refresh"):
            if phase == "theme reload":
                editor.command("colorscheme rose-pine")
            elif phase == "staging refresh":
                subprocess.run(["git", "-C", self.root, "add", "auth.lua"], check=True)
                self.lua(editor, "r.refresh()")
            rows = self.lua(
                editor,
                "local rows = {}; for row, i in pairs(s.rows) do "
                "table.insert(rows, {row, s.entries[i].status}) end; return rows",
            )
            self.assertEqual(set(expected), {status for _, status in rows})
            for row, status in rows:
                with self.subTest(phase=phase, status=status, row=row):
                    group, color = expected[status]
                    mark = self.lua(
                        editor,
                        "return vim.api.nvim_buf_get_extmark_by_id(s.tree_buf, "
                        f"vim.api.nvim_get_namespaces()['rediff.tree'], {row}, {{details=true}})",
                    )
                    self.assertEqual([row - 1, 1], mark[:2])
                    self.assertEqual(2, mark[2]["end_col"], "Color only the marker")
                    self.assertEqual(group, mark[2]["hl_group"])
                    self.assertEqual(color, editor.api.get_hl(0, {"name": group})["fg"])
                    self.assertNotIn("bg", editor.api.get_hl(0, {"name": group}))
                    # Read rendered cells with the row selected and with diff focus.
                    self.keys(editor, f" e{row}G")
                    for focus in (" e", " d"):
                        self.keys(editor, focus)
                        rendered = self.lua(
                            editor,
                            f"local p = vim.fn.screenpos(s.tree_win, {row}, 2); "
                            "return vim.api.nvim__inspect_cell(1, p.row - 1, p.col - 1)",
                        )
                        self.assertEqual(status, rendered[0])
                        self.assertEqual(color, rendered[1]["foreground"])

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
            [],
            self.lua(
                editor,
                'local rows = {}; for _, mark in ipairs(vim.api.nvim_buf_get_extmarks(s.tree_buf, vim.api.nvim_get_namespaces()["rediff.active-file"], 0, -1, {})) do table.insert(rows, mark[2]) end; return rows',
            ),
        )
        self.assertTrue(
            self.lua(editor, "return vim.api.nvim_get_current_win() == s.tree_win")
        )
        # Absolute motions reach empty headers without changing the preview.
        self.keys(editor, "gg")
        self.assertEqual(
            self.lua(editor, "return s.group_rows.staged"),
            editor.current.window.cursor[0],
        )
        self.assertEqual("plan.md", self.lua(editor, "return s.current.path"))
        # Jumping directly to a file still selects and displays it.
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

    def test_tree_navigation_skips_non_file_rows(self):
        subprocess.run(["git", "-C", self.root, "add", "auth.lua"], check=True)
        index = subprocess.check_output(["git", "-C", self.root, "write-tree"])
        editor = self.launch(self.root)
        for keys, path in (
            ("k", "auth.lua"),
            ("<Up>", "auth.lua"),
            ("j", "removed.lua"),
            ("l", "removed.lua"),
            ("<Right>", "removed.lua"),
            ("<Up>", "auth.lua"),
            ("<Down>", "removed.lua"),
            ("<Down>", "plan.md"),
            ("j", "plan.md"),
            ("<Down>", "plan.md"),
            ("gg", "auth.lua"),
            ("G", "plan.md"),
            ("k", "removed.lua"),
            ("k", "auth.lua"),
        ):
            with self.subTest(keys=keys, path=path):
                self.keys(editor, keys)
                self.assertEqual(path, self.lua(editor, "return s.current.path"))
                self.assertTrue(
                    self.lua(
                        editor,
                        "local cursor = vim.api.nvim_win_get_cursor(s.tree_win); "
                        "return vim.api.nvim_get_current_win() == s.tree_win "
                        "and s.rows[cursor[1]] == s.index and cursor[2] == 0",
                    )
                )
        self.assertEqual(
            index, subprocess.check_output(["git", "-C", self.root, "write-tree"])
        )

    def test_tree_groups_collapse_and_restore_file_selection(self):
        # Two staged and two unstaged entries, with different remembered files.
        subprocess.run(
            ["git", "-C", self.root, "add", "auth.lua", "removed.lua"], check=True
        )
        path = Path(self.root, "auth.lua")
        path.write_text(path.read_text().replace("M.timeout = 30", "M.timeout = 60"))
        index = subprocess.check_output(["git", "-C", self.root, "write-tree"])
        editor = self.launch(self.root)
        self.lua(editor, "require('rediff.live').stop(s)")

        def heading(group):
            self.assertTrue(
                self.lua(editor, "return vim.api.nvim_get_current_win()==s.tree_win")
            )
            self.assertEqual(
                self.lua(editor, f"return s.group_rows.{group}"),
                editor.current.window.cursor[0],
            )
            self.assertTrue(self.lua(editor, f"return s.collapsed.{group}==true"))
            self.assertEqual(
                "ReviewTreeSelection",
                self.lua(
                    editor,
                    f"return vim.api.nvim_buf_get_extmark_by_id(s.tree_buf, "
                    f"vim.api.nvim_create_namespace('rediff.tree'), s.group_rows.{group}, "
                    "{details=true})[3].line_hl_group",
                ),
            )
            self.assertTrue(
                editor.eval("foldtextresult(line('.'))").startswith(
                    f" ▸ {group.upper()}"
                )
            )

        def selected(group, path):
            self.assertEqual(
                [group, path, True],
                self.lua(
                    editor,
                    "return {s.current.group,s.current.path, "
                    "s.rows[vim.api.nvim_win_get_cursor(s.tree_win)[1]]==s.index}",
                ),
            )
            self.assertEqual(-1, editor.eval("foldclosed(line('.'))"))

        self.keys(editor, "j")
        selected("staged", "removed.lua")
        tick = self.lua(editor, "return vim.api.nvim_buf_get_changedtick(s.new_buf)")
        for left, right in (("h", "l"), ("<Left>", "<Right>"), ("h", "<CR>")):
            for _ in range(2):
                self.keys(editor, left + left)
                heading("staged")
                self.keys(editor, right + right)
                selected("staged", "removed.lua")
                self.assertEqual(
                    tick,
                    self.lua(
                        editor, "return vim.api.nvim_buf_get_changedtick(s.new_buf)"
                    ),
                )
        self.keys(editor, "hjj")
        selected("untracked", "plan.md")
        self.keys(editor, "h")
        heading("unstaged")
        self.keys(editor, "k")
        heading("staged")
        self.keys(editor, "j")
        heading("unstaged")
        self.keys(editor, "<Up>")
        heading("staged")
        self.keys(editor, "<Down>")
        heading("unstaged")
        self.keys(editor, "<CR><CR>")
        selected("untracked", "plan.md")
        self.keys(editor, "h")
        heading("unstaged")

        # Refresh can shift row numbers, but must preserve both folds and focus.
        Path(self.root, "aaa-new.lua").write_text("return 1\n")
        self.lua(editor, "r.refresh_live()")
        heading("unstaged")
        self.assertTrue(self.lua(editor, "return s.collapsed.staged"))
        self.keys(editor, "l")
        selected("untracked", "plan.md")
        self.keys(editor, "hk<Right>")
        selected("staged", "removed.lua")

        # Explicit group navigation reveals its target even when folded.
        self.keys(editor, ":fu<CR>")
        selected("unstaged", "auth.lua")
        self.assertIn(" ▾ UNSTAGED (3)", editor.current.buffer[:])
        self.keys(editor, "h:fs<CR>h")
        heading("staged")
        self.keys(editor, "]")
        self.assertFalse(self.lua(editor, "return s.collapsed.staged==true"))

        # A vanished remembered file falls back to a remaining file, not a blank.
        self.keys(editor, ":fu<CR>jjh")
        Path(self.root, "plan.md").unlink()
        self.lua(editor, "r.refresh_live()")
        heading("unstaged")
        self.keys(editor, "l")
        self.assertTrue(
            self.lua(
                editor, "return s.rows[vim.api.nvim_win_get_cursor(s.tree_win)[1]]~=nil"
            )
        )
        self.assertNotEqual("plan.md", self.lua(editor, "return s.current.path"))
        self.assertEqual(
            index, subprocess.check_output(["git", "-C", self.root, "write-tree"])
        )

    def test_tree_header_selection_has_no_second_file_highlight(self):
        subprocess.run(["git", "-C", self.root, "add", "auth.lua"], check=True)
        editor = self.launch(self.root)
        self.lua(editor, "require('rediff.live').stop(s)")
        self.keys(editor, "hj")
        self.assertEqual("removed.lua", self.lua(editor, "return s.current.path"))
        self.keys(editor, "k")

        def selection():
            return self.lua(
                editor,
                """
                local ns = vim.api.nvim_get_namespaces()
                local marks = vim.api.nvim_buf_get_extmarks(s.tree_buf, ns['rediff.active-file'], 0, -1, {})
                local header = vim.api.nvim_buf_get_extmark_by_id(s.tree_buf, ns['rediff.tree'],
                    s.group_rows.staged, {details=true})[3].line_hl_group
                return {vim.api.nvim_win_get_cursor(s.tree_win)[1] == s.group_rows.staged,
                    header, #marks, s.current.path, s.collapsed.unstaged == true}
                """,
            )

        expected = [True, "ReviewTreeSelection", 0, "removed.lua", False]
        self.assertEqual(expected, selection())
        self.lua(editor, "r.refresh_live()")
        self.assertEqual(
            expected, selection(), "Refresh must not restore a second highlight"
        )
        self.keys(editor, "<Tab>")
        self.assertEqual([True, "ReviewStaged", 1, "removed.lua", False], selection())
        self.assertTrue(
            self.lua(
                editor,
                "local mark=vim.api.nvim_buf_get_extmarks(s.tree_buf, "
                "vim.api.nvim_get_namespaces()['rediff.active-file'],0,-1,{})[1]; "
                "return s.rows[mark[2]+1] == s.index",
            )
        )
        self.keys(editor, "<Tab>")
        self.assertEqual(
            expected,
            selection(),
            "Returning to the tree restores only its cursor highlight",
        )

    def test_collapsed_tree_navigation_reaches_empty_groups(self):
        for empty, populated, away, back in (
            ("staged", "unstaged", "<Up>", "<Down>"),
            ("unstaged", "staged", "<Down>", "<Up>"),
        ):
            with self.subTest(empty=empty):
                if empty == "unstaged":
                    subprocess.run(["git", "-C", self.root, "add", "."], check=True)
                index = subprocess.check_output(["git", "-C", self.root, "write-tree"])
                editor = self.launch(self.root)
                self.lua(editor, "require('rediff.live').stop(s)")

                def heading(editor, group):
                    self.assertEqual(
                        self.lua(editor, f"return s.group_rows.{group}"),
                        editor.current.window.cursor[0],
                    )
                    self.assertEqual("ReviewTreeSelection", highlight(editor, group))

                def highlight(editor, group):
                    return self.lua(
                        editor,
                        f"return vim.api.nvim_buf_get_extmark_by_id(s.tree_buf, "
                        f"vim.api.nvim_create_namespace('rediff.tree'), s.group_rows.{group}, "
                        "{details=true})[3].line_hl_group",
                    )

                self.keys(editor, "h")
                heading(editor, populated)
                self.keys(editor, away)
                heading(editor, empty)
                self.lua(editor, "r.refresh_live()")
                heading(editor, empty)
                self.keys(editor, "<Tab>")
                self.assertNotEqual("ReviewTreeSelection", highlight(editor, empty))
                self.keys(editor, "<Tab>")
                heading(editor, empty)
                self.keys(editor, "h<CR><CR>")
                heading(editor, empty)
                self.assertEqual(-1, editor.eval("foldclosed(line('.'))"))
                self.keys(editor, back)
                heading(editor, populated)
                self.keys(editor, "l")
                self.assertTrue(
                    self.lua(
                        editor,
                        "return s.rows[vim.api.nvim_win_get_cursor(s.tree_win)[1]]==s.index",
                    )
                )
                self.assertEqual(
                    index,
                    subprocess.check_output(["git", "-C", self.root, "write-tree"]),
                )
                self.lua(editor, "r.leave()")

    def test_tab_focuses_diff_for_hunk_staging(self):
        editor = self.launch(self.root)
        baseline = subprocess.check_output(
            ["git", "-C", self.root, "show", "HEAD:auth.lua"]
        )
        changed = Path(self.root, "auth.lua").read_bytes()
        for layout in ("split", "merged"):
            self.keys(editor, f":view {layout}<CR>")
            self.keys(editor, "]")
            self.keys(editor, " e")
            self.keys(editor, "<Tab>")
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
            self.keys(editor, "<Tab>")
            self.keys(editor, "s")
            self.assertEqual(
                baseline,
                subprocess.check_output(["git", "-C", self.root, "show", ":auth.lua"]),
            )
            self.assertEqual(changed, Path(self.root, "auth.lua").read_bytes())
        self.keys(editor, "<Tab>")
        self.keys(editor, "<CR>")
        self.assertTrue(
            self.lua(
                editor,
                "return vim.api.nvim_get_current_win() == s.tree_win and s.composer == nil",
            )
        )
        self.keys(editor, "j")
        self.keys(editor, "<Tab>")
        self.assertEqual(
            [True, "removed.lua"],
            self.lua(
                editor,
                "return {vim.api.nvim_get_current_win() == s.new_win, s.current.path}",
            ),
        )

    def test_hunk_staging_ignores_metadata_words_in_file_contents(self):
        baseline = [f"original line {i}\n" for i in range(1, 46)]
        baseline[3:5] = ["new file mode 100644\n", "deleted file mode 100644\n"]
        path = Path(self.root, "auth.lua")
        path.write_text("".join(baseline))
        subprocess.run(["git", "-C", self.root, "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", self.root, "commit", "-qm", "Metadata words fixture"],
            check=True,
        )
        changed = baseline.copy()
        changed[5], changed[35] = "first edit\n", "later edit\n"
        path.write_text("".join(changed))
        editor = self.launch(self.root)
        self.keys(editor, "<Tab>")
        self.keys(editor, "s")
        partial = baseline.copy()
        partial[5] = changed[5]
        self.assertEqual(
            "".join(partial),
            subprocess.check_output(
                ["git", "-C", self.root, "show", ":auth.lua"], text=True
            ),
        )
        self.keys(editor, ":fs<CR>")
        self.keys(editor, "s")
        self.assertEqual(
            "".join(baseline),
            subprocess.check_output(
                ["git", "-C", self.root, "show", ":auth.lua"], text=True
            ),
        )
        self.assertEqual("".join(changed), path.read_text())

    def test_staging_advances_hunks_across_files_and_wraps(self):
        baseline = [f"-- original line {i}\n" for i in range(1, 61)]
        path = Path(self.root, "auth.lua")
        path.write_text("".join(baseline))
        Path(self.root, "beta.lua").write_text("return false\n")
        subprocess.run(["git", "-C", self.root, "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", self.root, "commit", "-qm", "Navigation fixture"], check=True
        )
        changed = (
            baseline[:4] + ["-- inserted one\n", "-- inserted two\n"] + baseline[4:]
        )
        changed[26], changed[46] = "-- changed middle\n", "-- changed last\n"
        path.write_text("".join(changed))
        Path(self.root, "beta.lua").write_text("return true\n")
        editor = self.launch(self.root)
        self.lua(editor, "vim.api.nvim_set_current_win(s.old_win)")
        self.keys(editor, "]")
        self.keys(editor, "s")
        self.assertEqual(
            ["unstaged", "auth.lua", 45, 47, True],
            self.lua(
                editor,
                "return {s.current.group, s.current.path, vim.api.nvim_win_get_cursor(s.old_win)[1], vim.api.nvim_win_get_cursor(s.new_win)[1], vim.api.nvim_get_current_win() == s.old_win}",
            ),
        )
        partial = baseline.copy()
        partial[24] = "-- changed middle\n"
        self.assertEqual(
            "".join(partial),
            subprocess.check_output(
                ["git", "-C", self.root, "show", ":auth.lua"], text=True
            ),
        )
        self.keys(editor, "s")
        self.assertEqual(
            ["unstaged", "beta.lua"],
            self.lua(editor, "return {s.current.group, s.current.path}"),
        )
        self.keys(editor, "s")
        self.assertEqual(
            ["unstaged", "auth.lua", 1],
            self.lua(
                editor, "return {s.current.group, s.current.path, s.selected_hunk}"
            ),
        )
        self.assertTrue(
            self.lua(
                editor,
                "return vim.api.nvim_get_current_win() == s.old_win and s.rows[vim.api.nvim_win_get_cursor(s.tree_win)[1]] == s.index",
            )
        )
        self.keys(editor, "s")
        self.assertEqual(
            "".join(changed),
            subprocess.check_output(
                ["git", "-C", self.root, "show", ":auth.lua"], text=True
            ),
        )
        self.assertEqual("".join(changed), path.read_text())
        self.assertTrue(
            self.lua(
                editor,
                "return s.current == nil and vim.api.nvim_get_current_win() == s.tree_win",
            )
        )
        self.assertEqual(" ▾ UNSTAGED (0)", editor.current.line)
        self.assertIn("No file selected", self.lua(editor, "return r.statusline()"))
        self.lua(editor, "r.refresh_live()")
        self.keys(editor, " R")
        self.keys(editor, "]")
        self.assertTrue(self.lua(editor, "return s.current == nil"))
        self.assertEqual(" ▾ UNSTAGED (0)", editor.current.line)
        path.write_text(path.read_text() + "-- agent made another change\n")
        self.wait_for(editor, 's.current ~= nil and s.current.group == "unstaged"')
        self.assertEqual("auth.lua", self.lua(editor, "return s.current.path"))

    def test_lowercase_s_stages_new_file_and_advances(self):
        path = Path(self.root, "fresh file.txt")
        path.write_text("First line\nLast line without newline")
        editor = self.launch(self.root)
        self.lua(editor, "require('rediff.live').stop(s)")

        def show(group):
            self.lua(
                editor,
                "r.refresh(); for i, entry in ipairs(s.entries) do "
                f"if entry.path == 'fresh file.txt' and entry.group == '{group}' then "
                "r.show(i); vim.api.nvim_set_current_win(s.new_win); return end end; error('Missing file')",
            )

        show("untracked")
        # Reject stale new-file snapshots before adding any content to the index.
        before = subprocess.check_output(["git", "-C", self.root, "write-tree"])
        path.write_text(path.read_text() + " — edited")
        self.keys(editor, "s")
        self.assertEqual(
            before, subprocess.check_output(["git", "-C", self.root, "write-tree"])
        )
        self.assertIn("Reviewed content changed", editor.vars["startup_notice"])
        show("untracked")
        self.keys(editor, "s")
        self.assertEqual(
            path.read_bytes(),
            subprocess.check_output(
                ["git", "-C", self.root, "show", ":fresh file.txt"]
            ),
        )
        self.assertEqual(
            b"fresh file.txt\n",
            subprocess.check_output(
                ["git", "-C", self.root, "diff", "--cached", "--name-only"]
            ),
        )
        self.assertTrue(
            self.lua(
                editor,
                "return s.current.group ~= 'staged' and s.current.path ~= 'fresh file.txt'",
            )
        )
        self.assertTrue(
            self.lua(editor, "return vim.api.nvim_get_current_win() == s.new_win")
        )

        show("staged")
        self.keys(editor, "s")
        self.assertEqual(
            before,
            subprocess.check_output(["git", "-C", self.root, "write-tree"]),
            "s also unstages the entire added-file hunk",
        )
        self.assertTrue(path.exists())

        # Intent-to-add files arrive as UNSTAGED new-file patches, not untracked.
        subprocess.run(
            ["git", "-C", self.root, "add", "-N", "--", path.name], check=True
        )
        show("unstaged")
        self.keys(editor, ":view merged<CR>s")
        self.assertEqual(
            path.read_bytes(),
            subprocess.check_output(
                ["git", "-C", self.root, "show", ":fresh file.txt"]
            ),
        )
        self.assertTrue(
            self.lua(
                editor,
                "return s.current.group ~= 'staged' and s.current.path ~= 'fresh file.txt'",
            )
        )

    def test_staging_uses_git_hunk_boundary_in_merged_view(self):
        baseline = [f"-- original line {i}\n" for i in range(1, 61)]
        path = Path(self.root, "auth.lua")
        path.write_text("".join(baseline))
        subprocess.run(["git", "-C", self.root, "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", self.root, "commit", "-qm", "Grouped hunk fixture"],
            check=True,
        )
        changed = baseline.copy()
        changed[4], changed[7], changed[39] = "-- first\n", "-- nearby\n", "-- later\n"
        path.write_text("".join(changed))
        editor = self.launch(self.root)
        self.keys(editor, ":view merged<CR>")
        self.keys(editor, "<Tab>")
        self.assertEqual(3, self.lua(editor, "return #s.diff.changes"))
        self.keys(editor, "s")
        partial = baseline.copy()
        partial[4], partial[7] = changed[4], changed[7]
        self.assertEqual(
            "".join(partial),
            subprocess.check_output(
                ["git", "-C", self.root, "show", ":auth.lua"], text=True
            ),
        )
        self.assertEqual(
            ["unstaged", 40],
            self.lua(
                editor,
                "return {s.current.group, vim.api.nvim_win_get_cursor(s.new_win)[1]}",
            ),
        )
        self.keys(editor, "s")
        self.assertTrue(
            self.lua(
                editor,
                "return s.current == nil and vim.api.nvim_get_current_win() == s.tree_win",
            )
        )
        self.assertEqual(
            [""],
            self.lua(
                editor, "return vim.api.nvim_buf_get_lines(s.new_buf, 0, -1, false)"
            ),
        )
        self.assertEqual(
            0,
            self.lua(
                editor,
                'return #vim.api.nvim_buf_get_extmarks(s.new_buf, vim.api.nvim_get_namespaces()["codediff-inline"], 0, -1, {})',
            ),
        )
        self.assertEqual(
            "".join(changed),
            subprocess.check_output(
                ["git", "-C", self.root, "show", ":auth.lua"], text=True
            ),
        )
        self.keys(editor, ":fs<CR>")
        self.assertEqual("staged", self.lua(editor, "return s.current.group"))

    def test_lowercase_s_toggles_only_current_hunk(self):
        editor = self.launch(self.root)
        self.keys(editor, "<Tab>")
        changed = Path(self.root, "auth.lua").read_bytes()
        self.keys(editor, "S")
        self.keys(editor, ":fs<CR>")
        self.assertEqual("staged", self.lua(editor, "return s.current.group"))
        self.keys(editor, "s")
        # The first hunk returns to HEAD while the second stays staged.
        partial = changed.replace(b"    return true\n", b"    return false\n")
        self.assertEqual(
            partial,
            subprocess.check_output(["git", "-C", self.root, "show", ":auth.lua"]),
        )
        self.keys(editor, ":fu<CR>")
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
            self.keys(editor, f":view {layout}<CR>:fu<CR>")
            self.lua(editor, f"vim.api.nvim_set_current_win(s.{pane}_win)")
            self.keys(editor, " R")
            self.keys(editor, "S")
            self.assertEqual(
                changed,
                subprocess.check_output(["git", "-C", self.root, "show", ":auth.lua"]),
            )
            self.assertEqual(
                ["removed.lua", "unstaged"],
                self.lua(editor, "return {s.current.path, s.current.group}"),
            )
            self.keys(editor, ":fs<CR>")
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

    def test_diff_file_staging_keeps_tree_selection_in_original_group(self):
        editor = self.launch(self.root)
        self.lua(editor, "require('rediff.live').stop(s)")
        initial_index = subprocess.check_output(["git", "-C", self.root, "write-tree"])

        def selected(path, group, pane):
            self.assertEqual(
                [path, group, True, True, True],
                self.lua(
                    editor,
                    "local row=vim.api.nvim_win_get_cursor(s.tree_win)[1]; "
                    "local marks=vim.api.nvim_buf_get_extmarks(s.tree_buf, "
                    "vim.api.nvim_create_namespace('rediff.active-file'), 0, -1, {}); "
                    "return {s.current.path,s.current.group,s.rows[row]==s.index, "
                    f"vim.api.nvim_get_current_win()==s.{pane}_win, "
                    "#marks==1 and marks[1][2]==row-1}",
                ),
            )

        def exhausted(group, pane):
            self.wait_for(editor, "s.current == nil and s.index == nil")
            index = subprocess.check_output(["git", "-C", self.root, "write-tree"])
            self.lua(editor, "r.refresh_live()")
            self.keys(editor, " RSS")
            self.assertEqual(
                [group, True, True, 0],
                self.lua(
                    editor,
                    "return {s.exhausted_group, "
                    f"vim.api.nvim_win_get_cursor(s.tree_win)[1]==s.group_rows.{group}, "
                    f"vim.api.nvim_get_current_win()==s.{pane}_win, "
                    "#vim.api.nvim_buf_get_extmarks(s.tree_buf, "
                    "vim.api.nvim_create_namespace('rediff.active-file'), 0, -1, {})}",
                ),
            )
            self.assertEqual(
                index, subprocess.check_output(["git", "-C", self.root, "write-tree"])
            )

        for layout, pane in (("split", "new"), ("split", "old"), ("merged", "new")):
            with self.subTest(layout=layout, pane=pane):
                # A partially staged file exists in both lists before S.
                subprocess.run(["git", "-C", self.root, "add", "auth.lua"], check=True)
                path = Path(self.root, "auth.lua")
                path.write_text(path.read_text() + "-- another edit\n")
                self.lua(editor, "r.refresh()")
                self.keys(editor, f":view {layout}<CR>:fu<CR>")
                self.lua(editor, f"vim.api.nvim_set_current_win(s.{pane}_win)")
                self.keys(editor, "S")
                selected("removed.lua", "unstaged", pane)
                self.keys(editor, "S")
                selected("plan.md", "untracked", pane)
                self.keys(editor, "S")
                exhausted("unstaged", pane)

                # Delete a middle staged row: next, not previous or same-path unstaged.
                self.keys(editor, ":fs<CR>")
                self.lua(
                    editor, f"r.show(2); vim.api.nvim_set_current_win(s.{pane}_win)"
                )
                self.keys(editor, "S")
                selected("removed.lua", "staged", pane)
                # Last row falls back to previous, and final row leaves an empty group.
                self.keys(editor, "S")
                selected("auth.lua", "staged", pane)
                self.keys(editor, "S")
                exhausted("staged", pane)
                self.assertEqual(
                    initial_index,
                    subprocess.check_output(["git", "-C", self.root, "write-tree"]),
                )

    def test_navigation_and_layouts(self):
        path = Path(self.root, "auth.lua")
        path.write_text(
            path.read_text().replace(
                "  return nil\nend", "  return nil\nend -- changed"
            )
        )
        editor = self.launch(self.root)
        self.keys(editor, "<Tab>")
        # Layout changes themselves must use the current snapshot, not read disk.
        self.lua(editor, 'require("rediff.live").stop(s)')
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

    def test_wrap_changes_apply_to_both_diff_panes(self):
        path = Path(self.root, "aaa-wrap.txt")
        text = "The old configuration keeps this deliberately long line visible across several screen rows when wrapping is enabled in the diff viewer. END\n"
        path.write_text(text)
        for args in (["add", "aaa-wrap.txt"], ["commit", "-m", "Wrap fixture"]):
            subprocess.run(
                ["git", "-C", self.root, *args], check=True, capture_output=True
            )
        path.write_text(text.replace("old", "new"))
        editor = self.launch(self.root, file="auth.lua")
        editor.command("setlocal nowrap")
        ordinary = editor.current.window.handle
        self.keys(editor, " r")
        self.lua(editor, "require('rediff.live').stop(s)")
        tree_wrap = self.lua(editor, "return vim.wo[s.tree_win].wrap")

        def both(value):
            self.assertEqual(
                [value, value],
                self.lua(
                    editor, "return {vim.wo[s.old_win].wrap,vim.wo[s.new_win].wrap}"
                ),
            )
            self.assertEqual(
                tree_wrap, self.lua(editor, "return vim.wo[s.tree_win].wrap")
            )
            if self.lua(editor, "return s.current.path == 'aaa-wrap.txt'"):
                for pane in ("old", "new"):
                    rows = self.lua(
                        editor,
                        f"return vim.api.nvim_win_text_height(s.{pane}_win,{{start_row=0,end_row=0}}).all",
                    )
                    self.assertEqual(value, rows > 1, f"{pane} pane rendered wrap")

        both(False)
        for pane, command, expected in (
            ("new", "set wrap", True),
            ("old", "set nowrap", False),
            ("old", "setlocal wrap", True),
            ("new", "setlocal invwrap", False),
            ("new", "set wrap!", True),
            ("old", "setglobal nowrap", True),
        ):
            with self.subTest(pane=pane, command=command):
                self.lua(editor, f"vim.api.nvim_set_current_win(s.{pane}_win)")
                self.keys(editor, f":{command}<CR>")
                both(expected)
        for action in (
            "r.show(#s.entries)",
            "r.refresh()",
            "r.view('merged'); r.view('split')",
        ):
            self.lua(editor, action)
            both(True)
        self.keys(editor, ":view merged<CR>:set nowrap<CR>:view split<CR>")
        both(False)
        # Options in ordinary editing windows must not affect the Review tab.
        self.lua(editor, "vim.api.nvim_set_current_tabpage(s.previous_tab)")
        self.assertFalse(editor.api.get_option_value("wrap", {"win": ordinary}))
        self.keys(editor, ":setlocal wrap<CR>")
        both(False)
        self.lua(
            editor,
            "vim.api.nvim_set_current_tabpage(s.tab); vim.api.nvim_set_current_win(s.new_win)",
        )
        self.keys(editor, ":set wrap<CR>")
        both(True)
        self.lua(editor, "r.leave()")
        self.keys(editor, ":setlocal nowrap<CR>")
        self.assertFalse(editor.current.window.options["wrap"])

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
        self.lua(editor, 'require("rediff.live").stop(s)')
        path = Path(self.root, "auth.lua")
        path.write_text(path.read_text().replace("return nil", "return 321"))
        self.keys(editor, " R")
        self.assertIn("return 321", self.lua(editor, "return s.current.new"))

    def test_harness_file_events_refresh_unseen_changes_without_polling(self):
        editor = self.launch(self.root)
        self.wait_for(editor, "not require('rediff.awareness').get(s.root).scanning")
        self.lua(
            editor,
            """
            local f=require('rediff.feedback')
            local config=vim.fn.stdpath('config')
            vim.fn.mkdir(config,'p')
            f.write(config..'/settings.json',{review_refresh_interval=0})
            require('rediff.live').start(s)
            local awareness=require('rediff.awareness')
            awareness.accept(s.root, vim.deepcopy(awareness.get(s.root).current))
            -- Test event delivery, not the focused-hunk dwell timeout.
            awareness.stop()
            local system=vim.system
            local streams={}
            vim.system=function(argv,opts,callback)
                if argv[1] ~= 'rediff-amp-live' or argv[2] ~= 'watch' then
                    return system(argv,opts,callback)
                end
                streams[argv[4]]=opts.stdout
                return {kill=function() end}
            end
            local connections=require('rediff.connections')
            for _,target in ipairs({{root=s.root,session='T-here'}, {root=s.root..'-other',session='T-other'}}) do
                connections.connect(vim.tbl_extend('force',target,
                    {name='amp-live',connection='/fake',capabilities={'activity'}}))
            end
            function _G.emit_files(thread,root,sequence,revision)
                streams[thread](nil,vim.json.encode({version=1,root=root,thread=thread,
                    sequence=sequence,files_revision=revision,state='running',title='',tool=vim.NIL})..'\\n')
            end
            """,
        )
        self.assertFalse(self.lua(editor, "return s.live_refresh == true"))
        snapshot = self.lua(editor, "return s.current.id")
        path = Path(self.root, "auth.lua")
        path.write_text(path.read_text().replace("return true", "return 'agent edit'"))
        self.lua(
            editor,
            "emit_files('T-other',s.root..'-other',0,1); emit_files('T-here',s.root,0,0)",
        )
        time.sleep(0.4)
        pump(editor)
        self.assertEqual(
            snapshot,
            self.lua(editor, "return s.current.id"),
            "Other worktrees/activity cannot refresh this review",
        )

        self.keys(editor, " diKeep this note<Esc>")
        self.lua(editor, "emit_files('T-here',s.root,1,1)")
        time.sleep(0.4)
        pump(editor)
        self.assertEqual(
            snapshot,
            self.lua(editor, "return s.current.id"),
            "Queue events while a note is being edited",
        )
        self.keys(editor, ":w<CR>")
        self.wait_for(editor, 's.current.new:find("agent edit",1,true) ~= nil')
        self.wait_for(editor, "not require('rediff.awareness').get(s.root).scanning")
        self.assertEqual(
            [snapshot, "Keep this note"],
            self.lua(editor, "return {s.comments[1].snapshot_id,s.comments[1].text}"),
        )
        self.assertEqual(
            "unseen",
            self.lua(
                editor,
                "local a=require('rediff.awareness');return a.file_state(a.get(s.root),'unstaged\\0auth.lua')",
            ),
        )
        self.assertGreater(
            self.lua(
                editor,
                "return #vim.api.nvim_buf_get_extmarks(s.new_buf,vim.api.nvim_get_namespaces()['rediff.awareness'],0,-1,{})",
            ),
            0,
        )

        # An edit arriving while a previous refresh finishes must trigger another pass.
        self.lua(
            editor,
            """
            local refresh=r.refresh_live
            r.refresh_live=function()
                r.refresh_live=refresh
                local applied=refresh()
                vim.fn.writefile({'return "second edit"'},s.root..'/auth.lua')
                emit_files('T-here',s.root,3,3)
                return applied
            end
            emit_files('T-here',s.root,2,2)
            """,
        )
        self.wait_for(editor, "s.current.new == 'return \"second edit\"\\n'")
        self.wait_for(editor, "not require('rediff.awareness').get(s.root).scanning")
        self.lua(
            editor,
            "local a=require('rediff.awareness');a.accept(s.root,vim.deepcopy(a.get(s.root).current)); emit_files('T-here',s.root,4,4)",
        )
        time.sleep(0.4)
        pump(editor)
        self.wait_for(editor, "not require('rediff.awareness').get(s.root).scanning")
        self.assertTrue(
            self.lua(
                editor,
                "local a=require('rediff.awareness');return a.file_state(a.get(s.root),'unstaged\\0auth.lua')==nil",
            ),
            "An event without a content change must not create unseen markers",
        )

    def test_live_refresh_preserves_focus_and_annotations(self):
        editor = self.launch(self.root)
        self.assertEqual(
            3000,
            self.lua(
                editor,
                """
                local live = require('rediff.live')
                live.stop(s)
                local create, timer = vim.uv.new_timer, nil
                vim.uv.new_timer = function() timer = create(); return timer end
                live.start(s)
                vim.uv.new_timer = create
                return timer:get_repeat()
            """,
            ),
            "Review polls every three seconds without filesystem events",
        )
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
        time.sleep(3.2)  # Cross a poll interval while the annotation editor is open.
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
                require('rediff.live').stop(s)
                local git = require('rediff.git')
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

    def test_live_refresh_interval_setting_and_disable(self):
        editor = self.launch(self.root)

        def configure(settings):
            return editor.exec_lua(
                STATE
                + """
                local config = vim.fn.stdpath('config')
                vim.fn.mkdir(config, 'p')
                require('rediff.feedback').write(config .. '/settings.json', ...)
                local create, timer = vim.uv.new_timer, nil
                vim.uv.new_timer = function() timer = create(); return timer end
                require('rediff.live').start(s)
                vim.uv.new_timer = create
                return {s.live_refresh == true, timer and timer:get_repeat() or -1}
                """,
                settings,
            )

        self.assertEqual([True, 10000], configure({"review_refresh_interval": 10}))
        self.assertEqual(
            [False, -1],
            configure({"review_refresh_interval": 0}),
            "Disabled refresh creates no timer",
        )
        path = Path(self.root, "auth.lua")
        path.write_text(path.read_text().replace("return nil", "return 987"))
        time.sleep(3.2)
        pump(editor)
        self.assertNotIn("return 987", self.lua(editor, "return s.current.new"))
        self.keys(editor, " R")
        self.assertIn(
            "return 987",
            self.lua(editor, "return s.current.new"),
            "Manual refresh still works",
        )
        self.lua(editor, "r.leave(); r.open()")
        self.assertFalse(
            self.lua(editor, "return s.live_refresh == true"),
            "Disabled setting survives Review reopen",
        )

        for value in (-1, 0.5, "3", False):
            with self.subTest(invalid=value):
                self.assertEqual(
                    [False, -1], configure({"review_refresh_interval": value})
                )
                self.assertIn(
                    "review_refresh_interval must be", editor.vars["startup_notice"]
                )
        self.assertEqual(
            [True, 3000], configure({}), "Omitting the setting restores the default"
        )
        self.assertEqual([True, 1000], configure({"review_refresh_interval": 1}))
        path.write_text(path.read_text().replace("return 987", "return 654"))
        self.wait_for(editor, 's.current.new:find("return 654", 1, true) ~= nil')

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
        self.keys(editor, " r<Tab>")
        index = subprocess.check_output(["git", "-C", self.root, "write-tree"])
        self.keys(editor, ":harness use amp<CR>")
        self.assertEqual(
            "amp",
            self.lua(editor, 'return require("rediff.harness").get(s.root).provider'),
        )
        self.assertFalse(marker.exists(), "Selecting a type must not launch a process")
        self.lua(
            editor,
            """
            local h = require('rediff.harness')
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
                "local h=require('rediff.harness').get(s.root); "
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
        self.keys(editor, "j<CR>")
        self.assertEqual(target, self.lua(editor, "return s.root"))
        self.keys(editor, ":worktree switch<CR>")
        self.assertNotIn("new", editor.current.buffer[0])
        self.assertNotIn("delete", editor.current.buffer[0])
        self.keys(editor, "ndd")
        self.assertEqual("n", editor.api.get_mode()["mode"])
        self.assertEqual("rediff-worktrees", editor.current.buffer.options["filetype"])
        self.assertTrue(Path(target).exists())
        self.keys(editor, "k<CR>")
        self.assertEqual(self.root, self.lua(editor, "return s.root"))
        # Changing provider clears an incompatible feedback connection, not drafts.
        self.keys(editor, ":harness use claude<CR>")
        self.assertEqual(
            ["claude", "none", 1],
            self.lua(
                editor,
                "local h=require('rediff.harness').get(s.root); return {h.provider,h.target.name,#s.comments}",
            ),
        )
        restarted = self.launch(self.root, headless=True)
        self.assertEqual(
            "claude",
            self.lua(
                restarted,
                'return require("rediff.harness").get(require("rediff.git").root()).provider',
            ),
        )
        # Missing CLI and invalid branch must fail before any worktree is created.
        self.assertFalse(
            self.lua(
                editor,
                """
            local original = vim.fn.exepath
            vim.fn.exepath = function() return '' end
            local ok = pcall(require('rediff.worktree').new, 'missing-cli')
            vim.fn.exepath = original
            return ok
        """,
            )
        )
        self.assertFalse(Path(self.root + "-missing-cli").exists())
        self.assertFalse(
            self.lua(editor, "return pcall(require('rediff.worktree').new, '--bad')")
        )
        self.assertEqual(
            2, self.lua(editor, "return #require('rediff.worktree').list()")
        )

    def test_worktree_panel_create_delete_and_owned_processes(self):
        binary = Path(self.directory.name, "bin")
        binary.mkdir()
        executable = binary / "claude"
        log = Path(self.directory.name, "harness-pids")
        executable.write_text(
            '#!/bin/sh\nprintf "%s|%s\\n" "$PWD" "$$" >> "$HARNESS_TEST_LOG"\n'
            'echo "Fake harness ready"\nexec sleep 600\n'
        )
        executable.chmod(0o755)
        os.environ["PATH"] = str(binary) + os.pathsep + os.environ["PATH"]
        os.environ["HARNESS_TEST_LOG"] = str(log)
        editor = self.launch(self.root)
        self.keys(editor, ":harness use claude<CR>")
        self.lua(editor, "require('rediff.harness').launch(s.root)")
        self.wait_for(editor, "vim.fn.filereadable(vim.env.HARNESS_TEST_LOG) == 1")
        main_pid = int(log.read_text().split("|")[1])
        self.keys(editor, "<C-\\><C-n>:worktree list<CR>ntopic<CR>")
        target = Path(self.root + "-topic")
        self.wait_for(editor, "#vim.fn.readfile(vim.env.HARNESS_TEST_LOG) == 2")
        topic_pid = int(log.read_text().splitlines()[1].split("|")[1])
        self.assertEqual(str(target), self.lua(editor, "return s.root"))
        self.assertEqual("terminal", editor.current.buffer.options["buftype"])
        # A separately started harness in the same directory is not ours to kill.
        external = subprocess.Popen(["sleep", "600"], cwd=target)
        try:
            self.keys(editor, "<C-\\><C-n>:worktree list<CR>dd1<CR>")
            self.assertTrue(target.exists(), "Cancel leaves the worktree intact")
            os.kill(topic_pid, 0)
            self.assertEqual(
                "rediff-worktrees", editor.current.buffer.options["filetype"]
            )
            self.keys(editor, "dd2<CR>")
            self.assertFalse(
                target.exists(), self.lua(editor, "return vim.g.startup_notice")
            )
            self.assertEqual(self.root, self.lua(editor, "return s.root"))
            self.assertEqual(
                "rediff-worktrees", editor.current.buffer.options["filetype"]
            )
            self.assertNotIn("topic", "\n".join(editor.current.buffer[:]))
            with self.assertRaises(ProcessLookupError):
                os.kill(topic_pid, 0)
            os.kill(main_pid, 0)
            self.assertIsNone(
                external.poll(), "Externally started harness must survive"
            )
            subprocess.run(
                ["git", "-C", self.root, "show-ref", "--verify", "refs/heads/topic"],
                check=True,
                capture_output=True,
            )
            self.assertTrue(
                self.lua(
                    editor,
                    """
                for _,win in ipairs(vim.api.nvim_list_wins()) do
                    local cwd=vim.api.nvim_win_call(win, vim.fn.getcwd)
                    if vim.fn.isdirectory(cwd) ~= 1 then return false end
                end
                return true
            """,
                )
            )
            # Reuse the pane to create another worktree, then delete by typed command.
            self.keys(editor, "nsecond<CR>")
            self.wait_for(editor, "#vim.fn.readfile(vim.env.HARNESS_TEST_LOG) == 3")
            self.keys(editor, "<C-\\><C-n>:worktree delete second<CR>")
            self.assertFalse(Path(self.root + "-second").exists())
            self.assertEqual(self.root, self.lua(editor, "return s.root"))
            os.kill(main_pid, 0)
            # The delete picker offers only deletion, not the management actions.
            self.keys(editor, ":worktree new third<CR>")
            self.wait_for(editor, "#vim.fn.readfile(vim.env.HARNESS_TEST_LOG) == 4")
            third_pid = int(log.read_text().splitlines()[3].split("|")[1])
            self.keys(editor, "<Esc>:worktree delete<CR>")
            self.assertIn("Enter delete", editor.current.buffer[0])
            self.assertNotIn("new", editor.current.buffer[0])
            self.assertNotIn("switch", editor.current.buffer[0])
            self.keys(editor, "n")
            self.assertEqual("n", editor.api.get_mode()["mode"])
            self.keys(editor, "<CR>1<CR>")
            self.assertTrue(Path(self.root + "-third").exists())
            self.assertEqual(
                "rediff-worktrees", editor.current.buffer.options["filetype"]
            )
            self.keys(editor, "<CR>2<CR>")
            self.assertFalse(Path(self.root + "-third").exists())
            self.assertEqual(self.root, self.lua(editor, "return s.root"))
            self.assertNotEqual(
                "rediff-worktrees", editor.current.buffer.options["filetype"]
            )
            with self.assertRaises(ProcessLookupError):
                os.kill(third_pid, 0)
        finally:
            external.terminate()
            external.wait(timeout=5)

    def test_live_refresh_preserves_harness_terminal_input(self):
        subprocess.run(["git", "-C", self.root, "add", "."], check=True)
        subprocess.run(
            ["git", "-C", self.root, "commit", "-m", "Clean fixture"],
            check=True,
            capture_output=True,
        )
        editor = self.launch(self.root)
        self.lua(
            editor,
            "require('rediff.harness').launch(s.root, {'sh', '-c', "
            "[[echo ready; while IFS= read -r line; do printf 'Reply: %s\\n' \"$line\"; done]]})",
        )
        buf, win = editor.current.buffer.number, editor.current.window.handle
        job = editor.current.buffer.vars["terminal_job_id"]
        path = Path(self.root, "terminal edits.txt")
        for interval in (1, 0):
            # Exercise fallback polling and file events independently.
            self.lua(
                editor,
                "local f=require('rediff.feedback'); local p=vim.fn.stdpath('config'); "
                "vim.fn.mkdir(p,'p'); "
                f"f.write(p..'/settings.json',{{review_refresh_interval={interval}}}); "
                "require('rediff.live').start(s)",
            )
            self.keys(editor, "keep this draft")
            for text in ("first", "second"):
                path.write_text(text + "\n")
                if interval == 0:
                    self.lua(editor, "require('rediff.live').changed(s.root)")
                self.wait_for(
                    editor, f'#s.entries == 1 and s.current.new == "{text}\\n"'
                )
                self.assertEqual(buf, editor.current.buffer.number)
                self.assertEqual(win, editor.current.window.handle)
                self.assertEqual(job, editor.current.buffer.vars["terminal_job_id"])
                self.assertEqual("t", editor.api.get_mode()["mode"])
                self.assertEqual(
                    [text],
                    self.lua(
                        editor,
                        "return vim.api.nvim_buf_get_lines(s.new_buf,0,-1,false)",
                    ),
                )
            self.keys(editor, f" {interval}<CR>")
            self.wait_for(
                editor,
                f"table.concat(vim.api.nvim_buf_get_lines({buf},0,-1,false),'\\n'):find('Reply: keep this draft {interval}',1,true) ~= nil",
            )
            path.unlink()
            if interval == 0:
                self.lua(editor, "require('rediff.live').changed(s.root)")
            self.wait_for(editor, "#s.entries == 0")
            self.assertEqual(buf, editor.current.buffer.number)
            self.assertEqual("t", editor.api.get_mode()["mode"])

    def test_harness_terminal_does_not_inherit_outer_terminal_identity(self):
        executable = Path(self.directory.name, "terminal-fixture")
        log = Path(self.directory.name, "terminal-env")
        executable.write_text(
            "#!/bin/sh\n"
            'if [ -n "$TMUX" ] || [ "$TERM_PROGRAM" = tmux ]; then\n'
            "  printf '\\033Ptmux;\\033\\033]777;notify;amp;Agent is ready\\007\\033\\\\'\n"
            "else\n"
            "  printf '\\033]777;notify;amp;Agent is ready\\007'\n"
            "fi\n"
            "printf '\\033]2;Readiff terminal fixture\\007Harness ready\\n'\n"
            'printf \'%s\\n\' "$TERM" "$TMUX" "$TMUX_PANE" "$TERM_PROGRAM" '
            '"$KITTY_WINDOW_ID" "$COLUMNS" "$LINES" "$NVIM" '
            '"$SSH_AUTH_SOCK" "$COLORTERM" "$HOME" "$PATH" > "$HARNESS_TEST_LOG"\n'
            "while IFS= read -r line; do printf 'Reply: %s\\n' \"$line\"; done\n"
        )
        executable.chmod(0o755)
        os.environ["HARNESS_TEST_LOG"] = str(log)
        editor = self.launch(self.root)
        # Set the parent's identity after UI startup; don't alter its own renderer.
        self.lua(
            editor,
            """
            vim.env.TMUX='/outer/tmux,123,0'
            vim.env.TMUX_PANE='%42'
            vim.env.TERM_PROGRAM='tmux'
            vim.env.KITTY_WINDOW_ID='27'
            vim.env.TERM='screen-256color'
            vim.env.COLUMNS='999'
            vim.env.LINES='777'
            vim.env.SSH_AUTH_SOCK='/test/forwarded-agent'
            vim.env.COLORTERM='truecolor'
        """,
        )
        editor.exec_lua(
            "require('rediff.harness').launch(require('rediff.review').state.root, {...})",
            str(executable),
        )
        self.wait_for(editor, "vim.fn.filereadable(vim.env.HARNESS_TEST_LOG)==1")
        self.wait_for(
            editor,
            "table.concat(vim.api.nvim_buf_get_lines(0,0,-1,false),'\\n'):find('Harness ready',1,true) ~= nil",
        )
        self.assertEqual(
            ["Harness ready"], [line for line in editor.current.buffer if line]
        )
        self.assertEqual(
            "Readiff terminal fixture", editor.current.buffer.vars["term_title"]
        )
        self.assertEqual(
            [
                "xterm-256color",
                "",
                "",
                "",
                "",
                "",
                "",
                editor.vvars["servername"],
                "/test/forwarded-agent",
                "truecolor",
                self.directory.name,
                self.lua(editor, "return vim.env.PATH"),
            ],
            log.read_text().splitlines(),
        )
        self.assertEqual(
            ["/outer/tmux,123,0", "%42", "tmux", "27", "screen-256color", "999", "777"],
            self.lua(
                editor,
                "return {vim.env.TMUX,vim.env.TMUX_PANE,vim.env.TERM_PROGRAM,"
                "vim.env.KITTY_WINDOW_ID,vim.env.TERM,vim.env.COLUMNS,vim.env.LINES}",
            ),
        )
        self.keys(editor, "typing still works<CR>")
        self.wait_for(
            editor,
            "table.concat(vim.api.nvim_buf_get_lines(0,0,-1,false),'\\n'):find('Reply: typing still works',1,true) ~= nil",
        )

    def test_harness_wheel_keeps_live_view_and_forwards_tui_mouse_events(self):
        import sys

        log = Path(self.directory.name, "mouse-input")
        fixture = """
import os, sys, tty
tty.setraw(0)
os.write(1, b''.join(b'History line %d\\r\\n' % i for i in range(80)))
if sys.argv[2] != 'none':
    os.write(1, b'\\x1b[?1000h')
    if sys.argv[2] == 'mouse':
        os.write(1, b'\\x1b[?1006h')
os.write(1, b'LIVE TUI READY')
with open(sys.argv[1], 'ab', buffering=0) as log:
    while True:
        data = os.read(0, 1024)
        if not data: break
        log.write(data)
"""
        editor = self.launch(self.root)
        for reporting in ("none", "mouse", "legacy"):
            editor.exec_lua(
                "require('rediff.harness').launch(require('rediff.review').state.root, ...)",
                [sys.executable, "-u", "-c", fixture, str(log), reporting],
            )
            self.wait_for(
                editor,
                "table.concat(vim.api.nvim_buf_get_lines(0,0,-1,false),'\\n'):find('LIVE TUI READY',1,true) ~= nil",
            )
            win = editor.current.window.handle
            row, col = editor.api.win_get_position(win)

            def view():
                return editor.exec_lua(
                    "return vim.api.nvim_win_call(...,function() local v=vim.fn.winsaveview(); return {v.topline,v.leftcol} end)",
                    win,
                )

            live = view()
            for focus in ("input", "normal", "diff"):
                if focus == "normal":
                    self.keys(editor, "<Esc>")
                elif focus == "diff":
                    self.keys(editor, "<D-w>p")
                before = log.read_bytes()
                focused_win = editor.current.window.handle
                mode = editor.api.get_mode()["mode"]
                for direction in ("up", "up", "down", "down", "right", "left"):
                    editor.api.input_mouse("wheel", direction, "", 0, row + 3, col + 6)
                    pump(editor)
                    self.assertEqual(live, view(), (reporting, focus, direction))
                self.assertEqual(focused_win, editor.current.window.handle)
                if reporting != "none" or focus != "input":
                    self.assertEqual(mode, editor.api.get_mode()["mode"])
                received = log.read_bytes()[len(before):]
                if reporting == "mouse" and focus in ("input", "normal"):
                    self.assertEqual(2, received.count(b"\x1b[<64;"), focus)
                    self.assertEqual(2, received.count(b"\x1b[<65;"), focus)
                elif reporting == "legacy" and focus in ("input", "normal"):
                    self.assertEqual(2, received.count(b"\x1b[M`"), focus)
                    self.assertEqual(2, received.count(b"\x1b[Ma"), focus)
                elif reporting == "none":
                    self.assertEqual(b"", received)
                if focus == "input":
                    native_events = received
                elif focus == "normal":
                    # Identical coordinates/protocol, with no leaked mode-switch keys.
                    self.assertEqual(native_events, received)
                    diff_row, diff_col = editor.api.win_get_position(
                        self.lua(editor, "return s.new_win")
                    )
                    before = log.read_bytes()
                    editor.api.input_mouse("wheel", "up", "", 0, diff_row + 2, diff_col + 6)
                    pump(editor)
                    self.assertEqual(before, log.read_bytes())
                    self.assertEqual(focused_win, editor.current.window.handle)
                    self.assertEqual(mode, editor.api.get_mode()["mode"])
            self.lua(editor, "require('rediff.harness').stop(s.root)")
            pump(editor)
            log.unlink()

    def test_harness_terminal_pane_reuses_process_across_layouts_and_reviews(self):
        binary = Path(self.directory.name, "bin")
        binary.mkdir()
        executable = binary / "claude"
        log = Path(self.directory.name, "pane-launches")
        executable.write_text(
            '#!/bin/sh\nprintf "%s|%s\\n" "$PWD" "$$" >> "$HARNESS_TEST_LOG"\n'
            'echo "Interactive harness ready"\n'
            'while IFS= read -r line; do printf "Reply: %s\\n" "$line"; done\n'
        )
        executable.chmod(0o755)
        os.environ["PATH"] = str(binary) + os.pathsep + os.environ["PATH"]
        os.environ["HARNESS_TEST_LOG"] = str(log)
        editor = self.launch(self.root)
        self.lua(editor, "require('rediff.live').stop(s)")
        tabs = len(editor.api.list_tabpages())
        edit_windows = self.lua(editor, "return #vim.api.nvim_tabpage_list_wins(s.previous_tab)")
        self.keys(editor, ":harness use claude<CR>:harness open<CR>")
        self.wait_for(editor, "vim.fn.filereadable(vim.env.HARNESS_TEST_LOG)==1")
        buf = editor.current.buffer.number
        job = editor.current.buffer.vars["terminal_job_id"]
        self.assertEqual("terminal", editor.current.buffer.options["buftype"])
        self.assertEqual("hide", editor.current.buffer.options["bufhidden"])
        self.assertEqual(tabs, len(editor.api.list_tabpages()))
        self.assertEqual(4, len(editor.api.tabpage_list_wins(0)))
        self.assertEqual(self.root, log.read_text().split("|")[0])
        self.keys(editor, "hello pane<CR>")
        self.wait_for(
            editor,
            f"table.concat(vim.api.nvim_buf_get_lines({buf},0,-1,false),'\\n'):find('Reply: hello pane',1,true) ~= nil",
        )
        # Ctrl-W belongs to the TUI, including consecutive word deletions.
        # A long mapping timeout exposes any intercepted prefix immediately.
        self.lua(editor, "vim.o.timeoutlen = 10000")
        for text, deleted in (
            ("single unwanted", "<C-w>"),
            ("double one two", "<C-w><C-w>"),
        ):
            self.keys(editor, text + deleted)
            self.assertEqual(buf, editor.current.buffer.number)
            self.assertEqual("t", editor.api.get_mode()["mode"])
            self.assertFalse(editor.api.get_mode()["blocking"])
            self.keys(editor, "kept<CR>")
            expected = text.split()[0] + " kept"
            self.wait_for(
                editor,
                f"table.concat(vim.api.nvim_buf_get_lines({buf},0,-1,false),'\\n'):find('Reply: {expected}',1,true) ~= nil",
            )
        self.keys(editor, "<D-w>p")
        self.assertNotEqual("terminal", editor.current.buffer.options["buftype"])
        self.assertEqual("n", editor.api.get_mode()["mode"])
        self.keys(editor, ":ho<CR>")
        self.assertEqual(buf, editor.current.buffer.number)
        self.assertEqual(4, len(editor.api.tabpage_list_wins(0)))
        for layout, count in (("merged", 3), ("split", 4)):
            self.keys(
                editor, f"<C-\\><C-n>:hide<CR>:view {layout}<CR>:harness open<CR>"
            )
            self.assertEqual(buf, editor.current.buffer.number)
            self.assertEqual(job, editor.current.buffer.vars["terminal_job_id"])
            self.assertEqual(count, len(editor.api.tabpage_list_wins(0)))
            self.keys(editor, "pending input")
            for _ in range(2):
                self.keys(editor, "<Esc>")
                self.assertEqual(buf, editor.current.buffer.number)
                self.assertEqual("nt", editor.api.get_mode()["mode"])
            self.keys(editor, "<D-w><Up>")
            self.assertNotEqual(buf, editor.current.buffer.number)
            self.assertEqual("n", editor.api.get_mode()["mode"])
            self.keys(editor, ":ho<CR>")
            self.assertEqual(buf, editor.current.buffer.number)
            self.assertEqual(job, editor.current.buffer.vars["terminal_job_id"])
            self.keys(editor, f" intact {layout}<CR>")
            self.wait_for(
                editor,
                f"table.concat(vim.api.nvim_buf_get_lines({buf},0,-1,false),'\\n'):find('Reply: pending input intact {layout}',1,true) ~= nil",
            )
            self.keys(editor, "<C-\\><C-n>")
            self.lua(editor, "r.refresh_live()")
            self.assertEqual(buf, editor.current.buffer.number)
            self.keys(editor, "i<D-w>p")
            self.assertNotEqual("terminal", editor.current.buffer.options["buftype"])
            self.keys(editor, ":ho<CR>")
        for _ in range(2):
            edit_focus = self.lua(editor, "return vim.api.nvim_tabpage_get_win(s.previous_tab)")
            self.keys(editor, "<Esc><Space>r")
            self.assertTrue(self.lua(editor, "return r.active() == nil"))
            self.assertEqual(edit_windows + 1, len(editor.api.tabpage_list_wins(0)))
            self.assertEqual(
                1,
                sum(w.buffer.number == buf for w in editor.current.tabpage.windows),
                "Leaving Review keeps exactly one harness pane visible",
            )
            self.assertEqual(edit_focus, editor.current.window.handle)
            self.keys(editor, ":ho<CR>")
            self.assertEqual(job, editor.current.buffer.vars["terminal_job_id"])
            self.keys(editor, "<Esc><Space>r")
            self.assertEqual(
                1,
                sum(w.buffer.number == buf for w in editor.current.tabpage.windows),
                "Entering Review keeps the harness visible without :ho",
            )
            self.assertEqual(1, len(editor.funcs.win_findbuf(buf)))
            self.assertEqual(4, len(editor.api.tabpage_list_wins(0)))
            self.keys(editor, ":ho<CR>")
            self.assertEqual(job, editor.current.buffer.vars["terminal_job_id"])
        # Hiding in Review carries through both directions of the toggle.
        self.keys(editor, "<Esc>:hide<CR><Space>r")
        self.assertTrue(self.lua(editor, "return r.active() == nil"))
        self.assertEqual(edit_windows, len(editor.api.tabpage_list_wins(0)))
        self.assertEqual([], editor.funcs.win_findbuf(buf))
        self.keys(editor, "<Space>r")
        self.assertEqual([], editor.funcs.win_findbuf(buf))
        # Reopen, leave Review, then hide in editing; it must not reappear either.
        self.keys(editor, ":ho<CR><Esc><Space>r:ho<CR><Esc>:hide<CR><Space>r")
        self.assertEqual([], editor.funcs.win_findbuf(buf))
        self.keys(editor, "<Space>r")
        self.assertTrue(self.lua(editor, "return r.active() == nil"))
        self.assertEqual([], editor.funcs.win_findbuf(buf))
        self.assertEqual(
            1, len(log.read_text().splitlines()), "Reopening must not restart the agent"
        )
        self.lua(editor, "require('rediff.harness').stop(require('rediff.git').root())")
        self.assertFalse(editor.api.buf_is_valid(buf))
        self.assertEqual(-3, editor.funcs.jobwait([job], 0)[0])

    def test_harness_resume_requires_confirmation_and_uses_selected_session(self):
        binary = Path(self.directory.name, "bin")
        binary.mkdir()
        log = Path(self.directory.name, "resume-args")
        for provider in ("amp", "claude"):
            executable = binary / provider
            executable.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$0" "$PWD" "$@" > "$HARNESS_TEST_LOG"\n'
                'echo "Resumed harness ready"\nexec sleep 600\n'
            )
            executable.chmod(0o755)
        os.environ["PATH"] = str(binary) + os.pathsep + os.environ["PATH"]
        os.environ["HARNESS_TEST_LOG"] = str(log)
        editor = self.launch(self.root)
        thread = "T-00000000-1111-2222-3333-444444444444"
        for provider, session, argv in (
            ("amp", thread, ["threads", "continue", thread]),
            ("claude", "claude-session", ["--resume", "claude-session"]),
        ):
            with self.subTest(provider=provider):
                name = "amp-live" if provider == "amp" else provider
                self.lua(
                    editor,
                    f"""
                    require('rediff.harness').select(s.root, {{name='{name}',session='{session}',connection='/fake/connection.json'}})
                    vim.g.resume_choice = 2
                    vim.fn.confirm = function(prompt, choices, default)
                        vim.g.resume_prompt = prompt
                        assert(default == 2)
                        return vim.g.resume_choice
                    end
                """,
                )
                self.keys(editor, ":harness open<CR>")
                self.assertFalse(
                    log.exists(),
                    "Open must confirm before resuming an external session",
                )
                self.keys(editor, ":harness resume<CR>")
                self.assertFalse(
                    log.exists(), "Cancelling resume must not spawn a process"
                )
                self.assertIn(session, self.lua(editor, "return vim.g.resume_prompt"))
                self.assertIn(
                    "other terminal", self.lua(editor, "return vim.g.resume_prompt")
                )
                self.lua(editor, "vim.g.resume_choice=1")
                self.keys(editor, ":harness resume<CR>")
                self.wait_for(
                    editor, "vim.fn.filereadable(vim.env.HARNESS_TEST_LOG)==1"
                )
                self.assertEqual(
                    [str(binary / provider), self.root, *argv],
                    log.read_text().splitlines(),
                )
                job = editor.current.buffer.vars["terminal_job_id"]
                self.keys(editor, "<C-\\><C-n>:harness resume<CR>")
                self.assertEqual(job, editor.current.buffer.vars["terminal_job_id"])
                self.keys(editor, "<C-\\><C-n>:hide<CR>:ho<CR>")
                self.assertEqual(job, editor.current.buffer.vars["terminal_job_id"])
                self.keys(editor, "<C-\\><C-n>")
                self.lua(editor, "require('rediff.harness').stop(s.root)")
                self.lua(
                    editor,
                    "for _,entry in ipairs(require('rediff.connections').connected()) do require('rediff.connections').disconnect(entry.key) end",
                )
                log.unlink()
        # A selection change while the confirmation is open must not resume a stale thread.
        self.lua(
            editor,
            """
            vim.fn.confirm = function()
                require('rediff.harness').select(s.root, {name='none'})
                return 1
            end
        """,
        )
        self.keys(editor, ":harness resume<CR>")
        self.assertIn(
            "Checkout or harness changed",
            self.lua(editor, "return vim.g.startup_notice"),
        )
        self.assertFalse(log.exists())

    def test_harness_open_defaults_to_current_worktree_and_reuses_its_process(self):
        binary = Path(self.directory.name, "bin")
        binary.mkdir()
        log = Path(self.directory.name, "chosen-harnesses")
        cli = binary / "claude"
        cli.write_text(
            '#!/bin/sh\nprintf "%s|%s\\n" "$PWD" "$2" >> "$HARNESS_TEST_LOG"\n'
            'echo "Selected harness ready"\nexec sleep 600\n'
        )
        cli.chmod(0o755)
        os.environ["PATH"] = str(binary) + os.pathsep + os.environ["PATH"]
        os.environ["HARNESS_TEST_LOG"] = str(log)
        other = Path(self.directory.name, "other-worktree")
        subprocess.run(
            ["git", "-C", self.root, "worktree", "add", "-b", "other", str(other)],
            check=True,
            capture_output=True,
        )
        editor = self.launch(self.root)
        editor.exec_lua(
            """
            local c=require('rediff.connections')
            local root=require('rediff.review').state.root
            c.connect({name='claude',root=root,session='session-a'})
            c.connect({name='claude',root=...,session='session-b'})
            vim.fn.confirm=function() return 1 end
        """,
            str(other),
        )
        self.keys(editor, ":ho<CR>")
        self.wait_for(editor, "vim.fn.filereadable(vim.env.HARNESS_TEST_LOG)==1")
        self.assertEqual([self.root + "|session-a"], log.read_text().splitlines())
        local_job = editor.current.buffer.vars["terminal_job_id"]
        self.assertEqual(self.root, editor.funcs.getcwd())
        self.assertEqual("session-a", self.lua(editor, "return s.harness.session"))
        self.keys(editor, "<Esc>:worktree switch other<CR>:ho<CR>")
        self.wait_for(editor, "#vim.fn.readfile(vim.env.HARNESS_TEST_LOG)==2")
        self.assertEqual(str(other) + "|session-b", log.read_text().splitlines()[1])
        other_job = editor.current.buffer.vars["terminal_job_id"]
        self.assertNotEqual(other_job, local_job)
        self.assertEqual(4, len(editor.api.tabpage_list_wins(0)))
        self.keys(editor, "<Esc>:worktree switch main<CR>:ho<CR>")
        self.assertEqual(local_job, editor.current.buffer.vars["terminal_job_id"])
        self.keys(editor, "<Esc>:worktree switch other<CR>:ho<CR>")
        self.assertEqual(other_job, editor.current.buffer.vars["terminal_job_id"])
        self.assertEqual(
            2,
            len(log.read_text().splitlines()),
            "Selecting an owned session must reuse it",
        )
        self.assertEqual("session-b", self.lua(editor, "return s.harness.session"))
        # Multiple local sessions still use the explicitly selected local default.
        self.keys(editor, "<Esc>")
        self.lua(
            editor,
            "require('rediff.connections').connect({name='claude',root=s.root,session='session-c'})",
        )
        self.keys(editor, ":ho<CR>")
        self.assertEqual(other_job, editor.current.buffer.vars["terminal_job_id"])
        # Without a default, prompt for only this worktree's sessions.
        self.keys(editor, "<Esc>")
        self.lua(editor, "require('rediff.harness').select(s.root,{name='none'})")
        self.keys(editor, ":ho<CR>")
        self.keys(editor, "<CR>")  # Cancel without launching anything.
        self.assertEqual(2, len(log.read_text().splitlines()))
        self.keys(editor, ":ho<CR>1<CR>")
        self.assertEqual(other_job, editor.current.buffer.vars["terminal_job_id"])
        self.assertEqual("session-b", self.lua(editor, "return s.harness.session"))
        self.keys(editor, "<Esc>:ho<CR>")
        self.assertEqual(other_job, editor.current.buffer.vars["terminal_job_id"])

    def test_worktree_delete_protects_data_and_other_editors(self):
        target = Path(self.directory.name, "linked worktree")
        subprocess.run(
            ["git", "-C", self.root, "worktree", "add", "-b", "topic", str(target)],
            check=True,
            capture_output=True,
        )
        editor = self.launch(self.root)

        def rejected(message, name="topic"):
            ok, error = editor.exec_lua(
                "return {pcall(require('rediff.worktree').delete, ...)}", name
            )
            self.assertFalse(ok)
            self.assertIn(message, error)
            self.assertTrue(target.exists())

        rejected("main worktree", "main")
        rejected("Unknown worktree", "not-a-worktree")
        untracked = target / "untracked"
        untracked.write_text("do not lose me")
        rejected("uncommitted or untracked")
        untracked.unlink()
        tracked = target / "auth.lua"
        original = tracked.read_text()
        tracked.write_text(original + "-- changed\n")
        rejected("uncommitted or untracked")
        subprocess.run(["git", "-C", target, "add", "auth.lua"], check=True)
        rejected("uncommitted or untracked")
        subprocess.run(
            ["git", "-C", target, "reset", "--hard", "HEAD"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", self.root, "worktree", "lock", str(target)], check=True
        )
        rejected("locked")
        subprocess.run(
            ["git", "-C", self.root, "worktree", "unlock", str(target)], check=True
        )
        self.lua(editor, "require('rediff.harness').get(s.root)")
        # A second editor has a live review lock for this otherwise clean checkout.
        other = self.launch(str(target))
        rejected("Another editor owns")
        self.lua(other, "r.leave()")
        buf = editor.exec_lua(
            "local b=vim.fn.bufadd(...); vim.fn.bufload(b); return b", str(tracked)
        )
        editor.api.buf_set_lines(buf, 0, 1, False, ["-- unsaved edit"])
        rejected("unsaved buffer")
        editor.api.buf_set_option(buf, "modified", False)
        # Prefix-sharing buffers in a different directory must be preserved.
        sibling = editor.exec_lua(
            "return vim.fn.bufadd(...)", str(target) + "-other/file"
        )
        editor.api.buf_set_lines(sibling, 0, -1, False, ["keep this unrelated draft"])
        editor.exec_lua("require('rediff.worktree').delete(...)", str(target))
        self.assertFalse(target.exists())
        self.assertFalse(editor.api.buf_is_valid(buf))
        self.assertTrue(editor.api.buf_is_valid(sibling))
        self.assertTrue(editor.api.buf_get_option(sibling, "modified"))

    def test_worktree_delete_rechecks_git_after_stopping_harness(self):
        target = Path(self.directory.name, "late-write")
        subprocess.run(
            ["git", "-C", self.root, "worktree", "add", "-b", "topic", str(target)],
            check=True,
            capture_output=True,
        )
        editor = self.launch(self.root)
        result = self.lua(
            editor,
            """
            local h = require('rediff.harness')
            local stop = h.stop
            h.stop = function(path)
                stop(path)
                vim.fn.writefile({'last-minute harness edit'}, path .. '/late.txt')
            end
            local ok,err = pcall(require('rediff.worktree').delete, 'topic')
            h.stop = stop
            return {ok,err}
        """,
        )
        self.assertFalse(result[0])
        self.assertIn("untracked", result[1])
        self.assertEqual(
            "last-minute harness edit\n", (target / "late.txt").read_text()
        )
        self.assertEqual(
            2, self.lua(editor, "return #require('rediff.worktree').list()")
        )

    def test_hunk_navigation_avoids_rebuilding_current_file_and_writing_drafts(self):
        # Only auth is staged; the other files remain in a different group.
        subprocess.run(["git", "-C", self.root, "add", "auth.lua"], check=True)
        editor = self.launch(self.root)
        self.lua(
            editor,
            """
            require('rediff.live').stop(s)
            vim.g.navigation_writes = 0
            local feedback = require('rediff.feedback')
            local write = feedback.write
            feedback.write = function(...)
                vim.g.navigation_writes = vim.g.navigation_writes + 1
                return write(...)
            end
            """,
        )
        tick = self.lua(editor, "return vim.api.nvim_buf_get_changedtick(s.new_buf)")
        for keys, hunk in (("[", 2), ("]", 1), ("3]", 2), ("3[", 1)):
            self.keys(editor, keys)
            self.assertEqual(
                ["auth.lua", "staged", hunk, tick],
                self.lua(
                    editor,
                    "return {s.current.path, s.current.group, s.selected_hunk, "
                    "vim.api.nvim_buf_get_changedtick(s.new_buf)}",
                ),
                "Wrapping within one file must not replace its source buffers",
            )
        self.keys(editor, ":fu<CR>")
        self.assertEqual("removed.lua", self.lua(editor, "return s.current.path"))
        self.keys(editor, "]")
        self.assertEqual("plan.md", self.lua(editor, "return s.current.path"))
        self.assertEqual(0, self.lua(editor, "return vim.g.navigation_writes"))

    def test_large_added_file_uses_blank_alignment_rows(self):
        Path(self.root, "large.lua").write_text(
            "".join(f"local value_{i} = {i}\n" for i in range(20000))
        )
        editor = self.launch(self.root)
        self.lua(editor, "require('rediff.live').stop(s)")
        self.keys(editor, "3]")
        self.assertEqual("large.lua", self.lua(editor, "return s.current.path"))
        # The old buffer has one empty physical line, so 19,999 fillers align it.
        self.assertEqual(
            [19999, 0, 20000],
            self.lua(
                editor,
                """
                local rows, decorated = 0, 0
                for _, mark in ipairs(vim.api.nvim_buf_get_extmarks(s.old_buf,
                    vim.api.nvim_get_namespaces()['codediff-filler'], 0, -1, {details=true})) do
                    for _, row in ipairs(mark[4].virt_lines or {}) do
                        rows = rows + 1
                        if #row > 0 then decorated = decorated + 1 end
                    end
                end
                return {rows, decorated, vim.api.nvim_buf_line_count(s.new_buf)}
                """,
            ),
            "Keep every alignment row without allocating wide filler decorations",
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
                        vim.api.nvim_get_namespaces()['rediff.active-file'], 0, -1, {})
                    if vim.api.nvim_get_current_win() == s.tree_win then
                        return s.rows[row] == s.index and vim.wo[s.tree_win].cursorline and #marks == 0
                    end
                    return s.rows[row] == s.index and not vim.wo[s.tree_win].cursorline
                        and #marks == 1 and marks[1][2] == row - 1
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
        self.keys(editor, ":fu<CR>")
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
        self.keys(editor, ":focus unstaged<CR>")
        position("unstaged", "auth.lua", 1, "old_win")
        self.keys(editor, ":focus st<Tab><CR>")
        position("staged", "auth.lua", 1, "old_win")
        self.keys(editor, ":focus un<Tab><CR>")
        position("unstaged", "auth.lua", 1, "old_win")
        self.keys(editor, ":fs<CR>")
        self.lua(editor, "vim.api.nvim_set_current_win(s.new_win)")
        self.keys(editor, "2]")
        position("staged", "removed.lua", 1, "new_win")
        self.keys(editor, "[")
        position("staged", "auth.lua", 2, "new_win")

        self.lua(editor, "vim.api.nvim_set_current_win(s.tree_win)")
        self.keys(editor, ":fu<CR>[")
        position("untracked", "plan.md", 1)
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
        self.keys(editor, ":focus unstaged<CR>")
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
        initial_index = subprocess.check_output(["git", "-C", self.root, "write-tree"])
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
            self.assertEqual(expected, self.lua(editor, "return s.current.path"))
        self.keys(editor, "S")

        def exhausted(group):
            # Key input returns before slow Git work finishes. Capture the index
            # only after the final file has actually left this group.
            self.wait_for(editor, "s.current == nil and s.index == nil")
            index = subprocess.check_output(["git", "-C", self.root, "write-tree"])
            # Both polling and manual refresh must preserve the empty group,
            # not select the file that just moved to the other group.
            self.lua(editor, "r.refresh_live()")
            self.keys(editor, " R")
            self.assertEqual(
                f" ▾ {group} (0)",
                self.lua(editor, "return vim.api.nvim_get_current_line()"),
            )
            self.assertTrue(
                self.lua(editor, "return s.current == nil and s.index == nil")
            )
            self.keys(editor, "SS")
            self.assertEqual(
                index, subprocess.check_output(["git", "-C", self.root, "write-tree"])
            )

        exhausted("UNSTAGED")
        # Unstaging the final row selects the preceding STAGED file, never the
        # same path's new UNSTAGED row. Subsequent S presses drain that group.
        self.keys(editor, ":fs<CR>jjS")
        self.assertEqual(
            ["plan.md", "staged"],
            self.lua(editor, "return {s.current.path, s.current.group}"),
        )
        self.keys(editor, "S")
        self.assertEqual(
            ["auth.lua", "staged"],
            self.lua(editor, "return {s.current.path, s.current.group}"),
        )
        self.keys(editor, "S")
        exhausted("STAGED")
        self.assertEqual(
            initial_index,
            subprocess.check_output(["git", "-C", self.root, "write-tree"]),
        )
        self.assertTrue(
            self.lua(
                editor,
                'return table.concat(vim.api.nvim_buf_get_lines(s.tree_buf, 0, -1, false), "\\n"):find("UNTRACKED", 1, true) == nil',
            )
        )

    def test_red_green_diff_backgrounds_preserve_syntax_after_theme_reload(self):
        editor = self.launch(self.root)
        for reload_theme in (False, True):
            if reload_theme:
                editor.command("colorscheme rose-pine")
            for name, background in (
                ("CodeDiffLineInsert", 0x234B2C),
                ("CodeDiffLineDelete", 0x602A2A),
                ("CodeDiffCharInsert", 0x356B3E),
                ("CodeDiffCharDelete", 0x8A3939),
            ):
                highlight = editor.api.get_hl(0, {"name": name})
                self.assertEqual(background, highlight["bg"])
                self.assertNotIn(
                    "fg", highlight, "Diff backgrounds must retain syntax colors"
                )
                self.assertFalse(highlight.get("nocombine", False))
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
        highlights = [editor.api.get_hl(0, {"name": group}) for group in deleted_groups]
        self.assertEqual({0x602A2A, 0x8A3939}, {hl["bg"] for hl in highlights})
        self.assertTrue(
            any("fg" in hl for hl in highlights),
            "Deleted virtual lines need syntax colors too",
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
            'return vim.api.nvim_buf_get_extmarks(s.new_buf, vim.api.nvim_get_namespaces()["rediff.difftastic"], 0, -1, {details=true})',
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
                'return vim.api.nvim_buf_get_extmarks(s.new_buf, vim.api.nvim_get_namespaces()["rediff.difftastic"], 0, -1, {})',
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
