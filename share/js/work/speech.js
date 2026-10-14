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
// id seen and the one thing still worth showing -- the latest question whose
// session is still waiting on it (status state "question").
export function catchUp(events, sessions) {
  const waiting = new Set((sessions || []).filter((s) => s.state === "question").map((s) => s.id));
  let maxId = 0, pending = null;
  for (const e of events || []) {
    maxId = Math.max(maxId, e.id);
    if (e.kind === "question" && waiting.has(e.session)) pending = e;
  }
  return { maxId, pending };
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
