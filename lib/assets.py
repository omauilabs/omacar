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
    omacar assets fetch [--pin]      download what the `fetch` list names, once,
                                     and check each file against its pin
    omacar assets fetch --from DIR   install the `fetch` list from local copies in
                                     DIR instead of downloading, still checked
                                     against the same pins

Stdlib only; the copies are rsync over ssh.
"""

import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import urllib.request

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


# ---- fetched once, at install ---------------------------------------------
#
# Files too big for a public repository and free to download: MediaPipe's
# engine and face model. The manifest's `fetch` list names each one's source,
# size and, once pinned, SHA-256. `omacar assets fetch` downloads what is
# missing or wrong and refuses anything that is not what the list says. It
# runs at install; the app never fetches anything, on the road or anywhere.
#
# `--from DIR` installs from local copies (named by basename, in DIR) instead
# of the URL -- the offline path, run from the Omarchy box's backup copy at
# ~/Projects/.omacar-vendor-backup/mediapipe-1.0.1/ when there is no signal.
# Every file is still checked against the same pin; a mismatch is refused
# exactly as a bad download would be.

def fetch_status(manifest=None, root=ROOT):
    """{name: {file, present, ok, why}} for every `fetch` entry."""
    m = manifest if manifest is not None else load_manifest()
    out = {}
    for name, e in (m.get("fetch") or {}).items():
        p = os.path.join(root, e["file"])
        row = {"file": e["file"], "present": os.path.isfile(p), "ok": False, "why": "not fetched"}
        if row["present"]:
            if e.get("bytes") and os.path.getsize(p) != e["bytes"]:
                row["why"] = "the wrong size"
            elif not e.get("sha256"):
                row["why"] = "not pinned"
            elif sha256(p) != e["sha256"]:
                row["why"] = "does not match the manifest"
            else:
                row["ok"], row["why"] = True, None
        out[name] = row
    return out


def _download(url, dest):
    with urllib.request.urlopen(url, timeout=60) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 16)


def fetch(names=None, pin=False, manifest_path=MANIFEST, root=ROOT, from_dir=None):
    """Download every `fetch` entry that is missing or wrong, then check it:
    first the size the list names, then the pin. With `pin`, an entry with no
    pin yet is pinned to what arrived. That is done once, where the download
    is trusted, and the manifest is committed. With `from_dir`, each file is
    installed from a local copy named by its basename in that directory
    instead of downloaded from its URL, and is verified exactly the same way."""
    m = load_manifest(manifest_path)
    done, pinned = {}, False
    for name, e in (m.get("fetch") or {}).items():
        if names and name not in names:
            continue
        if fetch_status({"fetch": {name: e}}, root)[name]["ok"]:
            done[name] = "already here"
            continue
        dest = os.path.join(root, e["file"])
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        part = dest + ".part"
        try:
            if from_dir is not None:
                source = os.path.join(from_dir, os.path.basename(e["file"]))
                if not os.path.isfile(source):
                    raise ValueError(f"{name}: {source} is not there")
                shutil.copyfile(source, part)
            else:
                _download(e["url"], part)
            size = os.path.getsize(part)
            if e.get("bytes") and size != e["bytes"]:
                raise ValueError(f"{name}: {size} bytes arrived; the manifest says {e['bytes']}")
            digest = sha256(part)
            if e.get("sha256"):
                if digest != e["sha256"]:
                    raise ValueError(f"{name}: what arrived does not match its pin")
            elif pin:
                e["sha256"] = digest
                pinned = True
            else:
                raise ValueError(f"{name}: not pinned yet; fetch it once with --pin where the download is trusted")
            os.replace(part, dest)
            done[name] = "fetched"
        finally:
            if os.path.exists(part):
                os.remove(part)
    if pinned:
        tmp = manifest_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(m, f, indent=2)
            f.write("\n")
        os.replace(tmp, manifest_path)
    return done


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
        for name, s in fetch_status().items():
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
    if cmd == "fetch":
        try:
            got = fetch(pin="--pin" in args, from_dir=_opt(args, "--from", None))
            for name, what in got.items():
                print(f"  {what:<13} {name}")
        except (OSError, ValueError) as e:
            print(f"omacar: {e}", file=sys.stderr)
            return 1
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
