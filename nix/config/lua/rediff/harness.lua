local M = {}
local api = vim.api
local feedback = require("rediff.feedback")
local connections = require("rediff.connections")
local sessions = {}
local messages = {}
local display_cwd, display_root
local installing = false

local function notify(message, status)
	vim.notify(message, status == "failed" and vim.log.levels.ERROR or vim.log.levels.INFO)
end

local function save(s)
	feedback.write(s.metadata_path or (s.directory .. "/harness.json"), {
		target = s.target,
		provider = s.provider,
		last = s.last,
	})
end

local function clear_sent_message(s, path)
	local payload = feedback.read(path)
	if not payload or not payload.message or not s.last_message or s.last_message.path ~= path then
		return -- Reviews and already-consumed acknowledgments cannot clear a new draft.
	end
	local loaded = s.buf and api.nvim_buf_is_loaded(s.buf)
	local text = loaded and table.concat(api.nvim_buf_get_lines(s.buf, 0, -1, false), "\n") or s.message
	s.message = text
	if payload.message == text then
		s.message = ""
		if loaded then
			api.nvim_buf_set_lines(s.buf, 0, -1, false, { "" })
			vim.bo[s.buf].modified = false
		end
	end
	s.last_message = nil -- Consume once, even if newer text prevented clearing.
	save(s)
end

local function delivery_status(s)
	local ok, argv = pcall(feedback.command, s.target)
	if not s.last or not ok or not vim.deep_equal(s.last.argv, argv) then
		return "idle"
	end
	local status = feedback.read(s.directory .. "/" .. s.last.id .. ".status.json") or {}
	return status.status == "running" and "uncertain" or (status.status or "idle")
end

function M.get(root)
	if not sessions[root] then
		local directory = feedback.directory(root)
		local saved = feedback.read(directory .. "/harness.json") or {}
		local draft = feedback.read(directory .. "/draft.json") or {}
		local target = saved.target or draft.harness or { name = feedback.settings().harness or "none" }
		sessions[root] = {
			root = root,
			directory = directory,
			target = target,
			provider = saved.provider or feedback.provider(target),
			message = "", -- Draft text belongs only to this editor process, including legacy saved drafts.
			last = saved.last,
			epoch = 0,
		}
		sessions[root].delivery = delivery_status(sessions[root])
		if feedback.is_live(target) and target.session then
			local s = sessions[root]
			s.restoring = true
			s.delivery = "checking connection"
			s.connection_error = "The previous "
				.. feedback.provider(target)
				.. " session is unavailable. Select a live harness, then run :harness send."
			local function restored(result)
				vim.schedule(function()
					if s.epoch ~= 0 then
						return -- An explicit selection supersedes startup discovery.
					end
					s.restoring = false
					local ok, matches = pcall(vim.json.decode, result.stdout or "")
					if result.code == 0 and ok and type(matches) == "table" then
						local local_matches, selected = {}, nil
						for _, match in ipairs(matches) do
							if
								type(match) == "table"
								and match.root == root
								and type(match.session) == "string"
								and type(match.connection) == "string"
							then
								table.insert(local_matches, match)
								if match.session == target.session then
									selected = match
								end
							end
						end
						selected = selected or (#local_matches == 1 and local_matches[1])
						if selected then
							target.session, target.connection, target.title, target.capabilities =
								selected.session, selected.connection, selected.title, selected.capabilities
							target.root = root
							connections.connect(target)
							s.connection_error = nil
							save(s)
						end
					end
					s.delivery = s.connection_error and "disconnected" or delivery_status(s)
					local compose = s.compose_after_restore
					s.compose_after_restore = nil
					if compose then
						compose()
					end
				end)
			end
			local ok = pcall(
				vim.system,
				{ "rediff-" .. target.name, "discover", root },
				{ text = true, timeout = 5000 },
				restored
			)
			if not ok then
				restored({ code = 1 })
			end
		elseif target.session then
			connections.connect(vim.tbl_extend("force", target, { root = root }))
		end
	end
	return sessions[root]
end

local function current_message()
	for _, s in pairs(messages) do
		if s.buf == api.nvim_get_current_buf() then
			return s
		end
	end
	for _, s in pairs(sessions) do
		if s.buf == api.nvim_get_current_buf() then
			return s
		end
	end
end

function M.in_message(root)
	local s = current_message()
	return s ~= nil and s.root == root
end

local function current(cached)
	local message = current_message()
	if message then
		return message
	end
	local review = require("rediff.review").active()
	if review then
		return M.get(review.root)
	end
	local cwd = vim.fn.getcwd()
	if not cached or display_cwd ~= cwd then
		display_cwd = cwd
		local ok, root = pcall(require("rediff.git").root)
		display_root = ok and root or nil
	end
	return M.get(assert(display_root, "Not in a Git worktree"))
end

function M.statusline(root)
	local ok, s
	if root then
		ok, s = pcall(M.get, root)
	else
		ok, s = pcall(current, true) -- No Git processes or receipt reads on repeated redraws.
	end
	if not ok then
		return "harness: unavailable"
	end
	local label = s.target.name == "none" and "local" or s.target.name
	local id = s.target.session
	if id then
		label = label .. " · " .. (#id > 12 and ("…" .. id:sub(-8)) or id)
	elseif s.target.name == "amp" or s.target.name == "omp" or s.target.name == "claude" then
		label = label .. " · no session"
	end
	return "harness: " .. vim.fn.strtrans(label .. " · " .. s.delivery):gsub("%%", "%%%%")
end

function M.select(root, target)
	local s = M.get(root)
	assert(not feedback.busy(root), "Wait for the current feedback delivery before switching harnesses")
	assert(not target.root or target.root == root, "Annotations must stay in their own worktree")
	feedback.command(target)
	target = {
		name = target.name,
		session = target.session,
		root = target.root,
		connection = target.connection,
		title = target.title,
		capabilities = target.capabilities,
	}
	-- Review holds this same target object; there is only one binding per root.
	for key in pairs(s.target) do
		s.target[key] = nil
	end
	for key, value in pairs(target) do
		s.target[key] = value
	end
	if feedback.is_live(target) or target.name == "amp" or target.name == "claude" then
		s.provider = feedback.provider(target)
	end
	s.epoch = s.epoch + 1
	s.restoring, s.connection_error, s.compose_after_restore = nil, nil, nil
	s.delivery = delivery_status(s)
	save(s)
	if target.session then
		connections.connect(vim.tbl_extend("force", target, { root = root }))
	end
	notify("Harness: " .. target.name .. (target.session and (" · " .. target.session) or "") .. "; nothing sent")
end

function M.use(name, root)
	assert(name == "amp" or name == "omp" or name == "claude", "Usage: Harness use amp|omp|claude")
	local s = M.get(root or current().root)
	assert(not feedback.busy(s.root), "Wait for the current feedback delivery before switching harnesses")
	local bound = feedback.provider(s.target)
	if bound ~= name then
		M.select(s.root, { name = "none" })
	end
	s.provider = name
	save(s)
	notify("Selected " .. name .. " for new worktrees; feedback connection is " .. s.target.name)
end

function M.launch_command(root)
	local name = M.get(root).provider
	assert(name == "amp" or name == "omp" or name == "claude", "Select a harness first: :harness use amp|omp|claude")
	local executable = vim.fn.exepath(name)
	assert(executable ~= "", "Install/authenticate the " .. name .. " CLI and put it on PATH first")
	return { executable }
end

function M.launch(root)
	local argv = M.launch_command(root)
	vim.cmd.tabnew()
	vim.cmd.tcd(root)
	local job = vim.fn.jobstart(argv, {
		cwd = root,
		term = true,
		on_exit = function(_, code)
			if code ~= 0 then
				vim.schedule(function()
					notify("Harness exited with status " .. code .. " in " .. root .. "; worktree retained", "failed")
				end)
			end
		end,
	})
	assert(job > 0, "Could not start harness; worktree retained at " .. root)
	vim.cmd.startinsert()
end

function M.deliver(root, id, path, argv, retry, callback, owner)
	local s = owner or M.get(root)
	assert(not s.restoring, "Checking the saved harness session. Try again shortly.")
	assert(not s.connection_error, s.connection_error)
	assert(not feedback.busy(root), "A feedback delivery is already running for this repository")
	s.last = { id = id, path = path, argv = vim.deepcopy(argv) }
	save(s)
	s.delivery = #argv == 0 and "queued" or "running"
	local ok, err = pcall(feedback.deliver, root, id, path, argv, retry, function(status)
		s.delivery = status.status
		if status.status == "accepted" or status.status == "completed" then
			clear_sent_message(s, path)
			require("rediff.review").clear_sent(root, path)
		elseif status.status == "failed" then
			notify("Feedback failed: " .. (status.error or "Inspect :ReviewOutbox before retrying"), "failed")
		else
			notify("Feedback kept in local outbox (:ReviewOutbox)")
		end
		if callback then
			callback(status)
		end
		vim.cmd.redrawstatus()
	end)
	if not ok then
		s.delivery = delivery_status(s)
		error(err)
	end
end

function M.retry()
	local s = current()
	local last = assert(s.last, "No previous submission in this checkout")
	assert(
		vim.deep_equal(last.argv, feedback.command(s.target)),
		"Harness changed; reselect the original target before retrying"
	)
	M.deliver(s.root, last.id, last.path, last.argv, true, nil, s)
end

function M.status()
	local s = current()
	local status = s.last and feedback.read(s.directory .. "/" .. s.last.id .. ".status.json")
	notify(
		"Selected type: "
			.. s.provider
			.. "\nFeedback harness: "
			.. s.target.name
			.. (s.target.session and (" · " .. s.target.session) or "")
			.. "\nLast delivery: "
			.. (status and status.status or "none")
	)
end

function M.connect(provider)
	provider = provider or "amp"
	local s = M.get(current().root)
	if s.restoring then
		s.restoring, s.compose_after_restore = nil, nil
		s.delivery = "disconnected"
	end
	s.epoch = s.epoch + 1
	local epoch = s.epoch
	connections.discover(function(err)
		local valid, context = pcall(current)
		if s.epoch ~= epoch or not valid or context.root ~= s.root then
			return
		end
		if err then
			notify(err, "failed")
			return
		end
		local matches = vim.tbl_filter(function(entry)
			return entry.name == provider .. "-live" and entry.root == s.root and entry.online
		end, connections.items())
		if #matches == 0 then
			if provider == "amp" then
				notify(
					"No live Amp session found for this checkout. Reload Amp's plugins, then run :harness connect amp."
				)
			else
				notify(
					"No live oh-my-pi session found for this checkout. Install with :harness install omp, restart OMP, then run :harness connect omp."
				)
			end
		elseif #matches == 1 then
			local ok, failure = pcall(M.select, s.root, matches[1])
			if not ok then
				notify(tostring(failure), "failed")
			end
		else
			M.panel("connect", provider)
		end
	end)
end

local function find_connection(label)
	local matches = vim.tbl_filter(function(entry)
		return entry.key == label or entry.alias == label or entry.session == label
	end, connections.items())
	assert(#matches == 1, "Choose a unique harness alias or ID from :harness list")
	return matches[1]
end

function M.disconnect(entry)
	assert(not feedback.busy(current().root), "Wait for the current feedback delivery before disconnecting")
	assert(not feedback.busy(entry.root), "Wait for this worktree's delivery before disconnecting")
	for _, message in pairs(messages) do
		assert(
			message.connection_key ~= entry.key or not feedback.busy(message.root),
			"Wait for this harness's delivery before disconnecting"
		)
	end
	connections.disconnect(entry.key)
	for root, s in pairs(sessions) do
		if root == entry.root and s.target.name == entry.name and s.target.session == entry.session then
			M.select(root, { name = "none" })
		end
	end
end

function M.panel(purpose, provider)
	local root, directory = current().root, vim.fn.getcwd()
	local source = M.get(root)
	local epoch = source.epoch
	return require("rediff.harness_panel").open({
		root = root,
		provider = provider,
		purpose = purpose or "manage",
		on_select = function(entry)
			if purpose == "connect" and source.epoch ~= epoch then
				return
			end
			if purpose == "send" then
				M.compose(entry, root, directory)
			elseif entry.connected then
				M.disconnect(entry)
				epoch = source.epoch
			elseif entry.root == root then
				M.select(root, {
					name = entry.name,
					session = entry.session,
					root = entry.root,
					connection = entry.connection,
					title = entry.title,
					capabilities = entry.capabilities,
				})
				epoch = source.epoch
			else
				connections.connect(entry)
			end
		end,
		on_message = function(entry)
			M.compose(entry, root, directory)
		end,
		on_disconnect = M.disconnect,
	})
end

local function message_session(target, root, directory)
	local key = vim.fn.sha256(root .. "\0" .. target.key)
	if not messages[key] then
		local outbox = feedback.directory(root)
		local path = outbox .. "/message-" .. key .. ".json"
		local saved = feedback.read(path) or {}
		messages[key] = {
			root = root,
			directory = outbox,
			metadata_path = path,
			message = "",
			epoch = 0,
			target = vim.deepcopy(target),
			provider = target.provider,
			last = saved.last,
			key = key,
			connection_key = target.key,
			origin_directory = directory,
		}
		messages[key].delivery = delivery_status(messages[key])
	end
	local s = messages[key]
	-- A descriptor may change after reload, but never retarget an in-flight message.
	if not feedback.busy(root) then
		s.target = vim.deepcopy(target)
	end
	return s
end

function M.compose(target, root, directory)
	root = root or current().root
	directory = directory or vim.fn.getcwd()
	if not target then
		local source = M.get(root)
		if source.restoring then
			local win, buf = api.nvim_get_current_win(), api.nvim_get_current_buf()
			source.compose_after_restore = function()
				local ok, context = pcall(current)
				if
					ok
					and context.root == root
					and api.nvim_get_current_win() == win
					and api.nvim_get_current_buf() == buf
				then
					local opened, err = pcall(M.compose, nil, root, directory)
					if not opened then
						notify(tostring(err), "failed")
					end
				end
			end
			return
		end
		if source.connection_error then
			notify(source.connection_error)
			M.panel("connect")
			return
		end
		local connected = connections.connected()
		if #connected > 1 then
			M.panel("send")
			return
		end
		target = connected[1]
	end
	local s
	if target then
		local live = assert(connections.get(target.key), "Harness no longer exists")
		assert(live.connected and live.online, "Harness is offline. Reconnect it from :harness list")
		s = message_session(live, root, directory)
	else
		s = M.get(root)
	end
	local loaded = s.buf and api.nvim_buf_is_loaded(s.buf)
	if loaded then
		local win = vim.fn.bufwinid(s.buf)
		if win ~= -1 then
			api.nvim_set_current_win(win)
			return
		end
	end
	-- Start the next composition empty only after an ACK for this exact text.
	-- Keep in-flight/failed drafts and edits made while delivery was running.
	if s.last_message then
		local receipt = feedback.read(s.directory .. "/" .. s.last_message.id .. ".status.json") or {}
		if receipt.status == "accepted" or receipt.status == "completed" then
			clear_sent_message(s, s.last_message.path)
		end
	end
	if not loaded then
		if s.buf and api.nvim_buf_is_valid(s.buf) then
			api.nvim_buf_delete(s.buf, { force = true })
		end
		local buf = api.nvim_create_buf(false, true)
		s.buf = buf
		api.nvim_buf_set_name(buf, "harness://" .. (s.key or vim.fn.sha256(s.root)) .. "/message")
		vim.bo[buf].buftype = "acwrite"
		vim.bo[buf].bufhidden = "hide"
		vim.bo[buf].swapfile = false
		vim.bo[buf].undofile = false
		vim.bo[buf].filetype = "markdown"
		api.nvim_buf_set_lines(buf, 0, -1, false, vim.split(s.message, "\n", { plain = true }))
		vim.bo[buf].modified = false
		vim.keymap.set("n", "<Tab>", function()
			return require("rediff.review").active() and "<Cmd>Explorer<CR>" or "<Tab>"
		end, { buffer = buf, expr = true, desc = "Focus Review tree" })
		local function capture()
			if api.nvim_buf_is_loaded(buf) then
				s.message = table.concat(api.nvim_buf_get_lines(buf, 0, -1, false), "\n")
			end
		end
		local function reject()
			error("Harness messages do not write files; :w submits the message")
		end
		api.nvim_create_autocmd(
			{ "TextChanged", "TextChangedI", "BufLeave", "BufUnload" },
			{ buffer = buf, callback = capture }
		)
		api.nvim_create_autocmd(
			{ "FileWriteCmd", "FileAppendCmd", "FilterWritePre" },
			{ buffer = buf, callback = reject }
		)
		api.nvim_create_autocmd("BufWriteCmd", {
			buffer = buf,
			callback = function(event)
				if event.match ~= api.nvim_buf_get_name(buf) then
					reject()
				end
				assert(not feedback.busy(s.root), "Wait for the current feedback delivery")
				capture()
				if vim.trim(s.message) == "" then
					vim.bo[buf].modified = false -- :wq can close a successfully cleared message.
					return
				end
				local argv = feedback.command(s.target)
				if s.connection_key then
					local connected = connections.get(s.connection_key)
					assert(
						connected and connected.connected and connected.online,
						"Reconnect this harness before sending"
					)
				end
				local recipient = s.target.session
						and {
							provider = feedback.provider(s.target),
							id = s.target.session,
							repository = s.target.root or s.root,
						}
					or nil
				local id, path = feedback.enqueue(s.root, nil, nil, argv, s.message, nil, recipient, s.origin_directory)
				s.last_message = { id = id, path = path }
				M.deliver(s.root, id, path, argv, false, nil, s)
				vim.bo[buf].modified = false
			end,
		})
	end
	vim.cmd("botright 6split")
	api.nvim_win_set_buf(0, s.buf)
	vim.wo.wrap = true
	vim.wo.linebreak = true
	vim.wo.number = false
	vim.wo.signcolumn = "no"
	local label = s.target.alias or (s.target.title ~= "" and s.target.title) or s.target.session or s.target.name
	vim.wo.winbar = (
		" To "
		.. vim.fn.strtrans(label)
		.. " · "
		.. vim.fn.strtrans(s.target.root or s.root)
		.. " · :w send · :wq send+close · :q retain "
	):gsub("%%", "%%%%")
end

function M.install(provider)
	provider = provider or "amp"
	assert(provider == "amp" or provider == "omp", "Usage: Harness install amp|omp")
	assert(not installing, "Plugin installation is already running")
	local has_session, session = pcall(current)
	local target = has_session and vim.deepcopy(session.target) or {}
	local reload = provider == "amp" and target.name == "amp-live"
	local directory = assert(vim.env.HOME, "HOME must be set") .. "/.config/amp/plugins/"
	local prompt = "Install the bundled rediff Amp plugin at "
		.. directory
		.. "rediff.ts?\n"
		.. "This does not install the Amp CLI.\n"
		.. (
			reload and ("Ask Amp thread " .. target.session .. " to reload its plugins after installation?")
			or "No live Amp target selected; reload plugins manually once, then :harness connect amp."
		)
	if provider == "omp" then
		prompt = "Install the bundled rediff.ts extension for oh-my-pi?\n"
			.. "Uses OMP's profile/directory settings (default ~/.omp/agent/extensions).\n"
			.. "This does not install the OMP CLI. Run /restart in OMP after installation."
	end
	if vim.fn.confirm(prompt, "&Install\n&Cancel", 2) ~= 1 then
		return
	end
	local argv = { "rediff-install-" .. provider .. "-plugin" }
	installing = true
	local ok, err = pcall(vim.system, argv, { text = true }, function(result)
		vim.schedule(function()
			if result.code ~= 0 or not reload then
				installing = false
				notify(
					vim.trim(result.code == 0 and result.stdout or result.stderr),
					result.code ~= 0 and "failed" or nil
				)
				return
			end
			notify("Installed rediff; requesting plugin reload from Amp.")
			local started, reload_err = pcall(vim.system, {
				"rediff-amp-live",
				"reload",
				target.connection,
				target.session,
				target.root or session.root,
			}, { text = true }, function(reply)
				vim.schedule(function()
					installing = false
					notify(
						vim.trim(reply.code == 0 and reply.stdout or reply.stderr),
						reply.code ~= 0 and "failed" or nil
					)
				end)
			end)
			if not started then
				installing = false
				notify("Installed, but reload request failed: " .. tostring(reload_err), "failed")
			end
		end)
	end)
	if not ok then
		installing = false
		error(err)
	end
end

function M.setup()
	api.nvim_create_user_command("Harness", function(opts)
		local ok, err = pcall(function()
			local args = opts.fargs
			if args[1] == "connect" and (#args == 1 or (#args == 2 and (args[2] == "amp" or args[2] == "omp"))) then
				if args[2] then
					M.connect(args[2])
				else
					M.panel("connect")
				end
			elseif #args == 1 and (args[1] == "list" or args[1] == "panel") then
				M.panel("manage")
			elseif #args == 2 and args[1] == "use" then
				M.use(args[2])
				if args[2] == "amp" or args[2] == "omp" then
					M.connect(args[2])
				end
			elseif #args == 2 and args[1] == "install" and (args[2] == "amp" or args[2] == "omp") then
				M.install(args[2])
			elseif #args == 0 or (#args == 1 and args[1] == "status") then
				M.status()
			elseif #args <= 2 and args[1] == "send" then
				M.compose(args[2] and find_connection(args[2]) or nil)
			elseif #args >= 3 and args[1] == "rename" then
				connections.rename(find_connection(args[2]).key, table.concat(args, " ", 3))
			elseif #args == 1 and args[1] == "retry" then
				M.retry()
			elseif #args <= 2 and args[1] == "disconnect" then
				local connected = connections.connected()
				if args[2] then
					M.disconnect(find_connection(args[2]))
				elseif #connected > 1 then
					M.panel("manage")
				elseif #connected == 1 then
					M.disconnect(connected[1])
				else
					M.select(current().root, { name = "none" })
				end
			else
				error(
					"Usage: Harness list | connect [amp|omp] | use amp|omp|claude | send [alias] | rename ID ALIAS | disconnect [alias] | status | retry | install amp|omp"
				)
			end
		end)
		if not ok then
			notify(tostring(err), "failed")
		end
	end, {
		nargs = "*",
		complete = function(_, line, pos)
			local args = vim.split(line:sub(1, pos), "%s+")
			if #args == 2 then
				return {
					"list",
					"panel",
					"rename",
					"install",
					"connect",
					"use",
					"disconnect",
					"send",
					"status",
					"retry",
				}
			elseif #args == 3 and args[2] == "use" then
				return { "amp", "omp", "claude" }
			elseif #args == 3 and (args[2] == "connect" or args[2] == "install") then
				return { "amp", "omp" }
			elseif #args == 3 and (args[2] == "send" or args[2] == "disconnect" or args[2] == "rename") then
				return vim.tbl_map(function(entry)
					return entry.alias or entry.session
				end, connections.connected())
			end
			return {}
		end,
	})
	for from, to in pairs({ harness = "Harness", hs = "Harness send", hl = "Harness list", hc = "Harness connect" }) do
		vim.cmd(
			string.format(
				"cnoreabbrev <expr> %s getcmdtype() == ':' && getcmdline() == '%s' && getcmdpos() == %d ? '%s' : '%s'",
				from,
				from,
				#from + 1,
				to,
				from
			)
		)
	end
	api.nvim_create_autocmd({ "QuitPre", "VimLeavePre" }, {
		callback = function()
			for _, s in pairs(vim.tbl_extend("force", sessions, messages)) do
				if s.buf and api.nvim_buf_is_loaded(s.buf) then
					s.message = table.concat(api.nvim_buf_get_lines(s.buf, 0, -1, false), "\n")
					save(s) -- Also removes legacy persisted drafts without writing on every edit.
					-- Session-only drafts need not be sent to allow :q/:qa.
					-- Include hidden composers, which otherwise cause E162 at exit.
					vim.bo[s.buf].modified = false
				end
			end
		end,
	})
end

return M
