#!/usr/bin/env python3
"""The cameras: which is which, what each can do, and the recorder that owns them.

    omacar cams status      what is plugged in, and what is recording
    omacar cams on | off    enable and start, or stop and disable, omacar-cams.service
    omacar cams sim         the recorder, here, with lavfi test pictures for
                            every role that has no camera (SIMULATED in the app)
    omacar cams run [--sim] the recorder itself; what the unit runs

ONE FFMPEG PER CAMERA. The input is decoded once and split two ways: the
recording, compressed on the GPU into one-minute fragmented-MP4 clips, and a
640-px, 10 fps JPEG live picture, which this process writes atomically to
$XDG_RUNTIME_DIR/omacar-cams/<role>.jpg. Files, not sockets, the way the
daemon and live.json already work.

CAMERAS ARE FOUND BY NAME, never by /dev/videoN: the tablet's IPU6 driver
claims video0-video63, so USB cameras land at video64 and up, in any order.
And on a machine where the IPU6 owns those low nodes, nothing below video64 is
ever opened, whatever a pattern matches: opening one wedged the IPU6 firmware
and rebooted the Surface on 2026-09-27.

A CAMERA THAT STALLS IS RESTARTED. A camera that browns out on USB power
leaves ffmpeg alive and blocked in its read, writing nothing. After ten
seconds without a live frame it is reported "stalled", never REC, killed, and
started again.

Stdlib only; ffmpeg and v4l2-ctl do the work.
"""

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import camstore  # noqa: E402

ROLES = camstore.ROLES
BY_ID = "/dev/v4l/by-id"
VAAPI = "/dev/dri/renderD128"
SYSFS = "/sys/class/video4linux"
MIN_USB_NODE = 64       # the IPU6 claims video0-video63 on the tablet
# The defaults; omacar-cameras.json may lower them (camstore.load_config).
CAPS = camstore.DEFAULT_CAPS
BITRATE = {"front": "6M", "rear": "6M", "cabin": "1M"}
FORMAT_ORDER = ("MJPG", "H264", "YUYV")
INPUT_FORMAT = {"MJPG": "mjpeg", "H264": "h264", "YUYV": "yuyv422"}
# What `sim` stands up for a role with no camera. 720p rather than 1080p, so
# three test pictures cost the box no more than real cameras would.
SIM_MODE = {"front": {"fmt": "SIM", "w": 1280, "h": 720, "fps": 30.0},
            "rear": {"fmt": "SIM", "w": 1280, "h": 720, "fps": 30.0},
            "cabin": {"fmt": "SIM", "w": 640, "h": 480, "fps": 30.0}}
LIVE_WIDTH = 640
LIVE_FPS = 10
LIVE_GIVE_UP = 10       # seconds without a new frame before a live stream ends
STALL_SECS = 10         # ffmpeg alive, no new frame for this long: stalled
START_GRACE = 15        # a camera's first frame may take this long
BOUNDARY = "omacarframe"


def run_dir():
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(base, "omacar-cams")


def live_path(role):
    return os.path.join(run_dir(), f"{role}.jpg")


def status_path():
    return os.path.join(run_dir(), "status.json")


# ---- which camera is which -------------------------------------------------

def match_roles(names, patterns):
    """{role: by-id name} for the cameras whose names match each role's pattern.

    Only capture nodes count: a UVC camera also exposes -video-index1, which
    carries metadata, not pictures. Case-sensitive, so the Ace Pro's "Ace" is
    not found inside "Surface"; and tried with underscores read as spaces as
    well, because udev writes "Osmo Action 4" as Osmo_Action_4."""
    out, taken = {}, set()
    capture = sorted(n for n in names if n.endswith("-video-index0"))
    for role in ROLES:
        pat = patterns.get(role)
        if not pat:
            continue
        rx = re.compile(pat)
        for n in capture:
            if n not in taken and (rx.search(n) or rx.search(n.replace("_", " "))):
                out[role] = n
                taken.add(n)
                break
    return out


def _node_name(sysfs, node):
    try:
        with open(os.path.join(sysfs, f"video{node}", "name"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def usb_floor(sysfs=SYSFS):
    """The lowest /dev/videoN a camera may be. On a machine where any node is
    the IPU6's (sysfs names them "Intel IPU6 ISYS Capture N"), that is video64;
    elsewhere, the box for one, where the C920 is video0, it is 0. A machine
    whose sysfs cannot be read is treated as the tablet."""
    try:
        names = os.listdir(sysfs)
    except OSError:
        return MIN_USB_NODE
    for n in names:
        m = re.fullmatch(r"video(\d+)", n)
        if m and re.search(r"\bIPU", _node_name(sysfs, m.group(1)), re.I):
            return MIN_USB_NODE
    return 0


def find_cameras(cfg=None, by_id=BY_ID, sysfs=SYSFS):
    """{role: /dev/v4l/by-id/...} for what is plugged in now. Each link is
    resolved, and a node below the floor, or named as the IPU's, is refused
    whatever the config's patterns say."""
    cfg = cfg or camstore.load_config()
    try:
        names = os.listdir(by_id)
    except OSError:
        names = []
    floor = usb_floor(sysfs)
    out = {}
    for role, name in match_roles(names, cfg["patterns"]).items():
        path = os.path.join(by_id, name)
        m = re.fullmatch(r"video(\d+)", os.path.basename(os.path.realpath(path)))
        if not m or int(m.group(1)) < floor or re.search(r"\bIPU", _node_name(sysfs, m.group(1)), re.I):
            continue
        out[role] = path
    return out


# ---- what each camera can do -------------------------------------------------

_FMT = re.compile(r"\[\d+\]:\s*'(\w+)'")
_SIZE = re.compile(r"Size:\s*Discrete\s+(\d+)x(\d+)")
_FPS = re.compile(r"\(([\d.]+)\s*fps\)")


def parse_formats(text):
    """`v4l2-ctl --list-formats-ext` as [{fmt, w, h, fps: [...]}]."""
    modes, fmt, cur = [], None, None
    for line in (text or "").splitlines():
        m = _FMT.search(line)
        if m:
            fmt, cur = m.group(1), None
            continue
        m = _SIZE.search(line)
        if m and fmt:
            cur = {"fmt": fmt, "w": int(m.group(1)), "h": int(m.group(2)), "fps": []}
            modes.append(cur)
            continue
        m = _FPS.search(line)
        if m and cur is not None:
            cur["fps"].append(float(m.group(1)))
    return modes


def choose_mode(modes, cap):
    """The best mode at or under `cap` (w, h, fps), preferring MJPEG, then
    H.264, then YUYV. Within a format, a mode that manages 15 fps beats one
    that does not, then the bigger picture, then the higher rate: a C920's
    YUYV 1080p runs at 5 fps, which is a slideshow, not a dashcam."""
    cw, ch, cf = cap
    for fmt in FORMAT_ORDER:
        best = None
        for m in modes:
            if m["fmt"] != fmt or m["w"] > cw or m["h"] > ch:
                continue
            fps = max((f for f in m["fps"] if f <= cf + 0.01), default=None)
            if fps is None:
                continue
            key = (fps >= 15, m["w"] * m["h"], fps)
            if best is None or key > best[0]:
                best = (key, {"fmt": fmt, "w": m["w"], "h": m["h"], "fps": fps})
        if best:
            return best[1]
    return None


def probe_modes(device):
    try:
        r = subprocess.run(["v4l2-ctl", "-d", device, "--list-formats-ext"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_formats(r.stdout)


# ---- the command ------------------------------------------------------------

def ffmpeg_args(role, mode, clip_dir, device=None, clip_secs=camstore.CLIP_SECS,
                duration=None, vaapi=VAAPI):
    """One ffmpeg for one camera: decode once, split two ways.

      recording  hwupload -> h264_vaapi. -fps_mode passthrough, so a camera
                 that slows in the dark is not padded with duplicates (on
                 2026-09-27 a dim room gave 10 real fps and 407 of 600 frames
                 were padding). Keyframes forced at every clip boundary,
                 because without them 5 s segments came out 6 s and 4 s long.
                 A keyframe every 2 s besides, so the fragmented MP4 has
                 fragments to seek to.
      live       10 fps, 640 px wide, MJPEG on stdout, for this process to cut
                 into files.

    With `device` None, the input is a test picture (lavfi testsrc2) in the
    role's mode, at real time. `duration` stops it (tests only).
    """
    w, h, fps = mode["w"], mode["h"], mode["fps"]
    rate = f"{fps:g}"
    src = ["-t", str(duration)] if duration else []
    if device is None:
        src += ["-re", "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate={rate}"]
    else:
        src += ["-f", "v4l2", "-input_format", INPUT_FORMAT[mode["fmt"]],
                "-video_size", f"{w}x{h}", "-framerate", rate, "-i", device]
    gop = str(max(1, int(round(fps * 2))))
    return [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-nostats",
        "-progress", "pipe:2", "-vaapi_device", vaapi, *src,
        "-filter_complex",
        f"[0:v]split=2[rec][live];[rec]format=nv12,hwupload[recv];"
        f"[live]fps={LIVE_FPS},scale={LIVE_WIDTH}:-2[livev]",
        "-map", "[recv]", "-c:v", "h264_vaapi", "-b:v", BITRATE[role],
        "-maxrate", BITRATE[role], "-g", gop, "-fps_mode", "passthrough",
        "-force_key_frames", f"expr:gte(t,n_forced*{clip_secs})",
        "-f", "segment", "-segment_time", str(clip_secs), "-segment_format", "mp4",
        "-segment_format_options", "movflags=+frag_keyframe+empty_moov+default_base_moof",
        "-reset_timestamps", "1", "-strftime", "1",
        os.path.join(clip_dir, "%Y%m%d-%H%M%S.mp4"),
        "-map", "[livev]", "-c:v", "mjpeg", "-q:v", "6", "-f", "mjpeg", "pipe:1",
    ]


def split_jpegs(buf):
    """(whole JPEGs, the unfinished rest) from concatenated JPEGs. ffmpeg's
    MJPEG never carries a thumbnail, and 0xFF inside entropy-coded data is
    always stuffed, so FFD9 inside a frame is its end."""
    frames = []
    while True:
        s = buf.find(b"\xff\xd8")
        if s < 0:
            return frames, b""
        e = buf.find(b"\xff\xd9", s + 2)
        if e < 0:
            return frames, buf[s:]
        frames.append(buf[s:e + 2])
        buf = buf[e + 2:]


class FpsMeter:
    """Real frames per second over the last few seconds, from ffmpeg's running
    frame count. ffmpeg's own fps= averages since it started, which hides a
    camera that slowed down when the road went dark ten minutes ago."""

    def __init__(self, window=5.0):
        self.window = window
        self.points = deque()

    def add(self, t, frames):
        self.points.append((t, frames))
        while len(self.points) > 2 and t - self.points[0][0] > self.window:
            self.points.popleft()

    def fps(self, now=None):
        if len(self.points) < 2:
            return None
        if now is not None and now - self.points[-1][0] > self.window:
            return None            # ffmpeg has stopped counting: no rate, not the last one
        (t0, f0), (t1, f1) = self.points[0], self.points[-1]
        return round((f1 - f0) / (t1 - t0), 1) if t1 > t0 else None


# ---- one camera ---------------------------------------------------------------

class Camera:
    """One role's ffmpeg, its live picture, and the last thing it said."""

    def __init__(self, role, device, mode, sim=False):
        self.role, self.device, self.mode, self.sim = role, device, mode, sim
        self.proc = None
        self.error = None
        self.started = None
        self.last_frame = None
        self.meter = FpsMeter()

    def start(self):
        clip_dir = os.path.join(camstore.videos(), self.role)
        os.makedirs(clip_dir, exist_ok=True)
        os.makedirs(run_dir(), exist_ok=True)
        args = ffmpeg_args(self.role, self.mode, clip_dir,
                           device=None if self.sim else self.device)
        self.proc = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.started = time.time()
        threading.Thread(target=self._frames, daemon=True).start()
        threading.Thread(target=self._progress, daemon=True).start()

    # A reader that dies leaves ffmpeg blocked on a full pipe; the error is
    # kept, and the stall watchdog restarts the camera.
    def _frames(self):
        try:
            out = live_path(self.role)
            tmp = out + ".tmp"
            buf = b""
            while True:
                chunk = self.proc.stdout.read1(1 << 16)
                if not chunk:
                    return
                frames, buf = split_jpegs(buf + chunk)
                if frames:
                    with open(tmp, "wb") as f:
                        f.write(frames[-1])
                    os.replace(tmp, out)
                    self.last_frame = time.time()
                if len(buf) > 8 << 20:
                    buf = b""      # eight megabytes with no end marker is not a picture
        except Exception as e:                                 # noqa: BLE001
            self.error = f"live picture reader failed: {type(e).__name__}: {e}"[:300]

    def _progress(self):
        try:
            for raw in self.proc.stderr:
                line = raw.decode("utf-8", "replace").strip()
                key, sep, val = line.partition("=")
                if sep and re.fullmatch(r"[a-z0-9_]+", key):
                    if key == "frame" and val.isdigit():
                        self.meter.add(time.time(), int(val))
                    continue
                if line:
                    self.error = line[:300]
        except Exception as e:                                 # noqa: BLE001
            self.error = f"progress reader failed: {type(e).__name__}: {e}"[:300]

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.alive():
            # SIGINT, not SIGKILL: ffmpeg closes the clip it is writing, so the
            # last minute is a playable file rather than a truncated one.
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def stalled(self, now=None):
        """ffmpeg alive, and no live frame for STALL_SECS (or none at all
        START_GRACE after it started)."""
        if not self.alive():
            return False
        now = time.time() if now is None else now
        if self.last_frame is None:
            return now - (self.started or now) > START_GRACE
        return now - self.last_frame > STALL_SECS

    def kill(self):
        # SIGKILL, not SIGINT: a stalled ffmpeg is blocked in the V4L2 read and
        # may never see a gentler signal. The fragments written so far play.
        if self.alive():
            self.proc.kill()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    def status(self):
        now = time.time()
        alive = self.alive()
        stalled = self.stalled(now)
        fresh = self.last_frame is not None and now - self.last_frame < 3
        since = self.last_frame or self.started or now
        return {"device": self.device, "mode": self.mode, "sim": self.sim,
                "recording": alive and not stalled, "stalled": stalled,
                "live": alive and fresh and not stalled,
                "fps": self.meter.fps(now) if alive and not stalled else None,
                "since": self.started,
                "error": f"stalled: no picture for {int(now - since)} s" if stalled else self.error}


# ---- the recorder --------------------------------------------------------------

def _live():
    try:
        import records
        return records.live()
    except Exception:                                          # noqa: BLE001
        return None


class Recorder:
    """Every role's camera, the loop, and the watch for hard braking."""

    def __init__(self, sim=False):
        self.sim = sim
        self.cams = {}
        self.notes = {}
        self.retry_at = {}
        self.brake = camstore.BrakeWatch()
        self.last_t = None

    def discover(self):
        cfg = camstore.load_config()
        found = find_cameras(cfg)
        now = time.time()
        for role in ROLES:
            cam = self.cams.get(role)
            if cam and cam.alive():
                continue
            if cam and cam.error:
                self.notes[role] = cam.error
            if now < self.retry_at.get(role, 0):
                continue
            self.retry_at[role] = now + 5          # a camera that dies is retried, not spun
            if not cfg["patterns"].get(role):
                self.notes[role] = "off in omacar-cameras.json"
                self.cams.pop(role, None)
                continue
            dev = found.get(role)
            if dev:
                mode = choose_mode(probe_modes(dev), cfg["caps"][role])
                if not mode:
                    self.notes[role] = "no mode at or under 1080p30"
                    self.cams.pop(role, None)
                    continue
                new = Camera(role, dev, mode)
            elif self.sim:
                new = Camera(role, None, SIM_MODE[role], sim=True)
            else:
                self.notes[role] = "no camera"
                self.cams.pop(role, None)
                continue
            new.start()
            self.cams[role] = new

    def check_stalls(self):
        """Kill any camera that has stalled; discover() starts it again."""
        for role, cam in list(self.cams.items()):
            if cam.stalled():
                self.notes[role] = f"stalled; restarted at {time.strftime('%H:%M:%S')}"
                cam.kill()
                self.retry_at[role] = 0

    def watch_braking(self):
        s = _live()
        # The simulator's speed is not the car's: never hard braking.
        if not s or not s.get("connected") or s.get("simulated"):
            self.brake.feed(time.time(), None)
            return
        t = s.get("t")
        kph = (s.get("values") or {}).get("SPEED")
        if t is None or t == self.last_t or not isinstance(kph, (int, float)):
            return
        self.last_t = t
        if self.brake.feed(t, kph):
            camstore.mark("hard-braking", t=t, speed_kph=self.brake.peak)

    def write_status(self):
        doc = {"pid": os.getpid(), "t": time.time(), "sim": self.sim,
               "note": self.notes.get("recorder"), "roles": {}}
        for role in ROLES:
            cam = self.cams.get(role)
            doc["roles"][role] = cam.status() if cam else {
                "device": None, "mode": None, "sim": False, "recording": False, "stalled": False,
                "live": False, "fps": None, "since": None, "error": self.notes.get(role)}
        os.makedirs(run_dir(), exist_ok=True)
        tmp = status_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        os.replace(tmp, status_path())

    def _safely(self, what, fn):
        """One failing chore must not take the recorder down: systemd would
        restart it every 5 s, and every restart cuts every clip short."""
        try:
            fn()
        except Exception as e:                                 # noqa: BLE001
            self.notes["recorder"] = f"{what} failed: {type(e).__name__}: {e}"[:300]

    def run(self):
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        next_find = next_settle = next_janitor = 0.0
        while not stop.is_set():
            now = time.time()
            if now >= next_find:
                self._safely("the stall watchdog", self.check_stalls)
                self._safely("discovery", self.discover)
                next_find = now + 2
            # live.json is written five times a second; one second of braking
            # needs every sample of it.
            self._safely("the hard-braking watch", self.watch_braking)
            if now >= next_settle:
                self._safely("locking", camstore.settle)
                self._safely("the status file", self.write_status)
                next_settle = now + 1
            if now >= next_janitor:
                self._safely("the loop", camstore.janitor)
                next_janitor = now + 30
            stop.wait(0.2)
        for cam in self.cams.values():
            cam.stop()
        try:
            os.remove(status_path())
        except OSError:
            pass


# ---- what the API and the CLI read ---------------------------------------------

def _read_status():
    try:
        with open(status_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _ours(pid):
    """Is `pid` a live recorder, rather than a number the kernel has since
    handed to something else? /proc says what it is running; without /proc
    (not Linux) the pid is all there is."""
    if not _alive(pid):
        return False
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as f:
            argv = f.read().decode("utf-8", "replace").split("\0")
    except OSError:
        return True
    return any(a.endswith("cams.py") for a in argv) and ("run" in argv or "sim" in argv)


def running(st):
    """A status file says a recorder is running only if it is fresh and its
    pid is still a recorder."""
    return bool(st) and time.time() - st.get("t", 0) < 5 and _ours(st.get("pid"))


def overview():
    """GET /api/cams. Per role: device, mode, recording, real fps, clip count,
    storage used, and the last error; and storage used of the budget."""
    cfg = camstore.load_config()
    st = _read_status()
    running_now = running(st)
    found = find_cameras(cfg)
    use = camstore.usage()
    roles = {}
    for role in ROLES:
        r = (st or {}).get("roles", {}).get(role, {}) if running_now else {}
        why = r.get("error") if running_now else ("recorder off" if role in found else "no camera")
        roles[role] = {
            "device": r.get("device") or found.get(role),
            "mode": r.get("mode"), "sim": bool(r.get("sim")),
            "recording": bool(r.get("recording")), "stalled": bool(r.get("stalled")),
            "live": bool(r.get("live")),
            "fps": r.get("fps"), "clips": use["clips"][role], "used": use["by_role"][role],
            "error": why,
        }
    return {"running": running_now, "sim": bool(running_now and st.get("sim")),
            "note": (st or {}).get("note") if running_now else None,
            "storage": {"used": use["used"], "budget": camstore.budget_bytes(cfg)},
            "loop": True, "roles": roles, "now": time.time()}


def stream_live(out, role, frames=None, give_up=LIVE_GIVE_UP, clock=time.time,
                sleep=time.sleep):
    """Write the role's live picture to `out` as multipart MJPEG, each part
    carrying its own length, until `frames` parts have gone or no new frame
    has arrived for `give_up` seconds. Returns the number sent."""
    path = live_path(role)
    last, sent, quiet_since = None, 0, clock()
    while True:
        try:
            st = os.stat(path)
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = None
        if stamp is not None and stamp != last:
            try:
                with open(path, "rb") as f:
                    jpg = f.read()
            except OSError:
                jpg = b""
            last = stamp
            if jpg:
                out.write(b"--%s\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n"
                          % (BOUNDARY.encode(), len(jpg)) + jpg + b"\r\n")
                out.flush()
                sent += 1
                quiet_since = clock()
                if frames and sent >= frames:
                    return sent
        elif clock() - quiet_since > give_up:
            return sent
        sleep(1.0 / LIVE_FPS)


def _print_status():
    ov = overview()
    state = ("running" + (" (sim)" if ov["sim"] else "")) if ov["running"] else "off"
    s = ov["storage"]
    print(f"\n  recorder  {state}")
    print(f"  storage   {s['used'] / 1e9:.1f} of {s['budget'] / 1e9:.0f} GB\n")
    for role, r in ov["roles"].items():
        m = r["mode"]
        mode = f"{m['fmt']} {m['w']}x{m['h']}@{m['fps']:g}" if m else "-"
        fps = f"{r['fps']:.1f} fps" if r["fps"] else ""
        state = "REC" if r["recording"] else ("STALLED" if r["stalled"] else "---")
        print(f"  {role:<6} {state:<7} {mode:<22} {fps:<9} "
              f"{r['clips']:>4} clips  {os.path.basename(r['device'] or '') or '(none)'}")
        if r["error"]:
            print(f"         {r['error']}")
    print()
    return 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "status":
        return _print_status()
    if cmd in ("on", "off"):
        how = ["enable", "--now"] if cmd == "on" else ["disable", "--now"]
        return subprocess.run(["systemctl", "--user", *how, "omacar-cams.service"]).returncode
    if cmd in ("run", "sim"):
        st = _read_status()
        if running(st) and st.get("pid") != os.getpid():
            print(f"omacar: the recorder is already running (pid {st['pid']}): "
                  f"omacar cams off first", file=sys.stderr)
            return 1
        Recorder(sim=(cmd == "sim" or "--sim" in argv[2:])).run()
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
