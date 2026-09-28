// Cameras, after mockup 6: a main feed and two small feeds (tap one to swap it
// into the main slot), the last hour as a timeline of clips and events,
// playback, Save clip and Mark event, camera selection, and storage.
//
// NOTHING HERE OPENS A CAMERA. A V4L2 device has one owner and it is the
// recorder (lib/cams.py). The feeds are its live pictures as MJPEG, which an
// <img> shows with no decoder; playback is its clips through a <video>, which
// can seek because the server answers Range requests. Three feeds and drowsy
// mode's cabin stream hold four of the six connections Chromium allows to one
// host, which leaves two for everything else: do not add a fifth stream.

import { h, clear, icon, toast } from "../core.js";
import { ICONS } from "../icons.js";
import { getJSON, postJSON, liveUrl, clipUrl } from "../camapi.js";
import { ROLES, ROLE_LABEL, camBadge, feedState, storageLine, timelineModel, clipAt,
         stepAcross, hhmm, hhmmss } from "../camlogic.js";

// Transport and settings glyphs in the style of the Lucide set the app already
// uses (share/js/icons.js), kept here so this branch adds a file rather than
// lines to icons.js.
const G = {
  play: ["M6 3 20 12 6 21z"],
  pause: ["M14 4h4v16h-4z", "M6 4h4v16H6z"],
  back: ["M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8", "M3 3v5h5"],
  fwd: ["M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8", "M21 3v5h-5"],
  save: ["M9 6a3 3 0 1 1-6 0 3 3 0 0 1 6 0z", "M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0z",
         "M20 4 8.12 15.88", "M14.47 14.48 20 20", "M8.12 8.12 12 12"],
  mark: ["m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z"],
  mute: ["M11 5 6 9H2v6h4l5 4z", "m22 9-6 6", "m16 9 6 6"],
  disk: ["M22 12H2", "M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"],
  loop: ["M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8", "M21 3v5h-5",
         "M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16", "M8 16H3v5"],
};

const pct = (x) => (x * 100).toFixed(3) + "%";

export default function camerasView(root) {
  let alive = true;
  let ov = null;
  let clips = [], events = [];
  let model = null;
  let main = "front";
  let playing = null;              // { role, file, start } while a clip plays in the main slot
  const streaming = {};            // role -> its <img> holds a live stream

  // ---- the feeds ------------------------------------------------------------
  const feeds = {};
  for (const role of ROLES) {
    const img = h("img.cam-img", { alt: `${ROLE_LABEL[role]}, live`, draggable: "false" });
    img.addEventListener("error", () => { streaming[role] = false; });
    const f = {
      img,
      title: h("span.cam-title"),
      rec: h("span.cam-rec", { hidden: true }, h("span.cam-dot"), "REC"),
      sim: h("span.cam-sim", { hidden: true }, "SIMULATED"),
      clock: h("span.cam-clock"),
      why: h("div.cam-why", { hidden: true }),
    };
    f.node = h("div.cam-feed", { data: { role, rec: "0" }, title: `Show the ${ROLE_LABEL[role].toLowerCase()} large` },
      img, f.why, h("div.cam-cap", f.rec, f.title, f.sim, f.clock));
    f.node.addEventListener("click", () => { if (role !== main) choose(role); });
    feeds[role] = f;
  }
  const video = h("video.cam-video", { playsinline: true, muted: true, preload: "auto" });
  const mainSlot = h("div.cam-main");
  const sideSlot = h("div.cam-side");
  const badge = h("span.tb-src.cam-badge");

  // ---- the timeline -----------------------------------------------------------
  const tlEvent = h("span.cam-tl-event");
  const tlSpan = h("span.cam-tl-span");
  const track = h("div.cam-track", { title: "Tap a moment to play it" });
  track.addEventListener("click", (e) => {
    if (!model) return;
    const r = track.getBoundingClientRect();
    const t = model.t0 + ((e.clientX - r.left) / r.width) * (model.t1 - model.t0);
    const hit = clipAt(clips, main, t);
    if (hit) play(main, hit.file, hit.pos); else toast("Nothing was recorded then.");
  });

  // ---- playback ---------------------------------------------------------------
  const when = h("span.cam-when", "LIVE");
  const pos = h("input.cam-scrub", { type: "range", min: "0", max: "60", step: "0.1", value: "0",
    "aria-label": "Position in the clip", disabled: true });
  pos.addEventListener("input", () => { if (playing) video.currentTime = Number(pos.value); });
  const playBtn = h("button.cam-btn.play", { type: "button", "aria-label": "Play", onclick: () => toggle() }, icon(G.play, 28));
  const liveBtn = h("button.cam-live", { type: "button", hidden: true, onclick: () => goLive() }, "Live");
  const tbtn = (g, label, fn) => h("button.cam-btn", { type: "button", "aria-label": label, title: label, onclick: fn }, icon(g, 26));
  const act = (g, label, fn) => h("button.cam-act", { type: "button", onclick: fn }, icon(g, 22), h("span", label));
  const muteBtn = h("button.cam-act", { type: "button", disabled: true, title: "No audio recorded" },
    icon(G.mute, 22), h("span", "Mute"), h("span.cam-act-note", "No audio recorded"));

  // ---- selection and storage --------------------------------------------------
  const selBtns = ROLES.map((r) => h("button.cam-pick", { type: "button", data: { role: r }, "aria-pressed": "false",
    onclick: () => choose(r) }, icon(ICONS.camera, 22), h("span", r[0].toUpperCase() + r.slice(1))));
  const meter = h("span.cam-meter-fill");
  const storage = h("span.cam-store-v");

  root.appendChild(h("div.cams",
    h("div.cams-feeds", mainSlot, sideSlot),
    h("div.card.cam-timeline", h("div.cam-tl-head", h("span.cam-tl-title", "Event timeline"), tlEvent, badge, tlSpan), track),
    h("div.card.cam-playbar",
      h("div.cam-scrubrow", when, pos),
      h("div.cam-transport", tbtn(G.back, "Back 10 seconds", () => step(-10)), playBtn,
        tbtn(G.fwd, "Forward 10 seconds", () => step(10)), liveBtn),
      h("div.cam-actions", act(G.save, "Save clip", saveClip), act(G.mark, "Mark event", markEvent), muteBtn)),
    h("div.cams-foot",
      h("div.card.cam-select", h("div.cam-card-t", "Camera selection"), h("div.cam-picks", ...selBtns)),
      h("div.card.cam-settings", h("div.cam-card-t", "Settings & storage"),
        h("div.cam-row", icon(G.disk, 20), h("span.cam-row-l", "Storage"), h("span.cam-meter", meter), storage),
        h("div.cam-row", icon(G.loop, 20), h("span.cam-row-l", "Loop recording"), h("span.cam-row-v.on", "On")),
        h("div.cam-row.is-off", icon(ICONS.vehicle, 20), h("span.cam-row-l", "Parking watch"),
          h("span.cam-row-v", "Off · coming later"))))));

  function choose(role) {
    if (playing) goLive();
    main = role;
    place();
  }

  // Placed by moving nodes, never rebuilding them: a feed keeps its stream.
  function place() {
    mainSlot.appendChild(feeds[main].node);
    for (const r of ROLES) if (r !== main) sideSlot.appendChild(feeds[r].node);
    for (const r of ROLES) feeds[r].node.classList.toggle("is-main", r === main);
    for (const b of selBtns) b.setAttribute("aria-pressed", String(b.dataset.role === main));
    drawTimeline();
  }

  function paint() {
    const b = camBadge(ov);
    badge.textContent = b.text;
    badge.className = "tb-src cam-badge" + (b.tone ? " " + b.tone : "");
    for (const role of ROLES) {
      const s = feedState(ov, role), f = feeds[role];
      f.title.textContent = s.title;
      f.rec.hidden = !s.rec;
      f.sim.hidden = !s.sim;
      f.why.hidden = !s.why;
      f.why.textContent = s.why || "";
      f.node.dataset.rec = s.rec ? "1" : "0";
      if (s.live && !streaming[role]) { f.img.src = liveUrl(role); streaming[role] = true; }
      if (!s.live && streaming[role]) { f.img.removeAttribute("src"); streaming[role] = false; }
    }
    const st = ov && ov.storage;
    storage.textContent = storageLine(st);
    meter.style.width = st && st.budget ? Math.min(100, (100 * st.used) / st.budget) + "%" : "0";
  }

  function paintPlay() {
    const going = !!playing && !video.paused;
    clear(playBtn);
    playBtn.appendChild(icon(going ? G.pause : G.play, 28));
    playBtn.setAttribute("aria-label", going ? "Pause" : "Play");
    liveBtn.hidden = !playing;
    pos.disabled = !playing;
    when.textContent = playing ? hhmmss(playing.start + video.currentTime) : "LIVE";
  }

  function drawTimeline() {
    model = timelineModel(clips, events, main, Date.now() / 1000);
    clear(track);
    for (const k of model.ticks) {
      track.appendChild(h("span.cam-tick" + (k.locked ? ".locked" : ""),
        { style: { left: pct(k.x), width: pct(Math.max(k.w, 0.002)) } }));
    }
    for (const l of model.labels) track.appendChild(h("span.cam-label", { style: { left: pct(l.x) } }, l.text));
    for (const e of model.markers) {
      track.appendChild(h("span.cam-marker", { data: { kind: e.kind }, title: e.label, style: { left: pct(e.x) } }));
    }
    if (playing) {
      const x = (playing.start + video.currentTime - model.t0) / (model.t1 - model.t0);
      track.appendChild(h("span.cam-head", { style: { left: pct(Math.min(1, Math.max(0, x))) } }));
    }
    const last = model.markers[model.markers.length - 1];
    tlEvent.textContent = last ? last.label : "No events in the last hour";
    tlEvent.classList.toggle("none", !last);
    tlSpan.textContent = `${hhmm(model.t0)} – ${hhmm(model.t1)}`;
  }

  function tickClock() {
    const now = Date.now() / 1000;
    for (const role of ROLES) {
      const s = playing && playing.role === role ? hhmmss(playing.start + video.currentTime) : hhmmss(now);
      if (feeds[role].clock.textContent !== s) feeds[role].clock.textContent = s;
    }
    if (playing) {
      pos.value = String(video.currentTime);
      when.textContent = hhmmss(playing.start + video.currentTime);
    }
  }

  async function refresh() {
    try { ov = await getJSON("/api/cams"); } catch { ov = null; }
    if (alive) paint();
  }

  async function refreshClips() {
    const from = Math.floor(Date.now() / 1000) - 3600;
    try {
      const d = await getJSON(`/api/cams/clips?from=${from}`);
      clips = d.clips;
      events = d.events;
    } catch { /* keep what was there */ }
    if (alive) drawTimeline();
  }

  function play(role, file, at) {
    const c = clips.find((k) => k.role === role && k.file === file);
    if (!c) return;
    playing = { role, file, start: c.start };
    const f = feeds[role];
    f.node.appendChild(video);
    f.node.classList.add("is-playing");
    video.src = clipUrl(role, file);
    video.addEventListener("loadedmetadata", () => {
      video.currentTime = Math.max(0, Math.min(at, (video.duration || at) - 0.1));
      video.play().catch(() => {});
    }, { once: true });
    pos.max = String(Math.max(1, c.end - c.start));
    paintPlay();
    drawTimeline();
  }

  function goLive() {
    if (!playing) return;
    const f = feeds[playing.role];
    video.pause();
    video.removeAttribute("src");
    video.load();
    video.remove();
    f.node.classList.remove("is-playing");
    playing = null;
    paintPlay();
    drawTimeline();
  }

  // Play, from live, replays the last ten seconds: the one thing a dashcam's
  // play button can mean while it is already showing the present.
  function toggle() {
    if (!playing) { step(-10); return; }
    if (video.paused) video.play().catch(() => {}); else video.pause();
  }

  async function step(delta) {
    if (!playing) {
      await refreshClips();
      const hit = clipAt(clips, main, Date.now() / 1000 + delta);
      if (hit) play(main, hit.file, hit.pos); else toast("Nothing recorded to go back to yet.");
      return;
    }
    const next = stepAcross(clips, playing.role, playing.file, video.currentTime, delta);
    if (!next) { goLive(); return; }
    if (next.file === playing.file) video.currentTime = next.pos;
    else play(playing.role, next.file, next.pos);
  }

  video.addEventListener("play", paintPlay);
  video.addEventListener("pause", paintPlay);
  // On to the next minute, or back to live after the newest.
  video.addEventListener("ended", () => {
    if (!playing) return;
    const later = clips.filter((c) => c.role === playing.role && c.start > playing.start)
      .sort((a, b) => a.start - b.start);
    if (later.length) play(playing.role, later[0].file, 0); else goLive();
  });

  async function saveClip() {
    const t = playing ? playing.start + video.currentTime : Date.now() / 1000;
    try {
      await postJSON("/api/cams/lock", { t });
      toast(`Saved: the minute around ${hhmmss(t)} is kept, on every camera.`);
      refreshClips();
    } catch (e) { toast("Could not save the clip: " + e.message, "bad"); }
  }

  async function markEvent() {
    try {
      await postJSON("/api/cams/mark", {});
      toast("Marked. The minute around now is kept, on every camera.");
      refreshClips();
    } catch (e) { toast("Could not mark the event: " + e.message, "bad"); }
  }

  place();
  paintPlay();
  refresh();
  refreshClips();
  const timers = [setInterval(refresh, 2000), setInterval(refreshClips, 10000),
                  setInterval(tickClock, 250), setInterval(drawTimeline, 5000)];

  return () => {
    alive = false;
    for (const t of timers) clearInterval(t);
    for (const r of ROLES) feeds[r].img.removeAttribute("src");
    video.pause();
    video.removeAttribute("src");
    video.load();
  };
}
