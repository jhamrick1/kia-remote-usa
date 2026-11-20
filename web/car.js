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
let waiting = false; // expecting the car's picture (it was there last time): no drawing meanwhile
let lastV = null;
let turner = null;   // the 360 view, once its frames are loaded

// What this phone saw last time: {spin: frames} or {photo: true}. Lets a fresh start (after the
// phone dropped the app from memory) go straight to the real car instead of flashing the drawing.
const LOOK_KEY = 'sportage.carlook';
const WAIT_MS = 6000;       // give up waiting and show the drawing
function readLook() {
  try { return JSON.parse(localStorage.getItem(LOOK_KEY) || 'null'); } catch { return null; }
}
function saveLook(look) {
  try {
    if (look) localStorage.setItem(LOOK_KEY, JSON.stringify(look)); else localStorage.removeItem(LOOK_KEY);
  } catch { /* private mode */ }
}

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
    const look = readLook();
    waiting = !!(look?.spin || look?.photo);
    if (waiting) setTimeout(() => { if (waiting) { waiting = false; renderPhoto(lastV); } }, WAIT_MS);
    if (look?.spin >= 8) {
      // Straight to the 360 view, frames from the browser's cache; the single photo only if that fails.
      stage.classList.add('has-spin');
      loadSpin(img, stage, look.spin).then((t) => {
        if (t) { turner = t; photoReady = true; waiting = false; renderPhoto(lastV); return; }
        stage.classList.remove('has-spin');
        saveLook(null);
        loadPhoto(img, stage);
      });
    } else {
      loadPhoto(img, stage);
    }
  }
  lastV = v;
  // With the 360 view the car turns to what's open; without it the drawing shows anything open.
  const view = turner ? viewFor(v) : null;
  const show = photoReady && (turner ? view !== null : !attention(v));
  if (show && turner) turner.face(view);
  wrap.hidden = !show;
  // While waiting, the stage keeps the picture's shape and stays empty (no drawing to flash).
  stage.classList.toggle('is-photo', show || (waiting && !photoReady));
}

function loadPhoto(img, stage) {
  img.addEventListener('load', () => {
    photoReady = true;
    waiting = false;
    if (!readLook()?.spin) saveLook({ photo: true });
    renderPhoto(lastV);
    loadSpin(img, stage).then((t) => {
      turner = t;
      if (t) { saveLook({ spin: t.count }); renderPhoto(lastV); }
    });
  }, { once: true });
  img.addEventListener('error', () => {
    photoReady = false;
    waiting = false;
    saveLook(null);
    renderPhoto(lastV);
  }, { once: true });
  img.src = 'api/car-photo';
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
