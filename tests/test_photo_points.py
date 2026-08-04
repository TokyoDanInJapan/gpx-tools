"""Placing photographs along a track.

The two estimators are what this file is about. `calibrate` is a mode rather
than a median, and the reason is a lap course. `zone_offset` is a lookup rather
than a guess, and the reason is everywhere that is not Japan. Both were wrong
answers first, so both are pinned here.
"""

import datetime as dt
import json

import pytest
from conftest import northward

from photos import photo_points


def test_calibrate_returns_nothing_without_anchors():
    assert photo_points.calibrate([]) == (None, 0)


def test_calibrate_takes_the_mode_not_the_median():
    """The Sado case: a lap course scatters anchors, and the median lands
    on a value no anchor claimed."""
    anchors = [h * 3600 for h in (-11.8, 19.2, 9.0, 9.01, 8.99, -3.0, 15.0)]
    offset, agreed = photo_points.calibrate(anchors)
    assert offset == pytest.approx(9.0 * 3600, abs=60)
    assert agreed == 3


def test_calibrate_keeps_the_cameras_own_drift():
    """A clock nineteen minutes slow is corrected by nineteen minutes, not
    snapped to the nearest hour."""
    nineteen = 9 * 3600 + 19 * 60
    offset, _ = photo_points.calibrate([nineteen, nineteen + 10, nineteen - 10])
    assert offset == pytest.approx(nineteen, abs=30)


def test_calibrate_splits_claims_more_than_half_an_hour_apart():
    """Real timezones are whole or half hours. A wider spread is two claims."""
    close = [9 * 3600, 9 * 3600 + 300, 9 * 3600 - 300]
    far = [13 * 3600]
    offset, agreed = photo_points.calibrate(close + far)
    assert agreed == 3
    assert offset == pytest.approx(9 * 3600, abs=600)


def test_cumulative_km_accumulates_along_the_track():
    points = [(lat, lon, when, seg)
              for (lat, lon, _, when), seg in zip(northward(5, step_deg=0.01), [0] * 5)]
    kms = photo_points.cumulative_km(points)
    assert kms[0] == 0
    assert kms == sorted(kms)
    assert kms[-1] == pytest.approx(4 * 0.01 * 111.19, rel=0.01)


def test_cumulative_km_does_not_bridge_a_segment_gap():
    """A ferry is not four hundred kilometres of riding."""
    near = [(35.0, 139.0, None, 0), (35.01, 139.0, None, 0)]
    far = [(40.0, 139.0, None, 1), (40.01, 139.0, None, 1)]
    kms = photo_points.cumulative_km(near + far)
    assert kms[2] - kms[1] == 0


def test_read_timed_points_keeps_the_segment_index(tmp_path):
    from conftest import gpx

    path = tmp_path / "two.gpx"
    path.write_text(gpx([northward(3, interval_s=10),
                         northward(3, start_lat=40.0, interval_s=10)]), encoding="utf-8")
    points = photo_points.read_timed_points(path)
    assert [p[3] for p in points] == [0, 0, 0, 1, 1, 1]
    assert all(isinstance(p[2], dt.datetime) for p in points)


def test_gallery_files_reads_the_manifest_in_order(tmp_path):
    manifest = tmp_path / "ride.json"
    manifest.write_text(json.dumps({"images": [
        {"src": "photos/cycling/ride/img003.jpg"},
        {"src": "photos/cycling/ride/img001.jpg"},
    ]}), encoding="utf-8")
    assert photo_points.gallery_files(manifest) == ["img003.jpg", "img001.jpg"]


def test_pixel_of_maps_the_window_corners():
    window = {"u0": 0.0, "v0": 0.0, "u1": 1.0, "v1": 1.0, "w": 1024, "h": 1024}
    x, y = photo_points.pixel_of(0.0, 0.0, window)
    assert (x, y) == (512.0, 512.0)   # null island sits at the centre of the world


def test_pixel_of_returns_none_for_a_degenerate_window():
    window = {"u0": 0.5, "v0": 0.5, "u1": 0.5, "v1": 0.5, "w": 10, "h": 10}
    assert photo_points.pixel_of(0.0, 0.0, window) is None


@pytest.mark.parametrize(("lat", "lon", "zone_hours"), [
    (35.68, 139.69, 9),      # Tokyo
    (-43.53, 172.63, 13),    # Christchurch, in the southern summer
    (16.87, 96.20, 6.5),     # Yangon, and the half-hour offset that broke guessing
])
def test_zone_offset_is_looked_up_not_guessed(lat, lon, zone_hours):
    """Longitude agreed on Japan and was wrong on everywhere else, silently."""
    pytest.importorskip("timezonefinder")
    when = dt.datetime(2024, 2, 1, 0, 0, tzinfo=dt.timezone.utc)
    points = [(lat, lon, when, 0)]
    assert photo_points.zone_offset(points, when) == pytest.approx(zone_hours * 3600)


def test_zone_offset_gives_up_quietly_without_timezonefinder(monkeypatch):
    """It is an optional dependency, and the anchors can still place a ride."""
    import builtins

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "timezonefinder":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    when = dt.datetime(2024, 2, 1, 0, 0, tzinfo=dt.timezone.utc)
    assert photo_points.zone_offset([(35.68, 139.69, when, 0)], when) is None
