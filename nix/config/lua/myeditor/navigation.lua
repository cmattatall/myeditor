local M = {}
local api = vim.api
local review = require("myeditor.review")
local git = require("myeditor.git")

function M.explorer()
	local s = review.active()
	if s then
		api.nvim_set_current_win(s.tree_win)
	else
		require("neo-tree.command").execute({ action = "focus", source = "filesystem", position = "left" })
	end
end

function M.diff()
	local s = review.active()
	if s then
		api.nvim_set_current_win(s.new_win)
	else
		vim.cmd("wincmd l")
	end
end

local function pick_review(text)
	local s = assert(review.active())
	local entries, items, targets = s.entries, {}, {}
	local skipped = 0
	local function add(label, target)
		table.insert(targets, target)
		table.insert(items, #targets .. "\t" .. label)
	end
	for index, entry in ipairs(entries) do
		local label = "[" .. entry.group .. "] " .. vim.fn.strtrans(entry.path)
		if not text then
			add(label, { index = index })
		else
			local ok, snapshot = pcall(git.snapshot, s.root, entry)
			if ok then
				for _, side in ipairs({ "old", "new" }) do
					for line, content in ipairs(git.lines(snapshot[side])) do
						add(string.format("%s:%d (%s)  %s", label, line, side, vim.fn.strtrans(content)), {
							index = index,
							side = side,
							line = line,
							snapshot = snapshot,
						})
					end
				end
			else
				skipped = skipped + 1
			end
		end
	end
	if skipped > 0 then
		vim.notify("Search skipped " .. skipped .. " unsupported/unreadable files", vim.log.levels.WARN)
	end
	require("fzf-lua").fzf_exec(items, {
		prompt = text and "Review text> " or "Changed files> ",
		previewer = false,
		fzf_opts = { ["--delimiter"] = "\t", ["--with-nth"] = "2..", ["--no-multi"] = true },
		actions = {
			["enter"] = function(selected)
				if not selected[1] then
					return
				end
				local ok, err = pcall(function()
					assert(review.active() == s and s.entries == entries, "Review changed; reopen the picker")
					local target = assert(targets[tonumber(selected[1]:match("^(%d+)"))])
					if target.snapshot then
						git.validate(s.root, target.snapshot)
					end
					review.show(target.index)
					if target.side then
						assert(s.current.id == target.snapshot.id, "Search result changed; search again")
						if target.side == "old" and not s.old_win then
							review.view("split")
						end
						api.nvim_set_current_win(target.side == "old" and s.old_win or s.new_win)
						api.nvim_win_set_cursor(0, { target.line, 0 })
						vim.cmd("normal! zz")
					end
				end)
				if not ok then
					vim.notify(tostring(err), vim.log.levels.ERROR)
				end
			end,
		},
	})
end

local function project_options()
	local ok, root = pcall(git.root)
	return {
		cwd = ok and root or vim.fn.getcwd(),
		-- Default file actions can otherwise replace the explorer itself.
		actions = {
			["enter"] = function(selected, opts)
				if vim.bo.filetype == "neo-tree" then
					vim.cmd("wincmd l")
				end
				require("fzf-lua").actions.file_edit(selected, opts)
			end,
		},
	}
end

function M.files()
	if review.active() then
		return pick_review(false)
	end
	local opts = project_options()
	opts.cmd = "rg --files --hidden -g '!.git'"
	require("fzf-lua").files(opts)
end

function M.text()
	if review.active() then
		return pick_review(true)
	end
	local opts = project_options()
	opts.rg_opts =
		"--hidden --glob=!.git --column --line-number --no-heading --color=always --smart-case --max-columns=4096 -e"
	require("fzf-lua").grep_project(opts)
end

return M
