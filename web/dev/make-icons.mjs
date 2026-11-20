// Renders the PNG icons from the master SVGs: node web/dev/make-icons.mjs
import { readFile, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { launch } from './pw.mjs';

const ICONS = join(resolve(dirname(fileURLToPath(import.meta.url)), '..'), 'icons');
const jobs = [
  ['icon.svg', 'icon-192.png', 192, true],
  ['icon.svg', 'icon-512.png', 512, true],
  ['icon-maskable.svg', 'icon-maskable-512.png', 512, false],
  ['icon-maskable.svg', 'apple-touch-icon.png', 180, false],
];

const browser = await launch();
for (const [src, out, size, transparent] of jobs) {
  const svg = await readFile(join(ICONS, src), 'utf8');
  const page = await browser.newPage({ viewport: { width: size, height: size }, deviceScaleFactor: 1 });
  const data = `data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}`;
  await page.setContent(`<html><body style="margin:0;background:transparent"><img src="${data}" width="${size}" height="${size}"></body></html>`);
  await page.waitForFunction(() => document.images[0].complete);
  await writeFile(join(ICONS, out), await page.screenshot({ omitBackground: transparent }));
  await page.close();
  console.log(out);
}
await browser.close();
