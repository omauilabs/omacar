// The Cameras screen in the demo: the live screen while a camera is recording,
// and, while none is, a composed empty state (hardening D).
//
// Mockup 6's Cameras is a main feed, two small ones, the event timeline and the
// playback bar, all of it footage. With no footage (the owner's cameras wait for
// parts) it would be three "No camera" rectangles, in the voice of a fault. This
// is the camera glyph, "Front, rear and cabin cameras", "Recorded in one-minute
// clips, and a hard stop saves the clip" and the three cameras by name, in the
// same look as Home's card (demo/css/nofootage.css).
//
// IT DOES NOT REPLACE THE LIVE SCREEN, IT HANDS OVER TO IT, AND BACK. When a camera is
// recording (footage.js, GET /api/cams) share/js/views/cameras.js is mounted on
// the same root with the same options and is the screen it always is, unchanged:
// footage that arrives while the empty state is up replaces it with no reload.
// The screen looks when it is mounted and every RECHECK_MS after, in both states;
// with no camera recording for HANDBACK_LOOKS looks in a row it unmounts the live
// screen and draws the empty state again (a feed that died, a folder that was
// emptied). A look that fails changes nothing and one that finds a camera starts
// the count again.
//
// With no answer yet it draws nothing for the moment the look takes (a few ms on
// the box's own server), so a demo with footage never flashes the empty state.
// A look that tells nothing at all (null) is the empty state, never a blank
// screen.
//
//   camerasView({ footage, live, every, cancel }) -> mount(root, { arg }) -> unmount
//   register(D, deps)                              D.views.cameras = camerasView(deps)

import { clear, h } from "../../../js/core.js";
import liveCamerasView from "../../../js/views/cameras.js";
import { getFootage, emptyState, RECHECK_MS, HANDBACK_LOOKS } from "../footage.js";

export function camerasView({
  footage = getFootage(),
  live = liveCamerasView,
  every = (fn, ms) => setInterval(fn, ms),
  cancel = (id) => clearInterval(id),
} = {}) {
  return function mount(root, opts) {
    let alive = true;
    let mode = null;                // "empty" | "live"
    let unmountLive = null;
    let misses = 0;                 // looks in a row that found no camera, while live

    function dropLive() {
      if (!unmountLive) return;
      const un = unmountLive;
      unmountLive = null;
      try { un(); } catch (e) { console.warn("demo Cameras:", e); }
    }

    function show(want) {
      if (want === mode) return;
      mode = want;
      misses = 0;
      dropLive();                   // the live screen goes before anything is drawn in its place
      clear(root);
      if (want === "empty") {
        root.appendChild(h("div.nf-view", emptyState({ roles: true })));
        return;
      }
      try { unmountLive = live(root, opts) || null; }
      catch (e) {
        console.warn("demo Cameras:", e);
        mode = null;
        show("empty");
      }
    }

    // The answer decides. Nothing known and nothing told is the empty state.
    function look() {
      return footage.check().then((has) => {
        if (!alive) return;
        if (has === true) { misses = 0; show("live"); return; }
        if (has === false) {
          if (mode === "live" && ++misses < HANDBACK_LOOKS) return;
          show("empty");
          return;
        }
        if (mode === null) show("empty");
      });
    }

    const known = footage.has();
    if (known !== null && known !== undefined) show(known ? "live" : "empty");
    look();
    const timer = every(look, RECHECK_MS);

    return () => {
      alive = false;
      cancel(timer);
      dropLive();
    };
  };
}

export function register(D, deps = {}) {
  D.views.cameras = camerasView(deps);
}
