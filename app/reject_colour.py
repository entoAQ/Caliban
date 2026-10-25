"""Larva share of a reject-stream photo by colour and texture -- no model, no
AI call.

The tray rig's reject photos (capture_commands kind 'capture_reject') show
what the destoner threw out on the white tray: larvae mixed with frass. The
question for each one is whether there are enough larvae in it to be worth
running back through the destoner. The reject-vision prompt answers that
with a guess (larves_pct, rounded to 5, +/-10-15 points); this measures it
from the pixels, so the two can be compared and the measurement calibrated
against weighed samples (rig/reject_colour_readings.sql).

Three rules, tuned by eye on ten rig photos of 2026-10-16/18 (2048 x 1152
review copies -- the same size measure() is given):
  * tray     -- bright and nearly colourless; the light stripes across it
                are only a darker grey, still colourless.
  * material -- everything else that is grainy: the local spread of
                brightness over ~15 px above TEX. Shadows along the tray's
                edge are dark but smooth, and were counted as frass without
                this.
  * larva    -- material that is warm and saturated (golden-tan); the rest
                of the material is frass (dull grey-brown).

The larva figure is a share of the visible AREA of the material, not of its
weight: larvae are denser than crumbly frass, so the weight share is likely
higher. That conversion is what the weighed samples are for.

Only meaningful on photos the rig takes: a handheld shot, tilted and dim,
loses the larvae's colour and reads far too low.
"""

import io

import numpy as np
from PIL import Image

VERSION = "colour-v1"
TRAY_SAT, TRAY_BRIGHT = 0.20, 120
TEX, TEX_WINDOW = 8, 15
LARVA_SAT, LARVA_WARM, LARVA_BRIGHT = 0.38, 30, 70


def _box_mean(x, k):
    """Mean of x over a k x k window (anchored at the bottom-right corner;
    the half-window offset does not matter for a threshold)."""
    c = np.cumsum(np.cumsum(np.pad(x, ((k, 0), (k, 0))), 0), 1)
    return (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / (k * k)


def measure(contents):
    """JPEG/PNG bytes -> {"larva_area_pct", "material_pct", "method_version"}.
    larva_area_pct is None when there is almost no material to judge."""
    a = np.asarray(Image.open(io.BytesIO(contents)).convert("RGB")).astype(np.float32)
    r, b = a[..., 0], a[..., 2]
    mx, mn = a.max(axis=2), a.min(axis=2)
    sat = (mx - mn) / np.maximum(mx, 1)

    tray = (sat < TRAY_SAT) & (mx > TRAY_BRIGHT)
    grey = a.mean(axis=2)
    spread = np.sqrt(np.maximum(_box_mean(grey ** 2, TEX_WINDOW) - _box_mean(grey, TEX_WINDOW) ** 2, 0))
    material = ~tray & (spread > TEX)
    larva = material & (sat > LARVA_SAT) & (r > b + LARVA_WARM) & (mx > LARVA_BRIGHT)

    n = int(material.sum())
    material_pct = 100.0 * n / material.size
    return {
        "larva_area_pct": round(100.0 * int(larva.sum()) / n, 1) if material_pct >= 1.0 else None,
        "material_pct": round(material_pct, 1),
        "method_version": VERSION,
    }
