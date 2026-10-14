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
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import records, workcfg, workprivacy  # noqa: E401,E402

QUEUE = os.path.join(records.STATE, "work-queue")
ANSWER_S = 5.0
CID = re.compile(r"^[A-Za-z0-9-]{1,64}$")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A relay never redirects; following one could carry the bearer token to
    another host."""
    def redirect_request(self, *a, **kw):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _open(req, timeout):
    return _OPENER.open(req, timeout=timeout)


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
    with _open(req, timeout) as r:
        return json.loads(r.read() or b"{}")


def _secrets():
    """The car's identifying strings. No vehicle record is [] (nothing to scrub);
    a read that fails raises, so the clip stays queued rather than going out
    unscrubbed."""
    db = records.connect()
    return workprivacy.secrets_from(records.vehicle(db))


# A relay answer that says the clip itself is bad (not the link, not the token):
# keep it out of the way instead of letting it stall every clip behind it.
RETRY_CODES = (401, 403, 408, 429)


class Sender:
    def __init__(self, relay_url, queue_dir=QUEUE, post=_post, secrets=_secrets,
                 clock=time.time):
        self.url, self.dir, self.post, self.secrets = relay_url, queue_dir, post, secrets
        self.clock, self.seq = clock, 0
        self.bad = os.path.join(self.dir, "bad")
        os.makedirs(self.dir, exist_ok=True)
        for f in os.listdir(self.dir):                    # a write that never finished
            if f.endswith(".tmp"):
                try: os.remove(os.path.join(self.dir, f))
                except OSError: pass
        self.write_lock = threading.Lock()                # dedupe + write: never waits on the network
        self.drain_lock = threading.Lock()                # one drain at a time
        self.online = None

    @staticmethod
    def _id_of(name):
        """The client id in a queue file name: 22 digits, a dash, the id, .json."""
        return name[:-5].split("-", 1)[1] if name.endswith(".json") and "-" in name else None

    def enqueue(self, item):
        cid = str(item.get("client_id", ""))
        if not CID.match(cid):
            return False
        with self.write_lock:
            if any(self._id_of(f) == cid for f in self.pending()):
                return True                                # the page retried; one clip
            rec = {k: item.get(k) for k in ("client_id", "audio_b64", "mime")}
            # Named by time, so the oldest is first by name: mtimes can tie.
            self.seq += 1
            name = f"{int(self.clock() * 1e6):018d}{self.seq % 10000:04d}-{cid}"
            tmp = os.path.join(self.dir, name + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rec, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, os.path.join(self.dir, name + ".json"))
        return True

    def pending(self):
        return sorted(f for f in os.listdir(self.dir) if f.endswith(".json"))

    def _quarantine(self, name):
        try:
            os.makedirs(self.bad, exist_ok=True)       # made when first needed
            os.replace(os.path.join(self.dir, name), os.path.join(self.bad, name))
        except OSError:
            pass

    def drain_once(self):
        """Send the queue oldest first; returns the count sent. A bad clip goes
        to bad/ and the rest continue; a dead link, a refused token or a relay
        error stops the drain and keeps everything."""
        if not self.drain_lock.acquire(blocking=False):
            return 0                                       # a drain is already running
        sent = 0
        try:
            for name in self.pending():                    # a snapshot; enqueue goes on meanwhile
                p = os.path.join(self.dir, name)
                try:
                    with open(p, encoding="utf-8") as f:
                        rec = json.load(f)
                    if not isinstance(rec, dict) or not rec.get("audio_b64"):
                        raise ValueError("empty clip")
                except (OSError, ValueError):
                    self._quarantine(name)
                    continue
                try:
                    secrets = self.secrets()
                    self.post(self.url + "/utterance", dict(rec, secrets=secrets), ANSWER_S)
                except urllib.error.HTTPError as e:
                    if 400 <= e.code < 500 and e.code not in RETRY_CODES:
                        self._quarantine(name)
                        continue
                    self.online = False
                    break
                except Exception:                          # noqa: BLE001  link, secrets, anything else
                    self.online = False
                    break
                os.remove(p)
                self.online = True
                sent += 1
        finally:
            self.drain_lock.release()
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
        with _open(_request(sender().url + path), ANSWER_S) as r:
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
