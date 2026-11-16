import { createHash } from 'node:crypto';
import { NeteaseRealtimeTransport } from './vendor/realtimeTransport.js';

// The native SDK runs here, outside the existing account/song bridge process.
// Room messages live only in this bounded memory queue.
export class RoomChatWorker {
  constructor(factory = (onMessage) => new NeteaseRealtimeTransport(true, onMessage)) {
    this.roomId = '';
    this.accountId = '';
    this.sequence = 0;
    this.messages = [];
    this.seen = new Set();
    this.sent = new Set();
    this.transport = factory((message) => this.receive(message));
  }

  receive(message) {
    if (!this.roomId || message?.category !== 'text' || typeof message.text !== 'string') return;
    const text = message.text.trim().slice(0, 500);
    if (!text) return;
    const key = String(message.messageId || createHash('sha256').update([
      this.roomId, message.senderId || '', message.timetagMs || 0, text,
    ].join('\0')).digest('hex'));
    if (this.seen.has(key)) return;
    this.seen.add(key);
    if (this.seen.size > 500) this.seen.delete(this.seen.values().next().value);
    this.messages.push({ cursor: ++this.sequence, message_id: key, text,
      own: message.senderId === this.accountId || this.sent.has(key),
      received_at: Number(message.receivedAtMs) || Date.now() });
    if (this.messages.length > 100) this.messages.shift();
  }

  state(status = undefined) {
    const connected = this.transport.getStatus().connected === true;
    return { provider: 'netease_chat', code: 200, success: true,
      status: status || (connected ? 'connected' : 'disconnected'),
      connected, room_id: this.roomId, cursor: this.sequence };
  }

  async dispatch(request) {
    const action = request.action;
    const roomId = String(request.room_id || '');
    if (!/^[a-zA-Z0-9_-]{1,128}$/.test(roomId)) throw new Error('invalid_room');
    if (action === 'connect') {
      if (!/^\d{1,20}$/.test(String(request.chat_room_id || '')) || !request.credentials?.accId || !request.credentials?.token) {
        throw new Error('invalid_credentials');
      }
      if (this.roomId !== roomId) {
        if (this.roomId) await this.transport.disconnect();
        this.messages = [];
        this.seen.clear();
        this.sent.clear();
      }
      this.roomId = roomId;
      this.accountId = String(request.credentials.accId);
      await this.transport.connect({ roomId, chatRoomId: String(request.chat_room_id),
        credentials: request.credentials, memberProfile: request.member_profile });
      return this.state('connected');
    }
    if (this.roomId !== roomId) throw new Error('wrong_room');
    if (action === 'disconnect') {
      await this.transport.disconnect();
      this.messages = [];
      this.seen.clear();
      this.sent.clear();
      this.accountId = '';
      return this.state('disconnected');
    }
    if (action === 'status') return this.state();
    if (!this.transport.getStatus().connected) throw new Error('not_connected');
    if (action === 'messages') {
      const requested = Number(request.cursor) || 0;
      const cursor = requested > this.sequence ? 0 : requested;
      const messages = this.messages.filter(message => message.cursor > cursor).slice(0, 50);
      return { ...this.state('messages'), messages,
        cursor: messages.at(-1)?.cursor ?? Math.min(cursor, this.sequence) };
    }
    if (action === 'send') {
      const text = typeof request.text === 'string' ? request.text.trim() : '';
      if (!text || text.length > 500 || /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/u.test(text)) throw new Error('invalid_text');
      // This promise resolves only after the SDK's sendMsg callback says 200.
      const result = await this.transport.sendChatRoomText(text);
      if (result?.ok !== true || result.code !== 200 || result.roomId !== roomId) throw new Error('send_failed');
      this.sent.add(result.messageId);
      if (this.sent.size > 500) this.sent.delete(this.sent.values().next().value);
      for (const message of this.messages) if (message.message_id === result.messageId) message.own = true;
      return { ...this.state('sent'), message_id: result.messageId };
    }
    throw new Error('invalid_action');
  }
}

if (typeof process.send === 'function') {
  // Suppress SDK diagnostics and message bodies; only bounded IPC replies leave
  // this worker. No cookie or IM credential is written into the chat transcript.
  for (const method of ['log', 'info', 'warn', 'error', 'debug', 'trace']) console[method] = () => {};
  const worker = new RoomChatWorker();
  let queue = Promise.resolve();
  process.on('message', request => {
    queue = queue.then(async () => {
      try {
        const result = await worker.dispatch(request);
        process.send?.({ id: request.id, result });
      } catch (error) {
        const known = new Set(['invalid_room', 'wrong_room', 'not_connected', 'invalid_text', 'invalid_action', 'send_failed']);
        process.send?.({ id: request.id, error: known.has(error?.message) ? error.message : 'unavailable' });
      }
    });
  });
  process.on('disconnect', () => process.exit(0));
}
