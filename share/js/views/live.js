// The cluster. The gauge OmaCar started as, kept whole inside the workshop.
//
// It is the view you leave up while the car is running, so it shows the few
// values that change second to second and nothing that does not. The ring
// itself is unchanged from the standalone cluster — the shader was tuned
// against the real display and moving it would have been a good way to break
// something that worked.

import { h, store, temp, econ, U, readOnly,
         adapterState, adapterLabel, connectCar } from "../core.js";
import { makeRing } from "../ring.js";

const MAX_SPEED_KPH = 180, MAX_RPM = 7000;

export default function live(root) {
  let mode = localStorage.getItem("omacar.mode") || "normal";
  const shown = { eff: 0.25, speed: 0, rpm: 0 };
  let raf = null, alive = true;

  const canvas = h("canvas", { width: 900, height: 900, role: "img",
    "aria-label": "Ambient meter: ring colour shows driving efficiency, the bright arc shows road speed." });

  const speedEl = h("div", { style: { fontSize: "clamp(3rem, 9vw, 5.6rem)", fontWeight: "600",
    lineHeight: ".92", letterSpacing: "-.03em", fontVariantNumeric: "tabular-nums" } }, "—");
  const unitEl = h("div.eyebrow", { style: { marginTop: ".35rem" } }, U.units.speed.toUpperCase());
  const rpmEl = h("div.muted", { style: { marginTop: "1rem" } }, "— RPM");
  const modeEl = h("div.eyebrow", mode.toUpperCase());

  const cells = {};
  function cell(id, label) {
    const v = h("div.v", { style: { fontSize: "1.05rem" } }, "—");
    cells[id] = v;
    return h("div.card", h("div.stat-tile", h("div.k", label), v));
  }

  root.appendChild(h("div.grid", { style: { gridTemplateColumns: "1fr minmax(280px, 40vh) 1fr",
      alignItems: "center", gap: "22px" } },
    h("div.sect",
      cell("economy", "Economy"), cell("load", "Engine load"),
      cell("throttle", "Throttle"), cell("timing", "Timing advance")),
    h("div", { style: { position: "relative", display: "grid", placeItems: "center" } },
      canvas,
      h("div", { style: { position: "absolute", textAlign: "center", pointerEvents: "none" } },
        modeEl, speedEl, unitEl, rpmEl)),
    h("div.sect",
      cell("coolant", "Coolant"), cell("intake", "Intake air"),
      cell("stft", "Short fuel trim"), cell("ltft", "Long fuel trim"))));

  const basisEl = h("span.muted");
  // The status line is held rather than looked up by id on every paint. It used
  // to be written with textContent, which is why the old copy could only ever
  // be a sentence telling the user to go and run a command in a terminal: there
  // is no way to hang a button off a text node. Keeping the element means the
  // strip can carry a control as well as a state.
  const dotEl = h("span.dot", { id: "live-dot" });
  const statusEl = h("span", { id: "live-status" }, "connecting…");
  const connectBtn = h("button.btn.primary.sm", { hidden: true, onclick: onConnect }, "Connect");
  // One line under the button for whatever went wrong, or for the serial group
  // hint, which is the single most useful sentence this screen can show on a
  // fresh Arch install and until now was only ever printed to a terminal.
  const hintEl = h("span.muted");

  // What connect.resolve() found, once the server has told us. Fetched at mount
  // so the button can name the port before it is pressed rather than after.
  let adapter = null;
  let connecting = false;

  root.appendChild(h("div.card.flat",
    h("div.row.wrapline",
      dotEl,
      statusEl,
      connectBtn,
      basisEl,
      hintEl,
      h("div.seg.right", ["econ", "normal", "sport"].map((m) => h("button", {
        "aria-pressed": m === mode ? "true" : "false",
        onclick: (e) => {
          mode = m;
          localStorage.setItem("omacar.mode", m);
          for (const b of e.target.parentElement.children) b.setAttribute("aria-pressed", "false");
          e.target.setAttribute("aria-pressed", "true");
          modeEl.textContent = m.toUpperCase();
        },
      }, m[0].toUpperCase() + m.slice(1)))))));

  const draw = makeRing(canvas);
  if (!draw) {
    root.appendChild(h("p.muted", "WebGL is unavailable — the readouts still work, the ring does not."));
  }

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const lerp = (a, b, k) => a + (b - a) * k;

  function paint() {
    const v = store.values, s = store.sample;
    const kph = v.SPEED === undefined || v.SPEED === null ? null : v.SPEED;
    speedEl.textContent = kph === null ? "—" : String(Math.round(kph * U.units.km));
    unitEl.textContent = U.units.speed.toUpperCase();
    rpmEl.textContent = (v.RPM === null || v.RPM === undefined ? "—" : Math.round(v.RPM)) + " RPM";

    const set = (id, text) => { if (cells[id]) cells[id].textContent = text; };
    // Economy is undefined at a standstill and meaningless with the engine
    // off. Saying "idling" over a stopped engine is a small lie the rest of
    // this tool would not tell.
    const running = (v.RPM || 0) > 200;
    set("economy", s.economy_lphk ? econ(s.economy_lphk)
      : running ? "idling" : "—");
    set("load", v.ENGINE_LOAD === undefined || v.ENGINE_LOAD === null ? "—" : Math.round(v.ENGINE_LOAD) + " %");
    set("throttle", v.THROTTLE_POS === undefined || v.THROTTLE_POS === null ? "—" : Math.round(v.THROTTLE_POS) + " %");
    set("timing", v.TIMING_ADVANCE === undefined || v.TIMING_ADVANCE === null ? "—" : v.TIMING_ADVANCE.toFixed(1) + " °");
    set("coolant", temp(v.COOLANT_TEMP));
    set("intake", temp(v.INTAKE_TEMP));
    set("stft", v.SHORT_FUEL_TRIM_1 === undefined || v.SHORT_FUEL_TRIM_1 === null ? "—" : v.SHORT_FUEL_TRIM_1.toFixed(1) + " %");
    set("ltft", v.LONG_FUEL_TRIM_1 === undefined || v.LONG_FUEL_TRIM_1 === null ? "—" : v.LONG_FUEL_TRIM_1.toFixed(1) + " %");

    dotEl.className = "dot" + (store.connected ? " ok live" : connecting ? " warn" : " bad");
    if (store.connected) {
      statusEl.textContent = `${s.port || "connected"}  ·  ${s.protocol || ""}`;
      hintEl.textContent = "";
    } else if (connecting) {
      statusEl.textContent = "connecting…";
    } else {
      // Whatever the daemon last said about itself, and nothing invented on top
      // of it. "not connected" is the honest floor when it has said nothing.
      statusEl.textContent = s.status || "not connected";
    }
    // A cockpit is deliberately read-only — serve.py refuses the write — so it
    // gets the state without a control that could not work.
    connectBtn.hidden = store.connected || readOnly;

    // Say which way efficiency was measured. A ring that looks authoritative
    // while guessing is worse than one that admits it estimated.
    basisEl.textContent = store.connected ? "· " + ({
      economy: "efficiency from measured fuel flow",
      load: "efficiency estimated from load — no MAF on this ECU",
      idle: "stopped — efficiency undefined",
      off: "engine off",
    }[s.efficiency_basis] || "") : "";
  }

  function tick() {
    if (!alive) return;
    const v = store.values, s = store.sample;
    const k = reduced ? 1 : 0.12;
    shown.eff = lerp(shown.eff, s.efficiency === undefined || s.efficiency === null ? 0.25 : s.efficiency, k);
    shown.speed = lerp(shown.speed, v.SPEED || 0, k);
    shown.rpm = lerp(shown.rpm, v.RPM || 0, k);
    if (draw) draw({ eff: shown.eff, speed: shown.speed, rpm: shown.rpm,
                     sport: mode === "sport", live: store.connected,
                     maxSpeed: MAX_SPEED_KPH, maxRpm: MAX_RPM });
    raf = requestAnimationFrame(tick);
  }

  // The whole of the Connect behaviour. It deliberately does not touch the
  // gauges: the ring keeps drawing its last values while the daemon starts, and
  // goes live of its own accord the moment a sample arrives.
  async function onConnect() {
    if (connecting) return;
    connecting = true;
    connectBtn.disabled = true;
    connectBtn.textContent = "Connecting…";
    hintEl.textContent = "";
    paint();
    const r = await connectCar();
    connecting = false;
    if (!alive) return;                 // the view was left mid-connect
    adapter = r.adapter || adapter;
    connectBtn.disabled = false;
    connectBtn.textContent = adapterLabel(adapter);
    hintEl.textContent = r.ok ? "" : r.message;
    paint();
  }

  adapterState().then((a) => {
    if (!alive) return;
    adapter = a;
    connectBtn.textContent = adapterLabel(a);
    // Said before the button is pressed, not after: if the device is there but
    // unreadable, or not there at all, the user can fix it while reading. A car
    // that is already talking needs none of it.
    if (store.connected) return;
    if (a.warning) hintEl.textContent = a.warning;
    else if (a.known && !a.port) hintEl.textContent = "No adapter found — plug the cable into the car's OBD-II port.";
  });

  const off = store.on("live", paint);
  paint();
  tick();
  return () => { alive = false; cancelAnimationFrame(raf); off(); };
}
