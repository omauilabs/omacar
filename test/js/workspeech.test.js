import { eq, ok } from "./assert.js";
import { createSpeech, REPLAY_S, repeatQuestion, offlineOnce } from "../js/work/speech.js";
import { silenceStop, SILENCE_S } from "../js/work/recorder.js";

function rig() {
  const log = []; let t = 0; let alert = false; let playing = null;
  const s = createSpeech({
    play: (item, done) => { playing = { item, done }; log.push("play " + item.id); return () => { log.push("cut " + item.id); }; },
    alertBusy: () => alert, now: () => t,
  });
  return { s, log, set t(v) { t = v; }, get t() { return t; }, alert: (v) => { alert = v; }, finish: () => { const p = playing; playing = null; p.done(); } };
}

export default [
  ["one line plays", () => { const r = rig(); r.s.say({ id: 1, at: 0, clip: "1.wav" }); r.s.tick(); eq(r.log, ["play 1"]); }],
  ["two never overlap", () => { const r = rig(); r.s.say({ id: 1, at: 0 }); r.s.say({ id: 2, at: 0 }); r.s.tick(); r.s.tick(); eq(r.log, ["play 1"]); r.finish(); r.s.tick(); eq(r.log, ["play 1", "play 2"]); }],
  ["an alert cuts speech off at once", () => { const r = rig(); r.s.say({ id: 1, at: 0 }); r.s.tick(); r.alert(true); r.s.tick(); eq(r.log, ["play 1", "cut 1"]); }],
  ["nothing starts during an alert", () => { const r = rig(); r.alert(true); r.s.say({ id: 1, at: 0 }); r.s.tick(); eq(r.log, []); }],
  ["the cut line replays after the alert", () => { const r = rig(); r.s.say({ id: 1, at: 0 }); r.s.tick(); r.alert(true); r.s.tick(); r.alert(false); r.t = 30; r.s.tick(); eq(r.log, ["play 1", "cut 1", "play 1"]); }],
  ["unless it is over two minutes old", () => { const r = rig(); r.s.say({ id: 1, at: 0 }); r.s.tick(); r.alert(true); r.s.tick(); r.alert(false); r.t = REPLAY_S + 1; r.s.tick(); eq(r.log, ["play 1", "cut 1"]); }],
  ["the same event twice is said once", () => { const r = rig(); r.s.say({ id: 1, at: 0 }); r.s.say({ id: 1, at: 0 }); r.s.tick(); r.finish(); r.s.tick(); eq(r.log, ["play 1"]); }],
  ["a pending question is said again when the car next stops", () =>
    eq([repeatQuestion(40, 0, true, false), repeatQuestion(0, 0, true, false),
        repeatQuestion(40, 0, true, true), repeatQuestion(40, 0, false, false)],
       [true, false, false, false])],
  ["offline is said once per drive", () => eq(
    [offlineOnce({ online: false, said: false, newDrive: false }),
     offlineOnce({ online: false, said: true, newDrive: false }),
     offlineOnce({ online: false, said: true, newDrive: true })],
    [true, false, true])],
  ["silence stop needs sound first", () => { eq(silenceStop(Array(100).fill(0), 0.05), false); }],
  ["then 1.2 s of quiet stops it", () => { const lv = [...Array(10).fill(0.3), ...Array(Math.ceil(SILENCE_S / 0.05)).fill(0)]; eq(silenceStop(lv, 0.05), true); }],
  ["1.1 s of quiet does not", () => { const lv = [...Array(10).fill(0.3), ...Array(22).fill(0)]; eq(silenceStop(lv, 0.05), false); }],
];
