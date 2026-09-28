#!/usr/bin/env python3
"""Private assets: pictures that ship in the app but not in the public repo.

omauilabs/omacar is public, and the car renders carry Honda's badge. The owner
wants them in the app and not in git, so they live in share/assets/private/,
which .gitignore excludes, and the committed share/assets/manifest.json names
each one: its file, what it is for, where its callouts point and, once pinned,
its SHA-256. A missing or altered file is reported rather than drawn broken,
and the app falls back to a placeholder.

    omacar assets status             what is here, against the manifest
    omacar assets pin NAME           record the present file's hash and size
    omacar assets push [--to DEST]   copy this machine's files to the tablet
    omacar assets sync [--from SRC]  copy them here from somewhere else

Stdlib only; the copies are rsync over ssh.
"""

import hashlib
import json
import os
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "share", "assets", "manifest.json")
PRIVATE = os.path.join(ROOT, "share", "assets", "private")
# The tablet as the Omarchy box's ~/.ssh/config names it (Host omacar), and the
# box as the tablet can reach it over the tailnet.
DEFAULT_TO = "omacar:Projects/omacar/share/assets/private/"
DEFAULT_FROM = "jmyers@omarchy:Projects/omacar/share/assets/private/"
URL_BASE = "assets/private/"


def load_manifest(path=MANIFEST):
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc.get("assets"), dict):
        raise ValueError("the manifest has no `assets` object")
    return doc


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def png_size(path):
    """(width, height) from a PNG's IHDR, or None when it is not a PNG."""
    with open(path, "rb") as f:
        head = f.read(24)
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def status(manifest=None, private=PRIVATE):
    """{name: {file, present, ok, why}} for every asset the manifest names."""
    m = manifest if manifest is not None else load_manifest()
    out = {}
    for name, a in m["assets"].items():
        f = a.get("file") or ""
        p = os.path.join(private, f)
        row = {"file": f, "present": False, "ok": False, "why": "not installed"}
        if f and os.path.isfile(p):
            row["present"] = True
            want = a.get("sha256")
            if want and sha256(p) != want:
                row["why"] = "does not match the manifest"
            else:
                row["ok"] = True
                row["why"] = None if want else "not pinned"
        out[name] = row
    return out


def public_view(manifest=None, private=PRIVATE):
    """What /api/assets hands the browser: a URL only for a file that checks out."""
    m = manifest if manifest is not None else load_manifest()
    st = status(m, private)
    return {name: {"url": URL_BASE + a["file"] if st[name]["ok"] else None,
                   "why": st[name]["why"],
                   "anchors": a.get("anchors") or {},
                   "width": a.get("width"),
                   "height": a.get("height")}
            for name, a in m["assets"].items()}


def pin(name, path=MANIFEST, private=PRIVATE):
    m = load_manifest(path)
    if name not in m["assets"]:
        raise ValueError(f"no asset called {name!r} in the manifest")
    a = m["assets"][name]
    p = os.path.join(private, a["file"])
    if not os.path.isfile(p):
        raise ValueError(f"{a['file']} is not in {private}")
    a["sha256"] = sha256(p)
    size = png_size(p)
    if size:
        a["width"], a["height"] = size
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return a


def _rsync(src, dest):
    if not src.endswith("/"):
        src += "/"
    return subprocess.run(["rsync", "-a", "--chmod=F644,D755", src, dest]).returncode


def _opt(args, flag, default):
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args):
            return args[i + 1]
    return default


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    args = argv[2:]
    if cmd == "status":
        missing = 0
        for name, s in status().items():
            note = f"  ({s['why']})" if s["why"] else ""
            print(f"  {'ok' if s['ok'] else '--'}  {name:<10} {s['file']}{note}")
            missing += 0 if s["ok"] else 1
        return 1 if missing else 0
    if cmd == "pin":
        if not args:
            print("usage: omacar assets pin NAME", file=sys.stderr)
            return 2
        a = pin(args[0])
        print(f"  pinned {args[0]}: {a['sha256'][:16]}…")
        return 0
    if cmd == "push":
        os.makedirs(PRIVATE, exist_ok=True)
        return _rsync(PRIVATE, _opt(args, "--to", DEFAULT_TO))
    if cmd == "sync":
        os.makedirs(PRIVATE, exist_ok=True)
        return _rsync(_opt(args, "--from", DEFAULT_FROM), PRIVATE + "/")
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
