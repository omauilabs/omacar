// H.264 access units in, pixels on a canvas out, by whichever route works.
//
// THERE ARE TWO ROUTES AND THE FIRST ONE SILENTLY DOES NOTHING ON SOME
// BROWSERS, which is the only reason there are two.
//
//   WebCodecs   the right answer. A VideoDecoder takes access units directly,
//               with no container and no muxing, at the lowest latency the
//               machine can manage.
//
//   MediaSource the fallback. The same bytes wrapped as fragmented MP4 and
//               handed to a <video> element, which is a different decoder
//               inside the same browser.
//
// Measured, not assumed: a Chromium with no hardware decoder answers
// `VideoDecoder.isConfigSupported` with yes, configures without complaint,
// accepts every chunk, and returns no frames -- there is no software fallback
// wired up for H.264 there. The same browser, same machine, same bytes, plays
// them through a <video> element perfectly. So the capability check cannot be
// believed and the only honest test is whether a picture came back.
//
// Hence the shape of this file: try the good route, watch for pictures, and
// switch if none arrive. Then say which route is carrying the picture, because
// a driver looking at a working screen still deserves to know.

import { createMuxer } from "./fmp4.js";

// How much to give the good route before concluding it is not going to work.
// Long enough that a slow first key frame is not mistaken for a broken
// decoder; short enough that nobody is looking at a black screen wondering.
//
// TWO MEASURES, BECAUSE THE CLOCK IS NOT ALWAYS RUNNING. A frame count says
// the same thing as a stopwatch -- sixty pictures at twenty a second is three
// seconds -- and it keeps saying it in a headless browser on a virtual clock,
// where performance.now() can stay frozen for the whole of a stream. The
// count found this bug; the stopwatch alone had silently never fired.
const PATIENCE_MS = 1500;
const PATIENCE_UNITS = 20;
const PATIENCE_FRAMES = 60;

export const NAL_IDR = 5;
export const NAL_SPS = 7;

// The NAL unit types in an Annex-B buffer. Handles both the 3-byte and 4-byte
// start codes, because a stream carries whichever the encoder felt like.
export function nalTypes(bytes) {
  const out = [];
  for (let i = 0; i + 3 < bytes.length; i++) {
    if (bytes[i] !== 0 || bytes[i + 1] !== 0) continue;
    let at = -1;
    if (bytes[i + 2] === 1) at = i + 3;
    else if (bytes[i + 2] === 0 && bytes[i + 3] === 1) at = i + 4;
    if (at >= 0 && at < bytes.length) out.push(bytes[at] & 0x1F);
  }
  return out;
}

// An H.264 access unit a decoder can START from.
//
// The obvious test is "does it carry an IDR slice", NAL type 5. That is not
// enough, and assuming it was would have shipped a black screen: an encoder is
// free to emit its recoverable frames as type 1 non-IDR I-slices. The real
// question is whether the unit carries the parameter sets the decoder needs,
// so an access unit containing an SPS (7) counts whether the slice after it is
// a 5 or a 1.
export function looksLikeKeyFrame(bytes) {
  return nalTypes(bytes).some((t) => t === NAL_IDR || t === NAL_SPS);
}

// ------------------------------------------------------------- the good route

function webcodecsRoute({ canvas, onPicture, onTrouble }) {
  if (typeof VideoDecoder === "undefined") return null;
  let decoder = null, configured = false, needKey = true, rebuilds = 0;
  let ctx = canvas ? canvas.getContext("2d") : null;
  let pictures = 0, refused = 0;

  function draw(frame) {
    if (!ctx) return;
    if (canvas.width !== frame.displayWidth || canvas.height !== frame.displayHeight) {
      canvas.width = frame.displayWidth;
      canvas.height = frame.displayHeight;
    }
    ctx.drawImage(frame, 0, 0);
  }

  function build() {
    decoder = new VideoDecoder({
      output: (frame) => {
        try {
          draw(frame);
          pictures += 1;
          if (pictures === 1) onPicture(frame.displayWidth, frame.displayHeight);
        } finally { frame.close(); }
      },
      // A decoder can be knocked over by one bad frame and be perfectly well
      // afterwards, so it is rebuilt rather than mourned. What it must not do
      // is go round that loop forever: a browser that cannot decode this codec
      // fails identically every time and would hide behind an endless retry.
      error: (e) => {
        try { if (decoder && decoder.state !== "closed") decoder.close(); }
        catch { /* already gone */ }
        decoder = null; configured = false; needKey = true;
        rebuilds += 1;
        if (rebuilds > 2) onTrouble(String(e && e.message || e));
      },
    });
    decoder.configure({ codec: "avc1.42E01E", optimizeForLatency: true });
    configured = true;
  }

  return {
    name: "webcodecs",
    get pictures() { return pictures; },
    get stats() { return { pictures, refused, rebuilds }; },
    push(unit, isKey) {
      if (rebuilds > 2) return;
      if (!decoder) {
        if (!isKey) return;
        try { build(); } catch (e) { onTrouble(String(e && e.message || e)); return; }
      }
      if (needKey && !isKey) return;
      try {
        decoder.decode(new EncodedVideoChunk({
          type: isKey ? "key" : "delta",
          timestamp: performance.now() * 1000,
          data: unit,
        }));
        needKey = false;
      } catch (e) {
        // A decoder that has lost its place asks for a key frame, and waiting
        // for one is the whole recovery.
        needKey = true;
        refused += 1;
        if (refused > 200) onTrouble(String(e && e.message || e));
      }
    },
    stop() {
      try { if (decoder && decoder.state !== "closed") decoder.close(); }
      catch { /* fine */ }
      decoder = null; configured = false;
    },
  };
}

// ------------------------------------------------------------- the fallback

function mediaSourceRoute({ canvas, fps, onPicture, onTrouble }) {
  if (typeof MediaSource === "undefined") return null;
  const mux = createMuxer();
  const ticks = Math.max(1, Math.round(mux.timescale / (fps || 20)));
  const ctx = canvas ? canvas.getContext("2d") : null;
  const video = document.createElement("video");
  video.muted = true;
  video.playsInline = true;
  video.preload = "auto";
  // Off the layout and out of the way. The element is never seen; it exists to
  // hold a decoder, and its pictures are drawn onto the canvas the rest of the
  // app already knows about.
  video.setAttribute("aria-hidden", "true");
  video.style.cssText = "position:absolute;width:1px;height:1px;opacity:0;"
    + "pointer-events:none;left:-9999px;top:0";
  document.body.appendChild(video);

  const source = new MediaSource();
  video.src = URL.createObjectURL(source);
  let buffer = null, queue = [], pictures = 0, raf = 0, stopped = false;
  let opened = false, playError = "", queued = 0, appended = 0;

  function pump() {
    if (!buffer || buffer.updating || !queue.length || stopped) return;
    try { buffer.appendBuffer(queue.shift()); }
    catch (e) { playError = "append: " + String(e && e.message || e);
                onTrouble(playError); }
  }

  function trim() {
    if (!buffer || buffer.updating || !buffer.buffered.length) return;
    const end = buffer.buffered.end(buffer.buffered.length - 1);
    const start = buffer.buffered.start(0);
    // A live stream that keeps everything it has ever shown grows without
    // bound; four seconds is more than enough to decode from and nothing is
    // ever seeked back to.
    if (end - start > 4) {
      try { buffer.remove(start, end - 2); } catch { /* it will come round again */ }
      return;
    }
    // AND IT MUST NOT FALL BEHIND. A <video> element plays at its own pace and
    // will happily drift a second behind a live source, which on a phone
    // screen is a touch that lands somewhere the driver is no longer looking.
    if (end - video.currentTime > 0.6) {
      try { video.currentTime = end - 0.1; } catch { /* not seekable yet */ }
    }
  }

  function paint() {
    if (stopped) return;
    raf = requestAnimationFrame(paint);
    if (!ctx || !video.videoWidth) return;
    if (canvas.width !== video.videoWidth || canvas.height !== video.videoHeight) {
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
    }
    ctx.drawImage(video, 0, 0);
    pictures += 1;
    if (pictures === 1) onPicture(video.videoWidth, video.videoHeight);
  }

  // TWO THINGS HAVE TO HAPPEN AND THEY RACE. The media source opens on its own
  // schedule, and the codec cannot be named until a key frame has taught the
  // muxer what it is. Whichever lands second does the opening, which is why
  // this is a function called from both places and not a one-shot listener.
  function tryOpen() {
    if (opened || stopped || !mux.ready || source.readyState !== "open") return;
    opened = true;
    try {
      buffer = source.addSourceBuffer(`video/mp4; codecs="${mux.ready.codec}"`);
      // Sequence mode stamps each fragment after the last one, which is what a
      // live stream with no wall-clock of its own wants.
      buffer.mode = "sequence";
      buffer.addEventListener("updateend", () => { appended += 1; trim(); pump(); });
      buffer.addEventListener("error", () => {
        playError = playError || "the media buffer refused a segment";
        onTrouble(playError);
      });
      // IN FRONT OF ANYTHING ALREADY WAITING. Segments can pile up while the
      // media source is still opening -- it opens on its own schedule, and on
      // a fast stream every frame can arrive first -- and a buffer handed a
      // media segment before its initialisation segment rejects the lot.
      queue.unshift(mux.ready.init);
      pump();
      video.play().catch((e) => { playError = String(e && e.message || e); });
      paint();
    } catch (e) { onTrouble(String(e && e.message || e)); }
  }

  source.addEventListener("sourceopen", tryOpen);

  return {
    name: "mediasource",
    get pictures() { return pictures; },
    get stats() {
      return {
        pictures,
        // Enough to tell "the element never started" from "the element is
        // playing and the picture is black", which look identical on screen.
        readyState: video.readyState,
        paused: video.paused,
        at: Number(video.currentTime.toFixed(2)),
        buffered: buffer && buffer.buffered.length
          ? Number(buffer.buffered.end(buffer.buffered.length - 1).toFixed(2)) : 0,
        played: video.played.length ? Number(video.played.end(0).toFixed(2)) : 0,
        queued, appended, source: source.readyState,
        why: playError,
      };
    },
    // Whether there is still buffered media that might yet become a picture.
    // A <video> element takes a moment to start, and concluding "nothing was
    // decoded" while it is still spinning up would be a claim retracted by the
    // next frame.
    pending() {
      // "Is there still reason to expect a picture." Three ways there is: the
      // media source has not finished opening, segments are waiting to go in,
      // or media is buffered that the element has not shown yet.
      if (stopped) return false;
      if (!opened || !buffer) return true;
      if (queue.length || buffer.updating) return true;
      return buffer.buffered.length > 0 && video.readyState >= 1;
    },
    push(unit, isKey) {
      if (stopped) return;
      // The parameter sets have to be in hand before a source buffer can be
      // opened at all, so the first key frame is what starts everything.
      if (!mux.ready) mux.learn(unit);
      // TRIED EVERY TIME, NOT ONLY WHILE LEARNING. This used to sit inside the
      // branch above, so once the muxer knew the codec the buffer could only
      // ever be opened by the media source's own event -- and on a stream
      // arriving faster than that event, every frame was dropped and the
      // screen stayed black with a perfectly healthy connection behind it.
      tryOpen();
      if (!mux.ready) return;
      // Queued whether or not the buffer exists yet: pump() is a no-op until
      // it does, and dropping these would throw away exactly the frames that
      // arrive during the open.
      queue.push(mux.segment(unit, isKey, ticks));
      queued += 1;
      if (appended > 0 && queue.length > 90) queue.splice(0, queue.length - 45);
      pump();
    },
    stop() {
      stopped = true;
      cancelAnimationFrame(raf);
      try { if (source.readyState === "open") source.endOfStream(); } catch { /* fine */ }
      try { video.pause(); } catch { /* fine */ }
      try { URL.revokeObjectURL(video.src); } catch { /* fine */ }
      video.remove();
    },
  };
}

// ------------------------------------------------------------------ the sink

export function videoSink({ canvas, fps, onPicture, onRoute, onUndecodable }) {
  let route = null, switched = false, done = false;
  let units = 0, firstAt = 0, name = "", endTimer = 0;

  // EVERYTHING SINCE THE LAST KEY FRAME, KEPT, so that changing route does not
  // cost a wait for the next one. On a real adapter key frames come about five
  // seconds apart, and five seconds of black while the screen quietly switches
  // decoders is the failure this whole file exists to avoid. Bounded, because
  // a stream that never sends another key frame must not become a memory leak.
  const SINCE_KEY_MAX = 120;
  let sinceKey = [], sinceKeyBytes = 0;

  function remember(unit, isKey) {
    if (isKey) { sinceKey = []; sinceKeyBytes = 0; }
    if (sinceKey.length >= SINCE_KEY_MAX || sinceKeyBytes > (4 << 20)) {
      sinceKey = []; sinceKeyBytes = 0;      // give up rather than grow
      return;
    }
    sinceKey.push(unit);
    sinceKeyBytes += unit.length;
  }

  const say = (fn, ...a) => { try { if (fn) fn(...a); } catch { /* the caller's problem */ } };

  function announce(r, why) {
    if (!r || name === r.name) return;
    name = r.name;
    say(onRoute, r.name, why || "");
  }

  function toFallback(why) {
    if (switched || done) return;
    switched = true;
    if (route) route.stop();
    route = mediaSourceRoute({
      canvas, fps,
      onPicture: (w, h) => say(onPicture, w, h),
      onTrouble: (e) => {
        // BOTH ROUTES GONE. There is nothing else to try and nothing to be
        // gained by hiding it, so the screen is told in words rather than left
        // black.
        if (done) return;
        done = true;
        say(onUndecodable, "This browser decoded none of the picture. "
          + units + " frames arrived and neither of its two H.264 decoders "
          + "would take them" + (e ? " (" + e + ")" : "") + ". The adapter is "
          + "not the problem.");
      },
    });
    if (!route) {
      done = true;
      say(onUndecodable, "This browser has no way to decode H.264: no "
        + "WebCodecs and no MediaSource. " + units + " frames arrived and none "
        + "of them can be shown here.");
      return;
    }
    announce(route, why);
    for (const u of sinceKey) route.push(u, looksLikeKeyFrame(u));
  }

  route = webcodecsRoute({
    canvas,
    onPicture: (w, h) => say(onPicture, w, h),
    onTrouble: (why) => toFallback(why),
  });
  if (route) announce(route);
  else toFallback("this browser has no WebCodecs");

  return {
    get route() { return name; },
    get stats() { return { units, route: name, ...(route ? route.stats : {}) }; },
    push(unit) {
      if (!route) return;
      units += 1;
      if (!firstAt) firstAt = performance.now();
      const isKey = looksLikeKeyFrame(unit);
      remember(unit, isKey);
      route.push(unit, isKey);
      // THE ONLY HONEST CAPABILITY TEST: did a picture come back. Asking the
      // browser first gives a yes that means nothing.
      if (!switched && route.pictures === 0
          && (units >= PATIENCE_FRAMES
              || (units > PATIENCE_UNITS
                  && performance.now() - firstAt > PATIENCE_MS))) {
        toFallback("no picture after " + units + " frames");
      }
    },
    // A STREAM THAT ENDED WITH NOTHING TO SHOW IS TOLD, NOT SWITCHED. Changing
    // route here would be theatre: no more frames are coming, so the new
    // decoder would have nothing to decode. What is owed at this point is the
    // sentence saying so.
    ended() {
      if (done || !units) return false;
      if (route && route.pictures > 0) return false;
      // SAYING NOTHING ARRIVED AND THEN SHOWING A PICTURE IS WORSE THAN
      // WAITING. The fallback is a <video> element with a few seconds of
      // buffered media in front of it, and it is entitled to the moment it
      // takes to start. So the verdict is deferred while it is still holding
      // something it might show, and given once it is not.
      // MEASURED IN RENDERED FRAMES, NOT SECONDS.
      //
      // A stopwatch was the obvious way to be patient and it is wrong twice
      // over. A freshly switched route has nothing buffered yet, so asking it
      // straight away gets a no; and a browser whose clock is being held still
      // -- a headless one, a backgrounded tab -- burns the whole allowance in
      // an instant while rendering nothing, so the verdict lands moments
      // before the picture it says never came.
      //
      // Animation frames are the honest unit: they tick exactly when the
      // browser is in a position to paint, which is exactly when a picture
      // could appear. If they are not ticking, neither is the verdict, and
      // that is correct rather than a compromise.
      const FLOOR = 30, CEILING = 240;
      let tries = 0;
      const again = (fn) => (typeof requestAnimationFrame === "function"
        ? requestAnimationFrame(fn) : setTimeout(fn, 16));
      const settle = () => {
        if (done) return;
        if (route && route.pictures > 0) return;
        const holding = route && route.pending && route.pending();
        if (tries < FLOOR || (holding && tries < CEILING)) {
          tries += 1;
          endTimer = again(settle);
          return;
        }
        done = true;
        say(onUndecodable, units + " frames arrived and none of them reached "
          + "the screen: " + (switched
            ? "both of this browser's H.264 decoders refused the stream"
            : "this browser's H.264 decoder produced nothing")
          + ". The adapter is not the problem.");
      };
      settle();
      return true;
    },
    stop() {
      done = true;
      if (typeof cancelAnimationFrame === "function") cancelAnimationFrame(endTimer);
      clearTimeout(endTimer);
      if (route) route.stop();
      route = null;
    },
  };
}
