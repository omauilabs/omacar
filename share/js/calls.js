// Hands-free calls: what the screens say about the phone (lib/calls.py).
//
// callsView() and placement() are pure (test/js/calls.test.js); the fetches
// at the bottom are the only part that touches the network.
//
// THE RULES THEY HOLD.
//  * A call is never drawn from a stale file: lib/calls.py reads a file older
//    than a few seconds as "no call", and this says "Last seen N ago".
//  * Where it came from is always on the card: "Simulated" for `omacar calls
//    sim`, never blank, so a rehearsal can never be taken for the phone.
//  * Answer, Decline, Mute, Hang up and Volume are one tap and stay available
//    while the car moves (that is what hands-free is for). Calling back is the
//    only thing that is parked-only, and it is greyed, never hidden.
//  * A call never hides a safety alert. Drowsy mode's layers and any element
//    marked data-alert="child" are above the call's layer (CALL_Z), and while
//    one is showing an incoming call shrinks to a compact key pair at the
//    edge instead of a card in the middle (placement()).

import { withToken } from "./core.js";

// The page's stacking, lowest to highest, for everything that can be over the
// stage at once (css/radar.css 80, css/calls.css, css/drowsy.css 250 and 300,
// css/parking.css 260). test/calls_test.py reads the stylesheets and fails if
// these drift from them.
export const Z = { radar: 80, call: 240, drowsyBanner: 250, parking: 260, drowsyLayer: 300 };

export function durationText(s) {
  s = Math.max(0, Math.floor(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  const two = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${two(m)}:${two(r)}` : `${m}:${two(r)}`;
}

export function ago(s) {
  if (s < 90) return `${Math.round(s)} s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 129600) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

// The same grouping lib/calls.py format_number gives a US number; anything else
// is shown exactly as the phone sent it.
export function fmtNumber(n) {
  n = (n || "").trim();
  const d = n.replace(/\D/g, "");
  if (n.startsWith("+1") && d.length === 11) return `+1 (${d.slice(1, 4)}) ${d.slice(4, 7)}-${d.slice(7)}`;
  if (d.length === 10 && !n.startsWith("+")) return `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}`;
  return n;
}

const OUTCOME = {
  missed: ["Missed", "bad"], declined: ["Declined", ""], answered: ["Answered", ""],
  outgoing: ["Outgoing", ""], unanswered: ["No answer", ""],
};

// `st` is /api/calls. `now` and `fetchedAt` (ms) let the duration keep counting
// between polls from the server's own seconds. `pending` is a tap just made
// and not yet confirmed: "answer" | "reject" | "hangup" | "mute".
export function callsView(st, { locked = false, pending = null, now = Date.now(), fetchedAt = now } = {}) {
  const none = { state: "absent", source: "", sourceTone: "", who: "", sub: "", secs: 0, dur: "",
                 muted: false, canAnswer: false, canDecline: false, canHangup: false, canMute: false,
                 canVolume: false, volume: null, waiting: null, ended: null, recent: [], line: "",
                 callBackNote: "", pending };
  if (!st || !st.present) {
    return Object.assign(none, { line: "No phone yet: omacar calls pair" });
  }
  const sim = st.source === "sim";
  const linked = !!(st.link && st.link.connected) && !st.stale;
  const phone = (st.link && st.link.phone) || "Phone";
  const source = sim ? "Simulated" : st.stale ? `Last seen ${ago(st.age || 0)}`
    : linked ? `${phone.replace(/ \(simulated\)$/, "")} · hands-free` : "Phone not connected";
  const sourceTone = sim ? "warn" : linked ? "ok" : "";
  const extra = (now - fetchedAt) / 1000;
  const c = st.call;
  const secs = c && c.answered_at ? (c.secs || 0) + Math.max(0, extra) : 0;
  const parked = !locked;
  const recent = (st.recent || []).slice(0, 12).map((r) => {
    const [word, tone] = OUTCOME[r.outcome] || [r.outcome, ""];
    const who = r.name || fmtNumber(r.number) || "Unknown caller";
    return {
      id: r.id, who, sub: r.name && r.number ? fmtNumber(r.number) : "", outcome: word, tone,
      dir: r.dir, secs: r.secs || 0, dur: r.secs ? durationText(r.secs) : "",
      at: r.at, canCallBack: parked && linked && !!r.number && !c,
    };
  });
  const base = Object.assign(none, {
    state: st.stale ? "offline" : (st.phase || "idle"), source, sourceTone, recent,
    muted: pending === "mute" ? !st.muted : !!st.muted,
    volume: st.volume && st.volume.speaker != null ? st.volume.speaker : null,
    callBackNote: locked ? "Call back when you stop" : "",
    line: st.stale ? "No call information" : !linked ? "Phone not connected: omacar calls pair" : st.note || "No call",
    mic: st.mic || "",
  });
  if (st.stale) return base;
  const waiting = st.waiting ? { who: st.waiting.who, number: st.waiting.number } : null;
  if (st.phase === "incoming" && c) {
    return Object.assign(base, {
      who: c.who, sub: c.name && c.number ? fmtNumber(c.number) : "", line: "Incoming call",
      canAnswer: pending === null, canDecline: pending === null, waiting,
    });
  }
  if (["active", "held", "dialing"].includes(st.phase) && c) {
    const line = st.phase === "held" ? "On hold" : st.phase === "dialing" ? "Calling" : "On call";
    return Object.assign(base, {
      who: c.who, sub: c.name && c.number ? fmtNumber(c.number) : "", line, secs, dur: durationText(secs),
      canHangup: pending !== "hangup", canMute: st.phase !== "dialing", canVolume: base.volume !== null,
      waiting, canAnswer: !!waiting && pending === null, canDecline: !!waiting && pending === null,
    });
  }
  if (st.phase === "ended" && st.ended) {
    const e = st.ended;
    return Object.assign(base, { who: e.name || fmtNumber(e.number) || "Unknown caller", line: "Call ended",
                                 dur: durationText(e.secs), ended: { secs: e.secs } });
  }
  return base;
}

// Called inside a screen's own state (a call card is "over any screen"), the
// music steps aside for every phase in which the call has the AUX feed.
export function duckWanted(view) {
  return ["active", "held", "dialing"].includes(view.state);
}

// ---- precedence ---------------------------------------------------------------------------
// alerts: [{kind: "child" | "drowsy" | "radar", level: n}]. What the call may
// do while they are showing. It never covers one: the card steps aside to a
// compact key pair on the left edge, and everything the call draws sits at CALL_Z,
// under every drowsy and parking layer.
export function placement(view, alerts = []) {
  const incoming = view.state === "incoming" || !!view.waiting;
  const bar = ["active", "held", "dialing", "ended"].includes(view.state);
  if (!incoming && !bar) return { show: "none", yield: false, z: Z.call };
  const child = alerts.some((a) => a.kind === "child");
  const drowsy = alerts.some((a) => a.kind === "drowsy" && a.level >= 1);
  const yielding = child || drowsy;
  return { show: incoming ? (yielding ? "compact" : "card") : "bar", yield: yielding, z: Z.call,
           bar: bar && !incoming };
}

// What is showing now, read from the page: drowsy mode's layer (data-level 1-3,
// css/drowsy.css) and anything marked data-alert="child".
export function alertsNow(root = document) {
  const out = [];
  const layer = root.querySelector(".dz-layer:not([hidden])");
  if (layer) out.push({ kind: "drowsy", level: Number(layer.dataset.level) || 1 });
  for (const el of root.querySelectorAll('[data-alert="child"]:not([hidden])')) out.push({ kind: "child", level: 1, el });
  return out;
}

// ---- the wire -----------------------------------------------------------------------------
export async function readCalls() {
  const r = await fetch(withToken("/api/calls"), { cache: "no-store" });
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
}

export function post(body) {
  return fetch(withToken("/api/calls"), { method: "POST", cache: "no-store", body: JSON.stringify(body) })
    .then(async (r) => { const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.error || String(r.status)); return j; });
}

// One shared feed so the card, the bar and the Calls screen never disagree.
export const callsFeed = (() => {
  const subs = new Set();
  let last = null, at = 0, timer = null, running = false;
  const tick = async () => {
    try { last = await readCalls(); at = Date.now(); } catch { /* keep the last; its age shows */ }
    for (const f of subs) f(last, at);
    const live = last && last.present && last.phase && last.phase !== "idle";
    timer = setTimeout(tick, document.hidden ? 4000 : live ? 600 : 2000);
  };
  return {
    subscribe(fn) {
      subs.add(fn);
      if (last) fn(last, at);
      if (!running) { running = true; tick(); }
      return () => subs.delete(fn);
    },
    poke() { clearTimeout(timer); tick(); },
    get last() { return last; },
    get at() { return at; },
  };
})();
