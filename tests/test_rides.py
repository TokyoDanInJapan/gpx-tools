"""Reading a track with its clock, and the gates that decide what counts."""

import pytest
from conftest import DEG_LAT_M, northward

from common.rides import (
    MAX_SPEED_MS,
    MIN_SPEED_MS,
    SMOOTH_HALF_M,
    parse_time,
    read_timed_segments,
    smooth_elevation,
)


def test_parse_time_reads_the_zulu_form_gpx_actually_uses():
    assert parse_time("2024-03-03T01:02:03Z") == pytest.approx(1709427723.0)


def test_parse_time_reads_an_explicit_offset():
    assert parse_time("2024-03-03T10:02:03+09:00") == parse_time("2024-03-03T01:02:03Z")


def test_parse_time_returns_none_rather_than_raising():
    """A recorder that writes rubbish costs that point's time, not the ride."""
    assert parse_time("") is None
    assert parse_time(None) is None
    assert parse_time("last Tuesday") is None


def test_read_timed_segments_carries_the_clock(track):
    path = track([northward(3, interval_s=10)])
    (seg,) = read_timed_segments(path)
    assert [p[3] for p in seg] == [parse_time("2024-03-03T00:00:00Z"),
                                   parse_time("2024-03-03T00:00:10Z"),
                                   parse_time("2024-03-03T00:00:20Z")]


def test_read_timed_segments_drops_a_lone_point(track):
    """Nothing this reader feeds can measure anything from a single point."""
    path = track([northward(1), northward(4, start_lat=40.0)])
    assert [len(s) for s in read_timed_segments(path)] == [4]


def test_read_timed_segments_keeps_the_boundaries(track):
    path = track([northward(3), northward(5, start_lat=40.0)])
    assert [len(s) for s in read_timed_segments(path)] == [3, 5]


def test_the_gates_bracket_a_plausible_riding_speed():
    """~1 km/h to ~90 km/h: below is standing still, above is a GPS spike."""
    assert pytest.approx(1.0, abs=0.02) == MIN_SPEED_MS * 3.6
    assert pytest.approx(90.0) == MAX_SPEED_MS * 3.6


def test_smooth_elevation_flattens_a_single_spike():
    """One bad sample among level ones should not read as a climb."""
    step = 10.0  # metres between points, so the window spans several of them
    cum = [i * step for i in range(11)]
    ele = [100.0] * 11
    ele[5] = 200.0
    out = smooth_elevation(cum, ele)
    assert out[5] < 120.0
    assert max(out) < max(ele)


def test_smooth_elevation_averages_over_metres_not_points():
    """Two points far apart must not smooth into each other."""
    far = SMOOTH_HALF_M * 10
    cum = [0.0, far, 2 * far]
    ele = [0.0, 100.0, 0.0]
    assert smooth_elevation(cum, ele) == ele


def test_smooth_elevation_falls_back_when_an_elevation_is_missing():
    """A gap cannot be averaged across, so the raw values are returned intact."""
    cum = [0.0, 10.0, 20.0]
    ele = [100.0, None, 120.0]
    assert smooth_elevation(cum, ele) is ele


def test_a_northward_track_measures_as_the_arc_it_is(track):
    """The fixture's geometry, checked against the reader rather than assumed."""
    from common.gpxtools import haversine, read_points

    path = track([northward(11, step_deg=0.001)])
    points = read_points(path)
    total = sum(haversine(a[0], a[1], b[0], b[1]) for a, b in zip(points, points[1:]))
    assert total == pytest.approx(10 * 0.001 * DEG_LAT_M, rel=1e-4)
