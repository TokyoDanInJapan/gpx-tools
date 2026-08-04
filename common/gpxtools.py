"""Reading GPX and measuring distance on it - the two things every tool here does.

This is the bottom layer, and it is deliberately narrow: parse a point, read a
track, measure between two places. Nothing here fetches anything, draws
anything, or knows what the track is for.

The rule that keeps it narrow is that a tool must not reach sideways into
another tool for a shared basic. Without it, each script grows its own copy of
`haversine` and then imports the rest from whichever sibling happens to have it
- which is how this toolbox looked before the module existed. Byte-identical
copies of one function were the visible cost. The import graph was the real one.

Two readers sit above this one and are worth knowing about before adding a
third here. `common.rides.read_timed_segments` returns points with their clock
attached, for anything measuring riding time. `photos.photo_points` keeps its
own again, because it wants the times and nothing else. Both are separate on
purpose: a caller that flattens a track to measure it is saying something
different from one that walks it in order, and one function with three flags
would hide that rather than share it.
"""

import math
import xml.etree.ElementTree as ET

#: Mean Earth radius, in metres.
EARTH_RADIUS_M = 6371000.0


def local_name(tag):
    """Strip any XML namespace, returning the bare tag name.

    GPX files in the wild declare the schema on the root, so every tag arrives
    as `{http://www.topografix.com/GPX/1/1}trkpt`. Matching on the local name
    means a file that omits the namespace, or uses 1.0 instead of 1.1, reads
    the same.
    """
    return tag.split("}")[-1]


def haversine(lat1, lon1, lat2, lon2):
    """Great-circle distance between two lat/lon points, in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def parse_point(el):
    """Return (lat, lon, ele_or_None) for a trkpt/rtept element, or None.

    A point without usable coordinates is dropped rather than raising: tracks
    exported by consumer devices do sometimes carry a malformed point, and one
    bad row should not cost the whole ride.
    """
    try:
        lat = float(el.attrib["lat"])
        lon = float(el.attrib["lon"])
    except (KeyError, ValueError):
        return None
    ele = None
    for child in el:
        if local_name(child.tag) == "ele" and child.text:
            try:
                ele = float(child.text)
            except ValueError:
                ele = None
            break
    return (lat, lon, ele)


def read_points(path):
    """Return an ordered list of (lat, lon, ele_or_None) from trkpt/rtept.

    Flat: segment and track boundaries are not preserved. Use this when the
    track is one continuous thing to measure. When a gap between segments
    matters, read the segments instead.
    """
    root = ET.parse(path).getroot()
    points = []
    for el in root.iter():
        if local_name(el.tag) in ("trkpt", "rtept"):
            pt = parse_point(el)
            if pt is not None:
                points.append(pt)
    return points


def points_in_bbox(points, bbox):
    """True if any of the points fall inside (south, west, north, east).

    A route is kept when it has at least one point in the box, so a track that
    merely clips the region still counts. With bbox None, every route is kept.

    Here rather than in the tool that first wanted it, because two now do:
    both use it to keep a track that happened elsewhere off a map of somewhere
    else.
    """
    if bbox is None:
        return True
    south, west, north, east = bbox
    return any(south <= lat <= north and west <= lon <= east for lat, lon, _ in points)


def read_segments(path):
    """Return track points grouped by segment: a list of point lists.

    Each <trkseg> (and each <rte>) becomes its own list, so a track that has
    been split at its teleports reads as distinct runs instead of one line that
    jumps across the gaps. Empty segments are dropped. A file with no segment
    structure still yields a single group of all its points.

    This is the boundary that stops a renderer drawing a ferry crossing as if
    it were a road. Callers that flatten it with `read_points` are saying they
    do not care where the track stops and restarts - which is right for
    measuring a distance and wrong for drawing one.
    """
    root = ET.parse(path).getroot()
    segments = []
    current = None  # the open segment, or None before the first one is seen
    for el in root.iter():
        tag = local_name(el.tag)
        if tag in ("trkseg", "rte"):
            current = []
            segments.append(current)
        elif tag in ("trkpt", "rtept"):
            pt = parse_point(el)
            if pt is None:
                continue
            if current is None:  # points outside any segment (rare/loose GPX)
                current = []
                segments.append(current)
            current.append(pt)
    return [seg for seg in segments if seg]
