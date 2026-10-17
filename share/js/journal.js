// The trip journal's view logic (lib/journal.py makes the entries; this groups
// and filters them). Pure: no DOM, no clock of its own. test/js/journal.test.js.

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const midnight = (secs) => {
  const d = new Date(secs * 1000);
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime() / 1000;
};

export function dayKey(t) {
  const d = new Date(t * 1000);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function dayLabel(t, now) {
  const gap = Math.round((midnight(now) - midnight(t)) / 86400);
  if (gap === 0) return "Today";
  if (gap === 1) return "Yesterday";
  const d = new Date(t * 1000);
  return `${DAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

// The kinds a badge can have, in the order their chips appear, with the chip's
// word. A chip exists only when some drive has that badge: a filter that can
// only ever answer "none" is clutter on a touch screen.
export const KIND_CHIPS = [
  ["clips", "Clips"], ["radar", "Radar"], ["air", "Stale air"],
  ["fridge", "Fridge"], ["coach", "Coach"], ["fuel", "Fill-ups"],
];

export function chipsFor(entries) {
  const count = (pred) => entries.filter(pred).length;
  const chips = [{ id: "all", label: "All", n: entries.length }];
  for (const [kind, label] of KIND_CHIPS) {
    const n = count((e) => (e.badges || []).some((b) => b.kind === kind));
    if (n) chips.push({ id: kind, label, n });
  }
  const noted = count((e) => !!e.note);
  if (noted) chips.push({ id: "noted", label: "Noted", n: noted });
  return chips;
}

export function filterEntries(entries, chip) {
  if (!chip || chip === "all") return entries;
  if (chip === "noted") return entries.filter((e) => !!e.note);
  return entries.filter((e) => (e.badges || []).some((b) => b.kind === chip));
}

export function groupDays(entries, now) {
  const out = [];
  for (const e of entries) {
    const key = dayKey(e.t0);
    let g = out[out.length - 1];
    if (!g || g.key !== key) {
      g = { key, label: dayLabel(e.t0, now), entries: [], km: 0 };
      out.push(g);
    }
    g.entries.push(e);
    g.km += e.km || 0;
  }
  return out;
}

export function dayTotal(g, imperial) {
  const n = g.entries.length;
  const d = g.km * (imperial ? 0.621371 : 1);
  const dist = g.km > 0 ? ` · ${d < 10 ? d.toFixed(1) : Math.round(d)} ${imperial ? "mi" : "km"}` : "";
  return `${n} ${n === 1 ? "drive" : "drives"}${dist}`;
}

// Where the journal came from, said on screen always. The simulator is never
// mistaken for the car.
export function sourceLamp(pub) {
  if (!pub || !pub.present) return { text: "No drives yet", tone: "" };
  return pub.simulated ? { text: "Simulated", tone: "warn" } : { text: "Recorded", tone: "ok" };
}

// Typing is parked-only. The reason is part of the answer, so the screen can
// say why a thing is grey instead of just being grey.
export function canEdit(locked) {
  return locked ? { ok: false, why: "Parked only" } : { ok: true, why: "" };
}

export const QUICK_NOTES = ["Commute", "Errand", "School run", "Road trip", "Rain", "Heavy traffic"];

// Adding a quick note to what is already written, without doubling it.
export function addQuick(note, word) {
  const have = String(note || "").split(/\s*·\s*/).filter(Boolean);
  if (have.includes(word)) return have.join(" · ");
  return [...have, word].join(" · ");
}

export function clock(t) {
  const d = new Date(t * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

// "07:42 – 08:20", or just the start while a drive is still open.
export function span(e) {
  return e.t1 && !e.open ? `${clock(e.t0)} – ${clock(e.t1)}` : `${clock(e.t0)} – now`;
}
