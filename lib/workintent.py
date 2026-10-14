"""What the driver meant: one transcript, one checked intent.

The model proposes; Python decides. A project must be on the allowlist, a new
task must say what to do, and a confirmation is a yes only when somebody said
yes -- silence, a cough or an unrelated sentence is never one.
"""
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

KINDS = {"new_task", "status", "stop", "result"}
YES = {"yes", "yeah", "yep", "sure", "ok", "okay", "go", "start it", "do it", "go ahead"}
NO = {"no", "nope", "cancel", "stop", "don't", "dont", "cancel that", "never mind"}
PROMPT = """You turn one spoken sentence from a driver into JSON. Kinds:
new_task (start work: project + instruction), status (what's going on),
stop (stop a session: project), result (read me the result: project).
Projects: {projects}. Reply with only a JSON object:
{{"kind": ..., "project": ... or null, "instruction": ... or "", "session": null}}
Sentence: {said}"""


@dataclass(frozen=True)
class Intent:
    kind: str
    project: str | None = None
    instruction: str = ""
    session: str | None = None
    answer: str = ""
    choices: tuple = ()


def _words(s):
    return re.sub(r"[^a-z0-9' ]", " ", s.lower()).split()


def _phrase(s):
    return " ".join(_words(s))


def _dist(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def match_project(said, cfg):
    said = _phrase(said)
    exact = [n for n, p in cfg.projects.items() if said in p.names]
    if exact:
        return exact
    w = said.split()
    grams = {" ".join(w[i:i + k]) for k in (1, 2, 3) for i in range(len(w) - k + 1)} or {said}
    # One slip for a short name, two for a long one: "omakar" is omacar, but
    # "omalab" is not (two slips in six letters is a different word).
    close = [n for n, p in cfg.projects.items()
             if any(_dist(g, name) <= (1 if len(name) <= 6 else 2)
                    for g in grams for name in p.names if len(name) >= 4)]
    return close or [n for n, p in cfg.projects.items()
                     if any(name.startswith(said) for name in p.names) and len(said) >= 2]


FILLER = {"please", "it", "now"}
STOP = {"stop", "cancel", "never mind", "stop it", "cancel that"}
# With nothing pending, a bare stop names no project: the relay stops the one
# running session, or asks which when there are two.
BARE_STOP = {"stop", "stop it"}
NO_WORDS = {w for phrase in NO for w in phrase.split()}


def _exact(said, vocab):
    """The whole utterance is a phrase from vocab, plus at most one filler word."""
    if said in vocab:
        return True
    w = said.split()
    return len(w) > 1 and w[-1] in FILLER and " ".join(w[:-1]) in vocab


def _claude(prompt):
    import ai
    import records
    proc = subprocess.run(
        [ai.claude_bin() or "claude", "-p", "--output-format", "json",
         "--model", "haiku", "--max-turns", "1", "--strict-mcp-config",
         "--disallowedTools", "Bash", "Read", "Write", "Edit", "Glob", "Grep",
         "WebSearch", "WebFetch", "Task", "Agent", "TodoWrite", "NotebookEdit"],
        input=prompt, capture_output=True, text=True, timeout=30,
        cwd=records.STATE)
    if proc.returncode != 0:
        raise RuntimeError("intent: claude failed")
    text = json.loads(proc.stdout).get("result", "")
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else {}


def parse(transcript, cfg, *, pending, ask_model=None):
    said = _phrase(transcript)
    if pending == "confirm":
        # Strict: a yes is an exact phrase (plus one filler) with no NO word
        # anywhere. "ok cancel", "yes stop", "go to omalens" are never a yes.
        if _exact(said, NO):
            return Intent("no")
        if _exact(said, YES) and not NO_WORDS & set(said.split()):
            return Intent("yes")
        return Intent("unclear")
    if pending == "question":
        if said in STOP:
            return Intent("stop")
        return Intent("answer", answer=transcript.strip())
    if not said:
        return Intent("unclear")
    if said in BARE_STOP:
        return Intent("stop")
    try:
        got = (ask_model or _claude)(PROMPT.format(
            projects=", ".join(sorted(cfg.projects)), said=transcript))
    except Exception:                                    # noqa: BLE001
        return Intent("unclear")
    if not isinstance(got, dict) or got.get("kind") not in KINDS:
        return Intent("unclear")
    kind = got["kind"]
    if kind == "status":
        return Intent("status")
    names = match_project(str(got.get("project") or ""), cfg) if got.get("project") else []
    if len(names) > 1:
        return Intent("choose", choices=tuple(sorted(names)))
    if not names:
        return Intent("unclear")
    # The transcript checks the model: if the driver's own words point at other
    # projects than the one the model picked, ask rather than guess.
    heard = match_project(transcript, cfg)
    if heard and names[0] not in heard:
        return Intent("choose", choices=tuple(sorted(set(heard) | {names[0]})))
    if kind == "new_task" and not str(got.get("instruction") or "").strip():
        return Intent("unclear")
    return Intent(kind, project=names[0], instruction=str(got.get("instruction") or "").strip())
