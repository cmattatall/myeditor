local M = {}
local api = vim.api
local git = require("rediff.git")
local review = require("rediff.review")
local harness = require("rediff.harness")
local feedback = require("rediff.feedback")
local editing_tabs = {}

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

local function guarded(fn)
	local ok, err = pcall(fn)
	if not ok then
		vim.notify(tostring(err), vim.log.levels.ERROR)
	end
end

function M.choose()
	local source = root()
	vim.ui.select(M.list(), {
		prompt = "Switch worktree:",
		format_item = function(entry)
			return vim.fn.strtrans(
				(entry.path == source and "* " or "  ")
					.. entry.path
					.. "  ["
					.. (entry.branch and entry.branch:gsub("^refs/heads/", "") or "detached")
					.. "]"
			)
		end,
	}, function(entry)
		if entry then
			guarded(function()
				assert(root() == source, "Checkout changed; reopen the worktree picker")
				M.switch(entry.path)
			end)
		end
	end)
end

function M.setup()
	api.nvim_create_user_command("Worktree", function(opts)
		guarded(function()
			local args = opts.fargs
			if (#args == 1 and args[1] == "list") or (#args == 1 and args[1] == "switch") then
				M.choose()
			elseif #args == 2 and args[1] == "switch" then
				M.switch(args[2])
			elseif #args == 1 and args[1] == "new" then
				local source = root()
				vim.ui.input({ prompt = "New branch (worktree created beside this checkout): " }, function(branch)
					if branch and branch ~= "" then
						guarded(function()
							assert(root() == source, "Checkout changed; run :worktree new again")
							M.new(branch)
						end)
					end
				end)
			elseif args[1] == "new" and (#args == 2 or #args == 3) then
				M.new(args[2], args[3])
			else
				error("Usage: Worktree list | switch [branch|path] | new [branch [path]]")
			end
		end)
	end, {
		nargs = "+",
		complete = function(_, line)
			if line:match("^Worktree%s+%a*$") then
				return { "list", "switch", "new" }
			end
			return {}
		end,
	})
	vim.cmd(
		[[cnoreabbrev <expr> worktree getcmdtype() == ':' && getcmdline() == 'worktree' && getcmdpos() == 9 ? 'Worktree' : 'worktree']]
	)
end

return M
