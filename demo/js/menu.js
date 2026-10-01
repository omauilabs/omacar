// The presenter's menu (doc/design/2026-09-30-meetup-demo.md §6, "Cues"):
// opened by a long press on the OmaCar logo, or Esc.
//
//   Start tour / Resume tour      Resume first while the tour is paused
//   Drowsy moment                 the cues, POSTed to /api/demo/cue
//   Hard braking
//   Park / Drive                  by the car's state
//   Restart the drive             the world, Home and Work back to their start
//   Play backup video             says the command: the page cannot start mpv
//   Exit demo                     says how: the page cannot close its own kiosk window
//
// Opening it pauses a running tour: whoever opened it is about to talk. A tour
// that is still resetting (there is no step to pause yet) is held: it starts
// its first step once the menu has closed.
//
// "Hard braking" says "The clip is saved" only while a camera is recording
// (`saved()`, from footage.js; `refresh()` looks again as the menu opens, and the
// items are drawn again with what it finds): with no footage there is no clip.
//
//   createCues({ post, sample, now }) -> { send(name), parked() }
//   createMenu({ tour, cues, host, restart, saved, refresh }) -> { open(), close(), toggle(), isOpen() }

import { h, icon } from "../../js/core.js";

// On the tablet `omacar` on the PATH is the live app's, an older checkout; the
// demo is run through its own wrapper, `omacar-demo`.
export const BACKUP_TEXT = "omacar-demo video";
export const EXIT_TEXT = "Run omacar-demo off";

// How long the menu believes its own Park or Drive over what the world last
// said: the screens that do not poll fast see the world every 20 s.
const BELIEVE_MS = 15000;

// ---- the cues -------------------------------------------------------------------

export function postCue(name) {
  return fetch("/api/demo/cue", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cue: name }),
  }).then((r) => { if (!r.ok) throw new Error(`the cue ${name}: ${r.status}`); return r.json(); });
}

// `sample()` is the world's latest `demo` block (store.sample.demo), or null.
export function createCues({ post = postCue, sample = () => null, now = () => Date.now() } = {}) {
  let said = null;            // { parked, at }: the last Park or Drive sent from here
  return {
    send(name) {
      if (name === "park" || name === "drive") said = { parked: name === "park", at: now() };
      if (name === "restart") said = { parked: false, at: now() };
      let sent;
      try { sent = Promise.resolve(post(name)); } catch (e) { sent = Promise.reject(e); }
      return sent.catch((e) => { console.warn("demo cue:", e); throw e; });
    },
    parked() {
      if (said && now() - said.at < BELIEVE_MS) return said.parked;
      const s = sample();
      return !!(s && s.parked);
    },
  };
}

// ---- the menu --------------------------------------------------------------------

const G = {
  play: ["M7 4.5v15l12-7.5z"],
  resume: ["M5 4.5v15l10-7.5z", "M18 5v14"],
  eye: ["M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z", "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"],
  brake: ["M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z", "M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z", "M4.5 4.5l2 2", "M17.5 17.5l2 2"],
  park: ["M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z", "M9 17V7h4a3 3 0 0 1 0 6H9"],
  drive: ["M5 12h14", "M13 6l6 6-6 6"],
  restart: ["M3 12a9 9 0 1 0 3-6.7", "M3 4v5h5"],
  film: ["M4 4h16v16H4z", "M8 4v16", "M16 4v16", "M4 9h4", "M16 9h4", "M4 15h4", "M16 15h4"],
  exit: ["M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4", "M10 17l-5-5 5-5", "M5 12h11"],
  x: ["M18 6 6 18", "M6 6l12 12"],
};

export function createMenu({ tour, cues, host = document.body, restart = () => {},
                             saved = () => true, refresh = null } = {}) {
  let sheet = null;
  let offTour = null;
  let drawnSaved = null;      // what the items were last drawn as
  // While it is open every key is the menu's: Esc closes it, Tab, Enter and
  // Space work its buttons, and the tour and the page hear none of them.
  const onKey = (e) => {
    if (!sheet) return;
    e.stopPropagation();
    if (e.key === "Escape") { e.preventDefault(); close(); }
  };

  function row({ id, g, t, s, key, primary, run, keepOpen }) {
    const note = h("div.dm-note", { hidden: true });
    const btn = h("button.dm-item" + (primary ? ".primary" : ""), { type: "button", data: { item: id },
      onclick: () => {
        const said = run();
        if (keepOpen) {
          if (said) { note.replaceChildren(...said); note.hidden = false; }
          return;
        }
        close();
      } },
      h("span.dm-i", icon(g, 22)),
      h("span.dm-w", h("span.dm-t", t), s ? h("span.dm-s", s) : null),
      key ? h("kbd.dm-k", key) : null);
    return h("div.dm-row", btn, note);
  }

  function items() {
    const out = [];
    const st = tour ? tour.state : "idle";
    if (st === "paused" && tour.index >= 0) {
      const step = tour.steps[tour.index] || {};
      out.push(row({ id: "resume", g: G.resume, t: "Resume tour", primary: true, key: "Space",
                     s: `Step ${tour.index + 1}${step.title ? ", " + step.title : ""}`,
                     run: () => tour.resume() }));
    }
    out.push(row({ id: "start", g: G.play, t: "Start tour", primary: st !== "paused",
                   s: "About six minutes, from the top", run: () => tour.start() }));
    out.push(row({ id: "drowsy", g: G.eye, t: "Drowsy moment", key: "D",
                   s: "Level 1, then Level 2", run: () => cue("drowsy") }));
    drawnSaved = clipSaved();
    out.push(row({ id: "brake", g: G.brake, t: "Hard braking", key: "B",
                   s: drawnSaved ? "The clip is saved" : null, run: () => cue("hard_brake") }));
    const parked = cues ? cues.parked() : false;
    out.push(row({ id: "park", g: parked ? G.drive : G.park, t: parked ? "Drive" : "Park", key: "P",
                   s: parked ? "The car pulls away" : "The car pulls over and stops",
                   run: () => cue(parked ? "drive" : "park") }));
    out.push(row({ id: "restart", g: G.restart, t: "Restart the drive",
                   s: "From Marina, with Home and Work as they were", run: () => restart() }));
    out.push(row({ id: "video", g: G.film, t: "Play backup video", keepOpen: true,
                   s: "The recorded tour, full screen",
                   run: () => ["Run ", h("code", BACKUP_TEXT), " on the tablet."] }));
    out.push(row({ id: "exit", g: G.exit, t: "Exit demo", keepOpen: true,
                   s: "The live app is underneath",
                   run: () => ["Run ", h("code", "omacar-demo off")] }));
    return out;
  }

  // Only a camera that is recording has a clip to save. A question that throws is "no".
  function clipSaved() {
    try { return saved() === true; } catch { return false; }
  }

  function cue(name) {
    if (cues) cues.send(name).catch(() => {});
  }

  function paint() {
    if (!sheet) return;
    sheet.querySelector(".dm-list").replaceChildren(...items());
  }

  function open() {
    if (sheet) return;
    if (tour && tour.hold) tour.hold();
    if (tour && tour.state === "running") tour.pause();
    const card = h("div.dm-card", { role: "dialog", "aria-modal": "true", "aria-label": "Demo menu" },
      h("div.dm-h",
        h("div", h("div.dm-title", "OmaCar demo"), h("div.dm-sub", "Presenter's menu")),
        h("button.dm-close", { type: "button", "aria-label": "Close", onclick: () => close() }, icon(G.x, 20))),
      h("div.dm-list"));
    sheet = h("div.dm-sheet", { onclick: (e) => { if (e.target === sheet) close(); } }, card);
    paint();
    host.appendChild(sheet);
    document.addEventListener("keydown", onKey, true);
    if (tour && tour.subscribe) offTour = tour.subscribe(paint);
    const first = sheet.querySelector(".dm-item");
    if (first && first.focus) first.focus({ preventScroll: true });
    // Look again at the cameras; if the answer is not what the items say, draw them
    // again (and give the focus back to the one that had it).
    if (refresh) {
      const again = () => {
        if (!sheet || clipSaved() === drawnSaved) return;
        const at = document.activeElement && document.activeElement.dataset
          ? document.activeElement.dataset.item : null;
        paint();
        const back = at && sheet.querySelector(`[data-item="${at}"]`);
        if (back && back.focus) back.focus({ preventScroll: true });
      };
      try { Promise.resolve(refresh()).then(again, () => {}); } catch { /* the items as they are */ }
    }
  }

  function close() {
    if (!sheet) return;
    document.removeEventListener("keydown", onKey, true);
    if (offTour) { offTour(); offTour = null; }
    sheet.remove();
    sheet = null;
    if (tour && tour.release) tour.release();
  }

  return {
    open, close,
    toggle() { if (sheet) close(); else open(); },
    isOpen: () => !!sheet,
  };
}
