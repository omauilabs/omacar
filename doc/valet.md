# Valet mode

Hand the car over without handing over yourself. One tap hides what is
personal, locks what could be changed, and keeps a small log of how the car was
driven while you were gone. Turning it off asks for a four-digit PIN, and then
the app tells you what happened: "While you were away".

## What it does

**Turning it on.** Settings, "Valet mode", or the Valet tile on the Cabin
overview. Parked only: the server reads `live.json` and refuses with 409 if the
car is above 3 km/h (the app's own "moving" line). The first time, it asks how
it will be turned off: choose a PIN (twice), or "No PIN", which says plainly
that anyone at the tablet can then turn it off with one tap.

**Turning it off.** Tap the Valet lamp in the top bar (or the gear, which in
valet mode opens the same thing), or the Cabin tile. With a PIN: the keypad.
Five wrong PINs start a five-minute cooldown, during which even the right PIN
is refused; the count and the cooldown live in `valet.json`, so reloading the
page or opening another window does not reset them. Without a PIN: one tap.

**Hidden, and not routable either.** Work (Agent), Profile (the garage), Log
(drive history and trips), Docs, Report, Replay, Road cameras (pins), and the
Cabin tab's garage-door tile. The Agent's "Earlier answers" and Home's Agent
card are gone; Home's Navigation card (which shows your first pinned road
camera) is replaced by a quiet card that says why. A deep link to a hidden
screen lands on Home with "Not available in Valet mode". The dock still shows
all six tabs; the chip rows simply lose the hidden screens. The fridge and the
air sensors stay.

**Locked.** Settings (the gear opens only the way out), Customize layout, the
equalizer's editing, and every write-capable chip. These ride the same
mechanism as "the car is moving": `store.lockedAsMoving` (`share/js/core.js`)
now also reads `valet.on`, so a control that was greyed at speed is greyed in
valet mode, and its tooltip says "Locked in Valet mode" instead of "Available
when you stop". Presets and bypass on the equalizer, being one tap, stay one tap.

**Privacy.** Valet implies `privacy.on` (`share/js/privacy.js`): VIN, plate and
name are masked, and the "VIN hidden" pill steps aside for the Valet lamp. The
owner's own privacy setting is untouched underneath and returns afterwards.

**The lamp.** "Valet / On" in the top bar, in the console's style, steady and
calm. It turns amber and says "On, no log" if the recorder is not running.

## The log

While valet is on, `lib/valet.py run` (`omacar-valet.service`) reads
`live.json` and keeps:

| | how |
|---|---|
| top speed | the highest sample |
| time above 80 mph | seconds with speed over 128.7 km/h |
| distance | the odometer's change if it moved, else the speed integrated; the report says which |
| hard stops | braking of 3.5 m/s² (about 0.36 g) or more, measured over 0.6 to 1.6 s of history (whole-km/h speed at 5 Hz is too noisy to difference sample to sample), starting above 15 km/h; one event until braking eases below 1.5 m/s² |
| start and end | the two times |

**No location.** Nothing here reads a GPS, and the files hold only those
numbers (`test/valet_test.py` checks the report and log for any coordinate-like
key).

**It tells the truth about itself.** If the recorder was not running, the
report says "The valet log was not running" and gives only the times, never a
column of zeros. A trace from the simulator says Simulated on the card.
Silences longer than 5 s are gaps, not driving at the last speed.

## Files

| file | holds | written by |
|---|---|---|
| `~/.config/omacar/valet.json` (mode 600) | the PIN's salted scrypt hash (n=2^14, r=8, p=1), failed-try count, cooldown | the API |
| `$XDG_STATE_HOME/omacar/valet-state.json` | on/off, since, the last report | the API |
| `$XDG_STATE_HOME/omacar/valet-log.json` | the running log, once a second | the recorder |

One writer per file, so nothing merges two processes' writes. The recorder
reads valet-state.json and writes nothing at all while valet is off.

## API

`GET /api/valet` returns `{on, since, has_pin, no_pin, configured, locked_for,
tries_left, recording, starting, last_report, so_far?}`; never the hash.
`POST /api/valet` takes `{action: "on", pin?|no_pin?}`, `{action: "off", pin}`,
`{action: "ack"}` (the report has been seen) or `{action: "set_pin", old?,
new?|no_pin?}` (valet off only). Refusals: 409 moving or needs setup (`needs_setup`), 403 wrong PIN
(`tries_left`), 429 cooldown (`retry_in`). It is in `COCKPIT_WRITES` because it
changes what the app shows, not what the car does; the demo server answers a
stub.

## Install

```
sed "s|__ROOT__|$PWD|g" share/systemd/omacar-valet.service > ~/.config/systemd/user/omacar-valet.service
systemctl --user daemon-reload && systemctl --user enable --now omacar-valet.service
```

CLI: `omacar valet status | log | on | off | run`.

## What it is not

The PIN is a deterrent, not a vault. It stops somebody in the driver's seat
from opening Settings or your history. Hiding screens is a courtesy of the app,
like the tiers (`lib/modes.py` is the boundary for those writes); a person with
a shell on the tablet, or the API token on the network, is past what this
promises. The PIN check itself (`off`) is server side, so posting `off` with no
PIN is refused whatever the browser believes. Four digits can honestly be given
only a slow hash and a lockout, which is what it has.

Forgot the PIN: with a shell, `rm ~/.config/omacar/valet.json` clears it and
the next turn-on asks again; valet mode can be ended by writing
`{"on": false}` into `valet-state.json`. That is by design: a PIN the owner can
never recover would lock them out of their own data.

## Tests

`python3 test/valet_test.py` (PIN hashing and verification, the lockout, the
parked check, the log's arithmetic on synthetic traces, the recorder against
`live.json`) and `test/js/valet.test.js` (what hides, where a deep link lands,
the lock words, the PIN pad, the report card, valet implies privacy).
