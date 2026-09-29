// Road cameras: every Caltrans camera in Monterey County, and the Imjin
// Parkway project's construction cameras, with the owner's commute pinned
// first.
//
// STILLS, AND THEIR AGE. Each picture is Caltrans' latest JPEG, fetched
// through our own server (lib/roadcams.py) so its own timestamp comes with it.
// A tile says how old its picture is and never says "live". With no
// connection the last picture stays up, with its time and "No connection".
//
// ONE FETCHER PER CAMERA, NOT PER TILE. A pinned camera is drawn twice, once
// pinned and once in its road's group, and it costs one request. Nothing is
// fetched for a tile that is off screen: fifty cameras refreshing every two
// minutes on a phone's hotspot is a data plan, not a feature.
//
// WHILE THE CAR MOVES the screen follows WHILE_MOVING in share/js/roadcams.js,
// which is the one place the rule lives, and "moving" is store.moving in
// core.js. The controls it parks grey out with the reason; they never vanish.
// The layout changes on a transition only, never on every sample.
import { h, clear, icon, store, api, toast } from "../core.js";
import { ICONS } from "../icons.js";
import { WHILE_MOVING, WHY_PARKED, layoutFor, ageText, isStale,
         nextRefreshMs, arrange, firstPinnedStill, netState, feedLine } from "../roadcams.js";

// Chromium fires `load` on a frame even when what loaded is an error page, so
// this only catches a frame that never finishes. What a TrueLook page shows
// inside the frame is TrueLook's, and cannot be read from here.
const FRAME_TIMEOUT_MS = 20000;
const LIST_EVERY_MS = 30 * 60 * 1000;
const RETRY_LIST_MS = 2 * 60 * 1000;
const AGE_EVERY_MS = 15000;
const SANDBOX = "allow-scripts allow-same-origin";

// "SR-1 : Imjin Parkway" reads as "Imjin Parkway" under an SR-1 badge.
function shortName(cam) {
  const i = cam.name.indexOf(" : ");
  return i > 0 ? cam.name.slice(i + 3) : cam.name;
}

export default function roadcamsView(root) {
  let alive = true;
  let data = null;           // the server's listing
  let draft = null;          // pins being edited, or null
  let layout = null;         // "all" | "one" | "none", as applied
  let net = "unknown";       // the internet as last heard: online | offline | unknown
  let sig = "";              // which cameras the groups were built for
  let pinnedSig = null;      // which pins the pinned section was built for
  let driveFor = null;       // which camera the driving tile was built for

  const stills = new Map();  // camera id -> one fetcher, however many tiles
  const embeds = new Set();  // construction camera tiles
  let pinnedTiles = [];
  let groupTiles = [];
  let driveTiles = [];

  // ---- the frame, built once -----------------------------------------------
  const sub = h("div.rc-sub", "Reading the camera list…");
  const editBtn = h("button.btn.rc-editbtn", { type: "button", onclick: () => startEdit() },
    icon(ICONS.layout, 18), "Edit pins");
  const resetBtn = h("button.btn", { type: "button", hidden: true, onclick: () => resetDraft() },
    "Back to the commute");
  const cancelBtn = h("button.btn", { type: "button", hidden: true, onclick: () => stopEdit() },
    "Cancel");
  const doneBtn = h("button.btn.primary", { type: "button", hidden: true, onclick: () => savePins() },
    "Done");
  const head = h("div.rc-head",
    h("div.rc-titles", h("div.rc-title", "Road cameras"), sub),
    h("div.rc-actions", resetBtn, cancelBtn, doneBtn, editBtn));

  const jump = h("div.rc-jump");
  const pinnedGrid = h("div.rc-grid");
  const pinnedSec = h("section.rc-sec", { data: { sec: "pinned" } },
    h("div.rc-sec-h", h("h2.rc-sec-t", "Pinned"), h("span.rc-sec-n", "your commute first")),
    pinnedGrid);
  const groupsBox = h("div.rc-groups");
  const listNote = h("div.card.rc-note-card", { hidden: true });
  const parked = h("div.rc-parked", jump, listNote, pinnedSec, groupsBox);

  const driveSlot = h("div.rc-drive-slot");
  const lockedBrowse = h("button.btn", { type: "button", disabled: true, title: WHY_PARKED },
    "All cameras");
  const lockedEdit = h("button.btn", { type: "button", disabled: true, title: WHY_PARKED },
    "Edit pins");
  const lockedEmbeds = h("button.btn", { type: "button", disabled: true, title: WHY_PARKED },
    "Imjin Parkway cameras");
  const driving = h("div.rc-driving", { hidden: true },
    driveSlot,
    h("div.rc-drive-side",
      h("p.rc-why", "One camera while the car moves. Browsing, pins and the construction "
                    + "cameras are here when you stop."),
      h("div.rc-locked", lockedBrowse, lockedEdit, lockedEmbeds),
      h("p.rc-locked-why", WHY_PARKED)));

  const rc = h("div.rc", { data: { editing: "0" } }, head, parked, driving);
  root.appendChild(rc);

  // ---- which tiles are on screen ---------------------------------------------
  //
  // MEASURED, NOT OBSERVED. An IntersectionObserver reports on the next
  // rendered frame, and a tablet whose compositor is starved -- or a headless
  // test run on virtual time -- can go a long while without one, leaving the
  // whole screen waiting for pictures it never asks for. So visibility is a
  // plain measurement after every build and every scroll: a handful of rects,
  // at most every 120 ms.
  const watched = new Set();
  const MARGIN = 200;
  let visTimer = null;

  function watch(t) {
    t.node.__tile = t;
    watched.add(t);
  }

  function measure() {
    visTimer = null;
    if (!alive) return;
    const top = -MARGIN, bottom = window.innerHeight + MARGIN;
    const stage = root.closest(".stage");
    const box = stage ? stage.getBoundingClientRect() : null;
    const lo = box ? Math.max(top, box.top - MARGIN) : top;
    const hi = box ? Math.min(bottom, box.bottom + MARGIN) : bottom;
    for (const t of watched) {
      const r = t.node.getBoundingClientRect();
      const vis = r.height > 0 && r.bottom > lo && r.top < hi;
      if (vis === t.visible) continue;
      t.visible = vis;
      if (t.kind === "still") wake(t.still);
      else paintEmbed(t);
    }
  }
  const measureSoon = () => { if (!visTimer) visTimer = setTimeout(measure, 120); };

  function drop(tiles) {
    for (const t of tiles) {
      watched.delete(t);
      t.node.__tile = null;
      if (t.kind === "still") {
        t.still.tiles.delete(t);
        wake(t.still);
      } else if (t.kind === "embed") {
        unmountFrame(t);
        embeds.delete(t);
      }
      t.node.remove();
    }
  }

  // ---- the stills -------------------------------------------------------------
  function fetcher(cam) {
    let s = stills.get(cam.id);
    if (!s) {
      s = { cam, url: null, age: null, at: 0, error: null, offline: false, noServer: false,
            busy: false, loaded: false, nextAt: 0, timer: null, tiles: new Set() };
      stills.set(cam.id, s);
    }
    s.cam = cam;
    return s;
  }

  const ageOf = (s) => (s.age === null ? null : s.age + (Date.now() / 1000 - s.at));
  const wanted = (s) => [...s.tiles].some((t) => t.visible);

  // Asked for when it is due, and only while a tile showing it is on screen.
  // nextAt survives the tile scrolling away, so scrolling back never asks
  // Caltrans sooner than the once-a-minute rule allows.
  function wake(s) {
    clearTimeout(s.timer);
    s.timer = null;
    if (!alive || s.busy || !wanted(s)) return;
    s.timer = setTimeout(() => { s.timer = null; if (wanted(s)) load(s); },
                         Math.max(0, s.nextAt - Date.now()));
  }

  async function load(s) {
    if (!alive || s.busy) return;
    s.busy = true;
    try {
      put(s, await api.roadcamImage(s.cam.id));
      s.error = null;
      s.offline = s.noServer = false;
      setNet("online");
    } catch (e) {
      if (!alive) return;
      s.error = String((e && e.message) || e);
      s.offline = !!(e && e.offline);
      s.noServer = !!(e && e.noServer);
      if (s.offline) setNet("offline");
      // Nothing on screen yet: the last picture kept on disk, with its own
      // time, beats a blank tile.
      if (!s.url && e && e.saved) {
        try { put(s, await api.roadcamImage(s.cam.id, true)); } catch { /* none kept */ }
      }
    } finally {
      s.busy = false;
    }
    if (!alive) return;
    s.loaded = true;
    s.nextAt = Date.now() + (s.error ? 60000 : nextRefreshMs(s.cam, ageOf(s)));
    paintStill(s);
    wake(s);
  }

  function put(s, got) {
    if (!alive || !got || !got.blob) return;
    const url = URL.createObjectURL(got.blob);
    const old = s.url;
    s.url = url;
    s.age = got.age;
    s.at = got.at;
    for (const t of s.tiles) t.img.src = url;
    if (old) URL.revokeObjectURL(old);
  }

  // After a refusal or a camera leaving the list, a picture still on screen
  // -- or the one kept on disk, fetched in load() -- is the last good one, and
  // says so. Its age stays its own, and stays amber once it is old.
  function paintStill(s) {
    const a = ageOf(s);
    const flag = s.noServer ? "No server" : s.offline ? "No connection"
      : s.error ? (s.url ? "Last good image" : "Not updating") : "";
    const state = s.noServer ? "noserver" : s.offline ? "offline" : s.error ? "error"
      : !s.url ? "loading" : a === null ? "unknown" : isStale(a, s.cam) ? "stale" : "ok";
    const when = s.url ? ageText(a) : (s.loaded ? "no picture" : "loading…");
    for (const t of s.tiles) {
      if (s.url && t.img.getAttribute("src") !== s.url) t.img.src = s.url;
      t.node.dataset.has = s.url ? "1" : "0";
      if (t.node.dataset.state !== state) t.node.dataset.state = state;
      if (t.age.textContent !== when) t.age.textContent = when;
      if (t.flag.textContent !== flag) t.flag.textContent = flag;
      t.flag.hidden = !flag;
      t.node.title = s.error || "";
    }
  }

  function stillTile(cam) {
    const img = h("img.rc-img", { alt: `${cam.name}, Caltrans camera`, decoding: "async" });
    const flag = h("span.rc-flag", { hidden: true });
    const age = h("span.rc-age", "loading…");
    const edit = h("div.rc-edit");
    const node = h("figure.rc-tile", { data: { id: cam.id, kind: "still", has: "0", state: "loading" } },
      h("div.rc-pic", img, h("span.rc-none", "No picture yet"), flag),
      h("figcaption.rc-cap",
        h("div.rc-name", h("span.rc-route", cam.route), h("span.rc-nm", shortName(cam))),
        h("div.rc-meta",
          h("span.rc-where", [cam.place, cam.direction].filter(Boolean).join(" · ")), age)),
      edit);
    const t = { kind: "still", node, img, flag, age, edit, cam, still: fetcher(cam), visible: false };
    t.still.tiles.add(t);
    watch(t);
    paintStill(t.still);
    return t;
  }

  // ---- the construction cameras ----------------------------------------------
  const online = () => navigator.onLine !== false && net !== "offline";

  function setNet(v) {
    if (net === v) return;
    net = v;
    for (const t of embeds) paintEmbed(t);
  }

  // TAP TO LOAD. A TrueLook page is a live video player, and three of them
  // are pinned by default: on a phone's hotspot that is a data plan, so none
  // starts until it is asked for. The poster is the button.
  function embedTile(cam) {
    const t = { kind: "embed", cam, visible: false, frame: null, timer: null,
                loaded: false, failed: false, asked: false };
    t.poster = h("button.rc-poster", { type: "button",
      onclick: () => { if (layout === "all" && online()) { t.asked = true; paintEmbed(t); } } },
      h("span.rc-poster-t"), h("span.rc-poster-s"));
    t.box = h("div.rc-pic.rc-frame", t.poster);
    t.say = h("div.rc-estate");
    t.edit = h("div.rc-edit");
    t.node = h("figure.rc-tile.rc-embed", { data: { id: cam.id, kind: "embed" } },
      t.box,
      h("figcaption.rc-cap",
        h("div.rc-name", h("span.rc-route", "Imjin Pkwy"), h("span.rc-nm", cam.name)),
        h("div.rc-meta", h("span.rc-where", "Imjin Parkway, Marina"),
          h("span.rc-age.rc-own", "live embed — its own clock")),
        h("p.rc-enote", cam.note || "A construction camera, not a Caltrans one."),
        t.say),
      t.edit);
    embeds.add(t);
    watch(t);
    paintEmbed(t);
    return t;
  }

  function mountFrame(t) {
    const f = h("iframe.rc-iframe", {
      src: t.cam.url, title: t.cam.name, sandbox: SANDBOX,
      referrerpolicy: "no-referrer", allow: "", loading: "eager",
    });
    t.loaded = t.failed = false;
    f.addEventListener("load", () => {
      if (t.frame !== f) return;            // a frame already taken down
      t.loaded = true;
      t.failed = false;
      clearTimeout(t.timer);
      paintEmbed(t);
    });
    t.timer = setTimeout(() => { if (!t.loaded) { t.failed = true; paintEmbed(t); } },
                         FRAME_TIMEOUT_MS);
    t.frame = f;
    t.box.appendChild(f);
  }

  // Detached before it is removed. Removing a frame stops whatever it was
  // playing; pointing it at about:blank first, as this used to, fires its
  // load event synchronously, which re-entered here halfway through.
  function unmountFrame(t) {
    clearTimeout(t.timer);
    t.timer = null;
    const f = t.frame;
    t.frame = null;
    t.loaded = t.failed = false;
    if (f) f.remove();
  }

  // Framed only while parked, online and on screen. TrueLook's page is a
  // live video player, so a frame scrolled out of sight is taken down rather
  // than left streaming to nobody -- and a pinned construction camera is drawn
  // twice, pinned and in its group, so keeping both would be two streams.
  // A tap is forgotten when the car moves or the connection goes, so a
  // player never restarts by itself; scrolling away and back keeps it.
  function paintEmbed(t) {
    const parkedNow = layout === "all";
    const up = online();
    if (!parkedNow || !up) t.asked = false;
    if (!t.asked || !t.visible) unmountFrame(t);
    else if (!t.frame) mountFrame(t);
    const [text, tone] = !parkedNow ? [WHY_PARKED, "parked"]
      : !up ? ["No connection. This camera can be loaded when there is one.", "offline"]
      : !t.asked ? ["Not loaded: a live player uses mobile data.", "idle"]
      : t.failed ? ["It did not load. The project may have taken this camera down: "
                    + "the widening was due to finish in June 2026.", "failed"]
      : !t.frame ? ["Loads again when it is on screen.", "waiting"]
      : !t.loaded ? ["Loading from TrueLook…", "loading"]
      : ["Shown by TrueLook. If it is blank or cannot find the camera, the project "
         + "has probably taken it down.", "shown"];
    if (t.say.textContent !== text) t.say.textContent = text;
    // The poster: the button while it can be pressed, the reason while not.
    const [title, small] = !parkedNow ? [WHY_PARKED, ""]
      : !up ? ["No connection", ""]
      : ["Tap to load the live view", "It uses mobile data"];
    const [pt, ps] = t.poster.children;
    if (pt.textContent !== title) pt.textContent = title;
    if (ps.textContent !== small) ps.textContent = small;
    t.poster.disabled = !parkedNow || !up;
    t.node.dataset.estate = tone;
  }

  function missingTile(p) {
    const edit = h("div.rc-edit");
    const node = h("figure.rc-tile.rc-missing", { data: { id: p.id || "", kind: "missing" } },
      h("div.rc-pic.rc-gone", h("span.rc-none",
        p.id ? "No longer in Caltrans' list" : "Not in the list yet")),
      h("figcaption.rc-cap",
        h("div.rc-name", h("span.rc-nm", p.name)),
        h("div.rc-meta", h("span.rc-where",
          p.id ? "Out of service, or taken off the list. It stays pinned until you "
                 + "unpin it, and saving the pins keeps it."
               : "The camera list has not been downloaded yet."))),
      edit);
    return { kind: "missing", node, edit, id: p.id };
  }

  // ---- building the sections ----------------------------------------------------
  const tileFor = (cam) => (cam.kind === "embed" ? embedTile(cam) : stillTile(cam));

  function buildPinned() {
    // What it was built from, so a reload of the same list leaves it alone;
    // a draft is never the saved pins, so leaving the editor always redraws.
    pinnedSig = draft ? null : pinSig(data || {});
    drop(pinnedTiles);
    clear(pinnedGrid);
    const pins = draft || (data && data.pins) || [];
    const { pinned } = arrange(data, draft || null);
    pinnedTiles = pinned.map((p) => (p.cam ? tileFor(p.cam) : missingTile(p)));
    for (const t of pinnedTiles) pinnedGrid.appendChild(t.node);
    if (!pinnedTiles.length) {
      pinnedGrid.appendChild(h("p.rc-empty", draft
        ? "Nothing pinned. Pin cameras below; the first Caltrans one is what a moving car shows."
        : "Nothing pinned. Edit pins to put your commute first."));
    }
    // Reordering and unpinning, on the pinned tiles themselves.
    pinnedTiles.forEach((t, i) => {
      clear(t.edit);
      if (!draft || !t.node.dataset.id) return;
      const id = t.node.dataset.id;
      t.edit.append(
        h("button.btn", { type: "button", disabled: i === 0, "aria-label": "Move earlier",
                          onclick: () => move(id, -1) }, "↑ Earlier"),
        h("button.btn", { type: "button", disabled: i === pins.length - 1,
                          "aria-label": "Move later", onclick: () => move(id, 1) }, "↓ Later"),
        h("button.btn", { type: "button", onclick: () => toggle(id) }, "Unpin"));
    });
    rc.dataset.pinned = String(pinnedTiles.length);
    measureSoon();
  }

  function buildGroups() {
    drop(groupTiles);
    groupTiles = [];
    clear(groupsBox);
    clear(jump);
    const { groups } = arrange(data);
    jump.appendChild(h("button.chip", { type: "button",
      onclick: () => pinnedSec.scrollIntoView({ block: "start" }) }, "Pinned"));
    for (const g of groups) {
      const grid = h("div.rc-grid");
      const sec = h("section.rc-sec", { data: { sec: g.id } },
        h("div.rc-sec-h", h("h2.rc-sec-t", g.label),
          h("span.rc-sec-n", `${g.cams.length} camera${g.cams.length === 1 ? "" : "s"}`)),
        grid);
      for (const cam of g.cams) {
        const t = tileFor(cam);
        t.pinBtn = h("button.btn.rc-pinbtn", { type: "button", onclick: () => toggle(cam.id) }, "Pin");
        t.edit.appendChild(t.pinBtn);
        groupTiles.push(t);
        grid.appendChild(t.node);
      }
      groupsBox.appendChild(sec);
      jump.appendChild(h("button.chip", { type: "button",
        onclick: () => sec.scrollIntoView({ block: "start" }) }, g.label));
    }
    paintPinButtons();
    measureSoon();
  }

  // Rebuilt only when the camera it shows changes, never because the list
  // was read again or the car stopped and started.
  function buildDriving() {
    const cam = firstPinnedStill(data);
    const want = layout === "none" ? "none" : !data ? "loading" : cam ? camSig([cam]) : "no-cam";
    if (want === driveFor) return;
    driveFor = want;
    drop(driveTiles);
    driveTiles = [];
    clear(driveSlot);
    if (layout === "none") return;
    if (!data) {
      driveSlot.appendChild(h("div.card.rc-drive-none", "Reading the camera list…"));
    } else if (cam) {
      const t = stillTile(cam);
      t.node.classList.add("rc-drive-tile");
      driveTiles.push(t);
      driveSlot.appendChild(t.node);
    } else {
      driveSlot.appendChild(h("div.card.rc-drive-none",
        data && (data.cameras || []).some((c) => c.kind === "still")
          ? "No Caltrans camera is pinned. Pin one when you stop, and it shows here."
          : "No Caltrans camera to show: the list has not been downloaded."));
    }
    measureSoon();
  }

  // ---- the pins -------------------------------------------------------------------
  const maxPins = () => (data && data.max_pins) || 12;

  function paintPinButtons() {
    const pins = draft || (data && data.pins) || [];
    for (const t of groupTiles) {
      if (!t.pinBtn) continue;
      const on = pins.includes(t.cam.id);
      t.pinBtn.setAttribute("aria-pressed", on ? "true" : "false");
      t.pinBtn.textContent = on ? "Pinned ✓" : "Pin";
      t.pinBtn.disabled = !on && pins.length >= maxPins();
      t.pinBtn.title = t.pinBtn.disabled ? `At most ${maxPins()} cameras can be pinned` : "";
    }
  }

  function startEdit() {
    if (!data || layout !== "all") return;
    draft = [...(data.pins || [])];
    paintEditing();
  }

  function stopEdit() {
    draft = null;
    paintEditing();
  }

  function paintEditing() {
    const on = draft !== null;
    rc.dataset.editing = on ? "1" : "0";
    editBtn.hidden = on;
    resetBtn.hidden = cancelBtn.hidden = doneBtn.hidden = !on;
    buildPinned();
    paintPinButtons();
  }

  function toggle(id) {
    if (!draft) return;
    const i = draft.indexOf(id);
    if (i >= 0) draft.splice(i, 1);
    else if (draft.length < maxPins()) draft.push(id);
    buildPinned();
    paintPinButtons();
  }

  function move(id, by) {
    const i = draft ? draft.indexOf(id) : -1;
    const j = i + by;
    if (i < 0 || j < 0 || j >= draft.length) return;
    [draft[i], draft[j]] = [draft[j], draft[i]];
    buildPinned();
  }

  async function savePins() {
    if (!draft) return;
    doneBtn.disabled = true;
    try {
      const d = await api.saveRoadcamPins(draft);
      if (!alive) return;
      draft = null;
      take(d);
      paintEditing();
      toast("Pins saved.");
    } catch (e) {
      toast("Could not save the pins: " + ((e && e.message) || e), "bad");
    } finally {
      doneBtn.disabled = false;
    }
  }

  // The draft only, like every other change here: nothing is saved until Done.
  function resetDraft() {
    if (!draft || !data) return;
    draft = [...(data.default_pins || [])];
    buildPinned();
    paintPinButtons();
  }

  // ---- parked, or moving -------------------------------------------------------------
  function applyLayout() {
    // store.moving: a red light or a hand-off stays "moving" until the car
    // has sat still and connected for a minute, so the grid and the players
    // do not come back at every stop.
    const next = layoutFor(store.moving, WHILE_MOVING);
    if (next === layout) return;
    layout = next;
    rc.dataset.layout = next;
    parked.hidden = next !== "all";
    driving.hidden = next === "all";
    editBtn.disabled = next !== "all" || !data;
    editBtn.title = next !== "all" ? WHY_PARKED : "";
    for (const b of [resetBtn, cancelBtn, doneBtn]) {
      b.disabled = next !== "all";
      b.title = editBtn.title;
    }
    if (next !== "all") {
      const stage = root.closest(".stage");
      if (stage) stage.scrollTop = 0;
    }
    buildDriving();
    measure();
    for (const t of embeds) paintEmbed(t);
  }

  // ---- the list ------------------------------------------------------------------------
  function take(d) {
    data = d;
    const fresh = netState(d);
    if (fresh !== "unknown" && net === "unknown") setNet(fresh);
    sub.textContent = feedLine(d);
    sub.dataset.tone = d.feed && (d.feed.error || d.feed.warning) ? "warn" : "";
    sub.title = (d.feed && d.feed.warning) || "";
    const f = d.feed || {};
    listNote.hidden = !!f.fetched_at;
    if (!f.fetched_at) {
      clear(listNote);
      listNote.append(h("div.title", "No Caltrans cameras yet"),
        h("p.lede", "The list of cameras has not been downloaded, so only the Imjin "
                    + "Parkway construction cameras are here. It is fetched as soon as "
                    + "there is a connection."),
        f.error ? h("p.muted", f.error) : null);
    }
    // A RELOAD REBUILDS ONLY WHAT CHANGED. The list is read again every half
    // hour, and every two minutes while it is in trouble; rebuilding every
    // tile each time took a loaded construction camera's player down and put
    // it up again, and replaced the one tile a moving car shows.
    const nextSig = camSig(d.cameras);
    if (nextSig !== sig) {
      sig = nextSig;
      buildGroups();
    }
    if (!draft && pinSig(d) !== pinnedSig) buildPinned();
    paintPinButtons();
    editBtn.disabled = layout !== "all";
    buildDriving();
  }

  // What a tile is drawn from, so "changed" means something a tile shows.
  function camSig(cams) {
    return JSON.stringify((cams || []).map((c) =>
      [c.id, c.kind, c.name, c.route, c.place, c.direction, c.updated_minutes, c.url || ""]));
  }
  function pinSig(d) {
    return JSON.stringify([d.pins, d.pins_missing, camSig((d.cameras || [])
      .filter((c) => (d.pins || []).includes(c.id)))]);
  }

  // Read again every half hour (the server keeps the list six hours), or
  // every two minutes while there is no list or it could not be refreshed, so
  // a connection that comes back is noticed without leaving the screen.
  let listTimer = null;
  async function loadList() {
    clearTimeout(listTimer);
    try {
      const d = await api.roadcams();
      if (!alive) return;
      take(d);
    } catch (e) {
      if (!alive) return;
      if (!data) {
        sub.textContent = store.noServer
          ? "OmaCar cannot reach its own server, so it cannot ask Caltrans either."
          : "Could not read the camera list: " + ((e && e.message) || e);
        sub.dataset.tone = "warn";
      }
    }
    if (!alive) return;
    const troubled = !data || (data.feed && data.feed.error);
    listTimer = setTimeout(loadList, troubled ? RETRY_LIST_MS : LIST_EVERY_MS);
  }

  const repaintAges = () => { for (const s of stills.values()) if (s.tiles.size) paintStill(s); };
  // The browser coming back online is a reason to ask for the list again at
  // once, rather than at the next two-minute retry.
  const onNet = (e) => {
    for (const t of embeds) paintEmbed(t);
    if (e && e.type === "online") loadList();
  };

  editBtn.disabled = true;
  applyLayout();
  loadList();
  const offLive = store.on("live", applyLayout);
  const offCar = store.on("car", applyLayout);
  const ages = setInterval(repaintAges, AGE_EVERY_MS);
  window.addEventListener("online", onNet);
  window.addEventListener("offline", onNet);
  // Scroll events do not bubble, but they can be caught on the way down: this
  // hears the stage scrolling without having to know which element it is.
  document.addEventListener("scroll", measureSoon, { capture: true, passive: true });
  window.addEventListener("resize", measureSoon);

  return () => {
    alive = false;
    offLive();
    offCar();
    clearInterval(ages);
    clearTimeout(listTimer);
    window.removeEventListener("online", onNet);
    window.removeEventListener("offline", onNet);
    document.removeEventListener("scroll", measureSoon, { capture: true });
    window.removeEventListener("resize", measureSoon);
    clearTimeout(visTimer);
    watched.clear();
    for (const t of embeds) unmountFrame(t);
    for (const s of stills.values()) {
      clearTimeout(s.timer);
      if (s.url) URL.revokeObjectURL(s.url);
    }
    stills.clear();
  };
}
