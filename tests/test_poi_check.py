"""Checking that a write-up's places sit near the route it plots them on."""

import json

import pytest
from conftest import northward

from common.gpxtools import read_points
from maps import poi_check


@pytest.fixture
def route(track):
    """A 10 km line due north, so a distance from it is easy to reason about."""
    return read_points(track([northward(101, step_deg=0.001)]))


def test_a_place_on_the_route_measures_as_zero(route):
    lat, lon, _ = route[50]
    assert poi_check.min_distance_km(lat, lon, route) == pytest.approx(0, abs=0.001)


def test_the_distance_is_to_the_nearest_point_not_the_start(route):
    """A place beside the far end of the ride is near the route, not 10 km from it."""
    lat, lon, _ = route[-1]
    assert poi_check.min_distance_km(lat + 0.001, lon, route) < 0.2


def test_a_place_off_the_corridor_measures_far(route):
    lat, lon, _ = route[50]
    assert poi_check.min_distance_km(lat, lon + 0.1, route) > 8


def test_the_house_thresholds_are_ordered():
    """ok within OK_KM, tolerable to SOFT_KM, drop beyond it."""
    assert poi_check.OK_KM < poi_check.SOFT_KM


def test_main_flags_a_place_that_should_be_dropped(track, tmp_path, capsys):
    path = track([northward(101, step_deg=0.001)])
    pois = tmp_path / "pois.json"
    pois.write_text(json.dumps({
        "on the route": {"lat": 35.05, "lon": 139.0},
        "miles away": {"lat": 35.05, "lon": 140.0},
        "not plotted": {"lat": None, "lon": None},
    }), encoding="utf-8")

    with pytest.raises(SystemExit) as exit_status:
        poi_check.main([str(path), str(pois)])
    assert exit_status.value.code == 1

    printed = capsys.readouterr().out
    assert "DROP" in printed
    assert "not plotted" in printed   # listed, never silently forgotten
