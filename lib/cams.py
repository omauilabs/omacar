#!/usr/bin/env python3
"""The cameras: which is which, what each can do, and the recorder that owns them.

    omacar cams status      what is plugged in, and what is recording
    omacar cams on | off    enable and start, or stop and disable, omacar-cams.service
    omacar cams sim         the recorder, here, with lavfi test pictures for
                            every role that has no camera (SIMULATED in the app)
    omacar cams run [--sim] the recorder itself; what the unit runs
    omacar cams demo [--from DIR]
                            the meetup demo's cameras: footage on a loop, in
                            place of live cameras (see "the demo's cameras")

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
import shutil
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
    well, because udev writes "Osmo Action 4" as Osmo_Action_4.

    A pattern that is not a valid regular expression matches nothing, rather
    than raising: a mistyped glob in omacar-cameras.json (for example
    "*C920*") must disable only its own role, never take find_cameras() and
    everything downstream of it -- discover(), overview(), `omacar cams
    status` -- down with it. See invalid_patterns()."""
    out, taken = {}, set()
    capture = sorted(n for n in names if n.endswith("-video-index0"))
    for role in ROLES:
        pat = patterns.get(role)
        if not pat:
            continue
        try:
            rx = re.compile(pat)
        except re.error:
            continue
        for n in capture:
            if n not in taken and (rx.search(n) or rx.search(n.replace("_", " "))):
                out[role] = n
                taken.add(n)
                break
    return out


def invalid_patterns(patterns):
    """{role: pattern} for a role whose configured pattern is not a valid
    regular expression. match_roles() already treats such a pattern as no
    match rather than raising; this is how a caller finds out *why* a role
    has no camera, so the status line can name the role and the bad pattern
    instead of a plain, misleading "no camera"."""
    bad = {}
    for role in ROLES:
        pat = patterns.get(role)
        if not pat:
            continue
        try:
            re.compile(pat)
        except re.error:
            bad[role] = pat
    return bad


def _node_name(sysfs, node):
    try:
        with open(os.path.join(sysfs, f"video{node}", "name"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def usb_floor(sysfs=SYSFS):
    """The floor of last resort, used only for a node whose own USB identity
    cannot be told (see _is_usb): video64 on a machine where any node is
    named as the IPU6's ("Intel IPU6 ISYS Capture N"), or where a node's name
    cannot be read at all -- fail closed rather than let one unreadable name
    make the floor 0 on what might be the tablet. Otherwise 0: the box, for
    one, has no IPU and its C920 is video0. A machine whose sysfs cannot be
    listed at all is treated as the tablet."""
    try:
        names = os.listdir(sysfs)
    except OSError:
        return MIN_USB_NODE
    for n in names:
        m = re.fullmatch(r"video(\d+)", n)
        if not m:
            continue
        try:
            with open(os.path.join(sysfs, n, "name"), encoding="utf-8") as f:
                node_name = f.read().strip()
        except OSError:
            return MIN_USB_NODE     # a name we cannot read is never proof this is not the tablet
        if re.search(r"\bIPU", node_name, re.I):
            return MIN_USB_NODE
    return 0


def _is_usb(sysfs, node):
    """True if videoN's own sysfs device resolves under the USB subsystem,
    False if it resolves under a different one, None if that cannot be told
    at all (no device link, or nothing at the far end of it).

    This is the kernel's own signal for what bus a device sits on -- the same
    "device/subsystem -> .../bus/usb" chain confirmed against the box's real
    C920 (device -> .../usb1/1-3/1-3:1.0, whose own subsystem -> /sys/bus/usb)
    -- so a real USB camera is never refused for enumerating at a low node
    number, which is the Wednesday risk: if uvcvideo enumerates before the
    IPU6 registers, a USB camera can land below video64."""
    target = os.path.realpath(os.path.join(sysfs, f"video{node}", "device", "subsystem"))
    if not os.path.isdir(target):
        return None
    return os.path.basename(target) == "usb"


def _scan_cameras(cfg, by_id, sysfs):
    """One pass over what by-id matches each role's pattern: which are
    accepted, and why a match that exists was refused. A node is accepted by
    identity, never by number alone:

      - named as the IPU6's (sysfs calls it "Intel IPU6 ISYS Capture N")?
        refused, whatever number it is.
      - otherwise a real USB device (_is_usb)? accepted, whatever number it
        is -- opening an IPU6 node wedged the IPU6 firmware and rebooted the
        Surface on 2026-09-27, but a USB camera has never done that.
      - otherwise, USB-ness itself could not be told (sysfs unreadable, or
        no device link)? fall back to usb_floor(): refused below video64,
        the tablet's own floor, since that is the only case where we cannot
        rule out this being an IPU6 node.

    Shared by find_cameras() (what discover() and the API use) and
    refused_cameras() (why a matched-but-rejected role is not "no camera")."""
    try:
        names = os.listdir(by_id)
    except OSError:
        names = []
    floor = usb_floor(sysfs)
    accepted, refused = {}, {}
    for role, name in match_roles(names, cfg["patterns"]).items():
        path = os.path.join(by_id, name)
        m = re.fullmatch(r"video(\d+)", os.path.basename(os.path.realpath(path)))
        if not m:
            refused[role] = f"{name} does not resolve to a video4linux device"
            continue
        node = m.group(1)
        if re.search(r"\bIPU", _node_name(sysfs, node), re.I):
            refused[role] = f"video{node} is an IPU6 capture node"
            continue
        usb = _is_usb(sysfs, node)
        if usb is False:
            refused[role] = f"video{node} is not a USB device"
            continue
        if usb is None and int(node) < floor:
            refused[role] = f"video{node} is below the IPU6 floor (video{floor}); its own USB identity could not be confirmed"
            continue
        accepted[role] = path
    return accepted, refused


def find_cameras(cfg=None, by_id=BY_ID, sysfs=SYSFS):
    """{role: /dev/v4l/by-id/...} for what is plugged in now, accepted by
    identity rather than by node number (see _scan_cameras)."""
    cfg = cfg or camstore.load_config()
    accepted, _ = _scan_cameras(cfg, by_id, sysfs)
    return accepted


def refused_cameras(cfg=None, by_id=BY_ID, sysfs=SYSFS):
    """{role: reason} for a by-id match that exists but that find_cameras()
    refused. A role refused this way is not unplugged -- it is a camera
    find_cameras() will not open -- and its status should say so rather than
    the "no camera" it would otherwise fall back to."""
    cfg = cfg or camstore.load_config()
    _, refused = _scan_cameras(cfg, by_id, sysfs)
    return refused


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
    """One role's ffmpeg, its live picture, and the last thing it said.

    `restarted`, when given, is the note the camera this one replaces left
    behind (for example "stalled; restarted at 12:03:04"). It is carried
    only so status() can say *why* while this one is still in its own start
    grace with no picture of its own yet -- it never on its own makes this
    instance the stalled one; that would need a live frame it has not had
    the chance to send."""

    def __init__(self, role, device, mode, sim=False, restarted=None):
        self.role, self.device, self.mode, self.sim = role, device, mode, sim
        self.proc = None
        self.error = None
        self.started = None
        self.last_frame = None
        self.restarted = restarted
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
        # RECORDING NEEDS A FIRST FRAME. Without this, the replacement ffmpeg
        # the watchdog starts after a kill reads REC for its whole 15 s start
        # grace even if it is exactly as wedged as the one it replaced -- REC
        # about 15 s of every 17 on a camera that has recorded nothing since
        # its restart. "stalled" alone is not enough: it is only true while
        # the killed process is still being reaped, at most a couple of
        # seconds.
        recording = alive and self.last_frame is not None and not stalled
        starting = alive and not recording and not stalled
        since = self.last_frame or self.started or now
        if stalled:
            error = f"stalled: no picture for {int(now - since)} s"
        elif starting:
            waited = int(now - (self.started or now))
            error = (f"restarting ({waited} s so far): {self.restarted}" if self.restarted
                     else f"starting: no picture yet ({waited} s)")
        else:
            error = self.error
        return {"device": self.device, "mode": self.mode, "sim": self.sim,
                "recording": recording, "stalled": stalled, "starting": starting,
                "live": alive and fresh and not stalled,
                "fps": self.meter.fps(now) if alive and not stalled else None,
                "since": self.started,
                "error": error}


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
        bad = invalid_patterns(cfg["patterns"])
        found = find_cameras(cfg)
        refused = refused_cameras(cfg)
        now = time.time()
        for role in ROLES:
            cam = self.cams.get(role)
            if cam and cam.alive():
                continue
            replacing = cam is not None       # a Camera existed here and died: this is a restart
            if cam and cam.error:
                self.notes[role] = cam.error
            if now < self.retry_at.get(role, 0):
                continue
            self.retry_at[role] = now + 5          # a camera that dies is retried, not spun
            # ONE BAD PATTERN DISABLES ONLY ITS OWN ROLE. match_roles() never
            # raises on a bad regex, but without this check the role would
            # just read the generic, misleading "no camera" -- and before
            # this fix, load_config()'s promise that a bad value is "ignored
            # rather than trusted" did not hold for patterns at all: one
            # mistyped glob such as "*C920*" raised inside match_roles() and
            # took discover(), overview() and `omacar cams status` down with
            # it, for every role, not just the mistyped one.
            if role in bad:
                self.notes[role] = f"bad pattern in omacar-cameras.json: {bad[role]!r}"
                self.cams.pop(role, None)
                continue
            if not cfg["patterns"].get(role):
                self.notes[role] = "off in omacar-cameras.json"
                self.cams.pop(role, None)
                continue
            # The note this role's last Camera left behind (a stall restart,
            # or its own ffmpeg error), carried into the new one so status()
            # can say why while it is still in its own start grace with no
            # picture yet -- see Camera.status().
            restart_note = self.notes.get(role) if replacing else None
            dev = found.get(role)
            if dev:
                mode = choose_mode(probe_modes(dev), cfg["caps"][role])
                if not mode:
                    self.notes[role] = "no mode at or under 1080p30"
                    self.cams.pop(role, None)
                    continue
                new = Camera(role, dev, mode, restarted=restart_note)
            elif self.sim:
                new = Camera(role, None, SIM_MODE[role], sim=True, restarted=restart_note)
            else:
                # A by-id match that exists but was refused (an IPU6 node, a
                # non-USB one, or one below the floor with no way to tell)
                # says so, rather than reading the same as an unplugged role.
                self.notes[role] = refused.get(role, "no camera")
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
                "starting": False, "live": False, "fps": None, "since": None,
                "error": self.notes.get(role)}
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
    return any(a.endswith("cams.py") for a in argv) and ("run" in argv or "sim" in argv or "demo" in argv)


def running(st):
    """A status file says a recorder is running only if it is fresh and its
    pid is still a recorder."""
    return bool(st) and time.time() - st.get("t", 0) < 5 and _ours(st.get("pid"))


def overview():
    """GET /api/cams. Per role: device, mode, recording, real fps, clip count,
    storage used, and the last error; and storage used of the budget.

    Never raises, whatever omacar-cameras.json says: a bad pattern or a
    refused camera is reported in `error`, not thrown."""
    cfg = camstore.load_config()
    st = _read_status()
    running_now = running(st)
    found = find_cameras(cfg)
    bad = invalid_patterns(cfg["patterns"])
    refused = refused_cameras(cfg)
    use = camstore.usage()
    roles = {}
    for role in ROLES:
        r = (st or {}).get("roles", {}).get(role, {}) if running_now else {}
        if running_now:
            why = r.get("error")
        elif role in bad:
            why = f"bad pattern in omacar-cameras.json: {bad[role]!r}"
        elif role in found:
            why = "recorder off"
        elif role in refused:
            why = refused[role]
        else:
            why = "no camera"
        roles[role] = {
            "device": r.get("device") or found.get(role),
            "mode": r.get("mode"), "sim": bool(r.get("sim")),
            "recording": bool(r.get("recording")), "stalled": bool(r.get("stalled")),
            "starting": bool(r.get("starting")), "live": bool(r.get("live")),
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
        state = ("REC" if r["recording"] else
                 "STALLED" if r["stalled"] else
                 "STARTING" if r["starting"] else "---")
        print(f"  {role:<6} {state:<7} {mode:<22} {fps:<9} "
              f"{r['clips']:>4} clips  {os.path.basename(r['device'] or '') or '(none)'}")
        if r["error"]:
            print(f"         {r['error']}")
    print()
    return 0


# ---- the demo's cameras --------------------------------------------------------
#
# The meetup demo has no cameras and no car. `cams.py demo` plays footage from
# files as if the three cameras were live, and leaves behind exactly what the
# recorder above leaves, so the app's own Cameras tab, Home's Dashcams card and
# overview() need no demo code at all:
#
#   clips   the last 50 minutes as one-minute pieces, named as the recorder
#           names them, in the videos folder; then one more each minute
#   live    <role>.jpg, 640 px wide at 10 fps, in the runtime folder
#   status  status.json, every second, in write_status()'s shape
#
# It writes only where OMACAR_VIDEOS and XDG_RUNTIME_DIR point, and refuses to
# run unless both are inside a folder called omacar-demo: pointed at the live
# app's own folders it would overwrite the real recorder's pictures and status.
# It opens no video device, and nothing above that finds one is called from here.

DEMO_MARK = "omacar-demo"
DEMO_MINUTES = 50
DEMO_ROLES = ROLES
DEMO_SEG_TRIES = (1.15, 1.6, 2.6)   # how far past 50 minutes to ask ffmpeg to run, in turn
DEMO_RETRY_SECS = 5                 # a feed that dies is started again, but not more often than this
DEMO_SCENE_FRESH = 10               # a live.json older than this is a world that has stopped
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_CLIPS = os.path.join(_ROOT, "share", "assets", "private", "demo", "clips")


class DemoRefused(RuntimeError):
    """The demo was asked to run somewhere it must not write."""


def demo_refusal():
    """Why `cams.py demo` must not run with this environment, or None.

    Both folders it writes must be named by the environment and be inside a
    folder called omacar-demo, by the name given and by where it really leads,
    so neither a path that climbs out of it nor a link into the live app's
    folders gets by."""
    for var in ("OMACAR_VIDEOS", "XDG_RUNTIME_DIR"):
        val = os.environ.get(var)
        if not val:
            return (f"{var} is not set; the demo writes only under a folder called {DEMO_MARK}, "
                    f"which the demo's own launcher sets")
        if DEMO_MARK not in val or DEMO_MARK not in os.path.realpath(os.path.expanduser(val)):
            return (f"{var}={val} is not inside a folder called {DEMO_MARK}; run from there, the demo "
                    f"would overwrite the live app's cameras")
    return None


def probe_clip(path):
    """{fmt, w, h, fps} of a clip's picture, from ffprobe; None if it cannot be read."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=codec_name,width,height,avg_frame_rate", "-of", "json", path],
                           capture_output=True, text=True, timeout=20)
        s = json.loads(r.stdout)["streams"][0]
        num, _, den = str(s.get("avg_frame_rate") or "0/1").partition("/")
        den = float(den or 1)
        fps = float(num) / den if den else 0.0
        name = str(s["codec_name"])
        return {"fmt": {"h264": "H264", "mjpeg": "MJPG"}.get(name, name.upper()),
                "w": int(s["width"]), "h": int(s["height"]), "fps": round(fps, 2) or 30.0}
    except (OSError, ValueError, KeyError, IndexError, subprocess.TimeoutExpired):
        return None


def demo_frame_args(src, out):
    """The live picture: `src` on a loop at its own speed, cut to 10 fps and
    640 px wide, one JPEG rewritten whole (atomic_writing renames it into
    place, so a reader never sees half of one). No sound. -y, because the
    picture is already there when a feed restarts: without it ffmpeg stops to
    ask whether to overwrite it, and with no stdin it just stops."""
    return ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-nostats", "-y",
            "-stream_loop", "-1", "-re", "-i", src, "-an",
            "-vf", f"fps={LIVE_FPS},scale={LIVE_WIDTH}:-2", "-q:v", "5",
            "-f", "image2", "-update", "1", "-atomic_writing", "1", out]


def demo_segment_args(src, pattern, secs, clip_secs=camstore.CLIP_SECS):
    """`secs` of `src` on a loop, copied (never re-encoded, and with no sound
    track) into one-minute fragmented-MP4 pieces, as the recorder writes them.
    A piece ends at the first keyframe past its minute, so each is a minute
    and a little."""
    return ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-nostats", "-y",
            "-stream_loop", "-1", "-i", src, "-t", str(secs), "-map", "0:v:0", "-an", "-c", "copy",
            "-f", "segment", "-segment_time", str(clip_secs), "-segment_format", "mp4",
            "-segment_format_options", "movflags=+frag_keyframe+empty_moov+default_base_moof",
            "-reset_timestamps", "1", pattern]


def demo_live_path():
    """The demo world's live.json, which says which scene it is in."""
    base = os.environ.get("OMACAR_STATE") or os.path.join(
        os.path.expanduser(os.environ.get("XDG_STATE_HOME") or "~/.local/state"), "omacar")
    return os.path.join(base, "live.json")


class DemoFeed:
    """One role's ffmpeg: a clip on a loop, cut into live pictures. Restarting
    it on another clip (the drowsy cabin) is stop and start."""

    def __init__(self, role, src, popen):
        self.role, self.src, self.popen = role, src, popen
        self.proc = None
        self.started = None
        self.last_frame = None
        self.retry_at = 0.0
        self.last_line = None

    def start(self, now, keep_frame=False):
        """Start it. Unless `keep_frame`, the last picture goes: it is the
        last of a feed that has died, and would read as fresh to nobody."""
        if not keep_frame:
            self.last_frame = None
            try:
                os.remove(live_path(self.role))
            except OSError:
                pass
        self.proc = self.popen(demo_frame_args(self.src, live_path(self.role)), stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.started = now
        self.last_line = None
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc):
        try:
            for raw in proc.stderr:
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    self.last_line = line[:300]
        except Exception:                                      # noqa: BLE001
            pass

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def poll_frame(self):
        try:
            self.last_frame = os.stat(live_path(self.role)).st_mtime
        except OSError:
            pass

    def stalled(self, now):
        if not self.alive():
            return False
        if self.last_frame is None:
            return now - (self.started or now) > START_GRACE
        return now - self.last_frame > STALL_SECS

    def terminate(self):
        if self.alive():
            self.proc.terminate()

    def reap(self):
        if self.proc is None:
            return
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass

    def kill(self):
        if self.alive():
            self.proc.kill()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass

    def switch(self, src, now):
        """The same role, on another clip. The picture in place stays until the
        new one writes over it, so the tab sees no gap."""
        self.terminate()
        self.reap()
        self.src = src
        self.start(now, keep_frame=True)

    def status(self, now, mode):
        """What Camera.status() says, for a feed that is a clip."""
        alive, lf = self.alive(), self.last_frame
        stalled = self.stalled(now)
        recording = alive and lf is not None and not stalled
        starting = alive and not recording and not stalled
        if stalled:
            error = f"stalled: no picture for {int(now - (lf or self.started or now))} s"
        elif starting:
            error = f"starting: no picture yet ({int(now - (self.started or now))} s)"
        elif not alive:
            error = self.last_line or "the footage stopped; starting it again"
        else:
            error = None
        return {"device": os.path.basename(self.src), "mode": mode, "sim": False,
                "recording": recording, "stalled": stalled, "starting": starting,
                "live": recording and now - lf < 3,
                "fps": mode["fps"] if mode and recording else None,
                "since": self.started, "error": error}


class DemoRecorder:
    """Every role's footage, the clips on disk, and the drowsy cabin."""

    def __init__(self, src_dir, minutes=DEMO_MINUTES, popen=subprocess.Popen):
        self.src_dir, self.minutes, self.popen = src_dir, minutes, popen
        self.sources = {}
        for role in DEMO_ROLES:
            path = os.path.join(src_dir, f"{role}.mp4")
            if os.path.isfile(path):
                self.sources[role] = path
        drowsy = os.path.join(src_dir, "cabin-drowsy.mp4")
        self.drowsy_src = drowsy if os.path.isfile(drowsy) else None
        self.modes, self.feeds = {}, {}
        # Per role, from seed(): the pieces the loop is made from, when the next
        # minute's clip begins, and which piece is next. `pool` is set last:
        # roll() walks it while seed() is still laying down the next role.
        self.pool, self.next_start, self.turn = {}, {}, {}
        self.problems = {}
        self.note = None
        self.drowsy = False
        self.stopping = threading.Event()
        self._building = None
        self._seeder = None
        self._next_slow = 0.0

    # -- the guard
    def _guard(self):
        why = demo_refusal()
        if why:
            raise DemoRefused(why)

    # -- the clips
    def _wipe(self, root):
        """The last run's clips, its pieces, its locked events and its list of
        them: a start is a fresh timeline. Only what this writes."""
        with camstore._lock(root):
            for role in DEMO_ROLES:
                d = os.path.join(root, role)
                for name in camstore._ls(d):
                    if camstore.CLIP_RE.match(name):
                        try:
                            os.remove(os.path.join(d, name))
                        except OSError:
                            pass
            for junk in (".pool", "locked"):
                shutil.rmtree(os.path.join(root, junk), ignore_errors=True)
            try:
                os.remove(os.path.join(root, "events.json"))
            except OSError:
                pass

    def _segments(self, role, src, root):
        """The pieces, oldest first: exactly `minutes` whole ones, or [].
        Asks ffmpeg for more than 50 minutes, and more again if a clip's
        keyframes fall far apart and the pieces run long."""
        pool = os.path.join(root, ".pool", role)
        for factor in DEMO_SEG_TRIES:
            shutil.rmtree(pool, ignore_errors=True)
            os.makedirs(pool)
            secs = int(self.minutes * camstore.CLIP_SECS * factor) + 30
            self._building = subprocess.Popen(
                demo_segment_args(src, os.path.join(pool, "seg%04d.mp4"), secs),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            _, err = self._building.communicate()
            code, self._building = self._building.returncode, None
            if self.stopping.is_set():
                return []
            if code != 0:
                tail = err.decode("utf-8", "replace").strip().splitlines()[-1:] or ["no message"]
                self.problems[role] = f"{role}: could not cut the clip into minutes ({tail[0][:160]})"
                return []
            segs = sorted(os.path.join(pool, n) for n in os.listdir(pool) if n.startswith("seg"))
            if len(segs) > self.minutes:           # the last one may be short: it is not used
                for extra in segs[self.minutes:]:
                    os.remove(extra)
                return segs[:self.minutes]
        self.problems[role] = f"{role}: the clip does not fill {self.minutes} minutes"
        return []

    @staticmethod
    def _place(src, dst):
        """`src` as a regular file at `dst`, whole from the moment it has the
        name: a link where the disk allows one (it costs nothing), else a copy."""
        tmp = os.path.join(os.path.dirname(dst), ".placing-" + os.path.basename(dst))
        try:
            os.link(src, tmp)
        except OSError:
            shutil.copyfile(src, tmp)
        os.replace(tmp, dst)

    def _note(self):
        self.note = "; ".join(self.problems.values()) or None

    def seed(self, now=None):
        """Lay out the last `minutes` minutes of clips, a minute apart, the
        newest begun half a minute ago. {role: clips laid down}."""
        self._guard()
        now = time.time() if now is None else now
        root = camstore.videos()
        self.note = f"laying out the last {self.minutes} minutes of clips"
        self.problems.clear()
        self._wipe(root)
        newest = int(now) - camstore.CLIP_SECS // 2
        got = {}
        for role, src in self.sources.items():
            if self.stopping.is_set():
                break
            segs = self._segments(role, src, root)
            if not segs:
                continue
            dest = os.path.join(root, role)
            os.makedirs(dest, exist_ok=True)
            for i, seg in enumerate(segs):
                t = newest - camstore.CLIP_SECS * (len(segs) - 1 - i)
                self._place(seg, os.path.join(dest, camstore.clip_name(t)))
            self.turn[role] = 0
            self.next_start[role] = newest + camstore.CLIP_SECS
            self.pool[role] = segs
            got[role] = len(segs)
        self._note()
        return got

    def _expire(self, role, newest, root):
        cutoff = newest - (self.minutes - 1) * camstore.CLIP_SECS
        d = os.path.join(root, role)
        with camstore._lock(root):
            for name in camstore._ls(d):
                start = camstore.clip_start(name)
                if start is not None and start < cutoff:
                    try:
                        os.remove(os.path.join(d, name))
                    except OSError:
                        pass

    def roll(self, now):
        """The clips that have begun since the last call: one a minute a role,
        the loop's pieces round again, and the oldest go, so the timeline is
        always the last 50 minutes. Clips an event has locked are elsewhere,
        and stay. Returns how many were laid down."""
        root = camstore.videos()
        added = 0
        for role in list(self.pool):
            pool = self.pool[role]
            behind = int((now - self.next_start[role]) // camstore.CLIP_SECS)
            if behind > self.minutes:              # asleep for a long while: not hours of clips
                self.next_start[role] += (behind - self.minutes) * camstore.CLIP_SECS
            while now >= self.next_start[role]:
                t = self.next_start[role]
                dst = os.path.join(root, role, camstore.clip_name(t))
                if not os.path.exists(dst):
                    self._place(pool[self.turn[role] % len(pool)], dst)
                    added += 1
                self.turn[role] += 1
                self.next_start[role] = t + camstore.CLIP_SECS
                self._expire(role, t, root)
        return added

    # -- the live pictures
    def start(self, now=None):
        """One ffmpeg a role, each cutting its clip into live pictures."""
        self._guard()
        now = time.time() if now is None else now
        os.makedirs(run_dir(), exist_ok=True)
        for role, src in self.sources.items():
            self.modes[role] = probe_clip(src)
            feed = DemoFeed(role, src, self.popen)
            feed.start(now)
            self.feeds[role] = feed

    def _scene(self, now):
        path = demo_live_path()
        try:
            if now - os.stat(path).st_mtime > DEMO_SCENE_FRESH:
                return None
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            return None
        demo = doc.get("demo") if isinstance(doc, dict) else None
        return demo.get("scene") if isinstance(demo, dict) else None

    def _follow_scene(self, now):
        """The cabin shows the drowsy clip while the world's scene is
        'drowsy', and its own again after. Only that one ffmpeg restarts."""
        feed = self.feeds.get("cabin")
        if feed is None or self.drowsy_src is None:
            return
        want = self._scene(now) == "drowsy"
        if want != self.drowsy:
            self.drowsy = want
            feed.switch(self.drowsy_src if want else self.sources["cabin"], now)

    def write_status(self, now=None):
        doc = {"pid": os.getpid(), "t": time.time() if now is None else now, "sim": False, "demo": True,
               "note": self.note, "children": {r: f.proc.pid for r, f in self.feeds.items() if f.alive()},
               "roles": {}}
        t = time.time()
        for role in DEMO_ROLES:
            feed = self.feeds.get(role)
            doc["roles"][role] = feed.status(t, self.modes.get(role)) if feed else {
                "device": None, "mode": None, "sim": False, "recording": False, "stalled": False,
                "starting": False, "live": False, "fps": None, "since": None,
                "error": f"no demo clip: {role}.mp4"}
        os.makedirs(run_dir(), exist_ok=True)
        tmp = status_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        os.replace(tmp, status_path())

    def _safely(self, what, fn):
        try:
            fn()
        except Exception as e:                                 # noqa: BLE001
            self.problems["demo"] = f"{what} failed: {type(e).__name__}: {e}"[:300]
            self._note()

    def tick(self, now=None):
        """One pass: follow the scene, mind every feed, and once a second lock
        what an event asked for, lay down the minute's clip and write the status."""
        now = time.time() if now is None else now
        self._safely("the scene", lambda: self._follow_scene(now))
        for feed in list(self.feeds.values()):
            feed.poll_frame()
            if feed.stalled(now):
                feed.kill()
                feed.retry_at = 0.0
            if not feed.alive() and now >= feed.retry_at:
                feed.retry_at = now + DEMO_RETRY_SECS
                self._safely("a feed", lambda f=feed: f.start(now))
        if now >= self._next_slow:
            self._next_slow = now + 1
            self._safely("locking", lambda: camstore.settle(now=now))
            self._safely("the minute's clip", lambda: self.roll(now))
            self._safely("the status file", lambda: self.write_status())

    def stop(self):
        """Every ffmpeg ends, the pieces the loop was made from go, and so does
        the status file. The clips stay: the tab plays them."""
        self.stopping.set()
        building = self._building
        if building is not None:
            try:
                building.terminate()
            except OSError:
                pass
        if self._seeder is not None:
            self._seeder.join(timeout=10)
        for feed in self.feeds.values():
            feed.terminate()
        for feed in self.feeds.values():
            feed.reap()
        shutil.rmtree(os.path.join(camstore.videos(), ".pool"), ignore_errors=True)
        try:
            os.remove(status_path())
        except OSError:
            pass

    def _seed_quietly(self):
        try:
            self.seed()
        except Exception as e:                                 # noqa: BLE001
            self.problems["seed"] = f"laying out the clips failed: {type(e).__name__}: {e}"[:300]
            self._note()

    def run(self):
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, lambda *_: self.stopping.set())
        self.start()
        # The pictures are live from the first second; the clips follow as
        # they are cut, and the status says so meanwhile.
        self.note = f"laying out the last {self.minutes} minutes of clips"
        self._seeder = threading.Thread(target=self._seed_quietly, daemon=True)
        self._seeder.start()
        self.write_status()
        while not self.stopping.wait(0.25):
            self.tick()
        self.stop()


def demo_main(args):
    src = DEMO_CLIPS
    rest = list(args)
    while rest:
        a = rest.pop(0)
        if a == "--from" and rest:
            src = rest.pop(0)
        elif a.startswith("--from="):
            src = a[len("--from="):]
        else:
            print("usage: cams.py demo [--from DIR]   (DIR holds front.mp4, rear.mp4, cabin.mp4 "
                  "and, for the drowsy moment, cabin-drowsy.mp4)", file=sys.stderr)
            return 2
    why = demo_refusal()
    if why:
        print(f"omacar: cams demo refused: {why}", file=sys.stderr)
        return 2
    rec = DemoRecorder(src)
    if not rec.sources:
        print(f"omacar: no footage in {src}: it needs front.mp4, rear.mp4 and cabin.mp4", file=sys.stderr)
        return 1
    missing = [r for r in DEMO_ROLES if r not in rec.sources]
    if missing:
        print(f"omacar: no footage for {', '.join(missing)} in {src}; those cameras will read empty",
              file=sys.stderr)
    st = _read_status()
    if running(st) and st.get("pid") != os.getpid():
        print(f"omacar: the demo's cameras are already running (pid {st['pid']})", file=sys.stderr)
        return 1
    rec.run()
    return 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "status":
        return _print_status()
    if cmd in ("on", "off"):
        how = ["enable", "--now"] if cmd == "on" else ["disable", "--now"]
        return subprocess.run(["systemctl", "--user", *how, "omacar-cams.service"]).returncode
    if cmd == "demo":
        return demo_main(argv[2:])
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
