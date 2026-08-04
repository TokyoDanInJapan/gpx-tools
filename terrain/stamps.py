"""Coastlines and routes, one set of line work per country ridden.

Each country comes out as simplified coastline loops plus whatever tracks fall
inside it, in coordinates ready to draw at any size. It was written to be
written into an animation - a country stamped into a fluid simulation and then
dissolved - but the output is just line work, and nothing here knows what draws
it.

    gpx-stamps --tracks-root rides --grid 384 -o stamps.json

Entirely offline, and no API key: Japan's land comes from the prefecture
polygons that ship with this package (whose mask-boundary union erases the
internal borders for free), and the UK and New Zealand from Natural Earth's
public-domain 50m country polygons, trimmed into
`common/data/uk_nz_outlines.geojson`.

Each country's window is the padded square around its tracks and the land it is
framed on (see REGIONS), and every coordinate is a unit-square fraction of that
window, y southward: image space, ready to scale onto a field of any size.

Tracks are assigned to the first country whose bbox they touch. Whatever
matches none is printed rather than dropped silently, because a track landing
outside every region is usually a region worth adding rather than a mistake.
`tracks` and `totalKm` count everything that was drawn.
"""

import argparse
import json
import math
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# The repository root, so the packages resolve when this is run straight from a
# checkout as `python3 terrain/stamps.py`. An install puts them on the path
# already, and this is then a no-op.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.gpxtools import points_in_bbox, read_segments
from common.prefectures import DEFAULT_GEOJSON as JAPAN_GEOJSON
from terrain.sampling import (
    GAP_CELLS,
    project,
    resample,
    split_runs,
    square_window,
    unproject_lat,
)

#: Both outline sets ship with the package, so nothing here needs a network or
#: a path into somebody's tree.
UK_NZ_GEOJSON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             os.pardir, "common", "data", "uk_nz_outlines.geojson")

#: The countries the splash cycles through, in stamp order. `feature` picks a
#: single feature out of the geojson by its `nam` property; None takes all
#: (Japan's file is one feature per prefecture). `bbox` decides which tracks
#: belong to the country and is deliberately generous, so it is not a frame:
#: `window` is the land the stamp must show, and the drawing window is the
#: square around that and the country's tracks together. A country with
#: neither tracks nor a `window` falls back to framing its `bbox`.
REGIONS = [
    {"name": "japan", "geojson": JAPAN_GEOJSON, "feature": None,
     "bbox": (24.0, 122.0, 46.0, 154.0)},
    {"name": "uk", "geojson": UK_NZ_GEOJSON, "feature": "United Kingdom",
     "bbox": (49.9, -8.6, 58.8, 1.8)},
    {
        "name": "nz",
        "geojson": UK_NZ_GEOJSON,
        "feature": "New Zealand",
        "bbox": (-47.5, 165.5, -34.0, 179.9),
        # Cape Reinga to Stewart Island, Fiordland to East Cape. Without it the
        # window is the square around eight rides that reach neither end, and
        # the stamp is a fragment: no Northland, no Southland, and the two
        # islands left sitting side by side across Cook Strait.
        "window": (-47.3, 166.4, -34.4, 178.6),
    },
]

#: Boundary loops shorter than this many mask-grid edges are islets too small
#: to read as ink and are dropped.
MIN_LOOP_EDGES = 12

#: Douglas-Peucker tolerance for the line work, in mask-grid cells. The stamp
#: target is far coarser than the mask, so this trims staircase noise without
#: visibly moving a coast.
SIMPLIFY_CELLS = 1.4


def tracks_below(root, sidecar=None):
    """Every GPX below `root`, sorted, optionally only those with a sidecar.

    Sorted for deterministic output. `sidecar` is an extension - pass `.md` and
    a track without one beside it is skipped, which is how a publication says
    "this track is not published" without moving the file.
    """
    found = []
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(".gpx"):
                continue
            if sidecar and (name[:-4] + sidecar) not in filenames:
                continue
            found.append(os.path.join(dirpath, name))
    return sorted(found)


def land_mask(geojson_path, feature, u0, v0, span, grid):
    """True where a cell's centre lies inside the region's polygons.

    Exterior rings only, holes ignored. Each ring only tests the cells inside
    its own bounding box, and matplotlib's Path does the point-in-polygon work
    vectorised.
    """
    import numpy as np
    from matplotlib.path import Path

    centres = (np.arange(grid) + 0.5) / grid
    lons = (u0 + centres * span) * 360.0 - 180.0
    lats = np.array([unproject_lat(v0 + c * span) for c in centres])
    lon_grid, lat_grid = np.meshgrid(lons, lats)
    points = np.column_stack([lon_grid.ravel(), lat_grid.ravel()])

    mask = np.zeros(grid * grid, dtype=bool)
    with open(geojson_path, encoding="utf-8") as fh:
        features = json.load(fh)["features"]
    for entry in features:
        if feature is not None and entry["properties"].get("nam") != feature:
            continue
        geometry = entry["geometry"]
        if geometry["type"] == "Polygon":
            polygons = [geometry["coordinates"]]
        elif geometry["type"] == "MultiPolygon":
            polygons = geometry["coordinates"]
        else:
            continue
        for polygon in polygons:
            ring = np.asarray(polygon[0], dtype=float)[:, :2]
            candidates = (
                ~mask
                & (points[:, 0] >= ring[:, 0].min())
                & (points[:, 0] <= ring[:, 0].max())
                & (points[:, 1] >= ring[:, 1].min())
                & (points[:, 1] <= ring[:, 1].max())
            )
            if not candidates.any():
                continue
            mask[candidates] |= Path(ring).contains_points(points[candidates])
    return mask.reshape(grid, grid)


def boundary_loops(mask):
    """The land/sea boundary of a boolean grid, chained into closed loops.

    Marching the cell edges rather than the polygons is what unions Japan's
    prefectures for free: an edge between two land cells is interior and never
    emitted, so internal borders vanish and only the coast remains.
    """
    grid = mask.shape[0]
    edges = set()
    for j in range(grid):
        for i in range(grid):
            if not mask[j, i]:
                continue
            if j == 0 or not mask[j - 1, i]:
                edges.add(((i, j), (i + 1, j)))
            if j == grid - 1 or not mask[j + 1, i]:
                edges.add(((i, j + 1), (i + 1, j + 1)))
            if i == 0 or not mask[j, i - 1]:
                edges.add(((i, j), (i, j + 1)))
            if i == grid - 1 or not mask[j, i + 1]:
                edges.add(((i + 1, j), (i + 1, j + 1)))

    neighbours = {}
    for a, b in edges:
        neighbours.setdefault(a, []).append(b)
        neighbours.setdefault(b, []).append(a)

    unused = set(edges)
    loops = []
    while unused:
        start, nxt = next(iter(unused))
        loop = [start]
        prev, here = start, nxt
        unused.discard((start, nxt))
        unused.discard((nxt, start))
        while here != start:
            loop.append(here)
            step = None
            for cand in neighbours[here]:
                key = (here, cand) if (here, cand) in unused else (cand, here)
                if cand != prev and key in unused:
                    step = cand
                    unused.discard(key)
                    break
            if step is None:
                break  # open chain (should not happen on a closed mask); keep what we have
            prev, here = here, step
        loops.append(loop)
    return loops


def simplify_closed(loop, tolerance):
    """Douglas-Peucker for a closed ring.

    Run naively on a ring whose first and last points coincide, DP's baseline
    has zero length, every deviation measures zero, and the whole coast
    collapses to a point - so the ring is split at its midpoint and the two
    halves are simplified as open chains.
    """
    closed = loop + [loop[0]]
    mid = len(closed) // 2
    return simplify(closed[: mid + 1], tolerance) + simplify(closed[mid:], tolerance)[1:]


def simplify(points, tolerance):
    """Douglas-Peucker, iterative, on an open polyline."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        lo, hi = stack.pop()
        ax, ay = points[lo]
        bx, by = points[hi]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy) or 1.0
        worst, at = 0.0, None
        for k in range(lo + 1, hi):
            px, py = points[k]
            d = abs((px - ax) * dy - (py - ay) * dx) / norm
            if d > worst:
                worst, at = d, k
        if at is not None and worst > tolerance:
            keep[at] = True
            stack.append((lo, at))
            stack.append((at, hi))
    return [p for p, k in zip(points, keep) if k]


def build_stamp(region, tracks, grid):
    """One country's stamp: outline loops and route polylines in its window."""
    framed = region.get("window") or (None if tracks else region["bbox"])
    corners = []
    if framed is not None:
        south, west, north, east = framed
        corners = [(south, west, None), (north, east, None)]
    all_points = [p for _, segments in tracks for seg in segments for p in seg] + corners
    u0, v0, span = square_window(all_points, 0.08 if tracks else 0.05)

    centre_lat = unproject_lat(v0 + span / 2.0)
    world_m = 2.0 * math.pi * 6378137.0 * math.cos(math.radians(centre_lat))
    cell_m = span * world_m / grid

    mask = land_mask(region["geojson"], region["feature"], u0, v0, span, grid)
    loops = boundary_loops(mask)
    outline = []
    for loop in loops:
        if len(loop) < MIN_LOOP_EDGES:
            continue
        slim = simplify_closed(loop, SIMPLIFY_CELLS)
        outline.append([[round(x / grid, 3), round(y / grid, 3)] for x, y in slim])
    outline.sort(key=len, reverse=True)

    spacing_m = max(cell_m / 2.0, 500.0)
    routes, travelled = [], 0.0
    for _, segments in tracks:
        runs = [run for seg in segments for run in split_runs(seg, GAP_CELLS * cell_m)]
        for run in runs:
            samples = resample(run, spacing_m)
            if len(samples) < 2:
                continue
            line = []
            for lat, lon, _, _ in samples:
                u, v = project(lat, lon)
                gx = (u - u0) / span * grid
                gy = (v - v0) / span * grid
                if 0 <= gx <= grid and 0 <= gy <= grid:
                    line.append((gx, gy))
            if len(line) >= 2:
                # A circular tour ends where it began - the same degenerate
                # baseline as the coastline rings, so split it.
                ends = math.hypot(line[0][0] - line[-1][0], line[0][1] - line[-1][1])
                if len(line) > 4 and ends < SIMPLIFY_CELLS * 2.0:
                    slim = simplify_closed(list(line[:-1]), SIMPLIFY_CELLS / 2.0)
                else:
                    slim = simplify(line, SIMPLIFY_CELLS / 2.0)
                routes.append([[round(x / grid, 3), round(y / grid, 3)] for x, y in slim])
            travelled += samples[-1][3]

    print(
        f"  {region['name']}: window {span * world_m / 1000:.0f} km, {len(tracks)} tracks, "
        f"{len(outline)} loops / {sum(len(o) for o in outline)} pts, "
        f"{len(routes)} routes / {sum(len(r) for r in routes)} pts"
    )
    return {"name": region["name"], "outline": outline, "routes": routes}, travelled


def build(paths, grid):
    """Everything splash-stamps.json holds."""
    by_region = {region["name"]: [] for region in REGIONS}
    excluded = []
    for path in paths:
        try:
            segments = [seg for seg in read_segments(path) if len(seg) >= 2]
        except (ET.ParseError, FileNotFoundError) as exc:
            raise ValueError(f"{path}: {exc}") from exc
        points = [p for seg in segments for p in seg]
        home = next((r for r in REGIONS
                     if len(points) >= 2 and points_in_bbox(points, r["bbox"])), None)
        if home is None:
            excluded.append(path)
            continue
        by_region[home["name"]].append((path, segments))

    for path in excluded:
        print(f"  outside every region: {path}")

    stamps, tracks, travelled = [], 0, 0.0
    for region in REGIONS:
        stamp, km = build_stamp(region, by_region[region["name"]], grid)
        stamps.append(stamp)
        tracks += len(by_region[region["name"]])
        travelled += km

    return {
        # Unit-square window coordinates per stamp, y southward - image space,
        # ready to scale onto a smoke field of any size.
        "stamps": stamps,
        "tracks": tracks,
        "totalKm": int(round(travelled / 1000.0)),
    }


def main():
    ap = argparse.ArgumentParser(description="Trace the splash's coastline-and-route stamps.")
    ap.add_argument("gpx", nargs="*", help="paths to .gpx files, or use --tracks-root")
    ap.add_argument(
        "--tracks-root",
        help="take every .gpx below this directory instead of naming them",
    )
    ap.add_argument(
        "--sidecar",
        help="with --tracks-root, skip a track with no sibling file of this "
             "extension (e.g. .md) - which is how a publication says a track is "
             "not published without moving it",
    )
    ap.add_argument("-o", "--out", default="splash-stamps.json", help="output JSON path")
    ap.add_argument(
        "--grid",
        type=int,
        default=384,
        help="mask cells per side each coast is traced at (default: 384)",
    )
    args = ap.parse_args()

    paths = args.gpx or (tracks_below(args.tracks_root, args.sidecar) if args.tracks_root else [])
    if not paths:
        ap.error("give some .gpx files, or --tracks-root")
    print(f"Tracing {len(REGIONS)} countries and {len(paths)} candidate tracks")
    try:
        data = build(paths, args.grid)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))
    print(f"Wrote {args.out} ({os.path.getsize(args.out) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
