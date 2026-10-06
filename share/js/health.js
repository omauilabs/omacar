// Pack health, the small half: the one-line verdict Home's Hybrid pack tile
// shows, and the lazy fetch behind it. The card itself lives in views/ima.js.
//
// This is its own module because readings.js needs it and views/ima.js imports
// readings.js; pulling the whole IMA view into the tile catalogue is a cycle.
import { api } from "./core.js";

// "Health: <verdict>", or null when there is nothing honest to say: the doc
// carries an error (no database, no drives) or has no verdict.
export function healthLine(doc) {
  if (!doc || doc.error || !doc.verdict) return null;
  return "Health: " + doc.verdict;
}

// The tile's copy, refreshed at most every ten minutes: the model only
// changes when a drive ends. A failed fetch leaves the line off, quietly.
const EVERY_MS = 10 * 60 * 1000;
let line = "", at = 0, inflight = false;

export function healthNote() {
  if (!inflight && Date.now() - at > EVERY_MS && typeof api.battery === "function") {
    inflight = true; at = Date.now();
    api.battery().then((d) => { line = healthLine(d) || ""; })
      .catch(() => { line = ""; })
      .finally(() => { inflight = false; });
  }
  return line;
}
