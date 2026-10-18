#!/usr/bin/env python3
"""OmaCar follows OmaAccess, and speaks only when it is allowed to.

lib/access.py's mapping and its speech gate are pure, so they are checked here
against OmaAccess's status JSON as its main branch (0.23.1) prints it, with no
omaaccess installed and no speaker: nothing in this file can make a sound.

The promises:
  * every setting OmaCar follows is read from OmaAccess's EFFECTIVE state
    (status --json), booleans as "on"/"off", text size in px;
  * a status that is not one (old version, garbage) is not guessed at;
  * a setting written back goes to OmaAccess under its own key and format;
  * a bad value is refused with words, never stored;
  * no speech when the person did not ask for it, none over a screen reader,
    and none overnight with the car off unless it is a safety alert;
  * the remote cockpit can neither change these settings nor make it speak;
  * the demo answers both routes without touching the machine.
"""

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import access  # noqa: E402

fails = 0


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"    FAIL  {msg}")


def check(msg, cond):
    (ok if cond else bad)(msg)


def status(**over):
    """OmaAccess's status --json, its shape from bin/omaaccess on main."""
    settings = {k: {"value": "off", "source": "default"} for k in
                ("contrast", "still", "dyslexiaFont", "bigTargets", "alerts", "captions",
                 "sr", "speakAlerts")}
    settings["textSize"] = {"value": "12", "source": "default"}
    settings["colorFilter"] = {"value": "none", "source": "default"}
    settings["language"] = {"value": "auto", "source": "default"}
    for k, v in over.items():
        settings[k] = {"value": v, "source": "you"}
    return {"version": "0.23.1", "lang": "en", "summary": "", "profiles": [],
            "settings": settings, "system": {"textSize": 12}}


def main():
    print("\n  OmaAccess's state, as OmaCar reads it")
    s = access.from_omaaccess(status())
    check("all off by default, scale 100%", s and not any(s[k] for k in
          ("contrast", "still", "dyslexia", "big", "flash", "captions", "reader", "speak"))
          and s["scale"] == 1.0 and s["filter"] == "none")
    s = access.from_omaaccess(status(contrast="on", still="on", dyslexiaFont="on", bigTargets="on",
                                     alerts="on", captions="on", sr="on", speakAlerts="on"))
    check("each OmaAccess switch turns on its OmaCar setting",
          all(s[k] for k in ("contrast", "still", "dyslexia", "big", "flash", "captions", "reader", "speak")))
    check("text size 18 px is a 150% page", access.from_omaaccess(status(textSize="18"))["scale"] == 1.5)
    check("text size is held to 100%..200%",
          (access.from_omaaccess(status(textSize="9"))["scale"],
           access.from_omaaccess(status(textSize="40"))["scale"]) == (1.0, 2.0))
    check("a colour filter is passed on by name", access.from_omaaccess(status(colorFilter="tritan"))["filter"] == "tritan")
    check("an unknown filter is not guessed at", access.from_omaaccess(status(colorFilter="sepia"))["filter"] == "none")
    check("garbage is not a status", access.from_omaaccess({"nope": 1}) is None and access.from_omaaccess(None) is None)

    print("\n  writing back")
    check("a switch goes to OmaAccess as on/off under its own key",
          access.to_omaaccess("dyslexia", True) == ("dyslexiaFont", "on")
          and access.to_omaaccess("flash", False) == ("alerts", "off"))
    check("a scale goes back as px", access.to_omaaccess("scale", 1.5) == ("textSize", "18"))
    for name, value in (("scale", 3), ("contrast", "yes"), ("filter", "sepia"), ("rm -rf", True)):
        try:
            access.clean(name, value)
            bad(f"{name}={value!r} was accepted")
        except ValueError as e:
            ok(f"{name}={value!r} refused: {e}")

    print("\n  OmaCar's own settings, without OmaAccess")
    scratch = tempfile.mkdtemp()
    saved = (access.CONF_DIR, access.LOCAL, access._omaaccess)
    try:
        access.CONF_DIR, access.LOCAL = scratch, os.path.join(scratch, "access.json")
        access._omaaccess = lambda: None
        pub = access.read_public()
        check("no OmaAccess: the source says so, and all is default",
              pub["source"] == "omacar" and pub["settings"]["scale"] == 1.0)
        access.write("big", True)
        access.write("scale", "1.3")
        with open(access.LOCAL) as f:
            doc = json.load(f)
        check("a change is stored in OmaCar's own file", doc == {"big": True, "scale": 1.3})
        check("and read back", access.read_public()["settings"]["big"] is True)
        with open(access.LOCAL, "w") as f:
            json.dump({"big": "banana", "scale": 9}, f)
        check("a hand-edited bad value falls back to the default",
              access.read_public()["settings"]["big"] is False and access.read_public()["settings"]["scale"] == 1.0)
    finally:
        access.CONF_DIR, access.LOCAL, access._omaaccess = saved
        shutil.rmtree(scratch, ignore_errors=True)

    print("\n  when OmaCar may speak (a child sleeps near the tablet)")
    on = {"speak": True, "reader": False}
    night, day = 2 * 60, 14 * 60
    gate = access.speech_gate
    check("reading aloud off: never, not even safety", gate("safety", {"speak": False}, day, True)[0] is False)
    check("a screen reader running: never (Orca reads the page)",
          gate("safety", {"speak": True, "reader": True}, day, True)[0] is False)
    check("daytime, asked for: yes", gate("info", on, day, False)[0] is True)
    check("overnight, car off, an ordinary alert: no", gate("info", on, night, False)[0] is False)
    check("overnight, car off, a safety alert: yes", gate("safety", on, night, False)[0] is True)
    check("overnight with the car on: yes (somebody is driving)", gate("info", on, night, True)[0] is True)
    check("quiet hours switched off by the owner: yes",
          gate("info", on, night, False, {"on": False})[0] is True)

    print("\n  who may reach it")
    import serve
    check("not a cockpit write: a remote screen cannot change it",
          "/api/access" not in serve.COCKPIT_WRITES and "/api/access/speak" not in serve.COCKPIT_WRITES)
    check("the demo answers both routes with fixed words",
          isinstance(serve.DEMO_ROUTES.get("/api/access"), dict)
          and serve.DEMO_ROUTES["/api/access/speak"].get("spoken") is False)

    print(f"\n  {'all good' if not fails else f'{fails} failed'}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
