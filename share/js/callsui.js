// The call on screen, over every screen: an incoming-call card, and a slim bar
// while the call is on. Loaded by app.html as its own module (like
// radarbanner.js), so no view knows it exists. The decisions are in calls.js
// (pure, tested); this only draws them.
//
// MADE FOR A THUMB, WHILE MOVING. Answer and Decline are 64 px keys with the
// caller's name large enough to read at arm's length; Mute and Hang up are on
// the bar the whole call. None of it is gated by the moving lock: hands-free
// is the point. No sound of its own: a child sleeps near the tablet, so the
// call is silent until it is answered, and the call's audio is the phone's,
// carried by PipeWire (lib/calls.py), not played here.
//
// A call never hides a safety alert (calls.js placement()): the card steps
// aside to a compact key pair at the left edge while drowsy mode or a child
// alert is showing, and every call layer sits under theirs.

import { h, icon } from "./core.js";
import { callsFeed, callsView, placement, alertsNow, duckWanted, post } from "./calls.js";

export const PHONE = ["M6.6 3.5h3l1.5 4.1-2 1.4a11.5 11.5 0 0 0 5.9 5.9l1.4-2 4.1 1.5v3a2 2 0 0 1-2.2 2A16.5 16.5 0 0 1 4.6 5.7a2 2 0 0 1 2-2.2z"];
export const HANGUP = ["M3.4 14.2c5.4-4.2 11.8-4.2 17.2 0l-1.6 3.2-3.6-1.3v-2.5a10 10 0 0 0-6.8 0v2.5L5 17.4z"];
export const MIC = ["M12 3.5a3 3 0 0 0-3 3v5a3 3 0 0 0 6 0v-5a3 3 0 0 0-3-3z", "M6.5 11.5a5.5 5.5 0 0 0 11 0", "M12 17v3.5"];
export const MIC_OFF = [...MIC, "M4.5 4.5l15 15"];

function start() {
  const stage = document.getElementById("stage");
  if (!stage) return;

  const lamp = h("span.cl-lamp", { "aria-hidden": "true" });
  const cap = h("span.cl-cap");
  const src = h("span.cl-src");
  const who = h("div.cl-who.display-num");
  const sub = h("div.cl-sub");
  const wait = h("div.cl-wait");
  const answerT = h("span"), declineT = h("span");
  const answer = h("button.cl-key.cl-answer", { type: "button" }, icon(PHONE, 26), answerT);
  const decline = h("button.cl-key.cl-decline", { type: "button" }, icon(HANGUP, 26), declineT);
  const card = h("div.cl-card", { role: "alertdialog", "aria-live": "assertive", "aria-label": "Incoming call", hidden: true },
    h("div.cl-top", lamp, cap, src), who, sub, wait,
    h("div.cl-keys", answer, decline));

  const blamp = h("span.cl-lamp", { "aria-hidden": "true" });
  const bwho = h("span.cl-bwho");
  const bline = h("span.cl-bline");
  const bsrc = h("span.cl-src");
  const bdur = h("span.cl-bdur.display-num");
  const mute = h("button.cl-bkey.cl-mute", { type: "button", "aria-pressed": "false" }, icon(MIC, 22), h("span", "Mute"));
  const hang = h("button.cl-bkey.cl-hang", { type: "button" }, icon(HANGUP, 22), h("span", "Hang up"));
  const bar = h("div.cl-bar", { role: "status", "aria-live": "polite", hidden: true },
    blamp, h("div.cl-bid", bwho, h("span.cl-bmeta", bline, bsrc)), bdur, mute, hang);
  document.body.append(card, bar);

  // Placed over the stage, and re-placed whenever it moves (a segment row
  // appearing under a tab shifts it), like the radar banner.
  function place() {
    const r = stage.getBoundingClientRect();
    const set = (n, v) => { card.style.setProperty(n, v); bar.style.setProperty(n, v); };
    set("--cl-l", `${Math.round(r.left)}px`);
    set("--cl-w", `${Math.round(r.width)}px`);
    set("--cl-t", `${Math.round(r.top)}px`);
    set("--cl-h", `${Math.round(r.height)}px`);
    set("--cl-b", `${Math.round(innerHeight - r.bottom)}px`);
  }
  if (globalThis.ResizeObserver) new ResizeObserver(place).observe(stage);
  addEventListener("resize", place);
  place();

  let st = null, at = Date.now(), pending = null, pendingAt = 0, ducked = false, lastKey = "";

  const act = (name, body, key) => {
    pending = key; pendingAt = Date.now();
    paint();
    post(body).then(() => callsFeed.poke()).catch((e) => {
      pending = null;
      cap.textContent = `Could not ${name}: ${e.message}`;
      paint();
    });
  };
  answer.onclick = () => act("answer", { action: "answer" }, "answer");
  decline.onclick = () => act("decline", { action: "reject" }, "reject");
  hang.onclick = () => act("hang up", { action: "hangup" }, "hangup");
  mute.onclick = () => act("mute", { action: "mute", value: !(st && st.muted) }, "mute");

  function paint() {
    // A tap is settled by the phone's own report, or after five seconds.
    const key = st ? `${st.phase}|${st.muted}|${st.call && st.call.state}|${st.waiting && st.waiting.path}` : "";
    if (pending !== null && (key !== lastKey || Date.now() - pendingAt > 5000)) pending = null;
    lastKey = key;
    const v = callsView(st, { locked: false, pending, now: Date.now(), fetchedAt: at });
    const p = placement(v, alertsNow());

    const wantDuck = duckWanted(v);
    if (wantDuck !== ducked) {
      ducked = wantDuck;
      import("./audiobus.js").then((m) => m.callDuck(wantDuck)).catch(() => { /* no audio stage: nothing to duck */ });
    }

    const sim = st && st.source === "sim";
    const showCard = p.show === "card" || p.show === "compact";
    card.hidden = !showCard;
    bar.hidden = p.show !== "bar";
    document.documentElement.dataset.call = v.state === "idle" || v.state === "absent" || v.state === "offline" ? "" : v.state;
    if (showCard) {
      // A call-waiting beside a live call is shown as its own caller.
      const w = v.state === "incoming" ? v : { who: v.waiting.who, sub: "" };
      card.dataset.mode = p.show;
      card.dataset.sim = sim ? "1" : "";
      cap.textContent = v.state === "incoming" ? "Incoming call" : "Second call";
      src.textContent = v.source;
      src.dataset.tone = v.sourceTone;
      who.textContent = w.who;
      sub.textContent = w.sub || "";
      sub.hidden = !w.sub;
      wait.hidden = true;
      answerT.textContent = pending === "answer" ? "Answering…" : v.state === "incoming" ? "Answer" : "Hold and answer";
      declineT.textContent = pending === "reject" ? "Declining…" : "Decline";
      answer.disabled = !v.canAnswer;
      decline.disabled = !v.canDecline;
    }
    if (p.show === "bar") {
      bar.dataset.yield = p.yield ? "1" : "";
      bar.dataset.state = v.state;
      bar.dataset.sim = sim ? "1" : "";
      bwho.textContent = v.who;
      bline.textContent = v.line;
      bsrc.textContent = sim ? "Simulated" : "";
      bsrc.dataset.tone = "warn";
      bdur.textContent = v.dur;
      const ended = v.state === "ended";
      mute.hidden = hang.hidden = ended;
      mute.disabled = !v.canMute;
      mute.setAttribute("aria-pressed", v.muted ? "true" : "false");
      mute.replaceChild(icon(v.muted ? MIC_OFF : MIC, 22), mute.firstChild);
      mute.lastChild.textContent = v.muted ? "Unmute" : "Mute";
      hang.disabled = !v.canHangup;
    }
  }

  callsFeed.subscribe((s, t) => { st = s; at = t; paint(); });
  // The duration is a clock between polls, and "Call ended" leaves by itself.
  setInterval(() => { if (!bar.hidden || !card.hidden) paint(); }, 500);
  // Drowsy mode's layer and a child alert come and go without telling anyone.
  // Only changes outside the call's own nodes count, or painting would wake itself.
  new MutationObserver((muts) => {
    if (muts.some((m) => !card.contains(m.target) && !bar.contains(m.target))) paint();
  }).observe(document.body, { attributes: true, subtree: true, attributeFilter: ["hidden", "data-level"] });
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
else start();
