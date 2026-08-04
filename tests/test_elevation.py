"""The elevation profile: banding, simplification, and the SVG itself.

This module is pure standard library and needs no network, which is what lets
every committed profile be regenerated offline. The property that makes that
worth having is the one asserted here first: the same track always produces
byte-identical output.
"""

import xml.etree.ElementTree as ET

import pytest
from conftest import northward

from common.gpxtools import read_segments
from terrain.elevation import (
    GRADE_BANDS,
    build_profiles,
    cluster_bands,
    grade_colour,
    min_band_km,
    render_elevation_svg,
    simplify_indices,
)


def climb(n=60, gain_per_point=8.0):
    """A steady climb: enough points to survive resampling and simplification."""
    return northward(n, step_deg=0.001,
                     eles=[100.0 + i * gain_per_point for i in range(n)])


def test_grade_bands_are_ordered_and_complete():
    """Each band's upper edge is the next one's floor, and the last is open."""
    uppers = [upper for upper, _, _ in GRADE_BANDS]
    assert uppers == sorted(uppers)
    assert uppers[-1] == float("inf")


def test_grade_colour_reads_the_magnitude_not_the_sign():
    """A 7% descent is drawn like a 7% climb. The profile shows steepness."""
    assert grade_colour(7.0) == grade_colour(-7.0)


def test_grade_colour_walks_up_the_bands():
    assert grade_colour(0.5) != grade_colour(11.0)
    assert grade_colour(100.0) == GRADE_BANDS[-1][1]


def test_build_profiles_returns_a_run_per_segment(track):
    path = track([climb(), climb(30)])
    profiles = build_profiles(read_segments(path))
    assert len(profiles) == 2


def test_build_profiles_carries_distance_height_and_a_colour_per_step(track):
    path = track([climb()])
    (profile,) = build_profiles(read_segments(path))
    km, elevations, colours, coords = profile
    assert km == sorted(km)              # distance only ever increases
    assert elevations[0] < elevations[-1]  # and this track only ever climbs
    assert len(colours) == len(km) - 1   # a colour spans the gap between points
    assert len(coords) == len(km)        # the map overlay bands the same samples


def test_build_profiles_chains_the_segments_without_bridging_the_gap(track):
    """A second segment continues the x axis. The teleport adds no kilometres."""
    path = track([climb(30), climb(30)])
    first, second = build_profiles(read_segments(path))
    assert second[0][0] == pytest.approx(first[0][-1])


def test_min_band_km_scales_with_the_ride(track):
    """A band too short to see is noise on the chart, and the floor is relative."""
    short = min_band_km(build_profiles(read_segments(track([climb(20)]))))
    long = min_band_km(build_profiles(read_segments(track([climb(200)]))))
    assert long > short


def test_simplify_keeps_the_ends():
    xs = list(range(10))
    ys = [0, 0, 0, 5, 0, 0, 0, 0, 0, 0]
    kept = simplify_indices(xs, ys, 0, 9, tolerance=1.0)
    assert kept[0] == 0 and kept[-1] == 9


def test_simplify_keeps_a_real_peak_and_drops_a_flat_run():
    xs = list(range(10))
    ys = [0, 0, 0, 50, 0, 0, 0, 0, 0, 0]
    assert 3 in simplify_indices(xs, ys, 0, 9, tolerance=1.0)
    flat = simplify_indices(xs, [0] * 10, 0, 9, tolerance=1.0)
    assert flat == [0, 9]


def test_cluster_bands_merges_a_run_too_short_to_show():
    """Alternating one-metre bands would draw a barcode, not a profile."""
    # dist holds the points. Colours span the gaps, so there is one fewer.
    dist = [i * 0.01 for i in range(21)]
    colours = ["a" if i % 2 else "b" for i in range(20)]
    bands = cluster_bands(dist, colours, min_len=0.5)
    assert len(bands) < len(colours)


def test_cluster_bands_keeps_a_run_long_enough_to_matter():
    dist = [i * 0.1 for i in range(21)]
    colours = ["a"] * 10 + ["b"] * 10
    bands = cluster_bands(dist, colours, min_len=0.5)
    assert [colour for colour, _, _ in bands] == ["a", "b"]


def test_render_writes_an_svg(track, tmp_path):
    out = tmp_path / "elevation.svg"
    render_elevation_svg(read_segments(track([climb()])), str(out))
    root = ET.parse(out).getroot()
    assert root.tag.endswith("svg")


def test_render_is_deterministic(track, tmp_path):
    """The property the offline regeneration check depends on."""
    segments = read_segments(track([climb()]))
    first, second = tmp_path / "a.svg", tmp_path / "b.svg"
    render_elevation_svg(segments, str(first))
    render_elevation_svg(segments, str(second))
    assert first.read_bytes() == second.read_bytes()


def test_render_shows_the_climb_in_its_axis(track, tmp_path):
    out = tmp_path / "elevation.svg"
    render_elevation_svg(read_segments(track([climb(60, gain_per_point=10)])), str(out))
    text = out.read_text()
    # The track rises from 100 m to about 690 m, so the axis has to reach past
    # 500 m however it chooses its ticks.
    assert any(f">{tick}<" in text for tick in (500, 600, 700))


def test_render_refuses_a_track_with_no_elevations(track, tmp_path):
    """Better to say so than to draw a flat line and call it a profile."""
    path = track([northward(10, step_deg=0.001)])
    with pytest.raises(ValueError, match="No elevation data"):
        render_elevation_svg(read_segments(path), str(tmp_path / "none.svg"))
