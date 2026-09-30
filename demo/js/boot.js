// The meetup demo's entry point (doc/design/2026-09-30-meetup-demo.md).
//
// Loaded only by demo/demo.html, and before js/main.js, so the door main.js
// reads once at load (globalThis.OMACAR_DEMO) is open by then. The shape:
//
//   views:      { [viewId]: mount | { mount, fast } }             replaces a live view's mount;
//               mount(root, {arg}) -> unmount|null, fast = poll /api/live at 4 Hz
//   extraViews: [{ id, label, title, mount, fast }]               the demo's own routable screens
//   tabRoots:   { [tabId]: viewId }                               the screen a tab opens on
//   cards:      { [cardId]: () -> { node, paint(), destroy() } }   dresses an existing Home card
//   afterBar:   (vbar) -> void                                    after every paint of the top bar
//   onKey:      (KeyboardEvent) -> boolean                        true when the demo used the key
//
// Each part of the demo registers itself from its own module; this file only
// wires them, in order (Task 8 of doc/design/2026-09-30-meetup-demo-plan.md):
//
//   1. the map, Home's nav card and the drowsy moment:        register(D)
//   2. Now Playing, the phone card, CarPlay, Android Auto,
//      Agent and Work:                                        register(D, deps)
//   3. and its own: the scripted Scan vehicle, Home's car and agent cards, the top
//      bar (the logo, DEMO and the long press), the menu, the tour and its
//      keys, and the `demo-tour` screen `omacar demo tour` asks for.
//   4. the quiet cue (hardening B): the music fades out when `demo off`
//      asks, and before the tour's reset:                       register(D, { radio })

import { h, store } from "../../js/core.js";
import { tyreState } from "../../js/systems.js";
import { register as navigation, mountMap } from "./views/navigation.js";
import { register as navCard } from "./cards/navcard.js";
import { register as agentCard } from "./cards/agentcard.js";
import { register as drowsy, chipAfterBar } from "./drowsy.js";
import { getRadio } from "./radio.js";
import { register as nowPlaying } from "./views/nowplaying.js";
import { register as phoneCard } from "./cards/phonecard.js";
import { register as carplay } from "./views/carplay.js";
import { register as androidAuto } from "./views/androidauto.js";
import { openScreen } from "./projection.js";
import { register as agent, agentActions, restoreHome } from "./views/agent.js";
import { register as work, workActions, resetWork } from "./views/work.js";
import { register as scan } from "./views/scan.js";
import { createTour, createCaptions, createReset, loadSteps } from "./tour.js";
import { createMenu, createCues } from "./menu.js";
import { createBar } from "./bar.js";
import { register as quiet, fadeOut } from "./quiet.js";

const D = globalThis.OMACAR_DEMO = {
  views: {}, extraViews: [], tabRoots: {}, cards: {}, afterBar: null, onKey: null,
};

// NOT THE LIVE APP'S FIRST-RUN CARDS. A new demo window (a fresh profile after
// `omacar demo trash`) would open on six cards about arming writes to the car,
// in front of the room. The flag is onboard.js's own, in the demo window's
// profile (DEMO_ROOT/browser), which the live kiosk never reads.
try { localStorage.setItem("omacar.onboarded", "1"); } catch { /* no storage: onboard.done reads true */ }

// ---- 1 and 2: the parts -----------------------------------------------------------
navigation(D);
navCard(D);
drowsy(D);

const radio = getRadio();
const deps = { radio, mountMap, back: () => { location.hash = "#home"; } };
nowPlaying(D, deps);
phoneCard(D, deps);
carplay(D, deps);
androidAuto(D, deps);
agent(D, deps);
work(D, deps);
scan(D);
agentCard(D);
quiet(D, { radio });

// So Home's radio row and every Now Playing have the station's names from the
// first paint. Loading plays nothing.
Promise.resolve().then(() => radio.load()).catch((e) => console.warn("Omarchy Radio:", e));

// ---- Home's car -----------------------------------------------------------------
// The live card with the demo's picture (the CR-Z cut from mockup 3,
// tools/demo_carpic.py), and the live card's own tyre callout.
const CAR_PICTURE = "/demo-media/crz-home.png";
D.cards.car = function carCard() {
  const img = h("img.car-img", { alt: "", hidden: true, draggable: "false" });
  img.addEventListener("load", () => { img.hidden = false; });
  img.src = CAR_PICTURE;
  const tyre = h("div.callout", { data: { tone: "" }, style: { left: "50%", top: "92%" } },
    h("span.co-dot"), h("span.co-k", "Tyres"), h("span.co-v"));
  const value = tyre.querySelector(".co-v");
  const node = h("div.card.hc.hc-car.dm-car", h("div.car-stage", img, tyre));
  return {
    node,
    paint() {
      const st = tyreState(store.car);
      if (tyre.dataset.tone !== st.tone) tyre.dataset.tone = st.tone;
      if (value.textContent !== st.text) value.textContent = st.text;
    },
  };
};

// ---- the tour, its menu and its keys ----------------------------------------------
const cues = createCues({ sample: () => (store.sample && store.sample.demo) || null });

// What a step's `do` calls: the modules' own functions, never a simulated tap.
const ACTIONS = {
  "radio.play": () => radio.play(0),
  "agent.ask": (id) => agentActions.ask(id),
  "agent.apply": () => agentActions.apply(),
  "work.update": () => workActions.update(),
  "projection.open": (id) => openScreen(id),
  "home.restore": () => restoreHome(),
};

// A screen, opened afresh even when the page is already on it: main.js mounts
// on hashchange, and step 1 after a reset must draw Home's restored layout.
function show(hash) {
  if (location.hash === hash) window.dispatchEvent(new HashChangeEvent("hashchange"));
  else location.hash = hash;
}

// The demo back to its start (tour.js createReset): the music faded out and
// the radio quiet, the drive from Marina, Work's sessions fresh, and Home and
// its look as they were before the agent's Apply, waiting for Home at most
// 3 s. A tour from the top and "Restart the drive" both run it.
const resetDemo = createReset({
  fade: () => fadeOut(),
  radio,
  cue: (name) => cues.send(name),
  resetWork,
  restoreHome: () => restoreHome(),
});

const steps = [];
// In #app, so the captions read its --nav, which is shorter on a screen with
// no chip row.
const captions = createCaptions({ host: document.getElementById("app") || document.body,
                                  onResume: () => tour.resume() });
const tour = createTour({
  steps,
  go: show,
  cue: (name) => { cues.send(name).catch(() => {}); },
  act: (name, arg) => {
    const f = ACTIONS[name];
    if (!f) throw new Error(`the tour has no action ${name}`);
    return f(arg);
  },
  // The drive's clock, for Resume. store.sample is the fast /api/live while a
  // screen polls it and the snapshot's copy (at most 20 s old) on one that
  // does not, which a 180 s margin does not mind.
  demo: () => (store.sample && store.sample.demo) || null,
  // CarPlay's and Android Auto's own screens, for Resume ("" is the first).
  screen: (id) => openScreen(id),
  caption: captions.caption,
  notice: captions.notice,
  reset: resetDemo,
  parked: () => cues.parked(),
  menu: () => menu.toggle(),
});
tour.subscribe((t) => {
  if (t.state !== "paused") captions.hideNote();
  // The caption band (demo.css) is there from Start to the end.
  document.body.classList.toggle("dt-touring", t.state !== "idle");
});
const ready = loadSteps().then((s) => { steps.push(...s); })
  .catch((e) => console.warn("demo tour:", e));

const menu = createMenu({
  tour, cues,
  restart: async () => { tour.stop(); await resetDemo(); show("#home"); },
});

const bar = createBar({ onMenu: () => menu.open() });
D.afterBar = (vbar) => { bar(vbar); chipAfterBar(vbar); };
D.onKey = (e) => tour.key(e);

// ANY TOUCH PAUSES THE TOUR. Captured on the way down, so a screen that stops
// its own events still counts; a tour that is not running ignores it.
document.addEventListener("pointerdown", () => tour.touch(), true);

// `omacar demo tour` asks the page for this screen (POST /api/screen), and
// main.js's honourAsk() opens it: the tour from the top, on Home.
D.extraViews.push({
  id: "demo-tour", label: "Tour", title: "The tour", askable: true,
  mount(root) {
    root.appendChild(h("div.dt-starting", "Starting the tour…"));
    setTimeout(() => { ready.then(() => tour.start()); }, 0);
    return null;
  },
});

// For the venue check and the end-to-end walk (tools and CDP), never the page.
globalThis.OMACAR_DEMO_TOUR = { tour, menu, ready };
