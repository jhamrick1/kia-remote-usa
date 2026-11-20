// A short, local log of what the app did around going to the background and coming back
// (requests, how long they took, how they failed). Sent to the PC when it can be reached, where
// it lands in server.log, so a "stuck after reopening" report can be read instead of guessed.
// No car data, no credentials: event names, timings and error kinds only.
const KEY = 'sportage.diag';
const MAX = 80;
let sending = false;

function read() {
  try { const list = JSON.parse(localStorage.getItem(KEY) || '[]'); return Array.isArray(list) ? list : []; } catch { return []; }
}

export function note(event, detail) {
  try {
    const list = read();
    const item = { t: new Date().toISOString(), e: String(event).slice(0, 40) };
    if (detail !== undefined && detail !== '') item.d = String(detail).slice(0, 160);
    list.push(item);
    localStorage.setItem(KEY, JSON.stringify(list.slice(-MAX)));
  } catch { /* storage blocked: no log */ }
}

/** Send what's queued; drop it once the PC has it. Plain fetch so it can't recurse through api.js. */
export async function flush() {
  if (sending) return;
  const list = read();
  if (!list.length) return;
  sending = true;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 8000);
  try {
    const res = await fetch('api/diag', {
      method: 'POST', cache: 'no-store', redirect: 'manual', signal: ctrl.signal,
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ events: list }),
    });
    if (res.ok) {
      const sent = new Set(list.map((x) => x.t + x.e));
      try { localStorage.setItem(KEY, JSON.stringify(read().filter((x) => !sent.has(x.t + x.e)))); } catch { /* ignore */ }
    }
  } catch { /* try again next time */ } finally {
    clearTimeout(timer);
    sending = false;
  }
}
