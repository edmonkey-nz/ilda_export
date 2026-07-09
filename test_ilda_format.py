import os
import struct
import tempfile

import ilda_format as ild
from ilda_format import IldaPoint


def _tmp():
    fd, path = tempfile.mkstemp(suffix=".ild")
    os.close(fd)
    return path


def test_header_bytes():
    path = _tmp()
    frames = [[IldaPoint(0, 0, r=10, g=20, b=30)]]
    ild.save(frames, path, name="SQUARE", company="LSYNTH")
    with open(path, "rb") as f:
        head = f.read(32)
    magic, res, fmt, name, company, recs, fno, total, proj, r2 = \
        struct.unpack(">4s3sB8s8sHHHBB", head)
    assert magic == b"ILDA"
    assert res == b"\x00\x00\x00"
    assert fmt == 5, "2D true colour => format 5"
    assert name.rstrip(b"\x00") == b"SQUARE"
    assert company.rstrip(b"\x00") == b"LSYNTH"
    assert recs == 1 and fno == 0 and total == 1
    assert proj == 0 and r2 == 0
    print("PASS header_bytes")


def test_status_bits_last_and_blank():
    path = _tmp()
    frame = [
        IldaPoint(100, 100, blank=True),   # travel
        IldaPoint(200, 200, blank=False),  # draw
        IldaPoint(300, 300, blank=False),  # draw + last
    ]
    ild.save([frame], path)
    with open(path, "rb") as f:
        f.read(32)  # skip header
        statuses = []
        for _ in range(3):
            _x, _y, status, _b, _g, _r = struct.unpack(">hhBBBB", f.read(8))
            statuses.append(status)
    assert statuses[0] & ild.STATUS_BLANKING and not statuses[0] & ild.STATUS_LAST_POINT
    assert statuses[1] == 0
    assert statuses[2] & ild.STATUS_LAST_POINT and not statuses[2] & ild.STATUS_BLANKING
    print("PASS status_bits_last_and_blank")


def test_bgr_order_on_disk():
    # True-colour formats store colour as B, G, R on disk.
    path = _tmp()
    ild.save([[IldaPoint(0, 0, r=1, g=2, b=3)]], path)
    with open(path, "rb") as f:
        f.read(32)
        _x, _y, _status, b, g, r = struct.unpack(">hhBBBB", f.read(8))
    assert (b, g, r) == (3, 2, 1), "on-disk order must be B,G,R"
    print("PASS bgr_order_on_disk")


def test_roundtrip_2d():
    path = _tmp()
    frames = [
        [IldaPoint(-100, 250, r=255, g=0, b=0, blank=False),
         IldaPoint(500, -750, r=0, g=255, b=0, blank=True)],
        [IldaPoint(0, 0, r=12, g=34, b=56)],
    ]
    ild.save(frames, path)
    back = ild.read(path)
    assert len(back) == 2
    a = back[0][0]
    assert (a.x, a.y, a.r, a.g, a.b, a.blank) == (-100, 250, 255, 0, 0, False)
    a2 = back[0][1]
    assert (a2.x, a2.y, a2.r, a2.g, a2.b, a2.blank) == (500, -750, 0, 255, 0, True)
    print("PASS roundtrip_2d")


def test_roundtrip_3d():
    path = _tmp()
    frames = [[IldaPoint(10, -20, 30, r=7, g=8, b=9)]]
    ild.save(frames, path, true_color_3d=True)
    with open(path, "rb") as f:
        _fmt = struct.unpack(">4s3sB", f.read(8))[2]
    assert _fmt == 4, "3D true colour => format 4"
    back = ild.read(path)
    p = back[0][0]
    assert (p.x, p.y, p.z, p.r, p.g, p.b) == (10, -20, 30, 7, 8, 9)
    print("PASS roundtrip_3d")


def test_clamping():
    path = _tmp()
    ild.save([[IldaPoint(999999, -999999, r=999, g=-5, b=128)]], path)
    p = ild.read(path)[0][0]
    assert p.x == ild.COORD_MAX and p.y == ild.COORD_MIN
    assert p.r == 255 and p.g == 0 and p.b == 128
    print("PASS clamping")


def test_eof_marker_present():
    path = _tmp()
    ild.save([[IldaPoint(0, 0)]], path)
    size = os.path.getsize(path)
    # header(32) + 1 record(8) + eof header(32) = 72
    assert size == 72, "expected trailing zero-record EOF header, got %d" % size
    with open(path, "rb") as f:
        f.seek(-32, os.SEEK_END)
        tail = f.read(32)
    recs = struct.unpack(">4s3sB8s8sHHHBB", tail)[5]
    assert recs == 0, "final header must carry zero records"
    print("PASS eof_marker_present")


def test_frame_numbering():
    path = _tmp()
    frames = [[IldaPoint(i, i)] for i in range(5)]
    ild.save(frames, path)
    with open(path, "rb") as f:
        data = f.read()
    off = 0
    seen = []
    while off + 32 <= len(data):
        _m, _r, _f, _n, _c, recs, fno, total, _p, _r2 = \
            struct.unpack_from(">4s3sB8s8sHHHBB", data, off)
        off += 32
        if recs == 0:
            break
        seen.append((fno, total))
        off += recs * 8
    assert seen == [(0, 5), (1, 5), (2, 5), (3, 5), (4, 5)]
    print("PASS frame_numbering")


if __name__ == "__main__":
    test_header_bytes()
    test_status_bits_last_and_blank()
    test_bgr_order_on_disk()
    test_roundtrip_2d()
    test_roundtrip_3d()
    test_clamping()
    test_eof_marker_present()
    test_frame_numbering()
    print("\nAll ILDA format tests passed.")
