local M = {}

-- Keep Neovim's native region coordinates, including partial-tab offsets.
-- start/end columns are 1-based bytes; end is inclusive when its offset is 0.
function M.capture(buf, kind, first, last)
	local options = { type = kind, exclusive = vim.o.selection == "exclusive" }
	local text = vim.fn.getregion(first, last, options)
	local positions = vim.fn.getregionpos(first, last, vim.tbl_extend("force", options, { eol = true }))
	local spans = {}
	for _, pair in ipairs(positions) do
		table.insert(spans, {
			line = pair[1][2],
			start_byte = pair[1][3],
			start_offset = pair[1][4],
			end_byte = pair[2][3],
			end_offset = pair[2][4],
		})
	end
	assert(#spans > 0, "Select source text before adding a comment")
	return {
		kind = kind == "V" and "line" or kind == "v" and "character" or "block",
		spans = spans,
		text = text,
		tabstop = vim.bo[buf].tabstop,
		coordinates = "nvim-getregionpos-v1",
	}
end

function M.line(buf, line)
	local text = vim.api.nvim_buf_get_lines(buf, line - 1, line, false)[1] or ""
	return {
		kind = "line",
		spans = { { line = line, start_byte = 1, end_byte = #text, start_offset = 0, end_offset = 0 } },
		text = { text },
		coordinates = "nvim-getregionpos-v1",
	}
end

return M
