// The meetup demo's tour (demo/js/tour.js, its steps in demo/data/tour.json),
// the top bar it dresses (demo/js/bar.js), its menu (demo/js/menu.js) and the
// scripted Scan vehicle (demo/js/views/scan.js).
//
// EVERYTHING OUTSIDE THEM IS A FAKE: the clock is a list of timers this file
// moves, the page's location is a string, a cue is a line in a log and so is
// every module action. Nothing here reaches a server, the radio or a speaker.
import { eq, ok } from "./assert.js";
import { createTour, createReset, createCaptions, fitSize, CAP_MIN_PX, loadSteps, PAUSED, PAUSED_MS, RESET_WAIT_MS,
         RESYNC_NEAR_S, RESYNC_PAUSE_S, RESYNC_WAIT_MS, RESYNC_SETTLE_S, CLOSING_SECS } from "../demo/js/tour.js";
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
  // `world` is the page's store.sample.demo (store.live.demo while a screen polls fast, the snapshot's
  // copy otherwise): { t, loop_secs }, or null when the page has none.
  const r = { c, steps, log: [], captions: [], notices: [], hash: "#home", resets: 0, menus: 0, parked: false,
              world: null, screens: [], toasts: [] };
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
    screen: (id) => { r.screens.push(id); return true; },
    toast: (text) => r.toasts.push(text),
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

const SENTENCE = "Oma Agent knows this car, and changes the dashboard for you. ";

// A caption element in the page with demo.css applied, and what a test asks of it.
// `later` is held: show() lets the fade's swap run at once.
async function styled() {
  const css = await (await fetch("../demo/css/demo.css")).text();
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);
  const host = document.createElement("div");
  document.body.appendChild(host);
  const pending = [];
  const caps = createCaptions({ host, later: (fn) => { pending.push(fn); return pending.length; }, cancel: () => {} });
  const cap = host.querySelector(".dt-cap");
  const words = cap.querySelector(".dt-cap-t");
  const textW = () => {
    const g = document.createRange();
    g.selectNodeContents(words);
    return g.getBoundingClientRect().width;
  };
  const size = () => parseFloat(getComputedStyle(cap).fontSize);
  return {
    cap, words, size,
    show(text) { caps.caption(text); while (pending.length) pending.shift()(); },
    fits: () => textW() <= words.getBoundingClientRect().width + 0.05,
    // The row is the taller of the line (1.3 of the size) and the accent bar (24 px);
    // the padding and border are 26 px more. A second line would add 1.3 of the size.
    oneLine: () => cap.getBoundingClientRect().height <= Math.max(size() * 1.3, 24) + 27,
    done() { caps.destroy(); host.remove(); style.remove(); },
  };
}

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

  // ---- Resume inside CarPlay or Android Auto ---------------------------------------------
  //
  // A projection's screens are its own: the page's address says `#carplay`
  // whichever of them is up, so "the page is where the tour left it" was true
  // even with the presenter three taps deep in Settings.
  ["Resume in CarPlay puts the projection back on the step's own screen, by openScreen", async () => {
    const r = await rig();
    await r.tour.start();
    r.c.advance(300 + 5);                  // CarPlay, 5 s in: still on its home screen
    r.tour.touch();
    r.tour.resume();
    eq([r.hash, r.screens], ["#carplay", [""]], "its first screen (an empty id is the projection's own)");
    r.c.advance(12 - 5 + 3);               // Maps opened at 12 s
    r.tour.touch();
    r.tour.resume();
    eq([r.hash, r.screens], ["#carplay", ["", "maps"]], "Maps, the screen the step last showed");
    r.c.advance(24 - 15 + 2);              // Now Playing at 24 s
    r.tour.touch();
    r.tour.resume();
    eq(r.screens, ["", "maps", "nowplaying"], "Now Playing");
    eq(r.tour.state, "running", "running again");
    eq(r.gos().filter((h) => h === "#carplay").length, 1, "and the page itself never went anywhere");
  }],

  ["Resume in Android Auto puts it back on its first screen; one outside a projection touches none", async () => {
    const r = await rig();
    await r.tour.start();
    r.c.advance(335 + 12);                 // Android Auto, 12 s in
    r.tour.touch();
    r.tour.resume();
    eq([r.hash, r.screens], ["#androidauto", [""]], "Android Auto's own first screen");
    r.key("2");                            // Navigation
    r.c.advance(10);
    r.tour.touch();
    r.tour.resume();
    r.key("8");                            // Work
    r.tour.touch();
    r.tour.resume();
    eq(r.screens, [""], "no other screen is a projection's");
  }],

  ["the page having left a projection is the address's to put right, not openScreen's as well", async () => {
    const r = await rig();
    await r.tour.start();
    r.c.advance(300 + 15);                 // CarPlay, after Maps
    r.tour.touch();
    r.hash = "#home";                      // the OmaCar tile
    r.tour.resume();
    eq([r.hash, r.screens], ["#carplay/maps", []], "the address takes it straight to Maps");
  }],

  ["a projection that is not there, or that throws, does not stop Resume", async () => {
    const warn = console.warn;
    console.warn = () => {};
    try {
      const r = await rig({ deps: { screen: () => { throw new Error("no projection"); } } });
      await r.tour.start();
      r.c.advance(300 + 5);
      r.tour.touch();
      eq(r.tour.resume(), true, "taken");
      eq(r.tour.state, "running", "running");
      r.c.advance(40);
      eq(r.hash, "#androidauto", "and on to the next step");
    } finally { console.warn = warn; }
  }],

  // Fix round 1: a Resume wait that ends under an open menu, and the menu's own Resume tour.
  ["a Resume wait that ends under an open menu, then the menu's Resume tour: one restart, and Resume works after", async () => {
    const r = await rig();
    r.world = { t: 100, loop_secs: 900 };
    await r.tour.start();
    r.c.advance(50);
    r.tour.touch();
    r.c.advance(400);                       // paused for more than 5 minutes
    const host = document.createElement("div");
    const menu = createMenu({ tour: r.tour, cues: createCues({ post: () => Promise.resolve(), sample: () => null }), host });
    const restarts = () => r.log.filter((l) => l[2] === "restart").length;
    eq(r.tour.resume(), true, "Resume starts a wait for the drive");
    menu.open();                            // the presenter opens the menu during it
    r.c.advance(RESYNC_WAIT_MS / 1000 + 0.3);
    eq([r.tour.state, r.c.timers.length], ["paused", 0], "the wait ran out under the menu: the step has not started");
    const gos = r.gos().length;
    [...host.querySelectorAll(".dm-item")].find((b) => b.querySelector(".dm-t").textContent === "Resume tour").click();
    eq(menu.isOpen(), false, "the menu closed");
    eq(restarts(), 1, "the menu's Resume tour did not send a second restart");
    eq([r.tour.state, r.tour.index], ["running", 1], "the held re-entry ran: the same step, running");
    eq(r.gos().length, gos + 1, "from its top, once");
    r.c.advance(5);
    r.tour.touch();
    eq(r.tour.state, "paused", "a touch pauses it");
    eq(r.tour.resume(), true, "and Resume is still taken: nothing is stuck waiting");
    eq(r.tour.state, "running", "running");
  }],

  ["Resume is refused while a held re-entry is waiting for the menu, and taken again once it has run", async () => {
    const r = await rig();
    await r.tour.start();
    r.c.advance(50);
    r.tour.touch();
    r.c.advance(400);
    r.tour.resume();
    r.tour.hold();
    r.c.advance(RESYNC_WAIT_MS / 1000 + 0.3);
    eq(r.tour.resume(), false, "refused: the re-entry is already pending");
    eq(r.log.filter((l) => l[2] === "restart").length, 1, "one restart");
    r.tour.release();
    eq(r.tour.state, "running", "the release ran it");
    r.tour.touch();
    eq(r.tour.resume(), true, "and Resume is taken");
  }],

  // Fix round 1: the clock a slow screen reads can be 20 s old.
  ["for 25 s after a restart the drive clock is not believed: a stale snapshot does not restart it twice", async () => {
    eq(RESYNC_SETTLE_S, 25, "20 s of snapshot, and some");
    const r = await rig();
    r.world = { t: 100, loop_secs: 900 };
    await r.tour.start();
    r.c.advance(50);
    r.tour.touch();
    r.world = { t: 700, loop_secs: 900 };
    r.tour.resume();                        // near the closing stop: restart
    const restarts = () => r.log.filter((l) => l[2] === "restart").length;
    eq(restarts(), 1, "the first");
    r.world = { t: 1, loop_secs: 900 };
    r.c.advance(0.25);
    eq([r.tour.state, r.tour.index], ["running", 1], "started over");
    r.c.advance(5);
    r.tour.touch();
    r.world = { t: 700, loop_secs: 900 };   // the snapshot, from before the restart
    r.tour.resume();
    eq([restarts(), r.tour.state], [1, "running"], "5 s on: the stale clock is not believed");
    r.c.advance(RESYNC_SETTLE_S - 5 - 0.5);
    r.tour.touch();
    r.tour.resume();
    eq(restarts(), 1, "24.5 s on: still not");
    r.c.advance(1);
    r.tour.touch();
    r.tour.resume();
    eq(restarts(), 2, "after 25 s it is believed again");
  }],

  ["the drive clock is not believed for 25 s after a tour from the top, either (its reset cued restart)", async () => {
    const r = await rig();
    r.world = { t: 700, loop_secs: 900 };   // the snapshot from before the reset
    await r.tour.start();
    r.c.advance(10);
    r.tour.touch();
    r.tour.resume();
    eq(r.log.filter((l) => l[2] === "restart").length, 0, "no restart 10 s in");
    eq(r.tour.state, "running", "running");
    r.c.advance(RESYNC_SETTLE_S);
    r.tour.touch();
    r.tour.resume();
    eq(r.log.filter((l) => l[2] === "restart").length, 1, "believed after that");
  }],

  ["a pause of more than 5 minutes still restarts, whatever was sent 25 s before", async () => {
    const r = await rig();
    await r.tour.start();
    r.c.advance(5);
    r.tour.touch();
    r.c.advance(RESYNC_PAUSE_S + 1);
    r.tour.resume();
    eq(r.log.filter((l) => l[2] === "restart").length, 1, "the long pause is not the clock's to excuse");
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

  // ---- the captions: one line, shrunk to fit ---------------------------------------------------
  //
  // In portrait the caption used to wrap onto two balanced lines (and the band
  // was 108 px to hold them). It is one line at every width now: the text
  // shrinks to fit, down to CAP_MIN_PX, and what still does not fit is cut with
  // an ellipsis rather than wrapped. These run against demo.css itself.
  ["fitSize: the largest size that fits, never under 16 px, and the top size when it all fits", () => {
    eq(CAP_MIN_PX, 16, "the floor");
    eq(fitSize((px) => px <= 18, 20), 18, "18 is the largest that fits");
    eq(fitSize(() => true, 20), 20, "all of it fits: the size it had");
    eq(fitSize(() => false, 20), 16, "none fits: the floor");
    eq(fitSize((px) => px <= 10, 20), 16, "and never under it");
    eq(fitSize(() => false, 14), 14, "a style that starts under the floor is not raised to it");
    const asked = [];
    fitSize((px) => { asked.push(px); return false; }, 22);
    eq(asked, [22, 21, 20, 19, 18, 17], "from the top, a pixel at a time");
  }],

  ["a caption that fits keeps the stylesheet's size, and never wraps", async () => {
    const t = await styled();
    try {
      t.show("Turn-by-turn on the tablet, working offline.");
      ok([20, 22].includes(t.size()), `the stylesheet's size: ${t.size()}`);
      eq(t.cap.style.fontSize, "", "no size of its own");
      ok(t.fits(), "and it fits");
      eq(getComputedStyle(t.cap).whiteSpace, "nowrap", "nowrap");
    } finally { t.done(); }
  }],

  ["longer captions take the largest size that fits on one line, never under 16 px", async () => {
    const t = await styled();
    try {
      t.show("x");
      const base = t.size();
      let before = base;
      for (const n of [1, 2, 3, 4, 6]) {
        t.show(SENTENCE.repeat(n).trim());
        const px = t.size();
        ok(t.oneLine(), `${n} sentences: one line, not ${Math.round(t.cap.getBoundingClientRect().height)} px tall`);
        ok(px >= CAP_MIN_PX && px <= base, `${n} sentences: ${px} px is between 16 and ${base}`);
        ok(px <= before, `${n} sentences: no bigger than fewer were (${px} after ${before})`);
        before = px;
        if (px > CAP_MIN_PX) {
          ok(t.fits(), `${n} sentences fit at ${px} px`);
        }
        if (px < base && px > CAP_MIN_PX) {
          t.cap.style.fontSize = `${px + 1}px`;
          ok(!t.fits(), `${n} sentences: ${px + 1} px would not have fitted, so ${px} is the largest`);
          t.cap.style.fontSize = `${px}px`;
        }
      }
      ok(before < base, `the longest was shrunk (${before} of ${base})`);
    } finally { t.done(); }
  }],

  ["too long even at 16 px: still one line at 16 px, cut with an ellipsis, never wrapped", async () => {
    const t = await styled();
    try {
      t.show(SENTENCE.repeat(40).trim());
      eq(t.size(), CAP_MIN_PX, "the floor");
      ok(t.oneLine(), "one line");
      ok(!t.fits(), "and it does not fit: it is cut");
      eq(getComputedStyle(t.words).textOverflow, "ellipsis", "with an ellipsis");
    } finally { t.done(); }
  }],

  ["the next caption starts from the stylesheet's size, not the last one's", async () => {
    const t = await styled();
    try {
      t.show("x");
      const base = t.size();
      t.show(SENTENCE.repeat(6).trim());
      ok(t.size() < base, "the long one shrank");
      t.show("Short.");
      eq(t.size(), base, "the short one did not inherit it");
      eq(t.cap.style.fontSize, "", "no size of its own");
    } finally { t.done(); }
  }],

  ["every one of the tour's captions is one line", async () => {
    const t = await styled();
    try {
      for (const step of await loadSteps()) {
        if (!step.caption) continue;
        t.show(step.caption);
        ok(t.oneLine(), `${step.id}: one line`);
        ok(t.size() >= CAP_MIN_PX, `${step.id}: ${t.size()} px`);
      }
    } finally { t.done(); }
  }],

  ["a turned screen refits the caption on its window's resize", async () => {
    const t = await styled();
    try {
      t.show(SENTENCE.trim());
      const wide = t.size();
      t.cap.style.maxWidth = "330px";            // a narrower window, as the viewport's would make it
      window.dispatchEvent(new Event("resize"));
      ok(t.size() < wide, `narrower: ${t.size()} px, from ${wide}`);
      ok(t.oneLine(), "and still one line");
      t.cap.style.maxWidth = "";
      window.dispatchEvent(new Event("resize"));
      eq(t.size(), wide, "wide again");
    } finally { t.done(); }
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
    eq(EXIT_TEXT, "Run omacar-demo off", "its words: Super+W would close the window without the fade");
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

  // ---- a step that needs what the demo does not have (hardening D) -------------------------
  //
  // The owner's cameras wait for parts, and the demo is shown without footage. tour.json's
  // Cameras step says `"needs": "clips"`; the page tells the tour (`has(need)`) whether the
  // demo has them, from GET /api/cams, and the tour jumps over a step whose need is not
  // met. Step numbers, keys and captions stay as they are: a skipped step is jumped over,
  // and its key does nothing but toast. `has` answers false, or nothing (null: not asked
  // yet, or no answer), and only an answer of false skips: the Cameras screen draws an
  // empty state of its own, so a step wrongly entered still looks finished, where a step
  // wrongly skipped is gone.
  ["tour.json: only the Cameras step needs anything, and it says what its key toasts", async () => {
    const steps = await loadSteps();
    eq(steps.filter((s) => s.needs).map((s) => [s.id, s.needs]), [["cameras", "clips"]], "Cameras needs clips");
    eq(steps[3].missing, "Cameras aren't connected in this demo", "the toast");
    eq(steps.map((s) => s.id), ["home", "navigation", "roadcams", "cameras", "drowsy", "vehicle",
                                "agent", "work", "carplay", "androidauto", "end"], "the steps are the same");
    eq(steps.map((s) => s.secs), [40, 40, 15, 35, 40, 30, 60, 40, 35, 30, 10], "and last as long");
  }],

  ["with no footage the tour jumps over Cameras, and every other step runs as it did", async () => {
    const r = await rig({ deps: { has: (need) => need !== "clips" } });
    const seen = [];
    r.tour.subscribe((t) => { if (t.state === "running" && seen[seen.length - 1] !== t.index) seen.push(t.index); });
    await r.tour.start();
    r.c.advance(TOTAL(r.steps) + 5);
    eq(seen, [-1, 0, 1, 2, 4, 5, 6, 7, 8, 9, 10], "step 4 is never entered");
    const want = [
      [0, "reset"], [0, "go", "#home"], [25, "do", "radio.play"],
      [40, "go", "#navigation"],
      [80, "go", "#roadcams"],
      [95, "go", "#home"], [95, "cue", "drive"], [98, "cue", "drowsy"],
      [135, "go", "#vehicle"], [150, "go", "#scan"],
      [165, "go", "#advisor"], [165, "cue", "park"], [173, "do", "agent.ask(night)"],
      [195, "do", "agent.apply"], [210, "do", "agent.ask(radio)"],
      [225, "go", "#work"], [225, "cue", "drive"], [249, "do", "work.update"],
      [265, "go", "#carplay"], [277, "do", "projection.open(maps)"], [289, "do", "projection.open(nowplaying)"],
      [300, "go", "#androidauto"], [320, "do", "home.restore"],
      [330, "go", "#home"],
    ];
    eq(r.log, want, "the tour, 35 s shorter");
    ok(!r.log.some((l) => l[2] === "#cameras" || l[2] === "hard_brake"), "no Cameras screen, no hard brake");
    ok(!r.captions.some((c) => c[1] && c[1].startsWith("Three cameras")), "and its caption is never shown");
    eq(r.tour.state, "idle", "it ends, on Home");
    eq(r.toasts, [], "and says nothing about it: the tour is not a key");
  }],

  ["with footage the tour is the one it was: Cameras is entered, with its cues", async () => {
    const r = await rig({ deps: { has: () => true } });
    await r.tour.start();
    r.c.advance(TOTAL(r.steps) + 5);
    ok(r.log.some((l) => l[1] === "go" && l[2] === "#cameras"), "Cameras");
    eq(r.log.filter((l) => l[0] >= 95 && l[0] <= 100 && l[1] === "cue").map((l) => l[2]), ["drive", "hard_brake"], "its cues");
  }],

  ["a missing answer (null: not asked, or no reply) does not skip: only false does", async () => {
    for (const unknown of [null, undefined]) {
      const r = await rig({ deps: { has: () => unknown } });
      await r.tour.start();
      r.c.advance(100);
      ok(r.log.some((l) => l[2] === "#cameras"), `has() = ${unknown}: Cameras is entered`);
    }
  }],

  ["the decision is made as the step is entered, not when the tour starts", async () => {
    let footage = false;
    const r = await rig({ deps: { has: () => footage } });
    await r.tour.start();
    r.c.advance(50);                      // Navigation
    footage = true;                       // the camera feed came up
    r.c.advance(50);
    ok(r.log.some((l) => l[2] === "#cameras"), "Cameras, which a tour that began without footage now shows");
    const later = await rig({ deps: { has: () => footage } });
    footage = true;
    await later.tour.start();
    later.c.advance(60);
    footage = false;                      // and it went
    later.c.advance(40);
    ok(!later.log.some((l) => l[2] === "#cameras"), "and one that began with footage skips it once it has gone");
  }],

  ["the key of a skipped step toasts and does nothing else; its neighbours' keys still work", async () => {
    const r = await rig({ deps: { has: (need) => need !== "clips" } });
    await r.tour.start();
    r.c.advance(50);                      // step 2, Navigation
    const before = [r.tour.state, r.tour.index, r.hash, r.log.length, r.captions.length];
    const k = r.key("4");
    eq([k.used, k.prevented], [true, true], "4 is the tour's, and kept from the page");
    eq(r.toasts, ["Cameras aren't connected in this demo"], "the quiet toast");
    eq([r.tour.state, r.tour.index, r.hash, r.log.length, r.captions.length], before,
       "the tour is where it was: no jump, no cue, no caption, no pause");
    r.c.advance(5);
    eq(r.tour.state, "running", "still running");
    r.key("5");
    eq([r.tour.index, r.hash], [4, "#home"], "5 jumps to the drowsy moment");
    r.key("3");
    eq([r.tour.index, r.hash], [2, "#roadcams"], "3 to the road cameras");
    eq(r.toasts.length, 1, "and neither toasted");
  }],

  ["the key of a skipped step, with the tour idle, starts nothing", async () => {
    const r = await rig({ deps: { has: () => false } });
    const k = r.key("4");
    eq([k.used, r.tour.state, r.tour.index, r.log.length], [true, "idle", -1, 0], "idle, and nothing sent");
    eq(r.toasts, ["Cameras aren't connected in this demo"], "toast");
    r.key("1");
    eq(r.tour.state, "running", "1 starts as ever");
  }],

  ["a step with no `missing` words toasts a plain default, and one with footage toasts nothing", async () => {
    const steps = [{ id: "a", go: "#a", secs: 5, at: [] },
                   { id: "b", go: "#b", secs: 5, needs: "clips", at: [] }];
    const r = await rig({ steps, deps: { has: () => false } });
    r.key("2");
    eq(r.toasts, ["That step is not available in this demo"], "the default");
    const w = await rig({ steps, deps: { has: () => true } });
    w.key("2");
    eq([w.toasts, w.tour.index], [[], 1], "with footage: it jumps");
  }],

  ["jump() to a skipped step goes on to the next one that can run", async () => {
    const r = await rig({ deps: { has: () => false } });
    await r.tour.jump(3);
    eq([r.tour.index, r.hash], [4, "#home"], "the drowsy moment");
    r.c.advance(3);
    eq(r.last(), [3, "cue", "drowsy"], "with its own cues");
  }],

  ["skipped steps at the end of the tour end it", async () => {
    const steps = [{ id: "a", go: "#a", secs: 5, caption: "A", at: [] },
                   { id: "b", go: "#b", secs: 5, caption: "B", needs: "x", at: [] },
                   { id: "c", go: "#c", secs: 5, caption: "C", needs: "x", at: [] }];
    const r = await rig({ steps, deps: { has: () => false } });
    await r.tour.start();
    r.c.advance(6);
    eq([r.tour.state, r.tour.index], ["idle", -1], "the tour is over");
    eq(r.gos(), ["#a"], "only the first was shown");
    eq(r.captions[r.captions.length - 1][1], null, "its caption gone");
    const all = await rig({ steps: steps.map((s) => ({ ...s, needs: "x" })), deps: { has: () => false } });
    await all.tour.start();
    eq([all.tour.state, all.gos()], ["idle", []], "a tour with nothing to show ends at once");
  }],

  ["the tour looks again at what the demo has: at its start (waited for) and at each step's entry", async () => {
    let footage = true, looks = 0;
    const r = await rig({ deps: { has: () => footage, refresh: async () => { looks++; footage = false; } } });
    const first = r.tour.start();
    eq(looks, 1, "the start's own look, with the reset, before step 1");
    await first;
    eq(looks, 2, "and one as step 1 opened");
    r.c.advance(TOTAL(r.steps) + 5);
    ok(!r.log.some((l) => l[2] === "#cameras"), "the start's answer (no footage) decided Cameras");
    eq(looks, 1 + 10, "ten steps opened, none of them Cameras");
  }],

  ["a look that never answers holds the start for RESET_WAIT_MS and no longer; one that fails holds nothing", async () => {
    const hung = await rig({ deps: { has: () => true, refresh: () => new Promise(() => {}) } });
    let started = false;
    const p = hung.tour.start().then(() => { started = true; });
    await tick();
    eq([started, hung.gos()], [false, []], "waiting");
    hung.c.advance(RESET_WAIT_MS / 1000);
    await p;
    eq(hung.gos(), ["#home"], "on after the cap");
    const warn = console.warn;
    const said = [];
    console.warn = (...a) => said.push(a.join(" "));
    try {
      const failed = await rig({ deps: { has: () => false, refresh: () => Promise.reject(new Error("no server")) } });
      await failed.tour.start();
      eq(failed.gos(), ["#home"], "a failed look: the tour starts");
      failed.c.advance(100);
      ok(!failed.log.some((l) => l[2] === "#cameras"), "on what it knew");
    } finally { console.warn = warn; }
    ok(said.every((x) => x.startsWith("demo tour")), "its warnings are the tour's own: " + said.join("|"));
  }],

  // boot.js hands the tour an EMPTY steps array and fills it once tour.json has loaded, so whether
  // any step needs something is a question for the moment of asking, never for the tour's making.
  ["a tour made with no steps and given them afterwards, as boot.js does, still looks: at its start, as each step opens, and on a skipped key", async () => {
    const steps = [];
    let looks = 0;
    const r = await rig({ steps, deps: { has: (need) => need !== "clips", refresh: async () => { looks++; } } });
    steps.push(...await loadSteps());                 // tour.json loaded after the tour was made
    const started = r.tour.start();
    eq(looks, 1, "the start's own look, with the reset");
    await started;
    eq(looks, 2, "and one as step 1 opened");
    r.c.advance(40);
    eq([r.tour.index, looks], [1, 3], "and one as Navigation opened");
    r.key("4");
    eq([r.toasts, looks], [["Cameras aren't connected in this demo"], 4], "the key of the skipped step looks again too");
    r.c.advance(TOTAL(r.steps));
    eq(looks, 4 + 8, "eight more as the other steps opened (Cameras jumped over)");
  }],

  ["and with footage, the look happens as the Cameras step itself opens", async () => {
    const steps = [];
    let looks = 0;
    const r = await rig({ steps, deps: { has: () => true, refresh: async () => { looks++; } } });
    steps.push(...await loadSteps());
    await r.tour.start();
    r.c.advance(94);
    const before = looks;
    eq(r.tour.index, 2, "still in the road cameras");
    r.c.advance(2);
    eq([r.tour.index, looks], [3, before + 1], "Cameras opened, and the page looked as it did");
  }],

  ["a tour with no step that needs anything never looks", async () => {
    let looks = 0;
    const steps = [{ id: "a", go: "#a", secs: 5, at: [] }, { id: "b", go: "#b", secs: 5, at: [] }];
    const r = await rig({ steps, deps: { refresh: async () => { looks++; } } });
    await r.tour.start();
    r.c.advance(11);
    eq(looks, 0, "no look");
  }],

  ["a throwing has() or refresh() is no reason to stop the tour", async () => {
    const warn = console.warn;
    console.warn = () => {};
    try {
      const r = await rig({ deps: { has: () => { throw new Error("boom"); }, refresh: () => { throw new Error("boom"); } } });
      await r.tour.start();
      r.c.advance(100);
      ok(r.log.some((l) => l[2] === "#cameras"), "Cameras is entered: a question that fails does not skip");
    } finally { console.warn = warn; }
  }],

  ["Resume after a skipped step goes back to the screen the tour is on, and its step numbers are the same", async () => {
    const r = await rig({ deps: { has: (need) => need !== "clips" } });
    await r.tour.start();
    r.c.advance(100);                      // step 5, the drowsy moment, 5 s in
    eq(r.tour.index, 4, "step 5 is step 5 (its number is stable)");
    r.tour.touch();
    eq(r.tour.state, "paused", "paused");
    r.hash = "#vehicle";
    r.tour.resume();
    eq([r.tour.state, r.tour.index, r.hash], ["running", 4, "#home"], "back on the drowsy moment's screen");
  }],

  // ---- the menu's "Hard braking": "The clip is saved" only while a camera is recording (hardening D) ----
  ["the menu's Hard braking says 'The clip is saved' by default, and with a camera recording", async () => {
    const r = await rig();
    for (const opts of [{}, { saved: () => true }]) {
      const host = document.createElement("div");
      const menu = createMenu({ tour: r.tour, cues: createCues({ post: () => Promise.resolve(), sample: () => null }), host, ...opts });
      menu.open();
      const sub = (id) => { const e = host.querySelector(`[data-item="${id}"] .dm-s`); return e ? e.textContent : null; };
      eq(sub("brake"), "The clip is saved", "kept");
      menu.close();
    }
  }],

  ["with no camera recording the Hard braking row drops it, and the other rows are as they were", async () => {
    const r = await rig();
    const host = document.createElement("div");
    const menu = createMenu({ tour: r.tour, cues: createCues({ post: () => Promise.resolve(), sample: () => null }), host,
                              saved: () => false });
    menu.open();
    const row = host.querySelector('[data-item="brake"]');
    eq([row.querySelector(".dm-t").textContent, row.querySelector(".dm-s")], ["Hard braking", null], "the title, and no subtitle");
    eq(row.querySelector(".dm-k").textContent, "B", "its key");
    eq(host.querySelector('[data-item="drowsy"] .dm-s').textContent, "Level 1, then Level 2", "Drowsy moment's is unchanged");
    eq(host.querySelector('[data-item="start"] .dm-s').textContent, "About six minutes, from the top", "so is Start tour's");
    row.click();
    eq([r.log.length, menu.isOpen()], [0, false], "it still sends the cue (to a fake: nothing here), and closes");
  }],

  ["the menu looks again as it opens: the answer that differs redraws the row, and the focus stays where it was", async () => {
    const r = await rig();
    const host = document.createElement("div");
    document.body.appendChild(host);
    let recording = false;
    const menu = createMenu({ tour: r.tour, cues: createCues({ post: () => Promise.resolve(), sample: () => null }), host,
                              saved: () => recording, refresh: async () => { recording = true; } });
    const sub = () => { const e = host.querySelector('[data-item="brake"] .dm-s'); return e ? e.textContent : null; };
    menu.open();
    ok(host.querySelector('[data-item="brake"]'), "drawn");
    host.querySelector('[data-item="brake"]').focus();
    eq(document.activeElement && document.activeElement.dataset.item, "brake", "the presenter is on it");
    await tick();
    eq(sub(), "The clip is saved", "footage came: drawn again with it");
    eq(document.activeElement && document.activeElement.dataset.item, "brake", "and the focus is still on that row");
    menu.close();
    // the other way, and one that did not change: no redraw at all
    recording = true;
    let draws = 0;
    const seen = new MutationObserver(() => { draws++; });
    menu.open();
    seen.observe(host.querySelector(".dm-list"), { childList: true });
    await tick();
    await tick();
    eq(draws, 0, "an answer that agrees with the items does not redraw them");
    menu.close();
    seen.disconnect();
    host.remove();
  }],

  ["a look that fails, and one that answers after the menu has closed, change nothing", async () => {
    const r = await rig();
    const host = document.createElement("div");
    let done;
    let recording = false;
    const mk = (refresh) => createMenu({ tour: r.tour, cues: createCues({ post: () => Promise.resolve(), sample: () => null }), host,
                                         saved: () => recording, refresh });
    const failing = mk(() => Promise.reject(new Error("no server")));
    failing.open();
    await tick();
    ok(host.querySelector('[data-item="brake"]') && !host.querySelector('[data-item="brake"] .dm-s'), "as drawn");
    failing.close();
    const late = mk(() => new Promise((yes) => { done = yes; }));
    late.open();
    late.close();
    recording = true;
    done();
    await tick();
    eq(host.querySelector(".dm-sheet"), null, "nothing redrawn into a menu that is gone");
    const throwing = mk(() => { throw new Error("boom"); });
    throwing.open();
    ok(host.querySelector(".dm-sheet"), "a refresh that throws does not stop it opening");
    throwing.close();
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
