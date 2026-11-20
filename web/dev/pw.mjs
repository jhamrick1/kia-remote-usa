// Finds Playwright for the dev scripts: `playwright`, else `playwright-core` (from PW_DIR if set)
// with the installed Chrome. Nothing here ships.
import { createRequire } from 'node:module';
import { existsSync } from 'node:fs';
import { join } from 'node:path';

export function chromium() {
  const bases = [process.env.PW_DIR, process.cwd(), import.meta.url].filter(Boolean);
  for (const base of bases) {
    const req = createRequire(base.startsWith('file:') ? base : join(base, 'x.js'));
    for (const name of ['playwright', 'playwright-core']) {
      try { return { chromium: req(name).chromium, core: name === 'playwright-core' }; } catch { /* next */ }
    }
  }
  throw new Error('Playwright not found: npm i --no-save playwright-core in a temp dir and set PW_DIR to it');
}

export async function launch() {
  const { chromium: c, core } = chromium();
  if (!core) return c.launch();
  const candidates = [process.env.CHROME,
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'].filter(Boolean);
  const executablePath = candidates.find((p) => existsSync(p));
  return c.launch({ executablePath });
}
