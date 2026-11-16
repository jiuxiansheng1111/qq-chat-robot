// Uses the same Java account, device flow and credential cache as minecraft-protocol.
const { Authflow, Titles } = require('prismarine-auth')
const fs = require('node:fs')
const [username, directory] = process.argv.slice(2)

function report (event) {
  process.stdout.write(JSON.stringify(event) + '\n')
}

async function login () {
  if (!username || !directory) throw new Error('Missing account settings')
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 })
  fs.accessSync(directory, fs.constants.W_OK)
  const auth = new Authflow(username, directory, {
    authTitle: Titles.MinecraftNintendoSwitch, deviceType: 'Nintendo', flow: 'live'
  }, challenge => {
    const code = String(challenge.user_code || '')
    if (!/^[A-Z0-9-]{4,16}$/.test(code)) throw new Error('Invalid device response')
    report({ state: 'device', code })
  })
  const { profile } = await auth.getMinecraftJavaToken({ fetchProfile: true })
  if (!profile?.id || !/^[a-zA-Z0-9_]{1,16}$/.test(profile.name)) throw new Error('Java profile unavailable')
  report({ state: 'verified' })
}

login().catch(() => {
  report({ state: 'failed' })
  process.exitCode = 1
})
