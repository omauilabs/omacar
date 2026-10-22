// The Work dashboard's pure logic (share/js/workdashlogic.js): what may be on
// screen while moving vs parked, and how the relay's numbers read.
import { eq } from "./assert.js";
import {
  viewMode, showsPr, needsReadingNotice, elapsed, glance, parseStat, barShare, groupByDir,
} from "../js/workdashlogic.js";

const STAT = ` lib/fastbaud.py          | 38 +++++++++++++++++++++++++++-----
 test/fastbaud_test.py    | 61 +++++++++++++++++++++++++++++++++++++++++
 README.md                |  9 ++++++
 share/logo.png           | Bin 0 -> 512 bytes
 4 files changed, 101 insertions(+), 7 deletions(-)`;

export default [
  ["parked: the full dashboard", () => eq(viewMode({ present: true, moving: false, whileMoving: false }), "full")],
  ["moving, default: a glance only", () => eq(viewMode({ present: true, moving: true, whileMoving: false }), "glance")],
  ["moving, owner chose while_moving: the full dashboard", () =>
    eq(viewMode({ present: true, moving: true, whileMoving: true }), "full")],
  ["no relay is down in every case, moving or not", () =>
    eq([true, false].map((m) => viewMode({ present: false, moving: m, whileMoving: true })), ["down", "down"])],
  ["PR links and diffs only with the full view", () => eq(["full", "glance", "down"].map(showsPr), [true, false, false])],
  ["reading notice: only moving + chosen + a PR on screen + not dismissed", () => {
    const base = { moving: true, whileMoving: true, mode: "full", anyPr: true, dismissed: false };
    eq(needsReadingNotice(base), true);
    eq([{ moving: false }, { whileMoving: false }, { mode: "glance" }, { anyPr: false }, { dismissed: true }]
      .map((o) => needsReadingNotice(Object.assign({}, base, o))), [false, false, false, false, false]);
  }],
  ["elapsed reads as minutes and hours, and unknown is a dash", () =>
    eq([elapsed(20), elapsed(125), elapsed(3600), elapsed(3900), elapsed(null), elapsed(undefined)],
       ["under 1m", "2m", "1h", "1h 05m", "—", "—"])],
  ["glance: counts, waiting first, zeros left out", () =>
    eq(glance({ counts: { waiting: 1, running: 2, failed: 0, done: 3 } }), "1 waiting for you · 2 running · 3 done")],
  ["glance with nothing says so", () => eq(glance({ counts: {} }), "No sessions")],
  ["a diffstat becomes files and a total", () => {
    const s = parseStat(STAT);
    eq(s.files.map((f) => f.name), ["lib/fastbaud.py", "test/fastbaud_test.py", "README.md", "share/logo.png"]);
    eq(s.total, "4 files changed, 101 insertions(+), 7 deletions(-)");
    eq([s.files[0].n, s.files[0].del > 0, s.files[3].bin], [38, true, true]);
  }],
  ["no stat is no files, not a crash", () => eq(parseStat(null), { files: [], total: "" })],
  ["bars are shares of the busiest file", () => eq(barShare(parseStat(STAT).files).map((x) => Math.round(x * 100)), [62, 100, 15, 0])],
  ["files fold by folder, top-level ones together", () => {
    const g = groupByDir(parseStat(STAT).files);
    eq(g.map((x) => [x.dir, x.files.length]), [["lib", 1], ["test", 1], ["(top level)", 1], ["share", 1]]);
    eq(g[0].files[0].base, "fastbaud.py");
  }],
];
