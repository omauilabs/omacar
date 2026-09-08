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

import { createMuxer, isSyncSample, nalsOf, parseSPS } from "./fmp4.js";

// The codec string an access unit's own parameter set describes. A constant is
// wrong here: "avc1.42E01E" is level 3.0, whose ceiling is 1620 macroblocks,
// and the 800x640 an adapter is configured for is 2000 of them. Browsers
// usually reconfigure from the in-band parameter sets and get away with it;
// a decoder that sizes its buffers from the declared level is exactly the kind
// that fails by returning no frames and saying nothing.
function codecOf(unit, fallback) {
  for (const nal of nalsOf(unit)) {
    if ((nal[0] & 0x1F) !== 7) continue;
    const s = parseSPS(nal);
    if (!s) continue;
    return "avc1." + [s.profile, s.compat, s.level]
      .map((v) => v.toString(16).padStart(2, "0")).join("");
  }
  return fallback;
}

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
  let i = 0;
  // ADVANCED PAST EACH START CODE RATHER THAN STEPPING ONE BYTE.
  // A four-byte code matched twice -- once at its own offset and once at the
  // next, where its last three bytes look like a three-byte code -- so every
  // NAL was reported twice. `.some()` did not care; a caller that counted
  // would have been wrong, and this reader is exported for callers to come.
  while (i + 2 < bytes.length) {
    if (bytes[i] === 0 && bytes[i + 1] === 0) {
      if (bytes[i + 2] === 1) {
        if (i + 3 < bytes.length) out.push(bytes[i + 3] & 0x1F);
        i += 3;
        continue;
      }
      if (bytes[i + 2] === 0 && i + 3 < bytes.length && bytes[i + 3] === 1) {
        if (i + 4 < bytes.length) out.push(bytes[i + 4] & 0x1F);
        i += 4;
        continue;
      }
    }
    i += 1;
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
  let codec = "";
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
    decoder.configure({ codec: codec || "avc1.42E01E", optimizeForLatency: true });
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
        codec = codecOf(unit, codec);
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
  let shown = -1, syncSeen = false, generation = 0, lastAt = 0;

  function pump() {
    if (!buffer || buffer.updating || !queue.length || stopped) return;
    try { buffer.appendBuffer(queue.shift()); }
    catch (e) { playError = "append: " + String(e && e.message || e);
                onTrouble(playError); }
  }

  // How close to the live edge to sit after a catch-up seek. NOT AS CLOSE AS
  // POSSIBLE: a hundred milliseconds is two frames, which is not enough
  // lookahead for the element to reach a playable state, so it plays them,
  // underruns, stalls, drifts, and gets seeked again -- a sawtooth of stalls
  // that a six-second recording is too short to reveal.
  const EDGE = 0.4;
  const DRIFT = 1.2;

  function trim() {
    if (!buffer || buffer.updating || !buffer.buffered.length) return;
    const end = buffer.buffered.end(buffer.buffered.length - 1);
    const start = buffer.buffered.start(0);
    // A live stream that keeps everything it has ever shown grows without
    // bound; four seconds is more than enough to decode from and nothing is
    // ever seeked back to.
    if (end - start > 4) {
      // NEVER THE RANGE THE ELEMENT IS PLAYING. Removing across the playhead
      // yanks the media out from under a playing element: readyState drops and
      // playback halts until the next append rescues it, which the driver sees
      // as a hitch every few seconds.
      const safe = Math.min(end - 2, video.currentTime - 0.5);
      if (safe > start) {
        try { buffer.remove(start, safe); } catch { /* it comes round again */ }
        return;
      }
    }
    // AND IT MUST NOT FALL BEHIND. A <video> element plays at its own pace and
    // will happily drift a second behind a live source, which on a phone
    // screen is a touch that lands somewhere the driver is no longer looking.
    if (end - video.currentTime > DRIFT) {
      try { video.currentTime = end - EDGE; } catch { /* not seekable yet */ }
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
    // A PICTURE IS A NEW FRAME, NOT AN ANIMATION TICK.
    //
    // This counted every repaint, so a stalled element showing a thirty-second
    // old frame counted sixty pictures a second and read as perfectly healthy
    // to everything that asks. The element's own clock is the honest signal:
    // it moves when a new frame is presented and stands still when one is not.
    if (video.currentTime !== shown) {
      shown = video.currentTime;
      pictures += 1;
      if (pictures === 1) onPicture(video.videoWidth, video.videoHeight);
    }
  }

  // TWO THINGS HAVE TO HAPPEN AND THEY RACE. The media source opens on its own
  // schedule, and the codec cannot be named until a key frame has taught the
  // muxer what it is. Whichever lands second does the opening, which is why
  // this is a function called from both places and not a one-shot listener.
  function tryOpen() {
    if (opened || stopped || !mux.ready || source.readyState !== "open") return;
    try {
      buffer = source.addSourceBuffer(`video/mp4; codecs="${mux.ready.codec}"`);
      // SET ONLY ONCE IT WORKED. Setting it first meant that a refused codec
      // string closed the door permanently: no retry, no buffer, and a push
      // loop that went on building segments into a queue nothing would ever
      // drain -- a couple of gigabytes an hour, quietly.
      opened = true;
      generation = mux.ready.generation || 1;
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
      // LEARNED EVERY TIME A PARAMETER SET COULD BE PRESENT, not only until it
      // knows something. A phone that rotates sends a new sequence parameter
      // set mid-stream, and a muxer that latched the first one goes on
      // declaring a size the stream stopped using -- which the source buffer
      // then refuses, with no reason given.
      if (isKey || !mux.ready) {
        const before = mux.ready && mux.ready.generation;
        mux.learn(unit);
        if (mux.ready && opened && mux.ready.generation !== generation
            && before !== undefined) {
          // A different stream in the same session. There is no way to change
          // an open buffer's initialisation, so the route says so and is
          // rebuilt around the new one.
          onTrouble("the stream changed shape");
          return;
        }
      }
      // TRIED EVERY TIME, NOT ONLY WHILE LEARNING. This used to sit inside the
      // branch above, so once the muxer knew the codec the buffer could only
      // ever be opened by the media source's own event -- and on a stream
      // arriving faster than that event, every frame was dropped and the
      // screen stayed black with a perfectly healthy connection behind it.
      tryOpen();
      if (!mux.ready) return;
      // THE DURATION IS MEASURED, NOT CONFIGURED. Stamping every picture with
      // exactly one twentieth of a second means the media timeline and the
      // wall clock diverge by however much the adapter's real rate differs
      // from the number in a config -- minutes of skew over a drive, which the
      // catch-up seek then papers over by stalling and seeking, over and over.
      const now = performance.now();
      let step = ticks;
      if (lastAt) {
        const measured = Math.round((now - lastAt) * (mux.timescale / 1000));
        // Believed only when it is plausible. A stall, a scheduling hiccup or
        // a frozen clock all produce numbers that would wreck the timeline.
        if (measured >= ticks / 4 && measured <= ticks * 4) step = measured;
      }
      lastAt = now;

      // A SYNC SAMPLE IS AN IDR, not merely something a decoder could start
      // from. Many encoders repeat the parameter sets in front of every
      // picture, and declaring each of those a random-access point invites the
      // player to splice into the middle of a group and show a smear.
      const sync = isSyncSample(unit) || (!syncSeen && isKey);
      if (sync) syncSeen = true;
      queue.push(mux.segment(unit, sync, step));
      queued += 1;
      // Capped whatever else is true. The old guard on `appended` meant the
      // one situation where the queue can actually run away -- a buffer that
      // never opened -- was the one where the cap was switched off.
      if (queue.length > 90) {
        // Never the initialisation segment, which has to go in first.
        const from = (appended === 0 && queue.length) ? 1 : 0;
        queue.splice(from, queue.length - 45);
      }
      pump();
    },
    stop() {
      stopped = true;
      cancelAnimationFrame(raf);
      try { if (source.readyState === "open") source.endOfStream(); } catch { /* fine */ }
      try { video.pause(); } catch { /* fine */ }
      try { URL.revokeObjectURL(video.src); } catch { /* fine */ }
      // AND THE ELEMENT LETS GO OF ITS DECODER. Revoking the URL and removing
      // the node does not: the element keeps the media resource it already
      // loaded, so an abandoned one holds a hardware decoder open. Clearing
      // the source and reloading is what actually releases it.
      try { video.removeAttribute("src"); video.load(); } catch { /* fine */ }
      video.remove();
    },
  };
}

// ------------------------------------------------------------------ the sink

export function videoSink({ canvas, fps, onPicture, onRoute, onUndecodable }) {
  let route = null, switched = false, done = false;
  let units = 0, firstAt = 0, name = "", endTimer = 0, endTimerIsFrame = false;
  let watchFrom = 0;

  // EVERYTHING SINCE THE LAST KEY FRAME, KEPT, so that changing route does not
  // cost a wait for the next one. On a real adapter key frames come about five
  // seconds apart, and five seconds of black while the screen quietly switches
  // decoders is the failure this whole file exists to avoid. Bounded, because
  // a stream that never sends another key frame must not become a memory leak.
  const SINCE_KEY_MAX = 240;
  let sinceKey = [], sinceKeyBytes = 0, sinceKeyGood = false;

  function remember(unit, isKey) {
    if (isKey) { sinceKey = []; sinceKeyBytes = 0; sinceKeyGood = true; }
    // OVER THE CAP, THE BUFFER IS NOT MERELY EMPTIED -- IT IS MARKED USELESS.
    // Emptying it and carrying on meant the next ordinary picture became
    // element zero, so the buffer began in the middle of a group; replaying it
    // into a fresh decoder taught it nothing and every unit was silently
    // dropped. A buffer that cannot start a decoder is worse than no buffer,
    // because it looks like one.
    if (!sinceKeyGood) return;
    if (sinceKey.length >= SINCE_KEY_MAX || sinceKeyBytes > (8 << 20)) {
      sinceKey = []; sinceKeyBytes = 0; sinceKeyGood = false;
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

  // How many times the fallback may be rebuilt before the trouble is treated
  // as permanent. A resolution change and a buffer that choked on one segment
  // are both recoverable by starting again; a browser that cannot decode this
  // codec fails identically every time.
  const REBUILDS = 3;
  let rebuilt = 0, shownAnything = false;

  function toFallback(why, { rebuilding } = {}) {
    if (done) return;
    if (switched && !rebuilding) return;
    switched = true;
    if (route) {
      if (route.pictures > 0) shownAnything = true;
      route.stop();
    }
    route = mediaSourceRoute({
      canvas, fps,
      onPicture: (w, h) => { shownAnything = true; say(onPicture, w, h); },
      onTrouble: (e) => {
        if (done) return;
        // TROUBLE AFTER A PICTURE IS NOT "THIS CANNOT DECODE".
        //
        // There was no check here, so any single hiccup in an hour -- one
        // refused segment, a resolution change, a buffer detached by the
        // element -- printed "decoded none of the picture" over a live phone
        // screen that was still updating underneath it. The claim has to earn
        // itself: something recoverable gets the route rebuilt, and only a
        // route that has never shown anything gets the verdict.
        if (rebuilt < REBUILDS) {
          rebuilt += 1;
          toFallback("rebuilt after " + e, { rebuilding: true });
          return;
        }
        if (shownAnything) {
          say(onRoute, "mediasource", "gave up after " + e);
          return;
        }
        done = true;
        say(onUndecodable, units + " frames arrived and neither of this "
          + "browser's H.264 decoders would show any of them"
          + (e ? " (" + e + ")" : "") + ". The adapter is not the problem.");
      },
    });
    if (!route) {
      done = true;
      say(onUndecodable, "This browser has no way to decode H.264: no "
        + "WebCodecs and no MediaSource. " + units + " frames arrived and none "
        + "of them can be shown here.");
      return;
    }
    name = "";                       // so the change is announced even on a rebuild
    announce(route, why);
    // RE-ARMED. Without this the watchdog below was disabled the moment the
    // route changed, so a fallback that also produced nothing left a black
    // screen with no message at all on a live stream -- the very failure this
    // file exists to prevent, moved one route to the right.
    watchFrom = units;
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
      const since = units - watchFrom;
      if (route.pictures === 0
          && (since >= PATIENCE_FRAMES
              || (since > PATIENCE_UNITS
                  && performance.now() - firstAt > PATIENCE_MS))) {
        if (!switched) {
          toFallback("no picture after " + since + " frames");
        } else if (!done && !shownAnything) {
          done = true;
          say(onUndecodable, units + " frames arrived and neither of this "
            + "browser's H.264 decoders showed any of them. The adapter is "
            + "not the problem.");
        } else {
          watchFrom = units;         // it has worked before; keep watching
        }
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
      const again = (fn) => {
        // The two live in different pools, so cancelling one with the other's
        // canceller can kill an unrelated timer that happens to share the
        // number. Which kind this is has to be remembered.
        if (typeof requestAnimationFrame === "function") {
          endTimerIsFrame = true;
          return requestAnimationFrame(fn);
        }
        endTimerIsFrame = false;
        return setTimeout(fn, 16);
      };
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
      if (endTimer) {
        if (endTimerIsFrame) cancelAnimationFrame(endTimer);
        else clearTimeout(endTimer);
        endTimer = 0;
      }
      if (route) route.stop();
      route = null;
    },
  };
}
