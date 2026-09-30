// Scan vehicle, as the meetup demo runs it (doc/design/2026-09-30-meetup-demo.md
// §4, the Vehicle row). Routed at #scan in place of the live Full system scan.
//
// The live screen (share/js/views/scan.js) asks the daemon to re-read every
// module (POST /api/scan), which the demo server refuses: there is no car. So
// this draws its own, in the live screen's look and with its classes, and says
// what it is: the CR-Z's eight modules, read one after another over 12 s, each
// "Scanning…" and then "No codes", and at the end "All systems normal".
//
//   createScan(root, { later, cancel }) -> unmount     the tests hold the clock
//   default export: the view's mount

import { h, icon } from "../../../js/core.js";
import { ICONS } from "../../../js/icons.js";

// The CR-Z's control units, as Honda names them.
export const MODULES = [
  { name: "PGM-FI", what: "Engine and emissions" },
  { name: "IMA", what: "Hybrid battery and motor" },
  { name: "ABS/VSA", what: "Brakes and stability" },
  { name: "SRS", what: "Airbags" },
  { name: "EPS", what: "Power steering" },
  { name: "Body", what: "Lights, locks and windows" },
  { name: "Meter", what: "Instrument cluster" },
  { name: "A/C", what: "Climate control" },
];
export const SCAN_SECS = 12;
export const DONE_TEXT = `All systems normal · ${MODULES.length} modules · 0 codes`;

const CHECK = ["M20 6 9 17l-5-5"];

export function createScan(root, { later = (fn, ms) => setTimeout(fn, ms), cancel = (id) => clearTimeout(id) } = {}) {
  const per = (SCAN_SECS * 1000) / MODULES.length;
  const timers = [];
  let alive = true;

  const bar = h("i");
  const count = h("span.ds-count", `0 of ${MODULES.length}`);
  const lines = MODULES.map((m) => {
    const st = h("span.ds-st", "Waiting");
    const prog = h("i");
    const line = h("div.scanline.ds-line", { data: { state: "wait" } },
      h("div.mname", h("span.dot"), h("span.ds-name", m.name), h("span.ds-what", m.what)),
      st,
      h("div.scan-prog", { style: { gridColumn: "1 / -1" } }, prog));
    return { line, st, prog };
  });
  const done = h("div.card.tint-ok.ds-done", { hidden: true },
    h("span.ds-ok", icon(CHECK, 26)),
    h("div", h("div.ds-done-t", DONE_TEXT),
      h("div.muted.ds-done-s", "2015 Honda CR-Z · every module answered · just now")));
  const head = h("section.sect",
    h("div.head",
      h("div", h("div.eyebrow", "Diagnostics"), h("div.title", "Scan vehicle")),
      h("div.right.row", h("span.pill.info.ds-pill", icon(ICONS.scan, 14), "Demo car"))),
    h("p.lede", "Reads every control unit in the car and says, in plain English, what each one is holding."));
  const list = h("div.card.ds-card",
    h("div.row.ds-top", h("div.eyebrow", "Reading modules"), h("div.muted.right", count)),
    h("div.ds-bar", bar),
    h("div.ds-lines", lines.map((l) => l.line)));
  const screen = h("div.ds", head, h("div.sect", done, list));
  root.appendChild(screen);

  function begin(i) {
    if (!alive) return;
    const l = lines[i];
    l.line.dataset.state = "scan";
    l.st.textContent = "Scanning…";
    l.prog.style.width = "100%";
    l.prog.style.transitionDuration = `${per / 1000}s`;
    bar.style.width = `${((i + 0.5) / MODULES.length) * 100}%`;
    timers.push(later(() => finish(i), per));
  }

  function finish(i) {
    if (!alive) return;
    const l = lines[i];
    l.line.dataset.state = "done";
    l.st.textContent = "No codes";
    l.line.querySelector(".dot").className = "dot ok";
    count.textContent = `${i + 1} of ${MODULES.length}`;
    bar.style.width = `${((i + 1) / MODULES.length) * 100}%`;
    if (i + 1 < MODULES.length) { begin(i + 1); return; }
    done.hidden = false;
    screen.dataset.done = "1";
  }

  timers.push(later(() => begin(0), 0));
  return () => {
    alive = false;
    for (const id of timers) cancel(id);
    timers.length = 0;
  };
}

export default function scanView(root) {
  return createScan(root);
}

export function register(D) {
  D.views.scan = scanView;
}
