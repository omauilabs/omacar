"""The diagnostic advisor: a master technician reading THIS car's numbers.

A scan tool tells you a code was set. A five-thousand-dollar scan tool tells
you what other technicians did about that code on this model. Neither of them
looks at your freeze frame, your fuel trims, your Mode 06 margins, your year of
economy and your service book at the same time and tells you what is actually
going on with your car. That is what this does, and it is the reason to build
this rather than buy one.

Free, and local by construction
-------------------------------
It shells out to the `claude` CLI the user already has, in headless mode. No
API key, no subscription of ours, no per-scan fee, and nothing about the car
leaves the machine except the evidence bundle the user asked a question about.
If the CLI is not installed the app degrades to the built-in knowledge base and
says so — it never pretends to an answer it did not get.

Grounded, or not at all
-----------------------
The failure mode of a language model on a diagnostic tool is a confident
invention: a code that does not exist, a measurement nobody took, a part number
from a different car. Every defence here is aimed at that:

  * The model is given a structured evidence bundle and told, in the system
    prompt, that it is the only source of fact about this vehicle.
  * It must answer in JSON against a fixed schema, and every finding must
    carry an `evidence` list of keys that exist in the bundle.
  * Findings whose evidence keys are not in the bundle are dropped before the
    answer is ever shown. That check is here, in Python, not a request to the
    model to please be careful.
  * Confidence is required per finding and surfaced in the UI.
  * No tools are enabled, so it cannot go and read files or the web and come
    back with something that is not about this car.

Answers are cached against a hash of the evidence, so re-asking the same
question of an unchanged car is instant and free.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import records  # noqa: E402

CACHE_DIR = os.path.join(records.STATE, "ai")
DEFAULT_MODEL = "claude-sonnet-5"
TIMEOUT = 240

# Diagnosis is worth thinking about; a plain-language rewrite is not. Kinds
# that reason get the better model, the rest get the fast one.
MODEL_FOR = {
    "triage": "claude-sonnet-5",
    "code": "claude-sonnet-5",
    "ask": "claude-sonnet-5",
    "predict": "claude-sonnet-5",
    "owner": "claude-haiku-4-5",
    "symptom": "claude-sonnet-5",
    "recording": "claude-sonnet-5",
}

SYSTEM = """\
You are the diagnostic brain of OmaCar, an OBD-II scan tool. You are a master \
automotive technician: thirty years in the trade, ASE master with L1, equally \
at home on a hybrid and a carburettor.

THE EVIDENCE BUNDLE IS THE ONLY THING YOU KNOW ABOUT THIS VEHICLE.
You may use general automotive knowledge — how systems work, what a code \
means, typical failure rates, test procedures. You may NOT invent anything \
specific to this car. Never state a trouble code, a measurement, a mileage, a \
date or a part number for this vehicle unless it appears in the bundle. If the \
bundle does not contain what you need, say so and say what to measure.

HOW YOU THINK.
Cheap and certain before expensive and probable. Rule out wiring and \
connections before condemning a part. A code is a symptom, not a diagnosis. \
Where two codes have one cause, say so — a list of four faults that are really \
one fault is how people end up buying four parts. Where the data contradicts \
the obvious reading, follow the data and say why.

TONE.
Talk like a good technician talks to another technician who is standing at the \
car: direct, specific, no filler, no hedging for its own sake. No exclamation \
marks. Never begin with a restatement of the question.

OUTPUT.
You have no tools that are any use here and must not call one. Everything you \
need is in the prompt. Reply with ONE JSON object and nothing else: no prose \
before or after, no markdown fence, no markdown inside the strings.
"""

# The schema, described to the model in the prompt rather than enforced by an
# API-side tool, because the CLI's print mode has no structured-output hook.
# Everything is re-validated in Python afterwards regardless.
SCHEMAS = {
    "triage": """{
  "headline": "one sentence: the state of this vehicle right now",
  "verdict": "ok" | "attention" | "urgent",
  "findings": [
    {
      "title": "short name for the problem",
      "severity": "info" | "warning" | "critical",
      "confidence": 0-100,
      "what": "what is wrong, 1-3 sentences",
      "why": "the reasoning from the evidence, 1-3 sentences",
      "evidence": ["bundle keys you used, e.g. faults.P0135, mode06.0x39"],
      "related_codes": ["codes in the bundle this explains"],
      "action": "the single next thing to do",
      "cost": "rough parts-and-labour band, or empty if you cannot say"
    }
  ],
  "order": ["ordered list of what to do first, second, third, and why each is in that place"],
  "safety": "anything that makes this unsafe to drive, or empty string",
  "emissions": "whether it would pass an emissions test and what blocks it, or empty string",
  "questions": ["things you would ask the owner that would change the diagnosis"]
}""",
    "code": """{
  "headline": "one sentence on what this code means for THIS car",
  "confidence": 0-100,
  "reading": "what this car's own numbers say about this code, 2-4 sentences",
  "ranked": [
    { "cause": "...", "likelihood": 0-100, "why": "why this rank for this car specifically", "test": "the one test that confirms or clears it", "cost": "rough band" }
  ],
  "evidence": ["bundle keys used"],
  "next": ["ordered steps"],
  "pitfalls": ["mistakes people make on this code"],
  "related": ["other codes in the bundle that share a cause with this one"]
}""",
    "ask": """{
  "answer": "the answer, 1-5 short paragraphs of plain text",
  "confidence": 0-100,
  "evidence": ["bundle keys used"],
  "caveats": ["anything the evidence cannot settle"],
  "followups": ["two or three questions worth asking next"]
}""",
    "owner": """{
  "summary": "3-5 sentences for somebody who is not a mechanic. No jargon. What is wrong, does it matter, what will it cost.",
  "spend_now": [ { "item": "...", "why": "...", "cost": "rough band" } ],
  "spend_later": [ { "item": "...", "why": "...", "when": "..." } ],
  "ignore": [ { "item": "...", "why": "..." } ],
  "safe_to_drive": "yes" | "yes, with care" | "no",
  "safe_note": "one sentence"
}""",
    "symptom": """{
  "headline": "one sentence on what this complaint most likely is",
  "confidence": 0-100,
  "hypotheses": [
    { "cause": "...", "likelihood": 0-100, "why": "what in the evidence supports or fails to rule this out", "distinguishes": "the one observation that separates this from the others" }
  ],
  "evidence": ["bundle keys used"],
  "record": {
    "channels": ["PID names from the bundle's own channel list, e.g. RPM, LONG_FUEL_TRIM_1"],
    "conditions": "exactly how to drive or run the engine while recording",
    "minutes": 5,
    "looking_for": ["what in that recording would confirm or kill each hypothesis"]
  },
  "cheap_checks": ["things worth doing before recording anything, because they cost nothing"],
  "questions": ["what you would ask the driver"]
}""",
    "recording": """{
  "headline": "one sentence on what this recording shows",
  "confidence": 0-100,
  "reading": "what the channels did and what that means, 2-5 sentences",
  "findings": [
    { "title": "...", "severity": "info" | "warning" | "critical", "confidence": 0-100, "what": "...", "why": "the statistic in the recording that says so", "evidence": ["bundle keys"], "action": "..." }
  ],
  "next": ["what to record or measure next, and why"]
}""",
    "predict": """{
  "headline": "one sentence on where this car is heading",
  "predictions": [
    { "what": "the failure you expect", "when": "a distance or time band", "confidence": 0-100, "basis": "the trend in the evidence that says so", "evidence": ["bundle keys"], "prevent": "what would stop or delay it" }
  ],
  "watch": ["values worth recording on the next drive, and what would worry you"]
}""",
}

PROMPTS = {
    "triage": "Read the whole bundle and tell me the state of this vehicle. "
              "Group codes that share a cause. Put the cheap, certain work "
              "first.",
    "code": "Focus on trouble code {code}. Use this car's own freeze frame, "
            "Mode 06 results, fuel trims and history to rank the causes for "
            "THIS car rather than for the model in general.",
    "ask": "{question}",
    "owner": "Explain this vehicle's condition to its owner, who is not a "
             "mechanic and wants to know what they actually have to spend "
             "money on.",
    "symptom": "The driver reports: {question}\n\nWork out what this most likely "
               "is, rule out what the evidence already rules out, and tell me "
               "exactly what to record on the next drive to settle it.",
    "recording": "A stretch of driving has been recorded and its per-channel "
                 "statistics are in `sample_statistics`. Read it: what do the "
                 "channels say, and does anything in it explain the codes or "
                 "the complaint?",
    "predict": "Using the trends in the bundle — Mode 06 margins, economy by "
               "month, service intervals, code history — tell me what is "
               "likely to fail next and roughly when.",
}


def available():
    """Whether the local Claude CLI is there to be driven."""
    return shutil.which("claude") is not None


# ---- the evidence bundle ----------------------------------------------------

def bundle(kind="triage", code=None, span=None):
    """Everything the model is allowed to know, flattened and keyed.

    Keys are stable and readable — `faults.P0135`, `mode06.0x39`,
    `perf.month` — because the model has to cite them and a human has to
    recognise the citation.
    """
    s = records.snapshot()
    u = s["units"]

    def dist(km):
        v = records.to_dist(km, u)
        return None if v is None else round(v, 1)

    def econ(lphk):
        v = records.to_econ(lphk, u)
        return None if v is None else round(v, 1 if u["system"] == "imperial" else 2)

    def temp(c):
        v = records.to_temp(c, u)
        return None if v is None else round(v)

    b = {
        "_units": {"distance": u["dist"], "speed": u["speed"],
                   "economy": u["econ"], "temperature": u["temp"],
                   "volume": u["vol"]},
        "vehicle": {
            "name": s["name"],
            "vin": s["vehicle"].get("vin"),
            "engine": s["vehicle"].get("engine"),
            "drivetrain": s["vehicle"].get("drivetrain"),
            "odometer": dist(s["odometer"]),
            "protocol": s["vehicle"].get("protocol"),
            "simulated": s["simulated"],
        },
        "now": {
            "status": s["status"],
            "connected": s["connected"],
        },
    }

    v = (s["live"].get("values") or {})
    if v:
        b["now"].update({
            "speed": round((v.get("SPEED") or 0) * u["km"]),
            "rpm": v.get("RPM"),
            "engine_load_pct": v.get("ENGINE_LOAD"),
            "throttle_pct": v.get("THROTTLE_POS"),
            "coolant": temp(v.get("COOLANT_TEMP")),
            "intake_air": temp(v.get("INTAKE_TEMP")),
            "ambient": temp(v.get("AMBIANT_AIR_TEMP")),
            "fuel_level_pct": v.get("FUEL_LEVEL"),
            "system_voltage": v.get("CONTROL_MODULE_VOLTAGE"),
            "short_fuel_trim_pct": v.get("SHORT_FUEL_TRIM_1"),
            "long_fuel_trim_pct": v.get("LONG_FUEL_TRIM_1"),
            "timing_advance_deg": v.get("TIMING_ADVANCE"),
            "maf_gps": v.get("MAF"),
            "economy": econ(s["live"].get("economy_lphk")),
        })

    b["modules"] = {
        m["id"]: {"name": m["name"], "system": m["system"],
                  "reachable_generically": m["generic"],
                  "codes": m["codes"], "software": m.get("sw")}
        for m in s["modules"]}

    b["faults"] = {}
    for f in s["faults"]:
        b["faults"][f["code"]] = {
            "description": f.get("descr"),
            "status": f.get("status"),
            "severity": f.get("severity"),
            "system": f.get("system"),
            "module": (f.get("module") or {}).get("id"),
            "times_seen": f.get("count"),
            "first_seen_days_ago": round((f["since"] or 0) / 86400) if f.get("since") else None,
            "last_seen_days_ago": round((f["ago"] or 0) / 86400) if f.get("ago") else None,
            "freeze_frame": f.get("freeze"),
            "note": f.get("detail"),
        }

    r = s["readiness"]
    b["readiness"] = {
        "ready_for_emissions_test": r["ready"],
        "incomplete": [m["name"] for m in r["monitors"]
                       if m["supported"] and not m["complete"]],
        "incomplete_reasons": {m["name"]: m.get("why")
                               for m in r["monitors"]
                               if m["supported"] and not m["complete"] and m.get("why")},
        "complete": [m["name"] for m in r["monitors"]
                     if m["supported"] and m["complete"]],
    }

    b["mode06"] = {
        m["mid"]: {"test": m["name"], "component": m["component"],
                   "value": m["value"], "min": m["lo"], "max": m["hi"],
                   "unit": m["unit"], "passed": m["pass"],
                   "fraction_of_limit": round(m["headroom"], 3) if m.get("headroom") else None,
                   "note": m.get("note")}
        for m in s["mode06"]}

    if s.get("service"):
        b["service"] = {
            "due_or_soon": s["service"]["due"],
            "overdue": s["service"]["overdue"],
            "items": [{
                "item": i["item"], "minder_code": i.get("code"),
                "life_remaining_pct": i["life"],
                "distance_left": dist(i["km_left"]) if i.get("km_left") is not None else None,
                "due_on": i.get("due_on"), "last_done": i.get("last_on"),
                "last_done_at": dist(i.get("last_km")),
                "note": i.get("note"),
            } for i in s["service"]["items"]],
        }

    p = s.get("perf")
    if p:
        def win(w):
            if not w:
                return None
            out = {"distance": dist(w["km"]), "economy": econ(w.get("lphk")),
                   "trips": w.get("trips")}
            if w.get("prev"):
                out["previous_period_economy"] = econ(w["prev"].get("lphk"))
                out["previous_period_distance"] = dist(w["prev"].get("km"))
            return out
        b["perf"] = {
            "day": win(p.get("day")), "week": win(p.get("week")),
            "month": win(p.get("month")), "year": win(p.get("year")),
            "by_month": [{"month": m["month"], "distance": dist(m["km"]),
                          "economy": econ(m.get("lphk"))}
                         for m in p.get("months", [])],
            "records_since": p.get("since"),
        }

    # A drive's worth of channel statistics rather than the rows themselves:
    # cheaper, and a model reads a shape far better than ten thousand numbers.
    db = records.connect()
    if db is not None:
        t0, t1 = (span or (time.time() - 7 * 86400, time.time()))
        series = records.samples(db, since=t0, until=t1, limit=6000)
        if series:
            b["sample_statistics"] = {
                "span_hours": round((t1 - t0) / 3600, 1),
                "rows": len(series),
                "channels": records.stats(series),
                "_note": "Statistics over the recorded samples, not raw rows. "
                         "Channels are metric as they come off the bus: speed "
                         "km/h, temperatures °C, maf g/s, lphk L/100km.",
            }
        db.close()

    if code:
        b["_focus_code"] = code
    # The channels a recording can actually be made of. A plan that asks for a
    # PID this car does not report is a plan nobody can carry out.
    b["_recordable_channels"] = (s["live"].get("supported")
                                 or sorted((s["live"].get("values") or {}).keys()))
    return b


def evidence_keys(b, prefix=""):
    """Every citable key in the bundle, as dotted paths one level into each
    section — which is the granularity the model is asked to cite at."""
    keys = set()
    for k, v in b.items():
        if k.startswith("_"):
            continue
        keys.add(k)
        if isinstance(v, dict):
            for k2 in v:
                if not str(k2).startswith("_"):
                    keys.add(f"{k}.{k2}")
    return keys


# ---- calling the model ------------------------------------------------------

def extract_json(text):
    """The first JSON object in a reply, fence or no fence."""
    if not text:
        return None
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    try:
        return json.loads(t)
    except ValueError:
        pass
    start = t.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(t)):
        c = t[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i + 1])
                except ValueError:
                    return None
    return None


def cache_path(key):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, key + ".json")


def cache_key(kind, prompt, b):
    h = hashlib.sha256()
    h.update(kind.encode())
    h.update(prompt.encode())
    h.update(json.dumps(b, sort_keys=True, default=str).encode())
    return h.hexdigest()[:24]


def _answering_model(envelope, asked_for):
    """Which model actually answered, not whichever key sorted first.

    This read `list(envelope["modelUsage"].keys())[0]`, and modelUsage carries
    every model the CLI billed for a turn -- including the little one it uses
    in the background to title a session. So a diagnosis reasoned out by Sonnet
    was labelled Haiku on screen, in the one place a person looks to judge how
    much to trust the answer. Every cached advisor result on this machine says
    haiku for a kind that routes to sonnet.

    Prefer the model we asked for when the envelope confirms it ran; otherwise
    name the one with the most output tokens, which is the one that did the
    work. Fall back to the request rather than to a guess.
    """
    usage = envelope.get("modelUsage") or {}
    if not usage:
        return asked_for
    for name in usage:
        if asked_for and asked_for in name:
            return name
    def output(v):
        if not isinstance(v, dict):
            return 0
        return (v.get("outputTokens") or v.get("output_tokens")
                or v.get("output") or 0)
    return max(usage, key=lambda k: output(usage[k]))


def run_claude(prompt, model, extra_system):
    """One headless turn. No tools, one turn, JSON out."""
    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--model", model,
        # REPLACE the CLI's own system prompt rather than appending to it.
        # Appending left the coding-agent persona in place, which cost forty
        # thousand tokens of irrelevant instruction per call and — worse — left
        # the model reaching for a tool on the first turn and the run aborting
        # against --max-turns. This is a diagnostic API, not a coding session.
        "--system-prompt", SYSTEM + "\n\n" + extra_system,
        # Belt and braces: even with the persona gone the harness offers tools,
        # and a diagnosis must come from the bundle rather than from the disk.
        "--disallowedTools", "Bash", "Read", "Write", "Edit", "Glob", "Grep",
        "WebSearch", "WebFetch", "Task", "Agent", "TodoWrite", "NotebookEdit",
        # A stray tool call should cost a turn, not the whole answer.
        "--max-turns", "4",
    ]
    env = dict(os.environ)
    # Whatever config directory the desktop happens to be running under, the
    # advisor uses the primary login — otherwise the answer depends on which
    # terminal launched the server.
    env.pop("CLAUDE_CONFIG_DIR", None)
    started = time.time()
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                          timeout=TIMEOUT, env=env, cwd=records.STATE)
    took = time.time() - started
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "claude failed").strip()[:400])
    try:
        env_out = json.loads(proc.stdout)
    except ValueError:
        raise RuntimeError("claude did not return JSON")
    if env_out.get("is_error"):
        raise RuntimeError(str(env_out.get("result"))[:400])
    return env_out.get("result", ""), took, env_out


def validate(kind, data, b):
    """Drop anything the bundle does not support, and say what was dropped.

    This is the load-bearing honesty check. A finding that cites evidence which
    is not in the bundle is a finding about a car we do not have.
    """
    if not isinstance(data, dict):
        return None, ["reply was not an object"]
    keys = evidence_keys(b)
    dropped = []

    def check(items, label):
        kept = []
        for it in items or []:
            if not isinstance(it, dict):
                continue
            ev = it.get("evidence")
            if isinstance(ev, list) and ev:
                bad = [e for e in ev if str(e).split("[")[0] not in keys
                       and str(e) not in keys]
                if bad and len(bad) == len(ev):
                    dropped.append(f"{label}: {it.get('title') or it.get('what') or '?'} "
                                   f"(cited {', '.join(str(x) for x in bad[:3])})")
                    continue
                it["evidence"] = [e for e in ev if e not in bad]
            kept.append(it)
        return kept

    if kind == "triage":
        data["findings"] = check(data.get("findings"), "finding")
    if kind == "predict":
        data["predictions"] = check(data.get("predictions"), "prediction")
    if kind == "recording":
        data["findings"] = check(data.get("findings"), "finding")
    if kind == "symptom":
        data["hypotheses"] = check(data.get("hypotheses"), "hypothesis")
    return data, dropped


def ask(kind="triage", question=None, code=None, model=None, refresh=False,
        span=None):
    """One grounded answer, cached against the evidence it was given."""
    if kind not in SCHEMAS:
        raise ValueError(f"unknown kind {kind!r}")
    b = bundle(kind, code=code, span=span)
    template = PROMPTS[kind]
    task = template.format(code=code or "", question=question or "")
    if span:
        task += (f"\n\nThe recording covers "
                 f"{datetime.fromtimestamp(span[0]).strftime('%d %b %H:%M:%S')} to "
                 f"{datetime.fromtimestamp(span[1]).strftime('%H:%M:%S')}.")

    prompt = (
        f"{task}\n\n"
        f"Reply with JSON in exactly this shape:\n{SCHEMAS[kind]}\n\n"
        f"EVIDENCE BUNDLE for this vehicle:\n"
        f"```json\n{json.dumps(b, indent=1, default=str)}\n```\n"
    )
    key = cache_key(kind, task, b)
    path = cache_path(key)
    if not refresh and os.path.exists(path):
        try:
            with open(path) as f:
                hit = json.load(f)
            hit["cached"] = True
            return hit
        except (OSError, ValueError):
            pass

    if not available():
        raise RuntimeError(
            "the `claude` CLI is not installed — the advisor needs it. "
            "Everything else in OmaCar works without it.")

    extra = ("Cite evidence using the bundle's own keys, e.g. \"faults.P0135\", "
             "\"mode06.0x39\", \"perf.month\", \"service.items\". Do not cite a "
             "key that is not in the bundle.")
    text, took, envelope = run_claude(prompt, model or MODEL_FOR.get(kind, DEFAULT_MODEL),
                                      extra)
    data = extract_json(text)
    if data is None:
        raise RuntimeError("the advisor did not return usable JSON")
    data, dropped = validate(kind, data, b)

    out = {
        "kind": kind,
        "question": question,
        "code": code,
        "at": int(time.time()),
        "took_s": round(took, 1),
        "model": _answering_model(envelope, model)
                 or (model or DEFAULT_MODEL),
        "data": data,
        "dropped": dropped,
        "evidence_keys": sorted(evidence_keys(b)),
        "cached": False,
    }
    try:
        with open(path, "w") as f:
            json.dump(out, f)
    except OSError:
        pass
    try:
        records.write_record("ai", f"{kind}: {question or code or ''}".strip(),
                             {"kind": kind, "headline": _headline(data)})
    except Exception:
        pass
    return out


def _headline(data):
    for k in ("headline", "answer", "summary"):
        v = (data or {}).get(k)
        if v:
            return str(v)[:200]
    return ""


def history(n=30):
    db = records.connect()
    out = records.records(db, kind="ai", n=n) if db else []
    if db:
        db.close()
    return out


# ---- terminal ---------------------------------------------------------------

def _print(out):
    d = out["data"]
    tag = "  (cached)" if out.get("cached") else f"  ({out['took_s']}s, {out['model']})"
    print()
    if d.get("headline"):
        print(f"  {d['headline']}{tag}")
    elif d.get("answer"):
        print(f"  {d['answer']}\n{tag}")
    elif d.get("summary"):
        print(f"  {d['summary']}{tag}")
    print()
    for f in d.get("findings", []):
        print(f"  [{f.get('severity', '?')}] {f.get('title')}   "
              f"{f.get('confidence', '?')}% confident")
        print(f"     {f.get('what', '')}")
        if f.get("why"):
            print(f"     why: {f['why']}")
        if f.get("action"):
            print(f"     do:  {f['action']}"
                  + (f"   ({f['cost']})" if f.get("cost") else ""))
        if f.get("evidence"):
            print(f"     from: {', '.join(str(x) for x in f['evidence'])}")
        print()
    for i, step in enumerate(d.get("order", []), 1):
        print(f"  {i}. {step}")
    for p in d.get("predictions", []):
        print(f"  {p.get('what')}  —  {p.get('when')}   "
              f"{p.get('confidence', '?')}% confident")
        if p.get("basis"):
            print(f"     {p['basis']}")
        if p.get("prevent"):
            print(f"     avoid it: {p['prevent']}")
        print()
    for r in d.get("ranked", []):
        print(f"  {r.get('likelihood', '?'):>3}%  {r.get('cause')}"
              + (f"   ({r['cost']})" if r.get("cost") else ""))
        if r.get("why"):
            print(f"        {r['why']}")
        if r.get("test"):
            print(f"        test: {r['test']}")
    if d.get("safety"):
        print(f"\n  SAFETY  {d['safety']}")
    if d.get("emissions"):
        print(f"  EMISSIONS  {d['emissions']}")
    for c in d.get("caveats", []):
        print(f"  caveat: {c}")
    if out.get("dropped"):
        print("\n  dropped as unsupported by the evidence:")
        for x in out["dropped"]:
            print(f"    - {x}")
    print()


def main(argv):
    kind = argv[0] if argv else "triage"
    if kind in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if not available():
        print("omacar ai: the `claude` CLI is not installed.", file=sys.stderr)
        return 1
    rest = [a for a in argv[1:] if a != "--refresh"]
    refresh = "--refresh" in argv
    question = " ".join(rest) if rest else None
    code = question if kind == "code" else None
    try:
        out = ask(kind, question=question if kind == "ask" else None,
                  code=code, refresh=refresh)
    except Exception as e:
        print(f"omacar ai: {e}", file=sys.stderr)
        return 1
    if "--json" in argv:
        print(json.dumps(out, indent=2))
        return 0
    _print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
