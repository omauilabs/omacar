import { ok } from "./assert.js";

export default [
  ["the fetched bundle loads, with the two things drowsy mode uses", async () => {
    const m = await import("../js/vendor/mediapipe/vision_bundle.mjs");
    ok(typeof m.FilesetResolver === "function" && typeof m.FaceLandmarker === "function",
       "FilesetResolver and FaceLandmarker are exported");
  }],
];
