// First-run onboarding.
//
// The audience is somebody who just installed a plugin, has never seen an OBD-II
// adapter, and does not know whether this thing is safe to plug into a car they
// still need to drive to work tomorrow. That last worry is the real one, and it
// is the reason the safety card is not buried at the end.
//
// Six cards, skippable at any point, shown once. It sets a flag rather than
// tracking "have they done the steps", because software that will not let you
// past a checklist until you complete it is software that traps somebody whose
// adapter is still in the post.

import { h, clear } from "./core.js";
import { learn } from "./learn.js";

const KEY = "omacar.onboarded";

export const onboard = {
  get done() {
    try { return localStorage.getItem(KEY) === "1"; } catch { return true; }
  },
  set done(v) {
    try { localStorage.setItem(KEY, v ? "1" : "0"); } catch { /* private mode */ }
  },
};

const CARDS = [
  {
    title: "A full diagnostic tool — reading and writing",
    body: [
      "OmaCar reads your car, and it can write to it: clear fault codes, run "
      + "functional tests that command actuators directly, and change module "
      + "settings. That is what a real scan tool does, and a tool that cannot "
      + "clear a code after you have fixed the fault is a viewer, not a "
      + "diagnostic.",
      "Writing starts switched off. One command arms it — omacar write arm — "
      + "and it disarms itself after fifteen minutes. Before anything is sent, "
      + "you are shown exactly what that operation does to the car, including "
      + "what you cannot undo.",
      "Reading is always available and needs no arming.",
    ],
    action: null,
  },
  {
    title: "What the guards actually stop",
    body: [
      "Writes refuse while the car is moving, and refuse below 12.2 volts — a "
      + "write interrupted by a brownout leaves a module holding half a change. "
      + "Sweeps refuse while moving too, and refuse below 11.8 volts.",
      "Reprogramming services (0x34/0x36/0x37) are not implemented. Not "
      + "disabled — absent. They need a manufacturer-signed firmware image this "
      + "tool cannot produce, and a partial transfer leaves a module unable to "
      + "boot. That is a tow truck, not a fault code.",
      "Everything else is available to you, with the consequences stated up "
      + "front rather than buried in a manual.",
    ],
    action: null,
  },
  {
    title: "What you need",
    body: [
      "An ELM327-compatible OBD-II adapter. A wired one is worth the small extra "
      + "cost — bluetooth adapters drop frames, and a dropped frame in the middle "
      + "of a multi-frame reply looks exactly like a car with nothing to say.",
      "Your car's port is almost always under the dash on the driver's side.",
      "Plug it in, then turn the ignition to position II — the one before the "
      + "engine starts. Most data is available there.",
    ],
    action: null,
  },
  {
    title: "Check the link first",
    body: [
      "Before anything else, confirm the adapter and the car are talking.",
      "From a terminal: omacar doctor",
      "It reports the port, the protocol it negotiated, the battery voltage and "
      + "which modules answered. If that works, everything else will.",
    ],
    action: null,
  },
  {
    title: "Two layouts, automatic",
    body: [
      "Plugged into the car, OmaCar switches to the car hub: big touch targets, "
      + "high contrast for sunlight, and a badge on every tile so you can see "
      + "what needs attention without opening it.",
      "Unplugged, it stays exactly where it is and offers you the hub rather "
      + "than taking you there.",
      "Navigate away by hand and it stays where you put it. Software that drags "
      + "you back to a screen you just left is software you end up fighting.",
    ],
    action: null,
  },
  {
    title: "If a term means nothing to you, turn on Learn mode",
    body: [
      "Readiness monitors, fuel trims, Mode 06, freeze frames — this app shows "
      + "real diagnostic data, and real diagnostic data is full of jargon.",
      "Learn mode adds an explanation to each screen. It hides nothing; it only "
      + "adds. Deciding what you are allowed to understand about your own car is "
      + "the vendor's move, not ours.",
      "The gear at the right of the vehicle bar turns it on and off.",
    ],
    action: { label: "Turn on Learn mode", run: () => { learn.on = true; } },
  },
  {
    title: "Going further than standard OBD-II",
    body: [
      "The standard set of readings is a legal minimum aimed at emissions. Your "
      + "car measures far more on the same wires, behind identifiers the "
      + "manufacturer never published.",
      "OmaCar can hunt for them — safely, read-only, and refusing to run while "
      + "you are moving. It is how we found 49 hybrid-specific fault codes on a "
      + "Honda that returned nothing from 8,192 standard identifiers.",
      "See doc/SWEEPING.md when you are ready.",
    ],
    action: null,
  },
];

export function showOnboarding(host, opts) {
  const onClose = (opts && opts.onClose) || (() => {});
  let i = 0;

  function draw() {
    clear(host);
    const card = CARDS[i];
    host.appendChild(h("div.ob-scrim",
      h("div.ob-card", { role: "dialog", "aria-modal": "true",
                         "aria-label": card.title },
        h("div.ob-dots", ...CARDS.map((_, n) =>
          h("span.ob-dot" + (n === i ? ".on" : "")))),
        h("h2.ob-title", card.title),
        ...card.body.map((p) => h("p.ob-body", p)),
        h("div.ob-actions",
          h("button.btn.ghost", { onclick: finish }, "Skip"),
          h("div.ob-spacer"),
          card.action
            ? h("button.btn", {
                onclick: () => { card.action.run(); next(); },
              }, card.action.label)
            : null,
          h("button.btn.primary", { onclick: next },
            i === CARDS.length - 1 ? "Start" : "Next")))));
    const first = host.querySelector(".btn.primary");
    if (first) first.focus();
  }

  function next() { if (++i >= CARDS.length) finish(); else draw(); }

  function finish() {
    onboard.done = true;
    clear(host);
    host.hidden = true;
    document.removeEventListener("keydown", onKey);
    onClose();
  }

  // Escape closes it. A modal you cannot dismiss is a modal that will be
  // dismissed by closing the whole application.
  function onKey(e) { if (e.key === "Escape") finish(); }
  document.addEventListener("keydown", onKey);

  host.hidden = false;
  draw();
}
