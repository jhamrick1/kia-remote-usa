// Vehicle health: Kia's monthly report (a check of each system, and the month's driving), with
// the warning lights the car reports right now on top.
import { t } from './strings.js';
import { $, el, icon } from './dom.js';
import * as api from './api.js';
import { int } from './format.js';

const GROUP_ORDER = ['chassis', 'hybrid', 'electric', 'powertrain', 'safety'];

/** now: {warnings: [{ico, text}], serviceKm} from the last state the dashboard has. */
export async function openHealth(now) {
  paintNow(now);
  $('health-error').hidden = true;
  $('health-report').hidden = true;
  $('health-meta').textContent = '';
  $('health-summary').replaceChildren();
  $('health-sheet').showModal();
  try {
    paintReport((await api.health()).report);
  } catch (err) {
    $('health-error').textContent = err.plain || t('app.unreachable');
    $('health-error').hidden = false;
  }
}

function paintNow({ warnings, serviceKm }) {
  const rows = warnings.map((w) => el('li', { class: 'health-row is-issue' },
    icon(w.ico), el('span', { class: 'health-name' }, w.text)));
  if (!rows.length) {
    rows.push(el('li', { class: 'health-row' }, icon('check', 'health-ok'),
      el('span', { class: 'health-name' }, t('health.now.clear'))));
  }
  if (typeof serviceKm === 'number') {
    rows.push(el('li', { class: `health-row${serviceKm < 1000 ? ' is-issue' : ''}` }, icon('wrench'),
      el('span', { class: 'health-name' }, t('health.now.service', { km: int(serviceKm) }))));
  }
  $('health-now-list').replaceChildren(...rows);
}

function paintReport(report) {
  const summary = $('health-summary');
  if (!report) {
    summary.className = 'health-summary';
    summary.replaceChildren(el('span', {}, t('health.none')));
    return;
  }
  const meta = [t('health.meta'), dateText(report.date),
    typeof report.odometer_km === 'number' ? `${int(report.odometer_km)} km` : null];
  $('health-meta').textContent = meta.filter(Boolean).join(' · ');

  const systems = report.systems || [];
  const issues = systems.filter((s) => s.ok === false).length;
  summary.className = `health-summary${issues ? ' is-issue' : ' is-ok'}`;
  summary.replaceChildren(
    icon(issues ? 'warn' : 'check'),
    el('span', {}, issues
      ? t(issues === 1 ? 'health.issue.one' : 'health.issue.many', { n: issues, total: systems.length })
      : t('health.all_ok', { n: systems.length })));

  paintSystems(systems);
  paintDriving(report.driving || {});
  $('health-systems').hidden = !systems.length;
  $('health-report').hidden = false;
}

function paintSystems(systems) {
  const groups = new Map();
  for (const s of systems) {
    const key = s.group || 'other';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(s);
  }
  const keys = [...groups.keys()].sort((a, b) => rank(a) - rank(b));
  const parts = [];
  for (const key of keys) {
    const label = t(`health.group.${key}`);
    parts.push(el('h4', { class: 'health-group' }, label.startsWith('health.') ? key : label));
    parts.push(el('ul', { class: 'health-list' }, ...groups.get(key).map((s) => {
      const state = s.ok === false ? 'issue' : s.ok === true ? 'ok' : 'unknown';
      return el('li', { class: `health-row${state === 'issue' ? ' is-issue' : ''}` },
        el('span', { class: 'health-name' }, s.name),
        el('span', { class: `health-state is-${state}` },
          state === 'ok' ? icon('check') : state === 'issue' ? icon('warn') : '',
          el('span', { class: state === 'ok' ? 'visually-hidden' : '' }, t(`health.state.${state}`))));
    })));
  }
  $('health-groups').replaceChildren(...parts);
}

function paintDriving(d) {
  const tiles = [];
  if (typeof d.starts === 'number') tiles.push(stat(t('health.starts'), int(d.starts)));
  if (typeof d.distance_km === 'number') tiles.push(stat(t('health.distance'), `${int(d.distance_km)} km`));
  if (typeof d.engine_on_s === 'number') {
    const tile = stat(t('health.engine_on'), duration(d.engine_on_s), 'health-stat-wide');
    if (typeof d.drive_s === 'number' && typeof d.idle_s === 'number' && d.drive_s + d.idle_s > 0) {
      const drivePct = Math.round((d.drive_s / (d.drive_s + d.idle_s)) * 100);
      const bar = el('div', { class: 'health-bar', attrs: { role: 'img',
        'aria-label': t('health.split', { drive: duration(d.drive_s), idle: duration(d.idle_s) }) } },
        el('span', { class: 'health-bar-drive' }));
      bar.firstChild.style.width = `${drivePct}%`;
      tile.append(bar, el('div', { class: 'health-legend', attrs: { 'aria-hidden': 'true' } },
        el('span', { class: 'is-drive' }, `${t('health.drive')} ${duration(d.drive_s)}`),
        el('span', { class: 'is-idle' }, `${t('health.idle')} ${duration(d.idle_s)}`)));
    }
    tiles.push(tile);
  }
  $('health-stats').replaceChildren(...tiles);
  $('health-driving').hidden = !tiles.length;
}

function stat(label, value, cls = '') {
  return el('div', { class: `health-stat ${cls}`.trim() },
    el('span', { class: 'health-stat-label' }, label),
    el('span', { class: 'health-stat-value' }, value));
}

function rank(group) {
  const i = GROUP_ORDER.indexOf(group);
  return i === -1 ? GROUP_ORDER.length : i;
}

export function duration(seconds) {
  const minutes = Math.round(seconds / 60);
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h ? t('health.hm', { h: int(h), m }) : t('health.m', { m });
}

function dateText(iso) {
  if (typeof iso !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(iso)) return null;
  const d = new Date(`${iso}T12:00:00`);
  return Number.isNaN(d.getTime()) ? null
    : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}
