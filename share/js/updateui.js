// Updates: the Settings sheet row, and the calm note on Home.
//
// lib/update.py decides everything -- when an update may run, whether it is
// safe, whether it came up -- and this only shows it and carries three taps
// back (Check now, Update when parked, Roll back). The server refuses every
// one of them unless the car is parked, whatever this screen believes; the
// buttons are greyed WHILE DRIVING with the reason beside them, never hidden,
// the rule every write screen keeps (store.lockedAsMoving).
//
// updateView() / homeNote() are pure (test/js/update.test.js).

import { h, clear, toast, store, withToken } from "./core.js";

const SAY_NOT_NOW = {
  moving: "the car is moving",
  on: "the car is on",
};

// "3 min ago" from epoch seconds. null for never.
export function ago(t, now = Date.now() / 1000) {
  if (!t) return null;
  const s = Math.max(0, Math.round(now - t));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

// What the sheet shows for a /api/update document. `moving` is the app's own
// lock (store.lockedAsMoving), checked as well as the server's verdict.
export function updateView(pub, moving = false, now = Date.now() / 1000) {
  if (!pub || pub.demo || pub.present === false) {
    return { state: "off", tone: "", headline: "Updates are not available here",
             running: null, current: null, available: null, changes: [], actions: {},
             why: "", last: null, gate: [], auto: "Off" };
  }
  const cur = pub.current ? {
    short: pub.current.short || String(pub.current.sha || "").slice(0, 12),
    date: String(pub.current.date || "").slice(0, 10),
    subject: pub.current.subject || "",
  } : null;
  const av = pub.available || null;
  const reasons = (pub.can_apply && pub.can_apply.reasons) || [];
  const parked = !moving && !!(pub.can_apply && pub.can_apply.ok);
  const adopted = !!pub.adopted;
  const busy = !!pub.running;

  let state, tone, headline;
  if (busy) { state = "working"; tone = ""; headline = "Updating"; }
  else if (!adopted) { state = "unset"; tone = "warn"; headline = "Not set up on this tablet"; }
  else if (av) {
    state = "waiting";
    tone = "info";
    headline = av.behind === 1 ? "1 update waiting" : `${av.behind || ""} updates waiting`.trim();
  } else if (pub.check_error) { state = "error"; tone = "warn"; headline = "Could not check"; }
  else { state = "current"; tone = "ok"; headline = "Up to date"; }

  const can = adopted && !busy && parked;
  let why = "";
  if (!adopted) why = "Run omacar update adopt once on the tablet.";
  else if (busy) why = "";
  else if (moving) why = "Available when you stop.";
  else if (!parked) why = "Not now: " + reasons.join("; ") + ".";

  const last = pub.last ? {
    ok: !!pub.last.ok,
    kind: pub.last.kind,
    text: pub.last.reason || "",
    when: ago(pub.last.at, now),
    to: pub.last.to || null,
  } : null;
  const gate = ((pub.last && pub.last.gate && pub.last.gate.steps) || []).map((s) => ({
    name: s.name, ok: !!s.ok, secs: s.secs, note: s.ok ? "" : (s.note || ""),
  }));

  return {
    state, tone, headline,
    running: busy,
    current: cur,
    available: av ? { short: av.short, date: String(av.date || "").slice(0, 10), behind: av.behind || 0 } : null,
    changes: av ? (av.changes || []).slice(0, 8) : [],
    actions: {
      check: !busy,
      apply: can && !!av,
      rollback: can && !!pub.previous,
    },
    why,
    checked: ago(pub.checked_at, now),
    last, gate,
    auto: pub.auto ? `On, ${pub.window}` : "Off",
  };
}

// Home's calm line. null when there is nothing to say.
export function homeNote(pub) {
  if (!pub || pub.demo || !pub.adopted || !pub.available) return null;
  const n = pub.available.behind || 0;
  const what = (pub.available.changes || [])[0];
  return {
    text: n > 1 ? `An update is waiting · ${n} changes` : "An update is waiting",
    sub: what || "",
  };
}

async function getPub() {
  const r = await fetch(withToken("/api/update"), { cache: "no-store" });
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}

async function ask(action) {
  const r = await fetch(withToken("/api/update"), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.reasons ? body.reasons.join("; ") : (body.error || "HTTP " + r.status));
  return body;
}

// ---- the sheet -----------------------------------------------------------------

export function openUpdateSheet() {
  const host = document.getElementById("modal-host");
  if (!host) return;
  let alive = true;
  let timer = null;
  let pub = null;
  let err = null;

  const close = () => {
    alive = false;
    clearTimeout(timer);
    host.hidden = true; clear(host); host.onclick = null;
    document.removeEventListener("keydown", esc);
  };
  const esc = (e) => { if (e.key === "Escape") close(); };

  const body = h("div.up");
  const sheet = h("div.sheet.up-sheet", { role: "dialog", "aria-modal": "true", "aria-label": "Updates" },
    h("div.sheet-head", h("div.title", "Updates"),
      h("button.btn.right", { type: "button", onclick: close }, "Done")),
    body);

  const cap = (t) => h("div.up-cap", t);

  function draw() {
    const v = updateView(pub, store.lockedAsMoving);
    clear(body);
    if (err && !pub) {
      body.appendChild(h("div.up-panel", h("div.up-line", "Could not reach the updater: " + err)));
      return;
    }

    // The state, as a lamp and a headline; what runs, over what waits.
    body.appendChild(h("div.up-panel.up-hero",
      h("div.up-state", { "data-tone": v.tone },
        h("span.up-lamp"), h("span.up-head", v.headline)),
      v.current ? h("div.up-ver",
        cap("Running"),
        h("div.up-sha", v.current.short),
        h("div.up-sub", [v.current.date, v.current.subject].filter(Boolean).join(" · "))) : null,
      v.available ? h("div.up-ver",
        cap("Waiting"),
        h("div.up-sha", v.available.short),
        h("div.up-sub", v.available.date)) : null));

    if (v.changes.length) {
      body.appendChild(h("div.up-panel",
        cap("What changed"),
        h("ul.up-changes", v.changes.map((c) => h("li", c)))));
    }

    // The three taps. Greyed with the reason beside them, never hidden.
    const btn = (label, key, action, cls) =>
      h("button.up-btn" + (cls || ""), {
        type: "button", disabled: !v.actions[key],
        title: v.actions[key] ? "" : (v.why || ""),
        onclick: async () => {
          try {
            await ask(action);
            toast(action === "check" ? "Checking…" : action === "apply"
              ? "Updating. OmaCar restarts when it is ready." : "Rolling back…");
            refresh(true);
          } catch (e) { toast(String(e.message || e), "bad"); }
        },
      }, label);
    body.appendChild(h("div.up-acts",
      btn("Check now", "check", "check"),
      btn("Update when parked", "apply", "apply", ".up-key"),
      btn("Roll back", "rollback", "rollback")));
    const note = v.running ? "Working. This takes a minute or two; the screen restarts at the end."
      : (v.why || (v.checked ? `Checked ${v.checked}.` : "Not checked yet."));
    body.appendChild(h("div.up-why", note));

    if (v.last) {
      body.appendChild(h("div.up-panel",
        cap("Last result" + (v.last.when ? " · " + v.last.when : "")),
        h("div.up-state", { "data-tone": v.last.ok ? "ok" : "bad" },
          h("span.up-lamp"), h("span.up-head.up-small", v.last.text || (v.last.ok ? "ok" : "failed"))),
        v.gate.length ? h("div.up-gate", v.gate.map((s) =>
          h("div.up-step", { "data-ok": s.ok ? "1" : "0" },
            h("span.up-lamp"), h("span.up-sn", s.name),
            h("span.up-ss", s.ok ? (s.secs ? `${s.secs} s` : "ok") : s.note || "failed")))) : null));
    }
    body.appendChild(h("div.up-foot", `Automatic updates: ${v.auto}. Set in ~/.config/omacar/update.json.`));
  }

  async function refresh(soon) {
    clearTimeout(timer);
    try { pub = await getPub(); err = null; } catch (e) { err = String(e.message || e); }
    if (!alive) return;
    draw();
    const busy = pub && pub.running;
    timer = setTimeout(() => refresh(false), busy || soon ? 2000 : 15000);
  }

  clear(host); host.appendChild(sheet); host.hidden = false;
  host.onclick = (e) => { if (e.target === host) close(); };
  document.addEventListener("keydown", esc);
  body.appendChild(h("div.up-panel", h("div.up-line", "Reading the updater…")));
  refresh(false);
}

// Home: a quiet line in the footer when something is waiting. Checked once
// when Home appears and every ten minutes after; says nothing otherwise.
export function mountHomeNote(node) {
  let alive = true;
  let timer = null;
  async function look() {
    let pub = null;
    try { pub = await getPub(); } catch { /* no updater, no note */ }
    if (!alive) return;
    const n = homeNote(pub);
    node.hidden = !n;
    clear(node);
    if (n) {
      node.appendChild(h("span.up-lamp"));
      node.appendChild(h("span.up-note-t", n.text));
      if (n.sub) node.appendChild(h("span.up-note-s", n.sub));
    }
    timer = setTimeout(look, 10 * 60 * 1000);
  }
  node.addEventListener("click", () => openUpdateSheet());
  look();
  return () => { alive = false; clearTimeout(timer); };
}
