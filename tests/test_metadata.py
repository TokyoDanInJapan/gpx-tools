"""Writing the <metadata> block from a post, and preserving what we do not own."""

import xml.etree.ElementTree as ET

from conftest import gpx, northward

from track import metadata

CONFIG = {
    "author": {"name": "A Rider", "email": "rider@example.com"},
    "site": "https://example.com",
    "license": "https://creativecommons.org/licenses/by-nc/4.0/",
    "posts": {"root": "posts", "sidecar": ".mdx"},
}


def post(tmp_path, title="Mount Bandai Tour", extra=""):
    """A track and its sibling post, in the tree layout the config describes."""
    directory = tmp_path / "posts" / "cycling" / "touring"
    directory.mkdir(parents=True)
    track = directory / "bandai.gpx"
    track.write_text(gpx([northward(3)]), encoding="utf-8")
    (directory / "bandai.mdx").write_text(
        f"---\ntitle: '{title}'\npublishDate: 2019-10-05T00:00:00Z\n"
        f"excerpt: 'Three days round the mountain'\n{extra}---\n\nProse.\n",
        encoding="utf-8")
    return track


def config_for(tmp_path):
    config = {k: (dict(v) if isinstance(v, dict) else v) for k, v in CONFIG.items()}
    config["posts"]["root"] = str(tmp_path / "posts")
    return config


def test_load_config_defaults_to_empty_rather_than_inventing(tmp_path, monkeypatch):
    """A block must not claim an author or a licence it was never told."""
    monkeypatch.chdir(tmp_path)
    config = metadata.load_config()
    assert config["site"] is None
    assert config["author"] == {"name": None, "email": None}
    assert config["posts"]["sidecar"] == ".mdx"


def test_load_config_reads_the_file_beside_you(tmp_path, monkeypatch):
    (tmp_path / "gpx-tools.yaml").write_text(
        "site: https://example.com\nposts:\n  root: rides\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    config = metadata.load_config()
    assert config["site"] == "https://example.com"
    assert config["posts"]["root"] == "rides"
    # A partial `posts:` block must not wipe the key it did not mention.
    assert config["posts"]["sidecar"] == ".mdx"


def test_post_url_is_the_path_below_the_root(tmp_path):
    track = post(tmp_path)
    url = metadata.post_url(str(track), config_for(tmp_path))
    assert url == "https://example.com/cycling/touring/bandai"


def test_post_url_is_none_without_a_site(tmp_path):
    """A relative href in a file that will be opened anywhere points at nothing."""
    track = post(tmp_path)
    config = config_for(tmp_path)
    config["site"] = None
    assert metadata.post_url(str(track), config) is None


def test_the_block_carries_the_title_author_and_licence(tmp_path):
    track = post(tmp_path)
    status, info = metadata.update_file(str(track), config_for(tmp_path))
    assert (status, info) == ("ok", "Mount Bandai Tour")

    root = ET.parse(track).getroot()
    meta = next(el for el in root.iter() if el.tag.endswith("metadata"))
    text = {el.tag.split("}")[-1]: el.text for el in meta}
    assert text["name"] == "Mount Bandai Tour"
    assert text["desc"] == "Three days round the mountain"
    author = next(el for el in meta if el.tag.endswith("author"))
    assert next(el for el in author if el.tag.endswith("name")).text == "A Rider"
    email = next(el for el in author if el.tag.endswith("email"))
    assert email.attrib == {"id": "rider", "domain": "example.com"}


def test_the_year_comes_from_the_publish_date(tmp_path):
    track = post(tmp_path)
    metadata.update_file(str(track), config_for(tmp_path))
    assert "<year>2019</year>" in track.read_text()


def test_a_second_run_changes_nothing(tmp_path):
    """Idempotence is what makes this safe to run over the whole tree."""
    track = post(tmp_path)
    config = config_for(tmp_path)
    metadata.update_file(str(track), config)
    first = track.read_text()
    assert metadata.update_file(str(track), config) == ("skip", "no change")
    assert track.read_text() == first


def test_a_track_with_no_post_is_skipped(tmp_path):
    track = post(tmp_path)
    (track.parent / "bandai.mdx").unlink()
    assert metadata.update_file(str(track), config_for(tmp_path))[0] == "skip"


def test_a_post_with_no_title_is_skipped(tmp_path):
    track = post(tmp_path)
    (track.parent / "bandai.mdx").write_text("---\npublishDate: 2019\n---\n", encoding="utf-8")
    assert metadata.update_file(str(track), config_for(tmp_path)) == (
        "skip", "no title in frontmatter")


def test_an_existing_time_and_bounds_survive(tmp_path):
    """We own name/desc/author/copyright. Everything else is the recorder's."""
    track = post(tmp_path)
    text = track.read_text().replace(
        "<trk>",
        "<metadata><time>2019-10-05T00:00:00Z</time>"
        '<bounds minlat="35.0" minlon="139.0" maxlat="35.1" maxlon="139.1" />'
        "<name>Whatever the exporter called it</name></metadata><trk>", 1)
    track.write_text(text, encoding="utf-8")

    metadata.update_file(str(track), config_for(tmp_path))
    written = track.read_text()
    assert "2019-10-05T00:00:00Z" in written
    assert "minlat=" in written
    assert "Whatever the exporter called it" not in written
    assert "Mount Bandai Tour" in written


def test_the_garmin_boilerplate_link_is_dropped(tmp_path):
    track = post(tmp_path)
    text = track.read_text().replace(
        "<trk>",
        '<metadata><link href="http://www.garmin.com">'
        "<text>Garmin Connect</text></link></metadata><trk>", 1)
    track.write_text(text, encoding="utf-8")

    metadata.update_file(str(track), config_for(tmp_path))
    assert "Garmin Connect" not in track.read_text()


def test_a_preserved_child_is_reindented_not_shifted(tmp_path):
    """Re-indenting from structure is what stops a block drifting right."""
    block = metadata.reindent_element(
        "<link href='https://example.com'>\n        <text>Bandai</text>\n   </link>")
    assert block == ("    <link href='https://example.com'>\n"
                     "      <text>Bandai</text>\n"
                     "    </link>")
    assert metadata.reindent_element(block) == block


def test_main_walks_the_tree(tmp_path, monkeypatch, capsys):
    post(tmp_path)
    (tmp_path / "gpx-tools.yaml").write_text(
        "site: https://example.com\n"
        "author: {name: A Rider, email: rider@example.com}\n"
        "posts: {root: posts, sidecar: .mdx}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert metadata.main(["--all"]) == 0
    assert "updated" in capsys.readouterr().out
