local tests = vim.fn.fnamemodify(debug.getinfo(1, "S").source:sub(2), ":p:h")

return function(root, equal, fails, keys)
	local harness = require("myeditor.harness")
	local feedback = require("myeditor.feedback")
	local review = require("myeditor.review")
	local s, session = review.state, harness.get(root)
	local thread = "T-00000000-1111-2222-3333-444444444444"
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
	keys(":w<CR>")
	equal({ "1" }, vim.fn.readfile(path .. ".calls"), "Unchanged message write is deduplicated")
	keys(":wq<CR>")
	equal(false, vim.api.nvim_win_is_valid(win), "Write-quit closes only message window")
	equal(s, review.state, "Message write-quit preserves Review")
	keys(":hs<CR>gg0c$Keep this unsent.<Esc>:q!<CR>")
	equal("Keep this unsent.", session.message, "Quit-bang retains changed message")
	equal(path, session.last.path, "Quit-bang does not send")
	keys(":hs<CR>")
	equal(
		{ "Keep this unsent." },
		vim.api.nvim_buf_get_lines(session.buf, 0, -1, false),
		"Reopen restores unsent draft"
	)
	local layout = vim.fn.winlayout()
	keys(":help<CR>")
	equal("editor", vim.api.nvim_win_get_config(0).relative, "Message help is an overlay")
	keys("q")
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
	feedback.settings = settings
	review.archive()
end
