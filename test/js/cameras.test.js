// The Cameras tab (share/js/views/cameras.js): what it holds open while a
// clip plays (final review, I2).
//
// serve.py speaks HTTP/1.1, so Chromium allows six connections to it. The
// three feeds, drowsy mode's cabin stream and a playing clip took five, and
// OmaPlay's phone picture a sixth, and then drowsy mode's own polls waited
// behind them: "Paused · no car data", with no alert able to start. So while
// a clip plays the tab holds the clip and nothing else, and its feeds come
// back when playback ends or the tab is left. Drowsy mode's cabin stream is
// a fetch in facewatch.js, not an element here, and is never let go of for
// playback.
//
// The view is mounted on a scripted server and hand-held timers, so nothing
// polls. Its <img> and <video> sources get a 404 from the runner's static
// server; no test looks at what the browser does with that, only at which
// elements hold a source.
import { eq } from "./assert.js";
import camerasView from "../js/views/cameras.js";

const role = (o) => Object.assign({ device: "/dev/v4l/by-id/x", mode: { fmt: "MJPG", w: 1920, h: 1080, fps: 30 },
  sim: false, recording: true, live: true, fps: 29.9, error: null }, o);
const OV = { running: true, storage: { used: 12.34e9, budget: 40e9 },
             roles: { front: role({}), rear: role({}), cabin: role({ mode: { fmt: "MJPG", w: 640, h: 480, fps: 30 } }) } };
const PAUSED = "Paused while a clip plays";
const settle = () => new Promise((r) => setTimeout(r, 0));

// The front camera's last two clips: Play, from live, goes back ten seconds,
// into the older one (a); the newer one (b) runs on past now.
function clips() {
  const t = Math.floor(Date.now() / 1000);
  return [{ role: "front", file: "a.mp4", start: t - 70, end: t - 5, locked: false },
          { role: "front", file: "b.mp4", start: t - 5, end: t + 55, locked: false }];
}

async function mount() {
  const root = document.createElement("div");
  document.body.appendChild(root);
  const get = async (path) => (path.startsWith("/api/cams/clips") ? { clips: clips(), events: [] } : OV);
  const stop = camerasView(root, { get, post: async () => ({}), every: () => 0, stopEvery: () => {} });
  await settle();
  const $ = (sel) => root.querySelector(sel);
  const v = {
    root, stop,
    // Every element in the tab that holds a source, as "img:front" or "video".
    held: () => [...root.querySelectorAll("[src]")].map((e) =>
      (e.tagName === "IMG" ? "img:" + e.closest(".cam-feed").dataset.role : e.tagName.toLowerCase())).sort(),
    // What each feed says in place of its picture, or "" when it says nothing.
    words: () => Object.fromEntries(["front", "rear", "cabin"].map((r) => {
      const w = root.querySelector(`.cam-feed[data-role="${r}"] .cam-why`);
      return [r, w.hidden ? "" : w.textContent];
    })),
    play: async () => { $("button.cam-btn.play").click(); await settle(); await settle(); },
    ended: async () => { $("video.cam-video").dispatchEvent(new Event("ended")); await settle(); },
    live: async () => { $("button.cam-live").click(); await settle(); },
    tap: async (r) => { root.querySelector(`.cam-feed[data-role="${r}"]`).click(); await settle(); },
    done: () => { stop(); root.remove(); },
  };
  return v;
}

const LIVE = ["img:cabin", "img:front", "img:rear"];
const NONE = { front: "", rear: "", cabin: "" };

export default [
  ["while a clip plays the tab holds the clip and nothing else, and the side feeds say why they are paused", async () => {
    const v = await mount();
    try {
      const before = [v.held(), v.words()];
      await v.play();
      eq([before, v.held(), v.words()],
         [[LIVE, NONE], ["video"], { front: "", rear: PAUSED, cabin: PAUSED }]);
    } finally { v.done(); }
  }],
  ["playing on into the next clip still holds only the clip; after the newest, the feeds come back", async () => {
    const v = await mount();
    try {
      await v.play();                             // the older clip, a
      await v.ended();                            // on into b
      const next = [v.held(), v.root.querySelector("video").getAttribute("src").includes("b.mp4")];
      await v.ended();                            // past the newest: live again
      eq([next, v.held(), v.words()], [[["video"], true], LIVE, NONE]);
    } finally { v.done(); }
  }],
  ["Live brings the feeds back, and so does tapping a paused feed, which becomes the main one", async () => {
    const v = await mount();
    try {
      await v.play();
      await v.live();
      const live = [v.held(), v.words()];
      await v.play();
      await v.tap("rear");
      const main = v.root.querySelector(".cam-feed.is-main").dataset.role;
      eq([live, v.held(), v.words(), main], [[LIVE, NONE], LIVE, NONE, "rear"]);
    } finally { v.done(); }
  }],
  ["leaving the tab while a clip plays lets go of the clip too", async () => {
    const v = await mount();
    await v.play();
    v.stop();
    const held = v.held();
    v.root.remove();
    eq(held, []);
  }],
  ["a feed with no picture keeps its own reason while a clip plays", async () => {
    const was = OV.roles.cabin;
    OV.roles.cabin = role({ recording: false, live: false, error: "no camera" });
    const v = await mount();
    try {
      await v.play();
      eq([v.held(), v.words()], [["video"], { front: "", rear: PAUSED, cabin: "No camera" }]);
    } finally { v.done(); OV.roles.cabin = was; }
  }],
];
