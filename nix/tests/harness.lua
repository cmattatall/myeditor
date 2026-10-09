local tests = vim.fn.fnamemodify(debug.getinfo(1, "S").source:sub(2), ":p:h")

return function(root, equal, fails, keys)
	local harness = require("rediff.harness")
	local feedback = require("rediff.feedback")
	local review = require("rediff.review")
	local connections = require("rediff.connections")
	for _, entry in ipairs(connections.connected()) do
		connections.disconnect(entry.key)
	end
	local s, session = review.state, harness.get(root)
	local thread = "T-00000000-1111-2222-3333-444444444444"
	equal(1, vim.fn.executable("rediff-install-amp-plugin"), "Amp installer is packaged")
	local home, confirm, notify = vim.env.HOME, vim.fn.confirm, vim.notify
	local install_home = vim.fn.tempname()
	vim.env.HOME = install_home
	local plugin = install_home .. "/.config/amp/plugins/readiff.ts"
	local notices, choice = {}, 2
	vim.notify = function(message)
		table.insert(notices, message)
	end
	vim.fn.confirm = function(message, _, default)
		equal(
			true,
			message:find("readiff.ts", 1, true) ~= nil or message:find("oh-my-pi", 1, true) ~= nil,
			"Confirmation names the installed plugin"
		)
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
		local bundled = vim.env.REDIFF_RUNTIME .. "/amp/readiff.ts"
		equal(vim.fn.readfile(bundled), vim.fn.readfile(plugin), "Editor installs its exact bundled plugin")
		equal(
			"ctrl+l",
			vim.json.decode(table.concat(vim.fn.readfile(install_home .. "/.config/amp/settings.json"), "\n"))["amp.keymap"]["thread.interrupt"],
			"Harness install configures Amp interruption away from Escape"
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
		equal({ "amp", "omp" }, vim.fn.getcompletion("Harness install ", "cmdline"), "Install provider completion")
		local omp_plugin = install_home .. "/.omp/agent/extensions/rediff.ts"
		choice = 2
		vim.cmd("Harness install omp")
		equal(0, vim.fn.filereadable(omp_plugin), "Cancelling OMP installation writes nothing")
		choice, notices = 1, {}
		vim.cmd("Harness install omp")
		assert(vim.wait(5000, function()
			return #notices > 0
		end))
		equal(
			vim.fn.readfile(vim.env.REDIFF_RUNTIME .. "/omp/rediff.ts"),
			vim.fn.readfile(omp_plugin),
			"OMP extension is bundled"
		)
		equal(true, notices[1]:find("/restart", 1, true) ~= nil, "OMP restart is explicit, not sent to an agent")
	end, debug.traceback)
	vim.env.HOME, vim.fn.confirm, vim.notify = home, confirm, notify
	vim.fn.delete(install_home, "rf")
	assert(install_ok, install_err)

	-- Reload uses the existing authenticated bridge, never a feedback delivery.
	local original_system, original_target = vim.system, session.target
	local calls, callbacks = {}, {}
	session.target = { name = "amp-live", connection = "/fake/connection.json", session = thread }
	local original_last = session.last
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
			{ "rediff-amp-live", "reload", "/fake/connection.json", thread, root },
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
		equal(original_last, session.last, "Reload preserves the last feedback retry target")
	end, debug.traceback)
	vim.system, vim.fn.confirm, vim.notify = original_system, confirm, notify
	session.target = original_target
	assert(reload_ok, reload_err)

	equal(1, vim.fn.executable("rediff-amp-live"), "Live adapter is packaged")
	local system, discover, picker = vim.system, nil, nil
	local panel = require("rediff.harness_panel")
	local panel_open = panel.open
	panel.open = function(opts)
		-- Select from this discovery's Amp rows, not retained CLI bindings from earlier tests.
		picker = {
			items = vim.tbl_filter(function(entry)
				return entry.name == "amp-live" and entry.root == root and entry.online
			end, connections.items()),
			opts = opts,
		}
	end
	vim.system = function(argv, opts, callback)
		equal({ "rediff-live", "discover", "--all" }, argv, "Discovery lists sessions across worktrees")
		equal(true, opts.text, "Discovery returns JSON text asynchronously")
		discover = callback
	end
	local matches = {
		{
			provider = "amp",
			root = root,
			session = thread,
			connection = "/fake/session-one/connection.json",
			title = "one",
		},
	}
	local function respond(value)
		discover({ code = 0, stderr = "", stdout = vim.json.encode(value) })
		vim.wait(10)
	end
	keys(":harness use amp<CR>")
	respond(matches)
	equal("amp", session.provider, "Use persists the worktree launch type")
	equal("amp-live", s.harness.name, "Lowercase use connects Review feedback to the only live session")
	equal(
		"harness: amp-live · …44444444 · idle",
		harness.statusline(root),
		"Status names the live harness and thread suffix"
	)
	equal(true, review.statusline():find(harness.statusline(root), 1, true) ~= nil, "Review displays harness status")
	equal(
		{ "rediff-amp-live", "send", matches[1].connection, thread },
		feedback.command(s.harness),
		"Live receiver argv"
	)
	harness.connect()
	vim.cmd("Harness disconnect")
	respond(matches)
	equal("none", s.harness.name, "Late discovery cannot undo disconnect")
	vim.cmd("Harness use amp")
	matches[2] = {
		provider = "amp",
		root = root,
		session = "T-second",
		connection = "/fake/session-two/connection.json",
		title = "two",
	}
	respond(matches)
	equal(2, #picker.items, "Multiple sessions require a picker")
	equal("none", s.harness.name, "Opening picker does not bind or send")
	picker.opts.on_select(picker.items[2])
	equal("T-second", s.harness.session, "Session picker selects by ID")
	vim.cmd("Harness use amp")
	respond({})
	equal("T-second", s.harness.session, "No match preserves the existing binding")
	vim.cmd("Harness use amp")
	respond(matches)
	-- Dismiss without invoking the panel's selection callback.
	equal("T-second", s.harness.session, "Dismissing selection preserves the existing binding")
	discover = nil
	vim.cmd("Harness use claude")
	equal(nil, discover, "Claude has no live discovery adapter")
	picker.opts.on_select(picker.items[1])
	equal("none", s.harness.name, "Old picker cannot rebind after switching providers")
	vim.cmd("Harness use amp")
	vim.cmd("Harness use claude")
	respond(matches)
	equal("claude", session.provider, "Late Amp discovery cannot change the selected provider")
	equal("none", s.harness.name, "Late Amp discovery cannot reconnect after switching providers")
	local no_match, no_match_level
	vim.notify = function(message, level)
		no_match, no_match_level = message, level
	end
	vim.cmd("Harness use amp")
	respond({})
	vim.notify = notify
	equal(vim.log.levels.INFO, no_match_level, "No matches is guidance, not a Lua error")
	equal(
		"No live Amp session found for this checkout. Reload Amp's plugins, then run :harness connect amp.",
		no_match,
		"No matches explains recovery"
	)
	equal("amp", session.provider, "No matches still selects Amp for new worktrees")
	equal("none", s.harness.name, "No matches does not invent a feedback connection")
	discover = nil
	harness.use("amp", root)
	equal(nil, discover, "Inheriting a launch type does not discover sessions for a new worktree")
	harness.connect()
	respond(matches)
	picker.opts.on_select(picker.items[2])
	equal(false, connections.get(picker.items[2].key).connected, "Enter disconnects an already connected harness")
	picker.opts.on_select(connections.get(picker.items[2].key))
	local amp_target = vim.deepcopy(s.harness)
	local omp = {
		provider = "omp",
		root = root,
		session = "11111111-2222-4333-8444-555555555555",
		connection = "/fake/omp/connection.json",
		title = "OMP",
	}
	vim.cmd("Harness use omp")
	respond({ matches[1], matches[2], omp })
	equal("omp", session.provider, "OMP is the worktree launch type")
	equal("omp-live", s.harness.name, "Only the matching provider autoconnects in a mixed worktree")
	equal(omp.session, s.harness.session, "OMP keeps its own session identifier")
	equal(
		{ "rediff-omp-live", "send", omp.connection, omp.session },
		feedback.command(s.harness),
		"OMP uses its own transport"
	)
	fails(function()
		feedback.command({ name = "omp" })
	end, "connect omp")
	panel.open = function(opts)
		picker = { opts = opts }
	end
	vim.cmd("Harness connect omp")
	respond({
		omp,
		vim.tbl_extend("force", omp, { session = "second", connection = "/fake/omp-two/connection.json" }),
		matches[1],
	})
	equal("omp", picker.opts.provider, "OMP chooser excludes Amp sessions")
	for _, entry in ipairs(connections.connected()) do
		if entry.provider == "omp" then
			harness.disconnect(entry)
		end
	end
	harness.select(root, amp_target)
	vim.system, panel.open = system, panel_open
	vim.api.nvim_set_current_tabpage(s.previous_tab)
	equal(
		true,
		review.statusline():find("harness: amp-live · T-second · idle", 1, true) ~= nil,
		"Editing also displays the binding"
	)
	local git = require("rediff.git")
	local get_root = git.root
	git.root = function()
		error("Status redraw must not run Git")
	end
	equal("harness: amp-live · T-second · idle", harness.statusline(), "Repeated redraw reuses workspace context")
	git.root = get_root
	vim.api.nvim_set_current_tabpage(s.tab)
	for _, entry in ipairs(connections.connected()) do
		harness.disconnect(entry)
	end

	local function annotate(text)
		return review.add_comment({
			file = s.current.path,
			side = "new",
			snapshot_id = s.current.id,
			selection = require("rediff.selection").line(s.new_buf, 5),
		}, text)
	end
	local settings = feedback.settings
	feedback.settings = function()
		return {
			feedback_command = { vim.v.progpath, "--headless", "-u", "NONE", "-l", tests .. "/receiver.lua", "wait" },
		}
	end
	harness.select(root, { name = "custom" })
	local comment = annotate("Review comment")
	vim.api.nvim_set_current_win(s.new_win)
	keys(":w<CR>")
	equal(true, feedback.busy(root), "Review feedback holds the in-flight lock")
	equal("harness: custom · running", harness.statusline(root), "Status shows in-flight feedback")
	fails(function()
		harness.select(root, { name = "none" })
	end, "Wait for the current")
	fails(review.submit, "Wait for the current")
	vim.fn.writefile({}, session.last.path .. ".release") -- Release the deliberately pending receiver.
	assert(vim.wait(10000, function()
		return not feedback.busy(root)
	end))
	equal("harness: custom · completed", harness.statusline(root), "Acknowledgment updates the persistent status bar")
	local path = session.last.path
	equal(s.last_submission, path, "Review delivery is the harness retry target")
	local review_payload = feedback.read(path)
	equal({ comment }, review_payload.comments, "Review :w sends the accumulated annotation batch")
	equal(nil, review_payload.message, "Review feedback carries no freeform message")
	equal({}, s.comments, "Successful review send clears individual annotations")
	harness.retry()
	equal(path, session.last.path, "Retry uses the original payload")
	equal({ "1" }, vim.fn.readfile(path .. ".calls"), "Retry cannot resend completed feedback")
	local retained = annotate("Unsent review comment")
	vim.cmd("Harness disconnect")
	fails(harness.retry, "Harness changed")
	equal(
		"harness: local · idle",
		harness.statusline(root),
		"Disconnect cannot attribute old delivery to a new target"
	)
	equal(retained, s.comments[1], "Disconnect retains review drafts")
	equal(
		"none",
		feedback.read(session.directory .. "/harness.json").target.name,
		"Binding is persisted independently of Review"
	)
	harness.select(root, { name = "custom" })
	notices = {}
	vim.notify = function(message)
		table.insert(notices, message)
	end
	local pending
	vim.system = function(argv, opts, callback)
		if argv[1] == "git" then
			return system(argv, opts, callback)
		end
		pending = callback
	end
	local function finish(status)
		assert(
			vim.wait(5000, function()
				return pending ~= nil
			end),
			"Feedback receiver did not start"
		)
		pending({
			code = status == "failed" and 1 or 0,
			stderr = status == "failed" and "Fixture failure" or "",
			stdout = vim.json.encode({ submission_id = session.last.id, status = status }),
		})
		pending = nil
		assert(vim.wait(1000, function()
			return not feedback.busy(root)
		end))
	end
	local delivery_ok, delivery_err = xpcall(function()
		vim.api.nvim_set_current_win(s.new_win)
		keys(":w<CR>")
		finish("failed")
		equal(true, notices[1]:find("Fixture failure", 1, true) ~= nil, "Delivery failure is reported")
		equal({ retained }, s.comments, "Failure preserves the annotation batch")
		harness.retry()
		finish("accepted")
		equal({}, s.comments, "Accepted retry clears the delivered annotations")
	end, debug.traceback)
	vim.system, vim.notify = system, notify
	feedback.settings = settings
	assert(delivery_ok, delivery_err)
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
	local function reopen()
		local child = vim.fn.jobstart(
			{ vim.fn.exepath("rediff"), "--embed", "--headless", "-i", "NONE" },
			{ rpc = true }
		)
		assert(child > 0)
		local ok, restored = pcall(
			vim.rpcrequest,
			child,
			"nvim_exec_lua",
			[[
			local root = ...
			vim.cmd.cd(root)
			require("rediff.feedback").deliver = function() error("Opening must not send") end
			local s = require("rediff.harness").get(root)
			return { last = s.last, delivery = s.delivery }
		]],
			{ restart_root }
		)
		vim.rpcnotify(child, "nvim_command", "q")
		if vim.fn.jobwait({ child }, 5000)[1] == -1 then
			vim.fn.jobstop(child)
			vim.fn.jobwait({ child }, 1000)
		end
		assert(ok, restored)
		return restored
	end
	local ok, err = xpcall(function()
		for _, status in ipairs({ "completed", "failed", "running" }) do
			local id, payload_path = feedback.enqueue(restart_root, { { text = "Annotation" } }, nil, {})
			feedback.write(directory .. "/" .. id .. ".status.json", { status = status })
			local last = { id = id, path = payload_path, argv = {} }
			feedback.write(directory .. "/harness.json", { target = { name = "none" }, last = last })
			local restored = reopen()
			equal(last, restored.last, "Fresh launch restores the retry target: " .. status)
			equal(
				status == "running" and "uncertain" or status,
				restored.delivery,
				"Fresh launch reports the saved delivery receipt: " .. status
			)
		end
	end, debug.traceback)
	vim.fn.delete(restart_root, "rf")
	vim.fn.delete(directory, "rf")
	assert(ok, err)
end
