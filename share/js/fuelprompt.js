// "Fill-up: how much?" -- the one thing the fuel log ever asks unprompted, and
// the calmest way this app has of asking it.
//
// lib/fuel.py notices that the tank jumped while the car was parked and leaves
// a question in the log. This shows it: a card at the corner above the dock,
// with a stepper for the gallons and one for the price, and Skip. No modal, no
// keyboard, NO SOUND (the owner's child sleeps near the tablet at night), and
// nothing on it moves unless it is answered.
//
// WHEN IT IS UP: a question is waiting, the car is not moving
// (store.lockedAsMoving: the same lock Customize uses, so it goes away by
// itself when the car pulls off and comes back at the next stop), and no
// drowsiness alert is using the screen. Skip is the only way to put a
// question down for good; pulling away only puts it off.

import { store } from "./core.js";
import { drowsy } from "./drowsyrun.js";
import { readFuel, fillSheet, shouldAsk } from "./fuelcard.js";

const POLL_MS = 15000;

let dz = { level: 0, banner: false, testing: false };
drowsy.on((s) => { dz = s; });

let st = null, sheet = null, shownId = null, host = null, dismissed = null;

const alerting = () => dz.level > 0 || dz.banner || dz.testing;

function hide() {
  if (host) { host.remove(); host = null; }
  sheet = null; shownId = null;
}

function show(pending) {
  const app = document.getElementById("app");
  if (!app) return;
  hide();
  host = document.createElement("div");
  host.className = "fuel-ask";
  host.setAttribute("role", "region");
  host.setAttribute("aria-label", "Fill-up");
  const done = (next) => { st = next || st; dismissed = pending.id; hide(); window.dispatchEvent(new Event("omacar:fuel-changed")); };
  sheet = fillSheet({ st, pending, onDone: done, onCancel: () => done(null) });
  host.appendChild(sheet.node);
  app.appendChild(host);
  shownId = pending.id;
}

export function settle() {
  const p = shouldAsk(st, { locked: !!store.lockedAsMoving, alert: alerting(), dismissed });
  if (!p) { if (shownId !== null) hide(); return; }
  if (shownId !== p.id) show(p);
  else if (sheet) sheet.paint();
}

async function poll() {
  try { st = await readFuel(); } catch { /* the last answer stands */ }
  settle();
}

setInterval(poll, POLL_MS);
// The lock changes as the car moves; checked often, because it costs nothing.
setInterval(settle, 1000);
window.addEventListener("omacar:fuel-changed", poll);
poll();
