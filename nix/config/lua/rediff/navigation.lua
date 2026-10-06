local M = {}
local api = vim.api
local review = require("rediff.review")
local git = require("rediff.git")
local definition_generation = 0

function M.definition()
	local s = assert(review.active(), "Enter Review first")
	local win, buf = api.nvim_get_current_win(), api.nvim_get_current_buf()
	assert(buf == s.old_buf or buf == s.new_buf, "Select a symbol in a Review source pane")
	local snapshot, cursor = s.current, api.nvim_win_get_cursor(win)
	local path = s.root .. "/" .. snapshot.path
	assert(vim.fn.filereadable(path) == 1, "This source file no longer exists in the worktree")
	-- LSP needs a real URI, not review://. Keep the source hidden and leave both diff panes intact.
	local source = vim.fn.bufadd(path)
	vim.fn.bufload(source)
	api.nvim_buf_call(source, function()
		vim.cmd("checktime")
	end)
	local old = table.concat(api.nvim_buf_get_lines(buf, 0, -1, false), "\n") .. "\n"
	local new = table.concat(api.nvim_buf_get_lines(source, 0, -1, false), "\n") .. "\n"
	local offset = 0
	for _, hunk in ipairs(vim.diff(old, new, { result_type = "indices" })) do
		local first, count, _, added = unpack(hunk)
		if count == 0 then
			if first >= cursor[1] then
				break
			end
		elseif first > cursor[1] then
			break
		else
			assert(
				cursor[1] >= first + count,
				"This line changed in the source file; refresh Review or open the current file"
			)
		end
		offset = offset + added - count
	end
	local tick = api.nvim_buf_get_changedtick(source)
	definition_generation = definition_generation + 1
	local generation = definition_generation
	local function current()
		return generation == definition_generation
			and review.active() == s
			and s.current == snapshot
			and api.nvim_get_current_win() == win
			and api.nvim_win_get_buf(win) == buf
			and vim.deep_equal(api.nvim_win_get_cursor(win), cursor)
			and api.nvim_buf_is_valid(source)
			and api.nvim_buf_get_changedtick(source) == tick
	end
	local function request()
		-- The pinned Neovim requests definitions from the current buffer/cursor.
		api.nvim_buf_call(source, function()
			local view = vim.fn.winsaveview()
			api.nvim_win_set_cursor(0, { cursor[1] + offset, cursor[2] })
			vim.lsp.buf.definition({
				on_list = function(list)
					if not current() then
						return
					end
					vim.cmd.tabnew()
					vim.fn.setqflist({}, " ", list)
					vim.cmd.cfirst()
					if #list.items > 1 then
						vim.cmd.copen()
					end
				end,
			})
			vim.fn.winrestview(view)
		end)
	end
	if #vim.lsp.get_clients({ bufnr = source, method = "textDocument/definition" }) > 0 then
		return request()
	end
	-- First lookup may have to wait for the server's asynchronous initialization.
	local waiting = true
	local attach = api.nvim_create_autocmd("LspAttach", {
		buffer = source,
		callback = function()
			vim.schedule(function()
				if
					waiting
					and current()
					and #vim.lsp.get_clients({ bufnr = source, method = "textDocument/definition" }) > 0
				then
					waiting = false
					request()
				end
			end)
		end,
	})
	api.nvim_exec_autocmds("FileType", { buffer = source })
	vim.notify("Starting definition lookup; waiting for the language server…")
	vim.defer_fn(function()
		pcall(api.nvim_del_autocmd, attach)
		if waiting and current() then
			vim.notify(
				"No definition-capable language server attached. See :checkhealth vim.lsp and try again.",
				vim.log.levels.WARN
			)
		end
		waiting = false
	end, 15000)
end

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
