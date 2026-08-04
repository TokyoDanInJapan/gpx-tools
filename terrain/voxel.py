"""The terrain a ride crosses, sampled into a grid the browser can build blocks from.

The route maps this repo already generates are flat pictures: tiles from above,
with the track drawn over them. They say where a ride went and, with the
elevation profile beside them, how much climbing it took - but the two facts sit
in separate graphics and the reader has to join them up. This writes the third
thing needed to put them in one picture: the *land*, as a height per cell over
the area the track covers, so a page can stand the route on the hills it
actually climbed.

The heights come from Tilezen's terrarium tiles, hosted openly by AWS and needing
no API key - unlike the map layers in maps.layers, which is why they are not in
that table. A terrarium tile is a PNG whose pixels encode metres:

    elevation = (R * 256 + G + B / 256) - 32768

so decoding is exact rather than a colour-ramp guess, and sea level lands on
(128, 0, 0) rather than on a shade of blue.

Two coordinate systems meet here and it is worth being clear which is which.
Tiles live in Web Mercator. The grid is a square window on that projection,
padded around the track's bounding box. Mercator is conformal, so a square in
projected space is locally square on the ground - the grid cells come out as
squares of roughly equal metres, which is what makes the blocks look like
terrain rather than like terrain stretched north-south. The scale factor is not
constant across a large window, so `cell_m` is the size at the window's centre
latitude and drifts a percent or two at the edges. At the size of a day ride
that is far below one cell.

Output is a `voxel.json` beside the post's other map artefacts:

    gpx-voxel <file.gpx> [--grid 96] [--imagery [LAYER]]

It carries the height field, the route resampled into grid coordinates and
banded by the same gradient colours as the elevation profile, and the numbers a
renderer needs to label what it draws. What it does *not* carry is any choice
about how the terrain looks - the block quantisation, the vertical exaggeration
and the palette are all the component's business, because they are the dials you
turn while looking at the thing.

`--imagery` adds a second pass over the same window, this time through the
ordinary map layers in maps.layers, averaging each cell down to one colour and
writing them as a `grid x grid` PNG - `voxel-map.png`. That is the same picture
as the post's flat `map.jpg`, resampled onto the blocks, so the reader can swap
between reading the terrain by height and reading it by what is actually on the
ground: forest, town, water, snow. It is off by default because it needs an API
key and the elevation pass does not.

One colour per cell is the whole point rather than a limitation. At 270 metres a
cell there is no road left to see, and a texture with more detail than the grid
would smear map across the face of a block instead of colouring the block. The
supersampling ratios differ for the same reason the passes differ: elevation is
averaged to kill DEM speckle, imagery to average a drawn map back into the
landcover under it.
"""

import argparse
import json
import math
import os
import sys
from pathlib import Path

# The repository root, so the packages resolve when this is run straight from a
# checkout as `python3 terrain/voxel.py`. An install puts them on the path already,
# and this is then a no-op.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.gpxtools import read_segments
from maps.layers import DEFAULT_LAYER, LAYERS, resolve_layer
from terrain.elevation import GRADE_BANDS
from terrain.sampling import (
    GAP_CELLS,
    IMAGERY_SUPERSAMPLE,
    PAD_FRACTION,
    SEA_LEVEL_RGB,
    SUPERSAMPLE,
    TERRARIUM_CREDIT,
    TERRARIUM_URL,
    TILE_PX,
    band_indices,
    choose_zoom,
    decode_terrarium,
    project,
    resample,
    sample_grid,
    split_runs,
    square_window,
    tile_mosaic,
    tile_rect,
    unproject_lat,
)

# ---------------------------------------------------------------------------
# Fetching and decoding
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# The route, in grid coordinates
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def sample_imagery(layer, api_key, u0, v0, span, grid):
    """The map, box-averaged to one colour per grid cell, as a PIL image.

    Written as a `grid x grid` PNG rather than as numbers in voxel.json for two
    reasons. It is a third of the size a JSON array of triples would be, and it
    is already the shape the renderer wants: a texture, uploaded once and
    sampled at cell centres with nearest-neighbour filtering, so each block top
    comes out one flat colour and the blocks stay blocks.

    Fetched on the same window as the height field, so cell (i, j) of this image
    is cell (i, j) of that field - the colour belongs to the block it lands on.
    """
    from PIL import Image

    url_template, attribution, tile_px = resolve_layer(layer, api_key)
    zoom = choose_zoom(u0, v0, span, grid, IMAGERY_SUPERSAMPLE, tile_px)
    x0, y0, nx, ny, count = tile_rect(span, zoom, u0, v0)

    print(f"  imagery: {layer}, zoom {zoom}, {count} tiles")
    # White, not black, for tiles the provider does not have: the map layers are
    # paper-like and a missing tile should read as blank paper, not as a hole.
    mosaic = tile_mosaic(url_template, layer, zoom, x0, y0, nx, ny, tile_px, 4,
                         (255.0, 255.0, 255.0))
    cells = sample_grid(mosaic, zoom, x0, y0, u0, v0, span, grid, tile_px)
    return Image.fromarray(cells.round().clip(0, 255).astype("uint8"), "RGB"), attribution


def build(gpx_path, grid, layer=None, api_key=None):
    """Everything voxel.json holds, plus the imagery PNG when one was asked for."""
    import numpy as np

    segments = [seg for seg in read_segments(gpx_path) if len(seg) >= 2]
    points = [p for seg in segments for p in seg]
    if len(points) < 2:
        raise ValueError("Need at least two track points to sample terrain around.")

    u0, v0, span = square_window(points, PAD_FRACTION)
    zoom = choose_zoom(u0, v0, span, grid, SUPERSAMPLE)
    x0, y0, nx, ny, count = tile_rect(span, zoom, u0, v0)

    centre_lat = unproject_lat(v0 + span / 2.0)
    # Metres per unit of projected space at this latitude, hence per grid cell.
    world_m = 2.0 * math.pi * 6378137.0 * math.cos(math.radians(centre_lat))
    cell_m = span * world_m / grid

    print(f"  window {span * world_m / 1000:.1f} km square, zoom {zoom}, {count} tiles")
    mosaic = tile_mosaic(TERRARIUM_URL, "terrarium", zoom, x0, y0, nx, ny, TILE_PX, 8,
                         SEA_LEVEL_RGB)
    heights = sample_grid(decode_terrarium(mosaic), zoom, x0, y0, u0, v0, span, grid, TILE_PX)

    # Resolved before any of the route work below, so a missing API key fails
    # while there is still nothing on disk to be half-written.
    imagery, imagery_credit = (None, None)
    if layer:
        imagery, imagery_credit = sample_imagery(layer, api_key, u0, v0, span, grid)

    # Walked at a third of a cell so the track is followed round its switchbacks
    # rather than across them, then thinned to one sample per cell entered.
    #
    # Thinning matters more than it sounds. A mountain-bike loop doubling back
    # inside a 3 km window is sampled every 10 m, which is four thousand points
    # describing a few hundred cells - and the renderer paints cells, so all but
    # the first sample in each was bytes nobody could see. On those rides it is
    # most of the file.
    #
    # It is safe because of what it drops: only a sample landing in the cell the
    # last kept one is already in. Two kept samples are therefore in adjacent
    # cells - the point just before the second was in the first's cell - so
    # although they can be nearly two cells apart in a straight line (a cell's
    # diagonal, plus the step that left it), that line only ever crosses the two
    # cells the track was actually in. The renderer walks it in half-cell steps,
    # which cannot skip one.
    spacing_m = max(cell_m / 3.0, 10.0)
    route, travelled, segment = [], 0.0, 0
    runs = [run for seg in segments for run in split_runs(seg, GAP_CELLS * cell_m)]
    for run in runs:
        samples = resample(run, spacing_m)
        if len(samples) < 2:
            continue
        last_cell = None
        for (lat, lon, _, dist), band in zip(samples, band_indices(samples)):
            u, v = project(lat, lon)
            gx = (u - u0) / span * grid
            gy = (v - v0) / span * grid
            if not (0 <= gx < grid and 0 <= gy < grid):
                continue
            cell = (int(gx), int(gy))
            if cell == last_cell:
                continue
            last_cell = cell
            # The first crossing of a cell wins, which is also how the renderer
            # resolves a cell visited twice - so an out-and-back keeps the
            # climb's gradient colours rather than the descent's gentler ones.
            route.append([round(gx, 2), round(gy, 2), band,
                          round((travelled + dist) / 1000, 2), segment])
        travelled += samples[-1][3]
        # Numbered per run that produced samples, and carried on every one of
        # them, because the renderer walks from each sample to the next and has
        # no other way to know that two of them are not joined. Without it a
        # ferry is drawn as a road: this is what the split above is *for*, and
        # flattening the runs into one list without the number threw the whole
        # thing away.
        segment += 1

    data = {
        "grid": grid,
        "cellM": round(cell_m, 1),
        "heights": [int(round(h)) for h in heights.flatten()],
        "hMin": int(round(float(np.min(heights)))),
        "hMax": int(round(float(np.max(heights)))),
        # [grid x, grid y, gradient band, km along the ride, segment].
        # Fractional cell coordinates rather than cell indices: the renderer
        # decides how coarse to draw, and rounding here would fix that decision
        # in the data. The segment is what stops it joining a ferry to a road.
        "route": route,
        "bands": [colour for _, colour, _ in GRADE_BANDS],
        "bandLabels": [label for _, _, label in GRADE_BANDS],
        "bounds": {
            "south": round(unproject_lat(v0 + span), 6),
            "north": round(unproject_lat(v0), 6),
            "west": round(u0 * 360.0 - 180.0, 6),
            "east": round((u0 + span) * 360.0 - 180.0, 6),
        },
        "credit": TERRARIUM_CREDIT,
    }
    if imagery_credit:
        # The provider's required attribution travels with the picture it
        # belongs to, so the page cannot show one without the other.
        data["imageryCredit"] = imagery_credit
        data["imageryLayer"] = layer
    return data, imagery


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Sample the terrain around a GPX track into a voxel grid."
    )
    ap.add_argument("gpx", help="path to the .gpx file")
    ap.add_argument(
        "--outdir",
        default=None,
        help="directory for voxel.json (default: the GPX file's directory)",
    )
    ap.add_argument(
        "--grid",
        type=int,
        default=96,
        help="cells per side of the height field (default: 96)",
    )
    ap.add_argument(
        "--imagery",
        nargs="?",
        const=DEFAULT_LAYER,
        default=None,
        choices=sorted(LAYERS),
        metavar="LAYER",
        help=f"also sample a map layer into voxel-map.png (default layer: {DEFAULT_LAYER})",
    )
    ap.add_argument(
        "--api-key",
        default=None,
        help="override the imagery layer's API key (else its env var or .env)",
    )
    args = ap.parse_args(argv)

    if args.grid < 8 or args.grid > 512:
        raise SystemExit("--grid must be between 8 and 512.")

    outdir = args.outdir or os.path.dirname(os.path.abspath(args.gpx))
    os.makedirs(outdir, exist_ok=True)
    out_path = os.path.join(outdir, "voxel.json")
    imagery_path = os.path.join(outdir, "voxel-map.png")

    print(f"Sampling terrain for {args.gpx}")
    data, imagery = build(args.gpx, args.grid, args.imagery, args.api_key)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))

    size_kb = os.path.getsize(out_path) / 1024
    print(
        f"  {data['grid']}x{data['grid']} cells of {data['cellM']:.0f} m, "
        f"{data['hMin']}-{data['hMax']} m, {len(data['route'])} route samples"
    )
    print(f"Wrote {out_path} ({size_kb:.0f} KB)")

    if imagery is not None:
        imagery.save(imagery_path, optimize=True)
        print(f"Wrote {imagery_path} ({os.path.getsize(imagery_path) / 1024:.0f} KB)")


if __name__ == "__main__":
    sys.exit(main())
