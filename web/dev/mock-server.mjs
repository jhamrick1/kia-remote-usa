// Dev mock of the car service (docs/api-contract.md). Zero dependencies.
//   node web/dev/mock-server.mjs [--port 8765] [--auth ok|needs_login|needs_otp|error] [--warnings]
// Serves web/ at / with a strict CSP, and the JSON API with a fake Sportage in Ottawa.
// Test hooks (dev only): POST /__mock/set {"auth": "...", "warnings": true, "doors": {...}, ...}
// Unlock needs {"pin": "1234"} or an assertion from a passkey registered here. The mock checks
// the challenge and the credential id only, never the signature: the real crypto is tested in
// tests/test_unlock.py and end to end by web/dev/passkey-e2e.mjs against the real server.
import http from 'node:http';
import { randomBytes } from 'node:crypto';
import { readFile, stat } from 'node:fs/promises';
import { extname, join, normalize, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
const arg = (name, def) => { const i = args.indexOf(`--${name}`); return i >= 0 ? args[i + 1] : def; };
const PORT = Number(arg('port', 8765));
const COMMAND_MS = Number(arg('command-ms', 3000));

const CSP = "default-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'";
const TYPES = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.json': 'application/json',
  '.webmanifest': 'application/manifest+json', '.woff2': 'font/woff2',
};

const iso = (ms) => {
  // local time with offset, like the service
  const d = new Date(ms);
  const off = -d.getTimezoneOffset();
  const pad = (n) => String(Math.floor(Math.abs(n))).padStart(2, '0');
  const local = new Date(ms + off * 60000).toISOString().slice(0, 19);
  return `${local}${off >= 0 ? '+' : '-'}${pad(off / 60)}:${pad(off % 60)}`;
};

const door = () => ({ open: false, locked: true });
const vehicle = {
  name: 'SPORTAGE', model: 'SPORTAGE', year: 2026,
  updated_at: iso(Date.now() - 12 * 60000),
  locked: true,
  doors: { front_left: door(), front_right: door(), rear_left: door(), rear_right: door() },
  trunk_open: false, hood_open: false, sunroof_open: false,
  windows: { front_left: false, front_right: false, rear_left: false, rear_right: false },
  engine_on: false,
  fuel_percent: 72, range_km: 540, odometer_km: 18342,
  battery_12v_percent: 88,
  climate: { on: false, set_temp_c: 21, defrost: false, steering_heat: false, rear_window_heat: false, mirror_heat: false },
  seats: { front_left: 'Off', front_right: 'Off', rear_left: 'Off', rear_right: 'Off' },
  outside_temp_c: -4,
  tires: { warning: false, front_left: false, front_right: false, rear_left: false, rear_right: false },
  warnings: { washer_fluid: false, brake_fluid: false, oil: false, smart_key_battery: false, aux_battery: false },
  location: { lat: 45.42153, lon: -75.69719, updated_at: iso(Date.now() - 14 * 60000) },
  next_service_km: 6658,
};

const MOCK_PIN = '1234';
const b64url = (buf) => Buffer.from(buf).toString('base64url');

const world = {
  passkeys: new Map(),     // id -> {id, label, rp_id, created_at, last_used_at}
  challenges: new Map(),   // challenge -> {kind, expires}
  pinFails: [],
  pinLockedUntil: 0,
  demo: true,
  auth: arg('auth', 'ok'),
  auth_error: null,
  otp: { has_email: true, has_sms: true },
  commands: new Map(),
  log: [],
  pending: null,
  lastWake: 0,
  presets: [
    { id: 'winter', name: 'Winter morning', icon: 'snow', options: { set_temp_c: 22, defrost: true, heating: 1, front_left_seat: 8, duration_min: 10 } },
    { id: 'quick', name: 'Quick warm', icon: 'bolt', options: { set_temp_c: 21, duration_min: 5 } },
    { id: 'summer', name: 'Summer cool', icon: 'sun', options: { set_temp_c: 17, front_left_seat: 3, duration_min: 10 } },
  ],
};
if (args.includes('--warnings')) applyWarnings(true);

function applyWarnings(on) {
  vehicle.warnings.washer_fluid = on;
  vehicle.warnings.smart_key_battery = on;
  vehicle.tires.warning = on;
  vehicle.tires.rear_left = on;
}

function state() {
  return {
    demo: world.demo,
    auth: world.auth,
    auth_error: world.auth === 'error' ? (world.auth_error || 'Kia says the account is temporarily locked. Try again in 30 minutes.') : null,
    otp: world.auth === 'needs_otp' ? world.otp : null,
    vehicle: world.auth === 'ok' ? vehicle : null,
    pending: world.pending ? world.commands.get(world.pending) : null,
  };
}

function finishCommand(c) {
  if (world.failNext) {
    world.failNext = false;
    Object.assign(c, { status: 'failed', message: 'Kia says the car did not respond. It may be out of cell coverage.', finished_at: iso(Date.now()) });
    world.pending = null;
    return;
  }
  const v = vehicle;
  const o = c.options || {};
  if (c.action === 'lock') { v.locked = true; for (const d of Object.values(v.doors)) d.locked = true; }
  if (c.action === 'unlock') { v.locked = false; for (const d of Object.values(v.doors)) d.locked = false; }
  if (c.action === 'climate_start') {
    v.climate = { on: true, set_temp_c: o.set_temp_c ?? 21, defrost: !!o.defrost,
      steering_heat: o.heating === 1, rear_window_heat: o.heating === 1, mirror_heat: o.heating === 1 };
    v.engine_on = true;
  }
  if (c.action === 'climate_stop') { v.climate.on = false; v.engine_on = false; }
  v.updated_at = iso(Date.now());
  c.status = 'success';
  c.message = null;
  c.finished_at = iso(Date.now());
  world.pending = null;
}

function issue(kind, extra = {}) {
  const c = b64url(randomBytes(32));
  world.challenges.set(c, { kind, expires: Date.now() + 120000, ...extra });
  return c;
}

function take(credential, kind) {
  try {
    const cd = JSON.parse(Buffer.from(credential.response.clientDataJSON, 'base64url').toString('utf8'));
    const rec = world.challenges.get(cd.challenge);
    world.challenges.delete(cd.challenge);
    return rec && rec.kind === kind && rec.expires > Date.now() ? rec : null;
  } catch { return null; }
}

/** null when the PIN is right, else [status, body] */
function pinRefusal(pin) {
  const now = Date.now();
  if (world.pinLockedUntil > now) {
    const m = Math.ceil((world.pinLockedUntil - now) / 60000);
    return [429, { error: `Too many wrong PINs. The PIN is refused for ${m} more minute${m === 1 ? '' : 's'}. Use the fingerprint, or wait.`, needs: 'verification' }];
  }
  if (pin === MOCK_PIN) { world.pinFails = []; return null; }
  world.pinFails = world.pinFails.filter((t) => now - t < 15 * 60000).concat(now);
  if (world.pinFails.length >= 5) {
    world.pinLockedUntil = now + 15 * 60000;
    world.pinFails = [];
    return [429, { error: 'Too many wrong PINs. The PIN is refused for 15 more minutes. Use the fingerprint, or wait.', needs: 'verification' }];
  }
  return [403, { error: "That PIN isn't right.", needs: 'verification' }];
}

const hostName = (req) => (req.headers.host || '').replace(/:\d+$/, '');
const publicPasskeys = () => [...world.passkeys.values()];

async function body(req) {
  let raw = '';
  for await (const chunk of req) raw += chunk;
  if (!raw) return {};
  try { return JSON.parse(raw); } catch { return null; }
}

function json(res, code, data) {
  res.writeHead(code, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
  res.end(JSON.stringify(data));
}

async function apiRoute(req, res, path) {
  const b = req.method === 'GET' ? {} : await body(req);
  if (b === null) return json(res, 400, { error: 'The request body is not valid JSON.' });

  if (req.method === 'GET' && path === '/api/state') return json(res, 200, state());
  if (req.method === 'POST' && path === '/api/login') {
    if (!b.email || !b.password || !/^\d{4}$/.test(b.pin || '')) return json(res, 400, { error: 'Email, password and a 4-digit PIN are needed.' });
    world.auth = 'needs_otp';
    return json(res, 200, state());
  }
  if (req.method === 'POST' && path === '/api/otp/send') return json(res, 200, { sent: true });
  if (req.method === 'POST' && path === '/api/otp/verify') {
    if (String(b.code || '').length < 4) return json(res, 400, { error: 'That code is not right. Check the latest message from Kia.' });
    world.auth = 'ok';
    return json(res, 200, state());
  }
  if (req.method === 'POST' && path === '/api/logout') { world.auth = 'needs_login'; return json(res, 200, state()); }
  if (req.method === 'POST' && path === '/api/refresh') {
    if (b.force) {
      if (Date.now() - world.lastWake < 10 * 60000) return json(res, 429, { error: 'The car was woken less than 10 minutes ago.' });
      world.lastWake = Date.now();
      await new Promise((r) => setTimeout(r, 2500));
      vehicle.updated_at = iso(Date.now());
    }
    return json(res, 200, state());
  }
  if (req.method === 'POST' && path === '/api/unlock/challenge') {
    const mine = publicPasskeys().filter((p) => p.rp_id === hostName(req));
    if (!mine.length) return json(res, 404, { error: 'No fingerprint is set up for unlock at this address.', needs: 'pin' });
    return json(res, 200, { challenge: issue('unlock'), rpId: hostName(req), timeout: 120000, userVerification: 'required',
      allowCredentials: mine.map((p) => ({ id: p.id, type: 'public-key' })) });
  }
  if (req.method === 'POST' && path === '/api/passkeys/register/options') {
    const label = typeof b.label === 'string' && b.label.trim() ? b.label.trim().slice(0, 40) : 'Phone';
    return json(res, 200, {
      challenge: issue('register', { label }), rp: { name: 'Car Remote', id: hostName(req) },
      user: { id: b64url(randomBytes(16)), name: `Car unlock (${label})`, displayName: `Car unlock (${label})` },
      pubKeyCredParams: [{ type: 'public-key', alg: -7 }, { type: 'public-key', alg: -257 }],
      timeout: 120000, attestation: 'none',
      authenticatorSelection: { authenticatorAttachment: 'platform', residentKey: 'preferred', userVerification: 'required' },
      excludeCredentials: publicPasskeys().map((p) => ({ id: p.id, type: 'public-key' })),
    });
  }
  if (req.method === 'POST' && path === '/api/passkeys/register') {
    if (!b.credential || typeof b.credential.id !== 'string') return json(res, 400, { error: 'Send the new passkey and the Kia PIN.' });
    const refused = pinRefusal(b.pin);
    if (refused) return json(res, ...refused);
    const rec = take(b.credential, 'register');
    if (!rec) return json(res, 403, { error: 'That check expired or was already used. Try again.', needs: 'verification' });
    const item = { id: b.credential.id, label: rec.label, rp_id: hostName(req), created_at: iso(Date.now()), last_used_at: null };
    world.passkeys.set(item.id, item);
    return json(res, 200, item);
  }
  if (req.method === 'GET' && path === '/api/passkeys') return json(res, 200, { passkeys: publicPasskeys() });
  const pk = path.match(/^\/api\/passkeys\/([\w-]+)$/);
  if (req.method === 'DELETE' && pk) {
    if (!world.passkeys.delete(pk[1])) return json(res, 404, { error: 'No passkey with that id.' });
    return json(res, 200, { passkeys: publicPasskeys() });
  }
  if (req.method === 'POST' && path === '/api/command') {
    const ok = ['lock', 'unlock', 'climate_start', 'climate_stop', 'hazard', 'horn_lights'];
    if (!ok.includes(b.action)) return json(res, 400, { error: 'Unknown action.' });
    if (world.pending) return json(res, 409, { error: 'Another command is still running.' });
    let via = null;
    if (b.action === 'unlock') {
      if (b.assertion) {
        const known = world.passkeys.get(b.assertion.rawId || b.assertion.id);
        if (!take(b.assertion, 'unlock') || !known) return json(res, 403, { error: "The fingerprint check didn't go through. Try again or use the PIN.", needs: 'verification' });
        known.last_used_at = iso(Date.now());
        via = `passkey:${known.label}`;
      } else if (b.pin !== undefined) {
        const refused = pinRefusal(b.pin);
        if (refused) return json(res, ...refused);
        via = 'pin';
      } else {
        return json(res, 403, { error: 'Unlocking needs your fingerprint or the Kia PIN.', needs: 'verification' });
      }
    }
    const id = `cmd_${Date.now().toString(36)}`;
    const c = { id, action: b.action, options: b.options || null, status: 'pending', message: null, sent_at: iso(Date.now()), finished_at: null, who: 'local', via };
    world.commands.set(id, c);
    world.log.unshift(c);
    world.log.length = Math.min(world.log.length, 50);
    world.pending = id;
    if (!world.holdCommands) setTimeout(() => finishCommand(c), COMMAND_MS);
    return json(res, 200, { id, status: 'pending' });
  }
  const m = path.match(/^\/api\/command\/([\w-]+)$/);
  if (req.method === 'GET' && m) {
    const c = world.commands.get(m[1]);
    return c ? json(res, 200, c) : json(res, 404, { error: 'No command with that id.' });
  }
  if (req.method === 'GET' && path === '/api/log') return json(res, 200, { entries: world.log });
  if (req.method === 'GET' && path === '/api/presets') return json(res, 200, { presets: world.presets });
  if (req.method === 'PUT' && path === '/api/presets') {
    if (!Array.isArray(b.presets)) return json(res, 400, { error: 'Presets must be a list.' });
    world.presets = b.presets;
    return json(res, 200, { presets: world.presets });
  }
  return json(res, 404, { error: 'Not found.' });
}

// Dev-only knobs for screenshots: never part of the real service.
async function mockRoute(req, res) {
  const b = await body(req) || {};
  if (b.auth) world.auth = b.auth;
  if ('warnings' in b) applyWarnings(!!b.warnings);
  if (b.vehicle) deepAssign(vehicle, b.vehicle);
  if ('holdCommands' in b) world.holdCommands = !!b.holdCommands;
  if ('failNext' in b) world.failNext = !!b.failNext;
  if (b.seedLog) {
    const now = Date.now();
    const mk = (action, status, minAgo, message = null) => ({ id: `cmd_seed${minAgo}`, action, options: null, status, message,
      sent_at: iso(now - minAgo * 60000), finished_at: iso(now - minAgo * 60000 + 20000), who: 'local', via: action === 'unlock' ? 'pin' : null });
    world.log = [mk('lock', 'success', 47), mk('climate_start', 'success', 62),
      mk('unlock', 'failed', 190, 'Kia says the car did not respond. It may be out of cell coverage.'), mk('horn_lights', 'success', 1500)];
  }
  if (b.finishPending && world.pending) finishCommand(world.commands.get(world.pending));
  if (b.resetUnlock) { world.passkeys.clear(); world.challenges.clear(); world.pinFails = []; world.pinLockedUntil = 0; }
  json(res, 200, state());
}

function deepAssign(target, src) {
  for (const [k, v] of Object.entries(src)) {
    if (v && typeof v === 'object' && !Array.isArray(v) && target[k] && typeof target[k] === 'object') deepAssign(target[k], v);
    else target[k] = v;
  }
}

async function staticRoute(req, res, path) {
  if (path === '/') path = '/index.html';
  const file = normalize(join(ROOT, decodeURIComponent(path)));
  if (!file.startsWith(ROOT) || file.includes(`${ROOT}\\dev`) || file.includes(`${ROOT}/dev`)) {
    res.writeHead(404); return res.end();
  }
  try {
    const st = await stat(file);
    if (!st.isFile()) throw new Error('not a file');
    const data = await readFile(file);
    res.writeHead(200, {
      'Content-Type': TYPES[extname(file)] || 'application/octet-stream',
      'Cache-Control': 'no-cache',
      'Content-Security-Policy': CSP,
      'X-Content-Type-Options': 'nosniff',
      'Referrer-Policy': 'no-referrer',
    });
    res.end(data);
  } catch {
    res.writeHead(404, { 'Content-Type': 'text/plain' });
    res.end('Not found');
  }
}

http.createServer(async (req, res) => {
  const path = new URL(req.url, 'http://x').pathname;
  try {
    if (path.startsWith('/api/')) return await apiRoute(req, res, path);
    if (path === '/__mock/set' && req.method === 'POST') return await mockRoute(req, res);
    return await staticRoute(req, res, path);
  } catch (err) {
    json(res, 500, { error: 'The mock crashed.' });
    console.error(err);
  }
}).listen(PORT, '127.0.0.1', () => console.log(`mock car service on http://127.0.0.1:${PORT}`));
