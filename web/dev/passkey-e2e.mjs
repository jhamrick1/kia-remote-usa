// End to end: the REAL car service (--demo, a temp data folder) with Chromium's virtual
// authenticator standing in for the phone's fingerprint. Real WebAuthn, real signatures.
//   node web/dev/passkey-e2e.mjs        (needs .venv and Playwright, see pw.mjs)
// Writes web/dev/shots/e2e-*.png and exits non-zero on any problem.
import { spawn } from 'node:child_process';
import { mkdtemp, rm, mkdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { launch } from './pw.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, '..', '..');
const OUT = join(HERE, 'shots');
const PORT = 8799;
const PAGE = `http://localhost:${PORT}/`;      // a name, so WebAuthn has an RP ID
const API = `http://127.0.0.1:${PORT}/api/`;   // the test's own checks

const problems = [];
const check = (ok, what) => { if (!ok) problems.push(what); console.log(`${ok ? 'ok  ' : 'FAIL'} ${what}`); };
await mkdir(OUT, { recursive: true });
const data = await mkdtemp(join(tmpdir(), 'kia-e2e-'));
const python = join(REPO, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const server = spawn(python, ['-m', 'server', '--demo', '--port', String(PORT), '--data-dir', data], { cwd: REPO, stdio: 'pipe' });
let serverOut = '';
server.stdout.on('data', (d) => { serverOut += d; });
server.stderr.on('data', (d) => { serverOut += d; });

const get = async (path) => (await fetch(API + path)).json();
const lastEntry = async () => (await get('log')).entries[0];

let browser;
try {
  for (let i = 0; ; i += 1) {
    try { await fetch(API + 'state'); break; } catch { if (i > 60) throw new Error(`server didn't start:\n${serverOut}`); }
    await new Promise((r) => setTimeout(r, 250));
  }
  browser = await launch();
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, serviceWorkers: 'block' });
  const page = await ctx.newPage();
  page.on('console', (m) => {
    if (m.type() === 'error' && !/status of 40[34]/.test(m.text())) problems.push(`console: ${m.text()}`);
  });
  page.on('pageerror', (e) => problems.push(`page error: ${e.message}`));
  await page.addInitScript(() => {
    document.addEventListener('securitypolicyviolation', (e) => console.error(`CSP violation: ${e.violatedDirective} ${e.blockedURI}`));
  });
  const cdp = await ctx.newCDPSession(page);
  await cdp.send('WebAuthn.enable');
  const { authenticatorId } = await cdp.send('WebAuthn.addVirtualAuthenticator', { options: {
    protocol: 'ctap2', transport: 'internal', hasResidentKey: true, hasUserVerification: true,
    isUserVerified: true, automaticPresenceSimulation: true } });

  const shot = (name) => page.screenshot({ path: join(OUT, `e2e-${name}.png`) });
  const waitDone = async () => {
    await page.waitForFunction(() => document.querySelector('.cmd-status.is-success, .cmd-status.is-failed'), null, { timeout: 15000 });
  };
  const clearPill = () => page.evaluate(() => { document.getElementById('cmd-status').hidden = true; document.getElementById('cmd-status').className = 'cmd-status'; });

  await page.goto(PAGE);
  await page.waitForSelector('#dash:not([hidden])');
  await page.waitForTimeout(500);

  // 1. No passkey yet: the PIN sheet, with "Use fingerprint next time".
  await page.click('#act-unlock');
  await page.click('#confirm-ok');
  await page.waitForSelector('#pin-sheet[open]');
  await page.waitForTimeout(300);
  check(await page.locator('#pin-remember-row').isVisible(), 'PIN sheet offers "Use fingerprint next time"');
  await page.fill('#pin-input', '1234');
  await page.click('#pin-remember-row');
  await shot('1-pin-and-remember');
  await page.click('#pin-ok');
  await waitDone();
  let entry = await lastEntry();
  check(entry.action === 'unlock' && entry.status === 'success' && entry.via === 'pin', `unlock with the PIN (via ${entry.via})`);
  const keys = (await get('passkeys')).passkeys;
  check(keys.length === 1 && keys[0].rp_id === 'localhost', 'passkey registered on the server for localhost');
  const stored = await page.evaluate(() => localStorage.getItem('sportage.passkey'));
  check(stored === keys[0]?.id, 'this browser remembers its credential id');
  const { credentials } = await cdp.send('WebAuthn.getCredentials', { authenticatorId });
  check(credentials.length === 1, 'the virtual authenticator holds one passkey');
  await shot('2-unlocked-with-pin');

  // 2. Lock (never asks), then unlock with the fingerprint: no PIN sheet.
  await clearPill();
  await page.click('#act-lock');
  await waitDone();
  check((await lastEntry()).action === 'lock', 'lock needs no verification');
  await clearPill();
  await page.click('#act-unlock');
  await page.waitForTimeout(400);   // the challenge arrives while the confirm sheet is open
  await page.click('#confirm-ok');
  await waitDone();
  check(!(await page.locator('#pin-sheet[open]').count()), 'fingerprint unlock shows no PIN sheet');
  entry = await lastEntry();
  check(entry.status === 'success' && String(entry.via).startsWith('passkey:'), `unlock with the passkey (via ${entry.via})`);
  const after = (await get('passkeys')).passkeys[0];
  check(!!after.last_used_at, 'server recorded the passkey use');
  await shot('3-unlocked-with-fingerprint');

  // 3. The fingerprint check fails (not verified): falls back to the PIN sheet.
  await clearPill();
  await page.click('#act-lock');
  await waitDone();
  await clearPill();
  await cdp.send('WebAuthn.setUserVerified', { authenticatorId, isUserVerified: false });
  await page.click('#act-unlock');
  await page.waitForTimeout(400);
  await page.click('#confirm-ok');
  await page.waitForSelector('#pin-sheet[open]', { timeout: 10000 });
  await page.waitForTimeout(300);
  const text = await page.locator('#pin-text').textContent();
  check(/fingerprint check/.test(text) || /PIN/.test(await page.locator('#pin-error').textContent()), `failed fingerprint offers the PIN (${text})`);
  check((await lastEntry()).action === 'lock', 'nothing unlocked without verification');
  await shot('4-fingerprint-failed-pin');
  await page.click('#pin-cancel');
  await cdp.send('WebAuthn.setUserVerified', { authenticatorId, isUserVerified: true });

  // 4. Replaying the last good assertion straight at the API is refused.
  const replay = await page.evaluate(async () => {
    const o = await (await fetch('api/unlock/challenge', { method: 'POST' })).json();
    const toBytes = (s) => Uint8Array.from(atob(s.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((s.length + 3) % 4)), (c) => c.charCodeAt(0));
    const b64 = (b) => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    const cred = await navigator.credentials.get({ publicKey: { challenge: toBytes(o.challenge), rpId: o.rpId, userVerification: 'required',
      allowCredentials: o.allowCredentials.map((c) => ({ type: 'public-key', id: toBytes(c.id) })) } });
    const a = { id: cred.id, rawId: b64(cred.rawId), type: cred.type, response: {
      clientDataJSON: b64(cred.response.clientDataJSON), authenticatorData: b64(cred.response.authenticatorData),
      signature: b64(cred.response.signature), userHandle: null } };
    const send = () => fetch('api/command', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'unlock', assertion: a }) });
    const first = await send();
    await new Promise((r) => setTimeout(r, 3500));
    const second = await send();
    return [first.status, second.status, (await second.json()).needs];
  });
  check(replay[0] === 200 && replay[1] === 403 && replay[2] === 'verification', `assertion replay refused (${replay})`);
  await page.waitForTimeout(800);

  // 5. The menu toggle turns it off: gone on the server and in this browser.
  await page.reload();
  await page.waitForSelector('#dash:not([hidden])');
  await page.waitForTimeout(400);
  await page.click('#menu-btn');
  await page.waitForTimeout(500);
  check((await page.locator('#menu-passkey-state').textContent()) === 'On', 'menu shows fingerprint on');
  await shot('5-menu-on');
  await page.click('#menu-passkey');
  await page.waitForTimeout(800);
  check((await get('passkeys')).passkeys.length === 0, 'turning off removes the passkey on the server');
  check(!(await page.evaluate(() => localStorage.getItem('sportage.passkey'))), 'turning off forgets it in the browser');
  await page.click('#menu-btn');
  await page.waitForTimeout(500);
  check((await page.locator('#menu-passkey-state').textContent()) === 'Off', 'menu shows fingerprint off');
  await shot('6-menu-off');
  await page.click('#menu-sheet button[value="cancel"]');

  // 6. Registration needs the PIN: a wrong PIN enrols nothing.
  await page.click('#menu-btn');
  await page.click('#menu-passkey');
  await page.waitForSelector('#pin-sheet[open]');
  await page.waitForTimeout(500);
  await page.fill('#pin-input', '9999');
  await page.click('#pin-ok');
  await page.waitForSelector('#pin-error:not([hidden])', { timeout: 10000 });
  check((await get('passkeys')).passkeys.length === 0, 'a wrong PIN enrols no passkey');
  await shot('7-setup-wrong-pin');
  await page.fill('#pin-input', '1234');
  await page.click('#pin-ok');
  await page.waitForFunction(() => localStorage.getItem('sportage.passkey'), null, { timeout: 10000 });
  check((await get('passkeys')).passkeys.length === 1, 'the right PIN turns it on again');
  await ctx.close();
} catch (err) {
  problems.push(`crashed: ${err.stack || err}`);
} finally {
  await browser?.close();
  server.kill();
  await new Promise((r) => setTimeout(r, 500));
  await rm(data, { recursive: true, force: true }).catch(() => {});
}

if (problems.length) { console.log('\nPROBLEMS:\n' + problems.join('\n')); process.exitCode = 1; } else console.log('\nall good');
