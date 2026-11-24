// WebSocket client: calls (request/response), server events, and server questions (dialogs).

const listeners = new Map(); // type → Set<fn>
const pending = new Map(); // call id → {resolve, reject}
let ws = null;
let nextId = 1;
let askHandler = null;
let retry = 0;
const queue = [];

export function on(type, fn) {
  if (!listeners.has(type)) listeners.set(type, new Set());
  listeners.get(type).add(fn);
}

function emit(type, msg) {
  for (const fn of listeners.get(type) || []) {
    try { fn(msg); } catch (e) { console.error(e); }
  }
}

export function onAsk(fn) { askHandler = fn; }

export function call(method, params = {}) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject, method });
    send({ type: 'call', id, method, params });
  });
}

export function answer(id, value) { send({ type: 'answer', id, value }); }

function send(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
  else queue.push(msg);
}

export function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => {
    retry = 0;
    emit('open');
    while (queue.length) ws.send(JSON.stringify(queue.shift()));
  };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    switch (msg.type) {
      case 'result': {
        const p = pending.get(msg.id);
        if (!p) return;
        pending.delete(msg.id);
        if ('error' in msg) p.reject(new Error(msg.error));
        else p.resolve(msg.result);
        return;
      }
      case 'ask':
        if (askHandler) askHandler(msg);
        return;
      default:
        emit(msg.type, msg);
    }
  };
  ws.onclose = () => {
    for (const [, p] of pending) p.reject(new Error('connection lost'));
    pending.clear();
    emit('close');
    retry = Math.min(retry + 1, 6);
    setTimeout(connect, 300 * 2 ** retry);
  };
}
