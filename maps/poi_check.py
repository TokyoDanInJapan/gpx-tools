#!/usr/bin/env python3
"""
Check that a post's places of interest sit near the plotted GPX route.

Usage:
  gpx-poi-check <file.gpx> <pois.json>

For each POI in the pois.json cache (written by maps/mapgen.py --post), computes
the minimum great-circle distance from its coordinates to the track and prints
one line per place. House rule: curated POIs belong within ~5 km of the route;
up to ~6.5 km is tolerable (geocoding error), clearly beyond ~7 km should be
dropped from the post or have its coordinates corrected by hand.

Entries with null lat/lon are listed informationally only - a null keeps a
place off the map on purpose (narrative-prose links are often left unplotted).
Exit status is 1 if any POI is DROP-flagged, so it can gate a checklist step.
"""

import argparse
import json
import sys
from pathlib import Path

# The repository root, so the packages resolve when this is run straight from a
# checkout as `python3 maps/poi_check.py`. An install puts them on the path already,
# and this is then a no-op.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.gpxtools import haversine, read_points

OK_KM = 5.0      # within the corridor
SOFT_KM = 6.5    # tolerable - likely geocoding error, verify coords
# beyond SOFT_KM: drop the POI or fix its lat/lon in pois.json


def min_distance_km(lat, lon, points):
    """Minimum distance from a point to the track's points, in km."""
    return min(haversine(lat, lon, p[0], p[1]) for p in points) / 1000.0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Flag post POIs that sit too far from the GPX route."
    )
    ap.add_argument("gpx", help="path to the .gpx file")
    ap.add_argument("pois", help="path to the post's pois.json cache")
    args = ap.parse_args(argv)

    points = read_points(args.gpx)
    if not points:
        sys.exit(f"no track points in {args.gpx}")
    with open(args.pois, encoding="utf-8") as fh:
        cache = json.load(fh)

    failures = 0
    for name, entry in cache.items():
        lat, lon = entry.get("lat"), entry.get("lon")
        if lat is None or lon is None:
            print(f"unplotted  --  {name}  (null lat/lon - fill in pois.json "
                  "by hand if it should appear on the map)")
            continue
        km = min_distance_km(lat, lon, points)
        if km <= OK_KM:
            flag = "ok    "
        elif km <= SOFT_KM:
            flag = "CHECK "
            # borderline: verify the coordinates are the right place
        else:
            flag = "DROP  "
            failures += 1
        print(f"{flag} {km:6.1f} km  {name}")

    if failures:
        print(f"\n{failures} place(s) beyond the corridor - drop them from "
              "the post or correct their lat/lon in pois.json, then re-run")
        sys.exit(1)
    print("\nall plotted POIs within the route corridor")


if __name__ == "__main__":
    sys.exit(main())
