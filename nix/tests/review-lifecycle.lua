local tests = vim.fn.fnamemodify(debug.getinfo(1, "S").source:sub(2), ":p:h")

return function(root, equal, fails)
	local review = require("rediff.review")
	local feedback = require("rediff.feedback")
	local selection = require("rediff.selection")
	local system, settings, notify = vim.system, feedback.settings, vim.notify
	local notice
	vim.notify = function(message)
		notice = message
	end
	local pending, calls = nil, 0
	feedback.settings = function()
		return { feedback_command = { "annotation-test-receiver" } }
	end
	vim.system = function(argv, opts, callback)
		if argv[1] ~= "annotation-test-receiver" then
			return system(argv, opts, callback)
		end
		calls = calls + 1
		pending = { callback = callback, payload = feedback.read(argv[#argv]) }
	end
	local function acknowledge(status)
		pending.callback({
			code = status == "failed" and 1 or 0,
			stderr = status == "failed" and "Test failure" or "",
			stdout = vim.json.encode({ submission_id = pending.payload.submission_id, status = status }),
		})
		assert(vim.wait(1000, function()
			return not feedback.busy(root)
		end))
	end
	local function add(text, id)
		local s = review.state
		return review.add_comment({
			file = s.current.path,
			side = "new",
			snapshot_id = s.current.id,
			selection = selection.line(s.new_buf, 5),
		}, text, id)
	end
	local ok, err = xpcall(function()
		review.select_harness("custom")
		local edited = add("Original text")
		add("Send once")
		local path = review.submit()
		equal(2, #review.state.comments, "Pending submission keeps its annotations")
		add("Edited during delivery", edited.id)
		local newer = add("Added during delivery")
		acknowledge("accepted")
		equal({ edited, newer }, review.state.comments, "Late ACK clears sent notes, preserving edits and additions")
		equal("Original text", feedback.read(path).comments[1].text, "Outbox preserves the actual sent text")
		vim.cmd("ReviewRetry")
		equal(1, calls, "Cached acknowledgment does not invoke the receiver again")
		equal({ edited, newer }, review.state.comments, "Cached ACK cannot clear changed notes")
		review.submit()
		acknowledge("completed")
		equal({}, review.state.comments, "Completed batch is deleted")
		equal(true, review.statusline():find("0 comments", 1, true) ~= nil, "Comment count reflects the cleared batch")
		fails(review.submit, "No feedback to submit")
		equal(2, calls, "Empty follow-up cannot resend the old batch")

		local failed = add("Retry this note")
		review.submit()
		acknowledge("failed")
		equal(true, notice:find("Test failure", 1, true) ~= nil, "Failed delivery is reported")
		equal({ failed }, review.state.comments, "Failed delivery retains notes")
		vim.cmd("ReviewRetry")
		acknowledge("accepted")
		equal({}, review.state.comments, "Successful explicit retry clears notes")

		-- Both orders matter: acknowledgment while closed and after reopening.
		for _, reopen_first in ipairs({ false, true }) do
			add("Sent before leaving Review")
			review.submit()
			local retained = add("New unsent note")
			review.leave()
			if reopen_first then
				review.open()
			end
			acknowledge("accepted")
			if not reopen_first then
				review.open()
			end
			equal({ retained }, review.state.comments, "ACK clears the cached batch across Review leave/reopen")
			review.archive()
		end
		review.select_harness("none")
		local local_note = add("Local queue only")
		review.submit()
		equal({ local_note }, review.state.comments, "Local-only queue is not a successful delivery")
		review.archive()
	end, debug.traceback)
	vim.system, feedback.settings, vim.notify = system, settings, notify
	assert(ok, err)

	-- Separate OS processes sharing the same state directory must not restore notes.
	local restart_root = dofile(tests .. "/fixture.lua").create()
	local directory = feedback.directory(restart_root)
	feedback.write(directory .. "/draft.json", {
		comments = { { text = "Legacy note from an earlier launch" } },
		snapshots = {},
		draft = { text = "Legacy unwritten annotation" },
		reviewed = { ["unstaged\0auth.lua"] = "legacy-mark" },
		harness = { name = "claude", session = "preserve-binding" },
	})
	local function launch(write)
		local child = vim.fn.jobstart(
			{ vim.fn.exepath("rediff"), "--embed", "--headless", "-i", "NONE" },
			{ rpc = true }
		)
		assert(child > 0)
		local success, result = pcall(
			vim.rpcrequest,
			child,
			"nvim_exec_lua",
			[[
			local root, write = ...
			vim.cmd.cd(root)
			local review = require("rediff.review")
			require("rediff.feedback").deliver = function() error("Opening must not send") end
			review.open()
			local s = review.state
			local result = {
				count = #s.comments,
				draft = s.draft ~= nil,
				marked = s.reviewed ~= nil,
				session = s.harness.session,
			}
			if write then
				vim.api.nvim_set_current_win(s.new_win)
				review.compose()
				vim.api.nvim_buf_set_lines(s.composer, 0, -1, false, { "Saved before quitting" })
				review.save_composer()
				assert(#s.comments == 1)
			end
			return result
		]],
			{ restart_root, write }
		)
		vim.rpcnotify(child, "nvim_command", "qa!")
		local exited = vim.fn.jobwait({ child }, 5000)[1]
		if exited == -1 then
			vim.fn.jobstop(child)
			vim.fn.jobwait({ child }, 1000)
		end
		assert(success, result)
		equal(0, exited, "Editor exits with session-local annotations")
		return result
	end
	ok, err = xpcall(function()
		equal(
			{ count = 0, draft = false, marked = false, session = "preserve-binding" },
			launch(true),
			"Fresh launch ignores legacy notes and composer, preserving the harness"
		)
		equal(nil, feedback.read(directory .. "/draft.json").comments, "Quit does not persist annotations")
		equal(nil, feedback.read(directory .. "/draft.json").reviewed, "Legacy reviewed marks are not retained")
		equal(
			{ count = 0, draft = false, marked = false, session = "preserve-binding" },
			launch(false),
			"Second fresh launch has no notes or reviewed marks; binding survives"
		)
	end, debug.traceback)
	vim.fn.delete(restart_root, "rf")
	vim.fn.delete(directory, "rf")
	assert(ok, err)
end
