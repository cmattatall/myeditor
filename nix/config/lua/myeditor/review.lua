local M = { state = nil }
local api = vim.api
local git = require("myeditor.git")
local feedback = require("myeditor.feedback")
local harness = require("myeditor.harness")
local selection = require("myeditor.selection")
local tree_ns = api.nvim_create_namespace("myeditor.tree")
local active_ns = api.nvim_create_namespace("myeditor.active-file")
local hunk_ns = api.nvim_create_namespace("myeditor.hunk")
local reviews = {} -- Pending annotations belong to this editor process, not the next launch.

local function notify(message, level)
	vim.notify(message, level or vim.log.levels.INFO, { title = "myeditor" })
end

local function guard(fn)
	return function(...)
		local ok, err = pcall(fn, ...)
		if not ok then
			notify(tostring(err), vim.log.levels.ERROR)
		end
	end
end

local function state()
	return assert(M.state, "Enter Review with <leader>r first")
end

function M.active()
	return M.state and api.nvim_get_current_tabpage() == M.state.tab and M.state or nil
end

local function entry_key(entry)
	return entry.group .. "\0" .. entry.path
end

local function save()
	local s = state()
	feedback.write(s.directory .. "/draft.json", {
		harness = s.harness,
		reviewed = s.reviewed,
	})
end

local function map(buf, mode, key, fn, description)
	vim.keymap.set(mode, key, guard(fn), { buffer = buf, desc = description })
end

local function map_hunks(buf)
	for key, direction in pairs({ ["]"] = 1, ["["] = -1 }) do
		-- Updating a mapping in place retains its old precedence.
		pcall(vim.keymap.del, "n", key, { buffer = buf })
		vim.keymap.set(
			"n",
			key,
			guard(function()
				for _ = 1, vim.v.count1 do
					M.jump_hunk(direction)
				end
			end),
			{
				buffer = buf,
				nowait = true,
				desc = direction > 0 and "Next hunk across files" or "Previous hunk across files",
			}
		)
	end
end

local function reject_write()
	error("Review buffers do not write files. Use :w for feedback, or :ReviewOutbox for exports.")
end

local function owned_buffer(name, lines, editable)
	local buf = api.nvim_create_buf(false, true)
	api.nvim_buf_set_name(buf, name)
	vim.bo[buf].buftype = "acwrite"
	vim.bo[buf].bufhidden = "hide"
	vim.bo[buf].swapfile = false
	vim.bo[buf].undofile = false
	vim.bo[buf].readonly = false
	local undo_levels = vim.bo[buf].undolevels
	vim.bo[buf].undolevels = -1
	api.nvim_buf_set_lines(buf, 0, -1, false, lines)
	vim.bo[buf].undolevels = undo_levels
	vim.bo[buf].modified = false
	vim.bo[buf].modifiable = editable or false
	api.nvim_create_autocmd("BufWriteCmd", {
		buffer = buf,
		callback = function(event)
			if event.match ~= api.nvim_buf_get_name(buf) then
				reject_write()
			end
			if editable then
				M.save_composer()
				local win = state().composer_win
				-- Defer closing so :wq can finish without closing a source pane too.
				vim.schedule(function()
					if api.nvim_win_is_valid(win) and api.nvim_win_get_buf(win) == buf then
						api.nvim_win_close(win, true)
					end
				end)
			else
				M.submit()
			end
			if api.nvim_buf_is_valid(buf) then
				vim.bo[buf].modified = false
			end
		end,
	})
	api.nvim_create_autocmd({ "FileWriteCmd", "FileAppendCmd", "FilterWritePre" }, {
		buffer = buf,
		callback = reject_write,
	})
	return buf
end

local function set_lines(buf, lines)
	vim.bo[buf].modifiable = true
	api.nvim_buf_set_lines(buf, 0, -1, false, lines)
	vim.bo[buf].modified = false
	vim.bo[buf].modifiable = false
end

local function wrap_comment(text, width)
	local lines = {}
	for _, line in ipairs(vim.split(text, "\n", { plain = true })) do
		while vim.fn.strdisplaywidth(line) > width do
			local count = 1
			while vim.fn.strdisplaywidth(vim.fn.strcharpart(line, 0, count + 1)) <= width do
				count = count + 1
			end
			table.insert(lines, vim.fn.strcharpart(line, 0, count))
			line = vim.fn.strcharpart(line, count)
		end
		table.insert(lines, line)
	end
	return table.concat(lines, "\n")
end

local function render_comments()
	local s = state()
	if not s.current or not api.nvim_win_is_valid(s.old_win) or not api.nvim_win_is_valid(s.new_win) then
		return
	end
	local comments = {}
	for _, comment in ipairs(s.comments) do
		if comment.snapshot_id == s.current.id then
			local win = comment.side == "old" and s.old_win or s.new_win
			local width = api.nvim_win_get_width(win) - vim.fn.getwininfo(win)[1].textoff - 4
			local rendered = vim.deepcopy(comment)
			rendered.text = wrap_comment(comment.text, math.max(20, width))
			table.insert(comments, rendered)
		end
	end
	require("review.store").comments = { [s.current.path] = comments }
	local marks = require("review.marks")
	marks.render_for_buffer(s.old_buf, "old", s.current.path)
	marks.render_for_buffer(s.new_buf, "new", s.current.path)
	marks.align_buffers(s.old_buf, s.new_buf, s.current.path, s.current.path)
	vim.cmd.redrawstatus()
end

local function side_at_cursor()
	local s = state()
	local buf = api.nvim_get_current_buf()
	assert(buf == s.old_buf or buf == s.new_buf, "Focus a source pane first")
	return buf == s.old_buf and "old" or "new", buf
end

local function hunk_range(change, side, buf)
	local range = change[side == "old" and "original" or "modified"]
	local count = api.nvim_buf_line_count(buf)
	local first = math.max(1, math.min(range.start_line, count))
	return first, math.max(first, math.min(range.end_line - 1, count))
end

local function mark_hunk(index)
	local s = state()
	s.selected_hunk = index
	for _, pane in ipairs({ { s.old_buf, "old" }, { s.new_buf, "new" } }) do
		local buf, side = unpack(pane)
		api.nvim_buf_clear_namespace(buf, hunk_ns, 0, -1)
		local change = index and s.diff.changes[index]
		if change then
			local first, last = hunk_range(change, side, buf)
			for line = first, last do
				api.nvim_buf_set_extmark(buf, hunk_ns, line - 1, 0, {
					sign_text = line == first and "▶" or "┃",
					sign_hl_group = "ReviewHunk",
					priority = 5000, -- Keep the selected hunk visible above comment signs.
				})
			end
		end
	end
end

local function focus_hunk(index)
	local s = state()
	mark_hunk(index)
	for _, pane in ipairs({ { s.old_win, s.old_buf, "old" }, { s.new_win, s.new_buf, "new" } }) do
		local win, buf, side = unpack(pane)
		local first, last = hunk_range(s.diff.changes[index], side, buf)
		api.nvim_win_call(win, function()
			api.nvim_win_set_cursor(win, { first, 0 })
			local height = api.nvim_win_text_height(win, { start_row = first - 1, end_row = last - 1 }).all
			local margin = math.max(0, math.floor((api.nvim_win_get_height(win) - height) / 2))
			if height >= api.nvim_win_get_height(win) then
				margin = 2
			end
			vim.cmd("normal! zt")
			if margin > 0 then
				vim.cmd.normal({
					args = { margin .. api.nvim_replace_termcodes("<C-y>", true, false, true) },
					bang = true,
				})
			end
		end)
	end
end

function M.show(index)
	local s = state()
	local entry = assert(s.entries[index], "No changed file selected")
	local snapshot = git.snapshot(s.root, entry)
	s.index, s.current = index, snapshot
	s.snapshots[snapshot.id] = snapshot
	local old_lines, new_lines = git.lines(snapshot.old), git.lines(snapshot.new)
	set_lines(s.old_buf, old_lines)
	set_lines(s.new_buf, new_lines)
	-- Codediff's empty-file path requires one empty line, just like Neovim.
	-- Zero-element arrays silently produce no hunks for added/deleted files.
	old_lines = api.nvim_buf_get_lines(s.old_buf, 0, -1, false)
	new_lines = api.nvim_buf_get_lines(s.new_buf, 0, -1, false)
	local ft = vim.filetype.match({ filename = entry.path }) or ""
	vim.bo[s.old_buf].filetype = ft
	vim.bo[s.new_buf].filetype = ft
	-- <nowait> only wins when defined after competing buffer-local prefixes.
	-- Filetype plugins (e.g. Markdown's [[/]]) install these on every switch.
	map_hunks(s.old_buf)
	map_hunks(s.new_buf)
	vim.wo[s.old_win].winbar = " OLD · " .. (entry.group == "staged" and "HEAD" or "INDEX")
	vim.wo[s.new_win].winbar = " NEW · " .. (entry.group == "staged" and "INDEX" or "WORKTREE")
	s.diff = require("codediff.ui.view.render").compute_and_render(
		s.old_buf,
		s.new_buf,
		old_lines,
		new_lines,
		true,
		true,
		s.old_win,
		s.new_win,
		true
	)
	render_comments()
	api.nvim_set_current_win(s.new_win)
	mark_hunk(nil)
	if #s.diff.changes > 0 then
		focus_hunk(1)
	end
	api.nvim_buf_clear_namespace(s.tree_buf, active_ns, 0, -1)
	for row, item in pairs(s.rows) do
		if item == index then
			api.nvim_buf_set_extmark(s.tree_buf, active_ns, row - 1, 0, {
				line_hl_group = "ReviewActiveFile",
			})
			api.nvim_win_set_cursor(s.tree_win, { row, 0 })
			break
		end
	end
	save()
end

function M.refresh(preferred)
	local s = state()
	local entries, reviewed = git.entries(s.root), {}
	s.entries = {}
	for _, entry in ipairs(entries) do
		local key = entry_key(entry)
		if s.reviewed[key] then
			local ok, snapshot = pcall(git.snapshot, s.root, entry)
			if ok and git.review_fingerprint(snapshot) == s.reviewed[key] then
				reviewed[key] = s.reviewed[key]
			end
		end
		if not s.only_unreviewed or not reviewed[key] then
			table.insert(s.entries, entry)
		end
	end
	s.reviewed = reviewed
	local lines, rows, headings =
		{ s.only_unreviewed and " REVIEW · Unreviewed only" or " REVIEW · Git changes", "" }, {}, {}
	for _, group in ipairs({ "staged", "unstaged", "untracked" }) do
		local count = 0
		for _, entry in ipairs(s.entries) do
			if entry.group == group then
				count = count + 1
			end
		end
		table.insert(lines, string.format(" %s (%d)", group:upper(), count))
		headings[#lines] = group == "staged" and "ReviewStaged" or "ReviewUnstaged"
		for i, entry in ipairs(s.entries) do
			if entry.group == group then
				table.insert(
					lines,
					(s.reviewed[entry_key(entry)] and " ✓ " or " ○ ") .. vim.fn.strtrans(entry.path)
				)
				rows[#lines] = i
			end
		end
		if count == 0 then
			table.insert(lines, "   (none)")
		end
		table.insert(lines, "")
	end
	vim.list_extend(lines, {
		" ─────────────────────────",
		" Enter    open file",
		" ] / [    next/prev hunk",
		" Tab      tree/diff focus",
		" Spc f    fuzzy files",
		" Spc /    fuzzy text",
		" Spc m    mark reviewed",
		" Spc u    unreviewed only",
		" Spc s    stage hunk (diff)",
		" Spc S    (un)stage file",
		" Spc R    refresh",
		" :w       send feedback",
		" ?/:help  keybindings",
		" Spc q    return to editor",
	})
	s.rows = rows
	set_lines(s.tree_buf, lines)
	api.nvim_buf_clear_namespace(s.tree_buf, tree_ns, 0, -1)
	api.nvim_buf_clear_namespace(s.tree_buf, active_ns, 0, -1)
	for row, highlight in pairs(headings) do
		api.nvim_buf_set_extmark(s.tree_buf, tree_ns, row - 1, 0, { line_hl_group = highlight })
	end
	if #s.entries > 0 then
		local index = math.min(s.index or 1, #s.entries)
		if preferred then
			for i, entry in ipairs(s.entries) do
				if entry_key(entry) == entry_key(preferred) then
					index = i
					break
				end
			end
		end
		local ok, err = pcall(M.show, index)
		if not ok then
			s.current = nil
			s.diff = nil
			mark_hunk(nil)
			set_lines(s.old_buf, {})
			set_lines(s.new_buf, { tostring(err) })
			notify(tostring(err), vim.log.levels.WARN)
		end
	else
		s.current = nil
		s.diff = nil
		mark_hunk(nil)
		set_lines(s.old_buf, {})
		set_lines(s.new_buf, {})
	end
	save()
end

function M.toggle_reviewed()
	local s = state()
	local win = api.nvim_get_current_win()
	local index = s.index
	if win == s.tree_win then
		index = s.rows[api.nvim_win_get_cursor(win)[1]]
	end
	local entry = assert(s.entries[index], "Select a changed file")
	local key = entry_key(entry)
	if s.reviewed[key] then
		s.reviewed[key] = nil
	else
		local snapshot = index == s.index and s.current or git.snapshot(s.root, entry)
		git.validate(s.root, snapshot)
		s.reviewed[key] = git.review_fingerprint(snapshot)
	end
	local current = s.current and s.current.id
	local old_view = api.nvim_win_call(s.old_win, vim.fn.winsaveview)
	local new_view = api.nvim_win_call(s.new_win, vim.fn.winsaveview)
	s.index = index
	M.refresh(entry)
	if s.current and s.current.id == current then
		api.nvim_win_call(s.old_win, function()
			vim.fn.winrestview(old_view)
		end)
		api.nvim_win_call(s.new_win, function()
			vim.fn.winrestview(new_view)
		end)
	end
	api.nvim_set_current_win(win)
end

function M.toggle_unreviewed()
	local s = state()
	local win = api.nvim_get_current_win()
	s.only_unreviewed = not s.only_unreviewed
	M.refresh(s.entries[s.index or 1])
	api.nvim_set_current_win(win)
end

function M.jump_hunk(direction)
	local s = state()
	local win = api.nvim_get_current_win()
	local side = win == s.old_win and "old" or "new"
	local buf = side == "old" and s.old_buf or s.new_buf
	local line = api.nvim_win_get_cursor(side == "old" and s.old_win or s.new_win)[1]
	local changes = s.diff and s.diff.changes or {}
	local first, last, step = 1, #changes, 1
	if direction < 0 then
		first, last, step = #changes, 1, -1
	end
	for i = first, last, step do
		local target = hunk_range(changes[i], side, buf)
		if (direction > 0 and target > line) or (direction < 0 and target < line) then
			focus_hunk(i)
			return
		end
	end
	-- Boundaries cross files/groups but never wrap back to already-reviewed work.
	local previous, selected = s.index, s.selected_hunk
	local old_view = api.nvim_win_call(s.old_win, vim.fn.winsaveview)
	local new_view = api.nvim_win_call(s.new_win, vim.fn.winsaveview)
	for i = (s.index or 0) + direction, direction > 0 and #s.entries or 1, direction do
		local ok, err = pcall(M.show, i)
		if not ok then
			notify("Skipping " .. s.entries[i].path .. ": " .. tostring(err), vim.log.levels.WARN)
		elseif #s.diff.changes > 0 then
			focus_hunk(direction > 0 and 1 or #s.diff.changes)
			api.nvim_set_current_win(win)
			return
		end
	end
	if s.index ~= previous and previous then
		M.show(previous)
		api.nvim_win_call(s.old_win, function()
			vim.fn.winrestview(old_view)
		end)
		api.nvim_win_call(s.new_win, function()
			vim.fn.winrestview(new_view)
		end)
		mark_hunk(selected)
		api.nvim_set_current_win(win)
	end
	notify(direction > 0 and "Last hunk" or "First hunk")
end

function M.stage(whole_file, unstage)
	local s = assert(M.active(), "Enter Review first")
	local win = api.nvim_get_current_win()
	local snapshot, side, line = s.current, "new", 1
	if win == s.tree_win then
		assert(whole_file, "Focus a source pane to stage/unstage a hunk; Space S acts on the selected file")
		local index = s.rows[api.nvim_win_get_cursor(win)[1]]
		local entry = assert(s.entries[index], "Select a changed file")
		snapshot = index == s.index and s.current or git.snapshot(s.root, entry)
	else
		side, line = side_at_cursor(), api.nvim_win_get_cursor(win)[1]
	end
	assert(snapshot, "No changed file selected")
	if unstage ~= nil then
		assert(
			(snapshot.group == "staged") == unstage,
			unstage and "Select a STAGED entry" or "Select an UNSTAGED or UNTRACKED entry"
		)
	end
	git.stage(s.root, snapshot, side, line, whole_file)
	M.refresh(snapshot)
	api.nvim_set_current_win(win)
	notify("Index updated; source files unchanged. Existing comments retain their original snapshots.")
end

function M.add_comment(anchor, text, existing_id)
	local s = state()
	assert(vim.trim(text) ~= "", "Comment cannot be empty")
	if existing_id then
		for _, comment in ipairs(s.comments) do
			if comment.id == existing_id then
				comment.text = text
				save()
				render_comments()
				return comment
			end
		end
		error("Comment no longer exists")
	end
	local first, last = anchor.selection.spans[1], anchor.selection.spans[#anchor.selection.spans]
	local comment = {
		id = vim.fn.sha256(tostring(vim.uv.hrtime()) .. text),
		file = anchor.file,
		line = first.line,
		line_end = last.line,
		side = anchor.side,
		type = "note",
		text = text,
		snapshot_id = anchor.snapshot_id,
		selection = anchor.selection,
	}
	table.insert(s.comments, comment)
	save()
	render_comments()
	return comment
end

function M.save_composer()
	local s = state()
	if not s.draft then
		return
	end
	local loaded = s.composer and api.nvim_buf_is_loaded(s.composer)
	local text = loaded and table.concat(api.nvim_buf_get_lines(s.composer, 0, -1, false), "\n") or s.draft.text
	local comment
	if vim.trim(text) ~= "" then
		comment = M.add_comment(s.draft.anchor, text, s.draft.comment_id)
		s.draft.comment_id = comment.id
	end
	s.draft.text, s.draft.saved = text, true
	save()
	if loaded then
		vim.bo[s.composer].modified = false
	end
	return comment
end

local function release_composer()
	local s = state()
	local buf = s.composer
	s.draft = nil
	s.composer, s.composer_win = nil, nil
	save()
	api.nvim_buf_delete(buf, { force = true })
end

function M.compose(visual, keys)
	local s = state()
	if s.composer and api.nvim_win_is_valid(s.composer_win) then
		api.nvim_set_current_win(s.composer_win)
		if keys then
			api.nvim_feedkeys(api.nvim_replace_termcodes(keys, true, false, true), "ni", false)
		else
			api.nvim_feedkeys("i", "ni", false)
		end
		return
	end
	if s.composer then
		release_composer()
	end
	local side, buf = side_at_cursor()
	assert(s.current, "No diff to annotate")
	local line = api.nvim_win_get_cursor(0)[1]
	local anchor = { file = s.current.path, side = side, snapshot_id = s.current.id }
	if visual then
		anchor.selection = selection.capture(buf, vim.fn.mode(), vim.fn.getpos("v"), vim.fn.getpos("."))
		vim.cmd.normal({ args = { api.nvim_replace_termcodes("<Esc>", true, false, true) }, bang = true })
	else
		anchor.selection = selection.line(buf, line)
	end
	local draft = { anchor = anchor, text = "" }
	s.draft = draft
	local composer = owned_buffer("review://" .. s.id .. "/comment", vim.split(draft.text, "\n"), true)
	s.composer = composer
	vim.bo[composer].filetype = "markdown"
	vim.cmd("belowright 6new")
	s.composer_win = api.nvim_get_current_win()
	api.nvim_win_set_buf(s.composer_win, composer)
	vim.wo[s.composer_win].number = false
	vim.wo[s.composer_win].signcolumn = "no"
	vim.wo[s.composer_win].wrap = true
	vim.wo[s.composer_win].winbar = " Annotation · :w save+close · :q discard+close %<"
		.. vim.fn.strtrans(draft.anchor.file):gsub("%%", "%%%%")
		.. ":"
		.. draft.anchor.selection.spans[1].line
		.. " ("
		.. draft.anchor.side
		.. ")"
	api.nvim_create_autocmd({ "TextChanged", "TextChangedI", "BufLeave" }, {
		buffer = composer,
		callback = function()
			if M.state == s and s.draft and s.composer then
				s.draft.text = table.concat(api.nvim_buf_get_lines(composer, 0, -1, false), "\n")
				s.draft.saved = not vim.bo[composer].modified
				save()
			end
		end,
	})
	save()
	if keys then
		api.nvim_feedkeys(api.nvim_replace_termcodes(keys, true, false, true), "ni", false)
	else
		api.nvim_feedkeys("i", "ni", false)
	end
end

function M.clear_sent(root, path)
	local s = reviews[root]
	if not s then
		return
	end
	local payload = feedback.read(path)
	if not payload or not payload.comments then
		return
	end
	local sent = {}
	for _, comment in ipairs(payload.comments) do
		if comment.id then
			sent[comment.id] = comment
		end
	end
	s.comments = vim.tbl_filter(function(comment)
		if not vim.deep_equal(comment, sent[comment.id]) then
			return true
		end
		if s.draft and s.draft.comment_id == comment.id then
			s.draft.comment_id = nil
		end
		return false
	end, s.comments)
	if M.state == s then
		render_comments()
	end
end

function M.submit(retry)
	local s = state()
	assert(not feedback.busy(s.root), "Wait for the current feedback delivery before submitting again")
	assert(#s.comments > 0, "No feedback to submit")
	local snapshots, snapshot_status, warnings = {}, {}, {}
	for _, comment in ipairs(s.comments) do
		local snapshot = assert(s.snapshots[comment.snapshot_id], "Missing comment snapshot")
		if not snapshots[snapshot.id] then
			local ok, current = pcall(git.snapshot, s.root, snapshot)
			local status = not ok and "unverified" or (current.id == snapshot.id and "current" or "changed")
			snapshot_status[snapshot.id] = status
			snapshots[snapshot.id] = snapshot -- Keep the reviewed text and coordinates, never silently re-anchor.
			if status ~= "current" then
				table.insert(warnings, vim.fn.strtrans(snapshot.path) .. " (" .. status .. ")")
			end
		end
	end
	local argv = feedback.command(s.harness)
	local id, path = feedback.enqueue(s.root, s.comments, snapshots, argv, nil, snapshot_status)
	if #warnings > 0 then
		notify(
			"Sending original review context; agent must check current files: " .. table.concat(warnings, ", "),
			vim.log.levels.WARN
		)
	end
	s.last_submission = path
	s.delivery = #argv == 0 and "queued" or "running"
	local ok, err = pcall(harness.deliver, s.root, id, path, argv, retry, function(status)
		s.delivery = status.status
	end)
	if not ok then
		s.delivery = "failed"
		error(err)
	end
	return path
end

function M.select_harness(name, session)
	local s = state()
	if not name then
		notify("Harness: " .. s.harness.name .. (s.harness.session and (" · " .. s.harness.session) or ""))
		return
	end
	assert(s.delivery ~= "running", "Wait for the current feedback delivery before switching harnesses")
	assert(not session or name == "amp" or name == "claude", "Only built-in harnesses take a session ID")
	local target = { name = name, session = session }
	harness.select(s.root, target)
	s.delivery = "draft"
	save()
end

function M.archive()
	local s = state()
	assert(not s.composer, "Close the comment composer first")
	feedback.write(s.directory .. "/archive-" .. tostring(vim.uv.hrtime()) .. ".json", {
		comments = s.comments,
		snapshots = s.snapshots,
		draft = s.draft,
	})
	s.comments, s.snapshots, s.draft = {}, {}, nil
	if s.current then
		s.snapshots[s.current.id] = s.current
	end
	save()
	render_comments()
	notify("Draft round archived locally; nothing sent to the agent")
end

local function remove_comment(comment)
	local s = state()
	for i, item in ipairs(s.comments) do
		if item.id == comment.id then
			table.remove(s.comments, i)
			if s.draft and s.draft.comment_id == comment.id then
				s.draft.comment_id = nil
			end
			save()
			render_comments()
			return
		end
	end
end

function M.delete_comment()
	local s = state()
	local side, line = side_at_cursor(), api.nvim_win_get_cursor(0)[1]
	local matches = {}
	for _, comment in ipairs(s.comments) do
		if
			s.current
			and comment.snapshot_id == s.current.id
			and comment.side == side
			and comment.line <= line
			and comment.line_end >= line
		then
			table.insert(matches, comment)
		end
	end
	if #matches == 0 then
		notify("No annotation on this line")
	elseif #matches == 1 then
		remove_comment(matches[1])
	else
		vim.ui.select(matches, {
			prompt = "Delete annotation:",
			format_item = function(comment)
				return comment.text:gsub("\n", " ")
			end,
		}, function(comment)
			if M.state == s and comment then
				remove_comment(comment)
			end
		end)
	end
end

function M.list_comments()
	local s = state()
	vim.ui.select(s.comments, {
		prompt = "Comments (select to remove a draft; submitted payloads are retained):",
		format_item = function(comment)
			return string.format(
				"%s:%d [%s/%s] %s",
				comment.file,
				comment.line,
				comment.side,
				comment.selection.kind,
				comment.text:gsub("\n", " ")
			)
		end,
	}, function(comment)
		if M.state ~= s or not comment then
			return
		end
		if vim.fn.confirm("Remove this draft comment?", "&Remove\n&Cancel", 2) == 1 then
			remove_comment(comment)
		end
	end)
end

function M.leave()
	local s = state()
	if s.composer then
		release_composer()
	end
	save()
	M.state = nil
	vim.uv.fs_unlink(s.directory .. "/editor.lock")
	if api.nvim_tabpage_is_valid(s.tab) then
		api.nvim_set_current_tabpage(s.tab)
		vim.cmd("tabclose!")
	end
	for _, buf in ipairs({ s.old_buf, s.new_buf, s.tree_buf, s.composer }) do
		if api.nvim_buf_is_valid(buf) then
			api.nvim_buf_delete(buf, { force = true })
		end
	end
	if api.nvim_tabpage_is_valid(s.previous_tab) then
		api.nvim_set_current_tabpage(s.previous_tab)
	end
	api.nvim_exec_autocmds("User", { pattern = "ReviewLeave" })
end

function M.open()
	if M.state then
		api.nvim_set_current_tabpage(M.state.tab)
		return
	end
	local root = git.root()
	git.entries(root) -- Validate before changing windows.
	local target = harness.get(root).target
	local directory = feedback.directory(root)
	local lock = directory .. "/editor.lock"
	if vim.fn.filereadable(lock) == 1 then
		local pid = tonumber(vim.fn.readfile(lock)[1])
		assert(pid and not vim.uv.kill(pid, 0), "Another editor owns this review: " .. lock)
		assert(vim.uv.fs_unlink(lock))
	end
	local fd = assert(vim.uv.fs_open(lock, "wx", 384), "Review already open in another editor")
	vim.uv.fs_write(fd, tostring(vim.uv.os_getpid()), 0)
	vim.uv.fs_close(fd)
	local ok, draft = pcall(feedback.read, directory .. "/draft.json")
	if not ok then
		vim.uv.fs_unlink(lock)
		error("Could not read saved feedback; file left intact: " .. tostring(draft))
	end
	draft = draft or {}
	local pending = reviews[root] or {}
	local s = {
		root = root,
		directory = directory,
		comments = pending.comments or {},
		snapshots = pending.snapshots or {},
		harness = target,
		reviewed = draft.reviewed or {},
		previous_tab = api.nvim_get_current_tabpage(),
		id = string.format("%.0f", vim.uv.hrtime()),
		delivery = "draft",
	}
	M.state = s
	reviews[root] = s
	vim.cmd.tabnew()
	s.tab = api.nvim_get_current_tabpage()
	vim.cmd("tcd " .. vim.fn.fnameescape(root))
	s.tree_win = api.nvim_get_current_win()
	s.tree_buf = owned_buffer("review://" .. s.id .. "/files", {})
	api.nvim_win_set_buf(s.tree_win, s.tree_buf)
	vim.cmd.vsplit()
	s.old_win = api.nvim_get_current_win()
	s.old_buf = owned_buffer("review://" .. s.id .. "/old", {})
	api.nvim_win_set_buf(s.old_win, s.old_buf)
	vim.cmd.vsplit()
	s.new_win = api.nvim_get_current_win()
	s.new_buf = owned_buffer("review://" .. s.id .. "/new", {})
	api.nvim_win_set_buf(s.new_win, s.new_buf)
	vim.wo[s.tree_win].number = false
	vim.wo[s.tree_win].signcolumn = "no"
	vim.wo[s.tree_win].winfixwidth = true
	api.nvim_win_set_width(s.tree_win, math.min(28, math.floor(vim.o.columns / 5)))
	vim.cmd("wincmd =")
	map(s.tree_buf, "n", "<CR>", function()
		M.show(assert(s.rows[api.nvim_win_get_cursor(0)[1]], "Select a changed file"))
	end, "Open changed file")
	map(s.tree_buf, "n", "q", M.leave, "Leave Review")
	for _, buf in ipairs({ s.old_buf, s.new_buf }) do
		for _, key in ipairs({
			"i",
			"a",
			"o",
			"O",
			"I",
			"A",
			"c",
			"C",
			"s",
			"S",
			"r",
			"R",
			"D",
			"x",
			"X",
			"p",
			"P",
			"u",
			"<C-r>",
			".",
			"~",
			">",
			"<",
			"=",
			"J",
			"gJ",
			"gq",
			"gw",
			"gu",
			"gU",
			"g~",
			"gp",
			"gP",
		}) do
			map(buf, "n", key, function()
				local prefix = '"' .. vim.v.register .. (vim.v.count > 0 and tostring(vim.v.count) or "")
				M.compose(false, prefix .. key)
			end, "Comment instead of editing code")
		end
		map(buf, "n", "d", M.delete_comment, "Delete annotation on this line")
		map(buf, "x", "i", function()
			M.compose(true)
		end, "Comment on exact selection")
		map(buf, "x", "<leader>c", function()
			M.compose(true)
		end, "Comment on exact selection")
	end
	for _, buf in ipairs({ s.tree_buf, s.old_buf, s.new_buf }) do
		map_hunks(buf)
		map(buf, "n", "<leader>s", function()
			M.stage(false)
		end, "Stage/unstage Git hunk (source pane)")
		map(buf, "n", "<leader>S", function()
			M.stage(true)
		end, "Stage/unstage selected file")
		map(buf, "n", "<leader>R", M.refresh, "Refresh snapshot")
		map(buf, "n", "<leader>q", M.leave, "Leave Review")
		map(buf, "n", "<leader>c", M.list_comments, "List/remove draft comments")
		map(buf, "n", "<leader>m", M.toggle_reviewed, "Toggle file reviewed")
		map(buf, "n", "<leader>u", M.toggle_unreviewed, "Toggle unreviewed-only filter")
		map(buf, "n", "<Tab>", function()
			api.nvim_set_current_win(api.nvim_get_current_win() == s.tree_win and s.new_win or s.tree_win)
		end, "Focus tree/diff")
		map(buf, "n", "<leader>j", function()
			if #s.entries > 0 then
				M.show((s.index or 0) % #s.entries + 1)
			end
		end, "Next file")
		map(buf, "n", "<leader>k", function()
			if #s.entries > 0 then
				M.show(((s.index or 1) - 2) % #s.entries + 1)
			end
		end, "Previous file")
	end
	M.refresh()
	api.nvim_exec_autocmds("User", { pattern = "ReviewEnter" })
end

function M.statusline()
	local s = M.state
	if s and api.nvim_get_current_tabpage() == s.tab then
		local file = s.current and vim.fn.strtrans(s.current.path):gsub("%%", "%%%%") or "No changes"
		return " REVIEW · "
			.. harness.statusline(s.root)
			.. " · "
			.. #s.comments
			.. " comments   %<"
			.. file
			.. "%=%l:%c "
	end
	return " EDIT · " .. vim.fn.mode():upper() .. " · " .. harness.statusline() .. " %<%f %m%=%l:%c %p%%"
end

function M.setup()
	api.nvim_set_hl(0, "ReviewStaged", { fg = "#a9dc76", bg = "#26352b", bold = true })
	api.nvim_set_hl(0, "ReviewUnstaged", { fg = "#ffd580", bg = "#3b3324", bold = true })
	api.nvim_set_hl(0, "ReviewActiveFile", { bg = "#333d4d", bold = true })
	api.nvim_set_hl(0, "ReviewHunk", { fg = "#ffd580", bold = true })
	api.nvim_create_autocmd("CursorMoved", {
		callback = function()
			local s = M.state
			if not s or not s.diff then
				return
			end
			local buf = api.nvim_get_current_buf()
			if buf ~= s.old_buf and buf ~= s.new_buf then
				return
			end
			local side = buf == s.old_buf and "old" or "new"
			local line = api.nvim_win_get_cursor(0)[1]
			for i, change in ipairs(s.diff.changes) do
				local first, last = hunk_range(change, side, buf)
				if line >= first and line <= last then
					mark_hunk(i)
					return
				end
			end
			mark_hunk(nil)
		end,
	})
	api.nvim_create_user_command(
		"ReviewHarness",
		guard(function(opts)
			assert(#opts.fargs <= 2, "Usage: ReviewHarness [amp THREAD_ID | claude SESSION_ID | custom | none]")
			M.select_harness(opts.fargs[1], opts.fargs[2])
		end),
		{
			nargs = "*",
			complete = function()
				return { "amp", "claude", "custom", "none" }
			end,
		}
	)
	for _, name in ipairs({ "ReviewStage", "ReviewUnstage" }) do
		api.nvim_create_user_command(
			name,
			guard(function(opts)
				assert(opts.args == "hunk" or opts.args == "file", "Usage: " .. name .. " hunk|file")
				M.stage(opts.args == "file", name == "ReviewUnstage")
			end),
			{
				nargs = 1,
				complete = function()
					return { "hunk", "file" }
				end,
			}
		)
	end
	local commands = {
		Review = M.open,
		ReviewLeave = M.leave,
		ReviewRefresh = M.refresh,
		ReviewComments = M.list_comments,
		ReviewMark = M.toggle_reviewed,
		ReviewUnreviewed = M.toggle_unreviewed,
		WriteFeedback = M.submit,
		ReviewRetry = harness.retry,
		ReviewArchive = function()
			if vim.fn.confirm("Archive this round and start empty?", "&Archive\n&Cancel", 2) == 1 then
				M.archive()
			end
		end,
		ReviewOutbox = function()
			local root = M.state and M.state.root or git.root()
			vim.cmd("tabnew " .. vim.fn.fnameescape(feedback.directory(root)))
		end,
	}
	for name, fn in pairs(commands) do
		api.nvim_create_user_command(
			name,
			guard(function()
				fn()
			end),
			{}
		)
	end
	api.nvim_create_autocmd("QuitPre", {
		callback = function()
			if M.state and M.state.composer and api.nvim_buf_is_loaded(M.state.composer) then
				-- Closing discards edits; only explicit writes add/update comments.
				vim.bo[M.state.composer].modified = false
			end
		end,
	})
	api.nvim_create_autocmd("VimLeavePre", {
		callback = function()
			if M.state then
				M.state.draft = nil
				save()
				vim.uv.fs_unlink(M.state.directory .. "/editor.lock")
			end
		end,
	})
	api.nvim_create_autocmd("TabClosed", {
		callback = function()
			if M.state and not api.nvim_tabpage_is_valid(M.state.tab) then
				M.leave()
			end
		end,
	})
	api.nvim_create_autocmd("WinClosed", {
		callback = function()
			vim.schedule(function()
				local s = M.state
				if not s then
					return
				end
				if s.composer and not api.nvim_win_is_valid(s.composer_win) then
					release_composer()
				end
				if
					not api.nvim_win_is_valid(s.old_win)
					or not api.nvim_win_is_valid(s.new_win)
					or not api.nvim_win_is_valid(s.tree_win)
				then
					M.leave()
				end
			end)
		end,
	})
	api.nvim_create_autocmd("VimResized", {
		callback = function()
			if M.state then
				render_comments()
			end
		end,
	})
end

return M
