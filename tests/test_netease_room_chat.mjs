import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { RoomChatWorker } from '../integrations/netease/realtime/worker.mjs';
import { NeteaseRealtimeTransport, buildRealtimeChatTextMessage } from '../integrations/netease/realtime/vendor/realtimeTransport.js';
const require = createRequire(import.meta.url);
const { RoomChatBridge } = require('../integrations/netease/realtime/bridge.cjs');

function fixture() {
  let connected = false;
  const sends = [];
  const transport = { getStatus: () => ({ connected }),
    connect: async () => { connected = true; },
    disconnect: async () => { connected = false; },
    sendChatRoomText: async text => {
      sends.push(text);
      return { ok: true, code: 200, roomId: 'room-a', messageId: 'sent-1' };
    } };
  const worker = new RoomChatWorker(() => transport);
  const connect = room => worker.dispatch({ action: 'connect', room_id: room,
    chat_room_id: '12345', credentials: { accId: 'bot-local-test', token: 'test-only' } });
  return { worker, transport, sends, connect };
}

test('current room messages deduplicate, mark own echoes, and never cross a room switch', async () => {
  const { worker, connect } = fixture();
  await connect('room-a');
  const message = { category: 'text', text: '这首歌好听', senderId: 'friend-local-test', messageId: 'm1' };
  worker.receive(message);
  worker.receive(message);
  worker.receive({ ...message, messageId: 'm2', senderId: 'bot-local-test' });
  const result = await worker.dispatch({ action: 'messages', room_id: 'room-a', cursor: 0 });
  assert.equal(result.messages.length, 2);
  assert.equal(result.messages[0].own, false);
  assert.equal(result.messages[1].own, true);
  assert.equal(JSON.stringify(result).includes('test-only'), false);
  await connect('room-b');
  assert.deepEqual((await worker.dispatch({ action: 'messages', room_id: 'room-b', cursor: 0 })).messages, []);
  await assert.rejects(worker.dispatch({ action: 'send', room_id: 'room-a', text: '不能串房' }), /wrong_room/);
});

test('bounded message pages do not skip later messages or hang after a worker cursor reset', async () => {
  const { worker, connect } = fixture();
  await connect('room-a');
  for (let i = 0; i < 120; i++) worker.receive({ category: 'text', text: '内容' + i, messageId: String(i) });
  const first = await worker.dispatch({ action: 'messages', room_id: 'room-a', cursor: 0 });
  const next = await worker.dispatch({ action: 'messages', room_id: 'room-a', cursor: first.cursor });
  assert.equal(first.messages.length, 50);
  assert.equal(next.messages.length, 50);
  assert.equal(new Set([...first.messages, ...next.messages].map(m => m.message_id)).size, 100);
  const reset = await worker.dispatch({ action: 'messages', room_id: 'room-a', cursor: 99999 });
  assert.equal(reset.messages.length, 50);
});

test('invalid messages and disconnected rooms cannot call native send', async () => {
  const { worker, connect, sends } = fixture();
  await connect('room-a');
  for (const text of ['', ' ', 'x'.repeat(501), '\u0000secret']) {
    await assert.rejects(worker.dispatch({ action: 'send', room_id: 'room-a', text }), /invalid_text/);
  }
  await worker.dispatch({ action: 'disconnect', room_id: 'room-a' });
  await assert.rejects(worker.dispatch({ action: 'send', room_id: 'room-a', text: '不能发' }), /not_connected/);
  assert.equal(sends.length, 0);
});

test('worker reports failure when the transport does not confirm sending', async () => {
  const { worker, connect, transport } = fixture();
  await connect('room-a');
  transport.sendChatRoomText = async () => ({ ok: false, code: 403 });
  await assert.rejects(worker.dispatch({ action: 'send', room_id: 'room-a', text: '测试' }), /send_failed/);
});

for (const code of [200, 403]) test(`native SDK callback ${code} determines send success`, async () => {
  const handlers = new Map();
  let submitted;
  const chatroom = { on: (name, callback) => handlers.set(name, callback),
    sendMsg: (room, message) => { submitted = message; return true; } };
  const transport = new NeteaseRealtimeTransport();
  transport.chatroom = chatroom;
  transport.roomNumber = 12345;
  transport.status = { connected: true, roomId: 'room-a', chatRoomId: '12345' };
  transport.installRuntimeHandlers(chatroom);
  let settled = false;
  const pending = transport.sendChatRoomText('测试文本').finally(() => { settled = true; });
  await Promise.resolve();
  assert.equal(settled, false, 'Submitting locally is not a server ACK');
  handlers.get('sendMsg')(99999, 200, submitted);
  await Promise.resolve();
  assert.equal(settled, false, 'An ACK from another room is ignored');
  handlers.get('sendMsg')(12345, code, submitted);
  if (code === 200) assert.equal((await pending).code, 200);
  else await assert.rejects(pending, /403/);
});

test('wire format is an actual text ChatRoom message with listenTogether metadata', () => {
  const value = buildRealtimeChatTextMessage('本鱼在听', 'room-a');
  assert.equal(value.msg_type_, 0);
  assert.equal(value.msg_attach_, '本鱼在听');
  const ext = JSON.parse(value.msg_setting_.ext_);
  assert.equal(ext.clientExt.bizType, 'listenTogether');
  assert.equal(ext.clientExt.roomId, 'room-a');
});

test('bridge checks real room membership before handing off any message', async () => {
  let invoked = 0;
  const bridge = new RoomChatBridge({ dataRoot: 'unused', getCookie: () => 'not-output',
    getProfile: () => ({}), getRoom: async () => ({ in_room: true, room_id: 'room-b' }) });
  bridge.invoke = async () => { invoked++; throw new Error('should not run'); };
  const response = await bridge.handle({ action: 'send', room_id: 'room-a', text: '不能串房' });
  assert.equal(response.success, false);
  assert.equal(response.status, 'not_in_room');
  assert.equal(invoked, 0);
  assert.equal(JSON.stringify(response).includes('not-output'), false);
});

test('disconnect invalidates a pending credential lookup before any SDK worker can start', async () => {
  let finishCredentials;
  let didRequestCredentials;
  const credentialsRequested = new Promise(resolve => { didRequestCredentials = resolve; });
  let invoked = 0;
  const bridge = new RoomChatBridge({ dataRoot: 'unused', getCookie: () => 'not-output',
    getProfile: () => ({}), getRoom: async () => ({ in_room: true, room_id: 'room-a', chat_room_id: '12345' }),
    getCredentials: async () => {
      didRequestCredentials();
      return await new Promise(resolve => { finishCredentials = resolve; });
    } });
  bridge.invoke = async () => { invoked++; return { success: true }; };
  const pending = bridge.handle({ action: 'connect', room_id: 'room-a' });
  await credentialsRequested;
  const disconnected = await bridge.handle({ action: 'disconnect', room_id: 'room-a' });
  finishCredentials({ accId: 'test-bot', token: 'test-token' });
  assert.equal(disconnected.success, true);
  assert.equal((await pending).success, false);
  assert.equal(invoked, 0);
});
