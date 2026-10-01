// The meetup demo's tour (doc/design/2026-09-30-meetup-demo.md §6): about six
// minutes, eleven steps, one caption each, run unattended.
//
// The steps are data, demo/data/tour.json: a screen to open, a caption, how
// long, and what to do how far in. What a step DOES is always one of three
// things, and never a simulated tap:
//
//   { t, cue }   a cue to the demo world (POST /api/demo/cue): park, drive, ...
//   { t, go }    another screen, by its address (#scan)
//   { t, do }    one of the modules' own functions (boot.js's ACTIONS), with `arg`
//
// ANY TOUCH OR KEY PAUSES IT, because the presenter has started talking: the
// caption goes, "Tour paused · tap Resume" shows for 3 s, and nothing moves
// until Resume (the menu, Space, or the notice itself). Resume carries on from
// the same second of the same step; what had already been done is not done
// again, and if the touch went to another screen the page goes back first. In
// CarPlay or Android Auto, whose screens the page's address does not show, it
// is the projection that goes back: to the screen the step last showed.
//
// THE DRIVE DOES NOT STOP FOR A PRESENTER. Its loop ends in a parked car, and a
// step that shows speed, a turn or a brake is about a moving one. So a Resume
// near the loop's closing stop (within RESYNC_NEAR_S of it), or after a pause
// of more than RESYNC_PAUSE_S, starts the drive over (the `restart` cue) and the
// step with it, from its top, its actions again. The demo is not reset: Home,
// Work and the radio stay as they are. For RESYNC_SETTLE_S after a restart (its
// own, or a fresh start's reset) the world's clock is not believed, since a
// screen that does not poll fast reads a snapshot up to 20 s old.
//
// THE MENU HOLDS THE TOUR'S START. A tour from the top resets the demo first, for
// up to RESET_WAIT_MS; a menu opened in that window has no step to pause, and
// step 1 used to begin under it. The menu calls hold() as it opens and
// release() as it closes, and a start that finishes its reset in between
// waits for the release.
//
// A STEP THAT NEEDS WHAT THE DEMO DOES NOT HAVE IS JUMPED OVER (hardening D). The
// owner's cameras wait for parts, and the demo is shown with no footage: tour.json's
// Cameras step says `"needs": "clips"`, the page tells the tour (`has(need)`) what
// the demo has, and a step whose need it says is not there (`has` answers false) is
// skipped as it comes up: no screen, no caption, no cues. The numbers, keys and
// captions of the other steps stay as they are, so the tour is the one it was with
// 35 s fewer.
// Its key does nothing but toast the step's own `missing` words (the tour, running
// or not, is left alone). `has` answers false only when it knows: null (not asked
// yet, or no answer) enters the step, since the Cameras screen draws an empty state
// of its own and a step that is wrongly shown still looks finished, where one that
// is wrongly skipped is gone. It is asked as each step opens, not once at the start,
// so footage that arrives mid-tour is shown; `refresh()` makes the page look again
// (GET /api/cams): at the start (waited for with the reset, for RESET_WAIT_MS at
// most), in the background as each step opens, and when the key of a skipped step
// is pressed. Whether to look at all (does any step `need` anything?) is asked each
// time and not once when the tour is made: the page hands the tour an empty list
// of steps and fills it after tour.json has loaded (boot.js).
//
// Its keys (the Type Cover's): 1-9 jump to that step (and 0 to the tenth),
// D drowsy, B hard braking, P park or drive, Space pause and resume, Esc the
// menu. They are the tour's, so they do not pause it; anything else does.
//
// Everything outside is injected, so the tests hold the clock and the page:
//
//   clock  { later(fn, ms) -> id, cancel(id), now() -> ms }
//   go(hash), here() -> hash           where the page is, and moving it
//   cue(name), act(name, arg)          the world, and the modules
//   demo() -> { t, loop_secs } | null  the world's clock, the page's store.sample.demo
//   screen(id) -> boolean              a projection's own screen ("" is its first), projection.js openScreen
//   caption(text | null), notice(text, ms)
//   reset() -> Promise                 the demo back to its start, before step 1
//   parked() -> boolean, menu()        for P, and for Esc
//   has(need) -> boolean | null        whether the demo has what a step `needs`; false skips the step
//   refresh() -> Promise               look again at what the demo has (footage.js check)
//   toast(text)                        the quiet word for the key of a skipped step

export const PAUSED = "Tour paused · tap Resume";
export const PAUSED_MS = 3000;
// A reset that hangs (a server that does not answer) must not hold the tour.
export const RESET_WAIT_MS = 3000;

// The loop's last CLOSING_SECS are the car parked at the venue (drive.json's
// closing scene). Resume starts the drive over when the loop is within
// RESYNC_NEAR_S of that, or the tour was paused for more than RESYNC_PAUSE_S,
// and then waits up to RESYNC_WAIT_MS for the world's clock to fall under
// RESYNC_BELOW_S before it begins the step again.
export const CLOSING_SECS = 120;
export const RESYNC_NEAR_S = 180;
export const RESYNC_PAUSE_S = 300;
export const RESYNC_WAIT_MS = 3000;
export const RESYNC_BELOW_S = 30;
// After a restart, the world's clock is not believed for RESYNC_SETTLE_S: a screen that does
// not poll fast reads the snapshot's copy, up to 20 s old, which still says the old drive.
export const RESYNC_SETTLE_S = 25;
const RESYNC_LOOK_MS = 200;       // the world ticks at 5 Hz

export function loadSteps(url = new URL("../data/tour.json", import.meta.url)) {
  return fetch(url, { cache: "no-store" })
    .then((r) => { if (!r.ok) throw new Error(`tour.json: ${r.status}`); return r.json(); })
    .then((doc) => {
      if (!doc || !Array.isArray(doc.steps) || !doc.steps.length) throw new Error("tour.json has no steps");
      return doc.steps.map((s) => ({ ...s, at: Array.isArray(s.at) ? s.at : [] }));
    });
}

// The two screens that hold screens of their own: #carplay, #carplay/maps.
const PROJECTION = /^#(?:carplay|androidauto)(?:\/([^/?#]*))?$/;

const MODIFIERS = new Set(["Shift", "Control", "Alt", "Meta", "OS", "Super", "Hyper", "CapsLock", "Fn"]);

export function createTour(deps = {}) {
  const d = {
    clock: { later: (fn, ms) => setTimeout(fn, ms), cancel: (id) => clearTimeout(id), now: () => Date.now() },
    go: (hash) => { location.hash = hash; },
    here: () => location.hash,
    cue: () => {}, act: () => {},
    demo: () => null,
    screen: () => false,
    caption: () => {}, notice: () => {},
    reset: () => Promise.resolve(),
    parked: () => false,
    menu: () => {},
    has: () => true, refresh: () => Promise.resolve(), toast: () => {},
    ...deps,
  };
  const steps = d.steps || [];
  const subs = new Set();
  let timers = [];
  let startedAt = 0;          // clock ms at which the current step's second 0 was
  let offset = 0;             // seconds into the step when it was paused
  let fired = new Set();      // the current step's actions that have run
  let where = null;           // where Resume takes the page: the step's latest screen
  let wentTo = null;          // the address the tour itself last set
  let epoch = 0;              // bumped by every start and stop: a late reset starts nothing
  let pausedAt = 0;           // clock ms at which the tour was last paused
  let restartedAt = -Infinity; // clock ms at which the tour last had the drive restarted
  let resyncing = false;      // Resume has sent restart and is waiting for the drive
  let held = false;           // the menu is open: no step starts under it
  let waiting = null;         // the start that is waiting for the menu to close

  const tour = {
    steps,
    state: "idle",            // "idle" | "running" | "paused"
    index: -1,
    elapsed: () => (tour.state === "paused" ? offset : (d.clock.now() - startedAt) / 1000),
    subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },

    // From the top (the default) the demo is reset first, so every tour is the
    // same tour: the drive from Marina, Home as it was, Work's sessions fresh.
    // A jump into the middle keeps the world as it is.
    start({ at = 0, fresh = at === 0 } = {}) {
      if (!steps.length) { console.warn("demo tour: no steps (tour.json has not loaded)"); return Promise.resolve(); }
      const mine = ++epoch;
      resyncing = false;
      waiting = null;
      cancelAll();
      if (!fresh) { enter(clampIndex(at)); return Promise.resolve(); }
      set("running", -1);
      restartedAt = d.clock.now();      // the reset cues restart
      let guard = null;
      const waited = new Promise((done) => { guard = d.clock.later(done, RESET_WAIT_MS); });
      let reset;
      try { reset = Promise.resolve(d.reset()); } catch (e) { reset = Promise.reject(e); }
      reset = reset.catch((e) => console.warn("demo tour: the reset failed:", e));
      // The look at what the demo has goes with the reset, under the same cap.
      return Promise.race([Promise.all([reset, look()]), waited]).then(() => {
        d.clock.cancel(guard);
        if (mine === epoch) whenFree(() => enter(clampIndex(at)));
      });
    },
    jump(i) { return tour.start({ at: i, fresh: false }); },

    pause() {
      if (tour.state !== "running" || tour.index < 0) return false;
      offset = Math.max(0, (d.clock.now() - startedAt) / 1000);
      pausedAt = d.clock.now();
      cancelAll();
      set("paused");
      d.caption(null);
      d.notice(PAUSED, PAUSED_MS);
      return true;
    },

    resume() {
      if (tour.state !== "paused" || resyncing || waiting) return false;
      if (driveMovedOn()) { resync(); return true; }
      startedAt = d.clock.now() - offset * 1000;
      set("running");
      if (d.here() !== wentTo) nav(where);
      else backToProjection();
      d.caption(steps[tour.index].caption || null);
      schedule();
      return true;
    },

    toggle() {
      if (tour.state === "running") return tour.pause();
      if (tour.state === "paused") return tour.resume();
      return tour.start();
    },

    // The presenter's menu is open (hold) or has closed (release).
    hold() { held = true; },
    release() {
      held = false;
      const go = waiting;
      waiting = null;
      if (go) go();
    },

    stop() {
      epoch++;
      resyncing = false;
      waiting = null;
      cancelAll();
      set("idle", -1);
      d.caption(null);
    },

    // A touch anywhere on the page.
    touch() { if (tour.state === "running") tour.pause(); },

    // A keydown the page did not keep for a text field. True: the tour used it.
    key(e) {
      if (e.ctrlKey || e.metaKey || e.altKey) return false;
      const k = e.key || "";
      if (MODIFIERS.has(k)) return false;
      const own = () => { if (e.preventDefault) e.preventDefault(); return true; };
      if (/^[0-9]$/.test(k)) {
        const i = k === "0" ? 9 : Number(k) - 1;
        if (i >= steps.length) return false;
        if (skipped(i)) { missing(i); return own(); }
        tour.jump(i);
        return own();
      }
      switch (k.toLowerCase()) {
        case "d": d.cue("drowsy"); return own();
        case "b": d.cue("hard_brake"); return own();
        case "p": d.cue(d.parked() ? "drive" : "park"); return own();
        case " ": case "spacebar": tour.toggle(); return own();
        case "escape": case "esc":
          // The Agent's preview or Work's review sheet is open: Esc is its,
          // and closes it (their own listeners), not the presenter's menu.
          if (overlayOpen()) { tour.touch(); return false; }
          d.menu();
          return own();
        default:
          tour.touch();
          return false;
      }
    },
  };

  function overlayOpen() {
    return typeof document !== "undefined" && !!document.querySelector("[data-demo-overlay]");
  }

  function clampIndex(i) { return Math.max(0, Math.min(steps.length - 1, Number(i) || 0)); }

  // Is the step one the demo cannot show right now? Only `false` says so.
  function skipped(i) {
    const need = steps[i] && steps[i].needs;
    if (!need) return false;
    try { return d.has(need) === false; }
    catch (e) { console.warn("demo tour: what the demo has:", e); return false; }
  }

  // The first step from `i` on that can be shown, or -1 when none of them can.
  function showable(i) {
    for (let j = i; j < steps.length; j++) if (!skipped(j)) return j;
    return -1;
  }

  // The page looks again at what the demo has. Never rejects: what it knew stands.
  // Only a tour with a step that needs something ever asks, and that is asked HERE,
  // each time: the page hands the tour an empty `steps` and fills it once tour.json
  // has loaded (boot.js), so it cannot be known when the tour is made.
  function look() {
    if (!steps.some((s) => s.needs)) return Promise.resolve();
    try { return Promise.resolve(d.refresh()).catch((e) => console.warn("demo tour: looking at what the demo has:", e)); }
    catch (e) { console.warn("demo tour: looking at what the demo has:", e); return Promise.resolve(); }
  }

  // The key of a step that is jumped over says why, quietly, and the page looks
  // again so that the next press knows if the footage has come.
  function missing(i) {
    try { d.toast(steps[i].missing || "That step is not available in this demo"); }
    catch (e) { console.warn("demo tour: the toast:", e); }
    look();
  }

  function set(state, index = tour.index) {
    tour.state = state;
    tour.index = index;
    for (const fn of subs) { try { fn(tour); } catch (e) { console.error(e); } }
  }

  function cancelAll() {
    for (const id of timers) d.clock.cancel(id);
    timers = [];
  }

  // The world's clock, or null when the page has none to read.
  function worldClock() {
    let w = null;
    try { w = d.demo(); } catch (e) { console.warn("demo tour: the drive's clock:", e); }
    return w && Number.isFinite(w.t) && Number.isFinite(w.loop_secs) ? w : null;
  }

  // Has the drive left the tour behind? Near its closing stop, or paused so
  // long that the story is cold.
  function driveMovedOn() {
    if ((d.clock.now() - pausedAt) / 1000 > RESYNC_PAUSE_S) return true;
    if ((d.clock.now() - restartedAt) / 1000 < RESYNC_SETTLE_S) return false;   // the clock still says the old drive
    const w = worldClock();
    return !!w && w.t >= w.loop_secs - CLOSING_SECS - RESYNC_NEAR_S;
  }

  // The drive over, then the step over. The tour stays "paused" until the
  // world's clock has fallen back (so the step's own cues land on the new
  // drive, not the old one), but for RESYNC_WAIT_MS at most. A jump, a stop or
  // a new start in the meantime (epoch) ends the wait: it was overtaken.
  function resync() {
    resyncing = true;
    const mine = epoch;
    const began = d.clock.now();
    restartedAt = began;
    try { d.cue("restart"); } catch (e) { console.warn("demo tour: the restart cue:", e); }
    const watchClock = () => {
      if (mine !== epoch) return;
      const w = worldClock();
      if ((w && w.t < RESYNC_BELOW_S) || d.clock.now() - began >= RESYNC_WAIT_MS) {
        resyncing = false;
        whenFree(() => enter(tour.index));
        return;
      }
      timers.push(d.clock.later(watchClock, RESYNC_LOOK_MS));
    };
    timers.push(d.clock.later(watchClock, RESYNC_LOOK_MS));
  }

  // A projection's screens are its own and the address says only `#carplay`,
  // so a presenter who tapped around inside it left the page "where the tour
  // put it". Put it on the screen the step last showed (`where` is that, with
  // the screen after the slash once a step has opened one); "" is its first.
  // When the page itself has gone elsewhere, nav(where) remounts it on that
  // address and this is not needed.
  function backToProjection() {
    const m = PROJECTION.exec(where || "");
    if (!m) return;
    try { d.screen(m[1] || ""); } catch (e) { console.warn("demo tour: back to the projection's screen:", e); }
  }

  // What has to wait for the menu, waits.
  function whenFree(go) {
    if (held) waiting = go;
    else go();
  }

  function nav(hash) {
    if (!hash) return;
    wentTo = hash;
    d.go(hash);
  }

  function enter(from) {
    cancelAll();
    resyncing = false;          // a wait whose timer was just cancelled is over, however it came to be
    const i = showable(from);
    if (i < 0) { finish(); return; }        // nothing from here on can be shown: that was the end
    const s = steps[i];
    fired = new Set();
    offset = 0;
    startedAt = d.clock.now();
    set("running", i);
    where = s.go;
    nav(s.go);
    d.caption(s.caption || null);
    schedule();
    look();                     // for the steps after this one: the page asks again, in the background
  }

  // The last step is Home, and the page stays there.
  function finish() {
    cancelAll();
    set("idle", -1);
    d.caption(null);
  }

  function schedule() {
    const s = steps[tour.index];
    const el = (d.clock.now() - startedAt) / 1000;
    s.at.forEach((a, k) => {
      if (fired.has(k)) return;
      timers.push(d.clock.later(() => fire(k), Math.max(0, (Number(a.t) || 0) - el) * 1000));
    });
    timers.push(d.clock.later(next, Math.max(0, s.secs - el) * 1000));
  }

  function fire(k) {
    const a = steps[tour.index].at[k];
    fired.add(k);
    try {
      if (a.cue) d.cue(a.cue);
      if (a.go) { where = a.go; nav(a.go); }
      if (a.do) {
        const r = d.act(a.do, a.arg);
        if (r && typeof r.catch === "function") r.catch((e) => console.warn(`demo tour: ${a.do} failed:`, e));
      }
      if (a.resumeAt) where = a.resumeAt;
    } catch (e) {
      console.warn(`demo tour: step ${steps[tour.index].id}, ${a.do || a.cue || a.go} failed:`, e);
    }
  }

  function next() {
    if (tour.index + 1 < steps.length) enter(tour.index + 1);
    else finish();
  }

  return tour;
}

// ---- the demo back to its start ----------------------------------------------------
//
// What a tour from the top and the menu's "Restart the drive" both do first:
// the radio quiet, the world's `restart` cue (the drive from Marina), Work's
// sessions as work.json starts them, and Home and its look as they were before
// the agent's Apply. Each step's failure is logged and the rest go on. Home
// goes through the server (/api/home), so the wait for it is capped at
// RESET_WAIT_MS: a server that does not answer never holds a restart.
//
//   createReset({ fade, radio, cue, resetWork, restoreHome, later, cancel }) -> () => Promise
//
// `fade` (quiet.js fadeOut) takes the music down to silence first, so it never
// cuts in one step (hardening B), for RESET_FADE_MS at most: the fade takes
// 1.2 s, and a restart is never held by the music.

export const RESET_FADE_MS = 2000;

export function createReset({ fade, radio, cue, resetWork, restoreHome,
                              later = (fn, ms) => setTimeout(fn, ms), cancel = (id) => clearTimeout(id) } = {}) {
  const step = (what, fn) => {
    try { return fn(); } catch (e) { console.warn(`demo reset: ${what}:`, e); return undefined; }
  };
  return async function reset() {
    if (fade) {
      let cap = null;
      const capped = new Promise((done) => { cap = later(done, RESET_FADE_MS); });
      const faded = Promise.resolve(step("the music", () => fade())).catch((e) => console.warn("demo reset: the music:", e));
      await Promise.race([faded, capped]);
      cancel(cap);
    }
    step("the radio", () => radio && radio.pause());
    Promise.resolve(step("the restart cue", () => cue && cue("restart"))).catch(() => {});
    step("Work", () => resetWork && resetWork());
    let guard = null;
    const waited = new Promise((done) => { guard = later(done, RESET_WAIT_MS); });
    const home = Promise.resolve(step("Home", () => restoreHome && restoreHome()))
      .catch((e) => console.warn("demo reset: Home:", e));
    await Promise.race([home, waited]);
    cancel(guard);
  };
}

// ---- the words on the screen -------------------------------------------------------
//
// One line at the bottom, over everything the tour shows (the projections, the
// agent's preview, a Level 2 alert), fading out and in between steps. The pause
// notice takes the same place, and is itself a Resume button.
//
// ONE LINE AT EVERY WIDTH, AND NEVER WRAPPED. The stylesheet sets the size (22 px,
// 20 in portrait) and the text shrinks from there, a pixel at a time, to the
// largest that fits, down to CAP_MIN_PX. What does not fit even then is cut with
// an ellipsis (demo.css), not wrapped onto a second line.
//
//   createCaptions({ host, onResume }) -> { caption(text | null), notice(text, ms), destroy() }

export const FADE_MS = 280;
export const CAP_MIN_PX = 16;

// The largest whole size from `max` down to `min` at which `fits(px)` is true;
// `min` when none is, and `max` itself when it is already under `min`.
export function fitSize(fits, max, min = CAP_MIN_PX) {
  const top = Math.round(max);
  for (let px = top; px > min; px--) if (fits(px)) return px;
  return Math.min(top, min);
}

export function createCaptions({ host = document.body, onResume = () => {},
                                 later = (fn, ms) => setTimeout(fn, ms), cancel = (id) => clearTimeout(id) } = {}) {
  const words = document.createElement("span");
  words.className = "dt-cap-t";
  const cap = document.createElement("div");
  cap.className = "dt-cap";
  cap.setAttribute("role", "status");
  cap.setAttribute("aria-live", "polite");
  cap.appendChild(words);
  const note = document.createElement("button");
  note.type = "button";
  note.className = "dt-note";
  note.hidden = true;
  note.addEventListener("click", () => { hideNote(); onResume(); });
  host.appendChild(cap);
  host.appendChild(note);

  let shown = null, swap = null, noteTimer = null;

  // Does the text, at the size it has, fit the line? Sub-pixel: a box that is
  // at its maximum width clips a text that is 0.3 px wider, with an ellipsis
  // that scrollWidth (which rounds) would never report.
  function fits() {
    const r = document.createRange();
    r.selectNodeContents(words);
    return r.getBoundingClientRect().width <= words.getBoundingClientRect().width + 0.05;
  }

  function fit() {
    cap.style.fontSize = "";                     // the stylesheet's: 22 px, 20 in portrait
    const max = parseFloat(getComputedStyle(cap).fontSize) || 20;
    const px = fitSize((p) => { cap.style.fontSize = `${p}px`; return fits(); }, max);
    cap.style.fontSize = px === Math.round(max) ? "" : `${px}px`;
  }

  // A turned screen changes the width, and the stylesheet's size with it.
  const onResize = () => { if (shown && words.textContent) fit(); };
  window.addEventListener("resize", onResize);

  function hideNote() {
    if (noteTimer !== null) { cancel(noteTimer); noteTimer = null; }
    note.classList.remove("on");
    note.hidden = true;
  }

  return {
    caption(text) {
      const want = text || null;
      if (want === shown) return;
      shown = want;
      if (swap !== null) { cancel(swap); swap = null; }
      const fadeIn = () => {
        swap = null;
        if (!shown) return;
        words.textContent = shown;
        fit();
        cap.classList.add("on");
      };
      if (cap.classList.contains("on")) {
        cap.classList.remove("on");
        swap = later(fadeIn, FADE_MS);
      } else {
        fadeIn();
      }
    },
    notice(text, ms) {
      hideNote();
      // "Tour paused · tap Resume", with Resume in bold: the word to tap.
      const i = text.lastIndexOf("Resume");
      note.replaceChildren(document.createTextNode(i >= 0 ? text.slice(0, i) : text));
      if (i >= 0) {
        const b = document.createElement("b");
        b.textContent = text.slice(i);
        note.appendChild(b);
      }
      note.hidden = false;
      // A frame later, so the fade in is a transition and not a jump.
      requestAnimationFrame(() => note.classList.add("on"));
      noteTimer = later(hideNote, ms);
    },
    hideNote,
    destroy() {
      hideNote();
      if (swap !== null) cancel(swap);
      window.removeEventListener("resize", onResize);
      cap.remove();
      note.remove();
    },
  };
}
