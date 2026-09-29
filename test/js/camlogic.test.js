import { eq } from "./assert.js";
import { camBadge, feedState, storageLine, timelineModel, clipAt, stepAcross } from "../js/camlogic.js";

const at = (h, m, s = 0) => new Date(2026, 8, 30, h, m, s).getTime() / 1000;
const role = (o) => Object.assign({ device: "/dev/v4l/by-id/x", mode: { fmt: "MJPG", w: 1920, h: 1080, fps: 30 },
  sim: false, recording: true, live: true, fps: 29.9, error: null }, o);
const ov = (roles, running = true) => ({ running, storage: { used: 12.34e9, budget: 40e9 },
  roles: Object.assign({ front: role({}), rear: role({}), cabin: role({ mode: { fmt: "MJPG", w: 640, h: 480, fps: 30 } }) }, roles) });

export default [
  ["three real cameras read LIVE · 3 cameras", () => eq(camBadge(ov({})).text, "LIVE · 3 cameras")],
  ["one simulated role makes the whole badge SIMULATED", () =>
    eq(camBadge(ov({ rear: role({ sim: true }) })).text, "SIMULATED")],
  ["one camera is singular", () =>
    eq(camBadge(ov({ rear: role({ recording: false }), cabin: role({ recording: false }) })).text, "LIVE · 1 camera")],
  ["no recorder is not recording", () => eq(camBadge(ov({}, false)).text, "NOT RECORDING")],
  ["no server says so", () => eq(camBadge(null).text, "NO SERVER")],
  ["a feed names its role and resolution", () => eq(feedState(ov({}), "front").title, "Front camera · 1080p")],
  ["the cabin at 480p", () => eq(feedState(ov({}), "cabin").title, "Cabin camera · 480p")],
  ["a recording feed is REC, with nothing to explain", () =>
    eq([feedState(ov({}), "rear").rec, feedState(ov({}), "rear").why], [true, null])],
  ["recorder off, with a camera plugged in, says how to start it", () =>
    eq(feedState(ov({}, false), "front").why, "Recorder off — omacar cams on")],
  ["recorder off with nothing plugged in says no camera", () =>
    eq(feedState(ov({ front: role({ device: null }) }, false), "front").why, "No camera")],
  ["a camera that failed says what ffmpeg said", () =>
    eq(feedState(ov({ rear: role({ recording: false, error: "VIDIOC_STREAMON: No space left on device" }) }), "rear").why,
       "VIDIOC_STREAMON: No space left on device")],
  // Task 1's fix round: REC needs a first live frame, so a camera still in
  // its start grace (or restarting after a stall) reads its own status line,
  // never REC, until it has one.
  ["a camera still starting is not REC, and says so", () =>
    eq([feedState(ov({ front: role({ recording: false, live: false, error: "starting: no picture yet (3 s)" }) }), "front").rec,
        feedState(ov({ front: role({ recording: false, live: false, error: "starting: no picture yet (3 s)" }) }), "front").why],
       [false, "starting: no picture yet (3 s)"])],
  ["a camera restarting after a stall carries the stall's own note", () =>
    eq(feedState(ov({ rear: role({ recording: false, live: false,
        error: "restarting (2 s so far): stalled: no picture for 12 s" }) }), "rear").why,
       "restarting (2 s so far): stalled: no picture for 12 s")],
  // A by-id match that exists but was refused (an IPU6 node, a non-USB one,
  // or one below the tablet's floor) is not "no camera" — it says why.
  ["a refused camera carries its own reason, not a bare No camera", () =>
    eq(feedState(ov({ cabin: role({ recording: false, live: false, error: "video64 is an IPU6 capture node" }) }), "cabin").why,
       "video64 is an IPU6 capture node")],
  // A mistyped pattern in omacar-cameras.json disables only its own role,
  // with an error naming the role's own bad pattern — never "no camera" and
  // never a role it did not touch.
  ["an invalid pattern disables only its own role, with an error", () => {
    const o = ov({ front: role({ device: null, recording: false, live: false,
      error: "bad pattern in omacar-cameras.json: '*C920*'" }) });
    eq(feedState(o, "front").why, "bad pattern in omacar-cameras.json: '*C920*'");
    eq([feedState(o, "rear").rec, feedState(o, "cabin").rec], [true, true]);
  }],
  ["storage reads used of budget", () => eq(storageLine({ used: 12.34e9, budget: 40e9 }), "12.3 of 40 GB")],
  ["the timeline puts the main camera's clips and every event on the last hour", () => {
    const clips = [{ role: "front", file: "a", start: at(9, 30), end: at(9, 31), locked: null },
                   { role: "front", file: "b", start: at(8, 30), end: at(8, 31), locked: null },
                   { role: "rear", file: "c", start: at(9, 30), end: at(9, 31), locked: null }];
    const m = timelineModel(clips, [{ id: "e", kind: "hard-braking", t: at(9, 38) }], "front", at(10, 0));
    eq(m.ticks.map((t) => [t.file, t.x.toFixed(3)]), [["a", "0.500"]]);
    eq(m.markers.map((k) => [k.label, k.x.toFixed(3)]), [["Hard braking · 09:38", "0.633"]]);
    eq([m.labels.length, m.labels[0].text, m.labels[12].text], [13, "09:00", "10:00"]);
  }],
  ["a tap on the timeline finds the clip and the second", () => {
    const clips = [{ role: "front", file: "a", start: 1000, end: 1060 }, { role: "front", file: "b", start: 1060, end: 1120 }];
    eq([clipAt(clips, "front", 1075), clipAt(clips, "front", 999), clipAt(clips, "rear", 1075)],
       [{ file: "b", pos: 15 }, null, null]);
  }],
  ["back ten seconds crosses into the previous clip", () =>
    eq(stepAcross([{ role: "front", file: "a", start: 1000, end: 1060 }, { role: "front", file: "b", start: 1060, end: 1120 }],
                  "front", "b", 4, -10), { file: "a", pos: 54 })],
  ["forward past a gap lands on the next clip's start", () =>
    eq(stepAcross([{ role: "front", file: "a", start: 1000, end: 1060 }, { role: "front", file: "c", start: 1300, end: 1360 }],
                  "front", "a", 55, 10), { file: "c", pos: 0 })],
  ["forward past the newest clip is back to live", () =>
    eq(stepAcross([{ role: "front", file: "a", start: 1000, end: 1060 }], "front", "a", 55, 10), null)],
];
