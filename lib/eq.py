#!/usr/bin/env python3
"""An equalizer for the AUX feed: ten parametric bands, in front of the car radio.

Everything OmaCar plays -- music, the radio, and the drowsy-driver ALERTS --
leaves the tablet through the headphone jack into the car's AUX input. This
shapes that sound without becoming the sink lib/audio.py watches.

    omacar eq status          the saved curve, and whether PipeWire is running it
    omacar eq on | off        insert / remove the equalizer
    omacar eq preset NAME     load a built-in or saved preset
    omacar eq apply           regenerate the config and (re)start it from what is
                              saved; idempotent. What install and boot call.

HOW IT IS ROUTED (doc/eq.md has the long version). The equalizer is a PipeWire
filter-chain run as its OWN process (share/systemd/omacar-eq.service) from a
generated config, and its input node carries filter.smart = true. WirePlumber's
smart-filter policy then links every application stream to the filter and the
filter to the DEFAULT sink -- so the default sink is still the headphone
jack's, lib/audio.py still holds it at 100% and still reads its active port to
say "AUX disconnected". Stopping the unit removes the filter and WirePlumber
relinks the streams straight to the hardware: that is bypass.

ALERTS GO THROUGH IT, so it must never make one quieter than the music's
promise or clip it (share/js/audiobus.js): every band is clamped to +-12 dB,
and a preamp is set so that the whole curve's maximum is at or below 0 dB --
computed from the same biquad formulas PipeWire runs. The user may lower the
preamp further and can never raise it above that automatic value.

Live changes (a slider moving) go through `pw-cli set-param ... Props` and
never restart anything. Only a change of band TYPE, or switching the equalizer
on or off, rewrites the config and restarts the unit.
"""

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

NBANDS = 10
TYPES = ("lowshelf", "peaking", "highshelf", "highpass", "lowpass")
CUTS = ("highpass", "lowpass")
TYPE_NAMES = {"lowshelf": "Low shelf", "peaking": "Peaking", "highshelf": "High shelf",
              "highpass": "Low cut", "lowpass": "High cut"}
FREQ_MIN, FREQ_MAX = 20.0, 20000.0
GAIN_MAX = 12.0            # dB, either way, per band
Q_MIN, Q_MAX = 0.3, 8.0
PREAMP_MIN = -24.0
RATE = 48000               # the tablet's PipeWire rate; the curve is computed at it
UNIT = "omacar-eq.service"
NODE = "omacar-eq"         # the smart filter's name, and its input node's name
LIVE_MIN_INTERVAL = 1.0 / 20.0   # /api/eq/live is applied at most 20 times a second
PW_TIMEOUT = 1.0
MAX_USER_PRESETS = 24
NAME_MAX = 24

DEFAULT_FREQS = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)


# ---- state --------------------------------------------------------------------

def _band(typ, freq, gain=0.0, q=1.0, on=True):
    return {"type": typ, "freq": float(freq), "gain": float(gain), "q": float(q), "on": bool(on)}


def default_bands():
    out = []
    for i, f in enumerate(DEFAULT_FREQS):
        typ = "lowshelf" if i == 0 else "highshelf" if i == NBANDS - 1 else "peaking"
        out.append(_band(typ, f, 0.0, 0.707 if typ != "peaking" else 1.0))
    return out


def _with(base, i, **kw):
    b = [dict(x) for x in base]
    b[i].update(kw)
    return b


def _preset(*edits):
    """The default layout with some bands changed: (index, {fields})."""
    b = default_bands()
    for i, kw in edits:
        b[i].update(kw)
    return b


BUILTIN_PRESETS = {
    "Flat": default_bands(),
    # Gentle on purpose: a little of the cabin's boom taken out, a little air put back.
    "Car cabin": _preset((2, {"gain": -2.0, "q": 1.0}),
                         (8, {"type": "highshelf", "freq": 8000, "gain": 2.0, "q": 0.707})),
    "Bass": _preset((0, {"gain": 5.0, "freq": 60}), (1, {"gain": 3.0, "freq": 100}),
                    (2, {"gain": 1.0})),
    "Vocal clarity": _preset((2, {"gain": -2.0}), (3, {"gain": -1.5}),
                             (5, {"gain": 1.5, "freq": 1500}), (6, {"gain": 3.0, "freq": 3000}),
                             (7, {"gain": 1.5, "freq": 5000})),
    "Loudness": _preset((0, {"gain": 4.0, "freq": 80}), (1, {"freq": 100}),
                        (9, {"gain": 3.0, "freq": 10000})),
    "Night": _preset((0, {"type": "highpass", "freq": 60, "q": 0.707, "gain": 0.0}),
                     (9, {"gain": -3.0, "freq": 6000})),
}
for _n, _b in BUILTIN_PRESETS.items():
    for _x in _b:
        _x.setdefault("on", True)


def default_state():
    return {"version": 1, "enabled": False, "preset": "Flat",
            "bands": default_bands(), "preamp": None, "user_presets": {}}


def _num(v, lo, hi, what):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"{what} must be a number")
    return min(hi, max(lo, float(v)))


def clean_band(raw, i=0):
    if not isinstance(raw, dict):
        raise ValueError(f"band {i + 1} must be an object")
    typ = raw.get("type")
    if typ not in TYPES:
        raise ValueError(f"band {i + 1}: type must be one of {', '.join(TYPES)}")
    gain = _num(raw.get("gain", 0.0), -GAIN_MAX, GAIN_MAX, f"band {i + 1} gain")
    if typ in CUTS:
        gain = 0.0                      # n/a for a cut; stored as 0 so it never lingers
    return {"type": typ,
            "freq": round(_num(raw.get("freq"), FREQ_MIN, FREQ_MAX, f"band {i + 1} freq"), 1),
            "gain": round(gain, 2),
            "q": round(_num(raw.get("q", 1.0), Q_MIN, Q_MAX, f"band {i + 1} q"), 3),
            "on": bool(raw.get("on", True))}


def clean_bands(raw):
    if not isinstance(raw, list) or len(raw) != NBANDS:
        raise ValueError(f"bands must be a list of {NBANDS}")
    return [clean_band(b, i) for i, b in enumerate(raw)]


def _clean_name(n):
    if not isinstance(n, str):
        raise ValueError("a preset name must be text")
    n = re.sub(r"\s+", " ", n).strip()
    if not n or len(n) > NAME_MAX:
        raise ValueError(f"a preset name is 1 to {NAME_MAX} characters")
    return n


def clean_preamp(v):
    """None = automatic. A number is the user's own trim and is applied only
    when it is LOWER than the automatic value (effective_preamp)."""
    if v is None:
        return None
    return round(_num(v, PREAMP_MIN, 0.0, "preamp"), 1)


def clean_state(raw):
    """A whole state from anything a client sent: clamped, and refused (ValueError)
    where it is not the shape of a state at all."""
    if not isinstance(raw, dict):
        raise ValueError("the state must be a JSON object")
    st = default_state()
    st["enabled"] = bool(raw.get("enabled", False))
    st["bands"] = clean_bands(raw.get("bands"))
    st["preamp"] = clean_preamp(raw.get("preamp"))
    preset = raw.get("preset")
    st["preset"] = _clean_name(preset) if isinstance(preset, str) and preset.strip() else None
    ups = raw.get("user_presets", {})
    if not isinstance(ups, dict) or len(ups) > MAX_USER_PRESETS:
        raise ValueError(f"user_presets must be an object of at most {MAX_USER_PRESETS}")
    out = {}
    for name, p in ups.items():
        name = _clean_name(name)
        if name in BUILTIN_PRESETS:
            raise ValueError(f"{name!r} is a built-in preset's name")
        if not isinstance(p, dict):
            raise ValueError("a preset must be an object")
        out[name] = {"bands": clean_bands(p.get("bands")), "preamp": clean_preamp(p.get("preamp"))}
    st["user_presets"] = out
    return st


# ---- the maths ----------------------------------------------------------------
#
# PipeWire's bq_* builtins call spa/plugins/audioconvert/biquad.c (Chromium OS's
# and WebKit's, the Web Audio API biquads): coefficients from the RBJ cookbook
# with alpha = sin(w0) / (2Q), Gain in dB, A = 10^(dB/40), and w0 = pi * freq /
# (rate / 2) -- note that is w0 = 2 pi f / rate. Q is a true Q for every type
# (the cuts included: a Q above 0.707 is a peak at the corner).

def biquad(typ, freq, q, gain_db, rate=RATE):
    w0 = math.pi * min(1.0, max(0.0, freq / (rate / 2.0)))
    if typ in ("lowpass", "highpass"):
        if w0 <= 0 or w0 >= math.pi:
            return (1.0, 0.0, 0.0, 0.0, 0.0) if (w0 >= math.pi) == (typ == "lowpass") \
                else (0.0, 0.0, 0.0, 0.0, 0.0)
        alpha = math.sin(w0) / (2 * q)
        k = math.cos(w0)
        s = (1 - k) if typ == "lowpass" else (1 + k)
        b0, b1, b2 = s / 2, (s if typ == "lowpass" else -s), s / 2
        a0, a1, a2 = 1 + alpha, -2 * k, 1 - alpha
    else:
        A = 10 ** (gain_db / 40.0)
        alpha = math.sin(w0) / (2 * q)
        k = math.cos(w0)
        if typ == "peaking":
            b0, b1, b2 = 1 + alpha * A, -2 * k, 1 - alpha * A
            a0, a1, a2 = 1 + alpha / A, -2 * k, 1 - alpha / A
        else:
            k2 = 2 * math.sqrt(A) * alpha
            ap, am = A + 1, A - 1
            if typ == "lowshelf":
                b0, b1, b2 = A * (ap - am * k + k2), 2 * A * (am - ap * k), A * (ap - am * k - k2)
                a0, a1, a2 = ap + am * k + k2, -2 * (am + ap * k), ap + am * k - k2
            else:
                b0, b1, b2 = A * (ap + am * k + k2), -2 * A * (am + ap * k), A * (ap + am * k - k2)
                a0, a1, a2 = ap - am * k + k2, 2 * (am - ap * k), ap - am * k - k2
    return (b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)


def mag_db(coef, f, rate=RATE):
    """|H(e^jw)| in dB at f Hz."""
    b0, b1, b2, a1, a2 = coef
    w = 2 * math.pi * f / rate
    c1, s1, c2, s2 = math.cos(w), math.sin(w), math.cos(2 * w), math.sin(2 * w)
    nr, ni = b0 + b1 * c1 + b2 * c2, -(b1 * s1 + b2 * s2)
    dr, di = 1 + a1 * c1 + a2 * c2, -(a1 * s1 + a2 * s2)
    num, den = nr * nr + ni * ni, dr * dr + di * di
    if den <= 0:
        return 0.0
    return 10 * math.log10(max(num, 1e-30) / den)


def log_grid(n=512, lo=FREQ_MIN, hi=FREQ_MAX):
    r = math.log(hi / lo)
    return [lo * math.exp(r * i / (n - 1)) for i in range(n)]


def active_bands(bands):
    return [b for b in bands if b["on"]]


def curve_db(bands, freqs, rate=RATE):
    """The combined response of the enabled bands, before the preamp."""
    coefs = [biquad(b["type"], b["freq"], b["q"], b["gain"], rate) for b in active_bands(bands)]
    return [sum(mag_db(c, f, rate) for c in coefs) for f in freqs]


def peak_db(bands, rate=RATE):
    """The curve's maximum over 20 Hz - 20 kHz: a 1024-point log grid, plus each
    band's own corner and its neighbours, where a narrow peak actually sits."""
    freqs = log_grid(1024)
    for b in active_bands(bands):
        for m in (0.85, 0.95, 1.0, 1.05, 1.18):
            f = b["freq"] * m
            if FREQ_MIN <= f <= FREQ_MAX:
                freqs.append(f)
    return max(curve_db(bands, freqs, rate))


def auto_preamp(bands, rate=RATE):
    """-max(0, peak), rounded DOWN to 0.1 dB so the maximum lands at or below
    0 dB, never a rounding error above it."""
    p = max(0.0, peak_db(bands, rate))
    return -math.ceil(p * 10 - 1e-9) / 10.0 if p > 0 else 0.0


def effective_preamp(state_or_bands, user=None):
    """The preamp that is applied: the automatic value, or the user's if lower."""
    if isinstance(state_or_bands, dict):
        bands, user = state_or_bands["bands"], state_or_bands.get("preamp")
    else:
        bands = state_or_bands
    auto = auto_preamp(bands)
    return auto if user is None else round(min(auto, user), 1)


# ---- the config PipeWire is run with ------------------------------------------

def node_name(i):
    return f"eq_band_{i + 1}"


def structure(state):
    """What the generated graph is made of. A disabled shelf or peak is still in
    it (at 0 dB it is exactly nothing, so it can be switched live); a disabled
    cut cannot be made nothing, so it is left out. When this changes the config is
    rewritten and the unit restarted; when it does not, everything is live."""
    return [None if (not b["on"] and b["type"] in CUTS) else b["type"] for b in state["bands"]]


def band_controls(b, flat=False):
    """{control: value} for one band as PipeWire is to be told. flat = what makes
    the band do nothing, for a disabled band and for the A/B compare."""
    if not b["on"] or flat:
        if b["type"] == "highpass":
            return {"Freq": 1.0, "Q": b["q"], "Gain": 0.0}
        if b["type"] == "lowpass":
            return {"Freq": RATE / 2.0, "Q": b["q"], "Gain": 0.0}
        return {"Freq": b["freq"], "Q": b["q"], "Gain": 0.0}
    return {"Freq": b["freq"], "Q": b["q"], "Gain": 0.0 if b["type"] in CUTS else b["gain"]}


def mult(db):
    return 10 ** (db / 20.0)


def generate_config(state):
    """The standalone PipeWire config: pipewire.conf's own skeleton (the shipped
    filter-chain.conf's context.modules) with one filter-chain module whose capture
    node is a WirePlumber smart filter."""
    names, nodes = [], []
    pre = effective_preamp(state)
    nodes.append('                    { type = builtin name = preamp label = linear\n'
                 f'                      control = {{ "Mult" = {mult(pre):.6f} "Add" = 0.0 }} }}')
    names.append("preamp")
    for i, (b, kind) in enumerate(zip(state["bands"], structure(state))):
        if kind is None:
            continue
        c = band_controls(b)
        nodes.append(f'                    {{ type = builtin name = {node_name(i)} label = bq_{kind}\n'
                     f'                      control = {{ "Freq" = {c["Freq"]:.2f} "Q" = {c["Q"]:.3f} '
                     f'"Gain" = {c["Gain"]:.2f} }} }}')
        names.append(node_name(i))
    links = [f'                    {{ output = "{a}:Out" input = "{b}:In" }}'
             for a, b in zip(names, names[1:])]
    return CONF % {"nodes": "\n".join(nodes), "links": "\n".join(links), "node": NODE,
                   "sig": json.dumps(structure(state))}


CONF = """# OmaCar's equalizer. GENERATED by lib/eq.py from ~/.config/omacar/eq.json: edit
# the equalizer in the app (or `omacar eq`), not this file.
# structure: %(sig)s
#
# Run by share/systemd/omacar-eq.service as its own pipewire process, modelled on
# PipeWire's shipped filter-chain.conf, so reshaping it never restarts the
# session's PipeWire. The capture node is a WirePlumber SMART FILTER with no
# filter.smart.target, which attaches it in front of the DEFAULT sink -- the
# default sink itself is never replaced.

context.properties = {
    log.level = 0
}

context.spa-libs = {
    audio.convert.* = audioconvert/libspa-audioconvert
    support.*       = support/libspa-support
}

context.modules = [
    { name = libpipewire-module-rt
        args = { }
        flags = [ ifexists nofail ]
    }
    { name = libpipewire-module-protocol-native }
    { name = libpipewire-module-client-node }
    { name = libpipewire-module-adapter }

    { name = libpipewire-module-filter-chain
        args = {
            node.name        = "%(node)s"
            node.description = "OmaCar Equalizer"
            media.name       = "OmaCar Equalizer"
            filter.graph = {
                nodes = [
%(nodes)s
                ]
                links = [
%(links)s
                ]
            }
            audio.channels = 2
            audio.position = [ FL FR ]
            capture.props = {
                node.name          = "%(node)s"
                media.class        = Audio/Sink
                filter.smart       = true
                filter.smart.name  = "%(node)s"
                priority.session   = 1
            }
            playback.props = {
                node.name        = "%(node)s-out"
                node.passive     = true
                stream.dont-remix = true
            }
        }
    }
]
"""


# ---- files --------------------------------------------------------------------

def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "omacar", "eq.json")


def state_dir():
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "omacar", "eq")


def conf_path():
    return os.path.join(state_dir(), "omacar-eq.conf")


def _atomic(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".eq-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load():
    try:
        with open(config_path()) as f:
            return clean_state(json.load(f))
    except (OSError, ValueError):
        return default_state()


def save(state):
    _atomic(config_path(), json.dumps(state, indent=2) + "\n")


def write_config(state):
    """Write the generated config; True when its text changed."""
    text = generate_config(state)
    try:
        with open(conf_path()) as f:
            if f.read() == text:
                return False
    except OSError:
        pass
    _atomic(conf_path(), text)
    return True


def _applied_structure():
    """The structure of the config the RUNNING unit was started from."""
    try:
        with open(os.path.join(state_dir(), "running.json")) as f:
            return json.load(f).get("structure")
    except (OSError, ValueError):
        return None


def _mark_running(state):
    _atomic(os.path.join(state_dir(), "running.json"),
            json.dumps({"structure": structure(state), "at": time.time()}))


# ---- PipeWire -----------------------------------------------------------------
# The three hooks tests replace.

def _run(args, timeout):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


def pipewire_state():
    """(present, why). Present = the tools exist and a PipeWire answers."""
    if not shutil.which("pw-cli") or not shutil.which("pipewire"):
        return False, "PipeWire isn't installed here"
    rc, _ = _run(["pw-cli", "info", "0"], PW_TIMEOUT)
    if rc != 0:
        return False, "PipeWire isn't running here"
    return True, None


def unit_active():
    rc, out = _run(["systemctl", "--user", "is-active", UNIT], 3)
    return rc == 0 and out.strip() == "active"


def _unit(*args):
    rc, _ = _run(["systemctl", "--user", *args, UNIT], 15)
    return rc == 0


_node_id = None


def find_node():
    """The filter-chain's node id, from pw-dump (its input node's name)."""
    global _node_id
    rc, out = _run(["pw-dump"], 2.0)
    if rc != 0:
        return None
    try:
        for o in json.loads(out):
            props = (o.get("info") or {}).get("props") or {}
            if o.get("type") == "PipeWire:Interface:Node" and props.get("node.name") == NODE:
                _node_id = o["id"]
                return _node_id
    except (ValueError, TypeError, KeyError):
        pass
    return None


def push(params):
    """pw-cli set-param <node> Props '{ params = [ "name" value ... ] }', 1 s
    timeout. params is {"eq_band_3:Gain": 2.0, ...}. True when it was accepted."""
    if not params:
        return True
    nid = _node_id if _node_id is not None else find_node()
    if nid is None:
        return False
    body = "{ params = [ " + " ".join(f'"{k}" {float(v):.6f}' for k, v in params.items()) + " ] }"
    rc, _ = _run(["pw-cli", "set-param", str(nid), "Props", body], PW_TIMEOUT)
    if rc != 0:                       # the id may be stale (the unit restarted): look once more
        nid = find_node()
        if nid is None:
            return False
        rc, _ = _run(["pw-cli", "set-param", str(nid), "Props", body], PW_TIMEOUT)
    return rc == 0


_pushed = {}     # what PipeWire was last told, so a slider sends only what moved


def push_changed(params):
    """push() the parameters that differ from the last ones sent (all of them the
    first time, or after a restart)."""
    diff = {k: v for k, v in params.items() if _pushed.get(k) != v}
    if not diff:
        return True
    ok = push(diff)
    if ok:
        _pushed.update(diff)
    return ok


def all_params(state, flat=False):
    p = {"preamp:Mult": 1.0 if flat else mult(effective_preamp(state))}
    for i, (b, kind) in enumerate(zip(state["bands"], structure(state))):
        if kind is None:
            continue
        for ctl, v in band_controls(b, flat=flat).items():
            p[f"{node_name(i)}:{ctl}"] = v
    return p


# ---- applying -----------------------------------------------------------------

_lock = threading.Lock()
_live_state = None       # the saved state with live edits laid over it
_live_last = 0.0


def apply(state=None, restart=False):
    """Make PipeWire match `state` (default: what is saved). Returns what it did:
    {"mode": "bypassed" | "restarted" | "live" | "unchanged" | "unavailable"}.

    Enabled: if the unit is not running, or the graph's structure changed, or
    `restart`, the config is rewritten and the unit restarted; otherwise every
    parameter is pushed live. Disabled: the unit is stopped, and WirePlumber
    links the streams straight to the hardware again."""
    global _live_state
    with _lock:
        _live_state = None
        state = state or load()
        present, why = pipewire_state()
        if not present:
            return {"mode": "unavailable", "why": why}
        if not state["enabled"]:
            return {"mode": "bypassed", "ok": _unit("disable", "--now")}
        changed = write_config(state)
        running = unit_active()
        if running and not changed and not restart and _applied_structure() == structure(state):
            return {"mode": "unchanged", "ok": True}
        if running and not restart and _applied_structure() == structure(state):
            ok = push_changed(all_params(state))
            return {"mode": "live", "ok": ok}
        global _node_id
        _node_id = None
        _pushed.clear()
        ok = _unit("restart") if running else _unit("enable", "--now")
        _mark_running(state)
        return {"mode": "restarted", "ok": ok}


def live(change):
    """A slider moving: {"bands": {"3": {"freq":..,"gain":..,"q":..}}, "preamp":
    x, "flat": bool}. Not saved (the app writes the whole state when the finger
    lifts), not restarting, and answered at most 20 times a second -- an excess
    one is dropped and says so; the commit that follows carries the final values.
    An A/B change ("flat") is a press and a release, not a stream, and is never dropped."""
    global _live_state, _live_last
    if not isinstance(change, dict):
        raise ValueError("the body must be a JSON object")
    flat = bool(change.get("flat"))
    with _lock:
        now = time.monotonic()
        if "flat" not in change and now - _live_last < LIVE_MIN_INTERVAL:
            return {"ok": True, "throttled": True}
        base = _live_state or load()
        st = json.loads(json.dumps(base))
        edits = change.get("bands") or {}
        if not isinstance(edits, dict):
            raise ValueError("bands must be an object of band number to fields")
        for k, v in edits.items():
            i = int(k)
            if not 0 <= i < NBANDS or not isinstance(v, dict):
                raise ValueError("no such band")
            merged = dict(st["bands"][i])
            for f in ("freq", "gain", "q"):
                if f in v:
                    merged[f] = v[f]
            st["bands"][i] = clean_band(merged, i)
        if "preamp" in change:
            st["preamp"] = clean_preamp(change["preamp"])
        if not st["enabled"] or not unit_active() or _applied_structure() != structure(st):
            return {"ok": False, "why": "the equalizer isn't running with this layout"}
        _live_state = st
        _live_last = now
        ok = push_changed(all_params(st, flat=flat))
        return {"ok": ok, "preamp": 0.0 if flat else effective_preamp(st)}


# ---- what the app and the CLI read ---------------------------------------------

def all_presets(state):
    return {"builtin": BUILTIN_PRESETS, "user": state["user_presets"]}


def describe(state=None):
    state = state or load()
    present, why = pipewire_state()
    freqs = log_grid(128)
    out = {"present": present, "why": why, "state": state,
           "auto_preamp": auto_preamp(state["bands"]),
           "preamp": effective_preamp(state),
           "peak_db": round(peak_db(state["bands"]), 2),
           "presets": all_presets(state),
           "curve": {"freqs": [round(f, 2) for f in freqs],
                     "db": [round(v, 3) for v in curve_db(state["bands"], freqs)]}}
    out["running"] = bool(present and unit_active())
    out["applied"] = bool(out["running"] and state["enabled"]
                          and _applied_structure() == structure(state))
    return out


def write_state(raw):
    """POST /api/eq: validate, clamp, save, apply. ValueError for a body that is
    not a state."""
    state = clean_state(raw)
    save(state)
    result = apply(state)
    out = describe(state)
    out["result"] = result
    return out


def set_preset(name):
    state = load()
    src = BUILTIN_PRESETS.get(name) or (state["user_presets"].get(name) or {}).get("bands")
    if src is None:
        raise ValueError(f"no preset called {name!r}")
    pre = None if name in BUILTIN_PRESETS else state["user_presets"][name]["preamp"]
    state["bands"], state["preamp"], state["preset"] = clean_bands(src), pre, name
    save(state)
    return state


# ---- command line -------------------------------------------------------------

def _print_status():
    d = describe()
    s = d["state"]
    print(f"  equalizer  {'on' if s['enabled'] else 'off (bypassed)'}"
          f"{'  preset ' + s['preset'] if s['preset'] else ''}")
    if not d["present"]:
        print(f"  pipewire   not applied: {d['why']}")
    else:
        print(f"  pipewire   {'running the equalizer' if d['applied'] else 'not running it'}")
    print(f"  preamp     {d['preamp']:+.1f} dB (automatic {d['auto_preamp']:+.1f}); "
          f"curve peak {d['peak_db']:+.1f} dB before it")
    for i, b in enumerate(s["bands"]):
        g = "     " if b["type"] in CUTS else f"{b['gain']:+5.1f}"
        print(f"  band {i + 1:2d}    {TYPE_NAMES[b['type']]:<10} {b['freq']:>7.0f} Hz  {g} dB  Q {b['q']:.2f}"
              f"{'' if b['on'] else '  (off)'}")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "status":
        _print_status()
        return 0
    if cmd in ("on", "off"):
        st = load()
        st["enabled"] = cmd == "on"
        save(st)
    elif cmd == "preset":
        if len(argv) < 3:
            print("  usage: omacar eq preset NAME", file=sys.stderr)
            return 2
        try:
            st = set_preset(" ".join(argv[2:]))
        except ValueError as e:
            print(f"  omacar: {e}", file=sys.stderr)
            return 1
    elif cmd == "write":
        # The unit's ExecStartPre: the config always exists before pipewire reads it.
        write_config(load())
        return 0
    elif cmd == "apply":
        pass
    else:
        print(__doc__)
        return 2
    r = apply()
    print(f"  {r['mode']}" + (f": {r['why']}" if r.get("why") else ""))
    return 0 if r.get("ok", True) is not False else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
