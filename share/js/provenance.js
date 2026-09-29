// Where a number came from, in words.
//
// The badge in the top bar and the line under Home say this, and they come
// from one function each so the two can never disagree. The failure that
// matters is reading invented numbers as your own car's, so the simulator and
// the bench are named before anything else.

import { handingOver } from "./core.js";
import { PAUSED, PAUSED_WHY } from "./readings.js";

// serverError is store.noServer: true whenever the server has stopped
// answering, including long after the first snapshot. A snapshot still in
// hand is not a reason to say anything else -- it is exactly what goes stale.
// pausedSince is store.pausedSince, which a hand-off with no `t` needs to be
// judged by (core.js handingOver()).
export function badge(car, live, serverError, pausedSince) {
  if (serverError) {
    return { text: "NO SERVER", tone: "bad",
             title: "cannot reach the OmaCar server — omacar server status" };
  }
  if (!car) return { text: "STARTING", tone: "", title: "Waiting for the first snapshot" };
  const s = live || car.live || {};
  if (car.simulated) {
    return { text: "SIMULATED", tone: "warn",
             title: "Simulated by omacar-sim. None of these numbers are your car's." };
  }
  if (s.kind === "bench") {
    return { text: "BENCH", tone: "warn",
             title: "A real adapter path talking to an emulator, not a car." };
  }
  if (s.connected) {
    return { text: "LIVE · OBD-II", tone: "ok",
             title: s.protocol ? `Live from the car · ${s.protocol}` : "Live from the car" };
  }
  // A HAND-OFF IS NOT "NO CAR". The cable is in and the car is answering; the
  // daemon has lent the adapter to a command for a while. This badge is on
  // every screen, and it said "The adapter has not answered" for about two
  // minutes in every four of a drive while the screens under it said
  // "Paused · adapter in use". One word, in badge case: "PAUSED · ADAPTER IN
  // USE" fitted, but in portrait it squeezed the car's name beside it down to
  // "2..", and the car is always named. The reason is in the title and on
  // each screen's own line. Neutral tone, like NO CAR's: it is the car's
  // state, not a fault. A hand-off that has stopped being re-stamped is a
  // stopped daemon and falls through to NO CAR.
  if (handingOver(s, pausedSince)) {
    return { text: PAUSED.toUpperCase(), tone: "",
             title: `${PAUSED}, ${PAUSED_WHY}: the drive recorder, a DTC sweep or a scan has `
                  + "the adapter for the moment. Every reading on screen is the last one taken." };
  }
  return { text: "NO CAR", tone: "",
           title: "The adapter has not answered. Anything shown is the last thing recorded." };
}

export function footerLine(car, live) {
  if (!car) return "";
  const s = live || car.live || {};
  const parts = ["OBD-II"];
  if ((car.signals || []).length) parts.push("Honda enhanced");
  const how = car.simulated ? "simulated"
    : s.kind === "bench" ? "bench emulator"
    : s.connected ? "live" : "last recorded";
  return `Vehicle data: ${parts.join(" + ")} · ${how}`;
}

// The data-src a tile carries when it draws a number. Never empty: a drawn
// number always came from somewhere, and test/app_test.py checks that it says so.
export function sourceKey(car, live) {
  const s = live || (car && car.live) || {};
  if (car && car.simulated) return "sim";
  if (s.kind === "bench") return "bench";
  if (s.connected) return "obd";
  return "recorded";
}
