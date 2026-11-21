// The dashboard's temperature unit, from the region the car service reports in /api/state.
// The service's contract stays Celsius (set_temp_c); the Kia Access USA region also takes
// set_temp_f -- the car's own 62-82 dial -- so there the dashboard speaks °F throughout.
let unit = 'c';
let dial = null;   // the region's dial in its own units (°F for usa), from /api/state's region block

/** From /api/state: region = {key, name, unit: 'c'|'f', dial: {min, max} | null} | null. */
export function setRegion(region) {
  if (region?.unit === 'f' && region?.dial) {
    unit = 'f';
    dial = region.dial;
  } else if (region) {
    unit = 'c';
    dial = null;
  }
}

export const isF = () => unit === 'f';
export const fDial = () => dial;

/** °C as the service reports it -> the number to show (whole °F, or °C unchanged); null stays null. */
export function fromC(c) {
  if (typeof c !== 'number' || !Number.isFinite(c)) return null;
  return unit === 'f' ? Math.round(c * 9 / 5 + 32) : c;
}
