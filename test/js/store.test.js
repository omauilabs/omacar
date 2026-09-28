// The store when the server goes away AFTER the first paint.
//
// The dead-server check in app_test.py only covers a server that was never
// there. This is the other half: a snapshot has already arrived saying the car
// is connected, and then every request starts failing. The store used to keep
// that snapshot's sample as the current one, so every tile went on saying
// "live" with the last numbers the server ever sent, and the badge said
// LIVE · OBD-II for as long as the server stayed down.
//
// The api object is patched per test and put back afterwards: these are the
// real refreshLive()/refreshCar() methods, failing the way fetch() fails when
// nothing is listening.
import { eq, ok } from "./assert.js";
import { store, api } from "../js/core.js";
import { badge, sourceKey } from "../js/provenance.js";
import { READINGS, readingState } from "../js/readings.js";

const CONNECTED = {
  connected: true, kind: "OBDLink SX", protocol: "ISO 15765-4 (CAN 11/500)",
  supported: ["SPEED", "RPM", "COOLANT_TEMP"],
  values: { SPEED: 42, RPM: 1800, COOLANT_TEMP: 90 },
};
const gone = async () => { throw new TypeError("Failed to fetch"); };

function reset() {
  store.car = null;
  store.live = null;
  store.error = null;
  store.liveError = null;
}

async function withApi(patch, fn) {
  const was = {};
  for (const k of Object.keys(patch)) { was[k] = api[k]; api[k] = patch[k]; }
  try { await fn(); } finally { Object.assign(api, was); reset(); }
}

export default [
  ["the live clock failing ends the last snapshot's claim to be current", () =>
    withApi({ live: gone }, async () => {
      reset();
      store.car = { live: CONNECTED, signals: [] };
      eq(store.connected, true, "before the failure, the snapshot says connected");
      await store.refreshLive();
      eq(store.connected, false, "connected after the server stopped answering");
      eq(store.state, "offline", "state");
      eq(readingState(READINGS.coolant, store.sample), "waiting", "the coolant tile's state");
      eq(readingState(READINGS.speed, store.sample), "waiting", "the dial's state");
      eq(badge(store.car, store.live, store.noServer).text, "NO SERVER", "the badge");
    })],

  ["so does the snapshot clock's, on a screen that does not poll fast", () =>
    withApi({ snapshot: gone }, async () => {
      reset();
      store.car = { live: CONNECTED, signals: [] };
      await store.refreshCar();
      ok(store.car && store.car.live, "the last snapshot is kept for what it records");
      eq(store.connected, false, "connected");
      eq(badge(store.car, store.live, store.noServer).text, "NO SERVER", "the badge");
      eq(sourceKey(store.car, store.sample), "recorded", "a number drawn now would not claim OBD");
    })],

  ["and the moment the server answers again, the sample is current again", () =>
    withApi({ live: gone }, async () => {
      reset();
      store.car = { live: CONNECTED, signals: [] };
      await store.refreshLive();
      eq(store.noServer, true, "down");
      api.live = async () => CONNECTED;
      await store.refreshLive();
      eq(store.noServer, false, "back");
      eq(store.connected, true, "connected");
      eq(badge(store.car, store.live, store.noServer).text, "LIVE · OBD-II", "the badge");
    })],

  ["leaving a live screen does not forget the server is down, until a snapshot says otherwise", () =>
    withApi({ live: gone }, async () => {
      reset();
      store.car = { live: CONNECTED, signals: [] };
      await store.refreshLive();
      eq(store.noServer, true, "down while the fast clock ran");
      store.dropLive();
      eq(store.noServer, true, "still down on a screen with no fast clock");
      eq(store.connected, false, "and the snapshot's old sample is still not current");
      api.snapshot = async () => ({ live: CONNECTED, signals: [] });
      await store.refreshCar();
      eq(store.noServer, false, "the snapshot clock got an answer");
    })],
];
