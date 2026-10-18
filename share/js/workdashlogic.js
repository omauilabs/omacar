// The Work dashboard's pure logic: what may be on screen, and how the relay's
// numbers read. No DOM, no clock, no fetch -- test/js/workdash.test.js holds it.
//
// THE RULE, mirrored from doc/work.md (Work by voice): by default Work is for
// a parked car -- while moving only a glance is shown, with no PR link and no
// diff. The owner's work.toml `while_moving = true` is their decision to see
// PRs and diffs on the road; the relay reports it in /health, and then the
// full dashboard stays up with one dismissible reading notice per drive.
// Anything unknown (no relay, no answer) is the cautious side: a glance.

// "full" | "glance" | "down": what the screen draws.
export function viewMode({ present, moving, whileMoving }) {
  if (!present) return "down";
  if (!moving) return "full";
  return whileMoving ? "full" : "glance";
}

// PR links and diffs may be drawn when the full dashboard is.
export const showsPr = (mode) => mode === "full";

// The notice the relay's own page shows: once per drive, only when a PR or
// diff is on screen while moving by the owner's choice.
export const READING_NOTICE =
  "Reading while driving: CA Vehicle Code §27602 restricts screens in the driver's view, "
  + "and NHTSA's guideline is glances under 2 seconds. Shown because you chose it in work.toml.";
export function needsReadingNotice({ moving, whileMoving, mode, anyPr, dismissed }) {
  return !!(moving && whileMoving && mode === "full" && anyPr && !dismissed);
}

// "12m", "1h 05m", "4h"; null (the relay never said when it began) is a dash,
// never "0m".
export function elapsed(s) {
  if (s === null || s === undefined || !Number.isFinite(s)) return "—";
  const m = Math.floor(Math.max(0, s) / 60);
  if (m < 1) return "under 1m";
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60), r = m % 60;
  return r ? `${h}h ${String(r).padStart(2, "0")}m` : `${h}h`;
}

// The one line a moving screen can afford: counts, then who is waiting.
export function glance(agg) {
  const c = (agg && agg.counts) || {};
  const parts = [];
  if (c.waiting) parts.push(`${c.waiting} waiting for you`);
  if (c.running) parts.push(`${c.running} running`);
  if (c.failed) parts.push(`${c.failed} failed`);
  if (c.done) parts.push(`${c.done} done`);
  return parts.length ? parts.join(" · ") : "No sessions";
}

// A `git diff --stat` into files and a total. Lines that are not a file row
// (the summary, blank lines) are not files. Binary rows have no counts.
export function parseStat(text) {
  const files = [];
  let total = "";
  for (const raw of String(text || "").split("\n")) {
    const line = raw.replace(/\s+$/, "");
    if (!line.trim()) continue;
    if (/^\s*\d+ files? changed/.test(line)) { total = line.trim(); continue; }
    const m = /^\s*(.+?)\s+\|\s+(?:(\d+)\s*([+-]*)|(Bin.*))\s*$/.exec(line);
    if (!m) continue;
    if (m[4]) { files.push({ name: m[1], n: null, add: 0, del: 0, bin: true }); continue; }
    const plus = (m[3].match(/\+/g) || []).length, minus = (m[3].match(/-/g) || []).length;
    files.push({ name: m[1], n: Number(m[2]), add: plus, del: minus, bin: false });
  }
  return { files, total };
}

// How wide each file's change bar is, 0..1, against the busiest file.
export function barShare(files) {
  const top = Math.max(1, ...files.map((f) => f.n || 0));
  return files.map((f) => (f.n || 0) / top);
}

// Files grouped by their folder (the relay gives a stat, not a patch, so the
// folder is the only structure there is to fold on). Top-level files go under
// "(top level)". Folders keep first-seen order.
export function groupByDir(files) {
  const by = new Map();
  for (const f of files) {
    const i = f.name.lastIndexOf("/");
    const dir = i > 0 ? f.name.slice(0, i) : "(top level)";
    if (!by.has(dir)) by.set(dir, []);
    by.get(dir).push(Object.assign({}, f, { base: i > 0 ? f.name.slice(i + 1) : f.name }));
  }
  return [...by].map(([dir, fs]) => ({
    dir, files: fs, n: fs.reduce((a, f) => a + (f.n || 0), 0),
  }));
}
