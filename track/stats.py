#!/usr/bin/env python3
"""
Compute ride statistics from a GPX track.

Usage:
  gpx-stats <file.gpx> [--ascent-threshold METRES]

Outputs the real figures a ride report needs, computed from the track points:
  - total distance (km), via the haversine great-circle formula
  - total ascent / descent (m), with a small smoothing threshold to filter
    GPS elevation noise
  - start / finish elevation (m)
  - high point / low point (m), each with its lat,lon so you can name it
  - bounding box of the route (handy for web-searching the area)

Reads GPX 1.0 or 1.1. Namespace-agnostic so it copes with most exporters
(Ride with GPS, Strava, Garmin and the rest).
"""

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# The repository root, so the packages resolve when this is run straight from a
# checkout as `python3 track/stats.py`. An install puts them on the path already,
# and this is then a no-op.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.gpxtools import haversine, read_points


def compute(points, ascent_threshold=3.0):
    """Compute ride stats. ascent_threshold (m) smooths GPS elevation noise."""
    if not points:
        raise ValueError("No track points found in GPX file.")

    total_distance = 0.0
    for (la1, lo1, _), (la2, lo2, _) in zip(points, points[1:]):
        total_distance += haversine(la1, lo1, la2, lo2)

    eles = [(la, lo, e) for (la, lo, e) in points if e is not None]

    ascent = descent = 0.0
    if eles:
        ref = eles[0][2]
        for _, _, e in eles[1:]:
            delta = e - ref
            if delta >= ascent_threshold:
                ascent += delta
                ref = e
            elif delta <= -ascent_threshold:
                descent += -delta
                ref = e

    high = max(eles, key=lambda t: t[2]) if eles else None
    low = min(eles, key=lambda t: t[2]) if eles else None

    lats = [la for la, _, _ in points]
    lons = [lo for _, lo, _ in points]

    return {
        "num_points": len(points),
        "total_distance_km": round(total_distance / 1000, 2),
        "total_ascent_m": round(ascent),
        "total_descent_m": round(descent),
        "start_ele_m": round(eles[0][2]) if eles else None,
        "finish_ele_m": round(eles[-1][2]) if eles else None,
        "high_point_m": round(high[2]) if high else None,
        "high_point_latlon": f"{high[0]:.5f},{high[1]:.5f}" if high else None,
        "low_point_m": round(low[2]) if low else None,
        "low_point_latlon": f"{low[0]:.5f},{low[1]:.5f}" if low else None,
        "start_latlon": f"{points[0][0]:.5f},{points[0][1]:.5f}",
        "finish_latlon": f"{points[-1][0]:.5f},{points[-1][1]:.5f}",
        "bbox": {
            "min_lat": round(min(lats), 5),
            "max_lat": round(max(lats), 5),
            "min_lon": round(min(lons), 5),
            "max_lon": round(max(lons), 5),
        },
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Compute ride stats from a GPX track.")
    ap.add_argument("gpx", help="path to the .gpx file")
    ap.add_argument(
        "--ascent-threshold",
        type=float,
        default=3.0,
        help="metres of change before counting ascent/descent (noise filter)",
    )
    args = ap.parse_args(argv)

    try:
        points = read_points(args.gpx)
        stats = compute(points, args.ascent_threshold)
    except (ET.ParseError, ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    import json

    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    sys.exit(main())
