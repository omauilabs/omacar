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
// Work and the radio stay as they are.
//
// THE MENU HOLDS THE TOUR'S START. A tour from the top resets the demo first, for
// up to RESET_WAIT_MS; a menu opened in that window has no step to pause, and
// step 1 used to begin under it. The menu calls hold() as it opens and
// release() as it closes, and a start that finishes its reset in between
// waits for the release.
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
//   demo() -> { t, loop_secs } | null  the world's clock, the page's store.live.demo
//   screen(id) -> boolean              a projection's own screen ("" is its first), projection.js openScreen
//   caption(text | null), notice(text, ms)
//   reset() -> Promise                 the demo back to its start, before step 1
//   parked() -> boolean, menu()        for P, and for Esc

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
      let guard = null;
      const waited = new Promise((done) => { guard = d.clock.later(done, RESET_WAIT_MS); });
      let reset;
      try { reset = Promise.resolve(d.reset()); } catch (e) { reset = Promise.reject(e); }
      reset = reset.catch((e) => console.warn("demo tour: the reset failed:", e));
      return Promise.race([reset, waited]).then(() => {
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
      if (tour.state !== "paused" || resyncing) return false;
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
    try { d.cue("restart"); } catch (e) { console.warn("demo tour: the restart cue:", e); }
    const look = () => {
      if (mine !== epoch) return;
      const w = worldClock();
      if ((w && w.t < RESYNC_BELOW_S) || d.clock.now() - began >= RESYNC_WAIT_MS) {
        resyncing = false;
        whenFree(() => enter(tour.index));
        return;
      }
      timers.push(d.clock.later(look, RESYNC_LOOK_MS));
    };
    timers.push(d.clock.later(look, RESYNC_LOOK_MS));
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

  function enter(i) {
    cancelAll();
    const s = steps[i];
    fired = new Set();
    offset = 0;
    startedAt = d.clock.now();
    set("running", i);
    where = s.go;
    nav(s.go);
    d.caption(s.caption || null);
    schedule();
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
    if (tour.index + 1 < steps.length) { enter(tour.index + 1); return; }
    // The last step is Home, and the page stays there.
    cancelAll();
    set("idle", -1);
    d.caption(null);
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
//   createReset({ radio, cue, resetWork, restoreHome, later, cancel }) -> () => Promise

export function createReset({ radio, cue, resetWork, restoreHome,
                              later = (fn, ms) => setTimeout(fn, ms), cancel = (id) => clearTimeout(id) } = {}) {
  const step = (what, fn) => {
    try { return fn(); } catch (e) { console.warn(`demo reset: ${what}:`, e); return undefined; }
  };
  return async function reset() {
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
//   createCaptions({ host, onResume }) -> { caption(text | null), notice(text, ms), destroy() }

export const FADE_MS = 280;

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
    destroy() { hideNote(); if (swap !== null) cancel(swap); cap.remove(); note.remove(); },
  };
}
