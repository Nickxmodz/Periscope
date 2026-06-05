"""
make_icon.py — generates assets/periscope.ico for the bundled executable.

Draws a clean submarine-periscope glyph (vertical shaft, bent head with a lens
and three "sightlines") on a rounded blue tile. Rendered at 4x and downsampled
for crisp anti-aliasing, then packed into a multi-resolution .ico.

Run:  python assets/make_icon.py
"""

import os
from PIL import Image, ImageDraw

S = 1024                      # supersampled master size
SS = 4                        # extra oversample for the master itself
N = S * SS

BG_TOP    = (33, 118, 235)    # #2176eb
BG_BOTTOM = (10, 61, 98)      # #0a3d62
SCOPE     = (244, 248, 252)   # near-white body
LENS      = (90, 214, 224)    # cyan glass
LENS_DK   = (32, 92, 110)


def rounded_rect(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def make_master():
    img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # --- vertical gradient tile ---
    tile = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    td = ImageDraw.Draw(tile)
    for y in range(N):
        t = y / (N - 1)
        r = round(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t)
        g = round(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t)
        b = round(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t)
        td.line([(0, y), (N, y)], fill=(r, g, b, 255))
    # rounded mask
    mask = Image.new("L", (N, N), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, N - 1, N - 1], radius=int(N * 0.22), fill=255)
    img.paste(tile, (0, 0), mask)

    # work in 0..1024 coords scaled by SS
    def sc(v):
        return int(v * SS)

    thick = sc(120)          # shaft / head thickness
    r_round = sc(60)

    # vertical shaft
    shaft_x = sc(430)
    rounded_rect(d, [shaft_x, sc(330), shaft_x + thick, sc(800)],
                 r_round, SCOPE)

    # bent head: horizontal arm going right from top of shaft
    arm_y = sc(330)
    rounded_rect(d, [shaft_x, arm_y, sc(720), arm_y + thick],
                 r_round, SCOPE)

    # lens housing at the right end of the arm
    lens_cx, lens_cy = sc(700), sc(390)
    lr = sc(118)
    d.ellipse([lens_cx - lr, lens_cy - lr, lens_cx + lr, lens_cy + lr],
              fill=SCOPE)
    lr2 = sc(82)
    d.ellipse([lens_cx - lr2, lens_cy - lr2, lens_cx + lr2, lens_cy + lr2],
              fill=LENS_DK)
    lr3 = sc(60)
    d.ellipse([lens_cx - lr3, lens_cy - lr3, lens_cx + lr3, lens_cy + lr3],
              fill=LENS)

    # three sightlines radiating to the right of the lens
    sl_x0 = sc(840)
    for dy, length in [(-110, 150), (0, 200), (110, 150)]:
        y = lens_cy + sc(dy)
        rounded_rect(d, [sl_x0, y - sc(26), sl_x0 + sc(length), y + sc(26)],
                     sc(26), SCOPE)

    # eyepiece handle at the bottom of the shaft
    rounded_rect(d, [sc(360), sc(740), shaft_x + thick + sc(30), sc(800)],
                 r_round, SCOPE)

    return img.resize((S, S), Image.LANCZOS)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    master = make_master()
    master.save(os.path.join(here, "periscope.png"))

    sizes = [256, 128, 64, 48, 32, 16]
    icons = [master.resize((s, s), Image.LANCZOS) for s in sizes]
    out = os.path.join(here, "periscope.ico")
    icons[0].save(out, format="ICO",
                  sizes=[(s, s) for s in sizes],
                  append_images=icons[1:])
    print("wrote", out)


if __name__ == "__main__":
    main()
