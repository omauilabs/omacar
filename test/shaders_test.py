#!/usr/bin/env python3
"""Every shader the music screen ships actually compiles.

THE CLASS OF BUG THIS CATCHES.

A fragment shader that fails to compile does not throw, does not stop the page,
and does not draw anything. WebGL hands the error to a function nobody called
and the canvas stays the colour it was cleared to, which on a dark theme is
indistinguishable from a shader that compiled fine and is drawing black. The
first anybody knows is a driver looking at an empty screen.

That is exactly the shape of the bug the asset test already guards elsewhere in
this suite -- a stylesheet that was written, was correct, and was never loaded.
The fix is the same: check the thing at build time rather than trusting it.

The shaders are extracted from share/js/views/music.js rather than kept in
separate files, because a copy in test/ would be the copy that stayed correct
while the shipped one rotted. This reads what the browser will actually run.

SKIPPED, LOUDLY, WITHOUT A COMPILER. glslangValidator is in the `glslang`
package and is not a dependency of this project -- OmaCar has none. A machine
without it says so and passes, because a test that cannot run is not a failure;
a test that silently does nothing is.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MUSIC = os.path.join(ROOT, "share", "js", "views", "music.js")

fails = 0


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"    FAIL  {msg}")


def sources():
    """(name, stage, source) for every shader in the music view."""
    text = open(MUSIC, encoding="utf-8").read()
    head = re.search(r"const HEAD = `(.*?)`;", text, re.S)
    vert = re.search(r"const VERT = `(.*?)`;", text, re.S)
    if not head or not vert:
        return None
    out = [("vertex quad", "vert", vert.group(1))]
    for m in re.finditer(
            r"(\w+): \{\s*\n\s*name: \"([^\"]+)\",\s*\n\s*what: \"[^\"]*\",\s*\n"
            r"\s*src: HEAD \+ `(.*?)`,", text, re.S):
        out.append((m.group(2), "frag", head.group(1) + m.group(3)))
    return out


def main():
    print("\n  The shaders the music screen ships\n")
    got = sources()
    if got is None:
        bad("could not find the shader sources in share/js/views/music.js — "
            "the extraction pattern and the file have drifted apart")
        return 1

    frags = [g for g in got if g[1] == "frag"]
    if len(frags) < 1:
        bad("no fragment shaders were extracted")
        return 1
    ok(f"{len(frags)} fragment shader(s) and a vertex shader extracted")

    # Every shader must declare a precision: WebGL requires it for floats in a
    # fragment shader and a missing one is a compile error on some drivers and
    # not others, which is the worst kind.
    for name, stage, src in frags:
        if "precision" not in src:
            bad(f"{name}: no precision qualifier")
        else:
            ok(f"{name}: declares a float precision")

    exe = shutil.which("glslangValidator")
    if not exe:
        print("\n    (skipping the compile: glslangValidator is not installed —\n"
              "     `sudo pacman -S glslang` to have this test mean something)\n")
        return 0 if not fails else 1

    tmp = tempfile.mkdtemp()
    try:
        for name, stage, src in got:
            path = os.path.join(tmp, re.sub(r"\W+", "_", name) + f".{stage}")
            with open(path, "w", encoding="utf-8") as f:
                f.write(src)
            # 100 is GLSL ES 1.00, which is the dialect WebGL 1 accepts and the
            # one the renderer asks for.
            r = subprocess.run([exe, "--glsl-version", "100", "-S", stage, path],
                               capture_output=True, text=True, check=False)
            if r.returncode == 0:
                ok(f"{name}: compiles as GLSL ES 1.00")
            else:
                detail = (r.stdout or r.stderr or "").strip().splitlines()
                bad(f"{name}: does not compile — "
                    + " / ".join(d.strip() for d in detail[:3]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fails:
        print(f"  {fails} failed\n")
        return 1
    print("  every shader compiles\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
