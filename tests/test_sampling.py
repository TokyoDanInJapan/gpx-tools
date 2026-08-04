"""The terrain sampling: the window, the zoom, the decode and the ride path.

Nothing here fetches a tile. Everything that decides *what* would be fetched,
and everything that turns bytes into ground, is arithmetic - which is the half
where a wrong answer is invisible in the picture and obvious in a number.
"""

import math

import pytest

from terrain import sampling

# --- projection -------------------------------------------------------------

def test_project_puts_null_island_at_the_centre_of_the_unit_square():
    assert sampling.project(0.0, 0.0) == pytest.approx((0.5, 0.5))


def test_project_and_unproject_round_trip():
    for lat in (-60.0, -43.5, 0.0, 35.68, 71.0):
        u, v = sampling.project(lat, 139.0)
        assert sampling.unproject_lat(v) == pytest.approx(lat, abs=1e-9)


def test_project_clamps_the_poles():
    """Mercator runs to infinity at 90 degrees. The clamp is what stops it."""
    assert sampling.project(89.9, 0.0)[1] == sampling.project(85.05, 0.0)[1]


# --- the window -------------------------------------------------------------

def points(*coords):
    return [(lat, lon, None) for lat, lon in coords]


def test_the_window_is_square():
    u0, v0, span = sampling.square_window(
        points((35.0, 139.0), (35.5, 139.02)), pad_fraction=0.0)
    # A square in projected space, which is what a square grid needs.
    assert span > 0
    assert isinstance(span, float)


def test_the_window_covers_every_point():
    track = points((35.0, 139.0), (35.2, 139.3), (34.9, 139.1))
    u0, v0, span = sampling.square_window(track, pad_fraction=0.0)
    for lat, lon, _ in track:
        u, v = sampling.project(lat, lon)
        assert u0 <= u <= u0 + span
        assert v0 <= v <= v0 + span


def test_padding_widens_the_window_around_the_same_centre():
    track = points((35.0, 139.0), (35.2, 139.3))
    bare = sampling.square_window(track, pad_fraction=0.0)
    padded = sampling.square_window(track, pad_fraction=0.12)
    assert padded[2] == pytest.approx(bare[2] * 1.24)
    assert padded[0] + padded[2] / 2 == pytest.approx(bare[0] + bare[2] / 2)


def test_a_stationary_track_still_gets_a_window():
    """Division by a zero span would otherwise take the whole run down."""
    _, _, span = sampling.square_window(points((35.0, 139.0), (35.0, 139.0)), 0.12)
    assert span > 0


# --- choosing a zoom --------------------------------------------------------

def test_tile_rect_counts_the_covering_rectangle():
    x0, y0, nx, ny, count = sampling.tile_rect(span=1.0, zoom=1, u0=0.0, v0=0.0)
    assert (nx, ny, count) == (2, 2, 4)


def test_a_window_straddling_a_boundary_costs_an_extra_tile():
    """Which is why the tile budget is checked against the window, not the span."""
    aligned = sampling.tile_rect(span=0.5, zoom=1, u0=0.0, v0=0.0)[4]
    straddling = sampling.tile_rect(span=0.5, zoom=1, u0=0.25, v0=0.25)[4]
    assert straddling > aligned


def test_choose_zoom_stays_inside_the_providers_range():
    zoom = sampling.choose_zoom(0.0, 0.0, span=1e-5, grid=128, supersample=3)
    assert sampling.MIN_ZOOM <= zoom <= sampling.MAX_ZOOM


def test_choose_zoom_drops_back_to_stay_inside_the_tile_budget():
    """A day ride's window gets the zoom it asked for only if it can afford it."""
    u0, v0, span = sampling.square_window(points((35.0, 139.0), (35.4, 139.4)), 0.12)
    zoom = sampling.choose_zoom(u0, v0, span, grid=128, supersample=3)
    assert sampling.tile_rect(span, zoom, u0, v0)[4] <= sampling.MAX_TILES


def test_a_window_too_large_for_the_budget_bottoms_out_rather_than_giving_up():
    """A multi-day tour cannot be afforded at any useful zoom, so it takes the
    coarsest one the providers hold and pays for the tiles."""
    u0, v0, span = sampling.square_window(points((33.0, 130.0), (43.0, 145.0)), 0.12)
    assert sampling.choose_zoom(u0, v0, span, grid=128, supersample=3) == sampling.MIN_ZOOM


def test_a_smaller_window_gets_a_closer_zoom():
    small = sampling.square_window(points((35.0, 139.0), (35.05, 139.05)), 0.12)
    large = sampling.square_window(points((33.0, 130.0), (43.0, 145.0)), 0.12)
    assert (sampling.choose_zoom(*small, grid=128, supersample=3)
            > sampling.choose_zoom(*large, grid=128, supersample=3))


# --- decoding ---------------------------------------------------------------

def terrarium(metres):
    """Encode a height the way the tiles do, so the decode can be inverted."""
    numpy = pytest.importorskip("numpy")
    raw = metres + 32768.0
    r = math.floor(raw / 256)
    g = math.floor(raw - r * 256)
    b = math.floor((raw - r * 256 - g) * 256)
    return numpy.array([[[r, g, b]]], dtype=float)


def test_decode_terrarium_inverts_the_encoding():
    for metres in (0.0, 1.0, 1917.0, 3776.0):
        decoded = sampling.decode_terrarium(terrarium(metres))
        assert decoded[0][0] == pytest.approx(metres, abs=0.01)


def test_the_sea_floor_becomes_sea_level():
    """The Japan Trench is real data and not ground anybody rode over."""
    assert sampling.decode_terrarium(terrarium(-7565.0))[0][0] == 0.0


# --- the ride path ----------------------------------------------------------

def line(n, step_deg=0.001, start_lat=35.0, ele=None):
    return [(start_lat + i * step_deg, 139.0, None if ele is None else ele + i)
            for i in range(n)]


def test_split_runs_breaks_at_a_teleport():
    """A backstop behind the file's own segments, for the hops nobody marked."""
    runs = sampling.split_runs(line(5) + line(5, start_lat=40.0), gap_m=1000)
    assert [len(r) for r in runs] == [5, 5]


def test_split_runs_leaves_a_continuous_track_alone():
    assert len(sampling.split_runs(line(20), gap_m=1000)) == 1


def test_split_runs_drops_a_lone_point():
    runs = sampling.split_runs(line(4) + line(1, start_lat=40.0), gap_m=1000)
    assert [len(r) for r in runs] == [4]


def test_resample_walks_at_a_fixed_spacing():
    """Sampled by GPS fix, the picture would show how fast the ride was."""
    samples = sampling.resample(line(50, step_deg=0.001), spacing_m=200.0)
    gaps = [b[3] - a[3] for a, b in zip(samples, samples[1:])]
    assert all(g == pytest.approx(200.0, abs=1e-6) for g in gaps)


def test_resample_interpolates_the_elevation():
    samples = sampling.resample(line(10, step_deg=0.01, ele=100.0), spacing_m=500.0)
    heights = [s[2] for s in samples]
    assert heights == sorted(heights)
    assert all(h is not None for h in heights)


def test_resample_keeps_the_start():
    samples = sampling.resample(line(10), spacing_m=1000.0)
    assert samples[0][3] == 0.0


def test_band_indices_are_one_per_sample():
    samples = sampling.resample(line(60, step_deg=0.001, ele=100.0), spacing_m=50.0)
    bands = sampling.band_indices(samples)
    assert len(bands) == len(samples)
    assert all(0 <= b < len(sampling.GRADE_BANDS) for b in bands)


def test_a_flat_ride_sits_in_the_gentlest_band():
    flat = [(35.0 + i * 0.001, 139.0, 100.0) for i in range(60)]
    samples = sampling.resample(flat, spacing_m=50.0)
    assert set(sampling.band_indices(samples)) == {0}


def test_a_steep_climb_reaches_the_steepest_band():
    # 50 m of rise per ~111 m of ground is well past the last edge.
    steep = [(35.0 + i * 0.001, 139.0, 100.0 + i * 50.0) for i in range(60)]
    samples = sampling.resample(steep, spacing_m=50.0)
    assert max(sampling.band_indices(samples)) == len(sampling.GRADE_BANDS) - 1


# --- the cache --------------------------------------------------------------

def test_the_cache_is_not_beside_the_installed_module(monkeypatch, tmp_path):
    monkeypatch.delenv("GPX_TOOLS_CACHE", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert sampling.cache_dir().startswith(str(tmp_path))


def test_the_cache_can_be_pointed_somewhere(monkeypatch, tmp_path):
    monkeypatch.setenv("GPX_TOOLS_CACHE", str(tmp_path / "tiles"))
    assert sampling.cache_dir() == str(tmp_path / "tiles")
