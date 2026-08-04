#!/usr/bin/env python3
"""
Draw a map of Japan, shading visited prefectures green and unvisited ones red.

Given any number of GPX files, this works out which Japanese prefectures the
tracks pass through (point-in-polygon, sampled along each track) and renders a
choropleth of all 47 prefectures: visited ones a translucent green, unvisited a
translucent red, with a key in the bottom-right. It draws the prefecture
boundaries directly from the GeoJSON, so there are no map tiles and no API key.

Usage:
  gpx-prefecture-map <file.gpx> [<file.gpx> ...] [-o OUT.jpg]
      [--prefectures GEOJSON] [--bbox S W N E]
      [--visited-color HEX] [--unvisited-color HEX] [--alpha A]

  # all cycling tours
  gpx-prefecture-map rides/**/*.gpx -o out/japan-prefectures.jpg

The default view frames the main archipelago plus the Okinawa main island. The
far Ogasawara / Sakishima outliers are cropped (override with --bbox). Points
outside Japan (overseas trips) simply match no prefecture and are ignored.

Dependencies: matplotlib (in the repo venv). The prefecture GeoJSON defaults to
tools/data/japan_prefectures.geojson.
"""

import argparse
import os
import sys
import xml.etree.ElementTree as ET

# Reuse the prefecture lookup (cleaned names + exterior rings) and the segment
# reader / distance helper from the stats tool, so detection here matches the
# stats charts exactly.
from pathlib import Path

# The repository root, so the packages resolve when this is run straight from a
# checkout as `python3 maps/prefecture_map.py`. An install puts them on the path already,
# and this is then a no-op.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.gpxtools import haversine
from common.prefectures import DEFAULT_GEOJSON, PrefectureLocator
from common.rides import read_timed_segments

SAMPLE_M = 1000.0  # resolve a prefecture roughly every kilometre along a track
DEFAULT_BBOX = (24.0, 126.5, 46.0, 146.5)  # south, west, north, east


def visited_prefectures(gpx_paths, locator):
    """Return the set of prefecture names any track passes through."""
    visited = set()
    for path in gpx_paths:
        try:
            segments = read_timed_segments(path)
        except (ET.ParseError, FileNotFoundError) as exc:
            print(f"Skipping {path}: {exc}", file=sys.stderr)
            continue
        for seg in segments:
            hint = None
            since = SAMPLE_M  # sample the first point immediately
            prev = None
            for p in seg:
                lat, lon = p[0], p[1]
                if prev is not None:
                    since += haversine(prev[0], prev[1], lat, lon)
                prev = (lat, lon)
                if since >= SAMPLE_M:
                    since = 0.0
                    name = locator.locate(lat, lon, hint)
                    if name:
                        visited.add(name)
                        hint = name
    return visited


def _simplify(ring, tol):
    """Douglas-Peucker ring simplification in degrees (iterative)."""
    if len(ring) <= 4 or tol <= 0:
        return ring
    keep = [False] * len(ring)
    keep[0] = keep[-1] = True
    stack = [(0, len(ring) - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        ax, ay = ring[a][0], ring[a][1]
        bx, by = ring[b][0], ring[b][1]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        worst, worst_d2 = -1, tol * tol
        for i in range(a + 1, b):
            px, py = ring[i][0] - ax, ring[i][1] - ay
            t = max(0.0, min(1.0, (px * dx + py * dy) / seg2)) if seg2 else 0.0
            ex, ey = px - t * dx, py - t * dy
            d2 = ex * ex + ey * ey
            if d2 > worst_d2:
                worst, worst_d2 = i, d2
        if worst >= 0:
            keep[worst] = True
            stack.append((a, worst))
            stack.append((worst, b))
    return [p for p, k in zip(ring, keep) if k]


def paths_json(locator, visited, out_path, bbox, width=640.0, tol=0.02):
    """Write per-prefecture SVG path data (Web-Mercator projected) as JSON.

    The page renders this inline (see PrefectureMapSvg.astro) so the map can be
    styled by the site's theme. Rings are Douglas-Peucker simplified by `tol`
    degrees and tiny offshore islets are dropped to keep the payload small.
    """
    import json as _json
    import math

    south, west, north, east = bbox

    def merc_y(lat):
        return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))

    y_top, y_bot = merc_y(north), merc_y(south)
    scale = width / (east - west)
    height = (y_top - y_bot) / math.radians(1) * scale  # same scale on both axes

    def project(lon, lat):
        x = (lon - west) * scale
        y = (y_top - merc_y(lat)) / math.radians(1) * scale
        return round(x, 1), round(y, 1)

    prefs = []
    for name, polys in locator.prefs:
        ds = []
        for (minx, miny, maxx, maxy), ring in polys:
            # Skip islets too small to see at this scale (~2px diagonal).
            if (maxx - minx) + (maxy - miny) < 0.05:
                continue
            pts = [project(p[0], p[1]) for p in _simplify(ring, tol)]
            if len(pts) < 4:
                continue
            ds.append("M" + "L".join(f"{x} {y}" for x, y in pts) + "Z")
        if ds:
            prefs.append({"name": name, "visited": name in visited, "d": "".join(ds)})

    payload = {
        "width": round(width),
        "height": round(height),
        "prefs": sorted(prefs, key=lambda p: p["name"]),
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        _json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write("\n")


def render(locator, visited, out_path, bbox, visited_color, unvisited_color, alpha):
    """Draw all prefectures: visited shaded one colour, unvisited another."""
    import math

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import PatchCollection
    from matplotlib.colors import to_rgb
    from matplotlib.patches import Patch
    from matplotlib.patches import Polygon as MplPolygon

    sea = "#dceaf5"  # pale sea blue, fills the whole canvas behind the land
    south, west, north, east = bbox
    fig, ax = plt.subplots(figsize=(9, 11))
    fig.patch.set_facecolor(sea)

    visited_patches, other_patches = [], []
    for name, polys in locator.prefs:
        target = visited_patches if name in visited else other_patches
        for _bbox, ring in polys:
            target.append(MplPolygon(ring, closed=True))

    vr, vg, vb = to_rgb(visited_color)
    ur, ug, ub = to_rgb(unvisited_color)
    visited_rgba = (vr, vg, vb, alpha)
    unvisited_rgba = (ur, ug, ub, alpha)
    visited_edge, unvisited_edge = "#1b5e20", "#7a0000"
    ax.add_collection(PatchCollection(
        other_patches, facecolor=unvisited_rgba, edgecolor=unvisited_edge, linewidths=0.4))
    ax.add_collection(PatchCollection(
        visited_patches, facecolor=visited_rgba, edgecolor=visited_edge, linewidths=0.5))

    ax.set_xlim(west, east)
    ax.set_ylim(south, north)
    # Correct for latitude so Japan isn't horizontally squashed.
    ax.set_aspect(1.0 / math.cos(math.radians((south + north) / 2)))
    ax.axis("off")

    # Key, bottom-right (out over the empty Pacific).
    ax.legend(
        handles=[
            Patch(facecolor=visited_rgba, edgecolor=visited_edge, label="Visited"),
            Patch(facecolor=unvisited_rgba, edgecolor=unvisited_edge, label="Not visited"),
        ],
        loc="lower right", frameon=True, framealpha=0.95, fontsize=12,
        borderpad=0.8, handlelength=1.4,
    )

    fig.tight_layout(pad=0.5)
    fig.savefig(out_path, format="jpeg", dpi=110, bbox_inches="tight",
                facecolor=sea)
    plt.close(fig)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Map of Japan with visited prefectures shaded transparent red."
    )
    ap.add_argument("gpx", nargs="+", help="paths to .gpx files")
    ap.add_argument("-o", "--out", default="prefecture-map.jpg", help="output JPEG path")
    ap.add_argument(
        "--prefectures", default=DEFAULT_GEOJSON,
        help="GeoJSON of prefecture polygons",
    )
    ap.add_argument(
        "--bbox", type=float, nargs=4, metavar=("S", "W", "N", "E"), default=DEFAULT_BBOX,
        help="map view bounds (south west north east); default frames the main islands + Okinawa",
    )
    ap.add_argument("--visited-color", default="#2e9e3f",
                    help="fill colour for visited prefectures")
    ap.add_argument("--unvisited-color", default="#cc0000",
                    help="fill colour for unvisited prefectures")
    ap.add_argument("--alpha", type=float, default=0.45, help="fill transparency (0-1)")
    ap.add_argument(
        "--paths-json", default=None,
        help="instead of a JPEG, write per-prefecture SVG path data as JSON "
             "(for inline, theme-aware rendering on the page)",
    )
    ap.add_argument(
        "--simplify", type=float, default=0.02,
        help="Douglas-Peucker tolerance in degrees for --paths-json (default 0.02)",
    )
    args = ap.parse_args(argv)

    if not os.path.exists(args.prefectures):
        print(f"Error: {args.prefectures} not found.", file=sys.stderr)
        sys.exit(1)

    try:
        locator = PrefectureLocator(args.prefectures)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Error: prefecture data unusable ({exc}).", file=sys.stderr)
        sys.exit(1)

    visited = visited_prefectures(args.gpx, locator)
    if not visited:
        print("Warning: no prefectures matched (no in-Japan track points?).", file=sys.stderr)

    if args.paths_json:
        os.makedirs(os.path.dirname(os.path.abspath(args.paths_json)), exist_ok=True)
        paths_json(locator, visited, args.paths_json, args.bbox, tol=args.simplify)
        print(f"Wrote {args.paths_json}")
    else:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        try:
            render(locator, visited, args.out, args.bbox,
                   args.visited_color, args.unvisited_color, args.alpha)
        except ImportError as exc:
            print(f"Error: missing dependency ({exc.name}); install matplotlib.", file=sys.stderr)
            sys.exit(1)
        print(f"Wrote {args.out}")
    print(f"  {len(visited)} / {len(locator.prefs)} prefectures visited")


if __name__ == "__main__":
    sys.exit(main())
