local tests = vim.fn.fnamemodify(debug.getinfo(1, "S").source:sub(2), ":p:h")

return function(equal)
	local suffix = require("rediff.cmdline").suffix
	for _, case in ipairs({
		{ "h", "elp" },
		{ "ha", "rness" },
		{ "Har", "ness" },
		{ "help", "" },
		{ "harness", "" },
		{ "ReviewRe", "fresh" },
		{ "Harness op", "en" },
		{ "Harness se", "" }, -- send was removed; :harness open takes interactive input.
		{ "Harness connect a", "mp" },
		{ "Harness install ", "amp" },
		{ "Harness connect amp ", "" },
		{ "Harness use a", "mp" },
		{ "Harness use c", "laude" },
		{ "workt", "ree" },
		{ "Worktree sw", "itch" },
		{ "Focus st", "aged" },
		{ "Focus u", "nstaged" },
		{ "Focus unstaged", "" },
		{ "edit some/path", "" },
		{ "!h", "" },
		{ "s/h/ha/", "" },
		{ "", "" },
	}) do
		equal(case[2], suffix(case[1]), "Command hint for " .. case[1])
	end
	-- feedkeys(..., "x") flushes pending mappings and conceals prefix timeouts.
	-- Send actual input to a separate event loop with a deliberately long timeout.
	local child = vim.fn.jobstart({ vim.fn.exepath("rediff"), "--embed", "--headless", "-i", "NONE" }, { rpc = true })
	assert(child > 0, "Could not start input-test editor")
	local root
	local function lua(code, ...)
		return vim.rpcrequest(child, "nvim_exec_lua", code, { ... })
	end
	local word_keys = {
		{ "<M-Left>", "<M-Right>" },
		{ "<M-Up>", "<M-Down>" },
		{ "<M-h>", "<M-l>" },
		{ "<M-k>", "<M-j>" },
		{ "<M-b>", "<M-f>" },
	}
	local function word_shortcuts(context)
		for _, pair in ipairs(word_keys) do
			lua([[
				vim.api.nvim_buf_set_lines(0, 0, -1, false, {"  alpha beta  gamma", "tail"})
				vim.api.nvim_win_set_cursor(0, {1, 8})
			]])
			vim.rpcnotify(child, "nvim_input", pair[1])
			equal(
				true,
				vim.wait(1000, function()
					return lua([[return vim.fn.mode() == "n" and vim.api.nvim_win_get_cursor(0)[2] == 2]])
				end, 10),
				context .. ": " .. pair[1] .. " moves backward by a word"
			)
			vim.rpcnotify(child, "nvim_input", "v" .. pair[2])
			equal(
				true,
				vim.wait(1000, function()
					return lua([[return vim.fn.mode() == "v" and vim.api.nvim_win_get_cursor(0)[2] == 8]])
				end, 10),
				context .. ": " .. pair[2] .. " extends the selection by a word"
			)
			vim.rpcnotify(child, "nvim_input", "<Esc>i" .. pair[1] .. "^" .. pair[2] .. "!<Esc>")
			equal(
				true,
				vim.wait(1000, function()
					return lua(
						[[return vim.fn.mode() == "n" and vim.api.nvim_get_current_line() == "  ^alpha !beta  gamma"]]
					)
				end, 10),
				context .. ": Option movement stays in Insert mode and does not insert special characters"
			)
			equal(
				{ "  ^alpha !beta  gamma", "tail" },
				lua([[return vim.api.nvim_buf_get_lines(0, 0, -1, false)]]),
				context .. ": Option movement does not change lines"
			)
		end
	end
	local function line_shortcuts(context)
		lua(
			[[vim.api.nvim_buf_set_lines(0, 0, -1, false, {"  alpha", "tail"}); vim.api.nvim_win_set_cursor(0, {1, 4})]]
		)
		vim.rpcnotify(child, "nvim_input", "<Esc>i<C-a>")
		equal(
			true,
			vim.wait(1000, function()
				return lua([[return vim.fn.mode() == "i" and vim.api.nvim_win_get_cursor(0)[2] == 0]])
			end, 10),
			context .. ": Ctrl-a moves before leading whitespace without leaving Insert mode"
		)
		vim.rpcnotify(child, "nvim_input", "^<C-e>!<Esc>")
		equal(
			true,
			vim.wait(1000, function()
				return lua([[return vim.fn.mode() == "n" and vim.api.nvim_get_current_line() == "^  alpha!"]])
			end, 10),
			context .. ": Ctrl-e moves after the last character"
		)
		equal(
			{ "^  alpha!", "tail" },
			lua([[return vim.api.nvim_buf_get_lines(0, 0, -1, false)]]),
			context .. ": shortcuts do not insert prior text or copy from the next line"
		)
	end
	local ok, err = xpcall(function()
		local channel = vim.rpcrequest(child, "nvim_get_api_info")[1]
		_G.rediff_input_results = {}
		root = lua([[local root = dofile(...).create(); vim.cmd.cd(root); return root]], tests .. "/fixture.lua")
		lua(
			[[
			local channel = ...
			vim.o.timeoutlen = 10000
			local r = require("rediff.review")
			r.open()
			r.jump_hunk = function(direction)
				vim.rpcnotify(channel, "nvim_exec_lua", "table.insert(_G.rediff_input_results, ...)", {direction})
			end
		]],
			channel
		)
		local expected = {}
		for pass = 1, 2 do
			lua([=[
				local r = require("rediff.review")
				r.show(1)
				r.show(#r.state.entries) -- Markdown installs buffer-local [[ and ]].
			]=])
			for _, pane in ipairs({ "new_win", "old_win", "tree_win" }) do
				lua([[vim.api.nvim_set_current_win(require("rediff.review").state[...])]], pane)
				for _, input in ipairs({ { "[", -1, 1 }, { "]", 1, 1 }, { "3]", 1, 3 } }) do
					for _ = 1, input[3] do
						table.insert(expected, input[2])
					end
					vim.rpcnotify(child, "nvim_input", input[1])
					local immediate = vim.wait(1000, function()
						return #_G.rediff_input_results == #expected
					end, 10)
					equal(
						true,
						immediate,
						input[1] .. " dispatches before timeout in " .. pane .. " on filetype pass " .. pass
					)
					equal(expected, _G.rediff_input_results, "Immediate mapping preserves direction and count")
				end
			end
		end
		lua([[vim.api.nvim_set_current_win(require("rediff.review").state.new_win)]])
		for _, move in ipairs({
			{ "<D-w>h", "old_win" },
			{ "<D-w>h", "tree_win" },
			{ "<D-w>l", "old_win" },
			{ "v<D-w>l", "new_win" },
		}) do
			vim.rpcnotify(child, "nvim_input", move[1])
			equal(
				true,
				vim.wait(1000, function()
					return lua(
						[=[return vim.api.nvim_get_current_win() == require("rediff.review").state[...]]=],
						move[2]
					)
				end, 10),
				"Cmd-w moves focus to " .. move[2]
			)
		end
		equal("n", lua([[return vim.fn.mode()]]), "Cmd-w leaves Visual mode before moving focus")
		for _, prefix in ipairs({ "", "v" }) do
			lua([[vim.g.window_input_done = false]])
			vim.rpcnotify(child, "nvim_input", prefix .. "<C-w>h<Esc>:let g:window_input_done = v:true<CR>")
			equal(
				true,
				vim.wait(1000, function()
					return lua([[return vim.g.window_input_done]])
				end, 10),
				"Ctrl-w input finishes without a window prefix"
			)
			equal(
				true,
				lua([[return vim.api.nvim_get_current_win() == require("rediff.review").state.new_win]]),
				"Ctrl-w does not navigate windows in " .. (prefix == "" and "Normal" or "Visual") .. " mode"
			)
		end
		for _, pane in ipairs({ "new_win", "old_win" }) do
			lua([[vim.api.nvim_set_current_win(require("rediff.review").state[...])]], pane)
			vim.rpcnotify(child, "nvim_input", ":ft<CR>")
			equal(
				true,
				vim.wait(1000, function()
					return lua([[return vim.api.nvim_get_current_win() == require("rediff.review").state.tree_win]])
				end, 10),
				"Typed ft focuses Review tree from " .. pane
			)
		end
		lua([[local r=require("rediff.review"); vim.api.nvim_set_current_win(r.state.new_win); r.compose(false)]])
		line_shortcuts("Annotation")
		word_shortcuts("Annotation")
		vim.rpcnotify(child, "nvim_input", ":ft<CR>")
		equal(
			true,
			vim.wait(1000, function()
				return lua([[return vim.api.nvim_get_current_win() == require("rediff.review").state.tree_win]])
			end, 10),
			"Typed ft focuses Review tree from an annotation"
		)
		equal(0, lua([[return #require("rediff.review").state.comments]]), "ft neither saves nor submits an annotation")
		lua([[require("rediff.review").leave(); vim.cmd("edit plan.md")]])
		equal("", lua([[return vim.fn.maparg("[", "n")]]), "Ordinary Markdown has no bare Review mapping")
		equal(
			1,
			lua([[return vim.fn.maparg("[[", "n", false, true).buffer]]),
			"Ordinary Markdown retains section mappings"
		)
		line_shortcuts("Ordinary file")
		word_shortcuts("Ordinary file")
		vim.rpcnotify(child, "nvim_input", ":ft<CR>")
		equal(
			true,
			vim.wait(1000, function()
				return lua([[return vim.bo.filetype == "neo-tree"]])
			end, 10),
			"Typed ft focuses filesystem tree outside Review"
		)
		lua([[vim.cmd("Neotree close")]])
		equal("", lua([[return vim.fn.maparg("hs", "c", true)]]), "The removed hs alias is not abbreviated")
		equal(
			false,
			vim.tbl_contains(lua([[return vim.fn.getcompletion("Harness ", "cmdline")]]), "send"),
			"Harness completion no longer offers send"
		)
		local function hint()
			return lua([[
				for _, win in ipairs(vim.api.nvim_list_wins()) do
					if vim.wo[win].winhighlight:find("RediffCommandHint", 1, true) then
						return table.concat(vim.api.nvim_buf_get_lines(vim.api.nvim_win_get_buf(win), 0, -1, false), "\n")
					end
				end
				return ""
			]])
		end
		local function input(keys, line, ghost, pos)
			vim.rpcnotify(child, "nvim_input", keys)
			equal(
				true,
				vim.wait(2000, function()
					return lua([[return vim.fn.getcmdline()]]) == line
						and hint() == ghost
						and (not pos or lua([[return vim.fn.getcmdpos()]]) == pos)
				end, 10),
				"Input " .. keys .. " leaves real text " .. line .. " and hint " .. ghost
			)
		end
		input(":h", "h", "elp")
		input("a", "ha", "rness")
		input("<Left>", "ha", "")
		input("<End>", "ha", "rness")
		input("<BS>", "h", "elp")
		input("a<Tab>", "Harness", "")
		input(" op", "Harness op", "en")
		input("<Tab>", "Harness open", "")
		input("<C-C>", "", "")
		input("/ha", "ha", "")
		input("<C-C>", "", "")
		input(":edit auth.l<Tab>", "edit auth.lua", "")
		input("<C-C>", "", "")
		input(":echo 'tail'", "echo 'tail'", "")
		input("<C-a>", "echo 'tail'", "", 1)
		input("<C-e>", "echo 'tail'", "", 12)
		input("<C-C>", "", "")
		input(":set ft=lua ", "set ft=lua ", "")
		input("<C-C>", "", "")
		input("/ft ", "ft ", "")
		input("<C-a>", "ft ", "", 1)
		input("<C-e>", "ft ", "", 4)
		input("<C-C>", "", "")
		for _, prefix in ipairs({ ":", "/" }) do
			for _, pair in ipairs(word_keys) do
				input(prefix .. "echo alpha beta", "echo alpha beta", "", 16)
				input(pair[1], "echo alpha beta", "", 12)
				input(pair[1], "echo alpha beta", "", 6)
				-- Command-line forward-word stops after alpha, before its following space.
				input(pair[2], "echo alpha beta", "", 11)
				input(pair[2], "echo alpha beta", "", 16)
				input("<C-C>", "", "")
			end
		end
		input(":h", "h", "elp")
		input("<CR>", "", "")
		equal("h", lua([[return vim.fn.histget("cmd", -1)]]), "Enter does not accept ghost text")
		equal("help", lua([[return vim.bo.buftype]]), "Native h abbreviation still opens help")
	end, debug.traceback)
	vim.fn.jobstop(child)
	vim.fn.jobwait({ child }, 1000)
	_G.rediff_input_results = nil
	if root then
		vim.fn.delete(root, "rf")
	end
	assert(ok, err)
end
