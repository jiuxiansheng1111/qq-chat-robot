'use strict';

// 只调用所需的账号接口，不启动 SDK 的公共服务。
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const { createRequire } = require('node:module');

const projectRoot = path.resolve(__dirname, '../..');
const dataRoot = path.join(projectRoot, 'data', 'netease');
const portIndex = process.argv.indexOf('--port');
const port = Number(portIndex >= 0 ? process.argv[portIndex + 1] : 3010);
if (!Number.isInteger(port) || port < 1024 || port > 65535) {
  process.stderr.write('Invalid NetEase bridge port.\n');
  process.exit(1);
}
fs.mkdirSync(dataRoot, { recursive: true });
const token = crypto.randomBytes(32).toString('hex');
const csrfToken = crypto.randomBytes(24).toString('hex');
const authPath = path.join(dataRoot, 'auth.json');
const tokenPath = path.join(dataRoot, 'bridge-token.txt');
const cookieKeys = new Set(['MUSIC_U', '__csrf', 'NMTID', 'MUSIC_A_T', 'MUSIC_R_T', 'MUSIC_SNS']);

// 上游异常可能带请求头，日志只写固定提示，避免泄露账号。
for (const method of ['log', 'info', 'warn', 'error', 'debug', 'trace']) {
  console[method] = () => {};
}
process.env.ENABLE_GENERAL_UNBLOCK = 'false';
process.env.ENABLE_RANDOM_CN_IP = 'false';
process.env.ENABLE_PROXY = 'false';
process.env.DEBUG_COOKIE = '0';
process.env.DEBUG_URL = '0';
// 重启后沿用设备标识和登录状态。
const devicePath = path.join(dataRoot, 'device.json');
let device;
try {
  if (fs.statSync(devicePath).size <= 1024) device = JSON.parse(fs.readFileSync(devicePath, 'utf8'));
} catch { /* 首次安装会创建设备标识。 */ }
if (!device || !/^[0-9A-F]{52}$/.test(device.id) || !/^[0-9a-f]{64}$/.test(device.nuid)) {
  device = { id: crypto.randomBytes(26).toString('hex').toUpperCase(), nuid: crypto.randomBytes(32).toString('hex'), created: Date.now() };
  fs.writeFileSync(devicePath, JSON.stringify(device), { mode: 0o600 });
}
global.deviceId = device.id;
const sdkRequire = createRequire(path.join(dataRoot, 'runtime', 'package.json'));
let sdkRoot;
let request;
let cookieToJson;
let getXeapiPublicKey;
const xeapiKeyPath = path.join(os.tmpdir(), 'xeapi_public_key');
let xeapiKeyReady = false;
let xeapiKeyPending = null;
const api = {};
try {
  sdkRoot = path.dirname(sdkRequire.resolve('@neteasecloudmusicapienhanced/api/package.json'));
  const requireFromSdk = createRequire(path.join(sdkRoot, 'package.json'));
  // 注册请求密钥的接口没设置超时，这里补上。
  requireFromSdk('axios').defaults.timeout = 12000;
  const anonymousPath = path.join(os.tmpdir(), 'anonymous_token');
  if (!fs.existsSync(anonymousPath)) fs.writeFileSync(anonymousPath, '', { mode: 0o600 });
  request = sdkRequire(path.join(sdkRoot, 'util', 'request.js'));
  ({ cookieToJson } = sdkRequire(path.join(sdkRoot, 'util', 'index.js')));
  ({ getXeapiPublicKey } = sdkRequire(path.join(sdkRoot, 'util', 'xeapiKey.js')));
  for (const name of ['login_qr_key', 'login_qr_create', 'login_qr_check', 'login_status', 'song_url_v1']) {
    api[name] = sdkRequire(path.join(sdkRoot, 'module', name + '.js'));
  }
} catch {
  process.stderr.write('NetEase dependencies missing. Run scripts/install_netease_member.ps1.\n');
  process.exit(1);
}

function normalizedCookie(value) {
  if (typeof value !== 'string' || value.length > 32768 || /[\r\n\0]/.test(value)) return '';
  // 可填写导出的 Cookie，也可只填 MUSIC_U。
  if (!value.includes('=')) value = 'MUSIC_U=' + value.trim();
  const cookies = {};
  for (const item of value.split(';')) {
    const split = item.indexOf('=');
    if (split < 1) continue;
    const key = item.slice(0, split).trim();
    const content = item.slice(split + 1).trim();
    if (cookieKeys.has(key) && content && !/[\s;]/.test(content)) cookies[key] = content;
  }
  return Object.entries(cookies).map(([key, value]) => key + '=' + value).join('; ');
}

let cookie = '';
try {
  if (fs.statSync(authPath).size <= 65536) {
    cookie = normalizedCookie(JSON.parse(fs.readFileSync(authPath, 'utf8')).cookie);
  }
} catch { /* 首次安装还没有登录。 */ }
let cachedStatus = null;
let statusAt = 0;
let qr = null;
let qrBusy = false;

async function ensureXeapiKey() {
  if (xeapiKeyReady) return;
  // 同时收到多个取歌请求时，只初始化一次。
  if (!xeapiKeyPending) {
    xeapiKeyPending = (async () => {
      let previous = {};
      try {
        if (fs.statSync(xeapiKeyPath).size <= 65536) {
          const saved = JSON.parse(fs.readFileSync(xeapiKeyPath, 'utf8'));
          if (saved && saved.deviceId === device.id) previous = saved;
        }
      } catch { /* 没有缓存时，直接向网易云获取。 */ }
      const key = await getXeapiPublicKey(previous, device.id);
      if (!key || typeof key.sk !== 'string' || !key.sk ||
        typeof key.publicKey !== 'string' || Buffer.from(key.publicKey, 'base64').length !== 32 ||
        !key.version || key.deviceId !== device.id) throw new Error('Invalid request key');
      const temporary = xeapiKeyPath + '.' + crypto.randomBytes(6).toString('hex') + '.tmp';
      try {
        fs.writeFileSync(temporary, JSON.stringify(key), { mode: 0o600 });
        fs.renameSync(temporary, xeapiKeyPath);
      } finally { if (fs.existsSync(temporary)) fs.unlinkSync(temporary); }
      xeapiKeyReady = true;
    })();
  }
  try { await xeapiKeyPending; }
  finally { xeapiKeyPending = null; }
}

async function call(name, args = {}, useCookie = cookie) {
  if (name === 'song_url_v1') await ensureXeapiKey();
  const result = await api[name]({
    ...args, cookie: {
      ...cookieToJson(useCookie), deviceId: device.id,
      _ntes_nuid: device.nuid, _ntes_nnid: device.nuid + ',' + device.created,
    }, timeout: 12000,
    unblock: 'false', randomCNIP: 'false', platform: 'pc',
  }, request);
  return result && result.body && typeof result.body === 'object' ? result.body : {};
}

async function accountStatus(force = false, candidate = cookie) {
  if (!candidate || !/(?:^|;\s*)MUSIC_U=/.test(candidate)) return { logged_in: false };
  if (!force && candidate === cookie && cachedStatus && Date.now() - statusAt < 60000) {
    return cachedStatus;
  }
  const body = await call('login_status', {}, candidate);
  const data = body.data || body;
  const profile = data.profile;
  const status = profile && data.account && data.code === 200
    ? { logged_in: true, nickname: String(profile.nickname || '').slice(0, 100), vip_type: Number(profile.vipType || 0) }
    : { logged_in: false };
  if (candidate === cookie) { cachedStatus = status; statusAt = Date.now(); }
  return status;
}

function saveCookie(value) {
  const normalized = normalizedCookie(value);
  if (!/(?:^|;\s*)MUSIC_U=/.test(normalized)) throw new Error('Invalid login');
  const temporary = authPath + '.' + crypto.randomBytes(6).toString('hex') + '.tmp';
  try {
    fs.writeFileSync(temporary, JSON.stringify({ cookie: normalized, saved_at: new Date().toISOString() }), { mode: 0o600 });
    fs.renameSync(temporary, authPath);
  } finally { if (fs.existsSync(temporary)) fs.unlinkSync(temporary); }
  cookie = normalized;
  cachedStatus = null;
  statusAt = 0;
}

function secureEquals(left, right) {
  const a = Buffer.from(String(left || ''));
  const b = Buffer.from(String(right || ''));
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

function reply(response, status, value, contentType = 'application/json; charset=utf-8') {
  response.writeHead(status, {
    'Content-Type': contentType, 'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
    'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
  });
  response.end(typeof value === 'string' ? value : JSON.stringify(value));
}

async function readJson(request) {
  if (!String(request.headers['content-type'] || '').startsWith('application/json')) throw new Error('Invalid body');
  let bytes = 0;
  const chunks = [];
  for await (const chunk of request) {
    bytes += chunk.length;
    if (bytes > 65536) throw new Error('Body too large');
    chunks.push(chunk);
  }
  const body = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
  if (!body || Array.isArray(body) || typeof body !== 'object') throw new Error('Invalid body');
  return body;
}

const server = http.createServer(async (req, res) => {
  const allowedHosts = new Set(['127.0.0.1:' + port, 'localhost:' + port]);
  if (!allowedHosts.has(req.headers.host)) return reply(res, 403, { error: '本机连接地址无效。' });
  if (req.headers.origin && !['http://127.0.0.1:' + port, 'http://localhost:' + port].includes(req.headers.origin)) {
    return reply(res, 403, { error: '请从本机登录页面操作。' });
  }
  let route;
  try { route = new URL(req.url, 'http://127.0.0.1:' + port).pathname; }
  catch { return reply(res, 400, { error: '请求地址无效。' }); }
  if (req.method === 'GET' && route === '/health') {
    return reply(res, 200, { provider: 'netease_member_bridge', status: 'ok' });
  }
  if (req.method === 'GET' && ['/', '/login', '/login.js', '/login.css'].includes(route)) {
    const name = route === '/login.js' ? 'login.js' : route === '/login.css' ? 'login.css' : 'login.html';
    const type = name.endsWith('.js') ? 'text/javascript; charset=utf-8'
      : name.endsWith('.css') ? 'text/css; charset=utf-8' : 'text/html; charset=utf-8';
    const content = fs.readFileSync(path.join(__dirname, name), 'utf8').replaceAll('__CSRF_TOKEN__', csrfToken);
    return reply(res, 200, content, type);
  }
  if (req.method !== 'POST') return reply(res, 404, { error: '接口不存在。' });
  const isBot = route === '/api/song/url';
  if (isBot ? !secureEquals(req.headers.authorization, 'Bearer ' + token)
    : !secureEquals(req.headers['x-bridge-csrf'], csrfToken)) {
    return reply(res, 403, { error: '本机连接验证失败，请重新打开登录页面。' });
  }
  let body;
  try { body = await readJson(req); }
  catch { return reply(res, 400, { error: '请求内容无效。' }); }
  try {
    if (route === '/api/status') return reply(res, 200, await accountStatus(true));
    if (route === '/api/qr/start') {
      if (qrBusy) return reply(res, 409, { error: '正在处理二维码，请稍后再试。' });
      qrBusy = true;
      try {
        const keyBody = await call('login_qr_key', {}, '');
        const key = keyBody.data && keyBody.data.unikey;
        if (typeof key !== 'string' || !key) throw new Error('QR unavailable');
        const imageBody = await call('login_qr_create', { key, qrimg: true }, '');
        const image = imageBody.data && imageBody.data.qrimg;
        if (typeof image !== 'string' || !image.startsWith('data:image/png;base64,')) throw new Error('QR unavailable');
        qr = { key, created: Date.now() };
        return reply(res, 200, { qrimg: image });
      } finally { qrBusy = false; }
    }
    if (route === '/api/qr/check') {
      if (!qr || Date.now() - qr.created > 180000) return reply(res, 200, { code: 800 });
      if (qrBusy) return reply(res, 200, { code: 801 });
      qrBusy = true;
      try {
        const result = await call('login_qr_check', { key: qr.key }, '');
        const code = Number(result.code);
        if (code === 803) {
          const candidate = normalizedCookie(result.cookie);
          const status = await accountStatus(true, candidate);
          if (!status.logged_in) throw new Error('QR login unavailable');
          saveCookie(candidate);
          qr = null;
          return reply(res, 200, { code, ...status });
        }
        if (code === 800) qr = null;
        if (![800, 801, 802].includes(code)) throw new Error('QR unavailable');
        return reply(res, 200, { code });
      } finally { qrBusy = false; }
    }
    if (route === '/api/login/import') {
      const candidate = normalizedCookie(body.cookie);
      const status = await accountStatus(true, candidate);
      if (!status.logged_in) return reply(res, 401, { error: '这份登录信息无效或已过期。' });
      saveCookie(candidate);
      qr = null;
      return reply(res, 200, status);
    }
    if (route === '/api/logout') {
      cookie = ''; cachedStatus = null; statusAt = 0; qr = null;
      if (fs.existsSync(authPath)) fs.unlinkSync(authPath);
      return reply(res, 200, { logged_in: false });
    }
    if (route === '/api/song/url') {
      const songId = String(body.song_id || '');
      if (!/^\d{1,20}$/.test(songId)) return reply(res, 400, { error: '歌曲 ID 无效。' });
      const status = await accountStatus();
      if (!status.logged_in) return reply(res, 401, { error: '请在本机页面登录网易云账号。' });
      const result = await call('song_url_v1', { id: songId, level: 'exhigh' });
      // 账号 cookie 和无关字段留在桥接进程里。
      const data = Array.isArray(result.data) ? result.data.filter(item => item && String(item.id) === songId).map(item => ({
        id: item.id, code: item.code, url: item.url, size: item.size, type: item.type,
        br: item.br, time: item.time, freeTrialInfo: item.freeTrialInfo,
        freeTrialPrivilege: item.freeTrialPrivilege,
      })) : [];
      return reply(res, 200, { provider: 'netease_account', song_id: songId, code: result.code, data });
    }
    return reply(res, 404, { error: '接口不存在。' });
  } catch {
    return reply(res, 502, { error: '网易云连接暂时失败，请稍后重试；扫码失败时可用已有登录信息连接。' });
  }
});
server.requestTimeout = 20000;
server.headersTimeout = 10000;
server.on('error', () => { process.stderr.write('NetEase bridge failed to start; check its local port.\n'); process.exit(1); });
server.listen(port, '127.0.0.1', () => {
  // 监听成功后再写 token，避免重复启动覆盖正在使用的凭据。
  try { fs.writeFileSync(tokenPath, token + '\n', { mode: 0o600 }); }
  catch { process.stderr.write('Unable to write the local bridge token.\n'); process.exit(1); }
  process.stdout.write('NetEase member bridge ready: http://127.0.0.1:' + port + '/login\n');
});
