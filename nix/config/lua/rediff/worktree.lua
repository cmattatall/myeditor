local M = {}
local api = vim.api
local git = require("rediff.git")
local review = require("rediff.review")
local harness = require("rediff.harness")
local feedback = require("rediff.feedback")
local editing_tabs = {}
local picker_win

local function root()
	local s = review.active()
	return s and s.root or git.root()
end

function M.list()
	local entries, entry = {}, nil
	for field in git.run(root(), { "worktree", "list", "--porcelain", "-z" }):gmatch("[^%z]+") do
		local key, value = field:match("^(%S+)%s?(.*)$")
		if key == "worktree" then
			entry = { path = value }
			table.insert(entries, entry)
		elseif entry then
			entry[key] = value ~= "" and value or true
		end
	end
	return entries
end

local function can_leave()
	local s = review.state
	assert(not s or not s.composer, "Save or close the annotation before switching worktrees")
	assert(not s or not feedback.busy(s.root), "Wait for feedback delivery before switching worktrees")
end

function M.switch(target)
	can_leave()
	local from = root()
	local path
	local absolute = vim.fn.fnamemodify(target, ":p"):gsub("/$", "")
	for _, entry in ipairs(M.list()) do
		if target == entry.path or absolute == entry.path or entry.branch == "refs/heads/" .. target then
			assert(not entry.bare and not entry.prunable, "Worktree is not available: " .. entry.path)
			path = entry.path
			break
		end
	end
	assert(path, "Unknown worktree: " .. target .. "; use :worktree list")
	if review.state and review.state.root == path then
		review.open()
		return
	end
	git.entries(path) -- Reject conflicts/unavailable worktrees before closing anything.
	local lock = feedback.directory(path) .. "/editor.lock"
	if vim.fn.filereadable(lock) == 1 then
		local pid = tonumber(vim.fn.readfile(lock)[1])
		assert(pid and not vim.uv.kill(pid, 0), "Another editor owns this review: " .. path)
	end
	local old = review.state
	local previous_tab = api.nvim_get_current_tabpage()
	if old then
		editing_tabs[old.root] = old.previous_tab
		review.leave()
	elseif vim.bo.buftype ~= "terminal" then
		editing_tabs[from] = previous_tab
	end
	local tab = editing_tabs[path]
	if tab and api.nvim_tabpage_is_valid(tab) then
		api.nvim_set_current_tabpage(tab)
	else
		vim.cmd.tabnew()
		editing_tabs[path] = api.nvim_get_current_tabpage()
	end
	vim.cmd.tcd(path)
	local ok, err = pcall(review.open)
	if not ok then
		-- A lock can race the preflight. Keep all buffers and restore the old Review.
		if old then
			api.nvim_set_current_tabpage(old.previous_tab)
			vim.cmd.tcd(old.root)
			pcall(review.open)
		elseif api.nvim_tabpage_is_valid(previous_tab) then
			api.nvim_set_current_tabpage(previous_tab)
		end
		error(err)
	end
	harness.connect(harness.get(path).provider, true)
end

function M.new(branch, destination)
	can_leave()
	local source = root()
	harness.launch_command(source) -- Fail before creating a branch if the CLI is absent.
	git.run(source, { "check-ref-format", "--branch", branch })
	local provider = harness.get(source).provider
	destination = destination or (source .. "-" .. branch:gsub("/", "-"))
	if destination:sub(1, 1) ~= "/" then
		destination = source .. "/" .. destination
	end
	assert(not vim.uv.fs_lstat(destination), "Destination already exists: " .. destination)
	git.run(source, { "worktree", "add", "-b", branch, "--", destination })
	local path = assert(vim.uv.fs_realpath(destination))
	vim.notify("Created worktree: " .. path)
	-- Inherit the launch type, never another checkout's thread/connection or drafts.
	harness.select(path, { name = "none" })
	harness.use(provider, path)
	M.switch(path)
	harness.launch(path)
	return path
end

local function inside(path, directory)
	return path == directory or path:sub(1, #directory + 1) == directory .. "/"
end

function M.delete(target)
	local source, entries = root(), M.list()
	local absolute = vim.fn.fnamemodify(target, ":p"):gsub("/$", "")
	local entry
	for _, candidate in ipairs(entries) do
		if candidate.path == absolute or candidate.branch == "refs/heads/" .. target then
			assert(not entry, "Ambiguous worktree: " .. target .. "; use its full path")
			entry = candidate
		end
	end
	assert(entry, "Unknown worktree: " .. target .. "; use :worktree list")
	local path = entry.path
	assert(entry ~= entries[1], "Cannot delete the main worktree")
	assert(not entry.locked, "Worktree is locked: " .. path)
	assert(not entry.bare and not entry.prunable, "Worktree is not available: " .. path)
	assert(not feedback.busy(path), "Wait for feedback delivery before deleting this worktree")
	local lock = feedback.directory(path) .. "/editor.lock"
	if vim.fn.filereadable(lock) == 1 then
		local pid = tonumber(vim.fn.readfile(lock)[1])
		assert(
			pid and (pid == vim.uv.os_getpid() or not vim.uv.kill(pid, 0)),
			"Another editor owns this review: " .. path
		)
	end
	local buffers = {}
	for _, buf in ipairs(api.nvim_list_bufs()) do
		if vim.bo[buf].buftype == "" and inside(api.nvim_buf_get_name(buf), path) then
			assert(
				not vim.bo[buf].modified,
				"Save or discard unsaved buffer before deletion: " .. api.nvim_buf_get_name(buf)
			)
			table.insert(buffers, buf)
		end
	end
	assert(
		git.run(path, { "status", "--porcelain", "--untracked-files=all" }) == "",
		"Worktree has uncommitted or untracked files: " .. path
	)
	local fallback = source == path and entries[1].path or source
	if source == path or (review.state and review.state.root == path) then
		M.switch(fallback) -- Validate the destination before stopping a harness or deleting files.
	end
	harness.stop(path)
	-- No --force: Git checks again after stopping the harness, including late writes.
	git.run(fallback, { "worktree", "remove", "--", path })
	for _, buf in ipairs(buffers) do
		if api.nvim_buf_is_valid(buf) then
			api.nvim_buf_delete(buf, {})
		end
	end
	-- Retain other tabs/buffers, but never leave a window in a removed directory.
	if inside(vim.fn.getcwd(-1, -1), path) then
		vim.cmd.cd(fallback)
	end
	for _, win in ipairs(api.nvim_list_wins()) do
		api.nvim_win_call(win, function()
			if inside(vim.fn.getcwd(), path) then
				if vim.fn.haslocaldir() == 1 then
					vim.cmd.lcd(fallback)
				else
					vim.cmd.tcd(fallback)
				end
			end
		end)
	end
	editing_tabs[path] = nil
	for _, connection in ipairs(require("rediff.connections").items()) do
		if connection.root == path and connection.connected then
			require("rediff.connections").disconnect(connection.key)
		end
	end
	vim.notify("Deleted worktree: " .. path .. "; branch retained")
end

local function guarded(fn)
	local ok, err = pcall(fn)
	if not ok then
		vim.notify(tostring(err), vim.log.levels.ERROR)
	end
end

local function prompt_new(before_create)
	local source = root()
	vim.ui.input({ prompt = "New branch (worktree created beside this checkout): " }, function(branch)
		if branch and branch ~= "" then
			guarded(function()
				assert(root() == source, "Checkout changed; run :worktree new again")
				if before_create then
					before_create()
				end
				M.new(branch)
			end)
		end
	end)
end

function M.choose(action)
	action = action or "list"
	if picker_win and api.nvim_win_is_valid(picker_win) then
		api.nvim_win_close(picker_win, true)
	end
	local source = root()
	local entries = M.list()
	local buf = api.nvim_create_buf(false, true)
	vim.bo[buf].bufhidden = "wipe"
	vim.bo[buf].filetype = "rediff-worktrees"
	local width = math.max(20, math.min(110, vim.o.columns - 6))
	local height = math.max(4, math.min(#entries + 3, vim.o.lines - 8))
	local win = api.nvim_open_win(buf, true, {
		relative = "editor",
		row = math.max(1, math.floor((vim.o.lines - height) / 2) - 1),
		col = math.floor((vim.o.columns - width) / 2),
		width = width,
		height = height,
		style = "minimal",
		border = "single",
		title = action == "list" and " Worktrees " or (" Worktree " .. action .. " "),
		title_pos = "center",
	})
	picker_win = win
	vim.wo[win].cursorline = true
	vim.wo[win].wrap = false
	local function close()
		if api.nvim_win_is_valid(win) then
			api.nvim_win_close(win, true)
		end
	end
	local function render()
		local help = action == "list" and " Enter switch   n new + harness   dd delete" or (" Enter " .. action)
		local lines = { help .. "   R refresh   q close", "" }
		for i, entry in ipairs(entries) do
			table.insert(
				lines,
				vim.fn.strtrans(
					(entry.path == source and "* " or "  ")
						.. (entry.branch and entry.branch:gsub("^refs/heads/", "") or "detached")
						.. (i == 1 and " [main worktree]" or "")
						.. (entry.locked and " [locked]" or "")
						.. "  "
						.. entry.path
				)
			)
		end
		vim.bo[buf].modifiable = true
		api.nvim_buf_set_lines(buf, 0, -1, false, lines)
		vim.bo[buf].modifiable = false
	end
	local function selected()
		assert(root() == source, "Checkout changed; reopen the worktree list")
		return entries[api.nvim_win_get_cursor(win)[1] - 2]
	end
	local function map(key, fn, desc)
		vim.keymap.set("n", key, function()
			guarded(fn)
		end, { buffer = buf, nowait = true, desc = desc })
	end
	local function delete_selected()
		local entry = selected()
		if not entry then
			return
		end
		vim.ui.select({ "Cancel", "Delete" }, {
			prompt = "Delete " .. vim.fn.strtrans(entry.path) .. " and stop its editor-owned harness? Branch is kept.",
		}, function(choice)
			if choice ~= "Delete" or not api.nvim_win_is_valid(win) then
				return
			end
			guarded(function()
				assert(root() == source, "Checkout changed; reopen the worktree list")
				close()
				local ok, err = pcall(M.delete, entry.path)
				if action == "list" or not ok then
					M.choose(action)
				end
				assert(ok, err)
			end)
		end)
	end
	if action == "delete" then
		map("<CR>", delete_selected, "Delete worktree and stop harness")
	else
		map("<CR>", function()
			local entry = selected()
			if entry then
				close()
				M.switch(entry.path)
			end
		end, "Switch worktree")
	end
	if action == "list" then
		map("n", function()
			prompt_new(close)
		end, "Create worktree and launch harness")
		map("dd", delete_selected, "Delete worktree and stop harness")
	end
	map("R", function()
		assert(root() == source, "Checkout changed; reopen the worktree list")
		entries = M.list()
		render()
	end, "Refresh worktrees")
	map("q", close, "Close worktrees")
	map("<Esc>", close, "Close worktrees")
	render()
	for i, entry in ipairs(entries) do
		if entry.path == source then
			api.nvim_win_set_cursor(win, { i + 2, 0 })
			break
		end
	end
end

function M.setup()
	api.nvim_create_user_command("Worktree", function(opts)
		guarded(function()
			local args = opts.fargs
			if #args == 1 and (args[1] == "list" or args[1] == "switch" or args[1] == "delete") then
				M.choose(args[1])
			elseif #args == 2 and args[1] == "switch" then
				M.switch(args[2])
			elseif #args == 2 and args[1] == "delete" then
				M.delete(args[2])
			elseif #args == 1 and args[1] == "new" then
				prompt_new()
			elseif args[1] == "new" and (#args == 2 or #args == 3) then
				M.new(args[2], args[3])
			else
				error("Usage: Worktree list | switch [branch|path] | new [branch [path]] | delete [branch|path]")
			end
		end)
	end, {
		nargs = "+",
		complete = function(_, line)
			if line:match("^Worktree%s+%a*$") then
				return { "list", "switch", "new", "delete" }
			end
			return {}
		end,
	})
	vim.cmd(
		[[cnoreabbrev <expr> worktree getcmdtype() == ':' && getcmdline() == 'worktree' && getcmdpos() == 9 ? 'Worktree' : 'worktree']]
	)
end

return M
