"""Themes you build yourself, kept beside the drive layout.

WHY THE SERVER AND NOT THE BROWSER.

For the same reason the drive layout lives here: a theme you make at the
kitchen table should be the theme the tablet on the dashboard is wearing.
localStorage would have made it per-browser, which on a tool that is
deliberately usable from a phone, a laptop and a bolted-down cockpit display
is the wrong answer three times over.

WHY NINE COLOURS AND NOT TWENTY-NINE.

A theme here is a handful of source colours. Everything the app actually
paints with -- the four surface steps, the four ink weights, the semantic
colours and their backgrounds, the badge inks, the in-car brights -- is
derived from those by theme.palette_of(), which is the same function an
Omarchy theme goes through.

That is deliberate and it is the whole design. palette_of() is where the
contrast floors live: where `faint` gets nudged until it clears 5:1 on the
surface it will actually sit on, where a theme's terminal-yellow gets pulled
until a warning is readable through a windscreen, where --bright earns its
glare margin. An editor that wrote the output tokens directly would let
somebody build a palette that is beautiful on a desk and illegible in a car,
and would need every one of those rules reimplemented in JavaScript to stop
them.

Nine decisions, and the result is legible by construction.
"""

import json
import os
import re
import sys
import time

STORE = os.path.join(os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME", "~/.config")),
    "omarchy", "omacar-themes.json")

# What the app is currently wearing. "omarchy" means follow the desktop.
DESKTOP = "omarchy"

HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}$")

# The colours a theme is made of. Mirrors theme.SOURCE_KEYS; `mode` is handled
# separately because it is not a colour.
COLOURS = ("background", "foreground", "accent",
           "red", "green", "yellow", "blue", "magenta")

# A starting point that is already a coherent theme rather than a blank form.
# Editing something is a far better first move than inventing something.
SEED = {
    "mode": "dark",
    "background": "#12131A",
    "foreground": "#E8EAF2",
    "accent": "#7AA2F7",
    "red": "#F7768E",
    "green": "#9ECE6A",
    "yellow": "#E0AF68",
    "blue": "#7AA2F7",
    "magenta": "#BB9AF7",
}

# Enough to keep a garage of them, few enough that the file stays a file.
MAX_THEMES = 40
MAX_NAME = 48


def _blank():
    return {"active": DESKTOP, "themes": {}}


def load():
    try:
        with open(STORE, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return _blank()
    if not isinstance(raw, dict):
        return _blank()
    out = _blank()
    themes = raw.get("themes")
    if isinstance(themes, dict):
        for tid, body in list(themes.items())[:MAX_THEMES]:
            clean = _clean(tid, body)
            if clean:
                out["themes"][tid] = clean
    active = raw.get("active")
    # An active id pointing at a theme that has been deleted is how an app
    # ends up unstyled with nothing on screen explaining why.
    out["active"] = active if active in out["themes"] else DESKTOP
    return out


def _clean(tid, body):
    """One theme, or None if it is not one. Never raises on a hand-edited file."""
    if not isinstance(tid, str) or not SLUG.match(tid):
        return None
    if not isinstance(body, dict):
        return None
    out = {"name": str(body.get("name") or tid)[:MAX_NAME]}
    out["mode"] = "light" if str(body.get("mode", "")).lower() == "light" else "dark"
    for key in COLOURS:
        v = body.get(key)
        if isinstance(v, str) and HEX.match(v.strip()):
            out[key] = v.strip().lower()
        else:
            out[key] = SEED[key]
    return out


def save(store):
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    body = dict(store)
    body["_comment"] = (
        "OmaCar themes. Nine source colours each; everything else is derived "
        "by lib/theme.py so the contrast floors always apply. Edit here or in "
        "the app: OmaCar → Themes.")
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(body, f, indent=2)
    os.replace(tmp, STORE)
    return load()


def put(tid, body):
    store = load()
    clean = _clean(tid, body)
    if not clean:
        return store, "that is not a theme"
    if tid not in store["themes"] and len(store["themes"]) >= MAX_THEMES:
        return store, "that is as many themes as this keeps"
    store["themes"][tid] = clean
    return save(store), None


def remove(tid):
    store = load()
    if tid in store["themes"]:
        del store["themes"][tid]
        if store["active"] == tid:
            store["active"] = DESKTOP
        return save(store), None
    return store, "no such theme"


def select(tid):
    store = load()
    if tid != DESKTOP and tid not in store["themes"]:
        return store, "no such theme"
    store["active"] = tid
    return save(store), None


def active():
    """(source colours, stamp) for whatever the app should be wearing.

    Returns (None, stamp) to mean "follow the desktop", so the caller keeps
    reading Omarchy's own theme file rather than this having to know how.
    """
    store = load()
    tid = store["active"]
    if tid == DESKTOP or tid not in store["themes"]:
        return None, 0
    # The stamp is what tells a running app the palette moved. It has to change
    # when the theme is edited AND when a different one is picked, so it comes
    # from the file both of those write.
    try:
        stamp = int(os.path.getmtime(STORE))
    except OSError:
        stamp = int(time.time())
    return store["themes"][tid], stamp


# ---- from the command line ---------------------------------------------------
#
# A theme is normally built in the app, which is the right place for it: you
# want to see the palette on the screen it will be worn on. This exists for the
# other case -- a set of themes that lives in the repository as a file, ported
# from somewhere else, that should land on a tablet the same way every time.

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    verb = argv[0] if argv else "list"
    store = load()

    if verb in ("-h", "--help", "help"):
        print("  omacar theme                       what is installed, and "
              "which is worn\n"
              "  omacar theme use <id>              wear one ('omarchy' "
              "follows the desktop)\n"
              "  omacar theme import <file.json>    add the themes in a file")
        return 0

    if verb == "list":
        worn = store["active"]
        for tid, t in sorted(store["themes"].items()):
            mark = "*" if tid == worn else " "
            print(f"  {mark} {tid:<18} {t['name']:<22} {t['mode']}")
        if not store["themes"]:
            print("  none built yet")
        print(f"\n  wearing: {worn}")
        return 0

    if verb == "use":
        if len(argv) < 2:
            print("  which one? omacar theme list", file=sys.stderr)
            return 2
        _store, err = select(argv[1])
        print(f"  {err}" if err else f"  wearing {argv[1]}")
        return 1 if err else 0

    if verb == "import":
        if len(argv) < 2:
            print("  which file?", file=sys.stderr)
            return 2
        try:
            with open(argv[1], encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError) as why:
            print(f"  cannot read it: {why}", file=sys.stderr)
            return 1
        themes = doc.get("themes") if isinstance(doc, dict) else None
        if not isinstance(themes, dict) or not themes:
            print("  no themes in that file", file=sys.stderr)
            return 1
        added, refused = 0, []
        for tid, body in themes.items():
            _store, err = put(tid, body)
            if err:
                # NAMED, NOT COUNTED. A file with one bad entry should say which
                # one, or the next edit is a guess.
                refused.append(f"{tid}: {err}")
            else:
                added += 1
        print(f"  {added} theme(s) installed")
        for r in refused:
            print(f"  refused {r}")
        return 0

    print(f"  unknown: {verb}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
