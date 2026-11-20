// Climate schedules: the panel list (with on/off switches) and the add/edit sheet.
import { t } from './strings.js';
import { $, el, vibrate } from './dom.js';
import * as api from './api.js';
import { describe } from './climate.js';

const DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
const WEEKDAYS = DAYS.slice(0, 5);

let list = [];           // schedules as the service has them
let presets = [];
let editing = null;      // the schedule being edited (a copy), or null when adding
let onChange = () => {};
let built = false;

/** "Weekdays", "Every day", "Weekends", or "Mon, Wed, Fri". */
export function daysText(days) {
  const set = new Set(days);
  if (set.size === 7) return t('sched.everyday');
  if (set.size === 5 && WEEKDAYS.every((d) => set.has(d))) return t('sched.weekdays');
  if (set.size === 2 && set.has('sat') && set.has('sun')) return t('sched.weekends');
  return DAYS.filter((d) => set.has(d)).map((d) => t(`day.${d}`)).join(', ');
}

/** "7:45" (or "7:45 AM", following the phone's locale). */
function timeText(hhmm) {
  const [h, m] = hhmm.split(':').map(Number);
  return new Date(2000, 0, 1, h, m).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

/** "Tomorrow 7:45", "Today 7:45", "Mon 7:45". */
function nextText(iso) {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return '';
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const days = Math.round((new Date(at).setHours(0, 0, 0, 0) - today) / 86400000);
  const time = at.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  const day = days === 0 ? t('sched.today') : days === 1 ? t('sched.tomorrow')
    : at.toLocaleDateString([], { weekday: 'short' });
  return t('sched.next', { when: `${day} ${time}` });
}

const presetName = (id) => presets.find((p) => p.id === id)?.name;

function render(next) {
  const rows = list.map((s) => {
    const name = presetName(s.preset_id) || t('sched.preset.gone');
    const sw = el('input', { class: 'switch', type: 'checkbox', attrs: { 'aria-label': t('sched.toggle', { time: timeText(s.time) }) } });
    sw.checked = s.enabled;
    sw.addEventListener('click', (e) => e.stopPropagation());
    sw.addEventListener('change', () => { vibrate(6); save(list.map((x) => (x.id === s.id ? { ...x, enabled: sw.checked } : x)), sw); });
    const body = el('button', { class: 'sched-body', type: 'button', attrs: { 'aria-label': t('sched.edit', { time: timeText(s.time) }) } },
      el('span', { class: 'sched-time fig', text: timeText(s.time) }),
      el('span', { class: 'sched-meta', text: `${daysText(s.days)} · ${name}` }));
    body.addEventListener('click', () => openSheet(s));
    return el('li', { class: `sched-item${s.enabled ? '' : ' is-off'}` }, body, sw);
  });
  $('sched-list').replaceChildren(...rows);
  $('sched-list').hidden = rows.length === 0;
  $('sched-next').textContent = next?.at ? nextText(next.at) : (list.length ? t('sched.none.on') : t('sched.none'));
  $('sched-add').disabled = presets.length === 0;
}

async function save(next, control = null) {
  if (control) control.disabled = true;
  try {
    const res = await api.putSchedules(next);
    list = res.schedules || [];
    render(res.next);
    onChange();
    return null;
  } catch (err) {
    render(null);          // put the switches back the way the service has them
    load();
    return err.plain || t('app.unreachable');
  } finally {
    if (control) control.disabled = false;
  }
}

/** Fetch and draw. Call again whenever presets change. */
export async function load(currentPresets) {
  if (currentPresets) presets = currentPresets;
  try {
    const res = await api.getSchedules();
    list = res.schedules || [];
    render(res.next);
  } catch { render(null); }
}

// ---------- sheet ----------

function build() {
  if (built) return;
  built = true;
  const days = $('ss-days');
  for (const d of DAYS) {
    const b = el('button', { class: 'day', type: 'button', text: t(`day.${d}.short`), attrs: { 'data-day': d, 'aria-label': t(`day.${d}.long`), 'aria-pressed': 'false' } });
    b.addEventListener('click', () => { vibrate(6); b.setAttribute('aria-pressed', String(b.getAttribute('aria-pressed') !== 'true')); });
    days.append(b);
  }
  const setDays = (want) => { for (const b of days.children) b.setAttribute('aria-pressed', String(want.includes(b.dataset.day))); vibrate(6); };
  $('ss-weekdays').addEventListener('click', () => setDays(WEEKDAYS));
  $('ss-everyday').addEventListener('click', () => setDays(DAYS));

  $('ss-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const time = $('ss-time').value;
    const chosen = [...days.children].filter((b) => b.getAttribute('aria-pressed') === 'true').map((b) => b.dataset.day);
    const preset = $('ss-presets').querySelector('input:checked')?.value;
    if (!/^\d\d:\d\d$/.test(time)) return fail(t('sched.err.time'));
    if (!chosen.length) return fail(t('sched.err.days'));
    if (!preset) return fail(t('sched.err.preset'));
    const item = { id: editing?.id || `s_${Date.now().toString(36)}`, enabled: editing ? editing.enabled : true, time, days: chosen, preset_id: preset };
    const next = editing ? list.map((x) => (x.id === item.id ? item : x)) : [...list, item];
    const err = await save(next, $('ss-save'));
    if (err) fail(err); else $('sched-sheet').close();
  });
  $('ss-delete').addEventListener('click', async () => {
    if (!editing) { $('sched-sheet').close(); return; }
    const err = await save(list.filter((x) => x.id !== editing.id), $('ss-delete'));
    if (err) fail(err); else $('sched-sheet').close();
  });
}

function fail(text) {
  const e = $('ss-error');
  e.textContent = text;
  e.hidden = false;
}

function openSheet(s) {
  build();
  editing = s ? { ...s } : null;
  $('ss-title').textContent = t(s ? 'sched.sheet.edit' : 'sched.sheet.add');
  $('ss-time').value = s?.time || '07:45';
  const want = s?.days || WEEKDAYS;
  for (const b of $('ss-days').children) b.setAttribute('aria-pressed', String(want.includes(b.dataset.day)));
  const pick = s?.preset_id || presets[0]?.id;
  $('ss-presets').replaceChildren(...presets.map((p) => {
    const input = el('input', { type: 'radio', attrs: { name: 'ss-preset', value: p.id } });
    input.checked = p.id === pick;
    return el('label', { class: 'ss-preset' }, input,
      el('span', { class: 'ss-preset-name', text: p.name }),
      el('span', { class: 'ss-preset-meta fig', text: describe(p.options) }));
  }));
  $('ss-delete').textContent = t(s ? 'sched.delete' : 'confirm.cancel');
  $('ss-delete').classList.toggle('btn-danger', !!s);
  $('ss-error').hidden = true;
  $('sched-sheet').showModal();
}

export function wire({ changed } = {}) {
  if (changed) onChange = changed;
  $('sched-add').addEventListener('click', () => openSheet(null));
}
