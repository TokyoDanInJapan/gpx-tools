"""The bottom layer: reading a GPX, and measuring on it."""

import math
import xml.etree.ElementTree as ET

import pytest
from conftest import DEG_LAT_M, gpx, northward

from common.gpxtools import (
    EARTH_RADIUS_M,
    haversine,
    local_name,
    parse_point,
    read_points,
    read_segments,
)


def test_local_name_strips_any_namespace():
    assert local_name("{http://www.topografix.com/GPX/1/1}trkpt") == "trkpt"
    assert local_name("trkpt") == "trkpt"


def test_haversine_north_matches_the_arc_length():
    # A degree of latitude is the same arc anywhere, so this is checkable by
    # hand: 2 * pi * R / 360.
    assert haversine(0, 0, 1, 0) == pytest.approx(DEG_LAT_M, rel=1e-5)


def test_haversine_is_symmetric_and_zero_at_a_point():
    assert haversine(35.0, 139.0, 35.0, 139.0) == 0
    assert haversine(35.0, 139.0, 36.0, 140.0) == pytest.approx(
        haversine(36.0, 140.0, 35.0, 139.0))


def test_haversine_spans_the_antipodes():
    """Half the great circle, which is where a naive flat-earth formula fails."""
    assert haversine(0, 0, 0, 180) == pytest.approx(math.pi * EARTH_RADIUS_M, rel=1e-9)


def test_a_degree_of_longitude_shrinks_towards_the_pole():
    assert haversine(60, 0, 60, 1) == pytest.approx(DEG_LAT_M * math.cos(math.radians(60)),
                                                    rel=1e-3)


def test_parse_point_reads_coordinates_and_elevation():
    el = ET.fromstring('<trkpt lat="35.5" lon="139.5"><ele>120.5</ele></trkpt>')
    assert parse_point(el) == (35.5, 139.5, 120.5)


def test_parse_point_returns_none_without_usable_coordinates():
    """One malformed row should not cost the whole ride."""
    assert parse_point(ET.fromstring('<trkpt lon="139.5" />')) is None
    assert parse_point(ET.fromstring('<trkpt lat="north" lon="139.5" />')) is None


def test_parse_point_survives_an_unusable_elevation():
    el = ET.fromstring('<trkpt lat="35.5" lon="139.5"><ele>high</ele></trkpt>')
    assert parse_point(el) == (35.5, 139.5, None)


def test_read_points_flattens_every_segment(tmp_path):
    path = tmp_path / "t.gpx"
    path.write_text(gpx([northward(3), northward(2, start_lat=40.0)]), encoding="utf-8")
    assert len(read_points(path)) == 5


def test_read_points_works_without_a_namespace(tmp_path):
    """A hand-edited file that omits the schema reads the same as one that has it."""
    path = tmp_path / "bare.gpx"
    path.write_text(gpx([northward(3)], namespaced=False), encoding="utf-8")
    assert len(read_points(path)) == 3


def test_read_points_accepts_a_route(tmp_path):
    path = tmp_path / "route.gpx"
    path.write_text('<gpx version="1.1"><rte>'
                    '<rtept lat="35.0" lon="139.0" />'
                    '<rtept lat="35.1" lon="139.0" />'
                    "</rte></gpx>", encoding="utf-8")
    assert len(read_points(path)) == 2


def test_read_segments_keeps_the_boundaries(track):
    """The fact that stops a renderer drawing a ferry crossing as a road."""
    path = track([northward(3), northward(2, start_lat=40.0)])
    segments = read_segments(path)
    assert [len(s) for s in segments] == [3, 2]


def test_read_segments_drops_empty_ones(tmp_path):
    path = tmp_path / "empty.gpx"
    path.write_text('<gpx version="1.1"><trk><trkseg></trkseg><trkseg>'
                    '<trkpt lat="35.0" lon="139.0" /></trkseg></trk></gpx>',
                    encoding="utf-8")
    assert [len(s) for s in read_segments(path)] == [1]


def test_read_segments_keeps_points_outside_any_segment(tmp_path):
    """Loose GPX puts points straight under <trk>. Losing them silently is worse."""
    path = tmp_path / "loose.gpx"
    path.write_text('<gpx version="1.1"><trk>'
                    '<trkpt lat="35.0" lon="139.0" />'
                    '<trkpt lat="35.1" lon="139.0" />'
                    "</trk></gpx>", encoding="utf-8")
    assert [len(s) for s in read_segments(path)] == [2]
