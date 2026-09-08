// OmaPlay — the layer the phone screen lives in.
//
// WHY A LAYER AND NOT A VIEW.
//
// Every other screen in this app is a view: the router unmounts the old one
// and mounts the new one, and that is right for a page of gauges. It is wrong
// here, because unmounting OmaPlay would stop the audio. Somebody glancing at
// their coolant temperature for four seconds must not have their music cut
// out, and a design where checking the engine costs you the podcast is a
// design nobody uses twice.
//
// So this mounts ONCE, above the router, and only its visibility and geometry
// change. The same reasoning the radio player already relies on, one level up.
//
// THE THING NO HEAD UNIT CAN DO.
//
// We own the compositor. Apple and Google own what is inside the video
// rectangle and we cannot touch it — but we can draw ON it, and OmaCar knows
// things about the car that a phone never will. watch.py already fires
// debounced, hysteretic rules at a running engine; putting those over the top
// of navigation is the entire argument for combining the two products, and it
// is the one capability a five-thousand-dollar head unit cannot match.
//
// LAYOUT MODES.
//
//   full     the phone screen, edge to edge
//   split    phone on the larger part, live car data beside it
//   pip      car data in front, phone in a corner
//   hidden   still running, still playing, not drawn
//
// `hidden` is not the same as stopped, and the difference is the whole reason
// this file exists.

import { h, clear, api as carApi } from "../core.js";
import { mockSource, MEDIA_DATA, MEDIA_ALBUM_COVER } from "./source.js";
import { gaugeRail } from "./rail.js";

export const MODES = ["full", "split", "pip", "hidden"];

function fmtTime(secs) {
  const s = Math.max(0, Math.floor(Number(secs) || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function createOmaPlay(opts = {}) {
  let source = null;
  let off = null;
  let mode = "hidden";
  let state = "idle";        // idle | connecting | playing | failed
  let media = {};
  let cover = null;
  const overlays = new Map();
  let alertTimer = null;

  const canvas = h("canvas.op-screen", { width: 800, height: 640 });
  const badge = h("div.op-badge");
  const npArt = h("div.op-art");
  const npTitle = h("div.op-title", "Nothing playing");
  const npSub = h("div.op-sub", "");
  const npBarFill = h("i");
  const npBar = h("div.op-bar", npBarFill);
  const npElapsed = h("span.op-t", "0:00");
  const npTotal = h("span.op-t", "0:00");
  const overlayHost = h("div.op-overlays");
  const statusLine = h("div.op-status", "Not connected");

  const nowPlaying = h("div.op-np",
    npArt,
    h("div.op-np-text",
      npTitle,
      npSub,
      h("div.op-np-time", npElapsed, npBar, npTotal)));

  // The car's own numbers, beside the phone. Built once here rather than on
  // entering split mode: it subscribes to the live store, and creating and
  // destroying that subscription every time somebody changes layout is how a
  // listener leak starts.
  const rail = gaugeRail();

  const stage = h("div.op-stage", canvas, overlayHost, badge);
  const root = h("div.omaplay", { data: { mode: "hidden", state: "idle" } },
    stage,
    h("div.op-side", nowPlaying, rail.el, statusLine));

  // ------------------------------------------------------------- painting

  // What the source has told us about itself, as opposed to what we assumed.
  let note = {};
  let decoded = false;

  function paintMedia() {
    const m = media || {};
    npTitle.textContent = m.MediaSongName || "Nothing playing";
    const bits = [m.MediaArtistName, m.MediaAlbumName].filter(Boolean);
    npSub.textContent = bits.join(" · ");
    const dur = Number(m.MediaSongDuration) || 0;
    const at = Number(m.MediaSongPlayTime) || 0;
    npElapsed.textContent = fmtTime(at);
    npTotal.textContent = fmtTime(dur);
    // Width, not transform: this moves once a second, so the cheaper
    // compositing of a transform buys nothing and a percentage width is
    // simpler to reason about when the panel is resized.
    npBarFill.style.width = dur > 0
      ? `${Math.min(100, (at / dur) * 100).toFixed(2)}%` : "0%";
    badge.textContent = m.MediaAPPName || "";
    badge.hidden = !m.MediaAPPName;
  }

  function paintCover() {
    clear(npArt);
    if (cover) {
      npArt.appendChild(h("img", { src: `data:image/jpeg;base64,${cover}`,
                                   alt: "" }));
      npArt.dataset.has = "1";
    } else {
      npArt.dataset.has = "0";
    }
  }

  // WHAT THE SCREEN IS ALLOWED TO CLAIM.
  //
  // Three different things can be on this canvas that are not a phone: the
  // mock, a replayed recording, and a real adapter whose driver has never once
  // met hardware. All three get the same amber frame, because the one
  // unforgivable outcome is somebody believing a stand-in is real.
  //
  // The adapter's label is the interesting one. It is earned away rather than
  // set: the driver stays "unproven" until a picture actually arrives from it,
  // and then it stops saying so, because at that point it has been proven and
  // continuing to hedge would be its own kind of lie.
  function standIn() {
    if (!source) return "";
    if (source.kind === "mock") return "MOCK — not a phone";
    if (note.replay) return "REPLAY — a recording, not a phone";
    if (note.undecodable) return "THIS BROWSER CANNOT DECODE H.264";
    if (note.unproven && !decoded) return "UNPROVEN — no picture yet";
    return "";
  }

  function paintState() {
    root.dataset.state = state;
    const label = standIn();
    if (label) root.dataset.standin = label;
    else delete root.dataset.standin;

    let line = {
      idle: "Not connected",
      connecting: "Connecting to your phone…",
      playing: "Connected",
      failed: "The adapter did not answer",
    }[state] || "";
    if (note.undecodable) line = note.undecodable;
    else if (state === "failed" && note.error) line = note.error;
    else if (state === "playing" && source && source.kind === "mock") {
      line = "Mock source — no phone connected";
    } else if (state === "playing" && note.replay) {
      line = "Replaying a recording";
    } else if (state === "playing" && note.phone) {
      line = note.phone + " connected";
      if (note.route === "mediasource") {
        line += " · decoding through the video element, because WebCodecs "
              + "would not";
      }
    } else if (state === "connecting" && note.opening) {
      line = note.opening;
    }
    statusLine.textContent = line;
  }

  function paintOverlays() {
    clear(overlayHost);
    for (const a of overlays.values()) {
      overlayHost.appendChild(h(
        "div.op-alert" + (a.urgency === "critical" ? ".bad"
          : a.urgency === "normal" ? ".warn" : ""),
        h("div.op-alert-t", a.title || ""),
        a.body ? h("div.op-alert-b", a.body) : null));
    }
    overlayHost.hidden = overlays.size === 0;
  }

  // ------------------------------------------------------------- messages

  function onMessage(msg) {
    if (!msg || !msg.type) return;
    switch (msg.type) {
      case "opening":
        state = "connecting";
        note = { ...note, opening: msg.note || "", replay: !!msg.replay,
                 unproven: !!msg.unproven };
        paintState();
        break;
      case "plugged":
        state = "playing";
        note = { ...note, replay: !!msg.replay, phone: msg.phone || "",
                 opening: "" };
        if (msg.note) note.opening = msg.note;
        paintState();
        break;
      case "picture":
        // A FRAME THAT ACTUALLY DECODED AND DREW. This is the moment the
        // driver stops being unproven, and the badge comes off by itself.
        // Bytes arriving would not have been enough: the adapter can be
        // talking and the picture still be nothing.
        decoded = true;
        paintState();
        break;
      case "geometry":
        note = { ...note, size: [msg.width, msg.height] };
        break;
      case "route":
        // Which of the two decoders is carrying the picture. It looks the same
        // either way, so this is not a warning -- but somebody wondering why
        // the tablet is warm or why a touch feels a beat late is owed it.
        note = { ...note, route: msg.route || "", routeWhy: msg.why || "" };
        paintState();
        break;
      case "undecodable":
        // Not a failure -- the stream is fine and the words say so. Putting it
        // in the status line is the difference between a driver replacing an
        // adapter that works and a driver opening the right browser.
        note = { ...note, undecodable: msg.note || "" };
        paintState();
        break;
      case "unplugged":
        state = "idle";
        media = {};
        cover = null;
        note = { ...note, phone: "" };
        paintMedia(); paintCover(); paintState();
        break;
      case "failure":
        state = "failed";
        note = { ...note, error: msg.error || "" };
        paintState();
        break;
      case "media": {
        const p = msg.message && msg.message.payload;
        if (!p) return;
        if (p.type === MEDIA_DATA && p.media) {
          // Merged, not replaced: the dongle sends partial updates, and a
          // play-position tick that arrived without the title would otherwise
          // blank the track name once a second.
          media = { ...media, ...p.media };
          paintMedia();
        } else if (p.type === MEDIA_ALBUM_COVER && p.base64Image) {
          cover = p.base64Image;
          paintCover();
        }
        break;
      }
      default:
        break;
    }
  }

  // --------------------------------------------------------------- sizing

  function resize() {
    if (mode === "hidden") return;
    const r = stage.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = Math.round(r.width * dpr);
    const hgt = Math.round(r.height * dpr);
    if (canvas.width !== w || canvas.height !== hgt) {
      canvas.width = w;
      canvas.height = hgt;
      // The real driver reconfigures the dongle here, because the phone
      // renders to whatever size it is told and letterboxing our own panel
      // would be a choice nobody asked for.
      if (source && source.send) source.send({ type: "resize", width: w, height: hgt });
    }
  }

  const ro = typeof ResizeObserver !== "undefined"
    ? new ResizeObserver(() => resize()) : null;

  // ------------------------------------------------------------------ api

  const api = {
    el: root,

    mount(host) {
      host.appendChild(root);
      if (ro) ro.observe(stage);
      paintMedia(); paintCover(); paintState(); paintOverlays();
      return api;
    },

    // THE CAR ALERTS, ACTUALLY ARRIVING.
    //
    // Drawing the watchdog's alerts over the phone screen is named at the top
    // of this file as the one capability a five-thousand-dollar head unit
    // cannot match -- and until now nothing called alert(). The overlay
    // existed, was painted, was styled, and was never given anything to show.
    //
    // The poll lives here rather than in a view because the layer outlives
    // every view: the point of it is that checking your coolant temperature
    // does not stop the music, and by the same token an alert must reach the
    // phone screen whether or not anybody is looking at a gauge. It only runs
    // while the layer is actually drawn, because polling on behalf of a hidden
    // surface is spending a request on nothing.
    watchAlerts(everyMs = 20000) {
        const tick = async () => {
          if (mode === "hidden") return;
          try {
            const { records } = await carApi.alerts(5);
            const fresh = (records || []).filter(
              (r) => Date.now() / 1000 - r.at < 900
                     && (r.payload || {}).urgency !== "low");
            const keep = new Set();
            for (const r of fresh) {
              const p = r.payload || {};
              const kind = p.kind || r.label || String(r.at);
              keep.add(kind);
              api.alert({ kind, urgency: p.urgency || "normal",
                          title: p.title || r.label, body: p.body || "" });
            }
            // A rule that stopped firing takes its overlay with it. The
            // watchdog's rules have hysteresis and re-raise, so a stale
            // warning left on a screen is a warning nobody trusts next time.
            for (const kind of [...overlays.keys()]) {
              if (!keep.has(kind)) api.clearAlert(kind);
            }
          } catch { /* the watchdog may not be running; that is not an error */ }
        };
        tick();
        alertTimer = setInterval(tick, everyMs);
        return api;
      },

    // Exposed so a caller can ask what is on screen before replacing it. The
    // app re-checks for an adapter each time the phone screen is opened, and
    // it must not interrupt a phone that is already connected to do it.
    get source() { return source; },

    setSource(src) {
      if (source) { try { source.stop(); } catch { /* already gone */ } }
      if (off) { off(); off = null; }
      note = {};
      decoded = false;
      source = src || mockSource();
      off = source.on(onMessage);
      paintState();
      return api;
    },

    start() {
      if (!source) api.setSource(opts.source || mockSource());
      state = "connecting";
      paintState();
      resize();
      try {
        source.start(canvas);
      } catch (e) {
        state = "failed";
        statusLine.textContent = String(e.message || e);
        root.dataset.state = "failed";
      }
      return api;
    },

    stop() {
      if (source) { try { source.stop(); } catch { /* fine */ } }
      state = "idle";
      paintState();
      return api;
    },

    get mode() { return mode; },
    setMode(next) {
      if (!MODES.includes(next)) return api;
      mode = next;
      root.dataset.mode = mode;
      root.hidden = mode === "hidden";
      // Deferred: the element has no size until the browser has laid out the
      // new mode, and asking on this tick reconfigures to the OLD geometry.
      requestAnimationFrame(resize);
      return api;
    },

    // Car alerts drawn over the phone screen. Keyed, so a rule that fires and
    // clears removes its own overlay rather than stacking duplicates — the
    // watchdog's rules have hysteresis and will re-raise.
    alert(a) {
      if (!a || !a.kind) return api;
      overlays.set(a.kind, a);
      paintOverlays();
      return api;
    },
    clearAlert(kind) {
      overlays.delete(kind);
      paintOverlays();
      return api;
    },

    get state() { return state; },
    get media() { return { ...media }; },

    destroy() {
      if (alertTimer) { clearInterval(alertTimer); alertTimer = null; }
      api.stop();
      if (off) off();
      if (ro) ro.disconnect();
      rail.destroy();
      root.remove();
    },
  };

  return api;
}
