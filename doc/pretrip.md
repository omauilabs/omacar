# Ready for a long drive? (pre-trip check)

Vehicle, Care, **Pre-trip**; and one tap from the power-on (Begin) screen. A
parked tool: while the car moves (or is handing over) it shows one line,
"Available when you stop", and nothing else.

## What it checks

Each check is a lamp (ok / warn / bad / unknown) and one line.

| Check | From | ok | warn | bad |
|---|---|---|---|---|
| Trouble codes | `/api/snapshot` faults | none active | pending only | any stored or permanent |
| Readiness monitors | snapshot readiness | all supported complete | some not yet run | |
| Service due | the existing schedule (`lib/book.py`) | nothing within 500 mi or 30 days | due soon, or overdue | overdue brakes or tyres |
| Service record | maintenance gaps (below) | all recorded once | | |
| 12 V battery | live system voltage | parked 12.4 V+, running 13.2 V+ | 12.4 / 13.2 | 11.8 / 12.6 |
| Hybrid battery | `/api/battery` verdict | Healthy | Watch it | Failing |
| Tyre pressures | `lib/tpms.py` if it exists | lowest 28 psi+ | under 28 | under 22 |
| Fuel for the trip | tank, fuel %, year average economy | range 1.25x the trip | tight, or fill up on the way | |
| Walk-round | ticks by the owner | all ticked | | |

**Unknown is never ok.** No data, an unread list, nobody having looked: grey
lamp, and it counts as a note. The verdict (`verdict()` in `share/js/pretrip.js`,
pure, tested):

- any bad: **Fix first**
- otherwise warns and unknowns are notes: **Ready, with N notes**
- no notes: **Ready**
- nothing known to be ok at all: **Not enough to say yet**

So on a car with no tyre source and an unticked walk-round the best reachable
answer is "Ready, with 2 notes". That is deliberate.

Thresholds (500 mi / 30 days, the voltages, 28 / 22 psi, which service items
are safety items) are this app's judgement, not Honda's, and live as constants
at the top of `share/js/pretrip.js`. The door-pillar placard is the authority
for tyre pressure; the screen says so when tyres are not measured.

## The trip distance

A numeric stepper, no keyboard: `-` / `+` in 25-unit steps (miles or km as the
app is set), cleared at 0. Stored in km in `pretrip.json`. Fuel range is the
app's own estimate (litres in the tank over the year's average economy), never
an ECU figure. No distance entered means the fuel row is unknown.

## The walk-round

Fluids, wipers, lights, tyre condition, spare: ticked by the owner, persisted
per trip (`New trip` clears them and the distance; service dates are kept).
The ids are in `lib/pretrip.py` `CHECKLIST` and are what is stored.

## Maintenance gaps

`lib/book.py` starts a schedule with every item "last done" at a placeholder
(today minus its interval). That is only so a countdown has somewhere to start.
Now `log_service()` also writes a row in a new `service_recorded` table, and
`lib/pretrip.py gaps()` lists every schedule item without one: "Never recorded,
tap to set".

Tapping an item offers Today, 1 month, 3 months, 6 months, 1 year, 2 years ago.
Today logs at the current odometer (existing `log` action). An earlier date uses
the new `POST /api/service {action: "record", item, days_ago}`: the odometer at
that date is unknown, so the distance interval waits for the next "Done today"
and the time interval counts from the date. Items that are gaps are excluded
from "Service due", because their dates are guesses.

A book that predates the table shows every item as a gap once; one tap each.
Items added by hand are gaps until first logged.

## Files

- `share/js/pretrip.js` pure judging; `share/js/views/pretrip.js` the screen;
  `share/css/pretrip.css`
- `lib/pretrip.py` the trip, ticks, gaps and the optional tyre source;
  `GET/POST /api/pretrip`; `omacar pretrip status|gaps`
- tests: `test/js/pretrip.test.js` (verdict and every check), `test/pretrip_test.py`

## Sim and demo

The simulator's book marks everything recorded except the 12 V battery, so a
gap shows. Rows that come from the car's data carry **Simulated** when the
record is the simulator's. The demo has no trip, ticks or gaps (its service
book is read-only) and no tyre source.

## Tyres

`lib/tpms.py` does not exist in this branch. `lib/pretrip.py tpms()` imports it
if it appears and calls `read_public()`, and the check accepts `tyres` or
`wheels` as a list or object of numbers or `{psi|kpa|pressure_kpa|bar}`. If
that module lands with a different shape, adjust `tyrePressures()`.

Nothing here writes to the car.
