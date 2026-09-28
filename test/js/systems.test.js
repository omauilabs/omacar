import { eq } from "./assert.js";
import { groupSystems, headline, tyreState } from "../js/systems.js";

const M = [
  { id: "PGM-FI", name: "Engine (PGM-FI)", system: "Powertrain", codes: ["P0135"] },
  { id: "IMA", name: "IMA Motor & Battery", system: "Hybrid", codes: [] },
  { id: "VSA", name: "VSA / ABS Modulator", system: "Chassis", codes: [] },
  { id: "EPS", name: "Electric Power Steering", system: "Chassis", codes: [] },
  { id: "TPMS", name: "Deflation Warning", system: "Chassis", codes: ["C1B00"] },
  { id: "MICU", name: "Body Control (MICU)", system: "Body", codes: [] },
  { id: "IMOES", name: "Immobiliser / Keyless", system: "Security", codes: [] },
];
const F = [{ code: "P0135", severity: "normal", module: { id: "PGM-FI" } },
           { code: "C1B00", severity: "normal", module: { id: "TPMS" } }];
const by = (gs) => Object.fromEntries(gs.map((g) => [g.id, g.status]));

export default [
  ["each module lands in exactly one system", () => {
    const gs = groupSystems(M, F);
    eq(gs.flatMap((g) => g.modules.map((m) => m.id)).sort(), M.map((m) => m.id).sort());
  }],
  ["codes make a system need attention", () =>
    eq(by(groupSystems(M, F)), { engine: "warn", hybrid: "ok", brakes: "ok", steering: "ok",
                                 tyres: "warn", electrical: "ok", other: "ok" })],
  ["a severe fault is a fault, not a warning", () =>
    eq(by(groupSystems(M, [{ code: "P0135", severity: "high", module: { id: "PGM-FI" } }])).engine, "bad")],
  ["no scan means unknown everywhere, never normal", () =>
    eq(new Set(Object.values(by(groupSystems([], [])))), new Set(["unknown"]))],
  ["the headline counts what needs attention", () =>
    eq(headline(groupSystems(M, F)), { text: "2 systems need attention", tone: "warn" })],
  ["and says all normal only when there was a scan", () =>
    eq([headline(groupSystems(M.map((m) => ({ ...m, codes: [] })), [])).text,
        headline(groupSystems([], [])).text], ["All systems normal", "No full scan yet"])],
  ["the tyre callout follows the deflation warning", () =>
    eq([tyreState({ modules: M, active_faults: F }), tyreState({ modules: [] }),
        tyreState({ modules: M.filter((m) => m.id !== "TPMS"), active_faults: [] })],
       [{ text: "Check", tone: "warn" }, { text: "Not read", tone: "" }, { text: "Not found", tone: "" }])],
];
