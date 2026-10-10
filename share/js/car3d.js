// The car in three dimensions -- when there is a car to show, and honestly
// nothing at all when there is not.
//
// WHAT THIS FILE IS AND IS NOT.
//
// It is a viewer. It is a lit stage, a camera on a slow turntable, a set of
// rules for behaving on a 2017 laptop, and a fallback for when any of that is
// unavailable. It contains NO car geometry. There is no 2015 Honda CR-Z model
// in this repository and this module does not pretend otherwise: with no file
// at MODEL_URL it draws the same silhouette the Qt panel draws and says
// nothing.
//
// That is a deliberate refusal, not an unfinished job. A photorealistic CR-Z
// is an asset you buy, scan or commission -- it is not something that can be
// written out of boxes and cylinders, and a car built out of boxes and
// cylinders is precisely the "cheap looking" outcome that was rejected before
// this file existed. docs/car3d.md is the other half of this change and is the
// half that actually answers the question: what a real model costs, why a
// free one is a licence trap for a public repository, and why photogrammetry
// of the actual car in the actual driveway is the recommendation.
//
// WHAT TO DROP IN.
//
//   share/models/crz.glb              the model (glTF binary, +Y up)
//   share/models/crz.materials.json   optional, per-material tuning
//
// Nothing else changes. The viewer measures whatever it is given, scales it
// into its own stage, frames it and lights it. Units do not matter (glTF in
// the wild is variously metres, centimetres and inches), orientation along the
// ground plane does not matter, and the material count does not matter.
//
// WHAT IT REFUSES TO DO.
//
// The machine this runs on is a Yoga 710 with Intel HD 620 that is also
// polling a serial port, and effects.js already carries a note about this
// project starving that compositor once. So the viewer gives up early and
// often, and every one of these paths ends at the silhouette rather than at a
// black rectangle:
//
//   - no model file                 (a HEAD probe before anything is loaded)
//   - no three.js vendored          (the dynamic import throws, we catch it)
//   - no WebGL, or a software one   (llvmpipe rendering a car is a space heater)
//   - the context is lost           (drivers do this; a frozen frame is a lie)
//   - it is simply too slow         (measured, stepped down twice, then given up)
//
// It also stops dead when scrolled off screen or when the tab is hidden, and
// it never runs faster than 30fps, for the same reason effects.js does not:
// this is decoration on a diagnostic tool and it must never be the reason a
// gauge stutters.
//
// WHY THE THREE.JS VERSION IS PINNED AND VENDORED.
//
// r185 exactly. Not "latest", and not a CDN: this application is served from
// the laptop's own disk and is expected to work in a car park with no signal,
// where a <script src="https://..."> is a viewer that works on the sofa and is
// a blank box at the roadside. See docs/car3d.md for the five files to fetch
// and where they go. The bare specifier "three" is used deliberately so that
// this module and GLTFLoader resolve to the SAME module instance -- importing
// the core by URL here and letting the loader import it by name would give two
// copies of three.js, and every `instanceof` between them would be false.

// ---------------------------------------------------------------------------

const MODEL_URL = "/models/crz.glb";
const TUNING_URL = "/models/crz.materials.json";

// 30fps, and a deliberately long turn. A showroom turntable takes about half a
// minute to come round; anything quicker reads as a spinning icon rather than
// as a car being shown to you, and it also means every frame is a bigger
// change, which is the frame rate you notice missing.
const FRAME_MS = 1000 / 30;
const TURN_SECONDS = 34;

// The pixel-ratio ladder. Start below the panel's real ratio -- a HiDPI screen
// asking for 2x is asking Intel HD 620 for four times the shading, for a car
// nobody is measuring pixels on -- and step down twice before giving up.
const DPR_STEPS = [1.5, 1.0, 0.75];

// A frame that takes this long is not sustainable next to a 250ms live poller
// and a serial read. Measured as a moving average so one slow frame during
// GLTF upload does not condemn the whole viewer.
const SLOW_FRAME_MS = 24;
const SLOW_FRAMES_BEFORE_STEPPING_DOWN = 45;

// The model is rescaled into a stage of this length whatever units it arrived
// in, so the light rig below can be built in fixed coordinates instead of
// being recomputed per model. Four units for a car that is four metres long
// keeps the arithmetic readable if anyone ever debugs the rig.
const STAGE_LENGTH = 4;

// ---------------------------------------------------------------------------
// The silhouette.
//
// The same artwork as plugin/Car.qml, from the same source (svgrepo.com family
// coupe, credited in full in that file's header). Sharing it is the point: the
// fallback should look like the car this application already draws everywhere
// else, not like a second opinion about what a car is.
//
// The source viewBox is square but the car occupies only y 34.69..64.69 of it,
// so the viewBox here is cropped to the ink -- the same discovery Car.qml
// records, and the same reason: used square, the car is a third of the height
// it could be.

const ART = {
  viewBox: "0 34.69 99.382 29.99",
  body: "M99.352,52.001c-0.026-0.983-0.331-1.941-0.882-2.759l-0.402-0.6l-1.757-4.635c-0.331-0.875-1.139-1.484-2.07-1.562 c-1.728-0.146-4.663-0.368-7.465-0.471c-9.794-5.201-27.904-10.43-43.262-4.731c-3.151,1.169-12.154,5.744-12.154,5.744 s-14.62-0.37-25.047,3.349c-4.108,1.465-6.699,5.543-6.266,9.884c0.087,0.869,0.215,1.642,0.341,2.266 c0.199,0.987,1.014,1.731,2.015,1.842l6.487,0.711c-0.408-0.852-0.695-1.773-0.818-2.755c-0.052-0.401-0.078-0.772-0.078-1.132 c0-4.967,4.041-9.008,9.008-9.008c4.968,0,9.01,4.042,9.01,9.008c0,0.26-0.017,0.514-0.038,0.768 c-0.095,1.115-0.399,2.172-0.868,3.135h45.045l0.365-0.021c-0.4-0.842-0.683-1.753-0.804-2.72 c-0.052-0.403-0.077-0.773-0.077-1.127c0-4.95,4.026-8.978,8.977-8.978s8.978,4.026,8.978,8.978c0,0.259-0.017,0.511-0.038,0.764 c-0.062,0.734-0.223,1.438-0.453,2.11L88.112,60l6.979-0.923c1.214-0.161,2.285-0.876,2.898-1.936l0.695-1.199 c0.479-0.83,0.721-1.777,0.695-2.735L99.352,52.001z M74.63,43.068H40.469c0,0,0.203-0.809-0.69-1.786 c0,0,8.733-5.633,22.22-4.226c3.711,0.388,8.246,0.651,14.42,3.86L74.63,43.068z",
  wheels: [
    { cx: 17.001, cy: 57.152, d: "M17.001,49.693c-4.12,0-7.46,3.338-7.46,7.459c0,0.319,0.026,0.631,0.066,0.938c0.462,3.677,3.593,6.521,7.394,6.521 c3.906,0,7.105-3.002,7.429-6.823c0.019-0.211,0.032-0.422,0.032-0.638C24.463,53.031,21.123,49.693,17.001,49.693z M13.264,54.342l1.522,1.521c-0.119,0.203-0.211,0.423-0.271,0.656H12.37C12.483,55.706,12.794,54.967,13.264,54.342z M12.364,57.812h2.16c0.062,0.229,0.15,0.447,0.27,0.646l-1.524,1.524C12.798,59.361,12.479,58.62,12.364,57.812z M16.356,61.787 c-0.809-0.111-1.543-0.429-2.164-0.896l1.517-1.518c0.199,0.116,0.418,0.201,0.647,0.262V61.787z M16.356,54.672 c-0.235,0.062-0.455,0.153-0.66,0.274l-1.521-1.521c0.625-0.475,1.366-0.788,2.181-0.901V54.672z M17.647,52.524 c0.813,0.113,1.555,0.428,2.18,0.902l-1.52,1.52c-0.205-0.121-0.426-0.214-0.66-0.274V52.524z M17.647,61.786v-2.151 c0.229-0.061,0.447-0.146,0.646-0.264l1.519,1.52C19.191,61.357,18.456,61.675,17.647,61.786z M20.738,59.988l-1.53-1.531 c0.118-0.199,0.217-0.414,0.278-0.646h2.144C21.516,58.62,21.21,59.367,20.738,59.988z M19.487,56.52 c-0.061-0.233-0.152-0.453-0.271-0.656l1.522-1.521c0.471,0.625,0.782,1.364,0.894,2.179L19.487,56.52L19.487,56.52z" },
    { cx: 78.611, cy: 57.186, d: "M78.611,49.758c-4.103,0-7.428,3.324-7.428,7.428c0,0.317,0.025,0.627,0.064,0.934c0.46,3.66,3.578,6.494,7.363,6.494 c3.889,0,7.074-2.989,7.396-6.794c0.019-0.21,0.032-0.42,0.032-0.634C86.04,53.082,82.715,49.758,78.611,49.758z M74.891,54.387 l1.516,1.516c-0.118,0.202-0.21,0.421-0.27,0.653h-2.136C74.112,55.744,74.422,55.009,74.891,54.387z M73.994,57.842h2.15 c0.062,0.229,0.15,0.444,0.269,0.644l-1.519,1.52C74.427,59.386,74.108,58.646,73.994,57.842z M77.969,61.801 c-0.805-0.112-1.535-0.429-2.154-0.896l1.51-1.51c0.198,0.115,0.417,0.201,0.645,0.26V61.801z M77.969,54.715 c-0.233,0.061-0.453,0.152-0.656,0.272l-1.514-1.514c0.622-0.471,1.36-0.784,2.17-0.897V54.715z M79.255,52.576 c0.812,0.113,1.549,0.428,2.17,0.898l-1.513,1.513c-0.204-0.12-0.423-0.213-0.657-0.272V52.576z M79.255,61.8v-2.145 c0.229-0.059,0.446-0.146,0.645-0.262l1.511,1.512C80.792,61.371,80.061,61.687,79.255,61.8z M82.332,60.009l-1.523-1.524 c0.117-0.199,0.216-0.412,0.276-0.643h2.134C83.106,58.646,82.802,59.389,82.332,60.009z M81.087,56.555 c-0.06-0.232-0.15-0.451-0.27-0.653l1.516-1.516c0.469,0.622,0.778,1.357,0.889,2.169H81.087z" },
  ],
};

const SVGNS = "http://www.w3.org/2000/svg";

function reducedMotion() {
  try { return matchMedia("(prefers-reduced-motion: reduce)").matches; }
  catch { return false; }
}

/**
 * The silhouette, as an <svg>. Exported because it is the honest thing to show
 * anywhere a car is wanted and no model exists, which -- until somebody buys
 * or scans one -- is everywhere.
 *
 * `spin` turns the wheels, the same trick Car.qml keeps for the same reason:
 * the wheels have spokes, so rotation is the one thing a single flat path
 * cannot express. It is refused outright under prefers-reduced-motion.
 */
export function silhouette({ spin = false } = {}) {
  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("class", "car3d-svg");
  svg.setAttribute("viewBox", ART.viewBox);
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");

  const body = document.createElementNS(SVGNS, "path");
  body.setAttribute("d", ART.body);
  body.setAttribute("fill", "currentColor");
  svg.appendChild(body);

  const turning = spin && !reducedMotion();
  for (const w of ART.wheels) {
    const g = document.createElementNS(SVGNS, "g");
    // transform-box: view-box, set in car3d.css, is what makes this origin
    // mean "17.001, 57.152 in the viewBox" rather than "somewhere inside this
    // path's own bounding box". Car.qml learned the same lesson the hard way
    // and its comment is worth reading before touching either.
    g.setAttribute("style", `transform-origin:${w.cx}px ${w.cy}px`);
    if (turning) g.setAttribute("class", "car3d-wheel is-turning");
    else g.setAttribute("class", "car3d-wheel");
    const p = document.createElementNS(SVGNS, "path");
    p.setAttribute("d", w.d);
    p.setAttribute("fill", "currentColor");
    g.appendChild(p);
    svg.appendChild(g);
  }
  return svg;
}

// ---------------------------------------------------------------------------
// Capability checks, cheapest first.

async function modelPresent(url) {
  // HEAD, not GET. The point of this probe is to find out whether a several
  // megabyte file exists without spending several megabytes finding out, and
  // lib/serve.py answers HEAD for static paths through SimpleHTTPRequestHandler.
  try {
    const r = await fetch(url, { method: "HEAD", cache: "no-store" });
    return r.ok;
  } catch { return false; }
}

function webglVerdict() {
  // A throwaway context, immediately released. Creating and dropping one is
  // far cheaper than discovering halfway through a GLTF parse that the driver
  // was never going to co-operate.
  let canvas, gl;
  try {
    canvas = document.createElement("canvas");
    gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
  } catch { gl = null; }
  if (!gl) return { ok: false, why: "no-webgl" };

  let renderer = "";
  try {
    const dbg = gl.getExtension("WEBGL_debug_renderer_info");
    if (dbg) renderer = String(gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) || "");
  } catch { /* privacy.resistFingerprinting hides this; not an error */ }

  // Software rasterisers report themselves, when they report at all. A car
  // rendered by llvmpipe is a laptop fan at full speed for a picture that
  // still looks worse than the silhouette, so this is a refusal rather than a
  // degradation. Browsers increasingly mask the string; an unknown renderer is
  // allowed through and caught later by the frame-time ladder instead.
  const software = /swiftshader|llvmpipe|softpipe|software|microsoft basic/i;
  try { gl.getExtension("WEBGL_lose_context")?.loseContext(); } catch { /* fine */ }

  if (software.test(renderer)) return { ok: false, why: "software-gl", renderer };
  return { ok: true, renderer };
}

// ---------------------------------------------------------------------------
// The light rig.
//
// WHY A HAND-BUILT RIG AND NOT three's RoomEnvironment.
//
// RoomEnvironment is the usual answer and it is a room: a boxy interior with
// scattered emitters, tuned for showing objects on a desk. Automotive paint is
// not lit that way, and clearcoat is the reason -- a car's surface is a mirror
// with a colour behind it, so what you actually see on the flank is the SHAPE
// OF THE LIGHT SOURCE stretched along the body. Long horizontal strips make a
// car read as a car. Scattered blobs make it read as a plastic toy that
// happens to be car-shaped, and that is the exact failure mode this whole
// change exists to avoid.
//
// So: one broad softbox overhead, two long strips down either side at
// deliberately unequal brightness (an even rig has no bright side and no dark
// side, and a car with no bright side has no shape), a dark floor so the lower
// body has something to reflect, and a mid-grey shell so nothing anywhere is
// pure black. Rendered once into a PMREM cube and then thrown away.
//
// It also costs no downloads, which matters: RoomEnvironment is another
// vendored addon, and an HDRI is another megabyte and another licence to read.

function studioEnvironment(THREE, renderer) {
  const scene = new THREE.Scene();
  const parts = [];

  const emitter = (w, h, d, intensity, x, y, z, rx = 0, rz = 0) => {
    const g = new THREE.BoxGeometry(w, h, d);
    const m = new THREE.MeshBasicMaterial();
    // setScalar past 1 is how an unlit material becomes a light source to
    // PMREM: the generator renders this scene with tone mapping off, so values
    // above white survive into the cube map as actual brightness.
    m.color.setScalar(intensity);
    const mesh = new THREE.Mesh(g, m);
    mesh.position.set(x, y, z);
    mesh.rotation.set(rx, 0, rz);
    scene.add(mesh);
    parts.push(g, m);
    return mesh;
  };

  const L = STAGE_LENGTH;

  // The shell. BackSide so we are inside it. Not black: a car in a black void
  // has a black roof and no horizon in its own reflections.
  const shellG = new THREE.BoxGeometry(L * 6, L * 4, L * 6);
  const shellM = new THREE.MeshBasicMaterial({ side: THREE.BackSide });
  shellM.color.setScalar(0.12);
  scene.add(new THREE.Mesh(shellG, shellM));
  parts.push(shellG, shellM);

  // The floor, darker than the shell. This is what puts a dark band along the
  // sill and under the arches, and it is most of what makes the car look like
  // it is standing on something.
  const floorG = new THREE.PlaneGeometry(L * 6, L * 6);
  const floorM = new THREE.MeshBasicMaterial({ side: THREE.DoubleSide });
  floorM.color.setScalar(0.03);
  const floor = new THREE.Mesh(floorG, floorM);
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = -0.02;
  scene.add(floor);
  parts.push(floorG, floorM);

  // Overhead softbox, running the length of the car rather than across it, so
  // the highlight it leaves on the roof and bonnet is a long streak.
  emitter(L * 0.55, 0.05, L * 1.35, 7.0, 0, L * 0.95, 0);

  // Key and fill strips. 3.4 against 1.2 is the asymmetry; equal strips gave a
  // flat, evenly lit body that looked like an untextured render.
  //
  // They are TALL as well as long, and that is not a detail. A door, a wing
  // and a sill are close to vertical, so a thin horizontal strip high up
  // barely reaches them -- the first rig here left the flanks nearly black
  // while the roof and bonnet blew out. Height on the strips is what puts
  // light on the side of a car.
  emitter(0.05, L * 0.60, L * 1.7, 3.4, -L * 1.05, L * 0.36, 0, 0, 0);
  emitter(0.05, L * 0.52, L * 1.7, 1.2, L * 1.05, L * 0.34, 0, 0, 0);

  // A low rear strip. Rim light along the shoulder line: without it the top of
  // the rear wing dissolves into the shell as the camera comes round the back.
  emitter(L * 0.9, L * 0.16, 0.05, 2.2, 0, L * 0.30, -L * 1.1);

  const pmrem = new THREE.PMREMGenerator(renderer);
  // A little blur. Perfectly sharp emitters give razor-edged reflections that
  // read as chrome rather than as clearcoat over paint.
  const target = pmrem.fromScene(scene, 0.035);
  pmrem.dispose();
  for (const p of parts) p.dispose();

  return target;
}

// A contact shadow, drawn rather than cast.
//
// Shadow maps are the obvious way and the wrong one here: they are a second
// render pass every frame, on the machine that must not have a second render
// pass every frame. The car does not move relative to the ground, so the
// shadow does not change, so it can be a single painted texture that costs
// nothing after the first frame. It is a soft ellipse, not a silhouette --
// which is honest, because it is not claiming to be the car's actual shadow.
function contactShadow(THREE, lengthUnits) {
  const size = 256;
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const ctx = c.getContext("2d");
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0.00, "rgba(0,0,0,0.62)");
  g.addColorStop(0.45, "rgba(0,0,0,0.34)");
  g.addColorStop(0.78, "rgba(0,0,0,0.08)");
  g.addColorStop(1.00, "rgba(0,0,0,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);

  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const geo = new THREE.PlaneGeometry(lengthUnits * 1.25, lengthUnits * 0.62);
  const mat = new THREE.MeshBasicMaterial({
    map: tex, transparent: true, depthWrite: false, toneMapped: false,
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.rotation.x = -Math.PI / 2;
  mesh.position.y = 0.002;
  mesh.renderOrder = -1;
  return { mesh, dispose: () => { tex.dispose(); geo.dispose(); mat.dispose(); } };
}

// ---------------------------------------------------------------------------
// Material handling.
//
// THE RULE HERE IS "DO NOT GUESS".
//
// It is tempting to walk the model looking for the mesh called something like
// "body" and give it clearcoat and a gunmetal base colour. That is inventing
// the car's appearance, which is the one thing this project does not do, and
// it is also wrong in practice: a good model already carries its paint, and
// overriding it makes a photorealistic asset look worse.
//
// So the only thing applied unconditionally is envMapIntensity, which is not a
// look, it is the exposure of the rig above. Everything else comes from an
// optional sidecar file keyed by MATERIAL NAME, which the person holding the
// model can read out of Blender in ten seconds. Absent the file, nothing is
// touched. Unknown keys are dropped rather than assigned, so a typo cannot
// quietly turn the paint into rubber.

const TUNABLE = new Set([
  "clearcoat", "clearcoatRoughness", "roughness", "metalness",
  "envMapIntensity", "transmission", "ior", "iridescence", "reflectivity",
  "sheen", "sheenRoughness", "opacity", "transparent", "side",
]);

async function tuning(url) {
  try {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) return null;
    const j = await r.json();
    return j && typeof j === "object" ? j : null;
  } catch { return null; }
}

function dressMaterials(THREE, root, tune) {
  root.traverse((o) => {
    if (!o.isMesh) return;
    o.castShadow = false;
    o.receiveShadow = false;
    const list = Array.isArray(o.material) ? o.material : [o.material];
    for (const m of list) {
      if (!m) continue;
      if ("envMapIntensity" in m) m.envMapIntensity = 1;
      const over = tune && m.name ? tune[m.name] : null;
      if (!over) continue;
      for (const [k, v] of Object.entries(over)) {
        if (!TUNABLE.has(k)) continue;
        // clearcoat and friends only exist on MeshPhysicalMaterial. Assigning
        // them to a MeshStandardMaterial silently does nothing, which is a
        // confusing afternoon, so upgrade rather than pretend.
        if (!(k in m) && m.isMeshStandardMaterial) continue;
        m[k] = v;
      }
      m.needsUpdate = true;
    }
  });
}

// ---------------------------------------------------------------------------

/**
 * Put a car in `host`.
 *
 * Always succeeds at showing something. Returns an unmount function, the
 * convention every view in this application follows (main.js calls it before
 * clearing the stage), and calling it releases the GL context explicitly --
 * see the note on forceContextLoss below for why that is not paranoia.
 *
 * opts:
 *   spin        turn the fallback silhouette's wheels (default false)
 *   rotate      auto-rotate the 3D car (default true, refused under
 *               prefers-reduced-motion)
 *   drag        pointer-drag to spin it by hand (default true)
 *   onState     called with a short status string whenever it changes
 */
export function mountCar(host, opts = {}) {
  const {
    spin = false, rotate = true, drag = true, onState = null,
  } = opts;

  host.classList.add("car3d");
  let state = "probing";
  let live = null;          // the 3D teardown, once there is one
  let fallbackEl = null;
  let dead = false;

  function setState(next, detail) {
    state = next;
    host.dataset.car3d = next;
    if (onState) { try { onState(next, detail); } catch { /* caller's problem */ } }
  }

  function showFallback(why) {
    if (dead || fallbackEl) return;
    // No apology text, no "3D unavailable" banner, no spinner that never
    // resolves. The silhouette is a legitimate picture of the car in its own
    // right -- it is what the panel shows -- and a caption explaining an
    // absence would be the application talking about itself instead of about
    // the car. `why` goes to onState and to the data attribute, where a
    // developer can find it and a driver never sees it.
    fallbackEl = document.createElement("div");
    fallbackEl.className = "car3d-flat";
    fallbackEl.appendChild(silhouette({ spin }));
    host.appendChild(fallbackEl);
    setState(why);
  }

  (async () => {
    if (!await modelPresent(MODEL_URL)) return showFallback("no-model");
    if (dead) return;

    const gpu = webglVerdict();
    if (!gpu.ok) return showFallback(gpu.why);

    let three;
    try {
      three = await start(host, gpu, { rotate, drag, setState });
    } catch (e) {
      // Every failure from here down -- three.js not vendored, an import map
      // missing so the bare specifier does not resolve, a Draco-compressed GLB
      // with no decoder, a corrupt file -- lands here. The message is worth
      // keeping: it is the difference between "vendor the decoder" and "the
      // file is broken", and there is nowhere else to read it.
      console.warn("[car3d] falling back to the silhouette:", e && e.message ? e.message : e);
      return showFallback("no-viewer");
    }
    if (dead) { three.destroy(); return; }
    live = three;
  })();

  return () => {
    dead = true;
    if (live) { live.destroy(); live = null; }
    if (fallbackEl) { fallbackEl.remove(); fallbackEl = null; }
    host.classList.remove("car3d");
    delete host.dataset.car3d;
  };
}

// ---------------------------------------------------------------------------
// The scene itself. Separated so that mountCar stays readable as a decision
// tree and this stays readable as graphics.

async function start(host, gpu, { rotate, drag, setState }) {
  setState("loading");

  // Bare specifiers, resolved by the import map in app.html. If that map is
  // absent this throws immediately and the caller shows the silhouette, which
  // is the correct behaviour for "three.js was never installed".
  const THREE = await import("three");
  const { GLTFLoader } = await import("three/addons/loaders/GLTFLoader.js");

  const canvas = document.createElement("canvas");
  canvas.className = "car3d-canvas";
  host.appendChild(canvas);

  // alpha, and no scene background.
  //
  // The card behind this canvas is painted by a theme token, and themes here
  // are a live system -- lib/theme.py regenerates them and main.js swaps the
  // sheet under a running page. A canvas that painted its own background would
  // be a rectangle of last week's grey sitting in the middle of the new
  // palette. Transparent, the stage is whatever the theme currently says it is
  // and this file never has to know.
  const renderer = new THREE.WebGLRenderer({
    canvas, alpha: true, antialias: true, powerPreference: "default",
  });
  renderer.setClearAlpha(0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  // ACES, not Neutral and not None.
  //
  // Untone-mapped, the specular streaks along a car's flank clip to flat white
  // and the body loses its shape exactly where the shape is most legible. ACES
  // rolls those highlights off instead, which is the whole reason it is the
  // default in every automotive renderer. Exposure sits slightly under 1
  // because the rig above is deliberately bright.
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.95;

  let dprIndex = 0;
  const dprCap = () => Math.min(DPR_STEPS[dprIndex], window.devicePixelRatio || 1);
  renderer.setPixelRatio(dprCap());

  const scene = new THREE.Scene();

  // 30 degrees, which is a long lens.
  //
  // Manufacturers photograph cars at 100mm and up, and the reason is not
  // taste: a wide lens on a four-metre object makes the near wheel enormous
  // and the far one tiny, and the car reads as a caricature of itself. Every
  // "why does my car render look like a toy" thread ends here.
  const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 200);

  const envTarget = studioEnvironment(THREE, renderer);
  scene.environment = envTarget.texture;

  const [gltf, tune] = await Promise.all([
    new GLTFLoader().loadAsync(MODEL_URL),
    tuning(TUNING_URL),
  ]);
  const model = gltf.scene;

  // Measure, centre, and rescale into the stage.
  //
  // glTF in the wild is metres, centimetres, inches and occasionally nothing
  // at all, and a marketplace model is as likely to be sitting a hundred units
  // below the origin as on it. Normalising here means the rig, the camera
  // distance and the shadow are all fixed numbers rather than a second set of
  // things that have to be re-derived for every model that ever gets dropped
  // in -- which is the actual test of whether this is a drop-in.
  const box = new THREE.Box3().setFromObject(model);
  const size = box.getSize(new THREE.Vector3());
  const longest = Math.max(size.x, size.z) || 1;
  const k = STAGE_LENGTH / longest;
  model.scale.setScalar(k);

  const box2 = new THREE.Box3().setFromObject(model);
  const centre = box2.getCenter(new THREE.Vector3());
  model.position.x -= centre.x;
  model.position.z -= centre.z;
  model.position.y -= box2.min.y;   // stand it on the floor, not through it

  dressMaterials(THREE, model, tune);
  scene.add(model);

  const shadow = contactShadow(THREE, STAGE_LENGTH);
  scene.add(shadow.mesh);

  // The bounding sphere of the thing we are actually going to look at. A
  // sphere rather than the box, because the box's apparent size changes as the
  // camera comes round -- frame to the long side and the car is tiny in
  // profile, frame to the short side and it walks out of shot at the corners.
  const bounds = new THREE.Box3().setFromObject(model);
  const height = bounds.max.y;
  const radius = bounds.getBoundingSphere(new THREE.Sphere()).radius;

  // The orbit. Camera moves, car stands still.
  //
  // Rotating the model instead would be one line shorter and wrong: the
  // reflections would sweep the wrong way across the paint, and the painted
  // contact shadow -- which is a fixed ellipse on the floor -- would swing
  // round with it and give the game away immediately.
  //
  // 14 degrees of elevation. Standing height looking at a low coupe, roughly:
  // higher reads as a satellite photograph of a car park and loses the
  // roofline against the floor, lower loses the bonnet entirely.
  const ELEVATION = 14 * (Math.PI / 180);
  const MARGIN = 1.14;              // breathing room around the sphere
  const target = new THREE.Vector3(0, height * 0.45, 0);
  let azimuth = Math.PI * 0.28;     // three-quarter front, the catalogue angle
  let distance = radius * 3;        // replaced by frame() before the first paint

  // Distance is DERIVED, not chosen.
  //
  // A hardcoded camera distance frames one model in one box and nothing else,
  // and this viewer's whole claim is that any GLB drops in. So: solve for the
  // distance at which the bounding sphere just fits, and do it against
  // whichever of the two fields of view is narrower. The vertical one is
  // fixed by the lens; the horizontal one falls out of the aspect ratio, and
  // on a phone in portrait it is the tighter of the two -- which is exactly
  // the case a fixed distance gets wrong, by cropping the nose and tail off.
  //
  // THE TWO FIELDS ARE FITTED SEPARATELY. The sphere is as tall as the car is
  // long, so fitting it into the vertical field left a low coupe in a wide card
  // a third of the card's width (Home, 2026-10-10). Across, the car can turn
  // its full length to the camera, so the sphere still governs; up and down,
  // what the camera sees from 14 degrees is the car's own height plus the
  // little of its length the elevation tips into view.
  const tall = (bounds.max.y - bounds.min.y) / 2 * Math.cos(ELEVATION)
    + radius * Math.sin(ELEVATION);
  function frame() {
    const vFov = camera.fov * (Math.PI / 180);
    const hFov = 2 * Math.atan(Math.tan(vFov / 2) * camera.aspect);
    distance = Math.max((radius * MARGIN) / Math.tan(hFov / 2),
                        (tall * MARGIN) / Math.tan(vFov / 2));
    camera.near = Math.max(0.01, distance - radius * 2);
    camera.far = distance + radius * 4;
    camera.updateProjectionMatrix();
  }

  function place() {
    const c = Math.cos(ELEVATION);
    camera.position.set(
      target.x + Math.sin(azimuth) * c * distance,
      target.y + Math.sin(ELEVATION) * distance,
      target.z + Math.cos(azimuth) * c * distance);
    camera.lookAt(target);
  }

  function resize() {
    const w = Math.max(1, host.clientWidth);
    const h = Math.max(1, host.clientHeight);
    renderer.setPixelRatio(dprCap());
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    frame();
    place();
  }
  const ro = new ResizeObserver(resize);
  ro.observe(host);
  resize();

  // --- the loop, and the rules about when it is allowed to run --------------

  const turning = rotate && !reducedMotion();
  let raf = 0, last = 0, prev = 0;
  let onScreen = true, held = false, stopped = false;
  let slowRun = 0, avgMs = 0;

  function tick(now) {
    if (stopped) return;
    raf = requestAnimationFrame(tick);
    if (now - last < FRAME_MS) return;
    const dt = prev ? Math.min(0.25, (now - prev) / 1000) : 0;
    last = now; prev = now;

    if (turning && !held) azimuth += (Math.PI * 2 / TURN_SECONDS) * dt;
    place();

    const t0 = performance.now();
    renderer.render(scene, camera);
    // A moving average, not a single frame. GPU work is queued, so any one
    // sample is noise; it is the trend that says whether this machine can
    // afford the picture.
    avgMs = avgMs ? avgMs * 0.9 + (performance.now() - t0) * 0.1
                  : performance.now() - t0;

    if (avgMs > SLOW_FRAME_MS) {
      if (++slowRun >= SLOW_FRAMES_BEFORE_STEPPING_DOWN) {
        slowRun = 0;
        if (dprIndex < DPR_STEPS.length - 1) {
          dprIndex++; avgMs = 0; resize();
          setState("degraded");
        } else {
          // Two step-downs and it is still costing more than the budget. This
          // is the promise at the top of the file being kept: the viewer gives
          // up rather than becoming the reason the gauges stutter.
          stop();
          setState("too-slow");
          host.dispatchEvent(new CustomEvent("car3d:giveup", { bubbles: true }));
        }
      }
    } else { slowRun = 0; }
  }

  function run() {
    if (stopped || raf || !onScreen || document.hidden) return;
    last = 0; prev = 0;
    raf = requestAnimationFrame(tick);
  }
  function pause() { cancelAnimationFrame(raf); raf = 0; }
  function stop() { stopped = true; pause(); }

  // Off-screen is not a small saving, it is the whole saving: this canvas
  // lives inside a scrolling stage, and a car turning quietly at the top of a
  // page nobody is looking at is pure cost.
  const io = new IntersectionObserver((es) => {
    onScreen = es.some((e) => e.isIntersecting);
    if (onScreen) run(); else pause();
  }, { threshold: 0.01 });
  io.observe(host);

  const onVis = () => (document.hidden ? pause() : run());
  document.addEventListener("visibilitychange", onVis);

  // Context loss. Drivers do this on suspend, on a GPU reset, and on this
  // family of Intel parts under memory pressure. The default behaviour is a
  // canvas frozen on its last frame, which is the worst possible outcome here
  // because it looks like a working picture of the car and is not.
  const onLost = (e) => {
    e.preventDefault();
    stop();
    canvas.remove();
    setState("context-lost");
    host.dispatchEvent(new CustomEvent("car3d:giveup", { bubbles: true }));
  };
  canvas.addEventListener("webglcontextlost", onLost);

  // Drag to look. Cheap, and it is the difference between a screensaver and
  // something you feel you are holding.
  let dragId = null, dragX = 0;
  const down = (e) => {
    if (!drag || dragId !== null) return;
    dragId = e.pointerId; dragX = e.clientX; held = true;
    canvas.setPointerCapture(dragId);
  };
  const move = (e) => {
    if (dragId !== e.pointerId) return;
    azimuth -= (e.clientX - dragX) * 0.008;
    dragX = e.clientX;
    if (!raf) { place(); renderer.render(scene, camera); }
  };
  const up = (e) => {
    if (dragId !== e.pointerId) return;
    try { canvas.releasePointerCapture(dragId); } catch { /* already gone */ }
    dragId = null; held = false;
  };
  if (drag) {
    canvas.addEventListener("pointerdown", down);
    canvas.addEventListener("pointermove", move);
    canvas.addEventListener("pointerup", up);
    canvas.addEventListener("pointercancel", up);
  }

  setState("live");
  run();

  return {
    destroy() {
      stop();
      io.disconnect();
      ro.disconnect();
      document.removeEventListener("visibilitychange", onVis);
      canvas.removeEventListener("webglcontextlost", onLost);
      if (drag) {
        canvas.removeEventListener("pointerdown", down);
        canvas.removeEventListener("pointermove", move);
        canvas.removeEventListener("pointerup", up);
        canvas.removeEventListener("pointercancel", up);
      }
      shadow.dispose();
      envTarget.dispose();
      scene.traverse((o) => {
        if (!o.isMesh) return;
        o.geometry?.dispose?.();
        const ms = Array.isArray(o.material) ? o.material : [o.material];
        for (const m of ms) {
          if (!m) continue;
          for (const v of Object.values(m)) { if (v && v.isTexture) v.dispose(); }
          m.dispose?.();
        }
      });
      renderer.dispose();
      // forceContextLoss, explicitly.
      //
      // renderer.dispose() releases three's own objects; it does NOT
      // guarantee the browser drops the underlying GL context, and contexts
      // are a hard-capped resource -- a browser keeps roughly sixteen and
      // silently kills the oldest when a seventeenth is asked for. In an
      // application whose whole navigation model is mount, unmount, mount
      // again, leaking one per visit means the car stops working after a
      // dozen trips through the view and takes an unrelated canvas with it.
      renderer.forceContextLoss();
      canvas.remove();
    },
  };
}

// THE CAR IN 3D WHEN IT CAN BE, A PICTURE WHEN IT CANNOT -- for a card that has
// a picture of its own (Home's car card, the demo's). mountCar() on `stage`,
// and onMode(true) while the 3D car is the thing on show, onMode(false) the
// moment it is not: no model, no WebGL, a software renderer, a lost context or
// a machine too slow for it. The card then shows its picture rather than
// mountCar()'s silhouette, so a card never ends up emptier than it was before
// a model existed. Returns the teardown, which releases the GL context.
const SHOWN = new Set(["loading", "live", "degraded"]);

export function carOrPicture(stage, onMode) {
  stage.hidden = true;
  return mountCar(stage, {
    onState(st) {
      if (st === "probing") return;
      const on = SHOWN.has(st);
      stage.hidden = !on;
      onMode(on, st);
    },
  });
}

export default mountCar;
