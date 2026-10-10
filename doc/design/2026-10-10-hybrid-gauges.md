# Live hybrid gauges: a glance bar for driving and a live page for the pack

The second part of the IMA work (battery health came first; understanding
warnings comes after). Capability `hyb.ima-live`. Owner's decisions,
2026-10-10: both a driving glance and a detailed page; until real pack current
is found, the glance shows an estimate, clearly labelled; one shared module in
the browser (approach A).

## What is known today

- **Charge is measured:** PID `0x5B`, `HYBRID_BATTERY_REMAINING`, on screen as
  "Hybrid pack".
- **Pack voltage, current and temperature have never been read.** The parked
  IMA session (`tools/ima-session.sh`) is the next attempt.
- **The "IMA" reading** (Charging / Assist / Steady) guesses direction from the
  charge drifting over 12 s (`share/js/readings.js`, `imaNow`).
- **The demo world** simulates assist and regen and publishes
  `demo.ima = {state, kw}` (`lib/demoworld.py`).
- **Validated readings already reach the screen:** a profile entry at
  confidence `validated` is polled by the daemon (`lib/signals.py`), its
  `{id, name, unit}` is published in the snapshot's `signals`, and its value
  appears in the live sample under that id.

## 1. The module: `share/js/hybrid.js`

One function turns a live sample (plus the car's snapshot) into one picture:

```
hybridNow(sample, car, now) -> {
  flow:   { value: -1..+1 | null, source, state, label },
  charge: { value, source },
  volts, amps, temp: { value, source } | { source: "not found yet" },
}
```

`source` is one of `measured`, `estimated`, `simulated`, `not found yet`.
Positive flow is assist, negative is regen (charging).

**Where flow comes from, first match wins:**

1. **Measured.** A validated signal whose `quantity` is `ima.amps` has a value:
   flow = amps / the pack's assist limit (`PACK_AMPS_MAX`, 100 A until the
   parked session says otherwise), clamped. Its sign is the profile's.
2. **Simulated.** The sample is the demo (`simulated: true` and `demo.ima`
   present): flow = `kw / MOTOR_KW`, signed by `state`. Labelled simulated.
3. **Estimated**, from what the car sends today:
   - **stopped** (speed under 3 km/h): 0, state `stop` ("Auto stop" if rpm is 0);
   - **regen**: throttle under 5% and slowing harder than 0.4 m/s²; magnitude
     `min(1, decel / 2.0)`;
   - **assist**: throttle over 30% and speeding up harder than 0.4 m/s²;
     magnitude `min(1, (throttle - 30) / 70)`, at least 0.1;
   - **otherwise** cruise: 0, or -0.1 when charge rose by more than 0.4 points
     over the last 12 s.

   Acceleration is the speed change across the last 2 s of samples. 0.4 m/s²
   is the demo world's `ASSIST_MPS2`, so the demo and the estimate agree.

**Rules:**
- A hand-off (`sample.handover`) clears the speed and charge history and hands
  back the last result, drawn paused, exactly as the existing IMA reading does.
- The estimate is never stored or sent. It exists only on screen.
- Volts, amps and temp come only from validated signals with quantity
  `ima.volts`, `ima.amps`, `ima.temp`. Anything else is "not found yet".
- In the demo, volts, amps and temp are simulated from `demo.ima` and labelled
  simulated.

**The one server change:** `lib/signals.py` passes an optional `quantity`
through `validated()` and `catalogue()`, so a profile entry can say which pack
quantity it is. Nothing is polled that was not polled before.

## 2. The screens

- **Drive screen: a "Hybrid flow" tile** (`readings.js` entry `flow`). A new
  gauge kind, `split`: a bar centred on zero, assist filling one way and regen
  the other, offered only to readings whose scale says `center: true`. As a
  number it reads "Assist 40%" / "Charging 25%" / "Steady". An estimate is
  drawn hatched with a large "EST" tag; measured is solid with no tag;
  simulated says "SIM". The owner's layout is not changed; the demo layout
  gains the tile.
- **Home:** the Hybrid pack card gains a thin split bar under the charge.
- **Vehicle → Hybrid: a "Live" section first**, above the Health card. The
  flow bar across the top, then four dials: charge, voltage, current,
  temperature, each with the last minute as a sparkline. An unfound reading is
  a dimmed dial with no needle: "Not found yet — the parked IMA session will
  look for it".
- **The "IMA" reading** is fed from `hybridNow`, so the tile and the bar never
  disagree.

## 3. Safety and testing

- No new request to the car of any kind; the estimate is display only; the
  drive-screen editor still opens only when stopped; demo values stay inside
  the demo's own data.
- **Unit tests** (`test/js/hybrid.test.js`, the existing headless-Chromium
  runner): braking gives regen, hard throttle gives assist, a stop gives 0, a
  hand-off clears history, the demo is labelled simulated, a validated
  `ima.amps` signal outranks the estimate, an unvalidated or quantity-less
  signal stays "not found yet". Python: `quantity` passes through `signals`.
- **Screenshots** of the tile, Home and the Live section in demo mode, light and
  dark, shown to the owner before merging; then on the tablet.

## Where the build departed from this (2026-10-10)

- **No new gauge kind.** The existing Bar already fills from zero on a scale
  that crosses it (it was built for fuel trim), so `flow` uses a -100..+100
  scale on the Bar, which gains the hatched estimate fill and an EST / SIM tag.
- **Home gets a "Hybrid flow" card** in the card catalogue
  (`share/data/home-cards.json`) instead of a bar inside the Hybrid pack card.
  It is not added to anyone's layout.
- **The "IMA" reading stays as it is.** It reports the measured charge moving,
  which is a real signal, and its hand-off behaviour is pinned by
  `test/js/handover.test.js`. The two can briefly differ (the estimate reacts
  to braking at once; the charge moves seconds later); the flow bar is the one
  meant for glancing.
- **The demo's drive layout is the app default**, which this does not change;
  the tile is added to the demo by hand in the editor before the meetup.

## Not in this design

Polling the car for pack voltage, current or temperature (waits for the parked
session and the collection-while-driving decision), block voltages, and any
change to battery health.

## Tasks

1. `lib/signals.py` quantity pass-through, with its test.
2. `share/js/hybrid.js` and `test/js/hybrid.test.js`.
3. The `split` gauge kind (`share/js/gauges.js`) and the `flow` reading, with
   the IMA reading moved onto `hybridNow`.
4. The Live section in `share/js/views/ima.js` and the Home card's bar; the
   demo layout gains the tile.
5. Screenshots, full test run on the box, PR, CI.
