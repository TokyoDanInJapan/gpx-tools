#!/usr/bin/env python3
"""
Split GPX tracks into separate <trkseg> segments at "teleports".

A teleport is a jump between two consecutive track points larger than a
distance threshold - a train, ferry or car hop between riding spots that was
logged as one continuous track. Left as-is, anything that joins consecutive
points (a map renderer, Strava, RideWithGPS) draws a straight line across the
gap. Breaking the <trkseg> there makes each leg a distinct segment, so the
teleport line disappears while everything stays in one .gpx file.

Usage:
  # report what would change (no files written) - the default
  gpx-split <file.gpx> [<file.gpx> ...] [--threshold-km KM]

  # apply the splits in place
  gpx-split <file.gpx> [...] --write [--threshold-km KM]

The default threshold is 5 km: large enough to ignore ordinary GPS dropouts
and sparsely-logged tracks (whose normal point spacing can exceed 1–2 km),
small enough to catch real between-location teleports. Lower it with
--threshold-km to split more aggressively.

The rewrite is surgical: it inserts only the </trkseg>/<trkseg> break lines at
teleport boundaries and leaves every other byte - indentation, namespace
prefixes, extensions, comments - exactly as it was, so diffs stay minimal.
Existing segment breaks are preserved. Teleports are only detected *within* a
segment, never across an already-split boundary.
"""

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# The repository root, so the packages resolve when this is run straight from a
# checkout as `python3 track/split.py`. An install puts them on the path already,
# and this is then a no-op.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.gpxtools import haversine, local_name


def find_teleports(root, threshold_m):
    """Return the document-order trkpt indices after which to break a segment.

    Walks track points in document order, resetting at each <trkseg> so a gap
    is only ever measured between points in the *same* existing segment. When a
    consecutive pair is farther apart than the threshold, the earlier point's
    index is recorded: a new segment should start at the point after it.

    Returns (break_indices, total_trkpts, jump_distances) where jump_distances
    is the metre length of each detected teleport, for reporting.
    """
    breaks = []
    jumps = []
    gidx = -1
    prev = None  # (lat, lon) of the previous point in this segment
    prev_idx = None
    for el in root.iter():
        tag = local_name(el.tag)
        if tag == "trkseg":
            prev, prev_idx = None, None  # gaps never span a segment boundary
        elif tag == "trkpt":
            gidx += 1
            try:
                cur = (float(el.attrib["lat"]), float(el.attrib["lon"]))
            except (KeyError, ValueError):
                continue
            if prev is not None:
                d = haversine(prev[0], prev[1], cur[0], cur[1])
                if d > threshold_m:
                    breaks.append(prev_idx)
                    jumps.append(d)
            prev, prev_idx = cur, gidx
    return breaks, gidx + 1, jumps


# Matches one whole <trkpt> element: either self-closing or with a body. The
# non-greedy body stops at the first </trkpt>, so matches don't run together.
_TRKPT_RE = re.compile(r"<trkpt\b[^>]*?(?:/>|>.*?</trkpt>)", re.DOTALL)


def _seg_indent(text):
    """Return the leading whitespace of the first <trkseg> line in the file."""
    m = re.search(r"^([ \t]*)<trkseg\b", text, re.MULTILINE)
    return m.group(1) if m else "    "


def split_text(text, break_indices):
    """Insert </trkseg><trkseg> after each trkpt whose index is in the set.

    Relies on regex trkpt matches being in the same document order as the
    parsed indices, so the Nth match is the Nth trkpt. Inserts the break lines
    right after the closing of the chosen trkpt, at the file's trkseg indent.
    """
    indent = _seg_indent(text)
    insert = f"\n{indent}</trkseg>\n{indent}<trkseg>"
    wanted = set(break_indices)

    out = []
    last = 0
    for i, m in enumerate(_TRKPT_RE.finditer(text)):
        if i in wanted:
            out.append(text[last : m.end()])
            out.append(insert)
            last = m.end()
    out.append(text[last:])
    return "".join(out), i + 1  # i+1 = number of trkpt matches found


def process(path, threshold_m, write):
    """Analyse one GPX file, and rewrite it if asked. Returns a summary dict."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    root = ET.fromstring(text)

    breaks, n_parsed, jumps = find_teleports(root, threshold_m)
    result = {"path": path, "teleports": len(breaks), "jumps_km": [j / 1000 for j in jumps]}

    if not breaks:
        return result

    new_text, n_matched = split_text(text, breaks)
    if n_matched != n_parsed:
        # The regex and the parser disagree on trkpt count, so index alignment
        # can't be trusted - refuse to write rather than corrupt the file.
        result["error"] = (
            f"trkpt count mismatch (parsed {n_parsed}, matched {n_matched}); skipped"
        )
        return result

    if write:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        result["written"] = True
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Split GPX tracks into segments at teleport jumps."
    )
    ap.add_argument("gpx", nargs="+", help="paths to .gpx files")
    ap.add_argument(
        "--threshold-km", type=float, default=5.0,
        help="minimum jump distance to treat as a teleport (default: 5.0 km)",
    )
    ap.add_argument(
        "--write", action="store_true",
        help="apply the splits in place (default: report only)",
    )
    args = ap.parse_args(argv)

    threshold_m = args.threshold_km * 1000.0
    total_breaks = 0
    changed = 0
    for path in args.gpx:
        try:
            res = process(path, threshold_m, args.write)
        except (ET.ParseError, FileNotFoundError, OSError) as exc:
            print(f"Skipping {path}: {exc}", file=sys.stderr)
            continue

        if res.get("error"):
            print(f"  !  {path}: {res['error']}", file=sys.stderr)
            continue
        if res["teleports"] == 0:
            continue

        total_breaks += res["teleports"]
        changed += 1
        jumps = ", ".join(f"{k:.1f}km" for k in res["jumps_km"])
        verb = "split" if res.get("written") else "would split"
        print(f"{verb} {res['path']}: {res['teleports']} teleport(s) [{jumps}]")

    mode = "Wrote" if args.write else "Dry run -"
    print(
        f"\n{mode} {total_breaks} new break(s) across {changed} file(s)"
        + ("" if args.write else "; re-run with --write to apply.")
    )


if __name__ == "__main__":
    sys.exit(main())
