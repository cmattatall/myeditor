local tests = vim.fn.fnamemodify(debug.getinfo(1, "S").source:sub(2), ":p:h")

return function(root, equal, fails, keys)
	local harness = require("myeditor.harness")
	local feedback = require("myeditor.feedback")
	local review = require("myeditor.review")
	local s, session = review.state, harness.get(root)
	local thread = "T-00000000-1111-2222-3333-444444444444"
	equal(1, vim.fn.executable("myeditor-install-amp-plugin"), "Amp installer is packaged")
	local home, confirm, notify = vim.env.HOME, vim.fn.confirm, vim.notify
	local install_home = vim.fn.tempname()
	vim.env.HOME = install_home
	local plugin = install_home .. "/.config/amp/plugins/anthrodiff.ts"
	local notices, choice, prompt = {}, 2, nil
	vim.notify = function(message)
		table.insert(notices, message)
	end
	vim.fn.confirm = function(message, _, default)
		prompt = message
		equal(2, default, "Plugin installation defaults to cancel")
		return choice
	end
	local install_ok, install_err = xpcall(function()
		keys(":harness install amp<CR>")
		equal(0, vim.fn.filereadable(plugin), "Cancelling installation writes nothing")
		choice = 1
		vim.cmd("Harness install amp")
		fails(harness.install, "already running")
		assert(vim.wait(5000, function()
			return #notices > 0
		end))
		equal(true, notices[1]:find("Installed", 1, true) ~= nil, "Installer reports success")
		local bundled = vim.env.MYEDITOR_RUNTIME .. "/amp/anthrodiff.ts"
		equal(vim.fn.readfile(bundled), vim.fn.readfile(plugin), "Editor installs its exact bundled plugin")
		local legacy = install_home .. "/.config/amp/plugins/revdiff.ts"
		vim.fn.writefile({ "old plugin" }, legacy)
		notices = {}
		vim.cmd("Harness install amp")
		assert(vim.wait(5000, function()
			return #notices > 0
		end))
		equal(true, prompt:find("old revdiff.ts will be moved", 1, true) ~= nil, "Replacement is explicitly disclosed")
		equal(0, vim.fn.filereadable(legacy), "Approved replacement removes the old active plugin")
		equal(
			{ "old plugin" },
			vim.fn.readfile(install_home .. "/.config/amp/plugin-backups/revdiff.ts"),
			"Replacement retains a backup"
		)
		vim.fn.delete(plugin)
		assert(vim.uv.fs_symlink(bundled, plugin))
		notices = {}
		vim.cmd("Harness install amp")
		assert(vim.wait(5000, function()
			return #notices > 0
		end))
		equal(true, notices[1]:find("Home Manager", 1, true) ~= nil, "Installer failures are visible")
		equal("link", vim.uv.fs_lstat(plugin).type, "Home Manager symlink is preserved")
		equal({ "amp" }, vim.fn.getcompletion("Harness install ", "cmdline"), "Install provider completion")
	end, debug.traceback)
	vim.env.HOME, vim.fn.confirm, vim.notify = home, confirm, notify
	vim.fn.delete(install_home, "rf")
	assert(install_ok, install_err)

	-- Reload uses the existing authenticated bridge, never the draft composer.
	local original_system, original_target = vim.system, session.target
	local calls, callbacks = {}, {}
	session.target = { name = "amp-live", connection = "/fake/connection.json", session = thread }
	local original_message, original_last = session.message, session.last
	vim.fn.confirm = function(message)
		assert(message:find(thread, 1, true) and message:find("reload", 1, true))
		return choice
	end
	vim.notify = function(message)
		table.insert(notices, message)
	end
	vim.system = function(argv, _, callback)
		table.insert(calls, argv)
		table.insert(callbacks, callback)
	end
	local reload_ok, reload_err = xpcall(function()
		choice = 2
		harness.install()
		equal(0, #calls, "Cancelled install never requests reload")
		choice = 1
		harness.install()
		callbacks[1]({ code = 1, stderr = "Install failed" })
		vim.wait(10)
		equal(1, #calls, "Failed install never requests reload")
		harness.install()
		callbacks[2]({ code = 0, stdout = "Installed" })
		vim.wait(10)
		equal(
			{ "myeditor-amp-live", "reload", "/fake/connection.json", thread, root },
			calls[3],
			"Successful install requests reload of the confirmed target"
		)
		fails(harness.install, "already running")
		callbacks[3]({ code = 0, stdout = "Reload request queued" })
		vim.wait(10)
		equal("Reload request queued", notices[#notices], "Queued is not reported as reloaded")
		harness.install()
		callbacks[4]({ code = 0, stdout = "Installed" })
		vim.wait(10)
		callbacks[5]({ code = 1, stderr = "Reload outcome uncertain" })
		vim.wait(10)
		equal("Reload outcome uncertain", notices[#notices], "Reload failure remains visible")
		equal(5, #calls, "Uncertain reload is never automatically retried")
		equal(original_message, session.message, "Reload does not change the message draft")
		equal(original_last, session.last, "Reload preserves the last feedback retry target")
	end, debug.traceback)
	vim.system, vim.fn.confirm, vim.notify = original_system, confirm, notify
	session.target = original_target
	assert(reload_ok, reload_err)

	equal(1, vim.fn.executable("myeditor-amp-live"), "Live adapter is packaged")
	local system, discover, picker = vim.system, nil, nil
	local fzf = require("fzf-lua")
	local fzf_exec = fzf.fzf_exec
	fzf.fzf_exec = function(items, opts)
		picker = { items = items, opts = opts }
	end
	vim.system = function(argv, opts, callback)
		equal({ "myeditor-amp-live", "discover", root }, argv, "Connection only discovers in the current root")
		equal(root, opts.cwd, "Discovery cwd is the worktree")
		discover = callback
	end
	local matches = { { session = thread, connection = "/fake/session-one/connection.json", title = "one" } }
	local function respond(value)
		discover({ code = 0, stderr = "", stdout = vim.json.encode(value) })
		vim.wait(10)
	end
	keys(":harness connect amp<CR>")
	respond(matches)
	equal("amp-live", s.harness.name, "Lowercase connect binds Review and messages together")
	equal(
		"harness: amp-live · …44444444 · idle",
		harness.statusline(root),
		"Status names the live harness and thread suffix"
	)
	equal(true, review.statusline():find(harness.statusline(root), 1, true) ~= nil, "Review displays harness status")
	equal(
		{ "myeditor-amp-live", "send", matches[1].connection, thread },
		feedback.command(s.harness),
		"Live receiver argv"
	)
	vim.cmd("Harness connect amp")
	vim.cmd("Harness disconnect")
	respond(matches)
	equal("none", s.harness.name, "Late discovery cannot undo disconnect")
	vim.cmd("Harness connect amp")
	matches[2] = { session = "T-second", connection = "/fake/session-two/connection.json", title = "two" }
	respond(matches)
	equal(2, #picker.items, "Multiple sessions require a picker")
	equal("none", s.harness.name, "Opening picker does not bind or send")
	picker.opts.actions.enter({ picker.items[2] })
	equal("T-second", s.harness.session, "Session picker selects by ID")
	vim.cmd("Harness connect amp")
	respond({})
	equal("T-second", s.harness.session, "No match preserves the existing binding")
	vim.system, fzf.fzf_exec = system, fzf_exec
	vim.api.nvim_set_current_tabpage(s.previous_tab)
	equal(
		true,
		review.statusline():find("harness: amp-live · T-second · idle", 1, true) ~= nil,
		"Editing also displays the binding"
	)
	local git = require("myeditor.git")
	local get_root = git.root
	git.root = function()
		error("Status redraw must not run Git")
	end
	equal("harness: amp-live · T-second · idle", harness.statusline(), "Repeated redraw reuses workspace context")
	git.root = get_root
	vim.api.nvim_set_current_tabpage(s.tab)
	vim.cmd("Harness disconnect")

	local comment = review.add_comment({
		file = s.current.path,
		side = "new",
		snapshot_id = s.current.id,
		selection = require("myeditor.selection").line(s.new_buf, 5),
	}, "Unsent review comment")
	local settings = feedback.settings
	feedback.settings = function()
		return {
			feedback_command = { vim.v.progpath, "--headless", "-u", "NONE", "-l", tests .. "/receiver.lua", "ok" },
		}
	end
	harness.select(root, { name = "custom" })
	keys(":hs<CR>iExplain the approach.<Esc>")
	local buf, win = session.buf, vim.api.nvim_get_current_win()
	equal("acwrite", vim.bo[buf].buftype, "hs opens a normal editable message buffer")
	for _, key in ipairs({ "i", "c", "d", "p", "u" }) do
		equal("", vim.fn.maparg(key, "n"), "Message composer preserves native " .. key)
	end
	vim.cmd.write()
	equal(true, feedback.busy(root), "Message and review share the in-flight lock")
	equal("harness: custom · running", harness.statusline(root), "Status shows an in-flight message")
	fails(function()
		harness.select(root, { name = "none" })
	end, "Wait for the current")
	fails(review.submit, "Wait for the current")
	assert(vim.wait(10000, function()
		return not feedback.busy(root)
	end))
	equal("harness: custom · completed", harness.statusline(root), "Acknowledgment updates the persistent status bar")
	local path = session.last.path
	local payload = feedback.read(path)
	equal("Explain the approach.", payload.message, "Write sends exact message text")
	equal(nil, payload.comments, "General message excludes pending comments")
	equal(nil, payload.snapshots, "General message excludes review snapshots")
	equal(comment, s.comments[1], "General send retains review drafts")
	equal(win, vim.api.nvim_get_current_win(), "Write keeps composer open")
	equal({ "" }, vim.api.nvim_buf_get_lines(buf, 0, -1, false), "Successful :w clears the visible message")
	equal(nil, feedback.read(session.directory .. "/harness.json").message, "Composer text is never persisted")
	keys(":w<CR>")
	equal({ "1" }, vim.fn.readfile(path .. ".calls"), "Writing an empty composer sends nothing")
	keys(":wq<CR>")
	equal(false, vim.api.nvim_win_is_valid(win), "Write-quit closes only message window")
	equal(s, review.state, "Message write-quit preserves Review")
	-- Lifecycle tests use the command directly; real-input alias coverage lives in input.lua.
	vim.cmd("Harness send")
	equal({ "" }, vim.api.nvim_buf_get_lines(session.buf, 0, -1, false), "Next message opens empty")
	keys("iExplain the approach.<Esc>:q!<CR>")
	vim.cmd("Harness send")
	equal(
		{ "Explain the approach." },
		vim.api.nvim_buf_get_lines(session.buf, 0, -1, false),
		"An old acknowledgment cannot clear a newly typed identical message"
	)
	vim.api.nvim_buf_set_lines(session.buf, 0, -1, false, { "" })
	keys("iKeep this unsent.<Esc>:q<CR>")
	equal("Keep this unsent.", session.message, "Plain quit retains changed message")
	equal(path, session.last.path, "Plain quit does not send")
	vim.cmd("Harness send")
	equal(
		{ "Keep this unsent." },
		vim.api.nvim_buf_get_lines(session.buf, 0, -1, false),
		"Reopen restores unsent draft"
	)
	local layout = vim.fn.winlayout()
	keys("?")
	equal("editor", vim.api.nvim_win_get_config(0).relative, "Message help is an overlay")
	keys("?")
	equal(layout, vim.fn.winlayout(), "Closing message help preserves layout")
	fails(function()
		vim.cmd("write " .. vim.fn.fnameescape(root .. "/leak.txt"))
	end, "do not write files")
	equal(0, vim.fn.filereadable(root .. "/leak.txt"), "Named writes cannot leak a message")
	keys(":q!<CR>")
	harness.retry()
	equal(path, session.last.path, "Retry uses original payload, not edited message")
	equal({ "1" }, vim.fn.readfile(path .. ".calls"), "Retry cannot resend a completed message")
	vim.cmd("Harness disconnect")
	fails(harness.retry, "Harness changed")
	equal(
		"harness: local · idle",
		harness.statusline(root),
		"Disconnect cannot attribute old delivery to a new target"
	)
	equal("Keep this unsent.", session.message, "Disconnect retains message draft")
	equal(comment, s.comments[1], "Disconnect retains review drafts")
	equal(
		"none",
		feedback.read(session.directory .. "/harness.json").target.name,
		"Binding is persisted independently of Review"
	)
	vim.cmd("Harness send")
	keys(":w<CR>")
	equal("Keep this unsent.", session.message, "Local queued feedback is not treated as sent")
	harness.select(root, { name = "custom" })
	notices = {}
	vim.notify = function(message)
		table.insert(notices, message)
	end
	local pending
	vim.system = function(_, _, callback)
		pending = callback
	end
	local function finish(status)
		pending({
			code = status == "failed" and 1 or 0,
			stderr = status == "failed" and "Fixture failure" or "",
			stdout = vim.json.encode({ submission_id = session.last.id, status = status }),
		})
		assert(vim.wait(1000, function()
			return not feedback.busy(root)
		end))
	end
	keys(":wq<CR>")
	equal(false, vim.fn.bufwinid(session.buf) ~= -1, "wq closes while send is pending")
	vim.cmd("Harness send")
	equal(
		{ "Keep this unsent." },
		vim.api.nvim_buf_get_lines(session.buf, 0, -1, false),
		"Pending send retains draft on reopen"
	)
	finish("failed")
	equal(true, notices[1]:find("Fixture failure", 1, true) ~= nil, "Delivery failure is reported")
	equal(
		{ "Keep this unsent." },
		vim.api.nvim_buf_get_lines(session.buf, 0, -1, false),
		"Failure preserves visible message"
	)
	harness.retry()
	finish("accepted")
	equal({ "" }, vim.api.nvim_buf_get_lines(session.buf, 0, -1, false), "Accepted retry clears the message")
	keys("iFirst message<Esc>:w<CR>")
	vim.api.nvim_buf_set_lines(session.buf, 0, -1, false, { "New draft typed during delivery" })
	finish("accepted")
	equal(
		{ "New draft typed during delivery" },
		vim.api.nvim_buf_get_lines(session.buf, 0, -1, false),
		"Late ACK preserves newer buffer edits even before capture"
	)
	equal(
		nil,
		feedback.read(session.directory .. "/harness.json").message,
		"Late ACK does not persist draft text across sessions"
	)
	keys(":q!<CR>")
	vim.cmd("Harness send")
	equal("New draft typed during delivery", session.message, "Reopen preserves the newer draft")
	keys(":wq<CR>")
	finish("completed")
	vim.cmd("Harness send")
	equal(
		{ "" },
		vim.api.nvim_buf_get_lines(session.buf, 0, -1, false),
		"ACK after closing also clears the next composition"
	)
	keys(":q<CR>")
	vim.system, vim.notify = system, notify
	equal(comment, s.comments[1], "Clearing messages never clears review comments")
	vim.cmd("Harness send")
	keys("iSeparate unsent message<Esc>:q!<CR>")
	vim.api.nvim_set_current_win(s.new_win)
	keys(":w<CR>")
	assert(vim.wait(10000, function()
		return not feedback.busy(root)
	end))
	local review_payload = feedback.read(s.last_submission)
	equal({ comment }, review_payload.comments, "Review :w sends the accumulated annotation batch")
	equal(nil, review_payload.message, "Review :w excludes the harness message draft")
	equal({}, s.comments, "Successful review send clears individual annotations")
	equal("Separate unsent message", session.message, "Review acknowledgment does not clear a general message")
	feedback.settings = settings
	review.archive()
	vim.cmd("new")
	vim.api.nvim_buf_set_lines(0, 0, -1, false, { "Unwritten ordinary buffer" })
	local hidden = vim.o.hidden
	vim.o.hidden = false
	fails(function()
		vim.cmd("q")
	end, "No write since last change")
	equal(true, vim.bo.modified, "Harness quit handling preserves ordinary unsaved-file protection")
	vim.cmd("q!")
	vim.o.hidden = hidden

	-- Exercise disk restoration in fresh editors, not the in-memory session cache.
	local restart_root = vim.fn.tempname()
	vim.fn.mkdir(restart_root, "p")
	assert(vim.system({ "git", "init", restart_root }, { text = true }):wait().code == 0)
	restart_root = vim.uv.fs_realpath(restart_root)
	local directory = feedback.directory(restart_root)
	local function reopen(retype, hidden)
		local child = vim.fn.jobstart(
			{ vim.fn.exepath("myeditor"), "--embed", "--headless", "-i", "NONE" },
			{ rpc = true }
		)
		assert(child > 0)
		local ok, text = pcall(
			vim.rpcrequest,
			child,
			"nvim_exec_lua",
			[[
			local root, retype, hidden = ...
			vim.cmd.cd(root)
			require("myeditor.feedback").deliver = function() error("Opening must not send") end
			vim.cmd("Harness send")
			local text = table.concat(vim.api.nvim_buf_get_lines(0, 0, -1, false), "\n")
			if retype then
				vim.api.nvim_buf_set_lines(0, 0, -1, false, {retype})
			end
			vim.cmd(hidden and "hide" or "q")
			return text
		]],
			{ restart_root, retype or false, hidden or false }
		)
		vim.rpcnotify(child, "nvim_command", "q")
		local exited = vim.fn.jobwait({ child }, 5000)[1]
		if exited == -1 then
			vim.fn.jobstop(child)
			vim.fn.jobwait({ child }, 1000)
		end
		assert(ok, text)
		equal(0, exited, "Plain quit exits even with a hidden message draft")
		return text
	end
	local ok, err = xpcall(function()
		for _, case in ipairs({
			{ status = "accepted" },
			{ status = "completed" },
			{ status = "failed" },
			{ status = "running" },
			{ status = "queued" },
			{ status = "accepted", draft = "New unsent draft" },
			{ status = "accepted", review = true },
			{ status = "accepted", tracked = false },
			{ status = "accepted", tracked = true },
		}) do
			local id, payload_path = feedback.enqueue(
				restart_root,
				case.review and { { text = "Annotation" } } or nil,
				nil,
				{},
				not case.review and "Sent text" or nil
			)
			feedback.write(directory .. "/" .. id .. ".status.json", { status = case.status })
			local last = { id = id, path = payload_path, argv = {} }
			feedback.write(directory .. "/harness.json", {
				target = { name = "none" },
				message = case.draft or "Sent text",
				last = last,
				last_message = case.tracked == true and last or case.tracked,
			})
			equal("", reopen("Sent text"), "Fresh launch: " .. vim.inspect(case))
			equal(nil, feedback.read(directory .. "/harness.json").message, "Legacy draft text is removed on save")
			equal(nil, feedback.read(directory .. "/harness.json").last_message, "ACK draft identity is process-local")
			equal(last, feedback.read(directory .. "/harness.json").last, "Delivery reference remains available")
			equal("", reopen(), "Another restart discards even a newly typed identical draft")
		end
		reopen("Hidden pending draft", true)
		equal("", reopen(), "Fresh launch never restores hidden composer text")
	end, debug.traceback)
	vim.fn.delete(restart_root, "rf")
	vim.fn.delete(directory, "rf")
	assert(ok, err)
end
