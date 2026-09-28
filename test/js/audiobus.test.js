import { eq, ok } from "./assert.js";
import { MUSIC_DB, ALERT_MAX_DB, dbToGain, gainToDb, headroomDb, musicIn, alertIn } from "../js/audiobus.js";
import { auxLine } from "../js/audiostate.js";

export default [
  ["music sits 12 dB below full scale", () => eq(MUSIC_DB, -12)],
  ["alerts may use all of it", () => eq(ALERT_MAX_DB, 0)],
  ["so an alert always has 12 dB over music", () => eq(headroomDb(), 12)],
  // The real graph, not arithmetic: this builds the page's own AudioContext.
  ["the stage is built that way: the music bus at -12 dB", () => eq(musicIn().gain.value.toFixed(4), "0.2512")],
  ["and the alert gate closed, so nothing plays on it until an alert opens it", () => eq(alertIn().gain.value, 0)],
  ["-12 dB is a quarter of the amplitude", () => eq(dbToGain(-12).toFixed(4), "0.2512")],
  ["gain and dB round-trip", () => ok(Math.abs(gainToDb(dbToGain(-7.5)) + 7.5) < 1e-9, "round trip")],
  ["silence is zero gain, not a tiny number", () => eq([dbToGain(-Infinity), dbToGain(-200)], [0, 0])],
  ["a Level 1 swell leaves 6 dB, a Level 2 duck 24", () => eq([headroomDb(-6), headroomDb(-24)], [6, 24])],
  ["AUX disconnected only when the port is known to be the speakers", () =>
    eq([auxLine({ aux: false }), auxLine({ aux: true }), auxLine({ aux: null }), auxLine(null)],
       ["AUX disconnected — sound is on the tablet's speakers", "", "", ""])],
];
