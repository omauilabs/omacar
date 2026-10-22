import { eq } from "./assert.js";
import { span, howLong, musicView, cabinView, clock, askView, askAnswer, isStale, STALE_MS, breath, PALETTES } from "../js/rearlogic.js";
import { noteShown, noteText, nowPlaying } from "../js/rearlink.js";

export default [
  ["minutes read as minutes, an hour and more as hours", () => {
    eq(span(1), { big: "1", unit: "minute" });
    eq(span(25), { big: "25", unit: "minutes" });
    eq(span(60), { big: "1 h", unit: "" });
    eq(span(72), { big: "1 h 12", unit: "" });
  }],
  ["How long: navigation's ETA says 'until we get there'", () =>
    eq(howLong({ kind: "eta", minutes: 14 }), { kind: "eta", big: "14", unit: "minutes", line: "until we get there", simulated: false })],
  ["How long: with no navigation it is the time driven, and says so", () => {
    const v = howLong({ kind: "elapsed", minutes: 50 });
    eq(v.line, "we've been driving"); eq(v.big, "50");
  }],
  ["How long: nothing known draws a dash, never a zero", () => {
    for (const hl of [null, undefined, { kind: "unknown" }]) {
      const v = howLong(hl); eq(v.big, "—"); eq(v.unit, "");
    }
  }],
  ["How long: a simulated drive is labelled", () => eq(howLong({ kind: "elapsed", minutes: 5, simulated: true }).simulated, true)],
  ["How long: nearly there", () => eq(howLong({ kind: "eta", minutes: 1 }).big, "Almost")],
  ["music off says so", () => eq(musicView({ state: "off" }, { music: true }).title, "Music is off")],
  ["skip keys need the owner's switch AND a player that can skip", () => {
    const m = { state: "playing", title: "T", artist: "A", can_skip: true };
    eq(musicView(m, { music: true }).skip, true);
    eq(musicView(m, { music: false }).skip, false);
    eq(musicView({ ...m, can_skip: false }, { music: true }).skip, false);
    eq(musicView(m, undefined).skip, false);
  }],
  ["no cabin sensor, no cabin card", () => eq(cabinView(null), null)],
  ["the cabin reads in words", () => {
    eq(cabinView({ degrees: 72, word: "just right" }).line, "It's just right in here");
    eq(cabinView({ degrees: 90, word: "hot" }).tone, "bad");
  }],
  ["a clock", () => { eq(clock(100), "1:40"); eq(clock(5), "0:05"); eq(clock(-3), "0:00"); }],
  ["ask: enabled when nothing holds it", () => eq(askView({ wait: 0, busy: false }, 0, 0).enabled, true)],
  ["ask: held by the server's wait, with the time left", () => {
    const v = askView({ wait: 100, busy: false }, 0, 0);
    eq(v.enabled, false); eq(v.note, "You can ask again in 1:40");
  }],
  ["ask: held by the local deadline between polls", () =>
    eq(askView({ wait: 0, busy: false }, 60000, 0).note, "You can ask again in 1:00")],
  ["ask: busy driver is a kind sentence, not an error", () => {
    const v = askView({ wait: 0, busy: true }, 0, 0);
    eq(v.enabled, false); eq(/busy/.test(v.note), true);
  }],
  ["ask answers by status", () => {
    eq(askAnswer(200, { wait: 120 }).ok, true);
    eq(askAnswer(429, { wait: 50 }).wait, 50);
    eq(askAnswer(409, {}).ok, false);
    eq(/\d{3}/.test(askAnswer(500, null).text), false);
  }],
  ["stale after twelve seconds, and before the first answer", () => {
    eq(isStale(null, 1000), true); eq(isStale(1000, 1000 + STALE_MS), false); eq(isStale(1000, 1001 + STALE_MS), true);
  }],
  ["breathing is smooth, in and out", () => {
    eq(breath(0).scale, 0); eq(breath(4).scale, 1); eq(breath(8).scale, 0);
    eq(breath(1).word, "Breathe in"); eq(breath(5).word, "Breathe out");
    let max = 0; for (let t = 0; t < 8; t += 0.05) max = Math.max(max, Math.abs(breath(t + 0.05).scale - breath(t).scale));
    eq(max < 0.08, true);
  }],
  ["palettes are dim", () => {
    for (const p of PALETTES) for (const c of p.bg) {
      const n = parseInt(c.slice(1), 16); eq(((n >> 16) & 255) < 70 && ((n >> 8) & 255) < 70, true);
    }
  }],
  ["the note: shown with no alert, hidden while one is up", () => {
    const n = { note: { id: 1, label: "a break" } };
    eq(noteShown(n, false), true);
    eq(noteShown({ ...n, alert: true }, false), false);
    eq(noteShown(n, true), false);
    eq(noteShown({ note: null }, false), false);
    eq(noteShown(null, false), false);
  }],
  ["the note says the server's phrase, nothing else", () => {
    eq(noteText({ label: "a break" }), "a break"); eq(noteText(null), "");
  }],
  ["now playing: a live stream cannot skip, so says so", () => {
    eq(nowPlaying({ playing: true, now: { title: "T", artist: "A" } }).can_skip, false);
    eq(nowPlaying({ playing: true, now: {}, skip() {} }).can_skip, true);
    eq(nowPlaying({ playing: true, now: {} }).title, "Omarchy Radio");
    eq(nowPlaying({ playing: false, now: {} }).title, "");
    eq(nowPlaying({ playing: true, now: {} }, true).source, "Simulated");
  }],
];
