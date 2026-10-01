// Home's Dashcams card in the demo: the live card while a camera is recording,
// and, while none is, a composed empty state (hardening D).
//
// The owner's cameras wait for parts. With no footage the live card says "No
// front camera" in the voice of a fault, in front of the room. This one shows
// what the demo's cameras are for: the camera glyph, "Front, rear and cabin
// cameras" and "Recorded in one-minute clips, and a hard stop saves the clip",
// styled as the card's own look (demo/css/nofootage.css), no REC and no reason.
//
// IT DOES NOT REPLACE THE LIVE CARD, IT HANDS OVER TO IT. When a camera is
// recording (footage.js, GET /api/cams) the live card (share/js/dashcard.js)
// is made on this card's own node and does everything it does today: the
// picture, REC, the drowsy chip, the AUX line, its own polls. So footage that
// arrives later is on Home with no reload, and the live app's states are not
// touched, but for one thing: its drowsy chip reads the DEMO's controller
// (demo/js/drowsy.js), not the live engine's, which the demo does not run and
// which says "Off" beside REC while the top bar says "Watching". The card looks
// when it is made (Home mounted) and, while it shows the empty state, every
// RECHECK_MS; once the live card has taken over it stops, since the live card
// polls for itself.
//
//   dashcamCard({ footage, live, liveCard, engine, go, every, cancel }) -> { node, paint(), destroy() }    home.js's card shape
//   register(D, deps)                                  D.cards.dashcam = dashcamCard

import { h, clear, icon } from "../../../js/core.js";
import { ICONS } from "../../../js/icons.js";
import { dashcamCard as liveDashcamCard } from "../../../js/dashcard.js";
import { getFootage, emptyState, RECHECK_MS } from "../footage.js";
import { demoEngine } from "../drowsy.js";

const goto = (id) => { location.hash = "#" + id; };

export function dashcamCard({
  footage = getFootage(),
  // The live card's own engine is the page's drowsy controller; with none made
  // (undefined) it keeps the live default, as the live app has it.
  engine = demoEngine,
  liveCard = liveDashcamCard,
  live = (node) => liveCard(node, { engine: engine() || undefined }),
  go = goto,
  every = (fn, ms) => setInterval(fn, ms),
  cancel = (id) => clearInterval(id),
} = {}) {
  // The live card's own classes, so the live look is exactly as it is today
  // (share/css/cameras.css's .hc-cam); .nf-card is the empty state's.
  const node = h("div.card.hc.hc-cam");
  node.setAttribute("role", "button");
  node.tabIndex = 0;
  node.addEventListener("click", () => { if (!node.closest(".editing")) go("cameras"); });
  node.addEventListener("keydown", (e) => { if (e.key === "Enter") go("cameras"); });

  let mode = null;            // "empty" | "live"
  let card = null;            // the live card, while it has the node
  let timer = null;           // the look every RECHECK_MS, while empty
  let dead = false;
  let turn = 0;               // bumped by every change: a live card still to be made for an old one is not

  function stopLooking() {
    if (timer !== null) { cancel(timer); timer = null; }
  }

  function drop() {
    if (card) { try { card.destroy(); } catch (e) { console.warn("demo Dashcams card:", e); } card = null; }
  }

  function show(want) {
    if (want === mode) return;
    mode = want;
    const mine = ++turn;
    drop();
    clear(node);
    node.classList.toggle("nf-card", want === "empty");
    if (want === "empty") {
      node.append(h("div.hc-title", icon(ICONS.camera, 18), "Dashcams"), emptyState());
      if (timer === null) timer = every(look, RECHECK_MS);
      return;
    }
    stopLooking();
    // A MICROTASK LATER, WHEN HOME HAS PUT THE NODE IN THE GRID. The live card's
    // first look at the cameras finds its node out of the document and waits for
    // its next 3 s tick ("Checking the front camera…"), and Home appends a card
    // only after making it. (home.js builds the live card after an import, which
    // is later in the same way.)
    Promise.resolve().then(() => {
      if (dead || mine !== turn) return;
      try { card = live(node); }
      catch (e) {
        // Words, never a black card: the live card could not be made.
        console.warn("demo Dashcams card:", e);
        mode = null;
        show("empty");
      }
    });
  }

  // The answer decides; a look that tells nothing (null) leaves what is shown.
  function look() {
    return footage.check().then((has) => {
      if (dead || has === null || has === undefined) return;
      show(has ? "live" : "empty");
    });
  }

  // What it knew, at once, and never a blank card while it asks: with no answer
  // yet it is the empty state.
  show(footage.has() === true ? "live" : "empty");
  look();

  return {
    node,
    paint() { if (card && card.paint) card.paint(); },
    destroy() { dead = true; stopLooking(); drop(); },
  };
}

export function register(D, deps = {}) {
  D.cards.dashcam = () => dashcamCard(deps);
}
