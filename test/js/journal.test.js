import { eq } from "./assert.js";
import { dayLabel, dayKey, groupDays, dayTotal, chipsFor, filterEntries, sourceLamp, canEdit,
         addQuick, span, clock } from "../js/journal.js";

// local-time anchors, so the test holds in any timezone
const at = (y, mo, d, h, mi = 0) => new Date(y, mo - 1, d, h, mi).getTime() / 1000;
const NOW = at(2026, 10, 17, 18, 0);
const e = (t0, over = {}) => Object.assign({ t0, t1: t0 + 1800, km: 10, open: false, badges: [], note: null }, over);

export default [
  ["today and yesterday are words, older days are dates", () => {
    eq([dayLabel(at(2026, 10, 17, 7), NOW), dayLabel(at(2026, 10, 16, 23), NOW), dayLabel(at(2026, 10, 12, 8), NOW)],
      ["Today", "Yesterday", "Mon 12 Oct"]);
  }],
  ["drives group by local day, newest first, order kept", () => {
    const g = groupDays([e(at(2026, 10, 17, 17)), e(at(2026, 10, 17, 8)), e(at(2026, 10, 16, 18))], NOW);
    eq(g.map((x) => [x.label, x.entries.length]), [["Today", 2], ["Yesterday", 1]]);
  }],
  ["a day's total says drives and distance in the owner's units", () => {
    const g = groupDays([e(at(2026, 10, 17, 17), { km: 40 }), e(at(2026, 10, 17, 8), { km: 2 })], NOW)[0];
    eq([dayTotal(g, true), dayTotal(g, false)], ["2 drives · 26 mi", "2 drives · 42 km"]);
  }],
  ["an unknown distance is left out, not a zero", () => {
    const g = groupDays([e(at(2026, 10, 17, 8), { km: null })], NOW)[0];
    eq(dayTotal(g, true), "1 drive");
  }],
  ["chips exist only for what some drive has", () => {
    const list = [e(1, { badges: [{ kind: "air" }] }), e(2, { badges: [{ kind: "radar" }, { kind: "air" }] }), e(3)];
    eq(chipsFor(list).map((c) => [c.id, c.n]), [["all", 3], ["radar", 1], ["air", 2]]);
  }],
  ["a noted chip appears once a drive has a note", () => {
    eq(chipsFor([e(1, { note: "Rain" })]).map((c) => c.id), ["all", "noted"]);
  }],
  ["filtering by chip", () => {
    const list = [e(1, { badges: [{ kind: "air" }] }), e(2, { note: "x" }), e(3)];
    eq([filterEntries(list, "all").length, filterEntries(list, "air").map((x) => x.t0), filterEntries(list, "noted").map((x) => x.t0)],
      [3, [1], [2]]);
  }],
  ["the simulator is always called Simulated", () => {
    eq([sourceLamp({ present: true, simulated: true }), sourceLamp({ present: true, simulated: false }), sourceLamp({ present: false })],
      [{ text: "Simulated", tone: "warn" }, { text: "Recorded", tone: "ok" }, { text: "No drives yet", tone: "" }]);
  }],
  ["typing is parked only, and says why", () => {
    eq([canEdit(true), canEdit(false)], [{ ok: false, why: "Parked only" }, { ok: true, why: "" }]);
  }],
  ["quick notes join with a dot and never double", () => {
    eq([addQuick("", "Commute"), addQuick("Commute", "Rain"), addQuick("Commute · Rain", "Rain")],
      ["Commute", "Commute · Rain", "Commute · Rain"]);
  }],
  ["an open drive reads 'now' at its end", () => {
    const t = at(2026, 10, 17, 7, 42);
    eq([span(e(t, { t1: t + 2280 })), span(e(t, { t1: null, open: true }))], [`07:42 – 08:20`, "07:42 – now"]);
  }],
  ["dayKey is the local date", () => eq(dayKey(at(2026, 1, 5, 23, 59)), "2026-01-05")],
  ["clock pads", () => eq(clock(at(2026, 1, 5, 7, 5)), "07:05")],
];
