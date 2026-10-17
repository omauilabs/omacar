#!/usr/bin/env bash
# What does each part of OmaCar cost the tablet? A measurement, not a guess.
#
# Owner, 2026-10-17: "is there a reason we are running as a web app vs native
# app?" The answer turns on a number nobody has measured: Chromium's share of
# the Surface Pro 7+'s CPU and memory during a drive, next to drowsy detection
# (MediaPipe, in the page), the camera recorder and the OBD daemon.
#
#   tools/measure-footprint.sh [seconds] [label]     default 300 s
#
# Run ON THE TABLET, in its graphical session, while the app is in the state
# you want measured. It samples every process once a second, groups them into
# chromium (kiosk), python (OmaCar services, each by module), ffmpeg/gst
# (cameras) and the rest, and writes a table plus a CSV of the raw samples to
# ~/.local/share/omacar/footprint/<label>-<time>/. It changes nothing.
#
# The suggested runs (each 5 min): parked idle on Home; `omacar sim start` on
# Gauges; the same with drowsy mode on; the same with all cameras recording.
set -euo pipefail
secs=${1:-300}
label=${2:-run}
out="$HOME/.local/share/omacar/footprint/${label}-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$out"
ncpu=$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN)
echo "  sampling ${secs}s into $out (cpu% is of ONE core; $ncpu cores)"
# Sampled in Python, so a command line with quotes or commas in it is quoted
# properly in the CSV rather than breaking it.
python3 - "$out" "$secs" <<'PY'
import csv, os, re, subprocess, sys, time
out, secs = sys.argv[1], int(sys.argv[2])
def group(args):
    if "chromium" in args or "chrome" in args:
        return "chromium"
    m = re.search(r"lib/([a-z_]+)\.py", args)
    if m and "python" in args:
        return "py:" + m.group(1)
    if "ffmpeg" in args or "gst-launch" in args:
        return "video"
    if "Hyprland" in args or "hyprland" in args:
        return "compositor"
    if "pipewire" in args or "wireplumber" in args:
        return "audio"
    return None
with open(os.path.join(out, "samples.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["t", "group", "pid", "cpu", "rss_kb", "cmd"])
    for _ in range(secs):
        t = int(time.time())
        ps = subprocess.run(["ps", "-eo", "pid=,pcpu=,rss=,args="], capture_output=True, text=True).stdout
        for line in ps.splitlines():
            parts = line.split(None, 3)
            if len(parts) < 4:
                continue
            g = group(parts[3])
            if g:
                w.writerow([t, g, parts[0], parts[1], parts[2], parts[3][:80]])
        time.sleep(1)
PY
python3 - "$out" "$ncpu" <<'PY'
import csv, sys, statistics as st, collections, os
out, ncpu = sys.argv[1], int(sys.argv[2])
by = collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0]))
for r in csv.DictReader(open(os.path.join(out, "samples.csv"))):
    b = by[r["group"]][r["t"]]
    b[0] += float(r["cpu"]); b[1] += int(r["rss_kb"])
rows = []
for g, ts in by.items():
    cpu = [v[0] for v in ts.values()]; rss = [v[1] for v in ts.values()]
    rows.append((g, st.mean(cpu), max(cpu), st.mean(rss) / 1024, max(rss) / 1024))
rows.sort(key=lambda r: -r[1])
lines = [f"{'group':22} {'cpu mean':>9} {'cpu max':>8} {'MB mean':>8} {'MB max':>7}"]
lines += [f"{g:22} {a:8.1f}% {b:7.1f}% {c:8.0f} {d:7.0f}" for g, a, b, c, d in rows]
lines.append(f"(cpu% is per core; the machine has {ncpu} cores = {ncpu * 100}%)")
open(os.path.join(out, "summary.txt"), "w").write("\n".join(lines) + "\n")
print("\n".join("  " + l for l in lines))
PY
