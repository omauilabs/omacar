"""Every command this tool has, as a picture you can put behind your windows.

WHY IT IS GENERATED AND NOT DRAWN.

A reference card that is maintained by hand is a reference card that is wrong.
It goes stale the first time somebody adds a command and does not think of it,
and a wrong reference is worse than none because it is consulted with
confidence. So the list comes out of `bin/omacar`'s own usage text -- the same
lines `omacar help` prints -- and cannot disagree with the tool it describes.

THE GROUPING IS EDITORIAL AND THE LIST IS NOT.

Which commands belong together is a judgement, and it lives here. But a command
this file has never heard of still appears, under "Everything else", because
the one thing a reference must never do is quietly omit something. Adding a
command to the CLI puts it on the wallpaper whether or not anybody remembers
this file exists.

TYPOGRAPHY, SINCE THE POINT IS READING IT ACROSS A ROOM.

A monospace face for the commands, because they are things to type and the
columns should line up. A proportional face for the prose, because monospace
prose is harder to read and looks like a terminal pretending to be a document.
"""

import html
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "bin", "omacar")

# Which commands belong together. The first word of the command decides, and
# anything not named here still appears -- see the note above.
GROUPS = [
    ("Start here", "The first five minutes",
     ["", "setup", "help", "doctor", "status"]),
    ("Driving", "What you use with the car moving",
     ["kiosk", "live", "drive", "cockpit", "watch", "daemon", "hotplug"]),
    ("Reading the car", "Questions the car will answer",
     ["survey", "scan", "dtc", "dtclog", "learn", "discover", "prospect",
      "listen", "candlog"]),
    ("Faults and care", "What is wrong, and what has been done",
     ["service", "odometer", "concerns", "photo", "share", "snapshot",
      "vehicle", "prune"]),
    ("Writing", "Nothing here sends anything until it is armed",
     ["write", "mode"]),
    ("The tablet", "A machine that lives in a car",
     ["tablet", "phone", "card", "state", "server"]),
    ("This screen", "The reference you are looking at",
     ["cheatsheet", "board"]),
    ("Agents and AI", "The car, as something a program can ask",
     ["ai", "mcp", "plugins", "profile"]),
    ("Without a car", "Everything here works on a desk",
     ["bench", "sim", "demo", "roadmap"]),
]


def entries(path=CLI):
    """Every command in the usage block, with its description.

    Continuation lines -- a command too long for one line, whose description
    sits under it -- are joined back on, because half a description is worse
    than none.
    """
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return out
    started = False
    for ln in lines:
        if not ln.startswith("#"):
            if started:
                break
            continue
        body = ln[1:]
        m = re.match(r"^   (omacar[^ ]*(?: [^ ]+)*?)(?:  +| *$)(.*)$", body)
        if m and body.startswith("   omacar"):
            started = True
            out.append([m.group(1).strip(), m.group(2).strip()])
            continue
        if started and body.startswith("      ") and out:
            # A continuation: the description of the command above it.
            more = body.strip()
            if more:
                out[-1][1] = (out[-1][1] + " " + more).strip()
    return [(c, d) for c, d in out if c]


def grouped(rows=None):
    """The entries, in their groups, with nothing dropped."""
    rows = entries() if rows is None else rows
    by_key = {}
    for cmd, desc in rows:
        parts = cmd.split()
        key = parts[1] if len(parts) > 1 else ""
        by_key.setdefault(key, []).append((cmd, desc))
    used, out = set(), []
    for title, about, keys in GROUPS:
        got = []
        for k in keys:
            if k in by_key and k not in used:
                used.add(k)
                got.extend(by_key[k])
        if got:
            out.append((title, about, got))
    # ANYTHING THIS FILE HAS NEVER HEARD OF STILL APPEARS. A reference that
    # silently omits a command is worse than no reference, because it is
    # consulted with confidence.
    rest = []
    for k, v in sorted(by_key.items()):
        if k not in used:
            rest.extend(v)
    if rest:
        out.append(("Everything else", "Not yet filed into a group above", rest))
    return out


# The look. Dark, because a wallpaper sits behind windows and a bright one
# fights everything on top of it, and because this machine's app is dark.
CSS = """
  :root { --s: 1; --W: __W__px; --H: __H__px; }
  * { box-sizing: border-box; margin: 0; }
  html, body { width: 100%; height: 100%; }
  body {
    background:
      radial-gradient(120% 90% at 12% 0%, #16202b 0%, #0d1319 55%, #090d12 100%);
    color: #e8eef5;
    font-family: Inter, "SF Pro Text", "Segoe UI", system-ui, sans-serif;
    padding: calc(0.04600 * var(--H)) calc(0.03600 * var(--W));
    position: relative;
    /* THE PAGE IS EXACTLY THE SCREEN, WHICH IT WAS NOT.
       The footer is positioned from the bottom of the body, and the body had
       no height of its own — so it was as tall as whatever the columns
       happened to be at that moment, and the footer floated up with them. The
       fit then measured the space between the columns and a footer that had
       already moved, concluded there was less room than there is, and left a
       band of empty screen underneath. A wallpaper is exactly one screen; say
       so. */
    /* Sized in pixels from the sheet this is being drawn at, not in vh.
       Chromium lays a headless page out in a viewport shorter than the window
       it screenshots -- 1681 against 1824 here -- so a page that trusted the
       viewport left a hundred and forty three pixels of screen it never knew
       about, as a band of nothing along the bottom. The size is known when the
       picture is drawn, so it is stated. */
    width: __W__px;
    height: __H__px;
    overflow: hidden;
    -webkit-font-smoothing: antialiased;
  }
  header { display: flex; align-items: center; gap: 1.1em; }
  .title-wrap { display: flex; align-items: baseline; gap: .7em; }
  .spacer { flex: 1 1 auto; }
  /* The status is a snapshot on a picture and says so, because a wallpaper
     drawn this morning that still claims "connected" this evening is a lie
     told by a reference screen. */
  .status { display: flex; align-items: center; gap: .5em;
            font-size: calc(0.01420 * var(--H)); color: #94a7ba; }
  .dot { width: calc(0.00780 * var(--H)); height: calc(0.00780 * var(--H)); border-radius: 50%; background: #56697c; }
  .status[data-state="on"] .dot { background: #4ade80; }
  .status[data-state="sim"] .dot { background: #fbbf24; }
  .status[data-state="off"] .dot,
  .status[data-state="stale"] .dot { background: #5b6a7a; }
  .when { color: #56697c; font-size: calc(0.01220 * var(--H)); }
  .actions { display: flex; gap: .6em; }
  .btn { display: flex; align-items: baseline; gap: .5em;
         padding: .55em 1.05em; border-radius: 999px;
         border: 1px solid #2b3a4a; background: #141d27;
         font-size: calc(0.01380 * var(--H)); color: #cfe0ee; }
  .btn .what { font-size: calc(0.01180 * var(--H)); color: #6f8296; }
  h1 { font-size: calc(0.03100 * var(--H)); font-weight: 640; letter-spacing: -.01em; }
  .sub { font-size: calc(0.01550 * var(--H)); color: #7d90a4; }
  .rule { height: 1px; background: linear-gradient(90deg,#2b3a4a,transparent);
          margin: calc(0.01800 * var(--H)) 0 calc(0.02200 * var(--H)); }
  /* COLUMNS ASSIGNED HERE, NOT BY THE BROWSER.
     CSS multi-column was tried twice and got it wrong both times: with no
     definite height it filled three columns and ran the rest off the bottom
     of the screen, and with a stated height it invented a fifth column and ran
     that off the right. Both failures lose commands from a reference whose one
     job is to be complete. Groups are unbreakable blocks of very different
     sizes, which is the case multicol balances worst, so the packing is done
     in Python where it can be checked. */
  main { display: grid; grid-template-columns: repeat(4, 1fr);
         column-gap: calc(0.02400 * var(--W)); align-items: start; }
  .col { min-width: 0; display: flex; flex-direction: column; }
  section { break-inside: avoid; margin-bottom: calc(0.02500 * var(--H)); }
  h2 { font-size: calc((0.01620 * var(--H)) * var(--s)); font-weight: 660; color: #d7e3f0;
       letter-spacing: .015em; }
  /* PADDING, NOT MARGIN, and the difference cost an evening. A bottom margin
     on the last child collapses out of its parent, so a heading measured on
     its own came back shorter than the same heading with rows under it — by
     about fifteen pixels a section, which over four sections is enough to
     overflow every column. The fit then concluded that nothing fitted at any
     size and left the type at its floor with a column empty. Padding is inside
     the box and is always counted. */
  .about { font-size: calc((0.01320 * var(--H)) * var(--s)); color: #6f8296;
           padding: .18em 0 .85em; }
  .row { display: grid; grid-template-columns: 1fr; gap: .04em;
         padding: .34em 0 .46em; border-top: 1px solid #1b2530; }
  .row:first-of-type { border-top: 0; }
  /* MONOSPACE FOR THE THINGS YOU TYPE, AND NOTHING ELSE. Prose set in a
     terminal face is harder to read and looks like a document pretending to
     be a terminal. */
  .cmd { font-family: "JetBrains Mono", "CaskaydiaMono Nerd Font",
         ui-monospace, monospace;
         font-size: calc((0.01420 * var(--H)) * var(--s)); color: #7fd0ff; letter-spacing: -.01em; }
  .cmd .arg { color: #5d92b4; }
  .desc { font-size: calc((0.01320 * var(--H)) * var(--s)); color: #94a7ba; line-height: 1.32; }
  footer { position: absolute; left: calc(0.03600 * var(--W)); right: calc(0.03600 * var(--W)); bottom: calc(0.02400 * var(--H));
           font-size: calc(0.01200 * var(--H)); color: #56697c;
           display: flex; justify-content: space-between; }
"""


def _cmd_html(cmd):
    """The command, with its optional parts dimmed so the verb stands out."""
    parts = cmd.split(" ", 1)
    head = html.escape(parts[0])
    if len(parts) == 1:
        return head
    rest = html.escape(parts[1])
    # Anything in brackets or with a pipe is a choice, not a thing to type.
    rest = re.sub(r"(\[[^\]]*\]|\S*\|\S*)",
                  lambda m: f'<span class="arg">{m.group(0)}</span>', rest)
    return f"{head} {rest}"


def pack(groups, columns=4):
    """Put the groups into columns, keeping their order and balancing height.

    Greedy: each group goes to the column that is currently shortest, and
    groups are placed in the order they are declared so the reading order down
    each column still makes sense. A group is estimated at two lines of heading
    plus a bit over two lines per command, which is close enough -- the point
    is that nothing runs off an edge, not that the columns end level.
    """
    cost = lambda rows: 3.0 + sum(2.2 + 0.5 * (len(d) > 58) for _c, d in rows)
    cols = [[] for _ in range(columns)]
    height = [0.0] * columns
    for g in groups:
        i = height.index(min(height))
        cols[i].append(g)
        height[i] += cost(g[2])
    return cols


# MASONRY, MEASURED RATHER THAN ESTIMATED.
#
# The first version guessed each group's height from its line count and dealt
# them into four columns. The guess was poor: two columns ran to the bottom of
# the screen and two stopped two thirds of the way down, which on a tablet is a
# quarter of a small screen spent on nothing. Estimating the height of wrapped
# text is not something to be good at from the outside.
#
# The browser already knows every height exactly, so it does the packing: the
# sections are laid out once, measured, and moved into whichever column is
# shortest at that moment. Tall groups land first so the last few can fill in
# around them, which is what stops one column finishing far below the others.
MASONRY = """
(function () {
  const main = document.querySelector('main');
  const root = document.documentElement;
  const all = [...main.querySelectorAll('section')];

  function columns(n) {
    main.style.gridTemplateColumns = 'repeat(' + n + ', 1fr)';
    main.innerHTML = '';
    const cols = [];
    for (let i = 0; i < n; i++) {
      const c = document.createElement('div');
      c.className = 'col';
      main.appendChild(c);
      cols.push(c);
    }
    return cols;
  }

  // MASONRY: WHOLE GROUPS, PLACED WHERE THERE IS ROOM.
  //
  // An earlier version broke a group across a column boundary and repeated its
  // heading, which fills the space beautifully and turns the page into a
  // newspaper. It stops looking like a board of cards and starts looking like
  // a table, and a reference you scan for one command is easier to read as
  // cards. So a group is never split: it is a block, it goes in the column
  // that has the most room, and the columns end where they end.
  function pack(n, scale) {
    root.style.setProperty('--s', scale);
    const cols = columns(n);
    // Measured in a real column, one at a time, so wrapped text is honest.
    const sized = [];
    for (const el of all) {
      cols[0].appendChild(el);
      sized.push({ el, h: el.getBoundingClientRect().height });
      el.remove();
    }
    const gapEl = document.createElement('section');
    cols[0].appendChild(gapEl);
    const gap = parseFloat(getComputedStyle(gapEl).marginBottom) || 0;
    gapEl.remove();

    // Tallest first, into the shortest column. A big block placed last is what
    // leaves one column standing well short of the others.
    const order = sized.slice().sort((a, b) => b.h - a.h);
    const height = new Array(n).fill(0);
    const placed = Array.from({ length: n }, () => []);
    for (const item of order) {
      let i = 0;
      for (let k = 1; k < n; k++) if (height[k] < height[i]) i = k;
      placed[i].push(item);
      height[i] += item.h + gap;
    }
    // Then keep swapping: move a block from the tallest column to the shortest
    // whenever that narrows the gap between them. A few dozen passes settles.
    for (let pass = 0; pass < 80; pass++) {
      let hi = 0, lo = 0;
      for (let k = 1; k < n; k++) {
        if (height[k] > height[hi]) hi = k;
        if (height[k] < height[lo]) lo = k;
      }
      if (height[hi] - height[lo] < 6) break;
      let best = -1, bestGap = height[hi] - height[lo];
      placed[hi].forEach((item, idx) => {
        const after = Math.abs((height[hi] - item.h - gap)
                             - (height[lo] + item.h + gap));
        if (after < bestGap) { bestGap = after; best = idx; }
      });
      if (best < 0) break;
      const [moved] = placed[hi].splice(best, 1);
      placed[lo].push(moved);
      height[hi] -= moved.h + gap;
      height[lo] += moved.h + gap;
    }
    placed.forEach((items, i) => {
      items.sort((a, b) => all.indexOf(a.el) - all.indexOf(b.el));
      items.forEach(({ el }) => cols[i].appendChild(el));
    });
    const tallest = Math.max(...cols.map((c) => c.getBoundingClientRect().height));
    const total = sized.reduce((sum, x) => sum + x.h + gap, 0);
    return { tallest: tallest, total: total };
  }

  const footer = document.querySelector('footer');
  const mainTop = main.getBoundingClientRect().top;
  const floor = footer ? footer.getBoundingClientRect().top : window.innerHeight;
  const avail = floor - mainTop - 40;

  // CHOOSE THE ARRANGEMENT THAT WASTES THE LEAST, not the one with the biggest
  // type. Fewer, wider columns wrap less and so pack differently; the only way
  // to know which arrangement leaves the least empty screen is to lay all of
  // them out and measure. Scanned rather than bisected, because a slightly
  // larger type breaks the groups differently and the height does not fall
  // smoothly as the size grows — 0.95 once overflowed where 1.00 fitted.
  let best = null;
  for (const n of [3, 4, 5]) {
    for (let s = 2.20; s >= 0.60; s -= 0.025) {
      const r = pack(n, s);
      if (r.tallest > avail) continue;
      const fill = r.total / (n * avail);
      // BIGGEST TYPE THAT FITS, with the emptiest screen as the tie-break.
      // Chasing least-waste alone picks three wide columns of small text: the
      // area is filled and nothing is readable across a car. Type size is what
      // a reference screen is for, and filling the height falls out of it —
      // the tallest column reaches the bottom by construction.
      if (!best || s > best.s + 0.02
          || (Math.abs(s - best.s) <= 0.02 && fill > best.fill)) {
        best = { n: n, s: s, fill: fill };
      }
      break;                      // largest type that fits at this width
    }
  }
  if (!best) best = { n: 4, s: 0.6, fill: 0 };
  const shown = pack(best.n, best.s);

  // WHERE EVERY COMMAND ENDED UP, so something else can be laid over the
  // picture and know what is under the finger. The page is laid out at exactly
  // the size it is drawn at, so these are image pixels with no conversion.
  const boxes = [...main.querySelectorAll('.row')].map((row) => {
    const r = row.getBoundingClientRect();
    return {
      command: row.querySelector('.cmd').textContent,
      x: Math.round(r.left), y: Math.round(r.top),
      w: Math.round(r.width), h: Math.round(r.height),
    };
  });
  const out = document.createElement('script');
  out.type = 'application/json';
  out.id = 'omacar-boxes';
  out.textContent = JSON.stringify({
    width: Math.round(document.body.getBoundingClientRect().width),
    height: Math.round(document.body.getBoundingClientRect().height),
    boxes: boxes,
  });
  document.body.appendChild(out);
  root.dataset.packed = best.n + ' cols @' + best.s.toFixed(2)
    + ' fill=' + (best.fill * 100).toFixed(0) + '%'
    + ' tallest=' + Math.round(shown.tallest) + '/' + Math.round(avail);
})();
"""


def page(when=None, size=None):
    when = when or time.strftime("%-d %B %Y")
    w, h = size or screen_size()
    groups = grouped()
    st = status_now()
    seen_at = (time.strftime("%H:%M", time.localtime(st["at"]))
               if st.get("at") else "")
    blocks = []
    for title, about, rows in groups:
        items = "".join(
            f'<div class="row"><div class="cmd">{_cmd_html(c)}</div>'
            f'<div class="desc">{html.escape(d)}</div></div>'
            for c, d in rows)
        blocks.append(f"<section><h2>{html.escape(title)}</h2>"
                      f'<div class="about">{html.escape(about)}</div>'
                      f"{items}</section>")
    # Four empty columns for the script to fill, and every section in the first
    # one to begin with so a browser with no scripting still shows all of them.
    cols = (f'<div class="col">{"".join(blocks)}</div>'
            + '<div class="col"></div>' * 3)
    actions = "".join(
        f'<span class="btn">{html.escape(a["label"])}'
        f'<span class="what">{html.escape(a["about"])}</span></span>'
        for a in ACTIONS)
    n = sum(len(r) for _t, _a, r in groups)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>OmaCar commands</title>
<style>{CSS.replace("__W__", str(w)).replace("__H__", str(h))}</style>
</head><body>
<header>
  <div class="title-wrap"><h1>OmaCar</h1>
    <span class="sub">every command, generated from the tool itself</span></div>
  <div class="spacer"></div>
  <div class="status" data-state="{html.escape(st['state'])}">
    <span class="dot"></span><span>{html.escape(st['label'])}</span>
    <span class="when">{html.escape('at ' + seen_at if seen_at else '')}</span>
  </div>
  <div class="spacer"></div>
  <div class="actions">{actions}</div>
</header>
<div class="rule"></div>
<main>{cols}</main>
<footer><span>{n} commands &middot; omacar help</span>
  <span>generated {html.escape(when)}</span></footer>
<script>{MASONRY}</script>
</body></html>"""


def screen_size():
    """The screen this is for, asked of the compositor when there is one."""
    try:
        import json as _json
        out = subprocess.run(["hyprctl", "-j", "monitors"], capture_output=True,
                             text=True, timeout=5)
        mons = _json.loads(out.stdout)
        if mons:
            m = mons[0]
            w, h = int(m.get("width") or 0), int(m.get("height") or 0)
            # A rotated panel reports its native size and a transform; the
            # wallpaper wants the way round it is actually being looked at.
            if int(m.get("transform") or 0) % 2 == 1:
                w, h = h, w
            if w > 0 and h > 0:
                return w, h
    except Exception:                                         # noqa: BLE001
        pass
    return 2736, 1824                       # the Surface Pro 7's own panel


def browser():
    for name in ("chromium", "chromium-browser", "google-chrome",
                 "google-chrome-stable"):
        p = shutil.which(name)
        if p:
            return p
    return None


def boxes_for(size, tmp, exe, src):
    """Where each command landed in the picture.

    A SECOND RUN OF THE SAME PAGE, because a headless browser will take a
    screenshot or print the document, not both. The layout is deterministic --
    same commands, same size, same fit -- so the second run lands in exactly
    the same places as the first.
    """
    import json as _json
    r = subprocess.run(
        [exe, "--headless=new", "--disable-gpu", "--no-sandbox",
         f"--user-data-dir={os.path.join(tmp, 'profile2')}",
         "--hide-scrollbars", "--force-device-scale-factor=1",
         f"--window-size={size[0]},{size[1]}",
         "--virtual-time-budget=90000", "--dump-dom", f"file://{src}"],
        capture_output=True, text=True, timeout=300)
    m = re.search(r'<script type="application/json" id="omacar-boxes">(.*?)</script>',
                  r.stdout, re.S)
    if not m:
        return None
    try:
        return _json.loads(html.unescape(m.group(1)))
    except ValueError:
        return None


def render(out_path, size=None):
    """Write the reference as a PNG. Returns the path, or raises."""
    exe = browser()
    if not exe:
        raise RuntimeError("chromium is not installed, and it is what draws "
                           "this. `sudo pacman -S chromium`.")
    w, h = size or screen_size()
    import tempfile
    tmp = tempfile.mkdtemp(prefix="omacar-cheatsheet-")
    src = os.path.join(tmp, "sheet.html")
    with open(src, "w", encoding="utf-8") as f:
        f.write(page(size=(w, h)))
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    try:
        r = subprocess.run(
            [exe, "--headless=new", "--disable-gpu", "--no-sandbox",
             f"--user-data-dir={os.path.join(tmp, 'profile')}",
             "--hide-scrollbars", "--force-device-scale-factor=1",
             f"--window-size={w},{h}", f"--screenshot={out_path}",
             # The fit tries about forty layouts. Under a virtual clock that
             # costs no real time, but the budget has to allow for it or the
             # screenshot is taken of a half-fitted page.
             "--virtual-time-budget=90000", f"file://{src}"],
            capture_output=True, text=True, timeout=180)
        if not os.path.exists(out_path):
            raise RuntimeError((r.stderr or "chromium wrote nothing").strip()[:300])
        # The same page again, for the hit boxes the board needs.
        got = boxes_for((w, h), tmp, exe, src)
        if got:
            import json as _json
            os.makedirs(os.path.dirname(BOXES), exist_ok=True)
            with open(BOXES, "w", encoding="utf-8") as f:
                _json.dump(got, f)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out_path


DEFAULT = os.path.join(os.path.expanduser("~"), ".local", "share", "omacar",
                       "omacar-commands.png")
# The same list the picture is drawn from, for anything that wants to do more
# than look at it. The board reads this rather than parsing the CLI a second
# time, because two parsers is two chances to disagree about what the commands
# are -- and the whole point of both is that they cannot.
DATA = os.path.join(os.path.expanduser("~"), ".local", "share", "omacar",
                    "omacar-commands.json")
# WHERE EACH COMMAND IS IN THE PICTURE. The board lies over the wallpaper as a
# sheet of invisible targets rather than drawing the reference a second time --
# two drawings of one thing is two things to keep in step, and the one on top
# was quietly winning.
BOXES = os.path.join(os.path.expanduser("~"), ".local", "share", "omacar",
                     "omacar-boxes.json")

# COMMANDS THAT CHANGE SOMETHING, and must be asked about before they run.
#
# Not a safety mechanism -- every one of these has its own gates further down,
# and the write arm is the real boundary. This is about a screen you can lean
# on: a reference you can tap should not start a sweep or throw away a demo
# because a sleeve brushed it.
CONFIRM = {"write", "prune", "demo", "sim", "mode", "tablet", "hotplug",
           "odometer", "service", "photo", "profile", "vehicle"}


def status_now():
    """Whether the car is there, as of this instant.

    ON A PICTURE THIS IS A SNAPSHOT AND MUST SAY SO. A wallpaper drawn at nine
    in the morning that still says "connected" at six in the evening is a lie
    told by a reference screen, which is the one thing it must not be. So the
    time it was true is drawn beside it, and the board -- which is a live
    surface -- refreshes the same field instead.
    """
    import json as _json
    try:
        import records
        with open(records.LIVE, encoding="utf-8") as f:
            live = _json.load(f)
    except Exception:                                         # noqa: BLE001
        return {"state": "unknown", "label": "no reading", "at": None}
    age = time.time() - (live.get("t") or 0)
    if age > 120:
        return {"state": "stale", "label": "nothing polling", "at": live.get("t")}
    if live.get("simulated"):
        return {"state": "sim", "label": "simulator", "at": live.get("t")}
    if not live.get("connected"):
        return {"state": "off", "label": "not connected", "at": live.get("t")}
    values = live.get("values") or {}
    rpm = values.get("RPM") or 0
    return {"state": "on",
            "label": "connected" + (f" · {rpm:.0f} rpm" if rpm > 200 else ""),
            "at": live.get("t")}


# The two things worth reaching for from a reference screen, and what they do.
ACTIONS = [
    {"id": "dashboard", "label": "Dashboard",
     "run": ["omacar"], "about": "open the app"},
    {"id": "plugin", "label": "Plugin",
     "run": ["qs", "-p", "/usr/share/omarchy/shell", "ipc", "call",
             "omacar", "toggle"],
     "about": "the bar panel"},
]


def as_json():
    """The commands, grouped, with what each one needs to run."""
    out = []
    for title, about, rows in grouped():
        items = []
        for cmd, desc in rows:
            parts = cmd.split()
            verb = parts[1] if len(parts) > 1 else ""
            # A command shown with a choice or a placeholder cannot simply be
            # run: somebody has to say which. Those open a terminal with the
            # line typed and waiting instead of firing.
            needs_input = bool(re.search(r"[\[|]|\bPID\b|\bH D\b", cmd))
            items.append({"command": cmd, "description": desc, "verb": verb,
                          "confirm": verb in CONFIRM,
                          "needs_input": needs_input})
        out.append({"title": title, "about": about, "commands": items})
    return {"generated": time.time(), "groups": out,
            "actions": ACTIONS, "status": status_now()}

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, YELLOW = "\033[32m", "\033[33m"


def _session_env():
    """The Wayland session, for a command that has to reach the compositor.

    Setting a wallpaper talks to the running shell over IPC, and an ssh shell
    carries no display — so it reported success and changed nothing. A tablet
    on a dashboard is exactly the machine somebody does this from over ssh.
    """
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    if not env.get("WAYLAND_DISPLAY"):
        import glob as _glob
        for sock in sorted(_glob.glob(
                os.path.join(env["XDG_RUNTIME_DIR"], "wayland-[0-9]*"))):
            if not sock.endswith(".lock"):
                env["WAYLAND_DISPLAY"] = os.path.basename(sock)
                break
    return env


def versioned(path):
    """A copy under a name that changes when the picture does.

    THE WALLPAPER WAS NOT UPDATING, AND THIS IS WHY. Omarchy points a symlink
    at the file and asks the shell to load it; asked for the same path twice it
    has no reason to read the bytes again, so a freshly drawn picture sat on
    disk while the old one stayed on the screen. Naming the file after its own
    contents means an unchanged picture costs nothing and a changed one is a
    different file, which the shell cannot mistake for the one it already has.
    """
    import hashlib
    with open(path, "rb") as f:
        tag = hashlib.sha256(f.read()).hexdigest()[:10]
    out = os.path.join(os.path.dirname(path),
                       f"omacar-commands-{tag}.png")
    if not os.path.exists(out):
        shutil.copy2(path, out)
    # Older versions are rubbish the moment a new one exists.
    import glob as _glob
    for old in _glob.glob(os.path.join(os.path.dirname(path),
                                       "omacar-commands-*.png")):
        if old != out:
            try:
                os.remove(old)
            except OSError:
                pass
    return out


def set_wallpaper(path):
    """Ask whatever is drawing the background to draw this instead.

    BEST EFFORT AND HONEST ABOUT IT. Which program owns the wallpaper is a
    property of somebody's desktop, not of this tool, and there are at least
    five plausible answers. So each is tried in turn and the one that worked is
    named -- and if none did, the file is still there and the command to use it
    is printed rather than a claim that it was set.
    """
    tries = [
        # Omarchy owns its own background, and asking it is both the thing that
        # works and the thing that survives a theme change. It goes first for
        # that reason rather than because it is likelier to be installed.
        ("omarchy", ["omarchy-theme-bg-set", path]),
        ("swww", ["swww", "img", path]),
        ("hyprpaper", ["hyprctl", "hyprpaper", "reload", f",{path}"]),
        ("swaybg", None),          # swaybg takes no runtime commands
    ]
    for name, cmd in tries:
        if cmd is None or not shutil.which(cmd[0]):
            continue
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                               env=_session_env())
            if r.returncode == 0:
                return name
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def main(argv):
    out = DEFAULT
    size = None
    do_set = False
    rest = []
    want_json = False
    for a in argv:
        if a == "--json":
            want_json = True
        elif a == "--set":
            do_set = True
        elif a.startswith("--size="):
            try:
                w, h = a.split("=", 1)[1].lower().split("x")
                size = (int(w), int(h))
            except ValueError:
                print(f"\n  not a size: {a}   (try --size=2736x1824)\n")
                return 2
        elif a in ("-h", "--help", "help"):
            print("\n  omacar cheatsheet [--set] [--size=WxH] [PATH]\n"
                  "\n  Draws every command this tool has as a picture, from the"
                  "\n  CLI's own usage text, so it cannot go out of date.\n"
                  "\n    --set        also ask the desktop to use it as the "
                  "wallpaper\n"
                  "    --size=WxH   default is whatever the screen is\n")
            return 0
        else:
            rest.append(a)
    if rest:
        out = os.path.abspath(rest[0])

    n = len(entries())
    if not n:
        print("\n  could not read the command list out of bin/omacar.\n")
        return 1

    # The data is written whichever way this was called, because the board
    # reads it and the board should never be looking at a stale list.
    import json as _json
    os.makedirs(os.path.dirname(DATA), exist_ok=True)
    with open(DATA, "w", encoding="utf-8") as f:
        _json.dump(as_json(), f, indent=1)
    if want_json:
        print(DATA)
        return 0
    try:
        render(out, size)
    except Exception as why:                                  # noqa: BLE001
        print(f"\n  {YELLOW}could not draw it{RESET}: {why}\n")
        return 1
    w, h = size or screen_size()
    kb = os.path.getsize(out) // 1024
    print(f"\n  {GREEN}drew {n} commands{RESET}   {w}×{h}, {kb} KB")
    print(f"  {DIM}{out}{RESET}")
    if do_set:
        who = set_wallpaper(versioned(out))
        if who:
            print(f"  {GREEN}set as the wallpaper{RESET}   {DIM}via {who}{RESET}")
        else:
            print(f"  {YELLOW}could not set it{RESET} — nothing here answers to "
                  f"a wallpaper command.")
            print(f"  {DIM}Point your desktop's wallpaper at the file above.{RESET}")
    else:
        print(f"  {DIM}--set to use it as the wallpaper{RESET}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
