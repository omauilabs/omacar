# Fuel log

What the pump says the car gets, beside what the car says it gets. Built into
the app (not a plugin; `examples/fuel-log` stays as a sample of the plugin
mechanism, and the built-in `omacar fuel` takes precedence over it).

## The two halves

OBD-II gives a fuel *level* (PID 0x2F, percent of tank) and nothing that
survives a fill-up: no gallons, no price. So:

- **The car supplies when.** `lib/fuel.py run` (`omacar-fuel.service`) samples
  `live.json` every 2 s and looks for a fill-up.
- **The owner supplies how much.** Once parked, a calm card asks
  "Fill-up: how much?" with a stepper for gallons (0.1 steps) and one for the
  price per gallon (\$0.01 steps), a "Filled to full" switch and Skip. Keys
  hold-to-repeat (slow, fast, then in strides); there is no keyboard. Metric
  is litres (0.1) and \$/L (\$0.001).

## Detection (pure, `fuel.detect`)

A fill-up is a rise of at least **15 percentage points** between two levels,
each the **median of 30 s of readings**, with the car stopped at both ends:

- across an ignition cycle (no reading for more than 45 s), the last 30 s
  before against the first 30 s after; or
- while standing still with the ignition on, the first 30 s of the stretch
  against its settled tail.

A dropout while moving, a fall, a 10-point top-up, hills and sloshing (the
median ignores a few wild readings), and a slope reading a few points high are
all not fill-ups. A detection is held back until its 30 s window has closed.
Two jumps within 15 minutes are one stop. It records the time, both levels,
the odometer if known, and the gauge's estimate of litres (rise x tank size),
which pre-fills the stepper.

## Economy (pure, `fuel.intervals`)

Real mpg is **full to full**: distance between two full fill-ups over the
litres put in at the end plus any part-fills between. A part-fill cannot end a
measurement. A fill-up with no quantity (skipped or unanswered) in the middle
drops that measurement, with a reason, and the next full fill-up starts a new
one; an unknown odometer, under 20 km, or an implausible figure (2 to 40
L/100 km) also drop it. Nothing is guessed.

Each fill-up stores the car's own integration (`car_km`, `car_litres`, from the
sample stream's instant economy) since the fill-up before. Summed over a
measurement it gives the **car's estimate**, shown beside the pump and labelled
so, only where it covers at least 60% of the distance. Cost per mile, the
distance-weighted average and calendar-month spend (every logged fill-up,
part or full) come from the same pass.

## Where it lives

- Log: table `fuel_fills` in the per-vehicle telemetry DB (next to the drive
  history), metric on disk. Rows are `pending`, `logged` or `skipped`;
  `manual` and `simulated` are flags.
- `/api/fuel`: GET the whole picture; POST `{op: answer|skip|add|remove}`.
  It writes only the log and never reaches the car. It is not in
  `COCKPIT_WRITES` (a read-only cockpit cannot log), and the demo stubs it.
- UI: Vehicle, Care, **Fuel** (chart of economy per tank, filled line, the
  car's estimate dashed, one column per fill-up with its tank gauge); Home card
  `fuel-log`; the calm card (`share/js/fuelprompt.js`).
- No sound, ever. The card shows only while `store.lockedAsMoving` is false and
  no drowsiness alert is up, comes back at the next stop, and is put down for
  good only by Skip.

## Commands

    omacar fuel status | list
    omacar fuel add 9.4 3.79 [--partial]   by hand (units follow the app)
    omacar fuel run                        the watcher
    omacar fuel sim [--pending] [--clear]  ten simulated fill-ups over three
                                           months; Simulated on every screen

When the log holds any real rows the simulated ones are ignored by the app.

## Not verified on a car

The detector is tested on synthetic traces. Whether the CR-Z's FUEL_LEVEL
settles within 30 s of ignition-on, or lags behind a fill, is unmeasured: if it
lags, `WINDOW_S`/`JUMP_PCT` in `lib/fuel.py` are the two numbers to tune.
