// The Work tab: talk to your coding sessions from the car.
// Design: doc/design/2026-10-13-work-voice-relay.md. While moving it shows one
// button and one line per session; pull requests and diffs appear only while
// parked -- unless the owner chose while_moving in work.toml (from /health),
// when they show always, with a one-time-per-drive reading notice.
//
// Speech goes out on the voice bus and always gives way to alerts (see
// work/speech.js). The view polls every 2 s while mounted; main.js polls every
// 5 s from its own clock so lines are heard on other tabs too, and the two
// share one in-flight request. Nothing here shows a transcript ("heard"
// events) at all, so none can leak onto a moving screen.
import { h, clear, store, api } from "../core.js";
import { audioContext, voiceIn, resume, schedule, currentDb, MUSIC_DB } from "../audiobus.js";
import { glidePlan } from "../ramps.js";
import { alertPlayer } from "../alertplayer.js";
import { record } from "../work/recorder.js";
import { createSpeech, repeatQuestion, offlineOnce, catchUp, duckPlan, afterSay, quietHold,
  shownQuestion, speakable, readingNotice, READING_TEXT } from "../work/speech.js";

const POLL_MS = 2000;
const NO_SIGNAL_MS = 5000;
const DUCK_DB = 12;
// Events that end a session's wait: its question is no longer pending.
const RESOLVES = ["pr_opened", "gate_failed", "stopped", "stuck", "finished", "limit", "interrupted"];

let speech = null;
let lastId = 0;
let caughtUp = false;      // the first poll after a load only catches up
let offlineSaid = false;
let wasMoving = false;
let prevSpeed = 0;
// Waiting questions by session id: { event, repeated }. The one shown is the
// session the relay is asking for (`asking`, from /health); see shownQuestion.
let questions = new Map();
let asking;                // undefined until the relay's status is heard
let inflight = null;
let recording = false;
let quietHours = [21, 7];  // from the relay's /health; work.toml's quiet
let notice = "";           // a short note under the button ("Saved, sent when parked.")
let readingShown = false;  // the reading notice has been shown this drive
let readingOpen = false;   // ... and not yet dismissed
const diffs = new Map();   // session id -> { stat, summary } | null, for the diff panel

const alertBusy = () => {
  try { return alertPlayer().voices.some((v) => v.end > audioContext().currentTime); }
  catch { return false; }
};

// Music ducks under speech as it does under alerts: 12 dB further down while
// a line plays, glided, and back after it. Never while an alert sounds (the
// alert player owns the music bus then), and always to an absolute level.
function duck(on) {
  const to = duckPlan(on, alertBusy(), MUSIC_DB, DUCK_DB);
  if (to === null) return;
  try {
    schedule("music", glidePlan(currentDb("music"), to, 0.3), audioContext().currentTime);
  } catch { /* no audio: speech still works without the duck */ }
}

function b64Of(bytes) {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

async function playWav(b64, done) {
  await resume();
  const ctx = audioContext();
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const buf = await ctx.decodeAudioData(bytes.buffer);
  const src = ctx.createBufferSource();
  src.buffer = buf; src.connect(voiceIn());
  duck(true);
  src.onended = () => { duck(false); done(); };
  src.start();
  const cut = () => { try { src.stop(); } catch { /* already ended */ } };
  cut.secs = buf.duration;
  return cut;
}

function getSpeech() {
  if (speech) return speech;
  speech = createSpeech({
    alertBusy,
    busy: () => recording,
    now: () => Date.now() / 1000,
    play: (item, finish) => {
      let cut = () => {};
      let stopped = false;
      // A line that never ends (a stuck decode, a lost onended) must not wedge
      // the queue: free it after its length + 5 s, or 40 s when unknown.
      let timer = setTimeout(() => { stopped = true; cut(); duck(false); done(); }, 40000);
      const done = () => { clearTimeout(timer); finish(); };
      // A clip that cannot be fetched (the offline clips before they are
      // installed, or no signal) is skipped: done() frees the queue.
      const fetchB64 = item.local
        ? fetch(`/assets/work/${item.local}.wav`).then((r) => {
            if (!r.ok) throw new Error(String(r.status));
            return r.arrayBuffer();
          }).then((ab) => b64Of(new Uint8Array(ab)))
        : api.workClip(item.clip).then((r) => r.wav_b64);
      fetchB64.then((b64) => playWav(b64, done))
        .then((c) => {
          cut = c; if (stopped) { c(); return; }
          clearTimeout(timer); timer = setTimeout(() => { stopped = true; c(); duck(false); done(); }, (c.secs + 5) * 1000);
        })
        .catch(() => { duck(false); done(); });
      return () => { stopped = true; clearTimeout(timer); cut(); };
    },
  });
  setInterval(() => speech.tick(), 200);
  return speech;
}

// Two rising notes, 0.15 s in all, on the voice bus.
function tone() {
  const ctx = audioContext(); const t = ctx.currentTime;
  [[523.25, 0], [783.99, 0.08]].forEach(([hz, at]) => {
    const o = ctx.createOscillator(); const g = ctx.createGain();
    o.frequency.value = hz;
    g.gain.setValueAtTime(0.3, t + at); g.gain.linearRampToValueAtTime(0, t + at + 0.07);
    o.connect(g); g.connect(voiceIn()); o.start(t + at); o.stop(t + at + 0.08);
  });
}

// Lines from the relay play unless quiet hours hold them (parked only).
const held = () => quietHold(new Date().getHours(), quietHours, store.moving);
function noteStatus(st) {
  if (!st) return;
  if (Array.isArray(st.quiet)) quietHours = st.quiet;
  if ("asking" in st) asking = st.asking;
  // A question whose session no longer waits (answered, ended) is dropped.
  if (Array.isArray(st.sessions)) {
    const waiting = new Set(st.sessions.filter((x) => x.state === "question").map((x) => x.id));
    for (const sid of [...questions.keys()]) if (!waiting.has(sid)) questions.delete(sid);
  }
}

function currentQuestion() {
  const e = shownQuestion([...questions.values()].map((q) => q.event), asking);
  return e ? questions.get(e.session) : null;
}

const noSignal = () => getSpeech().say({ id: "no_signal-" + Date.now(), at: Date.now() / 1000, local: "no_signal" });

async function talk(setBusy) {
  if (recording) return;
  recording = true; setBusy(true);
  // Whatever is being said stops now and plays again after; nothing new starts
  // while the driver records (speech's busy gate).
  getSpeech().interrupt();
  try {
    await resume();
    if (!alertBusy()) tone();
    duck(true);
    let blob;
    try { blob = await record({ ctx: audioContext() }); } finally { duck(false); }
    if (!blob.size) throw new Error("nothing recorded");
    const b64 = b64Of(new Uint8Array(await blob.arrayBuffer()));
    const sent = api.workSay({ client_id: crypto.randomUUID(), audio_b64: b64, mime: blob.type });
    const reply = await Promise.race([
      sent.catch(() => null),
      new Promise((r) => setTimeout(() => r(null), NO_SIGNAL_MS)),
    ]);
    const next = afterSay(reply);
    notice = next === "held" ? "Saved, sent when parked." : "";
    if (next === "no_signal") noSignal();
  } catch { noSignal(); }   // a denied or failed microphone is told, not silent
  finally { recording = false; setBusy(false); }
}

// For tests: the page's polling state is module-wide, so each mount starts clean.
export function resetWork() {
  lastId = 0; caughtUp = false; offlineSaid = false; wasMoving = false; prevSpeed = 0;
  questions = new Map(); asking = undefined; inflight = null; notice = ""; quietHours = [21, 7];
  readingShown = false; readingOpen = false; diffs.clear();
}

// One poll of the relay's events, shared by the view and main.js's clock.
export function pollWork() {
  if (inflight) return inflight;
  inflight = (async () => {
    const r = await api.workEvents(lastId).catch(() => null);
    if (!r) return null;
    const newDrive = store.moving && !wasMoving;
    wasMoving = store.moving;
    if (newDrive) { readingShown = false; readingOpen = false; }
    if (offlineOnce({ online: r.online, said: offlineSaid, newDrive })) {
      offlineSaid = true;
      getSpeech().say({ id: "offline-" + Date.now(), at: Date.now() / 1000, local: "offline" });
    }
    if (!caughtUp) {
      // After a load, only catch up: speak nothing from the retained backlog,
      // but keep a question that is still waiting (shown, said at the next stop).
      caughtUp = true;
      const st = await api.workStatus().catch(() => null);
      noteStatus(st);
      const c = catchUp(r.events, st && st.sessions);
      lastId = Math.max(lastId, c.maxId);
      for (const e of c.questions) questions.set(e.session, { event: e, repeated: false });
      prevSpeed = ((store.live || {}).values || {}).SPEED || 0;
      return r;
    }
    const asked = [];     // questions are spoken only once we know which one is the relay's
    for (const e of r.events || []) {
      lastId = Math.max(lastId, e.id);
      if (e.kind === "diff") diffs.delete(e.session);       // read again: the panel refetches
      if (e.kind === "question") questions.set(e.session, { event: e, repeated: false });
      else if (RESOLVES.includes(e.kind)) questions.delete(e.session);
      if (e.kind === "question") asked.push(e);
      else if (e.clip && !held()) getSpeech().say({ id: e.id, at: e.at, clip: e.clip, line: e.line });
    }
    // With a question waiting, ask the relay which one it is answering, so the
    // repeat below is the right one even when the tab is not open.
    if (questions.size) noteStatus(await api.workStatus().catch(() => null));
    for (const e of asked) {
      if (e.clip && !held() && speakable(e, asking)) getSpeech().say({ id: e.id, at: e.at, clip: e.clip, line: e.line });
    }
    const question = currentQuestion();
    const speed = ((store.live || {}).values || {}).SPEED || 0;
    if (question && question.event.clip && !held() && repeatQuestion(prevSpeed, speed, true, question.repeated)) {
      question.repeated = true;
      const e = question.event;
      getSpeech().say({ id: `${e.id}-again-${Date.now()}`, at: Date.now() / 1000, clip: e.clip, line: e.line });
    }
    prevSpeed = speed;
    return r;
  })().finally(() => { inflight = null; });
  return inflight;
}

export default function workView(root) {
  let alive = true;
  const list = h("div.work-list");
  const buttons = [];
  // Sized inline too: a thumb target in a moving car must not depend on a
  // stylesheet having loaded.
  const talkBtn = () => {
    const b = h("button.work-talk", {
      type: "button", style: { minWidth: "180px", minHeight: "180px" },
      onclick: () => talk((busy) => buttons.forEach((x) => x.classList.toggle("busy", busy))),
    }, "Talk to Work");
    buttons.push(b);
    return b;
  };
  const btn = talkBtn();
  // The pending question repeats the button, so the answer is one tap away.
  const qtext = h("div.work-question-text");
  const qcard = h("div.work-question", { hidden: true }, qtext, talkBtn());
  const note = h("div.work-note");
  const readingText = h("span", READING_TEXT);
  const reading = h("div.work-reading", { hidden: true }, readingText,
    h("button", { type: "button", "aria-label": "Dismiss", onclick: () => { readingOpen = false; reading.hidden = true; } }, "\u2715"));
  clear(root);
  root.append(h("div.work", reading, btn, note, qcard, list));

  let status = null;       // the last sessions answer, kept to redraw on a stop or start
  let drawnMoving = null;

  function draw() {
    const question = currentQuestion();
    qcard.hidden = !question;
    btn.hidden = !!question;
    qtext.textContent = question ? question.event.line : "";
    note.textContent = notice;
    clear(list);
    drawnMoving = store.moving;
    // PRs and diffs: parked, or any time when the owner chose while_moving.
    const whileMoving = !!(status && status.while_moving);
    const showPr = !store.moving || whileMoving;
    let onScreen = false;
    // The relay adds sessions in the order they start and gives no start time,
    // so newest first is that order reversed.
    for (const s of [...((status && status.sessions) || [])].reverse()) {
      const row = h("div.work-row",
        h("span.work-proj", s.project), " · ", s.state, " · ",
        h("span.work-line", s.line || ""));
      if (showPr && s.pr) {
        onScreen = true;
        row.append(s.repo
          // A new window, as documents.js opens a file: one tap must never
          // replace the dashboard (and its drowsy alerts) in a kiosk with no
          // back button. noopener: the opened page gets no handle on this one.
          ? h("a.work-pr", {
            href: `https://github.com/${s.repo}/pull/${s.pr}`, target: "_blank",
            rel: "noopener noreferrer",
          }, `Draft PR #${s.pr}`)
          : h("span.work-pr", `Draft PR #${s.pr}`));
        const d = diffs.get(s.id);
        // The stat lines and the summary; never the patch.
        if (d) row.append(h("div.work-diff", h("pre.work-diff-stat", d.stat), h("div.work-diff-summary", d.summary)));
      }
      list.append(row);
    }
    if (onScreen && readingNotice(store.moving, whileMoving, readingShown)) {
      readingShown = true; readingOpen = true;
    }
    reading.hidden = !readingOpen;
  }

  // The diff panel's content, fetched once per session (and again after a
  // "read me the diff"); no diff yet is null and stays quiet.
  async function fetchDiffs(st) {
    for (const s of (st && st.sessions) || []) {
      if (!s.pr || diffs.has(s.id)) continue;
      diffs.set(s.id, null);
      try { diffs.set(s.id, await api.workDiff(s.id)); } catch { /* none yet */ }
    }
  }

  async function refresh() {
    await pollWork();
    const st = await api.workStatus().catch(() => null);
    if (!alive) return;
    noteStatus(st);
    if (st) status = st;
    if (st && (st.while_moving || !store.moving)) await fetchDiffs(st).catch(() => {});
    if (!alive) return;
    draw();
  }
  const tick = () => { refresh().catch(() => { /* the next poll tries again */ }); };
  tick();
  const timer = setInterval(tick, POLL_MS);
  // The car stopping or starting redraws at once (PRs appear or vanish); other
  // samples do nothing.
  const off = store.on("live", () => { if (status && store.moving !== drawnMoving) draw(); });
  return () => { alive = false; clearInterval(timer); off(); };
}
