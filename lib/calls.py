#!/usr/bin/env python3
"""Hands-free calls through the car's AUX: the tablet as the car kit, the phone as the gateway.

Owner, 2026-10-17: the phone is an iPhone. The tablet is the HANDS-FREE unit (HFP
"HF" role); the phone is the Audio Gateway. Calls ring on the screen, are
answered and ended with a thumb, and their audio comes out of the same AUX
cable the music uses, with the Surface's microphone going the other way.

    omacar calls status      what calls.json says now
    omacar calls pair        walk through pairing the phone with bluetoothctl
    omacar calls use ADDR    remember which paired phone to follow
    omacar calls setup       write the WirePlumber file that hands the HF role to oFono
    omacar calls run        hold the link (omacar-calls.service)
    omacar calls sim [s]    a simulated incoming call, answered, ended; every
                            screen says "Simulated" while it runs
    omacar calls forget     erase the recent-calls list

WHY oFONO, AND NOT PIPEWIRE ALONE (doc/calls.md has the sources). PipeWire's
native bluez5 backend can be a hands-free unit (bluez5.roles has hfp_hf and it
is on by default), but all it does with the phone is carry the audio. The call
control it publishes on D-Bus (org.pipewire.Telephony, 2025) is for the OTHER
role, PipeWire acting as the phone's side; as the HF it exposes no answer, no
hang-up, no caller ID, no call state. oFono's hfp_hf_bluez5 plugin is the
hands-free unit with all of that: org.ofono.VoiceCallManager and
org.ofono.VoiceCall (state, LineIdentification, Answer, Hangup). So the split
is: oFono owns the service-level link and the AT commands, and PipeWire (with
bluez5.hfphsp-backend = "ofono") only receives the SCO audio link oFono hands
it. Exactly one program may register the HFP profile with BlueZ, which is why
the backend setting is not optional.

THE STATE MACHINE IS PURE. apply_event() takes D-Bus-shaped events (CallAdded,
CallRemoved, PropertyChanged, Snapshot) and a clock, and test/calls_test.py
runs it with no bus. The real link polls oFono over busctl (no D-Bus library
to install, no monitor privilege) and turns what it reads into a Snapshot
event; the simulator speaks the same events.

WHAT IS NEVER DONE. No ringtone: a child sleeps near the tablet, so an incoming
call is silent and visible (the phone itself still rings, if the owner lets
it). Nothing here reads the phone's contacts. The number and name shown are
what the phone sent with the call (+CLIP, and +CNAP when it sends one).
Dialing is only from the recent-calls list and only when parked; the number
never comes from the request, only from an entry already in the list.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STATE_DIR = os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")
CONFIG_DIR = os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME", "~/.config") + "/omacar")
STATE = os.path.join(STATE_DIR, "calls.json")
ASK = os.path.join(STATE_DIR, "calls-ask.json")
RECENT = os.path.join(STATE_DIR, "calls-recent.json")
CONFIG = os.path.join(CONFIG_DIR, "calls.json")
LIVE = os.path.join(STATE_DIR, "live.json")

STALE = 6.0              # s without a write: whoever was writing has stopped
ASK_FRESH = 15.0         # s an unanswered request stays good
ENDED_LINGER = 4.0       # s "Call ended" stays on the bar
RECENT_KEEP = 20
POLL_S = 0.5
MOVING_FRESH_S = 30      # a speed sample this young counts for "moving"

OFONO = "org.ofono"
IF_MGR = "org.ofono.VoiceCallManager"
IF_CALL = "org.ofono.VoiceCall"
IF_VOL = "org.ofono.CallVolume"
IF_MODEM = "org.ofono.Modem"

LIVE_STATES = ("incoming", "waiting", "dialing", "alerting", "active", "held")


# ======================================================================================
# The state machine (pure)
# ======================================================================================

def blank_machine(recent=None):
    return {"calls": {}, "recent": list(recent or []), "next_id": 1 + max(
        [r.get("id", 0) for r in (recent or [])] or [0]),
        "link": {"modem": None, "phone": None, "online": None},
        "volume": {"speaker": None, "mic": None}, "rejected": [], "last_end": None}


def _direction(state):
    return "out" if state in ("dialing", "alerting") else "in"


def _add_call(m, path, props, now):
    state = str(props.get("State") or "incoming")
    call = {"path": path, "state": state, "dir": _direction(state),
            "number": str(props.get("LineIdentification") or ""),
            "name": str(props.get("Name") or ""),
            "added_at": now, "answered_at": now if state == "active" else None}
    m["calls"][path] = call
    return call


def _set_state(call, state, now):
    call["state"] = state
    if state == "active" and call["answered_at"] is None:
        call["answered_at"] = now


def _finish(m, path, now):
    call = m["calls"].pop(path, None)
    if call is None:
        return None
    declined = path in m["rejected"]
    if declined:
        m["rejected"].remove(path)
    if call["dir"] == "in":
        outcome = "declined" if declined else "answered" if call["answered_at"] else "missed"
    else:
        outcome = "outgoing" if call["answered_at"] else "unanswered"
    secs = int(max(0, now - call["answered_at"])) if call["answered_at"] else 0
    row = {"id": m["next_id"], "number": call["number"], "name": call["name"],
           "dir": call["dir"], "outcome": outcome, "at": call["added_at"], "secs": secs}
    m["next_id"] += 1
    m["recent"].insert(0, row)
    del m["recent"][RECENT_KEEP:]
    m["last_end"] = dict(row, ended_at=now)
    return row


def mark_rejected(m, path):
    if path not in m["rejected"]:
        m["rejected"].append(path)


def apply_event(m, ev, now):
    """Fold one D-Bus-shaped event into the machine. Unknown events change nothing.

    {"member": "CallAdded", "path": P, "props": {"State": "incoming", "LineIdentification": "+1...", "Name": ""}}
    {"member": "CallRemoved", "path": P}
    {"member": "PropertyChanged", "interface": "org.ofono.VoiceCall", "path": P, "name": "State", "value": "active"}
    {"member": "PropertyChanged", "interface": "org.ofono.CallVolume", "name": "SpeakerVolume", "value": 60}
    {"member": "ModemAdded", "path": P, "props": {"Name": "James's iPhone", "Online": true}} / ModemRemoved
    {"member": "Snapshot", "calls": {P: props}}      what polling GetCalls produces
    """
    kind = ev.get("member")
    path = ev.get("path")
    if kind == "Snapshot":
        seen = ev.get("calls") or {}
        for p, props in seen.items():
            if p not in m["calls"]:
                _add_call(m, p, props, now)
            else:
                st = str(props.get("State") or m["calls"][p]["state"])
                if st == "disconnected":
                    _finish(m, p, now)
                    continue
                _set_state(m["calls"][p], st, now)
                if props.get("LineIdentification"):
                    m["calls"][p]["number"] = str(props["LineIdentification"])
                if props.get("Name"):
                    m["calls"][p]["name"] = str(props["Name"])
        for p in [p for p in m["calls"] if p not in seen]:
            _finish(m, p, now)
    elif kind == "CallAdded":
        props = ev.get("props") or {}
        if path not in m["calls"] and props.get("State") != "disconnected":
            _add_call(m, path, props, now)
    elif kind == "CallRemoved":
        _finish(m, path, now)
    elif kind == "PropertyChanged":
        iface, name, value = ev.get("interface"), ev.get("name"), ev.get("value")
        if iface == IF_CALL and path in m["calls"]:
            if name == "State":
                if value == "disconnected":
                    _finish(m, path, now)
                else:
                    _set_state(m["calls"][path], str(value), now)
            elif name == "LineIdentification":
                m["calls"][path]["number"] = str(value or "")
            elif name == "Name":
                m["calls"][path]["name"] = str(value or "")
        elif iface == IF_VOL:
            if name == "SpeakerVolume":
                m["volume"]["speaker"] = int(value)
            elif name == "MicrophoneVolume":
                m["volume"]["mic"] = int(value)
        elif iface == IF_MODEM and path == m["link"]["modem"]:
            if name == "Online":
                m["link"]["online"] = bool(value)
            elif name == "Name":
                m["link"]["phone"] = str(value)
    elif kind == "ModemAdded":
        props = ev.get("props") or {}
        m["link"].update(modem=path, phone=props.get("Name"), online=props.get("Online"))
    elif kind == "ModemRemoved":
        if path == m["link"]["modem"]:
            m["link"].update(modem=None, online=None)
            for p in list(m["calls"]):
                _finish(m, p, now)    # the link going away ends every call on it
    return m


def _pick(m, states):
    live = [c for c in m["calls"].values() if c["state"] in states]
    live.sort(key=lambda c: c["added_at"])
    return live[0] if live else None


def _public_call(c, now):
    if c is None:
        return None
    return {"path": c["path"], "state": c["state"], "dir": c["dir"],
            "number": c["number"], "name": c["name"], "who": who_of(c["number"], c["name"]),
            "answered_at": c["answered_at"],
            "secs": int(max(0, now - c["answered_at"])) if c["answered_at"] else 0}


def who_of(number, name):
    """What the screen calls the caller. Never invented: no name and no number
    is 'Unknown caller', which is what the phone told us."""
    return (name or "").strip() or format_number(number) or "Unknown caller"


def format_number(n):
    n = (n or "").strip()
    d = re.sub(r"\D", "", n)
    if n.startswith("+1") and len(d) == 11:
        return f"+1 ({d[1:4]}) {d[4:7]}-{d[7:]}"
    if len(d) == 10 and not n.startswith("+"):
        return f"({d[:3]}) {d[3:6]}-{d[6:]}"
    return n


def summarize(m, now):
    """Phase and the calls that matter, from the machine. Incoming outranks
    active, active outranks held, held outranks dialing; a call-waiting
    arrives beside an active one as `waiting` and does not change the phase."""
    incoming = _pick(m, ("incoming",))
    waiting = _pick(m, ("waiting",))
    active = _pick(m, ("active",))
    held = _pick(m, ("held",))
    dialing = _pick(m, ("dialing", "alerting"))
    end = m["last_end"]
    ended = None
    if incoming:
        phase, call = "incoming", incoming
    elif active:
        phase, call = "active", active
    elif held:
        phase, call = "held", held
    elif dialing:
        phase, call = "dialing", dialing
    elif end and now - end["ended_at"] < ENDED_LINGER and end["outcome"] in ("answered", "outgoing"):
        phase, call, ended = "ended", None, end
    else:
        phase, call = "idle", None
    return {"phase": phase, "call": _public_call(call, now),
            "waiting": _public_call(waiting, now),
            "ended": ended}


# ---- what an ask means (pure) ----------------------------------------------------------

ASKS = ("answer", "hangup", "reject", "mute", "volume", "dial")


def plan_ask(m, a, moving=False):
    """Operations for one request. Ops: ("call", path, interface, method, args),
    ("mute", bool), ("volume", 0..100), ("dial", number), ("refuse", why)."""
    act = a.get("action")
    incoming = _pick(m, ("incoming",)) or _pick(m, ("waiting",))
    other = _pick(m, ("active", "held"))
    modem = m["link"]["modem"]
    if act == "answer":
        if not incoming:
            return []
        if incoming["state"] == "waiting" and other and modem:
            return [("call", modem, IF_MGR, "HoldAndAnswer", [])]
        return [("call", incoming["path"], IF_CALL, "Answer", [])]
    if act == "reject":
        if not incoming:
            return []
        mark_rejected(m, incoming["path"])
        return [("call", incoming["path"], IF_CALL, "Hangup", [])]
    if act == "hangup":
        target = _pick(m, ("active",)) or _pick(m, ("dialing", "alerting")) or _pick(m, ("held",))
        if target:
            return [("call", target["path"], IF_CALL, "Hangup", [])]
        return plan_ask(m, {"action": "reject"}, moving)
    if act == "mute":
        return [("mute", bool(a.get("value")))]
    if act == "volume":
        return [("volume", int(a.get("value")))]
    if act == "dial":
        if moving:
            return [("refuse", "Call back is for when you have stopped")]
        row = next((r for r in m["recent"] if r["id"] == a.get("id")), None)
        if not row or not row["number"]:
            return [("refuse", "That call has no number to ring")]
        if not modem:
            return [("refuse", "The phone is not connected")]
        return [("dial", row["number"])]
    return []


# ---- audio routing (pure parts) ---------------------------------------------------------

def mac_of(modem_path):
    mt = re.search(r"dev_([0-9A-Fa-f_]{17})", modem_path or "")
    return mt.group(1).upper() if mt else None


def audio_nodes(pwdump, mac):
    """(phone's voice source, phone's microphone-input sink) node names from a
    `pw-dump` list, for the phone whose MAC (with underscores) is `mac`."""
    src = snk = None
    for o in pwdump or []:
        props = ((o.get("info") or {}).get("props")) or {}
        name = props.get("node.name") or ""
        if not mac or mac not in name.upper():
            continue
        cls = props.get("media.class")
        if cls == "Audio/Source" and src is None:
            src = name
        elif cls == "Audio/Sink" and snk is None:
            snk = name
    return (src, snk) if src and snk else None


def loopback_cmds(src, snk, mic=None):
    """Two pw-loopback processes. The caller's voice goes to the DEFAULT sink
    (no -P), so WirePlumber's smart-filter policy sends it through the AUX
    equalizer exactly as it does music; the microphone goes to the phone."""
    voice = ["pw-loopback", "-n", "omacar-call-voice", "-l", "60", "-C", src]
    talk = ["pw-loopback", "-n", "omacar-call-mic", "-l", "60", "-c", "1", "-P", snk]
    if mic:
        talk[-2:-2] = ["-C", mic]
    return [voice, talk]


def routing_wanted(phase):
    return phase in ("active", "held", "dialing")


# ---- reading oFono over busctl (pure parsing) -------------------------------------------

def unvariant(x):
    """busctl --json=short wraps every variant as {"type": "s", "data": ...}."""
    if isinstance(x, dict):
        if set(x) == {"type", "data"}:
            return unvariant(x["data"])
        return {k: unvariant(v) for k, v in x.items()}
    if isinstance(x, list):
        return [unvariant(v) for v in x]
    return x


def parse_modems(reply):
    """GetModems -> [(path, props)] for the Bluetooth hands-free ones."""
    data = unvariant((reply or {}).get("data", [[]]))[0] if isinstance(reply, dict) else []
    return [(p, props) for p, props in data if (props or {}).get("Type") == "hfp"]


def parse_calls(reply):
    data = unvariant((reply or {}).get("data", [[]]))[0] if isinstance(reply, dict) else []
    return {p: props for p, props in data}


def parse_volume(reply):
    return unvariant((reply or {}).get("data", [{}]))[0] if isinstance(reply, dict) else {}


# ======================================================================================
# The file the app reads
# ======================================================================================

def _config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def write(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    state["at"] = time.time()
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, STATE)


def compose(m, source, now, muted=False, note=None, mic=None):
    s = summarize(m, now)
    link = m["link"]
    connected = bool(link["modem"]) and link["online"] is not False
    s.update(source=source, recent=m["recent"], muted=bool(muted), note=note, mic=mic,
             volume=m["volume"],
             link={"connected": connected, "phone": link["phone"]})
    return s


def read_public(now=None):
    """For the API. {"present": False} when no link or simulator has ever written.
    A call is never kept past the writer: a file older than STALE shows no call
    at all, because 'ringing' from a minute ago is not ringing."""
    now = time.time() if now is None else now
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return {"present": False}
    st["present"] = True
    st["age"] = max(0.0, now - float(st.get("at", 0)))
    if st["age"] > STALE:
        st.update(phase="idle", call=None, waiting=None, ended=None, stale=True)
        st["link"] = dict(st.get("link") or {}, connected=False)
        return st
    st["stale"] = False
    # Durations are the server's clock, not the page's.
    for key in ("call", "waiting"):
        c = st.get(key)
        if c and c.get("answered_at"):
            c["secs"] = int(max(0, now - c["answered_at"]))
    return st


def load_recent():
    try:
        with open(RECENT, encoding="utf-8") as f:
            rows = json.load(f)
        return [r for r in rows if isinstance(r, dict)][:RECENT_KEEP]
    except (OSError, ValueError):
        return []


def save_recent(rows):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = RECENT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f)
    os.replace(tmp, RECENT)


# ---- asks ---------------------------------------------------------------------------------

def moving(now=None):
    """True when the car's own speed, sampled recently, is above zero, or the
    adapter is handed off (the car may be moving and we cannot see). Unknown
    is not 'stopped' for the one thing parked-only protects: dialing."""
    now = time.time() if now is None else now
    try:
        with open(LIVE, encoding="utf-8") as f:
            snap = json.load(f)
        t = snap.get("t") if isinstance(snap.get("t"), (int, float)) else os.stat(LIVE).st_mtime
    except (OSError, ValueError):
        return False         # no car link at all: a desk, or a car that is off
    if snap.get("handover") or snap.get("status") == "yielded":
        return True
    speed = (snap.get("values") or {}).get("SPEED")
    return isinstance(speed, (int, float)) and speed > 0 and now - t <= MOVING_FRESH_S


def take_ask():
    try:
        with open(ASK, encoding="utf-8") as f:
            a = json.load(f)
        os.unlink(ASK)
    except (OSError, ValueError):
        return None
    if time.time() - float(a.get("at", 0)) > ASK_FRESH:
        return None
    return a


def ask(action, value=None, call_id=None):
    """What the API calls. Validated here; carried out by whichever loop runs.
    Everything but dial is allowed while moving: that is the point of hands-free."""
    if action not in ASKS:
        raise ValueError(f"{action!r} is not something OmaCar does with a call")
    a = {"action": action, "at": time.time()}
    if action == "mute":
        a["value"] = bool(value)
    elif action == "volume":
        v = int(value)
        if not 0 <= v <= 100:
            raise ValueError("volume is 0 to 100")
        a["value"] = v
    elif action == "dial":
        if moving():
            raise PermissionError("Call back is for when you have stopped")
        a["id"] = int(call_id)
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = ASK + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(a, f)
    os.replace(tmp, ASK)
    return {"asked": action}


# ======================================================================================
# The real link: oFono over busctl
# ======================================================================================

def _busctl(*args, timeout=2):
    out = subprocess.run(["busctl", "--system", "--json=short", *args],
                         capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "busctl failed")
    return json.loads(out.stdout) if out.stdout.strip() else {}


def _call(path, iface, method, sig="", args=()):
    cmd = ["call", OFONO, path, iface, method]
    if sig:
        cmd += [sig, *[str(x) for x in args]]
    return _busctl(*cmd)


class Router:
    """The two pw-loopbacks that carry call audio. Stops only its own PIDs."""

    def __init__(self):
        self.procs = []

    def running(self):
        self.procs = [p for p in self.procs if p.poll() is None]
        return bool(self.procs)

    def start(self, modem, mic):
        out = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=3)
        nodes = audio_nodes(json.loads(out.stdout or "[]"), mac_of(modem))
        if not nodes:
            return "the phone's call audio is not in PipeWire yet"
        for cmd in loopback_cmds(nodes[0], nodes[1], mic):
            self.procs.append(subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        return None

    def stop(self):
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
        for p in self.procs:
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.kill()
        self.procs = []


def _wpctl_mute(mic, on):
    subprocess.run(["wpctl", "set-mute", mic or "@DEFAULT_AUDIO_SOURCE@", "1" if on else "0"],
                   capture_output=True, timeout=2)


def _poll(m, now):
    """One look at oFono, folded into the machine as events."""
    want = (_config().get("phone") or "").upper().replace(":", "_")
    modems = [(p, pr) for p, pr in parse_modems(_busctl("call", OFONO, "/", "org.ofono.Manager", "GetModems"))
              if not want or want in p.upper()]
    if not modems:
        if m["link"]["modem"]:
            apply_event(m, {"member": "ModemRemoved", "path": m["link"]["modem"]}, now)
        return "No phone is connected as hands-free: omacar calls pair"
    path, props = modems[0]
    if m["link"]["modem"] != path:
        apply_event(m, {"member": "ModemAdded", "path": path, "props": props}, now)
    m["link"]["online"] = props.get("Online")
    m["link"]["phone"] = props.get("Name") or m["link"]["phone"]
    try:
        calls = parse_calls(_call(path, IF_MGR, "GetCalls"))
    except RuntimeError:
        calls = {}           # oFono has the modem but not the voice-call atom yet
    apply_event(m, {"member": "Snapshot", "calls": calls}, now)
    try:
        v = parse_volume(_call(path, IF_VOL, "GetProperties"))
        for k in ("SpeakerVolume", "MicrophoneVolume"):
            if k in v:
                apply_event(m, {"member": "PropertyChanged", "interface": IF_VOL, "name": k, "value": v[k]}, now)
    except RuntimeError:
        pass
    return None


def _perform(ops, m, muted, note):
    for op in ops:
        try:
            if op[0] == "call":
                _call(op[1], op[2], op[3])
            elif op[0] == "dial":
                _call(m["link"]["modem"], IF_MGR, "Dial", "ss", [op[1], ""])
            elif op[0] == "mute":
                _wpctl_mute(_config().get("mic"), op[1])
                muted[0] = op[1]
            elif op[0] == "volume":
                _call(m["link"]["modem"], IF_VOL, "SetProperty", "sv", ["SpeakerVolume", "y", op[1]])
            elif op[0] == "refuse":
                note[0] = op[1]
        except (RuntimeError, subprocess.SubprocessError, OSError) as e:
            note[0] = f"{op[0]} failed: {e}"


def run():
    if not shutil.which("busctl"):
        print("  busctl (systemd) is needed to talk to oFono.", file=sys.stderr)
        return 1
    m = blank_machine(load_recent())
    router, muted, note = Router(), [False], [None]
    last_next = m["next_id"]
    while True:
        now = time.time()
        try:
            why = _poll(m, now)
            note[0] = why or (note[0] if note[0] and not note[0].startswith("No phone") else None)
        except (RuntimeError, subprocess.SubprocessError, OSError, ValueError) as e:
            note[0] = f"oFono is not answering ({e}): is ofono.service running?"
            m["link"]["online"] = False
        a = take_ask()
        if a:
            _perform(plan_ask(m, a, moving=moving(now)), m, muted, note)
        s = summarize(m, now)
        if routing_wanted(s["phase"]) and not router.running():
            err = router.start(m["link"]["modem"], _config().get("mic"))
            if err:
                note[0] = err
        elif not routing_wanted(s["phase"]) and router.running():
            router.stop()
        if s["phase"] == "idle" and muted[0]:
            _wpctl_mute(_config().get("mic"), False)    # never leave the mic muted after a call
            muted[0] = False
        if m["next_id"] != last_next:
            save_recent(m["recent"])
            last_next = m["next_id"]
        write(compose(m, "ofono", now, muted[0], note[0], _config().get("mic")))
        time.sleep(POLL_S)


# ======================================================================================
# The simulator
# ======================================================================================

SIM_PHONE = "iPhone (simulated)"
SIM_CALLER = {"LineIdentification": "+14155550142", "Name": "Alex Rivera (simulated)"}


def sim_recent(now):
    return [
        {"id": 3, "number": "+14155550177", "name": "", "dir": "in", "outcome": "missed", "at": now - 3600 * 5, "secs": 0},
        {"id": 2, "number": "+14155550119", "name": "Dana (simulated)", "dir": "in", "outcome": "answered", "at": now - 86400, "secs": 412},
        {"id": 1, "number": "+14155550142", "name": "Alex Rivera (simulated)", "dir": "out", "outcome": "outgoing", "at": now - 90000, "secs": 96},
    ]


def sim(seconds=None, ring_s=60.0, talk_s=45.0, rest_s=6.0, first_s=3.0):
    """A call that rings, is answered (by a tap on the screen, or by nobody: it
    stops ringing after ring_s), talks for talk_s, ends, then rests and rings
    again. Speaks the same events the real link folds in. Source "sim", always."""
    start = time.time()
    m = blank_machine(sim_recent(start))
    apply_event(m, {"member": "ModemAdded", "path": "/hfp/sim", "props": {"Name": SIM_PHONE, "Online": True}}, start)
    path, n = None, 0
    muted, volume, note, note_at = False, 60, None, 0.0
    m["volume"].update(speaker=volume, mic=70)
    phase_until = start + first_s
    mode = "rest"
    while seconds is None or time.time() - start < seconds:
        now = time.time()
        a = take_ask()
        if a:
            for op in plan_ask(m, a, moving=moving(now)):
                if op[0] == "call" and op[3] in ("Answer", "HoldAndAnswer"):
                    apply_event(m, {"member": "PropertyChanged", "interface": IF_CALL, "path": op[1],
                                    "name": "State", "value": "active"}, now)
                    mode, phase_until = "talk", now + talk_s
                elif op[0] == "call" and op[3] == "Hangup":
                    apply_event(m, {"member": "CallRemoved", "path": op[1]}, now)
                    mode, phase_until = "rest", now + rest_s
                elif op[0] == "mute":
                    muted = op[1]
                elif op[0] == "volume":
                    volume = op[1]
                    apply_event(m, {"member": "PropertyChanged", "interface": IF_VOL,
                                    "name": "SpeakerVolume", "value": volume}, now)
                elif op[0] == "dial":
                    n += 1
                    path = f"/hfp/sim/voicecall{n:02d}"
                    apply_event(m, {"member": "CallAdded", "path": path,
                                    "props": {"State": "dialing", "LineIdentification": op[1]}}, now)
                    mode, phase_until = "dial", now + 2.5
                elif op[0] == "refuse":
                    note, note_at = op[1], now
        if now >= phase_until:
            if mode == "rest":
                n += 1
                path = f"/hfp/sim/voicecall{n:02d}"
                apply_event(m, {"member": "CallAdded", "path": path,
                                "props": dict(SIM_CALLER, State="incoming")}, now)
                mode, phase_until = "ring", now + ring_s
            elif mode == "dial":
                apply_event(m, {"member": "PropertyChanged", "interface": IF_CALL, "path": path,
                                "name": "State", "value": "active"}, now)
                mode, phase_until = "talk", now + talk_s
            else:        # ring ran out, or the talk did: the far end hung up
                for p in list(m["calls"]):
                    apply_event(m, {"member": "CallRemoved", "path": p}, now)
                mode, phase_until = "rest", now + rest_s
        if note and now - note_at > 4:
            note = None
        write(compose(m, "sim", now, muted, note, "Surface microphone (simulated)"))
        time.sleep(0.25)


# ======================================================================================
# Pairing and setup
# ======================================================================================

WP_FILE = os.path.join(os.path.expanduser(os.environ.get("XDG_CONFIG_HOME", "~/.config")),
                       "wireplumber", "wireplumber.conf.d", "51-omacar-calls.conf")
WP_CONF = """\
# Written by `omacar calls setup` (lib/calls.py). oFono is the hands-free unit
# and owns the HFP profile with BlueZ; PipeWire only receives the call audio
# oFono hands it. Exactly one of them may register the profile, so the native
# backend must not.
monitor.bluez.properties = {
  bluez5.roles = [ a2dp_sink a2dp_source hfp_hf ]
  bluez5.hfphsp-backend = "ofono"
}
"""

PAIR_GUIDE = """\
  Pair the iPhone as a hands-free car kit

  Before you start (once):
    sudo pacman -S ofono bluez bluez-utils
    sudo systemctl enable --now bluetooth ofono
    omacar calls setup                 # then: systemctl --user restart wireplumber

  On the iPhone: Settings > Bluetooth ON, and stay on that screen.

  In the bluetoothctl prompt that opens next, type these one at a time:
    power on
    agent on
    default-agent
    pairable on
    discoverable on
    scan on                            # wait for your iPhone's name, then Ctrl-C
    pair  AA:BB:CC:DD:EE:FF            # confirm the code on BOTH screens
    trust AA:BB:CC:DD:EE:FF            # so it reconnects by itself
    connect AA:BB:CC:DD:EE:FF
    quit

  Then: omacar calls use AA:BB:CC:DD:EE:FF && omacar calls status
  If the phone asks to share contacts or notifications, say No: OmaCar uses neither.
"""


def pair():
    print(PAIR_GUIDE)
    if not shutil.which("bluetoothctl"):
        print("  bluetoothctl is not installed (sudo pacman -S bluez-utils).", file=sys.stderr)
        return 1
    if not sys.stdin.isatty():
        return 0
    os.execvp("bluetoothctl", ["bluetoothctl"])


def use(addr):
    if not re.fullmatch(r"[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}", addr or ""):
        print("  that is not a Bluetooth address (AA:BB:CC:DD:EE:FF)", file=sys.stderr)
        return 2
    cfg = _config()
    cfg["phone"] = addr.upper()
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    print(f"  following {addr.upper()}")
    return 0


def setup():
    os.makedirs(os.path.dirname(WP_FILE), exist_ok=True)
    with open(WP_FILE, "w", encoding="utf-8") as f:
        f.write(WP_CONF)
    print(f"  wrote {WP_FILE}\n  now: systemctl --user restart wireplumber pipewire")
    return 0


def forget():
    for p in (RECENT,):
        try:
            os.unlink(p)
        except OSError:
            pass
    print("  recent calls erased (the running link keeps none in memory past its next write)")
    return 0


def main(argv):
    what = argv[0] if argv else "status"
    if what in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if what == "status":
        print(json.dumps(read_public(), indent=2))
        return 0
    if what == "pair":
        return pair()
    if what == "use":
        return use(argv[1] if len(argv) > 1 else "")
    if what == "setup":
        return setup()
    if what == "run":
        return run()
    if what == "sim":
        sim(float(argv[1]) if len(argv) > 1 else None)
        return 0
    if what == "forget":
        return forget()
    print(f"  omacar calls: no such thing as '{what}'.\n"
          "  status | pair | use | setup | run | sim | forget", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
