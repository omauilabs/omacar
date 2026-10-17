// The voice readout in the top bar, and its settings sheet (lib/voice.py).
//
// Loaded on its own from app.html, like envprompt.js: main.js never builds
// this readout, so it needs no line there beyond Settings' row, which calls
// openVoiceSheet(). It polls /api/voice (a small file read) once a second while
// voice is on and every ten seconds while it is off.
//
// IT DRAWS; IT NEVER LISTENS. The microphone belongs to the service, which is a
// separate process. Nothing in the browser touches a microphone.

import { h, clear, store, toast, withToken } from "./core.js";
import { voiceView, needsToast, stepHour, clock12, speakNote, setupNote, titled } from "./voiceview.js";

let pub = null;
let lastSeq = -1;
let paint = () => {};

async function get() {
  const r = await fetch(withToken("/api/voice"), { cache: "no-store" });
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
}

async function save(patch) {
  const r = await fetch(withToken("/api/voice"), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
  });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || String(r.status));
  pub = body;
  paint();
  return body;
}

function mount() {
  const bar = document.getElementById("vbar");
  if (!bar) return;
  const state = h("span.tb-voice-s");
  const text = h("span.tb-voice-t");
  const node = h("button.tb-voice", { type: "button", hidden: true, onclick: () => openVoiceSheet() },
    h("span.tb-voice-dot", { "aria-hidden": "true" }), h("span.tb-voice-w", state, text));
  const place = () => {
    const right = bar.querySelector(".tb-right");
    if (!right) return false;
    right.insertBefore(node, right.firstChild);
    return true;
  };
  if (!place()) {
    const mo = new MutationObserver(() => { if (place()) mo.disconnect(); });
    mo.observe(bar, { childList: true, subtree: true });
  }
  paint = () => {
    const v = voiceView(pub);
    node.hidden = !v.show;
    node.dataset.tone = v.tone;
    state.textContent = v.state + (v.source ? ` · ${v.source}` : "");
    text.textContent = v.text;
    node.title = v.title || "Voice commands: tap for settings";
    if (needsToast(pub, lastSeq)) toast(pub.reply, "calm", 7000);
    if (pub && Number.isFinite(pub.seq)) lastSeq = Math.max(lastSeq, pub.seq);
  };
}

async function poll() {
  try { pub = await get(); } catch { pub = null; }
  paint();
  setTimeout(poll, pub && pub.enabled && !document.hidden ? 1000 : 10000);
}

// ---- the sheet: Settings -> Voice ------------------------------------------------

export function openVoiceSheet() {
  const host = document.getElementById("modal-host");
  if (!host) return;
  const close = () => { host.hidden = true; clear(host); host.onclick = null; document.removeEventListener("keydown", esc); };
  const esc = (e) => { if (e.key === "Escape") close(); };
  const rows = h("div.sheet-rows");

  const row = (label, note, value, onclick, on) =>
    h("button.sheet-row", { type: "button", onclick, disabled: !!store.lockedAsMoving,
                            "aria-pressed": on === undefined ? null : String(!!on) },
      h("span.sheet-l", h("span.sheet-lab", label), note ? h("span.sheet-note", note) : null),
      h("span.sheet-v" + (on ? ".on" : ""), value));
  const edit = (patch) => async () => {
    try { await save(patch); } catch (e) { toast("Could not change voice: " + (e.message || e), "bad"); }
    redraw();
  };

  const redraw = () => {
    clear(rows);
    const p = pub || { enabled: false, config: { quiet: {} }, phrases: [], can: {} };
    const c = p.config || { quiet: {} };
    const q = c.quiet || {};
    rows.appendChild(row("Voice commands",
      `Listens for “${titled(p.phrase_words || "hey jarvis")}” on the microphone. Nothing leaves the tablet.`,
      c.enabled ? "On" : "Off", edit({ enabled: !c.enabled }), c.enabled));
    const phrases = p.phrases || [];
    rows.appendChild(row("Wake phrase", "Your own model: drop it in ~/.config/omacar/voice/models/",
      titled(p.phrase_words || ""), () => {
        const next = phrases[(phrases.indexOf(c.phrase) + 1) % Math.max(1, phrases.length)];
        return edit({ phrase: next })();
      }));
    rows.appendChild(row("Quiet hours", "No listening and no speech, unless the car is on",
      q.on === false ? "Off" : `${clock12(q.start)} – ${clock12(q.end)}`,
      edit({ quiet: { on: q.on === false } }), q.on !== false));
    if (q.on !== false) {
      rows.appendChild(row("Quiet from", "Tap for an hour later", clock12(q.start),
        edit({ quiet: { start: stepHour(q.start, 1) } })));
      rows.appendChild(row("Quiet until", "Tap for an hour later", clock12(q.end),
        edit({ quiet: { end: stepHour(q.end, 1) } })));
    }
    rows.appendChild(row("Spoken replies", speakNote(p), c.speak === false ? "Off" : "On",
      edit({ speak: c.speak === false }), c.speak !== false));
    const miss = setupNote(p);
    if (miss) rows.appendChild(h("div.sheet-foot", miss));
    if (store.lockedAsMoving) rows.appendChild(h("div.sheet-foot", "Changing voice settings waits until you are parked."));
  };
  redraw();
  const sheet = h("div.sheet", { role: "dialog", "aria-modal": "true", "aria-label": "Voice" },
    h("div.sheet-head", h("div.title", "Voice"), h("button.btn.right", { type: "button", onclick: close }, "Done")),
    rows);
  clear(host); host.appendChild(sheet); host.hidden = false;
  host.onclick = (e) => { if (e.target === host) close(); };
  document.addEventListener("keydown", esc);
  get().then((p) => { pub = p; redraw(); paint(); }).catch(() => {});
}

mount();
poll();
