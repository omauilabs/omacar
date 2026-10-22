// The posted speed limit, as the screens see it: one feed, the pure rules
// that turn it into a sign, and the one rule that decides when the speed
// number should warn.
//
// The data is /api/speedlimit (lib/speedlimit.py): an offline OpenStreetMap
// database matched against the position from lib/location.py. Nothing here
// talks to the network except that one local route.
//
// FOUR THINGS THIS NEVER DOES.
//   * Guess. The server sends a limit only when its match is confident; with
//     no limit there is no sign, not a placeholder and not a zero.
//   * Pass a default off as posted. A way with no maxspeed tag may carry a
//     statutory default; the sign draws it hollow and dashed, says "Default",
//     and the over-limit rule never fires on it.
//   * Make a sound, or a modal. The only thing that ever changes in answer to
//     speeding is the speed number's tone. A child sleeps near the tablet.
//   * Hide where it came from. "Simulated" is written under the sign whenever
//     the position is simulated, and the OpenStreetMap credit is always there
//     (ODbL requires it); a tap on the sign spells the licence out.
//
// signView(), OverTracker and speedMph() are pure (test/js/speedlimit.test.js).

import { h, store, withToken, U } from "./core.js";

export const OVER_MARGIN_MPH = 5;       // over by MORE than this...
export const OVER_HOLD_MS = 5000;       // ...for MORE than this
export const KMH_PER_MPH = 1.609344;
const MAX_GAP_MS = 3000;                // updates older than this are a new run, not a continuation

export const CREDIT = "© OpenStreetMap contributors, ODbL";

// What the sign says. `body` is /api/speedlimit.
export function signView(body, { imperial = true } = {}) {
  const b = body || {};
  const sim = b.location_source === "sim";
  const none = { show: false, sim, text: "", unit: "", kind: null, caption: "", title: "" };
  if (!b.present || b.limit === null || b.limit === undefined || b.confidence !== "high") return none;
  const posted = b.source === "posted";
  const n = imperial ? b.limit : Math.round(b.limit * KMH_PER_MPH);
  const kind = posted ? "posted" : "default";
  return {
    show: true, sim, kind, text: String(n), unit: imperial ? "mph" : "km/h",
    caption: sim ? (posted ? "Simulated" : "Simulated · default") : posted ? "Limit" : "Default",
    title: (posted ? "Posted limit" : "Default limit, not posted") + (b.road ? ` on ${b.road}` : "")
      + (sim ? " (simulated position)" : "") + ". " + CREDIT,
  };
}

// The over-limit rule: the speed number warns when the car has been MORE than
// 5 mph over a POSTED limit for MORE than 5 s without a break. A default never
// warns (it is not a posted number), nor does a missing limit or speed. Updates
// that stop for more than 3 s (a screen that was not mounted) start a new run.
export class OverTracker {
  constructor({ margin = OVER_MARGIN_MPH, hold = OVER_HOLD_MS, gap = MAX_GAP_MS } = {}) {
    this.margin = margin; this.hold = hold; this.gap = gap;
    this.since = null; this.at = null;
  }
  reset() { this.since = this.at = null; }
  update(speedMph, limitMph, source, now) {
    const stale = this.at !== null && now - this.at > this.gap;
    this.at = now;
    const over = Number.isFinite(speedMph) && Number.isFinite(limitMph)
      && source === "posted" && speedMph - limitMph > this.margin;
    if (!over || stale) this.since = over ? now : null;
    else if (this.since === null) this.since = now;
    return this.since !== null && now - this.since > this.hold;
  }
}

// The car's speed in mph for the rule above: the OBD speed when it is live,
// else the receiver's own, else nothing. (Never a zero for "unknown".)
export function speedMph({ carKmh, carLive, gpsMph }) {
  if (carLive && Number.isFinite(carKmh)) return carKmh / KMH_PER_MPH;
  return Number.isFinite(gpsMph) ? gpsMph : null;
}

// ---- the feed ---------------------------------------------------------------------------

const subs = new Set();
let last = null, timer = null, inflight = false;
const tracker = new OverTracker();
let overNowFlag = false;

function nextDelay(st) {
  if (!st || !st.present) return 30000;        // no database: ask rarely
  return st.fix ? 1000 : 4000;
}

async function poll() {
  timer = null;
  if (inflight || !subs.size) return;
  inflight = true;
  try {
    const r = await fetch(withToken("/api/speedlimit"), { cache: "no-store" });
    if (r.ok) last = await r.json();
  } catch { /* keep the last answer */ }
  inflight = false;
  overNow();
  for (const f of [...subs]) { try { f(last); } catch (e) { console.warn("speedlimit feed:", e); } }
  if (subs.size) timer = setTimeout(poll, nextDelay(last));
}

export const limitFeed = {
  get last() { return last; },
  subscribe(fn) {
    subs.add(fn);
    if (last) { try { fn(last); } catch { /* the caller's problem */ } }
    if (!timer && !inflight) timer = setTimeout(poll, 0);
    return () => {
      subs.delete(fn);
      if (!subs.size) { if (timer) clearTimeout(timer); timer = null; tracker.reset(); overNowFlag = false; }
    };
  },
};

function currentSpeed() {
  const s = store.shown || {}, v = s.values || {};
  const kmh = Number(v.SPEED);
  return speedMph({ carKmh: kmh, carLive: !!s.connected && v.SPEED !== null && v.SPEED !== undefined,
                    gpsMph: last && last.gps_mph });
}

// How far over the posted limit the car is this instant, in mph; null when
// there is no confident posted limit or no speed. No holding time: the radar
// banner uses it to add "Over the limit" the moment a detector alerts.
export function overBy() {
  const l = last;
  if (!l || !l.present || l.confidence !== "high" || l.source !== "posted") return null;
  const v = currentSpeed();
  return v === null ? null : v - l.limit;
}

// Is the speed number over the limit right now (by the rule)? Cheap; the dial
// calls it on every paint and the feed on every poll.
export function overNow(now = Date.now()) {
  const l = last, ok = l && l.present && l.confidence === "high";
  overNowFlag = tracker.update(ok ? currentSpeed() : null, ok ? l.limit : null, ok ? l.source : null, now);
  return overNowFlag;
}

// ---- the sign -------------------------------------------------------------------------


// A small round sign. Posted: a solid ring around the number. Default: a
// dashed, hollow ring and the word "Default", so it cannot be mistaken for
// something painted on a pole. Hidden entirely when there is nothing sure to
// say. A tap shows the credit and the licence for a few seconds.
export function limitSign() {
  const num = h("span.sl-n");
  const unit = h("span.sl-u");
  const disc = h("span.sl-disc", { "aria-hidden": "true" }, num, unit);
  const cap = h("span.sl-cap");
  const credit = h("span.sl-credit", { hidden: true }, "Speed limits © OpenStreetMap contributors, ODbL");
  const el = h("button.sl-sign", { type: "button", hidden: true, "aria-label": "Speed limit" },
    disc, cap, credit);
  let shown = null, creditTimer = null;
  el.addEventListener("click", (e) => {
    e.stopPropagation();             // on Home the card around it is a link; the sign is not
    credit.hidden = false;
    clearTimeout(creditTimer);
    creditTimer = setTimeout(() => { credit.hidden = true; }, 6000);
  });
  return {
    el,
    paint(body) {
      const v = signView(body, { imperial: U.imperial });
      el.hidden = !v.show;
      if (!v.show) { shown = null; credit.hidden = true; return v; }
      const key = [v.text, v.unit, v.kind, v.caption].join("|");
      if (key !== shown) {
        shown = key;
        num.textContent = v.text; unit.textContent = v.unit; cap.textContent = v.caption;
        el.dataset.kind = v.kind;
        el.setAttribute("aria-label", `${v.sim ? "Simulated " : ""}speed limit ${v.text} ${v.unit}${v.kind === "default" ? ", default, not posted" : ""}`);
      }
      el.dataset.sim = v.sim ? "1" : "";
      el.title = v.title;
      return v;
    },
    destroy() { clearTimeout(creditTimer); },
  };
}

// Put a sign into `host` and keep it painted while `host` is on screen. Returns
// {sign, destroy}. The feed is shared, so two signs cost one request a second.
export function mountSign(host) {
  const sign = limitSign();
  host.appendChild(sign.el);
  const off = limitFeed.subscribe((b) => sign.paint(b));
  return { sign, destroy() { off(); sign.destroy(); } };
}
