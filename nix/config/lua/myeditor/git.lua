local M = {}

function M.run(root, args, input, allow_failure)
	local argv = { "git", "--literal-pathspecs", "-C", root }
	vim.list_extend(argv, args)
	local result = vim.system(argv, { stdin = input, text = false }):wait(10000)
	if not allow_failure and result.code ~= 0 then
		error(vim.trim(result.stderr or "Git failed"))
	end
	return result.stdout or "", result.code
end

function M.root()
	return vim.trim(M.run(vim.fn.getcwd(), { "rev-parse", "--show-toplevel" }))
end

function M.reference(root)
	local branch, code = M.run(root, { "symbolic-ref", "--quiet", "--short", "HEAD" }, nil, true)
	if code == 0 then
		return vim.trim(branch)
	end
	return "@" .. vim.trim(M.run(root, { "rev-parse", "--short", "HEAD" }))
end

local function paths(root, args)
	return vim.split(M.run(root, args), "\0", { plain = true, trimempty = true })
end

function M.entries(root)
	assert(
		#paths(root, { "diff", "--name-only", "--diff-filter=U", "-z" }) == 0,
		"Resolve merge conflicts before entering Review"
	)
	local result = {}
	for _, group in ipairs({ "staged", "unstaged", "untracked" }) do
		local args = { "diff", "--name-status", "--find-renames", "-z" }
		if group == "staged" then
			table.insert(args, "--cached")
		elseif group == "untracked" then
			args = { "ls-files", "--others", "--exclude-standard", "-z" }
		end
		local fields = paths(root, args)
		local i = 1
		while i <= #fields do
			if group == "untracked" then
				table.insert(result, { path = fields[i], group = group, status = "U" })
				i = i + 1
			else
				local status = fields[i]:sub(1, 1)
				table.insert(result, { path = fields[i + 1], group = group, status = status })
				i = i + 2
				if status == "R" then
					-- Keep per-path snapshots/staging for the two sides of a rename.
					table.insert(result, { path = fields[i], group = group, status = status })
					i = i + 1
				end
			end
		end
	end
	return result
end

local function blob(root, revision, path)
	local text, code = M.run(root, { "show", revision .. ":" .. path }, nil, true)
	if code ~= 0 then
		return ""
	end
	return text
end

local function worktree(root, path)
	local file = root .. "/" .. path
	local stat = vim.uv.fs_lstat(file)
	if not stat then
		return ""
	end
	assert(stat.type == "file", "Review currently supports regular files only: " .. path)
	assert(stat.size <= 2 * 1024 * 1024, "File exceeds the 2 MiB review limit: " .. path)
	local fd = assert(vim.uv.fs_open(file, "r", 0))
	local data = assert(vim.uv.fs_read(fd, stat.size, 0))
	assert(vim.uv.fs_close(fd))
	return data
end

function M.lines(text)
	local lines = vim.split(text, "\n", { plain = true })
	if lines[#lines] == "" then
		table.remove(lines)
	end
	return lines
end

function M.snapshot(root, entry)
	local old, new, patch
	if entry.group == "untracked" then
		old, new = "", worktree(root, entry.path)
		patch = vim.diff(old, new, { result_type = "unified", ctxlen = 3 })
	else
		old = blob(root, entry.group == "staged" and "HEAD" or "", entry.path)
		new = entry.group == "staged" and blob(root, "", entry.path) or worktree(root, entry.path)
		local args =
			{ "diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--no-color", "--binary", "--unified=3" }
		if entry.group == "staged" then
			table.insert(args, "--cached")
		end
		vim.list_extend(args, { "--", entry.path })
		patch = M.run(root, args)
	end
	assert(not old:find("\0", 1, true) and not new:find("\0", 1, true), "Binary files cannot be annotated")
	assert(#old <= 2 * 1024 * 1024 and #new <= 2 * 1024 * 1024, "File exceeds the 2 MiB review limit")
	local head = vim.trim(M.run(root, { "rev-parse", "--verify", "HEAD" }, nil, true))
	local id = vim.fn.sha256(
		table.concat({ root, entry.group, entry.path, head, vim.fn.sha256(old), vim.fn.sha256(new), patch }, "\0")
	)
	return { id = id, path = entry.path, group = entry.group, head = head, old = old, new = new, patch = patch }
end

function M.validate(root, snapshot)
	assert(
		M.snapshot(root, snapshot).id == snapshot.id,
		"Reviewed content changed: " .. snapshot.path .. ". Refresh and review again; drafts are preserved."
	)
end

-- Use Git's own patch, preserving path quoting, context and missing-newline markers.
function M.hunks(patch)
	local header, hunks = {}, {}
	for line in (patch .. "\n"):gmatch("(.-)\n") do
		local a, ac, b, bc = line:match("^@@ %-(%d+),?(%d*) %+(%d+),?(%d*) @@")
		if a then
			table.insert(hunks, {
				old_start = tonumber(a),
				old_count = tonumber(ac) or 1,
				new_start = tonumber(b),
				new_count = tonumber(bc) or 1,
				lines = { line },
			})
		elseif #hunks > 0 then
			if line ~= "" then
				table.insert(hunks[#hunks].lines, line)
			end
		else
			table.insert(header, line)
		end
	end
	return header, hunks
end

function M.stage(root, snapshot, side, line, whole_file)
	M.validate(root, snapshot)
	if snapshot.group == "untracked" then
		assert(whole_file, "New files must be staged with S (whole file)")
		M.run(root, { "add", "--", snapshot.path })
		return
	end
	local patch = snapshot.patch
	local chosen
	if not whole_file then
		assert(
			not patch:find("\nnew file mode ", 1, true) and not patch:find("\ndeleted file mode ", 1, true),
			"Added/deleted files must be staged or unstaged with S"
		)
		local header, hunks = M.hunks(patch)
		for _, hunk in ipairs(hunks) do
			local start = hunk[side .. "_start"]
			local count = hunk[side .. "_count"]
			if line >= math.max(start, 1) and line < math.max(start, 1) + math.max(count, 1) then
				chosen = hunk
				break
			end
		end
		assert(chosen, "Move the cursor into a Git hunk to stage it")
		patch = table.concat(header, "\n") .. "\n" .. table.concat(chosen.lines, "\n") .. "\n"
	end
	assert(patch ~= "", "No patch to stage")
	local args = { "apply", "--cached", "--whitespace=nowarn" }
	if snapshot.group == "staged" then
		table.insert(args, "--reverse")
	end
	M.run(root, args, patch)
	return chosen
end

return M
