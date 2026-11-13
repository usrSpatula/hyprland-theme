-- Luau Course checker
--
-- The course panel builds one chunk from three parts and runs it in Roblox Studio:
--   1. Settings: __RUNNER ("edit", "server" or "play"), __TEST, __LIMIT (seconds) and __PLATFORM
--   2. This file, which defines the Course API that test checks use
--   3. The test's check code, wrapped in a function that receives Course
-- The chunk returns a JSON report with one result per requirement.
--
-- In the "edit" runner, the learner's scripts are compiled from their source and run in a
-- sandbox, so the place itself is never changed. In "server" and "play", the chunk runs on
-- the server during a playtest, and everything it does is thrown away when the playtest stops.
-- Offline (for testing the course itself), __OFFLINE supplies a clock and mock instances.

local __json, __traceback, __finish
local Course = {}

do
	local BASE = getfenv(1)
	local OFFLINE = __PLATFORM == "offline"
	local EDIT = __RUNNER == "edit"
	local ROOT = if OFFLINE then __OFFLINE.root else game
	local now = if OFFLINE then __OFFLINE.now else os.clock
	local startedAt = now()

	Course.runner = __RUNNER
	Course.test = __TEST

	-- Text helpers --------------------------------------------------------------------------

	local function clip(text, limit)
		text = tostring(text)
		if utf8.len(text) == nil then
			text = string.gsub(text, "[\128-\255]", "?")
		end
		if #text > limit then
			text = string.sub(text, 1, limit - 3)
			-- Don't cut a multi-byte character in half
			while #text > 0 and utf8.len(text) == nil do
				text = string.sub(text, 1, -2)
			end
			text ..= "..."
		end
		return text
	end

	local JSON_ESCAPES = {
		['"'] = '\\"',
		["\\"] = "\\\\",
		["\b"] = "\\b",
		["\f"] = "\\f",
		["\n"] = "\\n",
		["\r"] = "\\r",
		["\t"] = "\\t",
	}

	local function encodeString(text)
		text = tostring(text)
		if utf8.len(text) == nil then
			text = string.gsub(text, "[\128-\255]", "?")
		end
		local escaped = string.gsub(text, '[%c"\\]', function(character)
			return JSON_ESCAPES[character] or string.format("\\u%04x", string.byte(character))
		end)
		return '"' .. escaped .. '"'
	end

	local function encode(value, depth)
		local kind = type(value)
		if kind == "nil" then
			return "null"
		elseif kind == "boolean" then
			return tostring(value)
		elseif kind == "number" then
			if value ~= value or value == math.huge or value == -math.huge then
				return "null"
			elseif value == math.floor(value) and math.abs(value) < 2 ^ 53 then
				return string.format("%d", value)
			end
			return string.format("%.17g", value)
		elseif kind == "string" then
			return encodeString(value)
		elseif kind == "table" then
			if depth > 20 then
				return "null"
			end
			local count = 0
			for _ in next, value do
				count += 1
			end
			if count == 0 then
				return "[]"
			end
			local parts = {}
			if #value == count then
				for index = 1, count do
					parts[index] = encode(value[index], depth + 1)
				end
				return "[" .. table.concat(parts, ",") .. "]"
			end
			for key, item in next, value do
				table.insert(parts, encodeString(key) .. ":" .. encode(item, depth + 1))
			end
			return "{" .. table.concat(parts, ",") .. "}"
		end
		return encodeString(tostring(value))
	end

	__json = function(value)
		return encode(value, 0)
	end

	__traceback = function(message)
		return debug.traceback(tostring(message), 2)
	end

	local function plainError(message)
		return tostring(message)
	end

	-- Showing values in messages -------------------------------------------------------------

	local STRING_ESCAPES = { ["\\"] = "\\\\", ['"'] = '\\"', ["\n"] = "\\n", ["\t"] = "\\t", ["\r"] = "\\r" }

	local function showValue(value, depth)
		local kind = typeof(value)
		if kind == "string" then
			return '"' .. clip(string.gsub(value, '[\\"\n\t\r]', STRING_ESCAPES), 80) .. '"'
		elseif kind == "number" then
			if value ~= value then
				return "NaN"
			end
			return tostring(value)
		elseif kind == "nil" or kind == "boolean" then
			return tostring(value)
		elseif kind == "table" then
			if depth >= 2 then
				return "{...}"
			end
			local count = 0
			for _ in next, value do
				count += 1
			end
			if count == 0 then
				return "{}"
			end
			local parts = {}
			if #value == count then
				for index = 1, math.min(count, 8) do
					parts[index] = showValue(value[index], depth + 1)
				end
				if count > 8 then
					table.insert(parts, "and " .. (count - 8) .. " more")
				end
			else
				local keys = {}
				for key in next, value do
					table.insert(keys, key)
				end
				table.sort(keys, function(a, b)
					return tostring(a) < tostring(b)
				end)
				for index = 1, math.min(#keys, 8) do
					local key = keys[index]
					local label = if type(key) == "string" and string.match(key, "^[%a_][%w_]*$")
						then key
						else "[" .. showValue(key, depth + 1) .. "]"
					table.insert(parts, label .. " = " .. showValue(rawget(value, key), depth + 1))
				end
				if #keys > 8 then
					table.insert(parts, "and " .. (#keys - 8) .. " more")
				end
			end
			return "{" .. table.concat(parts, ", ") .. "}"
		elseif kind == "function" then
			return "a function"
		elseif kind == "Instance" then
			local ok, name = pcall(function()
				return value:GetFullName()
			end)
			return if ok then name else "an Instance"
		elseif kind == "Vector3" then
			return string.format("Vector3.new(%g, %g, %g)", value.X, value.Y, value.Z)
		elseif kind == "Vector2" then
			return string.format("Vector2.new(%g, %g)", value.X, value.Y)
		elseif kind == "CFrame" then
			local position = value.Position
			return string.format("a CFrame at (%g, %g, %g)", position.X, position.Y, position.Z)
		elseif kind == "Color3" then
			return string.format(
				"Color3.fromRGB(%d, %d, %d)",
				math.round(value.R * 255),
				math.round(value.G * 255),
				math.round(value.B * 255)
			)
		elseif kind == "UDim2" then
			return string.format(
				"UDim2.new(%g, %g, %g, %g)",
				value.X.Scale,
				value.X.Offset,
				value.Y.Scale,
				value.Y.Offset
			)
		end
		return tostring(value)
	end

	function Course.show(value)
		return showValue(value, 0)
	end

	-- Results ------------------------------------------------------------------------------

	local order, results, notes = {}, {}, {}

	local function record(id, passed, detail)
		id = tostring(id)
		local entry = results[id]
		if not entry then
			entry = { id = id, passed = true, details = {} }
			results[id] = entry
			table.insert(order, id)
		end
		if not passed then
			entry.passed = false
		end
		if detail ~= nil and detail ~= "" and #entry.details < 8 then
			table.insert(entry.details, { passed = passed, text = clip(detail, 600) })
		end
	end

	function Course.check(id, ok, failDetail)
		local passed = if ok then true else false
		record(id, passed, if passed then nil else (failDetail or "This requirement wasn't met."))
		return passed
	end

	function Course.pass(id, detail)
		record(id, true, detail)
	end

	function Course.fail(id, detail)
		record(id, false, detail or "This requirement wasn't met.")
	end

	function Course.note(text)
		if #notes < 200 then
			table.insert(notes, clip(text, 600))
		end
	end

	-- Running learner code safely ---------------------------------------------------------

	local function isCallable(value)
		if type(value) == "function" then
			return true
		end
		if type(value) ~= "table" then
			return false
		end
		local ok, meta = pcall(getmetatable, value)
		return ok and type(meta) == "table" and rawget(meta, "__call") ~= nil
	end

	-- Runs fn in its own thread and stops waiting after `seconds`.
	-- Returns "ok", results... or "error", message or "timeout".
	local function runTimed(seconds, fn, ...)
		local arguments = table.pack(...)
		local state = { done = false }
		local thread = task.spawn(function()
			state.result = table.pack(xpcall(fn, plainError, table.unpack(arguments, 1, arguments.n)))
			state.done = true
		end)
		local deadline = now() + seconds
		while not state.done and now() < deadline do
			task.wait(0.03)
		end
		if not state.done then
			pcall(task.cancel, thread)
			return "timeout"
		end
		local result = state.result
		if result[1] then
			return "ok", table.unpack(result, 2, result.n)
		end
		return "error", result[2]
	end

	local function timeoutMessage(seconds)
		return string.format(
			"it was still running after %s seconds, so it may be stuck waiting for something or looping",
			tostring(seconds)
		)
	end

	local function finishTimed(seconds, outcome, ...)
		if outcome == "ok" then
			return true, ...
		elseif outcome == "timeout" then
			return false, timeoutMessage(seconds)
		end
		return false, clip((...), 600)
	end

	function Course.timed(seconds, fn, ...)
		if not isCallable(fn) then
			return false, "expected a function, but got " .. Course.show(fn)
		end
		return finishTimed(seconds, runTimed(seconds, fn, ...))
	end

	function Course.call(fn, ...)
		return Course.timed(10, fn, ...)
	end

	function Course.try(id, fn)
		local remaining = math.max(__LIMIT - (now() - startedAt), 2)
		local outcome, message = runTimed(remaining, fn)
		if outcome == "ok" then
			return true
		elseif outcome == "timeout" then
			Course.fail(id, "The check ran out of time here. Something in your code may be waiting forever or looping.")
		else
			Course.fail(id, "Something errored while checking this: " .. clip(message, 500))
		end
		return false
	end

	function Course.waitFor(fn, timeout)
		local deadline = now() + (timeout or 5)
		while true do
			local ok, value = pcall(fn)
			if ok and value then
				return value
			end
			if now() >= deadline then
				return nil
			end
			task.wait(0.05)
		end
	end

	-- Comparing values ---------------------------------------------------------------------

	local function deepEqual(a, b, seen)
		if a == b then
			return true
		end
		if type(a) ~= "table" or type(b) ~= "table" then
			return type(a) == "number" and type(b) == "number" and a ~= a and b ~= b
		end
		if seen[a] == b then
			return true
		end
		seen[a] = b
		for key, value in next, a do
			if not deepEqual(value, rawget(b, key), seen) then
				return false
			end
		end
		for key in next, b do
			if rawget(a, key) == nil then
				return false
			end
		end
		return true
	end

	function Course.equal(a, b)
		return deepEqual(a, b, {})
	end

	function Course.near(a, b, tolerance)
		local kindA, kindB = typeof(a), typeof(b)
		if kindA ~= kindB then
			return false
		elseif kindA == "number" then
			return math.abs(a - b) <= (tolerance or 1e-6)
		elseif kindA == "Vector3" or kindA == "Vector2" then
			return (a - b).Magnitude <= (tolerance or 1e-3)
		elseif kindA == "CFrame" then
			return (a.Position - b.Position).Magnitude <= (tolerance or 1e-3)
		end
		return false
	end

	-- Finding instances --------------------------------------------------------------------

	local function findNow(path)
		if type(path) ~= "string" then
			return nil
		end
		path = string.gsub(path, "^game%.", "")
		local node = ROOT
		for name in string.gmatch(path, "[^%.]+") do
			if node == ROOT and name == "workspace" then
				name = "Workspace"
			end
			node = node:FindFirstChild(name)
			if not node then
				return nil
			end
		end
		return if node == ROOT then nil else node
	end

	function Course.find(path, timeout)
		local found = findNow(path)
		if found or not timeout then
			return found
		end
		return Course.waitFor(function()
			return findNow(path)
		end, timeout)
	end

	-- Output -------------------------------------------------------------------------------

	local captured = {} -- edit runner: everything the sandbox printed, as { kind, text }
	local outputMark = 0

	local function capture(kind, text)
		table.insert(captured, { kind = kind, text = text })
	end

	local logKinds
	local function sessionOutput()
		if EDIT then
			return captured
		end
		local ok, history = pcall(function()
			return game:GetService("LogService"):GetLogHistory()
		end)
		if not ok then
			return {}
		end
		if not logKinds then
			logKinds = {
				[Enum.MessageType.MessageOutput] = "print",
				[Enum.MessageType.MessageWarning] = "warn",
				[Enum.MessageType.MessageError] = "error",
				[Enum.MessageType.MessageInfo] = "info",
			}
		end
		-- The history can include messages from before this playtest, so skip those
		local sessionStart = os.time() - math.floor(time()) - 2
		local list = {}
		for _, entry in history do
			if (entry.timestamp or 0) >= sessionStart then
				table.insert(list, { kind = logKinds[entry.messageType] or "info", text = entry.message })
			end
		end
		return list
	end

	function Course.output()
		local list = {}
		local all = sessionOutput()
		for index = outputMark + 1, #all do
			table.insert(list, { kind = all[index].kind, text = all[index].text })
		end
		return list
	end

	function Course.clearOutput()
		outputMark = #sessionOutput()
	end

	function Course.errors()
		local list = {}
		for _, line in Course.output() do
			if line.kind == "error" then
				table.insert(list, line.text)
			end
		end
		return list
	end

	-- Edit runner: a sandbox for the learner's scripts -------------------------------------

	local sandboxThreads = {}
	local moduleCache = {}
	local sandboxG, sandboxShared = {}, {}

	local function joinArguments(...)
		local count = select("#", ...)
		local parts = table.create(count)
		for index = 1, count do
			parts[index] = tostring((select(index, ...)))
		end
		return table.concat(parts, " ")
	end

	local function guarded(fn)
		return function(...)
			local ok, message = xpcall(fn, plainError, ...)
			if not ok then
				capture("error", clip(message, 600))
			end
		end
	end

	local function trackThread(thread)
		table.insert(sandboxThreads, thread)
		return thread
	end

	local sandboxTask = setmetatable({
		spawn = function(fn, ...)
			return trackThread(task.spawn(if type(fn) == "function" then guarded(fn) else fn, ...))
		end,
		defer = function(fn, ...)
			return trackThread(task.defer(if type(fn) == "function" then guarded(fn) else fn, ...))
		end,
		delay = function(seconds, fn, ...)
			return trackThread(task.delay(seconds, if type(fn) == "function" then guarded(fn) else fn, ...))
		end,
	}, { __index = task })

	local sandboxRequire

	local function makeEnvironment(instance)
		local environment = {
			script = instance,
			print = function(...)
				capture("print", joinArguments(...))
			end,
			warn = function(...)
				capture("warn", joinArguments(...))
			end,
			require = sandboxRequire,
			task = sandboxTask,
			_G = sandboxG,
			shared = sandboxShared,
		}
		return setmetatable(environment, { __index = BASE })
	end

	local function compileScript(instance, source)
		local chunk, problem = loadstring(source or instance.Source, "=" .. instance:GetFullName())
		if not chunk then
			local line, message = string.match(tostring(problem), ":(%d+): (.*)$")
			if line then
				return nil, "Your code has a syntax error on line " .. line .. " of " .. instance.Name .. ": " .. message
					.. ". A script with a syntax error doesn't run at all, not even the lines before it."
			end
			return nil, "Your code has a syntax error: " .. tostring(problem)
		end
		setfenv(chunk, makeEnvironment(instance))
		return chunk
	end

	sandboxRequire = function(target)
		local isModule = false
		if typeof(target) == "Instance" then
			isModule = target:IsA("ModuleScript")
		end
		if not isModule then
			error("Attempted to call require with invalid argument(s).", 2)
		end
		local cached = moduleCache[target]
		if cached then
			if cached.loading then
				error("Requested module was required recursively", 2)
			elseif cached.failed then
				error("Requested module experienced an error while loading", 2)
			end
			return cached.value
		end
		cached = { loading = true }
		moduleCache[target] = cached
		local chunk, problem = compileScript(target)
		if not chunk then
			cached.loading, cached.failed = false, true
			error(problem, 0)
		end
		local result = table.pack(xpcall(chunk, plainError))
		cached.loading = false
		if not result[1] then
			cached.failed = true
			error(result[2], 0)
		elseif result.n ~= 2 then
			cached.failed = true
			error(target:GetFullName() .. ": Module code did not return exactly one value", 0)
		end
		cached.value = result[2]
		return cached.value
	end

	local function freshSandbox()
		table.clear(moduleCache)
		table.clear(sandboxG)
		table.clear(sandboxShared)
	end

	-- Spaces before a colon and at the end of a line don't count, so `Total : 41` matches `Total: 41`
	local function tidy(text)
		return (string.gsub(string.gsub(text, "%s+:", ":"), "%s+$", ""))
	end

	local function outputSince(mark)
		local prints, warns, output = {}, {}, {}
		for index = mark + 1, #captured do
			local line = captured[index]
			table.insert(output, { kind = line.kind, text = line.text })
			if line.kind == "print" then
				table.insert(prints, tidy(line.text))
			elseif line.kind == "warn" then
				table.insert(warns, tidy(line.text))
			end
		end
		return prints, warns, output
	end

	local function newInfo()
		return { ok = false, timedOut = false, prints = {}, warns = {}, output = {} }
	end

	local function editOnly(name)
		if not EDIT then
			error("Course." .. name .. " only works in the edit runner", 2)
		end
	end

	function Course.source(path)
		editOnly("source")
		local instance = Course.find(path)
		if instance and instance:IsA("LuaSourceContainer") then
			return instance.Source
		end
		return nil
	end

	function Course.load(path)
		editOnly("load")
		local info = newInfo()
		local instance = Course.find(path)
		if not instance then
			info.error = "Couldn't find " .. path
			return nil, info
		elseif not instance:IsA("ModuleScript") then
			info.error = path .. " is a " .. instance.ClassName .. ", but it needs to be a ModuleScript"
			return nil, info
		end
		freshSandbox()
		local mark = #captured
		local outcome, value = runTimed(5, sandboxRequire, instance)
		info.prints, info.warns, info.output = outputSince(mark)
		if outcome == "ok" then
			info.ok = true
			return value, info
		elseif outcome == "timeout" then
			info.timedOut = true
			info.error = "Loading the module " .. timeoutMessage(5)
		else
			info.error = clip(value, 600)
		end
		return nil, info
	end

	function Course.run(path, options)
		editOnly("run")
		options = options or {}
		local info = newInfo()
		local instance = Course.find(path)
		if not instance then
			info.error = "Couldn't find " .. path
			return info
		elseif not instance:IsA("LuaSourceContainer") or instance:IsA("ModuleScript") then
			info.error = path .. " is a " .. instance.ClassName .. ", but it needs to be a Script"
			return info
		end
		local source = instance.Source
		for _, pair in options.replace or {} do
			source = string.gsub(source, pair[1], pair[2])
		end
		freshSandbox()
		local chunk, problem = compileScript(instance, source)
		if not chunk then
			info.error = problem
			info.syntax = true
			return info
		end
		local seconds = options.timeout or 3
		local mark = #captured
		local outcome, message = runTimed(seconds, chunk)
		info.prints, info.warns, info.output = outputSince(mark)
		if outcome == "ok" then
			info.ok = true
		elseif outcome == "timeout" then
			info.timedOut = true
			info.error = "Your script " .. timeoutMessage(seconds)
		else
			info.error = clip(message, 600)
		end
		return info
	end

	-- Server and play runners --------------------------------------------------------------

	function Course.require(path)
		if EDIT then
			error("Course.require only works in the server and play runners. Use Course.load instead.", 2)
		end
		local instance = Course.find(path, 5)
		if not instance then
			return nil, "Couldn't find " .. path
		elseif not instance:IsA("ModuleScript") then
			return nil, path .. " is a " .. instance.ClassName .. ", but it needs to be a ModuleScript"
		end
		local ok, value = Course.timed(10, require, instance)
		if ok then
			return value
		end
		-- Roblox prints the module's real error separately, so look for it
		local errors = Course.errors()
		local fullName = instance:GetFullName()
		for index = #errors, 1, -1 do
			if string.find(errors[index], fullName, 1, true) then
				value = errors[index]
				break
			end
		end
		return nil, "Requiring " .. path .. " failed: " .. clip(value, 500)
	end

	function Course.player(timeout)
		if __RUNNER ~= "play" then
			error("Course.player only works in the play runner", 2)
		end
		local Players = game:GetService("Players")
		local player = Course.waitFor(function()
			return Players:GetPlayers()[1]
		end, timeout or 20)
		if not player then
			return nil
		end
		local character = Course.waitFor(function()
			local model = player.Character
			return model and model:FindFirstChild("HumanoidRootPart") and model:FindFirstChildOfClass("Humanoid") and model
		end, timeout or 20)
		if not character then
			return nil
		end
		return player, character
	end

	-- Finishing ----------------------------------------------------------------------------

	__finish = function(ok, problem)
		for _, thread in sandboxThreads do
			pcall(task.cancel, thread)
		end
		local list = {}
		for _, id in order do
			local entry = results[id]
			table.insert(list, { id = entry.id, passed = entry.passed, details = entry.details })
		end
		return __json({
			version = 1,
			test = __TEST,
			runner = __RUNNER,
			results = list,
			notes = notes,
			seconds = math.floor((now() - startedAt) * 10) / 10,
			error = if ok then nil else clip(problem, 3000),
		})
	end
end
