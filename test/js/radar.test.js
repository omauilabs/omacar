import { eq } from "./assert.js";
import { radarView, bannerPhase, bannerHides, fmtFreq, headOf, alertRows, logRows, agoText } from "../js/radar.js";

// What the radar screens say (share/js/radar.js). Where the reading came from
// is never blank, a stale file never shows an alert, and the simulator is
// never mistaken for a detector.
const alert = (over = {}) => Object.assign({ band: "Ka", freq_mhz: 34700, dir: "front", front: 6, rear: 0,
  strength: 6, exact: true, priority: true }, over);
const live = (over = {}) => Object.assign({
  present: true, connected: true, source: "ble", name: "V1connection LE", muted: false, mode: "Logic",
  version: "V4.1037", gen2: true, alerts: [], today: { date: "x", count: 3 }, log: [],
  last_alert_at: 1000, at: 1100, age: 0,
}, over);

export default [
  ["frequencies read in GHz without padding", () =>
    eq([fmtFreq(34700), fmtFreq(24150), fmtFreq(10525), fmtFreq(35000), fmtFreq(0)], ["34.7", "24.15", "10.525", "35.0", ""])],
  ["the head is band and frequency; laser has no frequency", () =>
    eq([headOf(alert()), headOf({ band: "Laser", freq_mhz: null }), headOf({ band: "Photo", photo: "MRCT" })],
       ["Ka 34.7", "Laser", "Photo MRCT"])],
  ["no detector yet says how to find one", () => {
    const v = radarView({ present: false });
    eq([v.state, v.canMute, v.line], ["absent", false, "Pair a detector: omacar v1 scan"]);
  }],
  ["over Bluetooth it says so, in the ok tone", () => {
    const v = radarView(live(), { now: 1130 });
    eq([v.state, v.lamp, v.lampTone, v.todayText, v.lastText, v.canMute], ["idle", "V1 · Bluetooth", "ok", "3 alerts today", "Last alert 2 min ago", true]);
  }],
  ["the simulator always says Simulated", () => {
    const v = radarView(live({ source: "sim" }));
    eq([v.lamp, v.lampTone, v.sim], ["Simulated", "warn", true]);
  }],
  ["a lost link is Not connected, with no alert and no mute", () => {
    const v = radarView(live({ connected: false, alerts: [alert()], age: 400, stale: true }));
    eq([v.state, v.lamp, v.rows.length, v.canMute], ["offline", "Not connected", 0, false]);
  }],
  ["an alert: the priority first, the others counted, tone by band", () => {
    const v = radarView(live({ alerts: [alert(), alert({ band: "K", freq_mhz: 24150, priority: false, strength: 2 }),
                                        alert({ band: "Laser", freq_mhz: null, priority: false })] }));
    eq([v.state, v.pri.head, v.pri.tone, v.others, v.rows[1].tone, v.rows[2].tone, v.pri.dirWord],
       ["alert", "Ka 34.7", "warn", 2, "info", "bad", "Ahead"]);
  }],
  ["strength is clamped to the V1's eight bars", () =>
    eq(alertRows(live({ alerts: [alert({ strength: 99 }), alert({ strength: -3 })] })).map((r) => r.strength), [8, 0])],
  ["a pending mute shows until the detector agrees", () =>
    eq([radarView(live()).muted, radarView(live(), { pending: true }).muted], [false, true])],
  ["the log keeps band, frequency, direction and peak, and nothing positional", () => {
    const r = logRows(live({ log: [{ at: 1, band: "K", freq_mhz: 24150, dir: "front", peak: 4, secs: 8 }] }), () => "9:00");
    eq(r[0], { time: "9:00", head: "K 24.15", band: "K", dir: "front", dirWord: "Ahead", peak: 4, secs: 8, tone: "info" });
  }],
  ["ages", () => eq([agoText(30), agoText(300), agoText(7200)], ["30 s ago", "5 min ago", "2 h ago"])],
  ["the banner shows while active, holds 3 s, then goes", () =>
    eq([bannerPhase(true, null, 10), bannerPhase(false, 10, 12.9), bannerPhase(false, 10, 13), bannerPhase(false, null, 13)],
       ["on", "fading", "off", "off"])],
  ["the banner steps aside where the alert is already the screen", () =>
    eq([bannerHides("#radar", false), bannerHides("#home", true), bannerHides("", true), bannerHides("#home", false), bannerHides("#cameras", true)],
       [true, true, true, false, false])],
];
