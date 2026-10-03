local tests = vim.fn.fnamemodify(debug.getinfo(1, "S").source:sub(2), ":p:h")

return function(equal)
	local suffix = require("myeditor.cmdline").suffix
	for _, case in ipairs({
		{ "h", "elp" },
		{ "ha", "rness" },
		{ "Har", "ness" },
		{ "help", "" },
		{ "harness", "" },
		{ "ReviewRe", "fresh" },
		{ "Harness se", "nd" },
		{ "Harness connect a", "mp" },
		{ "Harness install ", "amp" },
		{ "Harness send ", "" },
		{ "Harness connect amp ", "" },
		{ "edit some/path", "" },
		{ "!h", "" },
		{ "s/h/ha/", "" },
		{ "", "" },
	}) do
		equal(case[2], suffix(case[1]), "Command hint for " .. case[1])
	end
	-- feedkeys(..., "x") flushes pending mappings and conceals prefix timeouts.
	-- Send actual input to a separate event loop with a deliberately long timeout.
	local child = vim.fn.jobstart({ vim.fn.exepath("myeditor"), "--embed", "--headless", "-i", "NONE" }, { rpc = true })
	assert(child > 0, "Could not start input-test editor")
	local root
	local function lua(code, ...)
		return vim.rpcrequest(child, "nvim_exec_lua", code, { ... })
	end
	local ok, err = xpcall(function()
		local channel = vim.rpcrequest(child, "nvim_get_api_info")[1]
		_G.myeditor_input_results = {}
		root = lua([[local root = dofile(...).create(); vim.cmd.cd(root); return root]], tests .. "/fixture.lua")
		lua(
			[[
			local channel = ...
			vim.o.timeoutlen = 10000
			local r = require("myeditor.review")
			r.open()
			r.jump_hunk = function(direction)
				vim.rpcnotify(channel, "nvim_exec_lua", "table.insert(_G.myeditor_input_results, ...)", {direction})
			end
		]],
			channel
		)
		local expected = {}
		for pass = 1, 2 do
			lua([=[
				local r = require("myeditor.review")
				r.show(1)
				r.show(#r.state.entries) -- Markdown installs buffer-local [[ and ]].
			]=])
			for _, pane in ipairs({ "new_win", "old_win", "tree_win" }) do
				lua([[vim.api.nvim_set_current_win(require("myeditor.review").state[...])]], pane)
				for _, input in ipairs({ { "[", -1, 1 }, { "]", 1, 1 }, { "3]", 1, 3 } }) do
					for _ = 1, input[3] do
						table.insert(expected, input[2])
					end
					vim.rpcnotify(child, "nvim_input", input[1])
					local immediate = vim.wait(1000, function()
						return #_G.myeditor_input_results == #expected
					end, 10)
					equal(
						true,
						immediate,
						input[1] .. " dispatches before timeout in " .. pane .. " on filetype pass " .. pass
					)
					equal(expected, _G.myeditor_input_results, "Immediate mapping preserves direction and count")
				end
			end
		end
		lua([[require("myeditor.review").leave(); vim.cmd("edit plan.md")]])
		equal("", lua([[return vim.fn.maparg("[", "n")]]), "Ordinary Markdown has no bare Review mapping")
		equal(
			1,
			lua([[return vim.fn.maparg("[[", "n", false, true).buffer]]),
			"Ordinary Markdown retains section mappings"
		)
		for pass = 1, 3 do
			vim.rpcnotify(child, "nvim_input", ":hs<CR>")
			equal(
				true,
				vim.wait(1000, function()
					return lua([[return vim.api.nvim_buf_get_name(0):match("^harness://") ~= nil]])
				end, 10),
				"Typed hs reopens composer on pass " .. pass
			)
			vim.rpcnotify(child, "nvim_input", ":q!<CR>")
			equal(
				true,
				vim.wait(1000, function()
					return lua([[return vim.bo.buftype == ""]])
				end, 10),
				"Composer closes back to file on pass " .. pass
			)
		end
		local function hint()
			return lua([[
				for _, win in ipairs(vim.api.nvim_list_wins()) do
					if vim.wo[win].winhighlight:find("MyeditorCommandHint", 1, true) then
						return table.concat(vim.api.nvim_buf_get_lines(vim.api.nvim_win_get_buf(win), 0, -1, false), "\n")
					end
				end
				return ""
			]])
		end
		local function input(keys, line, ghost)
			vim.rpcnotify(child, "nvim_input", keys)
			equal(
				true,
				vim.wait(2000, function()
					return lua([[return vim.fn.getcmdline()]]) == line and hint() == ghost
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
		input(" se", "Harness se", "nd")
		input("<Tab>", "Harness send", "")
		input("<C-C>", "", "")
		input("/ha", "ha", "")
		input("<C-C>", "", "")
		input(":edit auth.l<Tab>", "edit auth.lua", "")
		input("<C-C>", "", "")
		input(":h", "h", "elp")
		input("<CR>", "", "")
		equal("h", lua([[return vim.fn.histget("cmd", -1)]]), "Enter does not accept ghost text")
		equal("help", lua([[return vim.bo.buftype]]), "Native h abbreviation still opens help")
	end, debug.traceback)
	vim.fn.jobstop(child)
	vim.fn.jobwait({ child }, 1000)
	_G.myeditor_input_results = nil
	if root then
		vim.fn.delete(root, "rf")
	end
	assert(ok, err)
end
