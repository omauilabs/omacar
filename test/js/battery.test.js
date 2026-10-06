// The pack-health card and Home's one-line verdict, from the shape
// lib/battery_health.py summary() returns.
import { eq, ok } from "./assert.js";
import { healthCard, healthLine } from "../js/views/ima.js";

const SERIES = [{ t: 1, window_lo: 40, window_hi: 70, window_width: 30 },
                { t: 2, window_lo: 42, window_hi: 68, window_width: 26 }];
const WATCH = {
  verdict: "Watch it", drives: 12,
  reasons: ["The charge window narrowed by 8 points.", "2 recalibrations in 10 drives."],
  measures: {
    window: { state: "measured", series: SERIES },
    recals: { state: "measured", series: [{ t: 1, recals: 0 }, { t: 2, recals: 2 }] },
    floor_share: { state: "measured", series: [{ t: 1, floor_share: 0.1 }] },
    capacity: { state: "undiscovered", how: "the parked IMA session: tools/ima-session.sh" },
  },
  thresholds: { min_drives: { value: 10, source: "this app's threshold" },
                recalibration: { value: "x", source: "owner reports, no Honda source" } },
};
const EMPTY = { verdict: "Not enough data yet", reasons: [], drives: 0,
  measures: { window: { state: "measured", series: [] },
              recals: { state: "measured", series: [] },
              floor_share: { state: "measured", series: [] } } };

export default [
  ["the card shows the verdict pill and every reason", () => {
    const el = healthCard(WATCH);
    const pill = el.querySelector(".pill");
    eq(pill.textContent, "Watch it");
    ok(pill.classList.contains("warn"), "warn tone");
    for (const r of WATCH.reasons) ok(el.textContent.includes(r), r);
  }],
  ["an undiscovered measure says how to find it", () => {
    const t = healthCard(WATCH).textContent;
    ok(t.includes("Not measured yet"), "not measured");
    ok(t.includes("tools/ima-session.sh"), "how text");
  }],
  ["a measured row draws a sparkline and its latest value", () => {
    const row = healthCard(WATCH).querySelector('[data-state="measured"]');
    ok(row.querySelector("path").getAttribute("d").startsWith("M"), "path");
    ok(row.textContent.includes("26"), "latest");
  }],
  ["the footer names each threshold source", () => {
    const t = healthCard(WATCH).textContent;
    ok(t.includes("this app's threshold") && t.includes("owner reports, no Honda source"), "sources");
  }],
  ["healthLine reads the verdict, and is null on an error", () => {
    eq(healthLine(WATCH), "Health: Watch it");
    eq(healthLine({ error: "no database yet", verdict: "Not enough data yet", reasons: [] }), null);
    eq(healthLine(null), null);
  }],
  ["Not enough data yet with empty series renders", () => {
    const el = healthCard(EMPTY);
    eq(el.querySelector(".pill").textContent, "Not enough data yet");
    ok(!el.querySelector(".pill").classList.contains("ok"), "neutral");
  }],
];
