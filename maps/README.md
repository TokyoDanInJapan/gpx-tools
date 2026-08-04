# maps/

Drawing a track onto map tiles. Needs `staticmap` and Pillow
(`pip install ".[maps]"`), and `gpx-prefecture-map` needs matplotlib as well
unless it is only asked for `--paths-json`.

| tool | what it does |
| --- | --- |
| `gpx-mapgen` | one route as `map.jpg` and `elevation.svg`. `--overlay` writes `route.json` instead of drawing the route |
| `gpx-multimap` | many routes on one auto-fitted coverage map |
| `gpx-prefecture-map` | a map of Japan shading the prefectures the tracks passed through |
| `gpx-poi-check` | how far each plotted place sits from the route |

## Layers and keys

`layers.py` holds the table. Layers with a `key_env` need a free API key from
that provider. The rest need nothing.

| layers | key | labels |
| --- | --- | --- |
| `opencyclemap` (default), `transport`, `landscape`, `outdoors` | `THUNDERFOREST_API_KEY` | local script |
| `geoapify` | `GEOAPIFY_API_KEY` | English/romanised where OSM has it |
| `maptiler` | `MAPTILER_API_KEY` | English where OSM has `name:en` |
| `osm`, `opentopomap` | none | local script |

Precedence is `--api-key`, then the environment variable, then a `.env` -
`GPX_TOOLS_ENV` if set, otherwise `./.env`. There is deliberately no search
of the installation directory or the home directory: an installed package has
no repository root to hang a dotfile off, and guessing one reads a file the
caller never offered.

Tile labels are baked in by the provider, so the language of a map depends on
that provider's coverage and cannot be changed afterwards.

## Places of interest

With `--post`, `gpx-mapgen` reads the Markdown links out of a write-up, works
out what each one is from its wording and the heading it sits under, geocodes it
against the route's bounding box, and numbers the results on the map.

Two things make that safe to re-run. Results are cached in a hand-editable
`pois.json` beside the map, so a place already resolved is never geocoded
again, and a coordinate corrected by hand survives. And a place the geocoder
cannot find is cached with a null `lat`/`lon` rather than dropped - which keeps
it off the map, keeps it visible in the cache, and stops the next run asking
again. A null is an instruction, not a gap.

`gpx-poi-check` is the audit: it prints each place's minimum distance to the
track and flags anything beyond about 5 km, exiting 1 if any place is far
enough out to be dropped rather than corrected.

Bilingual write-ups repeat every link inside a Japanese translation block. Those
are skipped, or every marker would be plotted twice.

## --overlay and route.json

By default the route is drawn onto the tiles. With `--overlay` the tiles are
left plain and the route is written to `route.json` instead, for a page to draw
as SVG over the image:

| key | what it holds |
| --- | --- |
| `map` | the image's own pixel space, and the Mercator window it covers |
| `route` | polylines in that space, one entry per gradient band, with a segment index |
| `hover` | a thinned table of (km, elevation, x, y, segment) for syncing a cursor with the profile |
| `pois` | the numbered places, positioned in the same space |
| `plot` | the elevation profile's coordinate system, so the two graphics can be linked |

Migrate a page atomically: regenerate `map.jpg` and `route.json` together and
swap the markup in the same commit, or you get a map with no route on it.

`hover` is thinned to about one sample per chart unit and is for the cursor
only. Anything that needs the geometry - deciding whether a thumbnail overlaps
the road, for instance - has to read `route`, not `hover`.
