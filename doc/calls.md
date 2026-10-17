# Hands-free calls

The tablet is the car kit; the owner's iPhone is the phone. A call rings on the
tablet's screen (silently), is answered and ended with a thumb, and its audio
comes out of the AUX cable the music uses, with the Surface's microphone (or a
USB one) going back to the phone.

**Nothing here has been run against a real iPhone, a real oFono or a real
PipeWire call link.** Everything below under "Verified" was run on a Mac with
no Bluetooth; everything under "Not verified" is the first drive's list.

## Which stack, and why

Researched 2026-10-17.

- PipeWire's native bluez5 backend can *be* a hands-free unit: `bluez5.roles`
  takes `hfp_hf` (on by default), and `bluez5.hfphsp-backend` selects `native`
  (default), `ofono`, `hsphfpd`, `any` or `none`
  ([pipewire-props(7)](https://docs.pipewire.org/page_man_pipewire-props_7.html),
  [WirePlumber bluetooth configuration](https://pipewire.pages.freedesktop.org/wireplumber/daemon/configuration/bluetooth.html)).
- But the call control PipeWire publishes on D-Bus is for the other role. Its
  telephony service (`org.pipewire.Telephony`, with `AudioGateway1`, `Call1`
  and oFono-shaped `VoiceCallManager`/`VoiceCall` interfaces; Collabora, 2025,
  [announcement](https://gkiagia.gr/2025-02-20-pipewire-telephony/), source
  `spa/plugins/bluez5/telephony.c`) is for the **Audio Gateway** role: PipeWire
  acting as the phone to a headset. As the **hands-free unit** PipeWire parses
  the phone's `+CLIP`/`+CIEV` lines internally but exposes no answer, hang-up,
  caller ID or call state to applications. I could not find any documentation
  saying otherwise.
- oFono's `hfp_hf_bluez5` plugin *is* the hands-free unit with call control:
  `org.ofono.VoiceCallManager` (`GetCalls`, `Dial`, `HoldAndAnswer`,
  `HangupAll`) and `org.ofono.VoiceCall` (`State`, `LineIdentification`,
  `Name`, `Answer`, `Hangup`), plus `org.ofono.CallVolume`. Its
  `HandsfreeAudioManager` hands the SCO audio link to a registered agent, and
  PipeWire's `ofono` backend is that agent
  ([oFono handsfree-audio-api](https://github.com/rilmodem/ofono/blob/master/doc/handsfree-audio-api.txt)).
- People doing this today (phone-as-gateway on a Raspberry Pi) use BlueZ +
  oFono + PipeWire/WirePlumber together
  ([Sipfront, 2025](https://sipfront.com/blog/2025/03/automating-calls-on-real-mobile-devices-with-bluetooth-hfp-on-a-raspberry-pi/)).

**Decision: oFono owns the HFP link and the call control; PipeWire
(`bluez5.hfphsp-backend = "ofono"`) only receives and routes the audio.**
Exactly one program may register the HFP profile with BlueZ, so the backend
setting is required, not optional. `omacar calls setup` writes it
(`~/.config/wireplumber/wireplumber.conf.d/51-omacar-calls.conf`).

OmaCar polls oFono with `busctl` (no D-Bus library to install, and no monitor
privilege, which the system bus withholds from ordinary users), twice a second.

## Setup

1. `sudo pacman -S ofono bluez bluez-utils`; `sudo systemctl enable --now bluetooth ofono`.
2. `omacar calls setup`, then `systemctl --user restart wireplumber pipewire`.
3. `omacar calls pair` prints the bluetoothctl steps and opens it. iPhone:
   Settings > Bluetooth on, stay on that screen; `pair`, `trust`, `connect`.
   Say No if it asks to share contacts or notifications.
4. `omacar calls use AA:BB:CC:DD:EE:FF` so it follows that phone.
5. `systemctl --user enable --now omacar-calls.service` (or `omacar calls run`).
6. Cabin > Calls shows the link; `omacar calls status` prints what the app is told.
7. Optional microphone: `{"mic": "<pipewire node name>"}` in
   `~/.config/omacar/calls.json` (default: the default source, the Surface's).

`omacar calls sim` rehearses a call (rings, you tap Answer, it talks, it ends,
it rings again). Every screen says **Simulated**. It writes the same file as the
real link, so do not run it beside the service, and it never touches the recent
-calls log.

## What you see

- **Incoming card**, over any screen: caller in large type (the phone's name
  if it sent one, else the number, else "Unknown caller"), a lit **Answer** and
  a **Decline**, 64 px keys. No ringtone: the card is silent, because a child
  sleeps near the tablet.
- **Bar** while a call is on: caller, duration, **Mute** (lit while muted),
  **Hang up**. After the call, "Call ended" and the time for four seconds.
- **Call waiting**: a second card, "Hold and answer" / "Decline".
- **Cabin > Calls**: link state, call volume, which microphone, recent calls.
  **Call back** is parked-only: greyed (never hidden) while the car moves, and
  refused by the server too (speed above zero in the last 30 s, or an adapter
  hand-off). It dials a number already in the list by id; the request never
  carries a number, there is no keypad, and no dialing while moving.
- Answer, decline, hang up, mute and volume are never gated by the moving lock.
- Where it comes from is always on screen: "James's iPhone · hands-free",
  "Simulated", "Last seen N ago", "Phone not connected". A state file older than
  6 s shows **no call** at all: a ring from a minute ago is not ringing.

## Audio

While a call is active, held or dialing, `omacar-calls` starts two
`pw-loopback` processes: the phone's voice to the **default sink** (so
WirePlumber's smart-filter policy sends it through the AUX equalizer, like
everything else, when it is on) and the microphone to the phone. It stops only
processes it started. The page ducks the music to silence on a call-only gain
in `audiobus.js` (`callDuck`, 1.5 s down, 3 s back), separate from the music
bus the alerts schedule, so a drowsy alert's duck cannot bring the music back
under a call. Safety alerts still sound over a call, by design. Mute is
`wpctl set-mute` on the microphone, always cleared when the call ends. Volume is
`org.ofono.CallVolume.SpeakerVolume` (AT+VGS), and the car's own knob still
scales everything.

## Precedence

Everything the call draws sits at z-index 240: above the radar banner (80),
below drowsy mode's banner (250), the parking countdown (260) and drowsy
mode's layer (300). While drowsy mode or a child alert shows, the incoming
card shrinks to a key pair at the left edge instead of a card in the middle,
and the bar follows. `test/calls_test.py` reads the stylesheets and fails if
that order drifts.

**Hook for the child alert:** any element marked `data-alert="child"` (and not
`hidden`) is treated as a child alert (`alertsNow()` in `share/js/calls.js`).
The branch that builds it should set that attribute and keep its z-index above
240.

## Verified

- The call state machine, from D-Bus-shaped events and a fake clock (70 checks
  in `test/calls_test.py`): incoming, answered, ended, missed, declined,
  outgoing, held, call waiting, priority, snapshots, the phone leaving, and
  that a stale file shows no call.
- What each request means, that `dial` takes only a list id and is refused
  while moving, and that answer, decline, hang up, mute and volume are not.
- The parsers for `busctl --json=short` replies and `pw-dump`, as I understand
  their shapes, and the `pw-loopback` command lines.
- The view logic and precedence (`test/js/calls.test.js`) and the layer order
  in the CSS. The simulator, end to end through the API, and the screens at
  1368x912 (screenshots in `omacar-notes/screenshots-2026-10-17/features/calls-*`).

## Not verified (first drive's list)

- Any of it against a real iPhone and oFono. The `busctl` JSON shape, oFono's
  `State` values and when it drops a call, the property names on the modem.
- Whether oFono's hfp_hf_bluez5 plugin registers cleanly beside BlueZ on this
  Arch, and whether `bluez5.hfphsp-backend = "ofono"` produces the phone's
  `bluez_input`/`bluez_output` nodes (OmaCar finds them by MAC and says so
  in the note if they are missing).
- `pw-loopback` flags (`-C`, `-P`, `-l`, `-c`) as used.
- **Echo.** The tablet's microphone will hear the car's speakers (the AUX call
  audio), so the caller may hear themselves. This needs PipeWire's
  `libpipewire-module-echo-cancel` on the microphone path, not set up here.
- iPhone call-audio routing: the phone may keep the call on its own speaker
  until it is moved to the car kit from its call screen.
- Whether an iPhone sends a name (`+CNAP`) at all; expect the number only. No
  contact lookup is done, on purpose.
- The latency of a half-second poll for an incoming call.

## Privacy

Numbers and names are kept on the tablet only, in the last 20 calls
(`~/.local/state/omacar/calls-recent.json`). `omacar calls forget` erases them.
Nothing is sent anywhere.

## Decisions for the owner

- Ringtone: none (silent card). Say if the tablet may ring, and how quietly.
- The Calls screen is a chip under Cabin; there is no Home card yet.
- Call back from a missed call is on a list tap only; whether to allow it with
  the car stopped at a light is decided by the speed sample alone.
