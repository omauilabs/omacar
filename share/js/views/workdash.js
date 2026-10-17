// Agent → Sessions: the Work dashboard.
//
// Every card is a session the Work relay reports (lib/workclient.py), nothing
// else. With the relay not running the screen says so and shows no sessions.
// What may be on screen while the car moves is decided in workdashlogic.js
// (parked-only by default; the owner's `while_moving` lifts it).

import { h, icon, store, withToken } from "../core.js";
import { ICONS } from "../icons.js";
import {
  viewMode, showsPr, needsReadingNotice, READING_NOTICE, elapsed, glance,
  parseStat, barShare, groupByDir,
} from "../workdashlogic.js";

const NOT_RUNNING = "Work relay not running — omacar work serve";
const COUNTS = [["waiting", "Waiting for you"], ["running", "Running"], ["failed", "Failed"], ["done", "Done"]];

async function getJson(url) {
  const r = await fetch(withToken(url), { cache: "no-store" });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || String(r.status));
  return body;
}

export default function workDashView(root) {
  const lamp = h("span.wd-lamp", { "aria-hidden": "true" });
  const srcT = h("span");
  const src = h("span.wd-src", lamp, srcT);
  const readouts = COUNTS.map(([k, cap]) => {
    const v = h("span.wd-n.display-num", "0");
    return { k, v, node: h("div.wd-count", { data: { k } }, h("span.wd-cap", cap), v) };
  });
  const head = h("div.wd-head", h("h1.wd-title", icon(ICONS.agent, 22), "Work sessions"), src,
    h("div.wd-counts", ...readouts.map((r) => r.node)));
  const notice = h("div.wd-notice", { role: "note", hidden: true });
  const body = h("div.wd-body");
  const node = h("div.wd", head, notice, body);
  root.appendChild(node);

  let agg = null, dismissed = false, wasMoving = false, openDiff = null, diff = null, gone = false, timer = null;
  const diffFor = new Map();

  function mode() {
    const a = agg || {};
    return viewMode({ present: !!a.present, moving: !!store.lockedAsMoving, whileMoving: !!a.while_moving });
  }

  function card(s, m) {
    const pr = showsPr(m) ? s.pr : null;
    const meta = [
      h("span.wd-meta", h("span.wd-cap", "Elapsed"), h("span.wd-val", elapsed(s.elapsed_s))),
    ];
    const kids = [
      h("div.wd-ctop", h("span.wd-state", h("span.wd-slamp"), s.label),
        s.repo ? h("span.wd-repo", s.repo + (s.branch ? " · " + s.branch : "")) : null),
      h("h2.wd-proj", s.project),
      h("p.wd-line", s.question && s.group === "waiting" ? s.question : (s.milestone || "No update yet")),
      h("div.wd-metas", ...meta),
    ];
    if (pr) {
      const gate = pr.gate ? h("span.wd-gate", { data: { gate: pr.gate } },
        pr.gate === "passed" ? "Pre-push gate passed" : "Pre-push gate failing") : null;
      kids.push(h("div.wd-pr",
        pr.url ? h("a.wd-prlink", { href: pr.url, target: "_blank", rel: "noopener" }, `Draft PR #${pr.number}`)
               : h("span.wd-prlink", `Draft PR #${pr.number}`),
        gate));
    }
    if (showsPr(m) && s.hasDiff) {
      kids.push(h("button.wd-btn", { type: "button", onclick: () => show(s.id) }, "Show diff"));
    }
    return h("article.wd-card.card", { data: { group: s.group, state: s.state } }, ...kids);
  }

  function glanceView() {
    const w = agg.waiting;
    return h("div.wd-glance",
      w ? h("div.wd-wait.card", h("span.wd-cap", "Waiting for you"), h("strong", w.project),
            w.question ? h("span", w.question) : null)
        : h("div.wd-wait.card.calm", h("span.wd-cap", glance(agg)), h("strong", "Nothing is waiting for you")),
      h("p.wd-note", "Pull requests and diffs wait until you are parked."));
  }

  function downView() {
    return h("div.wd-down.card",
      icon(ICONS.agent, 40),
      h("h2", NOT_RUNNING),
      h("p", (agg && agg.detail) ? `Reason: ${agg.detail}.` : "Starting it brings your sessions here."),
      h("p.wd-note", "Everything else in OmaCar works without it."));
  }

  function diffView(s) {
    const d = diff;
    const wrap = h("div.wd-diff");
    wrap.appendChild(h("button.wd-back", { type: "button", onclick: () => { openDiff = null; paint(); } }, "‹ Back to sessions"));
    wrap.appendChild(h("h2.wd-proj", s ? s.project : "Diff"));
    if (!d) { wrap.appendChild(h("p.wd-line", "Loading…")); return wrap; }
    if (d.error) { wrap.appendChild(h("p.wd-line", d.error)); return wrap; }
    if (d.simulated) wrap.appendChild(h("span.wd-simtag", "Simulated"));
    if (d.summary) wrap.appendChild(h("p.wd-sum", d.summary));
    else wrap.appendChild(h("p.wd-note", "No spoken summary yet. Say “read me the diff” to the car."));
    const st = parseStat(d.stat);
    wrap.appendChild(h("p.wd-total", st.total || `${st.files.length} file${st.files.length === 1 ? "" : "s"} changed`));
    const share = barShare(st.files);
    const idx = new Map(st.files.map((f, i) => [f.name, share[i]]));
    for (const g of groupByDir(st.files)) {
      wrap.appendChild(h("details.wd-dir", { open: true },
        h("summary", h("span.wd-dirname", g.dir), h("span.wd-dirn", `${g.files.length} file${g.files.length === 1 ? "" : "s"} · ${g.n} lines`)),
        ...g.files.map((f) => h("div.wd-file",
          h("span.wd-fname", f.base),
          h("span.wd-bar", h("i", { style: { width: `${Math.round((idx.get(f.name) || 0) * 100)}%` } })),
          h("span.wd-fn", f.bin ? "binary" : `${f.n} line${f.n === 1 ? "" : "s"}`)))));
    }
    return wrap;
  }

  async function show(id) {
    openDiff = id; diff = diffFor.get(id) || null; paint();
    if (diff) return;
    try { diff = await getJson(`/api/workdash/diff?id=${encodeURIComponent(id)}`); }
    catch (e) { diff = { error: String(e.message || e) }; }
    if (openDiff === id && !gone) { diffFor.set(id, diff); paint(); }
  }

  function paint() {
    const a = agg || { counts: {}, sessions: [] };
    const m = mode();
    const moving = !!store.lockedAsMoving;
    if (moving && !wasMoving) dismissed = false;      // a new drive earns the notice again
    wasMoving = moving;
    node.dataset.mode = m;
    node.dataset.source = a.present ? a.source : "none";
    src.dataset.tone = !a.present ? "bad" : a.simulated ? "warn" : "ok";
    srcT.textContent = !a.present ? "Relay not running" : a.simulated ? "Simulated" : "Work relay · live";
    for (const r of readouts) r.v.textContent = a.present ? String((a.counts || {})[r.k] || 0) : "—";
    const anyPr = (a.sessions || []).some((s) => s.pr);
    const showNotice = needsReadingNotice({ moving, whileMoving: !!a.while_moving, mode: m, anyPr, dismissed });
    notice.hidden = !showNotice;
    if (showNotice) {
      notice.replaceChildren(h("span", READING_NOTICE),
        h("button", { type: "button", "aria-label": "Dismiss", onclick: () => { dismissed = true; paint(); } }, "×"));
    }
    if (m !== "full") openDiff = null;                 // a diff never stays up under a glance
    let kid;
    if (m === "down") kid = downView();
    else if (m === "glance") kid = glanceView();
    else if (openDiff) kid = diffView(a.sessions.find((s) => s.id === openDiff));
    else if (!a.sessions.length) kid = h("div.wd-down.card", h("h2", "No sessions yet"),
      h("p", "Ask the car to start one: “omacar: …”."));
    else kid = h("div.wd-grid", ...a.sessions.map((s) => card(s, m)));
    body.replaceChildren(kid);
  }

  const tick = async () => {
    try { agg = await getJson("/api/workdash"); }
    catch { agg = { present: false, detail: "the app server did not answer" }; }
    if (!gone) { paint(); timer = setTimeout(tick, 3000); }
  };
  const off = store.on ? store.on("live", () => { if (agg) paint(); }) : null;
  tick();
  return () => { gone = true; clearTimeout(timer); if (typeof off === "function") off(); };
}
