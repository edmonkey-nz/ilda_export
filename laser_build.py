# -*- coding: utf-8 -*-
"""
laser_build.py -- Convert projected polylines into blanked, normalised ILDA frames.

Compatible with Python 2.7 (C4D R21) and Python 3 (R23+). No Cinema 4D dependency,
so the scaling + blanking logic (the part that decides whether a laser draws a clean
image) is unit-tested on its own and reusable in a laser-synth pipeline.

Input model
-----------
vertex    = (x, y, z, (r, g, b))   -- coords in an arbitrary projected plane; 0..255 rgb
polyline  = list of vertices       -- one continuous lit stroke
frame     = list of polylines
clip      = list of frames

One global bounding box is computed across the whole clip so the image does not
"breathe" (rescale) frame to frame, then mapped into the ILDA device range with a
margin. Per frame the builder emits: a blanked travel move to each stroke's start
(repeated blank_dwell times so the galvos arrive), the lit stroke (first lit point
repeated on_dwell times to settle), and optional loop closure.
"""

from __future__ import division, print_function

__version__ = "1.0.0"

from ilda_format import IldaPoint, COORD_MAX


class BuildOptions(object):
    def __init__(self, invert_y=True, margin=0.06, blank_dwell=3,
                 on_dwell=1, true_color_3d=False):
        self.invert_y = invert_y
        self.margin = margin
        self.blank_dwell = max(1, int(blank_dwell))
        self.on_dwell = max(1, int(on_dwell))
        self.true_color_3d = true_color_3d


def _global_bounds(clip):
    minx = miny = minz = float("inf")
    maxx = maxy = maxz = float("-inf")
    any_pt = False
    for frame in clip:
        for poly in frame:
            for vert in poly:
                x, y, z = vert[0], vert[1], vert[2]
                any_pt = True
                if x < minx: minx = x
                if x > maxx: maxx = x
                if y < miny: miny = y
                if y > maxy: maxy = y
                if z < minz: minz = z
                if z > maxz: maxz = z
    if not any_pt:
        return (0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
    return (minx, miny, minz, maxx, maxy, maxz)


def _make_transform(bounds, opts):
    minx, miny, minz, maxx, maxy, maxz = bounds
    cx = 0.5 * (minx + maxx)
    cy = 0.5 * (miny + maxy)
    cz = 0.5 * (minz + maxz)
    span = max(maxx - minx, maxy - miny, 1e-9)  # uniform scale preserves aspect
    scale = (COORD_MAX * (1.0 - opts.margin)) / (0.5 * span)

    def xf(x, y, z):
        dx = (x - cx) * scale
        dy = (y - cy) * scale
        if opts.invert_y:
            dy = -dy
        dz = (z - cz) * scale if opts.true_color_3d else 0.0
        return dx, dy, dz

    return xf


def build_clip(clip, opts=None):
    """clip: list of frames of polylines. Returns list of frames of IldaPoint."""
    if opts is None:
        opts = BuildOptions()
    xf = _make_transform(_global_bounds(clip), opts)

    out_frames = []
    for frame in clip:
        pts = []
        for poly in frame:
            if not poly:
                continue
            # Blanked travel move to the stroke's first vertex.
            sx, sy, sz, srgb = poly[0]
            dx, dy, dz = xf(sx, sy, sz)
            sr, sg, sb = srgb
            for _ in range(opts.blank_dwell):
                pts.append(IldaPoint(dx, dy, dz, sr, sg, sb, blank=True))
            # Lit stroke.
            for j, vert in enumerate(poly):
                x, y, z, rgb = vert
                r, g, b = rgb
                ddx, ddy, ddz = xf(x, y, z)
                reps = opts.on_dwell if j == 0 else 1
                for _ in range(reps):
                    pts.append(IldaPoint(ddx, ddy, ddz, r, g, b, blank=False))
        # A frame with no lit geometry still needs one (blanked) point to be valid.
        if not pts:
            pts.append(IldaPoint(0, 0, 0, 0, 0, 0, blank=True))
        out_frames.append(pts)
    return out_frames
