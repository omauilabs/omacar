// The front screen's half of the back-seat screen (lib/rear.py, doc/rear-screen.md).
//
// Three small jobs, none of them a view:
//
//   1. draw the rear seat's note -- "Back seat wants: a break" -- as a soft card
//      over the stage's corner. SILENT (no sound, ever: a child sleeps near this
//      tablet), dismissable with one tap, never modal, and hidden while any
//      alert is up (the server says so; the radar banner is checked here too).
//   2. publish what the radio is playing, so the rear screen can show it.
//   3. apply a skip the rear screen asked for, when the owner has allowed it
//      and the player can skip. The radio is a live stream and cannot, so
//      can_skip is false today and the rear page shows no skip buttons.
//
// Loaded by app.html as its own module (like radarbanner.js); no view knows it.
// Not in a read-only cockpit: that screen is somebody else's tablet.

import { h, api, readOnly } from "./core.js";
import { postJSON } from "./camapi.js";
import { radio } from "./radio.js";

export const POLL_MS = 4000;
export const BEAT_MS = 15000;

// Should the note be on screen right now? Pure, so it can be held to its rule.
//   resp          GET /api/rear
//   bannerUp      the radar banner (or any other alert strip) is showing
export function noteShown(resp, bannerUp) {
  if (!resp || !resp.note) return false;
  if (resp.alert) return false;          // never while an alert is active
  if (bannerUp) return false;
  return true;
}

// The words, from the server's fixed list -- never from anything a child typed.
export function noteText(note) {
  return note && note.label ? String(note.label) : "";
}

// What to publish about the player.
export function nowPlaying(r, simulated = false) {
  const n = r.now || {};
  return {
    playing: !!r.playing,
    title: n.title || (r.playing ? "Omarchy Radio" : ""),
    artist: n.artist || "",
    can_skip: typeof r.skip === "function",
    source: simulated ? "Simulated" : "live",
  };
}

function start() {
  const stage = document.getElementById("stage");
  if (!stage || readOnly) return;

  const what = h("span.rn-what");
  const key = h("button.rn-key", { type: "button", "aria-label": "Dismiss the back seat's note" }, "Got it");
  const node = h("div.rear-note", { role: "status", hidden: true },
    h("span.rn-lamp", { "aria-hidden": "true" }),
    h("div.rn-text", h("span.rn-cap", "Back seat wants"), what), key);
  document.body.appendChild(node);

  let note = null;
  key.addEventListener("click", async () => {
    if (!note) return;
    const id = note.id;
    note = null;
    node.hidden = true;
    try { await postJSON("/api/rear/dismiss", { id }); } catch { /* it lapses on its own */ }
  });

  async function poll() {
    if (document.hidden) return;
    let resp = null;
    try { resp = await api.rear(); } catch { /* offline: leave the note as it is */ return; }
    note = resp.note || null;
    const bannerUp = !!document.querySelector(".rd-banner:not([hidden])");
    const show = noteShown(resp, bannerUp);
    if (show) what.textContent = noteText(note);
    node.hidden = !show;
    const cmd = resp.music_cmd;
    if (cmd && typeof radio.skip === "function") {
      try { radio.skip(cmd.cmd === "previous" ? -1 : 1); } catch { /* best effort */ }
      try { await postJSON("/api/rear/music/done", { id: cmd.id }); } catch { /* it lapses */ }
    }
  }

  async function beat() {
    if (document.hidden) return;
    try { await postJSON("/api/rear/nowplaying", nowPlaying(radio)); } catch { /* not important */ }
  }

  radio.on(beat);
  setInterval(poll, POLL_MS);
  setInterval(beat, BEAT_MS);
  poll();
  beat();
}

start();
