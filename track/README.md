# track/

Reading and editing the track itself. Everything here is standard library
except the two tools that read a post's YAML frontmatter, and nothing here
touches the network.

| tool | what it does |
| --- | --- |
| `gpx-stats` | distance, ascent, descent, high and low points, bounding box, as JSON |
| `gpx-ridestats` | the three figures a stat card shows. `--write` emits `<slug>.stats.json` |
| `gpx-merge` | several files into one track, one `<trkseg>` each |
| `gpx-merge-segments` | the opposite: collapse a track's segments into one |
| `gpx-split` | break a segment wherever the track teleports |
| `gpx-metadata` | write the house `<metadata>` block from a sibling post |

## Two figures, two tools

`gpx-stats` measures the track. `gpx-ridestats` measures the *ride* - which is
not the same thing, and the difference is the movement gates in
`common.rides`. A pair of points counts as distance only if it moved at all,
and as riding time only if its speed sits between about 1 and 90 km/h. Stops
and GPS spikes are therefore excluded from the time, which is why a stat card's
moving time is shorter than the clock says.

Those gates live in `common/` rather than here so that a single ride's figures
and the site-wide totals in `charts/` are computed by exactly the same rules. A
ride that did not add up to the total it rolls into would be worse than no
total at all.

A track with no `<time>` stamps gets `moving_time_s: null` rather than a
guess, so the card can hide its Time tile instead of showing a fiction.

## Merging and splitting are opposites, on purpose

Split where the gap is real - a ferry, a train, a drive between trailheads -
so nothing downstream draws a line across it. Merge where the gap is an
artefact of the recorder pausing, and the segments would otherwise read as legs
that were never separate.

Both are conservative, and both are idempotent. `gpx-split` measures only
*within* an existing segment, so a track that already records its ferries is
left alone. `gpx-merge-segments` joins segments only inside one `<trk>`, so a
file holding two rides keeps them apart.

## gpx-metadata and its config

The block it writes names an author, a licence and a page, none of which is a
property of a GPX file. They come from a config file - `gpx-tools.yaml` in the
working directory, or `--config`:

```yaml
author:  {name: A Rider, email: you@example.com}
site:    https://example.com
license: https://creativecommons.org/licenses/by-nc/4.0/
posts:   {root: rides, sidecar: .md}
```

`posts.root` is what makes a track's path into a URL: a file at
`<root>/cycling/touring/2019/bandai.gpx` is published at
`<site>/cycling/touring/2019/bandai`. `posts.sidecar` is the extension of the
file whose frontmatter carries the `title`, `excerpt` and `publishDate`.

Anything the config does not say is left out of the block rather than invented.
With no config at all the tool still runs and writes a name and a description.

We own `name`, `desc`, `author` and `copyright`, and replace them. Everything
else the recorder wrote - `link`, `time`, `keywords`, `bounds`, `extensions` -
is preserved and re-emitted in schema order, with one exception: the
`<link><text>Garmin Connect</text></link>` boilerplate exporters add is
dropped.

Preservation is idempotent because a kept child is re-indented from its own
structure rather than shifted by its current indentation. A block that picked
up stray whitespace on an earlier run is normalised, not moved further right.
