// The camera that faces the child seat, watched for a face.
//
// lib/childalert.py decides whether the seat is occupied; this only tells it
// what the camera sees, every couple of seconds: how many faces, and how bright
// the picture is. It reads the recorder's live picture (/api/cams/<role>/live),
// as facewatch.js does for drowsy mode -- a V4L2 device has one owner -- and
// runs MediaPipe's Face Landmarker over it, slowly (2 frames a second), on its
// own instance so drowsy mode's timing is untouched.
//
// WHAT A FACE DETECTOR CANNOT DO, AND WHY IT IS ENOUGH HERE. A sleeping child
// with the head turned, under a blanket, or in a dark car may show no face at
// all. So "no face" is reported as just that -- never as "empty" -- and the
// loop's latch carries the drive's sightings through to the switch-off. A face
// seen once is enough to arm it. Only the owner clears it.
//
// Every failure is reported, not swallowed: no engine (the MediaPipe files are
// fetched at install, `omacar assets fetch`), no picture, a detector that
// throws, or a picture too dark to see in. Each reads as "unknown" upstream.
//
// meanLuma() and reportFrom() are pure (test/js/childalert.test.js).

import { withToken } from "./core.js";
import { createMjpegParser } from "./mjpeg.js";

export const DARK_LUMA = 28;       // mean 0-255 below this: too dark to judge
export const FPS = 2;
export const REPORT_MS = 2000;
const VENDOR = new URL("./vendor/mediapipe/", import.meta.url);

// The mean brightness of RGBA pixels (Rec. 601 luma).
export function meanLuma(rgba) {
  let sum = 0, n = 0;
  for (let i = 0; i + 2 < rgba.length; i += 4) { sum += 0.299 * rgba[i] + 0.587 * rgba[i + 1] + 0.114 * rgba[i + 2]; n++; }
  return n ? sum / n : 0;
}

// One report from what the last few frames said. `faces` is the most seen in
// any one frame since the last report, so a face between two reports counts.
export function reportFrom({ faces = 0, luma = null, engine = true, picture = true, failed = false, camera = null }) {
  let state = "watching";
  if (!engine) state = "no-engine";
  else if (!picture) state = "no-picture";
  else if (failed) state = "error";
  else if (luma !== null && luma < DARK_LUMA) state = "dark";
  // A face is a face even in a dim picture: it is reported whatever the state.
  return { faces: Math.max(0, Math.min(10, faces | 0)), state, luma: luma === null ? null : Math.round(luma), camera };
}

async function loadDetector() {
  const { FaceLandmarker } = await import("./vendor/mediapipe/vision_bundle.mjs");
  const fileset = { wasmLoaderPath: new URL("wasm/vision_wasm_internal.js", VENDOR).href,
                    wasmBinaryPath: new URL("wasm/vision_wasm_internal.wasm", VENDOR).href };
  const make = (d) => FaceLandmarker.createFromOptions(fileset, {
    baseOptions: { modelAssetPath: new URL("face_landmarker.task", VENDOR).href, delegate: d },
    runningMode: "VIDEO",
    numFaces: 2,                     // a sibling, or a parent leaning in
    minFaceDetectionConfidence: 0.5, // MediaPipe's own default: "confident"
    minFacePresenceConfidence: 0.5,
    outputFaceBlendshapes: false,
    outputFacialTransformationMatrixes: false,
  });
  try { return await make("GPU"); } catch { return make("CPU"); }
}

const send = (body) => fetch(withToken("/api/child-alert/seen"), {
  method: "POST", cache: "no-store", body: JSON.stringify(body),
}).catch(() => {});

// Watch `role` until stop(). Reports go to the loop every REPORT_MS.
export function watchSeat(role, { report = send } = {}) {
  let stopped = false, ctrl = null, det = null, engine = true, picture = false, failed = false;
  let faces = 0, luma = null, lastFrame = 0, stamp = 0;
  const canvas = document.createElement("canvas");
  const g = canvas.getContext("2d", { willReadFrequently: true });
  const small = document.createElement("canvas");
  small.width = 32; small.height = 24;
  const sg = small.getContext("2d", { willReadFrequently: true });

  const every = setInterval(() => {
    if (stopped) return;
    report(reportFrom({ faces, luma, engine, picture, failed, camera: role }));
    faces = 0;
  }, REPORT_MS);

  (async () => {
    while (!stopped && !det) {
      try { det = await loadDetector(); engine = true; } catch (e) {
        engine = false;
        console.warn("child alert: face detection would not load; the latch still works:", e);
        await new Promise((r) => setTimeout(r, 60000));
      }
    }
    while (!stopped) {
      try {
        ctrl = new AbortController();
        const r = await fetch(withToken(`/api/cams/${role}/live`), { signal: ctrl.signal, cache: "no-store" });
        if (!r.ok || !r.body) throw new Error(String(r.status));
        const reader = r.body.getReader();
        const parser = createMjpegParser();
        for (;;) {
          const { value, done } = await reader.read();
          if (done || stopped) break;
          const jpgs = parser.push(value);
          const now = performance.now();
          if (!jpgs.length || now - lastFrame < 1000 / FPS) continue;
          lastFrame = now;
          let bmp;
          try { bmp = await createImageBitmap(new Blob([jpgs[jpgs.length - 1]], { type: "image/jpeg" })); } catch { continue; }
          picture = true;
          if (canvas.width !== bmp.width) { canvas.width = bmp.width; canvas.height = bmp.height; }
          g.drawImage(bmp, 0, 0);
          sg.drawImage(bmp, 0, 0, 32, 24);
          bmp.close();
          luma = meanLuma(sg.getImageData(0, 0, 32, 24).data);
          try {
            stamp = Math.max(performance.now(), stamp + 0.001);
            const res = det.detectForVideo(canvas, stamp);
            faces = Math.max(faces, (res.faceLandmarks || []).length);
            failed = false;
          } catch (e) {
            failed = true;
            console.warn("child alert: the face detector failed on a frame:", e);
          }
        }
      } catch { /* the stream ended or never started */ }
      picture = false;
      if (!stopped) await new Promise((r) => setTimeout(r, 3000));
    }
  })();

  return { stop() { stopped = true; clearInterval(every); if (ctrl) ctrl.abort(); if (det && det.close) det.close(); } };
}

// Follow the settings: watch the chosen camera while the alert is on, switch
// when the owner chooses another, stop when there is none.
export function startChildWatch({ poll = 5000 } = {}) {
  let current = null, watch = null;
  const tick = async () => {
    let want = null;
    try {
      const r = await fetch(withToken("/api/child-alert"), { cache: "no-store" });
      const pub = r.ok ? await r.json() : null;
      const cfg = (pub && pub.config) || {};
      want = pub && !pub.demo && cfg.enabled && cfg.camera ? cfg.camera : null;
    } catch { want = current; }      // no answer: keep doing what we were doing
    if (want !== current) {
      if (watch) watch.stop();
      watch = want ? watchSeat(want) : null;
      current = want;
    }
    setTimeout(tick, poll);
  };
  tick();
}
