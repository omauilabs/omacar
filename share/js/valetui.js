// Valet mode's screens: the top-bar lamp, the PIN pad, the first-time set-up,
// the report card, and the one tap that turns it on.
//
// Everything that decides anything is in valet.js (rules) and lib/valet.py
// (the PIN, the parked check, the lockout). This file asks the server and
// shows what it said; it never concludes a PIN is right.

import { h, clear, icon, store, U, toast, withToken } from "./core.js";
import { ICONS } from "./icons.js";
import { valet, padKey, cooldownText, reportView, lockReason } from "./valet.js";

// ---- the server ------------------------------------------------------------

export async function refreshValet() {
  try {
    const r = await fetch(withToken("/api/valet"), { cache: "no-store" });
    if (!r.ok) return false;
    return valet.apply(await r.json());
  } catch { return false; }
}

// POST, keeping the body of a refusal (tries_left, retry_in, needs_setup),
// which core.js's req() throws away.
async function ask(body) {
  let r, d = {};
  try {
    r = await fetch(withToken("/api/valet"), {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    d = await r.json();
  } catch (e) { return { ok: false, body: { error: "OmaCar cannot reach its own server." } }; }
  if (r.ok) valet.apply(d);
  return { ok: r.ok, status: r.status, body: d };
}

// ---- the sheet host ----------------------------------------------------------

function sheetHost(build) {
  const host = document.getElementById("modal-host");
  let box = null;
  const close = () => {
    document.removeEventListener("keydown", esc);
    // Something else has taken the host since (the report opens while the PIN
    // pad is still closing): leave that alone.
    if (box && !host.contains(box)) return;
    host.hidden = true; clear(host); host.onclick = null;
  };
  const esc = (e) => { if (e.key === "Escape") close(); };
  clear(host);
  box = build(close);
  host.appendChild(box);
  host.hidden = false;
  host.onclick = (e) => { if (e.target === host) close(); };
  document.addEventListener("keydown", esc);
  return close;
}

// ---- the PIN pad ---------------------------------------------------------------

// A keypad that submits on the fourth digit. `submit(pin)` resolves to
// { ok, error, retryIn } and the pad shakes nothing: it says what happened.
export function openPad({ title, note, submit, cancelLabel = "Cancel" }) {
  return sheetHost((close) => {
    let digits = "", busy = false, wait = 0, timer = null;
    const dots = [0, 1, 2, 3].map(() => h("span.vp-dot"));
    const msg = h("p.vp-msg", { role: "status" });
    const keys = [];
    const paint = () => {
      dots.forEach((d, i) => d.classList.toggle("on", i < digits.length));
      for (const k of keys) k.disabled = busy || wait > 0;
      if (wait > 0) msg.textContent = `Too many tries. Try again in ${cooldownText(wait)}.`;
    };
    const press = async (k) => {
      if (busy || wait > 0) return;
      digits = padKey(digits, k);
      paint();
      if (digits.length < 4) return;
      busy = true; paint();
      const r = await submit(digits);
      busy = false;
      if (r.ok) { if (timer) clearInterval(timer); close(); return; }
      digits = "";
      msg.textContent = r.error || "That did not work.";
      if (r.retryIn) {
        wait = r.retryIn;
        timer = setInterval(() => {
          wait -= 1;
          if (wait <= 0) { clearInterval(timer); wait = 0; msg.textContent = ""; }
          paint();
        }, 1000);
      }
      paint();
    };
    const key = (label, k, extra) => {
      const b = h("button.vp-key" + (extra || ""), { type: "button", onclick: () => press(k) }, label);
      keys.push(b);
      return b;
    };
    const pad = h("div.vp-pad",
      ...["1", "2", "3", "4", "5", "6", "7", "8", "9"].map((n) => key(n, n)),
      h("span"), key("0", "0"), key("⌫", "back", ".vp-back"));
    pad.querySelector(".vp-back").setAttribute("aria-label", "Delete");
    const box = h("div.sheet.vp", { role: "dialog", "aria-modal": "true", "aria-label": title },
      h("div.vp-head", h("span.vp-ico", icon(ICONS.valet, 22)), h("div.title", title)),
      note ? h("p.vp-note", note) : null,
      h("div.vp-dots", { "aria-hidden": "true" }, ...dots),
      msg, pad,
      h("button.btn.vp-cancel", { type: "button", onclick: () => { if (timer) clearInterval(timer); close(); } }, cancelLabel));
    // The keyboard works too: a tablet's docked keyboard, or the owner's.
    box.addEventListener("keydown", (e) => {
      if (/^\d$/.test(e.key)) { e.preventDefault(); press(e.key); }
      else if (e.key === "Backspace") { e.preventDefault(); press("back"); }
    });
    paint();
    if (valet.lockedFor > 0) {
      wait = valet.lockedFor;
      msg.textContent = `Too many tries. Try again in ${cooldownText(wait)}.`;
      timer = setInterval(() => {
        wait -= 1;
        if (wait <= 0) { clearInterval(timer); wait = 0; msg.textContent = ""; }
        paint();
      }, 1000);
      paint();
    }
    setTimeout(() => (keys[4] || keys[0]).focus(), 0);
    return box;
  });
}

// ---- turning it on ---------------------------------------------------------------

// The one tap. The first time, it asks how Valet mode will be turned off.
export async function turnOn() {
  if (valet.on) return;
  if (store.state === "driving") { toast("Valet mode turns on only while the car is parked.", "bad"); return; }
  if (valet.configured) {
    const r = await ask({ action: "on" });
    if (!r.ok) toast(r.body.error || "Could not turn Valet mode on.", "bad");
    else toast("Valet mode is on.");
    return;
  }
  setupSheet();
}

function setupSheet() {
  sheetHost((close) => {
    const go = async (body) => {
      const r = await ask(Object.assign({ action: "on" }, body));
      close();
      if (!r.ok) toast(r.body.error || "Could not turn Valet mode on.", "bad");
      else toast("Valet mode is on.");
    };
    return h("div.sheet.vp", { role: "dialog", "aria-modal": "true", "aria-label": "Set up Valet mode" },
      h("div.vp-head", h("span.vp-ico", icon(ICONS.valet, 22)), h("div.title", "Set up Valet mode")),
      h("p.vp-note",
        "Valet mode hides your history, documents and settings, locks every setting, and logs how the "
        + "car is driven (top speed, hard stops, distance), never where. Turning it off asks for a PIN."),
      h("div.sheet-rows",
        h("button.sheet-row", { type: "button", onclick: () => { close(); choosePin(); } },
          h("span.sheet-l", h("span.sheet-lab", "Choose a four-digit PIN"),
            h("span.sheet-note", "Asked every time Valet mode is turned off.")),
          h("span.sheet-v", "Recommended")),
        h("button.sheet-row.vp-nopin", { type: "button", onclick: () => go({ no_pin: true }) },
          h("span.sheet-l", h("span.sheet-lab", "No PIN"),
            h("span.sheet-note", "Anyone at the tablet can turn Valet mode off with one tap, and then see everything. "
              + "A PIN can be added later in Settings.")),
          h("span.sheet-v", "Not advised"))),
      h("button.btn.vp-cancel", { type: "button", onclick: close }, "Cancel"));
  });
}

// Twice, so a typo is not the PIN that locks the owner out.
function choosePin() {
  let first = null;
  const step = () => openPad({
    title: first ? "Enter it again" : "Choose a PIN",
    note: first ? "To be sure." : "Four digits. You will need it to turn Valet mode off.",
    submit: async (pin) => {
      if (!first) { first = pin; setTimeout(step, 0); return { ok: true }; }
      if (pin !== first) { first = null; setTimeout(step, 0); return { ok: true }; }
      const r = await ask({ action: "on", pin });
      if (!r.ok) return { ok: false, error: r.body.error };
      toast("Valet mode is on.");
      return { ok: true };
    },
  });
  step();
}

// ---- turning it off ----------------------------------------------------------------

export function leave() {
  if (!valet.on) return;
  // The report is shown by main.js when valet's state flips to off, so it
  // appears whichever way valet was turned off.
  if (!valet.hasPin) {
    ask({ action: "off" }).then((r) => {
      if (!r.ok) toast(r.body.error || "Could not turn Valet mode off.", "bad");
    });
    return;
  }
  openPad({
    title: "Leave Valet mode", note: "Enter your PIN.",
    submit: async (pin) => {
      const r = await ask({ action: "off", pin });
      if (r.ok) return { ok: true };
      const b = r.body;
      return { ok: false, retryIn: b.retry_in || 0,
               error: b.tries_left != null ? `${b.error} ${b.tries_left} ${b.tries_left === 1 ? "try" : "tries"} left.` : b.error };
    },
  });
}

// ---- the report ---------------------------------------------------------------------

export function showReport(rep) {
  const v = reportView(rep, { imperial: U.imperial });
  if (!v) return;
  // Shown means seen: acknowledged now, so a closed sheet does not come back.
  ask({ action: "ack" });
  sheetHost((close) => {
    const done = close;
    return h("div.sheet.vp.vp-report", { role: "dialog", "aria-modal": "true", "aria-label": "While you were away" },
      h("div.vp-head", h("span.vp-ico", icon(ICONS.valet, 22)), h("div.title", "While you were away"),
        v.simulated ? h("span.vp-sim", "Simulated") : null),
      h("div.vp-rows", ...v.rows.map((r) =>
        h("div.vp-row", { data: { key: r.key, tone: r.tone || "" } },
          h("span.vp-cap", r.label),
          h("span.vp-val", r.value),
          r.sub ? h("span.vp-sub", r.sub) : null))),
      v.note ? h("p.vp-note", v.note) : h("p.vp-note", "No location is recorded, only how the car was driven."),
      h("button.btn.primary.vp-cancel", { type: "button", onclick: done }, "Done"));
  });
}

// ---- the lamp ----------------------------------------------------------------------------

// The top bar's "Valet" readout, in the console's style: a lit point, a
// caption, the state. Tapping it asks to leave. Hidden when valet is off.
export function valetLamp() {
  const state = h("span.tb-valet-t");
  const node = h("button.tb-valet", {
    type: "button", hidden: true, title: "Valet mode is on. Tap to turn it off.",
    onclick: () => leave(),
  }, h("span.tb-valet-dot", { "aria-hidden": "true" }), state);
  const paint = () => {
    node.hidden = !valet.on;
    const t = valet.logging ? "On" : "On, no log";
    if (state.textContent !== t) state.textContent = t;
    node.dataset.tone = valet.logging ? "ok" : "warn";
    const why = lockReason(true, false);
    node.setAttribute("aria-label", `Valet mode on. ${why}. Tap to turn it off.`);
  };
  paint();
  return { node, paint };
}
