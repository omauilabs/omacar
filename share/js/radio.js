// Omarchy Radio, in the dashboard.
//
// A native <audio> element rather than an iframe of radio.omarchy.org. The site
// has no X-Frame-Options so framing it would work, but an iframe would bring
// its layout into a screen designed for a moving car -- small controls, its own
// scroll, and a third-party page that can change shape without warning. The
// stream sends Access-Control-Allow-Origin: *, so we can play it directly and
// draw controls at the size a driver can actually hit.
//
// Nothing here autoplays. Browsers block it, and a dashboard that starts
// playing music the moment you plug in your car would deserve to be blocked
// anyway.

import { h } from "./core.js";

const STREAM = "https://radio.cliamp.stream/omarchy/stream";
const STATS = "https://radio.cliamp.stream/statistics";
const STATION = "omarchy";
const VOL_KEY = "omacar.radio.volume";

let audio = null;
let listeners = new Set();
let now = { title: "", artist: "", listeners: null };
let metaTimer = null;
let statsTimer = null;

function emit() { for (const fn of listeners) fn(); }

// ---------------------------------------------------------------- now playing
//
// The track name only exists inside the audio stream. Icecast interleaves it:
// every `icy-metaint` bytes of audio comes a length byte, then that many bytes
// of "StreamTitle='Artist - Title';". There is no separate endpoint -- the
// station's /statistics gives listener counts and nothing about the track, and
// the playlist manifest lists every track but not which one is playing.
//
// SAMPLE AND DISCONNECT, RATHER THAN LISTEN TWICE.
//
// Reading metadata means opening the stream a SECOND time, alongside the one
// the <audio> element is playing. Held open, that doubles the bandwidth --
// 128 kbps becomes 256, about 114 MB an hour, which is real money on a phone
// hotspot in a car.
//
// But the first metadata block arrives after only 8192 bytes, half a second of
// audio. So this connects, reads to the first block, and aborts: roughly 8.5 KB
// per sample. At one sample every 20 seconds that is ~0.4 KB/s instead of
// 16 KB/s, for information that only changes every few minutes.
async function sampleMetadata(signal) {
  const res = await fetch(STREAM, {
    headers: { "Icy-MetaData": "1" },
    signal,
    cache: "no-store",
  });
  const interval = parseInt(res.headers.get("icy-metaint") || "0", 10);
  if (!interval || !res.body) { res.body?.cancel?.(); return null; }

  const reader = res.body.getReader();
  let skipped = 0;
  let pending = new Uint8Array(0);

  const concat = (a, b) => {
    const out = new Uint8Array(a.length + b.length);
    out.set(a); out.set(b, a.length);
    return out;
  };

  try {
    // Walk past exactly `interval` bytes of audio, then the length byte, then
    // the metadata itself. Chunks do not align to any of those boundaries, so
    // everything is buffered rather than assumed.
    while (skipped < interval) {
      const { value, done } = await reader.read();
      if (done) return null;
      skipped += value.length;
      if (skipped > interval) pending = value.slice(value.length - (skipped - interval));
    }
    while (pending.length < 1) {
      const { value, done } = await reader.read();
      if (done) return null;
      pending = concat(pending, value);
    }
    const len = pending[0] * 16;
    pending = pending.slice(1);
    if (len === 0) return "";                 // no change since the last block
    while (pending.length < len) {
      const { value, done } = await reader.read();
      if (done) return null;
      pending = concat(pending, value);
    }
    const text = new TextDecoder("utf-8", { fatal: false })
      .decode(pending.slice(0, len));
    const m = /StreamTitle='([^']*)'/.exec(text);
    return m ? m[1].trim() : "";
  } finally {
    try { await reader.cancel(); } catch { /* already gone */ }
  }
}

function splitTitle(raw) {
  // Icecast convention is "Artist - Title", but plenty of stations send only a
  // title. Splitting on the FIRST " - " keeps hyphenated track names intact.
  if (!raw) return { artist: "", title: "" };
  const i = raw.indexOf(" - ");
  return i > 0
    ? { artist: raw.slice(0, i).trim(), title: raw.slice(i + 3).trim() }
    : { artist: "", title: raw.trim() };
}

async function refreshNowPlaying() {
  const ctl = new AbortController();
  // A sample that hangs must not stack up behind the next one.
  const bail = setTimeout(() => ctl.abort(), 8000);
  try {
    const raw = await sampleMetadata(ctl.signal);
    if (raw === null) return;
    if (raw === "") return;                   // empty block: keep what we have
    const { artist, title } = splitTitle(raw);
    if (title !== now.title || artist !== now.artist) {
      now = { ...now, artist, title };
      emit();
      // Lock-screen and media-key integration, free if the browser has it.
      if ("mediaSession" in navigator && window.MediaMetadata) {
        try {
          navigator.mediaSession.metadata = new window.MediaMetadata({
            title: title || "Omarchy Radio",
            artist: artist || "Omarchy",
            album: "Omarchy Radio",
          });
        } catch { /* best effort */ }
      }
    }
  } catch { /* offline, aborted, or CORS -- the player still works */ }
  finally { clearTimeout(bail); }
}

async function refreshStats() {
  try {
    const res = await fetch(STATS, { cache: "no-store" });
    const d = await res.json();
    const n = d?.stations?.[STATION]?.active_listeners;
    if (typeof n === "number" && n !== now.listeners) {
      now = { ...now, listeners: n };
      emit();
    }
  } catch { /* not important enough to report */ }
}

function startPolling() {
  if (metaTimer) return;
  refreshNowPlaying();
  refreshStats();
  metaTimer = setInterval(refreshNowPlaying, 20000);
  statsTimer = setInterval(refreshStats, 60000);
}

function stopPolling() {
  // Nothing is sampled while paused. Polling a stream nobody is listening to
  // is pure waste, and this runs in a car.
  clearInterval(metaTimer); clearInterval(statsTimer);
  metaTimer = statsTimer = null;
}

function el() {
  if (audio) return audio;
  audio = new Audio();
  audio.preload = "none";          // no connection until asked
  audio.crossOrigin = "anonymous";
  audio.src = STREAM;
  let v = 0.8;
  try {
    const saved = parseFloat(localStorage.getItem(VOL_KEY));
    if (!Number.isNaN(saved)) v = Math.min(1, Math.max(0, saved));
  } catch { /* private mode */ }
  audio.volume = v;
  for (const e of ["playing", "pause", "waiting", "error", "stalled"]) {
    audio.addEventListener(e, emit);
  }
  return audio;
}

// ------------------------------------------------------------------- levels
//
// A meter that moves with the music, and ONLY with the music.
//
// THE ONE FACT THAT MAKES THIS HONEST.
//
// Web Audio will only let you analyse a media element it is permitted to read.
// Hang an AnalyserNode off a cross-origin <audio> whose server never allowed
// it and nothing throws -- getByteFrequencyData quietly returns an array of
// zeros, forever, and the meter lies flat with nothing in the console to
// explain why. The Omarchy stream sends Access-Control-Allow-Origin: *, and
// el() sets crossOrigin BEFORE assigning src, which is the order that matters:
// setting it afterwards is too late and taints the element for the life of the
// page.
//
// So every height below is a real amplitude off the real stream. If that ever
// stops being true the bars go still rather than improvising -- there is no
// code path here that animates anything without a measurement behind it, and
// there must never be one. A meter dancing to silence is a lie told sixty
// times a second.

let actx = null;
let sourceNode = null;
let analyser = null;
let analyserTried = false;
let bins = null;

// createMediaElementSource may be called ONCE for any given element, ever; a
// second call throws. `audio` outlives every remount of the player, so the
// graph is cached at module scope beside it rather than built per-player.
function analyserFor(a) {
  if (analyserTried) return analyser;
  analyserTried = true;
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return null;
  try {
    actx = new AC();
    sourceNode = actx.createMediaElementSource(a);
    analyser = actx.createAnalyser();
    // 1024 bins, ~23 Hz apiece at 48 kHz. 1024 was the first choice and it was
    // wrong: at 47 Hz per bin the bottom two bands both rounded to the same
    // single bin, so the first two bars moved as one object for ever. Bass
    // needs finer resolution than treble because the bands down there are
    // narrower in Hz, which is the same logarithmic fact from the other end.
    analyser.fftSize = 2048;
    analyser.smoothingTimeConstant = 0.72;
    // The default floor of -100 dB spends most of the meter's travel on room
    // noise nobody can hear. A broadcast stream is compressed and sits high,
    // so the window is tightened to where the music actually is.
    analyser.minDecibels = -76;
    analyser.maxDecibels = -20;
    // The element's audio now flows THROUGH this graph. Forget to connect the
    // far end to the destination and the visualiser works beautifully while
    // the radio goes silent.
    sourceNode.connect(analyser);
    analyser.connect(actx.destination);
    bins = new Uint8Array(analyser.frequencyBinCount);
  } catch {
    // Tainted, unsupported, or the element was already taken. The player must
    // keep working; only the meter is lost.
    analyser = null;
  }
  return analyser;
}

// Sixteen bands, spaced logarithmically between 45 Hz and 14 kHz.
//
// Linear spacing is the mistake that makes most visualisers look broken. The
// upper half of a linear FFT covers 12 kHz and above, where music carries
// almost no energy, so fifteen bars barely twitch while the first one does all
// the work. Pitch is logarithmic and so is this: each band spans roughly the
// same musical interval, which is why the meter ends up looking like the music
// sounds rather than like a spectrum analyser's datasheet.
const BANDS = 16;
const LO_HZ = 45;
const HI_HZ = 14000;

function bandRanges(sampleRate, binCount) {
  const nyquist = sampleRate / 2;
  const bin = (hz) => Math.min(binCount - 1,
                               Math.max(0, Math.round(hz / nyquist * binCount)));
  const out = [];
  for (let i = 0; i < BANDS; i++) {
    const lo = bin(LO_HZ * Math.pow(HI_HZ / LO_HZ, i / BANDS));
    const hi = bin(LO_HZ * Math.pow(HI_HZ / LO_HZ, (i + 1) / BANDS));
    // Every band must own at least one bin. The lowest few are narrower than a
    // single 47 Hz bin, and without this they would sit empty for ever.
    out.push([lo, Math.max(lo + 1, hi)]);
  }
  return out;
}

// The meter itself. Sixteen columns, each a bar that is scaled and a cap that
// holds the recent peak.
export function radioBars() {
  const wrap = h("div.radio-viz", { "aria-hidden": "true", data: { live: "0" } });
  const cols = [];
  for (let i = 0; i < BANDS; i++) {
    const bar = h("i");
    const cap = h("b");
    // --t places this bar across the spectrum for the stylesheet to colour it.
    // Set once, because it describes the band, not the moment.
    bar.style.setProperty("--t", (i / (BANDS - 1)).toFixed(3));
    wrap.appendChild(h("span", bar, cap));
    cols.push({ bar, cap, v: 0, peak: 0 });
  }

  let raf = 0;
  let idle = 0;
  let ranges = null;
  let height = 0;
  // Whether this meter has ever been in the document. Until it has, a false
  // isConnected means "not appended yet", not "thrown away".
  let mounted = false;
  let waited = 0;
  // Twenty half-second polls. Long enough for any caller that appends in the
  // same task or the next frame, short enough that a meter built and dropped
  // on the floor stops costing anything.
  const MOUNT_GRACE = 20;

  const stop = () => {
    if (raf) cancelAnimationFrame(raf);
    if (idle) clearTimeout(idle);
    raf = idle = 0;
  };

  function rest() {
    for (const c of cols) {
      if (c.v === 0 && c.peak === 0) continue;
      c.v = c.peak = 0;
      c.bar.style.transform = "scaleY(0.04)";
      c.cap.style.transform = "translateY(0)";
    }
  }

  function tick() {
    // hub.js rebuilds the whole player on every radio event, so the loop
    // belonging to a discarded copy has to notice and let go of itself.
    //
    // BUT NOT BEFORE IT HAS EVER BEEN MOUNTED. radioBars() builds `wrap` and
    // hands it back for the caller to append, so on the first tick it is a
    // detached element and isConnected is false. The first version of this
    // reaped itself on that very first call and the meter then sat flat for
    // ever, on every machine — and every check written for it still passed,
    // because all sixteen bars and all sixteen peak caps were present and
    // correct in the DOM. Structure was verified; motion never was.
    if (wrap.isConnected) {
      mounted = true;
    } else if (mounted) {
      stop();
      return;                       // was on screen, has been replaced
    } else if (++waited > MOUNT_GRACE) {
      stop();
      return;                       // built and never appended; do not poll for ever
    } else {
      // Not on screen yet. Wait for the caller to append it rather than
      // giving up, and do it on the slow clock — this costs two wakeups a
      // second for at most ten seconds.
      raf = 0;
      idle = setTimeout(tick, 500);
      return;
    }

    const live = !!analyser && !!audio && !audio.paused && !document.hidden;
    wrap.dataset.live = live ? "1" : "0";

    if (!live) {
      // Paused: drop off the frame clock entirely and poll slowly instead.
      // Holding a 60 Hz callback open to do nothing keeps the compositor
      // awake, and this runs in a car, on a battery.
      rest();
      raf = 0;
      idle = setTimeout(tick, 500);
      return;
    }

    idle = 0;
    raf = requestAnimationFrame(tick);

    if (!height) height = wrap.clientHeight || 40;
    if (!ranges) ranges = bandRanges(actx.sampleRate, analyser.frequencyBinCount);
    analyser.getByteFrequencyData(bins);

    for (let i = 0; i < BANDS; i++) {
      const [lo, hi] = ranges[i];
      let top = 0;
      for (let j = lo; j < hi; j++) if (bins[j] > top) top = bins[j];
      // Treble is quieter than bass in very nearly all music. Without a tilt
      // the right-hand half of the meter never leaves the floor. This is
      // cosmetic weighting OF a real measurement, which is a different thing
      // from inventing movement where there is none.
      const v = Math.min(1, (top / 255) * (1 + 0.85 * (i / (BANDS - 1))));
      const c = cols[i];
      // Rise instantly, fall smoothly. A meter that eases upward lags the beat
      // and immediately stops looking connected to what you are hearing.
      c.v = v > c.v ? v : c.v + (v - c.v) * 0.28;
      c.peak = c.v > c.peak ? c.v : Math.max(c.v, c.peak - 0.012);
      c.bar.style.transform = "scaleY(" + Math.max(0.04, c.v).toFixed(3) + ")";
      c.cap.style.transform = "translateY(" + (-c.peak * (height - 2)).toFixed(1) + "px)";
    }
  }

  tick();
  return wrap;
}

export const radio = {
  get playing() { return !!audio && !audio.paused; },
  get now() { return now; },
  get loading() { return !!audio && !audio.paused && audio.readyState < 3; },
  get failed() { return !!audio && !!audio.error; },
  get volume() { return audio ? audio.volume : 0.8; },
  set volume(v) {
    const a = el();
    a.volume = Math.min(1, Math.max(0, v));
    try { localStorage.setItem(VOL_KEY, String(a.volume)); } catch { /* ignore */ }
    emit();
  },
  async toggle() {
    const a = el();
    if (a.paused) {
      // A live stream that has been paused is stale; reloading rejoins at the
      // live edge instead of resuming minutes behind.
      if (a.currentTime > 0) a.load();
      try { await a.play(); } catch { /* autoplay policy or offline */ }
      // Built here, on the click, because an AudioContext created without a
      // user gesture arrives suspended and stays suspended -- and a suspended
      // context reports zeros, which is indistinguishable from silence.
      analyserFor(a);
      if (actx && actx.state === "suspended") {
        try { await actx.resume(); } catch { /* the meter goes still, not wrong */ }
      }
      startPolling();
    } else {
      a.pause();
      stopPolling();
    }
    emit();
  },
  stop() { if (audio) { audio.pause(); audio.load(); stopPolling(); emit(); } },
  on(fn) { listeners.add(fn); return () => listeners.delete(fn); },
};

// The player, sized for the car: a 64px transport button and a slider with a
// tall touch area, because a thin range input is unusable on a moving vehicle.
export function radioPlayer() {
  const status = radio.failed ? "offline"
    : radio.loading ? "connecting…"
    : radio.playing ? "live" : "paused";

  const btn = h("button.radio-play", {
    onclick: () => radio.toggle(),
    "aria-label": radio.playing ? "Pause radio" : "Play radio",
  }, radio.playing ? "❚❚" : "▶");

  const vol = h("input.radio-vol", {
    type: "range", min: "0", max: "100",
    value: String(Math.round(radio.volume * 100)),
    "aria-label": "Radio volume",
    oninput: (e) => { radio.volume = Number(e.target.value) / 100; },
  });

  const np = radio.now;
  const line2 = np.artist ? np.artist : status;

  return h("div.radio",
    btn,
    // Next to the transport, where it reads as "this is the thing playing"
    // rather than as decoration parked somewhere in the corner.
    radioBars(),
    h("div.radio-meta",
      // The track when we have one, the station when we do not. A player that
      // shows "Omarchy Radio" while a named track is playing is wasting the
      // only line that tells you what you are hearing.
      h("div.radio-name", np.title || "Omarchy Radio"),
      h("div.radio-status" + (radio.failed ? ".bad" : ""),
        line2,
        np.listeners != null
          ? h("span.radio-count", `${np.listeners} listening`)
          : null)),
    vol);
}

// ---- the player, wired ------------------------------------------------------
//
// radioPlayer() above is markup; this is markup that keeps itself up to date.
//
// IT LIVES HERE BECAUSE IT MOVED. The hub built this panel and painted it from
// inside its own redraw, reaching into the player by class name for five
// elements. That was a documented, deliberate coupling -- the volume control is
// a range input, and rebuilding it mid-drag destroys the slider under the
// finger holding it -- but it also meant the wiring belonged to whichever
// screen happened to host the radio. Moving the radio to the Music page, where
// it belongs, would have meant moving forty lines of painting with it and
// leaving the coupling behind in a second place.
//
// So the panel owns its own painting, and a screen that wants a radio asks for
// one and gets something that works.
export function radioPanel() {
  const node = radioPlayer();
  const play = node.querySelector(".radio-play");
  const name = node.querySelector(".radio-name");
  const status = node.querySelector(".radio-status");
  const vol = node.querySelector(".radio-vol");
  const line = document.createTextNode("");
  const count = h("span.radio-count");
  if (status) {
    while (status.firstChild) status.removeChild(status.firstChild);
    status.appendChild(line);
    status.appendChild(count);
  }
  if (vol) vol.style.touchAction = "pan-y";

  function paint() {
    if (play) {
      play.textContent = radio.playing ? "❚❚" : "▶";
      play.setAttribute("aria-label", radio.playing ? "Pause radio" : "Play radio");
    }
    const np = radio.now;
    if (name) name.textContent = np.title || "Omarchy Radio";
    const state = radio.failed ? "offline"
      : radio.loading ? "connecting…"
      : radio.playing ? "live" : "paused";
    const text = np.artist ? np.artist : state;
    if (line.data !== text) line.data = text;
    if (status) status.classList.toggle("bad", radio.failed);
    count.textContent = np.listeners != null ? `${np.listeners} listening` : "";
    count.hidden = np.listeners == null;
    // Never write over a slider somebody has hold of: the value it would be
    // given is the value they just set, and the write moves the thumb out from
    // under the finger mid-drag.
    if (vol && document.activeElement !== vol) {
      vol.value = String(Math.round(radio.volume * 100));
    }
  }

  paint();
  const off = radio.on(paint);
  return { node, play, paint, stop: () => off && off() };
}
