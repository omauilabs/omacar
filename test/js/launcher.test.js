// The launcher's Begin runs `omacar begin`, which stops the drive recorder and
// restarts the daemon (lib/begin.py). Nothing on a drive screen leads here any
// more, but the button itself also refuses while the car is moving, or was
// last seen moving, and says why -- the same rule as every write screen.
import { eq } from "./assert.js";
import launcher from "../js/views/launcher.js";
import { store, api } from "../js/core.js";

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// A fresh motion latch and a clock the test can move: store.moving waits a
// minute of stopped-and-connected before it calls a car parked.
const clock = { t: 1e12 };
const realClock = store.clock;
function mount(before) {
  store.clock = () => clock.t;
  store.motionLatched = false;
  store.stillSince = null;
  store.lastMoving = false;
  before();
  const root = document.createElement("div");
  document.body.appendChild(root);
  const un = launcher(root);
  return { root, btn: root.querySelector(".launch-go"),
           done: () => {
             un(); root.remove();
             store.clock = realClock;
             store.live = null;
             store.motionLatched = false;
             store.stillSince = null;
             store.emit("live");
           } };
}
const live = (values, connected = true, extra) => {
  store.live = Object.assign({ connected, values }, extra);
  store.emit("live");
};

export default [
  ["parked, Begin can be pressed", () => {
    const m = mount(() => live({ SPEED: 0, RPM: 0 }));
    try { eq([m.btn.disabled, m.btn.title], [false, ""]); } finally { m.done(); }
  }],
  ["moving, it greys out with the reason", () => {
    const m = mount(() => live({ SPEED: 60, RPM: 2500 }));
    try { eq([m.btn.disabled, m.btn.title], [true, "Available when you stop"]); } finally { m.done(); }
  }],
  ["and stays grey if the adapter drops while moving", () => {
    const m = mount(() => live({ SPEED: 60, RPM: 2500 }));
    try {
      live({}, false);
      eq(m.btn.disabled, true, "disabled after the drop");
      live({ SPEED: 0, RPM: 0 });
      eq(m.btn.disabled, true, "back and stopped is a red light until a minute has passed");
      clock.t += 60000;
      live({ SPEED: 0, RPM: 0 });
      eq(m.btn.disabled, false, "and free again after a minute stopped");
    } finally { m.done(); }
  }],

  ["a 40 s red light keeps it grey", () => {
    const m = mount(() => live({ SPEED: 60, RPM: 2500 }));
    try {
      live({ SPEED: 0, RPM: 800 });
      clock.t += 40000;
      live({ SPEED: 0, RPM: 800 });
      eq([m.btn.disabled, m.btn.title], [true, "Available when you stop"]);
    } finally { m.done(); }
  }],

  ["and locks during a DTC-sweep hand-off, even though the car was last seen stopped", () => {
    const m = mount(() => live({ SPEED: 0, RPM: 0 }));
    try {
      // records.live() marks the daemon's routine hand-off this way: connected
      // reads false, same as a drop, but it is not the car going off.
      live({}, false, { handover: true, status: "yielded" });
      eq([m.btn.disabled, m.btn.title], [true, "Available when you stop"],
         "locked for the hand-off, not just for a real drop while last seen moving");
      live({ SPEED: 0, RPM: 0 });
      eq(m.btn.disabled, true, "still locked just after: the car may have moved during the sweep");
      clock.t += 60000;
      live({ SPEED: 0, RPM: 0 });
      eq(m.btn.disabled, false, "free again once it has sat still and connected for a minute");
    } finally { m.done(); }
  }],

  ["a refused Begin re-applies the lock instead of blindly re-enabling", async () => {
    const wasStart = api.beginStart, wasStatus = api.beginStatus;
    // begin.py refusing a step: rc 1, no `running` key left behind.
    api.beginStart = async () => ({});
    api.beginStatus = async () => ({
      steps: [{ step: "port", ok: false, fatal: true, said: "in use by omacar-drivelog" }],
      rc: 1,
    });
    const m = mount(() => live({ SPEED: 0, RPM: 0 }));
    try {
      m.btn.click();
      // The car pulls away while the refused request is still in flight, on
      // the same "live" channel the real fast poll would deliver it on.
      live({ SPEED: 60, RPM: 2500 });
      for (let i = 0; i < 50 && !m.root.querySelector(".launch-anyway"); i++) await wait(20);
      eq(!!m.root.querySelector(".launch-anyway"), true, "the refusal reached the screen");
      eq([m.btn.disabled, m.btn.title], [true, "Available when you stop"],
         "the lock is re-applied from the car's current state, not blindly re-enabled");
    } finally { api.beginStart = wasStart; api.beginStatus = wasStatus; m.done(); }
  }],
];
