// Cabin → Calls: the phone as a car kit, at rest.
//
// The call itself is drawn over every screen by callsui.js; this screen is for
// the rest of the time: is the phone connected, how loud is a call, which
// microphone, and the recent calls with a way to call back. Calling back is
// the one parked-only thing: it is greyed while the car moves, never hidden,
// and nothing here has a keypad.

import { h, icon, store } from "../core.js";
import { callsFeed, callsView, post, ago } from "../calls.js";
import { PHONE } from "../callsui.js";

const STEP = 10;

export default function callsScreen(root) {
  const lamp = h("span.cl-lamp", { "aria-hidden": "true" });
  const srcT = h("span");
  const src = h("span.cl-src", srcT);
  const state = h("div.cv-state.display-num");
  const line = h("div.cv-line");
  const volv = h("span.cv-volv.display-num");
  const down = h("button.cv-step", { type: "button", "aria-label": "Quieter" }, "−");
  const up = h("button.cv-step", { type: "button", "aria-label": "Louder" }, "+");
  const mic = h("div.cv-fact-v");
  const note = h("div.cv-note");
  const list = h("div.cv-list");
  const node = h("div.cv",
    h("section.cv-hero",
      h("div.cv-head", icon(PHONE, 22), h("h1.cv-name", "Calls"), src),
      state, line,
      h("div.cv-ctl", h("div.cv-vol", down, h("div.cv-volb", h("span.cv-k", "Call volume"), volv), up)),
      h("div.cv-fact", h("span.cv-k", "Your microphone"), mic),
      note),
    h("section.cv-recent", h("div.cv-rh", "Recent calls"), list));
  root.appendChild(node);

  let st = null, at = Date.now(), volumePending = null;
  const bump = (dir) => {
    const v = callsView(st);
    if (v.volume === null) return;
    volumePending = Math.max(0, Math.min(100, (volumePending ?? v.volume) + dir * STEP));
    paint();
    post({ action: "volume", value: volumePending }).then(() => callsFeed.poke()).catch(() => { volumePending = null; paint(); });
  };
  down.onclick = () => bump(-1);
  up.onclick = () => bump(1);

  function paint() {
    const locked = !!store.lockedAsMoving;
    const v = callsView(st, { locked, now: Date.now(), fetchedAt: at });
    if (volumePending !== null && v.volume === volumePending) volumePending = null;
    node.dataset.state = v.state;
    srcT.textContent = v.source || "No phone";
    src.dataset.tone = v.sourceTone;
    const on = ["incoming", "active", "held", "dialing"].includes(v.state);
    state.textContent = { incoming: "Ringing", active: "On a call", held: "On hold", dialing: "Calling", ended: "Call ended" }[v.state] || "No call";
    line.textContent = on ? `${v.who}${v.dur ? " · " + v.dur : ""}` : v.state === "ended" ? `${v.who} · ${v.dur}` : v.line;
    const vol = volumePending ?? v.volume;
    volv.textContent = vol === null ? "—" : `${vol}%`;
    down.disabled = up.disabled = v.volume === null;
    mic.textContent = v.mic || "The tablet's default microphone";
    note.textContent = v.callBackNote;
    list.replaceChildren(...(v.recent.length ? v.recent.map((r) => {
      const call = h("button.cv-back", { type: "button", disabled: !r.canCallBack, "aria-label": `Call back ${r.who}` },
        icon(PHONE, 20), h("span", "Call back"));
      call.onclick = () => { call.disabled = true; post({ action: "dial", id: r.id }).then(() => callsFeed.poke()).catch(() => { /* the bar says nothing; the refusal is the grey key */ }); };
      return h("div.cv-row", { data: { dir: r.dir, tone: r.tone } },
        h("span.cv-dot", { "aria-hidden": "true" }),
        h("div.cv-rid", h("span.cv-rwho", r.who), h("span.cv-rsub", [r.sub, r.outcome, r.dur].filter(Boolean).join(" · "))),
        h("span.cv-rat", ago(Math.max(0, Date.now() / 1000 - r.at))), call);
    }) : [h("div.cv-empty", v.state === "absent"
      ? "Pair the phone as a hands-free car kit: omacar calls pair. Then its calls ring here."
      : "No calls yet.")]));
  }

  const un = callsFeed.subscribe((s, t) => { st = s; at = t; paint(); });
  const tick = setInterval(paint, 1000);
  return () => { un(); clearInterval(tick); };
}
