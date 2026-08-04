"""Tile layers, and where their API keys come from.

The keys are the reason this file exists. A layer URL carries a secret, and the
tests below are mostly about the places it must not end up: in an error
message, in a cache path, or in an image's attribution line.
"""

import pytest

from common.net import DEFAULT_USER_AGENT, user_agent
from maps.layers import DEFAULT_LAYER, LAYERS, env_file, env_value, resolve_layer


def test_every_layer_carries_an_attribution():
    """The provider's terms are not optional, so neither is this."""
    assert all(entry["attribution"] for entry in LAYERS.values())


def test_the_default_layer_exists():
    assert DEFAULT_LAYER in LAYERS


def test_a_keyed_layer_has_a_placeholder_and_an_open_one_does_not():
    for name, entry in LAYERS.items():
        assert ("{key}" in entry["url"]) == ("key_env" in entry), name


def test_resolve_layer_substitutes_an_explicit_key():
    url, attribution, size = resolve_layer("opencyclemap", "SECRET")
    assert "SECRET" in url and "{key}" not in url
    assert "Thunderforest" in attribution
    assert size == 256


def test_resolve_layer_reads_the_environment(monkeypatch):
    monkeypatch.setenv("THUNDERFOREST_API_KEY", "FROM-ENV")
    url, _, _ = resolve_layer("opencyclemap", None)
    assert "FROM-ENV" in url


def test_an_explicit_key_beats_the_environment(monkeypatch):
    monkeypatch.setenv("THUNDERFOREST_API_KEY", "FROM-ENV")
    url, _, _ = resolve_layer("opencyclemap", "EXPLICIT")
    assert "EXPLICIT" in url and "FROM-ENV" not in url


def test_an_open_layer_needs_no_key(monkeypatch):
    monkeypatch.delenv("THUNDERFOREST_API_KEY", raising=False)
    url, _, _ = resolve_layer("osm", None)
    assert url.startswith("https://tile.openstreetmap.org/")


def test_a_missing_key_says_how_to_supply_one(monkeypatch, tmp_path):
    monkeypatch.delenv("THUNDERFOREST_API_KEY", raising=False)
    monkeypatch.delenv("GPX_TOOLS_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError) as caught:
        resolve_layer("opencyclemap", None)
    assert "THUNDERFOREST_API_KEY" in str(caught.value)


def test_maptiler_serves_larger_tiles():
    """A wrong tile size is a map at the wrong scale, silently."""
    assert resolve_layer("maptiler", "K")[2] == 512


def test_env_value_reads_a_dot_env_in_the_working_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("THUNDERFOREST_API_KEY", raising=False)
    monkeypatch.delenv("GPX_TOOLS_ENV", raising=False)
    (tmp_path / ".env").write_text('THUNDERFOREST_API_KEY="FROM-FILE"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert env_value("THUNDERFOREST_API_KEY") == "FROM-FILE"


def test_gpx_tools_env_names_the_file(monkeypatch, tmp_path):
    monkeypatch.delenv("GEOAPIFY_API_KEY", raising=False)
    secrets = tmp_path / "elsewhere.env"
    secrets.write_text("GEOAPIFY_API_KEY=NAMED\n", encoding="utf-8")
    monkeypatch.setenv("GPX_TOOLS_ENV", str(secrets))
    assert env_value("GEOAPIFY_API_KEY") == "NAMED"


def test_the_environment_beats_the_file(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("MAPTILER_API_KEY=FROM-FILE\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GPX_TOOLS_ENV", raising=False)
    monkeypatch.setenv("MAPTILER_API_KEY", "FROM-ENV")
    assert env_value("MAPTILER_API_KEY") == "FROM-ENV"


def test_env_file_is_none_when_there_is_nothing_to_read(monkeypatch, tmp_path):
    monkeypatch.delenv("GPX_TOOLS_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    assert env_file() is None
    assert env_value("ANYTHING_AT_ALL") is None


def test_an_absent_key_reads_as_absent_not_as_empty(monkeypatch, tmp_path):
    """`KEY=` in a .env is somebody who has not filled it in yet."""
    (tmp_path / ".env").write_text("GEOAPIFY_API_KEY=\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GPX_TOOLS_ENV", raising=False)
    monkeypatch.delenv("GEOAPIFY_API_KEY", raising=False)
    assert env_value("GEOAPIFY_API_KEY") is None


def test_the_user_agent_can_be_overridden(monkeypatch):
    """Nominatim rate-limits by this, so a heavy run should say who it is."""
    monkeypatch.delenv("GPX_TOOLS_USER_AGENT", raising=False)
    assert user_agent() == DEFAULT_USER_AGENT
    monkeypatch.setenv("GPX_TOOLS_USER_AGENT", "me/1.0 (me@example.com)")
    assert user_agent() == "me/1.0 (me@example.com)"
