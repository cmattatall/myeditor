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

    def test_saved_amp_connection_is_rediscovered_before_sending(self):
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
                editor.command("Harness send")
                self.assertTrue(editor.exec_lua("return saved_session.restoring"))
                self.assertEqual(
                    0,
                    editor.exec_lua(
                        "return #require('rediff.connections').connected()"
                    ),
                )
                self.assertNotEqual("acwrite", editor.current.buffer.options["buftype"])
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
                    self.assertEqual(
                        "acwrite", editor.current.buffer.options["buftype"]
                    )
                    self.assertEqual([""], list(editor.current.buffer[:]))
                    self.assertEqual(1, editor.exec_lua("return #bridge_calls"))
                    editor.current.buffer[:] = ["Send to the selected local thread"]
                    editor.command("write")
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
                elif outcome == "gone":
                    self.assertEqual(
                        "connect",
                        editor.exec_lua(
                            "return require('rediff.harness_panel').state.purpose"
                        ),
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
                    self.assertNotEqual(
                        "acwrite", editor.current.buffer.options["buftype"]
                    )
                    self.assertEqual(
                        0,
                        editor.exec_lua(
                            "return #require('rediff.connections').connected()"
                        ),
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
        for kind in ("message", "annotation"):
            with self.subTest(kind=kind):
                if kind == "message":
                    self.keys(editor, ":harness send<CR>")
                else:
                    self.lua(
                        editor,
                        "vim.api.nvim_set_current_win(s.new_win); "
                        "vim.api.nvim_win_set_cursor(0,{5,0}); r.compose()",
                    )
                    self.keys(editor, "<Esc>")
                writes = self.lua(editor, "return vim.g.feedback_writes")
                self.keys(editor, f"iPending {kind}<Esc>")
                self.assertEqual(
                    writes, self.lua(editor, "return vim.g.feedback_writes")
                )
                if kind == "annotation":
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
                        self.lua(
                            editor, "return require('rediff.feedback').busy(s.root)"
                        )
                    )
                    self.assertTrue(self.lua(editor, "return vim.g.delivery_tick"))
                    if kind == "message":
                        self.keys(editor, "A with newer edits<Esc>")
                        self.assertEqual(
                            ["Pending message with newer edits"],
                            list(editor.current.buffer[:]),
                        )
                        self.keys(editor, "<Tab>")
                        self.assertTrue(
                            self.lua(
                                editor,
                                "return vim.api.nvim_get_current_win() == s.tree_win",
                            )
                        )
                    else:
                        self.keys(editor, "gg3j")
                        self.assertEqual(
                            4,
                            self.lua(
                                editor, "return vim.api.nvim_win_get_cursor(0)[1]"
                            ),
                        )
                        self.assertEqual(1, self.lua(editor, "return #s.comments"))
                    self.assertTrue(
                        self.lua(
                            editor, "return require('rediff.feedback').busy(s.root)"
                        )
                    )
                finally:
                    Path(path + ".release").touch()
                self.wait_for(editor, "not require('rediff.feedback').busy(s.root)")
                self.assertEqual(
                    "completed",
                    self.lua(
                        editor, "return require('rediff.harness').get(s.root).delivery"
                    ),
                )
                if kind == "message":
                    self.assertEqual(
                        "Pending message with newer edits",
                        self.lua(
                            editor,
                            "return require('rediff.harness').get(s.root).message",
                        ),
                    )
                else:
                    self.assertEqual(0, self.lua(editor, "return #s.comments"))

    def test_harness_use_discovers_and_picks_live_session(self):
        editor = self.launch(self.root)
        self.lua(
            editor,
            """
            local system = vim.system
            vim.system = function(argv, opts, callback)
                if argv[1] ~= 'rediff-amp-live' then return system(argv, opts, callback) end
                assert(argv[2] == 'discover' and argv[3] == '--all')
                callback({code=0, stdout=vim.json.encode({
                    {root=s.root, session='T-first', title='Alpha', connection='/fake/first.json'},
                    {root=s.root, session='T-second', title='Beta', connection='/fake/second.json'},
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
                if argv[1] ~= 'rediff-amp-live' then return system(argv,opts,callback) end
                if argv[2] == 'discover' then
                    callback({code=0,stdout=vim.json.encode({
                        {root=s.root,session='T-local',title='Local review',connection='/fake/local',capabilities={'activity'}},
                        {root=vim.g.external_root,session='T-remote',title='Remote worker',connection='/fake/remote',capabilities={'activity'}},
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
        self.assertEqual(
            "manage",
            self.lua(editor, "return require('rediff.harness_panel').state.purpose"),
        )
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
        self.keys(editor, ":harness send<CR>")
        self.assertEqual(
            "send",
            self.lua(editor, "return require('rediff.harness_panel').state.purpose"),
        )
        self.keys(editor, "/backend<CR><CR>")
        self.assertEqual("acwrite", editor.current.buffer.options["buftype"])
        self.keys(editor, "iMessage for external worker<Esc>:w<CR>")
        self.wait_for(editor, "not require('rediff.feedback').busy(s.root)")
        sent = self.lua(editor, "return _G.sent_feedback[1]")
        self.assertEqual(remote, sent["cwd"])
        self.assertEqual("T-remote", sent["argv"][3])
        self.assertEqual(self.root, sent["payload"]["sender"]["repository"])
        self.assertEqual(self.root, sent["payload"]["sender"]["directory"])
        self.assertTrue(sent["payload"]["sender"]["instance"])
        self.assertEqual(
            {"provider": "amp", "id": "T-remote", "repository": remote},
            sent["payload"]["recipient"],
        )
        self.assertNotIn("comments", sent["payload"])
        self.assertEqual([""], list(editor.current.buffer[:]))
        self.assertEqual(1, self.lua(editor, "return #s.comments"))
        self.keys(editor, "iRetain this remote draft<Esc>:q<CR>")
        self.keys(editor, ":harness send T-local<CR>")
        self.assertEqual([""], list(editor.current.buffer[:]))
        self.keys(editor, "iIndependent local draft<Esc>:q<CR>")
        self.keys(editor, ":harness send backend<CR>")
        self.assertEqual(["Retain this remote draft"], list(editor.current.buffer[:]))
        self.keys(editor, ":q<CR>")
        self.lua(editor, "vim.api.nvim_set_current_win(s.new_win)")
        self.keys(editor, ":w<CR>")
        self.wait_for(editor, "not require('rediff.feedback').busy(s.root)")
        annotation = self.lua(editor, "return _G.sent_feedback[2]")
        self.assertEqual("T-local", annotation["argv"][3])
        self.assertEqual(
            "Local annotation", annotation["payload"]["comments"][0]["text"]
        )
        self.assertEqual(0, self.lua(editor, "return #s.comments"))
        self.assertFalse(
            self.lua(
                editor,
                "return pcall(require('rediff.harness').select,s.root,{name='amp-live',root=vim.g.external_root,session='T-remote',connection='/fake/remote'})",
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
        self.keys(editor, "<CR>q:harness send backend<CR>")
        self.assertEqual(["Retain this remote draft"], list(editor.current.buffer[:]))
        self.assertEqual(2, self.lua(editor, "return #_G.sent_feedback"))

    def test_harness_stream_lifecycle_and_alias_persistence(self):
        editor = self.launch(self.root)
        self.lua(
            editor,
            """
            require('rediff.live').stop(s)
            _G.streams = {}
            local system = vim.system
            vim.system = function(argv, opts, callback)
                if argv[1] ~= 'rediff-amp-live' then return system(argv,opts,callback) end
                assert(argv[2] == 'watch')
                local stream = {out=opts.stdout,exit=callback,killed=false}
                table.insert(streams,stream)
                return {kill=function() stream.killed=true end}
            end
            _G.registry = require('rediff.connections')
            _G.target = {name='amp-live',root=s.root,session='T-stream',connection='/fake/stream',capabilities={'activity'}}
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
            self.keys(editor, " p")
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
                self.keys(editor, ":q<CR>")
                self.wait_for(editor, "s.composer == nil")
                self.assertTrue(
                    self.lua(
                        editor, f"return vim.api.nvim_get_current_win() == s.{side}_win"
                    )
                )
        self.assertEqual(original, Path(self.root, "auth.lua").read_bytes())
        self.assertEqual(
            index, subprocess.check_output(["git", "-C", self.root, "write-tree"])
        )

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
        self.keys(editor, "iDiscard this edit<Esc>:q!<CR>")
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
            ["Updated boundary", "Check lease timeout"], list(editor.current.buffer[:])
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
            self.lua(editor, 'return require("rediff.harness").get(s.root).last == nil')
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
                'local rows = {}; for _, mark in ipairs(vim.api.nvim_buf_get_extmarks(s.tree_buf, vim.api.nvim_get_namespaces()["rediff.active-file"], 0, -1, {})) do table.insert(rows, mark[2]) end; return rows',
            ),
        )
        self.assertTrue(
            self.lua(editor, "return vim.api.nvim_get_current_win() == s.tree_win")
        )
        # Absolute motions skip headings and select a file too.
        self.keys(editor, "gg")
        self.assertEqual("auth.lua", self.lua(editor, "return s.current.path"))
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
            ("h", "removed.lua"),
            ("l", "removed.lua"),
            ("<Left>", "removed.lua"),
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
        self.assertEqual("", self.lua(editor, 'return vim.fn.maparg("<CR>", "n")'))
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
        self.assertEqual(" UNSTAGED (0)", editor.current.line)
        self.assertIn("No file selected", self.lua(editor, "return r.statusline()"))
        self.lua(editor, "r.refresh_live()")
        self.keys(editor, " R")
        self.keys(editor, "]")
        self.assertTrue(self.lua(editor, "return s.current == nil"))
        self.assertEqual(" UNSTAGED (0)", editor.current.line)
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
            self.lua(editor, 'return require("rediff.harness").get(s.root).last == nil')
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
            local feedback = require('rediff.feedback')
            feedback.settings = function() return {feedback_command={'fake-receiver'}} end
            require('rediff.harness').select(s.root, {name='custom'})
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
            editor, 'require("rediff.harness").get(s.root).delivery == "accepted"'
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
        self.lua(editor, 'require("rediff.live").stop(s)')
        path = Path(self.root, "auth.lua")
        path.write_text(path.read_text().replace("return nil", "return 321"))
        self.keys(editor, " R")
        self.assertIn("return 321", self.lua(editor, "return s.current.new"))

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
        self.keys(editor, "<Tab>")
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

        self.lua(editor, "vim.api.nvim_set_current_win(s.tree_win)")
        self.keys(editor, ":fm<CR>[")
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
            index = subprocess.check_output(["git", "-C", self.root, "write-tree"])
            # Both polling and manual refresh must preserve the empty group,
            # not select the file that just moved to the other group.
            self.lua(editor, "r.refresh_live()")
            self.keys(editor, " R")
            self.assertEqual(
                f" {group} (0)",
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
