# Battery health, phase 1: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** phase 1 of `doc/design/2026-10-05-battery-health.md`:
- the charge-only health measures and a per-drive `battery_health` table, with its verdict;
- the bus-capture miner;
- the Health card;
- a first report from the owner's real history.

**Architecture:**
- `lib/battery_health.py` splits the recorded samples into drives and computes three charge-only measures per drive. It keeps them in a `battery_health` table, rebuildable from samples, and judges the trend.
- It is reached by `omacar battery rebuild|report` and `GET /api/battery`.
- `tools/ima_mine.py` ranks bus bytes against the samples for the IMA hunt.
- `share/js/views/ima.js` gains a Health card, and Home's Hybrid pack tile gains one line.

**Tech stack:** Python 3 stdlib (sqlite3, statistics), and ES modules with no build step. Tests follow the repo's own patterns.

## Global constraints

- **Read-only towards the car.** Nothing here opens a serial port or imports `connect`, `elm`, `daemon` or pyserial. It reads only the database and the capture files.
- **The data:**
  - the samples table is `samples(t REAL PRIMARY KEY, rpm, speed, load, throttle, coolant, intake, maf, stft, ltft, timing, lphk, eff, soc)`;
  - `soc` is PID `0x5B` in percent: the pack's remaining life, which the app treats as its charge figure;
  - `speed` is km/h.
- **States:** every measure keeps the app's states (`measured`, `stale`, `candidate`, `undiscovered`). Only `measured` feeds the verdict.
- **The verdict:** exactly one of "Healthy", "Watch it", "Failing" or "Not enough data yet", always with a list of reasons. It reads "Not enough data yet" until at least 10 drives have measures.
- **Thresholds:** each carries a `source`. The phase-1 values are `"this app's threshold"` for all numbers, and `"owner reports, no Honda source"` for the meaning of recalibrations.
- **Private data never enters the repo:** the VIN-named database, the captures and the report output. Tests use synthetic data only.
- **Tests:**
  - Python suites are `test/<name>_test.py` scripts with local `ok`/`bad`/`check` helpers and a nonzero exit on failure, each added to `test/all.sh`;
  - JS tests are `test/js/<name>.test.js`, whose default export is `[[name, fn], ...]`, using `eq`/`ok` from `./assert.js`.
- **Running tests:** on the box, `rsync -a --exclude .git --exclude share/assets/private --exclude share/js/vendor/mediapipe ./ jmyers@omarchy:Projects/.omacar-test/meetup/`, then run there. Run `./test/all.sh` once before the final commit.
- **Commits:** in the repo's voice, with the trailer `Co-Authored-By: <the model you actually are> <noreply@anthropic.com>`. Never push.

---

### Task 1: The health model, its table and its commands

**Files:**
- Create: `lib/battery_health.py`, `test/battery_health_test.py`
- Modify: `bin/omacar` (the `battery` verb and its help line), `lib/api.py` (`GET /api/battery`), `test/all.sh`

**Interfaces:**
- Produces:
  - `drives(db) -> list[dict]`, each `{"t0", "t1", "rows": [sqlite Row]}`;
  - `measure(drive) -> dict` (`{"t0", "t1", "minutes", "window_lo", "window_hi", "window_width", "recals", "floor_share"}`);
  - `rebuild(db) -> int` (rows written);
  - `history(db) -> list[dict]`;
  - `verdict(hist) -> {"verdict", "reasons": [str], "drives": int, "measures": {...}}`;
  - `summary() -> dict`, which is what `/api/battery` returns and which never raises.
- `omacar battery rebuild` prints the number of drives; `omacar battery report` prints the verdict, its reasons, and a line per measure.

**Definitions (exact):**
- **A drive** is a run of samples with `soc` not null, where consecutive `t` differ by at most 600 s, with at least 300 s between the first and last row, and at least one row with `speed > 3`.
- **Driving rows** are those with `speed > 3`.
- **The window:** `window_lo` and `window_hi` are the 5th and 95th percentiles of `soc` over the driving rows (`statistics.quantiles(n=20)`, first and last cut), and `window_width = hi - lo`.
- **Recalibrations (`recals`):** the count of consecutive row pairs where `abs(Δsoc) >= 8` and `Δt <= 10` s. A run of qualifying pairs within 30 s counts once.
- **Time near the floor (`floor_share`):** the share of driving rows with `soc < floor + 3`. `floor` is the median `window_lo` of the up to 30 previous drives, or this drive's own `window_lo` when there are none.
- **The verdict over `hist`, oldest first:**
  1. With fewer than 10 drives: "Not enough data yet", with the reason `"<n> of 10 drives so far"`.
  2. Otherwise, `recent` is the last 10 drives and `base` is up to 30 before them.
  3. **The recalibration rate** is the sum of `recals` over the drives in a set. If `base` is empty, it compares against zero.
  4. **The narrowing** is `1 - median(recent widths) / median(base widths)`, and only when `base` has at least 5 drives.
  5. **Failing:** recalibrations in more than 5 of the 10 recent drives, or a narrowing of 0.35 or more.
  6. **Watch it:** a recent recalibration rate of 2 or more with a base rate of 0 (or 2 or more above the base rate, scaled to 10 drives), or a narrowing of 0.15 or more.
  7. **Otherwise:** "Healthy".
  8. **Reasons** name each rule that fired, with numbers, for example `"3 recalibrations in the last 10 drives, up from none"` or `"usable window 22% narrower than the 30 drives before"`. A Healthy verdict carries `"no recalibrations and a steady window over the last 10 drives"`.
- **The table** is `battery_health(t0 REAL PRIMARY KEY, t1 REAL, minutes REAL, window_lo REAL, window_hi REAL, window_width REAL, recals INTEGER, floor_share REAL)`. It is created with `IF NOT EXISTS` on `records.connect_rw()`, and `rebuild` deletes the rows and refills them in one transaction.
- **`summary()`:**
  1. It calls `rebuild` only when the newest sample is newer than `max(t1)` in the table (cheap).
  2. It returns `verdict(history)` plus `"measures"`. Window, recalibrations and floor time are `{"state": "measured", "series": [...]}`. Capacity, resistance, sag and blocks are `{"state": "undiscovered", "how": "the parked IMA session: tools/ima-session.sh"}`.
  3. It adds `"thresholds"`, each with its source.
  4. On any error it returns `{"error": str, "verdict": "Not enough data yet", "reasons": []}`.

- [ ] **Step 1: Write the failing tests** in `test/battery_health_test.py`. Use a scratch `XDG_STATE_HOME` and a hand-made sqlite file with the samples schema above, and point `records` at it the way `test/ima_test.py` does (read it for the pattern). Cases:
  - Two blocks of 1 Hz samples 20 minutes apart give 2 drives, and a 4-minute block gives none.
  - A drive with `soc` cycling 50→70→50 gives a `window_width` within 1 of 20.
  - A step of 60→70 within 2 s counts 1 recalibration. Three such steps within 20 s count 1. A slow 60→70 over 60 s counts 0.
  - 9 drives give "Not enough data yet" with `"9 of 10 drives so far"`.
  - 40 steady drives give "Healthy".
  - 30 steady drives followed by 10 with one recalibration each in 3 of them give "Watch it", with a reason containing "recalibrations".
  - 30 drives of width 20 followed by 10 of width 12 (narrowing 0.40) give "Failing", with a reason containing "narrower".
  - `rebuild` twice gives an identical table.
  - `summary()` on a missing database returns a dict with `"error"` and doesn't raise.
  - The module source contains none of `serial`, `import connect`, `import elm`, `import daemon`.
- [ ] **Step 2:** Run them on the box; they fail (no module).
- [ ] **Step 3:** Implement `lib/battery_health.py` to the definitions above.
- [ ] **Step 4:** Add the `battery` verb to `bin/omacar`, next to the other offline commands, running `"$OMACAR_PY" "$ROOT/lib/battery_health.py" "$@"`, with a help line. Add the route in `lib/api.py` next to `/api/ima`, with the same lazy import and never-raise pattern. Add the suite to `test/all.sh`. Run the suite and `test/guards_test.py` (it checks the help and the command list).
- [ ] **Step 5:** Commit.

### Task 2: The bus-capture miner

**Files:**
- Create: `tools/ima_mine.py`, `test/ima_mine_test.py`
- Modify: `test/all.sh`

**Interfaces:**
- Consumes: capture files `~/.local/state/omacar/captures/*.json`. Each is a dict whose `"started"` is an epoch float. `"raw"`, where present, is a list of `{"t": seconds since started, "id": hex string, "data": hex string}`; read `lib/listen.py` `Capture.save` to confirm the field names before relying on them. The samples come from the car's database (`records.connect()`).
- Produces: `python3 tools/ima_mine.py [--captures DIR] [--out DIR] [--top N]`. It writes `out/candidates.json` and prints a summary:
  - how many captures had raw frames, how many seconds of bus they cover, and how many overlap samples;
  - the top N per quantity.

**Method (exact):**
1. For each frame ID, and each byte index (and each adjacent byte pair, big-endian, as a 16-bit value), build a series of `(t_abs, value)`.
2. Resample the series and the samples onto 1 s bins over the overlap. In each bin, take the last frame value and the nearest sample within 2 s.
3. Score each series by the absolute Pearson correlation against three targets:
   - **charge:** `soc`;
   - **current:** the discrete derivative of `soc` over 5 s, as a proxy;
   - **voltage:** `throttle` with the sign inverted, as a proxy for sag.
4. A series needs at least 60 bins and at least 3 distinct values, or it is skipped.
5. Rank by score. Each candidate is `{"id", "bytes": [i] | [i, i+1], "target", "r", "bins", "state": "candidate"}`.
6. With no overlap at all, write an empty list and say exactly that, with how much raw data exists and that more drives with the adapter in will feed it. That's the expected outcome today: about 13,000 raw frames exist, almost all from 29 September.

- [ ] **Step 1: Write the failing tests** in `test/ima_mine_test.py`. Build synthetic captures and a synthetic samples database:
  - over 300 s, ID `1A6` byte 2 equals `round(soc * 2)` and ID `2B0` byte 0 is random. The `1A6`/2 series must rank first for `charge` with r > 0.95, and `2B0`/0 must not appear in the top 5;
  - no capture overlapping the samples gives an empty list and a message containing "no overlap";
  - every candidate's state is `"candidate"`;
  - the source contains no `serial`, `import connect` or `import elm`.
- [ ] **Step 2:** Run them; they fail.
- [ ] **Step 3:** Implement `tools/ima_mine.py`.
- [ ] **Step 4:** Run the tests, and add the suite to `test/all.sh`.
- [ ] **Step 5:** Commit.

### Task 3: The Health card, Home's line, and the roadmap

**Files:**
- Modify: `share/js/views/ima.js` (the Health card at the top), `share/js/views/home.js` or `share/js/readings.js` (Home's Hybrid pack tile gains "Health: <verdict>"; read how the tile is drawn and add the line where the tile's footer is), `share/js/core.js` (`api.battery: () => req("/api/battery")`), `share/css/app.css` (minimal, using existing tokens), and `doc/ROADMAP.md`
- Create: `test/js/battery.test.js`

**Interfaces:**
- Consumes: `GET /api/battery` (Task 1's `summary()`).
- Produces:
  - `export function healthCard(doc) -> HTMLElement` in `share/js/views/ima.js`;
  - `export function healthLine(doc) -> string | null` (null when `doc` has an error or no verdict).

**The card:**
- **Title:** "Pack health".
- **The verdict** as a tone pill: Healthy (ok), Watch it (warn), Failing (bad), Not enough data yet (neutral).
- **The reasons** as a list.
- **One row per measure:**
  - a measured row has its label and a sparkline of its series (use the existing `share/js/spark.js`), plus its latest value;
  - an undiscovered row says "Not measured yet" and the `how` text.
- **The footer** names each threshold's source, in small type.
- **Fetching:** the card fetches `/api/battery` when the IMA view mounts, alongside its existing fetch, and on failure shows nothing rather than an error.

- [ ] **Step 1: Write the failing tests** in `test/js/battery.test.js`:
  - `healthCard` with a Watch it doc renders the pill text "Watch it" and every reason;
  - an undiscovered measure shows its `how` text;
  - `healthLine` returns `"Health: Watch it"`, and `null` for `{error}`;
  - the card renders without throwing for "Not enough data yet" with empty series.
- [ ] **Step 2:** Run the JS suite on the box (`python3 test/js_test.py`); it fails.
- [ ] **Step 3:** Implement the card, `healthLine`, the Home line and `api.battery`.
- [ ] **Step 4:** In `doc/ROADMAP.md`, correct the line saying PID `0x5B` "has never once been polled". It answers on this car, and the recorder has stored it in `samples.soc` since the column was added. Check whether `tools/roadmap` regeneration (`omacar roadmap --check`) needs anything; `test/roadmap_test.py` runs in `all.sh`.
- [ ] **Step 5:** Run the JS suite and `./test/all.sh` on the box, then commit.

### Task 4 (the controller): the first real report

Done by the controller on the tablet, since the data is private and stays there:
1. Update the tablet's demo checkout to the branch head.
2. Run `bin/omacar battery report` and `python3 tools/ima_mine.py` against the real database and captures, without changing the live checkout.
3. Show the owner the verdict, the reasons and what the miner found, or an honest "not enough bus data yet".
