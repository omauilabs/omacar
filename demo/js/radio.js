// Omarchy Radio, for the meetup demo: Ryan R. Hughes's seven songs, played from
// the tablet's own disk (doc/design/2026-09-30-meetup-demo.md).
//
// share/js/radio.js is the LIVE app's stream player, of radio.omarchy.org over
// the internet. This is not that, and it does not import it: the demo has no
// business opening a stream in a room with no signal, and the live player's
// state must never be touched by a demo. The two share only the output stage
// (share/js/audiobus.js), because the stage is what keeps music 12 dB below
// full scale with a limiter behind it, whoever is playing.
//
//   <audio> (one, reused for every track) -> the stage's music bus -> limiter -> out
//
// THE CONTRACT (shared-context.md, "Omarchy Radio"; Tasks 5, 6, 7 and 8 code
// against it, so these names are exact):
//
//   createRadio({ base, makeAudio, connect }) -> Radio
//   getRadio() -> Radio                     the page's one player
//   Radio: { load(), tracks, index, playing, play(i?), pause(), toggle(),
//            next(), prev(), seek(sec), position(), duration(), subscribe(fn) }
//
// Added, and harmless to anything that does not use them: `state()`, `error`,
// and a `resume` option (below).
//
// NOTHING HERE AUTOPLAYS. Loading a playlist, cueing a track, subscribing,
// moving on while paused and seeking never start a sample; a sample starts only
// from play(), or from a track ending while the radio was playing. The owner's
// child sleeps near the tablet, and a demo that speaks unasked is one that
// somebody will eventually unplug.

import { audioContext, musicIn, resume as resumeStage, dbToGain, MUSIC_DB } from "../../js/audiobus.js";

// Whole seconds as m:ss. Minutes do not roll into hours: a song is never one.
export function mmss(sec) {
  const s = Number.isFinite(sec) && sec > 0 ? Math.floor(sec) : 0;
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
}

// createMediaElementSource may be called ONCE for any element, ever: the
// second call throws. So the element is remembered.
const joined = new WeakSet();

// Put an element on the music bus. Called before its first sample, never
// after: an element that starts playing and is then rerouted plays its first
// moments straight to the speakers, at the element's own volume, and steps
// down 12 dB the instant the graph takes it.
export function connectToStage(el) {
  if (joined.has(el)) return;
  const node = audioContext().createMediaElementSource(el);
  joined.add(el);
  node.connect(musicIn());
}

const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));

// What a screen needs to paint, read from the Radio contract's own members
// rather than from state(), so it works on anything that keeps the contract
// (the screens are tested against a fake that does). `count` is 0 until the
// playlist has arrived.
export function snapshot(radio) {
  const tracks = radio.tracks || [];
  const t = tracks[radio.index];
  return {
    index: radio.index,
    title: t ? t.title : "",
    artist: t ? t.artist : "",
    playing: !!radio.playing,
    position: radio.position(),
    duration: radio.duration(),
    count: tracks.length,
    error: radio.error || null,
  };
}

export function createRadio({
  base = "/demo-media/radio/",
  makeAudio = () => new Audio(),
  connect = connectToStage,
  // The stage is resumed on every play, from the tap that asked for it: a
  // context that was built without a gesture starts suspended, and one
  // suspended stage is silence that looks exactly like a working radio. Its
  // own option, not part of `connect`, because connect happens once and a
  // play whose gesture was refused must be able to try again.
  resume = resumeStage,
} = {}) {
  // One array for the life of the radio, filled by load(), so a caller that
  // took `radio.tracks` before the playlist arrived is holding the real thing.
  const tracks = [];
  const subs = new Set();
  let el = null;
  let index = 0;
  let cued = -1;               // the track whose file the element holds
  let playing = false;
  let error = null;
  let loading = null;
  let wired = false;
  let timer = 0;

  const wrap = (i) => (tracks.length ? ((i % tracks.length) + tracks.length) % tracks.length : 0);
  const position = () => (el && Number.isFinite(el.currentTime) ? el.currentTime : 0);
  const duration = () => (el && Number.isFinite(el.duration) && el.duration > 0 ? el.duration : 0);

  function state() {
    const t = tracks[index];
    return {
      index,
      title: t ? t.title : "",
      artist: t ? t.artist : "",
      playing,
      position: position(),
      duration: duration(),
    };
  }

  function emit() {
    const s = state();
    for (const fn of [...subs]) {
      // A painter that throws must not stop the music or the painters after it.
      try { fn(s); } catch (e) { console.error(e); }
    }
  }

  // Ticks while it plays and somebody is listening, so a progress bar moves
  // without every screen running a clock of its own.
  function syncTimer() {
    const want = playing && subs.size > 0;
    if (want && !timer) timer = setInterval(emit, 500);
    else if (!want && timer) { clearInterval(timer); timer = 0; }
  }

  function failed(why) {
    playing = false;
    error = why;
    syncTimer();
    emit();
  }

  // The element, made on first need, so getRadio() alone costs nothing.
  function element() {
    if (el) return el;
    el = makeAudio();
    // BOTH BEFORE ANY src: crossOrigin set afterwards is too late and taints
    // the element for good, and a tainted element reads as silence once it is
    // routed through the stage.
    el.preload = "auto";
    el.crossOrigin = "anonymous";
    el.addEventListener("ended", () => { if (playing) step(1); });
    el.addEventListener("error", () => failed("That song did not load."));
    for (const type of ["loadedmetadata", "durationchange"]) el.addEventListener(type, emit);
    return el;
  }

  // Make track i the current one, at its start, paused.
  function cue(i) {
    index = wrap(i);
    element().src = base + encodeURIComponent(tracks[index].file);
    cued = index;
  }

  // Begin, or continue, the current track. The stage first, then the sample.
  function start() {
    const a = element();
    if (!wired) {
      wired = true;
      try {
        connect(a);
      } catch {
        // No graph (or the element was somehow already taken): play anyway, but
        // never at whatever the element's volume happens to be. The stage's own
        // music level is the most this radio is ever allowed to be.
        a.volume = dbToGain(MUSIC_DB);
      }
    }
    try {
      const p = resume();
      if (p && typeof p.catch === "function") p.catch(() => {});
    } catch { /* it needs a tap first; the next play tries again */ }
    playing = true;
    error = null;
    syncTimer();
    emit();
    return Promise.resolve(a.play()).then(() => true, (e) => {
      // Cut short by the next track, or by a pause: whoever did that has
      // already set the state, and this is not a failure.
      if (e && e.name === "AbortError") return false;
      failed(e && e.name === "NotAllowedError" ? "Tap play to start the music." : "That song did not play.");
      return false;
    });
  }

  // Move d tracks along the list. Continues if it was playing; a paused radio
  // only changes its mind (nothing autoplays), and the next tap on play is what
  // speaks. Always re-cues, so a one-song playlist starts over rather than
  // sitting at its end.
  function step(d) {
    if (!tracks.length) return Promise.resolve(false);
    const was = playing;
    cue(index + d);
    if (was) return start();
    emit();
    return Promise.resolve(false);
  }

  async function fetchPlaylist() {
    const res = await fetch(base + "playlist.json", { cache: "no-store" });
    if (!res.ok) throw new Error("playlist.json: HTTP " + res.status);
    const d = await res.json();
    const list = ((d && d.tracks) || []).filter((t) => t && typeof t.title === "string" && typeof t.file === "string")
      .map((t) => ({ title: t.title, artist: typeof t.artist === "string" ? t.artist : "", file: t.file }));
    if (!list.length) throw new Error("playlist.json holds no tracks");
    tracks.length = 0;
    tracks.push(...list);
    index = clamp(index, 0, tracks.length - 1);
    // Cued now, so the first song is buffered and its length is known before
    // anybody taps play. Cueing does not play.
    cue(index);
    error = null;
    emit();
  }

  function load() {
    if (!loading) {
      loading = fetchPlaylist().catch((e) => {
        loading = null;
        failed("The station did not load.");
        throw e;
      });
    }
    return loading;
  }

  function play(i) {
    // Task 6's agent calls play(0) with nothing loaded: load, then play.
    if (!tracks.length) return load().then(() => play(i), () => false);
    index = typeof i === "number" && Number.isFinite(i) ? wrap(Math.trunc(i)) : index;
    // The track already cued continues where it is; a different one starts at
    // 0. After a failure it is asked for again, so a file that was not there a
    // moment ago (a push still finishing) is picked up by the next tap.
    if (cued !== index || error) cue(index);
    return start();
  }

  function pause() {
    if (el) el.pause();
    playing = false;
    syncTimer();
    emit();
  }

  return {
    load,
    get tracks() { return tracks; },
    get index() { return index; },
    get playing() { return playing; },
    get error() { return error; },
    play,
    pause,
    toggle() {
      if (!playing) return play();
      pause();
      return Promise.resolve(false);
    },
    next() { return step(1); },
    prev() { return step(-1); },
    seek(sec) {
      const t = Number(sec);
      if (!el || !Number.isFinite(t)) return;
      const d = duration();
      el.currentTime = clamp(t, 0, d || t);
      emit();
    },
    position,
    duration,
    state,
    subscribe(fn) {
      subs.add(fn);
      syncTimer();
      return () => { subs.delete(fn); syncTimer(); };
    },
  };
}

let one = null;
export function getRadio() {
  if (!one) one = createRadio();
  return one;
}
