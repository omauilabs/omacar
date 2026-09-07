// Music: the windscreen as an instrument.
//
// A fullscreen audio-reactive shader driven by the microphone, with the car
// still on screen underneath it in a dock you can read at a glance.
//
// WHY A REAL SPECTRUM AND NOT THREE NUMBERS.
//
// The cheap version of this takes bass/mid/treble as three floats and waves
// them at a plasma. It looks like a screensaver that happens to wobble. The
// spectrum is uploaded here as a 256-wide texture and the wave as another, so a
// shader can sample the actual shape of the sound -- which is the difference
// between a visualiser that responds to music and one that responds to volume.
//
// WHAT THE MICROPHONE COSTS, SAID PLAINLY.
//
// Nothing leaves the machine. The audio goes from getUserMedia into an
// AnalyserNode and into a texture, and is never recorded, never written to
// disk and never sent anywhere -- there is no code here that could, and the
// app has no network destination to send it to. The stream is stopped when you
// leave the screen, so the browser's recording indicator goes out with it.
//
// TWO CONSTRAINTS THAT ARE NOT BUGS.
//
// getUserMedia needs a secure context. Loopback counts -- 127.0.0.1 and
// localhost are treated as trustworthy -- so this works in the app and in the
// kiosk. It does NOT work over `omacar cockpit`, which is plain HTTP to a LAN
// address, and the screen says so rather than failing silently with a dead
// canvas.
//
// And the microphone needs granting once per browser profile. The kiosk keeps
// its own profile, so the grant sticks after the first time.
//
// NO MICROPHONE IS NOT NO VISUALISER. Denied, unavailable, or muted, the
// shaders fall back to being driven by the car: engine speed for the bass,
// road speed for the mid, throttle for the treble. It is a worse visualiser and
// it says which one you are looking at, because a screen that quietly swaps its
// input for another is lying about what it shows.
//
// MOVING CARS GET A CALMER SCREEN, BY DEFAULT AND ON PURPOSE.
//
// A fullscreen animated shader in a driver's peripheral vision is a real
// distraction, and this project does not get to be careful about a voltage
// floor and cavalier about that. Above walking pace the motion damps and the
// brightness drops, the dock stays exactly as legible as it was, and the
// setting to turn that off is on the screen rather than buried -- it is the
// owner's car and the owner's call, but the default is the careful one.

import { h, clear, store, api, toast, U } from "../core.js";

// ---------------------------------------------------------------- the shaders
//
// One uniform contract, four looks. Every shader gets the spectrum and the
// waveform as textures, three band energies, a decaying beat envelope, the
// theme's own colours, and uCalm -- which is how much the car is asking them to
// settle down.

const VERT = `
attribute vec2 aPos;
void main() { gl_Position = vec4(aPos, 0.0, 1.0); }
`;

const HEAD = `
precision highp float;
uniform vec2  uRes;
uniform float uTime;
uniform float uLevel;
uniform float uBass;
uniform float uMid;
uniform float uTreble;
uniform float uBeat;
uniform float uCalm;
uniform vec3  uAccent;
uniform vec3  uInk;
uniform vec3  uGround;
uniform sampler2D uSpectrum;
uniform sampler2D uWave;

float spec(float x) { return texture2D(uSpectrum, vec2(clamp(x, 0.0, 1.0), 0.5)).r; }
float wave(float x) { return texture2D(uWave, vec2(clamp(x, 0.0, 1.0), 0.5)).r - 0.5; }
vec3  warm(float t) { return mix(uGround, uAccent, clamp(t, 0.0, 1.0)); }
`;

const SHADERS = {
  bloom: {
    name: "Bloom",
    what: "A spectrum ring that breathes on the bass.",
    src: HEAD + `
void main() {
  vec2 p = (gl_FragCoord.xy - 0.5 * uRes) / min(uRes.x, uRes.y);
  float r = length(p);
  float a = atan(p.y, p.x);
  // The ring samples the spectrum around itself, mirrored so it is symmetric.
  float k = abs(a) / 3.14159265;
  float amp = spec(pow(k, 0.75));
  float radius = 0.30 + 0.10 * uBass * (1.0 - 0.6 * uCalm) + 0.03 * uBeat;
  float band = abs(r - radius - amp * 0.18 * (1.0 - 0.5 * uCalm));
  float ring = smoothstep(0.055, 0.0, band);
  float glow = 0.10 / (band * 14.0 + 0.35);
  float core = smoothstep(radius, 0.0, r) * (0.25 + 0.5 * uLevel);
  vec3 col = uGround;
  col += uAccent * (ring * 1.15 + glow * (0.55 + 0.8 * uMid));
  col += uInk * core * 0.16;
  col += uAccent * uBeat * 0.14 * smoothstep(0.9, 0.0, r);
  gl_FragColor = vec4(col * (1.0 - 0.35 * uCalm), 1.0);
}`,
  },

  ribbon: {
    name: "Ribbon",
    what: "The waveform itself, flowing.",
    src: HEAD + `
void main() {
  vec2 uv = gl_FragCoord.xy / uRes;
  vec2 p = uv * 2.0 - 1.0;
  p.x *= uRes.x / uRes.y;
  float t = uTime * (0.25 + 0.35 * (1.0 - uCalm));
  vec3 col = uGround;
  // Three offset traces of the same wave: a ribbon rather than a line.
  for (int i = 0; i < 3; i++) {
    float fi = float(i);
    float off = fi * 0.13;
    float y = wave(fract(uv.x + t * 0.12 + off)) * (1.2 + 2.4 * uLevel) * (1.0 - 0.55 * uCalm);
    y += sin(uv.x * 6.0 + t + fi) * 0.05 * uMid;
    float d = abs(p.y - y);
    float line = smoothstep(0.035 + 0.02 * fi, 0.0, d);
    float glow = 0.045 / (d * (7.0 + fi * 4.0) + 0.22);
    vec3 tint = mix(uAccent, uInk, fi * 0.28);
    col += tint * (line * 0.9 + glow * (0.5 + 0.7 * uTreble));
  }
  col += uAccent * uBeat * 0.10;
  gl_FragColor = vec4(col * (1.0 - 0.35 * uCalm), 1.0);
}`,
  },

  plasma: {
    name: "Plasma",
    what: "Warped field, bass in the warp.",
    src: HEAD + `
void main() {
  vec2 uv = gl_FragCoord.xy / uRes;
  vec2 p = (uv - 0.5) * vec2(uRes.x / uRes.y, 1.0) * 3.0;
  float t = uTime * (0.30 + 0.5 * (1.0 - uCalm));
  float warp = 0.6 + 2.2 * uBass * (1.0 - 0.6 * uCalm);
  float v = 0.0;
  v += sin(p.x * 1.4 + t);
  v += sin(p.y * 1.7 - t * 0.8);
  v += sin((p.x + p.y) * 1.1 + t * 0.6) * warp;
  v += sin(length(p * (1.0 + uMid)) * 2.2 - t * 1.3);
  v = v * 0.25;
  float band = spec(fract(abs(v) * 0.5 + uTreble * 0.2));
  vec3 col = warm(0.30 + 0.45 * v + 0.35 * band);
  col += uInk * 0.10 * smoothstep(0.6, 1.0, abs(v));
  col += uAccent * uBeat * 0.16;
  gl_FragColor = vec4(col * (1.0 - 0.35 * uCalm), 1.0);
}`,
  },

  tunnel: {
    name: "Tunnel",
    what: "Forward motion, and it steps on the beat.",
    src: HEAD + `
void main() {
  vec2 p = (gl_FragCoord.xy - 0.5 * uRes) / min(uRes.x, uRes.y);
  float r = max(length(p), 1e-4);
  float a = atan(p.y, p.x);
  float speed = 0.35 + 1.10 * uLevel * (1.0 - 0.7 * uCalm);
  float z = uTime * speed + uBeat * 0.22;
  float depth = 0.32 / r + z;
  float rings = fract(depth);
  float wall = smoothstep(0.5, 0.0, abs(rings - 0.5)) ;
  float spokes = 0.5 + 0.5 * sin(a * 8.0 + depth * 2.0 + uMid * 6.0);
  float amp = spec(fract(depth * 0.25));
  vec3 col = uGround;
  col += uAccent * wall * (0.35 + 0.85 * amp) * spokes;
  col += uInk * 0.10 * wall;
  // Everything fades to the vanishing point so the centre is never a hot dot.
  col *= smoothstep(0.02, 0.42, r);
  col += uAccent * uBeat * 0.12 * smoothstep(0.7, 0.0, r);
  gl_FragColor = vec4(col * (1.0 - 0.35 * uCalm), 1.0);
}`,
  },
};

const ORDER = ["bloom", "ribbon", "plasma", "tunnel"];

// ------------------------------------------------------------------ the audio
//
// One AnalyserNode, read every frame. The bands are integrated over real
// frequency ranges rather than over thirds of the array, because an FFT bin is
// linear in frequency and hearing is not: a third of the bins is almost all
// treble and the "bass" band would barely move.

const BANDS = [
  ["bass", 20, 250],
  ["mid", 250, 4000],
  ["treble", 4000, 16000],
];

function makeAudio() {
  const state = {
    ok: false, reason: "", stream: null, ctx: null, analyser: null,
    freq: null, time: null, bass: 0, mid: 0, treble: 0, level: 0, beat: 0,
    history: [], source: "none",
  };

  state.start = async function start() {
    if (!window.isSecureContext) {
      state.reason = "This page is not a secure context, so the browser will "
        + "not hand over a microphone. That is the case over `omacar cockpit`, "
        + "which is plain HTTP on the network. Open the app on the machine "
        + "itself and it works.";
      return false;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      state.reason = "This browser exposes no microphone API.";
      return false;
    }
    try {
      state.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          // The processed voice path is wrong for music: it ducks, gates and
          // flattens exactly the dynamics this is trying to show.
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
      });
    } catch (e) {
      state.reason = (e && e.name === "NotAllowedError")
        ? "The microphone was refused. Grant it for this page and come back — "
          + "the kiosk keeps its own browser profile, so it is asked once."
        : "No microphone is available: " + String((e && e.message) || e);
      return false;
    }
    const AC = window.AudioContext || window.webkitAudioContext;
    state.ctx = new AC();
    if (state.ctx.state === "suspended") { try { await state.ctx.resume(); } catch { /* user gesture pending */ } }
    const src = state.ctx.createMediaStreamSource(state.stream);
    state.analyser = state.ctx.createAnalyser();
    state.analyser.fftSize = 2048;
    state.analyser.smoothingTimeConstant = 0.72;
    src.connect(state.analyser);
    state.freq = new Uint8Array(state.analyser.frequencyBinCount);
    state.time = new Uint8Array(state.analyser.frequencyBinCount);
    state.ok = true;
    state.source = "microphone";
    return true;
  };

  state.stop = function stop() {
    try { if (state.stream) state.stream.getTracks().forEach((t) => t.stop()); } catch { /* gone */ }
    try { if (state.ctx) state.ctx.close(); } catch { /* gone */ }
    state.stream = null; state.ctx = null; state.analyser = null; state.ok = false;
  };

  state.sample = function sample() {
    if (!state.ok) return false;
    state.analyser.getByteFrequencyData(state.freq);
    state.analyser.getByteTimeDomainData(state.time);
    const rate = state.ctx.sampleRate;
    const bins = state.freq.length;
    const perBin = (rate / 2) / bins;
    for (const [name, lo, hi] of BANDS) {
      const a = Math.max(0, Math.floor(lo / perBin));
      const b = Math.min(bins - 1, Math.ceil(hi / perBin));
      let sum = 0;
      for (let i = a; i <= b; i++) sum += state.freq[i];
      state[name] = (b >= a) ? (sum / (b - a + 1)) / 255 : 0;
    }
    let rms = 0;
    for (let i = 0; i < state.time.length; i++) {
      const v = (state.time[i] - 128) / 128;
      rms += v * v;
    }
    state.level = Math.min(1, Math.sqrt(rms / state.time.length) * 3.2);

    // A beat is energy well above the recent average, with a floor so silence
    // does not beat continuously against its own noise.
    state.history.push(state.bass);
    if (state.history.length > 43) state.history.shift();
    const avg = state.history.reduce((x, y) => x + y, 0) / (state.history.length || 1);
    const hit = state.bass > 0.10 && state.bass > avg * 1.35;
    state.beat = hit ? 1 : Math.max(0, state.beat - 0.06);
    return true;
  };

  return state;
}

// --------------------------------------------------------------------- the GL
function makeRenderer(canvas) {
  const gl = canvas.getContext("webgl", { antialias: false, alpha: false,
                                          powerPreference: "low-power" });
  if (!gl) return null;

  const quad = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, quad);
  gl.bufferData(gl.ARRAY_BUFFER,
                new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);

  function texture() {
    const t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return t;
  }
  const texSpec = texture();
  const texWave = texture();
  const N = 256;
  const specBuf = new Uint8Array(N);
  const waveBuf = new Uint8Array(N);

  const programs = {};
  function build(key) {
    if (programs[key]) return programs[key];
    const compile = (type, src) => {
      const s = gl.createShader(type);
      gl.shaderSource(s, src);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
        console.error("[omacar music]", key, gl.getShaderInfoLog(s));
        return null;
      }
      return s;
    };
    const vs = compile(gl.VERTEX_SHADER, VERT);
    const fs = compile(gl.FRAGMENT_SHADER, SHADERS[key].src);
    if (!vs || !fs) return null;
    const p = gl.createProgram();
    gl.attachShader(p, vs); gl.attachShader(p, fs); gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
      console.error("[omacar music] link", gl.getProgramInfoLog(p));
      return null;
    }
    const loc = {};
    for (const n of ["uRes", "uTime", "uLevel", "uBass", "uMid", "uTreble",
                     "uBeat", "uCalm", "uAccent", "uInk", "uGround",
                     "uSpectrum", "uWave"]) {
      loc[n] = gl.getUniformLocation(p, n);
    }
    const aPos = gl.getAttribLocation(p, "aPos");
    programs[key] = { p, loc, aPos };
    return programs[key];
  }

  function resize(cssW, cssH) {
    // Capped device pixel ratio. A Surface at full ratio is drawing four times
    // the fragments for a difference nobody sees on a moving shader, and the
    // fan noise is real.
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    const w = Math.max(1, Math.round(cssW * dpr));
    const hgt = Math.max(1, Math.round(cssH * dpr));
    if (canvas.width !== w || canvas.height !== hgt) {
      canvas.width = w; canvas.height = hgt;
    }
    gl.viewport(0, 0, canvas.width, canvas.height);
  }

  function upload(audio) {
    if (audio.ok && audio.freq) {
      const bins = audio.freq.length;
      for (let i = 0; i < N; i++) {
        // Logarithmic across the spectrum, so the bass end is not one pixel.
        const f = Math.pow(i / (N - 1), 2.0);
        specBuf[i] = audio.freq[Math.min(bins - 1, Math.floor(f * bins))];
      }
      const step = Math.max(1, Math.floor(audio.time.length / N));
      for (let i = 0; i < N; i++) waveBuf[i] = audio.time[i * step] || 128;
    } else {
      // Driven by the car instead: a smooth synthetic spectrum shaped by the
      // engine, so the screen still moves and still means something.
      const t = performance.now() / 1000;
      for (let i = 0; i < N; i++) {
        const x = i / (N - 1);
        const v = (1 - x) * (0.35 + 0.5 * audio.bass)
                + 0.25 * audio.mid * Math.sin(x * 22 + t * 2)
                + 0.15 * audio.treble * Math.sin(x * 61 - t * 3);
        specBuf[i] = Math.max(0, Math.min(255, Math.round(v * 255)));
        waveBuf[i] = Math.round(128 + 96 * audio.level
                                 * Math.sin(x * 14 + t * 3.1));
      }
    }
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, texSpec);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.LUMINANCE, N, 1, 0,
                  gl.LUMINANCE, gl.UNSIGNED_BYTE, specBuf);
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, texWave);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.LUMINANCE, N, 1, 0,
                  gl.LUMINANCE, gl.UNSIGNED_BYTE, waveBuf);
  }

  function draw(key, audio, t, calm, colours) {
    const prog = build(key);
    if (!prog) return false;
    gl.useProgram(prog.p);
    upload(audio);
    gl.bindBuffer(gl.ARRAY_BUFFER, quad);
    gl.enableVertexAttribArray(prog.aPos);
    gl.vertexAttribPointer(prog.aPos, 2, gl.FLOAT, false, 0, 0);
    const L = prog.loc;
    gl.uniform2f(L.uRes, canvas.width, canvas.height);
    gl.uniform1f(L.uTime, t);
    gl.uniform1f(L.uLevel, audio.level);
    gl.uniform1f(L.uBass, audio.bass);
    gl.uniform1f(L.uMid, audio.mid);
    gl.uniform1f(L.uTreble, audio.treble);
    gl.uniform1f(L.uBeat, audio.beat);
    gl.uniform1f(L.uCalm, calm);
    gl.uniform3fv(L.uAccent, colours.accent);
    gl.uniform3fv(L.uInk, colours.ink);
    gl.uniform3fv(L.uGround, colours.ground);
    gl.uniform1i(L.uSpectrum, 0);
    gl.uniform1i(L.uWave, 1);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    return true;
  }

  return { gl, draw, resize, lost: () => gl.isContextLost() };
}

// ------------------------------------------------------------------ the theme
function rgb(name, fallback) {
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue(name).trim() || fallback;
  const m = raw.match(/^#?([0-9a-f]{6})$/i);
  if (m) {
    const n = parseInt(m[1], 16);
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
  }
  const p = raw.match(/rgba?\(([^)]+)\)/i);
  if (p) {
    const parts = p[1].split(",").map((x) => parseFloat(x));
    return [(parts[0] || 0) / 255, (parts[1] || 0) / 255, (parts[2] || 0) / 255];
  }
  return [0.4, 0.66, 0.91];
}

// -------------------------------------------------------------------- the view
export default function music(root) {
  const stopFns = [];
  let raf = 0, alive = true;
  let shader = localStorage.getItem("omacar.music.shader") || "bloom";
  let calmWhenMoving = localStorage.getItem("omacar.music.calm") !== "off";

  root.classList.add("music-root");
  const canvas = h("canvas.music-canvas");
  const stage = h("div.music-stage", canvas);
  root.appendChild(stage);

  const notice = h("div.music-notice", { hidden: true });
  stage.appendChild(notice);

  // ---- the dock ------------------------------------------------------------
  //
  // Minified deliberately: four readouts, one row, large type, and the way out
  // is a real target rather than a chevron. It is the same contract drive mode
  // makes -- readable at arm's length, hittable without aiming.
  const cells = {};
  function cell(id, label) {
    const v = h("div.music-v", "—");
    const c = h("div.music-cell", v, h("div.music-k", label));
    cells[id] = v;
    return c;
  }
  const shaderBtn = h("button.btn.music-btn", SHADERS[shader].name);
  const srcPill = h("span.pill.music-src", "car");
  const dock = h("div.music-dock",
    h("button.btn.music-btn", { onclick: () => { location.hash = "#hub"; } }, "Workshop"),
    cell("speed", "speed"),
    cell("rpm", "engine"),
    cell("soc", "charge"),
    cell("econ", "econ"),
    h("div.music-spacer"),
    srcPill,
    shaderBtn);
  root.appendChild(dock);

  shaderBtn.addEventListener("click", () => {
    shader = ORDER[(ORDER.indexOf(shader) + 1) % ORDER.length];
    localStorage.setItem("omacar.music.shader", shader);
    shaderBtn.textContent = SHADERS[shader].name;
    toast(SHADERS[shader].name + " — " + SHADERS[shader].what);
  });

  const calmBtn = h("button.btn.music-btn",
    { onclick: () => {
        calmWhenMoving = !calmWhenMoving;
        localStorage.setItem("omacar.music.calm", calmWhenMoving ? "on" : "off");
        calmBtn.textContent = calmWhenMoving ? "Calm at speed" : "Full at speed";
        calmBtn.classList.toggle("music-on", calmWhenMoving);
        toast(calmWhenMoving
          ? "The screen settles down once the car is moving."
          : "Full motion at any speed. Your car, your call.");
      } },
    calmWhenMoving ? "Calm at speed" : "Full at speed");
  if (calmWhenMoving) calmBtn.classList.add("music-on");
  dock.insertBefore(calmBtn, srcPill);

  // ---- audio + render ------------------------------------------------------
  const audio = makeAudio();
  const renderer = makeRenderer(canvas);

  if (!renderer) {
    notice.hidden = false;
    clear(notice);
    notice.appendChild(h("div.music-title", "No WebGL here"));
    notice.appendChild(h("p.lede", "This browser has no WebGL context, so there "
      + "is nothing to draw the shaders with. Everything else in OmaCar works."));
  }

  function say(title, body) {
    notice.hidden = false;
    clear(notice);
    notice.appendChild(h("div.music-title", title));
    notice.appendChild(h("p.lede", body));
    const btn = h("button.btn.primary", { style: { marginTop: "12px" } },
                  "Try the microphone again");
    btn.addEventListener("click", async () => {
      const ok = await audio.start();
      if (ok) { notice.hidden = true; srcPill.textContent = "microphone"; }
      else say("Still no microphone", audio.reason);
    });
    notice.appendChild(btn);
  }

  (async () => {
    const ok = await audio.start();
    if (!alive) { audio.stop(); return; }
    srcPill.textContent = ok ? "microphone" : "car";
    srcPill.classList.toggle("warn", !ok);
    if (!ok) {
      say("Listening to the car instead", audio.reason
        + "  The shaders are being driven by engine speed, road speed and "
        + "throttle instead, so this is the car's shape and not the room's.");
    }
  })();

  const colours = {
    accent: rgb("--accent", "#4FA8E8"),
    ink: rgb("--ink", "#E8ECF1"),
    ground: rgb("--bg", "#0B0E12"),
  };

  const t0 = performance.now();
  let calm = 0;

  function frame() {
    if (!alive) return;
    raf = requestAnimationFrame(frame);
    if (!renderer || renderer.lost()) return;

    const v = store.values || {};
    if (!audio.sample()) {
      // The car drives the bands when the microphone does not.
      const rpm = Math.min(1, (v.RPM || 0) / 6300);
      const kph = Math.min(1, (v.SPEED || 0) / 140);
      const thr = Math.min(1, (v.THROTTLE_POS || 0) / 100);
      audio.bass = rpm;
      audio.mid = kph;
      audio.treble = thr;
      audio.level = Math.max(rpm * 0.7, kph * 0.5);
      audio.beat = Math.max(0, audio.beat - 0.05);
    }

    // Calm ramps in over walking pace rather than snapping at a threshold, so
    // pulling away is a settling rather than a jolt.
    const kph = v.SPEED || 0;
    const want = calmWhenMoving ? Math.min(1, Math.max(0, (kph - 5) / 35)) : 0;
    calm += (want - calm) * 0.05;

    const r = stage.getBoundingClientRect();
    renderer.resize(r.width, r.height);
    renderer.draw(shader, audio, (performance.now() - t0) / 1000, calm, colours);
  }
  raf = requestAnimationFrame(frame);

  // ---- the dock numbers ----------------------------------------------------
  function paintDock() {
    const v = store.values || {};
    const set = (id, text) => { if (cells[id]) cells[id].textContent = text; };
    set("speed", v.SPEED === undefined || v.SPEED === null
      ? "—" : String(Math.round(v.SPEED * U.units.km)));
    set("rpm", v.RPM === undefined || v.RPM === null
      ? "—" : String(Math.round(v.RPM)));
    const soc = v.HYBRID_BATTERY_REMAINING;
    set("soc", soc === undefined || soc === null ? "—" : Math.round(soc) + "%");
    const e = (store.live && store.live.economy_lphk);
    set("econ", e === undefined || e === null ? "—" : String(Math.round(e * 10) / 10));
  }
  paintDock();
  stopFns.push(store.on("live", paintDock));

  return () => {
    alive = false;
    cancelAnimationFrame(raf);
    audio.stop();
    root.classList.remove("music-root");
    for (const f of stopFns) f();
  };
}
