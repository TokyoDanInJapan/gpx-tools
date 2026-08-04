"""The terrain-sampling machinery the voxel tools share.

Module, not tool: no `main`, writes nothing. It exists so a second tool that
needs a height field does not have to reach sideways into the one that writes
`voxel.json` - the same reason `common.gpxtools` exists. Everything here was
extracted verbatim from `terrain/voxel.py`, which remains the place to read
about the per-track artefact.

The heights come from Tilezen's terrarium tiles, hosted openly by AWS and
needing no API key - unlike the map layers in `maps.layers`, which is why they
are not in that table. A terrarium tile is a PNG whose pixels encode metres:

    elevation = (R * 256 + G + B / 256) - 32768

so decoding is exact rather than a colour-ramp guess, and sea level lands on
(128, 0, 0) rather than on a shade of blue.

Two coordinate systems meet here and it is worth being clear which is which.
Tiles live in Web Mercator. A grid is a square window on that projection,
padded around the tracks' bounding box. Mercator is conformal, so a square in
projected space is locally square on the ground - the cells come out as squares
of roughly equal metres, which is what makes the blocks look like terrain
rather than like terrain stretched north-south. The scale factor is not
constant across a large window, so a `cell_m` computed at the window's centre
latitude drifts towards the edges: a percent or two over a day ride, closer to
ten percent over a whole country.
"""

import io
import math
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from common.gpxtools import haversine
from common.net import user_agent
from terrain.elevation import ELEV_SMOOTH_M, GRADE_BANDS

#: Open elevation tiles - no key, unlike every layer in maps.layers.
TERRARIUM_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
TERRARIUM_CREDIT = "Elevation: Tilezen terrarium tiles (SRTM, ASTER, NED and others) via AWS"

#: Terrarium's encoding of exactly 0 m. What a tile the dataset does not have
#: is filled with, so a gap reads as sea rather than as a plateau.
SEA_LEVEL_RGB = (128.0, 0.0, 0.0)
TILE_PX = 256

#: Terrarium's own range. Below 8 a day ride is a handful of pixels. Above 15
#: the tiles are upsampled from the same source data, so asking costs downloads
#: and buys nothing.
MIN_ZOOM, MAX_ZOOM = 8, 15

#: How many DEM pixels to aim for per grid cell, per side. Above 1 each cell is
#: a box-mean of several samples rather than one, which matters because raw DEM
#: has speckle - a single bad pixel becomes a spike of one block standing proud
#: of its neighbours, and the eye goes straight to it.
SUPERSAMPLE = 3

#: The same, for map imagery, and higher for a different reason. A map tile is
#: not a measurement: it has roads, contour lines and place labels drawn on it,
#: and a cell of only a few pixels lands on whether a road happened to cross it.
#: Sixteen samples average that back down into the landcover underneath - the
#: green of forest, the grey of a town, the blue of water - which is the part
#: that says anything at one colour per 270 metres.
IMAGERY_SUPERSAMPLE = 4

#: Ceiling on tiles fetched for one track. A long tour spans enough ground that
#: the ideal zoom would want hundreds. Dropping a zoom level quarters the count
#: and, at that span, costs resolution the grid could not have shown anyway.
MAX_TILES = 96

#: Fraction of the track's larger dimension left as margin on each side, so the
#: route does not run along the very edge of the terrain it sits on.
PAD_FRACTION = 0.12

#: Consecutive track points further apart than this many grid cells are a
#: recording gap, not a ride. Resampling across one would lay a neat line of
#: blocks along a road nobody took.
GAP_CELLS = 8.0

def project(lat, lon):
    """Web Mercator, normalised so the whole world is the unit square.

    Zoom-independent on purpose: tile coordinates at zoom z are just this
    multiplied by 2**z, and the grid window is expressed in these units so that
    choosing a zoom later does not move it.
    """
    u = (lon + 180.0) / 360.0
    s = math.sin(math.radians(max(-85.05, min(85.05, lat))))
    v = 0.5 - math.log((1.0 + s) / (1.0 - s)) / (4.0 * math.pi)
    return u, v


def unproject_lat(v):
    """Latitude from a normalised Mercator v - the inverse of `project`'s v."""
    return math.degrees(2.0 * math.atan(math.exp((0.5 - v) * 2.0 * math.pi)) - math.pi / 2.0)


def square_window(points, pad_fraction):
    """A padded square window in projected space covering every point.

    Square because the grid is square, and a square grid over a non-square
    window would stretch the land. Padding is applied to the larger dimension
    first and the smaller one is then grown to match, so the margin around a
    long thin ride is the same on all four sides rather than proportional to
    each axis.
    """
    us, vs = zip(*(project(lat, lon) for lat, lon, _ in points))
    u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
    span = max(u1 - u0, v1 - v0)
    span = max(span, 1e-6) * (1.0 + 2.0 * pad_fraction)
    cu, cv = (u0 + u1) / 2.0, (v0 + v1) / 2.0
    return cu - span / 2.0, cv - span / 2.0, span


def choose_zoom(u0, v0, span, grid, supersample, tile_px=TILE_PX,
                min_zoom=MIN_ZOOM, max_tiles=MAX_TILES):
    """The zoom whose tiles give about `supersample` pixels per grid cell.

    Clamped to what the providers actually hold, then reduced while the covering
    rectangle would exceed `max_tiles`. Both bounds are arguments because a
    country-sized window wants a coarser floor and a bigger budget than a day
    ride does. The reduction loop is separate from the
    clamp because the tile count depends on where the window falls, not only on
    how big it is: a window straddling a tile boundary covers one more tile per
    axis than the same window nudged sideways.
    """
    # Rounded up, so `supersample` is a floor on the samples per cell rather
    # than a target it can land under. Down would be the cheaper habit, but the
    # whole reason for supersampling is to average detail away, and two samples
    # per cell does not average much.
    wanted = grid * supersample
    ideal = math.log2(max(wanted, 1) / (span * tile_px))
    zoom = max(min_zoom, min(MAX_ZOOM, int(math.ceil(ideal))))
    while zoom > min_zoom and tile_rect(span, zoom, u0, v0)[4] > max_tiles:
        zoom -= 1
    return zoom


def tile_rect(span, zoom, u0=0.0, v0=0.0):
    """(x0, y0, nx, ny, count) - the tile rectangle covering a window."""
    n = 2**zoom
    x0, y0 = int(math.floor(u0 * n)), int(math.floor(v0 * n))
    x1 = int(math.ceil((u0 + span) * n))
    y1 = int(math.ceil((v0 + span) * n))
    nx, ny = max(1, x1 - x0), max(1, y1 - y0)
    return x0, y0, nx, ny, nx * ny


def cache_dir():
    """Where fetched tiles are kept between runs.

    `GPX_TOOLS_CACHE` names it outright. Otherwise it is the user's ordinary
    cache directory. Not beside this module, which is where it used to sit: an
    installed package lives in site-packages, and a tool that fills its own
    installation directory with a couple of gigabytes of tiles is a tool that
    loses them on the next upgrade.

    Read at each call rather than fixed at import, so a caller that sets the
    variable is obeyed however it is invoked.
    """
    named = os.environ.get("GPX_TOOLS_CACHE")
    if named:
        return named
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "gpx-tools", "tiles")


def fetch_tile(url_template, cache_name, zoom, x, y):
    """One tile as raw bytes, cached on disk. None if the provider has no such tile.

    Cached because the two things you do most while developing against this -
    change the grid size, change the padding - both re-request tiles that have
    not changed. The cache path is keyed on the *layer name*, never the URL:
    a URL carries the provider's API key, and a key must not end up as a
    directory name on disk.

    A tile outside a dataset's coverage 404s. That is returned as None and read
    as sea (for elevation) or as nothing drawn (for imagery) rather than as a
    failure, which is what it means for a coastal ride whose window runs out
    over water.
    """
    path = os.path.join(cache_dir(), cache_name, str(zoom), str(x), f"{y}.png")
    if os.path.exists(path):
        with open(path, "rb") as fh:
            return fh.read()

    url = url_template.format(z=zoom, x=x, y=y)
    request = urllib.request.Request(url, headers={"User-Agent": user_agent()})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return None
        raise

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return data


def tile_mosaic(url_template, cache_name, zoom, x0, y0, nx, ny, tile_px, workers, fill):
    """Stitch a rectangle of tiles into one RGB float array.

    Fetched in parallel: the tiles are independent and the wall clock is all
    round-trips, so a pool turns a minute of waiting into a few seconds. The
    decode itself stays here on one thread - it is milliseconds per tile and
    the array has to be assembled in order anyway.

    `workers` is lower for map imagery than for elevation on purpose. The
    elevation tiles come off an S3 bucket that does not care. The map layers are
    somebody's tile server, and OpenStreetMap's usage policy in particular asks
    for restraint.

    `fill` is the RGB a missing tile leaves behind, and it is per-channel
    because the two callers mean different things by "nothing here": white paper
    for the map, and terrarium's own encoding of sea level - (128, 0, 0), not
    grey - for the elevation, so a gap off the edge of the dataset reads as
    water rather than as a 128-metre plateau.
    """
    import numpy as np
    from PIL import Image

    coords = [(x, y) for y in range(ny) for x in range(nx)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        blobs = list(
            pool.map(lambda c: fetch_tile(url_template, cache_name, zoom,
                                          x0 + c[0], y0 + c[1]), coords)
        )

    mosaic = np.full((ny * tile_px, nx * tile_px, 3), fill, dtype=np.float32)
    for (x, y), blob in zip(coords, blobs):
        if blob is None:
            continue
        pixels = np.asarray(Image.open(io.BytesIO(blob)).convert("RGB"), dtype=np.float32)
        if pixels.shape[0] != tile_px or pixels.shape[1] != tile_px:
            raise ValueError(
                f"Tile {zoom}/{x0 + x}/{y0 + y} is {pixels.shape[1]}x{pixels.shape[0]} px, "
                f"not the {tile_px} px this layer declares."
            )
        mosaic[y * tile_px : (y + 1) * tile_px, x * tile_px : (x + 1) * tile_px] = pixels
    return mosaic


def decode_terrarium(mosaic):
    """Metres from terrarium's RGB encoding, with the sea as a floor.

    The decode itself is exact rather than a colour-ramp guess: the three
    channels are one big-endian fixed-point number, so this inverts the encoding
    instead of approximating it.

    The clamp is the part worth explaining. Terrarium carries *bathymetry* as
    well as topography, and a window round a coastal ride reaches out over
    water: the South Hokkaido tour picks up the Japan Trench at -7,565 m, which
    made its relief 9.5 km instead of 2 km and left every hill the ride actually
    climbed inside the top fifth of the range. Half the posts here touch the sea.
    The sea floor is real data, but it is not ground anybody rode over, and a
    model of a ride should spend its layers on the ride. Below sea level becomes
    sea level, and the water reads as the flat plane it looks like from a bike.

    Clamped before the cells are averaged, not after, so a cell straddling a
    coastline averages land against zero rather than against a trench.
    """
    import numpy as np

    metres = mosaic[:, :, 0] * 256.0 + mosaic[:, :, 1] + mosaic[:, :, 2] / 256.0 - 32768.0
    return np.maximum(metres, 0.0)


def sample_grid(mosaic, zoom, x0, y0, u0, v0, span, grid, tile_px):
    """Box-average a mosaic into grid x grid cells.

    Takes either a plain 2D field (elevation) or a 3D one (RGB imagery) and
    returns the same rank, so both passes share one resampler. That matters more
    than saving the duplicate loop: the height field and the colour field have
    to land on *exactly* the same cell boundaries, or the colour on a block
    would be the colour of the ground next door.

    Each cell averages every source pixel whose centre falls inside it, which is
    where SUPERSAMPLE earns its keep: nine samples per cell smooth the speckle
    that makes single blocks jump, and sixteen turn a map tile's roads and
    labels back into the landcover underneath them. Done with a summed-area
    table so the cost is four lookups per cell rather than a slice per cell - at
    128x128 the difference is not important, but the loop reads better without a
    nested Python slice in it, and the table is three lines.

    A cell whose footprint rounds to nothing (the window is smaller than one
    source pixel - a very short track at low zoom) falls back to a point sample
    so it returns a value rather than a divide-by-zero.
    """
    import numpy as np

    n = 2**zoom
    # Pixel coordinates of the window's edges within the mosaic.
    px0 = (u0 * n - x0) * tile_px
    py0 = (v0 * n - y0) * tile_px
    px_span = span * n * tile_px

    height, width = mosaic.shape[0], mosaic.shape[1]
    channels = mosaic.shape[2:]

    # Accumulated in float64, explicitly. `cumsum` keeps its input's dtype, and
    # the input here is float32: a 768x768 mosaic of metres runs the running
    # total up to ~1e9, where float32 spacing is about 64, so recovering a cell
    # mean by subtracting two of those totals threw away metres. It showed up as
    # cells a metre or two below a sea that had just been clamped to exactly
    # zero - which is a harmless-looking symptom of an error that was much
    # larger in the bottom-right of every large window, and in the colour pass
    # too.
    integral = np.zeros((height + 1, width + 1) + channels, dtype=np.float64)
    integral[1:, 1:] = mosaic.cumsum(axis=0, dtype=np.float64).cumsum(axis=1)

    edges_x = np.clip(np.round(px0 + np.linspace(0, px_span, grid + 1)), 0, width).astype(int)
    edges_y = np.clip(np.round(py0 + np.linspace(0, px_span, grid + 1)), 0, height).astype(int)

    out = np.zeros((grid, grid) + channels, dtype=np.float32)
    for j in range(grid):
        ya, yb = edges_y[j], edges_y[j + 1]
        for i in range(grid):
            xa, xb = edges_x[i], edges_x[i + 1]
            if xb > xa and yb > ya:
                total = integral[yb, xb] - integral[ya, xb] - integral[yb, xa] + integral[ya, xa]
                out[j, i] = total / ((xb - xa) * (yb - ya))
            else:
                py = min(max(int(py0 + px_span * (j + 0.5) / grid), 0), height - 1)
                px = min(max(int(px0 + px_span * (i + 0.5) / grid), 0), width - 1)
                out[j, i] = mosaic[py, px]
    return out


def split_runs(points, gap_m):
    """Split a point list wherever it teleports.

    A backstop behind the file's own `<trkseg>` boundaries, not a replacement
    for them. A track that has been through track/split.py already records its
    ferries and train hops as separate segments. This catches the ones that
    were never marked - a GPS glitch, or a track exported as one long segment
    with a lift in the middle of it.

    The threshold is in cells rather than metres because what matters is
    whether the renderer would draw the gap: a 10 km hop is nothing across
    Hokkaido at 4.5 km a cell and a teleport across a 3 km valley.
    """
    runs, current = [], [points[0]]
    for previous, point in zip(points, points[1:]):
        if haversine(previous[0], previous[1], point[0], point[1]) > gap_m:
            if len(current) >= 2:
                runs.append(current)
            current = []
        current.append(point)
    if len(current) >= 2:
        runs.append(current)
    return runs


def resample(run, spacing_m):
    """Walk a run at fixed ground spacing, returning (lat, lon, ele, dist_m).

    Even spacing is what makes the route read as a line of blocks: sampled by
    GPS fix instead, a slow climb would pack blocks solid and a fast descent
    would leave them scattered, so the picture would show how fast the ride was
    rather than where it went.
    """
    out, carried = [], 0.0
    total = 0.0
    out.append((run[0][0], run[0][1], run[0][2], 0.0))
    for (lat1, lon1, ele1), (lat2, lon2, ele2) in zip(run, run[1:]):
        leg = haversine(lat1, lon1, lat2, lon2)
        if leg <= 0:
            continue
        walked = spacing_m - carried
        while walked <= leg:
            f = walked / leg
            ele = None
            if ele1 is not None and ele2 is not None:
                ele = ele1 + (ele2 - ele1) * f
            out.append((lat1 + (lat2 - lat1) * f, lon1 + (lon2 - lon1) * f, ele, total + walked))
            walked += spacing_m
        carried = (carried + leg) % spacing_m
        total += leg
    return out


def band_indices(samples):
    """A gradient band per sample, from GRADE_BANDS.

    Grade is measured over a window of about ELEV_SMOOTH_M rather than between
    adjacent samples, matching the elevation profile: consumer GPS elevation is
    noisy enough that point-to-point grades on a flat road swing through every
    band, and the map beside the chart has to agree with it.
    """
    bands = []
    for i, (_, _, _, dist) in enumerate(samples):
        j = i
        while j > 0 and dist - samples[j][3] < ELEV_SMOOTH_M:
            j -= 1
        k = i
        while k < len(samples) - 1 and samples[k][3] - dist < ELEV_SMOOTH_M:
            k += 1
        run_m = samples[k][3] - samples[j][3]
        rise = None
        if run_m > 0 and samples[j][2] is not None and samples[k][2] is not None:
            rise = samples[k][2] - samples[j][2]
        grade = abs(rise / run_m * 100.0) if rise is not None else 0.0
        bands.append(next(n for n, (upper, _, _) in enumerate(GRADE_BANDS) if grade < upper))
    return bands
