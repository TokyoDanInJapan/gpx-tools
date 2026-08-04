# terrain/

The ground the ride crossed: the elevation profile beside it, and the terrain
model under it.

| module | what it does |
| --- | --- |
| `elevation.py` | the gradient bands, the simplifier, and the profile SVG |
| `gpx-voxel` | samples elevation tiles into a square height field around a track |

## elevation.py has no command of its own

It is imported rather than run - by `gpx-mapgen`, which draws the profile and
bands the route overlay by the same gradients, and by `gpx-statcharts`, which
bins distance into the same bands. One definition of what counts as steep,
shared by every graphic that shows it.

It is also pure standard library and needs no network, which is what lets
`gpx-mapgen --elevation-only` regenerate every profile a site holds while
offline. That is the cheapest correctness check this repository has: regenerate
and diff, and a correct change leaves every file byte-identical.

## gpx-voxel

```bash
gpx-voxel ride.gpx --grid 128 --imagery opencyclemap --outdir out/
```

Samples Tilezen's open terrarium elevation tiles - no key needed - into a
`grid x grid` height field around the track, and writes `voxel.json`. With
`--imagery` it makes a second pass through the ordinary map layers, averages
each cell down to one colour, and writes a `grid x grid` PNG beside it.

Tiles are cached between runs, under `GPX_TOOLS_CACHE` or
`~/.cache/gpx-tools`. Not beside the module: an installed package lives in
site-packages, and a tool that fills its own installation with a couple of
gigabytes of tiles loses them on the next upgrade. The cache path is keyed on
the layer *name*, never the URL, because a URL carries an API key and a key must
not become a directory name.

### What voxel.json holds

| key | what it holds |
| --- | --- |
| `grid`, `cellM` | the field's size, and what one cell is worth on the ground |
| `heights` | `grid * grid` metres, row-major |
| `hMin`, `hMax` | the range, for the colour ramp |
| `route` | the ride resampled at even spacing: cell x, cell y, gradient band, km, segment |
| `bands`, `bandLabels` | the gradient palette, matching the elevation profile |
| `bounds` | the window in degrees |
| `credit` | the elevation data's attribution |
| `imageryCredit`, `imageryLayer` | present only when `--imagery` was used |

`imageryCredit` travels with the imagery. Rendering the PNG without printing the
credit is a licence problem, not a styling choice - do not separate them.

### Three things that are easy to get wrong

- **The sea floor is clamped to sea level before averaging.** Terrarium carries
  bathymetry, and a window round a coastal ride reaches out over water: one tour
  here picks up the Japan Trench at -7,565 m, which would make its relief 9.5 km
  instead of 2 km and flatten every hill the ride actually climbed. Clamping
  before the cells are averaged is what lets a cell straddling a coastline
  average land against zero rather than against a trench.
- **The window is square.** The grid is square, and a square grid over an
  oblong window stretches the land. Padding grows the larger dimension first so
  the margin is even on all four sides.
- **The route is resampled at fixed ground spacing**, not per GPS fix.
  Otherwise a slow climb packs blocks solid and a fast descent scatters them,
  and the picture shows how fast the ride was rather than where it went.

A multi-day tour gets a window kilometres across, so its cells are coarse and
its relief heavily exaggerated. That is a property of fitting a square window to
a long ride, not a bug - but whatever renders `cellM` should say so.
