local tests = vim.fn.fnamemodify(debug.getinfo(1, "S").source:sub(2), ":p:h")

return function(equal)
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
	end, debug.traceback)
	vim.fn.jobstop(child)
	vim.fn.jobwait({ child }, 1000)
	_G.myeditor_input_results = nil
	if root then
		vim.fn.delete(root, "rf")
	end
	assert(ok, err)
end
