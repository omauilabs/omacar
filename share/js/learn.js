// Learn mode -- the app explaining itself, in place.
//
// The problem this solves: OmaCar shows real diagnostic data, and real
// diagnostic data is full of terms that mean nothing until somebody tells you
// what they mean. "Mode 06 TID 0x01 CID 0x81", "readiness: EVAP incomplete",
// "STFT +7.8%". A tool that shows those to a newcomer with no explanation has
// technically informed them and actually told them nothing.
//
// The alternative usually chosen is a Beginner Mode that hides things. This
// does the opposite: it hides NOTHING and adds explanation. Hiding data from
// someone trying to fix their own car is the vendor-tablet move -- deciding on
// their behalf what they are allowed to understand.
//
// Off by default for people who already know, one toggle away for people who
// do not, and remembered per browser.

const KEY = "omacar.learn";

export const learn = {
  get on() {
    try { return localStorage.getItem(KEY) === "1"; } catch { return false; }
  },
  set on(v) {
    try { localStorage.setItem(KEY, v ? "1" : "0"); } catch { /* private mode */ }
  },
  toggle() { this.on = !this.on; return this.on; },
};

// Written to be read by somebody standing at their own car with the bonnet
// open, not by somebody who already knows the answer. Each entry is one idea.
export const TOPICS = {
  pid: {
    title: "What a PID is",
    body: "A PID (Parameter ID) is one numbered question you can ask the car — "
        + "\"what is the coolant temperature?\" is PID 05. The OBD-II standard "
        + "defines about 200 of them and every car sold since 1996 must answer a "
        + "core set. Your car answers the ones listed here; the rest it simply "
        + "does not implement.",
  },
  readiness: {
    title: "Readiness monitors",
    body: "The car continuously tests its own emissions systems, but each test "
        + "only runs when its conditions are met — some need a cold start, some "
        + "need sustained highway speed, some need a nearly-full tank. A monitor "
        + "reading 'not complete' does NOT mean a fault. It means the test has "
        + "not had a chance to run yet. This is what an emissions station checks: "
        + "clear codes the day before and you will fail, because clearing resets "
        + "every monitor to 'not complete'.",
  },
  dtcstatus: {
    title: "Why a code can be stored but not a fault",
    body: "Each fault code carries a status byte of eight flags. 'Confirmed' "
        + "means the fault happened enough times to light the dash. 'Pending' "
        + "means it failed once and is being watched. 'Not run this cycle' means "
        + "the test has not executed since the last start — extremely common with "
        + "the engine off, and not a fault at all. OmaCar shows you which flags "
        + "are set rather than collapsing them all into a red light.",
  },
  freeze: {
    title: "Freeze frame",
    body: "When the car stores a fault it also photographs the conditions at "
        + "that instant — speed, load, coolant temperature, fuel trims. That "
        + "snapshot is often more useful than the code itself, because it tells "
        + "you whether the problem happens cold, under load, or at idle.",
  },
  trims: {
    title: "Fuel trims",
    body: "The engine constantly corrects how much fuel it injects. Short-term "
        + "trim is the moment-to-moment correction; long-term is the learned "
        + "average. Near 0% means the engine is getting what it expects. "
        + "Persistently above about +10% means it is adding fuel to compensate — "
        + "often an air leak. Persistently below −10% means it is pulling fuel "
        + "out. Trims frequently reveal a developing problem before any code sets.",
  },
  write: {
    title: "Writing a stored value",
    body: "UDS service 0x2E changes a configuration value inside a module, and "
        + "there is no undo: the previous value is gone unless it was written "
        + "down. So this screen reads the identifier back first and shows what "
        + "it holds, and a write carries that value as a claim the server checks "
        + "again before sending. The emissions range is refused whatever the "
        + "mode, and the list on the screen says it is named, not exhaustive.",
  },
  tests: {
    title: "Functional tests",
    body: "Commanding a part rather than asking about it: a fan you can switch "
        + "on is a fan you can prove. On a real car this is UDS service 0x2F "
        + "and the identifier for each part is specific to the model, so a "
        + "button is live only when this car's profile carries one that "
        + "somebody validated by watching the part move. Every other button is "
        + "off and says so. The part is released when the test ends, whatever "
        + "happens.",
  },
  mode06: {
    title: "Mode 06",
    body: "The raw results of the car's own self-tests, including the ones that "
        + "have not failed. Each has a measured value and the pass/fail limits it "
        + "is judged against. A test passing at 95% of its limit is about to "
        + "become a fault code, and Mode 06 is the only place you can see that "
        + "coming.",
  },
  prospect: {
    title: "Finding data the standard does not expose",
    body: "Generic OBD-II is a legally mandated minimum, aimed at emissions. "
        + "Manufacturers put far more on the same wires — hybrid battery cells, "
        + "transmission internals, individual sensor readings — behind "
        + "identifiers they do not publish. The prospector asks the car for those "
        + "identifiers one at a time and records which ones answer. See "
        + "doc/SWEEPING.md for the full method.",
  },
  gates: {
    title: "The protections built in",
    body: "OmaCar can refuse to do things. Sweeps refuse while the car is "
        + "moving, because flooding the bus with unknown requests at speed is not "
        + "a risk worth taking for data. Probes refuse below 11.8 V, because a "
        + "long key-on session with the engine off flattens the battery and trips "
        + "warning lights. And the firmware-flashing services are absent from the "
        + "code entirely — not disabled, absent — so no bug can reach them.",
  },
  documents: {
    title: "Why the paperwork lives here",
    body: "A car's history is not only what its computers remember. The oil "
        + "change receipt, the registration, the inspection certificate and the "
        + "citation are what a buyer asks for and what a warranty claim needs — "
        + "and they are what gets lost. Filed against the same vehicle record "
        + "as the drives and the fault codes, a folder of receipts becomes a "
        + "history you can search and hand over. The advisor can read a "
        + "photograph of a receipt and fill in the date, vendor, total and "
        + "odometer, but what it reads is always shown as extracted and never "
        + "replaces anything you typed.",
  },
  replay: {
    title: "Why replay matters more than live data",
    body: "An intermittent fault is, by definition, not happening while you "
        + "stare at the screen. OmaCar records every reading at about one per "
        + "second whenever it is connected, and segments them into drives. "
        + "Replay lets you scrub back to the exact second something changed "
        + "and read every channel at that instant — which is the moment you "
        + "actually needed to see, and the one you were driving through.",
  },
  resets: {
    title: "Why the reset list may be empty",
    body: "Service resets are manufacturer-specific routines, and the "
        + "identifiers are not published. Unlike readable values, they cannot "
        + "be found by sweeping: a routine is a procedure the module RUNS, so "
        + "guessing its number could spin a fan, cycle an ABS pump or retract a "
        + "parking brake. OmaCar therefore ships only routines somebody has "
        + "confirmed on a real car, and tells you the list is empty rather than "
        + "offering guesses that look like buttons.",
  },
  garage: {
    title: "One database per car",
    body: "OmaCar keeps a completely separate database for every vehicle it "
        + "meets, keyed by VIN. Plug into a different car and it switches "
        + "automatically — before anything is written — so one car's codes, "
        + "service history and drive log can never land on top of another's. "
        + "Give each one a driver's name and a family fleet becomes a list you "
        + "can actually read.",
  },
  hub: {
    title: "Car mode and workshop mode",
    body: "When the adapter connects, OmaCar switches to the car hub: big "
        + "targets, high contrast, and a badge on each tile so you can see what "
        + "needs attention without opening it. Unplug and it returns to the "
        + "workshop layout. Navigate away by hand and it stays where you put it — "
        + "software that drags you back to a screen you just left is software you "
        + "end up fighting.",
  },
};

// Renders an explanation, or nothing at all when learn mode is off. Views call
// this unconditionally; the check lives here so no view has to care.
export function explain(h, key, extra) {
  if (!learn.on) return null;
  const t = TOPICS[key];
  if (!t) return null;
  return h("aside.learn",
    h("div.learn-title", t.title),
    h("p.learn-body", t.body),
    extra ? h("p.learn-body.learn-extra", extra) : null);
}
