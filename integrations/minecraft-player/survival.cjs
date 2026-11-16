// One interruptible survival routine for the spawned player. No chat or server commands.
const { goals } = require('mineflayer-pathfinder')

const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
const between = (low, high) => low + Math.floor(Math.random() * (high - low + 1))
const logs = name => /_(log|stem)$/.test(name) && !name.startsWith('stripped_')
const planks = name => name.endsWith('_planks')
const pickaxe = name => name.endsWith('_pickaxe')
const stone = name => ['cobblestone', 'cobbled_deepslate', 'stone', 'deepslate'].includes(name)
const distance = (a, b) => a.distanceTo(b)

function safeFloor(block) {
  return block?.boundingBox === 'block' && !/lava|magma|cactus|campfire|leaves|ice|sand|gravel/.test(block.name)
}

class Survival {
  constructor(bot) {
    this.bot = bot
    this.epoch = 0
    this.enabled = false
    this.running = false
    this.activity = 'looking'
    this.preferred = ''
    this.last = null
    this.avoid = new Map()
    const types = test => bot.registry.blocksArray.filter(b => test(b.name)).map(b => b.id)
    this.blocks = {
      wood: types(logs),
      leaves: types(n => n.endsWith('_leaves') || n.endsWith('_wart_block')),
      stone: types(n => ['stone', 'deepslate'].includes(n)),
      coal: types(n => n === 'coal_ore' || n === 'deepslate_coal_ore')
    }
  }

  snapshot() {
    const stacks = new Map()
    for (const item of this.bot.inventory.items()) {
      stacks.set(item.name, (stacks.get(item.name) || 0) + item.count)
    }
    return { activity: this.activity, last: this.last, preferred: this.preferred,
      inventory: Object.fromEntries(stacks), restSeconds: Math.max(0, Math.ceil(((this.restUntil || 0) - Date.now()) / 1000)),
      ...(this.project ? { project: this.project.snapshot() } : {}) }
  }

  count(match) {
    return this.bot.inventory.items().reduce((n, item) => n + (match(item.name) ? item.count : 0), 0)
  }

  interrupt() {
    this.enabled = false
    this.epoch++
    clearTimeout(this.timer)
    this.restUntil = 0
    this.preferred = ''
    this.project = null
    if (this.bot.targetDigBlock) this.bot.stopDigging()
    this.bot.pathfinder.setGoal(null)
    this.activity = 'idle'
  }

  resetBreak() {
    this.stepsLeft = between(3, 6)
    this.breakAt = Date.now() + between(65000, 150000)
  }

  start(preferred = '', rest = false) {
    this.interrupt()
    this.enabled = true
    this.preferred = preferred
    this.resetBreak()
    if (rest) this.takeBreak()
    else this.activity = 'looking'
    this.schedule(rest ? this.restUntil - Date.now() : 700)
  }

  takeBreak() {
    this.activity = 'rest'
    this.restUntil = Date.now() + between(18000, 45000)
    this.resetBreak()
  }

  schedule(ms) {
    clearTimeout(this.timer)
    if (this.enabled && !this.running) this.timer = setTimeout(() => this.tick(), Math.max(0, ms))
  }

  active(epoch) {
    return this.enabled && epoch === this.epoch
  }

  async tick() {
    if (!this.enabled || this.running) return
    if (this.restUntil > Date.now()) return this.schedule(this.restUntil - Date.now())
    const epoch = this.epoch
    this.running = true
    try {
      if (await this.step(epoch) && this.active(epoch)) this.stepsLeft--
    } catch {
      // Unreachable blocks, server-denied digs and interrupted paths are normal in survival.
      if (this.active(epoch)) this.activity = 'looking'
    } finally {
      this.running = false
      if (this.active(epoch) && (this.stepsLeft <= 0 || Date.now() >= this.breakAt)) this.takeBreak()
      this.schedule(this.restUntil > Date.now() ? this.restUntil - Date.now() : between(850, 1800))
    }
  }

  async walk(goal, epoch) {
    if (!this.active(epoch)) return false
    const bot = this.bot
    const stop = setTimeout(() => { if (this.active(epoch)) bot.pathfinder.setGoal(null) }, 10000)
    try {
      await bot.pathfinder.goto(goal)
      return this.active(epoch)
    } finally {
      clearTimeout(stop)
    }
  }

  async step(epoch) {
    const bot = this.bot
    if (!bot.entity || !bot.blockAt(bot.entity.position) || bot.health <= 0) return false
    if (bot.food < 15) {
      const meal = bot.inventory.items().find(i => (bot.registry.foodsByName[i.name]?.foodPoints || 0) >= 3 &&
        !['rotten_flesh', 'chicken', 'spider_eye', 'poisonous_potato', 'pufferfish', 'chorus_fruit'].includes(i.name))
      if (meal) {
        this.activity = 'eating'
        await bot.equip(meal, 'hand')
        if (!this.active(epoch)) return false
        const before = bot.food
        await bot.consume()
        if (bot.food > before && this.active(epoch)) this.last = { kind: 'ate', name: meal.name }
        return true
      }
    }
    if (this.project) return this.project.step(this, epoch)
    if (bot.inventory.emptySlotCount() === 0) {
      this.takeBreak()
      return false
    }
    return this.gather(this.preferred, epoch)
  }

  async gather(preferred, epoch) {
    const hasPick = this.count(pickaxe) > 0
    if (!hasPick && preferred !== 'wood') {
      if (await this.preparePick(epoch)) return true
      if (!this.active(epoch)) return false
    }
    const material = (!hasPick && preferred !== 'wood') ? 'wood' : preferred ||
      (this.count(logs) < 4 ? 'wood' : this.count(stone) < 16 ? 'stone' : this.count(n => n === 'coal') < 6 ? 'coal' : '')
    if (material && await this.mine(material, epoch)) return true
    if (!this.active(epoch)) return false
    // If the preferred resource isn't visible, make a short trip on loaded, walkable terrain.
    return this.explore(epoch)
  }

  async craft(name, epoch, table) {
    if (!this.active(epoch)) return false
    const bot = this.bot
    const recipe = bot.recipesFor(bot.registry.itemsByName[name]?.id, null, 1, table)[0]
    if (!recipe) return false
    this.activity = 'crafting'
    const before = this.count(n => n === name)
    await bot.craft(recipe, 1, table)
    if (!this.active(epoch)) return false
    if (this.count(n => n === name) > before) this.last = { kind: 'crafted', name }
    return true
  }

  async preparePick(epoch) {
    const bot = this.bot
    const stacks = bot.inventory.items()
    const table = bot.findBlock({ matching: bot.registry.blocksByName.crafting_table.id, maxDistance: 8 })
    const portable = stacks.find(i => i.name === 'crafting_table')
    const needSticks = this.count(n => n === 'stick') < 2
    const needPlanks = 3 + (needSticks ? 2 : 0) + (!table && !portable ? 4 : 0)
    if (this.count(planks) < needPlanks) {
      const log = stacks.find(i => logs(i.name))
      if (!log) return false
      return this.craft(log.name.replace(/_(log|stem)$/, '_planks'), epoch)
    }
    if (needSticks) return this.craft('stick', epoch)
    if (!table && !portable) return this.craft('crafting_table', epoch)
    if (table) {
      if (!await this.walk(new goals.GoalLookAtBlock(table.position, bot.world), epoch)) return false
      return this.craft('wooden_pickaxe', epoch, table)
    }
    const feet = bot.entity.position.floored()
    for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const pos = feet.offset(dx, 0, dz)
      const base = bot.blockAt(pos.offset(0, -1, 0))
      if (!safeFloor(base) || bot.blockAt(pos)?.name !== 'air') continue
      this.activity = 'crafting'
      await bot.equip(portable, 'hand')
      if (!this.active(epoch)) return false
      await bot.placeBlock(base, feet.offset(0, 1, 0).minus(feet))
      return true
    }
    return false
  }

  async mine(material, epoch) {
    const bot = this.bot
    const now = Date.now()
    for (const [key, until] of this.avoid) if (until <= now) this.avoid.delete(key)
    const feet = bot.entity.position.floored()
    const candidates = bot.findBlocks({ matching: this.blocks[material], maxDistance: 20, count: 18 })
    const pos = candidates.find(p => !this.avoid.has(p.toString()) &&
      !(p.x === feet.x && p.z === feet.z && p.y <= feet.y) &&
      (material !== 'wood' || bot.findBlock({ point: p, matching: this.blocks.leaves, maxDistance: 5 })))
    if (!pos) return false
    this.avoid.set(pos.toString(), now + 45000)
    this.activity = material
    const goal = new goals.GoalLookAtBlock(pos, bot.world, { reach: 4.2 })
    if (!goal.isEnd(bot.entity.position.floored()) && !await this.walk(goal, epoch)) return false
    const block = bot.blockAt(pos)
    if (!this.active(epoch) || !block || !this.blocks[material].includes(block.type) || !bot.canDigBlock(block)) return false
    const tool = bot.pathfinder.bestHarvestTool(block)
    if (tool) await bot.equip(tool, 'hand')
    if (!this.active(epoch) || !block.canHarvest(bot.heldItem?.type ?? null)) return false
    const matches = material === 'wood' ? logs : material === 'stone' ? stone : n => n === 'coal' || n === block.name
    const before = this.count(matches)
    await bot.dig(block, true, 'raycast')
    if (!this.active(epoch)) return false
    this.activity = 'picking_up'
    if (this.count(matches) === before) {
      await delay(300)
      if (!this.active(epoch)) return false
      const drop = bot.nearestEntity(e => distance(e.position, pos) < 4 && matches(e.getDroppedItem?.()?.name || ''))
      const dest = drop?.position || pos
      // Stand at the foot of the tree if the log was above eye level.
      const goal = new goals.GoalNearXZ(Math.floor(dest.x), Math.floor(dest.z), 0)
      if (distance(bot.entity.position, dest) > 1.3) await this.walk(goal, epoch)
      for (let i = 0; i < 8 && this.active(epoch) && this.count(matches) <= before; i++) await delay(200)
    }
    const gained = this.count(matches) - before
    if (this.active(epoch) && gained > 0) {
      this.last = { kind: 'collected', name: material, count: gained }
      return true
    }
    return false
  }

  async explore(epoch) {
    if (!this.active(epoch)) return false
    const bot = this.bot
    const feet = bot.entity.position.floored()
    this.activity = 'exploring'
    for (let i = 0; i < 8; i++) {
      const angle = Math.random() * 2 * Math.PI
      const steps = between(4, 10)
      const x = Math.round(Math.cos(angle) * steps)
      const z = Math.round(Math.sin(angle) * steps)
      for (const y of [0, 1, -1, 2, -2]) {
        const pos = feet.offset(x, y, z)
        if (!safeFloor(bot.blockAt(pos.offset(0, -1, 0))) || bot.blockAt(pos)?.name !== 'air' ||
            bot.blockAt(pos.offset(0, 1, 0))?.name !== 'air') continue
        return this.walk(new goals.GoalNear(pos.x, pos.y, pos.z, 1), epoch)
      }
    }
    this.takeBreak()
    return false
  }
}

module.exports = Survival
