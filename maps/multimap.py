#!/usr/bin/env python3
"""
Generate a single OpenStreetMap coverage map from many GPX tracks.

Where maps/mapgen.py renders one route (plus an elevation profile), this tool
takes any number of GPX files and draws every route onto one map, auto-fitting
the view to cover them all. Use it for "everywhere I've ridden" style maps.

Usage:
  gpx-multimap <file.gpx> [<file.gpx> ...] [-o OUT]
      [--layer LAYER] [--api-key KEY] [--width PX] [--height PX]
      [--line-width PX] [--color HEX] [--bbox S W N E]

  # all cycling routes in Japan, excluding overseas tracks via a bounding box
  gpx-multimap rides/**/*.gpx -o out/coverage.jpg --bbox 24 122 46 154

Each input file is drawn as its own polyline, so separate trips stay visually
distinct even where they cross. The view, zoom and centre are fitted to all the
routes that pass the optional --bbox filter.

The tile layers, API-key handling and attribution stamping are shared with
maps/mapgen.py (imported from it), so the same --layer choices and the same
.env / env-var key resolution apply. See that file's header for the layer list.

Dependencies (same as maps/mapgen.py): staticmap, Pillow. Install into the venv:

  .venv/bin/pip install staticmap matplotlib
  gpx-multimap <files...>
"""

import argparse
import os
import sys
import xml.etree.ElementTree as ET

# Reuse the layer table, key resolution, point parsing and attribution stamp
# from the single-track tool so there is one source of truth for all of it.
from pathlib import Path

# The repository root, so the packages resolve when this is run straight from a
# checkout as `python3 maps/multimap.py`. An install puts them on the path already,
# and this is then a no-op.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.gpxtools import read_segments
from maps.layers import DEFAULT_LAYER, LAYERS, resolve_layer
from maps.mapgen import _draw_attribution


def in_bbox(points, bbox):
    """True if any of a route's points fall inside (south, west, north, east).

    A route is kept when it has at least one point in the box, so a track that
    merely clips the region still counts. With bbox None, every route is kept.
    """
    if bbox is None:
        return True
    south, west, north, east = bbox
    return any(south <= lat <= north and west <= lon <= east for lat, lon, _ in points)


def render_multimap(
    routes, out_path, width, height, url_template, attribution, tile_size,
    line_width, color,
):
    """Draw every route over the tile layer, fitted to cover them all."""
    from staticmap import Line, StaticMap

    smap = StaticMap(
        width, height, padding_x=40, padding_y=40,
        url_template=url_template, tile_size=tile_size,
    )
    casing = max(line_width + 2, 3)
    for points in routes:
        # staticmap wants (lon, lat). A white casing under each line keeps it
        # legible where routes overlap busy map detail or each other.
        coords = [(lon, lat) for lat, lon, _ in points]
        smap.add_line(Line(coords, "white", casing))
        smap.add_line(Line(coords, color, line_width))

    image = smap.render()  # auto-fits zoom/centre to all added geometry
    image = image.convert("RGB")
    _draw_attribution(image, attribution)
    image.save(out_path, "JPEG", quality=90)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Draw many GPX routes onto one coverage map."
    )
    ap.add_argument("gpx", nargs="+", help="paths to .gpx files")
    ap.add_argument(
        "-o", "--out", default="multimap.jpg", help="output JPEG path"
    )
    ap.add_argument(
        "--layer", default=DEFAULT_LAYER, choices=sorted(LAYERS),
        help=f"map tile layer (default: {DEFAULT_LAYER})",
    )
    ap.add_argument(
        "--api-key", default=None,
        help="override the selected layer's API key (else its env var or .env)",
    )
    ap.add_argument("--width", type=int, default=1024, help="map width in pixels")
    ap.add_argument("--height", type=int, default=1280, help="map height in pixels")
    ap.add_argument(
        "--line-width", type=int, default=3, help="route line width in pixels"
    )
    ap.add_argument(
        "--color", default="#cc0000", help="route line colour (hex or name)"
    )
    ap.add_argument(
        "--bbox", type=float, nargs=4, metavar=("S", "W", "N", "E"), default=None,
        help="keep only routes touching this lat/lon box (south west north east)",
    )
    args = ap.parse_args(argv)

    try:
        url_template, attribution, tile_size = resolve_layer(args.layer, args.api_key)

        routes = []  # flat list of polylines (segments) across all kept files
        kept, skipped = [], []
        for path in args.gpx:
            try:
                segments = read_segments(path)
            except (ET.ParseError, FileNotFoundError) as exc:
                print(f"Skipping {path}: {exc}", file=sys.stderr)
                skipped.append(path)
                continue
            # Keep a whole file if any of its segments touches the bbox, then
            # draw each segment as its own line, so legs split at a teleport stay apart.
            drawable = [seg for seg in segments if len(seg) >= 2]
            if not drawable or not any(in_bbox(seg, args.bbox) for seg in drawable):
                skipped.append(path)
                continue
            routes.extend(drawable)
            kept.append(path)

        if not routes:
            raise ValueError("No routes to draw (after bbox/empty filtering).")

        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        render_multimap(
            routes, args.out, args.width, args.height,
            url_template, attribution, tile_size, args.line_width, args.color,
        )
    except (ET.ParseError, ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except ImportError as exc:
        print(
            f"Error: missing dependency ({exc.name}). "
            "Install with: pip install staticmap",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Drew {len(kept)} file(s) as {len(routes)} segment(s) onto {args.out}")
    if args.bbox is not None and skipped:
        print(f"Skipped {len(skipped)} route(s) outside the bbox or empty.")


if __name__ == "__main__":
    sys.exit(main())
