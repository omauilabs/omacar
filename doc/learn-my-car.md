# Learn my car

Vehicle → Live → **Learn my car**. A guided routine that labels the CAN bus
with ground truth: the owner does one thing at a marked moment (hold the brake,
turn the wheel, switch the lights) while the tablet listens, and the moments are
written down so a miner can find the bytes that move only then.

It exists because the IMA discovery work has a yardstick problem. The
correlators rank bytes against channels the car already reports (speed, RPM,
coolant). The brake switch, the steering angle, the lamps, the IMA's readiness
and assist state have no such channel, and nobody has published which bytes
carry them on a CR-Z. A person pressing a pedal on command is the cheapest
yardstick there is.

## What is read, and what is never written

**Read:** the bus, passively. The only adapter operation is the monitor
`lib/listen.py` already performs (`ATMA`), which does not transmit and does not
acknowledge a frame. Frames, their arrival times and the adapter's own
messages are kept; nothing else is asked of any module.

**Never written, by construction:**

- No request of any kind: not a PID, not a service 0x22 read, not a functional
  test, not a session change. `lib/learncar.py` does not import `elm`,
  `actuate`, `write` or `dtc`, and `test/learncar_test.py` checks that by
  reading its source (the AST), so a later edit that adds one fails the suite.
- None of the three hard refusals is approached: no write while moving, no
  emissions-defeat write, no reprogramming. They are all moot here because
  there is no write path to guard, and the session is parked-only anyway.
- It is not a cockpit write (`lib/serve.py` `COCKPIT_WRITES`): it takes the
  adapter, so a read-only cockpit cannot start it. The demo server stubs the
  route and runs nothing.

**What the owner gives up while it runs:** the adapter. Listening takes the
port from the gauge daemon for the length of one step (20 to 60 seconds) and
hands it back afterwards, so the gauges freeze during a step and recover
between steps.

## Parked only

`check_parked()` (pure, tested) decides. A session starts, and each step
begins, only if the daemon's last road speed is a **fresh zero** (under 90 s
old), nobody else holds the adapter (no hand-off in progress), and the speed is
readable at all. Anything unknown counts against listening. The same function
is used by:

- `POST /api/learncar` start (409 with the reason, before any process spawns);
- the runner, at `start` and at every step's "Start this step";
- the simulator, every tick, so a simulated car that starts moving ends the
  session at once (`OMACAR_LEARNCAR_SIM_SPEED=35` shows the refusal).

The screen also greys (never hides) Begin and the step buttons when
`store.lockedAsMoving`, with the reason beside them.

**A limit worth knowing:** on a real car, while a step is listening the tablet
holds the adapter, so the daemon's road speed stops updating. The speed is
therefore checked immediately before each step, not during it. Steps are short,
and the owner is asked to stay parked; a car that moves mid-step is not seen
until the step ends. This is not yet exercised on a car.

The two engine steps ("Start the engine, idle 20 s" and "Rev to 2,000 rpm and
hold") also need the owner to tap **Parking brake is ON** before Start lights.
They run in neutral or Park, parked.

## The routine

| # | Step | Label | Windows | Engine |
|---|------|-------|---------|--------|
| 1 | Ignition to II, engine off (ACC, then ON) | ignition | 1 | no |
| 2 | Brake pedal: hold, release, x3 | brake | 3 | no |
| 3 | Steering wheel: full left, centre, full right, x2 | steering | 4 | no |
| 4 | Headlights on / off, x3 | headlights | 3 | no |
| 5 | Hazards on / off, x3 | hazards | 3 | no |
| 6 | Clutch in / out, x3 (manual) *or* select N, back to P, x2 (CVT) | clutch / gear | 3 / 2 | no |
| 7 | Start the engine, idle 20 s, x2 (IMA assist and charge) | engine | 2 | yes |
| 8 | Rev to 2,000 rpm and hold, x3 | rev | 3 | yes |

Each step: a prep line, **Start this step**, a 5 s countdown, then the cues in
a big type with a timeline and a live **Listening** lamp (green, with a frame
count), then **Done, next** (or **Do it again**). **Abort** is always there.
The script is data (`STEPS` in `lib/learncar.py`), the state machine
(`Machine`) is pure and takes `now` and `env`, so both are tested without a
clock or an adapter.

A step is cycles of a *rest* window (the thing not being done) and an *act*
window (the thing being done). The miner needs both. A `lag_s` per step is cut
from the start of each window after the first, so engine cranking is not filed
under "engine off".

## What it writes

Per step, beside the other captures in `$XDG_STATE_HOME/omacar/captures/`:

- `learncar-<step>-<timestamp>.json`: a normal `listen` capture saved **raw**
  (`started`, `raw[{t,id,data}]`, census, marks named `label:state`). Existing
  tools that read captures (`tools/ima_mine.py`, `omacar listen`) read it as
  they read any other.
- `labels.jsonl` (one file, appended, never rewritten), one line per window:

  ```json
  {"v":1,"capture":"learncar-brake-20261017-101500","label":"brake","step":"brake",
   "state":"held","active":true,"t0":1792344910.2,"t1":1792344915.1,"simulated":false}
  ```

  Times are absolute (the capture's frame times are `started + t`). `active`
  says whether the thing was being done; the miner compares the two kinds.

The simulator writes the same shapes under `captures/sim/`, with
`"simulated": true`, and results to `learncar-results-sim.json`. A real run
writes `learncar-results-car.json`. The loader refuses to mix them.

## How the ranking works

`correlate.rank_labelled(runs)` (pure; `correlate.load_labelled()` reads the
files). For each label, over every window of that label from every capture
(sessions pool, so confidence grows each time it is run):

1. **Quiet outside.** A byte must take at most 3 distinct values across the
   label's *rest* windows. Rolling counters, checksums and wheel-speed noise
   fail this, which is why they are never offered even though they "change
   inside the window".
2. **Novel inside.** In an *act* window, at least 40% of the frames carry a
   value the rest windows never showed (40% so a flashing lamp, novel about half
   the time, still counts).
3. **Every window.** Hits over windows is the confidence, reported as a fraction
   ("18/18 windows"). Strong means every window and at least three of them;
   fewer is a **hint**, and says so on screen. Under 75% it is not listed.
4. **Specificity.** If the byte also leaves its *own* rest values during some
   other label's action, it is listed after the specific ones, with "Also moves
   during ..." (a byte that moves whenever anything happens is "the car is
   busy", not "the brake").
5. Frames within 0.4 s of a window edge are dropped (reaction time), and an
   identifier heard fewer than 5 times in a window says nothing.

Output per candidate: identifier, byte (or a 16-bit pair of adjacent analog
bytes), `switch` with its rest and active values (`00 -> 40`) or `analog`, hits,
windows, where else it moves. A sentence is built from it: *"0x17C byte 3
changes only while the brake is held, 18/18 windows"*.

Run it any time: `omacar learncar mine [--sim]`.

## How results feed the IMA work

They are **candidates, never findings**, on the same ladder as everything else:
a person who watched the car has to promote one.

- **IMA readiness** (step 1) and **assist and charge** (step 7) point at the
  identifiers whose bytes change when the ignition goes to II and when the engine
  starts. Those identifiers are where `tools/ima_mine.py` should look first for
  charge, current and voltage, instead of scoring every byte of every id.
- **Rev** (step 8) separates throttle- and load-driven bytes from the
  engine-running ones, which is the split the IMA assist/regen work needs.
- **Brake, steering, lamps, clutch** give ground-truth switches to *exclude*:
  a byte already explained by the brake switch is not the IMA's.
- A confirmed byte goes into the profile as a `candidate` entry the usual way,
  and `omacar listen marks` can re-test it on the car.

## What is verified, and what is not

Verified in the sandbox: the step script, the state machine, the parked-only
refusal (start, go, mid-step), the ranking on synthetic frames (it finds a switch,
a 16-bit angle and a flashing lamp, and rejects counters, checksums, noise and
constants), the loader, the whole routine end to end against the synthetic bus,
the API, and the screens.

**Not verified: any of it on a CR-Z.** The synthetic bus is made up (0x17C and
the rest are placeholders). Unmeasured on the car: whether the bus is awake in
ACC (step 1's rest window may be silent, in which case it says so and ranks
nothing), whether the monitor keeps up through a 60 s capture (`listen` has
overflowed on this car before; see `doc/drive-day.md`), whether `OMACAR_CAF0`
is needed to see the short frames the adapter censors, and how long the gauge
daemon takes to come back between steps.

## Files

`lib/learncar.py` (script, machine, simulator, runner, API calls),
`lib/correlate.py` (`rank_labelled`, `load_labelled`), `share/js/learncar.js`
(pure view logic), `share/js/views/learncar.js`, `share/css/learncar.css`,
`test/learncar_test.py`, `test/js/learncar.test.js`.

```
omacar learncar steps        the script and how long each step takes
omacar learncar run --sim    a Simulated session (OMACAR_LEARNCAR_SPEED=N plays it N times faster)
omacar learncar mine [--sim] rank everything labelled so far
omacar learncar status       what the app is showing
```
