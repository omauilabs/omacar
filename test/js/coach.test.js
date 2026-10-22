import { eq, ok } from "./assert.js";
import { coachView, cardView, toneOf, band } from "../js/coachview.js";

// What the coach screens say (share/js/coachview.js). Where a number came from
// is always said, a missing input is never a zero, and nothing coaches while
// the car is moving.
const sub = (score, source, extra = {}) => Object.assign(
  { score, value: score === null ? null : 40, source, why: score === null ? "not enough data: no charge reading" : null }, extra);
const drive = (over = {}) => Object.assign({
  t0: 1_800_000_000, km: 12.4, minutes: 22, source: "real", simulated: false,
  headline: { score: 82, partial: false, basis: ["glide", "regen", "smooth"], missing: [], note: null },
  subs: { regen: sub(70, "estimated"), smooth: sub(90, "measured"), glide: sub(85, "measured") },
  facts: { autostop: { share: 71, seconds: 54, source: "measured", why: null },
           economy: { pct: 8, n: 5, source: "estimated", why: null } },
  hint: { id: "late-braking", text: "You slowed hard 6 times; easing off about 3 s earlier lets the IMA recover more." },
}, over);
const feed = (drives, over = {}) => Object.assign({ present: true, source: "real", simulated: false, drives, last: drives[drives.length - 1] }, over);

export default [
  ["no drive yet says how to get one, with no number", () => {
    const v = coachView({ present: false });
    eq([v.state, v.headline.text, v.subs.length, v.hint], ["absent", "—", 0, null]);
  }],
  ["tones: 75+ ok, 50+ warn, below bad, none for no score", () =>
    eq([toneOf(80), toneOf(60), toneOf(20), toneOf(null)], ["ok", "warn", "bad", "none"])],
  ["a null score is never a band of its own", () => eq(band(null), "No score")],
  ["every sub-score says measured or estimated", () => {
    const v = coachView(feed([drive()]));
    eq(v.subs.map((s) => [s.key, s.source]),
      [["regen", "Estimated"], ["smooth", "Measured"], ["glide", "Measured"]]);
  }],
  ["the facts say where they came from too", () => {
    const v = coachView(feed([drive()]));
    eq(v.facts.map((f) => [f.key, f.source]), [["autostop", "Measured"], ["economy", "Estimated"]]);
    ok(/8% less than your usual/.test(v.facts[1].text), v.facts[1].text);
  }],
  ["a simulated feed says Simulated everywhere, whatever it was labelled", () => {
    const v = coachView(feed([drive({ source: "sim" })], { source: "sim", simulated: true }));
    eq([v.source, v.sourceTone], ["Simulated", "warn"]);
    ok(v.subs.every((s) => s.source === "Simulated"), "sub-scores");
    ok(v.facts.every((f) => f.source === "Simulated"), "facts");
  }],
  ["a missing sub-score shows its reason and no number, never 0", () => {
    const d = drive({ subs: { regen: sub(null, "estimated"), smooth: sub(90, "measured"), glide: sub(85, "measured") },
      headline: { score: 88, partial: true, basis: ["glide", "smooth"], missing: ["regen"],
                  note: "Worked out from gliding and smoothness; regen capture not enough data." } });
    const v = coachView(feed([d]));
    eq([v.subs[0].score, v.subs[0].tone, v.subs[0].why], [null, "none", "Not enough data: no charge reading"]);
    eq([v.headline.n, v.headline.partial], [88, true]);
    ok(/Worked out from/.test(v.headline.note), v.headline.note);
  }],
  ["no headline: a dash, with the reason, not zero", () => {
    const d = drive({ headline: { score: null, partial: false, basis: [], missing: [], note: "Not enough data: too short" } });
    const v = coachView(feed([d]));
    eq([v.headline.text, v.headline.n, v.headline.note], ["—", null, "Not enough data: too short"]);
  }],
  ["parked, the hint is shown", () => {
    const v = coachView(feed([drive()]), { locked: false });
    ok(/6 times/.test(v.hint.text), "hint");
  }],
  ["moving, the hint is not in the view at all", () => {
    const v = coachView(feed([drive()]), { locked: true });
    eq([v.hint, v.state], [null, "moving"]);
    ok(/parked/.test(v.hintNote), v.hintNote);
  }],
  ["Home's card on the move says only Drive in progress", () => {
    const c = cardView(feed([drive()]), { locked: true });
    eq([c.state, c.line, c.big, c.hint, c.source], ["moving", "Drive in progress", "", "", ""]);
  }],
  ["Home's card parked shows the score, the band and the hint", () => {
    const c = cardView(feed([drive()]), { locked: false });
    eq([c.state, c.big, c.line, c.tone], ["ready", "82", "Good", "ok"]);
    ok(/slowed hard/.test(c.hint), c.hint);
  }],
  ["Home's card with nothing scored has no number", () => {
    const c = cardView({ present: false }, { locked: false });
    eq([c.state, c.big], ["absent", "—"]);
  }],
  ["a partial score says so on the card", () => {
    const d = drive({ headline: { score: 70, partial: true, basis: ["glide", "smooth"], missing: ["regen"], note: "x" } });
    ok(/partial/.test(cardView(feed([d]), {}).line), "partial");
  }],
  ["the trend is every drive, in order, and the last is the one shown", () => {
    const ds = [60, 70, 80].map((n, i) => drive({ t0: 1000 + i, headline: { score: n, partial: false, missing: [] } }));
    const v = coachView(feed(ds));
    eq(v.trend.map((p) => p.score), [60, 70, 80]);
    eq([v.index, v.isLast, v.headline.n], [2, true, 80]);
  }],
  ["picking an earlier drive shows that drive's score and its own hint", () => {
    const ds = [drive({ hint: { text: "first hint" }, headline: { score: 55, partial: false, missing: [] } }),
                drive({ hint: { text: "second hint" } })];
    const v = coachView(feed(ds), { pick: 0 });
    eq([v.headline.n, v.hint.text, v.isLast], [55, "first hint", false]);
  }],
  ["miles by default for an imperial car, km otherwise", () => {
    eq(coachView(feed([drive()]), { imperial: true }).km, "7.7 mi");
    eq(coachView(feed([drive()]), { imperial: false }).km, "12.4 km");
  }],
];
