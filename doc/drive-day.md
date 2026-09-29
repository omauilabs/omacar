# Drive day — Marina ↔ Los Banos, Tuesday 2026-09-29

Two legs, out and back. This is the one resource this project cannot buy, so
everything below is here to stop a leg being wasted.

Written 9 September after an adversarial read of the whole capture path found
nine defects, every one of which cost at least a leg. They are fixed; this is
what is left for a person to do.

---

## Tonight, on the tablet

The tablet has to be on and on the tailnet for any of this. In order:

```
cd ~/Projects/omacar && git pull
./install.sh                       # picks up the new units
systemctl --user daemon-reload
systemctl --user enable --now omacar-drivelog.service

omacar tablet awake                # one sudo, and it asks first

omacar preflight                   # and this is the one that matters
```

`omacar tablet awake` is in that list because suspending is the failure that has
actually destroyed a drive: on 8 September the tablet asked for
suspend-then-hibernate at 15:57 while it was recording, and that boot never
resumed. It masks the sleep targets and takes the power button away from
suspend. Preflight refuses to pass without it, which it used to do — the row was
there, coloured yellow, next to "this laptop cannot sleep".

It is interactive and needs one sudo, so it is a thing to do indoors rather than
in the driveway.

`omacar preflight` is the whole of the rest of this section. It asks the six
questions somebody would otherwise run six commands for, in a driveway, in the
dark, and it exits 0 only when nothing on this machine will stop a recording.
It fixes nothing — every line names the command that would.

It needs no car, so the right time to run it is the night before, indoors.

`omacar tablet status` now leads with a **recording the bus** row. Until
tonight there was no such row, which is how a tablet with a perfect dashboard
and no recorder reported itself completely ready.

## Then ten minutes in the driveway, engine idling

This exercises almost everything that cannot be tested without a car.

1. Plug the adapter in. `omacar drive status` should move from `waiting` to
   `capturing, leg 1` within about fifteen seconds.
2. Watch it for two minutes. The frames-and-elapsed line must **advance**. It
   updates every fifteen seconds now; before tonight it froze for the whole leg
   and the screen said the supervisor was dead.
3. `omacar drive off` — hand the port back.
4. One `omacar listen capture --seconds 30 --save --note econ`, then check the
   saved file has a `raw` key. It will: `--save` keeps the frames now. It did
   not on 8 September, and that is why that drive answered nothing.
5. `omacar drive on`.

If step 1 never leaves `waiting`, the engine gate is not seeing RPM. If it says
`not running`, the unit did not enable and the whole trip is gauges only.

## If nobody can drive this remotely: the parked IMA session

If the tablet has no connection for anyone to run this from outside the car,
one command runs the whole parked, read-only IMA hunt (the leads writeup is
`2026-09-28-ima-findings.md`, section 4, in the notes repo) — the passive
probes, the negative-response learning, the 0x2xxx positive control, the
HV-battery DIDs, and the 0x21 sweep, each already built with the safety gates
that section calls for:

```
~/Projects/omacar/tools/ima-session.sh
```

Have the engine running, the car in P, and the handbrake on before you start
it — it checks for an adapter, asks you to confirm you are parked, checks
road speed and battery voltage, and only then sends anything. It stands the
recorder down first and always brings it back, prints each step as it goes,
time-boxes itself to 25 minutes of sending, and saves everything under
`~/.local/state/omacar/ima-sessions/`. Add `--full` to also run the optional
block sweep (section 4 step 5); add `--dry-run` first to see every command it
would run without sending anything.

## Then, if there's time: the full-bus driveway check

Two more opt-ins exist, each one line in the environment, both off by default,
and both change what a capture actually records. Try them here, ignition on,
parked, never on the road for the first time.

`OMACAR_FASTBAUD=1` raises the adapter's own link from 115200 to 500000 baud.
Measured on this car: at 115200 the bus overflows the adapter every time — one
~111-frame buffer, about 75ms — and it stops. At 500000 the same adapter held
1,907 frames a second, sustained, for fifteen seconds straight. It is already
in this tree (commits 603fe09 and 52cc8ba); it is not new tonight, it is just
untested end to end on this car.

`OMACAR_CAF0=1` turns off the ELM's own CAN auto-formatting for the capture.
Left on — the default, forever, until this flag says otherwise — it censors
frames two ways: any frame whose first data byte is 0x01–0x07 gets CUT to
that many bytes plus one (measured on 636 of 636 such frames — 0x164 shows as
five bytes, `0400003C72`, when parked, and the full eight when byte0=00; 0x161
shows `05BF05BF2008`, six bytes, with its counter and checksum simply never
sent), and any frame whose first byte is 0x08–0x0F or ≥0x20 comes back with
" <DATA ERROR" appended, which the old parser threw the whole line away for —
818 lines out of 3,554 on one drive, 23% of everything the adapter sent.
Gear position (0x191: 0x01=P, 0x08=D) was invisible in every moving capture
because of exactly this. The host-side half of that fix — recovering a
`<DATA ERROR` line's real bytes, which are already on the wire and cost
nothing to strip out of the reply — is always on now and needs no flag.
`OMACAR_CAF0` is the other half, and the only one that changes what is SENT to
the adapter, which is why it stays opt-in until it has been driven.

`omacar drive off` first, always — a capture and the recorder both want the
one port. Then, in order:

1. Raised baud alone:

   ```
   timeout 90 env OMACAR_FASTBAUD=1 \
     omacar listen capture --seconds 20 --save --note fastbaud-test
   ```

   **Pass:** well over 20,000 frames, the raw timestamps span about 20s, the
   command returns on its own (`timeout` never has to fire), and afterwards
   the daemon reconnects — live data updates again.
   **Fail:** a hang (the 90s `timeout` fires), or about 120 frames. Either one
   means stop here; do not add `OMACAR_CAF0` on top of a link that has not
   proven itself.

2. Both opt-ins together:

   ```
   timeout 90 env OMACAR_FASTBAUD=1 OMACAR_CAF0=1 \
     omacar listen capture --seconds 20 --save --note caf0-test
   ```

   **Pass:** identifiers 097, 1AA, 1CF and 374 all show up, and the 0x161 and
   0x164 frames are the full 8 bytes each — not the truncated 6 and 5 bytes
   (respectively) ATCAF1 leaves them at.

3. If both pass, turn them on for the recorder itself with a user drop-in —
   nothing in this repo needs editing:

   ```
   mkdir -p ~/.config/systemd/user/omacar-drivelog.service.d
   cat > ~/.config/systemd/user/omacar-drivelog.service.d/fullbus.conf <<'EOF'
   [Service]
   Environment=OMACAR_FASTBAUD=1 OMACAR_CAF0=1
   EOF
   systemctl --user daemon-reload && systemctl --user restart omacar-drivelog
   ```

   To undo: delete `fullbus.conf` and run the same `daemon-reload` and
   `restart`.

4. **What actually changes, in the recorder itself.** `lib/drivelog.py` leases
   the port in `LEG_MINUTES`-long (20 min) legs with `BETWEEN_LEGS` (90s)
   between them for the daemon to reconnect, and ends a leg early if
   `QUIET_TIMEOUT` (120s) passes with nothing heard. `listen.DEFAULT_LIMIT`
   caps any one capture at 60,000 lines.

   Today, without either opt-in, a leg's ~75ms burst is followed by silence,
   the 120s quiet timeout ends it, and the 90s gap follows: **75ms of the bus
   every 3.5 minutes** (120 + 90 = 210s).

   With both opt-ins, the bus keeps arriving fast enough that the leg instead
   runs until the 60,000-line cap — at the measured ~1,900 frames/s that is
   about 60000 / 1900 ≈ 31s — then the same 90s gap: **about 31s of the full
   bus every 2 minutes** (31 + 90 ≈ 122s), instead of 75ms every 3.5 minutes.
   That is about a quarter of all bus time instead of under a
   thousandth of it (31/122 ≈ 25%, against 0.075/210 ≈ 0.04%): some
   seven hundred times as much of the bus, recorded uncensored, for the
   same drive.

## Telemetry versus capture

The number above (31/122 ≈ 25%) is how much of the drive is a full-bus
*capture*. The number that matters for a demo is the other side of the same
cycle: how much of the drive the daemon has the port back and the gauges are
alive — *telemetry*. A leg that captures more leaves less of that, and the
trade is now three independent knobs, each an environment override read (and
bounds-checked) at start-up in `lib/drivelog.py`, unset by default, no
different in kind from `OMACAR_FASTBAUD` and `OMACAR_CAF0` above:

- `OMACAR_DRIVELOG_BETWEEN` — seconds between legs (default `BETWEEN_LEGS`,
  90s).
- `OMACAR_DRIVELOG_LEG_LINES` — the line cap for one leg, passed straight to
  `listen()` as `limit=` (default `listen.DEFAULT_LIMIT`, 60,000).
- `OMACAR_DRIVELOG_QUIET` — the quiet timeout (default `QUIET_TIMEOUT`,
  120s).
- `OMACAR_DRIVELOG_END_ON_OVERFLOW=1` — end a leg the moment the adapter
  itself says `BUFFER FULL` or `STOPPED` (`Capture.overflowed()`), instead of
  waiting out the full quiet timeout afterwards. Off by default; see below.

A bad value for any of the first three (unparseable, or outside a sane
floor/ceiling) is logged and the default used instead — it costs the
override, never the leg. `omacar drive status` shows whichever numbers a
running supervisor actually started with, and the same line is written once
to the day's trip log at start-up.

| Setup | A leg holds the port for | Then the gap is | Cycle | Telemetry |
|---|---|---|---|---|
| Default (nothing set) | ~120s — a 75ms burst, then the 120s quiet timeout waits out the silence | 90s | ~210s | **~43%** |
| Fast link alone, default gap — this is also the **balanced** preset below | ~31s — the 60,000-line default cap at the ~1,900 lines/s measured above | 90s | ~121s | **~74%** |
| **Telemetry-first** preset below | ~5s — a 10,000-line cap at the same ~1,900 lines/s, plus a few seconds of hand-over | 240s | ~250s | **roughly 95%** |

**Balanced** — the fast link, the gap left at its default:

```
mkdir -p ~/.config/systemd/user/omacar-drivelog.service.d
cat > ~/.config/systemd/user/omacar-drivelog.service.d/balanced.conf <<'EOF'
[Service]
Environment=OMACAR_FASTBAUD=1
EOF
systemctl --user daemon-reload && systemctl --user restart omacar-drivelog
```

**Telemetry-first** — the fast link, a 10,000-line cap (~5s of bus) and a
240s gap, for roughly 95% telemetry and a ~5s CAN snapshot every 4 minutes or
so. It also ends a leg the moment the adapter overflows, with a 10s quiet
timeout, so a link that cannot keep up costs seconds rather than two minutes a
leg (without those two, one overflowing leg holds the port ~2 minutes, and
telemetry falls to about two thirds):

```
mkdir -p ~/.config/systemd/user/omacar-drivelog.service.d
cat > ~/.config/systemd/user/omacar-drivelog.service.d/telemetry-first.conf <<'EOF'
[Service]
Environment=OMACAR_FASTBAUD=1 OMACAR_DRIVELOG_LEG_LINES=10000 OMACAR_DRIVELOG_BETWEEN=240 OMACAR_DRIVELOG_END_ON_OVERFLOW=1 OMACAR_DRIVELOG_QUIET=10
EOF
systemctl --user daemon-reload && systemctl --user restart omacar-drivelog
```

Next to `fullbus.conf` above (both opt-ins, the default duty cycle): that
drop-in is the "fast link alone" row in the table, still at its own default
gap — `OMACAR_CAF0` changes what is recovered from each frame, not how long
a leg runs, so it composes with any of the drop-ins here. systemd reads
every `.conf` file in `omacar-drivelog.service.d/`, so two of them left in
place at once both apply — remove whichever is no longer wanted before
adding another, then `daemon-reload` and `restart` as above.

**Or let the driveway check choose.** `tools/driveway-check.sh`, parked with
the engine running, runs both test captures above, waits for the gauges to
really come back after each, picks one of three presets (`telemetry-first-caf0`,
`telemetry-first`, or `fallback` when the fast link fails), and with `--apply`
installs it as the single drop-in `driveway-preset.conf`, renaming the others
here to `.off`. `--dry-run` shows what it would do; `--remove-preset` undoes it.

## On the road — the recorder

Nothing to do. It watches `live.json`, records in twenty-minute legs, hands the
port back for ninety seconds between them so the gauges catch up, and ends a
leg when the frames stop, which is the ignition going off.

**In the first five minutes of leg one**, have the passenger run
`omacar drive status` once. It must say `capturing` with a leg number and an
advancing frame count. That is the whole check.

## On the road — the drive-mode byte

This is the open question: which byte moves when the ECON / SPORT / NORMAL
switch moves. Every capture on the tablet so far is one mode, so nothing can be
compared.

**Do not use `omacar listen marks` for this.** Use three separate captures.
Marks asks the driver to type a label at the moment of the switch, and the
seconds between the hand leaving the switch and the label landing are the part
that used to poison the answer. Three captures have no window boundaries at
all.

Parked, or at steady cruise with a passenger doing the typing:

```
omacar drive off                                        # the port is one thing

omacar listen capture --seconds 30 --save --note econ    # hold ECON, untouched
omacar listen capture --seconds 30 --save --note sport   # hold SPORT
omacar listen capture --seconds 30 --save --note normal  # hold NORMAL

omacar drive on
```

Then compare them. Adoption needs the captures named, so list them first:

```
omacar listen list                                      # the three names
omacar listen adopt 20260910-0812 20260910-0815 20260910-0818
```

A bare `omacar listen adopt` refuses and says why: it compares positions, so it
needs two or more captures.

Hold each position untouched for the whole thirty seconds. A switch moved
mid-capture is a capture that proves nothing.

**The port is one thing.** A twenty-minute leg holds it, and a capture started
during one fails after twelve seconds with "the daemon is holding the port".
`omacar drive off` first, always, and `omacar drive on` after.

## What can go wrong that nobody will notice

These are the failures that look like success, which is why they are listed:

- **`omacar drive on` does not start anything.** It clears a marker. If nothing
  is supervising it now says so and exits 1; before tonight it said "it will
  record again when the engine is running" regardless.
- **A stale status line.** `omacar drive status` exits 1 and prints in red when
  nothing has been written for two minutes. Trust it now — it was wrong for
  most of every leg until tonight, and an alarm that cries wolf once is an
  alarm nobody reads again.
- **A silent bus.** The status screen says "nothing heard yet" after two
  minutes of an open adapter with no frames. That is a real answer: the port is
  open and the car is not talking on any setting we know.

## Afterwards

```
omacar listen list                    what was captured
omacar drive status                   legs and frames for the run
omacar listen adopt NAME NAME NAME    compare them, propose a candidate
```

Adopt records a **candidate**, never more. Nothing a machine does can raise a
finding above what the evidence supports; only a person checking it against
something real makes it validated.

One known limitation, deliberately not fixed before the trip: adopting twice
writes over the first adoption. The captures are the evidence and adopt re-runs
in seconds, so run it once at the end rather than after each day.
