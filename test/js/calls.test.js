import { eq, ok } from "./assert.js";
import { fmtNumber, callsView, placement, alertsNow, duckWanted, durationText, ago, Z } from "../js/calls.js";

// What the call card, the bar and the Calls screen say (share/js/calls.js).
const call = (over = {}) => Object.assign({ path: "/c1", state: "incoming", dir: "in", number: "+14155550142",
  name: "", who: "+1 (415) 555-0142", answered_at: null, secs: 0 }, over);
const live = (over = {}) => Object.assign({
  present: true, source: "ofono", stale: false, age: 0, phase: "idle", call: null, waiting: null, ended: null,
  muted: false, volume: { speaker: 60, mic: 70 }, mic: "", note: null,
  link: { connected: true, phone: "James's iPhone" }, recent: [],
}, over);
const ring = (over = {}) => live(Object.assign({ phase: "incoming", call: call() }, over));
const talk = (over = {}) => live(Object.assign({ phase: "active", call: call({ state: "active", answered_at: 1, secs: 30 }) }, over));

export default [
  ["durations are m:ss, then h:mm:ss", () => eq([durationText(0), durationText(65), durationText(3725), durationText(-4)], ["0:00", "1:05", "1:02:05", "0:00"])],
  ["ages", () => eq([ago(30), ago(300), ago(7200), ago(200000)], ["30 s ago", "5 min ago", "2 h ago", "2 d ago"])],
  ["numbers group like the server's, and odd ones pass through", () =>
    eq([fmtNumber("+14155550142"), fmtNumber("4155550142"), fmtNumber("+442071838750"), fmtNumber("")], ["+1 (415) 555-0142", "(415) 555-0142", "+442071838750", ""])],
  ["no phone yet says how to get one", () => {
    const v = callsView({ present: false });
    eq([v.state, v.canAnswer, v.canHangup, v.line], ["absent", false, false, "No phone yet: omacar calls pair"]);
  }],
  ["connected and quiet: hands-free, in the ok tone", () => {
    const v = callsView(live());
    eq([v.state, v.source, v.sourceTone, v.line], ["idle", "James's iPhone · hands-free", "ok", "No call"]);
  }],
  ["the simulator always says Simulated, in the warn tone", () => {
    const v = callsView(ring({ source: "sim", link: { connected: true, phone: "iPhone (simulated)" } }));
    eq([v.source, v.sourceTone], ["Simulated", "warn"]);
  }],
  ["a phone that is not connected says so, not 'no call'", () => {
    const v = callsView(live({ link: { connected: false, phone: null } }));
    eq([v.source, v.line], ["Phone not connected", "Phone not connected: omacar calls pair"]);
  }],
  ["a stale file is offline: no call, no keys, how old", () => {
    const v = callsView(ring({ stale: true, age: 300, phase: "idle", call: null }));
    eq([v.state, v.source, v.canAnswer, v.canHangup], ["offline", "Last seen 5 min ago", false, false]);
  }],
  ["incoming: the caller, Answer and Decline", () => {
    const v = callsView(ring());
    eq([v.state, v.who, v.line, v.canAnswer, v.canDecline, v.canHangup], ["incoming", "+1 (415) 555-0142", "Incoming call", true, true, false]);
  }],
  ["a name and a number: the name is big, the number under it", () => {
    const v = callsView(ring({ call: call({ name: "Dana", who: "Dana" }) }));
    eq([v.who, v.sub], ["Dana", "+1 (415) 555-0142"]);
  }],
  ["moving locks nothing a call needs", () => {
    const a = callsView(ring(), { locked: true });
    const b = callsView(talk(), { locked: true });
    eq([a.canAnswer, a.canDecline, b.canHangup, b.canMute, b.canVolume], [true, true, true, true, true]);
  }],
  ["a tap just made disables the key until the phone agrees", () => {
    const v = callsView(ring(), { pending: "answer" });
    eq([v.canAnswer, v.canDecline], [false, false]);
  }],
  ["on a call: duration counts on between polls from the server's seconds", () => {
    const v = callsView(talk(), { now: 12000, fetchedAt: 10000 });
    eq([v.state, v.line, v.dur, v.canHangup, v.canMute], ["active", "On call", "0:32", true, true]);
  }],
  ["mute shows the pending state at once", () =>
    eq([callsView(talk()).muted, callsView(talk(), { pending: "mute" }).muted, callsView(talk({ muted: true }), { pending: "mute" }).muted], [false, true, false])],
  ["dialing and hold say so; dialing has no mute yet", () => {
    const d = callsView(live({ phase: "dialing", call: call({ state: "dialing", dir: "out" }) }));
    const hld = callsView(live({ phase: "held", call: call({ state: "held", answered_at: 1, secs: 5 }) }));
    eq([d.line, d.canMute, d.canHangup, hld.line], ["Calling", false, true, "On hold"]);
  }],
  ["a call-waiting during a call offers a second Answer", () => {
    const v = callsView(talk({ waiting: { who: "Sam", number: "9" } }));
    eq([v.waiting.who, v.canAnswer, v.canDecline], ["Sam", true, true]);
  }],
  ["ended: the talk time, once", () => {
    const v = callsView(live({ phase: "ended", ended: { name: "", number: "+1415", secs: 125 } }));
    eq([v.state, v.line, v.dur], ["ended", "Call ended", "2:05"]);
  }],
  ["recent calls: missed is flagged, a name wins, a number is under it", () => {
    const v = callsView(live({ recent: [
      { id: 2, number: "5", name: "", dir: "in", outcome: "missed", at: 1, secs: 0 },
      { id: 1, number: "7", name: "Dana", dir: "out", outcome: "outgoing", at: 1, secs: 90 }] }));
    eq([v.recent[0].outcome, v.recent[0].tone, v.recent[1].who, v.recent[1].sub, v.recent[1].dur], ["Missed", "bad", "Dana", "7", "1:30"]);
  }],
  ["call back: parked only, greyed and explained, never hidden", () => {
    const rows = [{ id: 1, number: "7", name: "", dir: "in", outcome: "missed", at: 1, secs: 0 }];
    const parked = callsView(live({ recent: rows }));
    const moving = callsView(live({ recent: rows }), { locked: true });
    eq([parked.recent[0].canCallBack, moving.recent.length, moving.recent[0].canCallBack, moving.callBackNote],
       [true, 1, false, "Call back when you stop"]);
  }],
  ["no call back from a number-less row, or with the phone away, or mid-call", () => {
    const r = (n) => [{ id: 1, number: n, name: "", dir: "in", outcome: "missed", at: 1, secs: 0 }];
    eq([callsView(live({ recent: r("") })).recent[0].canCallBack,
        callsView(live({ recent: r("7"), link: { connected: false } })).recent[0].canCallBack,
        callsView(talk({ recent: r("7") })).recent[0].canCallBack], [false, false, false]);
  }],
  ["music ducks while the call has the feed, not while it rings", () =>
    eq(["incoming", "active", "held", "dialing", "ended", "idle"].map((s) => duckWanted({ state: s })), [false, true, true, true, false, false])],

  // ---- precedence --------------------------------------------------------
  ["no call, no layer", () => eq(placement(callsView(live()), []).show, "none")],
  ["an incoming call is a card in the middle", () => eq(placement(callsView(ring()), []).show, "card")],
  ["a radar alert alone does not move it (the banner is at the top)", () =>
    eq(placement(callsView(ring()), [{ kind: "radar", level: 2 }]).show, "card")],
  ["a child alert shrinks the card to the edge", () => {
    const p = placement(callsView(ring()), [{ kind: "child", level: 1 }]);
    eq([p.show, p.yield], ["compact", true]);
  }],
  ["drowsy mode at any level does too", () =>
    eq([1, 2, 3].map((l) => placement(callsView(ring()), [{ kind: "drowsy", level: l }]).show), ["compact", "compact", "compact"])],
  ["the bar yields the same way, and stays a bar", () => {
    const p = placement(callsView(talk()), [{ kind: "child", level: 1 }]);
    eq([p.show, p.yield], ["bar", true]);
  }],
  ["a second call over a live one uses the card", () =>
    eq(placement(callsView(talk({ waiting: { who: "Sam", number: "9" } })), []).show, "card")],
  ["the call layer is under every safety layer and above the radar banner", () => {
    ok(Z.radar < Z.call && Z.call < Z.drowsyBanner && Z.call < Z.parking && Z.call < Z.drowsyLayer, "z order");
    eq(placement(callsView(ring()), []).z, Z.call);
  }],
  ["alertsNow reads drowsy's layer and anything marked as a child alert", () => {
    const root = document.createElement("div");
    root.innerHTML = '<div class="dz-layer" data-level="2"></div><div data-alert="child"></div><div data-alert="child" hidden></div>';
    eq(alertsNow(root).map((a) => a.kind + a.level), ["drowsy2", "child1"]);
    root.querySelector(".dz-layer").hidden = true;
    eq(alertsNow(root).map((a) => a.kind), ["child"]);
  }],
];
