// The back-seat screen's words and rules, with no DOM (share/rear.html,
// share/js/rear.js, lib/rear.py, doc/rear-screen.md). Pure, so test/js can hold it.
//
// The rule that shapes all of it: a child's screen says only what is true, in
// words a child can read, and never draws a number it does not have.

// 25 -> {big: "25", unit: "min"}; 72 -> {big: "1 h 12", unit: ""}; 120 -> {big: "2 h", unit: ""}
export function span(minutes) {
  const m = Math.max(0, Math.round(Number(minutes) || 0));
  if (m < 60) return { big: String(m), unit: m === 1 ? "minute" : "minutes" };
  const h = Math.floor(m / 60), r = m % 60;
  return { big: r ? `${h} h ${r}` : `${h} h`, unit: "" };
}

// The "How long?" card. `hl` is /api/rear/state's how_long.
//   eta      "N minutes to go"      (navigation said so, a moment ago)
//   elapsed  "We've been driving"   (no navigation: the time so far, never a guess)
//   unknown  nothing drawn but a dash and a plain sentence
export function howLong(hl) {
  if (!hl || hl.kind === "unknown") {
    return { kind: "unknown", big: "—", unit: "", line: "Waiting for the car to get going", simulated: false };
  }
  const s = span(hl.minutes);
  const sim = !!hl.simulated;
  if (hl.kind === "eta") {
    if (hl.minutes <= 1) return { kind: "eta", big: "Almost", unit: "there!", line: "We're nearly there", simulated: sim };
    return { kind: "eta", big: s.big, unit: s.unit, line: "until we get there", simulated: sim };
  }
  if (hl.minutes < 1) return { kind: "elapsed", big: "Just", unit: "started", line: "We've only just set off", simulated: sim };
  return { kind: "elapsed", big: s.big, unit: s.unit, line: "we've been driving", simulated: sim };
}

// The music card. Skip keys only when the owner allowed them AND the player can.
export function musicView(m, control) {
  if (!m || m.state === "off") return { state: "off", title: "Music is off", artist: "", skip: false, simulated: false };
  return {
    state: m.state,
    title: m.title || "Music",
    artist: m.artist || "",
    skip: !!(control && control.music && m.can_skip),
    simulated: !!m.simulated,
  };
}

export function cabinView(c) {
  if (!c) return null;
  return {
    big: `${c.degrees}°`,
    line: c.word === "just right" ? "It's just right in here" : `It's ${c.word} in here`,
    tone: c.word === "hot" ? "bad" : c.word === "warm" ? "warn" : c.word === "chilly" ? "info" : "ok",
    simulated: !!c.simulated,
  };
}

export function clock(sec) {
  const s = Math.max(0, Math.ceil(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// The ask button. `ask` is the state's ask; `until` a local deadline (ms) set
// after a successful send so the button counts down between polls.
export function askView(ask, until, now) {
  const left = Math.max(ask ? ask.wait : 0, until ? Math.ceil((until - now) / 1000) : 0);
  if (ask && ask.busy) return { enabled: false, label: "Ask the driver", note: "The driver is busy right now. Try again in a little bit." };
  if (left > 0) return { enabled: false, label: "Ask the driver", note: `You can ask again in ${clock(left)}` };
  return { enabled: true, label: "Ask the driver", note: "Tap to tell the driver what you need" };
}

// What a failed ask says, from the status code. Kind, and never an error code.
export function askAnswer(status, body) {
  if (status === 200) return { ok: true, text: "Sent! The driver will see it.", wait: (body && body.wait) || 120 };
  if (status === 429) return { ok: false, text: "You already asked. Please wait a little.", wait: (body && body.wait) || 0 };
  if (status === 409) return { ok: false, text: "The driver is busy right now. Try again in a little bit.", wait: 0 };
  return { ok: false, text: "That didn't go through. Try again in a moment.", wait: 0 };
}

// Has the car been out of reach for too long to trust what is on screen?
export const STALE_MS = 12000;
export const isStale = (lastOkMs, now) => lastOkMs === null || now - lastOkMs > STALE_MS;

// The calm scene's breathing: 4 s in, 4 s out. Returns {word, scale 0..1}.
export function breath(tSec) {
  const p = (tSec % 8) / 8;
  const inhale = p < 0.5;
  const e = inhale ? p * 2 : (1 - p) * 2;
  const k = e * e * (3 - 2 * e);          // smoothstep: no sudden moves
  return { word: inhale ? "Breathe in" : "Breathe out", scale: k };
}

// Three palettes, all dim: this screen is looked at in a dark back seat.
export const PALETTES = [
  { name: "Ocean", bg: ["#0b2433", "#102f4a"], orbs: ["#5ec8e5", "#7aa2f7", "#9be0d0"] },
  { name: "Sunset", bg: ["#2a1630", "#3b1f2e"], orbs: ["#f3a683", "#e77f9b", "#f7d794"] },
  { name: "Forest", bg: ["#0f2418", "#16301f"], orbs: ["#9bde68", "#6fcf97", "#d5e8a0"] },
];
