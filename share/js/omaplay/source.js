// OmaPlay — where the phone's screen comes from.
//
// WHAT OMAPLAY IS, AND WHAT IT IS NOT.
//
// It is a HOST for CarPlay and Android Auto, not a reimplementation of either.
// The phone renders those screens itself and hands over H.264 video; nobody
// gets to restyle Apple Maps, and any project claiming otherwise is confused
// about where the pixels come from. What we own is everything around and on
// top of that rectangle — the chrome, the layout, the now-playing panel, and
// the engine data overlaid on it, which is the one thing no head unit on the
// market can do.
//
// (And it is never called CarPlay or Android Auto in the interface. Those are
// Apple's and Google's marks and this repository is public.)
//
// WHY THERE IS A SOURCE ABSTRACTION AT ALL.
//
// Because the hardware is not here yet, and because waiting for it would mean
// designing the whole interface blind and then discovering the layout is wrong
// on the day the dongle arrives. A source is anything that emits the message
// shapes below and can paint into a canvas. The mock emits them from a script;
// the real one will emit them from a Carlinkit dongle over WebUSB. The layer
// above cannot tell the difference, which is the entire point.
//
// THE MESSAGE SHAPES ARE NOT INVENTED.
//
// They are transcribed from node-carplay's own source (MIT), src/web/
// CarplayWeb.ts and src/modules/messages/readable.ts, so that swapping the
// mock for the real driver is a substitution and not a rewrite:
//
//   { type: 'plugged' } | { type: 'unplugged' } | { type: 'failure' }
//   { type: 'video',   message: { width, height, flags, length, data } }
//   { type: 'audio',   message: AudioData }
//   { type: 'media',   message: { payload } }
//   { type: 'command', message: Command }
//
// and MediaData's payload is one of:
//
//   { type: 1, media: { MediaSongName, MediaAlbumName, MediaArtistName,
//                       MediaAPPName, MediaSongDuration, MediaSongPlayTime } }
//   { type: 3, base64Image }        // album art
//
// Those exact key names, capitals and all, are the wire format. They are ugly
// and they are not ours to tidy.

import { videoSink } from "./decode.js";
import { withToken } from "../core.js";

export const MEDIA_DATA = 1;
export const MEDIA_ALBUM_COVER = 3;

// The Carlinkit dongles node-carplay knows about, from DongleDriver.knownDevices.
// Here so the real source and the "is it plugged in" check agree.
export const KNOWN_DEVICES = [
  { vendorId: 0x1314, productId: 0x1520 },
  { vendorId: 0x1314, productId: 0x1521 },
];

// What the phone is asked to render into. node-carplay's DEFAULT_CONFIG is
// 800x640 at 20fps, which is a guess about somebody else's screen; the real
// source will override width/height/dpi from the element it is drawing into,
// because the phone renders to whatever shape it is told and there is no
// reason to letterbox our own panel.
export const DEFAULT_CONFIG = {
  width: 800, height: 640, fps: 20, dpi: 160,
  nightMode: false, boxName: "OmaPlay", mediaDelay: 300,
};

function bus() {
  const fns = new Set();
  return {
    on(fn) { fns.add(fn); return () => fns.delete(fn); },
    emit(msg) { for (const fn of [...fns]) { try { fn(msg); } catch { /* one bad listener must not stop the rest */ } } },
    clear() { fns.clear(); },
  };
}

// ---------------------------------------------------------------- the mock
//
// It does NOT pretend to be a phone. It cannot: producing convincing H.264 of
// a CarPlay screen would mean shipping a fake of somebody else's interface,
// which is both a trademark problem and exactly the invented-content this
// project forbids. So the mock paints something obviously synthetic and says
// MOCK across it, for the same reason demo mode wears an amber badge — the one
// unforgivable failure is somebody believing a fake is the real thing.
//
// What it IS faithful about is the message TIMING and SHAPES: a plug event
// after a delay, media metadata that changes track, a play position that
// advances, album art as base64. That is what the layout has to cope with, and
// that is what can be got right before the hardware exists.

const TRACKS = [
  { MediaSongName: "Mock Track One", MediaArtistName: "The Placeholders",
    MediaAlbumName: "Nothing Is Playing", MediaAPPName: "OmaPlay Mock",
    MediaSongDuration: 214 },
  { MediaSongName: "A Second Mock Track", MediaArtistName: "Test Signal",
    MediaAlbumName: "Nothing Is Playing", MediaAPPName: "OmaPlay Mock",
    MediaSongDuration: 178 },
  { MediaSongName: "Something With A Very Long Title That Has To Elide Somewhere",
    MediaArtistName: "An Artist With A Long Name Too", MediaAlbumName: "Edge Cases",
    MediaAPPName: "OmaPlay Mock", MediaSongDuration: 305 },
];

export function mockSource(opts = {}) {
  const b = bus();
  let raf = 0;
  let timer = 0;
  let canvas = null;
  let ctx = null;
  let t0 = 0;
  let track = 0;
  let elapsed = 0;
  let running = false;

  function pushTrack() {
    const media = { ...TRACKS[track % TRACKS.length], MediaSongPlayTime: Math.floor(elapsed) };
    b.emit({ type: "media", message: { payload: { type: MEDIA_DATA, media } } });
  }

  function paint() {
    if (!running) return;
    raf = requestAnimationFrame(paint);
    if (!ctx || !canvas.width) return;
    const w = canvas.width, h = canvas.height;
    const t = (performance.now() - t0) / 1000;

    // Deliberately not a phone screen. Moving bars so dropped frames and
    // stretched aspect ratios are visible at a glance, which is what this is
    // for while the layout is being built.
    const g = ctx.createLinearGradient(0, 0, w, h);
    g.addColorStop(0, "#101820");
    g.addColorStop(1, "#1a2630");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);

    const bars = 12;
    for (let i = 0; i < bars; i++) {
      const p = (i / bars + t * 0.08) % 1;
      ctx.fillStyle = `hsl(${200 + i * 6} 45% ${18 + 10 * Math.sin(t + i)}%)`;
      ctx.fillRect(p * w, 0, w / bars / 2, h);
    }
    // A moving box gives the eye something to judge smoothness by.
    const bx = (w - 90) * (0.5 + 0.5 * Math.sin(t * 0.9));
    const by = (h - 90) * (0.5 + 0.5 * Math.cos(t * 0.7));
    ctx.fillStyle = "#3d5a6c";
    ctx.fillRect(bx, by, 90, 90);

    ctx.fillStyle = "rgba(255,255,255,.82)";
    ctx.font = `600 ${Math.max(16, Math.round(w / 22))}px system-ui, sans-serif`;
    ctx.textAlign = "center";
    ctx.fillText("MOCK PHONE SCREEN", w / 2, h / 2 - 6);
    ctx.font = `400 ${Math.max(11, Math.round(w / 46))}px system-ui, sans-serif`;
    ctx.fillStyle = "rgba(255,255,255,.55)";
    ctx.fillText(`${w}×${h}  ·  no dongle connected`, w / 2, h / 2 + Math.round(w / 32));
  }

  return {
    kind: "mock",
    get running() { return running; },
    on: b.on,

    start(el) {
      if (running) return;
      canvas = el;
      ctx = canvas.getContext("2d");
      running = true;
      t0 = performance.now();
      // A real dongle takes a few seconds to hand the phone over. Emitting
      // `plugged` immediately would let a "connecting" state ship untested.
      setTimeout(() => { if (running) b.emit({ type: "plugged" }); },
                 opts.plugDelay ?? 1200);
      timer = setInterval(() => {
        if (!running) return;
        elapsed += 1;
        const dur = TRACKS[track % TRACKS.length].MediaSongDuration;
        if (elapsed >= dur) { elapsed = 0; track += 1; }
        pushTrack();
      }, 1000);
      raf = requestAnimationFrame(paint);
    },

    stop() {
      running = false;
      if (raf) cancelAnimationFrame(raf);
      if (timer) clearInterval(timer);
      raf = timer = 0;
      b.emit({ type: "unplugged" });
    },

    // The real driver takes touch and key commands back over USB. The mock
    // accepts and ignores them, so the input plumbing above can be written
    // and exercised now rather than after the hardware lands.
    send() { return false; },
  };
}

// ------------------------------------------------------------- the real one
//
// Deliberately a stub that REFUSES rather than a half-implementation that
// silently does nothing. It needs the node-carplay driver vendored and a
// Carlinkit CPC200-CCPA or CPC200-Autokit on the other end, plus a udev rule
// granting WebUSB access to vendor 0x1314. Until all three exist this must be
// unmistakably unavailable, because a source that quietly produces no frames
// looks exactly like a bug in the layer above it.

export function usbSource() {
  return {
    kind: "usb",
    running: false,
    on() { return () => {}; },
    start() {
      throw new Error(
        "OmaPlay's WebUSB source was never built. The dongle is driven from "
        + "the server instead — use dongleSource().");
    },
    stop() {},
    send() { return false; },
  };
}

// ---------------------------------------------------------------- the dongle
//
// WHY THE SERVER HOLDS THE USB DEVICE AND NOT THE BROWSER.
//
// The original plan was WebUSB: the page opens the dongle itself. It would
// work, and it costs two things this project is not willing to pay. WebUSB
// needs a user gesture and a device-picker dialog EVERY time the origin has
// not been granted the device, which on a tablet that boots into a kiosk means
// somebody taps through a permissions dialog before the phone appears. And it
// puts the driver in the browser, where this project has no bundler and would
// have to vendor a compiled dependency to get one.
//
// The server already owns the serial port for the same reason. It owns this
// too: Python reads the USB device, and the page receives a stream.
//
// WHAT COMES DOWN THE WIRE. One HTTP response, read as a stream, carrying
// self-delimiting records:
//
//     1 byte   kind    1 = H.264 access unit, 2 = a JSON event
//     4 bytes  length  big-endian
//     n bytes  payload
//
// Length-prefixed rather than newline-delimited because H.264 is binary and
// contains every byte value, and self-delimiting because a chunked HTTP body
// splits wherever it likes and a reader that assumed a record per chunk would
// work on a desk and tear frames in a car.
//
// THE DECODE IS THE BROWSER'S JOB, and it is good at it. WebCodecs hands H.264
// straight to the same hardware decoder the browser uses for video, which on a
// Surface is the difference between a warm tablet and a hot one. No library,
// no WASM, no build step.

const REC_VIDEO = 1;
const REC_EVENT = 2;

export function dongleSource(opts = {}) {
  const b = bus();
  let abort = null, canvas = null, sink = null;
  let running = false;
  // Counted because "the picture is black" has several causes and only one of
  // them is the adapter. These say which.
  const stats = { records: 0, video: 0, events: 0, pictures: 0, route: "" };

  function fail(why) {
    b.emit({ type: "failure", error: String(why && why.message || why) });
  }

  function makeSink() {
    return videoSink({
      canvas,
      fps: opts.fps || 20,
      onPicture: (w, h) => {
        stats.pictures += 1;
        b.emit({ type: "picture", width: w, height: h, route: stats.route });
      },
      onRoute: (name, why) => {
        stats.route = name;
        // A ROUTE CHANGE IS WORTH SAYING. The picture is identical either way,
        // but somebody looking at a working screen and wondering why it is
        // warm, or why a touch feels late, is owed the reason.
        b.emit({ type: "route", route: name, why: why || "" });
      },
      onUndecodable: (note) => b.emit({ type: "undecodable", note }),
    });
  }

  async function pump(res) {
    const reader = res.body.getReader();
    let buf = new Uint8Array(0);
    const need = (n) => buf.length >= n;
    const take = (n) => { const out = buf.subarray(0, n); buf = buf.subarray(n); return out; };

    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      if (value && value.length) {
        const merged = new Uint8Array(buf.length + value.length);
        merged.set(buf, 0); merged.set(value, buf.length);
        buf = merged;
      }
      // Drain every whole record the buffer now holds. A chunk boundary lands
      // wherever the kernel puts it, which is why the records carry their own
      // lengths and why this loop exists at all.
      for (;;) {
        if (!need(5)) break;
        const kind = buf[0];
        const len = (buf[1] << 24 | buf[2] << 16 | buf[3] << 8 | buf[4]) >>> 0;
        if (!need(5 + len)) break;
        take(5);
        const payload = take(len).slice();
        stats.records += 1;
        if (kind === REC_EVENT) {
          stats.events += 1;
          let msg = null;
          try { msg = JSON.parse(new TextDecoder().decode(payload)); } catch { msg = null; }
          if (msg) b.emit(msg);
        } else if (kind === REC_VIDEO) {
          stats.video += 1;
          if (!sink) sink = makeSink();
          sink.push(payload);
        }
      }
    }
  }

  return {
    kind: "dongle",
    get running() { return running; },
    get stats() { return { ...stats, ...(sink ? sink.stats : {}) }; },
    on(fn) { return b.on(fn); },

    async start(target) {
      canvas = target;
      running = true;
      // THE OLD SINK IS STOPPED, NOT DROPPED.
      //
      // A stream that ends -- the adapter re-enumerating, the server closing
      // the body, a wifi hiccup -- leaves the sink behind, and starting again
      // used to simply null the reference. The abandoned one keeps its own
      // animation loop painting a frozen frame onto the same canvas sixty
      // times a second, so the live picture and a still one alternate; and it
      // keeps a hidden video element and its decoder open. One more of each
      // per reconnect, for the length of a drive.
      if (sink) { try { sink.stop(); } catch { /* already gone */ } }
      sink = null;
      stats.records = stats.video = stats.events = stats.pictures = 0;
      stats.route = "";
      abort = new AbortController();
      try {
        const started = await fetch(withToken("/api/phone/start"), {
          method: "POST", signal: abort.signal,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode: opts.mode || "",
                                 path: opts.path || undefined,
                                 loop: opts.loop !== false,
                                 fps: opts.fps || undefined,
                                 width: canvas ? canvas.width : 800,
                                 height: canvas ? canvas.height : 640 }),
        }).then((r) => r.json());
        if (!started || started.error) {
          fail(started && started.error ? started.error : "the adapter would not open");
          running = false;
          return;
        }
        // WHAT THE SERVER SAID ABOUT ITSELF, PASSED ON UNEDITED. The screen
        // decides how loudly to hedge, and it can only do that if it is told
        // whether this is a recording and whether the driver has ever been
        // proven. Inventing either here would put the claim in the wrong file.
        b.emit({ type: "opening", note: started.note || "",
                 replay: !!started.replay, unproven: !!started.unproven });

        const res = await fetch(withToken("/api/phone/video"), { signal: abort.signal });
        if (!res.ok || !res.body) { fail(`the video stream returned ${res.status}`); running = false; return; }
        await pump(res);
      } catch (e) {
        if (!abort || !abort.signal.aborted) fail(e);
      } finally {
        running = false;
        if (sink) sink.ended();
        b.emit({ type: "unplugged" });
      }
    },

    stop() {
      running = false;
      try { if (abort) abort.abort(); } catch { /* already gone */ }
      if (sink) { try { sink.stop(); } catch { /* fine */ } sink = null; }
      fetch(withToken("/api/phone/stop"), { method: "POST" }).catch(() => {});
    },

    send(msg) {
      if (!msg || !running) return false;
      fetch(withToken("/api/phone/input"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(msg),
      }).catch(() => {});
      return true;
    },
  };
}

// The key-frame reader lives in decode.js now, beside the two decoders that
// use it. Re-exported here because that is where callers already look for it,
// and because a second copy of a fact is how the two come to disagree.
export { NAL_IDR, NAL_SPS, nalTypes, looksLikeKeyFrame } from "./decode.js";

export async function usbAvailable() {
  if (!navigator.usb) return false;
  try {
    const devices = await navigator.usb.getDevices();
    return devices.some((d) => KNOWN_DEVICES.some(
      (k) => k.vendorId === d.vendorId && k.productId === d.productId));
  } catch {
    return false;
  }
}
