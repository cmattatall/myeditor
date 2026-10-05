local M = {}
local feedback = require("rediff.feedback")
local entries = {}
local watchers = {}
local listeners = {}
local discovering
local closed = false
local MAX_LINE = 65536

local alias_path = vim.fn.stdpath("state") .. "/rediff/connection-aliases.json"
local aliases = {}
do
	local ok, value = pcall(function()
		return require("rediff.feedback").read(alias_path)
	end)
	if ok and type(value) == "table" then
		aliases = value
	end
end

local function changed()
	for fn in pairs(listeners) do
		vim.schedule(function()
			if listeners[fn] then
				pcall(fn)
			end
		end)
	end
end

local function identity(target)
	assert(type(target) == "table", "Connection target must be a table")
	assert(type(target.root) == "string" and target.root ~= "", "Connection target requires a root")
	assert(type(target.session) == "string" and target.session ~= "", "Connection target requires a session")
	local name = assert(target.name, "Connection target requires a name")
	local provider = target.provider or feedback.provider(target)
	assert(type(name) == "string" and name ~= "", "Connection target requires a name")
	assert(type(provider) == "string" and provider ~= "", "Connection target requires a provider")
	return provider, name
end

function M.key(target)
	local provider, name = identity(target)
	return vim.fn.sha256(table.concat({ provider, name, target.root, target.session }, "\0"))
end

local function copy(entry)
	return entry and vim.deepcopy(entry) or nil
end

local function has_activity(entry)
	if not feedback.is_live(entry) or type(entry.capabilities) ~= "table" then
		return false
	end
	for _, capability in ipairs(entry.capabilities) do
		if capability == "activity" then
			return true
		end
	end
	return false
end

local function stop(key)
	local watcher = watchers[key]
	if not watcher then
		return
	end
	watchers[key] = nil
	watcher.closed = true
	if watcher.child then
		pcall(watcher.child.kill, watcher.child, 15)
	end
end

local function stream_failed(key, watcher)
	if watchers[key] ~= watcher then
		return
	end
	stop(key)
	local entry = entries[key]
	if entry then
		entry.online = false
		entry.activity = { state = "unknown" }
		changed()
	end
end

local function start(entry)
	if closed or not entry.connected or not has_activity(entry) then
		stop(entry.key)
		return
	end
	local previous = watchers[entry.key]
	if previous and previous.connection == entry.connection then
		-- Discovery only checks liveness. Keep the stream and its revision watermark.
		return
	end
	stop(entry.key)
	assert(type(entry.connection) == "string" and entry.connection ~= "", "Live activity target requires a connection")
	local watcher = { connection = entry.connection, buffer = "", sequence = -1, files_revision = 0, closed = false }
	watchers[entry.key] = watcher
	local function consume(_, chunk)
		if not chunk or chunk == "" or watcher.closed then
			return
		end
		watcher.buffer = watcher.buffer .. chunk
		if #watcher.buffer > MAX_LINE and not watcher.buffer:find("\n", 1, true) then
			vim.schedule(function()
				stream_failed(entry.key, watcher)
			end)
			return
		end
		while true do
			local newline = watcher.buffer:find("\n", 1, true)
			if not newline then
				break
			end
			local line = watcher.buffer:sub(1, newline - 1)
			watcher.buffer = watcher.buffer:sub(newline + 1)
			if #line > MAX_LINE then
				vim.schedule(function()
					stream_failed(entry.key, watcher)
				end)
				return
			end
			if line ~= "" then
				vim.schedule(function()
					if watchers[entry.key] ~= watcher then
						return
					end
					local ok, snapshot = pcall(vim.json.decode, line)
					local sequence = ok and type(snapshot) == "table" and snapshot.sequence or nil
					local revision = 0
					if ok and type(snapshot) == "table" and snapshot.files_revision ~= nil then
						revision = snapshot.files_revision
					end
					local valid_state = ok
						and type(snapshot) == "table"
						and vim.list_contains(
							{ "unknown", "idle", "running", "awaiting-approval", "error" },
							snapshot.state
						)
					if
						not valid_state
						or snapshot.version ~= 1
						or snapshot.root ~= entry.root
						or snapshot.thread ~= entry.session
						or type(sequence) ~= "number"
						or sequence % 1 ~= 0
						or sequence < 0
						or sequence <= watcher.sequence
						or type(revision) ~= "number"
						or revision % 1 ~= 0
						or revision < watcher.files_revision
						or type(snapshot.title) ~= "string"
						or (snapshot.tool ~= nil and snapshot.tool ~= vim.NIL and type(snapshot.tool) ~= "string")
					then
						stream_failed(entry.key, watcher)
						return
					end
					watcher.sequence = sequence
					entry.online = true
					entry.title = snapshot.title
					entry.activity =
						{ state = snapshot.state, tool = snapshot.tool ~= vim.NIL and snapshot.tool or nil }
					if revision > watcher.files_revision then
						watcher.files_revision = revision
						require("rediff.live").changed(entry.root)
					end
					changed()
				end)
			end
		end
		if #watcher.buffer > MAX_LINE then
			vim.schedule(function()
				stream_failed(entry.key, watcher)
			end)
		end
	end
	local ok, child = pcall(vim.system, { "rediff-" .. entry.name, "watch", entry.connection, entry.session }, {
		text = true,
		stdout = consume,
	}, function()
		vim.schedule(function()
			stream_failed(entry.key, watcher)
		end)
	end)
	if not ok then
		stream_failed(entry.key, watcher)
		return
	end
	watcher.child = child
end

local function merge(target, connect)
	local key = M.key(target)
	local watcher = watchers[key]
	local live_title = watcher and watcher.connection == target.connection and watcher.sequence >= 0
	local entry = entries[key]
		or {
			key = key,
			activity = { state = "unknown" },
			connected = false,
			online = false,
		}
	for _, field in ipairs({ "name", "provider", "session", "root", "connection", "title", "capabilities" }) do
		if target[field] ~= nil and not (field == "title" and live_title) then
			entry[field] = vim.deepcopy(target[field])
		end
	end
	entry.provider = entry.provider or feedback.provider(entry)
	entry.alias = aliases[key]
	if connect ~= nil then
		entry.connected = connect
	end
	entries[key] = entry
	return entry
end

function M.items()
	local result = {}
	for _, entry in pairs(entries) do
		table.insert(result, copy(entry))
	end
	table.sort(result, function(a, b)
		local left = a.alias or a.title or a.session
		local right = b.alias or b.title or b.session
		return left == right and a.key < b.key or left < right
	end)
	return result
end

function M.connected()
	return vim.tbl_filter(function(entry)
		return entry.connected
	end, M.items())
end

function M.get(key)
	return copy(entries[key])
end

function M.connect(target)
	local entry = merge(target, true)
	entry.online = true
	if not feedback.is_live(entry) then
		entry.activity = { state = "unknown" }
	end
	start(entry)
	changed()
	return copy(entry)
end

function M.disconnect(key)
	local entry = assert(entries[key], "Unknown connection: " .. tostring(key))
	stop(key)
	entry.connected = false
	entry.activity = { state = "unknown" }
	changed()
	return copy(entry)
end

function M.rename(key, alias)
	local entry = assert(entries[key], "Unknown connection: " .. tostring(key))
	assert(type(alias) == "string", "Connection alias must be a string")
	aliases = require("rediff.feedback").read(alias_path) or aliases
	if alias ~= "" then
		assert(vim.trim(alias) == alias and alias ~= "", "Connection alias must be nonempty and trimmed")
		assert(
			#alias <= 80 and not alias:find("[%c]"),
			"Use an alias of at most 80 characters without control characters"
		)
		for other_key, other_alias in pairs(aliases) do
			assert(other_key == key or other_alias ~= alias, "Connection alias is already in use")
		end
		aliases[key] = alias
		entry.alias = alias
	else
		aliases[key] = nil
		entry.alias = nil
	end
	vim.fn.mkdir(vim.fn.fnamemodify(alias_path, ":h"), "p", 448)
	require("rediff.feedback").write(alias_path, aliases)
	changed()
	return copy(entry)
end

function M.discover(callback)
	assert(type(callback) == "function", "Discovery requires a callback")
	if closed then
		return
	end
	if discovering then
		table.insert(discovering, callback)
		return
	end
	discovering = { callback }
	local function finish(err)
		local callbacks = discovering
		discovering = nil
		for _, done in ipairs(callbacks) do
			done(err)
		end
	end
	local ok, err = pcall(vim.system, { "rediff-live", "discover", "--all" }, { text = true }, function(result)
		vim.schedule(function()
			if closed then
				return
			end
			if result.code ~= 0 then
				local message = vim.trim(result.stderr or "")
				finish(message ~= "" and message or "Harness discovery failed")
				return
			end
			local decoded, rows = pcall(vim.json.decode, result.stdout or "")
			if not decoded or type(rows) ~= "table" or not vim.islist(rows) then
				finish("Harness discovery returned invalid data")
				return
			end
			local seen = {}
			for _, row in ipairs(rows) do
				if
					type(row) == "table"
					and (row.provider == "amp" or row.provider == "omp")
					and type(row.root) == "string"
					and row.root ~= ""
					and type(row.session) == "string"
					and row.session ~= ""
					and type(row.connection) == "string"
					and row.connection ~= ""
				then
					row = vim.deepcopy(row)
					row.name = row.provider .. "-live"
					local entry = merge(row)
					entry.online = true
					seen[entry.key] = true
					if entry.connected then
						start(entry)
					end
				end
			end
			for key, entry in pairs(entries) do
				if feedback.is_live(entry) and not seen[key] then
					stop(key)
					if entry.connected then
						entry.online = false
						entry.activity = { state = "unknown" }
					else
						entries[key] = nil
					end
				end
			end
			changed()
			finish(nil)
		end)
	end)
	if not ok then
		vim.schedule(function()
			if not closed then
				finish(tostring(err))
			end
		end)
	end
end

function M.on_change(fn)
	assert(type(fn) == "function", "Change listener must be a function")
	listeners[fn] = true
	return function()
		listeners[fn] = nil
	end
end

vim.api.nvim_create_autocmd("VimLeavePre", {
	callback = function()
		closed = true
		for key in pairs(watchers) do
			stop(key)
		end
	end,
})

return M
