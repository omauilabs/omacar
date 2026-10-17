#!/usr/bin/env python3
"""The ambient light sensor reader (lib/ambient.py): a fake sysfs tree, no hardware."""

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import ambient  # noqa: E402

fails = 0


def check(msg, cond):
    global fails
    if cond:
        print(f"    ok  {msg}")
    else:
        fails += 1
        print(f"    FAIL  {msg}")


def dev(root, n, files):
    d = os.path.join(root, f"iio:device{n}")
    os.makedirs(d)
    for k, v in files.items():
        with open(os.path.join(d, k), "w") as f:
            f.write(str(v) + "\n")
    return d


def main():
    tmp = tempfile.mkdtemp(prefix="ambient-")
    try:
        print("\n  ambient light\n")
        check("no iio tree at all: absent, no lux, no error",
              ambient.read(os.path.join(tmp, "nowhere")) == {"present": False, "lux": None, "device": None})

        root = os.path.join(tmp, "a"); os.makedirs(root)
        dev(root, 0, {"name": "accel_3d", "in_accel_x_raw": 1})
        check("a device with no illuminance channel is not a light sensor",
              ambient.read(root)["present"] is False)

        root = os.path.join(tmp, "b"); os.makedirs(root)
        dev(root, 0, {"name": "accel_3d", "in_accel_x_raw": 1})
        dev(root, 1, {"name": "als", "in_illuminance_input": "312.5"})
        r = ambient.read(root)
        check("lux straight from in_illuminance_input, found past other devices",
              r == {"present": True, "lux": 312.5, "device": "als"})

        root = os.path.join(tmp, "c"); os.makedirs(root)
        dev(root, 0, {"name": "als", "in_illuminance_raw": 400, "in_illuminance_scale": "0.25"})
        check("raw times scale", ambient.read(root)["lux"] == 100.0)

        root = os.path.join(tmp, "d"); os.makedirs(root)
        dev(root, 0, {"name": "als", "in_illuminance_raw": 100, "in_illuminance_scale": "2", "in_illuminance_offset": "-20"})
        check("(raw + offset) times scale", ambient.read(root)["lux"] == 160.0)

        root = os.path.join(tmp, "e"); os.makedirs(root)
        dev(root, 0, {"name": "als", "in_illuminance_input": "0"})
        r = ambient.read(root)
        check("zero lux is a reading (a dark room), not an absence",
              r["present"] is True and r["lux"] == 0.0)

        root = os.path.join(tmp, "f"); os.makedirs(root)
        dev(root, 0, {"name": "als", "in_illuminance_input": "garbage"})
        r = ambient.read(root)
        check("an unreadable value is present with lux null, never a zero",
              r["present"] is True and r["lux"] is None)

        root = os.path.join(tmp, "g"); os.makedirs(root)
        dev(root, 0, {"name": "als", "in_illuminance_input": "-3"})
        check("a negative reading clamps to zero", ambient.read(root)["lux"] == 0.0)

        check("read_public labels its source",
              ambient.read_public(root)["source"] == "iio"
              and ambient.read_public(os.path.join(tmp, "nowhere"))["source"] is None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n  {'all good' if not fails else f'{fails} failed'}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
