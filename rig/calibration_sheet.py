#!/usr/bin/env python3
"""Generate the rig's calibration sheet.

One printed page that does the jobs the empty tray cannot:

  focus     Autofocus needs contrasty detail and fails on a uniform surface,
            which is exactly what a clean tray is. The star and the slanted
            edges give it something to lock onto.
  scale     A printed ruler measured through the actual lens at the actual
            working distance gives pixels-per-millimetre directly, with no
            arithmetic and no assumption about where the sensor plane sits.
            It is also what tells you later whether the ToF is honest.
  geometry  Four fiducial crosses at a known 200 x 150mm. Compare the spacings
            and you learn whether the boom is square to the tray: on a tilted
            boom the near edge images larger than the far one, so opposite
            spacings disagree. That is a check nothing else on the rig makes.

Deliberately not on the sheet: grey patches. Neither inkjet nor laser output
is colorimetric, and a patch that is approximately grey is worse than none,
because it looks authoritative while being wrong by an unknown amount. White
balance belongs on the tray itself, which is in every real capture anyway.

Writes an HTML page with the drawing inline as SVG in real millimetres. HTML
rather than a bare SVG because a browser's print dialog honours @page, so the
sheet comes out the size it says it is -- and the 100mm verification bar is
there to prove it did.

    python3 calibration_sheet.py            # Letter, the North American default
    python3 calibration_sheet.py --a4
"""

import argparse
import math
from pathlib import Path

# Landscape, to match the camera's 16:9 frame rather than fight it.
LETTER = (279.4, 215.9)
A4 = (297.0, 210.0)

# The fiducial rectangle. Round numbers on purpose: every measurement made
# from this sheet is a division by one of them, and dividing by 200 is a sum
# you can check in your head at the bench.
FID_W, FID_H = 200.0, 150.0


def svg(page):
    w, h = page
    cx, cy = w / 2, h / 2
    out = []
    add = out.append

    add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}mm" height="{h}mm" '
        f'viewBox="0 0 {w} {h}">')
    add(f'<rect width="{w}" height="{h}" fill="#fff"/>')

    # --- Siemens star -----------------------------------------------------
    # Wedges converging on a point present every spatial frequency at once, so
    # the radius at which they blur into grey reads off the resolution
    # directly. More useful than a bar target here, which only answers about
    # the frequencies you thought to print.
    spokes, r = 36, 34.0
    for i in range(spokes):
        a0 = 2 * math.pi * i / spokes
        a1 = a0 + math.pi / spokes
        x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
        x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
        add(f'<path d="M{cx:.2f},{cy:.2f} L{x0:.2f},{y0:.2f} '
            f'A{r},{r} 0 0 1 {x1:.2f},{y1:.2f} Z" fill="#000"/>')
    add(f'<circle cx="{cx}" cy="{cy}" r="2.5" fill="#fff"/>')

    # --- Fiducial crosses -------------------------------------------------
    fx0, fx1 = cx - FID_W / 2, cx + FID_W / 2
    fy0, fy1 = cy - FID_H / 2, cy + FID_H / 2
    for x in (fx0, fx1):
        for y in (fy0, fy1):
            add(f'<path d="M{x - 6},{y} H{x + 6} M{x},{y - 6} V{y + 6}" '
                f'stroke="#000" stroke-width="0.4"/>')
            add(f'<circle cx="{x}" cy="{y}" r="1.2" fill="none" '
                f'stroke="#000" stroke-width="0.4"/>')
    add(f'<text x="{cx}" y="{fy0 - 4}" font-family="sans-serif" font-size="4" '
        f'text-anchor="middle">crosses are 200.0 x 150.0 mm apart</text>')

    # --- Slanted edges ----------------------------------------------------
    # Five degrees off square, so the edge crosses the pixel grid at a shallow
    # angle and samples the transition at many sub-pixel offsets. An edge
    # parallel to the rows tells you almost nothing.
    for sx, sy in ((fx0 + 26, fy0 + 26), (fx1 - 26, fy0 + 26),
                   (fx0 + 26, fy1 - 26), (fx1 - 26, fy1 - 26)):
        add(f'<rect x="{sx - 11}" y="{sy - 11}" width="22" height="22" '
            f'fill="#000" transform="rotate(5 {sx} {sy})"/>')

    # --- Line pairs -------------------------------------------------------
    # Coarse to fine. Whichever group first turns to mush is roughly where the
    # system stops resolving, which is a blunter answer than the star gives
    # but an easier one to state in a report.
    lx, ly = fx0 + 8, cy - 22
    for pitch in (2.0, 1.0, 0.5, 0.25):
        for i in range(6):
            add(f'<rect x="{lx + i * pitch:.3f}" y="{ly}" '
                f'width="{pitch / 2:.3f}" height="14" fill="#000"/>')
        add(f'<text x="{lx}" y="{ly + 18}" font-family="sans-serif" '
            f'font-size="3">{pitch}mm</text>')
        ly += 24

    # --- Text block -------------------------------------------------------
    ty = cy - 22
    for size in (4.0, 3.0, 2.0, 1.5, 1.0):
        add(f'<text x="{fx1 - 8}" y="{ty}" font-family="serif" '
            f'font-size="{size}" text-anchor="end">'
            f'{size}mm the quick brown fox</text>')
        ty += size + 5

    # --- Ruler ------------------------------------------------------------
    # Aligned to the left fiducial so the ruler and the crosses agree, and a
    # disagreement between them means the print scaled.
    ry = fy1 - 12
    add(f'<path d="M{fx0},{ry} H{fx0 + 200}" stroke="#000" stroke-width="0.3"/>')
    for mm in range(0, 201):
        if mm % 10 == 0:
            tick, width = 5.0, 0.4
        elif mm % 5 == 0:
            tick, width = 3.0, 0.3
        else:
            tick, width = 1.5, 0.2
        add(f'<path d="M{fx0 + mm},{ry} V{ry - tick}" stroke="#000" '
            f'stroke-width="{width}"/>')
        if mm % 50 == 0:
            add(f'<text x="{fx0 + mm}" y="{ry + 4}" font-family="sans-serif" '
                f'font-size="3.2" text-anchor="middle">{mm}</text>')

    # --- Print verification ----------------------------------------------
    # The one thing that must be checked before the sheet is trusted. Every
    # number derived from it is a ratio against a printed distance, so a page
    # that came out at 96% makes every measurement 4% wrong, silently and
    # consistently -- the worst way to be wrong.
    vy = fy1 + 8
    add(f'<path d="M{cx - 50},{vy} H{cx + 50}" stroke="#000" stroke-width="0.6"/>')
    add(f'<path d="M{cx - 50},{vy - 2.5} V{vy + 2.5} M{cx + 50},{vy - 2.5} '
        f'V{vy + 2.5}" stroke="#000" stroke-width="0.6"/>')
    add(f'<text x="{cx}" y="{vy + 7}" font-family="sans-serif" font-size="3.6" '
        f'text-anchor="middle">'
        f'Print at 100% / actual size. This bar must measure exactly 100 mm.'
        f'</text>')

    add(f'<text x="4" y="{h - 3}" font-family="sans-serif" font-size="3" '
        f'fill="#888">Caliban bench rig calibration sheet</text>')
    add('</svg>')
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--a4", action="store_true", help="A4 instead of Letter")
    ap.add_argument("--out", default="calibration_sheet.html")
    args = ap.parse_args()

    page = A4 if args.a4 else LETTER
    w, h = page
    html = (
        "<!doctype html>\n<html><head><meta charset='utf-8'>\n"
        "<title>Caliban rig calibration sheet</title>\n<style>\n"
        f"  @page {{ size: {w}mm {h}mm; margin: 0; }}\n"
        "  html, body { margin: 0; padding: 0; background: #fff; }\n"
        "  svg { display: block; }\n"
        "</style></head><body>\n" + svg(page) + "\n</body></html>\n"
    )

    out = Path(args.out)
    out.write_text(html, encoding="utf-8")
    print(f"Wrote {out} ({'A4' if args.a4 else 'Letter'}, landscape)")
    print("Open it in a browser and print at 100% with margins set to none.")


if __name__ == "__main__":
    main()
