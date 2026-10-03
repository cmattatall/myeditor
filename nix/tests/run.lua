local tests = vim.fn.fnamemodify(debug.getinfo(1, "S").source:sub(2), ":p:h")
local git = require("myeditor.git")
local review = require("myeditor.review")
local feedback = require("myeditor.feedback")
local selection = require("myeditor.selection")
local root, baseline, changed, run = dofile(tests .. "/fixture.lua").create()
local assertions = 0

local function equal(expected, actual, label)
	assertions = assertions + 1
	assert(
		vim.deep_equal(expected, actual),
		label .. "\nexpected: " .. vim.inspect(expected) .. "\nactual: " .. vim.inspect(actual)
	)
end

local function fails(fn, message)
	local ok, err = pcall(fn)
	assertions = assertions + 1
	assert(
		not ok and tostring(err):find(message, 1, true),
		"Expected failure containing " .. message .. ": " .. tostring(err)
	)
end

local function anchor(side, line)
	local s = review.state
	return {
		file = s.current.path,
		side = side,
		snapshot_id = s.current.id,
		selection = selection.line(side == "old" and s.old_buf or s.new_buf, line),
	}
end

local function keys(input)
	vim.api.nvim_feedkeys(vim.api.nvim_replace_termcodes(input, true, false, true), "xt", false)
	vim.wait(10) -- Run scheduled window-close cleanup as the interactive loop does.
end

local function test()
	vim.cmd("cd " .. vim.fn.fnameescape(root))
	vim.cmd("edit auth.lua")
	local original_buf = vim.api.nvim_get_current_buf()
	local original_tab = vim.api.nvim_get_current_tabpage()
	local index_before = run({ "write-tree" })
	local layout = vim.fn.winlayout()
	vim.cmd.help()
	equal("myeditor.txt", vim.fn.fnamemodify(vim.api.nvim_buf_get_name(0), ":t"), "Bare help opens editor guide")
	equal("editor", vim.api.nvim_win_get_config(0).relative, "Help opens as an overlay")
	equal(layout, vim.fn.winlayout(), "Help leaves underlying splits unchanged")
	vim.cmd("help motion")
	equal(1, vim.api.nvim_buf_get_name(0):find(vim.env.VIMRUNTIME, 1, true), "Native help topics remain available")
	vim.cmd("help myeditor-harness")
	equal("myeditor.txt", vim.fn.fnamemodify(vim.api.nvim_buf_get_name(0), ":t"), "Editor help tags resolve")
	keys("q")
	equal(original_buf, vim.api.nvim_get_current_buf(), "q dismisses help to the original buffer")
	review.open()
	local s = review.state
	equal(1, vim.fn.executable("myeditor-harness"), "Nix launcher includes its receiver on PATH")
	equal("auth.lua", s.current.path, "First changed file")
	equal(false, vim.bo[s.new_buf].modifiable, "Review code is protected")
	equal("acwrite", vim.bo[s.new_buf].buftype, "Review write is virtual")
	equal(true, vim.bo[original_buf].modifiable, "Ordinary file remains editable")
	equal("", vim.bo[original_buf].buftype, "Ordinary file retains file semantics")
	equal(baseline, vim.api.nvim_buf_get_lines(s.old_buf, 0, -1, false), "Old pane is index")
	equal(changed, vim.api.nvim_buf_get_lines(s.new_buf, 0, -1, false), "New pane is disk snapshot")
	local function sidebar()
		return vim.api.nvim_buf_get_lines(s.tree_buf, 0, -1, false)
	end
	keys(" e")
	equal(s.tree_win, vim.api.nvim_get_current_win(), "Space e focuses Review explorer")
	vim.api.nvim_win_set_cursor(s.tree_win, { 1, 0 })
	fails(review.toggle_reviewed, "Select a changed file")
	keys("<Tab>")
	equal(s.new_win, vim.api.nvim_get_current_win(), "Tab returns to diff")
	keys(" m")
	equal(true, vim.list_contains(sidebar(), " ✓ auth.lua"), "Reviewed file has a Unicode check")
	keys(" u")
	equal(2, #s.entries, "Unreviewed filter hides reviewed file")
	local fzf = require("fzf-lua")
	local exec, items, opts = fzf.fzf_exec
	fzf.fzf_exec = function(values, options)
		items, opts = values, options
	end
	local navigation = require("myeditor.navigation")
	navigation.files()
	equal(2, #items, "Changed-file picker respects unreviewed filter")
	equal("removed.lua", s.current.path, "Filter advances past the hidden reviewed entry")
	keys(" u")
	equal("removed.lua", s.current.path, "Removing filter preserves file identity as its index shifts")
	review.show(1)
	keys(" m")
	equal(true, vim.tbl_isempty(s.reviewed), "Mark command toggles back to unreviewed")
	equal(true, vim.list_contains(sidebar(), " ○ auth.lua"), "Unmarking restores the unreviewed circle")
	navigation.files()
	local matched = vim.system({ "fzf", "--filter", "plnmd" }, { stdin = table.concat(items, "\n"), text = true })
		:wait()
	equal(0, matched.code, "File picker supports non-contiguous fuzzy matching")
	opts.actions.enter({ vim.trim(matched.stdout) })
	equal("plan.md", s.current.path, "File choice loads Review snapshot instead of editing disk buffer")
	equal(false, vim.bo[s.new_buf].modifiable, "Picker preserves source protection")
	navigation.text()
	local found
	for _, item in ipairs(items) do
		if item:find("removed.lua:2 (old)", 1, true) then
			found = item
		end
	end
	assert(found, "Text search includes deleted old-side lines")
	opts.actions.enter({ found })
	equal("removed.lua", s.current.path, "Text selection crosses files")
	equal(s.old_win, vim.api.nvim_get_current_win(), "Text selection focuses the correct side")
	equal(2, vim.api.nvim_win_get_cursor(0)[1], "Text selection uses exact source line")
	review.show(1)
	navigation.text()
	local stale_choice = opts.actions.enter
	local stale_line
	for _, item in ipairs(items) do
		if item:find("auth.lua:5 (new)", 1, true) then
			stale_line = item
		end
	end
	local notify, warning = vim.notify
	vim.notify = function(message)
		warning = message
	end
	vim.fn.writefile({ "changed while picker open" }, root .. "/auth.lua")
	stale_choice({ assert(stale_line) })
	equal(true, warning:find("Reviewed content changed", 1, true) ~= nil, "Stale search results rejected")
	equal(changed, vim.api.nvim_buf_get_lines(s.new_buf, 0, -1, false), "Stale picker leaves displayed snapshot intact")
	vim.fn.writefile(changed, root .. "/auth.lua")
	vim.notify, fzf.fzf_exec = notify, exec
	equal(true, vim.list_contains(sidebar(), " STAGED (0)"), "Empty staged section stays visible")
	equal(true, vim.list_contains(sidebar(), " UNSTAGED (2)"), "Unstaged count excludes untracked")
	equal(true, vim.list_contains(sidebar(), " UNTRACKED (1)"), "Untracked section has its own count")
	local headers = vim.api.nvim_buf_get_extmarks(
		s.tree_buf,
		vim.api.nvim_get_namespaces()["myeditor.tree"],
		0,
		-1,
		{ details = true }
	)
	equal("ReviewStaged", headers[1][4].line_hl_group, "Staged header has its own full-row color")
	equal("ReviewUnstaged", headers[2][4].line_hl_group, "Unstaged header has a distinct full-row color")
	equal(5, vim.api.nvim_win_get_cursor(s.new_win)[1], "Opening a file targets its first hunk")
	vim.api.nvim_set_current_win(s.tree_win)
	equal(1, vim.fn.maparg("]", "n", false, true).nowait, "Bare bracket does not wait for longer mappings")
	keys("]")
	equal(17, vim.api.nvim_win_get_cursor(s.new_win)[1], "Sidebar next hunk targets the second change")
	equal(s.tree_win, vim.api.nvim_get_current_win(), "Hunk navigation retains sidebar focus")
	local markers = vim.api.nvim_buf_get_extmarks(
		s.new_buf,
		vim.api.nvim_get_namespaces()["myeditor.hunk"],
		0,
		-1,
		{ details = true }
	)
	equal(16, markers[1][2], "Selected hunk marker follows cursor even with sidebar focused")
	equal("▶ ", markers[1][4].sign_text, "Selected hunk has a visible gutter arrow")
	keys("]")
	equal("removed.lua", s.current.path, "Next hunk crosses file boundary")
	equal(1, vim.api.nvim_win_get_cursor(s.new_win)[1], "Deleted file clamps the empty side to line one")
	equal(1, vim.api.nvim_win_get_cursor(s.old_win)[1], "Deleted file targets removed source lines")
	keys("]")
	equal("plan.md", s.current.path, "Next hunk crosses into untracked group")
	keys("]")
	equal("plan.md", s.current.path, "Final hunk does not wrap")
	keys("3[")
	equal("auth.lua", s.current.path, "Counted backwards navigation crosses files")
	equal(5, vim.api.nvim_win_get_cursor(s.new_win)[1], "Counted navigation returns to first hunk")
	keys("[")
	equal(5, vim.api.nvim_win_get_cursor(s.new_win)[1], "First hunk does not wrap")
	vim.api.nvim_set_current_win(s.old_win)
	keys("]")
	equal(17, vim.api.nvim_win_get_cursor(s.old_win)[1], "Old side navigates original coordinates")
	equal(s.old_win, vim.api.nvim_get_current_win(), "Old side retains focus")
	keys("[")
	vim.api.nvim_set_current_win(s.new_win)
	local comment = review.add_comment(anchor("new", 5), "Reject missing tokens; this grants access.")
	vim.cmd.write()
	local path = s.last_submission
	local payload = feedback.read(path)
	equal(comment.text, payload.comments[1].text, "Feedback serialized")
	equal("current", payload.snapshot_status[s.current.id], "Submission records current context")
	equal({ "    return true" }, payload.comments[1].selection.text, "Exact source context")
	equal(changed, vim.fn.readfile(root .. "/auth.lua"), "Feedback write leaves source unchanged")
	equal(index_before, run({ "write-tree" }), "Feedback write leaves index unchanged")
	equal(path, review.submit(), "Repeated write reuses batch")
	vim.cmd("write!")
	equal(path, s.last_submission, "Bang still submits feedback")
	local escape = root .. "/must-not-exist"
	fails(function()
		vim.cmd("write " .. escape)
	end, "Review buffers do not write files")
	fails(function()
		vim.cmd("1write " .. escape)
	end, "Review buffers do not write files")
	fails(function()
		vim.cmd("write >> " .. escape)
	end, "Review buffers do not write files")
	equal(0, vim.fn.filereadable(escape), "Alternate writes do not create files")
	fails(function()
		vim.api.nvim_buf_set_lines(s.new_buf, 0, 1, false, { "bad" })
	end, "modifiable")

	-- Actually exercise i -> composer -> :w; do not just call the serializer.
	vim.api.nvim_win_set_cursor(s.new_win, { 7, 0 })
	keys("iCheck the expiry boundary too.<Esc>")
	assert(s.composer, "i opens a comment composer")
	for _, mode in ipairs({ "n", "i" }) do
		equal(false, vim.fn.maparg("<C-s>", mode, false, true).buffer == 1, "No custom Ctrl-s mapping in " .. mode)
	end
	local composer = s.composer
	vim.cmd.write()
	equal(2, #s.comments, "Writing composer adds and submits its comment")
	equal(composer, vim.api.nvim_get_current_buf(), "Writing feedback keeps the annotation open")
	equal(
		"Check the expiry boundary too.",
		feedback.read(s.last_submission).comments[2].text,
		"Write includes the current composer text in its payload"
	)
	equal(changed, vim.fn.readfile(root .. "/auth.lua"), "Composer never modifies source")
	equal(index_before, run({ "write-tree" }), "Writing composer never stages source")
	local written = s.last_submission
	layout = vim.fn.winlayout()
	vim.cmd.help()
	equal("help", vim.bo.buftype, "Help opens from annotation")
	equal("editor", vim.api.nvim_win_get_config(0).relative, "Review help is also an overlay")
	equal(layout, vim.fn.winlayout(), "Review layout is unchanged behind help")
	equal(written, s.last_submission, "Opening help does not submit feedback")
	keys("<Esc>")
	equal(composer, vim.api.nvim_get_current_buf(), "Esc returns to the annotation")
	keys(":wq<CR>")
	equal(nil, s.composer, "Native wq closes annotation")
	equal(s.new_win, vim.api.nvim_get_current_win(), "Native wq returns to source pane")
	equal(3, #vim.api.nvim_tabpage_list_wins(0), "wq does not close a source pane")
	equal(written, s.last_submission, "Unchanged wq does not duplicate the batch")

	-- Source edit commands continue as native operators inside the annotation.
	keys("cwVerify<Esc>")
	equal(
		{ "Verify the expiry boundary too." },
		vim.api.nvim_buf_get_lines(s.composer, 0, -1, false),
		"cw consumes its motion instead of inserting w"
	)
	keys("oRemove this line<Esc>dd")
	equal(
		{ "Verify the expiry boundary too." },
		vim.api.nvim_buf_get_lines(s.composer, 0, -1, false),
		"o and dd edit only annotation lines"
	)
	keys("u")
	equal(2, vim.api.nvim_buf_line_count(s.composer), "Native undo restores deleted annotation line")
	keys("<C-r>")
	equal(1, vim.api.nvim_buf_line_count(s.composer), "Native redo deletes it again")
	keys("qaA!<Esc>q@a")
	equal(
		{ "Verify the expiry boundary too.!!" },
		vim.api.nvim_buf_get_lines(s.composer, 0, -1, false),
		"q records macros rather than closing annotation"
	)
	keys(":wq<CR>SReplacement note<Esc>:wq<CR>RNew<Esc>")
	equal(
		{ "Newlacement note" },
		vim.api.nvim_buf_get_lines(s.composer, 0, -1, false),
		"S and R substitute/replace annotation instead of staging/refreshing"
	)
	keys(":wq<CR>")
	vim.fn.setreg("b", "Start ", "v")
	keys('"bP')
	equal(
		{ "Start Newlacement note" },
		vim.api.nvim_buf_get_lines(s.composer, 0, -1, false),
		"Source paste preserves named register"
	)
	keys(":wq<CR>2dw")
	equal({ "note" }, vim.api.nvim_buf_get_lines(s.composer, 0, -1, false), "Source operator preserves count")
	keys(":wq<CR>")
	equal(2, #s.comments, "Editing a saved annotation updates it in place")
	equal(changed, vim.fn.readfile(root .. "/auth.lua"), "All native annotation edits leave source untouched")
	equal(index_before, run({ "write-tree" }), "Native s/S bindings never stage source")

	review.add_comment(anchor("new", 5), "Reconcile this feedback with the latest agent edits.", s.comments[1].id)
	local mutated = vim.deepcopy(changed)
	mutated[5] = "    return 'external edit'"
	vim.fn.writefile(mutated, root .. "/auth.lua")
	vim.cmd.write()
	local stale_path = s.last_submission
	local stale_payload = feedback.read(stale_path)
	equal("changed", stale_payload.snapshot_status[s.current.id], "Stale feedback is allowed and explicitly labeled")
	equal(s.current, stale_payload.snapshots[s.current.id], "Stale submission retains the original reviewed snapshot")
	equal(s.comments, stale_payload.comments, "Stale feedback keeps exact comment coordinates and selected text")
	equal(mutated, vim.fn.readfile(root .. "/auth.lua"), "Submitting stale feedback leaves agent edits untouched")
	fails(review.toggle_reviewed, "Reviewed content changed")
	fails(function()
		git.stage(root, s.current, "new", 5, false)
	end, "Reviewed content changed")
	equal(index_before, run({ "write-tree" }), "Stale staging leaves index unchanged")
	equal(changed, vim.api.nvim_buf_get_lines(s.new_buf, 0, -1, false), "External edits do not mutate snapshot")
	vim.fn.writefile(changed, root .. "/auth.lua")
	equal(stale_path, review.submit(), "Changed freshness alone cannot create a duplicate submission")
	equal(stale_payload, feedback.read(stale_path), "Existing payload remains immutable when source changes again")
	review.add_comment(anchor("new", 5), "Another unsent note.", s.comments[1].id)
	vim.cmd("ReviewRetry")
	equal(
		stale_path,
		require("myeditor.harness").get(root).last.path,
		"ReviewRetry sends the saved payload, not edited drafts"
	)
	local binary = assert(vim.uv.fs_open(root .. "/auth.lua", "w", 384))
	assert(vim.uv.fs_write(binary, "binary\0content", 0))
	assert(vim.uv.fs_close(binary))
	local unverified_payload = feedback.read(review.submit())
	equal(
		"unverified",
		unverified_payload.snapshot_status[s.current.id],
		"Unreadable current context does not discard feedback"
	)
	equal(
		s.current,
		unverified_payload.snapshots[s.current.id],
		"Unverified feedback still contains only reviewed source"
	)
	vim.fn.writefile(changed, root .. "/auth.lua")
	review.toggle_reviewed()
	review.leave()
	equal(original_tab, vim.api.nvim_get_current_tabpage(), "Return to original tab")
	review.open()
	equal(2, #review.state.comments, "Draft comments survive reopening")
	equal(true, review.state.reviewed["unstaged\0auth.lua"] ~= nil, "Reviewed identity persists across reopening")
	review.toggle_reviewed()
	review.archive()
	s = review.state
	vim.api.nvim_win_set_cursor(s.new_win, { 16, 0 })
	keys("VjiPreserve the refresh contract.<Esc>")
	equal(
		{ "Preserve the refresh contract." },
		vim.api.nvim_buf_get_lines(s.composer, 0, -1, false),
		"Visual entry types into the annotation"
	)
	equal("line", s.draft.anchor.selection.kind, "Visual mapping captures the native selection")
	equal(
		{ "function M.refresh(token)", "  return nil" },
		s.draft.anchor.selection.text,
		"Visual mapping scopes two exact lines"
	)
	keys(":q!<CR>")
	equal(nil, s.composer, "Native q! hides the annotation while retaining draft")
	keys("i<Esc>")
	equal(
		{ "Preserve the refresh contract." },
		vim.api.nvim_buf_get_lines(s.composer, 0, -1, false),
		"Reopened composer retains text"
	)
	keys(":wq<CR>")
	equal(17, s.comments[1].line_end, "Reopened composer retains range")
	review.archive()

	local thread = "T-00000000-1111-2222-3333-444444444444"
	local amp = { "myeditor-harness", "amp", thread }
	vim.cmd("ReviewHarness amp " .. thread)
	equal(amp, feedback.command(s.harness), "Command binds Amp to an explicit thread")
	review.leave()
	review.open()
	s = review.state
	equal(amp, feedback.command(s.harness), "Harness binding persists per repository")
	fails(function()
		review.select_harness("amp", "--last")
	end, "session ID")
	equal(amp, feedback.command(s.harness), "Invalid binding leaves previous target intact")
	fails(function()
		feedback.command(nil, { harness = "amp" })
	end, "Select a session")
	equal(
		{ "myeditor-harness", "claude", "other-session" },
		feedback.command({ name = "claude", session = "other-session" }),
		"Claude uses its own adapter"
	)
	equal(
		{ "/custom/receiver", "arg with spaces" },
		feedback.command({ name = "custom" }, { feedback_command = { "/custom/receiver", "arg with spaces" } }),
		"Custom argv retains argument boundaries"
	)
	local amp_id = feedback.enqueue(root, { { text = "same feedback", side = "old" } }, {}, amp)
	equal(
		amp_id,
		feedback.enqueue(root, { { side = "old", text = "same feedback" } }, {}, amp),
		"Canonical IDs ignore object key insertion order"
	)
	equal(
		false,
		amp_id
			== feedback.enqueue(
				root,
				{ { text = "same feedback", side = "old" } },
				{},
				{ "myeditor-harness", "amp", "another-thread" }
			),
		"Changing target cannot reuse an earlier receipt"
	)
	review.select_harness("none")
	equal({}, feedback.command(s.harness), "Local outbox can be selected without an agent")
	dofile(tests .. "/harness.lua")(root, equal, fails, keys)

	-- A partially staged file must show distinct HEAD/index/worktree states.
	local snapshot = review.state.current
	git.stage(root, snapshot, "new", 5, false)
	local staged = git.snapshot(root, { path = "auth.lua", group = "staged" })
	local expected_index = vim.deepcopy(baseline)
	expected_index[5] = changed[5]
	equal(expected_index, git.lines(staged.new), "Only first hunk staged")
	equal(baseline, git.lines(staged.old), "Staged old side is HEAD")
	equal(changed, vim.fn.readfile(root .. "/auth.lua"), "Staging leaves worktree unchanged")
	review.refresh()
	equal(true, vim.list_contains(sidebar(), " STAGED (1)"), "Staging updates visible count")
	equal("staged", s.current.group, "Staged entries appear first")
	equal(expected_index, vim.api.nvim_buf_get_lines(s.new_buf, 0, -1, false), "Staged pane shows index not worktree")
	vim.api.nvim_set_current_win(s.tree_win)
	keys("]")
	equal("unstaged", s.current.group, "Hunk jump distinguishes same path in two groups")
	equal("auth.lua", s.current.path, "Partially staged file is independently reviewable")
	equal(17, vim.api.nvim_win_get_cursor(s.new_win)[1], "Unstaged view targets the remaining hunk")
	keys("[")
	equal("staged", s.current.group, "Previous hunk crosses back into staged group")
	equal(5, vim.api.nvim_win_get_cursor(s.new_win)[1], "Staged hunk has its own coordinates")
	review.toggle_reviewed()
	equal(nil, s.reviewed["unstaged\0auth.lua"], "Staged reviewed mark does not mark worktree comparison")
	review.toggle_reviewed()
	git.stage(root, staged, "new", 5, false)
	equal(index_before, run({ "write-tree" }), "Unstage restores index")
	local deleted = git.snapshot(root, { path = "removed.lua", group = "unstaged" })
	equal("local obsolete = true\nreturn obsolete\n", deleted.old, "Deleted lines remain reviewable")
	equal("", deleted.new, "Deleted new side is empty")
	fails(function()
		git.stage(root, deleted, "old", 1, false)
	end, "Added/deleted files")
	git.stage(root, deleted, "old", 1, true)
	git.stage(root, git.snapshot(root, { path = "removed.lua", group = "staged" }), "old", 1, true)
	equal(index_before, run({ "write-tree" }), "Whole-file deletion can be unstaged")

	-- Different side coordinates must not be treated as identical line numbers.
	local shifted = vim.deepcopy(changed)
	table.insert(shifted, 2, "-- New header")
	vim.fn.writefile(shifted, root .. "/auth.lua")
	review.refresh()
	review.show(1)
	vim.api.nvim_set_current_win(s.old_win)
	keys("]")
	equal(5, vim.api.nvim_win_get_cursor(s.old_win)[1], "Old-side hunk keeps original line after insertion")
	equal(6, vim.api.nvim_win_get_cursor(s.new_win)[1], "New-side hunk accounts for inserted line")
	keys("]")
	equal(17, vim.api.nvim_win_get_cursor(s.old_win)[1], "Later original hunk is not shifted")
	equal(18, vim.api.nvim_win_get_cursor(s.new_win)[1], "Later modified hunk is shifted")
	keys("[")
	equal(6, vim.api.nvim_win_get_cursor(s.new_win)[1], "Reverse navigation synchronizes both sides")

	vim.fn.writefile({}, root .. "/zzz-empty.txt")
	review.refresh()
	review.show(#s.entries - 1)
	equal("plan.md", s.current.path, "Last text hunk precedes empty file")
	vim.api.nvim_win_set_cursor(s.new_win, { 2, 0 })
	keys("]")
	equal("plan.md", s.current.path, "Hunkless files do not trap navigation")
	equal(2, vim.api.nvim_win_get_cursor(s.new_win)[1], "End boundary restores cursor rather than first hunk")
	review.toggle_reviewed()
	review.toggle_unreviewed()
	vim.fn.writefile({ "Agent changed this plan" }, root .. "/plan.md")
	review.refresh()
	equal(nil, s.reviewed["untracked\0plan.md"], "Agent edit invalidates reviewed mark")
	equal(true, vim.list_contains(sidebar(), " ○ plan.md"), "Changed reviewed file reappears under unreviewed filter")
	review.toggle_unreviewed()
	for index = 1, #s.entries do
		review.show(index)
		review.toggle_reviewed()
	end
	review.toggle_unreviewed()
	equal(0, #s.entries, "All-reviewed filter handles empty results")
	equal(nil, s.current, "All-reviewed filter clears source selection")
	review.toggle_unreviewed()
	equal(4, #s.entries, "Disabling filter restores all files")

	review.leave()
	equal(0, vim.fn.maparg("]", "n", false, true).buffer or 0, "Review bracket mapping does not leak into editing")
	-- Exact selections: reversed, multibyte, blockwise tabs and exclusive endpoints.
	vim.cmd.enew()
	local buf = vim.api.nvim_get_current_buf()
	vim.bo[buf].tabstop = 4
	vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "aéXYZ", "123456", "\tABC", "abcdef" })
	vim.o.selection = "inclusive"
	local region = selection.capture(buf, "v", { buf, 1, 4, 0 }, { buf, 1, 2, 0 })
	equal({ "éX" }, region.text, "Reversed Unicode selection uses byte positions")
	region = selection.capture(buf, "V", { buf, 2, 1, 0 }, { buf, 1, 1, 0 })
	equal({ "aéXYZ", "123456" }, region.text, "Reversed line selection")
	vim.o.virtualedit = "block"
	vim.cmd.normal({ args = { vim.api.nvim_replace_termcodes("<C-v>", true, false, true) }, bang = true })
	region = selection.capture(buf, "\22", { buf, 4, 4, 0 }, { buf, 3, 1, 1 })
	equal("block", region.kind, "Block kind retained")
	equal({ "   ", "bcd" }, region.text, "Partial-tab block retains exact visible text")
	equal(1, region.spans[1].start_offset, "Partial-tab start offset retained")
	equal(4, region.spans[1].end_offset, "Partial-tab end offset retained")
	vim.cmd.normal({ args = { vim.api.nvim_replace_termcodes("<Esc>", true, false, true) }, bang = true })
	vim.o.selection = "exclusive"
	region = selection.capture(buf, "v", { buf, 2, 2, 0 }, { buf, 2, 5, 0 })
	equal({ "234" }, region.text, "Exclusive endpoint is excluded")

	-- Real receiver process: ACK, failed delivery, wrong ACK and explicit retry.
	local id, submission = feedback.enqueue(root, { { text = "transport test" } }, {})
	local function deliver(mode, retry)
		local status
		feedback.deliver(
			root,
			id,
			submission,
			{ vim.v.progpath, "--headless", "-u", "NONE", "-l", tests .. "/receiver.lua", mode },
			retry,
			function(value)
				status = value
			end
		)
		assert(
			vim.wait(10000, function()
				return status ~= nil
			end),
			"Receiver timed out"
		)
		return status
	end
	equal("failed", deliver("fail").status, "Nonzero receiver exit retains failed state")
	equal(true, vim.fn.filereadable(submission) == 1, "Failed delivery preserves payload")
	fails(function()
		deliver("ok")
	end, "Check the agent")
	equal("failed", deliver("wrong-id", true).status, "Mismatched acknowledgment rejected")
	equal("completed", deliver("ok", true).status, "Explicit retry receives acknowledgment")
	equal("completed", deliver("ok").status, "Completed batch is not resent")
	equal({ "3" }, vim.fn.readfile(submission .. ".calls"), "Exactly three receiver executions")
	equal(id, feedback.enqueue(root, { { text = "transport test" } }, {}), "Dedupe persists across reads")
	local fingerprint = { path = "demo.lua", group = "unstaged", old = "old\n", new = "new\n", patch = "" }
	local identity = git.review_fingerprint(fingerprint)
	fingerprint.old, fingerprint.new = "prefix\nold\n", "prefix\nnew\n"
	equal(identity, git.review_fingerprint(fingerprint), "Unchanged diff survives shifted hunk/context")
	fingerprint.new = "prefix\nother\n"
	equal(false, identity == git.review_fingerprint(fingerprint), "Changed addition invalidates semantic identity")
	fingerprint.old, fingerprint.new = "old\n", "new"
	equal(false, identity == git.review_fingerprint(fingerprint), "Final newline change invalidates reviewed identity")
	dofile(tests .. "/input.lua")(equal)
	print(string.format("PASS: %d assertions", assertions))
end

local ok, err = xpcall(test, debug.traceback)
if review.state then
	pcall(review.leave)
end
vim.fn.delete(root, "rf")
if not ok then
	io.stderr:write(err .. "\n")
	vim.cmd("cquit 1")
end
vim.cmd("qa!")
