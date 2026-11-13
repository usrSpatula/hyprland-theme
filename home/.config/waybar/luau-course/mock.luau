-- Stand-ins for the parts of Roblox the course checker uses, so tests run on this computer with
-- the luau command-line tool instead of in Studio. The panel puts this between the checker's
-- settings and harness.luau. Time is simulated, so waits finish instantly.

local __OFFLINE = {}
do
	local clock = 0
	local queue = {}
	local sequence = 0
	local instances = setmetatable({}, { __mode = "k" })
	local data = setmetatable({}, { __mode = "k" })

	-- Scheduler ------------------------------------------------------------------------------

	local function schedule(thread, wake, ...)
		sequence += 1
		table.insert(queue, { thread = thread, wake = wake, order = sequence, args = table.pack(...) })
	end

	local function resume(thread, ...)
		local ok, message = coroutine.resume(thread, ...)
		if not ok then
			__OFFLINE.uncaught = __OFFLINE.uncaught or {}
			table.insert(__OFFLINE.uncaught, tostring(message))
		end
	end

	local function toThread(fn)
		return if type(fn) == "thread" then fn else coroutine.create(fn)
	end

	task = {}

	function task.spawn(fn, ...)
		local thread = toThread(fn)
		resume(thread, ...)
		return thread
	end

	function task.defer(fn, ...)
		local thread = toThread(fn)
		schedule(thread, clock, ...)
		return thread
	end

	function task.delay(seconds, fn, ...)
		local thread = toThread(fn)
		schedule(thread, clock + math.max(seconds or 0, 0), ...)
		return thread
	end

	function task.wait(seconds)
		local start = clock
		schedule(coroutine.running(), clock + math.max(seconds or 0, 1 / 60))
		coroutine.yield()
		return clock - start
	end

	function task.cancel(thread)
		for index = #queue, 1, -1 do
			if queue[index].thread == thread then
				table.remove(queue, index)
			end
		end
		if coroutine.status(thread) == "suspended" then
			coroutine.close(thread)
		end
	end

	function __OFFLINE.run(main)
		task.spawn(main)
		local steps = 0
		while #queue > 0 do
			steps += 1
			if steps > 2000000 then
				error("offline scheduler: too many steps")
			end
			local best = 1
			for index = 2, #queue do
				local entry, current = queue[index], queue[best]
				if entry.wake < current.wake or (entry.wake == current.wake and entry.order < current.order) then
					best = index
				end
			end
			local entry = table.remove(queue, best)
			clock = math.max(clock, entry.wake)
			if coroutine.status(entry.thread) == "suspended" then
				resume(entry.thread, table.unpack(entry.args, 1, entry.args.n))
			end
		end
	end

	function __OFFLINE.now()
		return clock
	end

	-- Instances ------------------------------------------------------------------------------

	local SUPER = {
		Script = "BaseScript",
		LocalScript = "Script",
		BaseScript = "LuaSourceContainer",
		ModuleScript = "LuaSourceContainer",
		LuaSourceContainer = "Instance",
		Part = "BasePart",
		BasePart = "PVInstance",
		Model = "PVInstance",
		PVInstance = "Instance",
		Folder = "Instance",
		RemoteEvent = "Instance",
		RemoteFunction = "Instance",
		BindableEvent = "Instance",
		MeshPart = "BasePart",
		IntValue = "ValueBase",
		NumberValue = "ValueBase",
		StringValue = "ValueBase",
		BoolValue = "ValueBase",
		ValueBase = "Instance",
		Player = "Instance",
		ScreenGui = "LayerCollector",
		LayerCollector = "GuiBase2d",
		GuiBase2d = "Instance",
	}

	-- Vector3 -----------------------------------------------------------------------------

	local vectors = setmetatable({}, { __mode = "k" })
	local vectorMeta = {}

	local function vector(x, y, z)
		local value = setmetatable({ X = x or 0, Y = y or 0, Z = z or 0 }, vectorMeta)
		vectors[value] = true
		return value
	end

	local function length(v)
		return math.sqrt(v.X * v.X + v.Y * v.Y + v.Z * v.Z)
	end

	vectorMeta.__index = function(v, key)
		if key == "Magnitude" then
			return length(v)
		elseif key == "Unit" then
			local size = length(v)
			return vector(v.X / size, v.Y / size, v.Z / size)
		elseif key == "Dot" then
			return function(a, b)
				return a.X * b.X + a.Y * b.Y + a.Z * b.Z
			end
		elseif key == "Cross" then
			return function(a, b)
				return vector(a.Y * b.Z - a.Z * b.Y, a.Z * b.X - a.X * b.Z, a.X * b.Y - a.Y * b.X)
			end
		elseif key == "Lerp" then
			return function(a, b, alpha)
				return a + (b - a) * alpha
			end
		end
		error(tostring(key) .. " is not a valid member of Vector3", 2)
	end
	vectorMeta.__add = function(a, b)
		return vector(a.X + b.X, a.Y + b.Y, a.Z + b.Z)
	end
	vectorMeta.__sub = function(a, b)
		return vector(a.X - b.X, a.Y - b.Y, a.Z - b.Z)
	end
	vectorMeta.__mul = function(a, b)
		if type(a) == "number" then
			return vector(b.X * a, b.Y * a, b.Z * a)
		elseif type(b) == "number" then
			return vector(a.X * b, a.Y * b, a.Z * b)
		end
		return vector(a.X * b.X, a.Y * b.Y, a.Z * b.Z)
	end
	vectorMeta.__div = function(a, b)
		if type(b) == "number" then
			return vector(a.X / b, a.Y / b, a.Z / b)
		end
		return vector(a.X / b.X, a.Y / b.Y, a.Z / b.Z)
	end
	vectorMeta.__unm = function(a)
		return vector(-a.X, -a.Y, -a.Z)
	end
	vectorMeta.__eq = function(a, b)
		return a.X == b.X and a.Y == b.Y and a.Z == b.Z
	end
	vectorMeta.__tostring = function(a)
		return string.format("%g, %g, %g", a.X, a.Y, a.Z)
	end

	Vector3 = {
		new = vector,
		zero = vector(0, 0, 0),
		one = vector(1, 1, 1),
		xAxis = vector(1, 0, 0),
		yAxis = vector(0, 1, 0),
		zAxis = vector(0, 0, 1),
	}

	-- Enums: any Enum.Something.Item exists, and compares equal to itself ------------------

	local enumItems = setmetatable({}, { __mode = "k" })
	Enum = setmetatable({}, {
		__index = function(enums, enumName)
			local items = setmetatable({}, {
				__index = function(list, itemName)
					local item = setmetatable({ Name = itemName }, {
						__tostring = function()
							return "Enum." .. enumName .. "." .. itemName
						end,
					})
					enumItems[item] = true
					rawset(list, itemName, item)
					return item
				end,
			})
			rawset(enums, enumName, items)
			return items
		end,
	})

	-- Signals and connections ---------------------------------------------------------------

	local connections = setmetatable({}, { __mode = "k" })

	local function makeSignal()
		local signal = { entries = {} }
		function signal:Connect(fn)
			local connection = { Connected = true }
			function connection:Disconnect()
				self.Connected = false
			end
			connections[connection] = true
			table.insert(signal.entries, { fn = fn, connection = connection })
			return connection
		end
		function signal:Once(fn)
			local connection
			connection = signal:Connect(function(...)
				connection:Disconnect()
				fn(...)
			end)
			return connection
		end
		function signal:Fire(...)
			for _, entry in table.clone(signal.entries) do
				if entry.connection.Connected then
					task.spawn(entry.fn, ...)
				end
			end
		end
		return signal
	end

	local methods = {}

	local function new(className, name)
		local proxy = {}
		instances[proxy] = true
		data[proxy] = {
			ClassName = className,
			Name = name or className,
			children = {},
			fields = {},
			attributes = {},
			attributeSignals = {},
		}
		return setmetatable(proxy, {
			__index = function(self, key)
				local info = data[self]
				if methods[key] then
					return methods[key]
				elseif key == "Name" or key == "ClassName" then
					return info[key]
				elseif key == "Parent" then
					return info.parent
				elseif info.fields[key] ~= nil then
					return info.fields[key]
				end
				for _, child in info.children do
					if data[child].Name == key then
						return child
					end
				end
				error(string.format('%s is not a valid member of %s "%s"', tostring(key), info.ClassName, self:GetFullName()), 2)
			end,
			__newindex = function(self, key, value)
				local info = data[self]
				if key == "Parent" then
					if info.parent then
						local siblings = data[info.parent].children
						local index = table.find(siblings, self)
						if index then
							table.remove(siblings, index)
						end
					end
					info.parent = value
					if value then
						table.insert(data[value].children, self)
					end
				elseif key == "Name" then
					info.Name = tostring(value)
				elseif key == "ClassName" then
					error("ClassName is read-only", 2)
				else
					info.fields[key] = value
				end
			end,
			__tostring = function(self)
				return data[self].Name
			end,
		})
	end

	function methods.GetFullName(self)
		local names = {}
		local node = self
		while node and data[node].parent do
			table.insert(names, 1, data[node].Name)
			node = data[node].parent
		end
		return table.concat(names, ".")
	end

	function methods.IsA(self, className)
		local current = data[self].ClassName
		while current do
			if current == className then
				return true
			end
			current = SUPER[current]
		end
		return className == "Instance"
	end

	function methods.GetChildren(self)
		return table.clone(data[self].children)
	end

	function methods.GetDescendants(self)
		local list = {}
		local function visit(node)
			for _, child in data[node].children do
				table.insert(list, child)
				visit(child)
			end
		end
		visit(self)
		return list
	end

	function methods.FindFirstChild(self, name, recursive)
		local list = if recursive then methods.GetDescendants(self) else data[self].children
		for _, child in list do
			if data[child].Name == name then
				return child
			end
		end
		return nil
	end

	function methods.WaitForChild(self, name)
		return methods.FindFirstChild(self, name)
	end

	function methods.FindFirstChildOfClass(self, className)
		for _, child in data[self].children do
			if data[child].ClassName == className then
				return child
			end
		end
		return nil
	end

	function methods.FindFirstChildWhichIsA(self, className)
		for _, child in data[self].children do
			if methods.IsA(child, className) then
				return child
			end
		end
		return nil
	end

	function methods.FindFirstAncestor(self, name)
		local node = data[self].parent
		while node do
			if data[node].Name == name then
				return node
			end
			node = data[node].parent
		end
		return nil
	end

	function methods.GetAttribute(self, name)
		return data[self].attributes[name]
	end

	function methods.SetAttribute(self, name, value)
		local info = data[self]
		local old = info.attributes[name]
		info.attributes[name] = value
		if old ~= value and info.attributeSignals[name] then
			info.attributeSignals[name]:Fire()
		end
	end

	function methods.GetAttributeChangedSignal(self, name)
		local signals = data[self].attributeSignals
		signals[name] = signals[name] or makeSignal()
		return signals[name]
	end

	function methods.GetAttributes(self)
		return table.clone(data[self].attributes)
	end

	function methods.Destroy(self)
		self.Parent = nil
	end

	function methods.GetService(self, name)
		local found = methods.FindFirstChild(self, name)
		if not found then
			found = new(name, name)
			found.Parent = self
		end
		return found
	end

	local realTypeof = typeof
	typeof = function(value)
		if instances[value] then
			return "Instance"
		elseif vectors[value] then
			return "Vector3"
		elseif enumItems[value] then
			return "EnumItem"
		elseif connections[value] then
			return "RBXScriptConnection"
		end
		return realTypeof(value)
	end

	warn = function(...) end

	game = new("DataModel", "Game")

	-- Sensible starting properties for the classes the course creates with Instance.new
	local STARTING = {
		Part = function()
			return {
				Anchored = false,
				CanCollide = true,
				Transparency = 0,
				Size = vector(4, 1, 2),
				Position = vector(0, 0, 0),
				Material = Enum.Material.Plastic,
			}
		end,
		IntValue = function()
			return { Value = 0 }
		end,
		NumberValue = function()
			return { Value = 0 }
		end,
		StringValue = function()
			return { Value = "" }
		end,
		BoolValue = function()
			return { Value = false }
		end,
		Player = function()
			return { UserId = 0, DisplayName = "Player" }
		end,
	}

	Instance = {
		new = function(className, parent)
			local instance = new(className, className)
			for key, value in if STARTING[className] then STARTING[className]() else {} do
				data[instance].fields[key] = value
			end
			if parent then
				instance.Parent = parent
			end
			return instance
		end,
	}
	workspace = methods.GetService(game, "Workspace")

	local CONTAINERS = { StarterPlayerScripts = true, StarterCharacterScripts = true }

	-- Builds the place from a list of { path, class, source } entries
	function __OFFLINE.build(entries)
		for _, entry in entries do
			local names = string.split(entry.path, ".")
			local node = methods.GetService(game, names[1])
			for index = 2, #names do
				local child = methods.FindFirstChild(node, names[index])
				if not child then
					local className = if index == #names
						then entry.class
						elseif CONTAINERS[names[index]] then names[index]
						else "Folder"
					child = new(className, names[index])
					child.Parent = node
				end
				node = child
			end
			if entry.source then
				data[node].fields.Source = entry.source
			end
			if entry.enabled ~= nil then
				data[node].fields.Enabled = entry.enabled
			end
		end
	end

	__OFFLINE.root = game
end
