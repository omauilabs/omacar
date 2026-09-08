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
  the driver       the code that speaks the protocol. NOT WRITTEN YET.

The third is the honest one. This tool does not yet implement the Carlinkit
protocol: the phone screen runs a mock that says so on its face in amber. What
is here is the plumbing around it, and the point of checking the first two now
is that they are the parts that can be got wrong silently, weeks before anybody
looks at the third.

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

    # THE PART THAT IS NOT BUILT, SAID PLAINLY AND FIRST-PERSON.
    lines.append(f"    {YELLOW}driver{RESET}       not written")
    lines.append(f"                 {DIM}OmaCar does not speak the Carlinkit "
                 f"protocol yet. The phone{RESET}")
    lines.append(f"                 {DIM}screen runs a mock and says so in "
                 f"amber on its own face.{RESET}")
    lines.append(f"                 {DIM}Plugging a dongle in will not make a "
                 f"phone appear.{RESET}")
    lines.append("")
    return "\n".join(lines), bool(found)


def main(argv):
    text, found = report()
    print(text)
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
