// car3d.js carOrPicture: a card with a picture of its own shows the 3D car
// only while it is actually on show, and gets its picture back otherwise.
import { eq, ok } from "./assert.js";
import { carOrPicture } from "../js/car3d.js";

const until = (cond, ms = 5000) => new Promise((res, rej) => {
  const t0 = Date.now();
  (function poll() {
    if (cond()) return res();
    if (Date.now() - t0 > ms) return rej(new Error("timed out"));
    setTimeout(poll, 20);
  })();
});

export default [
  ["when the 3D car cannot show, the card gets its picture, not a silhouette", async () => {
    // No model (CI, a fresh clone) or a software renderer (headless Chromium
    // with the model present): either way the viewer falls back.
    const stage = document.createElement("div");
    document.body.appendChild(stage);
    const calls = [];
    const unmount = carOrPicture(stage, (on, st) => calls.push([on, st]));
    ok(stage.hidden, "hidden until the viewer says otherwise");
    await until(() => calls.length > 0);
    eq(calls[0][0], false);
    ok(["no-model", "software-gl", "no-webgl", "no-viewer"].includes(calls[0][1]), calls[0][1]);
    ok(stage.hidden, "still hidden: the picture is what shows");
    unmount();
    ok(!stage.classList.contains("car3d"), "and the teardown leaves nothing behind");
    stage.remove();
  }],
];
