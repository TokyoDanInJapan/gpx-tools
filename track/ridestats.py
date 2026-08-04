#!/usr/bin/env python3
"""
Compute the headline stats for a single ride or hike, for the little stat card
that sits under its elevation profile in the post.

Emits three figures per track:
  - distance_km   total distance ridden/walked
  - moving_time_s riding/walking time, stops and GPS spikes excluded
  - ascent_m      total ascent (climbing)

The maths come from common.rides - the same distance gate, moving speed window
and ascent threshold that feed the aggregate totals - so a single ride's numbers
always add up to the site-wide ones. Tracks with no
<time> stamps get moving_time_s = null (the card then hides the Time tile).

Usage:
  # print JSON for one track
  gpx-ridestats <file.gpx>

  # write <slug>.stats.json next to each given track
  gpx-ridestats --write <file.gpx> [<file.gpx> ...]
"""

import argparse
import json
import os
import sys
from pathlib import Path

# The repository root, so the packages resolve when this is run straight from a
# checkout as `python3 track/ridestats.py`. An install puts them on the path already,
# and this is then a no-op.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.gpxtools import haversine

# The shared movement gates, so a single ride's figures are computed by exactly
# the same rules as the aggregate totals it rolls up into.
from common.rides import (
    ASCENT_THRESHOLD_M,
    MAX_SPEED_MS,
    MIN_DIST_M,
    MIN_SPEED_MS,
    read_timed_segments,
)


def compute(path):
    """Return {distance_km, moving_time_s, ascent_m} for one GPX file."""
    segments = read_timed_segments(path)

    total_dist = 0.0
    total_time = 0.0
    total_ascent = 0.0
    saw_time = False

    for seg in segments:
        n = len(seg)
        if n < 2:
            continue
        lat = [p[0] for p in seg]
        lon = [p[1] for p in seg]
        ele = [p[2] for p in seg]
        tt = [p[3] for p in seg]

        cum = [0.0] * n
        for k in range(1, n):
            cum[k] = cum[k - 1] + haversine(lat[k - 1], lon[k - 1], lat[k], lon[k])

        # Ascent: threshold-filtered rises within this segment (raw elevation).
        eles = [e for e in ele if e is not None]
        ref = eles[0] if eles else None
        for e in eles[1:]:
            delta = e - ref
            if delta >= ASCENT_THRESHOLD_M:
                total_ascent += delta
                ref = e
            elif delta <= -ASCENT_THRESHOLD_M:
                ref = e

        # Distance and moving time, pair by pair.
        for k in range(n - 1):
            d = cum[k + 1] - cum[k]
            if d < MIN_DIST_M:
                continue
            total_dist += d
            if tt[k] is not None and tt[k + 1] is not None:
                dt = tt[k + 1] - tt[k]
                if dt > 0:
                    saw_time = True
                    if MIN_SPEED_MS <= d / dt <= MAX_SPEED_MS:
                        total_time += dt

    return {
        "distance_km": round(total_dist / 1000, 1),
        "moving_time_s": round(total_time) if saw_time else None,
        "ascent_m": round(total_ascent),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Compute a single ride/hike's headline stats.")
    ap.add_argument("gpx", nargs="+", help="path(s) to .gpx file(s)")
    ap.add_argument(
        "--write",
        action="store_true",
        help="write <slug>.stats.json next to each GPX instead of printing",
    )
    args = ap.parse_args(argv)

    for path in args.gpx:
        stats = compute(path)
        if args.write:
            out = os.path.splitext(path)[0] + ".stats.json"
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(stats, fh, indent=2)
                fh.write("\n")
            print(f"Wrote {out}  {stats}")
        else:
            print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    sys.exit(main())
