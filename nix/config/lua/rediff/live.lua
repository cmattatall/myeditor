local M = {}
local api = vim.api
local watchers = {}

function M.ready(s)
	local win = api.nvim_get_current_win()
	return require("rediff.review").active() == s
		and not s.composer
		and not s.annotation_id
		and vim.fn.mode() == "n"
		and (win == s.tree_win or win == s.old_win or win == s.new_win or require("rediff.harness").in_message(s.root))
		and not require("rediff.feedback").busy(s.root)
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
	local watcher = watchers[s]
	if watcher then
		for _, key in ipairs({ "timer", "event_timer" }) do
			if watcher[key] then
				watcher[key]:stop()
				watcher[key]:close()
			end
		end
		watchers[s] = nil
	end
	s.live_refresh = nil
end

function M.changed(root)
	local s = require("rediff.review").state
	local watcher = s and s.root == root and watchers[s]
	if not watcher then
		return
	end
	watcher.requested = watcher.requested + 1
	if not watcher.event_timer then
		watcher.event_timer = assert(vim.uv.new_timer())
		-- Coalesce bursts; retain the request while typing, composing, or delivering.
		watcher.event_timer:start(100, 200, vim.schedule_wrap(watcher.check))
	end
end

function M.start(s)
	-- Replace timers so restarting cannot duplicate polling or queued event checks.
	M.stop(s)
	if #api.nvim_list_uis() == 0 then
		return
	end
	local interval = require("rediff.feedback").settings().review_refresh_interval
	if interval == nil then
		interval = 3
	end
	if type(interval) ~= "number" or interval < 0 or interval % 1 ~= 0 then
		vim.notify(
			"review_refresh_interval must be a non-negative whole number of seconds (0 disables polling).",
			vim.log.levels.WARN
		)
		return
	end
	local watcher = { requested = 0, applied = 0 }
	watchers[s] = watcher
	local running, previous, last_error = false, nil, nil
	watcher.check = function()
		if running or watchers[s] ~= watcher or not M.ready(s) then
			return
		end
		running = true
		local requested = watcher.requested
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
				if watchers[s] ~= watcher or not M.ready(s) then
					running = false
					return
				end
				local ok, err = pcall(function()
					assert(result.code == 0, vim.trim(result.stderr or "Git status failed"))
					local signature = fingerprint(s.root, result.stdout)
					if
						(signature ~= previous or requested > watcher.applied)
						and require("rediff.review").refresh_live()
					then
						previous = signature
						watcher.applied = requested
					end
				end)
				running = false
				if not ok and tostring(err) ~= last_error then
					vim.notify("Auto-refresh paused: " .. tostring(err), vim.log.levels.WARN)
				end
				last_error = not ok and tostring(err) or nil
				if watchers[s] == watcher and watcher.event_timer and watcher.applied == watcher.requested then
					watcher.event_timer:stop()
					watcher.event_timer:close()
					watcher.event_timer = nil
				end
			end)
		end)
	end
	if interval > 0 then
		watcher.timer = assert(vim.uv.new_timer())
		s.live_refresh = true
		watcher.timer:start(0, interval * 1000, vim.schedule_wrap(watcher.check))
	end
end

return M
