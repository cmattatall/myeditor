local M = { state = nil }
local api = vim.api
local git = require("rediff.git")
local feedback = require("rediff.feedback")
local harness = require("rediff.harness")
local selection = require("rediff.selection")
local tree_ns = api.nvim_create_namespace("rediff.tree")
local active_ns = api.nvim_create_namespace("rediff.active-file")
local hunk_ns = api.nvim_create_namespace("rediff.hunk")
local annotation_ns = api.nvim_create_namespace("rediff.annotations")
local reviews = {} -- Pending annotations belong to this editor process, not the next launch.
local saved_guicursor

local function tree_focus(focused)
	local s = M.state
	if s and s.tree_win and api.nvim_win_is_valid(s.tree_win) then
		vim.wo[s.tree_win].cursorline = focused
	end
	if focused then
		saved_guicursor = saved_guicursor or vim.o.guicursor
		vim.o.guicursor = (saved_guicursor ~= "" and saved_guicursor .. "," or "") .. "n:ver1-ReviewTreeCursor-blinkon0"
	elseif saved_guicursor then
		vim.o.guicursor = saved_guicursor
		saved_guicursor = nil
	end
end

local function notify(message, level)
	vim.notify(message, level or vim.log.levels.INFO, { title = "rediff" })
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
	})
end

local function map(buf, mode, key, fn, description)
	vim.keymap.set(mode, key, guard(fn), { buffer = buf, desc = description })
end

local function map_staging(buf)
	if buf ~= state().tree_buf then
		map(buf, "n", "s", function()
			M.stage(false)
		end, "Stage/unstage Git hunk at cursor")
	end
	map(buf, "n", "S", function()
		M.stage(true)
	end, "Stage/unstage entire file")
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
				desc = direction > 0 and "Next hunk in Git group" or "Previous hunk in Git group",
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
		line = vim.fn.strtrans(line)
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
	return lines
end

local function opposite_line(line, side, changes)
	local offset = 0
	for _, change in ipairs(changes) do
		local source = change[side == "old" and "original" or "modified"]
		local target = change[side == "old" and "modified" or "original"]
		if line < source.start_line then
			break
		end
		if line < source.end_line then
			return target.start_line + math.min(line - source.start_line, target.end_line - target.start_line - 1)
		end
		offset = target.end_line - source.end_line
	end
	return line + offset
end

local function render_comments()
	local s = state()
	for _, buf in ipairs({ s.old_buf, s.new_buf }) do
		api.nvim_buf_clear_namespace(buf, annotation_ns, 0, -1)
	end
	if not s.current or not api.nvim_win_is_valid(s.new_win) then
		return
	end
	local function place(buf, line, lines, priority)
		api.nvim_buf_set_extmark(buf, annotation_ns, math.max(0, math.min(line, api.nvim_buf_line_count(buf)) - 1), 0, {
			virt_lines = lines,
			virt_lines_above = line < 1,
			priority = priority,
		})
	end
	for i, comment in ipairs(s.comments) do
		if comment.snapshot_id == s.current.id and (comment.side ~= "old" or s.old_win) then
			local old = comment.side == "old"
			local win = old and s.old_win or s.new_win
			local width = math.max(1, api.nvim_win_get_width(win) - vim.fn.getwininfo(win)[1].textoff - 2)
			local lines, padding = {}, {}
			for row, text in ipairs(wrap_comment(comment.text, width)) do
				lines[row] = { { (row == 1 and "● " or "  ") .. text, "ReviewAnnotation" } }
				padding[row] = {}
			end
			place(old and s.old_buf or s.new_buf, comment.line_end, lines, 250 + i)
			if s.old_win then
				place(
					old and s.new_buf or s.old_buf,
					opposite_line(comment.line_end, comment.side, s.diff.changes),
					padding,
					250 + i
				)
			end
		end
	end
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
	local panes = { { s.new_win, s.new_buf, "new" } }
	if s.old_win then
		table.insert(panes, 1, { s.old_win, s.old_buf, "old" })
	end
	for _, pane in ipairs(panes) do
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

function M.show(index, snapshot)
	local s = state()
	snapshot = snapshot or git.snapshot(s.root, assert(s.entries[index], "No changed file selected"))
	s.index, s.current = index, snapshot
	s.annotation_id = nil
	s.exhausted_group = nil
	s.snapshots[snapshot.id] = snapshot
	local old_lines, new_lines = git.lines(snapshot.old), git.lines(snapshot.new)
	set_lines(s.old_buf, old_lines)
	set_lines(s.new_buf, new_lines)
	-- Codediff's empty-file path requires one empty line, just like Neovim.
	-- Zero-element arrays silently produce no hunks for added/deleted files.
	old_lines = api.nvim_buf_get_lines(s.old_buf, 0, -1, false)
	new_lines = api.nvim_buf_get_lines(s.new_buf, 0, -1, false)
	local ft = vim.filetype.match({ filename = snapshot.path }) or ""
	vim.bo[s.old_buf].filetype = ft
	vim.bo[s.new_buf].filetype = ft
	-- <nowait> only wins when defined after competing buffer-local prefixes.
	-- Filetype plugins (e.g. Markdown's [[/]]) install these on every switch.
	map_hunks(s.old_buf)
	map_hunks(s.new_buf)
	map_staging(s.old_buf)
	map_staging(s.new_buf)
	local renderer = require("codediff.ui.view.render")
	if s.layout == "merged" then
		vim.wo[s.new_win].winbar = " MERGED · red: old / green: new · :view split for old-line selections"
		vim.wo[s.new_win].scrollbind = false
		vim.wo[s.new_win].wrap = false
		s.diff = assert(require("codediff.core.diff").compute_diff(old_lines, new_lines, renderer.diff_options()))
		-- Deleted virtual lines use the diff's readable foreground, not syntax colors.
		require("codediff.ui.inline").render_inline_diff(s.new_buf, s.diff, old_lines, new_lines, { filetype = "" })
	else
		vim.wo[s.old_win].winbar = " OLD · " .. (snapshot.group == "staged" and "HEAD" or "INDEX")
		vim.wo[s.new_win].winbar = " NEW · " .. (snapshot.group == "staged" and "INDEX" or "WORKTREE")
		s.diff = renderer.compute_and_render(
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
	end
	require("rediff.difftastic").highlight(s, snapshot)
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

function M.view(layout)
	local s = assert(M.active(), "Enter Review first")
	layout = layout or (s.layout == "merged" and "split" or "merged")
	assert(layout == "split" or layout == "merged", "Usage: view [split|merged]")
	if layout == s.layout then
		return
	end
	local selected, annotation_id = s.selected_hunk, s.annotation_id
	local source_line = api.nvim_win_get_cursor(s.new_win)
	local focus = api.nvim_get_current_win()
	s.layout = layout
	if layout == "merged" then
		local old_win = s.old_win
		s.old_win = nil
		api.nvim_win_close(old_win, true)
	else
		api.nvim_set_current_win(s.new_win)
		vim.cmd("leftabove vsplit")
		s.old_win = api.nvim_get_current_win()
		api.nvim_win_set_buf(s.old_win, s.old_buf)
	end
	for _, buf in ipairs({ s.old_buf, s.new_buf }) do
		for _, name in ipairs({ "codediff-highlight", "codediff-filler", "codediff-inline" }) do
			api.nvim_buf_clear_namespace(buf, api.nvim_create_namespace(name), 0, -1)
		end
	end
	if s.current then
		M.show(s.index, vim.deepcopy(s.current))
		if selected and s.diff.changes[selected] then
			focus_hunk(selected)
		else
			api.nvim_win_set_cursor(s.new_win, source_line)
		end
	end
	s.annotation_id = annotation_id
	api.nvim_set_current_win(api.nvim_win_is_valid(focus) and focus or s.new_win)
end

local function read_changes(s, preferred)
	local reference = git.reference(s.root)
	local entries = git.entries(s.root)
	local index = math.min(s.index or 1, #entries)
	if s.exhausted_group then
		index = nil
		for i, entry in ipairs(entries) do
			if (entry.group == "staged") == (s.exhausted_group == "staged") then
				index = i
				break
			end
		end
	end
	if preferred then
		for i, entry in ipairs(entries) do
			if entry_key(entry) == entry_key(preferred) then
				index = i
				break
			end
		end
	end
	local ok, snapshot = true, nil
	if entries[index] then
		ok, snapshot = pcall(git.snapshot, s.root, entries[index])
	end
	return {
		entries = entries,
		reference = reference,
		index = index,
		snapshot = ok and snapshot or nil,
		error = not ok and snapshot or nil,
	}
end

function M.refresh(preferred, changes)
	local s = state()
	changes = changes or read_changes(s, preferred)
	s.annotation_id = nil
	s.entries, s.reference = changes.entries, changes.reference
	local lines, rows, headings = { " REVIEW · Git changes", "" }, {}, {}
	s.group_rows = {}
	for _, group in ipairs({ "staged", "unstaged" }) do
		local count = 0
		for _, entry in ipairs(s.entries) do
			if (entry.group == "staged") == (group == "staged") then
				count = count + 1
			end
		end
		table.insert(lines, string.format(" %s (%d)", group:upper(), count))
		headings[#lines] = group == "staged" and "ReviewStaged" or "ReviewUnstaged"
		s.group_rows[group] = #lines
		for i, entry in ipairs(s.entries) do
			if (entry.group == "staged") == (group == "staged") then
				table.insert(lines, " " .. entry.status .. " " .. vim.fn.strtrans(entry.path))
				rows[#lines] = i
			end
		end
		if count == 0 then
			table.insert(lines, "   (none)")
		end
		table.insert(lines, "")
	end
	s.rows = rows
	set_lines(s.tree_buf, lines)
	api.nvim_buf_clear_namespace(s.tree_buf, tree_ns, 0, -1)
	api.nvim_buf_clear_namespace(s.tree_buf, active_ns, 0, -1)
	for row, highlight in pairs(headings) do
		api.nvim_buf_set_extmark(s.tree_buf, tree_ns, row - 1, 0, { line_hl_group = highlight })
	end
	if changes.index and s.entries[changes.index] then
		local ok, err = false, changes.error
		if not err then
			ok, err = pcall(M.show, changes.index, changes.snapshot)
		end
		if not ok then
			s.current = nil
			s.diff = nil
			mark_hunk(nil)
			set_lines(s.old_buf, {})
			set_lines(s.new_buf, { tostring(err) })
			notify(tostring(err), vim.log.levels.WARN)
		end
	else
		s.index, s.current = nil, nil
		s.diff = nil
		mark_hunk(nil)
		set_lines(s.old_buf, {})
		set_lines(s.new_buf, {})
		for _, buf in ipairs({ s.old_buf, s.new_buf }) do
			api.nvim_buf_clear_namespace(buf, -1, 0, -1)
		end
		if s.exhausted_group then
			api.nvim_win_set_cursor(s.tree_win, { s.group_rows[s.exhausted_group], 0 })
			vim.wo[s.new_win].winbar = s.exhausted_group == "staged"
					and " No staged changes · :fm to review unstaged changes"
				or " No unstaged changes · :fs to review staged changes"
			if s.old_win then
				vim.wo[s.old_win].winbar = ""
			end
		end
	end
	save()
end

function M.refresh_live()
	local s = state()
	local snapshot, entries = s.current, s.entries
	-- Git reads yield to input. Do not apply results after the user changed views,
	-- opened a composer/picker, or left this worktree while the reads were running.
	local changes = read_changes(s, snapshot)
	if not require("rediff.live").ready(s) or s.current ~= snapshot or s.entries ~= entries then
		return false
	end
	local win = api.nvim_get_current_win()
	local tree_entry = s.entries[s.rows[api.nvim_win_get_cursor(s.tree_win)[1]] or 0]
	local views = {}
	for _, pane in ipairs({ s.new_win, s.old_win }) do
		views[pane] = api.nvim_win_call(pane, vim.fn.winsaveview)
	end
	local ok, err = pcall(M.refresh, snapshot, changes)
	if snapshot and s.current and entry_key(snapshot) == entry_key(s.current) then
		for pane, view in pairs(views) do
			api.nvim_win_call(pane, function()
				vim.fn.winrestview(view)
			end)
		end
		mark_hunk(nil)
		local side = win == s.old_win and "old" or "new"
		local buf = side == "old" and s.old_buf or s.new_buf
		local line = api.nvim_win_get_cursor(side == "old" and s.old_win or s.new_win)[1]
		for i, change in ipairs(s.diff.changes) do
			local first, last = hunk_range(change, side, buf)
			if first <= line and line <= last then
				mark_hunk(i)
				break
			end
		end
	end
	if tree_entry then
		for row, index in pairs(s.rows) do
			if entry_key(s.entries[index]) == entry_key(tree_entry) then
				api.nvim_win_set_cursor(s.tree_win, { row, 0 })
				break
			end
		end
	end
	api.nvim_set_current_win(win)
	assert(ok, err)
	-- Live edits must not accumulate full unreferenced file snapshots forever.
	local keep = {}
	if s.current then
		keep[s.current.id] = true
	end
	for _, comment in ipairs(s.comments) do
		keep[comment.snapshot_id] = true
	end
	for id in pairs(s.snapshots) do
		if not keep[id] then
			s.snapshots[id] = nil
		end
	end
	return true
end

function M.focus(group)
	local s = assert(M.active(), "Enter Review first")
	assert(group == "staged" or group == "modified", "Usage: focus staged|modified")
	local win = api.nvim_get_current_win()
	for i, entry in ipairs(s.entries) do
		if (entry.group == "staged") == (group == "staged") then
			M.show(i)
			if win == s.tree_win or win == s.old_win then
				api.nvim_set_current_win(win)
			end
			return
		end
	end
	notify(group == "staged" and "No visible STAGED files" or "No visible UNSTAGED files")
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
		local start_line, end_line = hunk_range(changes[i], side, buf)
		if (direction > 0 and start_line > line) or (direction < 0 and end_line < line) then
			focus_hunk(i)
			return
		end
	end
	-- Wrap within the visible Git group; untracked entries belong to UNSTAGED.
	local previous, selected, snapshot = s.index, s.selected_hunk, s.current
	local staged = s.entries[previous or 0] and s.entries[previous].group == "staged"
	local old_view = s.old_win and api.nvim_win_call(s.old_win, vim.fn.winsaveview)
	local new_view = api.nvim_win_call(s.new_win, vim.fn.winsaveview)
	for offset = 1, #s.entries do
		local i = ((previous or 1) - 1 + direction * offset) % #s.entries + 1
		if (s.entries[i].group == "staged") == staged then
			local ok, err = pcall(M.show, i, i == previous and snapshot or nil)
			if not ok then
				notify("Skipping " .. s.entries[i].path .. ": " .. tostring(err), vim.log.levels.WARN)
			elseif #s.diff.changes > 0 then
				focus_hunk(direction > 0 and 1 or #s.diff.changes)
				api.nvim_set_current_win(win)
				return
			end
		end
	end
	if previous then
		if s.index ~= previous then
			M.show(previous, snapshot)
		end
		if old_view then
			api.nvim_win_call(s.old_win, function()
				vim.fn.winrestview(old_view)
			end)
		end
		api.nvim_win_call(s.new_win, function()
			vim.fn.winrestview(new_view)
		end)
		mark_hunk(selected)
	end
	api.nvim_set_current_win(win)
	notify("No hunks in this Git group")
end

local function advance_staged_hunk(snapshot, hunk, entries, index)
	local s = state()
	-- Worktree coordinates survive staging, even when the old side shifts.
	if s.current and entry_key(s.current) == entry_key(snapshot) then
		for i, change in ipairs(s.diff.changes) do
			if change.modified.start_line >= hunk.new_start + hunk.new_count then
				focus_hunk(i)
				return true
			end
		end
	end
	local remaining, order = {}, {}
	for i, entry in ipairs(s.entries) do
		if entry.group ~= "staged" then
			remaining[entry_key(entry)] = i
		end
	end
	-- Follow the previous file order, wrapping to earlier hunks only at the end.
	for offset = 1, #entries do
		local key = entry_key(entries[(index - 1 + offset) % #entries + 1])
		if remaining[key] then
			table.insert(order, remaining[key])
			remaining[key] = nil
		end
	end
	-- Include files that appeared while Git was applying the patch.
	for i, entry in ipairs(s.entries) do
		if remaining[entry_key(entry)] then
			table.insert(order, i)
		end
	end
	for _, i in ipairs(order) do
		local ok, err = pcall(M.show, i)
		if ok and #s.diff.changes > 0 then
			focus_hunk(1)
			return true
		elseif not ok then
			notify("Skipping " .. s.entries[i].path .. ": " .. tostring(err), vim.log.levels.WARN)
		end
	end
	s.exhausted_group = "unstaged"
	M.refresh()
	return false
end

function M.stage(whole_file, unstage)
	local s = assert(M.active(), "Enter Review first")
	assert(not s.annotation_id, "Press Space R to return to current files before staging")
	local win = api.nvim_get_current_win()
	local snapshot, side, line = s.current, "new", 1
	local next_entry, tree_group
	if win == s.tree_win then
		assert(whole_file, "Focus a source pane to stage/unstage a hunk; S acts on the selected file")
		local index = s.rows[api.nvim_win_get_cursor(win)[1]]
		local entry = assert(s.entries[index], "Select a changed file")
		snapshot = index == s.index and s.current or git.snapshot(s.root, entry)
		tree_group = entry.group == "staged" and "staged" or "unstaged"
		for i = index + 1, #s.entries do
			if (s.entries[i].group == "staged") == (tree_group == "staged") then
				next_entry = s.entries[i]
				break
			end
		end
		if not next_entry then
			for i = index - 1, 1, -1 do
				if (s.entries[i].group == "staged") == (tree_group == "staged") then
					next_entry = s.entries[i]
					break
				end
			end
		end
	else
		side, line = side_at_cursor(), api.nvim_win_get_cursor(win)[1]
	end
	assert(snapshot, "No changed file selected")
	if unstage ~= nil then
		assert(
			(snapshot.group == "staged") == unstage,
			unstage and "Select a STAGED entry" or "Select an UNSTAGED entry"
		)
	end
	local entries, index = s.entries, s.index
	local hunk = git.stage(s.root, snapshot, side, line, whole_file)
	if tree_group then
		s.exhausted_group = tree_group
		M.refresh(next_entry)
	else
		M.refresh(snapshot)
	end
	if hunk and snapshot.group ~= "staged" and not advance_staged_hunk(snapshot, hunk, entries, index) then
		win = s.tree_win
	end
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

function M.compose(visual, keys, comment)
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
	if comment then
		anchor = {
			file = comment.file,
			side = comment.side,
			snapshot_id = comment.snapshot_id,
			selection = vim.deepcopy(comment.selection),
		}
	end
	local draft = { anchor = anchor, text = comment and comment.text or "", comment_id = comment and comment.id }
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
	vim.wo[s.composer_win].winbar = " Note · %<"
		.. vim.fn.strtrans(draft.anchor.file):gsub("%%", "%%%%")
		.. ":"
		.. draft.anchor.selection.spans[1].line
		.. " "
		.. draft.anchor.side
		.. " · :w save · :q discard"
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
		if s.annotation_id == comment.id then
			s.annotation_id = nil
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
	s.annotation_id = nil
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
			if s.annotation_id == comment.id then
				s.annotation_id = nil
			end
			if s.draft and s.draft.comment_id == comment.id then
				s.draft.comment_id = nil
			end
			save()
			render_comments()
			return
		end
	end
end

local function comments_at_cursor()
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
	return matches
end

function M.delete_comment()
	local s = state()
	local matches = comments_at_cursor()
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

local function pick_comments(comments, action)
	local s = state()
	if #comments == 0 then
		notify("No annotations")
		return
	end
	local items = {}
	for i, comment in ipairs(comments) do
		local snapshot = s.snapshots[comment.snapshot_id]
		items[i] = string.format(
			"%d\t%s:%d-%d [%s/%s] %s",
			i,
			vim.fn.strtrans(comment.file),
			comment.line,
			comment.line_end,
			snapshot.group,
			comment.side,
			vim.fn.strtrans((comment.text:gsub("\n", " ")))
		)
	end
	require("fzf-lua").fzf_exec(items, {
		prompt = "Annotations> ",
		previewer = false,
		fzf_opts = { ["--delimiter"] = "\t", ["--with-nth"] = "2..", ["--no-multi"] = true },
		actions = {
			enter = guard(function(selected)
				if M.state == s and selected[1] then
					local comment = comments[tonumber(selected[1]:match("^(%d+)\t"))]
					if comment then
						action(comment)
					end
				end
			end),
		},
	})
end

function M.jump_comment(comment)
	local s = state()
	assert(not s.composer, "Save or close the current annotation before jumping")
	assert(vim.tbl_contains(s.comments, comment), "Annotation no longer exists")
	local snapshot = assert(s.snapshots[comment.snapshot_id], "Missing annotation snapshot")
	if comment.side == "old" and s.layout == "merged" then
		M.view("split")
	end
	local index
	for i, entry in ipairs(s.entries) do
		if entry_key(entry) == entry_key(snapshot) then
			index = i
			break
		end
	end
	M.show(index, snapshot)
	s.annotation_id = comment.id
	local win = comment.side == "old" and s.old_win or s.new_win
	api.nvim_set_current_win(win)
	local span = comment.selection.spans[1]
	local column =
		math.min(math.max(0, span.start_byte - 1), #api.nvim_buf_get_lines(0, span.line - 1, span.line, false)[1])
	api.nvim_win_set_cursor(win, { span.line, column })
	vim.cmd("normal! zz")
	vim.cmd.redrawstatus()
end

function M.edit_comment(comment)
	local s = state()
	if comment then
		M.jump_comment(comment)
		M.compose(false, "i", comment)
		return
	end
	if s.composer then
		M.compose()
		return
	end
	local matches = comments_at_cursor()
	for _, item in ipairs(matches) do
		if item.id == s.annotation_id then
			M.edit_comment(item)
			return
		end
	end
	if #matches == 0 then
		M.compose()
	elseif #matches == 1 then
		M.edit_comment(matches[1])
	else
		pick_comments(matches, M.edit_comment)
	end
end

function M.list_comments()
	pick_comments(vim.list_slice(state().comments), M.jump_comment)
end

function M.next_comment(direction)
	local s = state()
	if #s.comments == 0 then
		notify("No annotations")
		return
	end
	local index = direction > 0 and 0 or 1
	for i, comment in ipairs(s.comments) do
		if comment.id == s.annotation_id then
			index = i
			break
		end
	end
	M.jump_comment(s.comments[(index - 1 + direction) % #s.comments + 1])
end

function M.leave()
	local s = state()
	tree_focus(false)
	require("rediff.live").stop(s)
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
	local pending = reviews[root] or {}
	local s = {
		root = root,
		directory = directory,
		comments = pending.comments or {},
		snapshots = pending.snapshots or {},
		harness = target,
		previous_tab = api.nvim_get_current_tabpage(),
		id = string.format("%.0f", vim.uv.hrtime()),
		delivery = "draft",
		layout = "split",
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
	vim.wo[s.tree_win].cursorlineopt = "line"
	vim.wo[s.tree_win].winhighlight = "CursorLine:ReviewTreeSelection"
	api.nvim_win_set_width(s.tree_win, math.min(28, math.floor(vim.o.columns / 5)))
	vim.cmd("wincmd =")
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
				if key == "i" then
					M.edit_comment()
				else
					M.compose(false, prefix .. key)
				end
			end, "Comment instead of editing code")
		end
		map(buf, "n", "d", M.delete_comment, "Delete annotation on this line")
		for _, key in ipairs({ "a", "i", "<leader>c" }) do
			map(buf, "x", key, function()
				M.compose(true)
			end, "Comment on exact selection")
		end
	end
	for _, buf in ipairs({ s.tree_buf, s.old_buf, s.new_buf }) do
		map_hunks(buf)
		map_staging(buf)
		map(buf, "n", "<leader>R", M.refresh, "Refresh snapshot")
		map(buf, "n", "<leader>q", M.leave, "Leave Review")
		map(buf, "n", "@", M.list_comments, "Find/jump to annotations")
		for key, direction in pairs({ ["}"] = 1, ["{"] = -1 }) do
			map(buf, "n", key, function()
				for _ = 1, vim.v.count1 do
					M.next_comment(direction)
				end
			end, direction > 0 and "Next annotation" or "Previous annotation")
		end
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
	api.nvim_set_current_win(s.tree_win)
	require("rediff.live").start(s)
	api.nvim_exec_autocmds("User", { pattern = "ReviewEnter" })
end

function M.statusline()
	local s = M.state
	if s and api.nvim_get_current_tabpage() == s.tab then
		local file = s.current and vim.fn.strtrans(s.current.path):gsub("%%", "%%%%")
			or (#s.entries > 0 and "No file selected" or "No changes")
		return " REVIEW · "
			.. vim.fn.strtrans(s.reference or ""):gsub("%%", "%%%%")
			.. " · "
			.. harness.statusline(s.root)
			.. " · "
			.. (s.current and s.diff_engine or "text")
			.. " · "
			.. #s.comments
			.. " comments   %<"
			.. file
			.. (s.annotation_id and " · annotation snapshot · Space R to refresh" or "")
			.. "%=%l:%c "
	end
	return " EDIT · " .. vim.fn.mode():upper() .. " · " .. harness.statusline() .. " %<%f %m%=%l:%c %p%%"
end

function M.setup()
	local palette = require("rose-pine.palette")
	api.nvim_set_hl(0, "ReviewStaged", { fg = palette.foam, bg = palette.surface, bold = true })
	api.nvim_set_hl(0, "ReviewUnstaged", { fg = palette.gold, bg = palette.surface, bold = true })
	api.nvim_set_hl(0, "ReviewActiveFile", { bg = palette.highlight_med, bold = true })
	api.nvim_set_hl(0, "ReviewHunk", { fg = palette.gold, bold = true })
	local function review_highlights()
		api.nvim_set_hl(0, "ReviewAnnotation", { fg = "#ff9e64" })
		api.nvim_set_hl(0, "ReviewTreeSelection", { fg = palette.text, bg = palette.highlight_high, bold = true })
		api.nvim_set_hl(
			0,
			"ReviewTreeCursor",
			{ fg = palette.highlight_high, bg = palette.highlight_high, blend = 100 }
		)
	end
	review_highlights()
	api.nvim_create_autocmd("ColorScheme", { callback = review_highlights })
	api.nvim_create_autocmd({ "WinEnter", "BufEnter" }, {
		callback = function()
			local s = M.state
			tree_focus(
				s ~= nil and api.nvim_get_current_win() == s.tree_win and api.nvim_get_current_buf() == s.tree_buf
			)
		end,
	})
	api.nvim_create_autocmd({ "WinLeave", "BufLeave" }, {
		callback = function()
			tree_focus(false)
		end,
	})
	api.nvim_create_autocmd("CursorMoved", {
		callback = function()
			local s = M.state
			if not s then
				return
			end
			local buf = api.nvim_get_current_buf()
			if buf == s.tree_buf then
				local cursor = api.nvim_win_get_cursor(0)
				local index = s.rows[cursor[1]]
				-- Keep the intentional empty-group stop after staging, but ordinary
				-- motions must land on files rather than headings or separator rows.
				local empty_group = s.exhausted_group and cursor[1] == s.group_rows[s.exhausted_group]
				if not index and not empty_group then
					local previous = cursor[1]
					for row, item in pairs(s.rows) do
						if item == s.index then
							previous = row
							break
						end
					end
					local step = cursor[1] < previous and -1 or 1
					for _, direction in ipairs({ step, -step }) do
						local boundary = direction > 0 and api.nvim_buf_line_count(buf) or 1
						for row = cursor[1] + direction, boundary, direction do
							if s.rows[row] then
								cursor[1], index = row, s.rows[row]
								break
							end
						end
						if index then
							break
						end
					end
				end
				api.nvim_win_set_cursor(0, { cursor[1], 0 })
				if index and (index ~= s.index or not s.current) then
					local entry = s.entries[index]
					local ok, snapshot = pcall(git.snapshot, s.root, entry)
					-- Git reads yield to input; do not reopen a row the user has left.
					if
						M.active() ~= s
						or api.nvim_get_current_win() ~= s.tree_win
						or s.entries[index] ~= entry
						or s.rows[api.nvim_win_get_cursor(s.tree_win)[1]] ~= index
					then
						return
					end
					if not ok then
						notify(tostring(snapshot), vim.log.levels.ERROR)
						return
					end
					guard(M.show)(index, snapshot)
					api.nvim_set_current_win(s.tree_win)
				end
				return
			end
			if not s.diff or (buf ~= s.old_buf and buf ~= s.new_buf) then
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
		"View",
		guard(function(opts)
			M.view(opts.args ~= "" and opts.args or nil)
		end),
		{
			nargs = "?",
			complete = function()
				return { "split", "merged" }
			end,
		}
	)
	vim.cmd(
		[[cnoreabbrev <expr> view getcmdtype() == ':' && getcmdline() == 'view' && getcmdpos() == 5 && luaeval("require('rediff.review').active() ~= nil") ? 'View' : 'view']]
	)
	api.nvim_create_user_command(
		"Focus",
		guard(function(opts)
			M.focus(opts.args)
		end),
		{
			nargs = 1,
			complete = function()
				return { "staged", "modified" }
			end,
		}
	)
	api.nvim_create_user_command(
		"Annotations",
		guard(function(opts)
			assert(M.active(), "Enter Review first")
			local actions = {
				list = M.list_comments,
				new = M.compose,
				next = function()
					M.next_comment(1)
				end,
				prev = function()
					M.next_comment(-1)
				end,
			}
			assert(actions[opts.args], "Usage: annotations list|next|prev|new")()
		end),
		{
			nargs = 1,
			complete = function()
				return { "list", "next", "prev", "new" }
			end,
		}
	)
	for alias, command in pairs({
		focus = "Focus",
		fs = "Focus staged",
		fm = "Focus modified",
		annotations = "Annotations",
		al = "Annotations list",
	}) do
		vim.cmd(
			string.format(
				[[cnoreabbrev <expr> %s getcmdtype() == ':' && getcmdline() == '%s' && getcmdpos() == %d && luaeval("require('rediff.review').active() ~= nil") ? '%s' : '%s']],
				alias,
				alias,
				#alias + 1,
				command,
				alias
			)
		)
	end
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
				require("rediff.live").stop(M.state)
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
					(s.old_win and not api.nvim_win_is_valid(s.old_win))
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
			local s = M.state
			if s then
				if api.nvim_win_is_valid(s.tree_win) then
					api.nvim_win_set_width(s.tree_win, math.min(28, math.floor(vim.o.columns / 5)))
				end
				if s.old_win and api.nvim_win_is_valid(s.old_win) and api.nvim_win_is_valid(s.new_win) then
					local width = api.nvim_win_get_width(s.old_win) + api.nvim_win_get_width(s.new_win)
					api.nvim_win_set_width(s.old_win, math.floor(width / 2))
				end
				render_comments()
			end
		end,
	})
end

return M
