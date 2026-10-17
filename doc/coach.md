# Hybrid coach

A score for each drive, worked out after it is over, and one gentle hint. Code:
`lib/coach.py` (pure scoring, `omacar coach`), `share/js/coachview.js` (what the
screens say), `share/js/coachcard.js` (Home card), `share/js/views/coach.js`
(Vehicle → Hybrid → Coach). Tests: `test/coach_test.py`, `test/js/coach.test.js`.

Nothing here talks to the car. It reads `telemetry.db` read-only after a trip
has ended and writes one file of its own, `coach.json` (delete it any time).

## What this car gives it today

| Input | Where it comes from | Status |
|---|---|---|
| Speed | PID 0x0D, in `samples.speed` | **measured** |
| Throttle | PID 0x11, `samples.throttle` | **measured** |
| Engine rpm | PID 0x0C, `samples.rpm` | **measured** |
| Pack charge | PID 0x5B, `samples.soc` (the generic "hybrid pack remaining life") | **measured** (the percentage); what it is a percentage *of* is not known |
| Fuel per 100 km | `trips.lphk`, from air flow at a fixed air-fuel ratio | **estimated** |
| Brake pedal | not read | not available |
| Pack current, motor torque or power | never found (`doc/design/2026-10-10-hybrid-gauges.md`) | not available |

The daemon writes at most one sample a second, and fewer when the adapter is
busy. The scorer puts the drive on a one-second grid by linear interpolation
(rpm is held, not interpolated) and treats a hole longer than 10 s as a hole.

## What each number is

Every number on screen says which. A simulated drive says **Simulated** on every
number, whatever it would have been.

| Number | How it is worked out | Source |
|---|---|---|
| **Smoothness** (0-100) | RMS jerk of speed (change of acceleration) while moving, 70%; RMS rate of change of the throttle, 30% (speed alone if throttle was not read). 100 at 0.15 m/s³, 0 at 0.9 m/s³; throttle 100 at 5 %/s, 0 at 30 %/s | **measured** |
| **Gliding** (0-100) | Share of moving time at 20 km/h or more with the throttle under 5% and not slowing harder than 0.8 m/s². 100 at 35% | **measured** |
| **Regen capture** (0-100) | Energy back into the pack while slowing, divided by the kinetic energy shed. Energy shed is ½·m·(v₁²−v₂²) with an **assumed** 1,250 kg; energy recovered is the rise in the charge reading (from the start of the slowdown to 4 s after its end) times an **assumed** 5.8 Wh per point. 100 at 40% recovered | **estimated** (charge is measured, but the mass and the size of a charge point are assumed) |
| **Headline** (0-100) | Weighted mean of the sub-scores that exist: regen 40%, smoothness 35%, gliding 25% | follows its parts |
| Engine off at stops | Seconds stopped (under 3 km/h) with rpm under 100, as a share of seconds stopped. Shown, not scored: 6-speed cars need neutral and clutch up, CVT cars the brake held, and the engine must be warm | **measured** |
| Fuel against your usual | This drive's L/100 km against the median of this car's earlier drives within half to double the distance (at least 3 of them) | **estimated** |
| Hard slowdowns / launches | Runs of smoothed deceleration beyond 2.0 m/s² from at least 25 km/h, or acceleration beyond 2.2 m/s² | **measured** speed; it is "slowed hard", **not** "pressed the brake" |

Every threshold above is this app's own. None is a Honda figure. Regen capture
in particular will need tuning against real drives: the assumptions scale the
whole number, and a first real drive will say whether 40% is a fair "100".

## Never a zero for "don't know"

A sub-score with too little data is `null` with a reason ("not enough data: the
charge reading was missing while slowing") and shows as a dash. The headline is
then **partial** (worked out from the sub-scores that exist, and says which are
missing). With fewer than two sub-scores there is no headline. A drive under two
minutes moving or 0.8 km is not scored at all.

## The hint

One per drive, chosen by rule, most pressing first: slowed hard 3+ times;
launched hard 3+ times; almost no gliding on a drive of 5+ minutes; the engine
idling at stops; fuel 10% worse than usual. If none apply, "A clean drive" or
a note that too little was recorded. It is kept in the drive's record and
shown on Home and on the Coach screen **only when the app is not moving**
(`store.lockedAsMoving`). Home's card says only "Drive in progress" while
moving. The hint is not in the view at all while moving; it is not hidden by CSS.

## Where it shows

- **Vehicle → Hybrid → Coach**: the ring and number, the three sub-scores with a
  plain sentence each, engine-off-at-stops and fuel, the last 20 drives as a
  filled trend (tap a point, or the arrows, to open an earlier drive and its own
  hint).
- **Home card "coach"** (`m`, 4×2 landscape / 3×2 portrait): score and hint, parked
  only. Not in anyone's default layout; add it from Customize layout.
- **`/api/coach`**, read-only. It scores trips it has not seen, at most once a minute.

## Simulator

`omacar coach sim` writes 20 drives of three kinds (urban stop-and-go, highway,
mixed) with a random mix of careful and heavy-footed. They go through the same
scorer as real drives and are labelled Simulated in the file, on every drive and
on every screen. A simulated file is never overwritten by a real refresh;
`omacar coach clear` removes it.

## Not done

- Brake pedal, pack current and motor power are not read, so "late braking" is
  inferred from deceleration, and regen is inferred from the charge reading. If
  the parked IMA session finds pack current, regen capture can be measured, and
  its source changes from estimated to measured.
- Nothing here changes how the car is driven or drives anything. It never plays a sound.
