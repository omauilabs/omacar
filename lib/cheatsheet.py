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
  * { box-sizing: border-box; margin: 0; }
  html, body { width: 100%; height: 100%; }
  body {
    background:
      radial-gradient(120% 90% at 12% 0%, #16202b 0%, #0d1319 55%, #090d12 100%);
    color: #e8eef5;
    font-family: Inter, "SF Pro Text", "Segoe UI", system-ui, sans-serif;
    padding: 4.6vh 3.6vw;
    position: relative;
    -webkit-font-smoothing: antialiased;
  }
  header { display: flex; align-items: baseline; gap: .9em; }
  h1 { font-size: 3.1vh; font-weight: 640; letter-spacing: -.01em; }
  .sub { font-size: 1.55vh; color: #7d90a4; }
  .rule { height: 1px; background: linear-gradient(90deg,#2b3a4a,transparent);
          margin: 1.8vh 0 2.2vh; }
  /* COLUMNS ASSIGNED HERE, NOT BY THE BROWSER.
     CSS multi-column was tried twice and got it wrong both times: with no
     definite height it filled three columns and ran the rest off the bottom
     of the screen, and with a stated height it invented a fifth column and ran
     that off the right. Both failures lose commands from a reference whose one
     job is to be complete. Groups are unbreakable blocks of very different
     sizes, which is the case multicol balances worst, so the packing is done
     in Python where it can be checked. */
  main { display: grid; grid-template-columns: repeat(4, 1fr);
         column-gap: 2.4vw; align-items: start; }
  .col { min-width: 0; }
  section { break-inside: avoid; margin-bottom: 2.5vh; }
  h2 { font-size: 1.62vh; font-weight: 660; color: #d7e3f0;
       letter-spacing: .015em; }
  .about { font-size: 1.32vh; color: #6f8296; margin: .18em 0 .85em; }
  .row { display: grid; grid-template-columns: 1fr; gap: .04em;
         padding: .3em 0 .42em; border-top: 1px solid #1b2530; }
  .row:first-of-type { border-top: 0; }
  /* MONOSPACE FOR THE THINGS YOU TYPE, AND NOTHING ELSE. Prose set in a
     terminal face is harder to read and looks like a document pretending to
     be a terminal. */
  .cmd { font-family: "JetBrains Mono", "CaskaydiaMono Nerd Font",
         ui-monospace, monospace;
         font-size: 1.42vh; color: #7fd0ff; letter-spacing: -.01em; }
  .cmd .arg { color: #5d92b4; }
  .desc { font-size: 1.32vh; color: #94a7ba; line-height: 1.32; }
  footer { position: absolute; left: 3.6vw; right: 3.6vw; bottom: 2.4vh;
           font-size: 1.2vh; color: #56697c;
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


def page(when=None):
    when = when or time.strftime("%-d %B %Y")
    groups = grouped()
    cols = []
    for col in pack(groups):
        blocks = []
        for title, about, rows in col:
            items = "".join(
                f'<div class="row"><div class="cmd">{_cmd_html(c)}</div>'
                f'<div class="desc">{html.escape(d)}</div></div>'
                for c, d in rows)
            blocks.append(f"<section><h2>{html.escape(title)}</h2>"
                          f'<div class="about">{html.escape(about)}</div>'
                          f"{items}</section>")
        cols.append(f'<div class="col">{"".join(blocks)}</div>')
    blocks = cols
    n = sum(len(r) for _t, _a, r in groups)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>OmaCar commands</title><style>{CSS}</style></head><body>
<header><h1>OmaCar</h1>
  <span class="sub">every command, generated from the tool itself</span></header>
<div class="rule"></div>
<main>{''.join(blocks)}</main>
<footer><span>{n} commands &middot; omacar help</span>
  <span>generated {html.escape(when)}</span></footer>
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
        f.write(page())
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    try:
        r = subprocess.run(
            [exe, "--headless=new", "--disable-gpu", "--no-sandbox",
             f"--user-data-dir={os.path.join(tmp, 'profile')}",
             "--hide-scrollbars", "--force-device-scale-factor=1",
             f"--window-size={w},{h}", f"--screenshot={out_path}",
             "--virtual-time-budget=4000", f"file://{src}"],
            capture_output=True, text=True, timeout=180)
        if not os.path.exists(out_path):
            raise RuntimeError((r.stderr or "chromium wrote nothing").strip()[:300])
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

# COMMANDS THAT CHANGE SOMETHING, and must be asked about before they run.
#
# Not a safety mechanism -- every one of these has its own gates further down,
# and the write arm is the real boundary. This is about a screen you can lean
# on: a reference you can tap should not start a sweep or throw away a demo
# because a sleeve brushed it.
CONFIRM = {"write", "prune", "demo", "sim", "mode", "tablet", "hotplug",
           "odometer", "service", "photo", "profile", "vehicle"}


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
    return {"generated": time.time(), "groups": out}

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, YELLOW = "\033[32m", "\033[33m"


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
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
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
        who = set_wallpaper(out)
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
