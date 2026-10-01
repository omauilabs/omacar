// Home's Dashcams card in the demo: the live card while a camera is recording,
// and, while none is, a composed empty state (hardening D).
//
// The owner's cameras wait for parts. With no footage the live card says "No
// front camera" in the voice of a fault, in front of the room. This one shows
// what the demo's cameras are for: the camera glyph, "Front, rear and cabin
// cameras" and "Recorded in one-minute clips, and a hard stop saves the clip",
// styled as the card's own look (demo/css/nofootage.css), no REC and no reason.
//
// IT DOES NOT REPLACE THE LIVE CARD, IT HANDS OVER TO IT, AND BACK. When a camera
// is recording (footage.js, GET /api/cams) the live card (share/js/dashcard.js)
// is made on this card's own node and does everything it does today: the
// picture, REC, the AUX line, its own polls, so footage that arrives later is on
// Home with no reload. Its drowsy chip reads the DEMO's controller
// (demo/js/drowsy.js), not the live engine's, which the demo does not run and
// which says "Off" beside REC while the top bar says "Watching".
//
// THE CARD LOOKS WHEN IT IS MADE (Home mounted) AND EVERY RECHECK_MS AFTER, in both
// states. With no camera recording for HANDBACK_LOOKS looks in a row it destroys
// the live card and draws the empty state again (a feed that died, a folder that
// was emptied); a look that fails changes nothing and one that finds a camera
// starts the count again.
//
// UNTIL THE FIRST ANSWER IT DRAWS ITS FRAME AND NOTHING IN IT, as the Cameras
// screen draws nothing: a page that has not asked the cameras yet cannot know
// which to show, and the wrong one flashes (the empty state before a picture, or
// the live card's "Checking the front camera" before an empty state).
//
//   dashcamCard({ footage, live, liveCard, engine, go, every, cancel }) -> { node, paint(), destroy() }    home.js's card shape
//   register(D, deps)                                  D.cards.dashcam = dashcamCard

import { h, clear, icon } from "../../../js/core.js";
import { ICONS } from "../../../js/icons.js";
import { dashcamCard as liveDashcamCard } from "../../../js/dashcard.js";
import { getFootage, emptyState, RECHECK_MS, HANDBACK_LOOKS } from "../footage.js";
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

  let mode = null;            // null | "wait" (no answer yet) | "empty" | "live"
  let card = null;            // the live card, while it has the node
  let dead = false;
  let turn = 0;               // bumped by every change: a live card still to be made for an old one is not
  let misses = 0;             // looks in a row that found no camera, while live

  function drop() {
    if (card) { try { card.destroy(); } catch (e) { console.warn("demo Dashcams card:", e); } card = null; }
  }

  function show(want) {
    if (want === mode) return;
    mode = want;
    misses = 0;
    const mine = ++turn;
    drop();                                  // the live card goes before anything is drawn in its place
    clear(node);
    node.classList.toggle("nf-card", want !== "live");
    if (want === "empty") {
      node.append(h("div.hc-title", icon(ICONS.camera, 18), "Dashcams"), emptyState());
      return;
    }
    if (want === "wait") return;             // the frame, and nothing in it
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

  // The answer decides. A look that tells nothing (null) leaves what is shown, but
  // never leaves the card blank for good: with no answer ever, it is the empty state.
  function look() {
    return footage.check().then((has) => {
      if (dead) return;
      if (has === true) { misses = 0; show("live"); return; }
      if (has === false) {
        if (mode === "live" && ++misses < HANDBACK_LOOKS) return;
        show("empty");
        return;
      }
      if (mode === "wait") show("empty");
    });
  }

  // What it knew, at once; with no answer yet, the frame.
  const known = footage.has();
  show(known === true ? "live" : known === false ? "empty" : "wait");
  look();
  const timer = every(look, RECHECK_MS);

  return {
    node,
    paint() { if (card && card.paint) card.paint(); },
    destroy() { dead = true; cancel(timer); drop(); },
  };
}

export function register(D, deps = {}) {
  D.cards.dashcam = () => dashcamCard(deps);
}
