"""Reading a track *with its clock*, and the gates that decide what counts.

`common.gpxtools` reads where a track went. This module reads when as well, and
holds the thresholds that turn a list of points into a ride: what counts as
movement, what counts as riding rather than standing still, and what counts as
climbing rather than GPS noise.

The two readers are deliberately separate rather than one function with a flag.
A tool that measures a distance wants a flat list of places and should not have
to think about time. A tool that measures riding time cannot work without it. The
duplication is a few lines and it keeps each caller's shape honest.

Everything here was extracted from the aggregate chart tool, which is why the
numbers are stated as cycling values. Keeping them in one place is the point: a
single ride's figures and the site-wide totals it rolls up into are computed by
the same gates, so they always add up.
"""

import xml.etree.ElementTree as ET
from datetime import datetime

from common.gpxtools import local_name

# Movement gates. A pair of points must move at least MIN_DIST_M to count as
# distance at all. It counts as riding time only when its speed sits inside a
# plausible cycling range.
MIN_DIST_M = 0.5
MIN_SPEED_MS = 0.28   # ~1 km/h: below this the rider is stopped
MAX_SPEED_MS = 25.0   # ~90 km/h: above this is a GPS spike, not riding

#: ± window, in metres along the track, for the elevation smoothing that
#: gradients are read off. Raw consumer-GPS elevation is far too noisy to
#: differentiate directly.
SMOOTH_HALF_M = 50.0

#: Minimum rise before it counts as climbing. Without it, a flat ride
#: accumulates hundreds of metres of ascent out of receiver jitter alone.
ASCENT_THRESHOLD_M = 3.0


def parse_time(text):
    """Parse a GPX <time> ISO-8601 string to epoch seconds, or None."""
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.strip().replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def read_timed_segments(path):
    """Return track segments as lists of (lat, lon, ele_or_None, time_or_None).

    Honours <trkseg>/<rte> boundaries so teleport-split legs stay separate and
    no gap is ever measured across a boundary. Segments of fewer than two points
    are dropped: nothing here can be measured from a single point.
    """
    root = ET.parse(path).getroot()
    segments = []
    current = None
    for el in root.iter():
        tag = local_name(el.tag)
        if tag in ("trkseg", "rte"):
            current = []
            segments.append(current)
        elif tag in ("trkpt", "rtept"):
            try:
                lat = float(el.attrib["lat"])
                lon = float(el.attrib["lon"])
            except (KeyError, ValueError):
                continue
            ele = t = None
            for child in el:
                ctag = local_name(child.tag)
                if ctag == "ele" and child.text:
                    try:
                        ele = float(child.text)
                    except ValueError:
                        ele = None
                elif ctag == "time":
                    t = parse_time(child.text)
            if current is None:  # points outside any segment (loose GPX)
                current = []
                segments.append(current)
            current.append((lat, lon, ele, t))
    return [s for s in segments if len(s) >= 2]


def smooth_elevation(cum, ele):
    """Distance-windowed moving average of elevation, for stable gradients.

    Returns a smoothed copy where each point is the mean elevation of the points
    within ±SMOOTH_HALF_M along the track. Falls back to the raw values if any
    elevation is missing, since a gap cannot be averaged across.

    The window is in metres travelled, not in points. Sampling rate varies with
    speed - a climb is sampled far more densely per metre than a descent - so a
    fixed number of points either side would smooth the two ends of the same
    ride by different amounts.
    """
    if any(e is None for e in ele):
        return ele
    n = len(ele)
    prefix = [0.0] * (n + 1)
    for i in range(n):
        prefix[i + 1] = prefix[i] + ele[i]
    out = [0.0] * n
    lo = hi = 0
    for i in range(n):
        while cum[i] - cum[lo] > SMOOTH_HALF_M:
            lo += 1
        while hi < n - 1 and cum[hi + 1] - cum[i] <= SMOOTH_HALF_M:
            hi += 1
        out[i] = (prefix[hi + 1] - prefix[lo]) / (hi + 1 - lo)
    return out
