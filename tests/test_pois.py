"""Finding the places a write-up links to, and resolving them to coordinates.

Geocoding is the one part that talks to somebody else's server, so it is the
one part stubbed here. Everything above it - which links are places, what kind
of place each is, and what gets asked of the geocoder - is testable outright,
and is where the wrong answers would come from.
"""

import json

import pytest

from maps import pois


def write(tmp_path, body):
    path = tmp_path / "ride.mdx"
    path.write_text(body, encoding="utf-8")
    return path


def test_a_plain_link_is_a_place(tmp_path):
    found = pois.extract_pois(write(tmp_path, "Rode past [Lake Miyagase](https://example.com).\n"))
    assert [p["name"] for p in found] == ["Lake Miyagase"]
    assert found[0]["category"] == "poi"


def test_a_relative_link_is_not_a_place(tmp_path):
    """Cross-references to other posts and #anchors are not somewhere to plot."""
    body = "See [the last ride](/cycling/touring/bandai) and [this bit](#gear).\n"
    assert pois.extract_pois(write(tmp_path, body)) == []


def test_the_japanese_name_becomes_the_query(tmp_path):
    """Far more reliable in Japan than the romanised label."""
    body = "Past [Lake Miyagase (宮ヶ瀬湖)](https://example.com).\n"
    (found,) = pois.extract_pois(write(tmp_path, body))
    assert found["name"] == "Lake Miyagase"
    assert found["query"] == "宮ヶ瀬湖"


def test_a_latin_parenthetical_is_not_mistaken_for_a_name(tmp_path):
    body = "Past [Mount Cook (Aoraki)](https://example.com).\n"
    (found,) = pois.extract_pois(write(tmp_path, body))
    assert found["query"] == "Mount Cook"


def test_the_label_types_the_place(tmp_path):
    body = ("- [Doshi Campsite](https://example.com)\n"
            "- [Yamanaka Onsen](https://example.com)\n")
    found = pois.extract_pois(write(tmp_path, body))
    assert [p["category"] for p in found] == ["camp", "onsen"]


def test_the_section_types_a_bare_name(tmp_path):
    body = ("## Campsites\n\n[Somewhere Quiet](https://example.com)\n")
    (found,) = pois.extract_pois(write(tmp_path, body))
    assert found["category"] == "camp"


@pytest.mark.parametrize("opening", [
    '<div class="lang-ja" lang="ja">',
    '<div class="aw-lang-ja" lang="ja">',
    '<div class="lang-ja">',
    '<div lang="ja">',
])
def test_the_japanese_translation_block_is_skipped(tmp_path, opening):
    """Otherwise every marker on a bilingual post is plotted twice."""
    body = ('<div class="lang-en">\n\n'
            'Rode past [Lake Miyagase](https://example.com).\n\n'
            "</div>\n\n"
            f"{opening}\n\n"
            '[宮ヶ瀬湖](https://example.com)\n\n'
            "</div>\n")
    found = pois.extract_pois(write(tmp_path, body))
    assert [p["name"] for p in found] == ["Lake Miyagase"]


def test_a_place_linked_twice_is_listed_once(tmp_path):
    body = ("[Lake Miyagase](https://example.com) … and back past "
            "[Lake Miyagase](https://example.com).\n")
    assert len(pois.extract_pois(write(tmp_path, body))) == 1


def test_resolve_reads_the_cache_rather_than_the_network(tmp_path, monkeypatch):
    """The cache is hand-editable, so what it says wins."""
    monkeypatch.setattr(pois, "geocode", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("geocode must not be called when the cache has the answer")))
    post = write(tmp_path, "[Lake Miyagase](https://example.com)\n")
    cache = tmp_path / "pois.json"
    cache.write_text(json.dumps({"Lake Miyagase": {
        "query": "宮ヶ瀬湖", "url": "https://example.com",
        "category": "poi", "lat": 35.53, "lon": 139.23,
    }}), encoding="utf-8")

    (resolved,) = pois.resolve_pois(str(post), None, str(cache))
    assert resolved["name"] == "Lake Miyagase"
    assert (resolved["lat"], resolved["lon"]) == (35.53, 139.23)
    assert resolved["category"] == "poi"


def test_a_null_in_the_cache_keeps_a_place_off_the_map(tmp_path, monkeypatch):
    """A deliberate null is an instruction, not a gap to fill in again."""
    calls = []
    monkeypatch.setattr(pois, "geocode", lambda *a, **k: calls.append(a) or (1.0, 2.0))
    post = write(tmp_path, "[Somewhere Vague](https://example.com)\n")
    cache = tmp_path / "pois.json"
    cache.write_text(json.dumps({"Somewhere Vague": {
        "query": "Somewhere Vague", "url": "https://example.com",
        "category": "poi", "lat": None, "lon": None,
    }}), encoding="utf-8")

    assert pois.resolve_pois(str(post), None, str(cache)) == []
    assert calls == []


def test_an_unknown_place_is_geocoded_and_written_back(tmp_path, monkeypatch):
    monkeypatch.setattr(pois, "geocode", lambda query, viewbox, **k: (35.53, 139.23))
    post = write(tmp_path, "[Lake Miyagase (宮ヶ瀬湖)](https://example.com)\n")
    cache = tmp_path / "pois.json"

    resolved = pois.resolve_pois(str(post), None, str(cache))
    assert resolved[0]["lat"] == 35.53

    written = json.loads(cache.read_text())
    assert written["Lake Miyagase"]["query"] == "宮ヶ瀬湖"
    assert written["Lake Miyagase"]["lat"] == 35.53


def test_a_place_the_geocoder_cannot_find_is_cached_as_null(tmp_path, monkeypatch):
    """So the next run does not ask again, and a human can fill it in."""
    monkeypatch.setattr(pois, "geocode", lambda query, viewbox, **k: None)
    post = write(tmp_path, "[Nowhere In Particular](https://example.com)\n")
    cache = tmp_path / "pois.json"

    assert pois.resolve_pois(str(post), None, str(cache)) == []
    assert json.loads(cache.read_text())["Nowhere In Particular"]["lat"] is None


def test_an_unreadable_cache_is_rebuilt_rather_than_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(pois, "geocode", lambda query, viewbox, **k: (1.0, 2.0))
    post = write(tmp_path, "[Somewhere](https://example.com)\n")
    cache = tmp_path / "pois.json"
    cache.write_text("{ this is not json", encoding="utf-8")

    assert pois.resolve_pois(str(post), None, str(cache))[0]["lat"] == 1.0
