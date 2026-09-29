// The cabin watch's two kinds of failure (Task 9 fix round 1, I3): a stream
// that ends or will not start is retried quietly; a frame the tracker, or
// whatever onFrame runs, throws on is reported, and three in a row stop the
// watch. Everything outside watchCabin is faked: the stream, the decoder,
// the canvas and the landmarker. No clock is read; the loop is only given
// turns to run.
import { eq } from "./assert.js";
import { watchCabin, MAX_TRACKER_ERRORS } from "../js/facewatch.js";

const enc = (s) => new TextEncoder().encode(s);
const part = (n) => {
  const jpg = new Uint8Array([0xff, 0xd8, n, 0xff, 0xd9]);
  const head = enc(`--omacarframe\r\nContent-Type: image/jpeg\r\nContent-Length: ${jpg.length}\r\n\r\n`);
  const out = new Uint8Array(head.length + jpg.length + 2);
  out.set(head);
  out.set(jpg, head.length);
  out.set(enc("\r\n"), head.length + jpg.length);
  return out;
};
// A live stream of `n` pictures, one per read, that then stays open until
// the watch aborts it. `refuse` fetches fail first, as a recorder that is
// not up yet.
const live = (n, log, refuse = 0) => (signal) => {
  log.fetches++;
  if (log.fetches <= refuse) return Promise.reject(new Error("connection refused"));
  let i = 0;
  return Promise.resolve({ ok: true, body: { getReader: () => ({
    read: () => (i < n ? Promise.resolve({ value: part(i++), done: false })
      : new Promise((done) => signal.addEventListener("abort", () => done({ value: undefined, done: true })))),
  }) } });
};
const canvas = () => ({ width: 0, height: 0, getContext: () => ({ drawImage() {} }) });
const decode = async () => ({ width: 4, height: 3, close() {} });
const FACE = { faceBlendshapes: [{ categories: [{ categoryName: "eyeBlinkLeft", score: 0.1 },
  { categoryName: "eyeBlinkRight", score: 0.1 }] }] };
// Turns for the watch's loop: until `done()`, or 200 of them.
const turns = async (done = () => false) => {
  for (let i = 0; i < 200 && !done(); i++) await new Promise((res) => setTimeout(res, 0));
};
const watch = (o) => watchCabin(Object.assign({ canvas: canvas(), onFrame: () => {}, fps: Infinity, decode, retryMs: 0 }, o));

export default [
  ["a tracker that throws on every frame is reported each time, and the third in a row stops the watch", async () => {
    const log = { fetches: 0 }, errs = [];
    let detects = 0;
    watch({ landmarker: { detectForVideo() { detects++; throw new Error("GPU context lost"); } },
            onError: (e, i) => errs.push([e.message, i.consecutive, i.fatal]), fetchLive: live(10, log) });
    await turns(() => errs.length >= 3);
    await turns(() => false);                      // and nothing after it
    eq([MAX_TRACKER_ERRORS, errs, detects, log.fetches],
       [3, [["GPU context lost", 1, false], ["GPU context lost", 2, false], ["GPU context lost", 3, true]], 3, 1]);
  }],
  ["an error behind onFrame -- the measures or the ladder -- counts the same", async () => {
    const log = { fetches: 0 }, errs = [];
    watch({ landmarker: { detectForVideo: () => FACE }, onFrame: () => { throw new Error("ladder"); },
            onError: (e, i) => errs.push(i.fatal), fetchLive: live(10, log) });
    await turns(() => errs.length >= 3);
    await turns(() => false);
    eq([errs, log.fetches], [[false, false, true], 1]);
  }],
  ["a good frame between failures starts the count again: never fatal", async () => {
    const log = { fetches: 0 }, errs = [], frames = [];
    let n = 0;
    const w = watch({ landmarker: { detectForVideo() { if (n++ % 3 !== 2) throw new Error("glitch"); return FACE; } },
                      onFrame: (f) => frames.push(f.face), onError: (e, i) => errs.push(i.consecutive),
                      fetchLive: live(9, log) });
    await turns(() => n >= 9);
    await turns(() => false);
    w.stop();
    eq([errs, frames], [[1, 2, 1, 2, 1, 2], [true, true, true]]);
  }],
  ["a stream that will not start is retried quietly: no tracker error", async () => {
    const log = { fetches: 0 }, errs = [], frames = [];
    const w = watch({ landmarker: { detectForVideo: () => FACE }, onFrame: (f) => frames.push(f.face),
                      onError: () => errs.push(1), fetchLive: live(2, log, 2) });
    await turns(() => frames.length >= 2);
    w.stop();
    eq([log.fetches, frames, errs], [3, [true, true], []]);
  }],
  ["a picture that will not decode is skipped, and says nothing about the tracker", async () => {
    const log = { fetches: 0 }, errs = [], frames = [];
    let d = 0;
    const w = watch({ landmarker: { detectForVideo: () => FACE }, onFrame: (f) => frames.push(f.face),
                      onError: () => errs.push(1), fetchLive: live(5, log),
                      decode: async (b) => { if (d++ < 3) throw new Error("bad JPEG"); return decode(b); } });
    await turns(() => frames.length >= 2);
    w.stop();
    eq([frames.length, errs], [2, []]);
  }],
];
