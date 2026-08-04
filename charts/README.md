# charts/

Aggregating many tracks into statistics. One tool, `gpx-statcharts`, needing
matplotlib unless `--skip-images` is given.

```bash
gpx-statcharts rides/**/*.gpx -o out/ \
    --charts-json out/charts.json --totals out/totals.json \
    --url-root rides --title-from .md
```

## What it writes

Four images pairing riding time against distance - by elevation band, gradient
band, prefecture and season - plus `totals.json`, plus optionally `--charts-json`
carrying the same figures as data so a page can draw its own charts.

`--skip-images` writes only the JSON, and drops the matplotlib dependency with
it. A site that renders its own SVG charts wants exactly that.

## How the numbers are built

Every consecutive pair of points contributes its distance and, where there are
timestamps, its elapsed time to one bin. The bins come from the pair itself:
mid elevation for the elevation bands, a distance-smoothed slope for the
gradient bands, point-in-polygon every ~200 m for the prefectures, and the
ride's start date for the season.

The movement gates are `common.rides`', which is the point - a single ride's
`gpx-ridestats` figures are computed by the same rules, so the parts add up to
the whole.

Percentages are relative to each chart's own total, and the prefecture charts
normalise over the in-Japan total so their bars sum to about 100%. A ride
outside Japan resolves to no prefecture, so it is absent from those two charts.
It still counts in every other figure.

## Naming the rides

`totals.json` carries a top-five list of rides by their high point, and a ride
in it needs a name and a link. Both are supplied rather than assumed:

- `--url-root DIR` - a track below `DIR` is linked by its path within it.
- `--title-from EXT` - the title comes from the `title:` in the frontmatter of
  the track's sibling `EXT` file.

Without them a ride is named by its filename and linked as `/<filename>`, which
is honest about knowing nothing rather than guessing a directory layout.
