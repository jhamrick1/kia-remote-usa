// The car's 360 view (api/car-spin: frames of kia.ca's turntable, frame 1 = front three-quarter of
// the driver's side, 10 degrees apart). The hero turns the car to show what's open: driver side,
// passenger side (around the front), the rear for the trunk, the front for the hood. A drag turns
// it by hand; it settles back after a few seconds.

const STEP_MS = 42;          // per frame while turning
const DRAG_PX = 14;          // pixels of drag per frame
const SETTLE_MS = 4000;      // back to where it belongs after a drag

// Where to look, in degrees of turntable (frame 1 = 0). null: nothing to turn to.
export const VIEW = { home: 0, driver: 300, passenger: 120, rear: 260, front: 30 };

/** Which way to face for this state, or null when the drawing has to stand in. */
export function viewFor(v) {
  const open = (c) => v?.doors?.[c]?.open === true;
  const driver = open('front_left') || open('rear_left');
  const passenger = open('front_right') || open('rear_right');
  const trunk = v?.trunk_open === true;
  const hood = v?.hood_open === true;
  const other = v?.sunroof_open === true
    || Object.values(v?.windows || {}).some((w) => w === true)
    || ['front_left', 'front_right', 'rear_left', 'rear_right'].some((c) => v?.tires?.[c] === true);
  const count = [driver, passenger, trunk, hood].filter(Boolean).length;
  if (other || count > 1) return null;
  if (driver) return VIEW.driver;
  if (passenger) return VIEW.passenger;
  if (trunk) return VIEW.rear;
  if (hood) return VIEW.front;
  return VIEW.home;
}

/** Load every frame; resolves to a turner, or null when there's no 360 view. */
export async function loadSpin(img, stage) {
  let count = 0;
  try {
    const res = await fetch('api/car-spin', { cache: 'no-store', redirect: 'manual' });
    if (res.ok) count = Number((await res.json()).frames) || 0;
  } catch { /* offline: keep the single picture */ }
  if (count < 8) return null;
  const frames = Array.from({ length: count }, (_, i) => {
    const im = new Image();
    im.decoding = 'async';
    im.src = `api/car-spin/${i + 1}`;
    return im;
  });
  try {
    await Promise.all(frames.map((im) => im.decode()));
  } catch {
    return null;
  }
  return makeTurner(img, stage, frames);
}

function makeTurner(img, stage, frames) {
  const n = frames.length;
  const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  let at = 0;            // frame shown (0-based)
  let goal = 0;          // frame wanted by the car's state
  let timer = 0;
  let held = false;      // dragging
  let settle = 0;

  const show = (i) => { at = ((i % n) + n) % n; img.src = frames[at].src; };
  const frameOf = (deg) => Math.round((((deg % 360) + 360) % 360) / 360 * n) % n;

  function run() {
    clearTimeout(timer);
    if (held || at === goal) return;
    if (reduce) { show(goal); return; }
    const fwd = (goal - at + n) % n;
    show(at + (fwd <= n / 2 ? 1 : -1));
    timer = setTimeout(run, STEP_MS);
  }

  // drag to turn
  let startX = 0;
  let startAt = 0;
  stage.addEventListener('pointerdown', (e) => {
    if (!stage.classList.contains('is-photo')) return;
    held = true; startX = e.clientX; startAt = at;
    clearTimeout(timer); clearTimeout(settle);
    stage.setPointerCapture?.(e.pointerId);
  });
  stage.addEventListener('pointermove', (e) => {
    if (held) show(startAt - Math.round((e.clientX - startX) / DRAG_PX));
  });
  const release = () => {
    if (!held) return;
    held = false;
    settle = setTimeout(run, SETTLE_MS);
  };
  stage.addEventListener('pointerup', release);
  stage.addEventListener('pointercancel', release);

  stage.classList.add('has-spin');
  show(0);
  return {
    face(deg) { goal = frameOf(deg); if (!held) run(); },
  };
}
