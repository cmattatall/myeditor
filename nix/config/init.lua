vim.opt.runtimepath:prepend(assert(vim.env.MYEDITOR_RUNTIME, "Use the myeditor launcher"))
for path in (vim.env.MYEDITOR_PLUGINS or ""):gmatch("[^,]+") do
	vim.opt.runtimepath:append(path)
end

vim.g.mapleader = " "
vim.g.maplocalleader = " "
-- Use review.nvim's renderers without its file-buffer lifecycle or commands.
vim.g.loaded_review = true
vim.opt.number = true
vim.opt.termguicolors = true
vim.opt.signcolumn = "yes"
vim.opt.splitright = true
vim.opt.splitbelow = true
vim.opt.ignorecase = true
vim.opt.smartcase = true
vim.opt.updatetime = 250
vim.opt.completeopt = { "menuone", "noselect" }
vim.opt.expandtab = true
vim.opt.shiftwidth = 2
vim.opt.tabstop = 2
vim.opt.autowrite = false
vim.opt.autowriteall = false
require("rose-pine").setup({ variant = "main", dark_variant = "main" })
vim.cmd.colorscheme("rose-pine")
vim.fn.mkdir(vim.fn.stdpath("data"), "p")

vim.keymap.set("n", "<D-w>", "<C-w>", { desc = "Window command prefix (Cmd-w)" })
vim.keymap.set({ "i", "x" }, "<D-w>", "<Esc><C-w>", { desc = "Leave editing/selection for window command" })
vim.keymap.set({ "n", "x" }, "<C-w>", "<Nop>", { desc = "Use Cmd-w for window commands" })
vim.keymap.set("i", "<C-a>", "<Home>", { desc = "Start of line" })
vim.keymap.set("i", "<C-e>", "<End>", { desc = "End of line" })
vim.keymap.set("c", "<C-a>", "<C-b>", { desc = "Start of command line" })
for _, key in ipairs({ "<M-Left>", "<M-Up>", "<M-h>", "<M-k>", "<M-b>" }) do
	vim.keymap.set({ "n", "x", "i", "c" }, key, "<C-Left>", { desc = "Previous word" })
end
for _, key in ipairs({ "<M-Right>", "<M-Down>", "<M-l>", "<M-j>", "<M-f>" }) do
	vim.keymap.set({ "n", "x", "i", "c" }, key, "<C-Right>", { desc = "Next word" })
end

local palette = require("rose-pine.palette")
vim.api.nvim_set_hl(0, "MyeditorHelpBorder", { fg = palette.iris, bg = palette.surface })
vim.api.nvim_create_autocmd({ "FileType", "BufWinEnter" }, {
	callback = function(event)
		if vim.bo[event.buf].filetype ~= "help" or vim.api.nvim_get_current_buf() ~= event.buf then
			return
		end
		local width = math.max(1, math.min(90, vim.o.columns - 4))
		local height = math.max(1, math.min(32, vim.o.lines - 6))
		vim.api.nvim_win_set_config(0, {
			relative = "editor",
			row = math.max(0, math.floor((vim.o.lines - height - 2) / 2)),
			col = math.max(0, math.floor((vim.o.columns - width - 2) / 2)),
			width = width,
			height = height,
			style = "minimal",
			border = "single",
			title = " Help · ?/Esc/q close ",
			title_pos = "center",
		})
		vim.wo.winhighlight = "FloatBorder:MyeditorHelpBorder"
		vim.wo.wrap = true
		vim.wo.linebreak = true
		for _, key in ipairs({ "?", "q", "<Esc>" }) do
			vim.keymap.set("n", key, "<Cmd>close<CR>", { buffer = event.buf, desc = "Close help overlay" })
		end
	end,
})
vim.keymap.set("n", "?", "<Cmd>help myeditor<CR>", { desc = "Editor help overlay" })

require("codediff").setup({
	diff = { compute_moves = false, highlight_priority = 150 },
	highlights = {
		line_insert = "#28683e",
		line_delete = "#c62828",
		char_insert = "#58a46b",
		char_delete = "#ff5252",
	},
})
local function diff_contrast()
	-- Syntax colors cannot stay readable against both vivid red and green fills.
	for _, kind in ipairs({ "Line", "Char" }) do
		for _, side in ipairs({ "Insert", "Delete" }) do
			local name = "CodeDiff" .. kind .. side
			local highlight = vim.api.nvim_get_hl(0, { name = name })
			highlight.fg = kind == "Line" and "#ffffff" or "#101010"
			highlight.nocombine = true
			vim.api.nvim_set_hl(0, name, highlight)
		end
	end
end
-- CodeDiff's plugin entry point resets these groups after init.lua and on theme changes.
vim.api.nvim_create_autocmd({ "VimEnter", "ColorScheme" }, { callback = vim.schedule_wrap(diff_contrast) })
require("review.config").setup({ export = { clipboard = false, clear_on_close = false } })
require("review.highlights").setup()
require("neo-tree").setup({
	filesystem = { hijack_netrw_behavior = "disabled" },
	window = {
		width = 28,
		mappings = {
			["?"] = function()
				vim.cmd("help myeditor")
			end,
		},
	},
})
vim.api.nvim_create_autocmd("VimEnter", {
	once = true,
	callback = vim.schedule_wrap(function()
		if #vim.api.nvim_list_uis() > 0 then
			if pcall(require("myeditor.git").root) then
				local ok, err = pcall(require("myeditor.review").open)
				if ok then
					return
				end
				vim.notify("Could not open Review: " .. tostring(err), vim.log.levels.WARN)
			end
			require("neo-tree.command").execute({ action = "show", source = "filesystem", position = "left" })
		end
	end),
})

local review = require("myeditor.review")
review.setup()
require("myeditor.harness").setup()
require("myeditor.worktree").setup()
require("myeditor.cmdline").setup()
require("fzf-lua").setup({
	winopts = { width = 0.85, height = 0.8, preview = { hidden = true } },
	keymap = { fzf = { ["esc"] = "abort" } },
	files = { file_icons = false, git_icons = false },
	grep = { file_icons = false, git_icons = false },
})
local navigation = require("myeditor.navigation")
for _, binding in ipairs({
	{ "<leader>e", "Explorer", navigation.explorer, "Focus explorer" },
	{ "<leader>d", "FocusDiff", navigation.diff, "Focus diff/editor" },
	{ "<leader>f", "Files", navigation.files, "Fuzzy find files" },
	{ "<leader>/", "Search", navigation.text, "Fuzzy find text" },
}) do
	vim.keymap.set("n", binding[1], binding[3], { desc = binding[4] })
	vim.api.nvim_create_user_command(binding[2], binding[3], {})
end
vim.cmd("cnoreabbrev <expr> ft getcmdtype() == ':' && getcmdline() == 'ft' && getcmdpos() == 3 ? 'Explorer' : 'ft'")
vim.keymap.set("n", "<leader>r", review.open, { desc = "Enter Review workspace" })
vim.keymap.set("n", "<leader>E", function()
	if review.active() then
		navigation.explorer()
	else
		vim.cmd("Neotree toggle filesystem")
	end
end, { desc = "Toggle file tree (focus in Review)" })
vim.opt.laststatus = 3
vim.opt.statusline = "%{%v:lua.require('myeditor.review').statusline()%}"
