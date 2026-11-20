// Screenshots of the dashboard against the mock: node web/dev/shoot.mjs [base url]
// Writes web/dev/shots/*.png (gitignored) and fails on console errors or CSP violations.
// localhost, not 127.0.0.1: a passkey needs a name in the address (Chrome's virtual authenticator
// stands in for the phone's fingerprint).
import { mkdir } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { launch } from './pw.mjs';

const BASE = process.argv[2] || 'http://localhost:8765/';
const OUT = join(dirname(fileURLToPath(import.meta.url)), 'shots');
await mkdir(OUT, { recursive: true });

const mock = (body) => fetch(new URL('__mock/set', BASE), { method: 'POST', body: JSON.stringify(body) }).then((r) => r.json());
const reset = () => mock({
  auth: 'ok', warnings: false, holdCommands: false, seedLog: true, resetUnlock: true,
  vehicle: { locked: true, climate: { on: false }, engine_on: false, trunk_open: false, sunroof_open: false,
    doors: { front_left: { open: false }, rear_right: { open: false } }, windows: { front_right: false } },
});

const problems = [];
const browser = await launch();

const SIZES = {
  phone: { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true },
  desktop: { viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1 },
};

async function open(size, { authenticator = false } = {}) {
  const ctx = await browser.newContext({ ...SIZES[size], serviceWorkers: 'block' });
  const page = await ctx.newPage();
  if (authenticator) {
    const cdp = await ctx.newCDPSession(page);
    await cdp.send('WebAuthn.enable');
    await cdp.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', transport: 'internal',
      hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true } });
  }
  page.on('console', (m) => { if ((m.type() === 'error' || m.type() === 'warning') && !m.text().includes('blocked by Playwright') && !m.text().includes('status of 429') && !m.text().includes('status of 403')) problems.push(`${size} console ${m.type()}: ${m.text()}`); });
  page.on('pageerror', (e) => problems.push(`${size} page error: ${e.message}`));
  await page.addInitScript(() => {
    document.addEventListener('securitypolicyviolation', (e) => console.error(`CSP violation: ${e.violatedDirective} ${e.blockedURI}`));
  });
  await page.goto(BASE);
  await page.waitForSelector('#dash:not([hidden]), #auth:not([hidden])');
  await page.waitForTimeout(900);
  return { ctx, page };
}

async function shot(page, name, full = false) {
  await page.screenshot({ path: join(OUT, `${name}.png`), fullPage: full });
  console.log(name);
}

for (const size of ['phone', 'desktop']) {
  // 1. dashboard, locked
  await reset();
  let { ctx, page } = await open(size);
  await shot(page, `${size}-1-locked`);
  await shot(page, `${size}-1-locked-full`, true);

  // 2. a pending climate command
  await mock({ holdCommands: true });
  await page.click('#act-climate');
  await page.waitForTimeout(4200);
  await shot(page, `${size}-2-climate-pending`);
  await mock({ finishPending: true, holdCommands: false });
  await page.waitForTimeout(2600);
  await shot(page, `${size}-2b-climate-on`);
  await ctx.close();

  // 3. climate sheet open
  await reset();
  ({ ctx, page } = await open(size));
  await page.click('.preset-custom');
  await page.waitForTimeout(400);
  await page.click('#cs-temp-up');
  await page.waitForTimeout(300);
  await shot(page, `${size}-3-climate-sheet`);
  await page.click('[data-seat="front_left"]');
  await page.click('[data-level="7"]');
  await page.click('#cs-defrost', { force: true });
  await page.click('#cs-save');
  await page.fill('#cs-save-name', 'Hockey practice');
  await page.evaluate(() => { document.getElementById('climate-sheet').scrollTop = 9999; });
  await page.waitForTimeout(200);
  await shot(page, `${size}-3b-climate-sheet-bottom`);
  await ctx.close();

  // 4. warnings, unlocked, a door and a window open, a failed command
  await reset();
  await mock({ warnings: true, vehicle: { locked: false, doors: { front_left: { open: true } }, windows: { front_right: true }, battery_12v_percent: 41, warnings: { aux_battery: true } } });
  ({ ctx, page } = await open(size));
  await shot(page, `${size}-4-warnings`);
  await mock({ failNext: true });
  await page.click('#act-lock');
  await page.waitForTimeout(5200);
  await shot(page, `${size}-4b-failed`);
  await shot(page, `${size}-4-warnings-full`, true);
  await mock({ vehicle: { battery_12v_percent: 88, warnings: { aux_battery: false } } });
  await ctx.close();

  // 5. login screen
  await mock({ auth: 'needs_login' });
  ({ ctx, page } = await open(size));
  await shot(page, `${size}-5-login`, true);
  await ctx.close();

  // 6. one-time code + error
  await mock({ auth: 'needs_otp' });
  ({ ctx, page } = await open(size));
  await shot(page, `${size}-6-otp`);
  await ctx.close();
  await mock({ auth: 'error' });
  ({ ctx, page } = await open(size));
  await shot(page, `${size}-7-auth-error`);
  await ctx.close();

  // 8. nulls everywhere
  await mock({ auth: 'ok' });
  await mock({ vehicle: { locked: null, fuel_percent: null, range_km: null, odometer_km: null, battery_12v_percent: null,
    outside_temp_c: null, next_service_km: null, location: null, updated_at: null, doors: null, windows: null, tires: null, warnings: null, climate: null } });
  ({ ctx, page } = await open(size));
  await shot(page, `${size}-8-nulls`, true);
  const text = await page.evaluate(() => document.body.innerText);
  if (/null|undefined|NaN/.test(text)) problems.push(`${size}: page text shows null/undefined/NaN`);
  await ctx.close();
  // restore the car
  await mock({ vehicle: { locked: true, fuel_percent: 72, range_km: 540, odometer_km: 18342, battery_12v_percent: 88, outside_temp_c: -4,
    next_service_km: 6658, location: { lat: 45.42153, lon: -75.69719, updated_at: new Date(Date.now() - 840000).toISOString() },
    updated_at: new Date(Date.now() - 720000).toISOString(),
    doors: { front_left: { open: false, locked: true }, front_right: { open: false, locked: true }, rear_left: { open: false, locked: true }, rear_right: { open: false, locked: true } },
    windows: { front_left: false, front_right: false, rear_left: false, rear_right: false },
    tires: { warning: false, front_left: false, front_right: false, rear_left: false, rear_right: false },
    warnings: { washer_fluid: false, brake_fluid: false, oil: false, smart_key_battery: false, aux_battery: false },
    climate: { on: false, set_temp_c: 21, defrost: false } } });
}

// unlock confirm, the menu, waking the car twice (the second is refused with 429)
{
  await reset();
  const { ctx, page } = await open('phone');
  await page.click('#act-unlock');
  await page.waitForTimeout(400);
  await shot(page, 'phone-10-unlock-confirm');
  await page.click('#confirm-sheet button[value="cancel"]');
  const box = await page.locator('#refresh-btn').boundingBox();
  await page.mouse.move(box.x + 20, box.y + 20);
  await page.mouse.down();
  await page.waitForTimeout(700);
  await page.mouse.up();
  await page.waitForTimeout(400);
  if (!(await page.locator('#menu-sheet[open]').count())) problems.push('long-press on refresh did not open the menu');
  await shot(page, 'phone-11-menu');
  await page.click('#menu-wake');
  await page.waitForTimeout(800);
  await shot(page, 'phone-12-waking');
  await page.waitForTimeout(3000);
  await page.click('#menu-btn');
  await page.click('#menu-wake');
  await page.waitForTimeout(800);
  await shot(page, 'phone-13-wake-429');
  await ctx.close();
}

// unlock with the PIN sheet, a wrong PIN, fingerprint next time, then the menu toggle
{
  await reset();
  const { ctx, page } = await open('phone', { authenticator: true });
  const lastVia = async () => (await (await fetch(new URL('api/log', BASE))).json()).entries[0]?.via;
  await page.click('#act-unlock');
  await page.click('#confirm-ok');
  await page.waitForSelector('#pin-sheet[open]');
  await page.waitForTimeout(400);
  if (await page.locator('#pin-remember-row').isHidden()) problems.push('pin sheet: "Use fingerprint next time" is hidden');
  const attrs = await page.locator('#pin-input').evaluate((i) => [i.inputMode, i.autocomplete, i.type]);
  if (attrs.join() !== 'numeric,off,text') problems.push(`pin input attributes: ${attrs}`);
  await page.fill('#pin-input', '12');
  await shot(page, 'phone-14-pin-sheet');
  await page.fill('#pin-input', '0000');
  await page.click('#pin-ok');
  await page.waitForSelector('#pin-error:not([hidden])');
  await page.waitForTimeout(400);
  await shot(page, 'phone-15-pin-wrong');
  await page.fill('#pin-input', '1234');
  await page.click('#pin-remember-row');
  await page.waitForTimeout(300);
  await page.click('#pin-ok');
  await page.waitForSelector('.cmd-status.is-success', { timeout: 10000 });
  if (await lastVia() !== 'pin') problems.push('pin unlock: log entry not via pin');
  if (!(await page.evaluate(() => localStorage.getItem('sportage.passkey')))) problems.push('fingerprint next time: no passkey remembered');
  await shot(page, 'phone-16-unlocked-pin');
  // lock, then unlock again: the fingerprint, no PIN sheet
  await page.click('#act-lock');
  await page.waitForSelector('.cmd-status.is-pending');
  await page.waitForSelector('.cmd-status.is-success', { timeout: 10000 });
  await page.click('#act-unlock');
  await page.waitForTimeout(300);
  await page.click('#confirm-ok');
  await page.waitForSelector('.cmd-status.is-pending', { timeout: 5000 });
  if (await page.locator('#pin-sheet[open]').count()) problems.push('passkey unlock: the PIN sheet opened');
  await page.waitForSelector('.cmd-status.is-success', { timeout: 10000 });
  if (!String(await lastVia()).startsWith('passkey:')) problems.push('passkey unlock: log entry not via passkey');
  await page.click('#menu-btn');
  await page.waitForTimeout(500);
  await shot(page, 'phone-17-menu-fingerprint-on');
  await page.click('#menu-passkey');
  await page.waitForTimeout(600);
  await page.click('#menu-btn');
  await page.waitForTimeout(500);
  await shot(page, 'phone-18-menu-fingerprint-off');
  if ((await page.locator('#menu-passkey-state').textContent()) !== 'Off') problems.push('menu: fingerprint not off after the toggle');
  const left = (await (await fetch(new URL('api/passkeys', BASE))).json()).passkeys;
  if (left.length) problems.push('menu: passkey still on the service after turning off');
  // turn it on from the menu (PIN once)
  await page.click('#menu-passkey');
  await page.waitForSelector('#pin-sheet[open]');
  await page.waitForTimeout(500);
  await shot(page, 'phone-19-turn-on');
  await page.fill('#pin-input', '1234');
  await page.click('#pin-ok');
  try {
    await page.waitForFunction(() => localStorage.getItem('sportage.passkey'), null, { timeout: 5000 });
  } catch { problems.push('turn on: no passkey remembered'); }
  await page.waitForTimeout(300);
  await shot(page, 'phone-20-turned-on');
  await ctx.close();
}

// service worker: installs, then serves the shell offline (never /api/)
{
  await reset();
  const ctx = await browser.newContext(SIZES.phone);
  const page = await ctx.newPage();
  page.on('pageerror', (e) => problems.push(`sw page error: ${e.message}`));
  await page.goto(BASE);
  await page.evaluate(() => navigator.serviceWorker.ready);
  await page.reload();
  await page.waitForTimeout(800);
  await ctx.setOffline(true);
  await page.reload();
  await page.waitForTimeout(1200);
  await shot(page, 'phone-9-offline');
  const shell = await page.evaluate(() => !!document.getElementById('dash'));
  const cached = await page.evaluate(async () => (await (await caches.open((await caches.keys())[0])).keys()).map((r) => new URL(r.url).pathname));
  if (!shell) problems.push('offline: shell did not load from the cache');
  if (cached.some((u) => u.includes('/api/'))) problems.push('offline: an /api/ response was cached');
  console.log('sw cache:', cached.length, 'entries');
  await ctx.close();
}

await browser.close();
if (problems.length) { console.log('\nPROBLEMS:\n' + problems.join('\n')); process.exitCode = 1; } else console.log('\nno console errors, no CSP violations');
