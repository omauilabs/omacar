#!/usr/bin/env python3
"""The accessibility promises that can be read straight off the files.

The audit (axe-core 4.10, WCAG 2.2 A and AA plus best practice, on all 46
screens) found six kinds of failure on 2026-10-17; each fix has a line here so
it cannot quietly come back. What needs a running page (contrast as drawn,
target sizes, focus order) is test/app_test.py's and test/design_test.py's.

  * every page says its language (WCAG 3.1.1);
  * no page stops pinch-zoom (WCAG 1.4.4);
  * text is never drawn in --ghost, the decorative tone (2.0:1 on a card);
  * no table has a column header with nothing in it;
  * the page has its live regions, and a11y.js fills them;
  * every screen gets a level-one heading, and focus goes to it;
  * a Home card is not a button with buttons inside it;
  * access.css switches off every animation when reduce motion is on;
  * the flash for sounds can never strobe (WCAG 2.3.1);
  * no outline:none without a focus ring to replace it.
"""

import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
SHARE = os.path.join(ROOT, "share")

fails = 0


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"    FAIL  {msg}")


def check(msg, cond):
    (ok if cond else bad)(msg)


def read(*parts):
    with open(os.path.join(SHARE, *parts), encoding="utf-8") as f:
        return f.read()


def main():
    print("\n  pages")
    for page in sorted(glob.glob(os.path.join(SHARE, "*.html"))):
        name = os.path.basename(page)
        text = open(page, encoding="utf-8").read()
        check(f"{name} says its language", re.search(r'<html[^>]*\blang="[a-z]{2}', text) is not None)
        vp = re.search(r'<meta name="viewport" content="([^"]*)"', text)
        check(f"{name} lets a person zoom",
              not vp or not re.search(r"user-scalable\s*=\s*(no|0)|maximum-scale\s*=\s*1(\.0)?\b", vp.group(1)))

    app = read("app.html")
    check("app.html has a polite and an urgent live region",
          'id="say-polite" aria-live="polite"' in app and 'id="say-urgent" role="alert"' in app)
    check("app.html loads access.css", 'href="css/access.css"' in app)

    print("\n  styles")
    ghost_text = []
    for css in sorted(glob.glob(os.path.join(SHARE, "css", "*.css"))):
        for n, line in enumerate(open(css, encoding="utf-8"), 1):
            if re.search(r"(^|[;{\s])color:\s*var\(--ghost\)", line):
                ghost_text.append(f"{os.path.basename(css)}:{n}")
    check("no text is drawn in --ghost (" + (", ".join(ghost_text) or "none") + ")", not ghost_text)

    access = read("css", "access.css")
    check("reduce motion stops every animation and transition",
          ":root[data-a11y-still] *" in access and "animation-duration: 0.01ms !important" in access
          and "transition-duration: 0.01ms !important" in access)
    check("every control has a focus ring", re.search(r"^:focus-visible \{ outline: 3px", access, re.M) is not None)
    check("large targets move the shared tap tokens", ":root[data-a11y-big] { --tap: 64px" in access)

    bare = []
    for css in sorted(glob.glob(os.path.join(SHARE, "css", "*.css"))):
        for n, line in enumerate(open(css, encoding="utf-8"), 1):
            if line.lstrip().startswith(("/*", "*")) or "NO outline" in line:
                continue
            if re.search(r"outline:\s*(none|0)\b", line) and "tabindex=\"-1\"" not in line \
                    and "border-color" not in line and ".fl-step" not in line:
                bare.append(f"{os.path.basename(css)}:{n}")
    check("no outline:none without a replacement (" + (", ".join(bare) or "none") + ")", not bare)

    print("\n  scripts")
    a11y = read("js", "a11y.js")
    m = re.search(r"export const FLASH_GAP = (\d+);", a11y)
    check("the flash is at most one every 2 s (never 3 a second)", m and int(m.group(1)) >= 2000)
    check("every dialog is named, keeps Tab inside, and hands focus back",
          "aria-labelledby" in a11y and 'e.key !== "Tab"' in a11y and "openers.get(box)" in a11y)

    main_js = read("js", "main.js")
    check("every screen gets a level-one heading, and focus goes to it",
          'h("h1.sr-only", view.title)' in main_js and "heading.focus(" in main_js)
    check("OmaAccess's text size multiplies OmaCar's own", "* accessScale()" in main_js)

    home = read("js", "views", "home.js")
    tap = home[home.index("function tappable("):home.index("// ---- the cards")]
    check("a Home card is not itself a button (it holds buttons)",
          '"role"' not in tap and "tabIndex" not in tap and 'h("button.hc-go"' in tap)

    empty_th = []
    for js in sorted(glob.glob(os.path.join(SHARE, "js", "**", "*.js"), recursive=True)):
        for n, line in enumerate(open(js, encoding="utf-8"), 1):
            if re.search(r'h\("th[^"]*", ""\)', line):
                empty_th.append(f"{os.path.relpath(js, SHARE)}:{n}")
    check("no empty table header (" + (", ".join(empty_th) or "none") + ")", not empty_th)

    core = read("js", "core.js")
    check("a dangerous confirm does not start with focus on the danger",
          '.btn:not(." + tone + ")' in core)

    print(f"\n  {'all good' if not fails else f'{fails} failed'}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
