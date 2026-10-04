local M = {}
local api = vim.api
local win, buf
local namespace = api.nvim_create_namespace("myeditor.cmdline")

local function hide()
	if win and api.nvim_win_is_valid(win) then
		api.nvim_win_close(win, true)
		vim.cmd.redraw()
	end
	win = nil
end

function M.suffix(line)
	local lead, matches
	if line:match("^%a+$") then
		for _, preferred in ipairs({ "help", "harness", "worktree" }) do
			if vim.startswith(preferred, line) then
				return preferred:sub(#line + 1)
			end
		end
		lead, matches = line, vim.fn.getcompletion(line, "command")
	elseif line:match("^Harness%s+[%a%s]*$") or line:match("^Focus%s+%a*$") or line:match("^Worktree%s+%a*$") then
		lead, matches = line:match("(%a*)$"), vim.fn.getcompletion(line, "cmdline")
	else
		return ""
	end
	for _, match in ipairs(matches) do
		if match == lead then
			return ""
		end
	end
	for _, match in ipairs(matches) do
		if vim.startswith(match, lead) then
			return match:sub(#lead + 1)
		end
	end
	return ""
end

local function suggestion()
	local line = vim.fn.getcmdline()
	if vim.fn.getcmdtype() ~= ":" or vim.fn.getcmdpos() ~= #line + 1 or vim.fn.wildmenumode() == 1 then
		return ""
	end
	return M.suffix(line)
end

local function update()
	local suffix = suggestion()
	local col = vim.fn.strdisplaywidth(vim.fn.getcmdline()) + 1
	if suffix == "" or col + #suffix >= vim.o.columns then
		hide()
		return
	end
	if not buf then
		buf = api.nvim_create_buf(false, true)
		vim.bo[buf].bufhidden = "hide"
	end
	api.nvim_buf_set_lines(buf, 0, -1, false, { suffix })
	local config = {
		relative = "editor",
		row = vim.o.lines - math.max(1, vim.o.cmdheight),
		col = col,
		width = #suffix,
		height = 1,
		style = "minimal",
		focusable = false,
		mouse = false,
		zindex = 250,
		noautocmd = true,
	}
	if win and api.nvim_win_is_valid(win) then
		api.nvim_win_set_config(win, config)
	else
		win = api.nvim_open_win(buf, false, config)
		vim.wo[win].winhighlight = "Normal:MyeditorCommandHint,NormalFloat:MyeditorCommandHint"
	end
	vim.cmd.redraw()
end

function M.setup()
	local function highlight()
		api.nvim_set_hl(0, "MyeditorCommandHint", { link = "Comment" })
	end
	highlight()
	api.nvim_create_autocmd("ColorScheme", { callback = highlight })
	local scheduled = false
	local function schedule()
		if not scheduled then
			scheduled = true
			vim.schedule(function()
				scheduled = false
				update()
			end)
		end
	end
	api.nvim_create_autocmd({ "CmdlineChanged", "CmdlineEnter", "VimResized" }, { callback = schedule })
	api.nvim_create_autocmd("CmdlineLeave", { callback = hide })
	-- Cursor movement does not fire CmdlineChanged. Recheck after the key is handled.
	vim.on_key(function()
		if vim.fn.getcmdtype() ~= "" then
			schedule()
		end
	end, namespace)
	vim.o.wildcharm = 9 -- Mapped Tab must still invoke native wildcard completion.
	vim.keymap.set("c", "<Tab>", function()
		local suffix = suggestion()
		return suffix ~= "" and (suffix .. "<C-]>") or "<Tab>"
	end, { expr = true, desc = "Accept command hint or complete normally" })
end

return M
