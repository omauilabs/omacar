// THE DEMO WITH NO FOOTAGE (hardening D, doc/design/2026-09-30-meetup-demo-hardening.md).
//
// The owner's cameras wait for parts, and the footage waits for them. Until it
// comes the demo must look finished without it, and switch back by itself once
// clips are playing. This is the page's one answer to "is there footage?", and
// the words and the look of what is shown instead.
//
// THE ANSWER IS GET /api/cams, NOT A FLAG. The recorder reports each role
// (lib/cams.py overview()); the demo's camera feed (`cams.py demo`) is that
// recorder with clips for cameras, and with no clips it never starts, so every
// role says "no camera". Footage is a role that is recording. The page looks:
//
//   when the tour starts, and as each of its steps opens   (tour.js: has, refresh)
//   when Home mounts        (the Dashcams card, cards/dashcam.js)
//   when Cameras mounts     (the screen, views/cameras.js)
//
// and the card and the screen go on looking every RECHECK_MS while they show
// the empty state, so a demo started before its footage was in place picks it up
// without a reload. They stop once the live card or view has taken over: those
// poll for themselves.
//
//   createFootage({ get, later, cancel }) -> { has() -> true | false | null, check() -> Promise<true | false | null> }
//   getFootage()                          the page's one
//   hasFootage(overview)                  the question itself, for GET /api/cams's answer
//   emptyState({ roles })                 the empty state's body: a camera glyph and two lines

import { h, icon } from "../../js/core.js";
import { ICONS } from "../../js/icons.js";
import { getJSON } from "../../js/camapi.js";
import { ROLES, ROLE_LABEL } from "../../js/camlogic.js";

// What tour.json's `needs` calls it.
export const NEEDS_CLIPS = "clips";

// A look that is not answered in this long is given up (a miss). The live
// polls use LIVE_TIMEOUT_MS (5 s), and this one is shorter than the next look.
export const POLL_TIMEOUT_MS = 2500;
export const RECHECK_MS = 5000;

// The empty state's words: the brief's, exactly.
export const EMPTY_TITLE = "Front, rear and cabin cameras";
export const EMPTY_LINE = "Recorded in one-minute clips, and a hard stop saves the clip";

// Footage is a camera that is recording. A role that is only starting has none
// yet; a recorder that is not running has none at all; and one camera is enough
// for the live view, whose other feeds say for themselves what they lack.
export function hasFootage(ov) {
  if (!ov || !ov.running || !ov.roles) return false;
  return ROLES.some((r) => ov.roles[r] && ov.roles[r].recording);
}

// `get`, `later` and `cancel` are the page's unless a test hands in its own.
//
// ONE LOOK AT A TIME, AND GIVEN UP: a caller that comes round while a look is
// out waits on that one, and a look still out after POLL_TIMEOUT_MS is aborted.
// A look that fails, or is given up, changes nothing: what was known stands,
// and `null` stays null (nothing is known) rather than turning into a "no".
export function createFootage({ get = getJSON,
                                later = (fn, ms) => setTimeout(fn, ms), cancel = (id) => clearTimeout(id) } = {}) {
  let known = null;
  let out = null;
  return {
    has: () => known,
    check() {
      if (out) return out;
      const ctl = new AbortController();
      const bail = later(() => ctl.abort(), POLL_TIMEOUT_MS);
      let asked;
      try { asked = Promise.resolve(get("/api/cams", { signal: ctl.signal })); } catch (e) { asked = Promise.reject(e); }
      out = asked
        .then((ov) => { known = hasFootage(ov); }, () => { /* a miss: what was known stands */ })
        .then(() => { cancel(bail); out = null; return known; });
      return out;
    },
  };
}

let page = null;
export function getFootage() { return page || (page = createFootage()); }

// ---- what is shown instead --------------------------------------------------------------
//
// Styled as the card's and the screen's own look, in the app's tokens (demo/css/
// nofootage.css): a dark panel, the accent on the glyph. Not as a fault: no
// "No front camera", no "Off", nothing red, no REC. The screen's version adds the
// three cameras by name, the same ones as Camera selection on the live screen.
export function emptyState({ roles = false } = {}) {
  return h("div.nf-body",
    h("span.nf-glyph", icon(ICONS.camera, roles ? 40 : 28)),
    h("div.nf-title", EMPTY_TITLE),
    h("div.nf-line", EMPTY_LINE),
    roles ? h("div.nf-roles", ...ROLES.map((r) =>
      h("span.nf-role", icon(ICONS.camera, 20), h("span", ROLE_LABEL[r].replace(" camera", ""))))) : null);
}
