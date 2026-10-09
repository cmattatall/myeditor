local M = {}
local api = vim.api
local feedback = require("rediff.feedback")
local connections = require("rediff.connections")
local sessions = {}
local jobs = {} -- Only terminal jobs launched by this editor, never discovered sessions.
local display_cwd, display_root
local installing = false

local function notify(message, status)
	vim.notify(message, status == "failed" and vim.log.levels.ERROR or vim.log.levels.INFO)
end

local function save(s)
	feedback.write(s.directory .. "/harness.json", {
		target = s.target,
		provider = s.provider,
		last = s.last,
	})
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
				.. " session is unavailable. Select a live harness with :harness connect."
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

local function current(cached)
	if vim.b.rediff_harness_root then
		return M.get(vim.b.rediff_harness_root)
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
	s.restoring, s.connection_error = nil, nil
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

local function terminal_pane(root, buf)
	local pane
	for _, win in ipairs(api.nvim_tabpage_list_wins(0)) do
		if vim.b[api.nvim_win_get_buf(win)].rediff_harness_root then
			pane = win
			break
		end
	end
	if pane then
		api.nvim_set_current_win(pane)
	else
		vim.cmd("botright " .. math.max(6, math.min(16, math.floor(vim.o.lines / 3))) .. "split")
	end
	api.nvim_win_set_buf(0, buf)
	vim.cmd.lcd({ args = { root }, mods = { silent = true } })
	vim.wo.number = false
	vim.wo.relativenumber = false
	vim.wo.signcolumn = "no"
	vim.wo.winfixheight = true
	vim.wo.winbar =
		" Harness · Esc: Neovim controls · i: harness input · Cmd-W + direction: switch pane · :ho: open "
end

function M.move_pane(tab)
	local current_win = api.nvim_get_current_win()
	local windows = api.nvim_tabpage_list_wins(tab)
	for _, win in ipairs(windows) do
		local buf = api.nvim_win_get_buf(win)
		if vim.b[buf].rediff_harness_root then
			-- Keep an editing tab to return to even after :only in the harness.
			if #windows == 1 then
				local empty = api.nvim_open_win(api.nvim_create_buf(true, false), false, { split = "above", win = win })
				vim.wo[empty].winbar = ""
			end
			api.nvim_win_set_config(win, {
				split = "below",
				win = -current_win,
				height = api.nvim_win_get_height(win),
			})
			return
		end
	end
end

function M.launch(root, argv, target)
	for _, owned in pairs(jobs[root] or {}) do
		assert(
			target and (owned.session ~= target.session or owned.provider ~= feedback.provider(target)),
			"Harness already running in this editor; use :harness open"
		)
	end
	argv = argv or M.launch_command(root)
	local owned = {
		buf = api.nvim_create_buf(false, true),
		provider = target and feedback.provider(target) or M.get(root).provider,
		session = target and target.session,
	}
	vim.bo[owned.buf].bufhidden = "hide"
	vim.b[owned.buf].rediff_harness_root = root
	vim.keymap.set(
		"t",
		"<D-w>",
		"<C-\\><C-n><C-w>",
		{ buffer = owned.buf, desc = "Leave harness input for window command (Cmd-w)" }
	)
	vim.keymap.set(
		"t",
		"<Esc>",
		"<C-\\><C-n>",
		{ buffer = owned.buf, nowait = true, desc = "Return control to Neovim without interrupting harness" }
	)
	terminal_pane(root, owned.buf)
	jobs[root] = jobs[root] or {}
	-- This PTY ends at Neovim, not the outer tmux/Kitty terminal. Inheriting
	-- their identity makes CLIs emit passthrough sequences libvterm cannot decode.
	local env = vim.fn.environ()
	for _, name in ipairs({
		"TMUX",
		"TMUX_PANE",
		"TERM_PROGRAM",
		"TERM_PROGRAM_VERSION",
		"KITTY_WINDOW_ID",
		"KITTY_PID",
		"KITTY_LISTEN_ON",
		-- Keep jobstart's normal terminal environment defaults and PTY dimensions.
		"TERM",
		"COLUMNS",
		"LINES",
		"NVIM",
		"NVIM_LISTEN_ADDRESS",
		"NVIM_LOG_FILE",
		"VIM",
		"VIMRUNTIME",
	}) do
		env[name] = nil
	end
	local job = vim.fn.jobstart(argv, {
		cwd = root,
		term = true,
		clear_env = true,
		env = env,
		on_exit = function(id, code)
			jobs[root][id] = nil
			if code ~= 0 and not owned.stopping then
				vim.schedule(function()
					notify("Harness exited with status " .. code .. " in " .. root .. "; worktree retained", "failed")
				end)
			end
		end,
	})
	assert(job > 0, "Could not start harness; worktree retained at " .. root)
	jobs[root][job] = owned
	vim.cmd.startinsert()
end

function M.open(root)
	root = root or current().root
	local tab = api.nvim_get_current_tabpage()
	local choices, included = {}, {}
	for _, target in ipairs(connections.connected()) do
		local choice = { root = target.root, target = target }
		for id, owned in pairs(jobs[target.root] or {}) do
			if owned.session == target.session and owned.provider == feedback.provider(target) then
				choice.owned, choice.id = owned, id
				included[id] = true
				break
			end
		end
		table.insert(choices, choice)
	end
	for directory, processes in pairs(jobs) do
		for id, owned in pairs(processes) do
			if not included[id] then
				table.insert(choices, { root = directory, owned = owned, id = id })
			end
		end
	end
	if #choices == 0 then
		if M.get(root).target.session then
			M.resume()
		else
			M.launch(root)
		end
		return
	end
	for _, choice in ipairs(choices) do
		local target = choice.target
		choice.label = vim.fn.strtrans(
			(target and (target.alias or target.session) or (choice.owned.session or choice.owned.provider))
				.. (choice.owned and " [editor TUI]" or " [resume]")
				.. " · "
				.. choice.root
		)
	end
	table.sort(choices, function(a, b)
		return a.label < b.label
	end)
	local function open(choice)
		if not choice then
			return
		end
		local ok, err = pcall(function()
			assert(api.nvim_get_current_tabpage() == tab and current().root == root, "Checkout changed; run :ho again")
			if choice.owned then
				assert(jobs[choice.root][choice.id] == choice.owned, "Harness exited; run :ho again")
				terminal_pane(choice.root, choice.owned.buf)
				vim.cmd.startinsert()
			else
				local target = connections.get(choice.target.key)
				assert(target and target.connected, "Harness disconnected; run :ho again")
				M.resume(target)
			end
		end)
		if not ok then
			notify(tostring(err), "failed")
		end
	end
	if #choices == 1 then
		open(choices[1])
	else
		vim.ui.select(choices, {
			prompt = "Open harness TUI:",
			format_item = function(choice)
				return choice.label
			end,
		}, open)
	end
end

function M.resume(selected)
	local s = current()
	local target, epoch = vim.deepcopy(selected or s.target), s.epoch
	local root = target.root or s.root
	local provider = feedback.provider(target)
	for _, owned in pairs(jobs[root] or {}) do
		if owned.provider == provider and owned.session == target.session then
			terminal_pane(root, owned.buf)
			vim.cmd.startinsert()
			return
		end
	end
	assert(not feedback.busy(root), "Wait for feedback delivery before resuming the harness")
	assert(
		provider == "amp" or provider == "claude",
		"Resume supports selected Amp or Claude sessions; use :harness connect amp or :ReviewHarness claude SESSION_ID"
	)
	-- Validate the ID as a CLI target, including live bindings, before using it as an argument.
	feedback.command({ name = provider, session = target.session })
	local executable = vim.fn.exepath(provider)
	assert(executable ~= "", "Install/authenticate the " .. provider .. " CLI and put it on PATH first")
	if
		vim.fn.confirm(
			"Resume "
				.. provider
				.. " session "
				.. target.session
				.. " in this editor? Stop using its other terminal first. This starts a new TUI, not an attachment to that process.",
			"&Resume\n&Cancel",
			2
		) ~= 1
	then
		return
	end
	assert(current().root == s.root and s.epoch == epoch, "Checkout or harness changed; run :harness resume again")
	assert(vim.fn.isdirectory(root) == 1, "Harness worktree is unavailable: " .. root)
	local argv = provider == "amp" and { executable, "threads", "continue", target.session }
		or { executable, "--resume", target.session }
	M.launch(root, argv, target)
end

function M.stop(root)
	local ids, buffers = {}, {}
	for id, owned in pairs(jobs[root] or {}) do
		owned.stopping = true
		table.insert(ids, id)
		table.insert(buffers, owned.buf)
		vim.fn.jobstop(id)
	end
	-- Wait for termination before Git removes the process's working directory.
	for _, status in ipairs(vim.fn.jobwait(ids, 5000)) do
		assert(status ~= -1 and status ~= -2, "Harness did not stop; worktree retained at " .. root)
	end
	for _, buf in ipairs(buffers) do
		if api.nvim_buf_is_valid(buf) then
			api.nvim_buf_delete(buf, { force = true })
		end
	end
end

function M.deliver(root, id, path, argv, retry, callback)
	local s = M.get(root)
	assert(not s.restoring, "Checking the saved harness session. Try again shortly.")
	assert(not s.connection_error, s.connection_error)
	assert(not feedback.busy(root), "A feedback delivery is already running for this repository")
	s.last = { id = id, path = path, argv = vim.deepcopy(argv) }
	save(s)
	s.delivery = #argv == 0 and "queued" or "running"
	local ok, err = pcall(feedback.deliver, root, id, path, argv, retry, function(status)
		s.delivery = status.status
		if status.status == "accepted" or status.status == "completed" then
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
	M.deliver(s.root, last.id, last.path, last.argv, true)
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
		s.restoring = nil
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
	connections.disconnect(entry.key)
	for root, s in pairs(sessions) do
		if root == entry.root and s.target.name == entry.name and s.target.session == entry.session then
			M.select(root, { name = "none" })
		end
	end
end

function M.panel(purpose, provider)
	local root = current().root
	local source = M.get(root)
	local epoch = source.epoch
	return require("rediff.harness_panel").open({
		root = root,
		provider = provider,
		on_select = function(entry)
			if purpose == "connect" and source.epoch ~= epoch then
				return
			end
			if entry.connected then
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
		on_disconnect = M.disconnect,
	})
end

function M.install(provider)
	provider = provider or "amp"
	assert(provider == "amp" or provider == "omp", "Usage: Harness install amp|omp")
	assert(not installing, "Plugin installation is already running")
	local has_session, session = pcall(current)
	local target = has_session and vim.deepcopy(session.target) or {}
	local reload = provider == "amp" and target.name == "amp-live"
	local directory = assert(vim.env.HOME, "HOME must be set") .. "/.config/amp/plugins/"
	local prompt = "Install the bundled readiff Amp plugin at "
		.. directory
		.. "readiff.ts?\n"
		.. "Set Amp's interrupt shortcut to Ctrl+L instead of Esc Esc in ~/.config/amp/settings.json.\n"
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
			notify("Installed readiff; requesting plugin reload from Amp.")
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
			elseif #args == 1 and args[1] == "open" then
				M.open()
			elseif #args == 1 and args[1] == "resume" then
				M.resume()
			elseif #args == 2 and args[1] == "use" then
				M.use(args[2])
				if args[2] == "amp" or args[2] == "omp" then
					M.connect(args[2])
				end
			elseif #args == 2 and args[1] == "install" and (args[2] == "amp" or args[2] == "omp") then
				M.install(args[2])
			elseif #args == 0 or (#args == 1 and args[1] == "status") then
				M.status()
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
					"Usage: Harness open | resume | list | connect [amp|omp] | use amp|omp|claude | rename ID ALIAS | disconnect [alias] | status | retry | install amp|omp"
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
					"open",
					"resume",
					"list",
					"panel",
					"rename",
					"install",
					"connect",
					"use",
					"disconnect",
					"status",
					"retry",
				}
			elseif #args == 3 and args[2] == "use" then
				return { "amp", "omp", "claude" }
			elseif #args == 3 and (args[2] == "connect" or args[2] == "install") then
				return { "amp", "omp" }
			elseif #args == 3 and (args[2] == "disconnect" or args[2] == "rename") then
				return vim.tbl_map(function(entry)
					return entry.alias or entry.session
				end, connections.connected())
			end
			return {}
		end,
	})
	for from, to in pairs({
		harness = "Harness",
		hl = "Harness list",
		hc = "Harness connect",
		ho = "Harness open",
	}) do
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
end

return M
