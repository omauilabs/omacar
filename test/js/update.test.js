import { eq } from "./assert.js";
import { updateView, homeNote, ago } from "../js/updateui.js";

// What the Updates sheet and Home's note say (share/js/updateui.js). The rules
// that matter: the buttons are greyed with a reason when not parked and never
// hidden; "Update when parked" needs something waiting; Home says nothing
// unless an update waits on a tablet that can apply it.
const cur = { sha: "a".repeat(40), short: "aaaaaaaaaaaa", date: "2026-10-16T09:00:00+00:00", subject: "Fix the thing" };
const av = { sha: "b".repeat(40), short: "bbbbbbbbbbbb", date: "2026-10-17T01:00:00+00:00", behind: 3,
             changes: ["Add the fridge card", "Fix night look"] };
const parked = { ok: true, reasons: [] };
const base = { present: true, adopted: true, current: cur, available: null, can_apply: parked,
               previous: "cccccccccccc", auto: false, window: "02:00-05:00", checked_at: 1050 };

export default [
  ["the demo has no updater, and says so", () => {
    const v = updateView({ present: false, demo: true });
    eq([v.state, v.actions], ["off", {}]);
  }],
  ["up to date: a green lamp and nothing to apply", () => {
    const v = updateView(base, false, 1100);
    eq([v.state, v.tone, v.headline, v.actions.apply, v.checked], ["current", "ok", "Up to date", false, "just now"]);
  }],
  ["one waiting: singular, with the sha and the PR titles", () => {
    const v = updateView({ ...base, available: { ...av, behind: 1 } });
    eq([v.headline, v.available.short, v.changes.length], ["1 update waiting", "bbbbbbbbbbbb", 2]);
  }],
  ["several waiting, parked: Update is live", () => {
    const v = updateView({ ...base, available: av });
    eq([v.headline, v.actions.apply, v.actions.check, v.why], ["3 updates waiting", true, true, ""]);
  }],
  ["the app's own moving lock greys Update even if the server says parked", () => {
    const v = updateView({ ...base, available: av }, true);
    eq([v.actions.apply, v.actions.rollback, v.actions.check, v.why], [false, false, true, "Available when you stop."]);
  }],
  ["not parked per the server: greyed, with the reasons beside it", () => {
    const v = updateView({ ...base, available: av,
      can_apply: { ok: false, reasons: ["a radar alert is active", "battery 22% is under 30% and not charging"] } });
    eq([v.actions.apply, v.why],
       [false, "Not now: a radar alert is active; battery 22% is under 30% and not charging."]);
  }],
  ["a tablet that was never adopted says how, and cannot apply", () => {
    const v = updateView({ ...base, adopted: false, available: av });
    eq([v.state, v.actions.apply, v.why], ["unset", false, "Run omacar update adopt once on the tablet."]);
  }],
  ["while the updater works, nothing can be started", () => {
    const v = updateView({ ...base, available: av, running: true });
    eq([v.state, v.actions.apply, v.actions.check, v.actions.rollback], ["working", false, false, false]);
  }],
  ["rollback needs a previous release", () => {
    const v = updateView({ ...base, previous: null });
    eq(v.actions.rollback, false);
  }],
  ["the last result carries the gate's steps, a failure with its note", () => {
    const v = updateView({ ...base, last: { ok: false, kind: "apply", reason: "the gate failed", at: 950,
      gate: { steps: [{ name: "syntax", ok: true, secs: 0.2 }, { name: "guards_test", ok: false, secs: 3, note: "FAIL a guard" }] } } }, false, 1000);
    eq([v.last.ok, v.last.when, v.gate.map((s) => [s.name, s.ok, s.note])],
       [false, "just now", [["syntax", true, ""], ["guards_test", false, "FAIL a guard"]]]);
  }],
  ["a failed check is a warning, not 'up to date'", () => {
    const v = updateView({ ...base, check_error: "no route to host" });
    eq([v.state, v.tone], ["error", "warn"]);
  }],
  ["automatic updates show off by default and the window when on", () => {
    eq([updateView(base).auto, updateView({ ...base, auto: true }).auto], ["Off", "On, 02:00-05:00"]);
  }],
  ["Home says nothing when current, unadopted, or in the demo", () => {
    eq([homeNote(base), homeNote({ ...base, adopted: false, available: av }), homeNote({ demo: true }), homeNote(null)],
       [null, null, null, null]);
  }],
  ["Home's note is calm: a count and the first change", () => {
    eq(homeNote({ ...base, available: av }), { text: "An update is waiting · 3 changes", sub: "Add the fridge card" });
    eq(homeNote({ ...base, available: { ...av, behind: 1 } }).text, "An update is waiting");
  }],
  ["ago() speaks in minutes, hours and days", () => {
    eq([ago(null), ago(100, 130), ago(0 + 1, 1 + 600), ago(1, 1 + 7200), ago(1, 1 + 200000)],
       [null, "just now", "10 min ago", "2 h ago", "2 d ago"]);
  }],
];
