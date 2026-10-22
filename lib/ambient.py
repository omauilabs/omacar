#!/usr/bin/env python3
"""The tablet's ambient light sensor, read and nothing else.

The Surface Pro 7+ has one, which Linux exposes through the Industrial I/O
subsystem as /sys/bus/iio/devices/iio:deviceN/in_illuminance*. The app polls
GET /api/ambient while "Looks by the sun" is on, and uses the reading to wear
the night look in a tunnel or a garage (share/js/sunlook.js: darkness must last
20 s). Read-only: nothing here writes a file, opens a device node or starts a
service, so there is no unit for it.

    omacar ambient       what the sensor says now (or that there is none)

The answer is {"present": false} when no sensor is found. That is a normal
state, not an error -- the app then runs on the sun alone and says so. A sensor
that is there but cannot be read this instant is {"present": true, "lux": null}:
never a zero, because zero lux is a real reading (a dark room) and "unknown"
must not look like it.
"""

import glob
import json
import os
import sys

# Tests point this at a fake sysfs tree.
IIO_ROOT = os.environ.get("OMACAR_IIO_ROOT", "/sys/bus/iio/devices")

# Files the kernel drivers use for an illuminance channel, best first. The
# *_input form is already in lux; the raw form needs scale (and offset).
LUX_INPUT = ("in_illuminance_input", "in_illuminance0_input")
LUX_RAW = ("in_illuminance_raw", "in_illuminance0_raw")


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _num(text):
    try:
        v = float(text)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def _lux_of(dev):
    """Lux from one iio device directory, or None if it has no illuminance
    channel (present is separate: see read())."""
    for name in LUX_INPUT:
        v = _num(_read(os.path.join(dev, name)))
        if v is not None:
            return max(0.0, v)
    for name in LUX_RAW:
        raw = _num(_read(os.path.join(dev, name)))
        if raw is None:
            continue
        scale = _num(_read(os.path.join(dev, name.replace("_raw", "_scale"))))
        if scale is None:
            scale = _num(_read(os.path.join(dev, "in_illuminance_scale")))
        offset = _num(_read(os.path.join(dev, name.replace("_raw", "_offset")))) or 0.0
        return max(0.0, (raw + offset) * (1.0 if scale is None else scale))
    return None


def has_channel(dev):
    names = LUX_INPUT + LUX_RAW
    return any(os.path.exists(os.path.join(dev, n)) for n in names)


def read(root=None):
    """{"present": bool, "lux": float|None, "device": str|None}."""
    root = IIO_ROOT if root is None else root
    for dev in sorted(glob.glob(os.path.join(root, "iio:device*"))):
        if not has_channel(dev):
            continue
        lux = _lux_of(dev)
        name = _read(os.path.join(dev, "name")) or os.path.basename(dev)
        return {"present": True, "lux": None if lux is None else round(lux, 1), "device": name}
    return {"present": False, "lux": None, "device": None}


def read_public(root=None):
    r = read(root)
    r["source"] = "iio" if r["present"] else None
    return r


def main(argv):
    print(json.dumps(read_public(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
