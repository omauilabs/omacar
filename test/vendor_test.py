#!/usr/bin/env python3
"""What drowsy mode needs and must never fetch on the road: MediaPipe's
tasks-vision 1.0.1 engine and the Face Landmarker model.

They are not in git. share/assets/manifest.json lists them under `fetch`, with
the sizes the owner approved and SHA-256 pins, and `omacar assets fetch` puts
them in share/js/vendor/mediapipe/ once, at install. This checks the fetch
itself against local files, the shipped list, and the files installed here --
including `--from DIR`, the offline path that installs from a local backup
instead of the URL, verified against the same pins."""

import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import assets  # noqa: E402

SIZES = {"vision_bundle.mjs": 155439, "wasm/vision_wasm_internal.js": 323377,
         "wasm/vision_wasm_internal.wasm": 11756954, "face_landmarker.task": 3758596}
fails = 0


def head(t):
    print(f"\n  {t}")


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"  FAIL  {msg}")


def check(msg, got, want):
    if got == want:
        ok(msg)
    else:
        bad(f"{msg} (wanted {want!r}, got {got!r})")


def raises(fn):
    try:
        fn()
        return False
    except ValueError:
        return True


head("fetch downloads once, checks the size, and holds every file to its pin")
d = tempfile.mkdtemp(prefix="omacar-fetch-")
src = os.path.join(d, "source.bin")
with open(src, "wb") as f:
    f.write(b"engine" * 100)
mf = os.path.join(d, "manifest.json")


def manifest(**entry):
    e = dict({"url": "file://" + src, "file": "out/engine.bin", "bytes": 600, "sha256": None}, **entry)
    with open(mf, "w", encoding="utf-8") as fh:
        json.dump({"assets": {}, "fetch": {"engine": e}}, fh)


manifest()
check("an entry with no pin is refused, unless asked to pin it",
      raises(lambda: assets.fetch(manifest_path=mf, root=d)), True)
check("and nothing half-downloaded is left behind", os.listdir(os.path.join(d, "out")), [])
check("with --pin it is fetched", assets.fetch(pin=True, manifest_path=mf, root=d), {"engine": "fetched"})
_pinned = json.load(open(mf, encoding="utf-8"))["fetch"]["engine"]["sha256"]
check("and the pin is written into the manifest", _pinned, assets.sha256(src))
check("a second fetch finds it already here", assets.fetch(manifest_path=mf, root=d), {"engine": "already here"})
os.remove(os.path.join(d, "out", "engine.bin"))
manifest(sha256="0" * 64)
check("a download that does not match its pin is refused",
      raises(lambda: assets.fetch(manifest_path=mf, root=d)), True)
manifest(bytes=599)
check("so is one of the wrong size, before its hash is even taken",
      raises(lambda: assets.fetch(pin=True, manifest_path=mf, root=d)), True)
shutil.rmtree(d)

head("fetch --from installs local copies instead of downloading, verified the same way")
d = tempfile.mkdtemp(prefix="omacar-fetch-from-")
fixture = os.path.join(d, "fixture")
os.makedirs(fixture)
good = os.path.join(fixture, "engine.bin")
with open(good, "wb") as f:
    f.write(b"engine" * 100)
mf = os.path.join(d, "manifest.json")


def manifest_from(**entry):
    e = dict({"url": "https://example.invalid/engine.bin", "file": "out/engine.bin", "bytes": 600,
              "sha256": assets.sha256(good)}, **entry)
    with open(mf, "w", encoding="utf-8") as fh:
        json.dump({"assets": {}, "fetch": {"engine": e}}, fh)


manifest_from()
check("a good copy in the fixture directory installs, and the URL is never touched",
      assets.fetch(manifest_path=mf, root=d, from_dir=fixture), {"engine": "fetched"})
check("landing at the manifest's file path", os.path.isfile(os.path.join(d, "out", "engine.bin")), True)
check("with the fixture's own bytes", open(os.path.join(d, "out", "engine.bin"), "rb").read(), open(good, "rb").read())
os.remove(os.path.join(d, "out", "engine.bin"))
with open(good, "wb") as f:
    f.write(b"TAMPERED" * 75)  # still 600 bytes, but the wrong ones
check("a tampered copy is refused, exactly as a bad download would be",
      raises(lambda: assets.fetch(manifest_path=mf, root=d, from_dir=fixture)), True)
check("and nothing half-installed is left behind", os.path.exists(os.path.join(d, "out", "engine.bin")), False)
shutil.rmtree(d)

head("the shipped list: MediaPipe 1.0.1 and the model, at the approved sizes, pinned")
m = assets.load_manifest()["fetch"]
check("four files, and no no-SIMD pair",
      sorted(e["file"].split("share/js/vendor/mediapipe/")[1] for e in m.values()), sorted(SIZES))
check("each at the size the owner approved",
      {e["file"].split("share/js/vendor/mediapipe/")[1]: e["bytes"] for e in m.values()}, SIZES)
check("each from https", all(e["url"].startswith("https://") for e in m.values()), True)
check("and each pinned", [n for n, e in m.items() if not e.get("sha256")], [])
tracked = subprocess.run(["git", "-C", ROOT, "ls-files", "share/js/vendor/mediapipe"],
                         capture_output=True, text=True)
if tracked.returncode == 0:
    check("nothing under share/js/vendor/mediapipe is in git", tracked.stdout.split(), [])
else:
    ok("(not a git checkout: the tracked-files check is skipped)")

head("installed here")
st = assets.fetch_status()
check("every file is here and matches its pin (if not: omacar assets fetch)",
      {n: s["why"] for n, s in st.items() if not s["ok"]}, {})

print()
if fails:
    print(f"  {fails} failed\n")
    sys.exit(1)
print("  nothing will be fetched on the road\n")
