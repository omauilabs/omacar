// Work's speech queue: one line at a time, and alerts always win.
//
// An alert (drowsy or any other) cuts a line off at once. The line goes back
// to the front and plays again when the alert is over -- unless it is more
// than REPLAY_S old by then, because a status from three minutes ago is no
// longer news. Pure scheduling: `play` and `alertBusy` are given, so the
// tests run without an AudioContext. Event `at` is in seconds (the relay's
// clock), so `now` must return seconds too.
export const REPLAY_S = 120;

// A question still waiting is said again the next time the car stops: speed
// fell from over 3 km/h to 3 or less, and it has not been repeated yet.
export function repeatQuestion(prevSpeed, speed, pending, repeated) {
  return pending && !repeated && prevSpeed > 3 && speed <= 3;
}

// "Work is offline" once per drive, not on every poll.
export function offlineOnce({ online, said, newDrive }) {
  return !online && (!said || newDrive);
}

// The first poll after a page load only catches up: the relay keeps a week of
// events, and a reload mid-drive must not speak them. It returns the highest
// id seen and the one thing still worth showing -- the questions whose
// sessions are still waiting on them (status state "question"), the latest
// per session; `pending` is the latest of those.
export function catchUp(events, sessions) {
  const waiting = new Set((sessions || []).filter((s) => s.state === "question").map((s) => s.id));
  let maxId = 0, pending = null;
  const questions = new Map();
  for (const e of events || []) {
    maxId = Math.max(maxId, e.id);
    if (e.kind === "question" && waiting.has(e.session)) { pending = e; questions.set(e.session, e); }
  }
  return { maxId, pending, questions: [...questions.values()] };
}

// Which question the card shows and the car repeats. The relay answers the
// oldest waiting question first and names its session in /health's `asking`;
// showing any other would send the driver's answer to the wrong session.
// `asking` undefined means the relay has not been heard yet: the oldest.
export function shownQuestion(questions, asking) {
  const qs = questions || [];
  if (asking === undefined) return qs.reduce((a, q) => (a && a.id < q.id ? a : q), null);
  return qs.find((q) => q.session === asking) || null;
}

// Whether a polled event is spoken. A question is said only when it is the one
// the relay is asking about now (`asking`, from /health): a question for any
// other session would invite an answer that goes to the wrong one. `asking`
// undefined means the relay has not been heard: the relay only emits a
// question once it reaches the head of its queue, so say it.
export function speakable(event, asking) {
  if (event.kind !== "question") return true;
  return asking === undefined || event.session === asking;
}

// Quiet hours, the page's half: the owner's child sleeps near the parked
// tablet, so while parked between quiet[0] and quiet[1] (from the relay's
// /health, default 21-7) lines are shown, not spoken. While moving nothing is
// held. The box relay cannot know where the car is, so this rule lives here.
export function quietHold(hour, quiet, moving) {
  if (moving || !Array.isArray(quiet) || quiet.length !== 2) return false;
  const [start, end] = quiet;
  const inside = start > end ? (hour >= start || hour < end) : (hour >= start && hour < end);
  return inside;
}

// What the page does after posting a clip. The app's server answers once it
// has tried to forward it: `sent` means the relay has it; `held` means the
// relay is on the tablet and the car is moving, so the clip is saved and goes
// once parked (nothing is spoken while moving then). Anything else -- not
// sent, an error, no answer in time (reply null) -- is "No signal, saved".
export function afterSay(reply) {
  if (reply && reply.sent) return null;
  if (reply && reply.held) return "held";
  return "no_signal";
}

// The music level Work asks for under a line, or null to leave the bus alone.
// An alert owns the music bus while it sounds; and the target is always the
// absolute level, never a captured one, so repeated ducks cannot ratchet.
export function duckPlan(on, alertBusy, musicDb, duckDb = 12) {
  if (alertBusy) return null;
  return on ? musicDb - duckDb : musicDb;
}

// `busy` is anything else that must keep speech quiet (the driver's own
// recording).
export function createSpeech({ play, alertBusy, now, busy = () => false }) {
  const queue = [];
  const seen = new Set();
  let current = null;   // { item, cut }
  return {
    say(item) {
      if (seen.has(item.id)) return;
      seen.add(item.id);
      // Backstop: a line from long ago is no longer news (local clips have no age).
      if (!item.local && now() - item.at > REPLAY_S) return;
      queue.push({ ...item, interrupted: false });
    },
    // Cut the current line (the driver tapped to talk); it plays again later.
    interrupt() {
      if (!current) return;
      current.cut();
      queue.unshift({ ...current.item, interrupted: true });
      current = null;
    },
    tick() {
      if (current && alertBusy()) {
        current.cut();
        queue.unshift({ ...current.item, interrupted: true });
        current = null;
        return;
      }
      if (current || alertBusy() || busy() || !queue.length) return;
      let item = queue.shift();
      while (item && item.interrupted && now() - item.at > REPLAY_S) item = queue.shift();
      if (!item) return;
      const cut = play(item, () => { if (current && current.item === item) current = null; });
      current = { item, cut };
    },
    get queue() { return queue; },
    get current() { return current && current.item; },
  };
}
