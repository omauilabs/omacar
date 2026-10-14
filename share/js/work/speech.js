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

export function createSpeech({ play, alertBusy, now }) {
  const queue = [];
  const seen = new Set();
  let current = null;   // { item, cut }
  return {
    say(item) {
      if (seen.has(item.id)) return;
      seen.add(item.id);
      queue.push({ ...item, interrupted: false });
    },
    tick() {
      if (current && alertBusy()) {
        current.cut();
        queue.unshift({ ...current.item, interrupted: true });
        current = null;
        return;
      }
      if (current || alertBusy() || !queue.length) return;
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
