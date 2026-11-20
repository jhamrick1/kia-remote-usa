// Small DOM helpers: build elements without innerHTML, draw icons with createElementNS.

const SVG_NS = 'http://www.w3.org/2000/svg';

export const $ = (id) => document.getElementById(id);

/** el('div', {class: 'x', text: 'hi', hidden: true, attrs: {...}}, ...children) */
export function el(tag, opts = {}, ...children) {
  const node = document.createElement(tag);
  if (opts.class) node.className = opts.class;
  if (opts.text !== undefined) node.textContent = opts.text;
  if (opts.hidden) node.hidden = true;
  if (opts.type) node.type = opts.type;
  if (opts.attrs) for (const [k, v] of Object.entries(opts.attrs)) node.setAttribute(k, v);
  for (const c of children) if (c) node.append(c);
  return node;
}

// Icon paths on a 24 x 24 grid, stroked.
const ICONS = {
  snow: ['M12 3v18M4.2 7.5l15.6 9M4.2 16.5l15.6-9', 'M9.5 4.5 12 6l2.5-1.5M9.5 19.5 12 18l2.5 1.5'],
  sun: ['M12 7.5a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9Z', 'M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4'],
  bolt: ['M13 3 5 14h6l-1 7 8-11h-6l1-7Z'],
  flame: ['M12 21c-3.6 0-6-2.4-6-5.6 0-3.4 3-5.4 3.6-9.4 2.6 1.4 4 3.6 4 5.6 1-.6 1.6-1.8 1.8-3 1.6 1.6 2.6 3.8 2.6 6.4 0 3.4-2.4 6-6 6Z'],
  fan: ['M12 12c0-4 1-8 4-8s3 4-4 8Zm0 0c4 0 8 1 8 4s-4 3-8-4Zm0 0c0 4-1 8-4 8s-3-4 4-8Zm0 0c-4 0-8-1-8-4s4-3 8 4Z'],
  plus: ['M12 5v14M5 12h14'],
  seat: ['M8 4.5h5a2 2 0 0 1 2 2V13H8a1.5 1.5 0 0 1-1.5-1.5v-5A2 2 0 0 1 8 4.5Z', 'M6.5 13H17a1.5 1.5 0 0 1 1.5 1.5V17a1.5 1.5 0 0 1-1.5 1.5H8A1.5 1.5 0 0 1 6.5 17v-4Z', 'M9 18.5v2M16 18.5v2'],
  warn: ['M12 4 21 19H3L12 4Z', 'M12 10v4M12 16.5v.5'],
  drop: ['M12 3.5s-6 6.5-6 10.5a6 6 0 0 0 12 0c0-4-6-10.5-6-10.5Z'],
  key: ['M8 15a4 4 0 1 1 2.8-6.8L20 8v3h-2v2h-2v-2h-5.2A4 4 0 0 1 8 15Z'],
  battery: ['M4 8h14a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1Z', 'M21 10.5v3M7 12h4M9 10v4'],
  tire: ['M12 4a8 8 0 1 1 0 16 8 8 0 0 1 0-16Z', 'M12 8.5v4M12 15v.5'],
  oil: ['M3 10h5l2-2h4l6 3-3 5H6a3 3 0 0 1-3-3v-3Z', 'M20 15.5s1.5 1.6 1.5 2.6a1.5 1.5 0 0 1-3 0c0-1 1.5-2.6 1.5-2.6Z'],
};

export function icon(name, cls) {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  if (cls) svg.setAttribute('class', cls);
  for (const d of ICONS[name] || ICONS.fan) {
    const p = document.createElementNS(SVG_NS, 'path');
    p.setAttribute('d', d);
    p.setAttribute('class', 'ico');
    svg.append(p);
  }
  return svg;
}

export function vibrate(pattern) {
  try { if (navigator.vibrate) navigator.vibrate(pattern); } catch { /* not supported */ }
}

/**
 * Tap and long-press on one control. Long-press fires after `ms` and swallows the click.
 * Also opens on right-click (desktop) and on the context-menu gesture.
 */
export function onPress(node, { tap, long, ms = 520 }) {
  let timer = null;
  let fired = false;
  const clear = () => { clearTimeout(timer); timer = null; node.classList.remove('is-pressed'); };
  node.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    fired = false;
    if (!long) return;
    timer = setTimeout(() => { fired = true; clear(); vibrate(12); long(); }, ms);
  });
  for (const ev of ['pointerup', 'pointerleave', 'pointercancel']) node.addEventListener(ev, clear);
  node.addEventListener('click', (e) => {
    if (fired) { e.preventDefault(); fired = false; return; }
    tap(e);
  });
  if (long) {
    node.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      if (fired) return;
      clear();
      fired = true;
      long();
    });
  }
}
