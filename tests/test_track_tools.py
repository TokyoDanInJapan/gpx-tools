"""The tools that read and edit the track itself: stats, ridestats, merge, split."""

import json
import xml.etree.ElementTree as ET

import pytest
from conftest import DEG_LAT_M, gpx, northward

from common.gpxtools import read_points
from track import merge, merge_segments, ridestats, split, stats

# --- stats ------------------------------------------------------------------

def test_stats_distance_is_the_sum_of_the_legs(track):
    path = track([northward(11, step_deg=0.001)])
    out = stats.compute(read_points(path))
    assert out["total_distance_km"] == pytest.approx(10 * 0.001 * DEG_LAT_M / 1000, abs=0.01)
    assert out["num_points"] == 11


def test_stats_ignores_a_rise_below_the_threshold(track):
    """Consumer GPS jitters by a metre or two. A flat ride must read as flat."""
    eles = [100.0, 101.0, 100.0, 101.5, 100.0]
    path = track([northward(5, eles=eles)])
    out = stats.compute(read_points(path), ascent_threshold=3.0)
    assert out["total_ascent_m"] == 0


def test_stats_counts_a_climb_above_the_threshold(track):
    eles = [100.0, 150.0, 200.0, 150.0, 100.0]
    path = track([northward(5, eles=eles)])
    out = stats.compute(read_points(path))
    assert out["total_ascent_m"] == 100
    assert out["total_descent_m"] == 100
    assert out["high_point_m"] == 200


def test_stats_reports_the_bounding_box(track):
    path = track([northward(3, step_deg=0.01)])
    box = stats.compute(read_points(path))["bbox"]
    assert box["min_lat"] == 35.0
    assert box["max_lat"] == pytest.approx(35.02)
    assert box["min_lon"] == box["max_lon"] == 139.0


def test_stats_refuses_an_empty_track():
    with pytest.raises(ValueError):
        stats.compute([])


def test_stats_survives_a_track_with_no_elevations(track):
    path = track([northward(3)])
    out = stats.compute(read_points(path))
    assert out["high_point_m"] is None
    assert out["total_ascent_m"] == 0


# --- ridestats --------------------------------------------------------------

def test_ridestats_reports_no_moving_time_without_a_clock(track):
    """The stat card hides its Time tile rather than inventing a figure."""
    path = track([northward(5, eles=[0, 1, 2, 3, 4])])
    assert ridestats.compute(path)["moving_time_s"] is None


def test_ridestats_counts_time_while_moving(track):
    # 0.001 deg every 10 s is about 11 m/s - well inside the riding window.
    path = track([northward(7, step_deg=0.001, interval_s=10)])
    out = ridestats.compute(path)
    assert out["moving_time_s"] == 60
    # The card carries one decimal place, so compare against the rounded figure
    # rather than pretending to a precision the output does not have.
    assert out["distance_km"] == round(6 * 0.001 * DEG_LAT_M / 1000, 1)


def test_ridestats_excludes_a_stop(track):
    """A pair that does not move is not riding time, however long it lasts."""
    moving = northward(4, step_deg=0.001, interval_s=10)
    stopped = [(moving[-1][0], moving[-1][1], None,
                moving[-1][3].__class__(2024, 3, 3, 1, 0, 0, tzinfo=moving[-1][3].tzinfo))]
    path = track([moving + stopped])
    assert ridestats.compute(path)["moving_time_s"] == 30


def test_ridestats_excludes_a_gps_spike(track):
    """Nobody rides 300 km/h. That pair is a receiver glitch, not a sprint."""
    ordinary = northward(3, step_deg=0.001, interval_s=10)
    spike = [(40.0, 139.0, None, ordinary[-1][3])]
    path = track([ordinary + spike])
    assert ridestats.compute(path)["moving_time_s"] == 20


def test_ridestats_does_not_measure_across_a_segment_break(track):
    """The gap between two days is not a leg of the ride."""
    day_one = northward(4, step_deg=0.001, interval_s=10)
    day_two = northward(4, start_lat=36.0, step_deg=0.001, interval_s=10)
    one = ridestats.compute(track([day_one]))
    both = ridestats.compute(track([day_one, day_two]))
    assert both["distance_km"] == round(6 * 0.001 * DEG_LAT_M / 1000, 1)
    assert one["distance_km"] < both["distance_km"]


def test_ridestats_writes_a_sidecar(track, capsys):
    path = track([northward(5, step_deg=0.001, interval_s=10)])
    ridestats.main([str(path), "--write"])
    written = json.loads(path.with_suffix(".stats.json").read_text())
    assert set(written) == {"distance_km", "moving_time_s", "ascent_m"}


# --- merge ------------------------------------------------------------------

def test_merge_gives_each_file_its_own_segment(tmp_path):
    """Keeping the joins visible is the point, not an implementation detail."""
    a = tmp_path / "a.gpx"
    b = tmp_path / "b.gpx"
    a.write_text(gpx([northward(3)]), encoding="utf-8")
    b.write_text(gpx([northward(4, start_lat=40.0)]), encoding="utf-8")
    out = tmp_path / "merged.gpx"
    merge.write(merge.merge([str(a), str(b)], name="Two days"), out)

    root = ET.parse(out).getroot()
    segs = [s for s in root.iter() if s.tag.endswith("trkseg")]
    assert [len(list(s)) for s in segs] == [3, 4]


def test_merge_reads_a_route_as_track_points(tmp_path):
    planned = tmp_path / "planned.gpx"
    planned.write_text('<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1"><rte>'
                       '<rtept lat="35.0" lon="139.0"><ele>10</ele></rtept>'
                       '<rtept lat="35.1" lon="139.0" /></rte></gpx>', encoding="utf-8")
    out = tmp_path / "merged.gpx"
    merge.write(merge.merge([str(planned)]), out)
    assert len(read_points(out)) == 2


# --- merge_segments ---------------------------------------------------------

def test_merge_segments_collapses_the_boundaries(track):
    path = track([northward(3), northward(3, start_lat=40.0)])
    status, before = merge_segments.merge_file(path)
    assert (status, before) == ("ok", 2)
    assert path.read_text().count("<trkseg") == 1


def test_merge_segments_is_idempotent(track):
    path = track([northward(3), northward(3, start_lat=40.0)])
    merge_segments.merge_file(path)
    assert merge_segments.merge_file(path)[0] == "skip"


def test_merge_segments_keeps_separate_tracks_apart(tmp_path):
    """Only segments within one <trk> are merged. Two rides stay two rides."""
    path = tmp_path / "two.gpx"
    path.write_text('<gpx version="1.1">'
                    '<trk><trkseg><trkpt lat="35.0" lon="139.0" /></trkseg></trk>'
                    '<trk><trkseg><trkpt lat="36.0" lon="139.0" /></trkseg></trk>'
                    "</gpx>", encoding="utf-8")
    assert merge_segments.merge_file(path)[0] == "skip"


# --- split ------------------------------------------------------------------

def test_split_finds_the_teleport(track):
    """A ferry, a train, a drive: a jump the rider did not make."""
    leg = northward(4, step_deg=0.001)
    jump = northward(4, start_lat=45.0, step_deg=0.001)
    path = track([leg + jump])
    root = ET.parse(path).getroot()
    breaks, total, jumps = split.find_teleports(root, threshold_m=1000)
    assert breaks == [3]
    assert total == 8
    assert jumps[0] > 1_000_000


def test_split_measures_only_within_a_segment(track):
    """A gap that is already a segment boundary is not a teleport to find."""
    path = track([northward(4), northward(4, start_lat=45.0)])
    root = ET.parse(path).getroot()
    breaks, _, _ = split.find_teleports(root, threshold_m=1000)
    assert breaks == []


def test_split_rewrites_the_file(track):
    leg = northward(4, step_deg=0.001)
    jump = northward(4, start_lat=45.0, step_deg=0.001)
    path = track([leg + jump])
    split.process(str(path), threshold_m=1000, write=True)
    assert path.read_text().count("<trkseg") == 2
