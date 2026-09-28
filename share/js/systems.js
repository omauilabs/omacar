// Which scan modules make up each system the car screens name, and how each is
// doing. Built from the snapshot's `modules` (what the last full scan found,
// with the codes each one set) and `active_faults`. Nothing here asks the car
// anything, and nothing here says "normal" about a system nobody scanned.

export const SYSTEMS = [
  { id: "engine",     label: "Engine",        sub: "Engine control system",
    match: (m) => m.system === "Powertrain" },
  { id: "hybrid",     label: "Hybrid system", sub: "IMA battery and motor",
    match: (m) => m.system === "Hybrid" },
  { id: "brakes",     label: "Brakes",        sub: "ABS / VSA",
    match: (m) => /^(VSA|ABS)$/i.test(m.id || "") || /\b(abs|vsa|brake)/i.test(m.name || "") },
  { id: "steering",   label: "Steering",      sub: "Electric power steering",
    match: (m) => m.id === "EPS" },
  { id: "tyres",      label: "Tyres",         sub: "Deflation warning",
    match: (m) => m.id === "TPMS" },
  { id: "electrical", label: "Electrical",    sub: "12V system and body",
    match: (m) => m.system === "Body" },
];

const RANK = { unknown: 0, ok: 1, warn: 2, bad: 3 };
const SEVERE = new Set(["high", "critical", "severe"]);

function statusOf(mods, faults) {
  if (!mods.length) return "unknown";
  const ids = new Set(mods.map((m) => m.id));
  const codes = new Set(mods.flatMap((m) => m.codes || []));
  const mine = faults.filter((f) => (f.module && ids.has(f.module.id)) || codes.has(f.code));
  if (mine.some((f) => SEVERE.has(String(f.severity || "").toLowerCase()))) return "bad";
  return mine.length || codes.size ? "warn" : "ok";
}

export function groupSystems(modules, faults) {
  const mods = Array.isArray(modules) ? modules : [];
  const act = Array.isArray(faults) ? faults : [];
  const taken = new Set();
  const out = SYSTEMS.map((s) => {
    const mine = mods.filter((m) => !taken.has(m.id) && s.match(m));
    for (const m of mine) taken.add(m.id);
    return { id: s.id, label: s.label, sub: s.sub, modules: mine,
             codes: mine.flatMap((m) => m.codes || []), status: statusOf(mine, act) };
  });
  const rest = mods.filter((m) => !taken.has(m.id));
  out.push({ id: "other", label: "All other systems",
             sub: rest.length ? `${rest.length} more module${rest.length === 1 ? "" : "s"}` : "None found",
             modules: rest, codes: rest.flatMap((m) => m.codes || []), status: statusOf(rest, act) });
  return out;
}

export function worstOf(groups) {
  return groups.reduce((w, g) => (RANK[g.status] > RANK[w] ? g.status : w), "unknown");
}

export function headline(groups) {
  if (!groups.some((g) => g.modules.length)) return { text: "No full scan yet", tone: "" };
  const need = groups.filter((g) => g.status === "warn" || g.status === "bad");
  if (!need.length) return { text: "All systems normal", tone: "ok" };
  const tone = need.some((g) => g.status === "bad") ? "bad" : "warn";
  return { text: need.length === 1 ? `${need[0].label} needs attention`
                                   : `${need.length} systems need attention`, tone };
}

// The CR-Z has a deflation warning and no per-wheel sensors, so Home's tyre
// callout is one callout with the system's state. See the foundation design,
// "Home", for why the mockups' four temperatures are not drawn.
export function tyreState(car) {
  const mods = (car && car.modules) || [];
  if (!mods.length) return { text: "Not read", tone: "" };
  const g = groupSystems(mods, car.active_faults).find((x) => x.id === "tyres");
  if (!g.modules.length) return { text: "Not found", tone: "" };
  return g.status === "ok" ? { text: "OK", tone: "ok" } : { text: "Check", tone: g.status };
}
