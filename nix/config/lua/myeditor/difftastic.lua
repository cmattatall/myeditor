local M = {}
local api = vim.api
local ns = api.nvim_create_namespace("myeditor.difftastic")
local job

-- Keep Codediff's line geometry and filler lines: comments and Git staging use
-- the original snapshot coordinates. Only replace its textual token highlights.
function M.highlight(s, snapshot)
	if job then
		job:kill(15)
		job = nil
	end
	s.diff_engine = "text"
	for _, buf in ipairs({ s.old_buf, s.new_buf }) do
		api.nvim_buf_clear_namespace(buf, ns, 0, -1)
	end
	if vim.fn.executable("difft") ~= 1 or math.max(#snapshot.old, #snapshot.new) > 1000000 then
		return
	end
	local directory = vim.fn.tempname()
	local name = vim.fn.fnamemodify(snapshot.path, ":t")
	vim.fn.mkdir(directory .. "/old", "p", 448)
	vim.fn.mkdir(directory .. "/new", "p", 448)
	local old, new = directory .. "/old/" .. name, directory .. "/new/" .. name
	vim.fn.writefile(vim.split(snapshot.old, "\n", { plain = true }), old, "b")
	vim.fn.writefile(vim.split(snapshot.new, "\n", { plain = true }), new, "b")
	local ok, process = pcall(
		vim.system,
		{
			"difft",
			"--display",
			"json",
			"--color",
			"never",
			"--exit-code",
			"--byte-limit",
			"1000000",
			"--graph-limit",
			"3000000",
			old,
			new,
		},
		{ text = true, timeout = 2000, env = { DFT_UNSTABLE = "yes" } },
		vim.schedule_wrap(function(result)
			vim.fn.delete(directory, "rf")
			if require("myeditor.review").state ~= s or s.current ~= snapshot then
				return
			end
			job = nil
			if result.code ~= 0 and result.code ~= 1 then
				return
			end
			local decoded, diff = pcall(vim.json.decode, result.stdout)
			if
				not decoded
				or type(diff) ~= "table"
				or (diff.status ~= "unchanged" and type(diff.chunks) ~= "table")
			then
				return -- The pinned tool's JSON is experimental; retain a usable text diff on failure.
			end
			local spans = {}
			for _, chunk in ipairs(diff.chunks or {}) do
				for _, pair in ipairs(chunk) do
					for _, side in ipairs({
						{ "lhs", s.old_buf, "CodeDiffCharDelete" },
						{ "rhs", s.new_buf, "CodeDiffCharInsert" },
					}) do
						local line = pair[side[1]]
						if line then
							for _, change in ipairs(line.changes) do
								table.insert(spans, { side[2], line.line_number, change.start, change["end"], side[3] })
							end
						end
					end
				end
			end
			if s.layout == "merged" then
				local adapted = vim.deepcopy(s.diff)
				local old_lines = api.nvim_buf_get_lines(s.old_buf, 0, -1, false)
				local new_lines = api.nvim_buf_get_lines(s.new_buf, 0, -1, false)
				for _, change in ipairs(adapted.changes) do
					change.inner_changes = {}
					for _, span in ipairs(spans) do
						local side = span[1] == s.old_buf and "original" or "modified"
						local line = span[2] + 1
						if line >= change[side].start_line and line < change[side].end_line then
							local text = (side == "original" and old_lines or new_lines)[line]
							local empty = { start_line = 1, end_line = 1, start_col = 1, end_col = 1 }
							local inner = { original = empty, modified = empty }
							inner[side] = {
								start_line = line,
								end_line = line,
								start_col = vim.str_utfindex(text, "utf-16", span[3]) + 1,
								end_col = vim.str_utfindex(text, "utf-16", span[4]) + 1,
							}
							table.insert(change.inner_changes, inner)
						end
					end
				end
				require("codediff.ui.inline").render_inline_diff(s.new_buf, adapted, old_lines, new_lines, {
					filetype = "", -- Keep the high-contrast diff foreground on deleted virtual lines.
				})
			end
			local text_ns = api.nvim_get_namespaces()["codediff-highlight"]
			for _, buf in ipairs({ s.old_buf, s.new_buf }) do
				for _, mark in ipairs(api.nvim_buf_get_extmarks(buf, text_ns, 0, -1, { details = true })) do
					local group = mark[4].hl_group
					if group == "CodeDiffCharInsert" or group == "CodeDiffCharDelete" then
						api.nvim_buf_del_extmark(buf, text_ns, mark[1])
					end
				end
			end
			for _, span in ipairs(spans) do
				-- Difftastic reports zero-based UTF-8 byte offsets, exactly as extmarks require.
				api.nvim_buf_set_extmark(span[1], ns, span[2], span[3], {
					end_col = span[4],
					hl_group = span[5],
					priority = 200,
				})
			end
			s.diff_engine = "difftastic"
			vim.cmd.redrawstatus()
		end)
	)
	if ok then
		job = process
	else
		vim.fn.delete(directory, "rf")
	end
end

return M
