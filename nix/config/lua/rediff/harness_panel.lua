local M = { state = nil }
local api = vim.api

local spinner = { "|", "/", "-", "\\" }

local function notify(message, level)
	vim.notify(message, level or vim.log.levels.INFO, { title = "rediff" })
end

local function valid_win(win)
	return win and api.nvim_win_is_valid(win)
end

local function text(value)
	return vim.fn.strtrans(tostring(value or ""))
end

local function close()
	local s = M.state
	if not s or s.closing then
		return
	end
	s.closing = true
	if s.unsubscribe then
		pcall(s.unsubscribe)
	end
	if s.timer then
		s.timer:stop()
		s.timer:close()
	end
	if s.augroup then
		pcall(api.nvim_del_augroup_by_id, s.augroup)
	end
	for _, win in ipairs({ s.filter_win, s.win }) do
		if valid_win(win) then
			pcall(api.nvim_win_close, win, true)
		end
	end
	for _, buf in ipairs({ s.filter_buf, s.buf }) do
		if buf and api.nvim_buf_is_valid(buf) then
			pcall(api.nvim_buf_delete, buf, { force = true })
		end
	end
	M.state = nil
end

M.close = close

local function status(entry)
	if entry.online == false then
		return "offline"
	elseif entry.connected then
		return "connected"
	end
	return "available"
end

local function activity(entry, frame)
	local state = entry.activity and entry.activity.state or "unknown"
	if state == "running" then
		return spinner[frame] .. " running"
	end
	return text(state)
end

local function searchable(entry)
	local activity_state = entry.activity and entry.activity.state or "unknown"
	local tool = entry.activity and entry.activity.tool or ""
	return table.concat({
		entry.alias or "",
		entry.name or "",
		entry.provider or "",
		entry.title or "",
		entry.session or "",
		entry.root or "",
		activity_state,
		tool,
	}, " ")
end

local function matches(entry, query)
	if query == "" then
		return true
	end
	return #vim.fn.matchfuzzy({ searchable(entry) }, query) > 0
end

local function configure_windows(s)
	local width = math.max(20, math.min(88, vim.o.columns - 6))
	local height = math.max(6, math.min(22, vim.o.lines - 8))
	local row = math.max(1, math.floor((vim.o.lines - height - 3) / 2))
	local col = math.max(1, math.floor((vim.o.columns - width) / 2))
	s.width, s.height = width, height
	if valid_win(s.filter_win) then
		api.nvim_win_set_config(s.filter_win, {
			relative = "editor",
			row = row,
			col = col,
			width = width,
			height = 1,
		})
	end
	if valid_win(s.win) then
		api.nvim_win_set_config(s.win, {
			relative = "editor",
			row = row + 3,
			col = col,
			width = width,
			height = height,
		})
	end
end

local function selected_entry(s)
	return s.entries[s.selected]
end

local function refresh_items(s)
	local selected = selected_entry(s)
	local selected_key = selected and selected.key or s.selected_key
	local all = s.registry.items() or {}
	s.entries = {}
	for _, entry in ipairs(all) do
		if (not s.connected_only or entry.connected) and matches(entry, s.filter) then
			table.insert(s.entries, entry)
		end
	end
	s.selected = math.min(math.max(s.selected or 1, 1), math.max(#s.entries, 1))
	if selected_key then
		for index, entry in ipairs(s.entries) do
			if entry.key == selected_key then
				s.selected = index
				break
			end
		end
	end
	s.selected_key = selected_entry(s) and selected_entry(s).key or nil
end

local function render()
	local s = M.state
	if not s or s.closing or not valid_win(s.win) then
		return
	end
	refresh_items(s)
	local slots = math.max(1, s.height - 8)
	if s.selected < s.offset then
		s.offset = s.selected
	elseif s.selected >= s.offset + slots then
		s.offset = s.selected - slots + 1
	end
	s.offset = math.max(1, math.min(s.offset, math.max(1, #s.entries - slots + 1)))
	local mode = s.connected_only and "connected only" or "all sessions"
	local lines = { string.format(" Live harnesses · %s · %d ", mode, #s.entries), "" }
	s.rows = {}
	for index = s.offset, math.min(#s.entries, s.offset + slots - 1) do
		local entry = s.entries[index]
		local marker = index == s.selected and "›" or " "
		local label = entry.alias or (entry.title ~= "" and entry.title) or entry.session or entry.key
		local line = string.format(
			"%s %-10s %-20s %-6s %s",
			marker,
			status(entry),
			vim.fn.strcharpart(text(label), 0, 20),
			text(entry.provider),
			activity(entry, s.frame)
		)
		table.insert(lines, line)
		s.rows[#lines] = entry
	end
	while #lines < slots + 2 do
		table.insert(lines, "")
	end
	table.insert(lines, string.rep("─", math.max(1, s.width - 2)))
	local entry = selected_entry(s)
	if entry then
		local relation = entry.root == s.root and "current" or "external worktree"
		local alias = entry.alias or (entry.title ~= "" and entry.title) or "(no alias)"
		table.insert(
			lines,
			string.format(
				" %s · %s · %s",
				text(alias),
				text(entry.provider or "unknown"),
				text(entry.session or entry.key)
			)
		)
		table.insert(
			lines,
			string.format(
				" %s · %s · tool: %s",
				relation,
				activity(entry, s.frame),
				text(entry.activity and entry.activity.tool or "unknown")
			)
		)
		table.insert(lines, " " .. text(entry.root or "(root unknown)"))
	else
		table.insert(lines, " No matching harnesses")
		table.insert(lines, "")
		table.insert(lines, "")
	end
	local action = s.purpose == "send" and "Enter send"
		or (entry and entry.connected and "Enter disconnect" or "Enter connect")
	table.insert(lines, " " .. action .. "  / filter  c connected  R discover  q close")
	table.insert(lines, " s message  r rename  d disconnect")
	vim.bo[s.buf].modifiable = true
	api.nvim_buf_set_lines(s.buf, 0, -1, false, lines)
	vim.bo[s.buf].modifiable = false
	if entry then
		local row = 3 + (s.selected - s.offset)
		pcall(api.nvim_win_set_cursor, s.win, { row, 0 })
	end
end

local function safely(fn)
	local ok, err = pcall(fn)
	if not ok then
		notify(err, vim.log.levels.ERROR)
	end
end

local function act(callback, should_close)
	local s = M.state
	local entry = s and selected_entry(s)
	if not entry or not callback then
		return
	end
	if should_close then
		close()
	end
	safely(function()
		callback(entry)
	end)
end

local function move(delta)
	local s = M.state
	if not s or #s.entries == 0 then
		return
	end
	s.selected = math.max(1, math.min(#s.entries, s.selected + delta))
	s.selected_key = s.entries[s.selected].key
	render()
end

local function map(buf, key, fn, description)
	vim.keymap.set("n", key, fn, { buffer = buf, nowait = true, silent = true, desc = description })
end

function M.open(opts)
	opts = opts or {}
	close()
	local registry = require("rediff.connections")
	local purpose = opts.purpose or "manage"
	local s = {
		registry = registry,
		root = opts.root,
		purpose = purpose,
		connected_only = purpose == "send" or opts.connected_only == true,
		filter = "",
		entries = {},
		rows = {},
		selected = 1,
		offset = 1,
		frame = 1,
		opts = opts,
	}
	M.state = s
	s.buf = api.nvim_create_buf(false, true)
	s.filter_buf = api.nvim_create_buf(false, true)
	vim.bo[s.buf].bufhidden = "wipe"
	vim.bo[s.filter_buf].bufhidden = "wipe"
	vim.bo[s.filter_buf].buftype = "nofile"
	vim.bo[s.buf].filetype = "rediff-harness-panel"
	vim.bo[s.filter_buf].filetype = "rediff-harness-filter"
	s.win = api.nvim_open_win(s.buf, true, {
		relative = "editor",
		row = 1,
		col = 1,
		width = 40,
		height = 10,
		style = "minimal",
		border = "single",
		title = " Harnesses ",
		title_pos = "center",
	})
	s.filter_win = api.nvim_open_win(s.filter_buf, false, {
		relative = "editor",
		row = 1,
		col = 1,
		width = 40,
		height = 1,
		style = "minimal",
		border = "single",
		title = " Filter ",
		title_pos = "center",
	})
	for _, win in ipairs({ s.win, s.filter_win }) do
		vim.wo[win].winhighlight = "Normal:NormalFloat,FloatBorder:FloatBorder,CursorLine:Visual"
	end
	vim.wo[s.win].cursorline = true
	vim.wo[s.win].wrap = false
	local function focus_results()
		s.filter = api.nvim_buf_get_lines(s.filter_buf, 0, 1, false)[1] or ""
		vim.cmd.stopinsert()
		api.nvim_set_current_win(s.win)
		render()
	end
	vim.keymap.set({ "i", "n" }, "<CR>", focus_results, { buffer = s.filter_buf, silent = true })
	vim.keymap.set("i", "<Esc>", focus_results, { buffer = s.filter_buf, silent = true })
	configure_windows(s)

	map(s.buf, "q", close, "Close harness panel")
	map(s.buf, "<Esc>", close, "Close harness panel")
	map(s.buf, "j", function()
		move(1)
	end, "Next harness")
	map(s.buf, "k", function()
		move(-1)
	end, "Previous harness")
	map(s.buf, "<Down>", function()
		move(1)
	end, "Next harness")
	map(s.buf, "<Up>", function()
		move(-1)
	end, "Previous harness")
	map(s.buf, "<CR>", function()
		act(opts.on_select, purpose == "send")
	end, "Select harness")
	map(s.buf, "s", function()
		local entry = selected_entry(s)
		if entry and entry.connected then
			act(opts.on_message, true)
		end
	end, "Message connected harness")
	map(s.buf, "d", function()
		local callback = opts.on_disconnect or function(entry)
			registry.disconnect(entry.key)
		end
		act(callback, false)
	end, "Disconnect harness")
	map(s.buf, "r", function()
		local entry = selected_entry(s)
		if entry then
			vim.ui.input({ prompt = "Harness alias: ", default = entry.alias or "" }, function(alias)
				if alias ~= nil and M.state == s then
					safely(function()
						registry.rename(entry.key, alias)
					end)
				end
			end)
		end
	end, "Rename harness")
	map(s.buf, "/", function()
		api.nvim_set_current_win(s.filter_win)
		vim.cmd.startinsert()
	end, "Filter harnesses")
	map(s.buf, "c", function()
		if purpose ~= "send" then
			s.connected_only = not s.connected_only
			s.selected, s.offset = 1, 1
			render()
		end
	end, "Toggle connected harnesses")
	map(s.buf, "R", function()
		registry.discover(function(err)
			if err then
				notify(tostring(err), vim.log.levels.ERROR)
			end
		end)
	end, "Discover harnesses")
	map(s.filter_buf, "q", close, "Close harness panel")
	map(s.filter_buf, "<Esc>", close, "Close harness panel")

	s.augroup = api.nvim_create_augroup("RediffHarnessPanel" .. s.buf, { clear = true })
	api.nvim_create_autocmd({ "TextChanged", "TextChangedI" }, {
		group = s.augroup,
		buffer = s.filter_buf,
		callback = function()
			local line = api.nvim_buf_get_lines(s.filter_buf, 0, 1, false)[1] or ""
			s.filter = line
			s.selected, s.offset = 1, 1
			render()
		end,
	})
	api.nvim_create_autocmd("VimResized", {
		group = s.augroup,
		callback = function()
			configure_windows(s)
			render()
		end,
	})
	api.nvim_create_autocmd("CursorMoved", {
		group = s.augroup,
		buffer = s.buf,
		callback = function()
			local row = api.nvim_win_get_cursor(s.win)[1]
			local entry = s.rows[row]
			if entry and entry.key ~= s.selected_key then
				for index, candidate in ipairs(s.entries) do
					if candidate.key == entry.key then
						s.selected, s.selected_key = index, entry.key
						break
					end
				end
				render()
			elseif not entry and selected_entry(s) then
				render()
			end
		end,
	})
	api.nvim_create_autocmd("WinClosed", {
		group = s.augroup,
		callback = function(event)
			if tonumber(event.match) == s.win or tonumber(event.match) == s.filter_win then
				vim.schedule(function()
					if M.state == s then
						close()
					end
				end)
			end
		end,
	})
	s.unsubscribe = registry.on_change(function()
		if M.state == s then
			render()
		end
	end)
	s.timer = vim.uv.new_timer()
	s.timer:start(
		100,
		100,
		vim.schedule_wrap(function()
			if M.state ~= s then
				return
			end
			for _, entry in ipairs(s.entries) do
				if entry.activity and entry.activity.state == "running" then
					s.frame = (s.frame % #spinner) + 1
					render()
					break
				end
			end
		end)
	)
	render()
	if purpose ~= "send" then
		registry.discover(function(err)
			if err then
				notify(tostring(err), vim.log.levels.ERROR)
			end
		end)
	end
	return s
end

return M
