# OmaCar foundation: the new look, the new frame, Home and Vehicle

Step 1 of 5 in the OmaCar reimagining. Owner's mockups of 2026-09-27 are the reference (kept out of git in `doc/design/mockups/`, see open question 4). The later steps are Cameras, Navigation, Agent and Work, and OmaMenu, each with its own design doc.

## Problem

The app is organised around the tool's internals. Its five tabs, Car / Faults / Data / Care / AI, are the rooms a technician would draw, not the things a driver does: drive, find the way, record the road, check the car, ask. The owner's mockups define a different product with five tabs, Home / Navigation / Cameras / Vehicle / Agent, and a visual language the current app does not have: a deep navy-teal ground, a cyan accent, lime for healthy, quiet glass cards, a proportional sans with big light numerals, and line icons.

Everything after this step (the camera recorder's screen, the map, the Work tab) needs a frame to plug into. This step builds that frame and the two screens that use data the car already gives: Home and Vehicle.

What does not change: the data layer (`lib/`), the safety rules, the tier system, the honesty rules, and the no-build-step rule. Every existing screen keeps working and gets a home in the new structure.

## Shape

- Five tabs: Home, Navigation, Cameras, Vehicle, Agent. Agent has two modes, Car and Work.
- Navigation, Cameras and Agent → Work ship as honest placeholders in this step. Each says what it will do and which step builds it. None shows made-up content.
- Home and Vehicle are rebuilt against the mockups, in landscape (1368×912 CSS px) and portrait (912×1368) on the Surface Pro 7+.
- Every existing view is re-homed. Today's Faults, Data and Care tabs become sub-pages of Vehicle.
- The mockup palette becomes the default look. Omarchy's theme stays available as a look, not the default.
- Inter ships inside the app. The Surface has only Adwaita installed.
- The advisor moves to Opus 5.5.

## Rejected approaches

- **Restyle the five old tabs and keep the structure.** The owner asked for a reimagining. The old structure is the thing being replaced, and a new coat of paint on it would still send a driver to "Data" to see their coolant temperature.
- **Rewrite in a framework (React, Svelte).** Against the house rule of no build step, and it would throw away 17,000 lines of views that work on a real car. The views move; they are not rewritten.
- **Six tabs, with Agent and Work separate.** The mockups show five slots in both orientations. Work is a mode of talking to an agent, so it lives inside Agent.
- **Draw the mockups' per-wheel tyre temperatures and call the hybrid tile "IMA 68%" of charge.** The CR-Z has a deflation warning with no per-wheel pressures or temperatures, and Honda's own state of charge has never answered on this car. See the honesty rules in the design section.
- **Keep Omarchy's theme as the default colours.** The README's "wearing Omarchy's clothes" rule gave the app whatever palette the desktop had. The mockups are a designed palette, and the owner wants that look. The rule is retired for the default and kept as an option.

## Design

### Where every screen goes

| Today | New home |
|---|---|
| Hub (`hub`) | Replaced as the home screen by Home. Its tiles fold into Home and Vehicle. |
| Begin (`launcher`) | Home, as the "ready to drive" card when the car is off. |
| Gauges (`drive`), Cluster (`live`) | Vehicle → Live. Gauges also opens full screen from Home's speed dial. |
| Battery (`ima`) | Vehicle → Hybrid. |
| Phone (`omaplay`) | Home's phone card opens the OmaPlay layer, as the Phone chip does today. |
| Music (`music`) | Home card. |
| Effects (`effects`) | Settings → Looks. |
| Overview (`dash`), Profile (`garage`), Report (`report`) | Vehicle → Car. |
| Codes, Scan, Readiness | Vehicle → Diagnose. |
| Live lab (`data`), Replay, Tests, Identifiers | Vehicle → Live (Replay and Tests keep their tiers; Identifiers stays at the top tier). |
| Service, Resets, Trends, Log, Docs | Vehicle → Care (tiers unchanged). |
| Nursery | Home card, only when a nursery document exists (as today). |
| Advisor | Agent → Car. |
| Learn, Themes, Appearance | Settings. |

Vehicle's sub-pages are reached by a chip row under the header: **Overview · Diagnose · Live · Hybrid · Care · Car**. The routing rules in `main.js` stay: every view belongs to a tab or to `OFF_NAV`, controls are built once and only updated, and nothing writes `location.hash` after first paint except a tap. A guard test asserts that every view in today's table still has a route after the move.

### The look

Colours sampled from the mockups (`scripts/sample-mockup.py`, median of 7×7 patches; text and strokes by the brightest or most saturated pixel in their box):

| Token | Value | Sampled from |
|---|---|---|
| `--ground` | `#0A1314` | page gutters, Home and Vehicle (`#080F0F`, `#0C171A`) |
| `--panel` | `#0E1519` | card fills (`#0D1318`, `#10161B`, `#09181C`) |
| `--hair` | `#272E32` | card top edge |
| `--ink` | `#F6FCFF` | numerals and titles (`#FFFFFF`, `#F6FCFF`) |
| `--ink-2` | `#CAD4DA` | secondary labels |
| `--dim` | `#BDC7D1` at reduced weight | scale captions |
| `--accent` | `#22CDEC` | active tab, speed arc, callout dots (`#22CDEC`, `#2ED7E7`, `#38C0DA`) |
| `--accent-fill` | `#25B1BF` | primary button fill |
| `--ok` | `#9BDE68` | NORMAL pill, check marks, hybrid (`#9BDE68`, `#8EDD65`, `#A1F07A`) |
| `--rec` | `#F55240` | REC and record (`#F55240`, `#F84B34`) |

`--warn`, `--bad` and `--ai` keep their current hues, re-tuned against the new ground for contrast. Every text-on-ground pair must pass WCAG AA at its size; a test computes the ratios from the token file.

- **Cards:** `--panel` with a 1 px `--hair` border, 14 px radius (20 px for hero cards), and a faint top-lit gradient. No backdrop blur in the base style. The ground behind cards is flat, so blur would buy nothing and cost GPU time on a hot tablet.
- **Type:** Inter Variable, shipped as a woff2 in `share/fonts/` (SIL Open Font License, licence file alongside). Numerals are tabular everywhere. Hero speed is Inter Display at weight 300. The scale runs 12 / 13 / 15 / 17 / 20 / 28 / 44 / 96 px. `lib/fonts.py` keeps the font choice, and "Inter (bundled)" becomes the default stack.
- **Icons:** one line-icon set at a 1.75 stroke, vendored as an SVG sprite (Lucide, ISC licence) and replacing `icons.js` glyphs where the mockups show a different one.
- **Motion:** state changes fade over 160 ms. Nothing moves while the car moves except values.
- **Looks:** the mockup palette ships as the default look, "OmaCar". Daylight (sun), Dim and Night red stay, retuned to the new tokens. "Omarchy theme" becomes a look that maps the desktop theme onto the same tokens, as `lib/theme.py` does today.

### The frame

- **Top bar:** "OmaCar | <page>" on the left, the clock centred, and on the right a data-source badge and the settings button. The badge is always there: `LIVE · OBD-II` on a car, `SIMULATED` from `omacar-sim`, `DEMO DATA` for fixtures, in the same position and style as the mockups' "CONCEPT · DEMO DATA".
- **Tab bar:** five icon-and-label tabs. The active tab gets the accent label and an underline glow. Targets keep the TOUCHSYSTEM floor: 56 px on a coarse pointer, never under 48.
- **Parked and driving:** from the existing `store.state`. Driving keeps today's rules: write screens grey out with the reason, targets grow, and the stage never reflows under a finger. Work's voice-only driving mode builds on this in step 4.
- **Layout:** a 12-column landscape grid and a 6-column portrait grid, switched by `@media (orientation: portrait)`. Card internals use container queries, so a tile is correct at any width it gets. The two reference sizes are 1368×912 and 912×1368; `--safe-b` and the mount-cover allowance from the in-car critique stay.

### Home

Landscape follows mockup 3, portrait follows mockup 4.

- **Speed dial:** arc with speed, rpm, and the drive-mode pill (ECON, NORMAL or SPORT) only when `drive.js`'s drive-mode logic can tell it; otherwise no pill. Tapping it opens Gauges full screen.
- **Car:** the plain render with callouts that are real. The car itself cannot say which wheel is low, so by default there is one tyre callout carrying the deflation warning's state (OK, warning or not read). The callout component also takes per-wheel pressure and temperature, and draws the mockups' four corner callouts when a per-wheel source exists (see open question 5).
- **Navigation card:** placeholder until step 3, saying "Navigation arrives with offline maps".
- **Four signal tiles:** Coolant, 12V system, Fuel and Hybrid pack, each with its value, unit, range and a sparkline of the last minutes. Hybrid pack reads PID 0x5B and is labelled "Hybrid pack" with the same wording `ima.js` uses, never "state of charge". A reading the car does not support shows "Not on this car", and a supported reading with no answer yet shows "Waiting for the car". The tile choice uses the existing `omacar-drive.json`.
- **Phone:** CarPlay and Android Auto rows that open OmaPlay. A row is shown only for what the connected dongle supports.
- **Dashcams:** placeholder until step 2.
- **Oma Agent:** the last exchange and a tap target into Agent.
- **Footer:** the provenance line from the mockups ("Vehicle data: OBD-II + Honda enhanced · simulated"), built from the actual source.

### Vehicle

Follows mockup 5.

- **Header:** a status line computed from the last module scan: "All systems normal", or "2 systems need attention" naming them. It shows when that scan ran, and the car's identity (year, model, engine, hybrid system, odometer) from the profile and the odometer reading.
- **X-ray car:** the render from the owner's image 1, with callouts that point at systems the scan covers: Engine, Hybrid (IMA battery and motor), Brakes (ABS/VSA), Steering (EPS) and Tyres (deflation warning). Each callout takes its module's colour: accent while normal, warn or bad with a code. The mockup's three wheel callouts become the Brakes and Tyres callouts, because the scan reports per system, not per wheel.
- **Systems list:** Engine, Hybrid, Brakes, Electrical, and "All other systems", each with status and a chevron into Diagnose filtered to that module.
- **Live signals:** Engine speed, Coolant, 12V and Hybrid pack, with ranges and sparklines, the same components as Home's tiles.
- **Actions:** Scan vehicle (the existing full scan), Record session (the existing drive recorder) and Export (the existing report).
- **Codes and insight:** the Diagnostic codes card and an Oma Agent insight card, which appears only when the advisor has produced one from real evidence.
- **Data source switch:** OBD-II and Honda enhanced, shown only for sources the profile marks as validated. Unvalidated enhanced signals keep today's "candidate" treatment.

### Honesty rules carried into the new screens

- A value is drawn only when it was measured. Every tile has one of three states: live, not on this car, or waiting for the car. A zero is never a stand-in.
- The source badge and footer say where every number came from. Simulated data says so on every screen, as the mockups do.
- Nothing in this step invents a reading the mockups show but the car lacks: per-wheel tyre temperatures, Honda's own IMA state of charge, or a data-source switch for enhanced signals that were never validated.
- A guard test fails the build if a view renders a numeric reading without a source.

### Car imagery

- **X-ray render:** taken from image 1 by colour-to-alpha against its white background. That keeps the translucent body panels as partial transparency, so they composite correctly over the dark ground. It is stored with a JSON file of callout anchor points in render coordinates. Image 1 is 1504 px wide, which is about 1:1 on the Surface at the Vehicle hero's width at 2× scale.
- **Plain render for Home:** the only copy is inside the mockups at about 420 px wide, too soft at 2×. See open question 1.

### Opus 5.5

`lib/ai.py` moves every kind that uses `claude-sonnet-5` to `claude-opus-5-5`. The `owner` kind stays on `claude-haiku-4-5`, where speed matters more than depth. The advisor keeps its contract: every finding cites evidence keys and the answer is JSON. The existing cache is keyed by model, so old answers are not served as new ones.

## Rollout

1. Branch `redesign/foundation` from `omacar-launch` at `a623a17`, in a worktree at `~/Projects/.omacar-wt/foundation` on the Omarchy box. The PR targets `omacar-launch`, the active line. Pushes go from the Mac, because the box cannot push.
2. Commits, one job each:
   1. tokens, fonts and icon sprite
   2. the frame: top bar, tab bar, routes, with every old view re-homed and the guard test
   3. Home
   4. Vehicle
   5. car imagery and callout anchors
   6. Opus 5.5
   7. contrast and honesty guard tests
3. Checks: `test/all.sh` green; screenshots at 1368×912 and 912×1368 against `omacar-sim`, set side by side with mockups 3, 4 and 5 for review; then a run on the Surface itself.
4. Rollback: the PR is one branch, and the old look stays reachable as the "Omarchy theme" look until the owner signs off.

## Open questions

1. **Home's plain car render.** Options:
   - a photo of the owner's own CR-Z with the background removed, which fits `doc/car3d.md`'s preference for the real car;
   - a new render;
   - the X-ray render with its translucency turned off.

   Until decided, Home uses the X-ray render at reduced glow.
2. **Honda's badge in a public repo.** `omauilabs/omacar` is public, and the mockup renders carry the Honda "H". Keep the renders out of git, fetched at install into `~/.local/share/omacar/`, or remove the badge from the committed copies.
3. **"Customize layout".** The mockups show it. This step keeps it to choosing Home's four tiles; a drag-to-arrange editor is left out unless wanted.
4. **The mockups themselves.** They are kept git-ignored in `doc/design/mockups/` for the screenshot comparisons. Say if they should be committed, which would make them public.
5. **Tyre sensors.** The owner has Tymate TM2 sensors on the wheels, which broadcast pressure and temperature on 433.92 MHz to a solar receiver. A USB radio receiver (RTL-SDR) running `rtl_433` may decode them, which would give real per-wheel pressure and temperature. That becomes its own small step after Cameras if a test capture decodes. This step only makes the Home and Vehicle tyre callouts ready for it.
