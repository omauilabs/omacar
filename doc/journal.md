# Trip journal

Vehicle → Care → **Journal**. One card per drive, days as sections, newest
first. Tap a drive for its numbers, every note, and which sources were
listening. `lib/journal.py` builds the entries; `share/js/views/journal.js`
lays them out; `share/js/journal.js` is the pure grouping and filtering.

## One line per drive, by rules

    38 min, 24 mi, 51 mpg · stale air once · 2 K-band alerts

Not AI: each clause is a rule over a number that was recorded.

| Clause | Rule |
|---|---|
| duration, distance, economy | the drive log row (`trips`); left out when unknown, never zero |
| stale air once / twice / n times | cabin CO₂ over 1,500 ppm for 5 min, clearing under 1,400 (envnode's own rule) |
| 2 K-band alerts, 1 laser alert | the radar detector's encounter log, by band, inside the drive |
| fridge door alert, fridge ran warm | lid opened; more than 4 °C over set point for 10 min |
| coach 82 | `coach.read_public()` drive whose start matches (within 5 s) |
| 1 clip | `clips.list_incidents()` within 30 s of the drive |
| fill-up 7.9 gal | `fuellog.journal_items()` if that module exists |

## Sources are optional, and silence has three meanings

Every source is import-or-skip. For each drive an entry records, per source:

- **absent** — module not here. Nothing is said.
- **gap** — here, but it has nothing covering this drive (the radar log keeps
  the last 50 encounters, the air history a day, the fridge observer only
  since it started). Shown as "not recorded for this drive", never as "no
  alerts": a quiet drive and an unwatched drive are different.
- **none** — it was listening and had nothing to report.
- **found** — a clause and a badge.

A source whose own state says it is a simulator is treated as absent in a real
journal. The journal's own simulator writes a separate file.

## Where the data lives

`$XDG_STATE_HOME/omacar/`: `journal.json` (frozen entries, notes, when the
observer started), `journal-events.jsonl` (fridge lid and warm edges),
`journal-sim.json` (the simulated week). A drive is **frozen** ten minutes
after it ends, so the sources' short memories cannot lose it; entries store
structured findings, not text, so units follow the owner's setting.

The fridge keeps no history of its own, so `omacar-journal.service`
(`omacar journal run`) watches its state and records the edges. Without that
service, fridge notes simply do not appear (the source reads "not recorded").

## Adapters for modules still being built

`coach` and `clips` are read through their published `read_public()` /
`list_incidents()`. Fuel is read through an optional
`fuellog.journal_items()` returning `[{"t", "litres"}]`: **an assumption**, to be
matched to whatever the fuel log ships. Each adapter is wrapped; a missing or
changed module leaves its source "absent".

## Place names: opt-in, off, not available

No GPS in the tablet, so entries carry **no location**. When `lib/location.py`
exists, `place_name(t)` start/end names become available behind a switch
(`omacar journal places on`, or the switch on the Journal screen). It is off by
default, refuses to turn on while no location module exists, and with it off the
journal never calls the location module.

## Notes, and driving

The owner may add a note to a drive (typed, 280 characters, plus one-tap
words). Notes and filter chips are parked-only (`store.lockedAsMoving`): greyed
with "Parked only", never hidden. Voice is not built. Search is by chip only
(All, then a chip per kind that some drive has, then Noted); no keyboard.
`POST /api/journal/note` is a cockpit write (harmless); the places switch is not.

## API and CLI

- `GET /api/journal?n=60&imperial=1` — entries (with `line`, `badges`, `notes`,
  `checked`), `settings`, `source` (`real` | `sim`), `simulated`.
- `POST /api/journal/note` `{t0, text}`, `POST /api/journal/settings` `{places}`.
- `omacar journal status | run | sim | places [on|off] | note <t0> <text>`.
- `omacar journal sim` writes a week of drives, every entry flagged and shown as
  **Simulated**. Real drives, when there are any, take over from it.

## Tests

`python3 test/journal_test.py` (assembler with all, some and no sources; missing
data; gap vs none; hysteresis; fridge edges; places switch; simulator; freezing)
and `test/js/journal.test.js` (days, chips, filters, lock, quick notes).
