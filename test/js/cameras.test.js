// The Cameras tab (share/js/views/cameras.js): what it holds open while a
// clip plays (final review, I2), that its polls never pile up on a server that
// stops answering (final review, m2), and that a clip which will not play gives
// the feeds their streams back and says so (final review, m3).
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
// polls unless a test fires the tab's timer (`v.fire(ms)`). Its <img> sources
// get a 404 from the runner's static server; no test looks at what the browser
// does with that, only at which elements hold a source.
//
// A <video> source is different since m3: the tab acts on the video's `error`,
// and the runner's 404 is one. So a test that only wants a clip playing hands
// the tab a source the browser attaches and then waits on, a MediaSource's
// object URL: no request, no error, no metadata. One test leaves the 404 in
// place, to see the browser's own error reach the tab.
import { eq } from "./assert.js";
import camerasView from "../js/views/cameras.js";
import { LIVE_TIMEOUT_MS } from "../js/drowsyrun.js";

const role = (o) => Object.assign({ device: "/dev/v4l/by-id/x", mode: { fmt: "MJPG", w: 1920, h: 1080, fps: 30 },
  sim: false, recording: true, live: true, fps: 29.9, error: null }, o);
const OV = { running: true, storage: { used: 12.34e9, budget: 40e9 },
             roles: { front: role({}), rear: role({}), cabin: role({ mode: { fmt: "MJPG", w: 640, h: 480, fps: 30 } }) } };
const PAUSED = "Paused while a clip plays";
const COULD_NOT_PLAY = "That clip could not be played.";
const settle = () => new Promise((r) => setTimeout(r, 0));
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// Polling with a deadline, never a bare sleep that assumes how fast the box is.
async function until(fn, what) {
  for (let i = 0; i < 400 && !fn(); i++) await wait(5);
  if (!fn()) throw new Error("timed out waiting for " + what);
}

// The front camera's last two clips: Play, from live, goes back ten seconds,
// into the older one (a); the newer one (b) runs on past now.
function clips() {
  const t = Math.floor(Date.now() / 1000);
  return [{ role: "front", file: "a.mp4", start: t - 70, end: t - 5, locked: false },
          { role: "front", file: "b.mp4", start: t - 5, end: t + 55, locked: false }];
}

// Timers the test fires by hand. `ticks` are the tab's repeating polls with the
// interval each asked for; `laters` are the abort timers it sets (negative ids),
// each with the delay it asked for; `cancelled` is every id it stopped.
function timers() {
  const t = { ticks: [], laters: [], cancelled: [] };
  t.every = (fn, ms) => { t.ticks.push({ fn, ms }); return t.ticks.length; };
  t.later = (fn, ms) => { t.laters.push({ fn, ms }); return -t.laters.length; };
  t.stop = (id) => t.cancelled.push(id);
  return t;
}

// A server that never answers. A request stays out until its signal aborts it;
// one made with no signal stays out for good, as a fetch with none would.
function hung() {
  const s = { asked: [] };
  s.get = (path, opts) => {
    const r = { path, signal: opts && opts.signal, done: false };
    s.asked.push(r);
    return new Promise((_, no) => {
      if (r.signal) r.signal.addEventListener("abort", () => { r.done = true; no(new Error("aborted")); });
    });
  };
  const open = (re) => s.asked.filter((r) => !r.done && re.test(r.path)).length;
  // Requests still out: [the overview poll's, the clip list poll's].
  s.out = () => [open(/^\/api\/cams$/), open(/^\/api\/cams\/clips/)];
  return s;
}

// A source for a playing clip that neither loads nor fails. Each is a MediaSource
// of its own, since one can be attached to one element only once.
function quietClips() {
  const q = { files: [], urls: [] };
  q.src = (role, file) => {
    const url = URL.createObjectURL(new MediaSource());
    q.files.push(file);
    q.urls.push(url);
    return url;
  };
  q.free = () => { for (const u of q.urls) URL.revokeObjectURL(u); };
  return q;
}

// `real404`: leave the clip's source as the page builds it, which the runner
// answers with a 404.
async function mount({ get, post = async () => ({}), real404 = false } = {}) {
  if (!document.getElementById("toasts")) {
    const d = document.createElement("div");
    d.id = "toasts";
    document.body.appendChild(d);
  }
  const root = document.createElement("div");
  document.body.appendChild(root);
  const t = timers();
  get = get || (async (path) => (path.startsWith("/api/cams/clips") ? { clips: clips(), events: [] } : OV));
  const q = quietClips();
  const stop = camerasView(root, { get, post, every: t.every, later: t.later, stopEvery: t.stop,
                                   clipSrc: real404 ? undefined : q.src });
  await settle();
  const $ = (sel) => root.querySelector(sel);
  const v = {
    root, stop, t, q,
    // The tab's repeating poll that asked for this interval, fired once.
    fire: (ms) => t.ticks.find((k) => k.ms === ms).fn(),
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
    // The playing <video> reports that its clip cannot be played (a 404, a file
    // the janitor removed, a decode error).
    fail: async (video = $("video.cam-video")) => { video.dispatchEvent(new Event("error")); await settle(); },
    // Every toast on the page, in the order it was raised.
    toasts: () => [...document.getElementById("toasts").children].map((t) => t.textContent),
    // Whether the tab says it is live: no Live button, and the clock reads LIVE.
    isLive: () => $("button.cam-live").hidden && $(".cam-when").textContent === "LIVE",
    tap: async (r) => { root.querySelector(`.cam-feed[data-role="${r}"]`).click(); await settle(); },
    done: () => { stop(); root.remove(); q.free(); },
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
      const next = [v.held(), v.q.files, v.root.querySelector("video").getAttribute("src") === v.q.urls[1]];
      await v.ended();                            // past the newest: live again
      eq([next, v.held(), v.words()], [[["video"], ["a.mp4", "b.mp4"], true], LIVE, NONE]);
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
    v.q.free();
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

  // ---- the polls: one request at a time, and given up (final review, m2)
  ["a server that never answers gets one request out per poll, however often the poll comes round", async () => {
    const srv = hung();
    const v = await mount({ get: srv.get });
    try {
      const start = srv.out();
      for (let i = 0; i < 5; i++) v.fire(2000);          // the overview poll, every 2 s
      for (let i = 0; i < 3; i++) v.fire(10000);         // the clip list poll, every 10 s
      await settle();
      eq([start, srv.out(), srv.asked.length], [[1, 1], [1, 1], 2]);
    } finally { v.done(); }
  }],
  ["each request is given up after the drowsy poll's five seconds, and that poll's next one then goes out", async () => {
    const srv = hung();
    const v = await mount({ get: srv.get });
    try {
      eq([v.t.laters.map((l) => l.ms), LIVE_TIMEOUT_MS], [[LIVE_TIMEOUT_MS, LIVE_TIMEOUT_MS], 5000]);
      v.t.laters[0].fn();                                // five seconds pass on the overview poll's request
      await settle();
      const aborted = srv.asked.map((r) => r.signal.aborted);
      v.fire(2000);                                      // its next tick goes out ...
      v.fire(10000);                                     // ... the clip list's, still waiting, does not
      await settle();
      const after = [srv.out(), srv.asked.length];
      v.t.laters[1].fn();                                // the clip list's five seconds pass
      await settle();
      v.fire(10000);
      await settle();
      eq([aborted, after, srv.out(), srv.asked.length], [[true, false], [[1, 1], 3], [1, 1], 4]);
    } finally { v.done(); }
  }],
  ["a poll that has answered clears its abort timer, and asks again on the next tick", async () => {
    const v = await mount();
    try {
      const cleared = v.t.laters.map((_, i) => v.t.cancelled.includes(-(i + 1)));
      v.fire(2000); v.fire(10000); await settle();
      eq([cleared, v.t.laters.length], [[true, true], 4]);
    } finally { v.done(); }
  }],
  ["leaving the tab gives up the requests still out", async () => {
    const srv = hung();
    const v = await mount({ get: srv.get });
    const before = srv.out();
    v.stop();
    await settle();
    const after = srv.out();
    v.root.remove();
    eq([before, after], [[1, 1], [0, 0]]);
  }],

  // ---- a clip that will not play (final review, m3)
  ["a clip that fails to play gives the feeds their streams back and says in words that it could not play", async () => {
    const v = await mount();
    try {
      await v.play();
      const during = [v.held(), v.words(), v.isLive()];
      const said = v.toasts().length;
      await v.fail();
      eq([during, v.held(), v.words(), v.isLive(), v.root.querySelector("video"), v.toasts().slice(said)],
         [[["video"], { front: "", rear: PAUSED, cabin: PAUSED }, false], LIVE, NONE, true, null, [COULD_NOT_PLAY]]);
    } finally { v.done(); }
  }],
  ["a clip the server cannot serve, a real 404, does the same once the browser reports it", async () => {
    const v = await mount({ real404: true });
    try {
      const said = v.toasts().length;
      await v.play();
      await until(() => v.isLive(), "the browser's error to bring the tab back to live");
      eq([v.held(), v.words(), v.toasts().slice(said)], [LIVE, NONE, [COULD_NOT_PLAY]]);
    } finally { v.done(); }
  }],
  ["the message comes once: a second error from the clip it gave up on says nothing more", async () => {
    const v = await mount();
    try {
      await v.play();
      const video = v.root.querySelector("video");
      const said = v.toasts().length;
      await v.fail(video);
      await v.fail(video);
      eq([v.held(), v.toasts().slice(said)], [LIVE, [COULD_NOT_PLAY]]);
    } finally { v.done(); }
  }],
];
