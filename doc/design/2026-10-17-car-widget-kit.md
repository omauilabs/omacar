# The car widget kit: Omarchy apps and plugins on the car screen

Owner, 2026-10-17: "we need to build a widget library to run omarchy
apps/plugins with UI that is customized to a car experience." Approach chosen
the same day: **both, declarative first.**

1. **Phase 1, the car widget kit.** A plugin *declares* its content in JSON.
   OmaCar renders it with its own car widgets and enforces the driving rules.
   Any Omarchy app gets a car-quality screen without writing any UI.
2. **Phase 2, the panel host.** An existing Omarchy QML `panel` plugin runs
   unchanged in a Quickshell overlay, parked only, in car tokens. This is the
   roadmap's "the contract supports Omarchy's full-screen `panel` kind first".

This document is phase 1 in full, and phase 2 in outline.

## Why declarative

Omarchy plugins are Quickshell QML: they draw their own UI and know nothing
about a moving car. OmaCar is a web app and cannot render QML. More
importantly, the **plugin safety rules** (doc/ROADMAP.md, "Plugin safety
rules") must be enforced *by the app, not requested of the plugin*. Rules like
glanceable only, no text entry, thumb-size targets, nothing animating in
motion, writes greyed with a reason, and every number sourced cannot be
enforced on UI a plugin draws itself. They can be enforced, completely, on UI
OmaCar draws from a description. That is also how the car looks like one
product (rule 9).

## Where a car plugin comes from

Either kind of plugin opts in by shipping a `car.json` beside its own files:

| Kind | Directory |
|---|---|
| OmaCar plugin | `~/.config/omacar/plugins/<name>/car.json` |
| Omarchy plugin | `~/.config/omarchy/plugins/<id>/car.json` (next to its `manifest.json`) |

Dropping the file in installs it, and deleting it uninstalls it, as in
doc/PLUGINS.md. A malformed `car.json` disables that plugin alone, with the
reason shown in Cabin → Apps.

## car.json, schema 1

```json
{
  "schema": 1,
  "id": "now-playing",
  "name": "Now playing",
  "icon": "music",
  "needs": { "runs_code": true, "network": false, "car_data": [] },
  "data": { "command": ["./car-data"], "every_s": 2, "timeout_s": 2 },
  "card":   { "type": "media", "title": "$.title", "artist": "$.artist",
              "playing": "$.playing", "play": "toggle", "next": "next" },
  "screen": { "type": "stack", "children": [ ... ] },
  "actions": {
    "toggle": { "label": "Play/Pause", "command": ["playerctl", "play-pause"], "moving": "allowed" },
    "next":   { "label": "Next", "command": ["playerctl", "next"], "moving": "allowed" }
  }
}
```

- **`data.command`** runs with no shell, in the plugin's directory, with a
  timeout. It prints one JSON object to stdout, capped at 64 KB. This is the
  same pattern as omarchy-mobile's `omarchy-mobile-<name>` commands, so one
  data command serves both the phone and the car.
- **Bindings:** `"$.path.to.value"` reads from that JSON. A literal is drawn
  as given.
- **Actions** are argv lists, run without a shell and with a timeout.
  - `moving` is `"allowed"` (one tap, like the detector's mute) or
    `"parked"`, which is the default.
  - `confirm: true` asks first.
  - `car_write: true` marks an action that reaches the car. Those are
    **refused while moving, with no override** (rule 5). The server checks
    this as well as the page.
- **`needs`** is shown in Cabin → Apps before the plugin is relied on (rule
  7). `runs_code` is always true when there is a `data.command` or any action.
  The kit computes this itself; a plugin cannot declare it away.

## The widgets (schema 1)

| Widget | Shows | While moving |
|---|---|---|
| `metric` | label, value, unit, **source**, tone, optional trail (filled sparkline) | kept |
| `gauge` | an arc for value in min..max, with label and source | kept |
| `row` / `grid` / `stack` | containers | kept |
| `media` | art, title, artist, play/pause/next | title and artist, plus allowed buttons only |
| `list` | items: title, sub, trailing; each may bind an action | **first 3 items only**, no scrolling |
| `toggle` | a labelled switch bound to an action | only if the action is `moving: allowed` |
| `button` | a labelled key bound to an action | likewise; otherwise greyed, with the reason |
| `chart` | a series as a filled line | kept, but static |
| `text` | a short note, up to 280 characters | **hidden** while moving, if over 80 characters |

`image` is left out of schema 1. A plugin's pictures need a file route that
is safe against path tricks, and a half-built one is worse than none.

There is no text input widget, at all (rule 2).

A `metric` or `gauge` without a `source` is labelled **"Unlabelled source"**
in the warn tone (rule 6). A missing value draws as "—" with words, never 0.

**Glance mode** is the renderer's transform when the car moves
(`store.lockedAsMoving`, the app's own lock). At most 4 widgets are shown, in
declaration order (containers count their children). Nothing animates, and
targets are at least 56 px (rule 3).

## Where it appears in the app

- **Cabin → Apps:** a grid of installed car plugins. Each tile shows the
  plugin's `card`, its needs, and any error. Tapping opens its `screen` at
  `#app/<id>`, filling the stage.
- **Home:** a generic "App" card that holds one chosen plugin's `card`. It
  comes after the kit settles, together with the dynamic Home catalogue.
- **Theming** comes from tokens only. A plugin cannot ship CSS (rule 9).

## The server: `lib/carkit.py`

- **Discovery and validation:** malformed files are listed with their reason.
- **Data:** each `data.command` is run on its schedule, but only while
  something is looking: the last API read was within 10 s. Results are
  cached, and the last good value is kept, with its age.
- **Actions** go through `POST /api/carkit/<id>/action/<name>`.
  - The server re-checks `moving` against `live.json`, and its hand-off flag
    as moving, the same rule as `store.lockedAsMoving`.
  - It refuses `car_write` while moving, unconditionally.
  - It logs every run.
- **Routes:** `GET /api/carkit` and `GET /api/carkit/<id>`, with stubs in
  `DEMO_ROUTES`. They are not cockpit writes.
- **CLI:** `omacar carkit list | check <dir> | run <id> | new <name>`. The
  `new` command scaffolds a plugin with its test.

## Phase 2 in outline: the panel host

An Omarchy plugin whose `manifest.json` lists the `panel` kind can be opened
from Cabin → Apps as a **Quickshell layer surface over the kiosk**:

- **Parked only.** Its UI isn't car-aware, so it closes when the car moves
  (rule 1).
- **Car tokens:** its colours come from the active Omarchy theme, which
  OmaCar's looks already follow.
- **Closing:** a close key always sits outside it.

It needs the tablet's Hyprland to test, so it is built and verified there.

## Not in schema 1

- Plugin-drawn web views, as noted in rule 10: isolation is research.
- Location data.
- Writes to the car from plugins at all. `car_write` exists so the refusal is
  explicit and tested; no shipped example uses it.

## Shipped examples (`examples/carkit/`)

1. **Now playing:** `playerctl` metadata plus play/pause/next, with moving
   allowed. It is a real Omarchy desktop tool.
2. **Tablet:** CPU temperature, battery, disk and Wi-Fi as metrics, with
   sources.
3. **Omarchy themes:** the installed themes as a list, where tapping applies
   one with `omarchy-theme-set`. That is parked only, which shows the
   moving-lock working.
