"""Is the CarPlay dongle there, and can the browser open it?

WHAT THIS ANSWERS, AND WHAT IT DELIBERATELY DOES NOT.

The phone screen reaches a Carlinkit adapter through WebUSB, from inside the
browser. That means three separate things have to be true, and when it does not
work they fail in ways that look identical from the driver's seat: a black
rectangle. This says which of the three is missing.

  the dongle       plugged in, and one of the models the driver knows
  permission       the browser may open it -- a raw USB device is root-only on
                   Arch by default, so without the udev rule the device picker
                   lists the dongle, the owner picks it, and the open fails
  the driver       the code that speaks the protocol. WRITTEN, AND NEVER RUN
                   AGAINST AN ADAPTER.

The third is the honest one. lib/carlink.py speaks this protocol from a reading
of it -- every offset sourced, none of it confirmed by a device -- so the phone
screen says the picture is unproven until frames actually arrive, and stops
saying it when they do. What surrounds the driver is proven without hardware:
`omacar phone replay` plays a recording down the identical path.

WHY NOT JUST TRY IT AND SEE. Because a failure in the browser surfaces as a
permission error with no indication of which of the three caused it, and
because the browser cannot see a device that is not plugged in to tell you that
is the reason. Reading /sys is dull, exact, and available from a terminal.
"""

import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The models node-carplay recognises, kept in step with KNOWN_DEVICES in
# share/js/omaplay/source.js. Two copies of a fact is how they come to
# disagree, so this one names the other and a test holds them together.
KNOWN = {
    ("1314", "1520"): "Carlinkit (CPC200 family)",
    ("1314", "1521"): "Carlinkit (CPC200 family, alternate id)",
}

RULES = "/etc/udev/rules.d/99-omacar.rules"

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, YELLOW, RED = "\033[32m", "\033[33m", "\033[31m"


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def usb_devices():
    """Every USB device the kernel can see, as (vendor, product, path)."""
    out = []
    for path in sorted(glob.glob("/sys/bus/usb/devices/*")):
        vid = _read(os.path.join(path, "idVendor"))
        pid = _read(os.path.join(path, "idProduct"))
        if vid and pid:
            out.append((vid.lower(), pid.lower(), path))
    return out


def dongles():
    """The Carlinkit adapters plugged in right now."""
    found = []
    for vid, pid, path in usb_devices():
        name = KNOWN.get((vid, pid))
        if not name:
            continue
        busnum = _read(os.path.join(path, "busnum"))
        devnum = _read(os.path.join(path, "devnum"))
        node = (f"/dev/bus/usb/{int(busnum):03d}/{int(devnum):03d}"
                if busnum and devnum else "")
        found.append({
            "vendor": vid, "product": pid, "name": name, "sys": path,
            "node": node,
            "manufacturer": _read(os.path.join(path, "manufacturer")),
            "model": _read(os.path.join(path, "product")),
            "openable": bool(node) and os.access(node, os.R_OK | os.W_OK),
        })
    return found


def _browser():
    """What the last browser to open the phone screen reported.

    The running daemon first, because it has the freshest copy, and the file it
    writes if nothing is running. ASKING ONLY THE DAEMON WAS WRONG: `omacar
    phone` is most useful on a machine where nothing is up yet -- that is the
    whole point of a preparation step -- and it answered "not known" about a
    fact it had written down thirty seconds earlier.
    """
    import json as _json
    import urllib.error
    import urllib.request
    for port in PORTS:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/phone", timeout=1.0) as r:
                got = (_json.loads(r.read().decode()) or {}).get("browser")
                if got:
                    return got
        except (urllib.error.URLError, OSError, ValueError):
            continue
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import carlink
        return dict(carlink._load_browser())
    except Exception:                                         # noqa: BLE001
        return {}


def rule_installed():
    return "OMACAR_CARPLAY" in _read(RULES)


def report():
    """The three things that have to be true, and which of them are."""
    found = dongles()
    lines = []
    lines.append("")
    lines.append(f"  {BOLD}The phone screen{RESET}")
    lines.append("")

    if found:
        for d in found:
            who = " ".join(x for x in (d["manufacturer"], d["model"]) if x)
            lines.append(f"    {GREEN}dongle{RESET}       {d['name']}"
                         + (f"  {DIM}{who}{RESET}" if who else ""))
            lines.append(f"                 {DIM}{d['sys']}{RESET}")
            if d["openable"]:
                lines.append(f"    {GREEN}permission{RESET}   the browser can "
                             f"open it")
            else:
                lines.append(f"    {RED}permission{RESET}   {d['node'] or 'the device'} "
                             f"is not yours to open")
                if rule_installed():
                    lines.append(f"                 {DIM}the rule is installed; "
                                 f"unplug and replug it{RESET}")
                else:
                    lines.append(f"                 {DIM}run: omacar hotplug "
                                 f"install{RESET}")
    else:
        lines.append(f"    {DIM}dongle       none plugged in{RESET}")
        others = [f"{v}:{p}" for v, p, _s in usb_devices()]
        lines.append(f"                 {DIM}{len(others)} other USB device(s) "
                     f"are visible, so this is looking{RESET}")
        lines.append(f"    {DIM}permission   {'rule installed' if rule_installed() else 'rule NOT installed — omacar hotplug install'}{RESET}")

    # WHAT THE BROWSER ON THIS MACHINE ACTUALLY MANAGED.
    #
    # The fourth thing that has to be true, and the only one this tool cannot
    # test for itself: whether the browser here decodes H.264. It answers yes
    # when asked and then decodes nothing on some machines, so the only honest
    # source is a browser that has tried. The page reports back, and this
    # repeats it.
    seen = _browser()
    if seen:
        route = seen.get("route") or "?"
        pictures = seen.get("pictures") or 0
        headless = "headless" in (seen.get("agent") or "")
        how = {"webcodecs": "WebCodecs, the direct path",
               "mediasource": "a video element, because WebCodecs would not"}
        colour = GREEN if pictures else RED
        lines.append(f"    {colour}decoder{RESET}      "
                     + (how.get(route, route) if pictures
                        else "nothing decoded here"))
        if headless:
            # SAID AS WHAT IT IS. A headless browser has no GPU, so this is an
            # answer about one browser and not about the machine. Reporting it
            # as the machine's answer would understate what the tablet can do,
            # and the tablet is the thing anybody cares about.
            lines.append(f"                 {DIM}{seen.get('note') or 'measured headless'}"
                         f", {seen.get('at') or ''}{RESET}")
            lines.append(f"                 {DIM}the kiosk browser has a GPU "
                         f"and may do better — open the phone{RESET}")
            lines.append(f"                 {DIM}screen once and this is "
                         f"replaced by what it managed{RESET}")
        else:
            lines.append(f"                 {DIM}{pictures} picture(s) from "
                         f"{seen.get('units') or 0} frame(s), "
                         f"{seen.get('at') or 'at some point'}{RESET}")
            if seen.get("note"):
                lines.append(f"                 {DIM}{seen['note'][:120]}{RESET}")
    else:
        lines.append(f"    {DIM}decoder      no browser has opened the phone "
                     f"screen here yet{RESET}")
        lines.append(f"                 {DIM}whether this machine decodes "
                     f"H.264 is not yet known{RESET}")

    # THE PART THAT IS NOT BUILT, SAID PLAINLY AND FIRST-PERSON.
    lines.append(f"    {YELLOW}driver{RESET}       written, never run against "
                 f"an adapter")
    lines.append(f"                 {DIM}Every offset in it is sourced and none "
                 f"of it is confirmed.{RESET}")
    lines.append(f"                 {DIM}The screen says the picture is "
                 f"unproven until a frame{RESET}")
    lines.append(f"                 {DIM}arrives, and stops saying it when one "
                 f"does.{RESET}")
    lines.append("")
    lines.append(f"                 {DIM}Everything around the driver is proven "
                 f"without hardware:{RESET}")
    lines.append(f"                 {DIM}omacar phone replay{RESET}")
    lines.append("")
    return "\n".join(lines), bool(found)


# ------------------------------------------------------------------ the replay
#
# WHY A CANNED CLIP IS WORTH A COMMAND OF ITS OWN.
#
# The picture has to travel five separate things -- a USB driver, a framing, an
# HTTP stream, a hardware decoder and a canvas -- and only the first of them
# needs an adapter. Playing a recording through the other four proves them on a
# desk, so the morning a dongle is finally plugged in, exactly one thing is
# untested. It says REPLAY on its own face throughout.

PORTS = (7560, 7561, 7562, 7563, 7564, 7570, 7580)
CLIP = "phone-replay.h264"


def clip_path():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import records
    return os.path.join(records.STATE, CLIP)


def make_clip(path, seconds=5, width=800, height=640, fps=20):
    """A test pattern, encoded the way the adapter encodes.

    Baseline profile and yuv420p because that is what a car adapter emits and
    what every hardware decoder takes; a clip made with defaults can decode
    here and fail on the tablet, which would be a test proving the wrong thing.
    """
    import shutil
    import subprocess
    ff = shutil.which("ffmpeg")
    if not ff:
        return None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    r = subprocess.run(
        [ff, "-y", "-f", "lavfi", "-i",
         f"testsrc=size={width}x{height}:rate={fps}:duration={seconds}",
         "-c:v", "libx264", "-profile:v", "baseline", "-pix_fmt", "yuv420p",
         "-g", str(fps), "-f", "h264", path],
        capture_output=True, text=True)
    return path if r.returncode == 0 and os.path.exists(path) else None


def _daemon():
    """The running app, if one is up. Loopback only, which is the point."""
    import urllib.error
    import urllib.request
    for port in PORTS:
        url = f"http://127.0.0.1:{port}/api/phone"
        try:
            with urllib.request.urlopen(url, timeout=1.0) as r:
                if r.status == 200:
                    return port
        except (urllib.error.URLError, OSError):
            continue
    return None


def replay(argv):
    import json as _json
    import urllib.error
    import urllib.request

    path = argv[0] if argv else clip_path()
    if not os.path.exists(path):
        print(f"\n  no clip at {path}")
        made = make_clip(path)
        if not made:
            print("  and no ffmpeg here to make one. Either install ffmpeg, or\n"
                  "  put any Annex-B H.264 file at that path.\n")
            return 1
        print(f"  made one: {os.path.getsize(path)} bytes\n")

    port = _daemon()
    if port is None:
        print("\n  the app is not running. Start it with `omacar start`, then\n"
              "  run this again.\n")
        return 1

    body = _json.dumps({"mode": "replay", "path": path}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/phone/start", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5.0) as r:
            answer = _json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(f"\n  the app refused it: {e.read().decode()[:200]}\n")
        return 1

    if answer.get("error"):
        print(f"\n  {answer['error']}\n")
        return 1
    print(f"\n  {GREEN}replaying{RESET}    {os.path.basename(path)}")
    print(f"                 {DIM}A recording, not a phone. The screen says "
          f"so.{RESET}")
    print(f"\n  Open the Phone screen:  "
          f"http://127.0.0.1:{port}/app.html#omaplay\n")
    return 0


def note(argv):
    """Record which decoder a browser here managed, and which browser.

    WHY THE PREP CALLS THIS INSTEAD OF WRITING THE FILE ITSELF. The report is
    the daemon's, and its shape belongs in one place. A shell script composing
    the same JSON would be a second author of the same fact.

    LABELLED, because `omacar phone prep` measures in a HEADLESS browser, which
    has no GPU. The kiosk browser on the same machine has one and may do
    better, so this is an answer about a browser rather than about the machine
    -- and the moment the real one opens the phone screen it replaces this.
    """
    route = (argv[0] if argv else "").strip().lower()
    if route not in ("webcodecs", "mediasource"):
        print(f"  not a decoder route: {route!r}")
        return 2
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import carlink
    carlink.note_browser({
        "route": route,
        # A route is only ever recorded because a picture arrived, so this is
        # the count that says "yes, something decoded" without pretending to a
        # precision a headless run does not have.
        "pictures": 1,
        "note": "proven headless by `omacar phone prep`",
        "agent": "headless chromium",
    })
    return 0


def main(argv):
    if argv and argv[0] == "note":
        return note(argv[1:])
    if argv and argv[0] == "replay":
        return replay(argv[1:])
    if argv and argv[0] in ("-h", "--help", "help"):
        print("\n  omacar phone           what is plugged in, and can it be "
              "opened\n"
              "  omacar phone prep      get this machine ready for an adapter\n"
              "  omacar phone replay    play a recording down the real path\n")
        return 0
    text, found = report()
    print(text)
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
