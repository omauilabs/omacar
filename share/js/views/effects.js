// Two homages, full screen, driven by the car.
//
// WHY THEY ARE HERE AND NOT BEHIND A GAUGE.
//
// The four effects on the hub are readings rendered as motion: the stars
// stretch because the car is going faster, the bubbles quicken because the
// coolant is hotter. They belong behind the numbers they describe.
//
// These two are not that. A scanner sweeping under a speed readout would be
// decoration pretending to be instrumentation, and this tool has spent a lot
// of its comments refusing to do exactly that. So they get a destination of
// their own, which somebody opens on purpose, while parked, to look at.
//
// THEY STILL TAKE THE CAR AS INPUT. A scanner that sweeps at a fixed rate is a
// screensaver. The eye hurries with engine speed; Moya's glow rises with the
// state of the pack. If the car is not answering they run at a resting default
// rather than freezing, because a still screen reads as broken.

import { h, clear, store } from "../core.js";
import { mountShow } from "../cardfx.js";

const SHOWS = [
  { id: "scanner", name: "Scanner",
    sub: "Knight Industries Two Thousand",
    note: "The eye sweeps with engine speed. It slows at the ends because the "
        + "original is a physical scanner, not a light on a timer." },
  { id: "moya", name: "Moya",
    sub: "Leviathan",
    note: "Bioluminescence under the shell, brightening with the charge in the "
        + "IMA pack. No geometry and no repeat: the moment it reads as a "
        + "pattern it stops reading as alive." },
];

const KEY = "omacar.show";

function saved() {
  try {
    const v = localStorage.getItem(KEY);
    return SHOWS.some((s) => s.id === v) ? v : SHOWS[0].id;
  } catch { return SHOWS[0].id; }
}

export default function effects(root) {
  let current = saved();
  let stop = null;

  const stage = h("div.show-stage");
  const title = h("div.show-title");
  const sub = h("div.show-sub");
  const note = h("p.show-note");
  const picker = h("div.show-picker");

  // The readings the shows are allowed to see. One object, built fresh each
  // frame from the live store -- the renderers never reach into the app.
  const read = () => {
    const v = store.values || {};
    return { rpm: v.RPM, soc: v.HYBRID_BATTERY_REMAINING, speed: v.SPEED };
  };

  function mount(id) {
    if (stop) { stop(); stop = null; }
    current = id;
    try { localStorage.setItem(KEY, id); } catch { /* private mode */ }
    const spec = SHOWS.find((s) => s.id === id) || SHOWS[0];
    title.textContent = spec.name;
    sub.textContent = spec.sub;
    note.textContent = spec.note;
    for (const b of picker.children) {
      b.setAttribute("aria-current", b.dataset.id === id ? "true" : "false");
    }
    stage.dataset.show = id;
    stop = mountShow(stage, id, read);
  }

  for (const s of SHOWS) {
    const b = h("button.show-pick", { type: "button", onclick: () => mount(s.id) },
                s.name);
    b.dataset.id = s.id;
    picker.appendChild(b);
  }

  clear(root);
  root.appendChild(h("div.show",
    stage,
    h("div.show-card", title, sub, note, picker)));
  mount(current);

  return () => { if (stop) { stop(); stop = null; } };
}
