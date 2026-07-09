from ilda_format import COORD_MAX
from laser_build import build_clip, BuildOptions


def _square(size=1.0, rgb=(255, 255, 255)):
    h = size / 2.0
    return [(-h, -h, 0, rgb), (h, -h, 0, rgb), (h, h, 0, rgb), (-h, h, 0, rgb), (-h, -h, 0, rgb)]


def test_blank_then_lit_pattern():
    opts = BuildOptions(blank_dwell=3, on_dwell=2, invert_y=False)
    frames = build_clip([[_square()]], opts)
    pts = frames[0]
    # First 3 points blanked (travel), then first lit vertex repeated on_dwell(2) times.
    assert [p.blank for p in pts[:3]] == [True, True, True]
    assert pts[3].blank is False and pts[4].blank is False
    # Remaining square vertices are lit.
    assert all(p.blank is False for p in pts[3:])
    print("PASS blank_then_lit_pattern")


def test_fits_device_range_with_margin():
    opts = BuildOptions(margin=0.06, invert_y=False)
    frames = build_clip([[_square(size=10.0)]], opts)
    xs = [p.x for p in frames[0] if not p.blank]
    ys = [p.y for p in frames[0] if not p.blank]
    limit = COORD_MAX * (1 - 0.06)
    assert max(abs(min(xs)), abs(max(xs))) <= limit + 1
    assert max(abs(min(ys)), abs(max(ys))) <= limit + 1
    # A centred square should very nearly reach the margin limit on the long axis.
    assert max(abs(min(xs)), abs(max(xs))) > limit - 2
    print("PASS fits_device_range_with_margin")


def test_global_scale_is_stable_across_frames():
    # Two frames: a small square, then the SAME square. Device coords must match
    # exactly (proving scale is global, not per-frame).
    opts = BuildOptions(invert_y=False)
    small = _square(size=2.0)
    frames = build_clip([[small], [small]], opts)
    a = [(p.x, p.y) for p in frames[0]]
    b = [(p.x, p.y) for p in frames[1]]
    assert a == b
    # Now a clip where frame 2 is bigger: frame-1 square must NOT fill the range,
    # because the global box is driven by the larger frame.
    big = _square(size=10.0)
    frames2 = build_clip([[small], [big]], opts)
    small_extent = max(abs(p.x) for p in frames2[0])
    big_extent = max(abs(p.x) for p in frames2[1])
    assert small_extent < big_extent
    print("PASS global_scale_is_stable_across_frames")


def test_invert_y():
    opts_no = BuildOptions(invert_y=False)
    opts_yes = BuildOptions(invert_y=True)
    poly = [[(0, 1, 0, (255, 255, 255)), (0, -1, 0, (255, 255, 255))]]
    fn = build_clip([poly], opts_no)[0]
    fy = build_clip([poly], opts_yes)[0]
    lit_no = [p for p in fn if not p.blank]
    lit_yes = [p for p in fy if not p.blank]
    assert lit_no[0].y == -lit_yes[0].y
    print("PASS invert_y")


def test_empty_frame_is_valid():
    frames = build_clip([[]])
    assert len(frames) == 1 and len(frames[0]) == 1 and frames[0][0].blank is True
    print("PASS empty_frame_is_valid")


if __name__ == "__main__":
    test_blank_then_lit_pattern()
    test_fits_device_range_with_margin()
    test_global_scale_is_stable_across_frames()
    test_invert_y()
    test_empty_frame_is_valid()
    print("\nAll builder tests passed.")
