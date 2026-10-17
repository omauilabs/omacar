# The AUX equalizer

Ten parametric bands between everything OmaCar plays and the car radio's AUX
input. Open it from Settings ("Sound") or from the Music screen.

## What it does

Each band has a type (low shelf, peaking, high shelf, low cut, high cut), a
frequency (20 Hz - 20 kHz), a gain (+-12 dB; none for a cut), a width Q (0.3 - 8)
and an on/off. Six presets ship (Flat, Car cabin, Bass, Vocal clarity, Loudness,
Night) and you can save your own. State lives in `~/.config/omacar/eq.json`
(`$XDG_CONFIG_HOME` is honoured), written atomically.

`omacar eq status | on | off | preset NAME | apply`. `apply` rewrites the
PipeWire config from what is saved and starts or restarts the unit only if
something changed; it is safe to run twice. API: `GET /api/eq`, `POST /api/eq`
(the whole state, validated and clamped server-side), `POST /api/eq/live`
(parameters only, at most 20 a second). None of the writes is allowed in the
read-only cockpit (`serve.py` COCKPIT_WRITES does not list them).

## Routing, and why the equalizer is not the default sink

`lib/audio.py` holds the **default sink** at 100% and reads that sink's active
port to say "AUX disconnected". If the equalizer were the default sink, both
would be talking to a virtual node. So it is a WirePlumber **smart filter**: a
PipeWire filter-chain whose input node has `media.class = Audio/Sink`,
`filter.smart = true` and `filter.smart.name = "omacar-eq"`, and **no**
`filter.smart.target`. Per the WirePlumber smart-filter policy, with no target the
filter is attached in front of the default sink: streams link to the filter, and
the filter's output links to whichever device is the default. The default sink,
the volume hold and the AUX detection are untouched.

It is its own process: `share/systemd/omacar-eq.service` runs
`/usr/bin/pipewire -c ~/.local/state/omacar/eq/omacar-eq.conf`, a generated
config modelled on PipeWire's shipped `filter-chain.conf`. Reshaping the bands
therefore never restarts the session's PipeWire. **Bypass is stopping the unit**:
the filter disappears and WirePlumber relinks streams to the hardware.

* Slider moves (gain, freq, Q, preamp) -> `pw-cli set-param <node> Props '{ params = [ "eq_band_4:Gain" 3.0 ] }'`, 1 s timeout, no restart, no dropout.
* Changing a band's **type**, or switching the equalizer on or off, rewrites the
  config and restarts the unit (a short gap). Disabling a shelf or peak is live
  (it is set to 0 dB); disabling a cut removes it from the graph, so that restarts.

## Alerts go through it, so it never boosts

The drowsy-driver alerts play at full scale (`audiobus.js`) and pass through this
equalizer. So: every band is clamped to +-12 dB, and an **automatic preamp** is set
to `-max(0, peak)`, where the peak is the maximum of the combined response over a
20 Hz - 20 kHz log grid (plus each band's corner), computed from the same biquads
PipeWire runs, at 48 kHz, rounded so the maximum lands at or below 0 dB. You may
lower the preamp further (the - button by "Headroom"); you cannot raise it above
the automatic value. Note that PipeWire's cut filters take a true Q: a cut with Q 4
rises +12 dB at its corner, and the preamp accounts for it like any other peak.

## Parked only

Editing greys out while the car is moving, with the reason on it, using the rule
Home's Customize button uses (`store.lockedAsMoving`: driving, or an adapter
hand-off). Controls are greyed, never hidden. Picking a saved preset, the Bypass
toggle and hold-to-compare stay available at speed (one tap, nothing to aim).
The lock is in the page; the server does not know whether the car is moving.

## Without PipeWire

On a machine with no PipeWire (the Mac, CI) `GET /api/eq` says
`{"present": false, "why": "..."}`, and the screen still works on the saved state
and says "Not applied: PipeWire isn't running here". Nothing is started.

## Nothing plays by itself

"Play pink noise" is a button: -30 dBFS, stops after 10 seconds (or when you
leave the screen). It plays through the page's output, so it goes through the
equalizer. Hold-to-compare sets every band to 0 dB while held and restores them on
release (cuts are moved out of band, so it is "flat", not bit-exact).

## Installing it on the tablet

    cd ~/path/to/omacar && ./install.sh      # installs the unit file (none are enabled)
    omacar eq on                             # enable + start; same as turning it On in the app
    omacar eq apply                          # idempotent: regenerate, restart only if needed
    systemctl --user status omacar-eq
    wpctl status                             # the default sink must still be the headphone jack;
                                             # "OmaCar Equalizer" appears under Filters / as a sink

`omacar eq off` stops and disables the unit. The unit regenerates its config
from the saved state (`ExecStartPre`), so it also comes up correctly at login.

## Testing it in the driveway, with the AUX cable

1. Plug in AUX; Home should say AUX connected. Turn the radio down, engine off or
   in accessory.
2. Open Equalizer, tap On. The lamp goes green: "On - Flat".
3. Play music (Music screen) and hold **Compare**: with Flat nothing changes. Pick
   **Bass** and hold Compare repeatedly: the bass should drop out and return with
   no click. Drag a handle while music plays: no dropouts.
4. Tap **Play pink noise** (quiet, 10 s) and sweep a boost from 60 Hz to 8 kHz.
5. Play a drowsy alert (however you normally test them) with a +12 dB band set:
   the preamp should read about -12 dB and the alert must not clip.
6. Tap Bypass and confirm music carries on, unprocessed, without restarting.
7. Check `wpctl status` still names the jack as the default sink and `omacar audio
   status` still reads the port.

## Checked against

* WirePlumber smart filters: https://pipewire.pages.freedesktop.org/wireplumber/policies/smart_filters.html
  (`filter.smart`, `.name`, `.target`, no target = default sink; capture node
  `Audio/Sink`, playback node `node.passive`).
* PipeWire module-filter-chain: https://docs.pipewire.org/page_module_filter_chain.html
  (`bq_*` labels, `Freq`/`Q`/`Gain`, `linear` `Mult`/`Add`).
* Biquad source: `spa/plugins/audioconvert/biquad.c` and `spa/plugins/filter-graph/plugin_builtin.c`
  (Gain in dB, Freq in Hz, A = 10^(dB/40)); sample `sink-eq6.conf`.
* Config skeleton: `src/daemon/filter-chain.conf.in`.
* On PipeWire 1.6.8 in an isolated daemon (a private XDG_RUNTIME_DIR, no session
  manager, `filter.smart = false`): the generated config loads, the node appears
  as `omacar-eq` (`Audio/Sink`) with `omacar-eq-out`, and `pw-cli set-param ... Props`
  with these names is accepted. NOT confirmed there: that WirePlumber links the
  smart filter in front of the jack, that stopping the unit relinks cleanly, and
  that a live change is audible. Those are for the tablet.
