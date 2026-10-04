local M = {}
local api = vim.api
local timers = {}

function M.ready(s)
	local win = api.nvim_get_current_win()
	return require("myeditor.review").active() == s
		and not s.composer
		and vim.fn.mode() == "n"
		and (win == s.tree_win or win == s.old_win or win == s.new_win)
		and not require("myeditor.feedback").busy(s.root)
end

-- Porcelain v2 includes HEAD/index object IDs. Stat changed worktree paths too:
-- editing an already-modified file does not change its Git status letters.
local function fingerprint(root, output)
	local parts = { output }
	for record in output:gmatch("[^%z]+") do
		local kind = record:sub(1, 1)
		local path
		if kind == "?" then
			path = record:sub(3)
		elseif kind == "1" or kind == "2" then
			path = record:match("^" .. ("%S+ "):rep(kind == "1" and 8 or 9) .. "(.*)$")
		end
		if path then
			local stat = vim.uv.fs_lstat(root .. "/" .. path)
			if stat then
				table.insert(parts, vim.json.encode({ stat.size, stat.mode, stat.mtime, stat.ctime }))
			end
		end
	end
	return vim.fn.sha256(table.concat(parts, "\0"))
end

function M.stop(s)
	local timer = timers[s]
	if timer then
		timer:stop()
		timer:close()
		timers[s] = nil
		s.live_refresh = nil
	end
end

function M.start(s)
	if #api.nvim_list_uis() == 0 then
		return
	end
	local timer = assert(vim.uv.new_timer())
	timers[s] = timer
	s.live_refresh = true
	local running, previous, last_error = false, nil, nil
	timer:start(
		0,
		1000,
		vim.schedule_wrap(function()
			if running or timers[s] ~= timer or not M.ready(s) then
				return
			end
			running = true
			vim.system({
				"git",
				"--no-optional-locks",
				"-C",
				s.root,
				"status",
				"--porcelain=v2",
				"--branch",
				"-z",
				"--untracked-files=all",
			}, { text = false, timeout = 5000 }, function(result)
				vim.schedule(function()
					if timers[s] ~= timer or not M.ready(s) then
						running = false
						return
					end
					local ok, err = pcall(function()
						assert(result.code == 0, vim.trim(result.stderr or "Git status failed"))
						local signature = fingerprint(s.root, result.stdout)
						if signature ~= previous and require("myeditor.review").refresh_live() then
							previous = signature
						end
					end)
					running = false
					if not ok and tostring(err) ~= last_error then
						vim.notify("Auto-refresh paused: " .. tostring(err), vim.log.levels.WARN)
					end
					last_error = not ok and tostring(err) or nil
				end)
			end)
		end)
	)
end

return M
