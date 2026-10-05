local tests = vim.fn.fnamemodify(debug.getinfo(1, "S").source:sub(2), ":p:h")

return function(equal)
	local a = require("rediff.awareness")
	local feedback = require("rediff.feedback")
	local root, _, changed, git = dofile(tests .. "/fixture.lua").create()
	local file = "unstaged\0auth.lua"
	local s = a.get(root)
	local function capture()
		local done, result
		a.capture(root, function(value)
			result, done = value, true
		end)
		assert(vim.wait(5000, function()
			return done
		end))
		assert(result, "Capture failed")
		return result
	end
	vim.fn.writefile({ "unchanged" }, root .. "/stable.txt")
	git({ "add", "stable.txt" })
	git({ "commit", "-m", "Stable file for index refresh test" })
	assert(vim.uv.fs_utime(root .. "/stable.txt", os.time() + 5, os.time() + 5))
	local index_before = vim.fn.readfile(root .. "/.git/index", "b")
	local baseline = capture()
	equal(
		index_before,
		vim.fn.readfile(root .. "/.git/index", "b"),
		"Background capture never rewrites index stat data"
	)
	a.update(s, baseline)
	equal(nil, a.file_state(s, file), "Opening Review establishes a quiet baseline")
	local newer = vim.deepcopy(changed)
	newer[5] = "    return token ~= nil"
	vim.fn.writefile(newer, root .. "/auth.lua")
	local current = capture()
	a.update(s, current)
	equal("unseen", a.file_state(s, file), "Editing one hunk makes the file unseen")
	equal("unseen", a.hunk_state(s, file, current[file].hunks[1].id), "Edited hunk is unseen")
	equal(nil, a.hunk_state(s, file, current[file].hunks[2].id), "Unchanged second hunk stays neutral")
	s.seen[file .. "\0" .. current[file].hunks[1].id] = true
	equal("seen", a.file_state(s, file), "File is seen after its changed hunk is seen")

	-- Positions/context are not identity; repeated identical hunks still count.
	local moved = a.describe("@@ -41 +61 @@\n-old\n+new\n")
	local first = a.describe("@@ -1 +1 @@\n-old\n+new\n")
	equal(first.fingerprint, moved.fingerprint, "Context shifts do not create unread hunks")
	local twice = a.describe("@@ -1 +1 @@\n-old\n+new\n@@ -40 +40 @@\n-old\n+new\n")
	equal(false, first.fingerprint == twice.fingerprint, "Duplicate hunks retain multiplicity")
	a.update(s, baseline)
	a.update(s, current)
	equal("unseen", a.file_state(s, file), "Changed content returning after a revert is unseen again")
	vim.fn.writefile({ "a new file" }, root .. "/space\tand\nnewline.txt")
	local added = capture()
	a.update(s, added)
	equal("unseen", a.file_state(s, "untracked\0space\tand\nnewline.txt"), "NUL paths preserve tabs/newlines")
	git({ "add", "auth.lua" }) -- Disposable fixture only.
	local staged = capture()
	equal(true, staged["staged\0auth.lua"] ~= nil, "Staged changes have their own identity")
	equal(nil, staged[file], "Fully staged file is absent from unstaged capture")
	newer[17] = "  return false"
	vim.fn.writefile(newer, root .. "/auth.lua")
	local partial = capture()
	equal(true, partial[file] ~= nil and partial["staged\0auth.lua"] ~= nil, "Partial staging captures both groups")

	-- Actual feedback pipeline: capture before launching, never at the late ACK.
	local system, pending = vim.system, nil
	vim.system = function(argv, opts, callback)
		if argv[1] == "awareness-test-receiver" then
			pending = callback
			return
		end
		return system(argv, opts, callback)
	end
	local function send(status, recipient)
		local id, path = feedback.enqueue(root, {}, {}, {}, tostring(vim.uv.hrtime()), nil, recipient)
		local before = s.baseline
		feedback.deliver(root, id, path, { "awareness-test-receiver" }, false, function() end)
		assert(vim.wait(5000, function()
			return pending ~= nil
		end))
		equal(true, before == s.baseline, "Pending sends leave the previous baseline intact")
		newer[17] = "  return 'edited during delivery'"
		vim.fn.writefile(newer, root .. "/auth.lua")
		pending({
			code = status == "failed" and 1 or 0,
			stdout = vim.json.encode({ submission_id = id, status = status }),
			stderr = "",
		})
		pending = nil
		assert(vim.wait(5000, function()
			return not feedback.busy(root) and not s.scanning
		end))
		return before, id, path
	end
	local ok, err = xpcall(function()
		local before = send("failed")
		equal(true, before == s.baseline, "Failed delivery does not consume unseen changes")
		before = send("accepted", { repository = root .. "-other" })
		equal(true, before == s.baseline, "External-worktree messages do not reset local awareness")
		vim.fn.writefile(changed, root .. "/auth.lua")
		local pre_send = capture()
		local _, id, path = send("accepted", { repository = root })
		equal(pre_send[file].fingerprint, s.baseline[file].fingerprint, "ACK adopts pre-send content")
		equal("unseen", a.file_state(s, file), "Edits made before ACK remain unseen")
		local accepted = s.baseline
		feedback.deliver(root, id, path, { "awareness-test-receiver" }, true, function() end)
		equal(true, accepted == s.baseline, "Cached receipt cannot reset a newer baseline")
		equal(nil, pending, "Cached receipt never relaunches receiver")
		local queued_id, queued_path = feedback.enqueue(root, {}, {}, {}, "local-only")
		feedback.deliver(root, queued_id, queued_path, {}, false, function() end)
		equal(true, accepted == s.baseline, "Local-only queue leaves baseline intact")
	end, debug.traceback)
	vim.system = system
	vim.fn.delete(root, "rf")
	assert(ok, err)
end
