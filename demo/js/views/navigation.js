// The demo's Navigation screen (doc/design/2026-09-30-meetup-demo.md §4), and
// the moving map every other demo screen borrows.
//
//   navigationView(root, { arg }) -> unmount     the Navigation tab's Maps screen
//   mountMap(el, { style, follow, mpp }) -> { destroy() }
//   register(D)                                  opens the tab on this screen, fast
//
// A full-bleed map that follows the demo car heading-up, the next turn in a
// banner at the top as Home's nav card draws it in mockup 3, the ETA along the
// bottom, a chip to the live app's Road cameras, and the OpenStreetMap credit.
// Everything comes from store.live's `demo` block (lib/demoworld.py), read on
// every paint; without one the screen says it is waiting rather than drawing
// a car that is not there.
//
// THE MAP MOVES EVERY FRAME, THE WORDS FOUR TIMES A SECOND. Samples arrive at
// 4 Hz, and a car jumping 8 m at a time at 70 mph is a slideshow. So between
// samples the camera carries on at the sample's speed along its heading, for
// at most 0.6 s, and eases into each new one; a jump of more than 300 m (the
// loop starting again) is a cut, not a pan.

import { h, store, icon, speed as speedText } from "../../../js/core.js";
import { ICONS } from "../../../js/icons.js";
import { project, createMap, loadMapData, STYLES } from "../map.js";
import { bannerOf, etaOf, iconSvg, streetLine } from "../nav.js";

const RAD = Math.PI / 180;
// drive.json's destination (Task 1). live.json does not carry it; if it ever
// does, as demo.destination.label, that wins.
export const DESTINATION = "Omarchy Meetup, San Francisco";
export const CREDIT = "© OpenStreetMap contributors";

const text = (el, s) => { if (el.textContent !== s) el.textContent = s; };
export const demoOf = () => (store.sample && store.sample.demo) || null;
const wrap = (a) => ((a % 360) + 540) % 360 - 180;

// A compass needle that turns the short way round: the angle it is given is
// unwrapped against the last one, so 359 -> 1 is two degrees, not 358. The N
// on it (its <b>) is turned back by as much, so it stays upright as the needle
// swings round it, as mockup 3 draws it.
export function compassTurner(el) {
  let shown = null;
  return (heading) => {
    const want = -(Number(heading) || 0);
    shown = shown === null ? want : shown + wrap(want - shown);
    el.style.transform = `rotate(${shown.toFixed(1)}deg)`;
    const n = el.querySelector("b");
    if (n) n.style.transform = `rotate(${(-shown).toFixed(1)}deg)`;
  };
}

// ---- the moving map -----------------------------------------------------------

// `el` is filled: the canvas and its shade are laid over it, absolutely, so
// it needs a size of its own (a static element is made relative). `style` is
// one of map.js's STYLES; `follow` turns the map heading-up with the car at
// 70% down (false: north-up, the car in the middle); `mpp`, if given, fixes
// the scale instead of easing between 3 m/px driving and 1.5 stopped.
export function mountMap(el, { style = "omacar", follow = true, mpp = null } = {}) {
  if (getComputedStyle(el).position === "static") el.style.position = "relative";
  const canvas = h("canvas.dmap-canvas", { "aria-hidden": "true",
    style: { position: "absolute", inset: "0", width: "100%", height: "100%", display: "block" } });
  // A soft fall-off away from the car, so the eye lands on it, as on the
  // mockups' lit maps. Composited over the canvas, never redrawn with it.
  const [ax, ay] = follow ? [50, 70] : [50, 50];
  const dark = (STYLES[style] || STYLES.omacar).vignette || 0;
  const shade = h("div.dmap-shade", { "aria-hidden": "true", style: {
    position: "absolute", inset: "0", pointerEvents: "none",
    background: `radial-gradient(circle farthest-corner at ${ax}% ${ay}%, transparent 25%, rgba(0, 0, 0, ${dark}) 100%)` } });
  el.appendChild(canvas);
  el.appendChild(shade);
  let alive = true, map = null, data = null, raf = 0, last = 0, dirty = true, missing = null;
  const cam = { x: 0, y: 0, heading: 0, carHeading: 0, mpp: mpp || 3, set: false };
  const seen = { key: "", at: 0, x: 0, y: 0, heading: 0, v: 0, route_m: NaN };
  const cost = { frames: 0, ms: 0, worst: 0 };

  loadMapData().then((d) => {
    if (!alive) return;
    data = d;
    map = createMap(canvas, { data: d, style, anchor: follow ? [0.5, 0.7] : [0.5, 0.5] });
    map.resize();
    // Until the first sample: the route's start, facing along it.
    const r = d.route || [];
    if (r.length > 1) {
      cam.x = r[0][0]; cam.y = r[0][1];
      cam.carHeading = Math.atan2(r[1][0] - r[0][0], r[1][1] - r[0][1]) / RAD;
      cam.heading = follow ? cam.carHeading : 0;
    }
    dirty = true;
  }).catch((e) => {
    if (!alive) return;
    // Said quietly in the map's place, never a blank canvas that looks broken.
    el.dataset.missing = "1";
    missing = h("div.dmap-missing", "Map unavailable");
    el.appendChild(missing);
    console.warn("The demo map could not load:", (e && e.message) || e);
  });

  const ro = typeof ResizeObserver === "function"
    ? new ResizeObserver(() => { if (map) { map.resize(); dirty = true; } }) : null;
  if (ro) ro.observe(el);

  function frame(now) {
    raf = requestAnimationFrame(frame);
    const dt = last ? Math.min(0.25, (now - last) / 1000) : 0;
    last = now;
    if (!map || !el.isConnected) return;
    const demo = demoOf();
    const kph = Number((store.sample.values || {}).SPEED) || 0;
    let car = null;
    if (demo && Number.isFinite(demo.lat) && Number.isFinite(demo.lon)) {
      const key = `${demo.t}|${demo.lat}|${demo.lon}`;
      if (key !== seen.key) {
        const [x, y] = project(demo.lat, demo.lon, data.origin);
        Object.assign(seen, { key, at: now, x, y, heading: Number(demo.heading) || 0,
                              v: demo.parked ? 0 : kph / 3.6, route_m: Number(demo.route_m) });
      }
      const ahead = Math.min(0.6, (now - seen.at) / 1000) * seen.v;
      car = { x: seen.x + Math.sin(seen.heading * RAD) * ahead,
              y: seen.y + Math.cos(seen.heading * RAD) * ahead, heading: seen.heading,
              route_m: seen.route_m + ahead };
    }
    const before = [cam.x, cam.y, cam.heading, cam.carHeading, cam.mpp];
    if (car) {
      const jump = Math.hypot(car.x - cam.x, car.y - cam.y);
      if (!cam.set || jump > 300) {
        Object.assign(cam, { x: car.x, y: car.y, carHeading: car.heading, set: true });
        if (follow) cam.heading = car.heading;
      } else {
        const k = 1 - Math.exp(-dt / 0.12);
        cam.x += (car.x - cam.x) * k;
        cam.y += (car.y - cam.y) * k;
        const kh = 1 - Math.exp(-dt / 0.35);
        cam.carHeading += wrap(car.heading - cam.carHeading) * kh;
        if (follow) cam.heading = cam.carHeading;
      }
    }
    // About 3 m a pixel driving and 1.5 stopped, eased over a second or so.
    const wantMpp = mpp || (car && kph > 2 ? 3 : 1.5);
    cam.mpp += (wantMpp - cam.mpp) * (1 - Math.exp(-dt / 0.9));
    const after = [cam.x, cam.y, cam.heading, cam.carHeading, cam.mpp];
    if (!dirty && before.every((v, i) => Math.abs(v - after[i]) < 0.005)) return;
    dirty = false;
    map.setView({ x: cam.x, y: cam.y, heading: follow ? cam.heading : 0, mpp: cam.mpp,
                  car: car || cam.set ? { x: cam.x, y: cam.y, heading: cam.carHeading,
                                          route_m: car ? car.route_m : NaN } : null });
    const t0 = performance.now();
    map.draw();
    const ms = performance.now() - t0;
    cost.frames++; cost.ms += ms; cost.worst = Math.max(cost.worst, ms);
  }
  raf = requestAnimationFrame(frame);

  return {
    get map() { return map; },
    // Milliseconds per draw, for the budget (16 ms on the tablet).
    cost: () => ({ frames: cost.frames, mean: cost.frames ? cost.ms / cost.frames : 0, worst: cost.worst }),
    destroy() {
      alive = false;
      cancelAnimationFrame(raf);
      if (ro) ro.disconnect();
      if (map) map.destroy();
      canvas.remove();
      shade.remove();
      if (missing) missing.remove();
    },
  };
}

// ---- the screen ----------------------------------------------------------------

export default function navigationView(root) {
  const mapEl = h("div.dnav-map");
  const turnIcon = h("div.dnav-icon", { "aria-hidden": "true" });
  const dist = h("div.dnav-dist");
  const what = h("div.dnav-what");
  const onto = h("div.dnav-street");
  const banner = h("div.dnav-banner", { role: "status", "aria-live": "polite" },
    turnIcon, h("div.dnav-words", dist, h("div.dnav-line", what, onto)));
  const needle = h("span.dnav-needle", h("i"), h("b", "N"));
  const compass = h("div.dnav-compass", { title: "North" }, needle);
  const turn = compassTurner(needle);
  const cams = h("a.dnav-chip", { href: "#roadcams" }, icon(ICONS.camera || ICONS.nav, 18), "Road cameras");
  const road = h("div.dnav-road");
  const mph = h("b"), unit = h("span");
  const speedo = h("div.dnav-speed", mph, unit);
  const etaTime = h("b.dnav-time");
  const left = h("span.dnav-left");
  const dest = h("span.dnav-dest");
  const bottom = h("div.dnav-bottom",
    h("div.dnav-eta", h("span.dnav-k", "ETA"), etaTime),
    h("span.dnav-dot", "·"), left,
    h("div.dnav-to", h("span.dnav-k", "To"), dest));
  const wait = h("div.dnav-wait", { hidden: true },
    h("div.dnav-wait-card",
      h("div.dnav-wait-t", "Waiting for the demo drive"),
      h("div.dnav-wait-s", "The map picks the car up as soon as the drive starts.")));
  const credit = h("div.dnav-credit", CREDIT);
  const screen = h("div.dnav", mapEl, banner, h("div.dnav-side", cams, compass), road, speedo, bottom, wait, credit);
  root.appendChild(screen);
  const map = mountMap(mapEl, { style: "omacar", follow: true });

  function paint() {
    const demo = demoOf();
    const has = !!(demo && Number.isFinite(demo.lat));
    screen.dataset.state = has ? (demo.parked ? "parked" : "drive") : "waiting";
    wait.hidden = has;
    if (!has) return;
    const b = demo.next ? bannerOf(demo.next) : { icon: "arrive", distance: "", text: "Arrived", street: "" };
    if (turnIcon.dataset.icon !== b.icon) { turnIcon.innerHTML = iconSvg(b.icon); turnIcon.dataset.icon = b.icon; }
    text(dist, b.distance);
    text(what, b.text);
    text(onto, streetLine(b));
    const e = etaOf(demo);
    text(etaTime, e.time);
    text(left, `${e.distance} · ${e.remaining}`);
    text(dest, (demo.destination && demo.destination.label) || DESTINATION);
    text(road, demo.street || "");
    road.hidden = !demo.street;
    const kph = Number((store.sample.values || {}).SPEED);
    const [n, u] = speedText(Number.isFinite(kph) ? kph : null).split(" ");
    text(mph, n); text(unit, u || "");
    turn(demo.heading);
  }
  const off = store.on("live", paint);
  paint();

  return () => { off(); map.destroy(); };
}

export function register(D) {
  D.views.navigation = { mount: navigationView, fast: true };
  D.tabRoots.navigation = "navigation";
}
