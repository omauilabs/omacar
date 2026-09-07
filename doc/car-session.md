# A car session

What to run, in what order, and what counts as a result. Written for the 2015
CR-Z and usable on any car; the Fit runs the same list with different numbers.

The scarce resource is car time, not engineer time, so every step below names
its cost and what it brings home. Nothing here writes to the vehicle. The one
step that could is called out and is not part of the plan.

**Before you set off:** the tablet is charged, `omacar doctor` names the
adapter, and the engine is running for anything past step 2. A key-on session
with the engine off ran a battery down far enough to trip an ABS warning once
already, which is why the voltage floor exists.

---

## 1. Baseline — 3 minutes

```
omacar doctor
omacar survey
omacar status
```

Expect the VIN, **ISO 15765-4 (CAN 29/500)**, and a `profile` line naming
`honda-crz-2015`. Zero stored faults is the correct answer on this car and has
been for its whole history here.

**Brings home:** proof the link is good before anything expensive depends on it.

---

## 2. The passive census — 5 minutes, and the biggest single unknown

This has never been run on this car. Everything the tool knows so far came from
asking questions; this is the first time it listens.

```
omacar listen capture --seconds 30 --save --raw --note "parked, ignition on"
omacar listen capture --seconds 30 --save --raw --note "engine running, parked"
```

**Expect:** tens of identifiers, most broadcasting at a steady rate, with some
bytes moving and some constant. On a hybrid, expect more traffic with the engine
running than with the ignition merely on.

**If it says the bus was quiet:** that is a real answer only if it also says
zero lines arrived. If lines arrived and none parsed, it says so and calls it a
fault in the tool — keep the capture and it can be read afterwards.

**Brings home:** the map. Every later question is cheaper once this exists, and
on a car nobody has published a signal set for, the census alone is new.

---

## 3. The switch hunt — 10 minutes, parked, and this is the ECON/SPORT answer

The drive mode selector is a dashboard state, not an emissions PID. It will not
be in mode 01 and there is no reason to expect a diagnostic identifier for it.
It is almost certainly a byte in a broadcast frame, and this finds it.

```
omacar listen marks
```

Then, holding each position for **at least five seconds** before typing:

```
position> econ
position> normal
position> sport
position> econ again
```

The fourth is not redundant. A byte that reads the same for `econ` and
`econ again` and differently for the other two is behaving like the switch; a
byte that merely differs across three windows might be a counter.

Run the same procedure for anything else with a discrete position, one at a
time, each in its own capture:

| control | positions to mark |
|---|---|
| drive mode | econ, normal, sport, econ again |
| gear selector | park, reverse, neutral, drive, park again |
| headlights | off, side, dipped, off again |
| brake pedal | up, down, up again |
| air conditioning | off, on, off again |
| parking brake | off, on, off again |

**Expect:** for each, either one or two named candidates, or an honest nothing.
Nothing is a real result and worth recording — it means the state is not on the
bus the adapter can see, which is itself worth knowing.

**Brings home:** candidates, on the same ladder as everything else. A candidate
becomes validated only when you have seen it hold across a second capture.

---

## 4. Service 0x21 — 3 minutes, and it has never been asked

The prospector's whole 0x21 path is built, tested, and has never been run
against this car with a correctly shaped 29-bit header. It is one byte of
identifier, so the entire space is 256 requests — about thirteen seconds of bus
time each.

```
omacar prospect --service 0x21 --headers 18DA03F1 --parked   # hybrid / battery
omacar prospect --service 0x21 --headers 18DA04F1 --parked   # hybrid / motor
omacar prospect --service 0x21 --headers 18DA10F1 --parked   # engine
```

`--parked` is not optional courtesy: a sweep holds the port for minutes and can
wake sleeping modules, so it refuses to run while the car reports road speed.

**Expect:** honestly, possibly nothing. The 0x22 sweep of 8,192 identifiers on
these same modules returned nothing, and that null is in the repository. But
0x21 is the service Honda-era modules commonly use instead, it costs minutes
rather than hours, and it has never been tried.

**Brings home:** either live hybrid identifiers, or a second recorded null that
saves the next person the same afternoon.

---

## 5. Mode 01, everything the car advertises — 2 minutes

```
omacar doctor
```

The `supported` line is the count of standard commands this car answers, read
from its own support bitmaps. The daemon polls a **hardcoded fifteen** of them,
so the gap between that count and fifteen is the standard telemetry this car
publishes and nothing has ever asked for. PID 0x5B, the hybrid pack charge, was
found exactly that way and had been one line away for the life of the project.

Write the count down. Closing that gap is a change to the poll tiers rather
than something to run in the car, and knowing the size of it decides whether it
is worth doing.

---

## 6. The drive — 20 minutes, and it is the only step that needs moving

Two things happen at once. Drive normally.

```
omacar daemon start          # the gauge and the sample record
```

Then, in a second terminal for a stretch of steady driving:

```
omacar listen capture --seconds 60 --save --raw --note "60 km/h, steady"
omacar listen capture --seconds 60 --save --raw --note "accelerating hard"
omacar listen capture --seconds 60 --save --raw --note "regen, off throttle"
```

Do not touch the screen while moving. Drive mode is the only screen that is
safe to have on, and it switches to itself.

**Brings home:** the frames that move with the car. A byte that tracks road
speed across a capture is a candidate for road speed on the broadcast bus, and
the same method finds assist and regeneration — which on an IMA car is the
number the dashboard has been showing all along.

---

## 7. Afterwards, at the kitchen table

```
omacar listen list
omacar listen show <capture>
omacar profile
```

Anything that held across two captures is worth writing into the profile as a
candidate, with a note saying which capture and which car. Nothing gets
`validated` from a capture alone — validated means somebody checked it against
something real, and the switch you were holding is exactly that, so a
discriminator confirmed twice by hand is the one case where it is honest.

## What is deliberately not in this plan

**Writing anything.** No clear, no actuator, no configuration. The write path
exists, is gated, and is not what today is for. Finding one controllable
identifier for the Tests screen would be a genuine win and it is a separate
session with the car on level ground and nobody near it.

**Guessing.** If a capture is ambiguous, the answer is another capture, not a
better story about the first one.
