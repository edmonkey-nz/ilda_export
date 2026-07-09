# -*- coding: utf-8 -*-
"""
ilda_format.py -- Pure-Python reader/writer for the ILDA Image Data Transfer Format.

Compatible with Python 2.7 (Cinema 4D R21) *and* Python 3 (R23+). No third-party
dependencies. Deliberately decoupled from Cinema 4D so the byte encoding can be
unit-tested on its own and reused elsewhere (e.g. inside laser-synth).

Supports the two true-colour formats (explicit RGB, no palette needed):
  * Format 5 -- 2D true colour (X, Y)        ->  8 bytes / record
  * Format 4 -- 3D true colour (X, Y, Z)     -> 10 bytes / record

Header layout (32 bytes; multi-byte numeric fields are big-endian):
   0   "ILDA"                4s      16   company name          8s
   4   reserved (0)          3s      24   number of records     H  (0 => EOF)
   7   format code           B       26   frame number          H
   8   frame / palette name  8s       28   total frames          H
                                     30   projector number      B
                                     31   reserved (0)          B

Per-point status byte:  bit 7 (0x80) = last point of frame,
                        bit 6 (0x40) = blanking (1 = laser off).

Reference: ILDA Technical Standard, "ILDA Image Data Transfer Format", rev 011.
"""

from __future__ import division, print_function

__version__ = "1.0.0"

import struct

ILDA_MAGIC = b"ILDA"

COORD_MIN = -32768
COORD_MAX = 32767

STATUS_LAST_POINT = 0x80   # bit 7: final point of a frame
STATUS_BLANKING = 0x40     # bit 6: 1 = blanked (laser off)

_HEADER = struct.Struct(">4s3sB8s8sHHHBB")
assert _HEADER.size == 32, "ILDA header must be 32 bytes"

_REC_2D = struct.Struct(">hhBBBB")    # X, Y,    status, B, G, R
_REC_3D = struct.Struct(">hhhBBBB")   # X, Y, Z, status, B, G, R


class IldaPoint(object):
    """A single galvo target. Coordinates are already in ILDA device space.

    Plain class (not a dataclass) so it works under Python 2.7 in C4D R21.
    """
    __slots__ = ("x", "y", "z", "r", "g", "b", "blank")

    def __init__(self, x, y, z=0, r=255, g=255, b=255, blank=False):
        self.x = x
        self.y = y
        self.z = z
        self.r = r
        self.g = g
        self.b = b
        self.blank = blank

    def __repr__(self):
        return "IldaPoint(%r,%r,%r,rgb=(%r,%r,%r),blank=%r)" % (
            self.x, self.y, self.z, self.r, self.g, self.b, self.blank)


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _fit8(s):
    raw = s.encode("ascii", "replace")[:8]
    return raw + b"\x00" * (8 - len(raw))


def _pack_header(fmt, name, company, records, frame_no, total, projector=0):
    return _HEADER.pack(
        ILDA_MAGIC, b"\x00\x00\x00", fmt,
        _fit8(name), _fit8(company),
        records & 0xFFFF, frame_no & 0xFFFF, total & 0xFFFF,
        projector & 0xFF, 0,
    )


def save(frames, path, true_color_3d=False, name="C4D", company="ILDAEXP"):
    """Write a list of frames (each a list of IldaPoint) to an .ild file.

    A trailing zero-record header is appended as the end-of-file marker, per spec.
    Returns the number of frames written.
    """
    fmt = 4 if true_color_3d else 5
    rec = _REC_3D if true_color_3d else _REC_2D
    total = len(frames)

    f = open(path, "wb")
    try:
        for fi, pts in enumerate(frames):
            n = len(pts)
            if n > 0xFFFF:
                raise ValueError(
                    "Frame %d has %d points; ILDA record count is a uint16 "
                    "(max 65535). Reduce sampling density." % (fi, n))
            f.write(_pack_header(fmt, name, company, n, fi, total))
            for i, p in enumerate(pts):
                status = 0
                if p.blank:
                    status |= STATUS_BLANKING
                if i == n - 1:
                    status |= STATUS_LAST_POINT
                x = _clamp(int(round(p.x)), COORD_MIN, COORD_MAX)
                y = _clamp(int(round(p.y)), COORD_MIN, COORD_MAX)
                r = _clamp(int(p.r), 0, 255)
                g = _clamp(int(p.g), 0, 255)
                b = _clamp(int(p.b), 0, 255)
                if true_color_3d:
                    z = _clamp(int(round(p.z)), COORD_MIN, COORD_MAX)
                    f.write(rec.pack(x, y, z, status, b, g, r))
                else:
                    f.write(rec.pack(x, y, status, b, g, r))
        # End-of-file marker: a header carrying zero records.
        f.write(_pack_header(fmt, name, company, 0, total, total))
    finally:
        f.close()
    return total


def read(path):
    """Read an .ild file (formats 4 and 5) into a list of frames of IldaPoint.

    Provided for round-trip testing and reuse; ignores palette formats.
    """
    f = open(path, "rb")
    try:
        data = f.read()
    finally:
        f.close()

    frames = []
    off = 0
    while off + _HEADER.size <= len(data):
        unpacked = _HEADER.unpack_from(data, off)
        magic = unpacked[0]
        fmt = unpacked[2]
        records = unpacked[5]
        off += _HEADER.size
        if magic != ILDA_MAGIC:
            raise ValueError("Bad ILDA magic at offset %d" % (off - _HEADER.size))
        if records == 0:
            break  # EOF marker

        pts = []
        if fmt == 5:
            for _ in range(records):
                x, y, status, b, g, r = _REC_2D.unpack_from(data, off)
                off += _REC_2D.size
                pts.append(IldaPoint(x, y, 0, r, g, b, bool(status & STATUS_BLANKING)))
        elif fmt == 4:
            for _ in range(records):
                x, y, z, status, b, g, r = _REC_3D.unpack_from(data, off)
                off += _REC_3D.size
                pts.append(IldaPoint(x, y, z, r, g, b, bool(status & STATUS_BLANKING)))
        else:
            raise ValueError("Reader supports formats 4 and 5 only (got %d)" % fmt)
        frames.append(pts)
    return frames
