'use strict';
const { fork } = require('node:child_process');
const { randomUUID } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const ERROR_TEXT = {
  invalid_room: '一起听房间编号无效',
  wrong_room: '当前文字连接属于另一个房间，请先加入这次邀请',
  not_in_room: '当前账号没有在这次一起听房间里',
  not_connected: '一起听房间的文字连接尚未接通',
  invalid_text: '房间留言需要包含文字，且不能超过500个字符',
  invalid_action: '房间文字操作无效',
  send_failed: '房间没有确认这条留言发送成功',
  timeout: '房间文字连接暂时超时，尚未确认成功',
  unavailable: '网易云房间文字通道暂时未接通，QQ聊天和听歌仍可继续',
};

async function realtimeCredentials(cookie) {
  // Fixed NetEase endpoint. Account cookies stay in the local account bridge.
  const url = 'https://interface3.music.163.com/api/middle/im/token/get?bizName=music_listenTogether';
  const response = await fetch(url, { signal: AbortSignal.timeout(12000), headers: {
    cookie, accept: 'application/json', 'accept-language': 'zh-CN,zh;q=0.9',
    'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  } });
  let size = 0;
  const parts = [];
  for await (const part of response.body) {
    size += part.length;
    if (size > 65536) throw new Error('unavailable');
    parts.push(part);
  }
  const body = JSON.parse(Buffer.concat(parts).toString('utf8'));
  const data = body.data || {};
  if (!response.ok || body.code !== 200 || typeof data.accId !== 'string' || typeof data.token !== 'string') throw new Error('unavailable');
  return { accId: data.accId, token: data.token,
    addresses: Array.isArray(data.addr) ? data.addr.filter(v => typeof v === 'string').slice(0, 8) : [] };
}

class RoomChatBridge {
  constructor({ dataRoot, getCookie, getRoom, getProfile, forkWorker = fork, getCredentials = realtimeCredentials }) {
    Object.assign(this, { dataRoot, getCookie, getRoom, getProfile, forkWorker, getCredentials });
    this.worker = null;
    this.pending = new Map();
    this.generation = 0;
    this.roomId = '';
  }

  invoke(parameters) {
    if (!this.worker) {
      const cwd = path.join(this.dataRoot, 'room-chat');
      fs.mkdirSync(cwd, { recursive: true });
      const child = this.forkWorker(path.join(__dirname, 'worker.mjs'), [], {
        stdio: ['ignore', 'ignore', 'ignore', 'ipc'], windowsHide: true, cwd,
      });
      this.worker = child;
      child.on('message', message => {
        const pending = this.pending.get(message?.id);
        if (!pending) return;
        this.pending.delete(message.id);
        clearTimeout(pending.timer);
        if (message.error) pending.reject(new Error(message.error));
        else pending.resolve(message.result);
      });
      const died = () => {
        if (this.worker !== child) return;
        this.worker = null;
        for (const pending of this.pending.values()) {
          clearTimeout(pending.timer);
          pending.reject(new Error('unavailable'));
        }
        this.pending.clear();
      };
      child.on('exit', died);
      child.on('error', died);
    }
    const id = randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error('timeout'));
      }, parameters.action === 'connect' ? 45000 : 12000);
      this.pending.set(id, { resolve, reject, timer });
      this.worker.send({ ...parameters, id }, error => {
        if (!error) return;
        clearTimeout(timer);
        this.pending.delete(id);
        reject(new Error('unavailable'));
      });
    });
  }

  async handle(body) {
    const roomId = String(body.room_id || '');
    const action = String(body.action || 'status');
    try {
      if (!/^[a-zA-Z0-9_-]{1,128}$/.test(roomId)) throw new Error('invalid_room');
      if (!['connect', 'status', 'messages', 'send', 'disconnect'].includes(action)) throw new Error('invalid_action');
      if (action === 'send' && (typeof body.text !== 'string' || !body.text.trim() || body.text.trim().length > 500)) throw new Error('invalid_text');
      if (action === 'disconnect') {
        if (this.roomId && this.roomId !== roomId) throw new Error('wrong_room');
        // Killing this isolated SDK worker also invalidates a connect that is
        // still waiting for an enter ticket. It cannot reconnect after leave.
        this.close();
        return { provider: 'netease_chat', code: 200, success: true, connected: false,
          status: 'disconnected', room_id: roomId, cursor: 0 };
      }
      const generation = action === 'connect' ? ++this.generation : this.generation;
      if (action === 'connect' && this.roomId && this.roomId !== roomId) this.stopWorker();
      if (action === 'connect') this.roomId = roomId;
      const state = await this.getRoom();
      if (generation !== this.generation) throw new Error('not_connected');
      if (!state?.in_room || state.room_id !== roomId) throw new Error('not_in_room');
      if (action === 'connect') {
        if (!/^\d{1,20}$/.test(state.chat_room_id || '')) throw new Error('unavailable');
        const credentials = await this.getCredentials(this.getCookie());
        if (generation !== this.generation) throw new Error('not_connected');
        const result = await this.invoke({ action, room_id: roomId, chat_room_id: state.chat_room_id,
          credentials, member_profile: this.getProfile() });
        if (generation !== this.generation) throw new Error('not_connected');
        return result;
      }
      if (!this.worker) throw new Error('not_connected');
      return await this.invoke({ action, room_id: roomId, text: body.text,
        cursor: Math.max(0, Math.min(Number.MAX_SAFE_INTEGER, Number(body.cursor) || 0)) });
    } catch (error) {
      const status = Object.hasOwn(ERROR_TEXT, error?.message) ? error.message : 'unavailable';
      return { provider: 'netease_chat', code: 200, success: false, connected: false,
        status, room_id: roomId, message: ERROR_TEXT[status] };
    }
  }

  close() {
    this.generation++;
    this.roomId = '';
    this.stopWorker();
  }

  stopWorker() {
    const child = this.worker;
    this.worker = null;
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(new Error('not_connected'));
    }
    this.pending.clear();
    if (child) child.kill();
  }
}

module.exports = { RoomChatBridge, realtimeCredentials };
