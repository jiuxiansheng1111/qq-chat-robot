// One dedicated Java player. The parent exchanges JSON lines over private pipes.
const readline = require('node:readline')
const fs = require('node:fs')
const path = require('node:path')
const mineflayer = require('mineflayer')
const { pathfinder, Movements, goals } = require('mineflayer-pathfinder')
const Survival = require('./survival.cjs')

let bot
let phase = 'offline'
let following = ''
let life
let settleJoin
const interactive = process.argv[2] === '--login'

function result(status = phase) {
  return { status, following, ...(life && phase === 'in_world' ? life.snapshot() : {}) }
}

function finishJoin(status) {
  phase = status
  if (settleJoin) settleJoin(result())
}

function disconnect() {
  const previous = bot
  if (life) life.interrupt()
  life = undefined
  bot = undefined
  following = ''
  finishJoin('offline')
  if (previous) previous.end()
}

function join(options) {
  if (bot && phase === 'in_world') return result()
  disconnect()
  phase = 'connecting'
  return new Promise((resolve) => {
    const timeout = setTimeout(() => finishJoin('timeout'), interactive ? 240000 : 42000)
    settleJoin = (reply) => {
      clearTimeout(timeout)
      settleJoin = undefined
      resolve(reply)
    }
    const account = mineflayer.createBot({
      host: options.host, port: options.port || 25565,
      username: options.username || 'Dafeiyu', auth: options.auth || 'microsoft',
      version: options.version || false, profilesFolder: options.profilesFolder,
      hideErrors: true, logErrors: false,
      onMsaCode: (code) => {
        if (interactive) {
          process.stderr.write(String(code.message) + '\n')
        } else {
          finishJoin('login_required')
        }
      }
    })
    bot = account
    account.loadPlugin(pathfinder)
    account.on('spawn', () => {
      if (account !== bot) return
      if (life) life.interrupt()
      following = ''
      const movements = new Movements(account)
      movements.canDig = false
      movements.allow1by1towers = false
      movements.allowParkour = false
      movements.scafoldingBlocks = []
      account.pathfinder.setMovements(movements)
      if (!interactive) {
        life = new Survival(account)
        life.start()
      }
      finishJoin('in_world')
    })
    account.on('death', () => {
      if (account !== bot) return
      if (life) life.interrupt()
      following = ''
      phase = 'respawning'
    })
    account.on('entityGone', (entity) => {
      if (account === bot && following && entity.username === following) {
        account.pathfinder.setGoal(null)
        following = ''
        if (life && phase === 'in_world') life.start()
      }
    })
    account.on('kicked', () => {
      if (account === bot) finishJoin('refused')
    })
    account.on('error', () => {
      if (account === bot) finishJoin('connection_failed')
    })
    account.on('end', () => {
      if (account !== bot) return
      if (life) life.interrupt()
      life = undefined
      following = ''
      if (['in_world', 'connecting', 'respawning'].includes(phase)) finishJoin('offline')
    })
  })
}

async function command(request) {
  if (request.action === 'join') return await join(request.options)
  if (request.action === 'leave') {
    disconnect()
    return result()
  }
  if (request.action === 'status' || request.action === 'inventory' || phase !== 'in_world') return result()
  if (request.action === 'build_materials') {
    if (life.project?.id === request.target?.id) return result()
    const { Construction } = require('./construction.cjs')
    const project = new Construction(request.target, bot.game.dimension)
    life.start()
    life.project = project
    following = ''
    return result()
  }
  if (request.action === 'end_build') {
    if (life.project?.id === request.target) life.start('', true)
    return result()
  }
  if (request.action === 'stop') {
    following = ''
    const project = life.project
    life.start('', true)
    life.project = project
    return result()
  }
  if (request.action === 'work') {
    following = ''
    life.start(['wood', 'stone', 'coal'].includes(request.target) ? request.target : '')
    return result()
  }
  if (request.action === 'follow') {
    const target = String(request.target || '')
    if (!/^[A-Za-z0-9_]{1,16}$/.test(target)) return result('need_name')
    const player = Object.values(bot.players).find(p => p.username.toLowerCase() === target.toLowerCase())
    if (!player || !player.entity || player.username === bot.username) return result('player_unseen')
    life.interrupt()
    bot.pathfinder.setGoal(new goals.GoalFollow(player.entity, 2), true)
    following = player.username
    return result()
  }
  return result('unsupported')
}

async function main() {
  if (interactive) {
    const data = path.resolve(__dirname, '../../data')
    const options = JSON.parse(fs.readFileSync(path.join(data, 'game-companion.json'), 'utf8').replace(/^\uFEFF/, '')).minecraft
    if (!options?.host || options.auth === 'offline') throw new Error('先设置 Java 服务器和专用微软账号')
    options.profilesFolder = path.join(data, 'game-companion', 'mc-auth')
    const reply = await join(options)
    process.stderr.write(reply.status + '\n')
    disconnect()
    process.exit(reply.status === 'in_world' ? 0 : 1)
  }
  const lines = readline.createInterface({ input: process.stdin, terminal: false })
  for await (const line of lines) {
    let id
    try {
      const request = JSON.parse(line)
      id = request.id
      const reply = await command(request)
      process.stdout.write(JSON.stringify({ id, ...reply }) + '\n')
    } catch {
      process.stdout.write(JSON.stringify({ id, status: 'connection_failed' }) + '\n')
    }
  }
  disconnect()
  process.exit(0)
}

main().catch(() => { process.exitCode = 1 })
