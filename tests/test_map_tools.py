"""The parts of the map tools that do not need tiles: filtering and projecting.

Drawing is left to the drawing libraries and is not asserted here - a test can
say a JPEG appeared, not that the picture in it is right. What is asserted is
everything that decides which routes are on the map and where a boundary lands,
because those fail quietly rather than visibly.
"""

import json

import pytest
from conftest import northward

from common.gpxtools import read_points
from common.prefectures import PrefectureLocator
from maps import multimap, prefecture_map

# --- multimap ---------------------------------------------------------------

def test_a_route_inside_the_box_is_kept(track):
    points = read_points(track([northward(5)]))
    assert multimap.in_bbox(points, (24.0, 122.0, 46.0, 154.0)) is True


def test_a_route_outside_the_box_is_dropped(track):
    """This is how the Japan maps leave the overseas rides off."""
    points = read_points(track([northward(5, start_lat=-43.5)]))
    assert multimap.in_bbox(points, (24.0, 122.0, 46.0, 154.0)) is False


def test_a_route_that_merely_clips_the_box_is_kept(track):
    """One point inside is enough: a ride to the border still happened there."""
    points = read_points(track([northward(5, start_lat=45.999, step_deg=0.01)]))
    assert multimap.in_bbox(points, (24.0, 122.0, 46.0, 154.0)) is True


def test_no_box_keeps_everything(track):
    points = read_points(track([northward(5, start_lat=-43.5)]))
    assert multimap.in_bbox(points, None) is True


# --- prefecture map ---------------------------------------------------------

@pytest.fixture(scope="module")
def locator():
    return PrefectureLocator()


def test_visited_prefectures_reads_every_track(track, locator):
    tokyo = track([northward(5, start_lat=35.68, lon=139.69, step_deg=0.0001)])
    sapporo = track([northward(5, start_lat=43.06, lon=141.35, step_deg=0.0001)])
    assert prefecture_map.visited_prefectures([str(tokyo), str(sapporo)],
                                              locator) == {"Tokyo", "Hokkaido"}


def test_an_unreadable_track_is_reported_not_fatal(tmp_path, locator, capsys):
    """One bad file in a batch of sixty should not cost the other fifty-nine."""
    broken = tmp_path / "broken.gpx"
    broken.write_text("this is not xml", encoding="utf-8")
    assert prefecture_map.visited_prefectures([str(broken)], locator) == set()
    assert "Skipping" in capsys.readouterr().err


def test_simplify_keeps_the_shape_and_the_ends():
    ring = [(0.0, 0.0), (1.0, 0.001), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0), (0.0, 0.0)]
    simplified = prefecture_map._simplify(ring, tol=0.01)
    assert simplified[0] == ring[0]
    assert simplified[-1] == ring[-1]
    assert len(simplified) < len(ring)


def test_simplify_leaves_a_ring_that_is_already_minimal():
    ring = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)]
    assert prefecture_map._simplify(ring, tol=0.5) == ring


def test_paths_json_writes_a_path_per_prefecture(tmp_path, locator):
    out = tmp_path / "prefectures.json"
    prefecture_map.paths_json(locator, {"Tokyo", "Hokkaido"}, str(out),
                              prefecture_map.DEFAULT_BBOX)
    data = json.loads(out.read_text())
    assert data["width"] > 0 and data["height"] > 0
    names = {entry["name"] for entry in data["prefs"]}
    assert {"Tokyo", "Hokkaido", "Okinawa"} <= names


def test_paths_json_marks_which_were_visited(tmp_path, locator):
    out = tmp_path / "prefectures.json"
    prefecture_map.paths_json(locator, {"Tokyo"}, str(out), prefecture_map.DEFAULT_BBOX)
    data = json.loads(out.read_text())
    visited = {entry["name"] for entry in data["prefs"] if entry["visited"]}
    assert visited == {"Tokyo"}


def test_paths_json_projects_north_above_south(tmp_path, locator):
    """A y axis the wrong way up puts Hokkaido at the bottom, which reads as
    a different country rather than as a bug."""
    out = tmp_path / "prefectures.json"
    prefecture_map.paths_json(locator, set(), str(out), prefecture_map.DEFAULT_BBOX)
    data = json.loads(out.read_text())
    by_name = {entry["name"]: entry for entry in data["prefs"]}

    def first_y(entry):
        head = entry["d"].split("L")[0]
        return float(head.lstrip("M").split()[1])

    assert first_y(by_name["Hokkaido"]) < first_y(by_name["Okinawa"])
