local M = {}

function M.create()
	local root = vim.fn.tempname()
	vim.fn.mkdir(root, "p")
	root = vim.uv.fs_realpath(root)
	local function git(args)
		local cmd = { "git", "-C", root }
		vim.list_extend(cmd, args)
		local result = vim.system(cmd, { text = true }):wait()
		assert(result.code == 0, result.stderr)
		return result.stdout
	end
	git({ "init", "-b", "main" })
	git({ "config", "user.email", "test@example.invalid" })
	git({ "config", "user.name", "Review Test" })
	git({ "config", "commit.gpgsign", "false" })
	local lines = {
		"local M = {}",
		"",
		"function M.authorize(token)",
		"  if token == nil then",
		"    return false",
		"  end",
		"  return token.expires_at > os.time()",
		"end",
		"",
		"-- Preserve these defaults during the review.",
		"M.timeout = 30",
		"M.retries = 3",
		"M.region = 'central'",
		"M.debug = false",
		"",
		"function M.refresh(token)",
		"  return token",
		"end",
		"",
		"return M",
	}
	vim.fn.writefile(lines, root .. "/auth.lua")
	vim.fn.writefile({ "local obsolete = true", "return obsolete" }, root .. "/removed.lua")
	git({ "add", "." })
	git({ "commit", "-m", "Test baseline" })
	local changed = vim.deepcopy(lines)
	changed[5] = "    return true"
	changed[17] = "  return nil"
	vim.fn.writefile(changed, root .. "/auth.lua")
	vim.fn.delete(root .. "/removed.lua")
	vim.fn.writefile({ "# Proposed authentication changes", "Review the token boundary." }, root .. "/plan.md")
	return root, lines, changed, git
end

return M
