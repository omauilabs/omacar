// Home, mounted for real, driven through the store the way the live poller
// drives it: a sample is set and "live" is emitted. The server is absent here
// (the runner's static server answers 404 to every /api/ route), which is
// exactly the path home.js takes when the layout cannot be fetched: it falls
// back to the catalogue's default.
import { eq, ok } from "./assert.js";
import home from "../js/views/home.js";
import { store } from "../js/core.js";

const LOOK = "omacar.look";
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

function sample(values, connected = true) {
  store.live = { connected, values, supported: Object.keys(values) };
  store.emit("live");
}
const PARKED = () => sample({ SPEED: 0, RPM: 0 });
const IDLING = () => sample({ SPEED: 0, RPM: 800 });
const DRIVING = () => sample({ SPEED: 50, RPM: 2200 });
const DROPPED = () => sample({}, false);

function ensureHosts() {
  for (const id of ["toasts", "modal-host"]) {
    if (!document.getElementById(id)) {
      const d = document.createElement("div");
      d.id = id;
      document.body.appendChild(d);
    }
  }
}

// Mount Home with a look chosen, wait for its cards, run fn, always clean up.
async function withHome(look, before, fn) {
  ensureHosts();
  let was = null;
  try { was = localStorage.getItem(LOOK); localStorage.setItem(LOOK, look); } catch { /* none */ }
  const root = document.createElement("div");
  document.body.appendChild(root);
  if (before) before();
  const unmount = home(root);
  try {
    for (let i = 0; i < 40 && !root.querySelector(".home-grid > *"); i++) await wait(50);
    await fn(root);
  } finally {
    unmount();
    root.remove();
    store.live = null;
    try { if (was === null) localStorage.removeItem(LOOK); else localStorage.setItem(LOOK, was); } catch { /* none */ }
  }
}

// The look's background is a canvas the effect mounts inside .home-fx.
const running = (root) => !!root.querySelector(".home-fx canvas");

export default [
  ["the look's background runs only while parked, never on a dropped adapter or at idle", () =>
    withHome("green", PARKED, async (root) => {
      ok(running(root), "parked: Matrix runs behind Home");
      DRIVING();
      ok(!running(root), "driving: it stops");
      DROPPED();
      ok(!running(root), "the adapter dropped while moving: it must not start again");
      IDLING();
      ok(!running(root), "idling at a light: not the affirmative parked state");
      PARKED();
      ok(running(root), "parked again: it comes back");
      DROPPED();
      ok(!running(root), "an adapter that drops while parked is not known to be parked any more");
    })],

  ["a look changed while moving does not start the background", () =>
    withHome("green", DRIVING, async (root) => {
      ok(!running(root), "mounted while moving: off");
      try { localStorage.setItem(LOOK, "aurora"); } catch { /* none */ }
      document.dispatchEvent(new CustomEvent("omacar:look", { detail: "aurora" }));
      ok(!running(root), "still off after the look changed");
      PARKED();
      ok(running(root), "and the new look runs once parked");
    })],

  ["mounted with no car at all, nothing animates", () =>
    withHome("green", () => { store.live = null; }, async (root) => {
      ok(!running(root), "no sample, no background");
    })],

  // ---- the dial when the adapter drops ------------------------------------
  ["a dropped adapter is 'No data', never 'Car off', and Begin is offered once stopped", () =>
    withHome("normal", PARKED, async (root) => {
      const rpm = root.querySelector(".dial-rpm"), begin = root.querySelector(".dial-begin");
      DROPPED();
      eq(rpm.textContent, "No data", "the dial's words");
      eq(begin.hidden, false, "Begin keeps its space: never display:none");
      eq(begin.style.visibility, "", "last seen stopped, so Begin is offered");
    })],

  ["but not while the car was last seen moving, and it never shifts the dial", () =>
    withHome("normal", DRIVING, async (root) => {
      const begin = root.querySelector(".dial-begin");
      const gauge = root.querySelector(".hc-dial .g-svg");
      const top = gauge.getBoundingClientRect().top;
      DROPPED();
      eq(root.querySelector(".dial-rpm").textContent, "No data", "the dial's words");
      eq(begin.style.visibility, "hidden", "no Begin while last seen moving");
      eq(begin.hidden, false, "and its space is still reserved");
      eq(gauge.getBoundingClientRect().top, top, "the gauge did not move");
    })],

  ["a Home opened after the drop still knows the car was moving", async () => {
    DRIVING();
    await withHome("normal", DROPPED, async (root) => {
      eq(root.querySelector(".dial-begin").style.visibility, "hidden", "no Begin on a fresh Home");
    });
    PARKED(); store.live = null;
  }],

  ["leaving Home stops it", async () => {
    let kept = null;
    await withHome("green", PARKED, async (root) => { kept = root; ok(running(root), "running"); });
    eq(running(kept), false, "the canvas was removed on unmount");
  }],
];
