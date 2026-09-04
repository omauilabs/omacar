// Boot, the navigation, the vehicle bar, and the router.
//
// Two clocks. The snapshot — codes, service, a year of driving — changes on
// the scale of minutes and is polled slowly. The live sample changes five
// times a second and is polled fast, but only while a view that shows it is
// on screen. A diagnostic tool that hammers the daemon while you read a
// service schedule is a tool that gets in the way of the thing it is watching.

import { h, clear, icon, store, U, api, toast, dist, grouped, since } from "./core.js";

import { ICONS } from "./icons.js";
import { learn } from "./learn.js";
import { savedLook, applyLook } from "./looks.js";
import { privacy, vin as maskVin, odo as maskOdo } from "./privacy.js";
import { onboard, showOnboarding } from "./onboard.js";
import hub from "./views/hub.js";
import documentsView from "./views/documents.js";
import replayView from "./views/replay.js";
import resetsView from "./views/resets.js";
import learnView from "./views/learnview.js";
import garageView from "./views/garage.js";
import dash from "./views/dash.js";
import scan from "./views/scan.js";
import codes from "./views/codes.js";
import data from "./views/data.js";
import health from "./views/health.js";
import service from "./views/service.js";
import history from "./views/history.js";
import advisor from "./views/advisor.js";
import tests from "./views/tests.js";
import report from "./views/report.js";
import drive from "./views/drive.js";
import concernsView from "./views/concerns.js";
import live from "./views/live.js";


// ---------------------------------------------------------- the navigation
//
// THE TAB TABLE IS THE VIEW TABLE, AND THAT IS THE WHOLE IDEA.
//
// What was here before was one flat list of nineteen views carrying a
// `primary` flag. A rail down the left showed six of them, hid thirteen
// behind a "More" popup, grew a scroller because nineteen buttons do not fit
// a short window, capped its labels at .58rem for the same reason, and then
// had to promote whichever hidden view you were looking at INTO itself so you
// could see where you were. A navigation that has to lie about its own
// contents to be legible is the wrong navigation, and the file had already
// lost that argument with itself twice.
//
// So the shape below is the point, not the styling. A view exists only as a
// member of a tab, or as an explicit member of OFF_NAV. There is no third
// list to add one to, which means adding a screen forces the question "where
// does this live?" at the moment the screen is written — the only moment
// anybody actually knows the answer. The previous table let a view be added
// with no answer at all, and thirteen of them were.
//
// The first view in a tab is that tab's root: tapping the tab always goes
// there, including when you are already inside the tab. One entry, not a
// stack — see the back chip below for the only history this app keeps.
const TABS = [
  {
    id: "car", label: "Car", icon: ICONS.hub,
    views: [
      { id: "hub",    label: "Hub",      title: "Car hub",              mount: hub,        fast: true },
      { id: "drive",  label: "Gauges",   title: "Drive mode",           mount: drive,      fast: true },
      { id: "live",   label: "Cluster",  title: "Cluster",              mount: live,       fast: true },
      { id: "dash",   label: "Overview", title: "Overview",             mount: dash,       fast: true },
      { id: "garage", label: "Profile",  title: "Every car you own",    mount: garageView },
    ],
  },
  {
    id: "faults", label: "Faults", icon: ICONS.codes,
    views: [
      { id: "codes",  label: "Codes",     title: "Trouble codes",                 mount: codes, fast: true },
      { id: "scan",   label: "Scan",      title: "Full system scan",              mount: scan },
      { id: "health", label: "Readiness", title: "Readiness and on-board tests",  mount: health },
    ],
  },
  {
    id: "data", label: "Data", icon: ICONS.data,
    views: [
      { id: "data",   label: "Live lab", title: "Data lab",                 mount: data,       fast: true },
      { id: "replay", label: "Replay",   title: "Replay a recorded drive",  mount: replayView },
      // Actuator commands. Two taps from the bar, never one, and the chip
      // greys out with the reason on it while the car is moving.
      { id: "tests",  label: "Tests",    title: "Functional tests",         mount: tests, fast: true, write: true },
    ],
  },
  {
    id: "care", label: "Care", icon: ICONS.service,
    views: [
      { id: "service",   label: "Service", title: "Service schedule",                    mount: service },
      { id: "resets",    label: "Resets",  title: "Service resets and functional tests", mount: resetsView, write: true, fast: true },
      // fast, though it shows no gauge: its lock is a question about speed,
      // and a screen that gates on a value has to be watching that value.
      { id: "concerns",  label: "Trends",  title: "Areas of concern",                    mount: concernsView },
      { id: "history",   label: "Log",     title: "Drive history and records",           mount: history },
      { id: "documents", label: "Docs",    title: "Receipts, registrations and records", mount: documentsView },
    ],
  },
  {
    id: "ai", label: "AI", icon: ICONS.advisor,
    views: [
      { id: "advisor", label: "Advisor", title: "Advisor", mount: advisor, ai: true },
    ],
  },
];

// OFF THE NAVIGATION, ON PURPOSE — but still routable.
//
// `report` is an output pretending to be a place: there is not one link to
// #report anywhere in share/js, because what people actually want is to print
// the thing, not to visit it. `learn` is a mode more than a screen; its
// topics open from the Settings sheet, which is where the mode's own switch
// now lives too. Both keep working URLs so a bookmark, a dock card or an old
// deep link still lands somewhere rather than on a fallback.
//
// A view here declares `off` with the reason. Anything reachable only from
// inside another screen belongs in this list and nowhere else.
const OFF_NAV = [
  { id: "report", label: "Report", title: "Vehicle report",
    mount: report,    off: "printed from Faults and from Care · Log" },
  { id: "learn",  label: "Learn",  title: "Learn the car, and the app",
    mount: learnView, off: "opened from Settings" },
];

// Tab membership is stamped onto the view objects themselves rather than kept
// in a parallel lookup, so there is exactly one copy of the fact and a view
// found by the router already knows where it lives.
const VIEWS = [
  ...TABS.flatMap((t) => t.views.map((v) => Object.assign(v, { tab: t.id }))),
  ...OFF_NAV,
];

// THE APP HAS ONE HOME AND CHOOSES A SCREEN EXACTLY ONCE, AT COLD BOOT.
//
// It used to have two. route() fell back to "#dash" while the arrival screen
// was "hub", and autoDrive() pushed the hash to #hub when the adapter
// answered and back to #dash when it dropped — on every live sample, four
// times a second. A flaky ELM327 re-linking at a set of lights threw the user
// off whatever they were reading, and at boot the workshop overview painted
// first and was then replaced by the hub, so the wrong screen was visible at
// first paint.
//
// This is the rule, and it is the thing most likely to be re-broken by a
// well-meaning later change: after the first paint, nothing in this file may
// write location.hash except in direct response to a tap or a keypress.
const HOME = "hub";

let current = null;
let unmount = null;
let fastTimer = null;

function route() {
  const id = (location.hash || "#" + HOME).slice(1).split("/")[0];
  return VIEWS.find((v) => v.id === id) || VIEWS.find((v) => v.id === HOME);
}

const tabOf = (v) => (v && v.tab) || null;
const hiddenView = (v) => !!(v.ai && !store.aiOn);

// The one piece of history the app keeps.
//
// Eight screens deep-link across tabs — scan to codes, concerns to history,
// codes to report and so on — and with a tab bar those jumps silently change
// which tab is lit with nothing to say how you got there. So: one entry, set
// only when the origin and the destination are in different tabs, rendered as
// a chip and cleared the moment you tap anything in the bar. Not
// history.back(), which points at the wrong thing after a tab tap and does
// not exist at all in kiosk mode.
let cameFrom = null;
let barTap = false;

// Every navigation the bar itself initiates goes through here, so the back
// chip is cleared in one place rather than in five handlers.
function goto(id) {
  cameFrom = null;
  if (location.hash === "#" + id) { paintNavState(); return; }
  barTap = true;
  location.hash = "#" + id;
}

function go() {
  const view = route();
  const from = current;
  if (from) noteManualNav(from.id, view.id);
  cameFrom = (!barTap && from && tabOf(from) !== tabOf(view)) ? from : null;
  barTap = false;

  const stage = document.getElementById("stage");
  if (unmount) { try { unmount(); } catch { /* a view that fails to clean up must not block the next one */ } }
  unmount = null;
  clear(stage);
  current = view;
  paintNavState();

  const wrap = h("div.wrap");
  stage.appendChild(wrap);
  stage.scrollTop = 0;
  try {
    unmount = view.mount(wrap, { arg: (location.hash || "").split("/")[1] || null }) || null;
  } catch (e) {
    wrap.appendChild(h("div.card.tint-bad",
      h("div.title", "That view failed to draw"),
      h("p.lede", String(e && e.message || e)),
      h("p.muted", "Everything else still works. The terminal has the same data: omacar doctor")));
    console.error(e);
  }

  // Focus follows the content, but only when the bar did not send us here.
  //
  // A view's own button — "Open the scan", a fault row — is destroyed by the
  // navigation it triggers, and focus then falls to <body>, so the next Tab
  // starts again from the top of the document. Moving it to the stage puts a
  // keyboard user at the top of what they just asked for. When the tap came
  // from the tab bar the button survives, and stealing focus off it would
  // stop anybody tabbing along the bar.
  const bar = document.getElementById("navbar");
  if (!bar || !bar.contains(document.activeElement)) stage.focus();

  // Only poll fast for views that show a live value.
  //
  // AND DROP THE LAST SAMPLE ON THE WAY OUT. store.sample falls back to
  // `this.live || car.live`, and nothing else ever clears `live` -- so a
  // screen without the fast flag inherited whatever the last fast screen left
  // behind and kept it for as long as somebody stayed there. That is not a
  // lag, it is a latch, and it ran in both directions: drive, then park and
  // open Resets, and the frozen sample still said you were moving, so the
  // screen stayed locked and a service reset could not be run at all; or stop
  // at a light, open Resets, pull away, and it stayed unlocked at 30 mph.
  // Clearing it makes the fallback the twenty-second snapshot, which is old
  // but honest about being old.
  clearInterval(fastTimer);
  fastTimer = null;
  if (view.fast) {
    store.refreshLive();
    fastTimer = setInterval(() => store.refreshLive(), 250);
  } else {
    store.live = null;
  }
  document.title = `OmaCar — ${view.title}`;
}

// ------------------------------------------------------------- the tab bar
//
// BUILT ONCE, AND AFTERWARDS ONLY UPDATED.
//
// The vehicle bar used to be cleared and rebuilt on every live sample, and a
// tap whose pointerdown and pointerup straddle a rebuild fires its click on
// the nearest surviving ancestor rather than on the button — so the tap
// simply does not happen, about a quarter of the time, at random. Navigation
// is the last thing in the app that can afford that. Every persistent control
// below is constructed once; after that only text, `hidden` and state
// attributes change.
const els = {};

function buildNav() {
  if (els.tabs) return;
  const bar = document.getElementById("tabbar");
  els.tabs = new Map();
  for (const t of TABS) {
    const pip = h("span.pip", { hidden: true });
    const btn = h("button.tab", {
      type: "button",
      onclick: () => goto(t.views[0].id),
    }, h("span.tab-in", icon(t.icon, 22), h("span.tab-lbl", t.label)), pip);
    bar.appendChild(btn);
    els.tabs.set(t.id, { btn, pip });
  }

  // The left slot of the chip row. One button, reused: it is either the back
  // chip or the connection chip, and which of the two is a question of text
  // rather than of which node exists.
  els.lead = h("button.chip.chip-lead", {
    type: "button", style: { visibility: "hidden" },
    onclick: () => { if (els.lead.dataset.go) goto(els.lead.dataset.go); },
  });
  document.getElementById("subbar-lead").appendChild(els.lead);
  els.chips = document.getElementById("subbar-chips");
  els.here = h("span.subbar-here", { hidden: true });
  els.chips.appendChild(els.here);
}

// Badges roll up from the sub-screens to the tab, and only where there is
// something real to count. The snapshot knows about faults, readiness and
// service; it does not know whether a recording or an AI job is running, and
// a badge that is decoration is worse than no badge at all — so Data and AI
// carry none until the store grows the fact.
function tabBadge(id) {
  const car = store.car || {};
  if (id === "car") return store.connected ? { text: "", tone: "ok" } : null;
  if (id === "faults") {
    const n = (car.active_faults || []).length;
    if (n) return { text: String(n), tone: "bad" };
    if (car.readiness && !car.readiness.ready) return { text: "!", tone: "warn" };
    return null;
  }
  if (id === "care") {
    const due = car.service && car.service.due ? car.service.due : 0;
    return due ? { text: String(due), tone: "warn" } : null;
  }
  return null;
}

let chipsFor = null;
let scrolledChip = null;

function paintNavState() {
  buildNav();
  const here = current;
  const tab = tabOf(here);

  for (const t of TABS) {
    const { btn, pip } = els.tabs.get(t.id);
    // A tab whose only screen is the advisor disappears when there is no AI
    // available, rather than leading somewhere that apologises.
    btn.hidden = t.views.every(hiddenView);
    if (tab === t.id) btn.setAttribute("aria-current", "page");
    else btn.removeAttribute("aria-current");
    const b = tabBadge(t.id);
    pip.hidden = !b;
    if (b) {
      pip.textContent = b.text;
      pip.className = "pip " + b.tone + (b.text ? "" : " bare");
    }
  }

  // The chips are rebuilt when the tab changes and not otherwise: inside a
  // tab only their state moves, and replacing a row of buttons under a
  // travelling thumb is the bug described above.
  if (chipsFor !== tab) {
    chipsFor = tab;
    for (const old of els.chips.querySelectorAll(".chip[data-view]")) old.remove();
    const t = TABS.find((x) => x.id === tab);
    for (const v of (t ? t.views : [])) {
      if (hiddenView(v)) continue;
      els.chips.appendChild(h("button.chip", {
        type: "button",
        // data-write marks the chips that can ever lock, so the stylesheet
        // reserves the lock glyph's space on those and only those. A chip
        // that cannot lock is not padded for a state it will never reach.
        data: v.write ? { view: v.id, write: "1" } : { view: v.id },
        onclick: () => goto(v.id),
      }, v.label));
    }
  }

  // A screen that is off the navigation names itself here instead of lighting
  // a tab it does not belong to. Pretending the vehicle report is "in" Faults
  // would put the app back in the business of showing you a position it made
  // up, which is what the rail did.
  els.here.hidden = !(here && here.off);
  if (here && here.off) els.here.textContent = here.title;

  const driving = store.state === "driving";
  for (const c of els.chips.querySelectorAll(".chip[data-view]")) {
    const v = VIEWS.find((x) => x.id === c.dataset.view);
    if (here && here.id === v.id) c.setAttribute("aria-current", "page");
    else c.removeAttribute("aria-current");
    // Write-capable screens grey out while the car is moving; they never
    // vanish. A control that disappears is a control somebody hunts for at
    // 60 mph, which is worse than a control they cannot press.
    //
    // THE LABEL DOES NOT CHANGE, and that is the whole point of the lock
    // glyph. Appending " · stopped only" took "Resets" from six characters to
    // twenty-one -- about 124px -- so the three chips after it slid sideways
    // the instant SPEED crossed 3 and slid back when it dropped. Speed is
    // noisy at walking pace, so that fires exactly at the traffic light where
    // somebody is reaching for a chip. A box that changes size under a thumb
    // is the same defect as a button rebuilt under a thumb. The reason lives
    // in the title and in aria-disabled, where it can be read at a stop
    // without moving anything.
    const block = !!v.write && driving;
    c.disabled = block;
    c.classList.toggle("chip-locked", block);
    c.setAttribute("aria-disabled", block ? "true" : "false");
    if (c.title !== (block ? "Available when you stop" : "")) {
      c.title = block ? "Available when you stop" : "";
    }
    // Guarded: this runs four times a second, and replacing a text node that
    // has not changed is how the chip row stops accepting a fling.
    if (c.firstChild && c.firstChild.nodeValue !== v.label) {
      c.firstChild.nodeValue = v.label;
    } else if (!c.firstChild) {
      c.textContent = v.label;
    }
  }
  // Only when the current chip actually changed. This runs four times a
  // second while the car is connected, and scrolling a container under a
  // finger that is flinging it is the same class of defect as rebuilding a
  // button under a thumb -- the row would simply refuse to be scrolled.
  if (scrolledChip !== (here && here.id)) {
    scrolledChip = here && here.id;
    const cur = els.chips.querySelector('.chip[aria-current="page"]');
    if (cur) cur.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  paintLead();
}

// The left slot: what you were reading, or that the car is here. Never a jump.
function paintLead() {
  const lead = els.lead;
  // NOTE ON hidden VERSUS visibility, because the difference is the bug.
  //
  // `hidden` is display:none here, so an empty slot collapsed to nothing and
  // the chip appearing shoved the whole row sideways by its own width -- 261px
  // for "Car connected — open the hub". records.py ages a stale sample out
  // after fifteen seconds, so a flaky adapter at a set of lights did that
  // repeatedly while somebody was reading a fault code: you reach for Scan,
  // the row moves, you land on Readiness. The slot is a fixed width in the
  // stylesheet now and the button is hidden with visibility, which keeps the
  // geometry identical whether or not there is anything in it.
  if (cameFrom) {
    lead.style.visibility = "visible";
    lead.dataset.go = cameFrom.id;
    lead.textContent = "← " + cameFrom.label;
    lead.classList.remove("chip-link");
    return;
  }
  // The adapter answering is news, not an instruction. It used to move the
  // user; now it offers, and the offer stops as soon as they are on a car
  // screen or have said with a tap that they would rather be elsewhere.
  const want = auto.mode === "connect" ? store.connected
    : auto.mode === "moving" ? (store.values.SPEED || 0) > 3 : false;
  const show = want && tabOf(current) !== "car" && !overridden;
  lead.style.visibility = show ? "visible" : "hidden";
  if (show) {
    lead.dataset.go = HOME;
    lead.textContent = auto.mode === "moving" ? "Car moving — open the hub"
                                              : "Car connected — open the hub";
    lead.classList.add("chip-link");
  }
}

// ------------------------------------------------------- the vehicle bar
//
// The vehicle identity never leaves the screen: every scan tool that has ever
// shown somebody the wrong car's data got there by letting it.
function buildBar() {
  if (els.name) return;
  const bar = document.getElementById("vbar");
  els.name = h("div.name", "OmaCar");
  els.sub = h("div.sub", "connecting…");
  bar.appendChild(h("div.id", els.name, els.sub));
  bar.appendChild(h("div.spacer"));

  // Visible, not subtle. The failure that matters is thinking you are private
  // when you are not, and this is the one place always in frame.
  els.priv = h("button.pill.info.privacy-pill", {
    type: "button", hidden: true,
    title: "Identifying details are hidden. Tap to show them again.",
    onclick: () => { privacy.on = false; paintBar(); go(); },
  }, "VIN hidden");
  bar.appendChild(els.priv);

  els.sim = h("span.pill.info", { hidden: true }, "simulated car");
  bar.appendChild(els.sim);

  els.odo = h("span.odo");
  els.odoWrap = h("div.stat", { hidden: true }, h("span.muted", "ODOMETER"), els.odo);
  bar.appendChild(els.odoWrap);

  els.dot = h("span.dot");
  els.stateTxt = h("span");
  bar.appendChild(h("div.stat", els.dot, els.stateTxt));

  els.proto = h("span.muted");
  bar.appendChild(h("div.stat", els.proto));

  bar.appendChild(h("button.vbar-btn", {
    type: "button", id: "btn-settings",
    "aria-label": "Settings", title: "Units, privacy, learn mode",
    onclick: openSettings,
  }, icon(GEAR, 20)));
}

function paintBar() {
  buildBar();
  const car = store.car;
  if (!car) {
    els.name.textContent = "OmaCar";
    els.sub.textContent = "connecting…";
    return;
  }
  els.name.textContent = car.name || "Unknown vehicle";
  els.sub.textContent = [car.vehicle && car.vehicle.trim, car.vehicle && car.vehicle.engine,
                         maskVin(car.vehicle && car.vehicle.vin)].filter(Boolean).join("  ·  ");

  els.priv.hidden = !privacy.on;
  els.sim.hidden = !car.simulated;
  els.odoWrap.hidden = !car.odometer;
  if (car.odometer) els.odo.textContent = dist(car.odometer);

  const state = store.state;
  const tone = state === "driving" ? "ok" : state === "idling" ? "warn"
    : state === "parked" ? "info" : "";
  els.dot.className = "dot" + (tone ? " " + tone : "") + (state === "driving" ? " live" : "");
  els.stateTxt.textContent = state === "driving"
    ? `${Math.round((store.values.SPEED || 0) * U.units.km)} ${U.units.speed}`
    : state;
  els.proto.textContent = car.live && car.live.protocol ? car.live.protocol : "no link";
}

// ------------------------------------------------------- the settings sheet
//
// Where the rail's three orphaned foot buttons went. Learn, the privacy pill
// and the unit toggle were pinned to the bottom of a navigation strip that no
// longer exists, and they were the only three controls in the app with no
// screen of their own. This is a home for them and for anything else that is
// a preference rather than a destination — not a settings SYSTEM, which is a
// different piece of work.
const GEAR = [
  "M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4z",
  "M19.1 13.9a1.5 1.5 0 0 0 .3 1.7l.1.1a1.9 1.9 0 1 1-2.7 2.7l-.1-.1a1.5 1.5 0 0 0-2.5 1.1v.2a1.9 1.9 0 0 1-3.8 0v-.1a1.5 1.5 0 0 0-2.5-1.1l-.1.1a1.9 1.9 0 1 1-2.7-2.7l.1-.1a1.5 1.5 0 0 0-1.1-2.5h-.2a1.9 1.9 0 0 1 0-3.8h.1a1.5 1.5 0 0 0 1.1-2.5l-.1-.1a1.9 1.9 0 1 1 2.7-2.7l.1.1a1.5 1.5 0 0 0 2.5-1.1v-.2a1.9 1.9 0 0 1 3.8 0v.1a1.5 1.5 0 0 0 2.5 1.1l.1-.1a1.9 1.9 0 1 1 2.7 2.7l-.1.1a1.5 1.5 0 0 0 1.1 2.5h.2a1.9 1.9 0 0 1 0 3.8h-.1a1.5 1.5 0 0 0-1.4.9z",
];

function openSettings() {
  const host = document.getElementById("modal-host");
  const close = () => {
    host.hidden = true; clear(host); host.onclick = null;
    document.removeEventListener("keydown", esc);
  };
  const esc = (e) => { if (e.key === "Escape") close(); };

  // A row is a whole-width target with its state on the right, because a
  // 56px strip you can hit with a thumb beats a neat 30px switch you cannot.
  const row = (label, note, value, onclick, on) =>
    h("button.sheet-row", { type: "button", onclick, "aria-pressed": on === undefined ? null : String(!!on) },
      h("span.sheet-l", h("span.sheet-lab", label), note ? h("span.sheet-note", note) : null),
      h("span.sheet-v" + (on ? ".on" : ""), value));

  const rows = h("div.sheet-rows");
  const redraw = () => {
    clear(rows);

    rows.appendChild(row("Units", "The server owns this, so the dock card and the terminal follow",
      U.units.dist, async (e) => {
        // Written to the server rather than to this browser, because the CLI,
        // the dock card and this app all read the same file. Flipping it here
        // is why the dock card changes with the app instead of drifting.
        const btn = e.currentTarget;
        const next = U.imperial ? "metric" : "imperial";
        btn.disabled = true;
        btn.querySelector(".sheet-v").textContent = "changing…";
        try {
          await api.setUnits(next);
          await store.refreshCar();
          redraw();
          go();
          toast(`Now in ${next === "imperial" ? "miles" : "kilometres"}. The dock card and the terminal follow.`);
        } catch (err) {
          redraw();
          toast("Could not change units: " + (err.message || err), "bad");
        }
      }));

    rows.appendChild(row("Hide the VIN and plate", "For photographs and for film",
      privacy.on ? "Hidden" : "Visible", () => {
        const now = privacy.toggle();
        redraw();
        paintBar();
        go();
        toast(now ? "VIN, plate and name hidden — safe to photograph."
                  : "Identifying details visible again.");
      }, privacy.on));

    rows.appendChild(row("Learn mode", "Explains the terms in place, and hides nothing",
      learn.on ? "On" : "Off", () => { learn.toggle(); redraw(); go(); }, learn.on));

    rows.appendChild(row("Learn the car, and the app", "The topics, in one place",
      "Open", () => { close(); goto("learn"); }));

    rows.appendChild(row("Vehicle report", "The page you print for somebody else",
      "Open", () => { close(); goto("report"); }));

    // Plugin screens that did not say where they belong. Dropping them
    // silently into a tab would be guessing on the plugin author's behalf;
    // listing them here means they are reachable and honest about being
    // extras until a manifest names a tab for them.
    for (const v of VIEWS.filter((x) => x.plugin)) {
      rows.appendChild(row(v.label, v.title, "Open", () => { close(); goto(v.id); }));
    }
  };
  redraw();

  const sheet = h("div.sheet", { role: "dialog", "aria-modal": "true", "aria-label": "Settings" },
    h("div.sheet-head", h("div.title", "Settings"),
      h("button.btn.right", { type: "button", onclick: close }, "Done")),
    rows);

  clear(host); host.appendChild(sheet); host.hidden = false;
  host.onclick = (e) => { if (e.target === host) close(); };
  document.addEventListener("keydown", esc);
  const first = sheet.querySelector(".sheet-row");
  if (first) first.focus();
}

// ------------------------------------------------------------- automatic drive
//
// On a tablet on a dashboard the app should already be showing the right
// thing when you get in. It used to do that by moving you: the hash went to
// the hub when the adapter answered and to the overview when it dropped, on
// every live sample. That is fine exactly once, at boot, and infuriating
// every other time — a re-link at a set of lights took you off the fault code
// you were reading, and there is no way to distinguish "the link came up" from
// "the link came up again".
//
// So the boot case is handled by HOME, which needs no timer and no store
// event, and everything after it is an offer in the chip row. Nothing below
// writes location.hash.
let auto = { mode: "connect", back: true };
let wasConnected = null;
let overridden = false;

function autoDrive() {
  const connected = store.connected;

  // A fresh link is a fresh decision, so the offer comes back. `overridden`
  // now suppresses a chip rather than a navigation, which is the same
  // question asked at a volume the user can ignore.
  if (connected && wasConnected === false) overridden = false;

  wasConnected = connected;
  // The badges, the greyed write-capable chips and the offer chip all read
  // the live sample, so the whole bar state is refreshed here rather than in
  // three subscriptions that could disagree about when.
  paintNavState();
}

// Leaving car mode by hand while the car is still connected is a decision,
// and it sticks. Arriving there by hand is not an override.
function noteManualNav(fromId, toId) {
  // Moving BETWEEN the hub and the gauges is not leaving car mode -- both are
  // car screens, and treating a tap on "Gauges" as an override would stop the
  // app ever offering to bring you back.
  const carScreens = (id) => id === HOME || id === "drive";
  if (carScreens(fromId) && !carScreens(toId) && store.connected) overridden = true;
  if (carScreens(toId)) overridden = false;
}

// Arriving with a view already named in the URL is somebody asking for that
// view — a bookmark, a link from the dock card, a deep link out of another
// screen. Auto-drive is for the case where no view was asked for; it must not
// override one that was.
function honourInitialView() {
  const asked = (location.hash || "").slice(1).split("/")[0];
  if (asked && asked !== "drive" && VIEWS.some((v) => v.id === asked)) {
    overridden = true;
  }
}

export async function loadAuto() {
  try {
    const l = await api.driveLayout();
    auto = { mode: l.auto || "connect", back: l.auto_return !== false };
  } catch { /* the default is sensible and the app must start regardless */ }
}

// ---------------------------------------------------------------- the theme
//
// The palette comes from Omarchy, not from this app. An application on this
// desktop that ships its own colours is a guest who turned up in its own
// clothes — and when the theme changes, everything else on screen changes with
// it and a tool that did not would look broken rather than distinctive.
let themeStamp = -1;

async function applyTheme() {
  try {
    const { stamp, vars } = await api.theme();
    if (stamp === themeStamp) return;
    themeStamp = stamp;
    const root = document.documentElement;
    for (const [k, v] of Object.entries(vars)) {
      if (k === "mode") { root.style.colorScheme = v; continue; }
      root.style.setProperty("--" + k, v);
    }
    root.dataset.mode = vars.mode || "dark";
  } catch {
    // The stylesheet's own palette is the fallback, and it is the one the app
    // was designed against — so a missing theme is a non-event.
  }
}

async function boot() {
  window.addEventListener("hashchange", go);

  // The snapshot poller is the only clock running when no view is asking for
  // live samples, so both clocks drive the same repaint. autoDrive() ends in
  // paintNavState(), so the bar is correct on whichever tick arrives first.
  store.on("car", () => { paintBar(); autoDrive(); });
  store.on("live", () => { paintBar(); autoDrive(); });

  honourInitialView();
  // Before the first paint. A night-red look that arrives a beat late is a
  // flash of full-brightness white at the exact moment it matters most.
  applyLook(savedLook());

  await Promise.all([store.boot(), applyTheme(), loadAuto()]);
  document.getElementById("app").dataset.booting = "0";
  // Plugin screens, loaded at boot.
  //
  // Dynamically imported rather than bundled, because there is no bundler and
  // a plugin is a directory somebody dropped in. Each one is loaded on its own
  // and a failure is contained: a plugin that throws on import loses its own
  // screen and nothing else, which is the difference between a broken plugin
  // and a broken application.
  //
  // They arrive with no tab, so they land in OFF_NAV's territory — routable,
  // listed in the Settings sheet, and not silently pushed into somebody
  // else's section. A manifest that names a tab is what would change that.
  try {
    const r = await api.plugins();
    for (const v of r.views || []) {
      try {
        const mod = await import(v.src);
        if (typeof mod.default !== "function") continue;
        VIEWS.push({ id: v.id, label: v.label, title: v.title,
                     mount: mod.default, plugin: true, off: "a plugin screen" });
      } catch (e) {
        console.warn("plugin view failed to load:", v.id, e);
      }
    }
  } catch { /* no plugins, or the endpoint is unavailable */ }

  paintBar();
  go();

  // First run. After the app has drawn, not before: opening on a blank page
  // makes it look like the tour IS the application, and the point of the tour
  // is to describe the thing behind it.
  if (!onboard.done) {
    showOnboarding(document.getElementById("modal-host"), { onClose: go });
  }

  setInterval(() => store.refreshCar(), 20000);
  // Cheap: one stat on the server and a no-op unless the theme actually moved.
  setInterval(applyTheme, 5000);
  // The layout — and with it the auto-drive rule — can be changed from another
  // window or another device, so it is re-read rather than assumed.
  setInterval(loadAuto, 15000);

  // Keyboard: the digits are the five tabs, in the order they are on screen,
  // the way a tablet's hard keys would be. They used to index the nineteen-row
  // view table, which meant a stray digit in a continuous take navigated to
  // something nobody could name; 6 and above now do nothing on purpose.
  //
  // The guard covers <select> as well as text fields — documents.js has one,
  // and typing to jump through its options otherwise moved the whole app.
  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, textarea, select, [contenteditable]")) return;
    const n = parseInt(e.key, 10);
    if (n >= 1 && n <= TABS.length) {
      const t = TABS[n - 1];
      if (!t.views.every(hiddenView)) goto(t.views[0].id);
      return;
    }
    if (e.key === "r" && !e.metaKey && !e.ctrlKey) { store.refreshCar(); toast("Refreshed"); }
  });
}

boot().catch((e) => {
  document.getElementById("app").dataset.booting = "0";
  document.getElementById("stage").appendChild(
    h("div.wrap", h("div.card.tint-bad",
      h("div.title", "OmaCar could not start"),
      h("p.lede", String(e && e.message || e)),
      h("p.muted", "Is the server running? Try: omacar server status"))));
});
