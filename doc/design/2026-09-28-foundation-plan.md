# OmaCar foundation implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild OmaCar's frame, look, Home and Vehicle to the owner's mockups, as specified in `doc/design/2026-09-28-foundation.md`.

**Architecture:**
- **No build step, no framework, no rewrite of working views.** The app stays plain HTML, CSS and ES modules served by `lib/serve.py`. The existing 24 views keep working and are re-homed under five new tabs.
- **New pure modules underneath:** readings, trails, provenance, systems and Home cards. The two new screens (`views/home.js`, `views/vehicle.js`) and a layout editor are built on them.
- **Honda-badged images** come from a git-ignored folder described by a committed manifest.

**Tech stack:** vanilla JS (ES modules), CSS custom properties and grid, Python 3 stdlib (server and tests), headless Chromium (browser tests), rsync over ssh (private assets). The asset conversion tool uses Pillow and runs on the Mac only.

## Global constraints

- **No build step:** ES modules loaded from disk, Python stdlib only. The one exception is `tools/color_to_alpha.py`, which needs Pillow and runs on the Mac.
- **Reference sizes:** 1368×912 CSS px landscape and 912×1368 portrait (Surface Pro 7+ at scale 2).
- **Touch targets:** never under 48 px, 56 px on a coarse pointer (the existing `--tap` and `--tap-lg` tokens).
- **Colour tokens, exact values:**

  | Token | Value |
  |---|---|
  | `--ground` | `#0A1314` |
  | `--panel` | `#0E1519` |
  | card hairline | `#272E32` |
  | `--ink` | `#F6FCFF` |
  | `--ink-2` | `#CAD4DA` |
  | `--dim` | `#BDC7D1` |
  | `--accent` | `#22CDEC` |
  | `--accent-fill` | `#25B1BF` |
  | `--ok` | `#9BDE68` |
  | `--rec` | `#F55240` |

  Every text-on-background pair must pass WCAG AA (4.5:1). `test/design_test.py` enforces it.
- **Type:** Inter ships in the app at `share/fonts/InterVariable.ttf`, under the SIL OFL 1.1. Numerals are tabular everywhere.
- **Source badge text, exactly:** `LIVE · OBD-II`, `SIMULATED`, `BENCH`, `NO CAR`, `NO SERVER`, `STARTING`.
- **Hybrid tile:** labelled `Hybrid pack` and reads PID `HYBRID_BATTERY_REMAINING`. Never "state of charge".
- **Tile states:**
  - `live`: draws the value.
  - `absent`: draws "Not on this car".
  - `waiting`: draws "Waiting for the car".
  - A zero is never a stand-in for a missing value.
- **Honda-badged images** never enter git: `share/assets/private/` is ignored and a guard test checks that nothing under it is tracked.
- **Routing:** after first paint, nothing writes `location.hash` except in direct response to a tap or keypress. Navigation controls are built once and afterwards only updated.
- **Tests:** every test file added to `test/` goes into `test/all.sh` the same day.
- **Commits:**
  - The subject is one plain sentence, in the style of the existing log (for example "The button under your thumb moved 54 mm when the car started rolling").
  - The body explains why.
  - The last line is `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Where work happens:**
  - Code is edited and committed in the Mac clone `/Users/jmyers/omgarchy/omacar`.
  - Tests run on the Omarchy box against a mirror at `~/Projects/.omacar-test/foundation`, which has the real Chromium, the venv and the simulator.
  - GitHub pushes go from the Mac. The box cannot push.

**The test command** used by every task (called `BOXTEST` below):

```bash
rsync -a --delete --exclude share/assets/private/ /Users/jmyers/omgarchy/omacar/ jmyers@omarchy:Projects/.omacar-test/foundation/ && ssh jmyers@omarchy 'cd ~/Projects/.omacar-test/foundation && test/all.sh'
```

A single suite runs as, for example, `ssh jmyers@omarchy 'cd ~/Projects/.omacar-test/foundation && python3 test/js_test.py'` after the same rsync.

---

### Task 0: Workspace and baseline

**Files:** none changed.

- [ ] **Step 1: Clone to the Mac and fetch the design commits from the box**

```bash
git clone https://github.com/omauilabs/omacar.git /Users/jmyers/omgarchy/omacar
cd /Users/jmyers/omgarchy/omacar
git fetch ssh://jmyers@omarchy/home/jmyers/Projects/omacar redesign/foundation:redesign/foundation
git checkout redesign/foundation
git log --oneline -3
```

Expected: the top two commits are `dae30dd The owner's answers…` and `58576fd The foundation redesign, written down…`, on top of `a623a17`.

- [ ] **Step 2: Remove the box worktree that held the design commits**

The Mac clone now owns the branch.

```bash
ssh jmyers@omarchy 'git -C ~/Projects/omacar worktree remove ~/Projects/.omacar-wt/foundation && git -C ~/Projects/omacar worktree list'
```

- [ ] **Step 3: Record the baseline**

Run `BOXTEST`, then save the pass/fail summary of every suite to `/private/tmp/…/scratchpad/baseline.txt`. A suite that already fails before any change is not this plan's regression; name it in the PR instead.

---

### Task 1: A JavaScript unit harness that runs in the real browser

There is no node on the box or the tablet, so pure JS is tested by importing it into a page in headless Chromium and reading the results back from `document.title`, the same way `test/app_test.py` reads its geometry probe.

**Files:**
- Create: `test/js_test.py`, `test/js/assert.js`, `test/js/harness.test.js`
- Modify: `test/all.sh` (add the suite after `app_test.py`)

**Interfaces:**
- Produces: every later `test/js/*.test.js` file default-exports an array of `[name, fn]` pairs and imports app modules as `../js/<file>.js`. `assert.js` exports `eq(got, want, what)` and `ok(cond, what)`.

- [ ] **Step 1: Write the assertion module and a harness self-test**

`test/js/assert.js`:

```js
// The two assertions every unit here needs, and nothing more. Comparison is by
// JSON so arrays and plain objects compare by value.
export function eq(got, want, what = "value") {
  const g = JSON.stringify(got), w = JSON.stringify(want);
  if (g !== w) throw new Error(`${what}: got ${g}, want ${w}`);
}

export function ok(cond, what) {
  if (!cond) throw new Error(what);
}
```

`test/js/harness.test.js`:

```js
import { eq, ok } from "./assert.js";
import { h } from "../js/core.js";

export default [
  ["the harness can import the app's own modules", () => ok(typeof h === "function", "h is exported")],
  ["eq compares by value", () => eq({ a: [1, 2] }, { a: [1, 2] })],
  ["eq fails on a difference", () => {
    let threw = false;
    try { eq(1, 2); } catch { threw = true; }
    ok(threw, "eq(1, 2) must throw");
  }],
];
```

- [ ] **Step 2: Write the runner**

`test/js_test.py`:

```python
#!/usr/bin/env python3
"""The app's pure JavaScript, tested in the browser the app runs in.

There is no node on the tablet or on the box, and the house rule is no build
step, so a JavaScript unit test here is a module under test/js/ that a real
Chromium imports. Each module's default export is a list of [name, fn] pairs;
fn throws to fail. The runner page collects the results into document.title and
--dump-dom brings them back -- the same trick app_test.py's geometry probe uses.

Skipped loudly without a browser, like app_test.py: a test that cannot run is
not a failure, and a test that silently does nothing is.
"""

import html
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARE = os.path.join(ROOT, "share")
TESTS = os.path.join(ROOT, "test", "js")

RUNNER = """<!doctype html><meta charset="utf-8"><title>RUNNING</title>
<script type="module">
const files = %s;
const out = { passed: 0, failed: [] };
for (const f of files) {
  let mod;
  try { mod = await import("./_tests/" + f); }
  catch (e) { out.failed.push(f + " :: import :: " + ((e && e.message) || e)); continue; }
  for (const [name, fn] of mod.default) {
    try { await fn(); out.passed++; }
    catch (e) { out.failed.push(f + " :: " + name + " :: " + ((e && e.message) || e)); }
  }
}
document.title = "RESULT " + JSON.stringify(out);
</script>
"""


def browser():
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        p = shutil.which(name)
        if p:
            return p
    return None


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    print("\n  JavaScript units, in a real browser\n")
    exe = browser()
    if not exe:
        print("    (skipping: no chromium here. `sudo pacman -S chromium` to\n"
              "     have this test mean something.)\n")
        return 0
    files = sorted(f for f in os.listdir(TESTS) if f.endswith(".test.js"))
    if not files:
        print("    FAIL  test/js holds no *.test.js files\n")
        return 1
    # A COPY of share/, so nothing here can leave a test page in the repo.
    work = tempfile.mkdtemp()
    copy = os.path.join(work, "share")
    shutil.copytree(SHARE, copy)
    shutil.copytree(TESTS, os.path.join(copy, "_tests"))
    with open(os.path.join(copy, "_run.html"), "w", encoding="utf-8") as f:
        f.write(RUNNER % json.dumps(files))
    port = free_port()
    srv = subprocess.Popen([sys.executable, "-m", "http.server", str(port),
                            "--bind", "127.0.0.1"], cwd=copy,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    prof = tempfile.mkdtemp()
    try:
        for _ in range(40):
            time.sleep(0.25)
            try:
                with socket.create_connection(("127.0.0.1", port), 0.25):
                    break
            except OSError:
                continue
        r = subprocess.run(
            [exe, "--headless=new", "--disable-gpu", "--no-sandbox",
             f"--user-data-dir={prof}", "--virtual-time-budget=8000",
             "--dump-dom", f"http://127.0.0.1:{port}/_run.html"],
            capture_output=True, text=True, timeout=120)
        m = re.search(r"<title>RESULT (\{.*?\})</title>", r.stdout or "", re.S)
        if not m:
            print("    FAIL  the runner page never finished\n")
            return 1
        res = json.loads(html.unescape(m.group(1)))
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except subprocess.TimeoutExpired:
            srv.kill()
        shutil.rmtree(prof, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)
    for line in res["failed"]:
        print(f"    FAIL  {line}")
    print(f"\n  {res['passed']} passed, {len(res['failed'])} failed\n")
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Prove the harness fails a failing test**

Temporarily add `["deliberate", () => eq(1, 2)]` to `harness.test.js`, then rsync and run `python3 test/js_test.py` on the box.

Expected: `FAIL  harness.test.js :: deliberate :: value: got 1, want 2`, exit 1. Remove the deliberate test afterwards.

- [ ] **Step 4: Run it green, and add it to all.sh**

In `test/all.sh`, after the line `python3 "$ROOT/test/app_test.py" || fails=$((fails + 1))`, add:

```bash
# The pure JavaScript, imported into a real browser: there is no node on the
# box or the tablet, and there is no build step to hang one off.
python3 "$ROOT/test/js_test.py" || fails=$((fails + 1))
```

Run: rsync, then `python3 test/js_test.py`. Expected: `3 passed, 0 failed`.

- [ ] **Step 5: Commit**

```bash
git add test/js_test.py test/js/assert.js test/js/harness.test.js test/all.sh
git commit -m "JavaScript gets unit tests, run in the same browser the tablet uses" -m "There is no node on the box or the tablet, so each test/js/*.test.js module is imported by a page in headless Chromium and the results come back through document.title, the way app_test.py's geometry probe already works.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The palette, the bundled typeface and the icons

**Files:**
- Modify: `share/css/app.css` (the first `:root { … }` block, and the `.card` rule)
- Create: `share/fonts/InterVariable.ttf` and `share/fonts/OFL.txt`, both copied from the box's `inter-font` 4.1 package
- Modify: `share/css/fonts.css`, `lib/fonts.py`, `share/js/icons.js`, `share/js/core.js` (icon stroke width), `ATTRIBUTION.md`
- Create: `test/design_test.py`
- Modify: `test/all.sh`

**Interfaces:**
- Produces:
  - CSS tokens `--ink-2`, `--accent`, `--accent-fill`, `--on-accent`, `--rec`, `--accent-bg`, `--r-lg`, with `--r` changed to 14px.
  - Font family `"OmaCar Inter"` and the utility class `.display-num`.
  - `ICONS` keys `home, nav, camera, vehicle, agent, chevron, check, layout, phone, music, thermo, battery, fuel, leaf, gauge, rec, plus, x`.
  - `fonts.DEFAULT == "omacar"`.

- [ ] **Step 1: Write the failing contrast test**

`test/design_test.py`:

```python
#!/usr/bin/env python3
"""The palette keeps its own promise: every text colour clears WCAG AA on the
surface it is drawn on.

The tokens were sampled from the owner's mockups (doc/design/
2026-09-28-foundation.md, "The look"), and a sampled colour is only a start: a
caption that reads beautifully in a mockup can be 3:1 on a real panel in a real
car at noon. This reads the values out of app.css itself, so the test and the
stylesheet cannot drift apart.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS = os.path.join(ROOT, "share", "css", "app.css")

# (text token, background token, minimum ratio, what it is)
PAIRS = [
    ("ink", "ground", 7.0, "body text on the page"),
    ("ink", "panel", 7.0, "body text on a card"),
    ("ink-2", "panel", 4.5, "secondary labels on a card"),
    ("dim", "panel", 4.5, "captions on a card"),
    ("faint", "panel", 4.5, "the faintest text that is still text"),
    ("accent", "ground", 4.5, "the active tab's label"),
    ("accent", "panel", 4.5, "accent text on a card"),
    ("ok", "panel", 4.5, "NORMAL and the check marks"),
    ("warn", "panel", 4.5, "warnings"),
    ("bad", "panel", 4.5, "faults"),
    ("rec", "panel", 4.5, "REC"),
    ("on-accent", "accent-fill", 4.5, "text on the primary button"),
]

fails = 0


def check(msg, cond):
    global fails
    if not cond:
        fails += 1
    print(f"    {'ok  ' if cond else 'FAIL'}  {msg}")


def block(css, selector):
    m = re.search(re.escape(selector) + r"\s*\{(.*?)\n\}", css, re.S)
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})\s*;", m.group(1))) if m else {}


def lum(hexv):
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hexv[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def ratio(a, b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def run(name, tokens):
    print(f"\n  {name}\n")
    for fg, bg, need, what in PAIRS:
        if fg not in tokens or bg not in tokens:
            check(f"--{fg} and --{bg} are both declared ({what})", False)
            continue
        r = ratio(tokens[fg], tokens[bg])
        check(f"{what}: --{fg} on --{bg} is {r:.2f}:1 (needs {need})", r >= need)


def main():
    css = open(CSS, encoding="utf-8").read()
    base = block(css, ":root")
    run("The default palette", base)
    print()
    if fails:
        print(f"  {fails} failed\n")
        return 1
    print("  every pair reads\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Add to `test/all.sh`, after the `js_test.py` line:

```bash
# The palette clears WCAG AA against itself, read out of app.css.
python3 "$ROOT/test/design_test.py" || fails=$((fails + 1))
```

- [ ] **Step 2: Run it to see it fail**

Run: rsync, then `python3 test/design_test.py`.

Expected: FAIL. `--ink-2`, `--accent`, `--rec`, `--on-accent` and `--accent-fill` are not declared yet, and `--faint` (`#5F726E`) is under 4.5:1.

- [ ] **Step 3: Replace the palette in app.css**

In the first `:root {` block of `share/css/app.css`, replace everything from `--ground:  #070B0D;` down to and including `--bright-2: #C8D4D2;`, and change `--r: 10px;` to the two radius lines. Leave the comment above `--bright` and everything after `--vbar` alone.

```css
  /* THE MOCKUP PALETTE, SAMPLED RATHER THAN CHOSEN. Every value below was read
     off the owner's mockups of 2026-09-27 (doc/design/2026-09-28-foundation.md,
     "The look"): the median of a 7x7 patch for fills, the brightest or most
     saturated pixel in the box for text and strokes. Change one and run
     test/design_test.py, which holds every text colour to WCAG AA. */
  --ground:  #0A1314;
  --panel:   #0E1519;
  --panel-2: #111A1F;
  --raise:   #16222A;
  --edge:    #1E2A30;
  --edge-2:  #27363D;

  --ink:     #F6FCFF;
  --ink-2:   #CAD4DA;
  --dim:     #BDC7D1;
  /* Not sampled: the mockups have no text this faint. It is the faintest value
     that still clears 4.5:1 on a card, because a caption nobody can read in
     sunlight is not a caption. */
  --faint:   #7C8B94;
  --ghost:   #3A4950;

  --accent:      #22CDEC;
  --accent-fill: #25B1BF;
  --on-accent:   #041014;
  --rec:         #F55240;

  --ok:      #9BDE68;
  --warn:    #E5B457;
  --bad:     #E85D4E;
  --info:    #4FA8E8;
  --ai:      #8B7CF0;

  --ok-bg:     rgba(155, 222, 104, .12);
  --warn-bg:   rgba(229, 180, 87, .13);
  --bad-bg:    rgba(232, 93, 78, .13);
  --info-bg:   rgba(79, 168, 232, .12);
  --ai-bg:     rgba(139, 124, 240, .13);
  --accent-bg: rgba(34, 205, 236, .12);

  /* Derived surfaces, so a light theme does not end up with dark hairlines
     baked into a meter track. Everything below is expressed against these. */
  --track:  color-mix(in srgb, var(--ink) 13%, var(--panel));
  /* 11%, not 7%: that mix of ink into panel lands exactly on the mockups' card
     hairline, #272E32. */
  --hair:   color-mix(in srgb, var(--ink) 11%, var(--panel));
  --on-bad:  #12060A;
  --on-warn: #1A1305;
  --on-ai:   #0B0718;
  --bright:   #FFFFFF;
  --bright-2: #CAD4DA;
```

```css
  --r: 14px;
  --r-lg: 20px;
```

Then change the `.card {` rule's first line (`background: var(--panel); border: 1px solid var(--edge); border-radius: var(--r);`) to:

```css
  background: linear-gradient(180deg, color-mix(in srgb, var(--ink) 3%, var(--panel)), var(--panel));
  border: 1px solid var(--hair); border-radius: var(--r);
```

- [ ] **Step 4: Run the contrast test green**

Run: rsync, then `python3 test/design_test.py`. Expected: every pair ok.

- [ ] **Step 5: Ship Inter inside the app**

```bash
cd /Users/jmyers/omgarchy/omacar && mkdir -p share/fonts
scp jmyers@omarchy:/usr/share/fonts/inter/InterVariable.ttf share/fonts/InterVariable.ttf
scp jmyers@omarchy:/usr/share/licenses/inter-font/LICENSE.txt share/fonts/OFL.txt
ls -l share/fonts   # InterVariable.ttf is 879708 bytes (inter-font 4.1-1)
```

In `share/css/fonts.css`, replace the `:root { --sans…; --display…; --mono…; }` block with the following. The three `--` lines must be exactly what `lib/fonts.py` renders for the default stack, because `test/workshop_test.py` compares them.

```css
/* The bundled face first. share/fonts/InterVariable.ttf ships with the app
   (SIL OFL 1.1, share/fonts/OFL.txt) because the car's tablet has only Adwaita
   installed and the mockups are set in Inter. One variable file carries every
   weight and, through its opsz axis, the Display cut too. */
@font-face {
  font-family: "OmaCar Inter";
  src: url("../fonts/InterVariable.ttf") format("truetype");
  font-weight: 100 900;
  font-style: normal;
  font-display: swap;
}
:root {
  --sans:    "OmaCar Inter", "Inter", "Adwaita Sans", "Noto Sans", sans-serif;
  --display: "OmaCar Inter", "Inter", "Adwaita Sans", "Noto Sans", sans-serif;
  --mono:    "Adwaita Mono", "JetBrainsMono Nerd Font", ui-monospace, monospace;
}
/* Big numerals: the Display optical size, light, and tabular so a speed
   climbing 44, 45, 46 never changes width. */
.display-num {
  font-family: var(--display);
  font-variation-settings: "opsz" 32;
  font-weight: 300;
  font-variant-numeric: tabular-nums;
}
```

In `lib/fonts.py`:
- Change `DEFAULT = "workshop"` to `DEFAULT = "omacar"`.
- Insert this stack as the first entry of `STACKS`:

```python
    {
        "id": "omacar",
        "name": "OmaCar",
        "note": "Inter, shipped inside the app (share/fonts/InterVariable.ttf, "
                "SIL OFL 1.1), so it looks the same on a machine that has never "
                "installed a font. The owner's mockups are set in it.",
        "package": None,
        # Families that come with the app rather than from fontconfig. They are
        # never "missing": the browser loads them from share/fonts.
        "bundled": ["OmaCar Inter"],
        "sans": ["OmaCar Inter", "Inter", "Adwaita Sans", "Noto Sans", "sans-serif"],
        "display": ["OmaCar Inter", "Inter", "Adwaita Sans", "Noto Sans", "sans-serif"],
        "mono": ["Adwaita Mono", "JetBrainsMono Nerd Font", "ui-monospace", "monospace"],
    },
```

- In `missing()`, skip bundled heads. Change the loop body to:

```python
    for slot in SLOTS:
        head = stack[slot][0]
        if head in GENERIC or head in stack.get("bundled", ()):
            continue
        if head.lower() not in known and head not in out:
            out.append(head)
```

- In `resolve()`, bundled families are not known to fontconfig. QML callers (the bar widget) get the first *installed* family, which is `Inter` or `Adwaita Sans`, and that is correct. No change is needed there.

Append to `ATTRIBUTION.md`:

```markdown
## Bundled with the app

Two things ARE in this repository, both under licences that allow it:

- **Inter** (`share/fonts/InterVariable.ttf`), by Rasmus Andersson — SIL Open
  Font License 1.1, full text in `share/fonts/OFL.txt`. Copied from Arch's
  `inter-font` 4.1 package. The tablet has only Adwaita installed, and the
  mockups are set in Inter.
- **Icon paths** in `share/js/icons.js` marked "Lucide" — ISC License,
  © Lucide Contributors (https://lucide.dev). Paths only; no Lucide code.
```

- [ ] **Step 6: Add the icons, and match the stroke to the mockups**

In `share/js/core.js` `icon()`, change `svg.setAttribute("stroke-width", "1.7");` to `"1.75"`.

Add these entries inside `export const ICONS = {` in `share/js/icons.js`, above the closing `};`:

```js
  // ---- the redesign's set, from Lucide (ISC; see ATTRIBUTION.md) ----------
  home: ["M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8",
         "M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"],
  nav: ["M3 11 22 2 13 21 11 13z"],
  camera: ["m16 13 5.223 3.482a.5.5 0 0 0 .777-.416V7.87a.5.5 0 0 0-.752-.432L16 10.5",
           "M4 6h10a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2z"],
  vehicle: ["M19 17h2c.6 0 1-.4 1-1v-3c0-.9-.7-1.7-1.5-1.9C18.7 10.6 16 10 16 10s-1.3-1.4-2.2-2.3c-.5-.4-1.1-.7-1.8-.7H5c-.6 0-1.1.4-1.4.9l-1.4 2.9A3.7 3.7 0 0 0 2 12v4c0 .6.4 1 1 1h2",
            "M9 17a2 2 0 1 1-4 0 2 2 0 0 1 4 0z", "M9 17h6", "M19 17a2 2 0 1 1-4 0 2 2 0 0 1 4 0z"],
  agent: ["M2 10v3", "M6 6v11", "M10 3v18", "M14 8v7", "M18 5v13", "M22 10v3"],
  chevron: ["m9 18 6-6-6-6"],
  check: ["M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0z", "m9 12 2 2 4-4"],
  layout: ["M4 3h5a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z",
           "M15 3h5a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1h-5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z",
           "M4 14h5a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-5a1 1 0 0 1 1-1z",
           "M15 14h5a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1h-5a1 1 0 0 1-1-1v-5a1 1 0 0 1 1-1z"],
  phone: ["M7 2h10a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z", "M12 18h.01"],
  music: ["M9 18V5l12-2v13", "M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0z", "M21 16a3 3 0 1 1-6 0 3 3 0 0 1 6 0z"],
  thermo: ["M14 4v10.54a4 4 0 1 1-4 0V4a2 2 0 0 1 4 0z"],
  battery: ["M4 7h14a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2z", "M22 11v2"],
  fuel: ["M3 22h12", "M4 9h10", "M14 22V4a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v18",
         "M14 13h2a2 2 0 0 1 2 2v2a2 2 0 0 0 2 2 2 2 0 0 0 2-2V9.83a2 2 0 0 0-.59-1.42L18 5"],
  leaf: ["M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10z",
         "M2 21c0-3 1.85-5.36 5.08-6C9.5 14.52 12 13 13 12"],
  gauge: ["m12 14 4-4", "M3.34 19a10 10 0 1 1 17.32 0"],
  rec: ["M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0z", "M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0z"],
  plus: ["M5 12h14", "M12 5v14"],
  x: ["M18 6 6 18", "m6 6 12 12"],
```

- [ ] **Step 7: Run the whole suite**

Run `BOXTEST`.

Expected:
- `workshop_test.py` passes its font section, because the `fonts.css` lines equal `fonts.css(BY_ID["omacar"])`.
- `design_test.py` passes.
- Nothing else regresses against the baseline.

If `workshop_test.py` reports "its --sans default is still the one fonts.py hands out" as failing, compare the exact rendered string: `python3 -c "import sys; sys.path.insert(0,'lib'); import fonts; print(fonts.css(fonts.BY_ID['omacar']))"`, and copy it verbatim into `fonts.css`.

- [ ] **Step 8: Commit**

```bash
git add share/css/app.css share/css/fonts.css share/fonts lib/fonts.py share/js/icons.js share/js/core.js ATTRIBUTION.md test/design_test.py test/all.sh
git commit -m "The mockups' palette, their typeface and their icons, with a test that holds every text colour to AA" -m "Colours are sampled from the owner's mockups rather than chosen. --faint moves from #5F726E, which was 3.5:1 on a card, to the faintest value that clears 4.5:1. Inter ships inside the app because the tablet has only Adwaita.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: One readings catalogue, a memory of recent values, and sparklines

Home, Vehicle and drive mode must agree about every number, so the catalogue of readings leaves `views/drive.js` for its own module. It also gains a `pid` per reading, so a tile can tell "the car does not have this" from "the car has not said yet".

**Files:**
- Create: `share/js/readings.js`, `share/js/trail.js`, `share/js/spark.js`
- Modify: `share/js/views/drive.js` (the catalogue moves out), `share/js/main.js` (record samples)
- Test: `test/js/readings.test.js`, `test/js/trail.test.js`, `test/js/spark.test.js`

**Interfaces:**
- Produces:
  - From `readings.js`:
    - `READINGS`: the frozen catalogue. Each entry is `{ label, pid?, get(v, s, car) → {v, n, tone}, read?(v, s, car) → number|null, scale?() → {min, max, step, tick?, bands?} }`.
    - `readingState(def, sample) → "live" | "absent" | "waiting"`.
    - `num`, `raw`, `asTemp`, `pct`, `learnedFor(car)`, `learnedKey(car)`.
  - From `trail.js`: `record(sample, now?)`, `trail(pid) → [[ms, raw], …]`, `reset()`.
  - From `spark.js`: `sparkPath(points, w, h, lo?, hi?) → string`.

- [ ] **Step 1: Write the failing tests**

`test/js/readings.test.js`:

```js
import { eq } from "./assert.js";
import { READINGS, readingState } from "../js/readings.js";

const live = (values, supported) => ({ connected: true, values, supported });

export default [
  ["a supported reading with a value is live", () =>
    eq(readingState(READINGS.coolant, live({ COOLANT_TEMP: 88 }, ["COOLANT_TEMP"])), "live")],
  ["a reading the car does not support is absent", () =>
    eq(readingState(READINGS.charge, live({ COOLANT_TEMP: 88 }, ["COOLANT_TEMP"])), "absent")],
  ["a supported reading with no value yet is waiting", () =>
    eq(readingState(READINGS.coolant, live({}, ["COOLANT_TEMP"])), "waiting")],
  ["with no car it is waiting, never absent", () =>
    eq(readingState(READINGS.coolant, { connected: false, values: {} }), "waiting")],
  ["an unknown supported list never claims absent", () =>
    eq(readingState(READINGS.charge, live({}, undefined)), "waiting")],
  ["zero is a value, not a gap", () =>
    eq(readingState(READINGS.fuel, live({ FUEL_LEVEL: 0 }, ["FUEL_LEVEL"])), "live")],
  ["a derived reading has no pid and draws its own dash", () =>
    eq(readingState(READINGS.odometer, { connected: false }), "live")],
  ["the hybrid pack reads the generic remaining-life PID", () =>
    eq(READINGS.charge.pid, "HYBRID_BATTERY_REMAINING")],
  ["the catalogue is frozen", () => eq(Object.isFrozen(READINGS), true)],
];
```

`test/js/trail.test.js`:

```js
import { eq } from "./assert.js";
import { record, trail, reset } from "../js/trail.js";

const s = (t, values, connected = true) => ({ t, connected, values });

export default [
  ["a sample adds one point per numeric value", () => {
    reset();
    record(s(100, { COOLANT_TEMP: 80, VIN: "x" }));
    eq(trail("COOLANT_TEMP"), [[100000, 80]]);
    eq(trail("VIN"), []);
  }],
  ["the same sample polled twice is one point", () => {
    reset();
    record(s(100, { RPM: 900 }));
    record(s(100, { RPM: 900 }));
    eq(trail("RPM").length, 1);
  }],
  ["a disconnected sample adds nothing", () => {
    reset();
    record(s(100, { RPM: 900 }, false));
    eq(trail("RPM"), []);
  }],
  ["points older than five minutes fall off", () => {
    reset();
    record(s(100, { RPM: 1 }));
    record(s(100 + 301, { RPM: 2 }));
    eq(trail("RPM"), [[401000, 2]]);
  }],
  ["trail hands back a copy", () => {
    reset();
    record(s(1, { RPM: 1 }));
    trail("RPM").push([0, 0]);
    eq(trail("RPM").length, 1);
  }],
];
```

`test/js/spark.test.js`:

```js
import { eq } from "./assert.js";
import { sparkPath } from "../js/spark.js";

export default [
  ["fewer than two points draw nothing", () => eq(sparkPath([[0, 1]], 100, 20), "")],
  ["a rising line runs corner to corner", () =>
    eq(sparkPath([[0, 0], [10, 10]], 100, 20), "M0.0 20.0L100.0 0.0")],
  ["a flat line sits in the middle, not on the floor", () =>
    eq(sparkPath([[0, 5], [10, 5]], 100, 20), "M0.0 10.0L100.0 10.0")],
  ["a fixed scale clamps values outside it", () =>
    eq(sparkPath([[0, -50], [10, 500]], 100, 20, 0, 100), "M0.0 20.0L100.0 0.0")],
];
```

- [ ] **Step 2: Run them to see them fail**

Run: rsync, then `python3 test/js_test.py`. Expected: three `:: import ::` failures (the modules do not exist).

- [ ] **Step 3: Move the catalogue into readings.js**

Create `share/js/readings.js`. In order, it contains:
1. The header below.
2. A verbatim move of `share/js/views/drive.js` lines 23–28 (from `// The IMA tile's memory…` through `const socTrail = [];`).
3. A verbatim move of lines 30–275 (from `// ---------------------------------------------------------------- the catalogue` through `Object.freeze(TILES);`).
4. A verbatim move of lines 284–355 (from `function num(x, fmt) {` through the end of `function learnedKey(car) { … }`).

Leave `ACK_KEY` (line 21) and `FOOTERS` (lines 277–282) in drive.js. Header:

```js
// Every reading the app can show, in one place.
//
// This was drive mode's private catalogue (views/drive.js), and Home and
// Vehicle need the same numbers formatted the same way: the whole reason
// app_test.py checks that the hub and the drive screen agree about coolant is
// that two copies of a formatter WILL drift. So there is one copy, here.
//
// Each entry now also names the PID it reads, so a tile can tell the two kinds
// of empty apart: "the car does not report this" and "the car has not said yet".

import { U, temp, econ, dist, vol, grouped } from "./core.js";
```

After the moved code, add:

```js
export { TILES as READINGS };
export { num, raw, asTemp, pct, learnedFor, learnedKey };

// LIVE, ABSENT OR WAITING. Never a zero standing in for a missing value.
//
//   live     the value is in the sample: draw get()'s output
//   absent   the car answered the supported-PIDs question and this was not in
//            the answer: say "Not on this car"
//   waiting  no car, or a supported reading that has not arrived yet: say
//            "Waiting for the car"
//
// A reading with no pid is derived (economy, odometer, a fault count) and its
// get() already draws a dash when it has nothing, so it is always "live".
export function readingState(def, sample) {
  if (!def || !def.pid) return "live";
  const s = sample || {};
  if (!s.connected) return "waiting";
  const v = (s.values || {})[def.pid];
  if (v !== null && v !== undefined && !Number.isNaN(v)) return "live";
  const sup = s.supported;
  if (Array.isArray(sup) && sup.length && !sup.includes(def.pid)) return "absent";
  return "waiting";
}
```

In the moved `TILES` object, add a `pid` property to these entries, as the first line inside each:

| Entry | `pid` |
|---|---|
| `speed` | `"SPEED"` |
| `rpm` | `"RPM"` |
| `coolant` | `"COOLANT_TEMP"` |
| `intake` | `"INTAKE_TEMP"` |
| `ambient` | `"AMBIANT_AIR_TEMP"` |
| `volts` | `"CONTROL_MODULE_VOLTAGE"` |
| `charge` | `"HYBRID_BATTERY_REMAINING"` |
| `ima` | `"HYBRID_BATTERY_REMAINING"` |
| `fuel` | `"FUEL_LEVEL"` |
| `load` | `"ENGINE_LOAD"` |
| `throttle` | `"THROTTLE_POS"` |
| `timing` | `"TIMING_ADVANCE"` |
| `stft` | `"SHORT_FUEL_TRIM_1"` |
| `ltft` | `"LONG_FUEL_TRIM_1"` |
| `maf` | `"MAF"` |

In `learnedFor()`, add `pid: key,` to the object it builds.

In `share/js/views/drive.js`:
- Delete the moved lines.
- Add below the existing imports:

```js
import { READINGS as TILES, num, raw, asTemp, pct, learnedFor, learnedKey } from "../readings.js";
```

- Remove from the `../core.js` import list any names drive.js no longer uses. Check with `grep -n "\btemp(\|econ(\|vol(\|grouped(" share/js/views/drive.js`, and keep any name that still appears.

- [ ] **Step 4: Write trail.js and spark.js**

`share/js/trail.js`:

```js
// The last few minutes of every numeric reading, for sparklines.
//
// Filled from the live poller (main.js subscribes once) and read by tiles.
// Kept in module scope on purpose: a sparkline that starts empty every time you
// come back to Home is a sparkline that is never there when you look.

const WINDOW_MS = 5 * 60 * 1000;
const MAX_POINTS = 600;          // 5 minutes at 2 Hz, with room to spare
const series = new Map();        // pid -> [[ms, value], ...]
let lastT = 0;

export function record(sample, now = Date.now()) {
  if (!sample || !sample.connected || !sample.values) return;
  const t = sample.t ? sample.t * 1000 : now;
  // The poller asks four times a second and the daemon writes about five: the
  // same sample is often read twice, and twice is still one measurement.
  if (t <= lastT) return;
  lastT = t;
  for (const [k, v] of Object.entries(sample.values)) {
    if (typeof v !== "number" || Number.isNaN(v)) continue;
    let s = series.get(k);
    if (!s) { s = []; series.set(k, s); }
    s.push([t, v]);
    while (s.length && (t - s[0][0] > WINDOW_MS || s.length > MAX_POINTS)) s.shift();
  }
}

export function trail(pid) {
  return (series.get(pid) || []).slice();
}

export function reset() {
  series.clear();
  lastT = 0;
}
```

`share/js/spark.js`:

```js
// A sparkline's SVG path from [t, v] points, fitted into w x h.
//
// With a fixed lo/hi (a reading's own scale) the line sits where the value does
// on its dial; without one it fills the box. A flat line is drawn through the
// middle, because a line on the floor reads as "zero".
export function sparkPath(points, w, h, lo = null, hi = null) {
  if (!points || points.length < 2) return "";
  const t0 = points[0][0], t1 = points[points.length - 1][0];
  const vs = points.map((p) => p[1]);
  const min = lo === null ? Math.min(...vs) : lo;
  const max = hi === null ? Math.max(...vs) : hi;
  const flat = max === min;
  const dt = t1 - t0 || 1;
  return points.map(([t, v], i) => {
    const x = ((t - t0) / dt) * w;
    const c = Math.min(max, Math.max(min, v));
    const y = flat ? h / 2 : h - ((c - min) / (max - min)) * h;
    return (i ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
  }).join("");
}
```

In `share/js/main.js`, add `import { record } from "./trail.js";` with the other imports. In `boot()`, right after `store.on("live", () => { paintBar(); autoDrive(); });`, add:

```js
  // Every sample, for the sparklines. Cheap: an array push per reading.
  store.on("live", () => record(store.live));
```

- [ ] **Step 5: Run everything**

Run `BOXTEST`.

Expected:
- `js_test.py`: all green (readings, trail, spark, harness).
- `app_test.py`: still green. Drive mode now imports its catalogue, and its coolant must still agree with the hub.

- [ ] **Step 6: Commit**

```bash
git add share/js/readings.js share/js/trail.js share/js/spark.js share/js/views/drive.js share/js/main.js test/js/readings.test.js test/js/trail.test.js test/js/spark.test.js
git commit -m "The readings catalogue leaves drive mode so Home and Vehicle cannot format a number differently" -m "Each reading now names its PID, which is what lets a tile say 'Not on this car' rather than drawing a zero. Samples are kept for five minutes for sparklines.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The frame: five tabs, grouped Vehicle pages, the new top bar

**Files:**
- Modify: `share/js/main.js`, `share/app.html`, `share/css/app.css`
- Create: `share/js/provenance.js`, `share/js/placeholder.js`, `share/js/views/navigation.js`, `share/js/views/cameras.js`, `share/js/views/work.js`
- Modify: `share/js/views/music.js` (its `#hub` link), `share/js/onboard.js` (two strings)
- Test: `test/js/provenance.test.js`, and a new section in `test/guards_test.py`

**Interfaces:**
- Consumes: `ICONS.home/nav/camera/vehicle/agent` (Task 2).
- Produces:
  - Tab ids `home, navigation, cameras, vehicle, agent`, and `HOME = "home"`.
  - View ids `vehicle` (Vehicle → Overview), `navigation`, `cameras`, `work`.
  - `ALIASES = { hub: "home" }`.
  - `#segbar` in the page.
  - `provenance.js` exports `badge(car, live, serverError) → {text, tone, title}`, `footerLine(car, live) → string` and `sourceKey(car, live) → "sim"|"bench"|"obd"|"recorded"`.
  - `placeholder.js` exports `placeholder({ icon, title, step, lines }) → Element`.

- [ ] **Step 1: Write the failing tests**

`test/js/provenance.test.js`:

```js
import { eq } from "./assert.js";
import { badge, footerLine, sourceKey } from "../js/provenance.js";

export default [
  ["no server says so, not 'no car'", () => eq(badge(null, null, "404").text, "NO SERVER")],
  ["before the first snapshot it is starting", () => eq(badge(null, null, null).text, "STARTING")],
  ["the simulator is named before its numbers", () =>
    eq(badge({ simulated: true }, { connected: true }).text, "SIMULATED")],
  ["the bench is named", () => eq(badge({}, { connected: true, kind: "bench" }).text, "BENCH")],
  ["a real car is live", () => eq(badge({}, { connected: true, kind: "OBDLink SX" }).text, "LIVE · OBD-II")],
  ["no link is no car", () => eq(badge({}, { connected: false }).text, "NO CAR")],
  ["the footer says simulated", () =>
    eq(footerLine({ simulated: true, signals: [] }, {}), "Vehicle data: OBD-II · simulated")],
  ["the footer names enhanced signals only when there are some", () =>
    eq(footerLine({ signals: [{ id: "x" }] }, { connected: true }), "Vehicle data: OBD-II + Honda enhanced · live")],
  ["source keys", () => eq([
    sourceKey({ simulated: true }, {}), sourceKey({}, { kind: "bench" }),
    sourceKey({}, { connected: true }), sourceKey({}, { connected: false })],
    ["sim", "bench", "obd", "recorded"])],
];
```

Append to `test/guards_test.py`, before the `# ---…--- done` block:

```python
# ----------------------------------------------------- the navigation redesign
head("The five new tabs lost no screen")
import re as _re_nav  # noqa: E402
_main = open(os.path.join(ROOT, "share", "js", "main.js"), encoding="utf-8").read()
_ids = set(_re_nav.findall(r'\bid:\s*"([a-z0-9-]+)"', _main))
_alias_src = _main.split("const ALIASES", 1)[1].split("};", 1)[0] if "const ALIASES" in _main else ""
_alias = dict(_re_nav.findall(r'^\s*([a-z0-9]+):\s*"([a-z0-9-]+)"', _alias_src, _re_nav.M))
# Every view id the five old tabs and OFF_NAV had on 2026-09-17 (a623a17).
_OLD = ["hub", "drive", "live", "ima", "launcher", "omaplay", "music", "effects",
        "dash", "garage", "codes", "scan", "health", "data", "replay", "tests",
        "write", "service", "resets", "concerns", "history", "documents",
        "nursery", "advisor", "report", "learn", "themes"]
check("every old view still routes, itself or through an alias",
      [v for v in _OLD if v not in _ids and _alias.get(v) not in _ids], [])
check("the tabs are the mockups' five, in their order",
      _re_nav.findall(r'^  \{ id: "([a-z]+)", label: "[A-Za-z]+", icon:', _main, _re_nav.M),
      ["home", "navigation", "cameras", "vehicle", "agent"])
# `=(?!=)`: an assignment, not the `===` comparisons in goto() and honourAsk().
# One writer today (a623a17: goto()), and it must stay one.
check("nothing but a tap writes the hash: goto() is the only writer in main.js",
      len(_re_nav.findall(r"location\.hash\s*=(?!=)", _main)), 1)
```

- [ ] **Step 2: Run them to see them fail**

Run: rsync, then `python3 test/js_test.py`, then `python3 test/guards_test.py`.

Expected:
- `provenance.test.js` fails on import.
- The guard fails "the tabs are the mockups' five" (it finds `car, faults, data, care, ai`).

- [ ] **Step 3: Write provenance.js, placeholder.js and the three placeholder views**

`share/js/provenance.js`:

```js
// Where a number came from, in words.
//
// The badge in the top bar and the line under Home say this, and they come
// from one function each so the two can never disagree. The failure that
// matters is reading invented numbers as your own car's, so the simulator and
// the bench are named before anything else.

export function badge(car, live, serverError) {
  if (serverError && !car) {
    return { text: "NO SERVER", tone: "bad",
             title: "Cannot reach the OmaCar server — omacar server status" };
  }
  if (!car) return { text: "STARTING", tone: "", title: "Waiting for the first snapshot" };
  const s = live || car.live || {};
  if (car.simulated) {
    return { text: "SIMULATED", tone: "warn",
             title: "Simulated by omacar-sim. None of these numbers are your car's." };
  }
  if (s.kind === "bench") {
    return { text: "BENCH", tone: "warn",
             title: "A real adapter path talking to an emulator, not a car." };
  }
  if (s.connected) {
    return { text: "LIVE · OBD-II", tone: "ok",
             title: s.protocol ? `Live from the car · ${s.protocol}` : "Live from the car" };
  }
  return { text: "NO CAR", tone: "",
           title: "The adapter has not answered. Anything shown is the last thing recorded." };
}

export function footerLine(car, live) {
  if (!car) return "";
  const s = live || car.live || {};
  const parts = ["OBD-II"];
  if ((car.signals || []).length) parts.push("Honda enhanced");
  const how = car.simulated ? "simulated"
    : s.kind === "bench" ? "bench emulator"
    : s.connected ? "live" : "last recorded";
  return `Vehicle data: ${parts.join(" + ")} · ${how}`;
}

// The data-src a tile carries when it draws a number. Never empty: a drawn
// number always came from somewhere, and test/app_test.py checks that it says so.
export function sourceKey(car, live) {
  const s = live || (car && car.live) || {};
  if (car && car.simulated) return "sim";
  if (s.kind === "bench") return "bench";
  if (s.connected) return "obd";
  return "recorded";
}
```

`share/js/placeholder.js`:

```js
// A screen that is coming, saying what it will do and which step builds it.
// No sample map, no fake footage, no pretend sessions: an empty screen that
// tells the truth is better than a full one that does not.
import { h, icon } from "./core.js";

export function placeholder({ icon: ico, title, step, lines }) {
  return h("div.card.ph-card",
    h("div.ph-icon", icon(ico, 40)),
    h("div.ph-title", title),
    h("div.ph-step", step),
    ...lines.map((l) => h("p.ph-line", l)));
}
```

`share/js/views/navigation.js`:

```js
import { ICONS } from "../icons.js";
import { placeholder } from "../placeholder.js";

export default function navigationView(root) {
  root.appendChild(placeholder({
    icon: ICONS.nav, title: "Navigation", step: "Coming in step 3 of the redesign",
    lines: ["Offline maps of California with turn-by-turn and spoken directions, "
            + "working with no signal.",
            "It needs a GPS receiver plugged into the tablet: the Surface has none."],
  }));
}
```

`share/js/views/cameras.js`:

```js
import { ICONS } from "../icons.js";
import { placeholder } from "../placeholder.js";

export default function camerasView(root) {
  root.appendChild(placeholder({
    icon: ICONS.camera, title: "Cameras", step: "Coming in step 2 of the redesign",
    lines: ["Front, rear and cabin cameras wired to the tablet, recorded in one-minute "
            + "clips that loop, with hard braking locked from the car's own speed.",
            "Nothing is recorded yet."],
  }));
}
```

`share/js/views/work.js`:

```js
import { ICONS } from "../icons.js";
import { placeholder } from "../placeholder.js";

export default function workView(root) {
  root.appendChild(placeholder({
    icon: ICONS.agent, title: "Work", step: "Coming in step 4 of the redesign",
    lines: ["Your coding sessions on your Omarchy machines: what each is doing, "
            + "which one needs you, and a spoken instruction when you are driving.",
            "Voice only while the car moves. Reviews wait until you are parked."],
  }));
}
```

- [ ] **Step 4: Restructure the navigation in main.js**

1. **Imports.** Add these next to the other view imports:

```js
import navigationView from "./views/navigation.js";
import camerasView from "./views/cameras.js";
import workView from "./views/work.js";
import { badge } from "./provenance.js";
```

2. **The tab table.** Replace the whole `const TABS = [ … ];` with the version below. `home` still mounts the old `hub` until Task 6. Vehicle → Overview mounts `dash` until Task 8. The comment block above `TABS` stays, with one sentence added: "Vehicle groups its screens (Overview · Diagnose · Live · Hybrid · Care · Car); every other tab lists them."

```js
const TABS = [
  { id: "home", label: "Home", icon: ICONS.home,
    views: [
      { id: "home", label: "Home", title: "Home", mount: hub, fast: true },
    ] },
  { id: "navigation", label: "Navigation", icon: ICONS.nav,
    views: [
      { id: "navigation", label: "Navigation", title: "Navigation", mount: navigationView },
    ] },
  { id: "cameras", label: "Cameras", icon: ICONS.camera,
    views: [
      { id: "cameras", label: "Cameras", title: "Cameras", mount: camerasView },
    ] },
  { id: "vehicle", label: "Vehicle", icon: ICONS.vehicle,
    groups: [
      { id: "overview", label: "Overview", views: [
        { id: "vehicle", label: "Overview", title: "Vehicle diagnostics", mount: dash, fast: true },
      ] },
      { id: "diagnose", label: "Diagnose", views: [
        { id: "codes",  label: "Codes",     title: "Trouble codes",                mount: codes, fast: true },
        { id: "scan",   label: "Scan",      title: "Full system scan",             mount: scan },
        { id: "health", label: "Readiness", title: "Readiness and on-board tests", mount: health },
      ] },
      { id: "live", label: "Live", views: [
        { id: "drive",  label: "Gauges",   title: "Drive mode",              mount: drive,      fast: true },
        { id: "live",   label: "Cluster",  title: "Cluster",                 mount: live,       fast: true },
        { id: "data",   label: "Live lab", title: "Data lab",                mount: data,       fast: true },
        { id: "replay", label: "Replay",   title: "Replay a recorded drive", mount: replayView, tier: "power" },
        // Actuator commands. Two taps from the bar, never one, and the chip
        // greys out with the reason on it while the car is moving.
        { id: "tests",  label: "Tests",    title: "Functional tests",        mount: tests, fast: true, write: true, tier: "technician" },
        { id: "write",  label: "Identifiers", title: "Write by identifier",  mount: writeView, write: true, tier: "god" },
      ] },
      // NOT BEHIND A TIER. "How is my battery" is an owner's question.
      { id: "hybrid", label: "Hybrid", views: [
        { id: "ima", label: "Battery", title: "Battery, motor and regen", mount: imaView },
      ] },
      { id: "care", label: "Care", views: [
        { id: "service",   label: "Service", title: "Service schedule",                    mount: service },
        { id: "resets",    label: "Resets",  title: "Service resets and functional tests", mount: resetsView, write: true, fast: true, tier: "technician" },
        { id: "concerns",  label: "Trends",  title: "Areas of concern",                    mount: concernsView, tier: "power" },
        { id: "history",   label: "Log",     title: "Drive history and records",           mount: history },
        { id: "documents", label: "Docs",    title: "Receipts, registrations and records", mount: documentsView },
      ] },
      { id: "car", label: "Car", views: [
        { id: "garage", label: "Profile", title: "Every car you own", mount: garageView },
        { id: "dash",   label: "Summary", title: "Overview",          mount: dash, fast: true },
        { id: "report", label: "Report",  title: "Vehicle report",    mount: report },
      ] },
    ] },
  { id: "agent", label: "Agent", icon: ICONS.agent,
    views: [
      { id: "advisor", label: "Car",  title: "Oma Agent", mount: advisor, ai: true },
      { id: "work",    label: "Work", title: "Work",      mount: workView },
    ] },
];

// One shape downstream: a tab that lists its views is a tab with one group.
for (const t of TABS) {
  t.grouped = !!t.groups;
  if (!t.groups) t.groups = [{ id: t.id, label: t.label, views: t.views }];
  t.views = t.groups.flatMap((g) => g.views.map((v) => Object.assign(v, { tab: t.id, group: g.id })));
}
```

3. **Off the navigation.** Replace `const OFF_NAV = [ … ];` with the version below. Every entry keeps an `off` reason. `askable` marks the ones the voice assistant may still open (see `screens()`).

```js
const OFF_NAV = [
  { id: "learn",  label: "Learn",  title: "Learn the car, and the app",
    mount: learnView, off: "opened from Settings" },
  { id: "themes", label: "Themes", title: "Build a palette of your own",
    mount: themesView, off: "opened from Settings" },
  { id: "effects", label: "Effects", title: "Scanner and Leviathan",
    mount: effectsView, off: "opened from Settings", askable: true },
  // The power-on screen: a destination the tablet is pointed at, and Home's
  // "Begin" button when the car is off. Never something to browse to mid-drive.
  { id: "launcher", label: "Begin", title: "Ready to drive",
    mount: launcherView, off: "opened from Home when the car is off", hidden: true },
  { id: "omaplay", label: "Phone", title: "Your phone, and the car",
    mount: omaplayView, fast: true, off: "opened from Home's phone card", askable: true },
  { id: "music",  label: "Music", title: "Music",
    mount: musicView, fast: true, off: "opened from Home's phone card", askable: true },
  { id: "nursery", label: "Nursery", title: "The baby, from the road",
    mount: nurseryView, nursery: true, fast: true, off: "opened from Home's phone card", askable: true },
];

// Old addresses that still have to land somewhere: a bookmark, the dock card,
// the board. #hub was the home screen for a year.
const ALIASES = {
  hub: "home",
};
```

4. **VIEWS, HOME and route().**
   - Replace `...TABS.flatMap((t) => t.views.map((v) => Object.assign(v, { tab: t.id }))),` with `...TABS.flatMap((t) => t.views),`.
   - Change `const HOME = "hub";` to `const HOME = "home";`.
   - In `route()`, replace its first line with:

```js
  const asked = (location.hash || "#" + HOME).slice(1).split("/")[0];
  const id = ALIASES[asked] || asked;
```

5. **tabBadge().** Replace the function body:

```js
function tabBadge(id) {
  const car = store.car || {};
  if (id === "home") return store.connected ? { text: "", tone: "ok" } : null;
  if (id === "vehicle") {
    const n = (car.active_faults || []).length;
    if (n) return { text: String(n), tone: "bad" };
    if (car.readiness && !car.readiness.ready) return { text: "!", tone: "warn" };
    const due = car.service && car.service.due ? car.service.due : 0;
    return due ? { text: String(due), tone: "warn" } : null;
  }
  return null;
}
```

6. **The chip row and the segment row.** In `buildNav()`, after `els.chips.appendChild(els.here);`, add `els.seg = document.getElementById("segbar");`.

   Then replace the part of `paintNavState()` from `// The chips are rebuilt when the tab changes…` through the end of the `for (const c of els.chips.querySelectorAll(".chip[data-view]")) { … }` loop with the code below. Keep the `els.here` block that sits between them. Keep the scroll-into-view block after them, changing its selector to `'.chip[aria-current="page"]'` within `els.chips` only.

```js
  // THE CHIP ROW SHOWS A TAB'S GROUPS WHEN IT HAS THEM, AND ITS VIEWS WHEN IT
  // DOES NOT. Vehicle has six groups; Agent has two views; a tab with one view
  // has nothing to choose between and no row. Rebuilt when the TAB changes and
  // never otherwise, for the reason at the top of this section.
  if (chipsFor !== tab) {
    chipsFor = tab;
    for (const old of els.chips.querySelectorAll(".chip[data-key]")) old.remove();
    const t = TABS.find((x) => x.id === tab);
    for (const c of chipItems(t)) els.chips.appendChild(chipButton(c.key, c.label, c.target, c.write));
    // Home has no row at all, decided per TAB so it can only change with a tab
    // tap. Every other tab keeps the row, because the lead chip ("Car connected
    // -- open Home") can appear there at any moment and must not move anything.
    document.getElementById("app").dataset.subbar = (tab === "home" && !cameFrom) ? "0" : "1";
  }

  // THE SEGMENT ROW: the screens inside the current group, when there is more
  // than one. Rebuilt when the GROUP changes.
  const t = TABS.find((x) => x.id === tab);
  const group = t && t.grouped && here ? here.group : null;
  if (segFor !== group) {
    segFor = group;
    clear(els.seg);
    const g = group ? t.groups.find((x) => x.id === group) : null;
    const vis = g ? g.views.filter((v) => !hiddenView(v)) : [];
    els.seg.hidden = vis.length < 2;
    if (vis.length > 1) for (const v of vis) els.seg.appendChild(chipButton("v:" + v.id, v.label, v, !!v.write));
  }

  els.here.hidden = !(here && here.off);
  if (here && here.off) els.here.textContent = here.title;

  const driving = store.state === "driving";
  for (const c of [...els.chips.querySelectorAll(".chip[data-key]"),
                   ...els.seg.querySelectorAll(".chip[data-key]")]) {
    const key = c.dataset.key;
    const isGroup = key.startsWith("g:");
    const cur = here && (isGroup ? here.group === key.slice(2) : here.id === key.slice(2));
    if (cur) c.setAttribute("aria-current", "page");
    else c.removeAttribute("aria-current");
    // Write-capable screens grey out while the car is moving; they never
    // vanish, and THE LABEL DOES NOT CHANGE (see the history of this loop).
    const v = VIEWS.find((x) => x.id === c.dataset.view);
    const block = !isGroup && !!(v && v.write) && driving;
    c.disabled = block;
    c.classList.toggle("chip-locked", block);
    c.setAttribute("aria-disabled", block ? "true" : "false");
    const why = block ? "Available when you stop" : "";
    if (c.title !== why) c.title = why;
  }
```

   Add these helpers above `paintNavState()`, and add `let segFor = null;` next to `let chipsFor = null;`:

```js
// What the chip row offers for a tab: its groups (by their first visible
// screen), or its screens when it has no groups and more than one of them.
function chipItems(t) {
  if (!t) return [];
  if (t.grouped) {
    return t.groups
      .map((g) => ({ key: "g:" + g.id, label: g.label,
                     target: g.views.find((v) => !hiddenView(v)), write: false }))
      .filter((c) => c.target);
  }
  const vis = t.views.filter((v) => !hiddenView(v));
  return vis.length > 1
    ? vis.map((v) => ({ key: "v:" + v.id, label: v.label, target: v, write: !!v.write }))
    : [];
}

function chipButton(key, label, target, write) {
  // data-write marks the chips that can ever lock, so the stylesheet reserves
  // the lock glyph's space on those and only those.
  return h("button.chip", {
    type: "button",
    data: write ? { key, view: target.id, write: "1" } : { key, view: target.id },
    onclick: () => goto(target.id),
  }, label);
}
```

7. **paintLead().** Change the two strings to `"Car moving — open Home"` and `"Car connected — open Home"`.

8. **screens().** Change the filter to `VIEWS.filter((v) => (!v.off || v.askable) && !hiddenView(v))`.

9. **The top bar.** Replace `buildBar()` and `paintBar()`, and delete `paintDriveMode()` and `refreshDriveMode()` (drive mode moves to Home's speed dial in Task 6). Keep `loadThemeModes`, `paintDayNight`, `toggleDayNight`, `GEAR` and the settings sheet exactly as they are.

```js
// ------------------------------------------------------------- the top bar
//
// OmaCar | the page on the left, the clock in the middle, and on the right the
// car and where its numbers came from. The badge never leaves the screen: the
// failure that matters is reading the simulator's numbers as your own car's.
//
// THE CAR IS ALWAYS NAMED. On Home it is the page title ("OmaCar | CR-Z", as
// the mockups have it); everywhere else it sits at the right. Every scan tool
// that has shown somebody the wrong car's data got there by letting it slip.
function buildBar() {
  if (els.page) return;
  const bar = document.getElementById("vbar");
  els.page = h("span.tb-page");
  bar.appendChild(h("div.tb-left", h("span.tb-mark", "OmaCar"),
                    h("span.tb-div", { "aria-hidden": "true" }), els.page));
  els.clock = h("div.tb-clock.display-num");
  bar.appendChild(els.clock);

  const right = h("div.tb-right");
  els.name = h("span.tb-car");
  els.src = h("span.tb-src");
  // Visible, not subtle. The failure that matters is thinking you are private
  // when you are not.
  els.priv = h("button.pill.info.privacy-pill", {
    type: "button", hidden: true,
    title: "Identifying details are hidden. Tap to show them again.",
    onclick: () => { privacy.on = false; paintBar(); go(); },
  }, "VIN hidden");
  right.append(els.name, els.src, els.priv);

  els.daynight = h("button.vbar-btn", {
    type: "button", id: "btn-daynight", hidden: true,
    onclick: () => toggleDayNight(),
  }, icon(ICONS.moon, 20));
  right.appendChild(els.daynight);
  loadThemeModes();

  els.orb = h("button.vbar-btn", {
    type: "button", id: "btn-assistant", hidden: true,
    "aria-label": "Ask the assistant", title: "Ask the assistant",
    onclick: async () => {
      try {
        const r = await api.assistant("toggle");
        if (!r.present) { els.orb.hidden = true; toast(r.error, "bad"); }
      } catch (e) { toast(String((e && e.message) || e), "bad"); }
    },
  }, icon(ICONS.advisor, 20));
  right.appendChild(els.orb);
  api.assistant("present").then((r) => {
    if (r && r.present) els.orb.hidden = false;
  }).catch(() => { /* an older server, or none: no button */ });

  right.appendChild(h("button.vbar-btn", {
    type: "button", id: "btn-settings",
    "aria-label": "Settings", title: "Units, privacy, looks, learn mode",
    onclick: openSettings,
  }, icon(GEAR, 20)));
  bar.appendChild(right);

  tickClock();
  setInterval(tickClock, 15000);
}

// 9:41, not 9:41 AM: the mockups' clock, and the one a dashboard shows.
function tickClock() {
  const s = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
    .replace(/\s?[AP]M$/i, "");
  if (els.clock.textContent !== s) els.clock.textContent = s;
}

function paintBar() {
  buildBar();
  const car = store.car;
  const b = badge(car, store.live, store.error);
  if (els.src.textContent !== b.text) els.src.textContent = b.text;
  const cls = "tb-src" + (b.tone ? " " + b.tone : "");
  if (els.src.className !== cls) els.src.className = cls;
  els.src.title = b.title;

  const onHome = current && current.id === HOME;
  const model = car ? ((car.vehicle && car.vehicle.model) || car.title || "") : "";
  const page = onHome ? (model || "Home") : (current ? current.title : "");
  if (els.page.textContent !== page) els.page.textContent = page;
  const who = car ? (car.title || car.name || "") : "";
  els.name.hidden = onHome || !who;
  if (els.name.textContent !== who) els.name.textContent = who;
  els.priv.hidden = !privacy.on;
}
```

   In `go()`, after `paintNavState();`, add `paintBar();`, so the page title follows the navigation.

10. **The settings sheet.** After the "Learn the car, and the app" row, add:

```js
    rows.appendChild(row("Effects", "Scanner and Leviathan, full screen",
      "Open", () => { close(); goto("effects"); }));
```

- [ ] **Step 5: The page and the stylesheet**

In `share/app.html`, add the segment row between the header and the stage:

```html
  <header class="vbar" id="vbar"></header>

  <!-- The screens inside a Vehicle group (Diagnose: Codes, Scan, Readiness).
       Built by main.js, hidden whenever the group has only one screen. -->
  <div class="segbar" id="segbar" hidden></div>

  <main class="stage" id="stage" tabindex="-1"></main>
```

In `share/css/app.css`:
- Change the `.app` grid to four rows:

```css
  grid-template-rows: var(--vbar) auto 1fr var(--nav);
  grid-template-areas: "vbar" "segbar" "stage" "navbar";
```

- Replace the rules from `.vbar {` through `.vbar .odo { … }` (the `.id`, `.name`, `.sub`, `.spacer`, `.stat` and `.odo` rules) with the following. Keep the `.vbar-btn` rules.

```css
/* ------------------------------------------------------------ the top bar
   Three columns, so the clock is centred on the SCREEN rather than on
   whatever the two sides leave it. */
.vbar {
  grid-area: vbar;
  display: grid; grid-template-columns: 1fr auto 1fr; align-items: center;
  gap: 16px; padding: 0 20px;
  background: var(--ground); border-bottom: 1px solid var(--hair);
}
.tb-left { display: flex; align-items: center; gap: 14px; min-width: 0; }
.tb-mark { font-family: var(--display); font-weight: 600; font-size: 1.3rem;
           letter-spacing: -.01em; color: var(--ink); }
.tb-div { width: 1px; height: 22px; background: var(--edge-2); }
.tb-page { font-size: 1.05rem; color: var(--ink-2); white-space: nowrap;
           overflow: hidden; text-overflow: ellipsis; }
.tb-clock { font-size: 1.25rem; font-weight: 500; color: var(--ink); }
.tb-right { display: flex; align-items: center; justify-content: flex-end;
            gap: 10px; min-width: 0; }
.tb-car { font-size: .78rem; color: var(--ink-2); white-space: nowrap;
          overflow: hidden; text-overflow: ellipsis; }
.tb-src { font-size: .68rem; letter-spacing: .12em; color: var(--dim);
          padding: 4px 8px; border: 1px solid var(--edge-2); border-radius: 6px;
          white-space: nowrap; }
.tb-src.ok   { color: var(--ok);   border-color: color-mix(in srgb, var(--ok) 45%, transparent); }
.tb-src.warn { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 45%, transparent); }
.tb-src.bad  { color: var(--bad);  border-color: color-mix(in srgb, var(--bad) 45%, transparent); }

/* --------------------------------------------------- the segment row */
.segbar { grid-area: segbar; display: flex; gap: 8px; padding: 8px 20px;
          overflow-x: auto; background: var(--ground); border-bottom: 1px solid var(--hair); }
.segbar[hidden] { display: none; }
.segbar::-webkit-scrollbar { width: 0; height: 0; }

/* Home has no chip row. Decided per tab (main.js), so it never changes
   under a thumb. */
.app[data-subbar="0"] { --nav: calc(var(--tabbar) + var(--safe-b)); }
.app[data-subbar="0"] .subbar { display: none; }

/* ------------------------------------------------------- placeholders */
.ph-card { max-width: 640px; margin: 8vh auto 0; padding: 40px; text-align: center; }
.ph-icon { color: var(--accent); }
.ph-title { font-family: var(--display); font-size: 1.8rem; font-weight: 600;
            color: var(--ink); margin-top: 12px; }
.ph-step { font-size: .8rem; letter-spacing: .1em; text-transform: uppercase;
           color: var(--dim); margin: 6px 0 18px; }
.ph-line { color: var(--ink-2); line-height: 1.5; margin: 8px 0; }

/* ------------------------------------------------------------- motion
   State changes fade over 160 ms. Nothing slides or grows: on a screen read
   at speed, the only things that move are values. */
.card, .chip, .tab, .btn, .tb-src, .callout {
  transition: background-color .16s ease, color .16s ease, border-color .16s ease, opacity .16s ease;
}
@media (prefers-reduced-motion: reduce) {
  .card, .chip, .tab, .btn, .tb-src, .callout { transition: none; }
}
```

- Change the active tab to the mockups' accent label and glowing underline, by adding after the existing `.tab[aria-current="page"] .tab-in` rule:

```css
.tab[aria-current="page"] { color: var(--accent); }
.tab[aria-current="page"] .tab-in { background: transparent; }
.tab[aria-current="page"]::before {
  top: auto; bottom: 6px; left: 32%; right: 32%; height: 3px; border-radius: 3px;
  background: var(--accent);
  box-shadow: 0 0 12px 1px color-mix(in srgb, var(--accent) 55%, transparent);
}
```

- [ ] **Step 6: The two text references to the hub**

- `share/js/views/music.js`: the button `onclick: () => { location.hash = "#hub"; } }, "Workshop"` becomes `onclick: () => { location.hash = "#home"; } }, "Home"`.
- `share/js/onboard.js`: in the two strings at lines ~84–87, "switches to the car hub" becomes "switches to Home", and "offers you the hub" becomes "offers you Home".

- [ ] **Step 7: Run everything**

Run `BOXTEST`.

Expected:
- `guards_test.py`: the new section passes.
- `js_test.py`: provenance passes.
- `app_test.py`: passes, because Home still mounts the hub, so its `.hub-vital` probe still works; "the vehicle bar rendered" matches `vbar`; "a mounted view" matches `data-view` on the chips.

- [ ] **Step 8: Look at it**

Take a quick screenshot to catch layout breakage (the full screenshot tool arrives in Task 11):

```bash
ssh jmyers@omarchy 'cd ~/Projects/.omacar-test/foundation && (python3 lib/serve.py 7599 share >/dev/null 2>&1 &) && sleep 1 && chromium --headless=new --disable-gpu --hide-scrollbars --window-size=1368,968 --virtual-time-budget=6000 --screenshot=/tmp/frame.png "http://127.0.0.1:7599/app.html#codes"; pkill -f "serve.py 7599"'
scp jmyers@omarchy:/tmp/frame.png /private/tmp/claude-501/-Users-jmyers-omgarchy/ca0e3dbc-7045-4968-8cb1-198f8012b2c5/scratchpad/frame.png
```

Check: five tabs at the bottom with Vehicle lit; chips `Overview · Diagnose · Live · Hybrid · Care · Car` with Diagnose current; the segment row `Codes · Scan · Readiness`; the top bar reads `OmaCar | Trouble codes`, then the clock, then the car and the badge.

- [ ] **Step 9: Commit**

```bash
git add share/js/main.js share/app.html share/css/app.css share/js/provenance.js share/js/placeholder.js share/js/views/navigation.js share/js/views/cameras.js share/js/views/work.js share/js/views/music.js share/js/onboard.js test/js/provenance.test.js test/guards_test.py
git commit -m "Five tabs a driver would name, with every old screen still one tap from where it belongs" -m "Home, Navigation, Cameras, Vehicle and Agent replace Car, Faults, Data, Care and AI. Vehicle groups the workshop screens into Overview, Diagnose, Live, Hybrid, Care and Car. Navigation, Cameras and Work say what is coming instead of showing anything made up. The top bar always names the car and where its numbers came from.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Private assets: in the app, not in the public repo

**Files:**
- Modify: `.gitignore`
- Create: `share/assets/manifest.json`, `lib/assets.py`, `share/js/assets.js`, `tools/color_to_alpha.py`
- Modify: `lib/api.py` (GET `/api/assets`), `share/js/core.js` (`api.assets`), `bin/omacar` (the `assets` subcommand), `lib/doctor.py` (one section)
- Test: new section in `test/guards_test.py`

**Interfaces:**
- Produces:
  - `GET /api/assets → { name: { url: "assets/private/<file>" | null, why, anchors: {sys: [x, y]}, width, height } }`. Anchors are fractions of the image.
  - `assets.js` exports `asset(name) → Promise<entry|null>`.
  - `lib/assets.py` exports `status()`, `public_view()`, `pin(name)`, `png_size(path)`, `sha256(path)`.
  - The CLI `omacar assets status|pin NAME|push [--to DEST]|sync [--from SRC]`.

- [ ] **Step 1: Write the failing guard tests**

Append to `test/guards_test.py`, before the done block:

```python
# ------------------------------------------------------------ private assets
head("Honda-badged pictures ship in the app and never in the repo")
import json as _json_a  # noqa: E402
import shutil as _sh_a  # noqa: E402
import subprocess as _sp_a  # noqa: E402
import tempfile as _tf_a  # noqa: E402
import assets as _as  # noqa: E402

_gi = open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read().splitlines()
check("the private folder is ignored", "share/assets/private/" in _gi, True)
_tracked = _sp_a.run(["git", "-C", ROOT, "ls-files", "share/assets/private"],
                     capture_output=True, text=True)
if _tracked.returncode == 0:
    check("and nothing under it is tracked", _tracked.stdout.split(), [])
else:
    ok("(not a git checkout: the tracked-files check is skipped)")

_d = _tf_a.mkdtemp()
_png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (640).to_bytes(4, "big") + (480).to_bytes(4, "big") + b"\x08\x06\x00\x00\x00"
with open(os.path.join(_d, "a.png"), "wb") as _f:
    _f.write(_png)
_m = {"assets": {
    "here": {"file": "a.png", "sha256": None, "anchors": {"engine": [0.3, 0.5]}},
    "gone": {"file": "missing.png", "sha256": None},
    "altered": {"file": "a.png", "sha256": "0" * 64},
}}
_st = _as.status(_m, _d)
check("a present, unpinned file is usable", (_st["here"]["ok"], _st["here"]["why"]), (True, "not pinned"))
check("a missing file says so", (_st["gone"]["ok"], _st["gone"]["why"]), (False, "not installed"))
check("a file that does not match its pin is refused",
      (_st["altered"]["ok"], _st["altered"]["why"]), (False, "does not match the manifest"))
_pv = _as.public_view(_m, _d)
check("the browser gets a URL only for a file that checks out",
      [_pv["here"]["url"], _pv["gone"]["url"], _pv["altered"]["url"]],
      ["assets/private/a.png", None, None])
check("and the callout anchors ride along", _pv["here"]["anchors"], {"engine": [0.3, 0.5]})
check("a PNG's size is read from its header", _as.png_size(os.path.join(_d, "a.png")), (640, 480))
_mf = os.path.join(_d, "manifest.json")
with open(_mf, "w", encoding="utf-8") as _f:
    _json_a.dump({"assets": {"here": {"file": "a.png", "sha256": None}}}, _f)
_as.pin("here", _mf, _d)
_pinned = _json_a.load(open(_mf, encoding="utf-8"))["assets"]["here"]
check("pin records the hash and the size",
      (_pinned["sha256"] == _as.sha256(os.path.join(_d, "a.png")), _pinned["width"], _pinned["height"]),
      (True, 640, 480))
_sh_a.rmtree(_d)
check("the shipped manifest parses and names both car pictures",
      sorted(_as.load_manifest()["assets"]), ["crz-home", "crz-xray"])
```

- [ ] **Step 2: Run it to see it fail**

Run: rsync, then `python3 test/guards_test.py`. Expected: ImportError on `import assets`. The suite stops there, which counts as a failure.

- [ ] **Step 3: Write lib/assets.py, the manifest and .gitignore**

Append to `.gitignore`:

```
# Pictures that carry Honda's badge: shipped to the tablet by
# `omacar assets push`, never committed (the repo is public). See
# share/assets/manifest.json and lib/assets.py.
share/assets/private/
```

`share/assets/manifest.json`. The xray anchors are fractions of the 1504×1046 render, measured on the owner's image 1: engine block, IMA battery, front-right brake disc, steering wheel, rear-right tyre. Task 8 checks them on screen.

```json
{
  "_comment": "Pictures that ship in the app but not in this public repository, because they carry Honda's badge. The files live in share/assets/private/ (git-ignored). `omacar assets status` checks them against this list; `omacar assets pin NAME` records a file's hash; `omacar assets push` copies them to the tablet. Anchors are fractions of the image, [x, y] from the top left.",
  "assets": {
    "crz-xray": {
      "file": "crz-xray.png",
      "use": "Vehicle: the X-ray render, from the owner's mockup kit (image 1), colour-to-alpha against its white ground by tools/color_to_alpha.py",
      "sha256": null,
      "width": 1504,
      "height": 1046,
      "anchors": {
        "engine":   [0.30, 0.53],
        "hybrid":   [0.72, 0.56],
        "brakes":   [0.51, 0.77],
        "steering": [0.57, 0.36],
        "tyres":    [0.90, 0.62]
      }
    },
    "crz-home": {
      "file": "crz-home.png",
      "use": "Home: the owner's transparent picture of the car (to come). Until it is installed Home draws a placeholder.",
      "sha256": null,
      "anchors": {
        "tyres": [0.50, 0.90]
      }
    }
  }
}
```

`lib/assets.py`:

```python
#!/usr/bin/env python3
"""Private assets: pictures that ship in the app but not in the public repo.

omauilabs/omacar is public, and the car renders carry Honda's badge. The owner
wants them in the app and not in git, so they live in share/assets/private/,
which .gitignore excludes, and the committed share/assets/manifest.json names
each one: its file, what it is for, where its callouts point and, once pinned,
its SHA-256. A missing or altered file is reported rather than drawn broken,
and the app falls back to a placeholder.

    omacar assets status             what is here, against the manifest
    omacar assets pin NAME           record the present file's hash and size
    omacar assets push [--to DEST]   copy this machine's files to the tablet
    omacar assets sync [--from SRC]  copy them here from somewhere else

Stdlib only; the copies are rsync over ssh.
"""

import hashlib
import json
import os
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "share", "assets", "manifest.json")
PRIVATE = os.path.join(ROOT, "share", "assets", "private")
# The tablet as the Omarchy box's ~/.ssh/config names it (Host omacar), and the
# box as the tablet can reach it over the tailnet.
DEFAULT_TO = "omacar:Projects/omacar/share/assets/private/"
DEFAULT_FROM = "jmyers@omarchy:Projects/omacar/share/assets/private/"
URL_BASE = "assets/private/"


def load_manifest(path=MANIFEST):
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc.get("assets"), dict):
        raise ValueError("the manifest has no `assets` object")
    return doc


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def png_size(path):
    """(width, height) from a PNG's IHDR, or None when it is not a PNG."""
    with open(path, "rb") as f:
        head = f.read(24)
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def status(manifest=None, private=PRIVATE):
    """{name: {file, present, ok, why}} for every asset the manifest names."""
    m = manifest if manifest is not None else load_manifest()
    out = {}
    for name, a in m["assets"].items():
        f = a.get("file") or ""
        p = os.path.join(private, f)
        row = {"file": f, "present": False, "ok": False, "why": "not installed"}
        if f and os.path.isfile(p):
            row["present"] = True
            want = a.get("sha256")
            if want and sha256(p) != want:
                row["why"] = "does not match the manifest"
            else:
                row["ok"] = True
                row["why"] = None if want else "not pinned"
        out[name] = row
    return out


def public_view(manifest=None, private=PRIVATE):
    """What /api/assets hands the browser: a URL only for a file that checks out."""
    m = manifest if manifest is not None else load_manifest()
    st = status(m, private)
    return {name: {"url": URL_BASE + a["file"] if st[name]["ok"] else None,
                   "why": st[name]["why"],
                   "anchors": a.get("anchors") or {},
                   "width": a.get("width"),
                   "height": a.get("height")}
            for name, a in m["assets"].items()}


def pin(name, path=MANIFEST, private=PRIVATE):
    m = load_manifest(path)
    if name not in m["assets"]:
        raise ValueError(f"no asset called {name!r} in the manifest")
    a = m["assets"][name]
    p = os.path.join(private, a["file"])
    if not os.path.isfile(p):
        raise ValueError(f"{a['file']} is not in {private}")
    a["sha256"] = sha256(p)
    size = png_size(p)
    if size:
        a["width"], a["height"] = size
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return a


def _rsync(src, dest):
    if not src.endswith("/"):
        src += "/"
    return subprocess.run(["rsync", "-a", "--chmod=F644,D755", src, dest]).returncode


def _opt(args, flag, default):
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args):
            return args[i + 1]
    return default


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    args = argv[2:]
    if cmd == "status":
        missing = 0
        for name, s in status().items():
            note = f"  ({s['why']})" if s["why"] else ""
            print(f"  {'ok' if s['ok'] else '--'}  {name:<10} {s['file']}{note}")
            missing += 0 if s["ok"] else 1
        return 1 if missing else 0
    if cmd == "pin":
        if not args:
            print("usage: omacar assets pin NAME", file=sys.stderr)
            return 2
        a = pin(args[0])
        print(f"  pinned {args[0]}: {a['sha256'][:16]}…")
        return 0
    if cmd == "push":
        os.makedirs(PRIVATE, exist_ok=True)
        return _rsync(PRIVATE, _opt(args, "--to", DEFAULT_TO))
    if cmd == "sync":
        os.makedirs(PRIVATE, exist_ok=True)
        return _rsync(_opt(args, "--from", DEFAULT_FROM), PRIVATE + "/")
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

- [ ] **Step 4: Run the guards green**

Run: rsync, then `python3 test/guards_test.py`. Expected: the new section passes.

- [ ] **Step 5: Wire the route, the client, the CLI and doctor**

- `lib/api.py` `handle_get`, next to `/api/drive`:

```python
    if path == "/api/assets":
        # Pictures that ship outside git. A URL only for a file that checks out
        # against share/assets/manifest.json; see lib/assets.py.
        import assets
        return 200, assets.public_view()
```

- `share/js/core.js` `api`: add `assets: () => req("/api/assets"),`.

- `share/js/assets.js`:

```js
// The private pictures (lib/assets.py): asked for once per page, because they
// only change when somebody runs `omacar assets push`.
import { api } from "./core.js";

let all = null;

export function asset(name) {
  if (!all) all = api.assets().catch(() => ({}));
  return all.then((a) => (a && a[name]) || null);
}
```

- `bin/omacar`: in the `case`, after the `drivemode)` line, add:

```bash
  assets) shift; exec python3 "$ROOT/lib/assets.py" "$@" ;;
```

- `lib/doctor.py`: before `os.makedirs(connect.STATE, exist_ok=True)` near the end of `main()`, add:

```python
    # The pictures that ship outside git (lib/assets.py). Not the car's
    # business, but doctor is where "why is Home showing a placeholder" is asked.
    try:
        import assets as _assets
        print(f"  {BOLD}Private assets{RESET}")
        print()
        for _name, _s in _assets.status().items():
            _mark = f"{GREEN}ok{RESET}" if _s["ok"] else f"{RED}{_s['why']}{RESET}"
            print(f"    {_name:<12} {_mark}")
        print()
    except Exception as _why:                                  # noqa: BLE001
        print(f"  {DIM}private assets not checked ({type(_why).__name__}){RESET}\n")
```

- [ ] **Step 6: The conversion tool, and the X-ray render itself**

`tools/color_to_alpha.py`:

```python
#!/usr/bin/env python3
"""Recover a translucent render from a picture of it on a flat white ground.

The X-ray car in the owner's mockup kit is a render on white, and its body
panels are see-through: a flood fill or a threshold would keep a white halo
round every panel or punch holes in them. Colour-to-alpha is the inverse of
compositing onto white, pixel by pixel, so a panel that was 40% opaque comes
out 40% opaque and lands correctly on OmaCar's dark ground.

For a white ground the maths is short. A pixel c was made as
    c = a*f + (1 - a)*255
and the most transparent answer that explains it has a = (255 - min(c)) / 255,
so f = 255 - (255 - c) / a for each channel.

Needs Pillow, which is why it is in tools/ and not lib/: it runs on the machine
that prepares assets (the Mac), never on the tablet.

    tools/color_to_alpha.py IN OUT
"""

import sys

from PIL import Image

# JPEG and WebP grounds are never quite 255. Anything this close to white is
# ground, not a panel -- without the floor the whole canvas gets a faint haze.
FLOOR = 6


def color_to_alpha_white(img):
    src = img.convert("RGB")
    out = []
    for r, g, b in src.getdata():
        a = 255 - min(r, g, b)
        if a <= FLOOR:
            out.append((0, 0, 0, 0))
            continue
        k = 255.0 / a
        out.append((max(0, min(255, round(255 - (255 - r) * k))),
                    max(0, min(255, round(255 - (255 - g) * k))),
                    max(0, min(255, round(255 - (255 - b) * k))),
                    a))
    res = Image.new("RGBA", src.size)
    res.putdata(out)
    return res


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    color_to_alpha_white(Image.open(argv[1])).save(argv[2], optimize=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

Check the maths, then make the render. Run this on the Mac: it has Pillow 12.3, and the owner's images are in this session's scratch folder.

```bash
cd /Users/jmyers/omgarchy/omacar
python3 -c "
from PIL import Image
import importlib.util, sys
spec = importlib.util.spec_from_file_location('c2a', 'tools/color_to_alpha.py'); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
im = Image.new('RGB', (3, 1)); im.putdata([(255, 255, 255), (0, 0, 0), (128, 128, 128)])
print(list(m.color_to_alpha_white(im).getdata()))"
```

Expected: `[(0, 0, 0, 0), (0, 0, 0, 255), (0, 0, 0, 127)]`. White becomes clear, black stays opaque black, and mid-grey becomes half-transparent black.

```bash
IMG=/private/tmp/claude-501/-Users-jmyers-omgarchy/ca0e3dbc-7045-4968-8cb1-198f8012b2c5/images/1.webp
mkdir -p share/assets/private && python3 tools/color_to_alpha.py "$IMG" share/assets/private/crz-xray.png
python3 lib/assets.py pin crz-xray && python3 lib/assets.py status
ssh jmyers@omarchy 'mkdir -p ~/Projects/omacar/share/assets/private'
scp share/assets/private/crz-xray.png jmyers@omarchy:Projects/omacar/share/assets/private/crz-xray.png
```

Expected: `ok  crz-xray  crz-xray.png`, and `--  crz-home  crz-home.png  (not installed)`. The box's main checkout now holds the canonical private copy. `omacar assets push` from the box delivers it to the tablet in Task 12.

- [ ] **Step 7: Run everything, then commit**

Run `BOXTEST`. Then:

```bash
git add .gitignore share/assets/manifest.json lib/assets.py share/js/assets.js tools/color_to_alpha.py lib/api.py share/js/core.js bin/omacar lib/doctor.py test/guards_test.py
git status --short share/assets   # must list manifest.json only
git commit -m "Pictures with Honda's badge ship in the app through a folder git never sees" -m "The repository is public. share/assets/private/ is ignored; a committed manifest names each picture, pins its hash and carries its callout anchors, and the app gets a URL only for a file that checks out. The X-ray render is colour-to-alpha'd from the owner's image so its see-through panels stay see-through.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Home

**Files:**
- Create: `share/data/home-cards.json`, `share/js/homecards.js`, `share/js/systems.js`, `share/js/sigtile.js`, `share/js/views/home.js`, `share/css/home.css`
- Modify: `share/app.html` (link `home.css`), `share/js/main.js` (mount Home, drop the hub, add the Look row to Settings), `share/css/app.css` and `share/css/fonts.css` (remove the hub's rules)
- Delete: `share/js/views/hub.js`
- Modify: `test/app_test.py` (the geometry probe reads Home's coolant tile)
- Test: `test/js/homecards.test.js`, `test/js/systems.test.js`, `test/js/sigtile.test.js`

**Interfaces:**
- Consumes: `READINGS` and `readingState` (Task 3), `trail` (Task 3), `sparkPath` (Task 3), `sourceKey` and `footerLine` (Task 4), `asset` (Task 5), `makeGauge(kind, def) → {el, update(out, raw)}` from `gauges.js`.
- Produces:
  - `home-cards.json`: `{ cards: { id: { label, reading?, sizes: { name: { land: [cols, rows], port: [cols, rows] } } } }, default: { landscape: [[id, size]…], portrait: [[id, size]…] } }`.
  - `homecards.js`: `loadCatalogue()`, `orientation() → "landscape"|"portrait"`, `spanOf(cat, id, size, orient) → [cols, rows]`, `defaultLayout(cat)`, `moveItem(list, from, to)`, `nextSize(cat, id, size)`.
  - `systems.js`: `SYSTEMS`, `groupSystems(modules, faults) → [{id, label, sub, modules, codes, status}]`, `headline(groups) → {text, tone}`, `worstOf(groups)`, `tyreState(car) → {text, tone}`.
  - `sigtile.js`: `makeSignalTile(id, {label, def}) → {node, paint(car, sample)}`.

- [ ] **Step 1: Write the failing tests**

`test/js/homecards.test.js`:

```js
import { eq } from "./assert.js";
import { spanOf, moveItem, nextSize, defaultLayout } from "../js/homecards.js";

const cat = {
  cards: {
    dial: { label: "Speed", sizes: { m: { land: [3, 3], port: [3, 2] }, l: { land: [4, 3], port: [6, 2] } } },
    coolant: { label: "Coolant", reading: "coolant", sizes: { s: { land: [3, 1], port: [3, 1] } } },
  },
  default: { landscape: [["dial", "m"], ["coolant", "s"]], portrait: [["coolant", "s"], ["dial", "m"]] },
};

export default [
  ["a size spans what the catalogue says, per orientation", () =>
    eq([spanOf(cat, "dial", "l", "landscape"), spanOf(cat, "dial", "l", "portrait")], [[4, 3], [6, 2]])],
  ["an unknown size falls back to the card's first", () => eq(spanOf(cat, "dial", "zz", "landscape"), [3, 3])],
  ["moving an item shifts the rest", () => eq(moveItem(["a", "b", "c", "d"], 0, 2), ["b", "c", "a", "d"])],
  ["moving never loses or duplicates", () => eq(moveItem(["a", "b", "c"], 2, 0), ["c", "a", "b"])],
  ["sizes cycle", () => eq([nextSize(cat, "dial", "m"), nextSize(cat, "dial", "l")], ["l", "m"])],
  ["a card with one size stays at it", () => eq(nextSize(cat, "coolant", "s"), "s")],
  ["the default layout is a copy, with nothing hidden", () => {
    const d = defaultLayout(cat);
    d.landscape.cards[0][1] = "l";
    eq([cat.default.landscape[0][1], d.portrait.hidden], ["m", []]);
  }],
];
```

`test/js/systems.test.js`. The modules are shaped like `lib/sim.py` MODULES:

```js
import { eq } from "./assert.js";
import { groupSystems, headline, tyreState } from "../js/systems.js";

const M = [
  { id: "PGM-FI", name: "Engine (PGM-FI)", system: "Powertrain", codes: ["P0135"] },
  { id: "IMA", name: "IMA Motor & Battery", system: "Hybrid", codes: [] },
  { id: "VSA", name: "VSA / ABS Modulator", system: "Chassis", codes: [] },
  { id: "EPS", name: "Electric Power Steering", system: "Chassis", codes: [] },
  { id: "TPMS", name: "Deflation Warning", system: "Chassis", codes: ["C1B00"] },
  { id: "MICU", name: "Body Control (MICU)", system: "Body", codes: [] },
  { id: "IMOES", name: "Immobiliser / Keyless", system: "Security", codes: [] },
];
const F = [{ code: "P0135", severity: "normal", module: { id: "PGM-FI" } },
           { code: "C1B00", severity: "normal", module: { id: "TPMS" } }];
const by = (gs) => Object.fromEntries(gs.map((g) => [g.id, g.status]));

export default [
  ["each module lands in exactly one system", () => {
    const gs = groupSystems(M, F);
    eq(gs.flatMap((g) => g.modules.map((m) => m.id)).sort(), M.map((m) => m.id).sort());
  }],
  ["codes make a system need attention", () =>
    eq(by(groupSystems(M, F)), { engine: "warn", hybrid: "ok", brakes: "ok", steering: "ok",
                                 tyres: "warn", electrical: "ok", other: "ok" })],
  ["a severe fault is a fault, not a warning", () =>
    eq(by(groupSystems(M, [{ code: "P0135", severity: "high", module: { id: "PGM-FI" } }])).engine, "bad")],
  ["no scan means unknown everywhere, never normal", () =>
    eq(new Set(Object.values(by(groupSystems([], [])))), new Set(["unknown"]))],
  ["the headline counts what needs attention", () =>
    eq(headline(groupSystems(M, F)), { text: "2 systems need attention", tone: "warn" })],
  ["and says all normal only when there was a scan", () =>
    eq([headline(groupSystems(M.map((m) => ({ ...m, codes: [] })), [])).text,
        headline(groupSystems([], [])).text], ["All systems normal", "No full scan yet"])],
  ["the tyre callout follows the deflation warning", () =>
    eq([tyreState({ modules: M, active_faults: F }), tyreState({ modules: [] }),
        tyreState({ modules: M.filter((m) => m.id !== "TPMS"), active_faults: [] })],
       [{ text: "Check", tone: "warn" }, { text: "Not read", tone: "" }, { text: "Not found", tone: "" }])],
];
```

`test/js/sigtile.test.js`:

```js
import { eq, ok } from "./assert.js";
import { makeSignalTile } from "../js/sigtile.js";
import { reset } from "../js/trail.js";

export default [
  ["a live tile draws its number and names its source", () => {
    reset();
    const t = makeSignalTile("coolant", { label: "Coolant" });
    t.paint({ simulated: true }, { connected: true, values: { COOLANT_TEMP: 90 }, supported: ["COOLANT_TEMP"] });
    eq([t.node.dataset.state, t.node.dataset.src], ["live", "sim"]);
    ok(/\d/.test(t.node.querySelector(".sig-v").textContent), "digits drawn");
  }],
  ["a tile the car cannot fill draws words, not a zero", () => {
    const t = makeSignalTile("charge", { label: "Hybrid pack" });
    t.paint({}, { connected: true, values: {}, supported: ["COOLANT_TEMP"] });
    eq([t.node.dataset.state, t.node.querySelector(".sig-v").textContent,
        t.node.querySelector(".sig-note").textContent, t.node.dataset.src],
       ["absent", "", "Not on this car", undefined]);
  }],
  ["with no car it waits", () => {
    const t = makeSignalTile("fuel");
    t.paint(null, { connected: false });
    eq(t.node.querySelector(".sig-note").textContent, "Waiting for the car");
  }],
  ["it carries its own label", () =>
    eq(makeSignalTile("charge", { label: "Hybrid pack" }).node.querySelector(".sig-k").textContent, "Hybrid pack")],
];
```

- [ ] **Step 2: Run them to see them fail**

Run: rsync, then `python3 test/js_test.py`. Expected: three import failures.

- [ ] **Step 3: The catalogue and the pure modules**

`share/data/home-cards.json`:

```json
{
  "_comment": "Every card Home can hold, and how big each size is: [columns, rows] on the 12-column landscape grid and the 6-column portrait grid, rows 96 px. Read by share/js/homecards.js and by lib/homelayout.py, so the browser and the server can never disagree about what a card is. `default` is the mockups' layout.",
  "cards": {
    "dial":     { "label": "Speed",         "sizes": { "m": { "land": [3, 3], "port": [3, 2] }, "l": { "land": [4, 3], "port": [6, 2] } } },
    "car":      { "label": "Car",           "sizes": { "l": { "land": [5, 3], "port": [6, 3] }, "xl": { "land": [6, 3], "port": [6, 4] } } },
    "nav":      { "label": "Navigation",    "sizes": { "m": { "land": [4, 3], "port": [3, 2] }, "s": { "land": [4, 2], "port": [6, 1] } } },
    "coolant":  { "label": "Coolant",       "reading": "coolant",  "sizes": { "s": { "land": [3, 1], "port": [3, 1] }, "m": { "land": [6, 1], "port": [6, 1] } } },
    "volts":    { "label": "12V system",    "reading": "volts",    "sizes": { "s": { "land": [3, 1], "port": [3, 1] }, "m": { "land": [6, 1], "port": [6, 1] } } },
    "fuel":     { "label": "Fuel",          "reading": "fuel",     "sizes": { "s": { "land": [3, 1], "port": [3, 1] }, "m": { "land": [6, 1], "port": [6, 1] } } },
    "charge":   { "label": "Hybrid pack",   "reading": "charge",   "sizes": { "s": { "land": [3, 1], "port": [3, 1] }, "m": { "land": [6, 1], "port": [6, 1] } } },
    "intake":   { "label": "Intake air",    "reading": "intake",   "sizes": { "s": { "land": [3, 1], "port": [3, 1] }, "m": { "land": [6, 1], "port": [6, 1] } } },
    "econ_now": { "label": "Economy",       "reading": "econ_now", "sizes": { "s": { "land": [3, 1], "port": [3, 1] }, "m": { "land": [6, 1], "port": [6, 1] } } },
    "phone":    { "label": "Phone",         "sizes": { "m": { "land": [4, 2], "port": [3, 2] } } },
    "dashcam":  { "label": "Dashcams",      "sizes": { "m": { "land": [4, 2], "port": [3, 2] } } },
    "agent":    { "label": "Oma Agent",     "sizes": { "m": { "land": [4, 2], "port": [6, 2] } } }
  },
  "default": {
    "landscape": [["dial", "m"], ["car", "l"], ["nav", "m"],
                  ["coolant", "s"], ["volts", "s"], ["fuel", "s"], ["charge", "s"],
                  ["phone", "m"], ["dashcam", "m"], ["agent", "m"]],
    "portrait":  [["dial", "m"], ["nav", "m"], ["car", "l"],
                  ["coolant", "s"], ["volts", "s"], ["fuel", "s"], ["charge", "s"],
                  ["phone", "m"], ["dashcam", "m"], ["agent", "m"]]
  }
}
```

`share/js/homecards.js`:

```js
// The cards Home can hold (share/data/home-cards.json, which the server reads
// too), and the small pure operations the layout editor needs.

let catalogue = null;

export function loadCatalogue() {
  if (!catalogue) {
    catalogue = fetch("data/home-cards.json", { cache: "no-store" }).then((r) => {
      if (!r.ok) throw new Error(`home-cards.json: ${r.status}`);
      return r.json();
    });
  }
  return catalogue;
}

export function orientation() {
  return matchMedia("(orientation: portrait)").matches ? "portrait" : "landscape";
}

export function spanOf(cat, id, size, orient) {
  const c = cat.cards[id];
  if (!c) return [3, 1];
  const s = c.sizes[size] || Object.values(c.sizes)[0];
  return s[orient === "portrait" ? "port" : "land"];
}

export function defaultLayout(cat) {
  const out = {};
  for (const o of ["landscape", "portrait"]) {
    out[o] = { cards: cat.default[o].map((x) => x.slice()), hidden: [] };
  }
  return out;
}

export function moveItem(list, from, to) {
  const out = list.slice();
  const [it] = out.splice(from, 1);
  out.splice(Math.max(0, Math.min(out.length, to)), 0, it);
  return out;
}

export function nextSize(cat, id, size) {
  const sizes = Object.keys(cat.cards[id].sizes);
  return sizes[(sizes.indexOf(size) + 1) % sizes.length];
}
```

`share/js/systems.js`:

```js
// Which scan modules make up each system the car screens name, and how each is
// doing. Built from the snapshot's `modules` (what the last full scan found,
// with the codes each one set) and `active_faults`. Nothing here asks the car
// anything, and nothing here says "normal" about a system nobody scanned.

export const SYSTEMS = [
  { id: "engine",     label: "Engine",        sub: "Engine control system",
    match: (m) => m.system === "Powertrain" },
  { id: "hybrid",     label: "Hybrid system", sub: "IMA battery and motor",
    match: (m) => m.system === "Hybrid" },
  { id: "brakes",     label: "Brakes",        sub: "ABS / VSA",
    match: (m) => /^(VSA|ABS)$/i.test(m.id || "") || /\b(abs|vsa|brake)/i.test(m.name || "") },
  { id: "steering",   label: "Steering",      sub: "Electric power steering",
    match: (m) => m.id === "EPS" },
  { id: "tyres",      label: "Tyres",         sub: "Deflation warning",
    match: (m) => m.id === "TPMS" },
  { id: "electrical", label: "Electrical",    sub: "12V system and body",
    match: (m) => m.system === "Body" },
];

const RANK = { unknown: 0, ok: 1, warn: 2, bad: 3 };
const SEVERE = new Set(["high", "critical", "severe"]);

function statusOf(mods, faults) {
  if (!mods.length) return "unknown";
  const ids = new Set(mods.map((m) => m.id));
  const codes = new Set(mods.flatMap((m) => m.codes || []));
  const mine = faults.filter((f) => (f.module && ids.has(f.module.id)) || codes.has(f.code));
  if (mine.some((f) => SEVERE.has(String(f.severity || "").toLowerCase()))) return "bad";
  return mine.length || codes.size ? "warn" : "ok";
}

export function groupSystems(modules, faults) {
  const mods = Array.isArray(modules) ? modules : [];
  const act = Array.isArray(faults) ? faults : [];
  const taken = new Set();
  const out = SYSTEMS.map((s) => {
    const mine = mods.filter((m) => !taken.has(m.id) && s.match(m));
    for (const m of mine) taken.add(m.id);
    return { id: s.id, label: s.label, sub: s.sub, modules: mine,
             codes: mine.flatMap((m) => m.codes || []), status: statusOf(mine, act) };
  });
  const rest = mods.filter((m) => !taken.has(m.id));
  out.push({ id: "other", label: "All other systems",
             sub: rest.length ? `${rest.length} more module${rest.length === 1 ? "" : "s"}` : "None found",
             modules: rest, codes: rest.flatMap((m) => m.codes || []), status: statusOf(rest, act) });
  return out;
}

export function worstOf(groups) {
  return groups.reduce((w, g) => (RANK[g.status] > RANK[w] ? g.status : w), "unknown");
}

export function headline(groups) {
  if (!groups.some((g) => g.modules.length)) return { text: "No full scan yet", tone: "" };
  const need = groups.filter((g) => g.status === "warn" || g.status === "bad");
  if (!need.length) return { text: "All systems normal", tone: "ok" };
  const tone = need.some((g) => g.status === "bad") ? "bad" : "warn";
  return { text: need.length === 1 ? `${need[0].label} needs attention`
                                   : `${need.length} systems need attention`, tone };
}

// The CR-Z has a deflation warning and no per-wheel sensors, so Home's tyre
// callout is one callout with the system's state. See the foundation design,
// "Home", for why the mockups' four temperatures are not drawn.
export function tyreState(car) {
  const mods = (car && car.modules) || [];
  if (!mods.length) return { text: "Not read", tone: "" };
  const g = groupSystems(mods, car.active_faults).find((x) => x.id === "tyres");
  if (!g.modules.length) return { text: "Not found", tone: "" };
  return g.status === "ok" ? { text: "OK", tone: "ok" } : { text: "Check", tone: g.status };
}
```

`share/js/sigtile.js`:

```js
// A signal tile: icon, label, value, unit, range and a sparkline of the last
// minutes. Home and Vehicle use the same one, so the same reading can never
// look different on two screens.
//
// It draws a number only when the reading is live, and then it says where the
// number came from (data-src). Otherwise it draws words: "Not on this car" or
// "Waiting for the car". A zero is never a stand-in.

import { h, icon } from "./core.js";
import { ICONS } from "./icons.js";
import { READINGS, readingState } from "./readings.js";
import { trail } from "./trail.js";
import { sparkPath } from "./spark.js";
import { sourceKey } from "./provenance.js";

const NS = "http://www.w3.org/2000/svg";
const ICON_FOR = { coolant: "thermo", intake: "thermo", ambient: "thermo",
                   volts: "battery", fuel: "fuel", charge: "leaf", ima: "leaf",
                   rpm: "gauge", speed: "gauge" };
const text = (el, s) => { if (el.textContent !== s) el.textContent = s; };

export function makeSignalTile(id, { label, def } = {}) {
  const d = def || READINGS[id];
  if (!d) throw new Error(`no reading called ${id}`);
  const v = h("span.sig-v.display-num"), u = h("span.sig-u"), note = h("span.sig-note");
  const lo = h("span"), hi = h("span");
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 120 28");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(NS, "path");
  svg.appendChild(path);
  const node = h("div.sig", { data: { reading: id } },
    h("div.sig-head", icon(ICONS[ICON_FOR[id]] || ICONS.data, 18), h("span.sig-k", label || d.label)),
    h("div.sig-body",
      h("div.sig-val", v, u, note),
      h("div.sig-spark", svg, h("div.sig-scale", lo, hi))));

  return {
    node,
    paint(car, sample) {
      const s = sample || {};
      const st = readingState(d, s);
      node.dataset.state = st;
      if (st === "live") {
        const out = d.get(s.values || {}, s, car);
        text(v, String(out.v));
        text(u, out.n || "");
        text(note, "");
        node.dataset.tone = out.tone || "";
        node.dataset.src = sourceKey(car, s);
      } else {
        text(v, "");
        text(u, "");
        text(note, st === "absent" ? "Not on this car" : "Waiting for the car");
        node.dataset.tone = "";
        delete node.dataset.src;
      }
      const sc = d.scale ? d.scale() : null;
      text(lo, sc ? tick(sc, sc.min) : "");
      text(hi, sc ? tick(sc, sc.max) : "");
      // The trail is raw (Celsius, volts); the scale is in display units, so
      // each point goes through the same read() the number did.
      const pts = d.pid && d.read
        ? trail(d.pid).map(([t, r]) => [t, d.read({ [d.pid]: r }, s, car)]).filter((p) => p[1] !== null)
        : [];
      path.setAttribute("d", sparkPath(pts, 120, 28, sc ? sc.min : null, sc ? sc.max : null));
    },
  };
}

function tick(sc, x) {
  return sc.tick ? sc.tick(x) : String(Math.round(x));
}
```

- [ ] **Step 4: Run the pure tests green**

Run: rsync, then `python3 test/js_test.py`. Expected: homecards, systems and sigtile all pass.

- [ ] **Step 5: The Home view and its stylesheet**

`share/js/views/home.js`:

```js
// Home: the screen the tablet shows when somebody gets in.
//
// Cards in a grid, in the order and at the sizes the layout says
// (share/data/home-cards.json defines the cards; the owner's arrangement comes
// from the server in the layout editor's task). Every number comes from the same
// readings catalogue drive mode uses and says where it came from: a tile that
// has no value draws words, never a zero.

import { h, clear, icon, store, api } from "../core.js";
import { ICONS } from "../icons.js";
import { READINGS } from "../readings.js";
import { makeGauge } from "../gauges.js";
import { makeSignalTile } from "../sigtile.js";
import { footerLine } from "../provenance.js";
import { asset } from "../assets.js";
import { tyreState } from "../systems.js";
import { loadCatalogue, orientation, spanOf, defaultLayout } from "../homecards.js";

const text = (el, s) => { if (el.textContent !== s) el.textContent = s; };
// Only ever called from a tap: the routing rule in main.js.
const go = (id) => { location.hash = "#" + id; };

function tappable(node, id) {
  node.setAttribute("role", "button");
  node.tabIndex = 0;
  node.addEventListener("click", () => { if (!node.closest(".editing")) go(id); });
  node.addEventListener("keydown", (e) => { if (e.key === "Enter") go(id); });
  return node;
}

// ---- the cards ---------------------------------------------------------------

function dialCard() {
  const speed = READINGS.speed;
  const g = makeGauge("arc", { scale: speed.scale() });
  const rpm = h("div.dial-rpm");
  // A LABEL, NOT A READING: no identifier reports the drive mode, so this is
  // the one the driver last marked on the drive screen, and it says so.
  const mode = h("span.dial-mode", { hidden: true,
    title: "The drive mode you last marked. OmaCar cannot read it from the car." });
  const begin = h("button.btn.dial-begin", { type: "button", hidden: true,
    onclick: (e) => { e.stopPropagation(); go("launcher"); } }, "Begin");
  const node = tappable(h("div.card.hc.hc-dial", g.el, rpm, mode, begin), "drive");
  const paintMode = (m) => {
    mode.hidden = !m;
    if (m) { text(mode, String(m).toUpperCase()); mode.dataset.mode = m; }
  };
  const onMode = (e) => paintMode((e && e.detail) || null);
  document.addEventListener("omacar:drivemode", onMode);
  let asked = 0;
  return {
    node,
    paint() {
      const s = store.sample, v = store.values, car = store.car;
      g.update(speed.get(v, s, car), speed.read(v, s, car));
      text(rpm, store.connected ? `${READINGS.rpm.get(v, s, car).v} rpm` : "Car off");
      begin.hidden = store.connected;
      if (Date.now() - asked > 30000) {
        asked = Date.now();
        api.driveMode().then((d) => paintMode(d && d.mode)).catch(() => {});
      }
    },
    destroy() { document.removeEventListener("omacar:drivemode", onMode); },
  };
}

function carCard() {
  const img = h("img.car-img", { alt: "", hidden: true, draggable: "false" });
  const slot = h("div.car-slot", h("span", "Your car's picture goes here"));
  const tyre = h("div.callout", { data: { tone: "" } },
    h("span.co-dot"), h("span.co-k", "Tyres"), h("span.co-v"));
  const node = h("div.card.hc.hc-car", h("div.car-stage", img, slot, tyre));
  asset("crz-home").then((a) => {
    if (a && a.url) { img.src = a.url; img.hidden = false; slot.hidden = true; }
    const at = (a && a.anchors && a.anchors.tyres) || [0.5, 0.9];
    tyre.style.left = (at[0] * 100) + "%";
    tyre.style.top = (at[1] * 100) + "%";
  });
  return {
    node,
    paint() {
      const st = tyreState(store.car);
      tyre.dataset.tone = st.tone;
      text(tyre.querySelector(".co-v"), st.text);
    },
  };
}

function soonCard(ico, title, line, id) {
  const node = tappable(h("div.card.hc",
    h("div.hc-ph", icon(ico, 28), h("div.hc-ph-t", title), h("div.hc-ph-s", line))), id);
  return { node, paint() {} };
}

function phoneCard() {
  const chip = (label, id) => h("button.hc-chip", { type: "button",
    onclick: (e) => { e.stopPropagation(); if (!e.currentTarget.closest(".editing")) go(id); } }, label);
  const nursery = chip("Nursery", "nursery");
  const node = h("div.card.hc.hc-phone",
    h("div.hc-title", icon(ICONS.phone, 18), "Phone integration"),
    h("button.hc-row", { type: "button",
      onclick: (e) => { e.stopPropagation(); if (!e.currentTarget.closest(".editing")) go("omaplay"); } },
      icon(ICONS.phone, 22),
      h("span", "Apple CarPlay", h("span.sub", "Your phone's screen, through the adapter")),
      h("span.chev", icon(ICONS.chevron, 18))),
    h("div.hc-chips", chip("Music", "music"), nursery));
  return { node, paint() { nursery.hidden = !store.nurseryOn; } };
}

function agentCard() {
  const q = h("div.ag-q", { hidden: true });
  const a = h("div.ag-a", { hidden: true });
  const empty = h("div.hc-ph-s", "Ask about your car: what a code means, what is due, how a drive went.");
  const node = tappable(h("div.card.hc.hc-agent",
    h("div.hc-title", icon(ICONS.agent, 18), "Oma Agent", h("span.chev", icon(ICONS.chevron, 18))),
    q, a, empty), "advisor");
  let asked = 0;
  return {
    node,
    paint() {
      if (Date.now() - asked < 60000) return;
      asked = Date.now();
      api.aiHistory().then((d) => {
        const r = ((d && d.records) || [])[0];
        const p = r && r.payload;
        const said = p && p.data && (p.data.headline || p.data.answer || p.data.summary);
        q.hidden = a.hidden = !said;
        empty.hidden = !!said;
        if (said) {
          text(q, p.question || p.code || (r.label || "").replace(/^[a-z]+:\s*/, ""));
          text(a, said);
        }
      }).catch(() => { q.hidden = a.hidden = true; empty.hidden = false; });
    },
  };
}

const MAKERS = {
  dial: dialCard,
  car: carCard,
  nav: () => soonCard(ICONS.nav, "Navigation", "Offline maps and turn-by-turn arrive with the navigation step.", "navigation"),
  dashcam: () => soonCard(ICONS.camera, "Dashcams", "Front, rear and cabin recording arrive with the cameras step.", "cameras"),
  phone: phoneCard,
  agent: agentCard,
};

function makeCard(id, cat) {
  const c = cat.cards[id];
  if (!c) return null;
  if (c.reading) {
    const t = makeSignalTile(c.reading, { label: c.label });
    t.node.classList.add("card", "hc");
    return { node: t.node, paint: () => t.paint(store.car, store.sample) };
  }
  return MAKERS[id] ? MAKERS[id]() : null;
}

// ---- the view ---------------------------------------------------------------

export default function home(root) {
  let alive = true;
  let cat = null;
  let layout = null;
  const made = new Map();
  const grid = h("div.home-grid");
  const editBar = h("div.home-editbar", { hidden: true });
  const prov = h("span.home-prov");
  const custom = h("button.home-custom", { type: "button", hidden: true },
    icon(ICONS.layout, 18), "Customize layout");
  root.appendChild(h("div.home", editBar, grid, h("div.home-foot", prov, custom)));

  // PLACED BY MOVING NODES, NEVER BY REBUILDING THEM. appendChild on a node
  // that is already here moves it, so reordering keeps every card's state
  // (its gauge, its image, its listeners) and nothing is rebuilt under a thumb.
  function place(lay = layout) {
    const o = orientation();
    const want = [];
    for (const [id, size] of lay[o].cards) {
      let c = made.get(id);
      if (!c) {
        c = makeCard(id, cat);
        if (!c) continue;
        made.set(id, c);
      }
      const [cols, rows] = spanOf(cat, id, size, o);
      c.node.style.gridColumn = `span ${cols}`;
      c.node.style.gridRow = `span ${rows}`;
      c.node.dataset.card = id;
      c.node.dataset.size = size;
      want.push(c.node);
    }
    for (const n of [...grid.children]) if (!want.includes(n)) n.remove();
    for (const n of want) grid.appendChild(n);
    paint();
  }

  function paint() {
    if (!alive || !layout) return;
    for (const c of made.values()) if (c.node.isConnected) c.paint();
    text(prov, footerLine(store.car, store.live));
  }

  const offLive = store.on("live", paint);
  const offCar = store.on("car", paint);
  const mq = matchMedia("(orientation: portrait)");
  const onTurn = () => { if (layout) place(); };
  mq.addEventListener("change", onTurn);

  (async () => {
    cat = await loadCatalogue();
    layout = defaultLayout(cat);
    if (alive) place();
  })().catch((e) => {
    clear(grid);
    grid.appendChild(h("div.card.tint-bad", h("div.title", "Home could not load its cards"),
      h("p.lede", String((e && e.message) || e))));
  });

  return () => {
    alive = false;
    offLive();
    offCar();
    mq.removeEventListener("change", onTurn);
    for (const c of made.values()) if (c.destroy) c.destroy();
  };
}
```

`share/css/home.css`:

```css
/* Home: cards on a grid the layout arranges. 12 columns landscape, 6 portrait,
   96 px rows; each card's span comes from share/data/home-cards.json. */
.wrap:has(> .home) { max-width: none; }
.home { display: flex; flex-direction: column; gap: 12px; }
.home-grid {
  display: grid; gap: 12px; grid-auto-flow: row dense;
  grid-template-columns: repeat(12, minmax(0, 1fr)); grid-auto-rows: 96px;
}
@media (orientation: portrait) {
  .home-grid { grid-template-columns: repeat(6, minmax(0, 1fr)); }
}
.hc { position: relative; min-width: 0; min-height: 0; overflow: hidden; margin: 0; padding: 16px; }
.hc[role="button"] { cursor: pointer; }
.home-foot { display: flex; align-items: center; justify-content: space-between; gap: 12px;
             font-size: .75rem; color: var(--dim); }
.home-custom { display: inline-flex; align-items: center; gap: 8px; min-height: var(--tap);
               padding: 0 12px; background: none; border: 0; color: var(--ink-2); font: inherit; }

/* speed */
.hc-dial { display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; }
.hc-dial .g-svg { width: 100%; max-height: 72%; }
.dial-rpm { font-size: .95rem; color: var(--ink-2); font-variant-numeric: tabular-nums; }
.dial-mode { margin-top: 6px; padding: 3px 12px; border: 1.5px solid var(--ok); border-radius: 999px;
             color: var(--ok); font-size: .72rem; letter-spacing: .1em; }
.dial-mode[data-mode="sport"] { border-color: var(--warn); color: var(--warn); }
.dial-begin { margin-top: 8px; }

/* the car */
.hc-car { padding: 0;
  background: radial-gradient(ellipse at 50% 62%, color-mix(in srgb, var(--ink) 6%, var(--panel)), var(--panel) 72%); }
.car-stage { position: absolute; inset: 0; }
.car-img { position: absolute; inset: 6% 4%; width: 92%; height: 88%; object-fit: contain; }
.car-slot { position: absolute; inset: 14% 10%; display: grid; place-items: center;
            border: 1.5px dashed var(--edge-2); border-radius: var(--r); color: var(--faint); font-size: .8rem; }
.callout { position: absolute; transform: translate(-50%, -50%); display: flex; align-items: center; gap: 6px;
           padding: 4px 10px; background: color-mix(in srgb, var(--ground) 72%, transparent);
           border: 1px solid var(--hair); border-radius: 999px; font-size: .75rem; white-space: nowrap; }
.co-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 8px var(--accent); }
.callout[data-tone="warn"] .co-dot { background: var(--warn); box-shadow: 0 0 8px var(--warn); }
.callout[data-tone="bad"] .co-dot { background: var(--bad); box-shadow: 0 0 8px var(--bad); }
.callout[data-tone=""] .co-dot, .callout[data-tone="unknown"] .co-dot { background: var(--faint); box-shadow: none; }
.co-k { color: var(--ink-2); }
.co-v { color: var(--ink); font-weight: 500; }

/* coming-soon cards */
.hc-ph { height: 100%; display: flex; flex-direction: column; justify-content: center; gap: 6px; }
.hc-ph svg { color: var(--accent); }
.hc-ph-t { font-size: 1.05rem; color: var(--ink); }
.hc-ph-s { font-size: .8rem; color: var(--dim); line-height: 1.4; }

/* phone and agent */
.hc-title { display: flex; align-items: center; gap: 8px; font-size: .95rem; color: var(--ink); margin-bottom: 10px; }
.hc-title .chev { margin-left: auto; color: var(--dim); }
.hc-row { display: flex; align-items: center; gap: 12px; width: 100%; min-height: var(--tap);
          padding: 8px 12px; background: var(--panel-2); border: 1px solid var(--hair);
          border-radius: 12px; color: var(--ink); font: inherit; text-align: left; }
.hc-row svg:first-child { color: var(--accent); }
.hc-row .sub { display: block; font-size: .72rem; color: var(--dim); }
.hc-row .chev { margin-left: auto; color: var(--dim); }
.hc-chips { display: flex; gap: 8px; margin-top: 10px; }
.hc-chip { min-height: var(--tap); padding: 0 16px; border-radius: 999px; background: var(--panel-2);
           border: 1px solid var(--hair); color: var(--ink-2); font: inherit; }
.ag-q { margin-left: auto; width: fit-content; max-width: 90%; background: var(--panel-2); border: 1px solid var(--hair);
        border-radius: 10px; padding: 8px 12px; font-size: .85rem; color: var(--ink); }
.ag-a { margin-top: 10px; font-size: .85rem; color: var(--ink-2); line-height: 1.4;
        display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }

/* signal tiles: Home and Vehicle. A container, so a tile is right at whatever
   width the layout gives it: the sparkline goes before the number does. */
.sig { display: grid; grid-template-rows: auto 1fr; gap: 6px; container-type: inline-size; }
@container (max-width: 190px) {
  .sig-spark { display: none; }
}
.hc.sig { padding: 12px 14px; }
.sig-head { display: flex; align-items: center; gap: 8px; font-size: .8rem; color: var(--ink-2); }
.sig-body { display: flex; align-items: flex-end; gap: 12px; min-width: 0; }
.sig-val { display: flex; align-items: baseline; gap: 4px; white-space: nowrap; }
.sig-v { font-size: 1.85rem; line-height: 1; color: var(--ink); }
.sig-u { font-size: .85rem; color: var(--ink-2); }
.sig-note { font-size: .78rem; color: var(--dim); }
.sig-spark { flex: 1; min-width: 60px; }
.sig-spark svg { width: 100%; height: 28px; display: block; }
.sig-spark path { fill: none; stroke: var(--accent); stroke-width: 1.5; vector-effect: non-scaling-stroke; }
.sig[data-reading="charge"] .sig-spark path { stroke: var(--ok); }
.sig-scale { display: flex; justify-content: space-between; font-size: .62rem; color: var(--faint);
             font-variant-numeric: tabular-nums; }
.sig[data-tone="warn"] .sig-v { color: var(--warn); }
.sig[data-tone="bad"] .sig-v { color: var(--bad); }
```

In `share/app.html`, after `<link rel="stylesheet" href="css/omaplay.css">`, add `<link rel="stylesheet" href="css/home.css">`.

- [ ] **Step 6: Retire the hub**

1. `share/js/main.js`:
   - Replace `import hub from "./views/hub.js";` with `import home from "./views/home.js";`.
   - In `TABS`, change the home view's `mount: hub` to `mount: home`.
   - Change the import from `./looks.js` to `import { savedLook, saveLook, applyLook, nextLook, lookById } from "./looks.js";`.
   - In the settings sheet, right after the Mode row, add:

```js
    // THE LOOK, which lived on the hub and would otherwise have gone with it.
    rows.appendChild(row("Look", lookById(savedLook()).note || "",
      lookById(savedLook()).label, () => {
        const next = nextLook(savedLook());
        saveLook(next);
        applyLook(next);
        redraw();
      }));
```

2. Delete `share/js/views/hub.js`. Keep `share/js/cardfx.js`: `views/effects.js` imports it.
3. `share/css/app.css`: `grep -n '\.hub' share/css/app.css` lists 41 lines. Delete every rule whose selectors are all `.hub…`. Where a selector list mixes a `.hub…` selector with others, delete only the `.hub…` selector. Check afterwards that `grep -c '\.hub' share/css/app.css` is `0`.
4. `share/css/fonts.css`: delete the `.hub-title,` and `.hub-vital-v,` selector lines.

- [ ] **Step 7: Point app_test's probe at Home**

In `test/app_test.py` `GEOMETRY_PROBE`, replace the block from `const cool = [...document.querySelectorAll(".hub-vital")]` through `const vital = vitalV;` with:

```js
  // HOME'S COOLANT TILE. Value and unit are separate elements, for the reason
  // above: textContent of the whole tile would satisfy a unit regex by
  // accident.
  const cool = document.querySelector('.sig[data-reading="coolant"]');
  const vitalV = cool ? (cool.querySelector(".sig-v") || {}).textContent || "" : "";
  const vitalU = cool ? (cool.querySelector(".sig-u") || {}).textContent || "" : "";
  const vital = vitalV;
```

Rename the probe's output keys and the check messages from `hubCoolant` and `hubUnit` to `homeCoolant` and `homeUnit`, and "the hub and the drive screen agree" to "Home and the drive screen agree".

- [ ] **Step 8: Run everything**

Run `BOXTEST`. Expected:
- `app_test.py`: Home and drive agree on coolant, the exit button still does not move, nothing threw, and the boot screen let go.
- `js_test.py`: green.

- [ ] **Step 9: Look at it against the mockup**

Repeat the Task 4 screenshot command with `#home` at `--window-size=1368,968`, then at `912,1424` (the portrait inner height has the same 56 px quirk that `app_test.py` documents). Compare with `doc/design/mockups/3.webp` (landscape) and `4.webp` (portrait).

Check:
- the arc speed dial;
- the placeholder car slot with its tyre callout;
- the Navigation "coming" card;
- four signal tiles, where the Hybrid pack says "Not on this car" on the simulator, because its supported list has no PID 5B;
- phone, dashcams and agent cards;
- the provenance footer.

Fix spacing issues in `home.css` before committing.

- [ ] **Step 10: Commit**

```bash
git add share/data/home-cards.json share/js/homecards.js share/js/systems.js share/js/sigtile.js share/js/views/home.js share/css/home.css share/app.html share/js/main.js share/css/app.css share/css/fonts.css test/app_test.py test/js/homecards.test.js test/js/systems.test.js test/js/sigtile.test.js
git rm share/js/views/hub.js
git commit -m "Home, as the mockups draw it, with every number saying where it came from" -m "The hub is retired. Home's cards come from a catalogue the server can read too; its tiles draw 'Not on this car' or 'Waiting for the car' instead of a zero, and the tyre callout shows the deflation warning because the car has no per-wheel sensors. The Look setting moves from the hub to Settings.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The layout editor

**Files:**
- Create: `lib/homelayout.py`, `share/js/homeedit.js`
- Modify: `lib/api.py` (GET and POST `/api/home`), `share/js/core.js` (`api.home`, `api.saveHome`), `share/js/views/home.js` (server layout, long press, Customize), `share/css/home.css` (edit-mode styles)
- Test: new section in `test/workshop_test.py`

**Interfaces:**
- Consumes: `home-cards.json` (Task 6), `moveItem` and `nextSize` (Task 6), `place(lay)` inside `home.js`.
- Produces:
  - `GET /api/home → { landscape: {cards: [[id, size]…], hidden: [id…]}, portrait: {…} }`.
  - `POST /api/home` with the same shape saves, and returns the cleaned layout. `{action: "reset"}` deletes the file. A body that is not an object gets a 400.
  - `homeedit.js` exports `startEditing({ grid, bar, cat, layout, orient, place, save, onEnd }) → { finish(keep) } | null`.

- [ ] **Step 1: Write the failing server tests**

Append to `test/workshop_test.py`, before its final summary block:

```python
# ---- Home's layout ------------------------------------------------------------
head("Home's layout")
import homelayout  # noqa: E402

for _p in (homelayout.HOME_CFG, api.DRIVE_CFG):
    if os.path.exists(_p):
        os.remove(_p)
_cat = homelayout.catalogue()
_dflt = [list(x) for x in _cat["default"]["landscape"]]
_first = homelayout.home_layout()
ok("with no file, Home is the catalogue's default", _first["landscape"]["cards"] == _dflt)
ok("and nothing starts hidden", _first["landscape"]["hidden"] == [] and _first["portrait"]["hidden"] == [])
_saved = homelayout.save_home_layout({
    "landscape": {"cards": [["car", "xl"], ["nope", "m"], ["car", "l"], ["dial", "huge"]], "hidden": []},
    "portrait": _first["portrait"]})
_land = _saved["landscape"]
ok("an unknown card is dropped", all(c != "nope" for c, _ in _land["cards"]))
ok("a card placed twice is kept once", [c for c, _ in _land["cards"]].count("car") == 1)
ok("a size the card does not have becomes its first size",
   ["dial", list(_cat["cards"]["dial"]["sizes"])[0]] in _land["cards"])
ok("a default card that was left out is remembered as removed", "nav" in _land["hidden"])
ok("and stays removed when read back", "nav" in homelayout.home_layout()["landscape"]["hidden"])
ok("reset puts the default back",
   homelayout.save_home_layout({"action": "reset"})["landscape"]["cards"] == _dflt)
ok("the route answers", api.handle_get("/api/home", "")[0] == 200)
ok("and refuses a body that is not a layout", api.handle_post("/api/home", "[1, 2]")[0] == 400)
api.save_drive_layout({"tiles": ["intake", "coolant"]})
_mig = [c for c, _ in homelayout.home_layout()["landscape"]["cards"] if c in homelayout.SIGNAL_CARDS]
ok("the drive screen's tile choice carries over while Home has never been saved",
   _mig[:2] == ["intake", "coolant"] and len(_mig) == 4)
for _p in (homelayout.HOME_CFG, api.DRIVE_CFG):
    if os.path.exists(_p):
        os.remove(_p)
```

`handle_get`'s second argument is the raw query string (`qstr()` splits it on `&`), so `""` is the empty query, as in `guards_test.py`'s `/api/begin` check.

- [ ] **Step 2: Run it to see it fail**

Run: rsync, then run the workshop suite on the box exactly as `test/all.sh` does, in a scratch HOME. The simplest way is `test/all.sh`. Expected: `ModuleNotFoundError: homelayout`.

- [ ] **Step 3: Write lib/homelayout.py and the routes**

`lib/homelayout.py`:

```python
"""Home's layout: which cards, in what order, at what size, per orientation.

The cards themselves are share/data/home-cards.json, which the browser reads
too, so the two can never disagree about what a card is or how big it can be.
This file only stores the owner's arrangement, validated, the way the drive
layout is: a hand-edited file must not be able to put an unknown card or an
impossible size on a screen used at speed.

Stored in $XDG_CONFIG_HOME/omarchy/omacar-home.json, next to omacar-drive.json:
    {"landscape": {"cards": [["dial", "m"], ...], "hidden": ["nav"]},
     "portrait":  {...}}

`hidden` is how a DEFAULT card stays removed: a default card that is neither
placed nor hidden came into the catalogue after the file was saved, and is
appended rather than silently missing. Cards that are not in the default are
simply offered by the editor's Add sheet.
"""

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOGUE = os.path.join(ROOT, "share", "data", "home-cards.json")
HOME_CFG = os.path.join(os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME", "~/.config")), "omarchy", "omacar-home.json")
ORIENTS = ("landscape", "portrait")
SIGNAL_CARDS = ("coolant", "volts", "fuel", "charge", "intake", "econ_now")


def catalogue(path=CATALOGUE):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _clean(entry, cat, default, append_missing):
    cards = cat["cards"]
    placed = entry.get("cards") if isinstance(entry, dict) else None
    hidden = entry.get("hidden") if isinstance(entry, dict) else None
    seen, out = set(), []
    for item in placed if isinstance(placed, list) else default:
        if not (isinstance(item, (list, tuple)) and len(item) == 2):
            continue
        cid, size = item
        if not isinstance(cid, str) or cid not in cards or cid in seen:
            continue
        sizes = list(cards[cid]["sizes"])
        out.append([cid, size if size in sizes else sizes[0]])
        seen.add(cid)
    gone = [c for c in (hidden if isinstance(hidden, list) else [])
            if isinstance(c, str) and c in cards and c not in seen]
    defaults = [c for c, _ in default]
    if append_missing:
        for cid, size in default:
            if cid not in seen and cid not in gone:
                out.append([cid, size])
                seen.add(cid)
    else:
        # Saving: a default card the owner left out has been removed.
        gone += [c for c in defaults if c not in seen and c not in gone]
    return {"cards": out, "hidden": sorted(set(gone))}


def _default(cat, migrate):
    base = {o: [list(x) for x in cat["default"][o]] for o in ORIENTS}
    if not migrate:
        return base
    # THE DRIVE SCREEN'S TILE CHOICE CARRIES OVER ONCE: only while Home has
    # never been saved, and only if the drive layout was ever saved at all.
    try:
        import api
        if not os.path.exists(api.DRIVE_CFG):
            return base
        tiles = [t for t in api.drive_layout().get("tiles", [])
                 if t in SIGNAL_CARDS and t in cat["cards"]]
    except Exception:                                         # noqa: BLE001
        return base
    if not tiles:
        return base
    for o in ORIENTS:
        slots = [i for i, (cid, _s) in enumerate(base[o]) if cid in SIGNAL_CARDS]
        rest = [base[o][i][0] for i in slots if base[o][i][0] not in tiles]
        for i, cid in zip(slots, (tiles + rest)[:len(slots)]):
            base[o][i] = [cid, base[o][i][1]]
    return base


def home_layout():
    cat = catalogue()
    try:
        with open(HOME_CFG, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        doc = None
    default = _default(cat, migrate=doc is None)
    doc = doc if isinstance(doc, dict) else {}
    return {o: _clean(doc.get(o), cat, default[o], append_missing=True) for o in ORIENTS}


def save_home_layout(data):
    if not isinstance(data, dict):
        raise ValueError("a Home layout is an object with landscape and portrait")
    if data.get("action") == "reset":
        try:
            os.remove(HOME_CFG)
        except FileNotFoundError:
            pass
        return home_layout()
    cat = catalogue()
    cur = home_layout()
    doc = {o: _clean(data.get(o, cur[o]), cat, [list(x) for x in cat["default"][o]],
                     append_missing=False) for o in ORIENTS}
    doc["_comment"] = ("Home's arrangement. Edit in the app: Home → Customize layout. "
                       "Card ids and sizes are in share/data/home-cards.json.")
    os.makedirs(os.path.dirname(HOME_CFG), exist_ok=True)
    tmp = HOME_CFG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    os.replace(tmp, HOME_CFG)
    return home_layout()
```

`lib/api.py`:
- In `handle_get`, next to `/api/drive`:

```python
    if path == "/api/home":
        import homelayout
        return 200, homelayout.home_layout()
```

- In `handle_post`, next to `/api/drive`:

```python
    if path == "/api/home":
        import homelayout
        try:
            return 200, homelayout.save_home_layout(data)
        except ValueError as e:
            return 400, {"error": str(e)}
```

`share/js/core.js` `api`: add

```js
  home: () => req("/api/home"),
  saveHome: (body) => req("/api/home", { method: "POST", body: JSON.stringify(body) }),
```

- [ ] **Step 4: Run the server tests green**

Run `BOXTEST`. Expected: the "Home's layout" section passes.

- [ ] **Step 5: The editor**

`share/js/homeedit.js`:

```js
// Home's edit mode: drag a card to reorder, tap its size to cycle it, remove
// it, add one back. Nothing is saved until Done; Cancel puts everything back.
//
// ONLY WHILE PARKED. An editor on a moving car is an editor used while
// driving, the rule drive mode's own editor has always kept. If the car starts
// moving mid-edit, the edit is cancelled and says why.
//
// Cards reflow through CSS grid's own placement (grid-auto-flow: dense), so
// there is no free positioning: a layout can never overlap or push a card off
// the screen. Dragging only changes the ORDER.

import { h, clear, icon, store, toast } from "./core.js";
import { ICONS } from "./icons.js";
import { moveItem, nextSize } from "./homecards.js";

export function startEditing({ grid, bar, cat, layout, orient, place, save, onEnd }) {
  if (store.state === "driving") {
    toast("Available when you stop");
    return null;
  }
  const o = orient();
  const work = JSON.parse(JSON.stringify(layout));
  let alive = true;
  let drag = null;

  grid.classList.add("editing");
  const done = h("button.btn.btn-primary", { type: "button" }, "Done");
  const cancel = h("button.btn", { type: "button" }, "Cancel");
  const reset = h("button.btn", { type: "button" }, "Reset");
  const add = h("button.btn", { type: "button" }, icon(ICONS.plus, 16), " Add card");
  clear(bar);
  bar.append(h("span.he-note", "Drag to move · tap a size to change it"), add, reset, cancel, done);
  bar.hidden = false;

  function decorate() {
    for (const node of grid.children) {
      if (node.querySelector(":scope > .he-ctl")) continue;
      const id = node.dataset.card;
      const sizes = Object.keys(cat.cards[id].sizes);
      const size = h("button.he-size", { type: "button", hidden: sizes.length < 2,
        "aria-label": "Change size",
        onclick: (e) => {
          e.stopPropagation();
          const row = work[o].cards.find((x) => x[0] === id);
          row[1] = nextSize(cat, id, row[1]);
          redraw();
        } }, (node.dataset.size || "").toUpperCase());
      const rm = h("button.he-rm", { type: "button", "aria-label": "Remove " + cat.cards[id].label,
        onclick: (e) => {
          e.stopPropagation();
          work[o].cards = work[o].cards.filter((x) => x[0] !== id);
          if (cat.default[o].some((x) => x[0] === id)) {
            work[o].hidden = [...new Set([...work[o].hidden, id])];
          }
          redraw();
        } }, icon(ICONS.x, 16));
      node.appendChild(h("div.he-ctl", size, rm));
    }
  }
  function strip() { for (const c of grid.querySelectorAll(".he-ctl")) c.remove(); }
  function redraw() { strip(); place(work); decorate(); }

  // Pointer capture goes on the GRID, not the card: place() moves cards with
  // appendChild, and moving a node releases any capture it holds.
  function onDown(e) {
    const node = e.target.closest(".home-grid > [data-card]");
    if (!node || e.target.closest(".he-ctl")) return;
    e.preventDefault();
    grid.setPointerCapture(e.pointerId);
    drag = { node, id: node.dataset.card, x: e.clientX, y: e.clientY };
    node.classList.add("dragging");
  }
  function onMove(e) {
    if (!drag) return;
    drag.node.style.transform = `translate(${e.clientX - drag.x}px, ${e.clientY - drag.y}px)`;
    const under = [...grid.children].find((n) => {
      if (n === drag.node) return false;
      const r = n.getBoundingClientRect();
      return e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
    });
    if (!under) return;
    const list = work[o].cards;
    const from = list.findIndex((x) => x[0] === drag.id);
    const to = list.findIndex((x) => x[0] === under.dataset.card);
    if (from < 0 || to < 0 || from === to) return;
    work[o].cards = moveItem(list, from, to);
    // The dragged card's grid slot moved, so the finger's offset is measured
    // again from where the card now sits: it stays under the finger.
    drag.node.style.transform = "";
    const before = drag.node.getBoundingClientRect();
    redraw();
    const after = drag.node.getBoundingClientRect();
    drag.x += after.left - before.left;
    drag.y += after.top - before.top;
    drag.node.style.transform = `translate(${e.clientX - drag.x}px, ${e.clientY - drag.y}px)`;
  }
  function onUp() {
    if (!drag) return;
    drag.node.classList.remove("dragging");
    drag.node.style.transform = "";
    drag = null;
  }
  // In edit mode a tap on a card is part of editing, never a navigation.
  function swallow(e) {
    if (!e.target.closest(".he-ctl")) { e.stopPropagation(); e.preventDefault(); }
  }

  grid.addEventListener("pointerdown", onDown);
  grid.addEventListener("pointermove", onMove);
  grid.addEventListener("pointerup", onUp);
  grid.addEventListener("pointercancel", onUp);
  grid.addEventListener("click", swallow, true);
  const offLive = store.on("live", () => {
    if (store.state === "driving") {
      toast("Editing stopped: the car is moving");
      finish(false);
    }
  });

  add.onclick = () => {
    const placed = new Set(work[o].cards.map((x) => x[0]));
    const avail = Object.keys(cat.cards).filter((id) => !placed.has(id));
    if (!avail.length) { toast("Every card is already on Home"); return; }
    const host = document.getElementById("modal-host");
    const close = () => { host.hidden = true; clear(host); host.onclick = null; };
    const rows = avail.map((id) => h("button.sheet-row", { type: "button", onclick: () => {
      work[o].cards.push([id, Object.keys(cat.cards[id].sizes)[0]]);
      work[o].hidden = work[o].hidden.filter((x) => x !== id);
      close();
      redraw();
    } }, h("span.sheet-l", h("span.sheet-lab", cat.cards[id].label)), h("span.sheet-v", "Add")));
    clear(host);
    host.appendChild(h("div.sheet", { role: "dialog", "aria-modal": "true", "aria-label": "Add a card" },
      h("div.sheet-head", h("div.title", "Add a card"),
        h("button.btn.right", { type: "button", onclick: close }, "Close")),
      h("div.sheet-rows", rows)));
    host.hidden = false;
    host.onclick = (e) => { if (e.target === host) close(); };
  };
  reset.onclick = () => {
    work[o] = { cards: cat.default[o].map((x) => x.slice()), hidden: [] };
    redraw();
  };
  cancel.onclick = () => finish(false);
  done.onclick = () => finish(true);

  async function finish(keep) {
    if (!alive) return;
    alive = false;
    onUp();
    grid.removeEventListener("pointerdown", onDown);
    grid.removeEventListener("pointermove", onMove);
    grid.removeEventListener("pointerup", onUp);
    grid.removeEventListener("pointercancel", onUp);
    grid.removeEventListener("click", swallow, true);
    offLive();
    grid.classList.remove("editing");
    strip();
    bar.hidden = true;
    clear(bar);
    if (keep) {
      try { place(await save(work)); toast("Layout saved"); }
      catch (err) { toast("Could not save the layout: " + ((err && err.message) || err), "bad"); place(layout); }
    } else {
      place(layout);
    }
    if (onEnd) onEnd();
  }

  decorate();
  return { finish };
}
```

- [ ] **Step 6: Wire it into Home**

In `share/js/views/home.js`:
- Add `import { startEditing } from "../homeedit.js";`.
- Replace `layout = defaultLayout(cat);` inside the async loader with:

```js
    // The owner's arrangement from the server; the catalogue's default if the
    // server is older than this screen or unreachable.
    layout = await api.home().catch(() => null) || defaultLayout(cat);
```

- After the `mq.addEventListener("change", onTurn);` line, add:

```js
  // ---- customising: the button, or a long press on any card -------------
  let editor = null;
  function customise() {
    if (editor || !layout || !cat) return;
    editor = startEditing({
      grid, bar: editBar, cat, layout, orient: orientation, place,
      save: async (w) => { layout = await api.saveHome(w); return layout; },
      onEnd: () => { editor = null; },
    });
  }
  custom.hidden = false;
  custom.addEventListener("click", customise);
  let press = null;
  const unpress = () => { if (press) { clearTimeout(press.t); press = null; } };
  grid.addEventListener("pointerdown", (e) => {
    if (editor) return;
    press = { x: e.clientX, y: e.clientY, t: setTimeout(() => { press = null; customise(); }, 600) };
  });
  grid.addEventListener("pointermove", (e) => {
    if (press && Math.hypot(e.clientX - press.x, e.clientY - press.y) > 10) unpress();
  });
  grid.addEventListener("pointerup", unpress);
  grid.addEventListener("pointercancel", unpress);
```

- In the cleanup function, add `if (editor) editor.finish(false);` before `alive = false;`.

Append to `share/css/home.css`:

```css
/* ---- edit mode ---------------------------------------------------------- */
.home-editbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.home-editbar[hidden] { display: none; }
.he-note { margin-right: auto; font-size: .8rem; color: var(--ink-2); }
.btn-primary { background: var(--accent-fill); color: var(--on-accent); border-color: transparent; }
.home-grid.editing > [data-card] { outline: 1.5px dashed var(--edge-2); outline-offset: -1px;
  touch-action: none; cursor: grab; animation: he-wiggle .9s ease-in-out infinite alternate; }
.home-grid.editing > .dragging { z-index: 5; cursor: grabbing; animation: none; opacity: .95;
  box-shadow: 0 18px 40px rgba(0, 0, 0, .45); }
@keyframes he-wiggle { from { rotate: -.25deg; } to { rotate: .25deg; } }
@media (prefers-reduced-motion: reduce) { .home-grid.editing > [data-card] { animation: none; } }
.he-ctl { position: absolute; top: 8px; right: 8px; display: flex; gap: 6px; z-index: 2; }
.he-size, .he-rm { min-width: var(--tap); min-height: var(--tap); border-radius: 12px;
  background: var(--raise); border: 1px solid var(--edge-2); color: var(--ink);
  font: inherit; font-size: .75rem; display: grid; place-items: center; }
```

The wiggle uses `rotate`, not `transform`, so it composes with the drag's inline `transform: translate(…)` instead of fighting it.

- [ ] **Step 7: Run everything and try it**

Run `BOXTEST`. Then check by hand in a headed window, from the box's desktop or over the Surface later:
- long press a card;
- drag the Coolant tile ahead of the car;
- cycle the dial's size;
- remove Dashcams;
- Done, then reload: the layout persists;
- Customize, then Reset, then Done: the default returns.

Write down anything that misbehaves and fix it before committing.

- [ ] **Step 8: Commit**

```bash
git add lib/homelayout.py lib/api.py share/js/core.js share/js/homeedit.js share/js/views/home.js share/css/home.css test/workshop_test.py
git commit -m "Home can be rearranged by hand, while parked, and a hand-edited file cannot break it" -m "Long press or Customize layout; drag to reorder, tap to resize, remove and add. Saved per orientation in omacar-home.json and validated on the server against the same card catalogue the browser reads. The drive screen's tile choice carries over once.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Vehicle

**Files:**
- Create: `share/js/views/vehicle.js`, `share/css/vehicle.css`
- Modify: `share/app.html` (link `vehicle.css`), `share/js/main.js` (mount Vehicle), `lib/sim.py` (the simulator records when its "scan" happened)

**Interfaces:**
- Consumes: `groupSystems`, `headline`, `worstOf` (Task 6), `makeSignalTile` (Task 6), `asset("crz-xray")` with its anchors (Task 5), `learnedFor` (Task 3), `shortDate(secs)`, `clockOf(secs)` and `dist(km)` from `core.js`.
- Produces: the `vehicle` view (Vehicle → Overview).

- [ ] **Step 1: The simulator says when it scanned**

In `lib/sim.py`, where `meta` is built (`meta["seeded_at"] = int(time.time())`), add the line below. `records.vehicle()` already passes every `vehicle` key into the snapshot, so `car.vehicle.surveyed_at` appears with no other change.

```python
    # The modules above stand for a full scan, so the record says when it
    # happened -- the Vehicle screen shows "Last scan" from this, the same key
    # lib/survey.py's real scans are read back through (lib/api.py).
    meta["surveyed_at"] = int(time.time())
```

- [ ] **Step 2: The view**

`share/js/views/vehicle.js`:

```js
// Vehicle: the car, system by system, from the last full scan.
//
// Built to the owner's mockup (doc/design/mockups/5.webp): a status headline,
// the X-ray render with a callout on every system the scan covers, the system
// list, live signals, and the three actions. Everything on it is a reading or
// a scan result; a system nobody has scanned says so, and is never "Normal".

import { h, clear, icon, store, api, dist, shortDate, clockOf } from "../core.js";
import { ICONS } from "../icons.js";
import { groupSystems, headline, worstOf } from "../systems.js";
import { makeSignalTile } from "../sigtile.js";
import { learnedFor } from "../readings.js";
import { asset } from "../assets.js";

const text = (el, s) => { if (el.textContent !== s) el.textContent = s; };
const go = (id) => { location.hash = "#" + id; };   // taps only
const WORD = { ok: "Normal", warn: "Check", bad: "Fault", unknown: "Not scanned" };
const SYS_ICON = { engine: "gauge", hybrid: "leaf", brakes: "health", electrical: "battery", other: "dash" };
const LIVE = [["rpm", "Engine speed"], ["coolant", "Coolant"], ["volts", "12V system"], ["charge", "Hybrid pack"]];
const CALLOUTS = [["engine", "Engine"], ["hybrid", "Hybrid system"], ["brakes", "Brakes"],
                  ["steering", "Steering"], ["tyres", "Tyres"]];

function listRows(groups) {
  const by = Object.fromEntries(groups.map((g) => [g.id, g]));
  const rest = [by.steering, by.tyres, by.other].filter(Boolean);
  const mods = rest.flatMap((g) => g.modules);
  return [by.engine, by.hybrid, by.brakes, by.electrical,
          { id: "other", label: "All other systems",
            sub: mods.length ? `${mods.length} module${mods.length === 1 ? "" : "s"}` : "None found",
            status: worstOf(rest) }];
}

function sysRow(g) {
  return h("button.vh-sys-row", { type: "button", onclick: () => go("codes") },
    icon(ICONS[SYS_ICON[g.id]] || ICONS.codes, 22),
    h("span", g.label, h("span.sub", g.sub)),
    h("span.vh-sys-st", { data: { tone: g.status } },
      icon(g.status === "ok" ? ICONS.check : ICONS.codes, 18), WORD[g.status]),
    h("span.chev", icon(ICONS.chevron, 18)));
}

export default function vehicle(root) {
  let alive = true;
  const statusIco = h("span.vh-icon", icon(ICONS.check, 44));
  const title = h("div.vh-title");
  const when = h("div.vh-when");
  const carName = h("div.vh-car"), spec = h("div.vh-spec"), odo = h("div.vh-odo.display-num");

  const img = h("img.car-img", { alt: "", hidden: true, draggable: "false" });
  const slot = h("div.car-slot", h("span", "X-ray render not installed · omacar assets sync"));
  const stage = h("div.car-stage", img, slot);
  const callouts = new Map();
  for (const [id, label] of CALLOUTS) {
    const co = h("div.callout", { hidden: true, data: { sys: id, tone: "" } },
      h("span.co-dot"), h("span.co-k", label), h("span.co-v"));
    callouts.set(id, co);
    stage.appendChild(co);
  }
  asset("crz-xray").then((a) => {
    if (!a || !a.url) return;
    img.src = a.url; img.hidden = false; slot.hidden = true;
    for (const [id, co] of callouts) {
      const at = a.anchors && a.anchors[id];
      if (!at) continue;
      co.style.left = (at[0] * 100) + "%";
      co.style.top = (at[1] * 100) + "%";
      co.hidden = false;
    }
  });

  const sysList = h("div.vh-sys-list");
  const liveRow = h("div.vh-live");
  let tiles = [];
  function useTiles(list) {
    clear(liveRow);
    tiles = list;
    for (const t of tiles) { t.node.classList.add("card"); liveRow.appendChild(t.node); }
  }
  useTiles(LIVE.map(([id, label]) => makeSignalTile(id, { label })));

  // OBD-II AND HONDA ENHANCED, ONLY WHEN THERE IS SOMETHING ENHANCED TO SHOW:
  // a signal the profile has validated. The mockup's switch is not drawn over
  // candidates nobody has checked.
  const obdChip = h("button.chip", { type: "button", "aria-pressed": "true" }, "OBD-II");
  const hondaChip = h("button.chip", { type: "button", "aria-pressed": "false" }, "Honda enhanced");
  const srcRow = h("div.vh-src", { hidden: true }, h("span.vh-src-k", "Data source"), obdChip, hondaChip);
  obdChip.onclick = () => {
    obdChip.setAttribute("aria-pressed", "true"); hondaChip.setAttribute("aria-pressed", "false");
    useTiles(LIVE.map(([id, label]) => makeSignalTile(id, { label })));
    paint();
  };
  hondaChip.onclick = () => {
    const learned = Object.entries(learnedFor(store.car)).slice(0, 4);
    if (!learned.length) return;
    obdChip.setAttribute("aria-pressed", "false"); hondaChip.setAttribute("aria-pressed", "true");
    useTiles(learned.map(([id, def]) => makeSignalTile(id, { def, label: def.label })));
    paint();
  };

  const codesCard = h("div.card.vh-codes", { role: "button", tabindex: "0", onclick: () => go("codes") });
  const insight = h("div.card.vh-insight", { hidden: true, role: "button", tabindex: "0",
                                             onclick: () => go("advisor") });
  const actions = h("div.vh-actions",
    srcRow,
    h("span.vh-gap"),
    h("button.btn.btn-primary", { type: "button", onclick: () => go("scan") }, icon(ICONS.scan, 18), " Scan vehicle"),
    h("button.btn", { type: "button", onclick: () => go("replay") }, icon(ICONS.rec, 18), " Record session"),
    h("button.btn", { type: "button", onclick: () => go("report") }, icon(ICONS.report, 18), " Export"));

  root.appendChild(h("div.veh",
    h("div.vh-head",
      h("div.vh-status", statusIco, h("div", title, when)),
      h("div.vh-id", carName, spec, odo)),
    h("div.card.vh-hero", stage),
    h("div.card.vh-systems", h("div.hc-title", "Vehicle systems"), sysList),
    h("div.card.vh-livecard", h("div.hc-title", icon(ICONS.agent, 18), "Live signals"), liveRow),
    actions,
    codesCard,
    insight));

  let sysKey = null, codesKey = null;

  function paintCodes(car) {
    const act = (car && car.active_faults) || [];
    const key = act.map((f) => f.code).join();
    if (key === codesKey) return;
    codesKey = key;
    clear(codesCard);
    codesCard.appendChild(h("div.hc-title", icon(ICONS.codes, 18), "Diagnostic codes",
      h("span.chev", icon(ICONS.chevron, 18))));
    if (!act.length) {
      codesCard.appendChild(h("div.vh-none", icon(ICONS.check, 28), h("span", "No active codes")));
      return;
    }
    for (const f of act.slice(0, 3)) {
      codesCard.appendChild(h("div.vh-code", h("span.vh-code-c.display-num", f.code), h("span", f.descr || "")));
    }
    if (act.length > 3) codesCard.appendChild(h("div.vh-more", `and ${act.length - 3} more`));
  }

  function paint() {
    if (!alive) return;
    const car = store.car;
    const groups = groupSystems(car && car.modules, car && car.active_faults);
    const hl = headline(groups);
    text(title, hl.text);
    statusIco.dataset.tone = hl.tone;
    const v = (car && car.vehicle) || {};
    text(when, v.surveyed_at ? `Last scan: ${shortDate(v.surveyed_at)}, ${clockOf(v.surveyed_at)}`
                             : "No full scan has been run on this car yet");
    text(carName, [v.year, v.make, v.model].filter(Boolean).join(" ") || (car && car.title) || "");
    text(spec, [v.engine, v.drivetrain].filter(Boolean).join("  ·  "));
    text(odo, car && car.odometer ? dist(car.odometer) : "");

    const rows = listRows(groups);
    const key = rows.map((g) => g.id + ":" + g.status).join("|");
    if (key !== sysKey) {
      sysKey = key;
      clear(sysList);
      for (const g of rows) sysList.appendChild(sysRow(g));
    }
    for (const [id, co] of callouts) {
      const g = groups.find((x) => x.id === id);
      const st = g ? g.status : "unknown";
      co.dataset.tone = st === "ok" ? "ok" : st;
      text(co.querySelector(".co-v"), WORD[st]);
    }
    srcRow.hidden = !((car && car.signals) || []).length;
    for (const t of tiles) t.paint(car, store.sample);
    paintCodes(car);
  }

  api.aiHistory().then((d) => {
    const r = ((d && d.records) || []).find((x) => x.payload && x.payload.data && x.payload.data.headline);
    if (!r || !alive) return;
    clear(insight);
    insight.append(h("div.hc-title", icon(ICONS.agent, 18), "Oma Agent insight",
                     h("span.chev", icon(ICONS.chevron, 18))),
                   h("div.vh-insight-t", r.payload.data.headline));
    insight.hidden = false;
  }).catch(() => {});

  const offLive = store.on("live", paint);
  const offCar = store.on("car", paint);
  paint();
  return () => { alive = false; offLive(); offCar(); };
}
```

`share/css/vehicle.css`:

```css
/* Vehicle: headline, X-ray, systems, live signals, actions, codes. */
.wrap:has(> .veh) { max-width: none; }
.veh { display: grid; gap: 12px; grid-template-columns: repeat(12, minmax(0, 1fr)); }
.veh > * { grid-column: span 12; min-width: 0; }
.vh-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.vh-status { display: flex; align-items: center; gap: 14px; }
.vh-icon { color: var(--ok); display: grid; }
.vh-icon[data-tone="warn"] { color: var(--warn); }
.vh-icon[data-tone="bad"] { color: var(--bad); }
.vh-icon[data-tone=""] { color: var(--faint); }
.vh-title { font-family: var(--display); font-size: 1.9rem; font-weight: 600; color: var(--ink); }
.vh-when { font-size: .9rem; color: var(--ink-2); }
.vh-id { text-align: right; }
.vh-car { font-size: 1.15rem; color: var(--ink); }
.vh-spec { font-size: .85rem; color: var(--ink-2); }
.vh-odo { font-size: 1rem; color: var(--ink); }
.vh-hero { grid-column: span 7; position: relative; min-height: 340px; padding: 0;
  background: radial-gradient(ellipse at 50% 60%, color-mix(in srgb, var(--accent) 5%, var(--panel)), var(--panel) 70%); }
.vh-systems { grid-column: span 5; }
.vh-sys-row { display: flex; align-items: center; gap: 12px; width: 100%; min-height: var(--tap-lg);
  padding: 8px 12px; margin-top: 8px; background: var(--panel-2); border: 1px solid var(--hair);
  border-radius: 12px; color: var(--ink); font: inherit; text-align: left; }
.vh-sys-row .sub { display: block; font-size: .72rem; color: var(--dim); }
.vh-sys-row .chev { color: var(--dim); }
.vh-sys-st { margin-left: auto; display: flex; align-items: center; gap: 6px; font-size: .85rem; }
.vh-sys-st[data-tone="ok"] { color: var(--ok); }
.vh-sys-st[data-tone="warn"] { color: var(--warn); }
.vh-sys-st[data-tone="bad"] { color: var(--bad); }
.vh-sys-st[data-tone="unknown"] { color: var(--faint); }
.vh-live { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.vh-live .sig { min-height: 110px; padding: 12px 14px; }
.vh-actions { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
.vh-actions .btn { min-height: var(--tap); display: inline-flex; align-items: center; gap: 8px; }
.vh-gap { flex: 1; }
.vh-src { display: flex; align-items: center; gap: 8px; }
.vh-src[hidden] { display: none; }
.vh-src-k { font-size: .8rem; color: var(--ink-2); }
.vh-src .chip[aria-pressed="true"] { background: var(--accent-bg); color: var(--accent); border-color: var(--accent); }
.vh-codes, .vh-insight { grid-column: span 6; }
.vh-none { display: flex; align-items: center; gap: 12px; color: var(--ok); font-size: 1rem; }
.vh-code { display: flex; gap: 12px; align-items: baseline; margin-top: 6px; color: var(--ink-2); font-size: .85rem; }
.vh-code-c { color: var(--warn); font-size: 1rem; }
.vh-more { margin-top: 6px; font-size: .8rem; color: var(--dim); }
.vh-insight-t { color: var(--ink); font-size: .95rem; line-height: 1.4; }
@media (orientation: portrait) {
  .vh-hero, .vh-systems, .vh-codes, .vh-insight { grid-column: span 12; }
  .vh-live { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .vh-head { flex-direction: column; align-items: flex-start; }
  .vh-id { text-align: left; }
}
```

In `share/app.html`, after the `home.css` link, add `<link rel="stylesheet" href="css/vehicle.css">`.

In `share/js/main.js`:
- Add `import vehicleView from "./views/vehicle.js";`.
- In `TABS`, change Vehicle → Overview's view to `mount: vehicleView`. It currently mounts `dash`.

- [ ] **Step 3: Run everything and look at it**

Run `BOXTEST`. Then screenshot `#vehicle` at 1368×968 and 912×1424, as in Task 4 Step 8. The box's main checkout has the X-ray render. For the screenshot, copy it into the test mirror with `ssh jmyers@omarchy 'mkdir -p ~/Projects/.omacar-test/foundation/share/assets/private && cp ~/Projects/omacar/share/assets/private/crz-xray.png ~/Projects/.omacar-test/foundation/share/assets/private/'`.

Compare with `doc/design/mockups/5.webp`. Check:
- the headline reads "2 systems need attention" on the simulator (engine P0135, tyres C1B00), with "Last scan";
- each callout sits on its part of the render;
- the systems list;
- four live tiles, with the Hybrid pack reading "Not on this car" on the simulator;
- the actions;
- the codes card lists P0135 and C1B00.

If a callout misses its part, correct its anchor in `share/assets/manifest.json`, which is committed; anchors are not private.

- [ ] **Step 4: Commit**

```bash
git add share/js/views/vehicle.js share/css/vehicle.css share/app.html share/js/main.js lib/sim.py share/assets/manifest.json
git commit -m "Vehicle: every system the scan covers, on the X-ray, in the words the scan supports" -m "A system nobody scanned says 'Not scanned', never 'Normal'. The data-source switch appears only when the profile has validated an enhanced signal. The simulator now records when its scan happened, so 'Last scan' has something true to say.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: The advisor moves to Opus 5.5, and its cache learns which model answered

**Files:**
- Modify: `lib/ai.py`, `test/guards_test.py`, `doc/design/2026-09-28-foundation.md`

- [ ] **Step 1: Write the failing guard**

Append to `test/guards_test.py`, before the done block:

```python
# ------------------------------------------------------------- the advisor
head("The advisor asks Opus 5.5, and a cached answer names the model that gave it")
check("reasoning kinds ask Opus 5.5",
      sorted({ai.MODEL_FOR[k] for k in ("triage", "code", "ask", "predict", "symptom", "recording")}),
      ["claude-opus-5-5"])
check("the plain-language rewrite stays on the fast model", ai.MODEL_FOR["owner"], "claude-haiku-4-5")
check("the default is Opus 5.5", ai.DEFAULT_MODEL, "claude-opus-5-5")
_b = {"faults": {"P0135": {}}}
check("the same evidence asked of two models is two cache entries",
      ai.cache_key("triage", "t", _b, "claude-opus-5-5") != ai.cache_key("triage", "t", _b, "claude-sonnet-5"),
      True)
```

`ai` is already imported in `guards_test.py` (see the `_answering_model` checks). If it is not in scope at the end of the file, add `import ai  # noqa: E402`.

- [ ] **Step 2: Run it to see it fail**

Expected: model mismatches, and a `TypeError` from `cache_key`, which takes three arguments today.

- [ ] **Step 3: Change the models and the key**

In `lib/ai.py`:
- `DEFAULT_MODEL = "claude-opus-5-5"`.
- In `MODEL_FOR`, every `"claude-sonnet-5"` becomes `"claude-opus-5-5"`. `owner` stays `"claude-haiku-4-5"`.
- Replace `cache_key`:

```python
def cache_key(kind, prompt, b, model):
    # THE MODEL IS PART OF THE QUESTION. Without it, moving the advisor from
    # Sonnet to Opus served every old Sonnet answer as if Opus had just given
    # it, cached=True and all -- the cache was keyed by evidence alone.
    h = hashlib.sha256()
    h.update(kind.encode())
    h.update(prompt.encode())
    h.update(json.dumps(b, sort_keys=True, default=str).encode())
    h.update(model.encode())
    return h.hexdigest()[:24]
```

- In `ask()`, resolve the model once, before the key, and use it in both places:

```python
    use = model or MODEL_FOR.get(kind, DEFAULT_MODEL)
    key = cache_key(kind, task, b, use)
```

  and change the `run_claude(prompt, model or MODEL_FOR.get(kind, DEFAULT_MODEL), extra)` call to `run_claude(prompt, use, extra)`.
- Check `grep -n "cache_key(" lib/*.py` for other callers, and update each to pass its model.

- [ ] **Step 4: Correct the design doc**

In `doc/design/2026-09-28-foundation.md`, "Opus 5.5" section, replace "The existing cache is keyed by model, so old answers are not served as new ones." with:

> The cache key gains the model. Before this change it hashed only the kind, the prompt and the evidence, so switching models would have served every old Sonnet answer as a fresh Opus one.

- [ ] **Step 5: Run everything, then commit**

Run `BOXTEST`. Expected: the new guard section passes, and `workshop_test.py`'s "Advisor grounding" still passes.

```bash
git add lib/ai.py test/guards_test.py doc/design/2026-09-28-foundation.md
git commit -m "The advisor asks Opus 5.5, and its cache stops passing old Sonnet answers off as new ones" -m "cache_key hashed the evidence but not the model, so the switch would have served every cached answer under the new model's name.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: The looks: OmaCar by default, Omarchy's theme as a choice

**Files:**
- Modify: `share/js/looks.js`, `share/js/main.js` (`applyTheme`, the day/night button), `share/css/app.css` (the four look blocks), `test/design_test.py`
- Test: `test/js/looks.test.js`

**Interfaces:**
- Produces:
  - `LOOKS` gains `{ id: "omarchy" }`, and `normal` is labelled "OmaCar".
  - `applyLook(id)` dispatches `omacar:look` on `document`.
  - `applyTheme()` applies the desktop theme only when the saved look is `omarchy`.

- [ ] **Step 1: Write the failing tests**

`test/js/looks.test.js`:

```js
import { eq, ok } from "./assert.js";
import { LOOKS, lookById, applyLook } from "../js/looks.js";

export default [
  ["the default look is OmaCar's own", () => eq(lookById("normal").label, "OmaCar")],
  ["Omarchy's theme is a look you choose", () => ok(LOOKS.some((l) => l.id === "omarchy"), "omarchy look exists")],
  ["choosing a look says so", () => {
    let heard = null;
    const f = (e) => { heard = e.detail; };
    document.addEventListener("omacar:look", f);
    applyLook("day");
    document.removeEventListener("omacar:look", f);
    applyLook("normal");
    eq(heard, "day");
  }],
];
```

In `test/design_test.py`, extend `main()` so the Daylight look is also checked. Its tokens overlay the base:

```python
    day = dict(base)
    day.update(block(css, ':root[data-look="day"]'))
    run("The Daylight look", day)
```

- [ ] **Step 2: Run them to see them fail**

Expected: the looks tests fail. The Daylight run fails on `--accent`, `--ink-2`, `--rec` and `--on-accent`, which inherit dark-palette values that do not read on a light ground.

- [ ] **Step 3: looks.js**

- Relabel the first entry: `{ id: "normal", label: "OmaCar", effect: "off", note: "The mockups' palette. No background." },`.
- Add a last entry:

```js
  // The desktop's theme, mapped onto OmaCar's tokens by lib/theme.py. It used
  // to be the default; the owner's mockups are a designed palette now, and
  // this is how to wear the desktop's instead.
  { id: "omarchy", label: "Omarchy theme", effect: "off",
    note: "Your desktop theme's colours, mapped onto OmaCar's." },
```

- At the end of `applyLook()`, add:

```js
  // main.js applies or removes the desktop theme when this changes.
  document.dispatchEvent(new CustomEvent("omacar:look", { detail: look.id }));
```

- [ ] **Step 4: main.js: the theme only when asked for, and a day/night button that always works**

- Replace the first lines of `applyTheme()` (through `if (stamp === themeStamp) return;`) with:

```js
async function applyTheme() {
  // THE DESKTOP THEME IS A LOOK NOW, NOT THE DEFAULT. Removed the moment
  // another look is chosen, so the stylesheet's own palette is what shows.
  if (savedLook() !== "omarchy") {
    if (themeSheet) { themeSheet.remove(); themeSheet = null; }
    themeStamp = -1;
    return;
  }
  try {
    const { stamp, vars } = await api.theme();
    if (stamp === themeStamp) return;
```

- In `boot()`, after `applyLook(savedLook());`, add:

```js
  document.addEventListener("omacar:look", () => { applyTheme(); paintDayNight(); });
```

- Replace `paintDayNight(mode)` and the start of `toggleDayNight()`. Outside the Omarchy look, the button switches between the OmaCar and Daylight looks, so it always has somewhere to go.

```js
function paintDayNight(mode) {
  if (!els.daynight) return;
  const desktop = savedLook() === "omarchy";
  const usable = !desktop || !!(themeModes.light && themeModes.dark
                                && themeModes.light !== themeModes.dark);
  els.daynight.hidden = !usable;
  if (!usable) return;
  const dark = desktop ? mode !== "light" : savedLook() !== "day";
  clear(els.daynight);
  els.daynight.appendChild(icon(dark ? ICONS.sun : ICONS.moon, 20));
  els.daynight.title = dark ? "Switch to the day palette" : "Switch to the night palette";
  els.daynight.setAttribute("aria-label", els.daynight.title);
}

async function toggleDayNight() {
  if (savedLook() !== "omarchy") {
    const next = savedLook() === "day" ? "normal" : "day";
    saveLook(next);
    applyLook(next);
    return;
  }
  // ... the existing body continues unchanged from `const goingLight = …`
```

- [ ] **Step 5: The four look blocks learn the new tokens**

Append these declarations inside each block in `share/css/app.css`:

- `:root[data-look="day"]`:

```css
  --ink-2: #33424B; --accent: #00708A; --accent-fill: #007C95;
  --on-accent: #FFFFFF; --rec: #B8321F; --accent-bg: rgba(0, 112, 138, .10);
```

- `:root[data-look="dim"]`:

```css
  --ink-2: #9AA6AE; --accent: #1A9FB8; --accent-fill: #177E8A;
  --on-accent: #E6F6F9; --rec: #C2473A; --accent-bg: rgba(26, 159, 184, .12);
```

- `:root[data-look="red"]`:

```css
  --ink-2: #C2453D; --accent: #E0493F; --accent-fill: #8E2A24;
  --on-accent: #FFD9D6; --rec: #FF5A48; --accent-bg: rgba(224, 73, 63, .14);
```

- `:root[data-look="green"]`:

```css
  --ink-2: #7FD99A; --accent: #3BEA7A; --accent-fill: #1F9E50;
  --on-accent: #02140A; --rec: #F55240; --accent-bg: rgba(59, 234, 122, .12);
```

The Daylight values were checked against that block's own `--ground` (`#F2F4F3`) and `--panel` (`#FFFFFF`) before this plan was written:

| Pair | Ratio |
|---|---|
| `--ink-2` on panel | 10.39 |
| `--accent` on ground | 5.16 |
| `--accent` on panel | 5.70 |
| `--rec` on panel | 5.98 |
| `--on-accent` on `--accent-fill` | 4.86 |
| `--faint` (unchanged `#61706F`) on panel | 5.18 |

So `design_test.py` passes as written. Dim, Night·red and Matrix are not in the contrast test: they are deliberately low-glare palettes for the dark, and the owner judges them by eye.

- [ ] **Step 6: Run everything, then commit**

Run `BOXTEST`. Expected: looks tests pass, and both palettes pass the contrast checks.

```bash
git add share/js/looks.js share/js/main.js share/css/app.css test/design_test.py test/js/looks.test.js
git commit -m "The mockups' palette is the default, and the desktop's theme becomes a look you choose" -m "The README's 'wearing Omarchy's clothes' rule gave the app whatever palette the desktop had; the owner's mockups are a designed palette. Omarchy's theme is still one tap away in Settings → Look. The day/night button now always works: between OmaCar and Daylight, or between the desktop's light and dark themes when that look is chosen.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Every drawn number names its source, and a screenshot tool for review

**Files:**
- Modify: `test/app_test.py`
- Create: `tools/shoot.py`

- [ ] **Step 1: Write the failing honesty check**

In `test/app_test.py` `main()`, after the `# 3. THE BOOT SCREEN LET GO.` checks, add:

```python
        # 4. EVERY DRAWN NUMBER SAYS WHERE IT CAME FROM. A signal tile that
        #    draws a value carries data-src (sim, bench, obd, recorded); one
        #    that is waiting or absent carries none and draws words instead.
        #    Home is the arrival screen, so this reads the tiles it painted.
        tiles = re.findall(r'<div class="sig[^"]*"[^>]*>', dom)
        check(f"Home drew its signal tiles (found {len(tiles)})", len(tiles) >= 4)
        live_tiles = [t for t in tiles if 'data-state="live"' in t]
        check("every tile drawing a number names its source",
              all(re.search(r'data-src="[a-z]+"', t) for t in live_tiles))
        check("and no tile that is not live claims one",
              not any('data-src=' in t for t in tiles if t not in live_tiles))
```

- [ ] **Step 2: Run it**

Run `BOXTEST`. Expected: pass. Now prove the check can fail: temporarily comment out the `node.dataset.src = sourceKey(car, s);` line in `sigtile.js`, rerun, and see "every tile drawing a number names its source" FAIL with the simulator running. Restore the line.

- [ ] **Step 3: The screenshot tool**

`tools/shoot.py`:

```python
#!/usr/bin/env python3
"""Screenshots of Home and Vehicle at the tablet's two orientations, for
setting beside the owner's mockups. Not a test: it passes and fails nothing.

    tools/shoot.py [OUT_DIR]        default /tmp/omacar-shots

Uses the same server and the same onboarding seed as test/app_test.py, and the
same outer-window allowance: app.html's inner height comes back 56 px short of
--window-size, so 912 of inner height needs 968 of window.
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARE = os.path.join(ROOT, "share")
SIZES = {"landscape": (1368, 968), "portrait": (912, 1424)}
VIEWS = ("home", "vehicle")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main(argv):
    out = argv[1] if len(argv) > 1 else "/tmp/omacar-shots"
    os.makedirs(out, exist_ok=True)
    exe = shutil.which("chromium") or shutil.which("google-chrome")
    if not exe:
        print("no chromium here")
        return 1
    work = tempfile.mkdtemp()
    copy = os.path.join(work, "share")
    shutil.copytree(SHARE, copy)
    with open(os.path.join(copy, "_seed.html"), "w", encoding="utf-8") as f:
        f.write('<script>localStorage.setItem("omacar.onboarded","1")</script>ok')
    port = free_port()
    py = os.path.expanduser("~/.local/share/omacar/venv/bin/python")
    srv = subprocess.Popen([py if os.path.exists(py) else sys.executable,
                            os.path.join(ROOT, "lib", "serve.py"), str(port), copy],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    prof = tempfile.mkdtemp()
    try:
        time.sleep(1.5)
        base = [exe, "--headless=new", "--disable-gpu", "--no-sandbox",
                f"--user-data-dir={prof}", "--hide-scrollbars",
                "--force-device-scale-factor=2"]
        subprocess.run(base + ["--virtual-time-budget=2000", "--dump-dom",
                               f"http://127.0.0.1:{port}/_seed.html"],
                       capture_output=True, timeout=120)
        for orient, (w, hgt) in SIZES.items():
            for view in VIEWS:
                path = os.path.join(out, f"{view}-{orient}.png")
                subprocess.run(base + [f"--window-size={w},{hgt}", "--virtual-time-budget=9000",
                                       f"--screenshot={path}",
                                       f"http://127.0.0.1:{port}/app.html#{view}"],
                               capture_output=True, timeout=180)
                print(("  wrote " if os.path.exists(path) else "  FAILED ") + path)
    finally:
        srv.terminate()
        shutil.rmtree(prof, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

- [ ] **Step 4: Shoot, and review against the mockups**

```bash
rsync -a --delete --exclude share/assets/private/ /Users/jmyers/omgarchy/omacar/ jmyers@omarchy:Projects/.omacar-test/foundation/
ssh jmyers@omarchy 'cd ~/Projects/.omacar-test/foundation && python3 tools/shoot.py /tmp/omacar-shots'
mkdir -p /private/tmp/claude-501/-Users-jmyers-omgarchy/ca0e3dbc-7045-4968-8cb1-198f8012b2c5/scratchpad/shots
scp 'jmyers@omarchy:/tmp/omacar-shots/*.png' /private/tmp/claude-501/-Users-jmyers-omgarchy/ca0e3dbc-7045-4968-8cb1-198f8012b2c5/scratchpad/shots/
```

Open each PNG next to mockups 3, 4 and 5 and list the differences. Fix the ones that are CSS; leave the ones that are the design's honest deviations (the tyre callout, the Hybrid pack wording, the placeholders). Reshoot until the list only contains deliberate deviations.

- [ ] **Step 5: Commit**

```bash
git add test/app_test.py tools/shoot.py
git commit -m "The start-up test checks that every number Home draws names where it came from" -m "A tile drawing a value must carry data-src; one that is waiting or absent must not. tools/shoot.py takes Home and Vehicle at both orientations for review beside the mockups.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: On the tablet, then a draft PR

**Files:** none changed, unless the tablet finds something.

- [ ] **Step 1: Push the branch from the Mac**

```bash
cd /Users/jmyers/omgarchy/omacar && git push -u origin redesign/foundation
```

- [ ] **Step 2: Put the branch and the private pictures on the Surface without touching the kiosk's checkout**

```bash
ssh jmyers@omarchy 'ssh -o BatchMode=yes omacar "cd ~/Projects/omacar && git fetch origin && git worktree add ~/Projects/.omacar-wt/foundation origin/redesign/foundation"'
ssh jmyers@omarchy 'cd ~/Projects/omacar && python3 ~/Projects/.omacar-test/foundation/lib/assets.py push --to omacar:Projects/.omacar-wt/foundation/share/assets/private/'
```

The first run of `assets.py push` from the box copies the box's main-checkout private folder to the tablet's worktree.

- [ ] **Step 3: Run the suite and the screenshots on the Surface**

This checks the real fonts, the real Chromium and the real 8 GB machine.

```bash
ssh jmyers@omarchy 'ssh -o BatchMode=yes omacar "cd ~/Projects/.omacar-wt/foundation && test/all.sh; python3 tools/shoot.py /tmp/omacar-shots"'
```

Pull the shots back through the box and review them as in Task 11. Report any test that passes on the box and fails on the Surface, with its output.

- [ ] **Step 4: Open the draft PR**

```bash
cd /Users/jmyers/omgarchy/omacar
gh pr create --draft --base omacar-launch --head redesign/foundation \
  --title "Foundation redesign: five tabs, the mockups' look, Home and Vehicle" \
  --body-file /private/tmp/claude-501/-Users-jmyers-omgarchy/ca0e3dbc-7045-4968-8cb1-198f8012b2c5/scratchpad/pr-body.md
```

`pr-body.md` must contain:
- a summary of each task's commit;
- the list of deliberate deviations from the mockups;
- any suite that already failed at baseline (Task 0);
- the tablet results, with the landscape and portrait screenshots attached via the PR's web UI;
- the line `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

Then run `get_status` from the ccd_pr tools and bind the PR if it is not reported.

- [ ] **Step 5: Tell the owner how to see it on the dash**

Give the owner the one command that runs the branch's server on the tablet on a spare port, and how to open it in the kiosk browser. The kiosk itself keeps serving the main checkout until the PR is merged.
