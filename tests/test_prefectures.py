"""Point-in-polygon over the prefecture boundaries that ship with the package."""

import json
import pathlib

import pytest

from common.prefectures import DEFAULT_GEOJSON, PrefectureLocator, clean_name


@pytest.fixture(scope="module")
def locator():
    """The real boundaries. Loading them is the slow part, so load them once."""
    return PrefectureLocator()


def test_the_boundaries_ship_with_the_package():
    """A wheel that installs without its data is a tool that cannot run."""
    with open(DEFAULT_GEOJSON, encoding="utf-8") as fh:
        data = json.load(fh)
    assert len(data["features"]) == 47


def test_their_attribution_ships_with_them():
    """These polygons are somebody else's, and the distributor asks to be
    credited. A wheel carrying the data without the notice would leave the
    credit behind in a repository nobody installing it has read."""
    notice = pathlib.Path(DEFAULT_GEOJSON).parent / "README.md"
    assert notice.exists()
    text = notice.read_text(encoding="utf-8")
    assert "国土地理院" in text
    assert "dataofjapan/land" in text
    assert "Natural Earth" in text


def test_clean_name_handles_the_four_that_are_not_ken():
    assert clean_name("Hokkai Do") == "Hokkaido"
    assert clean_name("Tokyo To") == "Tokyo"
    assert clean_name("Osaka Fu") == "Osaka"
    assert clean_name("Kyoto Fu") == "Kyoto"


def test_clean_name_drops_the_ken_suffix():
    assert clean_name("Kanagawa Ken") == "Kanagawa"


def test_clean_name_leaves_an_unexpected_name_alone():
    assert clean_name("Somewhere Else") == "Somewhere Else"


@pytest.mark.parametrize(("lat", "lon", "expected"), [
    (35.6762, 139.6503, "Tokyo"),      # the metropolitan government building
    (35.4437, 139.6380, "Kanagawa"),   # Yokohama
    (43.0618, 141.3545, "Hokkaido"),   # Sapporo
    (34.6937, 135.5023, "Osaka"),      # Osaka
    (26.2124, 127.6809, "Okinawa"),    # Naha, and the reason the bbox is wide
])
def test_locate_places_a_known_city(locator, lat, lon, expected):
    assert locator.locate(lat, lon) == expected


def test_locate_returns_none_outside_japan(locator):
    """An overseas ride is absent from the prefecture charts, not miscounted."""
    assert locator.locate(-43.5321, 172.6362) is None   # Christchurch
    assert locator.locate(33.0, 143.0) is None          # the open Pacific


def test_a_hint_does_not_change_the_answer(locator):
    """The hint is a shortcut for the common case, never an override."""
    assert locator.locate(43.0618, 141.3545, hint="Tokyo") == "Hokkaido"
    assert locator.locate(35.6762, 139.6503, hint="Tokyo") == "Tokyo"


def test_a_multipolygon_prefecture_keeps_its_islands(locator):
    """Okinawa is dozens of separate rings. One of them is not the whole answer."""
    okinawa = dict(locator.prefs)["Okinawa"]
    assert len(okinawa) > 1
