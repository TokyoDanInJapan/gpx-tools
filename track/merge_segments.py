#!/usr/bin/env python3
"""Merge every <trkseg> in a GPX track into a single continuous segment.

GPS recorders emit a fresh <trkseg> each time recording is paused and resumed
(an overnight stop on a tour, a signal dropout). Where a ride is meant to
read as one continuous line, this collapses all of a track's segments into one
<trkseg> - the trackpoints are concatenated in order. Nothing is dropped.

It is the opposite of track/split.py, and the two exist for opposite reasons.
Split a track where the recorder teleported and the gap is real: a ferry, a
train, a drive between trailheads, and nothing should draw a line across it.
Merge one where the gap is an artefact of the recorder pausing, and the
segments would otherwise be read as legs that were never separate.

It works by deleting the segment boundaries only: wherever a </trkseg> is
followed by the next <trkseg>, both tags (and the whitespace between them) are
removed, leaving the surrounding trackpoints and their indentation untouched.
Every other part of the file - metadata, <trk> name/type, trackpoints - is left
exactly as it was, so it composes cleanly with track/metadata.py. Idempotent:
a track that is already a single segment is reported as unchanged.

A boundary is only removed inside one <trk> (a </trkseg> and <trkseg> with only
whitespace between them), so a file with several <trk> elements keeps them
separate - only the segments within each track are merged.

Usage:
    gpx-merge-segments <file.gpx> [<file.gpx> ...]
    gpx-merge-segments --all --root DIR      # every .gpx below DIR
"""

import argparse
import glob
import os
import re
import sys

# a </trkseg> immediately followed (only whitespace between) by the next
# <trkseg>: the join point between two segments of the same track.
BOUNDARY = re.compile(r"[ \t]*</trkseg>\s*<trkseg\b[^>]*>[ \t]*\n?")


def merge_file(gpx_path):
    with open(gpx_path, encoding="utf-8") as fh:
        text = fh.read()
    n_before = len(re.findall(r"<trkseg\b", text))
    new = BOUNDARY.sub("", text)
    if new == text:
        return "skip", n_before
    with open(gpx_path, "w", encoding="utf-8") as fh:
        fh.write(new)
    return "ok", n_before


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Idempotent: a track that is already one segment is left alone.")
    ap.add_argument("gpx", nargs="*", help="paths to .gpx files")
    ap.add_argument("--all", action="store_true", help="every .gpx below --root")
    ap.add_argument("--root", default=".", help="where --all looks (default: here)")
    args = ap.parse_args(argv)

    if not args.gpx and not args.all:
        ap.error("give some .gpx files, or --all")
    files = (sorted(glob.glob(os.path.join(args.root, "**", "*.gpx"), recursive=True))
             if args.all else args.gpx)
    n_ok = 0
    for f in files:
        status, segs = merge_file(f)
        if status == "ok":
            n_ok += 1
            print(f"  merged   {f}  ({segs} segments -> 1)")
        else:
            print(f"  skipped  {f}  (already {segs} segment)")
    print(f"\nMerged {n_ok} / {len(files)} files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
