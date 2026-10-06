vim.opt.runtimepath:prepend(assert(vim.env.REDIFF_RUNTIME, "Use the rediff launcher"))
for path in (vim.env.REDIFF_PLUGINS or ""):gmatch("[^,]+") do
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
vim.api.nvim_set_hl(0, "RediffHelpBorder", { fg = palette.iris, bg = palette.surface })
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
		vim.wo.winhighlight = "FloatBorder:RediffHelpBorder"
		vim.wo.wrap = true
		vim.wo.linebreak = true
		for _, key in ipairs({ "?", "q", "<Esc>" }) do
			vim.keymap.set("n", key, "<Cmd>close<CR>", { buffer = event.buf, desc = "Close help overlay" })
		end
	end,
})
vim.keymap.set("n", "?", "<Cmd>help rediff<CR>", { desc = "Editor help overlay" })

require("codediff").setup({
	-- Blank filler rows preserve alignment without rendering wide patterns per line.
	diff = { compute_moves = false, highlight_priority = 150, filler_text = "" },
	highlights = {
		line_insert = "#24352f",
		line_delete = "#3b2833",
		char_insert = "#354e40",
		char_delete = "#593743",
	},
})
require("review.config").setup({ export = { clipboard = false, clear_on_close = false } })
require("review.highlights").setup()
require("neo-tree").setup({
	filesystem = { hijack_netrw_behavior = "disabled" },
	window = {
		width = 28,
		mappings = {
			["?"] = function()
				vim.cmd("help rediff")
			end,
		},
	},
})
vim.api.nvim_create_autocmd("VimEnter", {
	once = true,
	callback = vim.schedule_wrap(function()
		if #vim.api.nvim_list_uis() == 0 or vim.fn.argc() > 0 or not pcall(require("rediff.git").root) then
			return
		end
		local ok, err = pcall(require("rediff.review").open)
		if ok then
			return
		end
		vim.notify("Could not open Review: " .. tostring(err), vim.log.levels.WARN)
		require("neo-tree.command").execute({ action = "show", source = "filesystem", position = "left" })
	end),
})

local review = require("rediff.review")
vim.lsp.config("lua_ls", { settings = { Lua = { workspace = { checkThirdParty = false } } } })
vim.lsp.enable({ "lua_ls", "nil_ls", "pyright", "ts_ls", "gopls", "rust_analyzer" })
review.setup()
require("rediff.harness").setup()
require("rediff.worktree").setup()
require("rediff.cmdline").setup()
require("fzf-lua").setup({
	winopts = { width = 0.85, height = 0.8, preview = { hidden = true } },
	keymap = { fzf = { ["esc"] = "abort" } },
	files = { file_icons = false, git_icons = false },
	grep = { file_icons = false, git_icons = false },
})
local navigation = require("rediff.navigation")
for _, binding in ipairs({
	{ "<leader>e", "Explorer", navigation.explorer, "Focus explorer" },
	{ "<leader>d", "FocusDiff", navigation.diff, "Focus diff/editor" },
	{ "<leader>f", "Files", navigation.files, "Fuzzy find files" },
	{ "<leader>/", "Search", navigation.text, "Fuzzy find text" },
	{
		"<leader>p",
		"Commands",
		function()
			if vim.bo.filetype == "help" and vim.api.nvim_win_get_config(0).relative ~= "" then
				vim.cmd.close()
			end
			require("fzf-lua").commands()
		end,
		"Find commands",
	},
}) do
	vim.keymap.set("n", binding[1], binding[3], { desc = binding[4] })
	vim.api.nvim_create_user_command(binding[2], binding[3], {})
end
vim.cmd("cnoreabbrev <expr> ft getcmdtype() == ':' && getcmdline() == 'ft' && getcmdpos() == 3 ? 'Explorer' : 'ft'")
vim.keymap.set("n", "<leader>r", function()
	if review.active() then
		review.leave()
	else
		review.open()
	end
end, { desc = "Toggle Review workspace" })
vim.keymap.set("n", "<leader>E", function()
	if review.active() then
		navigation.explorer()
	else
		vim.cmd("Neotree toggle filesystem")
	end
end, { desc = "Toggle file tree (focus in Review)" })
vim.opt.laststatus = 3
vim.opt.statusline = "%{%v:lua.require('rediff.review').statusline()%}"
