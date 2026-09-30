#!/usr/bin/env python3
"""Home's car picture for the meetup demo, cut from the owner's mockup 3.

    python3 tools/demo_carpic.py --mock doc/design/mockups/3.webp \\
        --out share/assets/private/demo/crz-home.png

Mockup 3 (landscape Home, 1536 x 1024) draws the CR-Z in the middle of the top
row with a tyre temperature at each corner. This cuts the car out of it: the
four labels and their leader lines are painted out with the background around
them, and the edge is feathered to nothing by a soft oval around the car, so
the picture sits on the app's dark cards with no box around it: the car, the
glow behind it and the lit floor under it, fading into the card. The demo
serves it at /demo-media/crz-home.png (demo/js/boot.js, Home's car card).

The picture carries Honda's badge and is the owner's, so it is private
(share/assets/private/ is git-ignored) and never committed. Pillow only, which
is on the box; it reads the mockup and writes one file, and nothing else.

Where things are, in the 1536 x 1024 frame (a mockup of another size is
scaled), found by looking: the box, x 300-772 and y 182-487, as far as the
speed dial on the left, the navigation card on the right, the top bar and the
tiles go; the car in it, x 325-730 and y 230-445; and each label.
"""

import argparse
import os
import sys

try:
    from PIL import Image, ImageDraw, ImageFilter
except ImportError:                     # pragma: no cover - the box has it
    print("demo_carpic: needs Pillow (python3 -c 'import PIL')", file=sys.stderr)
    sys.exit(1)

FRAME = (1536, 1024)
BOX = (300, 182, 772, 487)
CAR = (325, 230, 730, 445)
# The tyre labels' words ...
LABELS = [(368, 220, 412, 258),         # 72°F FL
          (704, 220, 748, 258),         # 74°F FR
          (323, 432, 364, 472),         # 73°F RL
          (704, 432, 748, 472)]         # 75°F RR
# ... and their leader lines, each from the words towards the car.
LEADERS = [((404, 251), (430, 276)),
           ((720, 259), (688, 280)),
           ((341, 431), (360, 412)),
           ((709, 431), (690, 412))]
LEADER_WIDTH = 12

# THE OVAL. An ellipse around the car, a little wider and taller than it, is
# opaque out to OVAL_IN of its radius and gone at OVAL_OUT: the car whole, the
# background soft. The box is tight (the car's nose is 25 px from its left
# edge), so the oval alone would leave the box's sides showing; each side also
# fades to nothing over EDGE pixels, and the picture is the product of the two.
OVAL_AXES = (1.06, 1.19)               # of the car's half width and half height
OVAL_IN, OVAL_OUT = 1.08, 1.5
EDGE = {"left": 22, "right": 40, "top": 40, "bottom": 42}


def scaled(size):
    sx, sy = size[0] / FRAME[0], size[1] / FRAME[1]
    return lambda x, y: (round(x * sx), round(y * sy))


def paint_out(img):
    """The labels and leaders gone: each pixel under them replaced by a wide
    median of the picture, which keeps the background's gradient and drops
    strokes a few pixels thick. Only under the marks, and feathered."""
    at = scaled(img.size)
    mask = Image.new("L", img.size, 0)
    d = ImageDraw.Draw(mask)
    for a, b, c, e in LABELS:
        d.rectangle([at(a, b), at(c, e)], fill=255)
    for p, q in LEADERS:
        d.line([at(*p), at(*q)], fill=255, width=max(3, round(LEADER_WIDTH * img.size[0] / FRAME[0])))
    mask = mask.filter(ImageFilter.GaussianBlur(1.5))
    # Median by channel: Pillow's MedianFilter works on each band alone.
    size = 21 if img.size[0] >= FRAME[0] else 15
    bands = [b.filter(ImageFilter.MedianFilter(size)) for b in img.split()]
    smooth = Image.merge(img.mode, bands).filter(ImageFilter.GaussianBlur(2))
    return Image.composite(smooth, img, mask)


def smooth(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def oval_alpha(size, box, car, k=1.0):
    """The alpha for the cut-out box: the oval around the car, times each
    side's fade. `box` and `car` in the picture's own pixels, `k` its scale
    against the 1536 x 1024 frame."""
    w, h = size
    cx = (car[0] + car[2]) / 2 - box[0]
    cy = (car[1] + car[3]) / 2 - box[1]
    ax = (car[2] - car[0]) / 2 * OVAL_AXES[0]
    ay = (car[3] - car[1]) / 2 * OVAL_AXES[1]
    edge = {side: max(1.0, px * k) for side, px in EDGE.items()}
    data = []
    for y in range(h):
        dy = ((y + 0.5 - cy) / ay) ** 2
        ey = smooth((y + 0.5) / edge["top"]) * smooth((h - y - 0.5) / edge["bottom"])
        for x in range(w):
            r = ((x + 0.5 - cx) / ax) ** 2 + dy
            r = r ** 0.5
            oval = 1.0 if r <= OVAL_IN else smooth((OVAL_OUT - r) / (OVAL_OUT - OVAL_IN))
            ex = smooth((x + 0.5) / edge["left"]) * smooth((w - x - 0.5) / edge["right"])
            data.append(round(oval * ex * ey * 255))
    alpha = Image.new("L", (w, h))
    alpha.putdata(data)
    return alpha


def cut(mock, box=BOX, car=CAR):
    img = Image.open(mock).convert("RGB")
    at = scaled(img.size)
    clean = paint_out(img)
    b = (*at(box[0], box[1]), *at(box[2], box[3]))
    c = (*at(car[0], car[1]), *at(car[2], car[3]))
    out = clean.crop(b).convert("RGBA")
    out.putalpha(oval_alpha(out.size, b, c, img.size[0] / FRAME[0]))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Cut Home's car picture out of mockup 3.")
    ap.add_argument("--mock", required=True, help="mockup 3 (doc/design/mockups/3.webp)")
    ap.add_argument("--out", required=True, help="where to write the PNG")
    ap.add_argument("--box", help="the box to cut, in the 1536 x 1024 frame: x0,y0,x1,y1")
    a = ap.parse_args(argv)
    box = BOX
    if a.box:
        try:
            box = tuple(int(v) for v in a.box.split(","))
            assert len(box) == 4 and box[0] < box[2] and box[1] < box[3]
        except (ValueError, AssertionError):
            ap.error("--box is x0,y0,x1,y1, whole pixels, x0 < x1 and y0 < y1")
    if not os.path.isfile(a.mock):
        ap.error(f"no mockup at {a.mock}")
    car = cut(a.mock, box)
    out = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".tmp.png"
    car.save(tmp, optimize=True)
    os.replace(tmp, out)
    print(f"  {out}  {car.size[0]} x {car.size[1]}, {os.path.getsize(out) // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
