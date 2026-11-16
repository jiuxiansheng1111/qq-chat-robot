// Minimal loopback Java 1.20.1 world for testing actual spawn and movement packets.
const path = require('node:path')
const load = require('node:module').createRequire(path.resolve(__dirname, '../../integrations/minecraft-player/package.json'))
const protocol = load('minecraft-protocol')
const { Vec3 } = load('vec3')
const version = '1.20.1'
const data = load('minecraft-data')(version)
const Item = load('prismarine-item')(version)
const survival = process.argv.includes('--survival')
const Chunk = load('prismarine-chunk')(version)
const floor = new Chunk({ minY: -64, worldHeight: 384 })
for (let x = 0; x < 16; x++) {
  for (let z = 0; z < 16; z++) {
    floor.setBlockStateId(new Vec3(x, 64, z), data.blocksByName.stone.defaultState)
  }
}
if (survival) {
  for (const [x, z] of [[9, 5], [10, 8], [7, 11], [13, 5]]) {
    for (let y = 65; y <= 68; y++) floor.setBlockStateId(new Vec3(x, y, z), data.blocksByName.oak_log.defaultState)
    floor.setBlockStateId(new Vec3(x, 69, z), data.blocksByName.oak_leaves.defaultState)
  }
}
const server = protocol.createServer({ host: '127.0.0.1', port: 0, version, 'online-mode': false })
server.once('listening', () => console.log(server.socketServer.address().port))
server.on('playerJoin', player => {
  player.write('login', { ...data.loginPacket, entityId: player.id })
  player.write('map_chunk', {
    x: 0, z: 0, heightmaps: { type: 'compound', name: '', value: {} },
    chunkData: floor.dump(), blockEntities: [], ...floor.dumpLight()
  })
  player.write('position', { x: 3.5, y: 65, z: 5.5, yaw: 0, pitch: 0, flags: 0, teleportId: 1 })
  const uuid = '12345678-1234-4321-abcd-123456789012'
  player.write('player_info', { action: { add_player: true, update_listed: true },
    data: [{ uuid, player: { name: 'TestGuide', properties: [] }, listed: 1 }] })
  player.write('named_entity_spawn', { entityId: 77, playerUUID: uuid, x: 11.5, y: 65, z: 5.5, yaw: 0, pitch: 0 })
  player.write('update_health', { health: 20, food: 20, foodSaturation: 5 })
  if (survival) simulateGathering(player)
  let moved = false
  player.on('packet', (packet, meta) => {
    if (!survival && !moved && ['position', 'position_look'].includes(meta.name) && packet.x > 4.6) {
      moved = true
      console.log('player moved toward TestGuide')
    }
  })
})

function simulateGathering(player) {
  let position = new Vec3(3.5, 65, 5.5)
  let started
  let stateId = 1
  let nextDrop = 100
  let wood = 0
  const drops = new Map()
  function slot(index, name, count) {
    player.write('set_slot', { windowId: 0, stateId: stateId++, slot: index,
      item: Item.toNotch(new Item(data.itemsByName[name].id, count)) })
  }
  slot(36, 'wooden_pickaxe', 1)
  function pickUp() {
    for (const [id, point] of drops) {
      if (point.distanceTo(position) > 1.8) continue
      drops.delete(id)
      player.write('collect', { collectedEntityId: id, collectorEntityId: player.id, pickupItemCount: 1 })
      player.write('entity_destroy', { entityIds: [id] })
      slot(37, 'oak_log', ++wood)
      console.log('picked up oak_log ' + wood)
    }
  }
  player.on('packet', (packet, meta) => {
    if (['position', 'position_look'].includes(meta.name)) {
      position = new Vec3(packet.x, packet.y, packet.z)
      pickUp()
    }
    if (meta.name !== 'block_dig') return
    const { x, y, z } = packet.location
    const pos = new Vec3(x, y, z)
    if (packet.status === 0) { started = pos; return }
    if (packet.status === 1) { started = null; return }
    if (packet.status !== 2 || !started?.equals(pos) || x < 0 || x > 15 || z < 0 || z > 15 ||
        position.offset(0, 1.62, 0).distanceTo(pos.offset(0.5, 0.5, 0.5)) > 5.2 ||
        floor.getBlockType(pos) !== data.blocksByName.oak_log.id) return
    started = null
    floor.setBlockStateId(pos, 0)
    player.write('block_change', { location: pos, type: 0 })
    const id = nextDrop++
    const ground = new Vec3(x + 0.5, 65, z + 0.5)
    drops.set(id, ground)
    player.write('spawn_entity', { entityId: id, objectUUID: require('node:crypto').randomUUID(),
      type: data.entitiesByName.item.id, x: ground.x, y: ground.y, z: ground.z, pitch: 0, yaw: 0, headPitch: 0,
      objectData: 0, velocity: { x: 0, y: 0, z: 0 } })
    player.write('entity_metadata', { entityId: id, metadata: [{ key: 8, type: 'item_stack',
      value: Item.toNotch(new Item(data.itemsByName.oak_log.id, 1)) }] })
    pickUp()
  })
}
process.stdin.resume()
process.stdin.on('end', () => { server.close(); process.exit(0) })
