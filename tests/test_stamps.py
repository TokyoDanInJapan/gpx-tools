"""Tracing a country: the mask, its boundary loops, and the simplifier.

The drawing here is line work rather than a picture, so unlike the rest of the
maps this one *is* checkable: a loop either closes or it does not, a simplified
coast either keeps its corners or loses them.
"""

import pytest

from terrain import stamps

# --- finding the tracks -----------------------------------------------------

def test_tracks_below_finds_them_in_a_stable_order(tmp_path):
    """Sorted, because the output is committed and a reordering is a diff."""
    (tmp_path / "b").mkdir()
    for name in ["z.gpx", "a.gpx"]:
        (tmp_path / name).write_text("")
    (tmp_path / "b" / "m.gpx").write_text("")
    found = stamps.tracks_below(str(tmp_path))
    assert [f.rsplit("/", 1)[-1] for f in found] == ["a.gpx", "m.gpx", "z.gpx"]


def test_a_sidecar_says_which_tracks_count(tmp_path):
    """A publication marks a track as published by what sits beside it."""
    (tmp_path / "published.gpx").write_text("")
    (tmp_path / "published.md").write_text("")
    (tmp_path / "draft.gpx").write_text("")
    found = stamps.tracks_below(str(tmp_path), sidecar=".md")
    assert [f.rsplit("/", 1)[-1] for f in found] == ["published.gpx"]


def test_no_sidecar_asked_for_takes_everything(tmp_path):
    (tmp_path / "one.gpx").write_text("")
    (tmp_path / "two.gpx").write_text("")
    assert len(stamps.tracks_below(str(tmp_path))) == 2


# --- the simplifier ---------------------------------------------------------

def test_simplify_keeps_the_ends():
    line = [(0, 0), (1, 0.01), (2, 0), (3, 0)]
    out = stamps.simplify(line, tolerance=0.5)
    assert out[0] == line[0] and out[-1] == line[-1]


def test_simplify_drops_a_point_that_is_on_the_line():
    straight = [(0, 0), (1, 0), (2, 0), (3, 0)]
    assert stamps.simplify(straight, tolerance=0.1) == [(0, 0), (3, 0)]


def test_simplify_keeps_a_real_corner():
    corner = [(0, 0), (1, 0), (2, 0), (2, 2), (2, 4)]
    out = stamps.simplify(corner, tolerance=0.1)
    assert (2, 0) in out


def test_simplify_leaves_two_points_alone():
    assert stamps.simplify([(0, 0), (1, 1)], tolerance=10) == [(0, 0), (1, 1)]


def test_a_closed_ring_does_not_collapse_to_a_point():
    """Run naively, Douglas-Peucker measures every deviation from a
    zero-length baseline and the whole coast disappears."""
    square = [(0, 0), (4, 0), (4, 4), (0, 4)]
    out = stamps.simplify_closed(square, tolerance=0.1)
    assert len(out) >= 4
    for corner in square:
        assert corner in out


def test_a_ring_simplifies_its_straights_but_keeps_its_shape():
    # A square with extra points along each edge.
    ring = []
    for x in range(0, 10, 2):
        ring.append((x, 0))
    for y in range(0, 10, 2):
        ring.append((8, y))
    for x in range(8, -1, -2):
        ring.append((x, 8))
    for y in range(8, -1, -2):
        ring.append((0, y))
    out = stamps.simplify_closed(ring, tolerance=0.5)
    assert len(out) < len(ring)
    assert (0, 0) in out and (8, 8) in out


# --- tracing the mask -------------------------------------------------------

def block_mask(grid=16, lo=4, hi=12):
    """A solid square of land in the middle of an empty grid."""
    np = pytest.importorskip("numpy")
    mask = np.zeros((grid, grid), dtype=bool)
    mask[lo:hi, lo:hi] = True
    return mask


def test_boundary_loops_finds_one_coast_around_one_island():
    loops = stamps.boundary_loops(block_mask())
    assert len(loops) == 1


def test_the_loop_closes():
    """An open coastline would be a line with two ends, which no island has."""
    (loop,) = stamps.boundary_loops(block_mask())
    assert len(loop) >= 4
    # Consecutive points are one mask edge apart, including from last to first.
    ring = loop + [loop[0]]
    for a, b in zip(ring, ring[1:]):
        assert abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1


def test_the_loop_traces_the_edge_it_was_given():
    (loop,) = stamps.boundary_loops(block_mask(grid=16, lo=4, hi=12))
    xs = [p[0] for p in loop]
    ys = [p[1] for p in loop]
    assert min(xs) == 4 and max(xs) == 12
    assert min(ys) == 4 and max(ys) == 12


def test_two_islands_are_two_loops():
    np = pytest.importorskip("numpy")
    mask = np.zeros((20, 20), dtype=bool)
    mask[2:6, 2:6] = True
    mask[12:18, 12:18] = True
    assert len(stamps.boundary_loops(mask)) == 2


def test_an_empty_mask_has_no_coast():
    np = pytest.importorskip("numpy")
    assert stamps.boundary_loops(np.zeros((8, 8), dtype=bool)) == []


def test_a_single_cell_traces_as_a_four_edge_loop():
    """Which is what `MIN_LOOP_EDGES` later drops: the tracer reports every
    island honestly, and the stamp decides which are too small to read."""
    np = pytest.importorskip("numpy")
    mask = np.zeros((20, 20), dtype=bool)
    mask[10:11, 10:11] = True
    (loop,) = stamps.boundary_loops(mask)
    assert len(loop) == 4
    assert len(loop) < stamps.MIN_LOOP_EDGES


# --- the regions themselves -------------------------------------------------

def test_every_region_names_a_file_that_ships():
    import os

    for region in stamps.REGIONS:
        assert os.path.exists(region["geojson"]), region["name"]


def test_the_regions_do_not_overlap():
    """A track is assigned to the first region whose box it touches, so two
    boxes overlapping would make that assignment depend on their order."""
    for i, a in enumerate(stamps.REGIONS):
        for b in stamps.REGIONS[i + 1:]:
            s1, w1, n1, e1 = a["bbox"]
            s2, w2, n2, e2 = b["bbox"]
            assert n1 < s2 or n2 < s1 or e1 < w2 or e2 < w1, f"{a['name']} vs {b['name']}"
