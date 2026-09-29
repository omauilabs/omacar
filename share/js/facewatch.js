// The cabin camera, watched: the recorder's live picture, through MediaPipe's
// Face Landmarker (fetched at install into share/js/vendor/mediapipe), into the measures'
// frame shape (drowsy.js, frameFrom). The recorder owns the camera -- a V4L2
// device has one owner -- so this reads its JPEGs rather than opening the
// device a second time.
//
// The page draws each picture into a canvas and hands the canvas to the
// landmarker in VIDEO mode, with blendshapes and the facial transformation
// matrix switched on.
//
// THE FRAME CLOCK. A frame's `t` is the moment its picture is handed to the
// landmarker, on the page's monotonic clock (performance.now(), in seconds):
// the same number, in milliseconds, MediaPipe is given as the frame's
// timestamp. drowsyrun.js steps its ladder on this same clock. It is taken
// right before detection, in the same synchronous run as onFrame, so nothing
// else on the page can step between a frame's stamp and its own step. A
// camera that stalls shows up as a gap in these times, which the measures
// treat as a discontinuity.

import { withToken } from "./core.js";
import { createMjpegParser } from "./mjpeg.js";
import { frameFrom } from "./drowsy.js";

const VENDOR = new URL("./vendor/mediapipe/", import.meta.url);

// The 12 MB engine loads here and only here, the first time a cabin picture
// is worth watching. GPU first; the CPU if the GPU will not start.
export async function loadLandmarker(delegate = "GPU") {
  const { FaceLandmarker } = await import("./vendor/mediapipe/vision_bundle.mjs");
  // The SIMD engine, named directly: it is the only one fetched (Task 8), and
  // Chromium on the tablet has WebAssembly SIMD.
  const fileset = { wasmLoaderPath: new URL("wasm/vision_wasm_internal.js", VENDOR).href,
                    wasmBinaryPath: new URL("wasm/vision_wasm_internal.wasm", VENDOR).href };
  const make = (d) => FaceLandmarker.createFromOptions(fileset, {
    baseOptions: { modelAssetPath: new URL("face_landmarker.task", VENDOR).href, delegate: d },
    runningMode: "VIDEO",
    numFaces: 1,
    outputFaceBlendshapes: true,
    outputFacialTransformationMatrixes: true,
  });
  try { return await make(delegate); } catch (e) { if (delegate !== "CPU") return make("CPU"); throw e; }
}

export function watchCabin({ landmarker, canvas, onFrame, fps = 12 }) {
  let stopped = false, ctrl = null, lastMs = 0, lastStamp = 0, busy = false;
  const g = canvas.getContext("2d");
  (async () => {
    while (!stopped) {
      try {
        ctrl = new AbortController();
        const r = await fetch(withToken("/api/cams/cabin/live"), { signal: ctrl.signal, cache: "no-store" });
        if (!r.ok || !r.body) throw new Error(String(r.status));
        const reader = r.body.getReader();
        const parser = createMjpegParser();
        for (;;) {
          const { value, done } = await reader.read();
          if (done || stopped) break;
          const jpgs = parser.push(value);
          const now = performance.now();
          if (!jpgs.length || busy || now - lastMs < 1000 / fps) continue;
          lastMs = now;
          busy = true;
          try {
            const bmp = await createImageBitmap(new Blob([jpgs[jpgs.length - 1]], { type: "image/jpeg" }));
            if (stopped) { bmp.close(); break; }
            if (canvas.width !== bmp.width) { canvas.width = bmp.width; canvas.height = bmp.height; }
            g.drawImage(bmp, 0, 0);
            bmp.close();
            // MediaPipe wants strictly increasing timestamps in VIDEO mode.
            const stamp = Math.max(performance.now(), lastStamp + 0.001);
            lastStamp = stamp;
            onFrame(frameFrom(landmarker.detectForVideo(canvas, stamp), stamp / 1000));
          } finally { busy = false; }
        }
      } catch { /* the stream ended or never started: try again shortly */ }
      if (!stopped) await new Promise((res) => setTimeout(res, 3000));
    }
  })();
  return { stop() { stopped = true; if (ctrl) ctrl.abort(); } };
}
