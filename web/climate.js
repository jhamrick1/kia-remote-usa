// Climate: preset cards and the custom sheet (temperature, duration, extras, seats, save).
import { t } from './strings.js';
import { $, el, icon, vibrate, onPress } from './dom.js';
import { tempShown } from './format.js';
import { isF, fromC, fDial } from './units.js';
import * as api from './api.js';

// The car's dial, in the dashboard's units: 0.5-°C steps (state.dial, 17-27 in Canada) or whole °F
// for the USA region (state.region.dial, 62-82). Past it the car falls back to its default; LO / HI
// sent remotely start the engine with climate off (tried 2026-11-17 in Canada), so they aren't offered.
let DIAL = dialSteps(17, 27);

function dialSteps(min, max, step = 0.5) {
  return Array.from({ length: Math.round((max - min) / step) + 1 }, (_, i) => min + i * step);
}

/** Take the region's dial from /api/state: the °C one, or the region's own dial in °F. */
export function setDial(dial) {
  const d = isF() ? fDial() : dial;
  const min = Number(d?.min); const max = Number(d?.max);
  if (Number.isFinite(min) && Number.isFinite(max) && max > min && (min !== DIAL[0] || max !== DIAL[DIAL.length - 1])) {
    DIAL = dialSteps(min, max, isF() ? 1 : 0.5);
  }
}
const SEATS = ['front_left', 'front_right']; // the Sportage has no rear seat heat (the Kia app shows front only)
const LEVELS = [5, 4, 3, 0, 6, 7, 8]; // cool high .. off .. heat high, left to right
const LAST_KEY = 'sportage.climate.last';

const isCool = (lvl) => lvl >= 3 && lvl <= 5;
const isHeat = (lvl) => lvl >= 6 && lvl <= 8;
const levelDigit = (lvl) => (isCool(lvl) ? lvl - 2 : isHeat(lvl) ? lvl - 5 : 0);

/** The temperature a set of options carries, in the dashboard's unit: set_temp_f when it has one,
 * else set_temp_c converted (in a °F region) or as-is. */
function tempIn(o) {
  if (o?.set_temp_f != null) return o.set_temp_f;
  const c = o?.set_temp_c;
  return isF() && typeof c === 'number' && Number.isFinite(c) ? fromC(c) : c;
}

/** The temperature option this build sends: set_temp_f (whole °F) in a °F region, set_temp_c
 * otherwise -- and never both (the service refuses that). */
function tempOption(value, base = {}) {
  const o = { ...base };
  if (isF()) { o.set_temp_f = onDial(value); delete o.set_temp_c; }
  else { o.set_temp_c = onDial(value); delete o.set_temp_f; }
  return o;
}

/** The options the big climate button sends: the last custom run, or the dial's default for 10 min. */
export function lastOptions() {
  let saved = null;
  try {
    const raw = JSON.parse(localStorage.getItem(LAST_KEY) || 'null');
    if (raw && typeof raw === 'object') saved = raw;
  } catch { /* private mode */ }
  if (saved) return tempOption(tempIn(saved), saved);
  return tempOption(isF() ? fromC(21) : 21, { duration_min: 10 });
}

/** "22 °C, 10 min" (or "72 °F, 10 min") for a preset or the big button. */
export function describe(o) {
  return t('preset.meta', { temp: tempShown(tempIn(o)), min: o?.duration_min ?? 10 });
}

/** Warm or cool feel of a set of options (drives card colour). */
export function feel(o) {
  const anyCool = SEATS.some((s) => isCool(o?.[`${s}_seat`]));
  const temp = tempIn(o);
  if (anyCool || temp === 'LO' || (typeof temp === 'number' && temp < (isF() ? 66 : 19))) return 'cool';
  return 'heat';
}

function presetIcon(p) {
  const s = `${p.icon || ''} ${p.name || ''}`.toLowerCase();
  if (/snow|winter|frost|cold|ice/.test(s)) return 'snow';
  if (/sun|summer|cool|ac\b/.test(s)) return 'sun';
  if (/bolt|quick|fast|flash/.test(s)) return 'bolt';
  if (/fire|flame|heat|warm/.test(s)) return 'flame';
  return feel(p.options) === 'cool' ? 'sun' : 'flame';
}

/** Render the preset rail. Tap starts it, press and hold edits it. onStart(options), onCustom(), onEdit(preset) */
export function renderPresets(container, presets, { onStart, onCustom, onEdit }) {
  const cards = [];
  for (const p of presets || []) {
    if (!p || typeof p !== 'object') continue;
    const name = typeof p.name === 'string' && p.name.trim() ? p.name : t('climate.custom');
    const btn = el('button', { class: `preset preset-${feel(p.options)}`, type: 'button', attrs: { 'aria-label': t('climate.preset.start', { name }) } },
      el('span', { class: 'preset-ico' }, icon(presetIcon(p))),
      el('span', {}, el('span', { class: 'preset-name', text: name }), el('span', { class: 'preset-meta fig', text: describe(p.options) })));
    btn.title = t('climate.edit.hint');
    onPress(btn, { tap: () => onStart(p.options || {}), long: onEdit ? () => onEdit(p) : undefined });
    cards.push(btn);
  }
  const custom = el('button', { class: 'preset preset-custom', type: 'button' },
    el('span', { class: 'preset-ico' }, icon('plus')),
    el('span', {}, el('span', { class: 'preset-name', text: t('climate.custom') }), el('span', { class: 'preset-meta', text: t('climate.custom.sub') })));
  custom.addEventListener('click', onCustom);
  cards.push(custom);
  container.replaceChildren(...cards);
}

// ---------- the custom sheet ----------

let draft = null;
let editing = null;      // the preset being edited, or null for a custom run
let deleteArmed = false;
let selectedSeat = 'front_left';
let handlers = { onStart: () => {}, onSaved: () => {} };
let savedTimer = null;
let built = false;

function seatKey(seat) { return `${seat}_seat`; }

function fromOptions(o) {
  return {
    temp: onDial(tempIn(o) ?? (isF() ? fromC(21) : 21)),
    duration_min: Math.min(10, Math.max(1, Math.round(o?.duration_min ?? 10))),
    defrost: o?.defrost === true,
    // Kia's heating code: 1 both, 2 rear window + mirrors, 3 steering wheel
    wheel: o?.heating === 1 || o?.heating === 3 || o?.heating === true,
    rear: o?.heating === 1 || o?.heating === 2 || o?.heating === true,
    seats: Object.fromEntries(SEATS.map((s) => [s, LEVELS.includes(o?.[seatKey(s)]) ? o[seatKey(s)] : 0])),
  };
}

function toOptions(d) {
  const heating = d.wheel && d.rear ? 1 : d.rear ? 2 : d.wheel ? 3 : 0;
  const o = tempOption(d.temp, { duration_min: d.duration_min, defrost: d.defrost, heating });
  for (const s of SEATS) o[seatKey(s)] = d.seats[s];
  return o;
}

/** The nearest dial position. */
function onDial(v) {
  const lo = DIAL[0]; const hi = DIAL[DIAL.length - 1];
  if (v === 'HI') return hi;
  if (v === 'LO') return lo;
  if (typeof v !== 'number' || Number.isNaN(v)) return Math.min(hi, Math.max(lo, isF() ? fromC(21) : 21));
  return Math.min(hi, Math.max(lo, isF() ? Math.round(v) : Math.round(v * 2) / 2));
}

function stepDial(v, by) {
  const i = DIAL.indexOf(onDial(v));
  return DIAL[Math.min(DIAL.length - 1, Math.max(0, i + by))];
}

function build() {
  if (built) return;
  built = true;

  // seat diagram: front row on top, driver on the left (left-hand drive)
  const seatsBox = $('cs-seats');
  for (const s of SEATS) {
    const b = el('button', { class: 'seat', type: 'button', attrs: { 'data-seat': s } },
      icon('seat'),
      el('span', { class: 'seat-name', text: t(`seat.${s}`) }),
      el('span', { class: 'seat-level' }));
    b.addEventListener('click', () => { selectedSeat = s; vibrate(8); paint(); });
    seatsBox.append(b);
  }

  // level row: cool 3 2 1, off, heat 1 2 3
  const levels = $('cs-levels');
  for (const lvl of LEVELS) {
    const cls = isCool(lvl) ? 'lvl lvl-cool' : isHeat(lvl) ? 'lvl lvl-heat' : 'lvl';
    const b = el('button', { class: cls, type: 'button', attrs: { role: 'radio', 'data-level': String(lvl), 'aria-label': t(`seat.level.${lvl}`) } });
    if (lvl === 0) b.textContent = t('seat.level.0');
    else b.append(el('span', { class: 'fig', text: String(levelDigit(lvl)) }));
    b.addEventListener('click', () => { draft.seats[selectedSeat] = lvl; vibrate(8); paint(); });
    levels.append(b);
  }
  levels.setAttribute('aria-label', t('climate.seats'));
  levels.after(el('div', { class: 'lvl-legend', attrs: { 'aria-hidden': 'true' } },
    el('span', { class: 'lvl-legend-cool', text: t('seat.scale.cool') }),
    el('span', { class: 'lvl-legend-heat', text: t('seat.scale.heat') })));

  const step = (id, fn) => onPress($(id), { tap: () => { fn(); vibrate(6); paint(); } });
  step('cs-temp-down', () => { draft.temp = stepDial(draft.temp, -1); });
  step('cs-temp-up', () => { draft.temp = stepDial(draft.temp, 1); });
  step('cs-dur-down', () => { draft.duration_min = Math.max(1, draft.duration_min - 1); });
  step('cs-dur-up', () => { draft.duration_min = Math.min(10, draft.duration_min + 1); });
  $('cs-defrost').addEventListener('change', (e) => { draft.defrost = e.target.checked; vibrate(6); });
  $('cs-wheel').addEventListener('change', (e) => { draft.wheel = e.target.checked; vibrate(6); });
  $('cs-rear').addEventListener('change', (e) => { draft.rear = e.target.checked; vibrate(6); });

  $('cs-start').addEventListener('click', () => {
    if (editing) { savePreset(); return; }
    const options = toOptions(draft);
    try { localStorage.setItem(LAST_KEY, JSON.stringify(options)); } catch { /* private mode */ }
    $('climate-sheet').close();
    handlers.onStart(options);
  });

  $('cs-save').addEventListener('click', () => {
    if (editing) { deletePreset(); return; }
    const row = $('cs-save-row');
    row.hidden = !row.hidden;
    if (!row.hidden) $('cs-save-name').focus();
  });
  $('cs-save-confirm').addEventListener('click', savePreset);
  $('cs-save-name').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); savePreset(); } });
}

async function savePreset() {
  const input = $('cs-save-name');
  const name = input.value.trim();
  if (!name) { input.focus(); return; }
  const btn = editing ? $('cs-start') : $('cs-save-confirm');
  btn.disabled = true;
  try {
    const current = await api.getPresets();
    const list = Array.isArray(current?.presets) ? current.presets : [];
    const options = toOptions(draft);
    const item = { id: editing?.id || `p_${Date.now().toString(36)}`, name, icon: feel(options) === 'cool' ? 'sun' : 'flame', options };
    const next = editing && list.some((p) => p.id === editing.id)
      ? list.map((p) => (p.id === editing.id ? item : p))
      : [...list, item];
    const res = await api.putPresets(next);
    if (editing) {
      handlers.onSaved(res?.presets || null);
      $('climate-sheet').close();
      return;
    }
    input.value = '';
    $('cs-save-row').hidden = true;
    $('cs-save-error').hidden = true;
    const save = $('cs-save');
    save.textContent = t('climate.saved');
    save.classList.add('is-done');
    clearTimeout(savedTimer);
    savedTimer = setTimeout(() => { save.textContent = t('climate.save'); save.classList.remove('is-done'); }, 2500);
    handlers.onSaved(res?.presets || null);
  } catch (err) {
    const msg = $('cs-save-error');
    msg.textContent = err.plain ? `${t('climate.save.failed')}. ${err.plain}` : t('climate.save.failed');
    msg.hidden = false;
  } finally {
    btn.disabled = false;
  }
}

async function deletePreset() {
  const btn = $('cs-save');
  if (!deleteArmed) {
    deleteArmed = true;
    btn.textContent = t('climate.edit.sure');
    vibrate(12);
    return;
  }
  btn.disabled = true;
  try {
    const current = await api.getPresets();
    const list = Array.isArray(current?.presets) ? current.presets : [];
    const res = await api.putPresets(list.filter((p) => p.id !== editing.id));
    handlers.onSaved(res?.presets || null);
    $('climate-sheet').close();
  } catch (err) {
    const msg = $('cs-save-error');
    msg.textContent = err.plain ? `${t('climate.delete.failed')}. ${err.plain}` : t('climate.delete.failed');
    msg.hidden = false;
    deleteArmed = false;
    btn.textContent = t('climate.edit.delete');
  } finally {
    btn.disabled = false;
  }
}

function paint() {
  $('cs-temp').textContent = tempShown(draft.temp);
  const at = DIAL.indexOf(draft.temp);
  $('cs-temp-mark').style.setProperty('--t', String(at / (DIAL.length - 1)));
  $('cs-temp-down').disabled = at <= 0;
  $('cs-temp-up').disabled = at >= DIAL.length - 1;
  $('cs-dur').textContent = t('climate.duration.value', { n: draft.duration_min });
  $('cs-dur-down').disabled = draft.duration_min <= 1;
  $('cs-dur-up').disabled = draft.duration_min >= 10;
  $('cs-defrost').checked = draft.defrost;
  $('cs-wheel').checked = draft.wheel;
  $('cs-rear').checked = draft.rear;

  for (const b of $('cs-seats').children) {
    const s = b.dataset.seat;
    const lvl = draft.seats[s];
    b.classList.toggle('is-selected', s === selectedSeat);
    b.classList.toggle('is-heat', isHeat(lvl));
    b.classList.toggle('is-cool', isCool(lvl));
    b.setAttribute('aria-pressed', String(s === selectedSeat));
    b.setAttribute('aria-label', `${t(`seat.${s}`)}, ${t(`seat.level.${lvl}`)}`);
    b.querySelector('.seat-level').textContent = t(`seat.short.${lvl}`);
  }
  const current = draft.seats[selectedSeat];
  for (const b of $('cs-levels').children) b.setAttribute('aria-checked', String(Number(b.dataset.level) === current));
}

/**
 * Open the custom sheet, prefilled from `options` (or the last custom run).
 * With `preset`, it edits that preset: name and Save changes up top, Delete instead of Save as preset.
 */
export function openClimateSheet(options, h, preset = null) {
  handlers = { ...handlers, ...h };
  build();
  editing = preset;
  deleteArmed = false;
  draft = fromOptions(preset ? preset.options : (options || lastOptions()));
  selectedSeat = 'front_left';
  $('cs-title').textContent = preset ? t('climate.edit.title', { name: preset.name }) : t('climate.sheet.title');
  $('cs-save-name').value = preset ? preset.name : '';
  $('cs-save-confirm').hidden = !!preset;   // editing: the big button saves
  $('cs-start').textContent = t(preset ? 'climate.edit.save' : 'climate.start');
  const save = $('cs-save');
  save.textContent = t(preset ? 'climate.edit.delete' : 'climate.save');
  save.classList.toggle('btn-danger', !!preset);
  save.classList.remove('is-done');
  $('cs-save-row').hidden = !preset;
  $('cs-save-error').hidden = true;
  paint();
  $('climate-sheet').showModal();
}
