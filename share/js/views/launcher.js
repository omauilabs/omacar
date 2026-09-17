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

const POLL_MS = 400;
// Long enough that the last line can be read before the screen changes, short
// enough that nobody sitting in a running car thinks it has hung.
const HANDOVER_MS = 900;

export default function launcher(root) {
  let timer = null;
  let started = false;
  let handover = null;

  const title = h("div.launch-title", {}, "OmaCar");
  const sub = h("div.launch-sub", {}, "Ready when you are");
  const btn = h("button.launch-go", { onclick: press }, "Begin");
  const steps = h("div.launch-steps");
  const foot = h("div.launch-foot");

  clear(root);
  root.appendChild(h("div.launch", {}, title, sub, btn, steps, foot));

  function press() {
    if (started) return;
    started = true;
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
    btn.disabled = false;
    started = false;
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

  return () => {
    if (timer) clearInterval(timer);
    if (handover) clearTimeout(handover);
  };
}
