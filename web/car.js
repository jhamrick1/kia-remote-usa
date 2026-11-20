// The hero car: maps vehicle state onto the static SVG in index.html (classes only).
import { t } from './strings.js';
import { $ } from './dom.js';
import { temp } from './format.js';
import { loadSpin, viewFor } from './spin.js';

const CORNERS = ['front_left', 'front_right', 'rear_left', 'rear_right'];

/** Every open thing on the car, in reading order, as string keys. */
export function openParts(v) {
  const open = [];
  for (const c of CORNERS) if (v?.doors?.[c]?.open === true) open.push(`part.door_${c}`);
  if (v?.trunk_open === true) open.push('part.trunk');
  if (v?.hood_open === true) open.push('part.hood');
  for (const c of CORNERS) if (v?.windows?.[c] === true) open.push(`part.window_${c}`);
  if (v?.sunroof_open === true) open.push('part.sunroof');
  return open;
}

/** 'heat' | 'cool' | null for a running climate. */
export function climateMode(v) {
  if (v?.climate?.on !== true) return null;
  const set = v.climate.set_temp_c;
  if (set === 'LO') return 'cool';
  if (typeof set !== 'number') return 'heat';
  const out = v.outside_temp_c;
  if (typeof out === 'number') return set >= out ? 'heat' : 'cool';
  return set >= 20 ? 'heat' : 'cool';
}

/** Sentence for the open summary under the car. */
export function openSummary(v) {
  const open = openParts(v);
  if (open.length === 0) {
    // only claim "all closed" when the car actually reported its doors
    const known = CORNERS.some((c) => typeof v?.doors?.[c]?.open === 'boolean');
    return { text: known ? t('status.allclosed') : '', alert: false };
  }
  if (open.length === 1) return { text: t('status.open.one', { what: t(open[0]) }), alert: true };
  if (open.length === 2) {
    const what = t('status.and', { a: t(open[0]), b: t(open[1]).toLowerCase() });
    return { text: t('status.open.one', { what }), alert: true };
  }
  return { text: t('status.open.many', { n: open.length }), alert: true };
}

let photoReady = false;
let photoAsked = false;
let lastV = null;
let turner = null;   // the 360 view, once its frames are loaded

function attention(v) {
  return openParts(v).length > 0 || CORNERS.some((c) => v?.tires?.[c] === true);
}

/** Swap the drawing for Kia's picture of the car once it loads; the drawing comes back while
 * something is open or a tire warns, which the picture can't show. */
function renderPhoto(v) {
  const wrap = $('car-photo');
  const img = $('car-photo-img');
  const stage = $('car-stage');
  if (!wrap || !img || !stage) return;
  if (!photoAsked && v) {
    photoAsked = true;
    img.addEventListener('load', () => {
      photoReady = true;
      renderPhoto(lastV);
      loadSpin(img, stage).then((t) => { turner = t; if (t) renderPhoto(lastV); });
    }, { once: true });
    img.addEventListener('error', () => { photoReady = false; }, { once: true });
    img.src = 'api/car-photo';
  }
  lastV = v;
  // With the 360 view the car turns to what's open; without it the drawing shows anything open.
  const view = turner ? viewFor(v) : null;
  const show = photoReady && (turner ? view !== null : !attention(v));
  if (show && turner) turner.face(view);
  wrap.hidden = !show;
  stage.classList.toggle('is-photo', show);
}

export function renderCar(app, v) {
  renderPhoto(v);
  const set = (id, on) => { const n = $(id); if (n) n.classList.toggle('is-open', on === true); };
  for (const c of CORNERS) {
    set(`car-door-${c}`, v?.doors?.[c]?.open);
    set(`car-window-${c}`, v?.windows?.[c]);
    const tire = $(`car-tire-${c}`);
    if (tire) tire.classList.toggle('is-warn', v?.tires?.[c] === true);
  }
  set('car-trunk', v?.trunk_open);
  set('car-hood', v?.hood_open);
  set('car-sunroof', v?.sunroof_open);

  app.classList.toggle('is-locked', v?.locked === true);
  app.classList.toggle('is-unlocked', v?.locked === false);
  const mode = climateMode(v);
  app.classList.toggle('is-climate-heat', mode === 'heat');
  app.classList.toggle('is-climate-cool', mode === 'cool');

  const lock = v?.locked === true ? t('status.locked') : v?.locked === false ? t('status.unlocked') : t('status.lock.unknown');
  $('lock-text').textContent = lock;
  const sum = openSummary(v);
  const extra = mode ? t('status.climate.on', { temp: temp(v.climate.set_temp_c) })
    : v?.engine_on === true ? t('status.engine') : null;
  const open = $('open-status');
  open.textContent = extra && sum.text ? `${sum.text}, ${extra.charAt(0).toLowerCase()}${extra.slice(1)}` : (extra || sum.text);
  open.classList.toggle('is-alert', sum.alert);
  $('car-summary').textContent = open.textContent ? `${lock}. ${open.textContent}.` : `${lock}.`;
}
