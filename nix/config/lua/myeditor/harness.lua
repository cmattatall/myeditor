local M = {}
local api = vim.api
local feedback = require("myeditor.feedback")
local sessions = {}
local display_cwd, display_root
local installing = false

local function notify(message, status)
	vim.notify(message, status == "failed" and vim.log.levels.ERROR or vim.log.levels.INFO)
end

local function save(s)
	feedback.write(s.directory .. "/harness.json", {
		target = s.target,
		message = s.message,
		last = s.last,
		last_message = s.last_message or false,
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
		local last_message = saved.last_message
		if last_message == nil then
			-- Older drafts tracked only the last delivery. Its payload and receipt
			-- still let compose distinguish a sent message from an unsent draft.
			last_message = saved.last
		end
		sessions[root] = {
			root = root,
			directory = directory,
			target = saved.target or draft.harness or { name = feedback.settings().harness or "none" },
			message = saved.message or "",
			last = saved.last,
			last_message = last_message,
			epoch = 0,
		}
		sessions[root].delivery = delivery_status(sessions[root])
	end
	return sessions[root]
end

local function current(cached)
	for _, s in pairs(sessions) do
		if s.buf == api.nvim_get_current_buf() then
			return s
		end
	end
	local review = require("myeditor.review").active()
	if review then
		return M.get(review.root)
	end
	local cwd = vim.fn.getcwd()
	if not cached or display_cwd ~= cwd then
		display_cwd = cwd
		local ok, root = pcall(require("myeditor.git").root)
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
	elseif s.target.name == "amp" or s.target.name == "claude" then
		label = label .. " · no session"
	end
	return "harness: " .. vim.fn.strtrans(label .. " · " .. s.delivery):gsub("%%", "%%%%")
end

function M.select(root, target)
	local s = M.get(root)
	assert(not feedback.busy(root), "Wait for the current feedback delivery before switching harnesses")
	feedback.command(target)
	-- Review holds this same target object; there is only one binding per root.
	for key in pairs(s.target) do
		s.target[key] = nil
	end
	for key, value in pairs(target) do
		s.target[key] = value
	end
	s.epoch = s.epoch + 1
	s.delivery = delivery_status(s)
	save(s)
	notify("Harness: " .. target.name .. (target.session and (" · " .. target.session) or "") .. "; nothing sent")
end

function M.deliver(root, id, path, argv, retry, callback)
	local s = M.get(root)
	assert(not feedback.busy(root), "A feedback delivery is already running for this repository")
	s.last = { id = id, path = path, argv = vim.deepcopy(argv) }
	save(s)
	s.delivery = #argv == 0 and "queued" or "running"
	local ok, err = pcall(feedback.deliver, root, id, path, argv, retry, function(status)
		s.delivery = status.status
		if status.status == "accepted" or status.status == "completed" then
			clear_sent_message(s, path)
			require("myeditor.review").clear_sent(root, path)
		end
		notify(
			"Feedback " .. status.status .. ": " .. path .. (status.error and ("\n" .. status.error) or ""),
			status.status
		)
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
		"Harness: "
			.. s.target.name
			.. (s.target.session and (" · " .. s.target.session) or "")
			.. "\nLast delivery: "
			.. (status and status.status or "none")
	)
end

function M.connect()
	local s = current()
	s.epoch = s.epoch + 1
	local epoch = s.epoch
	vim.system({ "myeditor-amp-live", "discover", s.root }, { cwd = s.root, text = true }, function(result)
		vim.schedule(function()
			local ok, err = pcall(function()
				if s.epoch ~= epoch or current() ~= s then
					return
				end
				assert(result.code == 0, result.stderr)
				local matches = vim.json.decode(result.stdout)
				assert(
					#matches > 0,
					"No live Amp session for this checkout; install/enable the anthrodiff Amp plugin and try again"
				)
				local function choose(index)
					if s.epoch ~= epoch or current() ~= s then
						return
					end
					local target = vim.deepcopy(matches[index])
					target.name = "amp-live"
					M.select(s.root, target)
				end
				if #matches == 1 then
					choose(1)
				else
					local items = {}
					for i, match in ipairs(matches) do
						items[i] = i .. "\t" .. vim.fn.strtrans(match.session .. " " .. (match.title or ""))
					end
					require("fzf-lua").fzf_exec(items, {
						prompt = "Amp sessions> ",
						previewer = false,
						fzf_opts = { ["--delimiter"] = "\t", ["--with-nth"] = "2..", ["--no-multi"] = true },
						actions = {
							enter = function(selected)
								if selected[1] then
									local selected_ok, selected_err =
										pcall(choose, tonumber(selected[1]:match("^(%d+)")))
									if not selected_ok then
										notify(tostring(selected_err), "failed")
									end
								end
							end,
						},
					})
				end
			end)
			if not ok then
				notify(tostring(err), "failed")
			end
		end)
	end)
end

function M.compose()
	local s = current()
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
		api.nvim_buf_set_name(buf, "harness://" .. vim.fn.sha256(s.root) .. "/message")
		vim.bo[buf].buftype = "acwrite"
		vim.bo[buf].bufhidden = "hide"
		vim.bo[buf].swapfile = false
		vim.bo[buf].undofile = false
		vim.bo[buf].filetype = "markdown"
		api.nvim_buf_set_lines(buf, 0, -1, false, vim.split(s.message, "\n", { plain = true }))
		vim.bo[buf].modified = false
		local function capture()
			if api.nvim_buf_is_loaded(buf) then
				s.message = table.concat(api.nvim_buf_get_lines(buf, 0, -1, false), "\n")
				save(s)
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
				local id, path = feedback.enqueue(s.root, nil, nil, argv, s.message)
				s.last_message = { id = id, path = path }
				M.deliver(s.root, id, path, argv)
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
	vim.wo.winbar = " Harness message · :w send · :wq send+close · :q retain draft "
end

function M.install()
	assert(not installing, "Plugin installation is already running")
	local directory = assert(vim.env.HOME, "HOME must be set") .. "/.config/amp/plugins/"
	local legacy = vim.uv.fs_lstat(directory .. "revdiff.ts") ~= nil
	local prompt = "Install the bundled anthrodiff Amp plugin at "
		.. directory
		.. "anthrodiff.ts?\n"
		.. "This does not install the Amp CLI or reload Amp."
	if legacy then
		prompt = prompt .. "\nThe old revdiff.ts will be moved to ~/.config/amp/plugin-backups/revdiff.ts."
	end
	if vim.fn.confirm(prompt, (legacy and "&Replace" or "&Install") .. "\n&Cancel", 2) ~= 1 then
		return
	end
	local argv = { "myeditor-install-amp-plugin" }
	if legacy then
		table.insert(argv, "--replace-revdiff")
	end
	installing = true
	local ok, err = pcall(vim.system, argv, { text = true }, function(result)
		vim.schedule(function()
			installing = false
			notify(vim.trim(result.code == 0 and result.stdout or result.stderr), result.code ~= 0 and "failed" or nil)
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
			if #args == 2 and args[1] == "connect" and args[2] == "amp" then
				M.connect()
			elseif #args == 2 and args[1] == "install" and args[2] == "amp" then
				M.install()
			elseif #args == 0 or (#args == 1 and args[1] == "status") then
				M.status()
			elseif #args == 1 and args[1] == "send" then
				M.compose()
			elseif #args == 1 and args[1] == "retry" then
				M.retry()
			elseif #args == 1 and args[1] == "disconnect" then
				M.select(current().root, { name = "none" })
			else
				error("Usage: Harness install amp | connect amp | disconnect | send | status | retry")
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
				return { "install", "connect", "disconnect", "send", "status", "retry" }
			elseif #args == 3 and (args[2] == "connect" or args[2] == "install") then
				return { "amp" }
			end
			return {}
		end,
	})
	for from, to in pairs({ harness = "Harness", hs = "Harness send" }) do
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
			for _, s in pairs(sessions) do
				if s.buf and api.nvim_buf_is_loaded(s.buf) then
					s.message = table.concat(api.nvim_buf_get_lines(s.buf, 0, -1, false), "\n")
					save(s)
					-- A locally persisted draft need not be sent to allow :q/:qa.
					-- Include hidden composers, which otherwise cause E162 at exit.
					vim.bo[s.buf].modified = false
				end
			end
		end,
	})
end

return M
