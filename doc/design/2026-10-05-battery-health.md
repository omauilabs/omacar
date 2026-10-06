# Battery health: the IMA pack, judged from what the car actually says

The first part of the IMA work. The owner's order (2026-10-05) is battery health, then live hybrid gauges, then understanding warnings. Each gets its own design. This one covers battery health: an honest verdict on the pack with its trend, plus block-level detail if the car exposes it.

## What is known today

- **Charge is measured.** Generic PID `0x5B` (hybrid battery remaining) answers on this car. The drive recorder has stored 17,592 readings in `samples.soc`, between 43.5% and 77.3%. (`doc/ROADMAP.md` still says `0x5B` was never polled; that line is out of date and is corrected by this work.)
- **Nothing else from the pack has been read.** Not voltage, current, temperature or block voltages.
- **Both hybrid controllers answer** on 29-bit headers: `18DA03F1` (hybrid / battery) and `18DA04F1` (hybrid / motor).
  - Their fault catalogues are captured: 49 codes and 26 codes.
  - Service `0x22` has answered only in `F100`–`F19F`.
  - Service `0x21` has never been asked with a correct header.
- **91 passive bus captures** sit on the tablet (`~/.local/state/omacar/captures/*.json`), from the drives of 7 and 29 September. They have never been searched for hybrid frames.

## Decisions (owner, 2026-10-05)

- **The outcome:** a verdict with its trend first, then block detail if the car will give it up.
- **The approach:** discovery-led. Nothing is built on guesses or on other owners' reports.
- **Collection while driving:** decided after the parked session, with the real identifiers in hand. The two options are passive-only, or proven read-only questions to the battery controller about once a second. Until then, collection on the road stays passive, as today.

## 1. Discovery

1. **Mining the recordings: `tools/ima_mine.py`, read-only, no car.**
   - For each capture, it lines up the broadcast frames with the drive samples recorded in the same seconds: speed, rpm, throttle and the `0x5B` charge.
   - For every frame ID, and every byte or 16-bit pair in it, it scores how well the value tracks each of three quantities:
     - **charge:** a slow drift that follows `0x5B`;
     - **current:** swings with throttle and braking, one sign under assist and the other under regen;
     - **pack voltage:** sags under assist and rises under regen.
   - It writes a ranked candidate list to `~/.local/state/omacar/ima-mine/`, as JSON plus a PNG plot of each top candidate against the samples.
   - A candidate is never shown as a reading (`lib/profile.py`'s rule).
2. **The parked session: `tools/ima-session.sh`, already built.**
   - The owner is in the car, parked, handbrake on, engine idling, for about 20 minutes. The script asks:
     - service `0x21` on both hybrid controllers, with the 29-bit header;
     - service `0x22` above `0x0FFF`, in short bursts;
     - the raw fault catalogues again.
   - While it runs, the owner blips the throttle in neutral a few times, so current and voltage bytes have something to move with.
3. **Confirmation.** A candidate becomes a measured quantity only when two independent checks agree:
   - **current:** its sign matches assist and regen, and its integral over a drive matches the change in `0x5B` charge, within 15%;
   - **voltage:** it falls when current rises and recovers when current returns to zero;
   - **block voltages:** they come as a set of similar values whose sum is close to the pack voltage.

   A confirmed quantity goes into the car's profile with its evidence: the capture or session files, and the check it passed.

## 2. The health model: `lib/battery_health.py`

The model runs **after each drive**, offline, on that drive's recorded samples, and never in the car's hot path. Each drive adds one row to a new table, `battery_health`, in the car's own database. The table is derived from samples already recorded, so it can be rebuilt from history at any time (`omacar battery rebuild`).

**The measures, by what they need:**

| Needs | Measure | Meaning |
|---|---|---|
| Charge (today) | Usable window | The 5th to 95th percentile of charge during driving. A narrowing window suggests lost usable capacity. |
| Charge (today) | Recalibrations | Abrupt charge jumps of 8 points or more within 10 s, with no matching driving event. Owners of Honda IMA cars widely report that frequent jumps go with a weakening pack. That is a community observation, with no Honda source, so it's marked as such on screen. |
| Charge (today) | Time near the floor | The share of driving minutes below the window's lower bound plus 3 points. |
| Current + charge | Usable capacity | Ah per percent of charge, from current integrated over swings of at least 10 points. |
| Current + voltage | Internal resistance | ΔV ÷ ΔI at assist onsets (a current step of at least 10 A within 1 s), as the median per drive. Compared like-for-like, by charge band (and pack temperature once known). |
| Current + voltage | Sag at full assist | The lowest pack voltage seen during the drive's strongest assist. |
| Block voltages | Spread | The highest minus the lowest block voltage, at rest and under load. |
| Block voltages | Lagging block | Which block is lowest, and whether the same block keeps lagging. |

**The verdict:** "Healthy", "Watch it" or "Failing", always with the reasons.
- It reads "Not enough data yet" until at least 10 drives have measures.
- It comes from trends across drives (the last 10 drives against the 30 before them), never from one drive.
- A measure counts only in the measured state. Stale, candidate and undiscovered measures never feed the verdict.
- **Starting thresholds:**
  - **Watch it:** recalibrations rising to 2 or more per 10 drives from zero; the usable window narrowing by 15% or more; resistance up 20% or more; block spread at rest above 0.3 V.
  - **Failing:** recalibrations in most drives; the window narrowed by 35% or more; resistance up 50% or more; one block lagging by 0.5 V or more under load in most drives.
- Every threshold carries its source. Where no published source exists, the screen marks it as "this app's threshold", not Honda's.

## 3. The screen and the safety rules

- **The Health card** goes at the top of Vehicle → Hybrid → Battery (`share/js/views/ima.js`). It shows:
  - the verdict and its reasons;
  - a trend line for each measure across drives;
  - once block voltages exist, one bar per block, with a block that keeps lagging marked;
  - each measure in its state, and an undiscovered one names the session that would find it.
- **Home's Hybrid pack tile** gains one line, "Health: <verdict>", which opens the card.
- **Safety:**
  - discovery is read-only;
  - the parked session runs only as `ima-session.sh` already does: parked, engine idling, the voltage floor enforced, no long key-on-engine-off sweep, never `omacar write`, a clear, or the services 0x10, 0x11, 0x14, 0x27, 0x28, 0x2E, 0x2F, 0x31, 0x34, 0x36, 0x37, 0x3E or 0x85, and never `ATCSM0`;
  - the tools read only recorded files;
  - private data (the VIN-named database and the captures) never enters the public repo.

## 4. Testing

- **`ima_mine`:**
  - with synthetic captures holding a planted byte that tracks a known current, the planted byte must rank first;
  - with a random byte, it must not reach the top 5;
  - nothing is ever promoted by the tool itself.
- **The health model:**
  - synthetic histories with known fade (a narrowing window, rising recalibrations, rising resistance, one lagging block) must get the right verdict and reasons;
  - 9 drives must give "Not enough data yet";
  - a stale or candidate measure must change nothing;
  - a rebuild must give the same table.
- **Real data:** a first report from the owner's 17,592 charge readings, shown to the owner, using only the measures charge alone supports.

## 5. Phases

1. **Now, without the owner:**
   - `ima_mine` run on the existing captures;
   - the charge-only measures and the `battery_health` table;
   - the Health card;
   - a first report from history;
   - correcting the roadmap's `0x5B` line.
2. **After the parked session:** confirm current and voltage, then add capacity, resistance and sag.
3. **If the car exposes block voltages:** spread and the lagging block.

Then live hybrid gauges, then understanding warnings, each with its own design.

## Not in this design

- No writes to the car of any kind, including cell balancing or a pack reset.
- No hardware added inside the hybrid battery.
- No use of identifiers reported by others until they are confirmed on this car.
