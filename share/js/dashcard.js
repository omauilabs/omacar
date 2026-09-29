// Home's Dashcams card: the front camera's live picture with a REC dot while
// the recorder runs, and in words why not when it does not -- no camera, or
// recorder off -- so a black rectangle never stands in for a reason. A test
// picture says SIMULATED, the same rule as the car's numbers.
//
// It also carries drowsy mode's chip, and says when the AUX cable is out.
//
// home.js makes the card's node (a tap opens Cameras) and imports this module
// where the card is made: see the redesign/cameras block in its MAKERS.
import { h } from "./core.js";
import { getJSON, liveUrl } from "./camapi.js";
import { dashState } from "./camlogic.js";
import { LIVE_TIMEOUT_MS, drowsy } from "./drowsyrun.js";
import { chipTone, chipHint } from "./drowsyui.js";
import { showAux } from "./audiostate.js";

export const POLL_MS = 3000;

// `engine`, `poll` and the timers are the page's unless a test hands in its
// own. clearTimeout and clearInterval share one list of timers, so `cancel`
// takes either kind of id.
export function dashcamCard(node, {
  engine = drowsy,
  poll = getJSON,
  every = (fn, ms) => setInterval(fn, ms),
  later = (fn, ms) => setTimeout(fn, ms),
  cancel = (id) => clearTimeout(id),
} = {}) {
  const img = h("img.dc-img", { alt: "Front camera, live", draggable: "false", hidden: true });
  const why = h("div.dc-why");
  const rec = h("span.dc-rec", { hidden: true }, h("span.dc-dot"), "REC");
  const sim = h("span.dc-sim", { hidden: true }, "SIMULATED");
  const dz = h("span.dc-drowsy");
  const top = h("div.dc-top", h("span.dc-title", "Dashcams"), sim, rec, dz);
  const aux = h("div.dc-aux", { hidden: true });
  node.append(h("div.dc-stage", img, why), top, aux);

  // THE TOP BAR'S CHIP, NOT A SECOND OPINION OF IT. Its text is the engine's;
  // its tone and its title are drowsyui.js's, the very ones the top bar's chip
  // uses, so the two chips cannot disagree.
  const offDz = engine.on((st) => {
    dz.textContent = st.chip;
    dz.dataset.tone = chipTone(st.chip);
    dz.title = chipHint(st);
  });

  // THE AUX LINE IS DROWSY MODE'S: showAux() says "AUX disconnected" for
  // exactly as long as the one audio state (audiostate.js) knows the port is
  // the tablet's speakers, and nothing while it is unknown. Not decided again
  // here, so Home, Begin and drowsy mode's own screens agree.
  const offAux = showAux(aux);

  let streaming = false, dead = false, busy = false;
  img.addEventListener("error", () => { streaming = false; });

  // ONE POLL AT A TIME, AND GIVEN UP. A server that stops answering must not
  // collect a request every 3 s: the browser lets a host have six, and the
  // live picture and every other poll on this page share them
  // (audiostate.js, drowsyrun.js pollCams). A request that is given up reads
  // as no server, which is what the card then says.
  async function refresh() {
    if (busy) return;
    busy = true;
    let ov = null;
    const ctl = new AbortController();
    const bail = later(() => ctl.abort(), LIVE_TIMEOUT_MS);
    try { ov = await poll("/api/cams", { signal: ctl.signal }); }
    catch { /* dashState says so */ }
    finally { cancel(bail); busy = false; }
    if (dead) return;
    const s = dashState(ov);
    rec.hidden = !s.rec;
    sim.hidden = !s.sim;
    why.hidden = !s.why;
    why.textContent = s.why || "";
    if (s.live) {
      if (!streaming) { img.src = liveUrl("front"); img.hidden = false; streaming = true; }
    } else {
      // Whatever the picture was doing, with no picture to show it goes, and
      // its address with it: a stream that failed has already lost `streaming`.
      img.removeAttribute("src");
      img.hidden = true;
      streaming = false;
    }
  }
  refresh();
  const timer = every(refresh, POLL_MS);

  return {
    top,
    paint() {},
    destroy() { dead = true; cancel(timer); img.removeAttribute("src"); offDz(); offAux(); },
  };
}
