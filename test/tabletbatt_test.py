#!/usr/bin/env python3
"""The tablet's battery (lib/tabletbatt.py): health maths, the limit heuristic,
the heat rule, and sysfs fixtures with attributes missing. No hardware."""

import datetime
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

fails = 0


def check(msg, cond):
    global fails
    if cond:
        print(f"    ok  {msg}")
    else:
        fails += 1
        print(f"    FAIL  {msg}")


def day(i, **kw):
    d = (datetime.date(2026, 1, 1) + datetime.timedelta(days=i)).isoformat()
    base = {"day": d, "health": None, "full": None, "design": None, "cycles": None,
            "peak_cap": None, "charge_samples": 0, "samples": 0, "max_temp": None,
            "hot_samples": 0}
    base.update(kw)
    return base


def main():
    scratch = tempfile.mkdtemp(prefix="omacar-tabbatt-")
    os.environ["XDG_STATE_HOME"] = os.path.join(scratch, "state")
    import tabletbatt as tb
    try:
        print("\n  parsing sysfs, with things missing")
        full = {"energy_full": "38700000\n", "energy_full_design": "43000000\n",
                "capacity": "63\n", "status": "Charging\n", "cycle_count": "88\n",
                "temp": "412\n"}
        r = tb.parse(full)
        check("health is full over design", r["health"] == 90.0)
        check("energy is Wh", (r["unit"], r["full"], r["design"]) == ("Wh", 38.7, 43.0))
        check("temp is tenths of a degree", r["temp_c"] == 41.2)
        check("cycles and charging read", (r["cycles"], r["charging"]) == (88, True))
        r = tb.parse({"charge_full": "5000000", "charge_full_design": "6000000"})
        check("charge_* works when energy_* is absent", (r["unit"], r["health"]) == ("Ah", 83.3))
        r = tb.parse({"capacity": "55", "status": "Discharging"})
        check("no capacity files: health is None, not 0", r["health"] is None and r["unit"] is None)
        check("no temp file: None, not 0", r["temp_c"] is None)
        check("no cycle_count: None", r["cycles"] is None)
        check("discharging, no charger: not charging", r["charging"] is False)
        r = tb.parse({})
        check("an empty directory parses to Nones", r["capacity"] is None and r["charging"] is None)
        check("zero design capacity does not divide",
              tb.parse({"energy_full": "1", "energy_full_design": "0"})["health"] is None)
        check("garbage text is None", tb.parse({"energy_full": "n/a", "energy_full_design": "x"})["health"] is None)
        check("an AC adapter alone means charging",
              tb.parse({"capacity": "50"}, ac_online=True)["charging"] is True)

        print("\n  reading a fake /sys/class/power_supply")
        root = os.path.join(scratch, "ps")
        for name, kind, files in (
                ("ACAD", "Mains", {"online": "1"}),
                ("BAT1", "Battery", {"energy_full": "30000000", "energy_full_design": "40000000",
                                     "capacity": "50", "status": "Not charging"}),
                ("hid-1234-battery", "Battery", {"capacity": "80"})):
            os.makedirs(os.path.join(root, name))
            for f, v in dict(files, type=kind).items():
                with open(os.path.join(root, name, f), "w") as fh:
                    fh.write(v + "\n")
        r = tb.read_sysfs(root)
        check("picks BAT1, ignores a peripheral's battery", r and r["name"] == "BAT1")
        check("health 75, charging on the charger", r["health"] == 75.0 and r["charging"])
        check("a machine with no battery is None", tb.read_sysfs(os.path.join(scratch, "nope")) is None)

        print("\n  the heat rule")
        check("46 C while charging speaks", tb.heat_note(46.0, True) == tb.HEAT_WORDS)
        check("exactly 45 does not", tb.heat_note(45.0, True) is None)
        check("hot but on battery: silent", tb.heat_note(50.0, False) is None)
        check("unknown temperature is silent, not 'fine'", tb.heat_note(None, True) is None)
        check("charging unknown is silent", tb.heat_note(50.0, None) is None)

        print("\n  folding samples into days")
        ds = []
        hot = dict(r, temp_c=47.0, capacity=60, charging=True)
        for _ in range(3):
            ds = tb.fold(ds, hot, "2026-03-01")
        ds = tb.fold(ds, dict(hot, capacity=40, charging=False, temp_c=33.0), "2026-03-01")
        ds = tb.fold(ds, hot, "2026-03-02")
        check("two days", len(ds) == 2)
        d = ds[0]
        check("peak charge counts only while charging", d["peak_cap"] == 60)
        check("charge samples and hot samples count", (d["charge_samples"], d["hot_samples"]) == (3, 3))
        check("max temp is the max", d["max_temp"] == 47.0)
        check("health kept", d["health"] == 75.0)
        check("fold does not mutate its input", len(tb.fold(ds, hot, "2026-03-03")) == 3 and len(ds) == 2)
        check("history is capped", len(tb.fold([day(i) for i in range(900)], hot, "2030-01-01")) == tb.KEEP_DAYS)

        print("\n  health trend and projection")
        flat = [day(i, health=96.0) for i in range(40)]
        check("flat health says no measurable decline", tb.project(flat)["state"] == "flat")
        slide = [day(i, health=100.0 - i * 0.1) for i in range(100)]
        p = tb.project(slide)
        # 100 days: 90.1 now (fit), 0.1/day = 3.04/month, (90.1-80)/3.04 = 3.3 months
        check("a steady slide declines", p["state"] == "declining")
        check("per month is about 3 points", abs(p["per_month"] + 3.04) < 0.05)
        check("months to 80 matches the arithmetic", abs(p["months_to_80"] - 3.3) < 0.2)
        check("not rough with 99 days", p["rough"] is False)
        check("under 21 days: learning, no number", tb.project([day(i, health=99 - i) for i in range(10)])["state"] == "learning")
        check("under 5 points: learning", tb.project([day(0, health=99.0), day(40, health=98.0)])["state"] == "learning")
        check("days with no health figure are skipped",
              tb.project([day(i) for i in range(60)])["state"] == "learning")
        short = [day(i, health=100.0 - i * 0.2) for i in range(30)]
        check("a short line is flagged rough", tb.project(short)["rough"] is True)
        check("already worn says so", tb.project([day(i, health=78.0) for i in range(40)])["state"] == "below")
        check("a rise is 'flat', never a negative decline",
              tb.project([day(i, health=90.0 + i * 0.05) for i in range(40)])["state"] == "flat")
        check("a glacial slope is flat-ish, over ten years",
              "ten years" in tb.project([day(i, health=100.0 - i * 0.003) for i in range(100)])["text"]
              or tb.project([day(i, health=100.0 - i * 0.003) for i in range(100)])["state"] == "flat")

        print("\n  is the UEFI limit likely on?")
        today = day(30)["day"]
        capped = [day(i, peak_cap=50, charge_samples=200) for i in range(14, 30)]
        check("peaks of 50 over many days: on", tb.limit_status(capped, today)["state"] == "on")
        check("55 still counts as capped", tb.limit_status([day(i, peak_cap=55, charge_samples=200) for i in range(20, 25)], today)["state"] == "on")
        full_days = [day(i, peak_cap=100, charge_samples=200) for i in range(20, 25)]
        check("peaks of 100: off", tb.limit_status(full_days, today)["state"] == "off")
        check("one day above 70 breaks it", tb.limit_status(capped + [day(29, peak_cap=88, charge_samples=200)], today)["state"] == "off")
        check("62 is unclear", tb.limit_status([day(i, peak_cap=62, charge_samples=200) for i in range(20, 25)], today)["state"] == "unclear")
        check("two days is not enough", tb.limit_status(capped[:2], today)["state"] == "unknown")
        check("short charges are not evidence",
              tb.limit_status([day(i, peak_cap=50, charge_samples=20) for i in range(20, 30)], today)["state"] == "unknown")
        check("nothing at all is unknown", tb.limit_status([], today)["state"] == "unknown")
        check("old capped days outside 14 do not count",
              tb.limit_status([day(i, peak_cap=50, charge_samples=200) for i in range(0, 8)], today)["state"] == "unknown")
        check("days with no peak (never charged) are skipped",
              tb.limit_status([day(i, peak_cap=None, charge_samples=200) for i in range(20, 30)], today)["state"] == "unknown")

        print("\n  the file the app reads")
        check("nothing written yet: not present", tb.read_public() == {"present": False})
        tb.write_state(dict(hot), "sim", now=1000.0)
        v = tb.view(tb._load(tb.STATE, None), ds, now=1010.0, today="2026-03-02")
        check("present, its source named", v["present"] and v["source"] == "sim")
        check("hot and charging: warning set", v["warning"] == tb.HEAT_WORDS)
        v = tb.view(tb._load(tb.STATE, None), ds, now=1000.0 + tb.STALE + 5, today="2026-03-02")
        check("a stale sample is flagged and warns nothing", v["stale"] and v["warning"] is None)
        check("view of no state is absent", tb.view(None, [])["present"] is False)
        check("the sampler writes real history from a fake sysfs",
              tb.sample_once(root, now=1.7e9)["health"] == 75.0
              and tb.read_public()["source"] == "sysfs")
        check("simulated history lives in its own file", tb.HISTORY["sim"] != tb.HISTORY["sysfs"])
        sh = tb._sim_history("limited", "2026-10-17")
        check("the limited sim ends capped", sh[-1]["peak_cap"] == 50)
        check("the limited sim is read as 'on'", tb.limit_status(sh, "2026-10-17")["state"] == "on")
        check("the normal sim is read as 'off'", tb.limit_status(tb._sim_history("normal", "2026-10-17"), "2026-10-17")["state"] == "off")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print(f"\n  {'all ok' if not fails else str(fails) + ' FAILED'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
