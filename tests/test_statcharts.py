"""Aggregating many tracks, and naming the rides in the totals.

The naming is the part worth pinning: what a track is called and where it links
to are properties of whatever publishes it, and the tool is told them rather
than assuming a directory layout.
"""

import json

import pytest
from conftest import northward

from charts import statcharts
from common.rides import parse_time, read_timed_segments


def ride(track, n=20, gain=5.0, start_lat=35.0, name=None):
    """One timed, climbing segment, written to disk."""
    eles = [100.0 + i * gain for i in range(n)]
    return track([northward(n, start_lat=start_lat, step_deg=0.001,
                            eles=eles, interval_s=10)], name=name)


# --- naming -----------------------------------------------------------------

def test_track_url_falls_back_to_the_filename():
    assert statcharts.track_url("rides/2019/bandai.gpx") == "/bandai"


def test_track_url_uses_the_path_below_the_root():
    assert statcharts.track_url("rides/cycling/bandai.gpx", root="rides") == "/cycling/bandai"


def test_track_url_survives_a_track_outside_the_root():
    assert statcharts.track_url("/elsewhere/bandai.gpx", root="rides") == "/bandai"


def test_sidecar_title_reads_the_frontmatter(tmp_path):
    (tmp_path / "bandai.mdx").write_text(
        "---\ntitle: 'Mount Bandai Tour'\n---\nProse\n", encoding="utf-8")
    assert statcharts.sidecar_title(str(tmp_path / "bandai.gpx"), ".mdx") == "Mount Bandai Tour"


def test_sidecar_title_is_none_without_a_sidecar(tmp_path):
    assert statcharts.sidecar_title(str(tmp_path / "bandai.gpx"), ".mdx") is None
    assert statcharts.sidecar_title(str(tmp_path / "bandai.gpx"), None) is None


# --- aggregation ------------------------------------------------------------

def test_the_accumulator_bins_distance_and_time(track):
    acc = statcharts.Accumulator(locator=None)
    for seg in read_timed_segments(ride(track)):
        acc.add_segment(seg, season_idx=0, year=2024)
    acc.end_ride()
    assert acc.total_dist > 0
    assert acc.dist_with_time == pytest.approx(acc.total_dist)


def test_a_ride_with_no_prefecture_still_counts_everywhere_else(track):
    """An overseas trip is absent from the prefecture charts, not from the totals."""
    acc = statcharts.Accumulator(locator=None)
    for seg in read_timed_segments(ride(track)):
        acc.add_segment(seg, season_idx=0, year=2024)
    acc.end_ride()
    assert acc.total_dist > 0
    assert not acc.pref_dist


def test_chart_data_returns_a_time_and_distance_pair_per_category(track):
    acc = statcharts.Accumulator(locator=None)
    for seg in read_timed_segments(ride(track)):
        acc.add_segment(seg, season_idx=1, year=2024)
    acc.end_ride()
    charts = statcharts.chart_data(acc)
    assert len(charts) == len(statcharts.CHART_TITLES)
    for _, labels, values in charts:
        assert len(labels) == len(values)


def test_year_series_fills_the_gap_years():
    """A year with no riding is a zero on the timeline, not a missing bar."""
    labels, values = statcharts.year_series({2019: 100.0, 2022: 50.0})
    assert labels == ["2019", "2020", "2021", "2022"]
    assert values == [100.0, 0.0, 0.0, 50.0]


def test_season_of_places_a_ride_by_its_start():
    winter = statcharts.Accumulator.season_of(parse_time("2024-01-15T00:00:00Z"))
    summer = statcharts.Accumulator.season_of(parse_time("2024-07-15T00:00:00Z"))
    assert statcharts.SEASON_LABELS[winter] == "Winter"
    assert statcharts.SEASON_LABELS[summer] == "Summer"


# --- the command itself -----------------------------------------------------

def test_main_writes_totals_and_charts(track, tmp_path, capsys):
    first = ride(track, name="bandai.gpx")
    second = ride(track, start_lat=36.0, name="hakone.gpx")
    (first.parent / "bandai.mdx").write_text(
        "---\ntitle: 'Mount Bandai Tour'\n---\n", encoding="utf-8")

    out = tmp_path / "out"
    totals_path = out / "totals.json"
    charts_path = out / "charts.json"
    statcharts.main([
        str(first), str(second), "--skip-images", "-o", str(out),
        "--totals", str(totals_path), "--charts-json", str(charts_path),
        "--url-root", str(first.parent), "--title-from", ".mdx",
    ])

    totals = json.loads(totals_path.read_text())
    assert totals["rides"] == 2
    assert totals["distance_km"] > 0
    assert totals["high_points"][0]["title"] in {"Mount Bandai Tour", "hakone"}
    assert totals["high_points"][0]["url"].startswith("/")

    charts = json.loads(charts_path.read_text())
    assert {"elevation", "gradient", "prefecture", "season"} <= set(charts)
    assert len(charts["elevation"]["labels"]) == len(charts["elevation"]["time_pct"])


def test_main_refuses_a_track_it_cannot_measure(tmp_path, capsys):
    empty = tmp_path / "empty.gpx"
    empty.write_text('<gpx version="1.1"><trk></trk></gpx>', encoding="utf-8")
    with pytest.raises(SystemExit):
        statcharts.main([str(empty), "--skip-images", "-o", str(tmp_path)])
