-- Automatic, process-local change awareness. No manual reviewed marks or disk state.
local M = {}
local api = vim.api
local git = require("rediff.git")
local roots = {}
local ns = api.nvim_create_namespace("rediff.awareness")
local timer

local function key(entry)
	return entry.group .. "\0" .. entry.path
end

function M.describe(patch)
	local _, hunks = git.hunks(patch)
	local counts, ids = {}, {}
	for _, hunk in ipairs(hunks) do
		local content = {}
		for _, line in ipairs(hunk.lines) do
			if line:match("^[+%-\\]") then
				table.insert(content, line)
			end
		end
		local hash = vim.fn.sha256(table.concat(content, "\n"))
		counts[hash] = (counts[hash] or 0) + 1
		hunk.id = hash .. ":" .. counts[hash]
		ids[hunk.id] = true
	end
	local ordered = vim.tbl_keys(ids)
	table.sort(ordered)
	return { hunks = hunks, ids = ids, fingerprint = vim.fn.sha256(#hunks > 0 and table.concat(ordered, "\0") or patch) }
end

-- All subprocesses are asynchronous and bounded. Paths come from NUL-delimited
-- status, never patch headers (which may quote spaces, tabs or non-ASCII names).
function M.capture(root, callback)
	local function run(args, done)
		local argv = { "git", "--no-optional-locks", "--literal-pathspecs", "-C", root }
		vim.list_extend(argv, args)
		local ok = pcall(vim.system, argv, { text = false, timeout = 10000 }, function(result)
			vim.schedule(function()
				if result.code ~= 0 then
					callback(nil)
				else
					done(result.stdout)
				end
			end)
		end)
		if not ok then
			callback(nil)
		end
	end
	run({ "-c", "status.renames=false", "status", "--porcelain=v1", "-z", "--untracked-files=all" }, function(output)
		local entries, result = {}, {}
		for record in output:gmatch("[^%z]+") do
			local x, y, path = record:sub(1, 1), record:sub(2, 2), record:sub(4)
			if x == "?" then
				table.insert(entries, { group = "untracked", path = path })
			else
				if x ~= " " then
					table.insert(entries, { group = "staged", path = path })
				end
				if y ~= " " then
					table.insert(entries, { group = "unstaged", path = path })
				end
			end
		end
		local i = 0
		local function next_entry()
			i = i + 1
			local entry = entries[i]
			if not entry then
				callback(result)
				return
			end
			if entry.group == "untracked" then
				local path = root .. "/" .. entry.path
				local stat = vim.uv.fs_lstat(path)
				if stat and stat.type == "file" and stat.size <= 2 * 1024 * 1024 then
					local fd = vim.uv.fs_open(path, "r", 0)
					if fd then
						local text = vim.uv.fs_read(fd, stat.size, 0)
						vim.uv.fs_close(fd)
						if text and not text:find("\0", 1, true) then
							result[key(entry)] = M.describe(vim.diff("", text, { ctxlen = 0 }))
						end
					end
				end
				vim.schedule(next_entry)
				return
			end
			local args = {
				"diff",
				"--no-ext-diff",
				"--no-textconv",
				"--no-renames",
				"--no-color",
				"--diff-algorithm=myers",
				"--unified=0",
				"--inter-hunk-context=0",
			}
			if entry.group == "staged" then
				table.insert(args, "--cached")
			end
			vim.list_extend(args, { "--", entry.path })
			run(args, function(patch)
				result[key(entry)] = M.describe(patch)
				next_entry()
			end)
		end
		next_entry()
	end)
end

function M.get(root)
	roots[root] = roots[root] or { seen = {}, pulses = {}, dwell = {} }
	return roots[root]
end

function M.hunk_state(s, file, id)
	local baseline = s.baseline and s.baseline[file]
	if not s.baseline or (baseline and baseline.ids[id]) then
		return nil
	end
	return s.seen[file .. "\0" .. id] and "seen" or "unseen"
end

function M.file_state(s, file)
	local current = s.current and s.current[file]
	local baseline = s.baseline and s.baseline[file]
	if not current or not s.baseline or (baseline and baseline.fingerprint == current.fingerprint) then
		return nil
	end
	local changed = false
	for id in pairs(current.ids) do
		local status = M.hunk_state(s, file, id)
		if status == "unseen" then
			return "unseen"
		end
		changed = changed or status == "seen"
	end
	-- A removed hunk or a mode-only change has no new changed lines to visit.
	return (changed or s.seen[file .. "\0" .. current.fingerprint]) and "seen" or "unseen"
end

function M.update(s, current)
	s.baseline = s.baseline or current
	local retained = {}
	for file, value in pairs(current) do
		if not s.current or not s.current[file] or s.current[file].fingerprint ~= value.fingerprint then
			s.pulses[file] = vim.uv.now()
			-- Revisiting earlier content after another edit is unseen again.
			for id in pairs(value.ids) do
				if not s.current or not s.current[file] or not s.current[file].ids[id] then
					s.seen[file .. "\0" .. id] = nil
				end
			end
			s.seen[file .. "\0" .. value.fingerprint] = nil
		end
		retained[file .. "\0" .. value.fingerprint] = s.seen[file .. "\0" .. value.fingerprint]
		for id in pairs(value.ids) do
			retained[file .. "\0" .. id] = s.seen[file .. "\0" .. id]
		end
	end
	s.seen = retained
	s.current = current
end

function M.scan(root)
	local s = M.get(root)
	if s.scanning then
		s.again = true
		return
	end
	s.scanning = true
	M.capture(root, function(current)
		s.scanning = nil
		if current then
			M.update(s, current)
			M.render()
		end
		if s.again then
			s.again = nil
			M.scan(root)
		end
	end)
end

-- Called before the receiver starts. A receipt can arrive after Review closes.
function M.before_send(root, recipient, callback)
	if not roots[root] or (recipient and recipient.repository ~= root) then
		callback(nil)
		return
	end
	M.capture(root, callback)
end

function M.accept(root, baseline)
	if baseline then
		local s = M.get(root)
		s.baseline, s.seen, s.dwell = baseline, {}, {}
		M.scan(root)
	end
end

local function highlight(status)
	return status == "unseen" and "ReviewUnseen" or "ReviewSeen"
end

function M.render(observe)
	local review = require("rediff.review").active()
	if not review or not review.rows then
		return
	end
	local s = M.get(review.root)
	local visible, now = {}, vim.uv.now()
	local win = api.nvim_get_current_win()
	local focused = observe
		and not review.composer
		and not review.annotation_id
		and vim.fn.mode() == "n"
		and (win == review.old_win or win == review.new_win)
	local painting = {
		current = s.current,
		baseline = s.baseline,
		seen = s.seen_revision,
		annotation = review.annotation_id,
		old_tick = api.nvim_buf_get_changedtick(review.old_buf),
		new_tick = api.nvim_buf_get_changedtick(review.new_buf),
		tree_tick = api.nvim_buf_get_changedtick(review.tree_buf),
		tree = review.tree_buf,
	}
	for file, start in pairs(s.pulses) do
		if now - start < 2000 and M.file_state(s, file) == "unseen" then
			painting["pulse" .. file] = math.floor((now - start) / 250) % 2
		end
	end
	local paint = false
	for _, fields in ipairs({ painting, s.painting or {} }) do
		for field in pairs(fields) do
			if not s.painting or painting[field] ~= s.painting[field] then
				paint = true
			end
		end
	end
	s.painting = painting
	for _, buf in ipairs({ review.tree_buf, review.old_buf, review.new_buf }) do
		if paint then
			api.nvim_buf_clear_namespace(buf, ns, 0, -1)
		end
	end
	local snapshot = review.current
	local file = snapshot and key(snapshot)
	local current = file and s.current and s.current[file]
	if snapshot and s.displayed_id ~= snapshot.id then
		s.displayed_id = snapshot.id
		s.displayed = M.describe(vim.diff(snapshot.old, snapshot.new, { ctxlen = 0 }))
	end
	local displayed = s.displayed
	-- Never paint current markers onto an old annotation snapshot or stale view.
	if current and vim.deep_equal(displayed.ids, current.ids) and not review.annotation_id then
		for _, hunk in ipairs(displayed.hunks) do
			local status = M.hunk_state(s, file, hunk.id)
			if status then
				for _, pane in ipairs({
					{ review.old_buf, review.old_win, "old" },
					{ review.new_buf, review.new_win, "new" },
				}) do
					local buf, pane_win, side = unpack(pane)
					local count = api.nvim_buf_line_count(buf)
					local first = math.max(1, math.min(count, hunk[side .. "_start"]))
					local last = math.min(count, first + math.max(1, hunk[side .. "_count"]) - 1)
					if paint then
						for line = first, last do
							api.nvim_buf_set_extmark(
								buf,
								ns,
								line - 1,
								0,
								{ number_hl_group = highlight(status), priority = 6000 }
							)
						end
						api.nvim_buf_set_extmark(buf, ns, first - 1, 0, {
							virt_text = { { status == "unseen" and " ◆ unseen" or " ◇ seen", highlight(status) } },
							virt_text_pos = "eol",
						})
					end
					if focused and pane_win == win then
						local top, bottom = vim.fn.line("w0"), vim.fn.line("w$")
						if first <= bottom and last >= top then
							visible[file .. "\0" .. hunk.id] = true
						end
					end
				end
			end
		end
		if focused then
			visible[file .. "\0" .. current.fingerprint] = true
		end
	end
	for id in pairs(s.dwell) do
		if not visible[id] then
			s.dwell[id] = nil
		end
	end
	for id in pairs(visible) do
		s.dwell[id] = s.dwell[id] or now
		if not s.seen[id] and now - s.dwell[id] >= 1000 then
			s.seen[id] = true
			s.seen_revision = (s.seen_revision or 0) + 1
		end
	end
	if not paint then
		return
	end
	for row, index in pairs(review.rows) do
		local entry = review.entries[index]
		local status = M.file_state(s, key(entry))
		if status then
			local age = now - (s.pulses[key(entry)] or 0)
			local group = status == "unseen" and age < 2000 and math.floor(age / 250) % 2 == 1 and "ReviewUnseenPulse"
				or highlight(status)
			api.nvim_buf_set_extmark(review.tree_buf, ns, row - 1, 0, {
				virt_text = { { status == "unseen" and "◆ " or "◇ ", group } },
				virt_text_pos = "inline",
			})
		end
	end
end

function M.start(root)
	if not M.get(root).current and not M.get(root).scanning then
		M.scan(root)
	end
	if not timer and #api.nvim_list_uis() > 0 then
		timer = assert(vim.uv.new_timer())
		timer:start(
			200,
			200,
			vim.schedule_wrap(function()
				M.render(true)
			end)
		)
	end
end

function M.stop()
	if timer then
		timer:stop()
		timer:close()
		timer = nil
	end
	for _, s in pairs(roots) do
		s.dwell = {}
	end
end

return M
