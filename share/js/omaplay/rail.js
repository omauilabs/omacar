// The car, beside the phone.
//
// WHAT THIS COLUMN IS FOR.
//
// Split mode puts navigation on the larger part of the screen and this on the
// rest. It is NOT drive mode squeezed into a narrow strip: drive mode is what
// you look at when the car is the thing you are attending to, and this is what
// you glance at while attending to the road. So it is four readouts, large,
// and nothing else. A fifth would be one more thing to read at 70mph and the
// whole reason it is worth having is that it can be taken in without being
// read.
//
// WHY THE DEFINITIONS ARE HERE AND NOT SHARED WITH DRIVE MODE.
//
// Drive mode's catalogue lives in a `const TILES` inside views/drive.js and is
// not exported, so there is no shared catalogue to import — and the four here
// want different scales anyway, because a gauge that must be legible out of
// the corner of an eye is not the same gauge as one being studied. Lifting
// TILES into gauges.js as a shared catalogue is the right eventual fix and is
// noted in the roadmap rather than done here, because drive.js is a file this
// change has no other reason to touch.
//
// THE BUG THIS FILE IS WRITTEN TO AVOID.
//
// Building the structure once and updating values in place, rather than
// re-rendering on every sample. This codebase has shipped the rebuild-per-tick
// mistake twice — the hub view visibly blinked at 4 Hz — and a strip that
// flickers beside live navigation would be worse than not having it.

import { h, store, U, temp } from "../core.js";
import { makeGauge } from "../gauges.js";

const num = (x, f) => (x === null || x === undefined || Number.isNaN(x)
  ? null : f(Number(x)));

const asTemp = (c) => (U.imperial ? c * 9 / 5 + 32 : c);

// Four, deliberately. Each is { label, kind, scale, get, read } in the shape
// gauges.js already expects: `get` returns what to print, `read` returns the
// raw number the needle or arc is positioned from.
export const RAIL = [
  {
    id: "speed",
    label: "Speed",
    kind: "dial",
    scale: () => ({ min: 0, max: U.imperial ? 140 : 220,
                    step: U.imperial ? 20 : 40 }),
    read: (v) => num(v.SPEED, (x) => x * U.units.km),
    get: (v) => ({ v: num(v.SPEED, (x) => Math.round(x * U.units.km)) ?? "—",
                   n: U.units.speed }),
  },
  {
    id: "rpm",
    label: "Engine",
    kind: "dial",
    // 7000 rather than a redline guess: the CR-Z's limiter is not something
    // this file knows, and inventing one would put a red zone on a gauge in
    // the wrong place.
    scale: () => ({ min: 0, max: 7000, step: 1000 }),
    read: (v) => num(v.RPM, (x) => x),
    get: (v) => ({ v: num(v.RPM, (x) => Math.round(x)) ?? "—", n: "rpm" }),
  },
  {
    id: "coolant",
    label: "Coolant",
    kind: "arc",
    scale: () => ({ min: asTemp(40), max: asTemp(120),
                    step: U.imperial ? 40 : 20,
                    // 100C is where watch.py starts caring, so the band on the
                    // gauge and the rule that fires an alert agree.
                    warn: asTemp(100), bad: asTemp(105) }),
    read: (v) => num(v.COOLANT_TEMP, asTemp),
    get: (v) => {
      const c = v.COOLANT_TEMP;
      return { v: c === null || c === undefined ? "—" : temp(c, false),
               n: U.units.temp,
               tone: c >= 105 ? "bad" : c >= 100 ? "warn" : "" };
    },
  },
  {
    id: "volts",
    label: "Battery",
    kind: "arc",
    scale: () => ({ min: 10, max: 16, step: 2,
                    tick: (x) => String(Math.round(x)) }),
    read: (v) => num(v.CONTROL_MODULE_VOLTAGE, (x) => x),
    get: (v) => {
      const x = v.CONTROL_MODULE_VOLTAGE;
      return { v: x === null || x === undefined ? "—" : Number(x).toFixed(1),
               n: "V",
               // Below 12.4 with the engine running means it is not charging;
               // watch.py has a `not_charging` rule at the same edge.
               tone: x !== null && x !== undefined && x < 12.4 ? "warn" : "" };
    },
  },
];

export function gaugeRail() {
  const root = h("div.op-rail");
  const cells = [];

  // Structure once.
  for (const def of RAIL) {
    const g = makeGauge(def.kind, def);
    const tile = h("div.op-rail-tile", { data: { id: def.id } },
      h("div.op-rail-k", def.label));
    tile.appendChild(g.el);
    root.appendChild(tile);
    cells.push({ def, g });
  }

  let stale = null;

  function paint() {
    const v = store.sample || {};
    for (const c of cells) {
      let out;
      try {
        out = c.def.get(v);
      } catch {
        out = { v: "—", n: "" };
      }
      c.g.update(out, c.def.read ? c.def.read(v) : null);
    }
    // A car that stopped answering must look like one. Dimming the whole rail
    // is better than four gauges frozen at their last reading, which is
    // indistinguishable from a car sitting at a steady 60mph.
    //
    // So must a hand-off. Its sample carries a FRESH `t` -- the daemon
    // re-stamps its last reading while the adapter is lent out -- so the age
    // test alone called it live; `handover` is what says it is not.
    const fresh = (v.t || 0) > 0 && (Date.now() / 1000 - v.t) < 10 && !v.handover;
    if (fresh !== stale) {
      root.dataset.live = fresh ? "1" : "0";
      stale = fresh;
    }
  }

  paint();
  const offLive = store.on("live", paint);
  // "car", not "units". The store emits exactly two events, `car` and `live` —
  // there is no `units` event, and subscribing to one would have been a
  // listener that never fired and a redraw that never happened. Units ride
  // along with the vehicle payload, so `car` is the event that actually
  // carries a change of system.
  const offCar = store.on("car", paint);

  return {
    el: root,
    destroy() { offLive(); offCar(); },
  };
}
