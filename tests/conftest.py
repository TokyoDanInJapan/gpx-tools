"""Shared helpers for building the small synthetic tracks the tests measure.

The tools read GPX, so the tests need GPX. Real tracks would make the suite
slow, the repository large, and every assertion a magic number nobody can check
by hand, so each track here is generated with geometry chosen to be verifiable
on paper:

  * A degree of latitude is very nearly 111.19 km on the mean-radius sphere the
    haversine uses, so a track that only moves north has a distance anyone can
    check with a calculator.
  * Elevations are given as an explicit list rather than a formula, so a test
    about the ascent threshold can point at the exact rise it is about.
  * Times are whole seconds from a fixed epoch. Nothing here reads the clock:
    a test that passes only on the day it was written is worse than no test.

`gpx()` builds the file from segments so a teleport, a pause and a ferry are
all expressible - which is what most of the interesting behaviour turns on.
"""

import datetime as dt

import pytest

#: Metres per degree of latitude, on the sphere common.gpxtools.haversine uses.
#: 2 * pi * 6371000 / 360.
DEG_LAT_M = 111194.9

#: A fixed start time, in UTC. Any date would do. This one is a Sunday.
START = dt.datetime(2024, 3, 3, 0, 0, 0, tzinfo=dt.timezone.utc)


def point(lat, lon, ele=None, when=None):
    """One trkpt element as text, with only the children it was given."""
    parts = [f'<trkpt lat="{lat}" lon="{lon}">']
    if ele is not None:
        parts.append(f"<ele>{ele}</ele>")
    if when is not None:
        parts.append(f"<time>{when.strftime('%Y-%m-%dT%H:%M:%SZ')}</time>")
    parts.append("</trkpt>")
    return "".join(parts)


def gpx(segments, namespaced=True):
    """A GPX 1.1 document: one <trk> holding a <trkseg> per given segment.

    Each segment is a list of (lat, lon, ele_or_None, time_or_None) tuples.
    With namespaced=False the schema declaration is dropped, which is the shape
    of a hand-edited file and the case `local_name` exists for.
    """
    ns = ' xmlns="http://www.topografix.com/GPX/1/1"' if namespaced else ""
    body = []
    for seg in segments:
        body.append("<trkseg>")
        body.extend(point(*p) for p in seg)
        body.append("</trkseg>")
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1"{ns}>'
            f"<trk><name>Test</name>{''.join(body)}</trk></gpx>")


def northward(n, start_lat=35.0, lon=139.0, step_deg=0.001, eles=None, interval_s=None):
    """A straight line due north: n points, `step_deg` apart.

    Distance is therefore (n - 1) * step_deg * DEG_LAT_M metres, which a test
    can assert against without trusting the code that produced it.
    """
    points = []
    for i in range(n):
        ele = eles[i] if eles is not None else None
        when = START + dt.timedelta(seconds=i * interval_s) if interval_s else None
        points.append((round(start_lat + i * step_deg, 6), lon, ele, when))
    return points


@pytest.fixture
def track(tmp_path):
    """Write a GPX file into the test's own directory, and return its path.

    Takes the same segment lists as `gpx()`. Returned as a callable rather than
    as a file, because most tests want two or three tracks that differ in one
    respect - which is the comparison the assertion is usually about.
    """
    made = []

    def write(segments, name=None, **kwargs):
        path = tmp_path / (name or f"track{len(made)}.gpx")
        path.write_text(gpx(segments, **kwargs), encoding="utf-8")
        made.append(path)
        return path

    return write
