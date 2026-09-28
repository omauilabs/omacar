"""Fonts you can actually try, kept beside the themes.

WHY A SYSTEM AND NOT A SWAP.

The note back from the meetup was "more polished text -- Inter or something".
Setting Inter is a two-line change and it is the wrong answer, because nobody
knows what this tool should be set in until it has been read in the three
places it gets read: a laptop on a bench, a phone held sideways under a bonnet,
and a tablet bolted to a dashboard in sunlight. A hardcoded family answers the
first of those and guesses at the other two. So this is a picker with a handful
of honest stacks behind it, and the answer is whichever one survives all three.

THREE SLOTS, NOT ONE.

    --sans      every word in the app
    --display   headings and the large readouts
    --mono      small numbers, hex, timestamps, gauge scales

app.css defined only --mono and set `body { font-family: var(--mono) }`, so the
whole tool was monospaced. That is a fine look for a terminal and it is exactly
why the prose in it reads like a log file. The eleven other places app.css
names --mono are all numbers, hex fields and clocks -- those were already the
right call -- so adding --sans and --display in share/css/fonts.css takes the
words off the mono without disturbing a single one of them.

TABULAR FIGURES ARE NOT OPTIONAL.

A speed that changes width as it counts is a speed that jitters, and at seventy
miles an hour a number that moves is a number you read twice. Every family
offered for a numeric slot here either has equal digit advances by construction
(any monospace, and iA Writer's duospaced faces) or carries the `tnum` feature
and is asked for it by fonts.css. Adwaita Sans is the one to watch: its digits
are proportional by default -- the zero is 833 units wide and the four is 1265
-- and it is only safe over a readout because fonts.css sets
font-variant-numeric on every one of them.

NOTHING IS FETCHED.

No webfont, no @font-face pointing at a CDN, no network at all. This is a tool
used in a car park with no signal, and a stack that needs the internet to
render is a stack that renders as something else entirely at the moment it
matters. Everything offered here is a family fontconfig can already see, and
the picker says plainly when one is missing and which package installs it.

WHY THE SERVER AND NOT THE BROWSER.

Same reason as themes and the drive layout: the font you settle on at the
kitchen table should be the font the dashboard is wearing. localStorage would
have made it per-browser, which on a tool deliberately usable from a phone, a
laptop and a bolted-down cockpit display is the wrong answer three times over.
The browser keeps a copy, but only so the first paint does not flash -- the
file below is the truth.
"""

import json
import os
import re
import subprocess
import time

STORE = os.path.join(os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME", "~/.config")),
    "omarchy", "omacar-fonts.json")

# The rollup the bar panel reads. Written by `omacar panel-cache`; we only ever
# merge a key into it, never create it -- see stamp_panel_cache().
PANEL_CACHE = os.path.join(os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state")),
    "omarchy", "liquid-glass-car.json")

DEFAULT = "omacar"

# The three slots every stack has to fill. Kept as a tuple because the order is
# the order fonts.css declares them in and the order the picker shows them in.
SLOTS = ("sans", "display", "mono")

# CSS keywords rather than families. These never appear in fc-list, must never
# be quoted, and are the reason every stack below can still render on a machine
# with none of the named fonts on it.
GENERIC = {"sans-serif", "serif", "monospace", "cursive", "fantasy",
           "system-ui", "ui-sans-serif", "ui-serif", "ui-monospace",
           "ui-rounded", "math", "emoji"}

# A family name, and nothing that could close a CSS declaration. The store is a
# file somebody is invited to hand-edit, and its contents are interpolated into
# a stylesheet -- so this is a whitelist, not a blacklist.
FAMILY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .+_-]{0,62}$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")

CUSTOM = "custom"
MAX_NAME = 48
# Six is already one more than any stack here needs. A fallback chain longer
# than that is not a fallback chain, it is a wish list.
MAX_CHAIN = 6

# ---- the curated set --------------------------------------------------------
#
# Every one of these was checked against fc-list on the machine it was written
# on, and every `package` is the Arch package that supplies the head family.
# `head` is what the stack is really asking for; everything after it in the
# chain is what happens when that font is not there, which is a thing the
# picker shows rather than hides.

STACKS = (
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
    {
        "id": "workshop",
        "name": "Workshop",
        "note": "Adwaita Sans for words, Adwaita Mono for numbers. Adwaita "
                "Sans is derived from Inter and ships with the desktop, so "
                "this is the Inter answer without anything to install.",
        "package": "adwaita-fonts",
        "sans": ["Adwaita Sans", "Inter", "Noto Sans", "Liberation Sans", "sans-serif"],
        "display": ["Adwaita Sans", "Inter", "Noto Sans", "Liberation Sans", "sans-serif"],
        "mono": ["Adwaita Mono", "JetBrainsMono Nerd Font", "ui-monospace", "monospace"],
    },
    {
        "id": "inter",
        "name": "Inter",
        "note": "The font that was asked for by name. Needs installing; "
                "until it is, this renders as Adwaita Sans, which is the same "
                "design and very nearly the same shapes.",
        "package": "inter-font",
        "sans": ["Inter", "Adwaita Sans", "Noto Sans", "Liberation Sans", "sans-serif"],
        "display": ["Inter", "Adwaita Sans", "Noto Sans", "Liberation Sans", "sans-serif"],
        "mono": ["JetBrainsMono Nerd Font", "Adwaita Mono", "ui-monospace", "monospace"],
    },
    {
        "id": "writer",
        "name": "Writer",
        "note": "iA Writer's duospaced faces. Wider and more deliberate than "
                "a UI sans, and its digits are fixed-width by construction, "
                "which is a rare thing in something this readable.",
        "package": "ttf-ia-writer",
        "sans": ["iA Writer Quattro S", "iA Writer Duo S", "Noto Sans", "sans-serif"],
        "display": ["iA Writer Duo S", "iA Writer Quattro S", "Noto Sans", "sans-serif"],
        "mono": ["iA Writer Mono S", "JetBrainsMono Nerd Font", "ui-monospace", "monospace"],
    },
    {
        "id": "terminal",
        "name": "Terminal",
        "note": "What the tool looked like before any of this: monospace all "
                "the way through. Note the family name -- app.css asked for "
                "\"JetBrains Mono\", and what is installed here is called "
                "\"JetBrainsMono Nerd Font\", which is not the same string.",
        "package": "ttf-jetbrains-mono-nerd",
        "sans": ["JetBrainsMono Nerd Font", "JetBrains Mono", "ui-monospace", "monospace"],
        "display": ["JetBrainsMono Nerd Font", "JetBrains Mono", "ui-monospace", "monospace"],
        "mono": ["JetBrainsMono Nerd Font", "JetBrains Mono", "ui-monospace", "monospace"],
    },
    {
        "id": "noto",
        "name": "Noto",
        "note": "Plain, wide coverage, and on this machine it is already "
                "there because everything pulls it in. The safe answer when a "
                "screen has to be legible rather than characterful.",
        "package": "noto-fonts",
        "sans": ["Noto Sans", "Liberation Sans", "sans-serif"],
        "display": ["Noto Sans", "Liberation Sans", "sans-serif"],
        "mono": ["Adwaita Mono", "JetBrainsMono Nerd Font", "ui-monospace", "monospace"],
    },
    {
        "id": "system",
        "name": "System",
        "note": "No opinion at all: whatever the browser and fontconfig "
                "between them decide a UI font is. Cannot be missing, which "
                "makes it the thing to fall back to when a stack looks wrong.",
        "package": None,
        "sans": ["system-ui", "sans-serif"],
        "display": ["system-ui", "sans-serif"],
        "mono": ["ui-monospace", "monospace"],
    },
)

BY_ID = {s["id"]: s for s in STACKS}


# ---- what is actually installed ---------------------------------------------
#
# fontconfig is asked, not guessed at. The alternative -- shipping a list of
# what we think Arch installs by default -- is how a picker ends up confidently
# offering a font that is not on the machine, which is the one failure mode
# that makes the whole feature untrustworthy.

_seen = {"at": 0.0, "families": None}
# Long enough that opening the picker does not shell out per card, short enough
# that installing a font and coming back finds it.
_TTL = 30.0


def families(force=False):
    """Every family fontconfig can see, lowercased. None if it could not look.

    None and empty are different answers and the picker shows them differently:
    "this font is missing" and "there is no fontconfig here to ask" want very
    different reactions from whoever is reading.
    """
    now = time.time()
    if not force and _seen["families"] is not None and now - _seen["at"] < _TTL:
        return _seen["families"]
    try:
        out = subprocess.run(["fc-list", ":", "family"], timeout=6,
                             capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    got = set()
    for line in out.stdout.splitlines():
        # One line per face, and a face lists every alias it answers to
        # comma-separated: "JetBrainsMono Nerd Font,JetBrainsMono NF".
        for name in line.split(","):
            name = name.strip()
            if name:
                got.add(name.lower())
    _seen["families"], _seen["at"] = got, now
    return got


def resolve(chain, known=None):
    """The first family in `chain` that will really render, or None.

    Generics are skipped rather than returned: "sans-serif" is a true answer to
    "what will the browser use" and a useless one to "what should I write into
    a QML font.family", which is the caller that needs this.
    """
    if known is None:
        known = families()
    for name in chain:
        if name in GENERIC:
            continue
        if known is None or name.lower() in known:
            return name
    return None


def missing(stack, known=None):
    """The head families of a stack that are not on this machine.

    Only the heads. A fallback that is absent is a fallback doing its job; a
    head that is absent means the card is showing you something other than
    what it is named after, and that is worth saying out loud.
    """
    if known is None:
        known = families()
    if known is None:
        return []
    out = []
    for slot in SLOTS:
        head = stack[slot][0]
        if head in GENERIC or head in stack.get("bundled", ()):
            continue
        if head.lower() not in known and head not in out:
            out.append(head)
    return out


# ---- turning a stack into CSS ------------------------------------------------

def css_value(chain):
    """One font-family value. Families quoted, generics bare."""
    parts = []
    for name in chain:
        if name in GENERIC:
            parts.append(name)
        elif FAMILY.match(name):
            parts.append('"%s"' % name)
    if not parts:
        parts.append("sans-serif")
    return ", ".join(parts)


def css(stack):
    """{sans, display, mono} as font-family values, ready for --sans etc."""
    return {slot: css_value(stack[slot]) for slot in SLOTS}


# ---- the store ---------------------------------------------------------------

def _blank():
    return {"active": DEFAULT, "custom": None}


def _clean_chain(raw, fallback):
    """A fallback chain, or the built-in one if it is not one.

    Never raises: this comes out of a file a person is encouraged to open.
    """
    if not isinstance(raw, (list, tuple)):
        return list(fallback)
    out = []
    for name in raw:
        if not isinstance(name, str):
            continue
        name = " ".join(name.split())
        if name in GENERIC or FAMILY.match(name):
            if name not in out:
                out.append(name)
        if len(out) >= MAX_CHAIN:
            break
    if not out:
        return list(fallback)
    # Something at the end that cannot itself be missing. Without this a custom
    # stack naming one font that is not installed renders as the browser's
    # default serif, which looks like the app broke rather than like a typo.
    if out[-1] not in GENERIC:
        out.append(fallback[-1] if fallback[-1] in GENERIC else "sans-serif")
    return out


def _clean_custom(body):
    """The hand-rolled stack, or None if there is not one."""
    if not isinstance(body, dict):
        return None
    seed = BY_ID[DEFAULT]
    out = {"id": CUSTOM,
           "name": str(body.get("name") or "Something else")[:MAX_NAME],
           "note": "Yours. Name any family fontconfig can see -- "
                   "`fc-list : family` lists them.",
           "package": None}
    for slot in SLOTS:
        out[slot] = _clean_chain(body.get(slot), seed[slot])
    return out


def load():
    """What the machine is wearing. Never raises on a hand-edited file."""
    try:
        with open(STORE, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return _blank()
    if not isinstance(raw, dict):
        return _blank()
    out = _blank()
    out["custom"] = _clean_custom(raw.get("custom"))
    active = raw.get("active")
    # An active id naming a stack that no longer exists -- or "custom" with no
    # custom block under it -- is how an app ends up rendering in the browser's
    # default serif with nothing on screen explaining why.
    if active in BY_ID or (active == CUSTOM and out["custom"]):
        out["active"] = active
    return out


def save(store):
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    body = {"active": store.get("active", DEFAULT)}
    if store.get("custom"):
        body["custom"] = {k: store["custom"][k] for k in ("name",) + SLOTS}
    body["_comment"] = (
        "OmaCar fonts. `active` is one of: "
        + ", ".join([s["id"] for s in STACKS] + [CUSTOM])
        + ". `custom` is your own stack -- three lists of family names, most "
        "wanted first, ending in a CSS generic. `fc-list : family` lists what "
        "this machine has. Edit here or in the app: OmaCar -> Themes.")
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(body, f, indent=2)
    os.replace(tmp, STORE)
    out = load()
    # The bar panel does not read this file and should not have to. Put the
    # resolved families where it already looks, so picking a font in the app
    # changes the panel too.
    stamp_panel_cache(out)
    return out


def select(sid):
    store = load()
    if sid == CUSTOM and not store["custom"]:
        return store, "there is no custom stack to wear yet"
    if sid != CUSTOM and sid not in BY_ID:
        return store, "no such font"
    store["active"] = sid
    return save(store), None


def put_custom(body):
    clean = _clean_custom(body)
    if not clean:
        return load(), "that is not a font stack"
    store = load()
    store["custom"] = clean
    store["active"] = CUSTOM
    return save(store), None


def stacks(store=None):
    """Everything pickable, in the order it should be shown."""
    store = store or load()
    out = list(STACKS)
    if store["custom"]:
        out.append(store["custom"])
    return out


def active(store=None):
    """(the stack being worn, stamp).

    The stamp is what tells a running app the choice moved, exactly as the
    theme stamp does -- it has to change when a different stack is picked AND
    when the custom one is edited, so it comes off the file both of those write.
    """
    store = store or load()
    sid = store["active"]
    stack = store["custom"] if sid == CUSTOM and store["custom"] else BY_ID.get(sid)
    if stack is None:
        stack = BY_ID[DEFAULT]
    try:
        stamp = int(os.path.getmtime(STORE))
    except OSError:
        stamp = 0
    return stack, stamp


def catalogue():
    """Everything the picker needs, in one read.

    The derived CSS ships with each card rather than being assembled in the
    browser, for the same reason a theme's palette does: one copy of the
    quoting rules, and a preview that cannot drift from what gets applied.
    """
    store = load()
    known = families()
    stack, stamp = active(store)
    out = []
    for s in stacks(store):
        out.append({
            "id": s["id"], "name": s["name"], "note": s["note"],
            "package": s.get("package"),
            "install": ("sudo pacman -S " + s["package"]) if s.get("package") else None,
            "families": {slot: list(s[slot]) for slot in SLOTS},
            "css": css(s),
            "missing": missing(s, known),
            # What each slot will really render as. None means every named
            # family in that chain is absent and a generic is doing the work.
            "resolved": {slot: resolve(s[slot], known) for slot in SLOTS},
        })
    return {"active": store["active"], "default": DEFAULT, "stamp": stamp,
            "stacks": out, "css": css(stack), "slots": list(SLOTS),
            # None here means fontconfig could not be asked at all, which is a
            # different thing from "nothing is installed" and the picker says so.
            "checked": known is not None,
            "custom": store["custom"] and {k: store["custom"][k]
                                           for k in ("name",) + SLOTS}}


# ---- the bar panel -----------------------------------------------------------

def panel_payload(store=None):
    """The chosen fonts, as QML can use them.

    Qt takes ONE family name, not a fallback chain, so the chain is resolved
    here against fontconfig and the winner is what gets written. An empty
    string means "nothing named is installed" and the panel should keep the
    shell's own font rather than ask Qt for something that is not there.
    """
    stack, _ = active(store)
    known = families()
    out = {"id": stack["id"], "name": stack["name"]}
    for slot in SLOTS:
        out[slot] = resolve(stack[slot], known) or ""
    return out


def stamp_panel_cache(store=None):
    """Merge the chosen fonts into the rollup the bar panel already reads.

    Merged into, never created. `omacar panel-cache` writes that file from
    records.snapshot() in one shot, so a cache we invented here would be a file
    full of fonts and no car, and the panel would show an empty vehicle rather
    than an absent one -- which is a far worse lie than a missing font.

    The same overwrite is why this is called on every read of /api/fonts and
    not only on a save: a panel-cache run drops the key, and the next time the
    app is open it comes back. The durable fix is one line in the panel-cache
    writer; until that lands, this is what keeps the two surfaces agreeing.
    """
    try:
        with open(PANEL_CACHE, encoding="utf-8") as f:
            cached = json.load(f)
    except (OSError, ValueError):
        return False
    if not isinstance(cached, dict):
        return False
    want = panel_payload(store)
    if cached.get("fonts") == want:
        return False
    cached["fonts"] = want
    tmp = PANEL_CACHE + ".fonts.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cached, f)
        os.replace(tmp, PANEL_CACHE)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False
    return True


# ---- from a terminal ---------------------------------------------------------

def _main(argv):
    if len(argv) > 1 and argv[0] == "use":
        store, err = select(argv[1])
        if err:
            print("  " + err)
            return 1
        print("  wearing %s" % store["active"])
        return 0
    cat = catalogue()
    if not cat["checked"]:
        print("\n  fc-list could not be run, so nothing below is verified.\n")
    print()
    for s in cat["stacks"]:
        mark = "*" if s["id"] == cat["active"] else " "
        note = ("  MISSING: " + ", ".join(s["missing"])) if s["missing"] else ""
        print("  %s %-10s %-22s%s" % (mark, s["id"], s["name"], note))
        if s["missing"] and s["install"]:
            print("      %s" % s["install"])
        for slot in SLOTS:
            print("      %-8s %s" % (slot, s["resolved"][slot] or "(generic)"))
    print()
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
