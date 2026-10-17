// The pre-trip check's judging (share/js/pretrip.js): the overall verdict
// across combinations, and the rule that an unknown is never an ok.
import { eq, ok } from "./assert.js";
import { verdict, codesCheck, readinessCheck, serviceCheck, gapsCheck, batteryCheck,
         imaCheck, tyresCheck, fuelCheck, manualCheck, buildChecks, dueItems,
         rangeKm, stepTrip, tyrePressures, CHECKLIST } from "../js/pretrip.js";

const L = (...lamps) => lamps.map((lamp, i) => ({ id: "c" + i, lamp }));
const svc = (items) => ({ items });
const item = (o) => Object.assign({ item: "Thing", life: 80, km_left: 9000, days_left: 300 }, o);

export default [
  // ---- verdict
  ["all ok is Ready", () => eq(verdict(L("ok", "ok", "ok")).label, "Ready")],
  ["one warn is Ready, with 1 note", () => eq(verdict(L("ok", "warn")).label, "Ready, with 1 note")],
  ["two notes pluralise", () => eq(verdict(L("ok", "warn", "warn")).label, "Ready, with 2 notes")],
  ["a bad anywhere is Fix first", () => {
    eq(verdict(L("ok", "warn", "bad")).label, "Fix first");
    eq(verdict(L("bad")).level, "fix");
  }],
  ["an unknown is a note, never Ready", () => {
    const v = verdict(L("ok", "ok", "unknown"));
    eq([v.label, v.level, v.notes], ["Ready, with 1 note", "notes", 1]);
  }],
  ["warns and unknowns add up as notes", () => eq(verdict(L("ok", "warn", "unknown", "unknown")).label, "Ready, with 3 notes")],
  ["nothing ok and nothing bad is not Ready", () => {
    eq(verdict(L("unknown", "unknown")).label, "Not enough to say yet");
    eq(verdict(L("warn", "unknown")).level, "unknown");
    eq(verdict([]).level, "unknown");
  }],
  ["a lamp nobody defined counts as unknown, not ok", () => {
    const v = verdict([{ lamp: "ok" }, { lamp: "green" }, { lamp: undefined }]);
    eq([v.ok, v.unknown, v.label], [1, 2, "Ready, with 2 notes"]);
  }],
  ["bad beats everything else", () => eq(verdict(L("bad", "unknown", "unknown")).label, "Fix first")],
  ["junk input is survivable", () => { eq(verdict(null).level, "unknown"); eq(verdict(undefined).ok, 0); }],

  // ---- codes
  ["no active codes is ok", () => eq(codesCheck([{ code: "P0420", active: false }]).lamp, "ok")],
  ["codes not read is unknown", () => eq(codesCheck(undefined).lamp, "unknown")],
  ["a stored code is bad and named", () => {
    const c = codesCheck([{ code: "P1449", active: true, status: "stored" }]);
    eq(c.lamp, "bad"); ok(c.line.includes("P1449"), "names the code");
  }],
  ["a pending code alone is a warn", () => eq(codesCheck([{ code: "P0135", active: true, status: "pending" }]).lamp, "warn")],
  ["pending plus stored is bad", () => eq(codesCheck([
    { code: "A", active: true, status: "pending" }, { code: "B", active: true, status: "permanent" }]).lamp, "bad")],

  // ---- readiness
  ["no monitor data is unknown", () => { eq(readinessCheck(null).lamp, "unknown"); eq(readinessCheck({ monitors: [] }).lamp, "unknown"); }],
  ["all supported monitors complete is ok; unsupported ones are ignored", () =>
    eq(readinessCheck({ monitors: [{ supported: true, complete: true }, { supported: false, complete: false }] }).lamp, "ok")],
  ["an unrun monitor is a warn, not a stop", () =>
    eq(readinessCheck({ monitors: [{ supported: true, complete: false, name: "EVAP" }] }).lamp, "warn")],

  // ---- service due
  ["no schedule is unknown", () => { eq(serviceCheck(null).lamp, "unknown"); eq(serviceCheck(svc([])).lamp, "unknown"); }],
  ["nothing near is ok", () => eq(serviceCheck(svc([item({})])).lamp, "ok")],
  ["500 miles is the edge: 804 km due, 805 km not", () => {
    eq(serviceCheck(svc([item({ km_left: 804 })])).lamp, "warn");
    eq(serviceCheck(svc([item({ km_left: 805 })])).lamp, "ok");
  }],
  ["30 days is the edge: 30 due, 31 not", () => {
    eq(serviceCheck(svc([item({ days_left: 30 })])).lamp, "warn");
    eq(serviceCheck(svc([item({ days_left: 31 })])).lamp, "ok");
  }],
  ["an item with no interval at all is not due", () => eq(serviceCheck(svc([item({ km_left: null, days_left: null })])).lamp, "ok")],
  ["overdue oil is a warn", () => eq(serviceCheck(svc([item({ item: "Engine oil & filter", life: -5, km_left: -400 })])).lamp, "warn")],
  ["overdue brakes or tyres is bad", () => {
    eq(serviceCheck(svc([item({ item: "Brake fluid", life: 0, days_left: -3 })])).lamp, "bad");
    eq(serviceCheck(svc([item({ item: "Front brake pads", life: -1 })])).lamp, "bad");
    eq(serviceCheck(svc([item({ item: "Tires", life: -1 })])).lamp, "bad");
  }],
  ["a tyre ROTATION overdue is only a warn", () => eq(serviceCheck(svc([item({ item: "Tire rotation", life: -1 })])).lamp, "warn")],
  ["brakes merely due soon is a warn", () => eq(serviceCheck(svc([item({ item: "Brake fluid", days_left: 10 })])).lamp, "warn")],
  ["unrecorded items are never judged due: their dates are placeholders", () => {
    const s = svc([item({ item: "12 V battery", life: -50, days_left: -900 })]);
    eq(serviceCheck(s, ["12 v battery"]).lamp, "ok");
    eq(dueItems(s, ["12 V BATTERY"]), []);
    eq(serviceCheck(s, []).lamp, "warn");
  }],

  // ---- gaps
  ["gaps unread is unknown", () => eq(gapsCheck(undefined).lamp, "unknown")],
  ["no gaps is ok", () => eq(gapsCheck([]).lamp, "ok")],
  ["gaps are unknown, never ok, and named", () => {
    const g = gapsCheck([{ item: "Coolant" }, { item: "Cabin filter" }, { item: "Tires" }]);
    eq(g.lamp, "unknown"); ok(g.line.startsWith("3 items never recorded: Coolant, Cabin filter"), g.line);
  }],

  // ---- 12 V
  ["no connection or no volts is unknown", () => {
    eq(batteryCheck({}).lamp, "unknown");
    eq(batteryCheck({ volts: 12.6, connected: false }).lamp, "unknown");
  }],
  ["parked: 12.6 ok, 12.3 warn, 11.5 bad", () => {
    eq(batteryCheck({ volts: 12.6, rpm: 0, connected: true }).lamp, "ok");
    eq(batteryCheck({ volts: 12.3, rpm: 0, connected: true }).lamp, "warn");
    eq(batteryCheck({ volts: 11.5, rpm: 0, connected: true }).lamp, "bad");
  }],
  ["running judges the charging system: 14.1 ok, 12.9 warn, 12.4 bad", () => {
    eq(batteryCheck({ volts: 14.1, rpm: 900, connected: true }).lamp, "ok");
    eq(batteryCheck({ volts: 12.9, rpm: 900, connected: true }).lamp, "warn");
    eq(batteryCheck({ volts: 12.4, rpm: 900, connected: true }).lamp, "bad");
  }],

  // ---- IMA
  ["IMA verdicts map to lamps", () => {
    eq(imaCheck({ verdict: "Healthy", reasons: ["steady"] }).lamp, "ok");
    eq(imaCheck({ verdict: "Watch it", reasons: ["x"] }).lamp, "warn");
    eq(imaCheck({ verdict: "Failing", reasons: ["y"] }).lamp, "bad");
  }],
  ["no pack data, an error or nothing is unknown", () => {
    eq(imaCheck({ verdict: "Not enough data yet", reasons: [] }).lamp, "unknown");
    eq(imaCheck({ error: "no database", verdict: "Healthy" }).lamp, "unknown");
    eq(imaCheck(null).lamp, "unknown");
  }],

  // ---- tyres
  ["no tyre source is Not measured, unknown", () => {
    const c = tyresCheck(null);
    eq(c.lamp, "unknown"); ok(c.line.startsWith("Not measured"), c.line);
    eq(tyresCheck({}).lamp, "unknown");
    eq(tyresCheck({ tyres: [] }).lamp, "unknown");
    eq(tyresCheck({ tyres: [{ foo: 1 }] }).lamp, "unknown");
  }],
  ["tyres in psi, kPa or bar are judged by the lowest", () => {
    eq(tyresCheck({ tyres: [{ psi: 33 }, { psi: 34 }, { psi: 33 }, { psi: 32 }] }).lamp, "ok");
    eq(tyresCheck({ tyres: [{ psi: 33 }, { psi: 27 }] }).lamp, "warn");
    eq(tyresCheck({ tyres: [{ psi: 33 }, { psi: 20 }] }).lamp, "bad");
    eq(tyresCheck({ wheels: { fl: { kpa: 230 }, fr: { kpa: 230 } } }).lamp, "ok");
    eq(tyresCheck({ tyres: [{ bar: 1.2 }] }).lamp, "bad");
    eq(tyrePressures({ tyres: [30] }), [30]);
  }],
  ["simulated tyres say so", () => eq(tyresCheck({ source: "sim", tyres: [{ psi: 33 }] }).simulated, true)],

  // ---- fuel
  ["no fuel reading is unknown", () => eq(fuelCheck({ tripKm: 400 }).lamp, "unknown")],
  ["fuel but no trip distance is unknown, with the range shown", () => {
    const c = fuelCheck({ fuelPct: 50, tankL: 40, lphk: 5 });
    eq(c.lamp, "unknown"); ok(c.line.includes("Enter the trip distance"), c.line);
    eq(Math.round(c.range), 400);
  }],
  ["range 400 km: 300 km trip ok, 350 tight, 500 fill up", () => {
    const f = { fuelPct: 50, tankL: 40, lphk: 5 };
    eq(fuelCheck({ ...f, tripKm: 300 }).lamp, "ok");
    eq(fuelCheck({ ...f, tripKm: 350 }).lamp, "warn");
    eq(fuelCheck({ ...f, tripKm: 500 }).lamp, "warn");
    ok(fuelCheck({ ...f, tripKm: 500 }).line.includes("fill up"), "says fill up");
  }],
  ["no tank size or no economy history is unknown, not ok", () => {
    eq(fuelCheck({ tripKm: 300, fuelPct: 60, lphk: 5 }).lamp, "unknown");
    eq(fuelCheck({ tripKm: 300, fuelPct: 60, tankL: 40 }).lamp, "unknown");
    eq(rangeKm({ fuelPct: 0, tankL: 40, lphk: 5 }), null);
  }],

  // ---- the walk-round
  ["nothing ticked is unknown", () => eq(manualCheck({}).lamp, "unknown")],
  ["some ticked is still unknown", () => eq(manualCheck({ oil: true }).lamp, "unknown")],
  ["only a literal true counts as ticked", () => eq(manualCheck(Object.fromEntries(CHECKLIST.map((c) => [c.id, "yes"]))).lamp, "unknown")],
  ["all ticked is ok", () => eq(manualCheck(Object.fromEntries(CHECKLIST.map((c) => [c.id, true]))).lamp, "ok")],

  // ---- the whole thing
  ["a car that has told us nothing is not Ready", () => {
    const v = verdict(buildChecks({}));
    eq(v.level, "unknown");
  }],
  ["everything good and ticked is Ready", () => {
    const ticks = Object.fromEntries(CHECKLIST.map((c) => [c.id, true]));
    const checks = buildChecks({
      faults: [], readiness: { monitors: [{ supported: true, complete: true }] },
      service: svc([item({})]), gaps: [],
      battery12: { volts: 12.7, rpm: 0, connected: true },
      ima: { verdict: "Healthy", reasons: [] },
      tpms: { tyres: [{ psi: 33 }, { psi: 33 }] },
      fuel: { tripKm: 300, fuelPct: 50, tankL: 40, lphk: 5 }, ticks });
    eq(checks.map((c) => c.lamp), Array(9).fill("ok"));
    eq(verdict(checks).label, "Ready");
  }],
  ["the same car with no tyre source and an unticked list is Ready, with 2 notes", () => {
    const checks = buildChecks({
      faults: [], readiness: { monitors: [{ supported: true, complete: true }] },
      service: svc([item({})]), gaps: [],
      battery12: { volts: 12.7, rpm: 0, connected: true },
      ima: { verdict: "Healthy", reasons: [] }, tpms: null,
      fuel: { tripKm: 300, fuelPct: 50, tankL: 40, lphk: 5 }, ticks: {} });
    eq(verdict(checks).label, "Ready, with 2 notes");
  }],
  ["a standing stored code makes the trip Fix first however good the rest is", () => {
    const checks = buildChecks({ faults: [{ code: "P1449", active: true, status: "stored" }],
      readiness: { monitors: [{ supported: true, complete: true }] } });
    eq(verdict(checks).label, "Fix first");
  }],
  ["a gap never turns a due check ok: unrecorded + due-looking = still unknown row", () => {
    const checks = buildChecks({ service: svc([item({ item: "Coolant", life: -9 })]),
      gaps: [{ item: "Coolant" }] });
    eq(checks.find((c) => c.id === "service").lamp, "ok");
    eq(checks.find((c) => c.id === "gaps").lamp, "unknown");
  }],

  // ---- the stepper
  ["the stepper moves 25 at a time in the unit on screen", () => {
    const mi = 1.609344;
    eq(Math.round(stepTrip(null, +1, mi) / mi), 25);
    eq(Math.round(stepTrip(25 * mi, +1, mi) / mi), 50);
    eq(Math.round(stepTrip(50 * mi, -1, mi) / mi), 25);
  }],
  ["the stepper clears at zero and never goes negative", () => {
    eq(stepTrip(25 * 1.609344, -1), null);
    eq(stepTrip(null, -1), null);
  }],
  ["the stepper works in km and is capped", () => {
    eq(stepTrip(100, +1, 1), 125);
    eq(stepTrip(7990, +1, 1), 8000);
  }],
];
