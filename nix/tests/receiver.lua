-- Fake receiver: exercise the real asynchronous process boundary without an LLM.
local mode, path = arg[1], arg[2]
local payload = vim.json.decode(table.concat(vim.fn.readfile(path), "\n"))
local counter = path .. ".calls"
local count = vim.fn.filereadable(counter) == 1 and tonumber(vim.fn.readfile(counter)[1]) or 0
vim.fn.writefile({ tostring(count + 1) }, counter)
if mode == "fail" then
	io.stderr:write("Deliberate transport failure\n")
	vim.cmd("cquit 9")
end
if mode == "wait" then
	-- Only this child waits. The UI test releases it after exercising the editor.
	local released = vim.wait(15000, function()
		return vim.fn.filereadable(path .. ".release") == 1
	end, 10)
	if not released then
		io.stderr:write("Editor did not release the pending receiver\n")
		vim.cmd("cquit 9")
	end
end
io.stdout:write(vim.json.encode({
	submission_id = mode == "wrong-id" and "wrong" or payload.submission_id,
	status = "completed",
	result = "Fixture received",
}))
