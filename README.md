# gpx-tools

Command-line tools for turning a GPX track into the things a ride or hike
write-up needs: the figures, the route map, the elevation profile, the terrain
model, and the photographs placed along the way.

## Tools

Tools are grouped by what they do. Each group has its own README.

| group | what it does |
| --- | --- |
| [`track/`](track/README.md) | reading and editing the track itself - stats, merge, split, metadata |
| [`maps/`](maps/README.md) | drawing a track onto map tiles, with the places it passes |
| [`terrain/`](terrain/README.md) | the elevation profile, the voxel model, and country line work |
| [`photos/`](photos/README.md) | placing a gallery's photographs along the track |
| [`charts/`](charts/README.md) | aggregating many tracks into statistics and charts |

`common/` holds what they share: reading a GPX (`gpxtools.py`), reading one with
its clock and the gates that decide what counts as riding (`rides.py`), the
Japanese prefecture boundaries (`prefectures.py`), and how the tools identify
themselves to the servers they fetch from (`net.py`).

## Install

The tools need Python 3.9 or later. The core is standard library plus PyYAML.
The drawing and photograph tools need more, grouped as extras:

```bash
pip install .                    # stats, profiles, merge/split, metadata
pip install ".[maps]"            # + route maps and coverage maps (staticmap, Pillow)
pip install ".[charts]"          # + the aggregate bar charts (matplotlib)
pip install ".[photos]"          # + photograph placement (Pillow, timezonefinder)
pip install ".[all]"             # everything
pip install -e ".[all,dev]"      # or, to keep editing in place
```

Every tool also runs directly from a checkout with no install step, because it
puts the repository root on the path itself:

```bash
python3 track/stats.py --help
```

> The package directories are top-level names - `common`, `track`, `maps`,
> `terrain`, `photos`, `charts` - so give this a virtualenv of its own rather
> than sharing one with anything that might claim the same names. That is how a
> toolbox like this is used anyway.

## What it does not know

These tools know about tracks, tiles, terrain and photographs. They do not know
what a blog post is.

That line is deliberate, and it is why several tools take an argument where you
might expect a convention. Where a track's page lives, what a ride is called,
which directory the photographs mirror - all of that belongs to whatever
publishes the track, so it is stated by the caller:

```bash
gpx-statcharts rides/**/*.gpx --url-root rides --title-from .md
gpx-photo-points ride.gpx --originals ~/photos --track-root rides --outroot build/rides
gpx-metadata --all --config gpx-tools.yaml
```

Run without them, each tool falls back to the file itself - a ride is named by
its filename, output lands beside the track - which is right for a one-off and
wrong for a site. Nothing is guessed from a path.

## Keys, caches and courtesy

Three tools fetch from somebody else's server, and all three are configured by
the environment rather than by a file in the installation:

| variable | what it does |
| --- | --- |
| `GPX_TOOLS_ENV` | path to a `.env` holding the tile provider API keys (default: `./.env`) |
| `GPX_TOOLS_CACHE` | where fetched tiles are kept (default: `~/.cache/gpx-tools`) |
| `GPX_TOOLS_USER_AGENT` | what the tools call themselves when fetching |

Key precedence is `--api-key`, then the layer's own environment variable, then
the `.env`. A key never appears in output: the map carries the provider's
attribution, not its key, and the tile cache is keyed on the layer name for the
same reason.

Before any heavy geocoding run, set `GPX_TOOLS_USER_AGENT` to something with a
way of reaching you in it. Nominatim rate-limits by user agent and refuses a
request that has none. The default names this project. That is enough for
occasional use, and not enough to be a good citizen at volume.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[all,dev]"

.venv/bin/pytest --cov     # tests, with the coverage floor enforced
.venv/bin/ruff check .     # lint
```

The tests build their own tracks rather than shipping GPX files, so the suite
needs no fixtures on disk and runs in a couple of seconds. `tests/conftest.py`
explains the geometry they use and why it is checkable by hand.

Nothing in the suite touches the network. The three tools that fetch are tested
either side of the fetch - what would be requested, and what is made of the
answer - with the request itself stubbed.

Coverage must stay at or above 60%, a floor set in `pyproject.toml` and enforced
both locally and in CI. It is deliberately not higher: over half of this code is
drawing, where a test can say a file appeared but not that the picture in it is
right. What is covered is everywhere a wrong answer could hide:
the geometry, the movement gates, the gradient banding, the clock calibration,
and the two outputs that are verifiable to the byte.

`ruff check` sorts imports, and wraps a long one to a name per line. Everything
else is laid out by hand: `ruff format` is deliberately not part of the checks,
because reformatting the tools wholesale would cost more in readability than it
would win in consistency.

### Proving a change did not move the output

Four of these tools produce output that can be checked exactly rather than
eyeballed, which is the cheapest test this repository has:

- **`gpx-mapgen --elevation-only`** is pure standard library and needs no
  network, so it can regenerate every profile a site holds. A correct refactor
  leaves all of them byte-identical.
- **`gpx-ridestats`** prints the same JSON it writes, so it can be diffed
  against a committed stat card without touching it.
- **`gpx-statcharts --skip-images`** and **`gpx-prefecture-map --paths-json`**
  both write JSON only, and both are deterministic.

All four were used to check this package against the tools it was extracted
from: 60 profiles, 60 stat cards, the aggregate totals and the prefecture map
data all came out unchanged.

## Releases

A release is a pushed tag. Everything after that is automatic.

```bash
# Set the version in pyproject.toml first. The workflow checks that they agree.
git tag -a v1.1.0 --cleanup=verbatim -F notes.md
git push origin v1.1.0
```

`.github/workflows/release.yml` then runs the checks against the tagged tree,
builds the sdist and the wheel, and attaches both to a GitHub Release, taking
the notes from the tag's own message. Nothing is published to PyPI. To install a
release, point `pip` at the wheel on the release page, or at the tag:

```bash
pip install "gpx-tools[all] @ git+https://github.com/TokyoDanInJapan/gpx-tools@v1.1.1"
```

Pin the tag rather than tracking a branch. Several of these tools write JSON
that something else reads - `route.json`, `photos.json`, `voxel.json`, the stat
cards - and a consumer that follows `main` can have its input change under it
without a commit of its own.

## The bundled data, which is not ours

Two GeoJSON files ship with the package so the tools that need boundaries need
no network and no key. **The MIT licence below covers the code, not them.**

- **`japan_prefectures.geojson`** — the 47 prefectures, derived from Global Map
  Japan (地球地図日本), © 国土地理院 (GSI), obtained through
  [dataofjapan/land](https://github.com/dataofjapan/land). Credit the source if
  you use it, and note that commercial use is asked to file a usage report with
  GSI - something this licence cannot grant on your behalf.
- **`uk_nz_outlines.geojson`** — the UK and New Zealand, trimmed from
  [Natural Earth](https://www.naturalearthdata.com/) 1:50m, which is public
  domain and asks for no credit.

[`common/data/README.md`](common/data/README.md) says the same, and ships inside
the wheel beside the files themselves.

## Licence

MIT. See [LICENSE](LICENSE). It covers the code. See above for the data.
