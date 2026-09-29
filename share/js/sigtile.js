// A signal tile: icon, label, value, unit, range and a sparkline of the last
// minutes. Home and Vehicle use the same one, so the same reading can never
// look different on two screens.
//
// It draws a number as current only when the reading is live, and then it says
// where the number came from (data-src). During a hand-off it draws the last
// number read, dimmed, with "Paused" beside it and no source. Otherwise it
// draws words: "Not on this car" or "Waiting for the car". A zero is never a
// stand-in.

import { h, icon, store } from "./core.js";
import { ICONS } from "./icons.js";
import { READINGS, readingState, pausedNote, raw } from "./readings.js";
import { trail } from "./trail.js";
import { sparkPath } from "./spark.js";
import { sourceKey } from "./provenance.js";

const NS = "http://www.w3.org/2000/svg";
const ICON_FOR = { coolant: "thermo", intake: "thermo", ambient: "thermo",
                   volts: "battery", fuel: "fuel", charge: "leaf", ima: "leaf",
                   rpm: "gauge", speed: "gauge" };
const text = (el, s) => { if (el.textContent !== s) el.textContent = s; };

export function makeSignalTile(id, { label, def } = {}) {
  const d = def || READINGS[id];
  if (!d) throw new Error(`no reading called ${id}`);
  const v = h("span.sig-v.display-num"), u = h("span.sig-u"), note = h("span.sig-note");
  const lo = h("span"), hi = h("span");
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 120 28");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(NS, "path");
  svg.appendChild(path);
  const node = h("div.sig", { data: { reading: id } },
    h("div.sig-head", icon(ICONS[ICON_FOR[id]] || ICONS.data, 18), h("span.sig-k", label || d.label)),
    h("div.sig-body",
      h("div.sig-val", v, u, note),
      h("div.sig-spark", svg, h("div.sig-scale", lo, hi))));

  return {
    node,
    paint(car, sample) {
      const s = sample || {};
      const st = readingState(d, s);
      node.dataset.state = st;
      if (st === "live") {
        const out = d.get(s.values || {}, s, car);
        text(v, String(out.v));
        text(u, out.n || "");
        text(note, "");
        node.dataset.tone = out.tone || "";
        node.dataset.src = sourceKey(car, s);
        // A SCALE AND A TRAIL ARE BOTH CLAIMS ABOUT A VALUE, so they are only
        // drawn here, alongside the number they describe.
        const sc = d.scale ? d.scale() : null;
        text(lo, sc ? tick(sc, sc.min) : "");
        text(hi, sc ? tick(sc, sc.max) : "");
        // The trail is raw (Celsius, volts); the scale is in display units,
        // so each point goes through the same read() the number did.
        const pts = d.pid && d.read
          ? trail(d.pid).map(([t, r]) => [t, d.read({ [d.pid]: r }, s, car)]).filter((p) => p[1] !== null)
          : [];
        path.setAttribute("d", sparkPath(pts, 120, 28, sc ? sc.min : null, sc ? sc.max : null));
      } else if (st === "paused" && (!d.pid || raw((s.values || {})[d.pid]) !== null)) {
        // THE LAST VALUE, NOT NOW. Drawn so the driver keeps a number, in the
        // dimmed ink app.css gives data-state="paused", and never with what
        // makes a number current: a source, a scale or a trail.
        //
        // BUT ITS WARNING STAYS. A coolant figure that was in the red when the
        // adapter was lent out is the last thing known about the engine; a
        // hand-off that greyed it out would hide a warning for as long as two
        // minutes at a time. The tone is kept, and app.css dims it rather
        // than dropping it.
        const out = d.get(s.values || {}, s, car);
        text(v, String(out.v));
        text(u, out.n || "");
        text(note, pausedNote(store.pausedSince));
        node.dataset.tone = out.tone || "";
        delete node.dataset.src;
        text(lo, "");
        text(hi, "");
        path.setAttribute("d", "");
      } else {
        text(v, "");
        text(u, "");
        text(note, st === "paused" ? pausedNote(store.pausedSince)
          : st === "absent" ? "Not on this car" : "Waiting for the car");
        node.dataset.tone = "";
        delete node.dataset.src;
        // AN ABSENT, WAITING OR EMPTY PAUSED TILE HAS NO VALUE TO PLACE ON
        // EITHER ONE. A "0 ... 100" scale under an empty number, or a
        // sparkline with nothing on it, both read as a real (if boring)
        // measurement rather than as "nothing has been read yet".
        text(lo, "");
        text(hi, "");
        path.setAttribute("d", "");
      }
    },
  };
}

function tick(sc, x) {
  return sc.tick ? sc.tick(x) : String(Math.round(x));
}
