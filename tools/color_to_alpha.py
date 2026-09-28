#!/usr/bin/env python3
"""Recover a translucent render from a picture of it on a flat white ground.

The X-ray car in the owner's mockup kit is a render on white, and its body
panels are see-through: a flood fill or a threshold would keep a white halo
round every panel or punch holes in them. Colour-to-alpha is the inverse of
compositing onto white, pixel by pixel, so a panel that was 40% opaque comes
out 40% opaque and lands correctly on OmaCar's dark ground.

For a white ground the maths is short. A pixel c was made as
    c = a*f + (1 - a)*255
and the most transparent answer that explains it has a = (255 - min(c)) / 255,
so f = 255 - (255 - c) / a for each channel.

Needs Pillow, which is why it is in tools/ and not lib/: it runs on the machine
that prepares assets (the Mac), never on the tablet.

    tools/color_to_alpha.py IN OUT
"""

import sys

from PIL import Image

# JPEG and WebP grounds are never quite 255. Anything this close to white is
# ground, not a panel -- without the floor the whole canvas gets a faint haze.
FLOOR = 6


def color_to_alpha_white(img):
    src = img.convert("RGB")
    out = []
    for r, g, b in src.getdata():
        a = 255 - min(r, g, b)
        if a <= FLOOR:
            out.append((0, 0, 0, 0))
            continue
        k = 255.0 / a
        out.append((max(0, min(255, round(255 - (255 - r) * k))),
                    max(0, min(255, round(255 - (255 - g) * k))),
                    max(0, min(255, round(255 - (255 - b) * k))),
                    a))
    res = Image.new("RGBA", src.size)
    res.putdata(out)
    return res


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    color_to_alpha_white(Image.open(argv[1])).save(argv[2], optimize=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
