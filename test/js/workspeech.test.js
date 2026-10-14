import { eq, ok } from "./assert.js";
import { createSpeech, REPLAY_S, repeatQuestion, offlineOnce, catchUp, duckPlan, afterSay, quietHold, shownQuestion, speakable, readingNotice, READING_TEXT } from "../js/work/speech.js";
import { silenceStop, SILENCE_S, record } from "../js/work/recorder.js";

function rig() {
  const log = []; let t = 0; let alert = false; let playing = null; let busy = false;
  const s = createSpeech({
    play: (item, done) => { playing = { item, done }; log.push("play " + item.id); return () => { log.push("cut " + item.id); }; },
    alertBusy: () => alert, busy: () => busy, now: () => t,
  });
  return { s, log, set t(v) { t = v; }, get t() { return t; }, alert: (v) => { alert = v; }, busy: (v) => { busy = v; }, finish: () => { const p = playing; playing = null; p.done(); } };
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
  ["catch-up: three old clips are not queued, and the ids are skipped", () => {
    const ev = [1, 2, 3].map((id) => ({ id, at: 0, kind: "finished", session: "s1", clip: id + ".wav" }));
    const c = catchUp(ev, [{ id: "s1", state: "done" }]);
    eq([c.maxId, c.pending], [3, null]);
  }],
  ["catch-up keeps a question only while its session still waits", () => {
    const q = { id: 4, at: 0, kind: "question", session: "s1", clip: "4.wav" };
    eq(catchUp([q], [{ id: "s1", state: "question" }]).pending, q);
    eq(catchUp([q], [{ id: "s1", state: "working" }]).pending, null);
  }],
  ["after catching up, a new event is queued", () => { const r = rig(); r.s.say({ id: 4, at: 0, clip: "4.wav" }); eq(r.s.queue.length, 1); }],
  ["a line older than two minutes is dropped by say()", () => { const r = rig(); r.t = REPLAY_S + 5; r.s.say({ id: 1, at: 0, clip: "1.wav" }); eq(r.s.queue.length, 0); r.s.say({ id: 2, at: 0, local: "offline" }); eq(r.s.queue.length, 1); }],
  ["music: no write while an alert sounds, absolute levels otherwise", () => eq(
    [duckPlan(true, true, -12), duckPlan(false, true, -12), duckPlan(true, false, -12), duckPlan(false, false, -12)],
    [null, null, -24, -12])],
  ["speech stays quiet while the driver records, then plays", () => { const r = rig(); r.busy(true); r.s.say({ id: 1, at: 0 }); r.s.tick(); eq(r.log, []); r.busy(false); r.s.tick(); eq(r.log, ["play 1"]); }],
  ["tapping to talk cuts the line, which plays again afterwards", () => { const r = rig(); r.s.say({ id: 1, at: 0 }); r.s.tick(); r.busy(true); r.s.interrupt(); r.s.tick(); eq(r.log, ["play 1", "cut 1"]); r.busy(false); r.t = 10; r.s.tick(); eq(r.log, ["play 1", "cut 1", "play 1"]); }],
  ["F2: sent:false plays No signal; sent plays nothing; held (tablet, moving) is silent", () => eq(
    [afterSay({ queued: true, sent: false }), afterSay(null), afterSay({ queued: true, sent: true }),
     afterSay({ queued: true, sent: false, held: true })],
    ["no_signal", "no_signal", null, "held"])],
  ["F7: quiet hours hold lines only while parked", () => eq(
    [quietHold(23, [21, 7], false), quietHold(23, [21, 7], true), quietHold(12, [21, 7], false),
     quietHold(6, [21, 7], false), quietHold(7, [21, 7], false), quietHold(10, [9, 17], false),
     quietHold(23, null, false)],
    [true, false, false, true, false, true, false])],
  ["F3: the question shown is the one the relay is asking", () => {
    const a = { id: 1, session: "s1", line: "A?" }, b = { id: 2, session: "s2", line: "B?" };
    eq([shownQuestion([a, b], "s1"), shownQuestion([b, a], "s2"), shownQuestion([a, b], null),
        shownQuestion([b, a], undefined)], [a, b, null, a]);
  }],
  ["E3: a question event for a session the relay is not asking is not spoken", () => {
    const q = { id: 3, kind: "question", session: "s2" }, fin = { id: 4, kind: "finished", session: "s2" };
    eq([speakable(q, "s1"), speakable(q, "s2"), speakable(q, null), speakable(q, undefined),
        speakable(fin, "s1")], [false, true, false, true, true]);
  }],
  ["C: the reading notice: only moving, only when chosen, once per drive", () => eq(
    [readingNotice(true, true, false), readingNotice(true, true, true), readingNotice(false, true, false),
     readingNotice(true, false, false), readingNotice(true, undefined, false), readingNotice(false, false, false)],
    [true, false, false, false, false, false])],
  ["C: the notice names the law and the guideline and says why it is shown", () => {
    ok(READING_TEXT.includes("\u00a727602") && READING_TEXT.includes("NHTSA") && READING_TEXT.includes("work.toml"), "text");
  }],
  ["a recorder that fails to start still releases the microphone", async () => {
    const stopped = [];
    const tracks = [0, 1].map((i) => ({ stop: () => stopped.push(i) }));
    const stream = { getTracks: () => tracks };
    const ctx = { createMediaStreamSource: () => ({ connect() {}, disconnect() {} }),
                  createAnalyser: () => ({ getFloatTimeDomainData() {} }) };
    let failed = false;
    try {
      await record({ getStream: async () => stream, ctx, makeRecorder: () => ({ start() { throw new Error("no"); } }) });
    } catch { failed = true; }
    ok(failed, "record() rejected");
    eq(stopped.sort(), [0, 1]);
  }],
];
