// The advisor. This is the reason to build this rather than buy a tablet.
//
// A five-thousand-dollar scan tool can tell you what other people did about a
// code on this model. It cannot read YOUR freeze frame, YOUR Mode 06 margins,
// YOUR fuel trims and YOUR year of economy at once and tell you what is going
// on with this particular car. That is a reasoning problem, and it is now a
// solved one — so it should be in a free tool rather than behind a subscription.
//
// It runs on the `claude` CLI the user already has: no API key, no account of
// ours, no per-scan fee, and the evidence never leaves the machine except as
// the question the user chose to ask.
//
// The failure mode of a language model on a diagnostic tool is a confident
// invention, so nothing here is taken on trust. Every finding must cite keys
// from the evidence bundle; the server drops the ones that cite anything else
// before this file ever sees them; and what survives is shown WITH its
// citations and its confidence, so the reader can check the working.

import { h, clear, store, api, toast, since, fullDate } from "../core.js";
import { valet } from "../valet.js";

// A recording handed over from the data lab. Stored in sessionStorage rather
// than the hash so a long span does not end up in a URL, and cleared as soon
// as it is used so a reload does not silently re-ask about an old drive.
function takeSpan() {
  try {
    const raw = sessionStorage.getItem("omacar.aiSpan");
    if (!raw) return null;
    sessionStorage.removeItem("omacar.aiSpan");
    const v = JSON.parse(raw);
    return Array.isArray(v) && v.length === 2 ? v : null;
  } catch { return null; }
}

const KINDS = [
  { id: "triage", label: "Diagnose the vehicle",
    blurb: "Everything at once: codes grouped by real cause, cheap and certain work first." },
  { id: "owner", label: "Explain it to the owner",
    blurb: "The same findings without the jargon — what to spend money on and what to leave." },
  { id: "predict", label: "What fails next",
    blurb: "Trends in the on-board tests, economy and service book, projected forward." },
];

export default function advisor(root, { arg }) {
  if (!store.aiOn) {
    root.appendChild(h("div.card",
      h("div.title", "The advisor needs the Claude CLI"),
      h("p.lede", { style: { marginTop: "8px" } },
        "OmaCar drives the `claude` command you already have, in headless mode. "
        + "There is no API key to buy and no subscription of ours — it uses yours, "
        + "and the car's data never leaves this machine."),
      h("p.muted", { style: { marginTop: "8px" } }, "Install it, then reload this page.")));
    return;
  }

  let job = null, timer = null, alive = true;

  root.appendChild(h("section.sect",
    h("div.head",
      h("div", h("div.eyebrow", "Intelligent diagnostics"),
        h("div.title", "Advisor")),
      h("span.pill.ai.right", "runs on your own Claude")),
    h("p.lede",
      "Reads this car's codes, freeze frames, Mode 06 results, readiness monitors, "
      + "fuel trims, service book and a year of driving together. Every claim it "
      + "makes has to cite the evidence it came from, and anything that cites "
      + "something not in the bundle is dropped before you see it.")));

  const actions = h("div.grid.g3");
  for (const k of KINDS) {
    actions.appendChild(h("button.card", {
      style: { cursor: "pointer", textAlign: "left" },
      onclick: () => run({ kind: k.id }),
    }, h("div.eyebrow", k.id === "triage" ? "Full diagnosis" : k.id === "owner" ? "Plain language" : "Forecast"),
      h("div", { style: { fontWeight: "600", margin: "4px 0 6px" } }, k.label),
      h("div.muted", k.blurb)));
  }
  root.appendChild(actions);

  // Free-form. The genuinely new thing: a scan tool you can ask a question.
  const input = h("input", { type: "text", placeholder:
    "Ask anything — “why did my economy drop in July?”, “will it pass a smog test?”, “is the catalyst about to go?”" });
  const askBtn = h("button.btn.ai", "Ask");
  const form = h("form.card", { onsubmit: (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q) return;
    run({ kind: "ask", question: q });
  } },
    h("div.eyebrow", "Ask the car"),
    h("div.row", { style: { marginTop: "10px" } }, input, askBtn));
  root.appendChild(form);

  // Symptom first. The case a code reader cannot help with at all: the car is
  // doing something wrong and the light is not on. Describe it and the advisor
  // works out what to record to settle it.
  const symptom = h("input", { type: "text", placeholder:
    "“It stalls when cold at junctions”, “hesitates at 40 mph under light throttle”, “rattles on start-up”" });
  const symForm = h("form.card", { onsubmit: (e) => {
    e.preventDefault();
    const q = symptom.value.trim();
    if (!q) return;
    run({ kind: "symptom", question: q });
  } },
    h("div.eyebrow", "Describe a symptom"),
    h("p.muted", { style: { marginTop: "4px" } },
      "No code needed. It will rule out what the data already rules out and "
      + "tell you exactly what to record on the next drive."),
    h("div.row", { style: { marginTop: "10px" } }, symptom,
      h("button.btn.ai", "Work it out")));
  root.appendChild(symForm);

  const out = h("div.sect");
  root.appendChild(out);

  const past = h("div.sect");
  root.appendChild(past);
  loadHistory();

  async function run(body) {
    clear(out);
    const started = Date.now();
    const status = h("div.thinking", h("span.spinner"),
      h("span", "Reading the evidence…"), h("span.muted.right", "0s"));
    out.appendChild(h("div.card.tint-ai", status));

    const tick = setInterval(() => {
      const s = Math.round((Date.now() - started) / 1000);
      status.lastChild.textContent = s + "s";
      status.children[1].textContent = s < 8 ? "Reading the evidence…"
        : s < 25 ? "Working through the codes and the data…"
        : s < 60 ? "Cross-checking against the on-board tests…"
        : "Still going — a full diagnosis takes a minute or so.";
    }, 500);

    try {
      const { id } = await api.aiStart(body);
      job = id;
      while (alive) {
        await sleep(1100);
        const st = await api.aiPoll(id);
        if (st.state === "done") {
          clearInterval(tick);
          clear(out);
          out.appendChild(render(st.result, body));
          loadHistory();
          return;
        }
        if (st.state === "error") {
          clearInterval(tick);
          clear(out);
          out.appendChild(h("div.card.tint-bad",
            h("div.title", "The advisor could not answer"),
            h("p.lede", { style: { marginTop: "6px" } }, st.error)));
          return;
        }
      }
    } catch (e) {
      clearInterval(tick);
      clear(out);
      out.appendChild(h("div.card.tint-bad",
        h("div.title", "The advisor is unavailable"),
        h("p.lede", { style: { marginTop: "6px" } }, String(e.message || e))));
    } finally {
      clearInterval(tick);
    }
  }

  async function loadHistory() {
    // Valet mode: the advisor's earlier answers are the owner's, not the valet's.
    if (valet.on) { clear(past); return; }
    try {
      const { records } = await api.aiHistory();
      clear(past);
      if (!records || !records.length) return;
      past.appendChild(h("div.eyebrow", "Earlier answers"));
      const box = h("div", { style: { display: "grid", gap: "6px" } });
      for (const r of records.slice(0, 8)) {
        box.appendChild(h("div.rowitem", { style: { cursor: "default" } },
          h("span.pill.ai", (r.payload && r.payload.kind) || "ai"),
          h("span.desc", (r.payload && r.payload.headline) || r.label),
          h("span.muted", since(Date.now() / 1000 - r.at))));
      }
      past.appendChild(box);
    } catch { /* history is a nicety; its absence is not an error worth showing */ }
  }

  // Deep links: #advisor/triage, #advisor/code:P0135 from the codes view, and
  // #advisor/recording from the data lab with the span left in sessionStorage.
  if (arg) {
    if (arg.startsWith("code:")) run({ kind: "code", code: arg.slice(5) });
    else if (arg === "recording") {
      const span = takeSpan();
      if (span) run({ kind: "recording", span });
      else toast("That recording is no longer selected — open it in the data lab first.", "bad");
    } else if (KINDS.some((k) => k.id === arg)) run({ kind: arg });
  }

  return () => { alive = false; clearInterval(timer); };
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

// ---------------------------------------------------------------- rendering
function render(res, asked) {
  const d = res.data || {};
  const out = h("div.sect");

  out.appendChild(h("div.card.tint-ai",
    h("div.row.wrapline",
      h("span.pill.ai", res.cached ? "AI · CACHED" : "AI ADVISOR"),
      h("span.muted", res.cached ? "unchanged evidence, so this is the answer from before"
        : `${res.took_s}s  ·  ${res.model}`),
      h("span.muted.right", fullDate(res.at))),
    h("div", { style: { marginTop: "10px", fontSize: "1.05rem", lineHeight: "1.5" } },
      d.headline || d.summary || d.answer || ""),
    d.verdict ? h("div.row", { style: { marginTop: "10px" } },
      h("span.pill." + (d.verdict === "urgent" ? "bad" : d.verdict === "attention" ? "warn" : "ok"),
        d.verdict)) : null,
    typeof d.confidence === "number" ? confidence(d.confidence) : null));

  // ---- findings ----
  for (const f of d.findings || []) {
    out.appendChild(h("div.finding." + (f.severity || "info"),
      h("div.row.wrapline",
        h("h3", f.title),
        h("span.pill" + (f.severity === "critical" ? ".bad" : f.severity === "warning" ? ".warn" : ""),
          f.severity || "info"),
        h("span.right"), confidence(f.confidence)),
      f.what ? h("div", f.what) : null,
      f.why ? h("div.why", h("span.eyebrow", "Because  "), f.why) : null,
      f.action ? h("div", { style: { marginTop: "2px" } },
        h("span.eyebrow", "Do  "), f.action,
        f.cost ? h("span.muted", `   ${f.cost}`) : null) : null,
      (f.related_codes || []).length
        ? h("div.row.wrapline", (f.related_codes).map((c) =>
            h("button.pill", { style: { cursor: "pointer", border: 0 },
              onclick: () => { location.hash = "#codes/" + c; } }, c)))
        : null,
      cites(f.evidence)));
  }

  // ---- predictions ----
  for (const p of d.predictions || []) {
    out.appendChild(h("div.finding",
      h("div.row.wrapline", h("h3", p.what),
        h("span.pill.info", p.when || ""), h("span.right"), confidence(p.confidence)),
      p.basis ? h("div.why", p.basis) : null,
      p.prevent ? h("div", h("span.eyebrow", "Avoid it  "), p.prevent) : null,
      cites(p.evidence)));
  }

  // ---- hypotheses, from a symptom ----
  if ((d.hypotheses || []).length) {
    const box = h("div", { style: { display: "grid", gap: "14px", marginTop: "10px" } });
    for (const hy of d.hypotheses) {
      box.appendChild(h("div",
        h("div.row.wrapline", h("span", { style: { fontWeight: "600" } }, hy.cause),
          h("span.muted.right", (hy.likelihood || 0) + "%")),
        h("div.meter.thin", { style: { margin: "6px 0" } },
          h("i", { style: { width: (hy.likelihood || 0) + "%",
                            background: (hy.likelihood || 0) >= 40 ? "var(--ai)" : "var(--edge-2)" } })),
        hy.why ? h("p.muted", hy.why) : null,
        hy.distinguishes ? h("p", { style: { fontSize: ".76rem", marginTop: "3px" } },
          h("span.eyebrow", "Tells it apart  "), hy.distinguishes) : null,
        cites(hy.evidence)));
    }
    out.appendChild(h("div.card", h("div.eyebrow", "What it could be"), box));
  }

  // ---- the recording plan ----
  // The part that closes the loop: the advisor names the channels, the lab
  // records them, and the recording comes back here to be read.
  if (d.record && (d.record.channels || []).length) {
    out.appendChild(h("div.card.tint-ai",
      h("div.eyebrow", "Record this"),
      h("div.row.wrapline", { style: { marginTop: "10px" } },
        d.record.channels.map((c) => h("span.pill.info", c))),
      d.record.conditions ? h("p.lede", { style: { marginTop: "10px" } },
        h("span.eyebrow", "Conditions  "), d.record.conditions) : null,
      d.record.minutes ? h("p.muted", { style: { marginTop: "4px" } },
        `About ${d.record.minutes} minutes is enough.`) : null,
      (d.record.looking_for || []).length
        ? h("ul", { style: { marginTop: "10px", display: "grid", gap: "5px" } },
            d.record.looking_for.map((x) => h("li.muted", "— " + x)))
        : null,
      h("div.row", { style: { marginTop: "12px" } },
        h("button.btn.ai", { onclick: () => { location.hash = "#data"; } },
          "Open the data lab"))));
  }

  if ((d.cheap_checks || []).length) {
    out.appendChild(h("div.card", h("div.eyebrow", "Free, and worth doing first"),
      h("ul", { style: { marginTop: "8px", display: "grid", gap: "5px" } },
        d.cheap_checks.map((c) => h("li.muted", "— " + c)))));
  }

  // ---- ranked causes, from a single-code question ----
  if ((d.ranked || []).length) {
    const box = h("div", { style: { display: "grid", gap: "12px", marginTop: "10px" } });
    for (const r of d.ranked) {
      box.appendChild(h("div",
        h("div.row", h("span", { style: { fontWeight: "600" } }, r.cause),
          h("span.muted.right", (r.likelihood || 0) + "%")),
        h("div.meter.thin", { style: { margin: "6px 0" } },
          h("i", { style: { width: (r.likelihood || 0) + "%",
                            background: (r.likelihood || 0) >= 40 ? "var(--ai)" : "var(--edge-2)" } })),
        r.why ? h("p.muted", r.why) : null,
        r.test ? h("p", { style: { fontSize: ".76rem" } }, h("span.eyebrow", "Test  "), r.test) : null,
        r.cost ? h("p.muted", r.cost) : null));
    }
    out.appendChild(h("div.card", h("div.eyebrow", "Ranked for this car"), box));
  }

  if (d.reading) out.appendChild(h("div.card", h("div.eyebrow", "What this car's numbers say"),
    h("p.lede", { style: { marginTop: "6px" } }, d.reading)));

  // ---- the plan ----
  if ((d.order || d.next || []).length) {
    const steps = d.order || d.next;
    out.appendChild(h("div.card",
      h("div.eyebrow", "Do it in this order"),
      h("ol", { style: { marginTop: "10px", display: "grid", gap: "8px" } },
        steps.map((s, i) => h("li", { style: { display: "flex", gap: "10px" } },
          h("span", { style: { color: "var(--ai)", fontWeight: "700", flex: "none" } }, String(i + 1)),
          h("span", s))))));
  }

  // ---- owner-facing money ----
  for (const [key, title, tone] of [["spend_now", "Spend on this now", "warn"],
                                    ["spend_later", "Plan for later", ""],
                                    ["ignore", "Leave alone", "ok"]]) {
    const items = d[key];
    if (!items || !items.length) continue;
    out.appendChild(h("div.card" + (tone ? ".tint-" + tone : ""),
      h("div.eyebrow", title),
      h("div", { style: { marginTop: "10px", display: "grid", gap: "10px" } },
        items.map((it) => h("div",
          h("div.row", h("span", { style: { fontWeight: "600" } }, it.item),
            h("span.muted.right", it.cost || it.when || "")),
          h("p.muted", it.why || ""))))));
  }

  if (d.answer && (d.caveats || d.followups)) {
    if ((d.caveats || []).length) {
      out.appendChild(h("div.card.flat", h("div.eyebrow", "What this cannot settle"),
        h("ul", { style: { marginTop: "8px", display: "grid", gap: "5px" } },
          d.caveats.map((c) => h("li.muted", "— " + c)))));
    }
    if ((d.followups || []).length) {
      out.appendChild(h("div.card.flat", h("div.eyebrow", "Worth asking next"),
        h("div.row.wrapline", { style: { marginTop: "8px" } },
          d.followups.map((q) => h("button.btn.sm", { onclick: () => {
            const el = document.querySelector('input[type="text"]');
            if (el) { el.value = q; el.focus(); }
          } }, q)))));
    }
  }

  for (const [key, title, tone] of [["safety", "Safety", "bad"], ["emissions", "Emissions", "warn"]]) {
    if (!d[key]) continue;
    out.appendChild(h("div.card.tint-" + tone, h("div.eyebrow", title),
      h("p.lede", { style: { marginTop: "6px" } }, d[key])));
  }
  if (d.safe_to_drive) {
    out.appendChild(h("div.card.tint-" + (d.safe_to_drive === "no" ? "bad" : d.safe_to_drive === "yes" ? "ok" : "warn"),
      h("div.eyebrow", "Safe to drive"),
      h("div", { style: { marginTop: "6px", fontWeight: "600" } }, d.safe_to_drive),
      d.safe_note ? h("p.lede", d.safe_note) : null));
  }

  if ((d.questions || []).length) {
    out.appendChild(h("div.card.flat", h("div.eyebrow", "It would ask you"),
      h("ul", { style: { marginTop: "8px", display: "grid", gap: "5px" } },
        d.questions.map((q) => h("li.muted", "— " + q)))));
  }
  if ((d.pitfalls || []).length) {
    out.appendChild(h("div.card.flat", h("div.eyebrow", "Common mistakes on this one"),
      h("ul", { style: { marginTop: "8px", display: "grid", gap: "5px" } },
        d.pitfalls.map((q) => h("li.muted", "— " + q)))));
  }

  // The honesty line. If anything was dropped for citing evidence that does
  // not exist, say so — quietly discarding it would leave the impression the
  // model never wandered.
  if ((res.dropped || []).length) {
    out.appendChild(h("div.card.flat",
      h("div.eyebrow", "Dropped as unsupported"),
      h("p.muted", { style: { marginTop: "6px" } },
        "These claims cited evidence that is not in this car's data, so they were "
        + "removed before the answer was shown:"),
      h("ul", { style: { marginTop: "6px", display: "grid", gap: "4px" } },
        res.dropped.map((x) => h("li.muted", "— " + x)))));
  }

  out.appendChild(h("p.muted", { style: { marginTop: "4px" } },
    "The advisor reasons over the evidence above. It is a very good second "
    + "opinion and it is not a substitute for measuring something."));
  return out;
}

function confidence(v) {
  if (typeof v !== "number") return null;
  return h("div.conf",
    h("span", "confidence"),
    h("div.meter", h("i", { style: { width: Math.max(0, Math.min(100, v)) + "%",
      background: v >= 75 ? "var(--ok)" : v >= 45 ? "var(--warn)" : "var(--bad)" } })),
    h("span", v + "%"));
}

function cites(list) {
  if (!list || !list.length) return null;
  return h("div.cites", list.map((c) => h("span.cite", String(c))));
}
