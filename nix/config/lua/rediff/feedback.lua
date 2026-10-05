local M = {}
local instance = tostring(vim.uv.os_getpid()) .. "-" .. tostring(vim.uv.hrtime())

function M.directory(root)
	local path = vim.fn.stdpath("state") .. "/reviews/" .. vim.fn.sha256(root)
	vim.fn.mkdir(path, "p", 448)
	return path
end

function M.write(path, value)
	local temp = path .. "." .. vim.uv.os_getpid() .. ".tmp"
	local fd = assert(vim.uv.fs_open(temp, "w", 384))
	local data = vim.json.encode(value)
	local ok, err = pcall(function()
		assert(vim.uv.fs_write(fd, data, 0) == #data, "Incomplete feedback write")
		assert(vim.uv.fs_fsync(fd))
	end)
	vim.uv.fs_close(fd)
	if not ok then
		vim.uv.fs_unlink(temp)
		error(err)
	end
	assert(vim.uv.fs_rename(temp, path))
end

function M.read(path)
	if vim.fn.filereadable(path) == 0 then
		return nil
	end
	return vim.json.decode(table.concat(vim.fn.readfile(path), "\n"))
end

function M.settings()
	return M.read(vim.fn.stdpath("config") .. "/settings.json") or { harness = "none", feedback_command = {} }
end

function M.command(target, settings)
	settings = settings or M.settings()
	target = target or { name = settings.harness or "none" }
	if target.name == "none" then
		return {}
	elseif target.name == "custom" then
		local argv = settings.feedback_command or {}
		assert(#argv > 0, "Configure feedbackCommand for the custom harness first")
		return vim.deepcopy(argv)
	elseif target.name == "amp-live" then
		assert(target.connection and target.session, "Select a live session with :Harness connect amp")
		return { "rediff-amp-live", "send", target.connection, target.session }
	end
	assert(target.name == "amp" or target.name == "claude", "Unknown harness: " .. tostring(target.name))
	assert(
		target.session and target.session ~= "",
		"Select a session with :ReviewHarness " .. target.name .. " SESSION_ID"
	)
	assert(not target.session:match("^%-"), "Expected a session ID, not a command option")
	if target.name == "amp" then
		local parts = vim.split(target.session, "-", { plain = true })
		assert(parts[1] == "T" and #parts == 6, "Expected an Amp T-uuid thread ID")
		for i, length in ipairs({ 8, 4, 4, 4, 12 }) do
			assert(#parts[i + 1] == length and parts[i + 1]:match("^%x+$"), "Expected an Amp T-uuid thread ID")
		end
	end
	return { "rediff-harness", target.name, target.session }
end

local function canonical(value)
	if type(value) ~= "table" then
		return vim.json.encode(value)
	end
	local parts = {}
	if vim.islist(value) then
		for _, item in ipairs(value) do
			table.insert(parts, canonical(item))
		end
		return "[" .. table.concat(parts, ",") .. "]"
	end
	local keys = vim.tbl_keys(value)
	table.sort(keys)
	for _, key in ipairs(keys) do
		table.insert(parts, vim.json.encode(key) .. ":" .. canonical(value[key]))
	end
	return "{" .. table.concat(parts, ",") .. "}"
end

-- Feedback is deduplicated by the full batch content. Receivers must also
-- deduplicate submission_id across uncertain deliveries and process restarts.
function M.enqueue(root, comments, snapshots, target, message, snapshot_status, recipient, directory)
	local payload = {
		protocol_version = 1,
		repository = root,
		sender = {
			instance = instance,
			pid = vim.uv.os_getpid(),
			app = vim.env.NVIM_APPNAME == "rediff" and "rediff" or "nvim",
			directory = directory or vim.fn.getcwd(),
			repository = root,
		},
		recipient = recipient,
		comments = vim.deepcopy(comments),
		snapshots = vim.deepcopy(snapshots),
		target = target or {},
		message = message,
	}
	local directory = M.directory(root)
	local id = vim.fn.sha256(canonical(payload))
	payload.submission_id = id
	-- Observation at initial submission, not batch identity. Agent edits must
	-- not cause unchanged feedback to be sent again or alter an uncertain retry.
	payload.snapshot_status = snapshot_status
	local path = directory .. "/" .. id .. ".json"
	if vim.fn.filereadable(path) == 0 then
		M.write(path, payload)
		M.write(directory .. "/" .. id .. ".status.json", { status = "queued" })
	end
	return id, path
end

local running = {}

function M.busy(root)
	return running[root] == true
end

function M.deliver(root, id, path, argv, retry, callback)
	local status_path = M.directory(root) .. "/" .. id .. ".status.json"
	local status = M.read(status_path) or { status = "queued" }
	assert(not running[root], "A feedback delivery is already running for this repository")
	if status.status == "accepted" or status.status == "completed" then
		callback(status)
		return
	end
	assert(
		status.status ~= "running" or retry,
		"Previous delivery is uncertain. Check the agent, then use :ReviewRetry explicitly."
	)
	assert(status.status ~= "failed" or retry, "Delivery failed. Check the agent before :ReviewRetry.")
	if #argv == 0 then
		callback(status)
		return
	end
	local command = vim.deepcopy(argv)
	table.insert(command, path)
	M.write(status_path, { status = "running" })
	running[root] = true
	local baseline
	local function done(result)
		vim.schedule(function()
			running[root] = nil
			local ok, ack = pcall(vim.json.decode, result.stdout or "")
			if
				result.code ~= 0
				or not ok
				or type(ack) ~= "table"
				or ack.submission_id ~= id
				or (ack.status ~= "accepted" and ack.status ~= "completed")
			then
				ack = {
					status = "failed",
					error = (result.stderr ~= "" and result.stderr)
						or "Receiver did not acknowledge this submission; delivery may be uncertain",
				}
			end
			M.write(status_path, ack)
			if ack.status == "accepted" or ack.status == "completed" then
				require("rediff.awareness").accept(root, baseline)
			end
			callback(ack)
		end)
	end
	local payload = M.read(path)
	local cwd = payload.recipient and payload.recipient.repository or root
	require("rediff.awareness").before_send(root, payload.recipient, function(captured)
		baseline = captured
		local ok, err = pcall(vim.system, command, { cwd = cwd, text = true }, done)
		if not ok then
			done({ code = 1, stdout = "", stderr = tostring(err) })
		end
	end)
end

return M
