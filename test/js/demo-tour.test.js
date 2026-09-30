// The meetup demo's tour (demo/js/tour.js, its steps in demo/data/tour.json),
// the top bar it dresses (demo/js/bar.js), its menu (demo/js/menu.js) and the
// scripted Scan vehicle (demo/js/views/scan.js).
//
// EVERYTHING OUTSIDE THEM IS A FAKE: the clock is a list of timers this file
// moves, the page's location is a string, a cue is a line in a log and so is
// every module action. Nothing here reaches a server, the radio or a speaker.
import { eq, ok } from "./assert.js";
import { createTour, createReset, loadSteps, PAUSED, PAUSED_MS, RESET_WAIT_MS,
         RESYNC_NEAR_S, RESYNC_PAUSE_S, RESYNC_WAIT_MS, CLOSING_SECS } from "../demo/js/tour.js";
import { createBar, LONG_PRESS_MS, LOGO } from "../demo/js/bar.js";
import { createMenu, createCues, BACKUP_TEXT, EXIT_TEXT } from "../demo/js/menu.js";
import { createScan, MODULES, SCAN_SECS, DONE_TEXT } from "../demo/js/views/scan.js";

const tick = () => new Promise((done) => setTimeout(done, 0));

// ---- the rig ---------------------------------------------------------------
function clock() {
  const c = { now: 0, nextId: 1, timers: [] };
  c.later = (fn, ms) => { const id = c.nextId++; c.timers.push({ id, at: c.now + Math.max(0, ms), fn }); return id; };
  c.cancel = (id) => { c.timers = c.timers.filter((t) => t.id !== id); };
  // Move the clock, running every timer that falls due on the way, in order.
  c.advance = (secs) => {
    const end = c.now + secs * 1000;
    for (;;) {
      const due = c.timers.filter((t) => t.at <= end).sort((a, b) => a.at - b.at || a.id - b.id)[0];
      if (!due) break;
      c.timers = c.timers.filter((t) => t !== due);
      c.now = due.at;
      due.fn();
    }
    c.now = end;
  };
  return c;
}

async function rig(over = {}) {
  const steps = over.steps || await loadSteps();
  const c = clock();
  // `world` is the page's store.live.demo: { t, loop_secs }, or null when the page has none.
  const r = { c, steps, log: [], captions: [], notices: [], hash: "#home", resets: 0, menus: 0, parked: false,
              world: null };
  const at = () => c.now / 1000;
  r.tour = createTour({
    steps,
    clock: { later: c.later, cancel: c.cancel, now: () => c.now },
    go: (hash) => { r.hash = hash; r.log.push([at(), "go", hash]); },
    here: () => r.hash,
    cue: (name) => r.log.push([at(), "cue", name]),
    act: (name, arg) => r.log.push([at(), "do", arg === undefined ? name : `${name}(${arg})`]),
    caption: (text) => r.captions.push([at(), text]),
    notice: (text, ms) => r.notices.push([at(), text, ms]),
    reset: () => { r.resets++; r.log.push([at(), "reset"]); return Promise.resolve(); },
    parked: () => r.parked,
    menu: () => { r.menus++; },
    demo: () => r.world,
    ...over.deps,
  });
  r.key = (key, mods = {}) => {
    let prevented = false;
    const used = r.tour.key({ key, ...mods, preventDefault() { prevented = true; } });
    return { used, prevented };
  };
  r.gos = () => r.log.filter((l) => l[1] === "go").map((l) => l[2]);
  r.last = () => r.log[r.log.length - 1];
  return r;
}

const TOTAL = (steps) => steps.reduce((s, x) => s + x.secs, 0);

export default [
  // ---- the steps -------------------------------------------------------------
  ["tour.json: eleven steps, about six minutes, each with a screen and the brief's captions", async () => {
    const steps = await loadSteps();
    eq(steps.map((s) => s.id), ["home", "navigation", "roadcams", "cameras", "drowsy", "vehicle",
                                "agent", "work", "carplay", "androidauto", "end"], "the order");
    eq(steps.map((s) => s.secs), [40, 40, 15, 35, 40, 30, 60, 40, 35, 30, 10], "the durations");
    ok(TOTAL(steps) >= 5.5 * 60 && TOTAL(steps) <= 6.5 * 60, `about six minutes, not ${TOTAL(steps)} s`);
    for (const s of steps) ok(/^#[a-z]/.test(s.go), `step ${s.id} opens a screen`);
    eq(steps[0].caption, "Your car, live: speed, the hybrid pack and every sensor, from the car's own computers.", "step 1");
    eq(steps[4].caption, "Drowsy mode watches the driver's eyes, entirely on the tablet, and wakes you gently.", "step 5");
    eq(steps[9].caption, null, "Android Auto has none");
    eq(steps[10].caption, "OmaCar: open source, on Omarchy.", "the last");
  }],

  ["the steps run in order, with their actions at the right offsets", async () => {
    const r = await rig();
    await r.tour.start();
    eq(r.resets, 1, "a tour from the top starts the demo over first");
    eq(r.tour.state, "running", "running");
    r.c.advance(TOTAL(r.steps) + 5);
    const want = [
      [0, "reset"], [0, "go", "#home"], [25, "do", "radio.play"],
      [40, "go", "#navigation"],
      [80, "go", "#roadcams"],
      [95, "go", "#cameras"], [95, "cue", "drive"], [100, "cue", "hard_brake"],
      [130, "go", "#home"], [130, "cue", "drive"], [133, "cue", "drowsy"],
      [170, "go", "#vehicle"], [185, "go", "#scan"],
      [200, "go", "#advisor"], [200, "cue", "park"], [208, "do", "agent.ask(night)"],
      [230, "do", "agent.apply"], [245, "do", "agent.ask(radio)"],
      [260, "go", "#work"], [260, "cue", "drive"], [284, "do", "work.update"],
      [300, "go", "#carplay"], [312, "do", "projection.open(maps)"], [324, "do", "projection.open(nowplaying)"],
      [335, "go", "#androidauto"], [355, "do", "home.restore"],
      [365, "go", "#home"],
    ];
    eq(r.log, want, "the tour");
    // One caption per step, as it starts, and none once it is over.
    eq(r.captions.map((c) => c[0]), [0, 40, 80, 95, 130, 170, 200, 260, 300, 335, 365, 375], "caption times");
    eq(r.captions[1][1], "Turn-by-turn on the tablet, working offline.", "step 2's");
    eq(r.captions[9][1], null, "Android Auto shows none");
    eq(r.captions[11][1], null, "the end clears it");
  }],

  ["the tour ends back on Home, idle, with nothing left to run", async () => {
    const r = await rig();
    await r.tour.start();
    r.c.advance(TOTAL(r.steps));
    eq(r.hash, "#home", "on Home");
    eq(r.tour.state, "idle", "idle");
    eq(r.c.timers.length, 0, "no timers left");
    const n = r.log.length;
    r.c.advance(600);
    eq(r.log.length, n, "nothing after the end");
  }],

  // ---- pausing -----------------------------------------------------------------
  ["a touch pauses it, says so for 3 s, and Space resumes from the same step", async () => {
    const r = await rig();
    await r.tour.start();
    r.c.advance(50);                       // step 2, Navigation, 10 s in
    r.tour.touch();
    eq(r.tour.state, "paused", "paused");
    eq(r.notices[r.notices.length - 1], [50, PAUSED, PAUSED_MS], "the notice");
    eq(PAUSED, "Tour paused · tap Resume", "its words");
    eq(r.captions[r.captions.length - 1], [50, null], "the caption goes while paused");
    const n = r.log.length;
    r.c.advance(300);
    eq(r.log.length, n, "nothing moves while paused");
    const k = r.key(" ");
    ok(k.used && k.prevented, "Space is the tour's, and does nothing else");
    eq(r.tour.state, "running", "running again");
    eq(r.tour.index, 1, "the same step");
    eq(r.captions[r.captions.length - 1][1], r.steps[1].caption, "its caption back");
    r.c.advance(29.9);
    eq(r.hash, "#navigation", "still Navigation 39.9 s into it");
    r.c.advance(0.1);
    eq(r.hash, "#roadcams", "on to step 3 after its whole 40 s");
  }],

  ["Resume takes the page back to the step's screen if the touch went somewhere else", async () => {
    const r = await rig();
    await r.tour.start();
    r.c.advance(95 + 10);                  // Cameras, after its hard brake
    r.tour.touch();
    r.hash = "#vehicle";                   // the person tapped a tab
    r.tour.resume();
    eq(r.hash, "#cameras", "back to Cameras");
    eq(r.log.filter((l) => l[2] === "hard_brake").length, 1, "the brake is not done twice");
    // A projection's own screen, which the page's address does not show.
    r.c.advance(300 + 15 - 105);           // CarPlay, after Maps
    r.tour.touch();
    r.tour.resume();
    eq(r.hash, "#carplay", "nothing moved: the page stays where it is");
    r.tour.touch();
    r.hash = "#home";
    r.tour.resume();
    eq(r.hash, "#carplay/maps", "moved: back to CarPlay's Maps");
  }],

  // ---- Resume, and the drive that kept going while the tour was paused ---------------------
  //
  // The tour and the drive share a story (Home's speed, Navigation's turn, the
  // Cameras' brake), and the drive does not stop for a presenter: its loop ends
  // in a parked car. Near that end, or after a long talk, Resume starts the
  // drive over and the step from its top.
  ["the drive's loop is 900 s, its closing stop the last 120, and Resume looks 180 s ahead of that", () => {
    eq([CLOSING_SECS, RESYNC_NEAR_S, RESYNC_PAUSE_S, RESYNC_WAIT_MS], [120, 180, 300, 3000], "the numbers");
  }],

  ["Resume within 180 s of the closing stop sends restart, waits for the drive, and re-enters the step from its top", async () => {
    const r = await rig();
    r.world = { t: 100, loop_secs: 900 };
    await r.tour.start();
    r.c.advance(95 + 8);                    // Cameras, 8 s in: the brake at 5 s has gone
    r.tour.touch();
    r.c.advance(20);                        // the presenter talks for 20 s
    r.world = { t: 600, loop_secs: 900 };   // 900 - 120 - 180: exactly the threshold
    r.hash = "#vehicle";
    const before = r.log.length;
    eq(r.tour.resume(), true, "Resume is taken");
    eq(r.log.slice(before), [[123, "cue", "restart"]], "restart is cued, and nothing else yet");
    eq(r.tour.state, "paused", "still waiting for the drive to start over");
    r.c.advance(1);
    eq(r.log.length, before + 1, "and still waiting a second on");
    r.world = { t: 1.2, loop_secs: 900 };   // the world has restarted
    r.c.advance(0.25);
    eq(r.tour.state, "running", "then running");
    eq(r.tour.index, 3, "the same step");
    eq(r.log.slice(before + 1).map((l) => l.slice(1)), [["go", "#cameras"], ["cue", "drive"]], "from its top: its screen and its first cue");
    eq(r.captions[r.captions.length - 1][1], r.steps[3].caption, "its caption back");
    eq(r.tour.elapsed() < 1, true, "and its clock from zero");
    r.c.advance(5);
    eq(r.log.filter((l) => l[2] === "hard_brake").length, 2, "its brake runs again, 5 s in");
    eq(r.resets, 1, "the demo is not reset: Home, Work and the radio stay as they are");
    r.c.advance(30);
    eq(r.hash, "#home", "and the tour goes on to step 5 after its whole 35 s");
    eq(r.tour.index, 4, "step 5");
  }],

  ["Resume at 599.9 s of the loop carries on as it always did, with no restart", async () => {
    const r = await rig();
    await r.tour.start();
    r.c.advance(50);
    r.tour.touch();
    r.world = { t: 599.9, loop_secs: 900 };
    const before = r.log.length;
    r.tour.resume();
    eq(r.tour.state, "running", "running at once");
    eq(r.log.length, before, "no cue, no screen");
    eq(r.tour.elapsed(), 10, "from the same second");
  }],

  ["a pause of more than 5 minutes restarts the drive however early in the loop; 5 minutes exactly does not", async () => {
    const r = await rig();
    await r.tour.start();
    r.c.advance(50);
    r.world = { t: 40, loop_secs: 900 };
    r.tour.touch();
    r.c.advance(RESYNC_PAUSE_S);            // 300 s: not more than
    r.tour.resume();
    ok(!r.log.some((l) => l[2] === "restart"), "5 minutes is not more");
    eq(r.tour.state, "running", "running");
    r.tour.touch();
    r.c.advance(RESYNC_PAUSE_S + 0.5);
    r.tour.resume();
    eq(r.log.filter((l) => l[2] === "restart").length, 1, "300.5 s is");
    r.world = { t: 0.6, loop_secs: 900 };
    r.c.advance(0.25);
    eq([r.tour.state, r.tour.index], ["running", 1], "the same step, running");
    eq(r.last().slice(1), ["go", "#navigation"], "started over");
  }],

  ["a drive that does not answer is waited for 3 s, never more, and the step starts over anyway", async () => {
    const r = await rig();
    r.world = { t: 700, loop_secs: 900 };   // and it never comes back under 30
    await r.tour.start();
    r.c.advance(50);
    r.tour.touch();
    r.tour.resume();
    r.c.advance(RESYNC_WAIT_MS / 1000 - 0.3);
    eq(r.tour.state, "paused", "2.7 s in: waiting");
    r.c.advance(0.4);
    eq([r.tour.state, r.tour.index], ["running", 1], "3 s in: on");
    eq(r.last().slice(1), ["go", "#navigation"], "the step from its top");
    eq(r.c.timers.length, r.steps[1].at.length + 1, "only the step's own timers are left: its actions and its end");
  }],

  ["a page with no drive clock (a screen that polls slowly, the server down) waits out the 3 s after a long pause", async () => {
    const r = await rig();
    await r.tour.start();
    r.c.advance(50);
    r.tour.touch();
    r.c.advance(400);
    r.tour.resume();
    eq(r.log.filter((l) => l[2] === "restart").length, 1, "restart is cued");
    r.c.advance(2.9);
    eq(r.tour.state, "paused", "no number to watch, so the whole wait");
    r.c.advance(0.2);
    eq(r.tour.state, "running", "then on");
  }],

  ["while it waits for the drive a second Resume does nothing, and a jump key wins", async () => {
    const r = await rig();
    r.world = { t: 800, loop_secs: 900 };
    await r.tour.start();
    r.c.advance(50);
    r.tour.touch();
    eq(r.tour.resume(), true, "the first");
    eq(r.tour.resume(), false, "the second is refused");
    eq(r.key(" ").used, true, "Space is the tour's");
    eq(r.log.filter((l) => l[2] === "restart").length, 1, "one restart, not three");
    r.key("3");                             // a jump while waiting
    eq([r.tour.state, r.tour.index, r.hash], ["running", 2, "#roadcams"], "the jump took it");
    r.c.advance(10);
    eq([r.tour.index, r.hash], [2, "#roadcams"], "and the late Resume does not drag it back to step 2");
    r.c.advance(5);
    eq(r.tour.index, 3, "it goes on from step 3");
  }],

  ["stop() while it waits for the drive ends the wait, and nothing starts afterwards", async () => {
    const r = await rig();
    r.world = { t: 800, loop_secs: 900 };
    await r.tour.start();
    r.c.advance(50);
    r.tour.touch();
    r.tour.resume();
    r.tour.stop();
    const n = r.log.length;
    r.c.advance(10);
    eq([r.tour.state, r.log.length, r.c.timers.length], ["idle", n, 0], "idle and silent");
  }],

  ["a key the tour does not own pauses it and is left to the page; modifiers alone do nothing", async () => {
    const r = await rig();
    await r.tour.start();
    r.c.advance(5);
    for (const key of ["Shift", "Control", "Alt", "Meta"]) r.key(key);
    eq(r.tour.state, "running", "a modifier on its own is not a touch");
    eq(r.key("w", { metaKey: true }).used, false, "Super+W is the desktop's");
    eq(r.tour.state, "running", "and does not pause");
    const k = r.key("x");
    eq(k.used, false, "x is the page's");
    eq(r.tour.state, "paused", "and pauses the tour");
  }],

  // ---- the keys ------------------------------------------------------------------
  ["1 jumps to step 1, and 4 to step 4, running from there without starting over", async () => {
    const r = await rig();
    await r.tour.start();
    r.c.advance(140);                      // step 5
    let k = r.key("1");
    ok(k.used, "1 is the tour's");
    eq(r.tour.index, 0, "step 1");
    eq(r.hash, "#home", "Home");
    eq(r.resets, 1, "a jump does not start the demo over");
    eq(r.tour.state, "running", "running");
    k = r.key("4");
    eq([r.tour.index, r.hash], [3, "#cameras"], "step 4");
    r.c.advance(5);
    eq(r.last(), [145, "cue", "hard_brake"], "its actions from its start");
    r.tour.touch();
    r.key("2");
    eq([r.tour.state, r.tour.index, r.hash], ["running", 1, "#navigation"], "a jump runs a paused tour");
  }],

  ["a digit starts an idle tour at that step; Space starts it from the top", async () => {
    const r = await rig();
    r.key("9");
    eq([r.tour.state, r.tour.index, r.hash, r.resets], ["running", 8, "#carplay", 0], "9: CarPlay");
    r.tour.stop();
    eq(r.tour.state, "idle", "stopped");
    r.key(" ");
    await tick();
    eq([r.tour.state, r.tour.index, r.resets], ["running", 0, 1], "Space from idle: the top");
  }],

  ["D, B and P are cues, and Esc is the menu; none of them pauses the tour", async () => {
    const r = await rig();
    await r.tour.start();
    r.c.advance(3);
    eq(r.key("d").used, true, "d");
    eq(r.last()[2], "drowsy", "the drowsy moment");
    r.key("B");
    eq(r.last()[2], "hard_brake", "hard braking");
    r.key("p");
    eq(r.last()[2], "park", "driving: P parks");
    r.parked = true;
    r.key("P");
    eq(r.last()[2], "drive", "parked: P drives");
    r.key("Escape");
    eq(r.menus, 1, "Esc opens the menu");
    eq(r.tour.state, "running", "none of them paused it");
  }],

  // Polish: Esc with the Agent's or Work's overlay open is the overlay's.
  ["Esc with a demo overlay open is left to the overlay, and opens no menu", async () => {
    const r = await rig();
    const ov = document.createElement("div");
    ov.setAttribute("data-demo-overlay", "");
    document.body.appendChild(ov);
    try {
      const k = r.key("Escape");
      eq([k.used, k.prevented, r.menus], [false, false, 0], "not the menu's");
    } finally { ov.remove(); }
    eq(r.key("Escape").used, true, "with none open, Esc is the menu again");
    eq(r.menus, 1, "opened");
  }],
  ["an action that throws is logged and the tour goes on", async () => {
    const r = await rig({ deps: { act: () => { throw new Error("no radio"); } } });
    const warn = console.warn;
    console.warn = () => {};
    try {
      await r.tour.start();
      r.c.advance(45);
      eq(r.hash, "#navigation", "on to step 2");
    } finally { console.warn = warn; }
  }],

  ["a fresh start waits for the demo to be reset, but never more than 3 s", async () => {
    let done;
    const r = await rig({ deps: { reset: () => new Promise((d) => { done = d; }) } });
    const started = r.tour.start();
    eq(r.gos(), [], "not yet");
    r.c.advance(3);
    await started;
    eq(r.gos(), ["#home"], "going anyway after 3 s");
    done();
  }],

  // ---- the reset: tour start and Restart the drive ------------------------------------
  ["the reset pauses the radio, cues restart, and calls resetWork() and restoreHome()", async () => {
    const c = clock();
    const did = [];
    const reset = createReset({
      radio: { pause: () => did.push("radio.pause") },
      cue: (name) => { did.push(`cue:${name}`); return Promise.resolve(); },
      resetWork: () => { did.push("resetWork"); return true; },
      restoreHome: () => { did.push("restoreHome"); return Promise.resolve(); },
      later: c.later, cancel: c.cancel,
    });
    await reset();
    eq(did, ["radio.pause", "cue:restart", "resetWork", "restoreHome"], "all four, in order");
    eq(c.timers.length, 0, "no timer left behind");
  }],
  ["Restart the drive waits for Home at most 3 s, as the tour's start does, and a failure is only logged", async () => {
    const c = clock();
    let homeBack = false;
    const reset = createReset({
      radio: { pause() { throw new Error("no radio"); } },
      cue: () => Promise.reject(new Error("no server")),
      resetWork: () => { throw new Error("no work"); },
      restoreHome: () => new Promise(() => {}),        // a server that never answers
      later: c.later, cancel: c.cancel,
    });
    const warn = console.warn;
    console.warn = () => {};
    try {
      const done = reset().then(() => { homeBack = true; });
      await tick();
      eq(homeBack, false, "waiting for Home");
      c.advance(RESET_WAIT_MS / 1000);
      await done;
      eq(homeBack, true, "and on after 3 s");
      eq(RESET_WAIT_MS, 3000, "3 s");
    } finally { console.warn = warn; }
  }],

  // ---- the menu, opened while the tour is still resetting ----------------------------------
  //
  // A tour from the top resets the demo first (up to 3 s). A presenter who
  // opens the menu in that window has not been paused (there is no step to
  // pause), and step 1 used to start under the open menu, with its caption.
  ["opening the menu while the tour is resetting holds its start until the menu closes", async () => {
    let done;
    const r = await rig({ deps: { reset: () => new Promise((d) => { done = d; }) } });
    const host = document.createElement("div");
    const menu = createMenu({ tour: r.tour, cues: createCues({ post: () => Promise.resolve(), sample: () => null }), host });
    r.tour.start();
    menu.open();
    done();
    await tick();
    eq(r.gos(), [], "the reset is over and step 1 has not started under the menu");
    eq(r.captions, [], "no caption either");
    eq([r.tour.state, r.tour.index], ["running", -1], "the tour is waiting");
    r.c.advance(30);
    eq(r.gos(), [], "however long the menu is open");
    menu.close();
    eq(r.gos(), ["#home"], "the menu closed: step 1");
    eq([r.tour.state, r.tour.index, r.captions.length], ["running", 0, 1], "from its top, with its caption");
    r.c.advance(25);
    eq(r.last().slice(1), ["do", "radio.play"], "its actions on its own clock, from the close");
  }],

  ["a menu opened and closed inside the reset holds nothing, and one open past the 3 s cap holds step 1 too", async () => {
    let done;
    const r = await rig({ deps: { reset: () => new Promise((d) => { done = d; }) } });
    const host = document.createElement("div");
    const menu = createMenu({ tour: r.tour, cues: createCues({ post: () => Promise.resolve(), sample: () => null }), host });
    const started = r.tour.start();
    menu.open();
    menu.close();
    done();
    await started;
    eq(r.gos(), ["#home"], "closed in time: step 1 as ever");
    r.tour.stop();
    const again = r.tour.start();          // a reset that never answers
    menu.open();
    r.c.advance(RESET_WAIT_MS / 1000 + 1);
    await again;
    eq(r.gos(), ["#home"], "open past the cap: still only the first");
    menu.close();
    eq(r.gos(), ["#home", "#home"], "until it closes");
  }],

  ["Start tour from the menu during the wait, or stopping, lets the held start go", async () => {
    const dones = [];
    const r = await rig({ deps: { reset: () => new Promise((d) => { dones.push(d); }) } });
    const host = document.createElement("div");
    const menu = createMenu({ tour: r.tour, cues: createCues({ post: () => Promise.resolve(), sample: () => null }), host });
    r.tour.start();
    menu.open();
    dones[0]();
    await tick();
    [...host.querySelectorAll(".dm-item")].find((b) => b.querySelector(".dm-t").textContent === "Start tour").click();
    eq(menu.isOpen(), false, "the menu closed");
    eq(r.gos(), [], "the held start was dropped, and the new one is resetting");
    dones[1]();
    await tick();
    eq(r.gos(), ["#home"], "one step 1, not two");
    // Stopping (Restart the drive does) drops a held start for good.
    r.tour.stop();
    r.tour.start();
    menu.open();
    dones[2]();
    await tick();
    r.tour.stop();
    menu.close();
    eq([r.tour.state, r.gos().length], ["idle", 1], "nothing starts after a stop");
  }],

  ["menu: opening it holds the tour's start and closing lets go, even with no tour to pause", () => {
    const calls = [];
    const fake = { state: "idle", index: -1, steps: [], hold: () => calls.push("hold"), release: () => calls.push("release"),
                   subscribe() { return () => {}; } };
    const host = document.createElement("div");
    const menu = createMenu({ tour: fake, cues: createCues({ post: () => Promise.resolve(), sample: () => null }), host });
    menu.open();
    menu.open();
    eq(calls, ["hold"], "once, however often it is opened");
    menu.close();
    menu.close();
    eq(calls, ["hold", "release"], "and let go once");
  }],

  // ---- the top bar -----------------------------------------------------------------
  ["bar: the word becomes the logo, a DEMO pill sits before the buttons, and twice is once", () => {
    const vbar = document.createElement("header");
    vbar.innerHTML = '<div class="tb-left"><span class="tb-mark">OmaCar</span><span class="tb-div"></span>'
      + '<span class="tb-page">CR-Z</span></div><div class="tb-clock">9:41</div>'
      + '<div class="tb-right"><span class="tb-car"></span><span class="tb-src warn">SIMULATED</span>'
      + '<button class="vbar-btn" id="btn-daynight"></button><button class="vbar-btn" id="btn-settings"></button></div>';
    const after = createBar({ onMenu() {} });
    after(vbar);
    after(vbar);
    const imgs = vbar.querySelectorAll(".tb-mark img");
    eq(imgs.length, 1, "one logo");
    eq([imgs[0].getAttribute("src"), imgs[0].alt], [LOGO, "OmaCar"], "the logo");
    eq(LOGO, "/demo-media/logo.png", "from the demo's media");
    const pills = vbar.querySelectorAll(".demo-pill");
    eq(pills.length, 1, "one pill");
    eq(pills[0].textContent, "DEMO", "its word");
    eq(pills[0].nextElementSibling.id, "btn-daynight", "just before the buttons");
    // A rebuilt right side gets its pill back.
    pills[0].remove();
    after(vbar);
    eq(vbar.querySelectorAll(".demo-pill").length, 1, "back");
  }],

  ["bar: a 700 ms press on the logo opens the menu; a shorter one does not", () => {
    const c = clock();
    let opened = 0;
    const vbar = document.createElement("header");
    vbar.innerHTML = '<div class="tb-left"><span class="tb-mark">OmaCar</span></div><div class="tb-right"></div>';
    const after = createBar({ onMenu: () => opened++, later: c.later, cancel: c.cancel });
    after(vbar);
    const mark = vbar.querySelector(".tb-mark");
    const ev = (type) => mark.dispatchEvent(new Event(type, { bubbles: true }));
    ev("pointerdown");
    c.advance((LONG_PRESS_MS - 100) / 1000);
    ev("pointerup");
    c.advance(1);
    eq(opened, 0, "a tap is not a press");
    ev("pointerdown");
    c.advance(LONG_PRESS_MS / 1000);
    eq(opened, 1, "a press is");
    ev("pointerup");
    eq(LONG_PRESS_MS, 700, "700 ms");
  }],

  // ---- the menu ---------------------------------------------------------------------
  ["menu: its items by state, cues posted, and the two it can only describe", async () => {
    const posted = [];
    const cues = createCues({ post: (name) => { posted.push(name); return Promise.resolve(); },
                              sample: () => ({ parked: false }), now: () => 0 });
    const fake = { state: "idle", index: -1, steps: [{ title: "Home" }], started: 0, resumed: 0, paused: 0,
                   start() { this.started++; }, resume() { this.resumed++; }, pause() { this.paused++; },
                   subscribe() { return () => {}; } };
    const host = document.createElement("div");
    let restarts = 0;
    const menu = createMenu({ tour: fake, cues, host, restart: () => { restarts++; } });
    const labels = () => [...host.querySelectorAll(".dm-item .dm-t")].map((n) => n.textContent);
    menu.open();
    eq(labels(), ["Start tour", "Drowsy moment", "Hard braking", "Park", "Restart the drive",
                  "Play backup video", "Exit demo"], "idle, driving");
    const click = (label) => [...host.querySelectorAll(".dm-item")]
      .find((b) => b.querySelector(".dm-t").textContent === label).click();
    click("Drowsy moment");
    eq(posted, ["drowsy"], "posted");
    eq(menu.isOpen(), false, "a cue closes the menu");
    menu.open();
    click("Park");
    eq(posted, ["drowsy", "park"], "park");
    menu.open();
    eq(labels()[3], "Drive", "parked now: Drive, before the world says so");
    click("Drive");
    eq(posted[2], "drive", "drive");
    fake.state = "paused";
    fake.index = 0;
    menu.open();
    eq(labels().slice(0, 2), ["Resume tour", "Start tour"], "paused: Resume first");
    click("Resume tour");
    eq(fake.resumed, 1, "resumed");
    menu.open();
    click("Play backup video");
    ok(menu.isOpen(), "the video row stays open to say what to run");
    ok(host.textContent.includes(BACKUP_TEXT), "and says it");
    eq(BACKUP_TEXT, "omacar-demo video", "the command: the wrapper, not the tablet's own omacar");
    ok(host.textContent.includes("Run omacar-demo video on the tablet."), "in a sentence");
    click("Exit demo");
    ok(host.textContent.includes(EXIT_TEXT), "how to leave");
    eq(EXIT_TEXT, "Run omacar-demo off, or press Super+W", "its words");
    ok(!/omacar demo/.test(host.textContent), "and neither row names the tablet's own omacar");
    click("Restart the drive");
    eq(restarts, 1, "restart");
    eq(posted.length, 3, "restart is the restart function's, not a bare cue here");
  }],

  ["menu: opening it pauses a running tour", () => {
    const fake = { state: "running", index: 2, steps: [{}, {}, { title: "Road cameras" }], paused: 0,
                   pause() { this.paused++; this.state = "paused"; }, subscribe() { return () => {}; } };
    const host = document.createElement("div");
    const menu = createMenu({ tour: fake, cues: createCues({ post: () => Promise.resolve(), sample: () => null }), host });
    menu.open();
    eq(fake.paused, 1, "paused");
    menu.toggle();
    eq(menu.isOpen(), false, "toggled shut");
  }],

  // ---- the scripted scan ----------------------------------------------------------------
  ["scan: eight modules go from Scanning… to No codes over 12 s, and it ends all normal", () => {
    const c = clock();
    const root = document.createElement("div");
    const stop = createScan(root, { later: c.later, cancel: c.cancel });
    eq(MODULES.map((m) => m.name), ["PGM-FI", "IMA", "ABS/VSA", "SRS", "EPS", "Body", "Meter", "A/C"], "the modules");
    eq(SCAN_SECS, 12, "12 s");
    const states = () => [...root.querySelectorAll(".scanline .ds-st")].map((n) => n.textContent);
    eq(states().length, 8, "all eight listed");
    c.advance(0.01);
    eq(states()[0], "Scanning…", "the first is being read");
    eq(states().slice(1), Array(7).fill("Waiting"), "the rest wait");
    c.advance(SCAN_SECS / 8);
    eq(states().slice(0, 2), ["No codes", "Scanning…"], "one after another");
    ok(!root.querySelector(".ds-done") || root.querySelector(".ds-done").hidden, "no summary yet");
    c.advance(SCAN_SECS);
    eq(states(), Array(8).fill("No codes"), "every one clean");
    eq(root.querySelector(".ds-done-t").textContent, DONE_TEXT, "the summary");
    eq(DONE_TEXT, "All systems normal · 8 modules · 0 codes", "its words");
    const w = root.querySelector(".ds-bar > i").style.width;
    eq(w, "100%", "the bar full");
    stop();
    eq(c.timers.length, 0, "unmounting stops it");
  }],
];
