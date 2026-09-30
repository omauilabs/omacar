#!/usr/bin/env python3
"""The recorder, without a camera: which camera is which, what mode it records
in, what the loop deletes, what counts as hard braking, and what an event
locks.

The last section runs the real ffmpeg command for a few seconds against a test
picture wherever there is ffmpeg and a VA-API render node (the box and the
tablet both have them), and skips loudly anywhere else. Scratch folders only:
nothing here touches ~/Videos or the real runtime directory.
"""

import builtins
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

SCRATCH = tempfile.mkdtemp(prefix="omacar-cams-test-")
os.environ["OMACAR_VIDEOS"] = os.path.join(SCRATCH, "videos")
os.environ["XDG_RUNTIME_DIR"] = os.path.join(SCRATCH, "run")
os.environ["XDG_CONFIG_HOME"] = os.path.join(SCRATCH, "config")
os.environ["XDG_STATE_HOME"] = os.path.join(SCRATCH, "state")

import cams      # noqa: E402
import camstore  # noqa: E402

fails = 0


def head(t):
    print(f"\n  {t}")


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"  FAIL  {msg}")


def check(msg, got, want):
    if got == want:
        ok(msg)
    else:
        bad(f"{msg} (wanted {want!r}, got {got!r})")


# ------------------------------------------------------------ which is which
head("each camera gets its role from its name")

C920 = "usb-046d_HD_Pro_Webcam_C920_F65398FF-video-index0"
DJI = "usb-DJI_Osmo_Action_4_0123456789AB-video-index0"
ACE = "usb-Insta360_Ace_Pro_ABCDEF012345-video-index0"
NAMES = [C920, "usb-046d_HD_Pro_Webcam_C920_F65398FF-video-index1", DJI, ACE,
         "usb-Microsoft_Surface_Camera_Front-video-index0"]
P = camstore.DEFAULT_PATTERNS
check("front, rear and cabin, by name", cams.match_roles(NAMES, P),
      {"front": DJI, "rear": ACE, "cabin": C920})
check("the metadata node (-video-index1) is never a camera",
      cams.match_roles(["usb-046d_HD_Pro_Webcam_C920_F65398FF-video-index1"], P), {})
check("'Ace' is not found inside 'Surface'",
      cams.match_roles(["usb-Microsoft_Surface_Camera_Front-video-index0"], P), {})
check("'Osmo Action 4' matches udev's underscores",
      cams.match_roles(["usb-SZ_Osmo_Action_4_77-video-index0"], {"front": "Osmo Action 4"}),
      {"front": "usb-SZ_Osmo_Action_4_77-video-index0"})
check("an infrared ELP drops in as the cabin with no code change",
      cams.match_roles(["usb-ELP_USB_Camera_IR_2020-video-index0"], P),
      {"cabin": "usb-ELP_USB_Camera_IR_2020-video-index0"})
os.makedirs(os.path.dirname(camstore.config_path()), exist_ok=True)
with open(camstore.config_path(), "w", encoding="utf-8") as f:
    json.dump({"patterns": {"front": "C920", "nope": "x"}, "budget_gb": 12}, f)
_cfg = camstore.load_config()
check("the config file's pattern wins, and an unknown role is ignored",
      (cams.match_roles(NAMES, _cfg["patterns"])["front"], "nope" in _cfg["patterns"]),
      (C920, False))
check("and so does its budget", camstore.budget_bytes(_cfg), 12 * 10**9)
os.remove(camstore.config_path())
check("with no file, the budget is 40 GB", camstore.budget_bytes(), 40 * 10**9)
with open(camstore.config_path(), "w", encoding="utf-8") as f:
    json.dump({"patterns": {"rear": ""}, "caps": {"rear": [1280, 720, 30], "front": "big"}}, f)
_cfg = camstore.load_config()
check("an empty pattern turns a role off", "rear" in cams.match_roles(NAMES, _cfg["patterns"]), False)
check("a role's resolution cap comes from the file", _cfg["caps"]["rear"], (1280, 720, 30))
check("and a cap that is not three numbers is ignored", _cfg["caps"]["front"], (1920, 1080, 30))
os.remove(camstore.config_path())

# ------------------------------------------------------- a mistyped pattern
head("one mistyped pattern disables only that role, never the recorder")

with open(camstore.config_path(), "w", encoding="utf-8") as f:
    json.dump({"patterns": {"cabin": "*C920*"}}, f)
_cfg = camstore.load_config()
check("match_roles() never raises on a bad pattern; it just cannot match",
      cams.match_roles(NAMES, _cfg["patterns"]), {"front": DJI, "rear": ACE})
check("invalid_patterns() names the role and the pattern",
      cams.invalid_patterns(_cfg["patterns"]), {"cabin": "*C920*"})
check("find_cameras() does not raise either, and simply has no cabin",
      "cabin" in cams.find_cameras(_cfg), False)
check("neither does overview(), with no recorder running at all",
      isinstance(cams.overview(), dict), True)

_r1 = cams.Recorder()
_r1.discover()
check("the recorder's own note names the role and the bad pattern",
      "*C920*" in (_r1.notes.get("cabin") or ""), True)
check("cabin is disabled, not merely unmatched",
      "cabin" in _r1.cams, False)
check("front and rear are still evaluated on their own -- not held back or crashed by cabin's mistake",
      (_r1.notes.get("front"), _r1.notes.get("rear")), ("no camera", "no camera"))
os.remove(camstore.config_path())

# ------------------------------------------------------------ the IPU6 floor
head("nothing below video64 is opened where the IPU6 owns those nodes")

_fake = os.path.join(SCRATCH, "fake")


def fake_machine(ipu):
    """A by-id folder, device nodes and a sysfs: the tablet's shape, or the box's."""
    shutil.rmtree(_fake, ignore_errors=True)
    for d in ("by-id", "dev", "sysfs"):
        os.makedirs(os.path.join(_fake, d))
    low = "Intel IPU6 ISYS Capture 3" if ipu else "USB2.0 Camera"
    for node, name, link in ((3, low, "usb-Sneaky_Cam-video-index0"), (70, "HD Pro Webcam C920", C920)):
        open(os.path.join(_fake, "dev", f"video{node}"), "w").close()
        os.symlink(os.path.join(_fake, "dev", f"video{node}"), os.path.join(_fake, "by-id", link))
        os.makedirs(os.path.join(_fake, "sysfs", f"video{node}"))
        with open(os.path.join(_fake, "sysfs", f"video{node}", "name"), "w") as fh:
            fh.write(name + "\n")
    return os.path.join(_fake, "by-id"), os.path.join(_fake, "sysfs")


_pats = {"patterns": {"front": "Sneaky", "rear": "Nothing", "cabin": "C920"}}
_by, _sys = fake_machine(ipu=True)
check("on the tablet the floor is video64", cams.usb_floor(_sys), 64)
check("and a pattern that matches video3 is refused, whatever the config says",
      sorted(cams.find_cameras(_pats, by_id=_by, sysfs=_sys)), ["cabin"])
_by, _sys = fake_machine(ipu=False)
check("on the box, with no IPU, a low node is a camera like any other",
      sorted(cams.find_cameras(_pats, by_id=_by, sysfs=_sys)), ["cabin", "front"])
check("a machine whose sysfs cannot be read is treated as the tablet",
      cams.usb_floor(os.path.join(SCRATCH, "nowhere")), 64)

# ------------------------------------------------------- refused by identity
head("a camera is refused by identity, never by number alone")


def identity_machine(nodes):
    """A fresh by-id folder and sysfs for one or more nodes, each carrying a
    real device/subsystem chain -- the same shape confirmed against the
    box's actual C920 (device -> .../usb1/1-3/1-3:1.0, whose own subsystem ->
    /sys/bus/usb). `nodes` is [(number, name, link, bus), ...], where `bus`
    is "usb", some other bus name such as "pci", or None for a node with no
    device link at all (its USB-ness cannot be told)."""
    root = os.path.join(SCRATCH, f"identity-{time.time_ns()}")
    by_id, sysfs, dev, devices, buses = (
        os.path.join(root, d) for d in ("by-id", "sysfs", "dev", "devices", "bus"))
    for d in (by_id, sysfs, dev, devices, buses):
        os.makedirs(d)
    for node, name, link, bus in nodes:
        vnode = os.path.join(dev, f"video{node}")
        open(vnode, "w").close()
        os.symlink(vnode, os.path.join(by_id, link))
        vdir = os.path.join(sysfs, f"video{node}")
        os.makedirs(vdir)
        with open(os.path.join(vdir, "name"), "w") as fh:
            fh.write(name + "\n")
        if bus is not None:
            iface = os.path.join(devices, f"iface{node}")
            busdir = os.path.join(buses, bus)
            os.makedirs(iface)
            os.makedirs(busdir, exist_ok=True)
            os.symlink(busdir, os.path.join(iface, "subsystem"))
            os.symlink(iface, os.path.join(vdir, "device"))
    return by_id, sysfs


_pats1 = {"patterns": {"cabin": "C920"}}
# A real IPU6 node beside the candidate, the tablet's own shape: the floor is
# 64 here, so a USB camera accepted at video0 anyway is accepted by identity,
# not because nothing raised the floor in the first place.
_ipu_node = (5, "Intel IPU6 ISYS Capture 5", "usb-Other_Cam-video-index0", None)
_by, _sys = identity_machine([_ipu_node, (0, "HD Pro Webcam C920", C920, "usb")])
check("a real USB camera at video0 is accepted even where the floor is 64 -- "
      "the Wednesday risk: if uvcvideo enumerates before the IPU6, a USB "
      "camera can land below video64",
      cams.find_cameras(_pats1, by_id=_by, sysfs=_sys), {"cabin": os.path.join(_by, C920)})

_by, _sys = identity_machine([(70, "Intel IPU6 ISYS Capture 70", C920, "usb")])
check("an IPU-named node is refused at any number, even one that is also USB",
      cams.find_cameras(_pats1, by_id=_by, sysfs=_sys), {})
check("and refused_cameras() says why, not just \"no camera\"",
      "IPU6" in cams.refused_cameras(_pats1, by_id=_by, sysfs=_sys).get("cabin", ""), True)

_by, _sys = identity_machine([(70, "Some PCI Capture Device", C920, "pci")])
check("a non-USB node is refused however high its number",
      cams.find_cameras(_pats1, by_id=_by, sysfs=_sys), {})
check("...and refused_cameras() names that too",
      "not a USB device" in cams.refused_cameras(_pats1, by_id=_by, sysfs=_sys).get("cabin", ""), True)

# An ambiguous node (no device link at all, so its USB-ness cannot be told)
# alongside the same genuine IPU6 node used above.
_by, _sys = identity_machine([_ipu_node, (3, "Unknown Capture", C920, None)])
check("with no way to tell USB-ness, a low node still falls back to the tablet's video64 floor",
      cams.find_cameras(_pats1, by_id=_by, sysfs=_sys), {})
_by2, _sys2 = identity_machine([_ipu_node, (70, "Unknown Capture", C920, None)])
check("...but the same unreadable identity at 64 or above is accepted",
      cams.find_cameras(_pats1, by_id=_by2, sysfs=_sys2), {"cabin": os.path.join(_by2, C920)})
_by3, _ = identity_machine([(3, "Unknown Capture", C920, None)])
check("and an entirely unreadable sysfs falls back the same way, with no other evidence at all",
      cams.find_cameras(_pats1, by_id=_by3, sysfs=os.path.join(SCRATCH, "no-such-sysfs")), {})

_by, _sys = fake_machine(ipu=False)      # video3 named "USB2.0 Camera", no device link at all
_unreadable = os.path.join(_sys, "video3", "name")
os.chmod(_unreadable, 0)
try:
    check("the floor never silently drops to zero because one node's name could not be read",
          cams.usb_floor(_sys), 64)
finally:
    os.chmod(_unreadable, 0o644)

# ------------------------------------------------------------ the mode
head("the best mode at or under 1080p30, MJPEG first")

# Trimmed from the box's C920, `v4l2-ctl --list-formats-ext`, 2026-09-28.
C920_FORMATS = "\n".join([
    "ioctl: VIDIOC_ENUM_FMT",
    "\tType: Video Capture",
    "",
    "\t[0]: 'YUYV' (YUYV 4:2:2)",
    "\t\tSize: Discrete 640x480",
    "\t\t\tInterval: Discrete 0.033s (30.000 fps)",
    "\t\t\tInterval: Discrete 0.067s (15.000 fps)",
    "\t\tSize: Discrete 1920x1080",
    "\t\t\tInterval: Discrete 0.200s (5.000 fps)",
    "\t\tSize: Discrete 2304x1536",
    "\t\t\tInterval: Discrete 0.500s (2.000 fps)",
    "\t[1]: 'H264' (H.264, compressed)",
    "\t\tSize: Discrete 640x480",
    "\t\t\tInterval: Discrete 0.033s (30.000 fps)",
    "\t\tSize: Discrete 1920x1080",
    "\t\t\tInterval: Discrete 0.033s (30.000 fps)",
    "\t[2]: 'MJPG' (Motion-JPEG, compressed)",
    "\t\tSize: Discrete 640x480",
    "\t\t\tInterval: Discrete 0.033s (30.000 fps)",
    "\t\tSize: Discrete 1280x720",
    "\t\t\tInterval: Discrete 0.033s (30.000 fps)",
    "\t\tSize: Discrete 1920x1080",
    "\t\t\tInterval: Discrete 0.033s (30.000 fps)",
    "\t\t\tInterval: Discrete 0.042s (24.000 fps)",
])
_modes = cams.parse_formats(C920_FORMATS)
check("every size of every format is read", len(_modes), 8)
check("with its frame rates", _modes[0], {"fmt": "YUYV", "w": 640, "h": 480, "fps": [30.0, 15.0]})
check("front and rear: MJPEG 1080p30", cams.choose_mode(_modes, cams.CAPS["front"]),
      {"fmt": "MJPG", "w": 1920, "h": 1080, "fps": 30.0})
check("the cabin at low resolution: MJPEG 640x480 at 30",
      cams.choose_mode(_modes, cams.CAPS["cabin"]),
      {"fmt": "MJPG", "w": 640, "h": 480, "fps": 30.0})
check("with no MJPEG, H.264 before YUYV",
      cams.choose_mode([m for m in _modes if m["fmt"] != "MJPG"], cams.CAPS["front"]),
      {"fmt": "H264", "w": 1920, "h": 1080, "fps": 30.0})
check("a 5 fps 1080p loses to 640x480 at 30: a slideshow is not a dashcam",
      cams.choose_mode([m for m in _modes if m["fmt"] == "YUYV"], cams.CAPS["front"]),
      {"fmt": "YUYV", "w": 640, "h": 480, "fps": 30.0})
check("nothing at or under the cap is no mode, not a guess",
      cams.choose_mode([{"fmt": "MJPG", "w": 3840, "h": 2160, "fps": [30.0]}], cams.CAPS["front"]),
      None)
check("a rate over the cap is not used",
      cams.choose_mode([{"fmt": "MJPG", "w": 1280, "h": 720, "fps": [60.0, 30.0]}],
                       cams.CAPS["front"])["fps"], 30.0)

# ------------------------------------------------------------ the command
head("one ffmpeg per camera, as measured on the tablet")

_a = cams.ffmpeg_args("front", {"fmt": "MJPG", "w": 1920, "h": 1080, "fps": 30.0},
                      "/v/front", device="/dev/v4l/by-id/x")
_s = " ".join(_a)
check("the camera is opened in its chosen mode",
      "-f v4l2 -input_format mjpeg -video_size 1920x1080 -framerate 30 -i /dev/v4l/by-id/x" in _s, True)
check("compressed on the GPU",
      ("-vaapi_device /dev/dri/renderD128" in _s and "format=nv12,hwupload" in _s
       and "-c:v h264_vaapi" in _s), True)
check("at 6 Mbit/s front", "-b:v 6M" in _s, True)
check("never padded with duplicates in the dark", "-fps_mode passthrough" in _s, True)
check("a keyframe at every clip boundary", "-force_key_frames expr:gte(t,n_forced*60)" in _s, True)
check("one-minute fragmented MP4, named by when it started",
      ("-segment_time 60" in _s and "frag_keyframe" in _s
       and os.path.join("/v/front", "%Y%m%d-%H%M%S.mp4") in _a), True)
check("and a 640-px 10 fps live picture on stdout",
      ("fps=10,scale=640:-2" in _s and "-c:v mjpeg" in _s and _a[-1] == "pipe:1"), True)
_c = " ".join(cams.ffmpeg_args("cabin", cams.SIM_MODE["cabin"], "/v/cabin"))
check("the cabin at 1 Mbit/s", "-b:v 1M" in _c, True)
check("with no device, a real-time test picture",
      "-re -f lavfi -i testsrc2=size=640x480:rate=30" in _c, True)

# ------------------------------------------------------------ live frames
head("the live picture is cut into whole frames")

A = b"\xff\xd8AAAA\xff\xd9"
B = b"\xff\xd8BB\xff\x00B\xff\xd9"
_f, _rest = cams.split_jpegs(b"junk" + A + B + b"\xff\xd8half")
check("two whole frames, in order", _f, [A, B])
check("and the half frame is kept for the next read", _rest, b"\xff\xd8half")
check("nothing whole is nothing", cams.split_jpegs(b"\xff\xd8abc"), ([], b"\xff\xd8abc"))
_m = cams.FpsMeter()
for _i in range(11):
    _m.add(100 + _i, _i * 10)
check("real fps over the last five seconds", _m.fps(), 10.0)
_m.add(111, 105)
check("and it follows a camera that slows in the dark", _m.fps() < 10.0, True)
check("a meter that has heard nothing for five seconds has no rate", _m.fps(now=117), None)


class FakeProc:
    """ffmpeg, alive until it is killed."""

    def __init__(self):
        self.code, self.signals = None, []

    def poll(self):
        return self.code

    def send_signal(self, sig):
        self.signals.append(sig)

    def kill(self):
        self.signals.append("KILL")
        self.code = -9

    def wait(self, timeout=None):
        return self.code


head("a camera that stops sending pictures is stalled, not REC, and restarted")
_now = time.time()
_cam = cams.Camera("front", "/dev/v4l/by-id/x", {"fmt": "MJPG", "w": 1920, "h": 1080, "fps": 30.0})
_cam.proc, _cam.started, _cam.last_frame = FakeProc(), _now - 60, _now - 11
_st = _cam.status()
check("ffmpeg alive with no picture for 11 s is stalled, and says so",
      (_st["recording"], _st["stalled"], _st["live"], _st["error"].startswith("stalled")),
      (False, True, False, True))
_cam.last_frame = _now - 1
check("a fresh picture is recording", (_cam.status()["recording"], _cam.status()["stalled"]), (True, False))
_cam.last_frame, _cam.started = None, _now - 10
check("a camera still starting has fifteen seconds' grace", _cam.stalled(), False)
check("and during that grace it is not REC either -- it has sent no picture yet",
      _cam.status()["recording"], False)
check("status says starting, not silence", _cam.status()["starting"], True)
_cam.started = _now - 16
check("and then it is stalled too", _cam.stalled(), True)
_rec = cams.Recorder()
_rec.cams["front"] = _cam
_rec.check_stalls()
check("the watchdog kills it, and it restarts on the next pass",
      ("KILL" in _cam.proc.signals, _rec.retry_at["front"], _rec.notes["front"].startswith("stalled")),
      (True, 0, True))


class StubProc:
    """Alive until killed, needing no real ffmpeg -- for exercising
    discover()'s restart path without spawning one."""

    def __init__(self):
        self.code = None

    def poll(self):
        return self.code

    def send_signal(self, sig):
        pass

    def kill(self):
        self.code = -9

    def wait(self, timeout=None):
        return self.code


def _stub_start(self):
    self.proc = StubProc()
    self.started = time.time()


_real_find, _real_probe, _real_start = cams.find_cameras, cams.probe_modes, cams.Camera.start
cams.find_cameras = lambda cfg=None, by_id=cams.BY_ID, sysfs=cams.SYSFS: {"front": "/dev/v4l/by-id/x"}
cams.probe_modes = lambda device: [{"fmt": "MJPG", "w": 1920, "h": 1080, "fps": [30.0]}]
cams.Camera.start = _stub_start
try:
    _r2 = cams.Recorder()
    _r2.discover()
    _first = _r2.cams["front"]
    check("a first start carries no restart note", _first.restarted, None)
    check("and it is not REC before its own first frame", _first.status()["recording"], False)
    _first.proc, _first.started, _first.last_frame = StubProc(), time.time() - 60, time.time() - 11
    _r2.check_stalls()
    _r2.discover()
    _second = _r2.cams["front"]
    check("check_stalls() + discover() together leave a fresh camera in place",
          _second is not _first, True)
    check("carrying why it restarted",
          bool(_second.restarted) and _second.restarted.startswith("stalled"), True)
    check("and the replacement is not REC either, until it sends a frame of its own",
          _second.status()["recording"], False)
finally:
    cams.find_cameras, cams.probe_modes, cams.Camera.start = _real_find, _real_probe, _real_start


class Sink:
    def __init__(self):
        self.data = b""

    def write(self, b):
        self.data += b

    def flush(self):
        pass


os.makedirs(cams.run_dir(), exist_ok=True)
with open(cams.live_path("rear"), "wb") as f:
    f.write(A)
_clock = [0.0]


def _tick(s):
    _clock[0] += s


_sink = Sink()
_n = cams.stream_live(_sink, "rear", clock=lambda: _clock[0], sleep=_tick)
check("a frame goes out as a multipart part with its length",
      (_n, _sink.data.startswith(
          b"--omacarframe\r\nContent-Type: image/jpeg\r\nContent-Length: 8\r\n\r\n" + A)),
      (1, True))
check("and silence ends the stream after ten seconds", _clock[0] >= cams.LIVE_GIVE_UP, True)
check("frames=N ends it after N frames",
      cams.stream_live(Sink(), "rear", frames=1, clock=lambda: _clock[0], sleep=_tick), 1)

# ------------------------------------------------------------ the loop
head("the loop deletes the oldest unlocked clips, and nothing else")

V = os.environ["OMACAR_VIDEOS"]
T0 = time.mktime((2026, 9, 30, 9, 0, 0, 0, 0, -1))


def put(role, t, size, event=None):
    d = os.path.join(V, "locked", event, role) if event else os.path.join(V, role)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, camstore.clip_name(t))
    with open(p, "wb") as fh:
        fh.write(b"\0" * size)
    return p


for _i in range(5):
    put("front", T0 + 60 * _i, 100)
put("rear", T0, 100)
put("cabin", T0 + 60, 100, event="20260930-090130-marked")
_clips = camstore.scan()
check("seven clips, loop and locked", len(_clips), 7)
check("a clip ends where the next starts, or a minute after its own start",
      [c["end"] - c["start"] for c in _clips if c["role"] == "front"], [60, 60, 60, 60, 60])
_d = camstore.plan_janitor(_clips, 450)
check("oldest first, only as many as it takes", [c["start"] - T0 for c in _d], [0, 60, 120])
check("never a locked clip, never the clip being written",
      [c for c in camstore.plan_janitor(_clips, 0) if c["locked"] or c["start"] - T0 == 240], [])
check("under budget, nothing goes", camstore.plan_janitor(_clips, 10**9), [])
check("janitor() deletes what the plan says", camstore.janitor(450),
      [f"front/{camstore.clip_name(T0 + s)}" for s in (0, 60, 120)])
check("and the storage count follows", camstore.usage()["used"], 400)

# ------------------------------------------------------------ hard braking
head("hard braking comes from the car's own speed")


def brakes(series):
    w = camstore.BrakeWatch()
    return [t for t, v in series if w.feed(t, v)]


STEADY = [(i / 5, 100.0) for i in range(10)]
check("16 km/h lost in 0.8 s is hard braking",
      brakes(STEADY + [(2.0, 96.0), (2.2, 90.0), (2.4, 86.0), (2.6, 84.0)]), [2.6])
check("the same 16 km/h over three seconds is not",
      brakes(STEADY + [(2.0 + i / 5, 100.0 - 16 * (i + 1) / 15) for i in range(15)]), [])
check("15.9 km/h in half a second is not", brakes(STEADY + [(2.0, 92.0), (2.4, 84.1)]), [])
check("one event per stop, not one per sample",
      len(brakes(STEADY + [(2.0, 80.0), (2.2, 60.0), (2.4, 40.0)])), 1)
check("a lost link is not a stop", brakes(STEADY + [(2.0, None), (2.2, 50.0)]), [])

# ------------------------------------------------------------ events
head("an event locks thirty seconds either side, on every camera")

shutil.rmtree(V)
for _r in camstore.ROLES:
    for _i in range(4):
        put(_r, T0 + 60 * _i, 10)
_t = T0 + 90
_ev = camstore.mark("marked", t=_t, speed_kph=88.0, now=_t)
check("written with its window, kind and speed",
      (_ev["kind"], _ev["t0"] - T0, _ev["t1"] - T0, _ev["speed_kph"], _ev["state"]),
      ("marked", 60, 120, 88.0, "pending"))
check("the clips it touches move at once, on every camera", sorted(_ev["files"]),
      sorted(f"{r}/{camstore.clip_name(T0 + 60)}" for r in camstore.ROLES))
check("into the event's folder",
      os.path.isfile(os.path.join(V, "locked", _ev["id"], "cabin", camstore.clip_name(T0 + 60))),
      True)
check("it stays pending while its window is open",
      camstore.load_events()["events"][0]["state"], "pending")
camstore.settle(now=T0 + 130)
check("and is locked once the window has closed",
      camstore.load_events()["events"][0]["state"], "locked")
check("the loop never deletes a locked clip",
      [c for c in camstore.plan_janitor(camstore.scan(), 0) if c["locked"]], [])
check("a double tap is one event", camstore.mark("marked", t=_t, now=_t)["id"], _ev["id"])
check("and one line in the file", len(camstore.load_events()["events"]), 1)
_ev2 = camstore.mark("hard-braking", t=T0 + 200, speed_kph=97.0, now=T0 + 200)
check("a later event takes the clips it overlaps", len(_ev2["files"]), 6)
put("front", T0 + 220, 10)
camstore.settle(now=T0 + 225)
_e2 = [e for e in camstore.load_events()["events"] if e["kind"] == "hard-braking"][0]
check("a clip that starts inside the window joins it as it appears",
      (f"front/{camstore.clip_name(T0 + 220)}" in _e2["files"], _e2["state"]), (True, "pending"))
camstore.settle(now=T0 + 240)
check("and the event locks when the window closes",
      [e for e in camstore.load_events()["events"] if e["kind"] == "hard-braking"][0]["state"],
      "locked")

# ------------------------------------------------------------ simulated speed
head("the simulator's speed is never hard braking")

_real_live = cams._live
_feed = []
cams._live = lambda: _feed[-1] if _feed else None


def _drive(samples):
    r = cams.Recorder()
    for s in samples:
        _feed.append(s)
        r.watch_braking()


def _stop(sim):
    return [{"connected": True, "simulated": sim, "t": 1000 + i / 5,
             "values": {"SPEED": 100.0 if i < 10 else 70.0}} for i in range(12)]


_before = len(camstore.load_events()["events"])
_drive(_stop(True))
check("a hard stop in simulated numbers locks nothing", len(camstore.load_events()["events"]), _before)
_drive(_stop(False))
check("the same stop from the car does", len(camstore.load_events()["events"]), _before + 1)
cams._live = _real_live

# ------------------------------------------------------------ the API's view
head("the recorder's own report, as the API reads it")


def _status(pid):
    with open(cams.status_path(), "w", encoding="utf-8") as f:
        json.dump({"pid": pid, "t": time.time(), "sim": True, "roles": {
            "front": {"device": None, "mode": cams.SIM_MODE["front"], "sim": True, "recording": True,
                      "live": True, "stalled": False, "fps": 29.9, "error": None}}}, f)


check("with no recorder running, it says off", cams.overview()["running"], False)
_status(os.getpid())
check("a fresh status file whose pid is not a recorder is not a running recorder",
      cams.overview()["running"], False)
_fake_rec = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "lib/cams.py", "run"])
try:
    _status(_fake_rec.pid)
    _ov = cams.overview()
    check("with one, it says so, and which role is simulated",
          (_ov["running"], _ov["sim"], _ov["roles"]["front"]["sim"], _ov["roles"]["front"]["fps"]),
          (True, True, True, 29.9))
    check("every role is reported, with its clips counted",
          (sorted(_ov["roles"]), _ov["roles"]["rear"]["clips"]), (["cabin", "front", "rear"], 4))
    check("storage used, of the budget", _ov["storage"]["budget"], 40 * 10**9)
finally:
    _fake_rec.kill()
    _fake_rec.wait()
os.remove(cams.status_path())

# ------------------------------------------------------------ the real command
head("the real command, for seven seconds, against a test picture")

if not (shutil.which("ffmpeg") and shutil.which("ffprobe") and os.path.exists(cams.VAAPI)):
    print("    (skipping: no ffmpeg, ffprobe or VA-API render node here. The box\n"
          "     and the tablet have all three, and that is where it counts.)")
else:
    _dir = os.path.join(SCRATCH, "real")
    os.makedirs(_dir)
    _p = subprocess.run(cams.ffmpeg_args("cabin", {"fmt": "SIM", "w": 640, "h": 480, "fps": 30.0},
                                         _dir, clip_secs=2, duration=7),
                        capture_output=True, timeout=90)
    _frames, _ = cams.split_jpegs(_p.stdout)
    check("ffmpeg ran to the end", _p.returncode, 0)
    check(f"the live picture came out at about 10 fps ({len(_frames)} frames in 7 s)",
          55 <= len(_frames) <= 80, True)
    _out = sorted(os.listdir(_dir))
    check(f"2 s segments came out ({len(_out)} of them)", len(_out) >= 3, True)

    def _probe(name, *q):
        return subprocess.run(["ffprobe", "-v", "error", *q, os.path.join(_dir, name)],
                              capture_output=True, text=True).stdout.strip()

    _durs = [float(_probe(c, "-show_entries", "format=duration", "-of", "csv=p=0"))
             for c in _out[:-1]]
    check(f"every whole segment is 2 s long, not 3 and 1 ({_durs})",
          all(abs(d - 2.0) <= 0.25 for d in _durs), True)
    check("and every one starts on a keyframe",
          all(_probe(c, "-select_streams", "v", "-read_intervals", "%+#1",
                     "-show_entries", "frame=key_frame", "-of", "csv=p=0").startswith("1")
              for c in _out), True)
    check("in H.264", _probe(_out[0], "-select_streams", "v", "-show_entries",
                             "stream=codec_name", "-of", "csv=p=0"), "h264")

# ------------------------------------------------------------ the demo's cameras
head("cams.py demo: footage on a loop, standing in for the live cameras")

# The meetup demo has no cameras and no car. `cams.py demo` plays footage from
# files as if the three cameras were live: one-minute clips for the last 50
# minutes, a live picture per role, and a status file that makes overview()
# report the recorder running. It writes only where OMACAR_VIDEOS and
# XDG_RUNTIME_DIR point, and only when both are inside a folder called
# omacar-demo. Scratch folders throughout: nothing here touches ~/Videos, the
# real runtime directory or a device.

DEMO_ROOT = os.path.join(SCRATCH, "omacar-demo")
DEMO_VIDEOS = os.path.join(DEMO_ROOT, "videos")
DEMO_RUN = os.path.join(DEMO_ROOT, "run")
DEMO_STATE = os.path.join(DEMO_ROOT, "state", "omacar")
DEMO_ENV = {"OMACAR_VIDEOS": DEMO_VIDEOS, "XDG_RUNTIME_DIR": DEMO_RUN, "OMACAR_STATE": DEMO_STATE,
            "XDG_STATE_HOME": os.path.join(DEMO_ROOT, "state"),
            "XDG_CONFIG_HOME": os.path.join(DEMO_ROOT, "config")}
CAMS_PY = os.path.join(ROOT, "lib", "cams.py")


def _env(**over):
    """This process's environment, made the demo's; a None removes a variable."""
    e = dict(os.environ)
    e.update(DEMO_ENV)
    for k, v in over.items():
        if v is None:
            e.pop(k, None)
        else:
            e[k] = v
    return e


def _cli(env, *args, timeout=60):
    return subprocess.run([sys.executable, CAMS_PY, "demo", *args], capture_output=True,
                          text=True, env=env, timeout=timeout)


_r = _cli(_env(OMACAR_VIDEOS=None))
check("with OMACAR_VIDEOS unset, it refuses and says which variable",
      (_r.returncode != 0, "OMACAR_VIDEOS" in _r.stderr), (True, True))
_r = _cli(_env(OMACAR_VIDEOS=os.path.join(SCRATCH, "videos")))
check("with OMACAR_VIDEOS outside omacar-demo, it refuses",
      (_r.returncode != 0, "omacar-demo" in _r.stderr), (True, True))
_r = _cli(_env(OMACAR_VIDEOS=os.path.join(DEMO_ROOT, "..", "videos")))
check("and the name in a path that climbs out of it does not count",
      (_r.returncode != 0, "omacar-demo" in _r.stderr), (True, True))
_r = _cli(_env(XDG_RUNTIME_DIR=os.path.join(SCRATCH, "run")))
check("nor does a runtime folder outside it: the live recorder's live pictures are not the demo's",
      (_r.returncode != 0, "XDG_RUNTIME_DIR" in _r.stderr), (True, True))
check("a refusal writes nothing", (os.path.exists(DEMO_VIDEOS), os.path.exists(DEMO_RUN)), (False, False))

_saved_env = {k: os.environ.get(k) for k in DEMO_ENV}
os.environ.update(DEMO_ENV)
check("inside the demo's folders there is nothing to refuse", cams.demo_refusal(), None)

with open(CAMS_PY, encoding="utf-8") as f:
    _src = f.read()
_demo_src = _src[_src.index("# ---- the demo's cameras"):_src.index("def main(argv):")]
check("the demo's code never names a camera device or the probing paths",
      [w for w in ("v4l2", "find_cameras", "probe_modes", "refused_cameras", "BY_ID", "/dev/video",
                   "by-id", "usb_floor") if w in _demo_src], [])

# ---- an _ours() that knows the demo
_stand_in = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "lib/cams.py", "demo"])
_other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "lib/cams.py", "status"])
try:
    for _pid, _want in ((_stand_in.pid, True), (_other.pid, False)):
        os.makedirs(cams.run_dir(), exist_ok=True)
        with open(cams.status_path(), "w", encoding="utf-8") as f:
            json.dump({"pid": _pid, "t": time.time(), "sim": False, "roles": {}}, f)
        check("a fresh status file from a `cams.py demo` process is a running recorder"
              if _want else "and from any other cams.py process it is not",
              cams.overview()["running"], _want)
finally:
    for _p in (_stand_in, _other):
        _p.kill()
        _p.wait()
    os.remove(cams.status_path())


class FakePopen:
    """ffmpeg that runs nothing: alive until terminated or killed."""
    made = []
    pids = iter(range(50000, 60000))

    def __init__(self, args, **kw):
        self.args, self.kw, self.code = list(args), kw, None
        self.pid = next(FakePopen.pids)
        self.stderr = io.BytesIO(b"")
        FakePopen.made.append(self)

    def poll(self):
        return self.code

    def terminate(self):
        self.code = -15

    def send_signal(self, sig):
        self.code = -int(sig)

    def kill(self):
        self.code = -9

    def wait(self, timeout=None):
        return self.code


def _dev_guard():
    """Every path under /dev that this process tried to open, /dev/null
    (which subprocess opens for DEVNULL) excepted."""
    seen = []
    real_os_open, real_open = os.open, builtins.open

    def _bad(p):
        p = os.fspath(p) if isinstance(p, (str, bytes, os.PathLike)) else ""
        p = p.decode() if isinstance(p, bytes) else p
        return p.startswith("/dev/") and p != "/dev/null"

    def os_open(p, *a, **k):
        if _bad(p):
            seen.append(p)
            raise PermissionError(f"the demo must not open {p}")
        return real_os_open(p, *a, **k)

    def bopen(p, *a, **k):
        if _bad(p):
            seen.append(p)
            raise PermissionError(f"the demo must not open {p}")
        return real_open(p, *a, **k)

    os.open, builtins.open = os_open, bopen
    return seen, lambda: (setattr(os, "open", real_os_open), setattr(builtins, "open", real_open))


if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
    print("    (skipping the rest: no ffmpeg or ffprobe here. The box and the tablet\n"
          "     have both, and that is where it counts.)")
else:
    def _make_clip(name, lavfi, audio=False):
        # A clip with a sound track, when asked: the demo's clips must come out silent whatever they went in as.
        sound = ["-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:a", "aac"] if audio else []
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", lavfi,
                        *sound, "-t", "3", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "40",
                        "-g", "25", "-pix_fmt", "yuv420p", os.path.join(CLIPS, name + ".mp4")],
                       check=True, capture_output=True, timeout=60)

    def _probe(path, *q):
        return subprocess.run(["ffprobe", "-v", "error", *q, path],
                              capture_output=True, text=True).stdout.strip()

    CLIPS = os.path.join(DEMO_ROOT, "clips")
    os.makedirs(CLIPS)
    _make_clip("front", "testsrc2=size=192x108:rate=25", audio=True)
    _make_clip("rear", "testsrc2=size=192x108:rate=25")
    _make_clip("cabin", "testsrc2=size=128x96:rate=25")
    _make_clip("cabin-drowsy", "smptebars=size=128x96:rate=25")

    # ---- the last 50 minutes of clips
    head("it lays out the last 50 minutes as one-minute clips")
    _T = time.time()
    _d = cams.DemoRecorder(CLIPS)
    check("it finds the footage: a clip for each role, and the drowsy cabin",
          (sorted(_d.sources), os.path.basename(_d.drowsy_src or "")),
          (["cabin", "front", "rear"], "cabin-drowsy.mp4"))
    check("seeding says how many clips each role got", _d.seed(now=_T),
          {"front": 50, "rear": 50, "cabin": 50})
    for _role in cams.ROLES:
        _dir = os.path.join(DEMO_VIDEOS, _role)
        _names = sorted(os.listdir(_dir))
        _starts = [camstore.clip_start(n) for n in _names]
        check(f"{_role}: fifty clips, each named YYYYmmdd-HHMMSS.mp4",
              (len(_names), all(camstore.CLIP_RE.match(n) for n in _names)), (50, True))
        check(f"{_role}: a minute apart, the newest begun within the last minute, the oldest 50 minutes back",
              (set(b - a for a, b in zip(_starts, _starts[1:])), 0 < _T - _starts[-1] <= 60,
               _T - _starts[0] <= 50 * 60), ({60.0}, True, True))
        check(f"{_role}: regular files, never symlinks, and the store will serve every one",
              all(os.path.isfile(os.path.join(_dir, n)) and not os.path.islink(os.path.join(_dir, n))
                  and camstore.clip_path(_role, n) for n in _names), True)
        _durs = [float(_probe(os.path.join(_dir, n), "-show_entries", "format=duration", "-of", "csv=p=0"))
                 for n in (_names[0], _names[25], _names[-1])]
        check(f"{_role}: each a minute long ({_durs})", all(59 <= x <= 62 for x in _durs), True)
    with open(os.path.join(DEMO_VIDEOS, "front", sorted(os.listdir(os.path.join(DEMO_VIDEOS, "front")))[0]),
              "rb") as f:
        _bytes = f.read()
    check("they are fragmented MP4, as the recorder writes them",
          (b"moov" in _bytes[:4096], b"moof" in _bytes), (True, True))
    check("the store lists all of them for the timeline",
          len(camstore.list_clips(t0=_T - 3600)), 150)
    check("the source had a sound track; the clips carry none",
          (_probe(os.path.join(CLIPS, "front.mp4"), "-select_streams", "a", "-show_entries",
                  "stream=codec_type", "-of", "csv=p=0"),
           _probe(os.path.join(DEMO_VIDEOS, "front", sorted(os.listdir(os.path.join(DEMO_VIDEOS, "front")))[0]),
                  "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0")),
          ("audio", ""))

    # ---- reseeding, and the minute that goes by
    head("a second start replaces the first; each minute adds a clip and the oldest goes")
    with open(os.path.join(DEMO_VIDEOS, "front", "20200101-000000.mp4"), "wb"):
        pass
    os.makedirs(os.path.join(DEMO_VIDEOS, "locked", "old", "front"))
    with open(os.path.join(DEMO_VIDEOS, "events.json"), "w", encoding="utf-8") as f:
        json.dump({"events": [{"id": "old", "kind": "marked", "t": 1, "t0": 0, "t1": 2, "state": "locked",
                               "files": []}]}, f)
    _d = cams.DemoRecorder(CLIPS)
    _d.seed(now=_T)
    check("the last run's clips, locked folder and events are gone, and there are 50 again",
          (len(os.listdir(os.path.join(DEMO_VIDEOS, "front"))),
           os.path.exists(os.path.join(DEMO_VIDEOS, "front", "20200101-000000.mp4")),
           os.path.exists(os.path.join(DEMO_VIDEOS, "locked")),
           camstore.load_events()["events"]), (50, False, False, []))
    _before = sorted(os.listdir(os.path.join(DEMO_VIDEOS, "rear")))
    check("nothing is added until the next minute begins", _d.roll(now=_T + 25), 0)
    check("then each role gets its next clip", _d.roll(now=_T + 31), 3)
    _after = sorted(os.listdir(os.path.join(DEMO_VIDEOS, "rear")))
    check("still fifty, the oldest gone and the new one a minute after the last",
          (len(_after), _after[0] == _before[1],
           camstore.clip_start(_after[-1]) - camstore.clip_start(_before[-1])), (50, True, 60.0))
    check("and a minute that has come and gone twice while nothing ran is caught up",
          _d.roll(now=_T + 31 + 120), 6)
    _ev = camstore.mark("marked", t=_T + 150, now=_T + 150)
    _d.tick(now=_T + 150)
    _d.roll(now=_T + 400)
    check("a marked event's clips are locked, and the minutes that follow never take them",
          (bool(_ev["files"]), all(os.path.exists(os.path.join(DEMO_VIDEOS, "locked", _ev["id"], *r.split("/")))
                                   for r in camstore.load_events()["events"][0]["files"])), (True, True))

    # ---- footage the tab's player may not play
    head("it says so when footage will not play in the tab")
    _hevc = os.path.join(DEMO_ROOT, "clips-odd")
    os.makedirs(_hevc)
    for _n in ("front", "rear", "cabin"):
        shutil.copy(os.path.join(CLIPS, _n + ".mp4"), _hevc)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=128x96:rate=25", "-t", "1", "-c:v", "mpeg4", os.path.join(_hevc, "rear.mp4")],
                   check=True, capture_output=True, timeout=60)
    with open(os.path.join(_hevc, "cabin.mp4"), "wb") as f:
        f.write(b"not a video")
    _warn = cams.demo_warnings(cams.DemoRecorder(_hevc).sources)
    check("H.264 says nothing; another codec and an unreadable file each get a sentence",
          [w.split(" ")[0] for w in _warn], ["rear.mp4", "cabin.mp4"])
    check("naming the file and what to do about it",
          (any(w.startswith("rear.mp4 is MPEG4, not H264") and "libx264" in w for w in _warn),
           any(w.startswith("cabin.mp4 could not be read") for w in _warn),
           any(w.startswith("front.mp4") for w in _warn)), (True, True, False))

    # ---- the ffmpeg commands
    head("its two kinds of ffmpeg")
    _seg = " ".join(cams.demo_segment_args("/c/front.mp4", "/o/seg%04d.mp4", 3100))
    check("clips: the source looped, copied not re-encoded, cut at a minute, fragmented as the recorder's are",
          all(x in _seg for x in ("-stream_loop -1 -i /c/front.mp4", "-c copy", "-f segment", "-segment_time 60",
                                  "-reset_timestamps 1", "movflags=+frag_keyframe+empty_moov+default_base_moof",
                                  "-an")), True)
    _fr = cams.demo_frame_args("/c/front.mp4", "/r/front.jpg")
    check("live pictures: real time, looped, 10 fps, 640 wide, quality 5, one file rewritten whole",
          (" ".join(_fr).startswith("ffmpeg") and
           all(x in " ".join(_fr) for x in ("-stream_loop -1 -re -i /c/front.mp4", "-vf fps=10,scale=640:-2",
                                            "-q:v 5", "-update 1", "-atomic_writing 1", " -y ")), _fr[-1]),
          (True, "/r/front.jpg"))

    # ---- the drowsy cabin, the watchdog and the status, against an ffmpeg that runs nothing
    head("the drowsy cabin swaps in for the scene, and only the cabin restarts")
    os.makedirs(DEMO_STATE, exist_ok=True)
    _B = time.time()          # `now` for the ticks below: the scene file's age is read against it
    LIVE = os.path.join(DEMO_STATE, "live.json")

    def _live(scene, age=0.0):
        with open(LIVE, "w", encoding="utf-8") as f:
            json.dump({"connected": True, "simulated": True, "demo": {"scene": scene}}, f)
        os.utime(LIVE, (time.time() - age, time.time() - age))

    def _src_of(proc):
        return proc.args[proc.args.index("-i") + 1]

    _live("drive")
    FakePopen.made.clear()
    _f = cams.DemoRecorder(CLIPS, popen=FakePopen)
    _f.start(now=_B)
    check("one ffmpeg per role", sorted(_src_of(p) for p in FakePopen.made),
          sorted(os.path.join(CLIPS, r + ".mp4") for r in ("front", "rear", "cabin")))
    check("each writing its live picture, in the demo's runtime folder",
          sorted(p.args[-1] for p in FakePopen.made),
          sorted(cams.live_path(r) for r in cams.ROLES))
    check("and nothing on any command line names a path under /dev",
          any(a.startswith("/dev/") or "=/dev/" in a for p in FakePopen.made for a in p.args), False)
    _front, _rear, _cabin = (next(p for p in FakePopen.made if _src_of(p).endswith(r + ".mp4"))
                             for r in ("front", "rear", "cabin"))
    _f.tick(now=_B)
    check("with the scene 'drive', nothing restarts", len(FakePopen.made), 3)
    _live("drowsy")
    _f.tick(now=_B + 1)
    check("on 'drowsy' the cabin's ffmpeg is replaced by one reading cabin-drowsy.mp4",
          (len(FakePopen.made), _cabin.code is not None, _src_of(FakePopen.made[-1])),
          (4, True, os.path.join(CLIPS, "cabin-drowsy.mp4")))
    check("front and rear are left alone", (_front.code, _rear.code), (None, None))
    _f.tick(now=_B + 2)
    check("and it does not restart again while the scene lasts", len(FakePopen.made), 4)
    _live("drive")
    _f.tick(now=_B + 3)
    check("when the scene ends the cabin goes back to its own clip",
          (len(FakePopen.made), _src_of(FakePopen.made[-1]), FakePopen.made[3].code is not None),
          (5, os.path.join(CLIPS, "cabin.mp4"), True))
    _live("drowsy", age=60)
    _f.tick(now=_B + 4)
    check("a live.json a minute old is a world that has stopped: no drowsy cabin", len(FakePopen.made), 5)
    os.remove(LIVE)
    _f.tick(now=_B + 5)
    check("nor is one that is not there", len(FakePopen.made), 5)

    _no_drowsy = os.path.join(DEMO_ROOT, "clips-no-drowsy")
    os.makedirs(_no_drowsy)
    for _n in ("front", "rear", "cabin"):
        shutil.copy(os.path.join(CLIPS, _n + ".mp4"), _no_drowsy)
    FakePopen.made.clear()
    _live("drowsy")
    _g = cams.DemoRecorder(_no_drowsy, popen=FakePopen)
    _g.start(now=_B)
    _g.tick(now=_B + 1)
    check("with no drowsy clip the cabin just carries on", len(FakePopen.made), 3)

    head("a feed that dies is started again, and one that stalls is killed")
    _live("drive")
    FakePopen.made.clear()
    _w = cams.DemoRecorder(CLIPS, popen=FakePopen)
    _w.start(now=_B)
    _front = next(p for p in FakePopen.made if _src_of(p).endswith("front.mp4"))
    _front.code = 1
    _w.tick(now=_B + 2)
    check("a dead ffmpeg is replaced at the next pass", len(FakePopen.made), 4)
    FakePopen.made[-1].code = 1
    _w.tick(now=_B + 3)
    check("but not more than once in five seconds", len(FakePopen.made), 4)
    _w.tick(now=_B + 8)
    check("and then it is", len(FakePopen.made), 5)

    head("its status is the recorder's status")
    os.makedirs(cams.run_dir(), exist_ok=True)
    _real_time = time.time()
    _s = cams.DemoRecorder(CLIPS, popen=FakePopen)
    _s.start(now=_real_time)
    _s.write_status()
    with open(cams.status_path(), encoding="utf-8") as f:
        _doc = json.load(f)
    _shape = set(cams.Camera("front", None, cams.SIM_MODE["front"]).status())
    check("the same keys as the recorder's, top and role by role",
          (set(_doc) >= {"pid", "t", "sim", "note", "roles"},
           {r: set(v) == _shape for r, v in _doc["roles"].items()}),
          (True, {"front": True, "rear": True, "cabin": True}))
    check("it is this process's pid and this second's time, and not a simulator",
          (_doc["pid"], abs(_doc["t"] - _real_time) < 5, _doc["sim"]), (os.getpid(), True, False))
    _fr = _doc["roles"]["front"]
    check("each role names its clip and reports its real mode, at the clip's own rate",
          (_fr["device"], _fr["mode"]["h"], _fr["mode"]["fps"], _doc["roles"]["cabin"]["mode"]["h"]),
          ("front.mp4", 108, 25.0, 96))
    check("before its first picture a role is starting, not REC",
          (_fr["recording"], _fr["starting"], _fr["live"]), (False, True, False))
    _live_jpg = cams.live_path("front")
    with open(_live_jpg, "wb") as f:
        f.write(b"\xff\xd8x\xff\xd9")
    _s.tick(now=time.time())
    with open(cams.status_path(), encoding="utf-8") as f:
        _fr = json.load(f)["roles"]["front"]
    check("with a fresh picture it is REC and live", (_fr["recording"], _fr["live"], _fr["stalled"]),
          (True, True, False))
    os.utime(_live_jpg, (time.time() - 6, time.time() - 6))
    _s.tick(now=time.time())
    _s.write_status()
    with open(cams.status_path(), encoding="utf-8") as f:
        _fr = json.load(f)["roles"]["front"]
    check("six seconds without a new picture is REC but not live", (_fr["recording"], _fr["live"]), (True, False))
    _n = len(FakePopen.made)
    os.utime(_live_jpg, (time.time() - 12, time.time() - 12))
    _s.tick(now=time.time())
    check("twelve is a stall: the feed is killed and started again", len(FakePopen.made) > _n, True)
    _s.stop()
    check("stopping ends every ffmpeg and takes the status file with it",
          (all(p.code is not None for p in FakePopen.made[-3:]), os.path.exists(cams.status_path())),
          (True, False))

    # ---- it opens nothing under /dev
    head("it never opens a device")
    _seen, _restore = _dev_guard()
    try:
        _z = cams.DemoRecorder(CLIPS)
        _z.seed(now=time.time())
        _z.start(now=time.time())
        _z.tick(now=time.time())
        _z.write_status()
        _z.stop()
    finally:
        _restore()
    check("with open() and os.open() refusing every path under /dev, a whole seed and start ran",
          (_seen, len(os.listdir(os.path.join(DEMO_VIDEOS, "cabin")))), ([], 50))

    # ---- the real thing, end to end: the command, the status, the drowsy cabin and SIGTERM
    head("the command itself: live pictures, status, the cabin scene, and SIGTERM")
    _live("drive")
    _log = open(os.path.join(DEMO_ROOT, "feed.log"), "w")
    _p = subprocess.Popen([sys.executable, CAMS_PY, "demo", "--from", CLIPS], env=_env(),
                          stdout=_log, stderr=_log)

    def _status():
        try:
            with open(cams.status_path(), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _until(what, cond, secs=90):
        end = time.time() + secs
        while time.time() < end:
            if _p.poll() is not None:
                return False
            if cond():
                return True
            time.sleep(0.4)
        return False

    _kids = {}
    try:
        _ready = _until("ready", lambda: (lambda o: o["running"] and o["note"] is None and all(
            o["roles"][r]["live"] and o["roles"][r]["clips"] >= 50 for r in cams.ROLES))(cams.overview()))
        check("it comes up: seeded, every role recording and live", _ready, True)
        _ov = cams.overview()
        check("overview() reports the recorder running, not simulated",
              (_ov["running"], _ov["sim"], [_ov["roles"][r]["recording"] for r in cams.ROLES],
               [_ov["roles"][r]["sim"] for r in cams.ROLES]), (True, False, [True] * 3, [False] * 3))
        check("with the real resolution, so the tab can say 1080p or whatever the footage is",
              [_ov["roles"][r]["mode"]["h"] for r in cams.ROLES], [108, 108, 96])
        check("and fifty clips a role on the timeline", [_ov["roles"][r]["clips"] for r in cams.ROLES],
              [50, 50, 50])
        check("the live pictures are 640 px wide JPEGs",
              [_probe(cams.live_path(r), "-show_entries", "stream=width,codec_name", "-of", "csv=p=0")
               for r in cams.ROLES], ["mjpeg,640"] * 3)
        _t1 = _status()["t"]
        time.sleep(1.4)
        check("the status file is rewritten every second", _status()["t"] > _t1, True)
        _kids = dict(_status()["children"])
        check("its children are one ffmpeg a role, alive",
              (sorted(_kids), all(cams._alive(pid) for pid in _kids.values())), (["cabin", "front", "rear"], True))
        _live("drowsy")
        check("on the drowsy scene the cabin's source changes",
              _until("drowsy", lambda: _status()["roles"]["cabin"]["device"] == "cabin-drowsy.mp4", 12), True)
        _now_kids = dict(_status()["children"])
        check("and only the cabin's process is new",
              (_now_kids["front"], _now_kids["rear"], _now_kids["cabin"] != _kids["cabin"]),
              (_kids["front"], _kids["rear"], True))
        check("the cabin stays REC through the swap", _status()["roles"]["cabin"]["recording"], True)
        _live("drive")
        check("and back again",
              _until("back", lambda: _status()["roles"]["cabin"]["device"] == "cabin.mp4", 12), True)
        _kids = dict(_status()["children"])
        _p.send_signal(signal.SIGTERM)
        try:
            _code = _p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            _code = "timed out"
        check("SIGTERM ends it promptly and cleanly", _code, 0)
        check("its ffmpeg children are all gone", [pid for pid in _kids.values() if cams._alive(pid)], [])
        check("and so is its status file", os.path.exists(cams.status_path()), False)
        check("the recorder reads as off again", cams.overview()["running"], False)
        check("its clips are left on disk for the tab",
              len(os.listdir(os.path.join(DEMO_VIDEOS, "front"))) >= 50, True)
    finally:
        if _p.poll() is None:
            _p.kill()
            _p.wait()
        for _pid in _kids.values():
            if cams._alive(_pid):
                os.kill(_pid, signal.SIGKILL)
        _log.close()

for _k, _v in _saved_env.items():
    if _v is None:
        os.environ.pop(_k, None)
    else:
        os.environ[_k] = _v

shutil.rmtree(SCRATCH, ignore_errors=True)
print()
if fails:
    print(f"  {fails} failed\n")
    sys.exit(1)
print("  the recorder holds\n")
