// Reconcile a material list with the actual contents of one named container.
const { Vec3 } = require('vec3')
const { goals } = require('mineflayer-pathfinder')

const materials = {
  wood: n => /_(log|stem)$/.test(n) && !n.startsWith('stripped_'),
  stone: n => ['cobblestone', 'cobbled_deepslate', 'stone', 'deepslate'].includes(n),
  coal: n => n === 'coal',
  glass: n => n === 'glass',
  water_bucket: n => n === 'water_bucket'
}
const harvestable = new Set(['wood', 'stone', 'coal'])
const boxNames = new Set(['chest', 'trapped_chest', 'barrel'])

function countItems(items, needs) {
  return Object.fromEntries(Object.keys(needs).map(key => [key,
    items.reduce((sum, stack) => sum + (materials[key](stack.name) ? stack.count : 0), 0)]))
}

class Construction {
  constructor(target, dimension) {
    if (!target || !Array.isArray(target.chest) || target.chest.length !== 3 ||
        !target.chest.every(Number.isSafeInteger) || !target.needs || typeof target.needs !== 'object' ||
        !Object.keys(target.needs).length || !Object.entries(target.needs).every(([key, value]) =>
          Object.hasOwn(materials, key) && Number.isInteger(value) && value > 0 && value <= 2048)) {
      throw new Error('invalid material plan')
    }
    const [x, y, z] = target.chest
    if (Math.abs(x) > 30000000 || Math.abs(z) > 30000000 || y < -2048 || y > 2048) throw new Error('invalid chest')
    this.position = new Vec3(x, y, z)
    this.id = String(target.id)
    this.dimension = String(target.dimension || dimension)
    this.needs = { ...target.needs }
    this.counts = null
    this.checked = 0
    this.nextCheck = 0
    this.state = 'checking'
  }

  snapshot() {
    return { id: this.id, dimension: this.dimension, counts: this.counts, checked: this.checked, state: this.state }
  }

  async inspect(life, epoch) {
    const bot = life.bot
    const pos = this.position
    this.nextCheck = Date.now() + 30000
    if (String(bot.game.dimension) !== this.dimension) { this.state = 'wrong_dimension'; return false }
    if (bot.entity.position.distanceTo(pos) > 96) { this.state = 'too_far'; return false }
    const goal = new goals.GoalNear(pos.x, pos.y, pos.z, 2)
    if (!goal.isEnd(bot.entity.position.floored())) {
      if (!await life.walk(goal, epoch)) return false
    }
    if (!life.active(epoch)) return false
    const block = bot.blockAt(pos)
    if (!block || !boxNames.has(block.name)) { this.state = 'missing_chest'; return false }
    const box = await bot.openContainer(block)
    let transferred = false
    try {
      if (!life.active(epoch) || String(bot.game.dimension) !== this.dimension) return false
      this.counts = countItems(box.containerItems(), this.needs)
      this.checked = Date.now()
      let working = this.counts
      for (const stack of bot.inventory.items()) {
        if (!life.active(epoch)) return false
        const key = Object.keys(this.needs).find(k => materials[k](stack.name))
        const amount = key ? Math.min(stack.count, this.needs[key] - working[key]) : 0
        if (amount <= 0 || stack.nbt) continue
        await box.deposit(stack.type, stack.metadata, amount)
        transferred = true
        working = countItems(box.containerItems(), this.needs)
      }
    } finally {
      box.close()
    }
    if (transferred) {
      if (!life.active(epoch) || !boxNames.has(bot.blockAt(pos)?.name)) return false
      // A local slot update may be optimistic. Reopening obtains a fresh server inventory.
      const confirmed = await bot.openContainer(bot.blockAt(pos))
      try {
        if (!life.active(epoch)) return false
        this.counts = countItems(confirmed.containerItems(), this.needs)
        this.checked = Date.now()
      } finally {
        confirmed.close()
      }
    }
    this.state = Object.keys(this.needs).every(k => this.counts[k] >= this.needs[k]) ? 'ready' : 'gathering'
    return true
  }

  async step(life, epoch) {
    const inventory = countItems(life.bot.inventory.items(), this.needs)
    const due = !this.counts || Object.keys(inventory).some(k => inventory[k] > 0 &&
      this.needs[k] > this.counts[k] && inventory[k] >= Math.min(4, this.needs[k] - this.counts[k]))
    if (Date.now() >= this.nextCheck || due && this.state === 'gathering') {
      this.state = 'checking'
      try {
        if (!await this.inspect(life, epoch)) return false
      } catch {
        this.state = 'chest_unavailable'
        this.nextCheck = Date.now() + 30000
        return false
      }
    }
    if (!life.active(epoch) || !this.counts || !['ready','gathering','needs_supplies'].includes(this.state)) return false
    if (this.state === 'ready') return false
    const need = Object.keys(this.needs).find(k => harvestable.has(k) && this.counts[k] + inventory[k] < this.needs[k])
    if (!need) { this.state = 'needs_supplies'; return false }
    this.state = 'gathering'
    return life.gather(need, epoch)
  }
}

module.exports = { Construction, countItems }
