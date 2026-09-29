// The screen the tablet powers on to, and the one button on it.
//
// WHY A SCREEN AND NOT A COMMAND. Getting ready to drive was seven commands
// typed in a driveway, one-handed, with the engine running. On 16 September
// three of them went wrong and two of those were invisible until the drive was
// over: a recorder holding the serial port, and a daemon that reported failure
// while connecting perfectly. `omacar begin` made that one press. This makes
// the press a target you can hit with a thumb, in the dark, wearing gloves.
//
// IT SHOWS THE STEPS, AND THAT IS THE POINT. A spinner over an unknown state is
// exactly what cost the switch hunt: `listen marks` had stopped receiving
// frames twenty seconds in while the status file went on saying "capturing",
// and nothing on any screen said otherwise. So every step from `begin --json`
// lands here as it happens, with its own words, and a failure keeps the line
// that explains it on screen instead of replacing it with a spinner.
//
// IT NEVER OPENS THE DASHBOARD OVER A FAILURE. A driver who pulls away on a
// red line is recording nothing, and a dashboard full of dashes looks close
// enough to working to be believed at sixty miles an hour.

import { h, clear, store, api } from "../core.js";
import { showAux } from "../audiostate.js";

const POLL_MS = 400;
// Long enough that the last line can be read before the screen changes, short
// enough that nobody sitting in a running car thinks it has hung.
const HANDOVER_MS = 900;

// Begin's chime, reached through here so that a test can press Begin in
// silence: nothing a test does may sound on the box's speakers.
export const beginSound = { play: () => import("../alertplayer.js").then((m) => m.beginChime()) };

export default function launcher(root) {
  let timer = null;
  let started = false;
  let handover = null;

  const title = h("div.launch-title", {}, "OmaCar");
  const sub = h("div.launch-sub", {}, "Ready when you are");
  const btn = h("button.launch-go", { onclick: press }, "Begin");
  const steps = h("div.launch-steps");
  const foot = h("div.launch-foot");
  // THE CAR CANNOT BE ASKED WHICH SOURCE ITS RADIO IS ON, so this says it.
  const aux = h("div.launch-aux", {},
    "Sound reaches the car through the AUX cable. Keep the car's radio on AUX: "
    + "OmaCar cannot see which source it is on, and the chime you hear on Begin is the check.");
  // BUT THE TABLET CAN SEE ITS OWN PORT: when sound is going to its speakers
  // and not down the cable, that is said outright, not left to the chime.
  const unplugged = h("div.aux-warn", { hidden: true });

  clear(root);
  root.appendChild(h("div.launch", {}, title, sub, btn, aux, unplugged, steps, foot));
  const offAux = showAux(unplugged);

  function press() {
    if (started) return;
    started = true;
    // THE CHIME SAYS THE PATH WORKS: from the tablet, down the AUX cable, out
    // of the car's speakers, rising from silence like every alert. No sound
    // is not a failed start, so it cannot stop the sequence.
    beginSound.play().catch(() => {});
    btn.disabled = true;
    btn.textContent = "Getting ready";
    sub.textContent = "One port, one owner — checking each step";
    api.beginStart().then(poll).catch(fail);
    timer = setInterval(poll, POLL_MS);
  }

  function fail(e) {
    clear(foot);
    foot.appendChild(h("div.launch-bad", {},
      "Could not start the sequence: " + (e && e.message ? e.message : String(e))));
    again();
  }

  function poll() {
    api.beginStatus().then(paint).catch(fail);
  }

  function paint(s) {
    if (!s) return;
    clear(steps);
    for (const st of s.steps || []) {
      const tone = st.ok ? "ok" : st.fatal ? "bad" : "warn";
      steps.appendChild(h("div.launch-step." + tone, {},
        h("span.launch-mark", {}, st.ok ? "✓" : st.fatal ? "✕" : "!"),
        h("span.launch-what", {}, st.step),
        h("span.launch-said", {}, st.said)));
    }
    if (s.running || s.rc === null || s.rc === undefined) return;

    if (timer) { clearInterval(timer); timer = null; }
    if (s.rc === 0) {
      sub.textContent = "Ready";
      btn.textContent = "Opening the dashboard";
      // The dashboard is the destination, not this screen. Anything that keeps
      // a driver here after the car is answering is in the way.
      handover = setTimeout(() => { location.hash = "#drive"; }, HANDOVER_MS);
      return;
    }
    // A REFUSAL KEEPS ITS REASON ON SCREEN. Each failing step already carries
    // the command that fixes it, printed by begin.py; repeating it here in
    // different words would be a second, competing account of the same fault.
    sub.textContent = "Not ready — the marked line says why";
    btn.textContent = "Begin";
    // RE-APPLY THE LOCK, NEVER JUST RE-ENABLE. The car may have started
    // moving during the request that just came back refused; started has to
    // drop first, since paintLock() itself refuses to touch the button while
    // it is still true.
    started = false;
    paintLock();
    again();
  }

  function again() {
    clear(foot);
    foot.appendChild(h("button.launch-anyway", {
      onclick: () => { location.hash = "#drive"; },
    }, "Open the dashboard anyway"));
    foot.appendChild(h("div.launch-note", {},
      "Nothing will be recorded until the marked step passes."));
  }

  // The car may already be connected — somebody came back to this screen
  // rather than powering on. Say so rather than making them press Begin to
  // find out.
  if (store.connected) {
    sub.textContent = "The car is already answering";
  }

  // NOT WHILE MOVING, AND NOT DURING A HAND-OFF. Begin stops
  // omacar-drivelog.service and restarts the daemon (lib/begin.py), so pressed
  // mid-drive it ends the very recording it exists to start. Nothing on a
  // drive screen leads here, and the button itself also greys out with the
  // reason, as every write screen's chip does, while the car is moving or was
  // last seen moving before the adapter dropped. The daemon also reports
  // connected: false, handover: true for ten to twenty seconds every few
  // minutes while it lends the adapter to the DTC sweep -- that can start
  // while stopped, when lastMoving is still false, so the lock has to know
  // about the hand-off by name rather than inferring it from motion.
  function paintLock() {
    if (started) return;
    const moving = store.state === "driving" || (!store.connected && store.lastMoving)
                   || !!store.sample.handover;
    btn.disabled = moving;
    btn.title = moving ? "Available when you stop" : "";
  }
  const offLive = store.on("live", paintLock);
  const offCar = store.on("car", paintLock);
  paintLock();

  return () => {
    offLive();
    offCar();
    offAux();
    if (timer) clearInterval(timer);
    if (handover) clearTimeout(handover);
  };
}
