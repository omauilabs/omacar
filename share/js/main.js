// Boot, the navigation, the vehicle bar, and the router.
//
// Two clocks. The snapshot — codes, service, a year of driving — changes on
// the scale of minutes and is polled slowly. The live sample changes five
// times a second and is polled fast, but only while a view that shows it is
// on screen. A diagnostic tool that hammers the daemon while you read a
// service schedule is a tool that gets in the way of the thing it is watching.

import { h, clear, icon, store, U, api, toast, confirmDialog, grouped, since, withToken } from "./core.js";
import { record } from "./trail.js";

import { ICONS } from "./icons.js";
import { learn } from "./learn.js";
import { savedLook, saveLook, applyLook, nextLook, lookById } from "./looks.js";
import { privacy } from "./privacy.js";
import { onboard, showOnboarding } from "./onboard.js";
import home from "./views/home.js";
import vehicleView from "./views/vehicle.js";
import documentsView from "./views/documents.js";
import themesView from "./views/themes.js";
import replayView from "./views/replay.js";
import resetsView from "./views/resets.js";
import learnView from "./views/learnview.js";
import imaView from "./views/ima.js";
import launcherView from "./views/launcher.js";
import effectsView from "./views/effects.js";
import nurseryView from "./views/nursery.js";
import { createOmaPlay } from "./omaplay/layer.js";
import { mockSource, dongleSource } from "./omaplay/source.js";

// OmaPlay is a LAYER, not a view.
//
// Every other entry in VIEWS is mounted into the stage and destroyed the
// moment you navigate away, which is right for a page of gauges and wrong
// here: unmounting OmaPlay would stop the audio, and a design where checking
// your coolant temperature costs you the podcast is one nobody uses twice.
//
// So it is created once, mounted as a sibling of the stage in the same grid
// cell, and the "view" below only changes its layout mode. `hidden` is not
// `stopped`, and that distinction is the entire point.
//
// Lazily, though: somebody who never opens it should not pay for the source's
// timers or the gauge rail's subscription to the live store.
let _omaplay = null;

// WHICH OF THE THREE THINGS THE PHONE SCREEN SHOWS.
//
// An adapter if one is plugged in; a recording if one has been made; the mock
// otherwise. The mock is last because it is the only one of the three that
// cannot ever become a phone, and it is the one every earlier version showed
// unconditionally.
//
// This asks the server rather than deciding, because the server is the only
// party that can see a USB device or a file on disk, and a screen that guessed
// would be wrong in exactly the case that matters. If the ask fails at all,
// the mock is the safe answer: it is the one that cannot pretend.
async function pickSource({ onlyReal } = {}) {
  try {
    const r = await fetch(withToken("/api/phone"), { cache: "no-store" });
    if (!r.ok) return mockSource();
    const it = await r.json();
    // Somebody already started something -- `omacar phone replay` from a
    // terminal, or another tab. Deliberate beats every rule below it.
    if (it.streaming) {
      return dongleSource({ mode: it.mode === "replay" ? "replay" : "dongle" });
    }
    if (it.dongles && it.dongles.length) return dongleSource({ mode: "dongle" });
    // A RECORDING IS NOT STARTED UNTIL SOMEBODY LOOKS AT IT. An adapter is
    // worth connecting the moment the app boots, because a phone screen that
    // is not being watched is still playing music. A canned clip is not: it
    // would decode a test pattern on a loop, in the background, for the whole
    // of a drive, on a tablet with no fan.
    if (it.replay && !onlyReal) return dongleSource({ mode: "replay" });
  } catch { /* no server, no adapter, no recording */ }
  return mockSource();
}

// Swap the mock for something real, if there is something real. Safe to call
// again: it does nothing unless what is on screen is the mock, so it cannot
// interrupt a phone that is already connected.
let _upgrading = false;

function upgrade(opts) {
  if (_upgrading || !_omaplay) return;
  if (_omaplay.source && _omaplay.source.kind !== "mock") return;
  _upgrading = true;
  pickSource(opts).then((src) => {
    if (!_omaplay || !src || src.kind === "mock") return;
    _omaplay.setSource(src);
    _omaplay.start();
  }).catch(() => { /* the mock is already up */ })
    .finally(() => { _upgrading = false; });
}

function omaplay() {
  if (!_omaplay) {
    _omaplay = createOmaPlay();
    _omaplay.mount(document.getElementById("app"));
    // The mock starts immediately so the layout is never empty, and is
    // replaced the moment the server says there is something better. Waiting
    // on the ask would put a blank rectangle in front of the driver for as
    // long as a USB enumeration takes.
    _omaplay.setSource(mockSource());
    _omaplay.start();
    upgrade({ onlyReal: true });
    // The car's own alerts, over the phone screen. See the note on
    // watchAlerts(): this is the argument for combining the two products at
    // all, and it was dead code until it was called.
    _omaplay.watchAlerts();
  }
  return _omaplay;
}

function omaplayView(root, { arg } = {}) {
  omaplay().setMode(["full", "split", "pip"].includes(arg) ? arg : "split");
  // ASKED AGAIN EVERY TIME THE SCREEN IS OPENED. The first ask happens when the
  // app boots, which on a tablet is before anybody has plugged anything in --
  // so an adapter connected afterwards would have shown a mock with MOCK
  // written across it and a perfectly good phone attached.
  upgrade();
  return () => { if (_omaplay) _omaplay.setMode("hidden"); };
}
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
import writeView from "./views/write.js";
import musicView from "./views/music.js";
import report from "./views/report.js";
import drive from "./views/drive.js";
import concernsView from "./views/concerns.js";
import live from "./views/live.js";
import navigationView from "./views/navigation.js";
import camerasView from "./views/cameras.js";
import workView from "./views/work.js";
import { badge } from "./provenance.js";


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
// Vehicle groups its screens (Overview · Diagnose · Live · Hybrid · Care ·
// Car); every other tab lists them.
const TABS = [
  { id: "home", label: "Home", icon: ICONS.home,
    views: [
      { id: "home", label: "Home", title: "Home", mount: home, fast: true },
    ] },
  { id: "navigation", label: "Navigation", icon: ICONS.nav,
    views: [
      { id: "navigation", label: "Navigation", title: "Navigation", mount: navigationView },
    ] },
  { id: "cameras", label: "Cameras", icon: ICONS.camera,
    views: [
      { id: "cameras", label: "Cameras", title: "Cameras", mount: camerasView },
    ] },
  { id: "vehicle", label: "Vehicle", icon: ICONS.vehicle,
    groups: [
      { id: "overview", label: "Overview", views: [
        { id: "vehicle", label: "Overview", title: "Vehicle diagnostics", mount: vehicleView, fast: true },
      ] },
      { id: "diagnose", label: "Diagnose", views: [
        { id: "codes",  label: "Codes",     title: "Trouble codes",                mount: codes, fast: true },
        { id: "scan",   label: "Scan",      title: "Full system scan",             mount: scan },
        { id: "health", label: "Readiness", title: "Readiness and on-board tests", mount: health },
      ] },
      { id: "live", label: "Live", views: [
        { id: "drive",  label: "Gauges",   title: "Drive mode",              mount: drive,      fast: true },
        { id: "live",   label: "Cluster",  title: "Cluster",                 mount: live,       fast: true },
        { id: "data",   label: "Live lab", title: "Data lab",                mount: data,       fast: true },
        { id: "replay", label: "Replay",   title: "Replay a recorded drive", mount: replayView, tier: "power" },
        // Actuator commands. Two taps from the bar, never one, and the chip
        // greys out with the reason on it while the car is moving.
        { id: "tests",  label: "Tests",    title: "Functional tests",        mount: tests, fast: true, write: true, tier: "technician" },
        { id: "write",  label: "Identifiers", title: "Write by identifier",  mount: writeView, write: true, tier: "god" },
      ] },
      // NOT BEHIND A TIER. "How is my battery" is an owner's question.
      { id: "hybrid", label: "Hybrid", views: [
        { id: "ima", label: "Battery", title: "Battery, motor and regen", mount: imaView },
      ] },
      { id: "care", label: "Care", views: [
        { id: "service",   label: "Service", title: "Service schedule",                    mount: service },
        { id: "resets",    label: "Resets",  title: "Service resets and functional tests", mount: resetsView, write: true, fast: true, tier: "technician" },
        { id: "concerns",  label: "Trends",  title: "Areas of concern",                    mount: concernsView, tier: "power" },
        { id: "history",   label: "Log",     title: "Drive history and records",           mount: history },
        { id: "documents", label: "Docs",    title: "Receipts, registrations and records", mount: documentsView },
      ] },
      { id: "car", label: "Car", views: [
        { id: "garage", label: "Profile", title: "Every car you own", mount: garageView },
        { id: "dash",   label: "Summary", title: "Overview",          mount: dash, fast: true },
        { id: "report", label: "Report",  title: "Vehicle report",    mount: report },
      ] },
    ] },
  { id: "agent", label: "Agent", icon: ICONS.agent,
    views: [
      { id: "advisor", label: "Car",  title: "Oma Agent", mount: advisor, ai: true },
      { id: "work",    label: "Work", title: "Work",      mount: workView },
    ] },
];

// One shape downstream: a tab that lists its views is a tab with one group.
for (const t of TABS) {
  t.grouped = !!t.groups;
  if (!t.groups) t.groups = [{ id: t.id, label: t.label, views: t.views }];
  t.views = t.groups.flatMap((g) => g.views.map((v) => Object.assign(v, { tab: t.id, group: g.id })));
}

// OFF THE NAVIGATION, ON PURPOSE — but still routable.
//
// `learn` is a mode more than a screen; its topics open from the Settings
// sheet, which is where the mode's own switch now lives too. Every entry here
// keeps a working URL so a bookmark, a dock card or an old deep link still
// lands somewhere rather than on a fallback.
//
// A view here declares `off` with the reason. `askable` marks the ones the
// voice assistant may still open (see `screens()`). Anything reachable only
// from inside another screen belongs in this list and nowhere else.
const OFF_NAV = [
  { id: "learn",  label: "Learn",  title: "Learn the car, and the app",
    mount: learnView, off: "opened from Settings" },
  { id: "themes", label: "Themes", title: "Build a palette of your own",
    mount: themesView, off: "opened from Settings" },
  { id: "effects", label: "Effects", title: "Scanner and Leviathan",
    mount: effectsView, off: "opened from Settings", askable: true },
  // The power-on screen: a destination the tablet is pointed at, and Home's
  // "Begin" button when the car is off. Never something to browse to mid-drive.
  { id: "launcher", label: "Begin", title: "Ready to drive",
    mount: launcherView, off: "opened from Home when the car is off", hidden: true },
  { id: "omaplay", label: "Phone", title: "Your phone, and the car",
    mount: omaplayView, fast: true, off: "opened from Home's phone card", askable: true },
  { id: "music",  label: "Music", title: "Music",
    mount: musicView, fast: true, off: "opened from Home's phone card", askable: true },
  { id: "nursery", label: "Nursery", title: "The baby, from the road",
    mount: nurseryView, nursery: true, fast: true, off: "opened from Home's phone card", askable: true },
];

// Old addresses that still have to land somewhere: a bookmark, the dock card,
// the board. #hub was the home screen for a year.
const ALIASES = {
  hub: "home",
};

// Tab membership is stamped onto the view objects themselves rather than kept
// in a parallel lookup, so there is exactly one copy of the fact and a view
// found by the router already knows where it lives.
const VIEWS = [
  ...TABS.flatMap((t) => t.views),
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
const HOME = "home";

// THE MODE, WHICH THE SERVER DECIDES AND THIS FILE ONLY REFLECTS.
//
// A view carries `tier` when it is not for everybody. The registry above is
// still the only list -- adding a mode did not add a second table to keep in
// step, which is the whole reason the tier is a property on the view rather
// than a set of ids somewhere else.
//
// Hiding a screen is a courtesy, NOT a boundary. The boundary is lib/modes.py,
// asked by api.py before any write route runs, so posting straight at the API
// with the app in simplified mode is refused by the server whatever the
// browser believes. If this were the only check it would be worth nothing.
const TIER_RANK = { simplified: 0, power: 1, technician: 2, god: 3 };
let tier = "power";

function tierAllows(v) {
  if (!v.tier) return true;
  return (TIER_RANK[tier] ?? 1) >= (TIER_RANK[v.tier] ?? 0);
}

async function loadMode() {
  try {
    const d = await api.mode();
    if (d && d.tier) {
      tier = d.tier;
      document.documentElement.dataset.tier = tier;
    }
  } catch { /* an older server, or none: everything stays visible */ }
}

let current = null;
let unmount = null;
let fastTimer = null;

function route() {
  const asked = (location.hash || "#" + HOME).slice(1).split("/")[0];
  const id = ALIASES[asked] || asked;
  return VIEWS.find((v) => v.id === id) || VIEWS.find((v) => v.id === HOME);
}

const tabOf = (v) => (v && v.tab) || null;
const hiddenView = (v) => !!(v.ai && !store.aiOn)
                       || !!(v.nursery && !store.nurseryOn)
                       // A destination rather than a place to browse to. The
                       // launcher is what the tablet is POINTED at on power-on;
                       // a tab for it mid-drive is a button that stops the car
                       // being read in order to check whether it can be read.
                       || !!v.hidden
                       || !tierAllows(v);

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
  paintBar();

  const wrap = h("div.wrap", { data: { view: view.id } });
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
  els.seg = document.getElementById("segbar");
}

// Badges roll up from the sub-screens to the tab, and only where there is
// something real to count. The snapshot knows about faults, readiness and
// service; it does not know whether a recording or an AI job is running, and
// a badge that is decoration is worse than no badge at all — so Data and AI
// carry none until the store grows the fact.
function tabBadge(id) {
  const car = store.car || {};
  if (id === "home") return store.connected ? { text: "", tone: "ok" } : null;
  if (id === "vehicle") {
    const n = (car.active_faults || []).length;
    if (n) return { text: String(n), tone: "bad" };
    if (car.readiness && !car.readiness.ready) return { text: "!", tone: "warn" };
    const due = car.service && car.service.due ? car.service.due : 0;
    return due ? { text: String(due), tone: "warn" } : null;
  }
  return null;
}

let chipsFor = null;
let segFor = null;
let scrolledChip = null;

// What the chip row offers for a tab: its groups (by their first visible
// screen), or its screens when it has no groups and more than one of them.
function chipItems(t) {
  if (!t) return [];
  if (t.grouped) {
    return t.groups
      .map((g) => ({ key: "g:" + g.id, label: g.label,
                     target: g.views.find((v) => !hiddenView(v)), write: false }))
      .filter((c) => c.target);
  }
  const vis = t.views.filter((v) => !hiddenView(v));
  return vis.length > 1
    ? vis.map((v) => ({ key: "v:" + v.id, label: v.label, target: v, write: !!v.write }))
    : [];
}

function chipButton(key, label, target, write) {
  // data-write marks the chips that can ever lock, so the stylesheet reserves
  // the lock glyph's space on those and only those.
  return h("button.chip", {
    type: "button",
    data: write ? { key, view: target.id, write: "1" } : { key, view: target.id },
    onclick: () => goto(target.id),
  }, label);
}

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

  // THE CHIP ROW SHOWS A TAB'S GROUPS WHEN IT HAS THEM, AND ITS VIEWS WHEN IT
  // DOES NOT. Vehicle has six groups; Agent has two views; a tab with one view
  // has nothing to choose between and no row. Rebuilt when the TAB changes and
  // never otherwise, for the reason at the top of this section.
  if (chipsFor !== tab) {
    chipsFor = tab;
    for (const old of els.chips.querySelectorAll(".chip[data-key]")) old.remove();
    const t = TABS.find((x) => x.id === tab);
    for (const c of chipItems(t)) els.chips.appendChild(chipButton(c.key, c.label, c.target, c.write));
    // Home has no row at all, decided per TAB so it can only change with a tab
    // tap. Every other tab keeps the row, because the lead chip ("Car connected
    // -- open Home") can appear there at any moment and must not move anything.
    document.getElementById("app").dataset.subbar = (tab === "home" && !cameFrom) ? "0" : "1";
  }

  // THE SEGMENT ROW: the screens inside the current group, when there is more
  // than one. Rebuilt when the GROUP changes.
  const t = TABS.find((x) => x.id === tab);
  const group = t && t.grouped && here ? here.group : null;
  if (segFor !== group) {
    segFor = group;
    clear(els.seg);
    const g = group ? t.groups.find((x) => x.id === group) : null;
    const vis = g ? g.views.filter((v) => !hiddenView(v)) : [];
    els.seg.hidden = vis.length < 2;
    if (vis.length > 1) for (const v of vis) els.seg.appendChild(chipButton("v:" + v.id, v.label, v, !!v.write));
  }

  els.here.hidden = !(here && here.off);
  if (here && here.off) els.here.textContent = here.title;

  const driving = store.state === "driving";
  for (const c of [...els.chips.querySelectorAll(".chip[data-key]"),
                   ...els.seg.querySelectorAll(".chip[data-key]")]) {
    const key = c.dataset.key;
    const isGroup = key.startsWith("g:");
    const cur = here && (isGroup ? here.group === key.slice(2) : here.id === key.slice(2));
    if (cur) c.setAttribute("aria-current", "page");
    else c.removeAttribute("aria-current");
    // Write-capable screens grey out while the car is moving; they never
    // vanish, and THE LABEL DOES NOT CHANGE (see the history of this loop).
    const v = VIEWS.find((x) => x.id === c.dataset.view);
    const block = !isGroup && !!(v && v.write) && driving;
    c.disabled = block;
    c.classList.toggle("chip-locked", block);
    c.setAttribute("aria-disabled", block ? "true" : "false");
    const why = block ? "Available when you stop" : "";
    if (c.title !== why) c.title = why;
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
  const show = want && tabOf(current) !== "home" && !overridden;
  lead.style.visibility = show ? "visible" : "hidden";
  if (show) {
    lead.dataset.go = HOME;
    lead.textContent = auto.mode === "moving" ? "Car moving — open Home"
                                              : "Car connected — open Home";
    lead.classList.add("chip-link");
  }
}

// ------------------------------------------------------------- the top bar
//
// OmaCar | the page on the left, the clock in the middle, and on the right the
// car and where its numbers came from. The badge never leaves the screen: the
// failure that matters is reading the simulator's numbers as your own car's.
//
// THE CAR IS ALWAYS NAMED. On Home it is the page title ("OmaCar | CR-Z", as
// the mockups have it); everywhere else it sits at the right. Every scan tool
// that has shown somebody the wrong car's data got there by letting it slip.
function buildBar() {
  if (els.page) return;
  const bar = document.getElementById("vbar");
  els.page = h("span.tb-page");
  bar.appendChild(h("div.tb-left", h("span.tb-mark", "OmaCar"),
                    h("span.tb-div", { "aria-hidden": "true" }), els.page));
  els.clock = h("div.tb-clock.display-num");
  bar.appendChild(els.clock);

  const right = h("div.tb-right");
  els.name = h("span.tb-car");

  els.src = h("span.tb-src");
  // Visible, not subtle. The failure that matters is thinking you are private
  // when you are not.
  els.priv = h("button.pill.info.privacy-pill", {
    type: "button", hidden: true,
    title: "Identifying details are hidden. Tap to show them again.",
    onclick: () => { privacy.on = false; paintBar(); go(); },
  }, "VIN hidden");
  right.append(els.name, els.src, els.priv);

  els.daynight = h("button.vbar-btn", {
    type: "button", id: "btn-daynight", hidden: true,
    onclick: () => toggleDayNight(),
  }, icon(ICONS.moon, 20));
  right.appendChild(els.daynight);
  loadThemeModes();

  els.orb = h("button.vbar-btn", {
    type: "button", id: "btn-assistant", hidden: true,
    "aria-label": "Ask the assistant", title: "Ask the assistant",
    onclick: async () => {
      try {
        const r = await api.assistant("toggle");
        if (!r.present) { els.orb.hidden = true; toast(r.error, "bad"); }
      } catch (e) { toast(String((e && e.message) || e), "bad"); }
    },
  }, icon(ICONS.advisor, 20));
  right.appendChild(els.orb);
  api.assistant("present").then((r) => {
    if (r && r.present) els.orb.hidden = false;
  }).catch(() => { /* an older server, or none: no button */ });

  right.appendChild(h("button.vbar-btn", {
    type: "button", id: "btn-settings",
    "aria-label": "Settings", title: "Units, privacy, looks, learn mode",
    onclick: openSettings,
  }, icon(GEAR, 20)));
  bar.appendChild(right);

  tickClock();
  setInterval(tickClock, 15000);
}

// 9:41, not 9:41 AM: the mockups' clock, and the one a dashboard shows.
function tickClock() {
  const s = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
    .replace(/\s?[AP]M$/i, "");
  if (els.clock.textContent !== s) els.clock.textContent = s;
}

// What the two sides of the toggle are, learned from the server once.
let themeModes = { light: null, dark: null, active: null };

async function loadThemeModes() {
  try {
    const d = await api.themes();
    const list = (d && d.themes) || [];
    themeModes.active = (d && d.active) || null;
    const cur = list.find((t) => t.id === themeModes.active);
    for (const t of list) {
      if (t.mode === "light" && !themeModes.light) themeModes.light = t.id;
      if (t.mode === "dark" && !themeModes.dark) themeModes.dark = t.id;
    }
    // Prefer the one being worn as its own side of the pair.
    if (cur) themeModes[cur.mode] = cur.id;
    paintDayNight(cur ? cur.mode : null);
  } catch { /* an older server, or none: no button, which is honest */ }
}

function paintDayNight(mode) {
  if (!els.daynight) return;
  // A TOGGLE WITH NOWHERE TO GO DOES NOT APPEAR. If this machine has only dark
  // themes built, a button that shrugs is worse than no button — that is the
  // complaint this control exists to answer, and repeating it in a new place
  // would be its own joke.
  const desktop = savedLook() === "omarchy";
  const usable = !desktop || !!(themeModes.light && themeModes.dark
                                && themeModes.light !== themeModes.dark);
  els.daynight.hidden = !usable;
  if (!usable) return;
  const dark = desktop ? mode !== "light" : savedLook() !== "day";
  clear(els.daynight);
  els.daynight.appendChild(icon(dark ? ICONS.sun : ICONS.moon, 20));
  els.daynight.title = dark ? "Switch to the day palette"
                            : "Switch to the night palette";
  els.daynight.setAttribute("aria-label", els.daynight.title);
}

async function toggleDayNight() {
  if (savedLook() !== "omarchy") {
    const next = savedLook() === "day" ? "normal" : "day";
    saveLook(next);
    applyLook(next);
    return;
  }
  const goingLight = !document.documentElement.dataset.mode
    ? true
    : document.documentElement.dataset.mode !== "light";
  const want = goingLight ? themeModes.light : themeModes.dark;
  if (!want) return;
  try {
    await api.selectTheme(want);
    themeModes.active = want;
    paintDayNight(goingLight ? "light" : "dark");
    await applyTheme();   // repaints from the palette the server derives
  } catch (e) {
    toast(String((e && e.message) || e), "bad");
  }
}

function paintBar() {
  buildBar();
  const car = store.car;
  const b = badge(car, store.live, store.error);
  if (els.src.textContent !== b.text) els.src.textContent = b.text;
  const cls = "tb-src" + (b.tone ? " " + b.tone : "");
  if (els.src.className !== cls) els.src.className = cls;
  els.src.title = b.title;

  const onHome = current && current.id === HOME;
  const model = car ? ((car.vehicle && car.vehicle.model) || car.title || "") : "";
  const page = onHome ? (model || "Home") : (current ? current.title : "");
  if (els.page.textContent !== page) els.page.textContent = page;
  const who = car ? (car.title || car.name || "") : "";
  els.name.hidden = onHome || !who;
  if (els.name.textContent !== who) els.name.textContent = who;
  els.priv.hidden = !privacy.on;
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

// Root font size, in the one place the whole stylesheet scales from. Every
// dimension in app.css that matters for a thumb is expressed in rem or in the
// --tap tokens, so moving this moves the targets with the type rather than
// leaving small buttons wearing big labels.
const TEXT_SIZES = { small: 15, normal: 16, large: 18, huge: 20 };
const TEXT_LABEL = { small: "Small", normal: "Normal", large: "Large", huge: "Huge" };

function textSize() {
  const v = localStorage.getItem("omacar.textSize");
  return TEXT_SIZES[v] ? v : "normal";
}

function setTextSize(name) {
  const px = TEXT_SIZES[name] || TEXT_SIZES.normal;
  try { localStorage.setItem("omacar.textSize", name); } catch { /* private window */ }
  document.documentElement.style.fontSize = px + "px";
}

function applyTextSize() {
  document.documentElement.style.fontSize =
    (TEXT_SIZES[textSize()] || TEXT_SIZES.normal) + "px";
}

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

    // MODE FIRST, because it changes what the rest of this sheet is for. The
    // note names the consequence rather than the tier: "power user" tells
    // somebody nothing about whether the fan is going to spin.
    const TIER_NOTE = {
      simplified: "The four things that matter, in plain words. Clearing codes only.",
      power: "Every reading and every screen. Clearing codes only.",
      technician: "Commands the car: actuators, published routines, sweeps.",
      god: "Also writes configuration. Drops back after 30 minutes.",
    };
    const TIER_LABEL = { simplified: "Simplified", power: "Power user",
                         technician: "Technician", god: "God mode" };
    rows.appendChild(row("Mode", TIER_NOTE[tier] || "", TIER_LABEL[tier] || tier,
      async () => {
        const order = ["simplified", "power", "technician", "god"];
        const next = order[(order.indexOf(tier) + 1) % order.length];
        // God mode is the one worth saying out loud before entering, because
        // it is the only tier that writes configuration -- and the two things
        // it still cannot do are the two worth naming.
        if (next === "god") {
          const yes = await confirmDialog({
            title: "Enter god mode?",
            body: "This unlocks writing stored configuration values into your car's "
                + "modules. There is no undo: the previous value is gone unless you "
                + "wrote it down, and a wrong one can disable a feature in a way the "
                + "car's own diagnostics will not flag.\n\n"
                + "It is your car and your right to do this. Two things stay refused "
                + "whatever you choose: reprogramming, which needs a manufacturer-signed "
                + "image this tool cannot produce, and writes that disable emissions "
                + "controls, which is Clean Air Act s203(a)(3) rather than a matter of "
                + "preference.\n\nIt drops back to Technician after thirty minutes.",
            confirm: "Enter god mode",
          });
          if (!yes) return;
        }
        try {
          const d = await api.setMode(next);
          tier = d.tier;
          document.documentElement.dataset.tier = tier;
          redraw();
          paintNavState();
          go();
          toast(`Mode: ${TIER_LABEL[tier] || tier}.`);
        } catch (e) {
          toast("Could not change mode: " + (e.message || e), "bad");
        }
      }));

    // THE LOOK, which lived on the hub and would otherwise have gone with it.
    rows.appendChild(row("Look", lookById(savedLook()).note || "",
      lookById(savedLook()).label, () => {
        const next = nextLook(savedLook());
        saveLook(next);
        applyLook(next);
        redraw();
      }));

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

    // TEXT SIZE IS A PROPERTY OF THE SCREEN, NOT OF THE CAR. The drive layout
    // is stored on the server on purpose -- the arrangement made at the kitchen
    // table is the one the tablet shows -- and this is the opposite case: a
    // 10-inch tablet at arm's length in daylight and a 27-inch monitor at a
    // desk want different sizes, and syncing them would make one of the two
    // wrong every time. So it is per-device, and it says so.
    rows.appendChild(row("Text size", "This screen only — a tablet at arm's length is not a desk",
      TEXT_LABEL[textSize()] || "Normal", () => {
        const order = Object.keys(TEXT_SIZES);
        setTextSize(order[(order.indexOf(textSize()) + 1) % order.length]);
        redraw();
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

    rows.appendChild(row("Effects", "Scanner and Leviathan, full screen",
      "Open", () => { close(); goto("effects"); }));

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

// What this app has, told to the server, so nothing else keeps a second copy
// of a registry that lives here and grows with whatever plugins are installed.
function screens() {
  const list = VIEWS.filter((v) => (!v.off || v.askable) && !hiddenView(v))
    .map((v) => ({ id: v.id, label: v.label, title: v.title, tab: v.tab }));
  fetch(withToken("/api/screen"), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ views: list }),
  }).catch(() => { /* no server, no assistant. Nothing here depends on it. */ });
}

let honoured = 0;

async function honourAsk() {
  let ask;
  try {
    const r = await fetch(withToken("/api/screen"), { cache: "no-store" });
    if (!r.ok) return;
    ask = (await r.json()).ask;
  } catch { return; }
  if (!ask || !ask.fresh || !ask.view) return;
  if (!(ask.at > honoured)) return;          // already acted on this one
  honoured = ask.at;
  const v = VIEWS.find((x) => x.id === ask.view);
  // A screen the current mode hides is not opened by asking for it. The tier
  // is decided by the server and this is not a way around it.
  if (!v || hiddenView(v)) return;
  if (location.hash === "#" + v.id) return;
  goto(v.id);
  // NEVER SILENTLY. A screen that changed by itself with nothing to say why is
  // indistinguishable from a bug.
  toast((ask.who || "Something") + " opened " + (v.label || v.id));
}

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
let themeSheet = null;

// APPLIED AS A STYLESHEET, NOT AS INLINE PROPERTIES.
//
// This used to do root.style.setProperty() for every token. An inline style on
// an element beats every rule in every stylesheet, so the theme silently
// outranked the `:root[data-look="..."]` palettes -- and a look could only
// change the four tokens the theme happens not to emit (--hair, --track, and
// until now --bright/--bright-2). Matrix and Night-red repainted a couple of
// meters and left the rail, the vehicle bar, the cards and the stage sitting
// in the desktop theme, which is exactly what "the theme does not carry over
// into the nav and top bar" looks like.
//
// A <style> appended after app.css gives the intended three-layer cascade:
//   app.css :root      the shipped default        (0,1,0), first
//   this sheet :root   the Omarchy theme          (0,1,0), later — wins
//   app.css :root[..]  the look the user picked   (0,2,0) — wins over both
// so an untouched app follows the desktop, and choosing a look overrides it
// deliberately, which is the whole reason a look exists.
async function applyTheme() {
  // THE DESKTOP THEME IS A LOOK NOW, NOT THE DEFAULT. Removed the moment
  // another look is chosen, so the stylesheet's own palette is what shows.
  if (savedLook() !== "omarchy") {
    if (themeSheet) { themeSheet.remove(); themeSheet = null; }
    themeStamp = -1;
    return;
  }
  try {
    const { stamp, vars } = await api.theme();
    if (stamp === themeStamp) return;
    themeStamp = stamp;
    const root = document.documentElement;
    if (!themeSheet) {
      themeSheet = document.createElement("style");
      themeSheet.id = "omarchy-theme";
      document.head.appendChild(themeSheet);
    }
    const decls = Object.entries(vars)
      .filter(([k]) => k !== "mode")
      .map(([k, v]) => `--${k}:${v}`)
      .join(";");
    themeSheet.textContent = `:root{color-scheme:${vars.mode || "dark"};${decls}}`;
    root.dataset.mode = vars.mode || "dark";
  } catch {
    // The stylesheet's own palette is the fallback, and it is the one the app
    // was designed against — so a missing theme is a non-event.
  }
}

async function boot() {
  // The mode before the first paint, so a simplified tablet never flashes the
  // technician screens on its way to hiding them.
  await loadMode();

  window.addEventListener("hashchange", go);

  // The snapshot poller is the only clock running when no view is asking for
  // live samples, so both clocks drive the same repaint. autoDrive() ends in
  // paintNavState(), so the bar is correct on whichever tick arrives first.
  store.on("car", () => { paintBar(); autoDrive(); });
  store.on("live", () => { paintBar(); autoDrive(); });
  // Every sample, for the sparklines. Cheap: an array push per reading.
  store.on("live", () => record(store.live));

  honourInitialView();
  // Before the first paint. A night-red look that arrives a beat late is a
  // flash of full-brightness white at the exact moment it matters most.
  applyLook(savedLook());
  document.addEventListener("omacar:look", () => { applyTheme(); paintDayNight(); });

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

  // Before the first paint that anybody measures a target against.
  applyTextSize();
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

  // ASKED FOR FROM OUTSIDE, WHICH IN A CAR MEANS SAID OUT LOUD.
  //
  // The tablet is used for hours a week in a moving car and the only safe
  // interaction there is the one that needs no hands and no eyes. The voice
  // assistant could already read this car; this is the half that lets it put a
  // screen up. Nothing about the car passes through it — the write arm, the
  // tier and the motion checks all sit downstream of a screen being visible
  // and none of them care which screen it is.
  //
  // THE RULE AT THE TOP OF THIS FILE STILL HOLDS. Nothing here may write
  // location.hash except in direct response to a person. A spoken request is a
  // person, and it is honoured exactly once: the timestamp of the last one
  // acted on is remembered, so a request that stays in the file cannot pull
  // somebody off whatever they are reading a second time. That is the failure
  // this app already had once, from autoDrive(), four times a second.
  screens();
  setInterval(honourAsk, 1500);
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
