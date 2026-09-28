#!/usr/bin/env python3
"""The recorder, without a camera: which camera is which, what mode it records
in, what the loop deletes, what counts as hard braking, and what an event
locks.

The last section runs the real ffmpeg command for a few seconds against a test
picture wherever there is ffmpeg and a VA-API render node (the box and the
tablet both have them), and skips loudly anywhere else. Scratch folders only:
nothing here touches ~/Videos or the real runtime directory.
"""

import json
import os
import shutil
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
_cam.started = _now - 16
check("and then it is stalled too", _cam.stalled(), True)
_rec = cams.Recorder()
_rec.cams["front"] = _cam
_rec.check_stalls()
check("the watchdog kills it, and it restarts on the next pass",
      ("KILL" in _cam.proc.signals, _rec.retry_at["front"], _rec.notes["front"].startswith("stalled")),
      (True, 0, True))


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

shutil.rmtree(SCRATCH, ignore_errors=True)
print()
if fails:
    print(f"  {fails} failed\n")
    sys.exit(1)
print("  the recorder holds\n")
