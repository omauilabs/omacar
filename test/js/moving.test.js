// store.moving: the red-light rule every lock reads (core.js).
//
// Moving while SPEED > 3, during any hand-off of the adapter, and until the
// car has been stopped and connected for a minute without a break. Driven the
// way the live poller drives it -- a sample set, "live" emitted -- on a clock
// the test moves, so a minute costs nothing.
import { eq } from "./assert.js";
import { store, SETTLE_MS } from "../js/core.js";

let t = 0;
function sample(values, connected = true, extra) {
  store.live = Object.assign({ connected, values }, extra);
  store.emit("live");
}
const DRIVE = () => sample({ SPEED: 45, RPM: 2200 });
const STOP = () => sample({ SPEED: 0, RPM: 800 });
const HANDOVER = () => sample({}, false, { handover: true, status: "yielded" });
const DROP = () => sample({}, false);
const at = (secs) => { t = secs * 1000; };

// A fresh store for each case: nothing seen, the test's clock.
function fresh(fn) {
  return () => {
    const was = store.clock;
    store.clock = () => t;
    store.motionLatched = false;
    store.stillSince = null;
    store.lastMoving = false;
    t = 0;
    try { fn(); } finally {
      store.clock = was;
      store.live = null;
      store.motionLatched = false;
      store.stillSince = null;
      store.lastMoving = false;
    }
  };
}

export default [
  ["the settle time is a minute", () => eq(SETTLE_MS, 60000)],

  ["a car never seen moving is parked, and so is no car at all", fresh(() => {
    STOP();
    eq(store.moving, false, "connected and still since the app started");
    DROP();
    eq(store.moving, false, "no adapter, never seen moving");
  })],

  ["a 40 s red light is still driving; a minute stopped is parked", fresh(() => {
    at(0); DRIVE();
    eq(store.moving, true, "driving");
    at(10); STOP();
    at(50); STOP();
    eq(store.moving, true, "40 s at the light");
    at(69); STOP();
    eq(store.moving, true, "59 s");
    at(70); STOP();
    eq(store.moving, false, "a minute stopped and connected");
  })],

  ["creeping forward at the light starts the minute again", fresh(() => {
    at(0); DRIVE();
    at(1); STOP();
    at(41); sample({ SPEED: 6, RPM: 1200 });
    at(42); STOP();
    at(92); STOP();
    eq(store.moving, true, "50 s since the creep, 91 s since the first stop");
    at(102); STOP();
    eq(store.moving, false);
  })],

  ["a hand-off while stopped counts as moving, and for a minute after it", fresh(() => {
    at(0); STOP();
    eq(store.moving, false, "parked");
    at(5); HANDOVER();
    eq(store.moving, true, "motion is unknown while the adapter is lent out");
    at(20); STOP();
    eq(store.moving, true, "the sweep has ended, but the car may have moved during it");
    at(79); STOP();
    eq(store.moving, true);
    at(80); STOP();
    eq(store.moving, false, "a minute connected and still after the hand-off");
  })],

  ["an adapter that drops out mid-drive keeps it moving until it is back and still", fresh(() => {
    at(0); DRIVE();
    at(1); DROP();
    at(600); DROP();
    eq(store.moving, true, "ten minutes with no adapter: nothing says it stopped");
    at(601); STOP();
    at(660); STOP();
    eq(store.moving, true, "59 s back and still");
    at(661); STOP();
    eq(store.moving, false);
  })],

  ["a drop-out during the minute does not count towards it", fresh(() => {
    at(0); DRIVE();
    at(1); STOP();
    at(40); DROP();
    at(50); STOP();
    at(100); STOP();
    eq(store.moving, true, "only 50 s connected since the drop");
    at(110); STOP();
    eq(store.moving, false);
  })],
];
