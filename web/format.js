// Formatting helpers. Every function copes with null/undefined and returns the quiet dash.
import { t } from './strings.js';

export const DASH = t('dash');

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

const intFmt = new Intl.NumberFormat('en-CA', { maximumFractionDigits: 0 });
const oneFmt = new Intl.NumberFormat('en-CA', { maximumFractionDigits: 1 });

/** Whole number with thousands separators, or the dash. */
export function int(v) {
  return isNum(v) ? intFmt.format(v).replace('-', '−') : DASH;
}

/** Up to one decimal (temperatures), true minus sign. */
export function dec(v) {
  return isNum(v) ? oneFmt.format(v).replace('-', '−') : DASH;
}

/** "21.5 °C" for a set temperature; "HI" / "LO" when the car reports the ends of the dial. */
export function temp(v) {
  if (v === 'HI' || v === 'LO') return v;
  return isNum(v) ? `${dec(v)}${t('unit.c')}` : DASH;
}

export function num(v) {
  return isNum(v) ? v : null;
}

/** Minutes since an ISO time, or null. */
export function minutesSince(iso, now = Date.now()) {
  if (!iso) return null;
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return null;
  return Math.max(0, (now - ms) / 60000);
}

/** "12 min ago" / "3 h ago" / "2 d ago" / "just now", or null. */
export function ago(iso, now) {
  const m = minutesSince(iso, now);
  if (m === null) return null;
  if (m < 1) return t('ago.just');
  if (m < 60) return t('ago.min', { n: Math.floor(m) });
  if (m < 60 * 24) return t('ago.hour', { n: Math.floor(m / 60) });
  return t('ago.day', { n: Math.floor(m / 1440) });
}

/** Lat/lon to "45.42153° N, 75.69719° W". */
export function coords(lat, lon) {
  if (!isNum(lat) || !isNum(lon)) return null;
  return t('loc.coords', {
    lat: Math.abs(lat).toFixed(5), ns: t(lat >= 0 ? 'loc.n' : 'loc.s'),
    lon: Math.abs(lon).toFixed(5), ew: t(lon >= 0 ? 'loc.e' : 'loc.w'),
  });
}

/** A maps link that opens the native maps app on Android and Google Maps on iPhone and desktop. */
export function mapsUrl(lat, lon) {
  if (!isNum(lat) || !isNum(lon)) return null;
  return `https://www.google.com/maps/search/?api=1&query=${lat.toFixed(6)},${lon.toFixed(6)}`;
}
