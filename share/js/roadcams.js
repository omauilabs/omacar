// Road cameras: the rules the screen follows, apart from the screen, so they
// can be tested on their own and so the driving rule is one line to change.

// ---------------------------------------------------------- the driving rule
//
// WHAT A MOVING CAR SHOWS, IN ONE PLACE. The owner's default until they
// answer; their answer is a change to this one line:
//
//   "one"   one small still: the first pinned Caltrans camera, with its age,
//           in a fixed tile with nothing to scroll
//   "none"  no picture at all, only the reason and the greyed controls
//   "all"   no rule: the parked screen, grid and construction cameras too
//
// Whatever it says, browsing, editing the pins and the construction cameras'
// frames are parked-only unless it says "all", and they grey out with the
// reason rather than vanish.
//
// WHETHER THE CAR IS MOVING is not decided here: it is store.moving in
// core.js, the one rule Begin uses too -- a stop at a red light, and a
// hand-off of the adapter, still count as moving until the car has sat
// connected and still for a minute.
export const WHILE_MOVING = "one";

// "one", "none" or "all" for the car as it is now.
export function layoutFor(moving, rule = WHILE_MOVING) {
  return moving ? rule : "all";
}

export const WHY_PARKED = "Available when you stop";

// ------------------------------------------------------------------ the age
//
// From the picture's own timestamp, and never "live": a road camera still is
// two minutes old when it is good and hours old when it is not, and the only
// honest thing to put under it is which.
export function ageText(secs) {
  if (secs === null || secs === undefined || Number.isNaN(secs)) return "age unknown";
  const s = Math.max(0, Math.floor(secs));
  if (s < 60) return "under a minute ago";
  const m = Math.floor(s / 60);
  if (m < 90) return `${m} min ago`;
  const hr = Math.floor(m / 60);
  if (hr < 48) return `${hr} h ago`;
  return `${Math.floor(hr / 24)} days ago`;
}

// Older than a camera should ever be: three of its own updates, and never less
// than ten minutes, so one late upload does not turn a tile amber.
export function isStale(secs, cam) {
  if (secs === null || secs === undefined) return false;
  const period = Math.max(1, (cam && cam.updated_minutes) || 2) * 60;
  return secs > Math.max(600, 3 * period);
}

// How long to wait before asking for this camera's next still, in ms.
//
// On the camera's own update frequency, and never more than once a minute.
// With the picture's age known, the ask is timed for when the next one should
// be up (plus a margin for Caltrans' upload), not a full period after the
// last ask -- otherwise a two-minute camera read just before its update would
// show a picture nearly four minutes old.
export function nextRefreshMs(cam, ageNow) {
  const period = Math.max(60, ((cam && cam.updated_minutes) || 2) * 60);
  if (ageNow === null || ageNow === undefined) return period * 1000;
  const due = period - ageNow + 15;
  return Math.round(Math.min(period + 15, Math.max(60, due)) * 1000);
}

// ----------------------------------------------------------- the arrangement
//
// The pinned cameras first, in the owner's order, then every group in the
// order the server sent (US-101, SR-1, SR-68, SR-156, SR-183, Imjin Parkway --
// lib/roadcams.py decides it, once). A pin that is not in the list is kept
// and named, not dropped.
export function arrange(listing, pins) {
  const d = listing || {};
  const byId = new Map((d.cameras || []).map((c) => [c.id, c]));
  const missingNames = new Map((d.pins_missing || []).map((m) => [m.id || m.name, m.name]));
  const pinned = [];
  for (const id of pins || d.pins || []) {
    const cam = byId.get(id);
    pinned.push(cam ? { id, cam } : { id, cam: null, name: missingNames.get(id) || id });
  }
  // Default pins the server could not resolve (no list yet) have no id at
  // all; they are still the owner's commute and still said.
  if (!pins) {
    for (const m of d.pins_missing || []) if (!m.id) pinned.push({ id: null, cam: null, name: m.name });
  }
  const groups = (d.groups || []).map((g) => ({
    id: g.id, label: g.label,
    cams: (g.ids || []).map((id) => byId.get(id)).filter(Boolean),
  })).filter((g) => g.cams.length);
  return { pinned, groups };
}

// The camera a moving car gets: the first pinned one Caltrans publishes a
// still for. A construction camera is a frame, which is parked-only.
export function firstPinnedStill(listing, pins) {
  const d = listing || {};
  const byId = new Map((d.cameras || []).map((c) => [c.id, c]));
  for (const id of pins || d.pins || []) {
    const cam = byId.get(id);
    if (cam && cam.kind === "still") return cam;
  }
  return null;
}

// Whether a frame from the internet is worth drawing: the browser thinks it is
// online, and the server's last word about the internet was not a failure.
export function netState(listing) {
  const n = (listing && listing.net) || {};
  if (n.fail_at && (!n.ok_at || n.fail_at > n.ok_at)) return "offline";
  if (n.ok_at) return "online";
  return "unknown";
}

// The line under the title: where the list came from and how old it is.
export function feedLine(listing) {
  const f = (listing && listing.feed) || {};
  const n = f.count || 0;
  const src = f.source || "Caltrans";
  if (!f.fetched_at) {
    return f.error ? `${src}: the camera list has not been downloaded yet. ${f.error}`
                   : `${src}: the camera list has not been downloaded yet.`;
  }
  const what = `${src} · ${n} camera${n === 1 ? "" : "s"} in Monterey County`;
  const age = f.age < 120 ? "list just checked" : `list from ${ageText(f.age)}`;
  if (f.error) return `${what} · ${f.offline ? "No connection" : f.error} · ${age}`;
  return `${what} · ${age}`;
}
