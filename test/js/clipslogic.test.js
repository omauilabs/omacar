import { eq } from "./assert.js";
import { triggerInfo, whenParts, peakText, speedLine, bytesText, serviceState, series, valueAt,
         speedTop, readoutKeys, stripPath, partAt, span, needsSeek, clampDt, dtText, toastFor } from "../js/clipslogic.js";

const T = 1_800_000_000;
const inc = (o) => Object.assign({ id: "x-brake", trigger: "brake", t: T, peak_g: 0.62, speed_before: 62.4,
  speed_after: 0, roles: { front: { joined: true, parts: [{ file: "front.mp4", start: T - 32, dur: 62 }] },
  rear: { joined: true, parts: [{ file: "rear.mp4", start: T - 30, dur: 60 }] } } }, o);
const tel = { rows: [{ dt: -2, speed: 60, rpm: 2000 }, { dt: -1, speed: 58, rpm: 1900 }, { dt: 0, speed: 50 },
                     { dt: 1, speed: 30, soc: 61 }, { dt: 2, speed: 0 }] };

export default [
  ["a trigger has its own words", () => eq([triggerInfo({ trigger: "brake" }).word, triggerInfo({ trigger: "manual" }).word,
    triggerInfo({ trigger: "sim" }).word], ["Hard braking", "Saved by hand", "Simulated"])],
  ["an unknown trigger falls back to its label", () => eq(triggerInfo({ trigger: "?", label: "Odd" }).word, "Odd")],
  ["today and yesterday are words", () => {
    const now = new Date(2026, 9, 17, 12, 0).getTime();
    eq([whenParts(new Date(2026, 9, 17, 9, 5, 7).getTime() / 1000, now), whenParts(new Date(2026, 9, 16, 23, 59).getTime() / 1000, now).day],
       [{ day: "Today", time: "09:05", secs: "07" }, "Yesterday"]);
  }],
  ["an older day names itself", () => eq(whenParts(new Date(2026, 9, 12, 14, 2).getTime() / 1000,
    new Date(2026, 9, 17, 12).getTime()).day, "Mon 12 Oct")],
  ["a peak is two decimals and g", () => eq(peakText({ peak_g: 0.6199 }), "0.62 g")],
  ["no peak is nothing, not 0.00 g", () => eq([peakText({ peak_g: null }), peakText({ peak_g: 0 }), peakText({})], [null, null, null])],
  ["before and after read as an arrow", () => eq(speedLine(inc()), "62 → 0 km/h")],
  ["only a before says so", () => eq(speedLine(inc({ speed_after: null })), "62 km/h before")],
  ["neither is nothing", () => eq(speedLine(inc({ speed_after: null, speed_before: null })), null)],
  ["miles per hour convert", () => eq(speedLine(inc({ speed_before: 100, speed_after: 0 }), (k) => k * 0.621371, "mph"), "62 → 0 mph")],
  ["sizes in MB and GB", () => eq([bytesText(0), bytesText(420e6), bytesText(2.5e9)], ["0 MB", "420 MB", "2.5 GB"])],
  ["no server, off, and watching are three states", () => eq([serviceState(null).tone, serviceState({ running: false }).tone,
    serviceState({ running: true, accel: { present: true } }).text], ["bad", "warn", "Watching speed and bumps"])],
  ["no accelerometer is said", () => eq(serviceState({ running: true, accel: { present: false } }).text, "Watching speed · no accelerometer")],
  ["the demo says so", () => eq(serviceState({ demo: true }).text, "Not recorded in the demo")],
  ["a series skips rows that lack the key", () => eq(series(tel, "rpm"), [[-2, 2000], [-1, 1900]])],
  ["the strip is on a fixed time axis, not stretched to its data", () =>
    eq(stripPath([[0, 0], [2, 100]], 600, 100, 0, 100), "M300.0 100.0L320.0 0.0")],
  ["a hole in the readings breaks the line", () =>
    eq(stripPath([[-5, 0], [-4, 0], [4, 0], [5, 0]], 600, 100, 0, 100), "M250.0 100.0L260.0 100.0M340.0 100.0L350.0 100.0")],
  ["fewer than two points draw nothing", () => eq(stripPath([[0, 1]], 600, 100, 0, 100), "")],
  ["a value near a time is found", () => eq(valueAt(tel, "speed", 0.9), 30)],
  ["a value too far away is null, not zero", () => eq(valueAt(tel, "rpm", 1.5, 0.5), null)],
  ["the speed axis tops at the next 20 above the peak", () => eq(speedTop(tel), 80)],
  ["... and grows for a fast car", () => eq(speedTop({ rows: [{ dt: 0, speed: 118 }, { dt: 1, speed: 100 }] }), 120)],
  ["readouts are only the keys with a line", () => eq(readoutKeys(tel), ["speed", "rpm"])],
  ["a time inside a part maps to the video's own time", () => eq(partAt(inc(), "front", -30).at, 2)],
  ["outside the part is nothing", () => eq(partAt(inc(), "rear", 31), null)],
  ["a role that was not recording is nothing", () => eq(partAt(inc(), "cabin", 0), null)],
  ["the span is the footage, clamped to the window", () => eq(span(inc()), [-30, 30])],
  ["an incident with no footage still scrubs the window", () => eq(span({ t: T, roles: {} }), [-30, 30])],
  ["a follower is moved only after drifting", () => eq([needsSeek(10, 10.2), needsSeek(10, 10.5)], [false, true])],
  ["clamping stays inside the span", () => eq([clampDt(-99, [-30, 30]), clampDt(99, [-30, 30])], [-30, 29.95])],
  ["times carry a sign", () => eq([dtText(-12.34), dtText(0), dtText(5)], ["−12.3 s", "0.0 s", "+5.0 s"])],
  ["the first answer sets the baseline and says nothing", () => eq(toastFor(undefined, { id: "a" }, true), { id: "a", say: null })],
  ["a new clip says Clip saved", () => eq(toastFor("a", { id: "b" }, true), { id: "b", say: "Clip saved" })],
  ["the same clip says nothing", () => eq(toastFor("a", { id: "a" }, true).say, null)],
  ["a service that stopped says nothing", () => eq(toastFor("a", { id: "b" }, false).say, null)],
  ["no clips yet, then one: it toasts", () => eq(toastFor(null, { id: "z" }, true).say, "Clip saved")],
];
