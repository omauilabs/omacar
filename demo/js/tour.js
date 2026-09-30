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
// again, and if the touch went to another screen the page goes back first.
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
//   caption(text | null), notice(text, ms)
//   reset() -> Promise                 the demo back to its start, before step 1
//   parked() -> boolean, menu()        for P, and for Esc

export const PAUSED = "Tour paused · tap Resume";
export const PAUSED_MS = 3000;
// A reset that hangs (a server that does not answer) must not hold the tour.
export const RESET_WAIT_MS = 3000;

export function loadSteps(url = new URL("../data/tour.json", import.meta.url)) {
  return fetch(url, { cache: "no-store" })
    .then((r) => { if (!r.ok) throw new Error(`tour.json: ${r.status}`); return r.json(); })
    .then((doc) => {
      if (!doc || !Array.isArray(doc.steps) || !doc.steps.length) throw new Error("tour.json has no steps");
      return doc.steps.map((s) => ({ ...s, at: Array.isArray(s.at) ? s.at : [] }));
    });
}

const MODIFIERS = new Set(["Shift", "Control", "Alt", "Meta", "OS", "Super", "Hyper", "CapsLock", "Fn"]);

export function createTour(deps = {}) {
  const d = {
    clock: { later: (fn, ms) => setTimeout(fn, ms), cancel: (id) => clearTimeout(id), now: () => Date.now() },
    go: (hash) => { location.hash = hash; },
    here: () => location.hash,
    cue: () => {}, act: () => {},
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
        if (mine === epoch) enter(clampIndex(at));
      });
    },
    jump(i) { return tour.start({ at: i, fresh: false }); },

    pause() {
      if (tour.state !== "running" || tour.index < 0) return false;
      offset = Math.max(0, (d.clock.now() - startedAt) / 1000);
      cancelAll();
      set("paused");
      d.caption(null);
      d.notice(PAUSED, PAUSED_MS);
      return true;
    },

    resume() {
      if (tour.state !== "paused") return false;
      startedAt = d.clock.now() - offset * 1000;
      set("running");
      if (d.here() !== wentTo) nav(where);
      d.caption(steps[tour.index].caption || null);
      schedule();
      return true;
    },

    toggle() {
      if (tour.state === "running") return tour.pause();
      if (tour.state === "paused") return tour.resume();
      return tour.start();
    },

    stop() {
      epoch++;
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
//   createReset({ fade, radio, cue, resetWork, restoreHome, later, cancel }) -> () => Promise
//
// `fade` (quiet.js fadeOut) takes the music down to silence first, so it never
// cuts in one step (hardening B).

export function createReset({ fade, radio, cue, resetWork, restoreHome,
                              later = (fn, ms) => setTimeout(fn, ms), cancel = (id) => clearTimeout(id) } = {}) {
  const step = (what, fn) => {
    try { return fn(); } catch (e) { console.warn(`demo reset: ${what}:`, e); return undefined; }
  };
  return async function reset() {
    await Promise.resolve(step("the music", () => fade && fade())).catch((e) => console.warn("demo reset: the music:", e));
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
