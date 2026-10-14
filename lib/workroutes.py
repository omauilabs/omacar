"""The app's side of Work: the page's clips, queued on disk, sent to the relay.

The page never talks to the relay. It posts here; the clip is written to the
queue at once and a sender forwards it oldest first, so a dead zone on the
Marina-Los Banos road costs a delay, not a clip. The car's identifying strings
ride along with each forward, read fresh from the vehicle record, and are never
written into the queue. Every request to the relay carries its bearer token
(the relay's token file on the tablet, work.toml's relay_token on the box); the
token is never logged or written anywhere by this module.
"""
import base64
import json
import os
import re
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import records, workcfg, workprivacy  # noqa: E401,E402

QUEUE = os.path.join(records.STATE, "work-queue")
ANSWER_S = 5.0
CID = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def _token():
    """The relay's bearer token, or "" when there is none to send."""
    cfg = workcfg.load()
    if cfg.host == "tablet":
        import workrelay                     # heavy; only the tablet reads its file
        return workrelay.read_token()
    return cfg.relay_token


def _request(url, data=None):
    tok = _token()
    if not tok:
        raise OSError("no relay token")      # offline: nothing is sent without it
    headers = {"Authorization": "Bearer " + tok}
    if data is not None:
        headers["Content-Type"] = "application/json"
    return urllib.request.Request(url, data=data, headers=headers)


def _post(url, payload, timeout):
    req = _request(url, json.dumps(payload).encode())
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def _secrets():
    try:
        db = records.connect()
        return workprivacy.secrets_from(records.vehicle(db))
    except Exception:                                          # noqa: BLE001
        return []


class Sender:
    def __init__(self, relay_url, queue_dir=QUEUE, post=_post, secrets=_secrets,
                 clock=time.time):
        self.url, self.dir, self.post, self.secrets = relay_url, queue_dir, post, secrets
        self.clock, self.seq = clock, 0
        os.makedirs(self.dir, exist_ok=True)
        self.lock = threading.Lock()
        self.online = None

    def enqueue(self, item):
        cid = str(item.get("client_id", ""))
        if not CID.match(cid):
            return False
        with self.lock:
            if any(f.endswith(f"-{cid}.json") for f in os.listdir(self.dir)):
                return True                                # the page retried; one clip
            rec = {k: item.get(k) for k in ("client_id", "audio_b64", "mime")}
            # Named by time, so the oldest is first by name: mtimes can tie.
            self.seq += 1
            name = f"{int(self.clock() * 1e6):018d}{self.seq % 10000:04d}-{cid}"
            tmp = os.path.join(self.dir, name + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rec, f)
            os.replace(tmp, os.path.join(self.dir, name + ".json"))
        return True

    def pending(self):
        return sorted(f for f in os.listdir(self.dir) if f.endswith(".json"))

    def drain_once(self):
        sent = 0
        with self.lock:
            for name in self.pending():
                p = os.path.join(self.dir, name)
                try:
                    with open(p, encoding="utf-8") as f:
                        rec = json.load(f)
                    self.post(self.url + "/utterance", dict(rec, secrets=self.secrets()), ANSWER_S)
                except (OSError, ValueError):
                    self.online = False
                    break
                os.remove(p)
                self.online = True
                sent += 1
        return sent


_sender = None
_sender_lock = threading.Lock()


def sender():
    global _sender
    with _sender_lock:
        if _sender is None:
            _sender = Sender(workcfg.load().relay_url)

            def loop():
                while True:
                    _sender.drain_once()
                    time.sleep(2)
            threading.Thread(target=loop, daemon=True).start()
    return _sender


def _get(path):
    try:
        with urllib.request.urlopen(_request(sender().url + path), timeout=ANSWER_S) as r:
            return r.read(), r.headers.get("Content-Type", "")
    except OSError:
        return None, ""


def _json_of(raw):
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def handle_get(path, query):
    if path == "/api/work/status":
        raw, _ = _get("/health")
        return 200, dict(_json_of(raw), online=raw is not None,
                         queued=len(sender().pending()))
    if path == "/api/work/events":
        after = 0
        m = re.search(r"(?:^|&)after=(\d+)", query or "")
        if m:
            after = int(m.group(1))
        raw, _ = _get(f"/events?after={after}")
        events = _json_of(raw).get("events", []) if raw else []
        return 200, {"events": events, "online": raw is not None,
                     "queued": len(sender().pending())}
    if path.startswith("/api/work/clip/"):
        name = os.path.basename(path)
        if not re.match(r"^\d+\.wav$", name):
            return 404, {"error": "no such clip"}
        raw, _ = _get(f"/clip/{name}")
        if not raw:
            return 404, {"error": "no such clip"}
        return 200, {"wav_b64": base64.b64encode(raw).decode()}
    return None


def handle_post(path, body):
    if path != "/api/work/utterance":
        return None
    try:
        data = json.loads(body or "{}")
    except ValueError:
        return 400, {"error": "bad json"}
    if not isinstance(data, dict) or not sender().enqueue(data):
        return 400, {"error": "bad client id"}
    threading.Thread(target=sender().drain_once, daemon=True).start()
    return 200, {"queued": True}


if __name__ == "__main__":
    # `omacar work status`: the relay's health through the same authenticated
    # path the page uses, so a bare curl's 403 is not the answer.
    if sys.argv[1:] == ["status"]:
        raw, _ = _get("/health")
        print(json.dumps(dict(_json_of(raw), online=raw is not None)))
        sys.exit(0 if raw is not None else 1)
    sys.exit("usage: workroutes.py status")
