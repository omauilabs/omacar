import { eq, ok } from "./assert.js";
import { alertView, lampView, seatView, mmss, CHIME_EVERY_MS, CHIME_EVERY_HOT_MS } from "../js/childalert.js";
import { meanLuma, reportFrom, DARK_LUMA } from "../js/childwatch.js";
import { tileStatus } from "../js/views/cabin.js";

// What the child alert shows (share/js/childalert.js, views/childalert.js) and
// what its camera watcher reports (share/js/childwatch.js). The rules that
// matter most: the card is there for every running alert, nothing on it is a
// zero for "unknown", it never appears for a car that is on, and the camera
// never reports "empty".

const ALERT = {
  running: true, enabled: true, source: "live", phase: "alert", car: "off", occupied: true,
  why: "a face seen 1 min ago", camera: { role: "rear", state: "dark" },
  alert: { elapsed: 75, off_for: 75, notified: true, notify_in: 0, snoozed_left: null },
  chime: true, heat: null, cabin_sensor: false, config: { enabled: true, chime: true, heat_c: 30, camera: "rear" },
};
const t = (c) => `${Math.round(c)}°`;

export default [
  ["no loop, no card", () => eq(alertView({ running: false, phase: "off" }), null)],
  ["a drive with the seat occupied is not a card", () =>
    eq(alertView({ ...ALERT, phase: "armed", car: "on", alert: null }), null)],
  ["a running alert is the full card, with both keys", () => {
    const v = alertView(ALERT, t);
    eq([v.mode, v.title, v.actions, v.tone], ["card", "Is your child still in the car?", ["have", "remind"], "warn"]);
    eq(v.why, "The car is off. A face seen 1 min ago.");
  }],
  ["it says the phone outbox honestly: queued, delivery not connected", () => {
    const f = alertView(ALERT, t).facts.find((x) => x.k === "Your phone");
    eq(f.v, "Alert queued · phone delivery not connected yet");
    eq(alertView({ ...ALERT, alert: { ...ALERT.alert, notified: false, notify_in: 12 } }, t)
      .facts.find((x) => x.k === "Your phone").v, "Alert queued in 12 s");
  }],
  ["no cabin sensor is said, never drawn as a number", () => {
    const f = alertView(ALERT, t).facts.find((x) => x.k === "Cabin");
    eq([f.v, alertView(ALERT, t).noSensor], ["No temperature sensor", true]);
  }],
  ["a dark camera is named, and the card still shows", () =>
    eq(alertView(ALERT, t).facts.find((x) => x.k === "Rear camera").v, "Too dark to see")],
  ["a hot cabin turns the card red, says why, and chimes twice as often", () => {
    const v = alertView({ ...ALERT, cabin_sensor: true,
      heat: { t_c: 33.4, hot: true, why: "hot and rising", rising: true } }, t);
    eq([v.tone, v.urgent, v.chimeEvery], ["bad", "It is 33° in the car and rising. Check now.", CHIME_EVERY_HOT_MS]);
    eq(v.facts.find((x) => x.k === "Cabin"), { k: "Cabin", v: "33° · hot and rising", tone: "bad" });
  }],
  ["the chime follows the loop's word, every 20 s", () => {
    const v = alertView(ALERT, t);
    eq([v.chime, v.chimeEvery], [true, CHIME_EVERY_MS]);
    eq(alertView({ ...ALERT, chime: false }, t).chime, false);
  }],
  ["snoozed: a countdown above the dock, with I have my child", () => {
    const v = alertView({ ...ALERT, phase: "snoozed", chime: false, alert: { ...ALERT.alert, snoozed_left: 102 } }, t);
    eq([v.mode, v.title, v.actions, v.chime], ["snoozed", "Reminding you in 1:42", ["have"], false]);
  }],
  ["a simulation says so on the card", () => eq(alertView({ ...ALERT, source: "sim" }, t).sim, true)],
  ["mm:ss", () => eq([mmss(0), mmss(75), mmss(600), mmss(null)], ["0:00", "1:15", "10:00", "0:00"])],

  ["the lamp lights only for a drive with the seat occupied", () => {
    eq(lampView({ ...ALERT, phase: "armed", car: "on", alert: null }).text, "Occupied");
    eq(lampView({ ...ALERT, phase: "armed", car: "on" }).tone, "on");
    eq(lampView({ ...ALERT, phase: "idle", car: "on" }), null);
    eq(lampView(ALERT), null);
    eq(lampView({ ...ALERT, phase: "armed", running: false }), null);
  }],
  ["an unreadable charger warns on the lamp", () =>
    eq(lampView({ ...ALERT, phase: "armed", car: "unknown" }).tone, "warn")],

  ["the screen: occupied, clearable only when parked", () => {
    const pub = { ...ALERT, phase: "armed", car: "on" };
    const parked = seatView(pub, { locked: false }), moving = seatView(pub, { locked: true });
    eq([parked.word, parked.canClear, parked.canSet], ["Occupied", true, true]);
    eq([moving.canClear, moving.canSet, moving.canAboard], [false, false, false]);
  }],
  ["nothing seen: Child aboard is one tap, even moving", () => {
    const v = seatView({ ...ALERT, phase: "idle", occupied: false, why: null }, { locked: true });
    eq([v.word, v.canAboard, v.canSet], ["Nothing seen", true, false]);
  }],
  ["no loop: it says nothing is watching, in red", () => {
    const v = seatView({ running: false, phase: "off", enabled: true, config: {} });
    eq([v.word, v.tone, v.source], ["Not watching", "bad", "Not running"]);
  }],
  ["no camera chosen: the line says what arms it instead", () => {
    const v = seatView({ ...ALERT, phase: "idle", occupied: false, config: { enabled: true, camera: null }, camera: { role: null, state: "none" } });
    ok(v.line.includes("Child aboard"), v.line);
    eq(v.camera, { k: "Camera", v: "None chosen", tone: "" });
  }],

  ["the Cabin tile: never reassuring without a loop", () => {
    eq(tileStatus("child", { running: false }).line, "Not watching");
    eq(tileStatus("child", { ...ALERT, phase: "armed" }).line, "Occupied");
    eq(tileStatus("child", ALERT).line, "Alerting now");
    eq(tileStatus("child", { ...ALERT, phase: "idle", occupied: false }).line, "Nothing seen");
  }],

  ["the watcher: luma of a black and a white frame", () => {
    eq(Math.round(meanLuma(new Uint8ClampedArray([0, 0, 0, 255, 0, 0, 0, 255]))), 0);
    eq(Math.round(meanLuma(new Uint8ClampedArray([255, 255, 255, 255]))), 255);
  }],
  ["a dark picture is 'dark', never 'empty', and a face in it still counts", () => {
    eq(reportFrom({ faces: 0, luma: DARK_LUMA - 1, camera: "rear" }), { faces: 0, state: "dark", luma: 27, camera: "rear" });
    eq(reportFrom({ faces: 1, luma: 10 }).faces, 1);
  }],
  ["every failure is its own state", () => {
    eq(reportFrom({ engine: false }).state, "no-engine");
    eq(reportFrom({ picture: false }).state, "no-picture");
    eq(reportFrom({ failed: true, luma: 90 }).state, "error");
    eq(reportFrom({ faces: 2, luma: 90 }).state, "watching");
  }],
];
