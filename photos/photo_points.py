#!/usr/bin/env python3
"""Place a gallery's photographs along the GPX track they were taken on.

Writes `photos.json`, giving each photograph a distance along the ride. That
distance is deliberately the only position recorded: a renderer that draws the
route on a map and one that flies it over a terrain model both derive their own
coordinates from the same number, so the two can never disagree about where a
photograph was taken.

Two signals are available, and the interesting part is that the weaker one
calibrates the stronger one.

**The clock is the placement.** A photo carries `DateTimeOriginal`, a track
carries `<time>`, and matching the two puts a photo exactly where the rider was
when it was taken - to within the sampling interval, which is a second or two.

**The GPS is the calibration.** `DateTimeOriginal` has no timezone in it: it is
whatever the camera's clock said, which is local time, while the track is UTC.
Rather than carry a table of which ride happened in which timezone - and get
daylight saving wrong - the offset is *derived*: photos whose own GPS puts them
on the route say what the clock read at a known moment, and `calibrate()` takes
the value those agree on. It comes out at +13 h for a February hike in New
Zealand, +9 h for a ride in Japan and +6.5 h in Myanmar, which is the check
that it works.

That ordering matters, because photo GPS is much worse than it looks. Phones
happily record a cell-tower fix: on the Pinnacles hike the median photo sits
720 m from the route it was taken on, and one lands 7.5 km away. Anchors are
therefore taken only from photos already within ANCHOR_M, where a coarse fix
cannot masquerade as a good one, and everything else is placed by the clock.

**The timezone checks the calibration.** `zone_offset()` turns the track's own
coordinates and date into an exact UTC offset, and the two estimators cover
each other: only the anchors can see the camera's own drift, and only the zone
cannot be fooled about the timezone. The anchors win unless they disagree with
the zone by more than an hour, at which point they are not measuring drift,
they are wrong - which is what happens on a lap course, where the same stretch
of road is nearest to photos taken hours apart.

Where there are no anchors at all - the 2015 tours were shot on a camera that
recorded no GPS - the zone alone places them, and `clockFrom` says so.

A photo with no usable time falls back to its own GPS, if that is near enough
to mean anything. One with neither is not placed, and is printed rather than
dropped silently. That matters here because a photo can fail to place for the
perfectly good reason that it was taken on a day the track does not cover, and
a silent drop looks identical to a bug.

**EXIF is read from the originals.** Published copies are usually stripped of
their metadata - which is the right thing to do with coordinates of where
someone lives - so the timestamps this needs exist only in the full-size tree.
--originals is where that tree is. The gallery manifest beside the track says
which files to look for, and in what order.

Needs Pillow, and timezonefinder for the zone lookup:

    gpx-photo-points <file.gpx> --originals ROOT
"""

import argparse
import datetime as dt
import json
import math
import os
import statistics
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# The repository root, so the packages resolve when this is run straight from a
# checkout as `python3 photos/photo_points.py`. An install puts them on the path already,
# and this is then a no-op.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.gpxtools import haversine, local_name, parse_point

try:
    from PIL import ExifTags, Image
except ImportError:  # pragma: no cover - the message is the whole handling
    sys.exit("Pillow is required: .venv/bin/pip install pillow")

# A photo has to be this close to the route before its own GPS is trusted
# enough to say what the clock read there. Loose enough to admit an ordinary
# handheld fix, tight enough that a cell-tower fix cannot get in.
ANCHOR_M = 60

# Without a usable clock, a photo is placed by GPS alone - but only if it is
# near enough that "nearest point on the route" means anything. Beyond this it
# is a photo of somewhere else (the car park, the station, home) and is dropped.
GPS_ONLY_MAX_M = 1500

# A corrected timestamp may sit a little outside the track's own span - the
# camera clock drifts, and people photograph the bike before starting it - but
# an hour out is a photo from another day.
TIME_SLACK_S = 3600

# Anchors within this of each other are the same answer. Real timezones are
# whole or half hours, and a camera clock drifts by minutes, so anything inside
# half an hour is one cluster. Anything outside it is a different claim.
CLUSTER_H = 0.5


def calibrate(anchors):
    """The clock offset the anchors agree on, in seconds, or None.

    A median is the obvious estimator and it is wrong here, because a loop
    course breaks the assumption underneath it. An anchor says "the photo whose
    GPS is nearest *this* point was taken at *that* time", and on a lap the
    rider is nearest the same point several times, hours apart. The Sado race
    produced anchors from -11.8 h to +19.2 h with a median of +2.9 h - a value
    no anchor actually claimed, and six hours from the +9.0 h that two of them
    agreed on precisely.

    So take the mode rather than the middle: the largest group of anchors that
    agree to within CLUSTER_H, and the median *of that group*. Wrong anchors
    scatter, right ones pile up, and the pile wins however many strays there
    are.

    The offset it returns absorbs the camera's own drift as well as the
    timezone, which is why it is not rounded to a whole hour afterwards. A
    camera running nineteen minutes slow should be corrected by nineteen
    minutes, not have them snapped away.
    """
    if not anchors:
        return None, 0
    hours = sorted(a / 3600 for a in anchors)
    best = []
    for i, start in enumerate(hours):
        group = [h for h in hours[i:] if h - start <= CLUSTER_H]
        if len(group) > len(best):
            best = group
    return statistics.median(best) * 3600, len(best)


def zone_offset(points, when):
    """The true UTC offset where and when the track was recorded, in seconds.

    Exact rather than estimated: `timezonefinder` turns the coordinates into an
    IANA zone and `zoneinfo` applies that zone's rules on the day in question,
    so daylight saving and the half-hour zones come out right. Checked against
    every post here that has agreeing anchors - 51 of 51 to within a minute,
    including +13 h for a New Zealand summer, -4 h for New York in June and
    +6:30 for Myanmar.

    It replaced guessing the zone from longitude, which agreed on the 41
    Japanese posts and was wrong on all ten of the rest, in exactly the places
    where being wrong is invisible.
    """
    try:
        from zoneinfo import ZoneInfo

        from timezonefinder import TimezoneFinder
    except ImportError:
        return None
    lat = statistics.median(p[0] for p in points)
    lon = statistics.median(p[1] for p in points)
    zone = TimezoneFinder().timezone_at(lat=lat, lng=lon)
    if not zone:
        return None
    return when.astimezone(ZoneInfo(zone)).utcoffset().total_seconds()


def read_timed_points(path):
    """Ordered (lat, lon, time_or_None, seg) for every trkpt, with segments.

    Not shared with `common.gpxtools.read_points`, which flattens and drops times.
    Segments are kept because a photo taken during a ferry crossing belongs to
    the gap, and distance must not accumulate across one.
    """
    root = ET.parse(path).getroot()
    points = []
    seg = 0
    for el in root.iter():
        name = local_name(el.tag)
        if name == "trkseg":
            seg += 1
            continue
        if name not in ("trkpt", "rtept"):
            continue
        pt = parse_point(el)
        if pt is None:
            continue
        when = None
        for child in el:
            if local_name(child.tag) == "time" and child.text:
                try:
                    when = dt.datetime.fromisoformat(child.text.strip().replace("Z", "+00:00"))
                except ValueError:
                    when = None
                break
        points.append((pt[0], pt[1], when, max(0, seg - 1)))
    return points


def cumulative_km(points):
    """Distance along the track at each point, in km, not crossing segments."""
    out = [0.0]
    for i in range(1, len(points)):
        a, b = points[i - 1], points[i]
        step = 0.0 if b[3] != a[3] else haversine(a[0], a[1], b[0], b[1]) / 1000.0
        out.append(out[-1] + step)
    return out


def exif_of(path):
    """(lat, lon) or None, and a naive UTC-stamped DateTimeOriginal or None."""
    try:
        exif = Image.open(path).getexif()
    except Exception:
        return None, None

    coords = None
    try:
        gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
        if gps and 2 in gps and 4 in gps:

            def dms(value, ref):
                deg = float(value[0]) + float(value[1]) / 60 + float(value[2]) / 3600
                return -deg if str(ref).upper().startswith(("S", "W")) else deg

            coords = (dms(gps[2], gps.get(1, "N")), dms(gps[4], gps.get(3, "E")))
    except Exception:
        coords = None

    when = None
    try:
        raw = exif.get_ifd(ExifTags.IFD.Exif).get(36867) or exif.get(306)
        if raw:
            # Stamped UTC so the arithmetic below has a tzinfo to work with;
            # it is really local time, which is exactly what the offset fixes.
            when = dt.datetime.strptime(str(raw).strip(), "%Y:%m:%d %H:%M:%S")
            when = when.replace(tzinfo=dt.timezone.utc)
    except Exception:
        when = None

    return coords, when


def mercator_uv(lat, lon):
    """Normalised Web Mercator, matching what `maps/mapgen.py` bakes into map.jpg."""
    u = (lon + 180.0) / 360.0
    s = math.sin(math.radians(lat))
    s = max(-0.9999, min(0.9999, s))
    v = 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)
    return u, v


def pixel_of(lat, lon, window):
    """Map-image pixel for a coordinate, or None when it falls outside."""
    u, v = mercator_uv(lat, lon)
    du = window["u1"] - window["u0"]
    dv = window["v1"] - window["v0"]
    if not du or not dv:
        return None
    x = (u - window["u0"]) / du * window["w"]
    y = (v - window["v0"]) / dv * window["h"]
    return round(x, 1), round(y, 1)


def gallery_files(manifest):
    """The image filenames a gallery manifest lists, in its own order."""
    with open(manifest, encoding="utf-8") as fh:
        data = json.load(fh)
    return [os.path.basename(img["src"]) for img in data.get("images", [])]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gpx", help="the .gpx track the photographs were taken on")
    ap.add_argument("--originals", required=True,
                    help="root of the full-size photo tree, mirroring --track-root")
    # A track's photographs and its outputs live in trees that mirror the tracks
    # themselves, so all three are addressed by the same relative path. Where
    # those trees are rooted is the caller's business, not this tool's.
    ap.add_argument("--track-root", default=None,
                    help="root the track's path is taken relative to when finding "
                         "its photographs and naming its output (default: the "
                         "directory the track is in)")
    ap.add_argument("--outroot", default=None,
                    help="root of the output tree; photos.json goes in the same "
                         "relative place below it (default: beside the track)")
    ap.add_argument("--outdir", help="write photos.json here, ignoring --outroot")
    ap.add_argument("--dry-run", action="store_true", help="report placements, write nothing")
    args = ap.parse_args(argv)

    base = os.path.splitext(args.gpx)[0]
    manifest = base + ".json"
    if not os.path.exists(manifest):
        sys.exit(f"no gallery manifest beside the track: {manifest}")

    rel = os.path.relpath(base, args.track_root or os.path.dirname(base) or ".")
    outdir = args.outdir or (os.path.join(args.outroot, rel) if args.outroot
                             else os.path.dirname(base) or ".")
    photo_dir = os.path.join(args.originals, rel)
    if not os.path.isdir(photo_dir):
        sys.exit(f"no originals for this post: {photo_dir}")

    points = read_timed_points(args.gpx)
    if not points:
        sys.exit("no track points")
    kms = cumulative_km(points)
    timed = [p for p in points if p[2] is not None]

    window = None
    route_json = os.path.join(outdir, "route.json")
    if os.path.exists(route_json):
        with open(route_json, encoding="utf-8") as fh:
            window = json.load(fh).get("map")

    files = gallery_files(manifest)
    read = [(f, *exif_of(os.path.join(photo_dir, f))) for f in files]

    # Calibrate the camera clock against the track, using only photos whose own
    # GPS already puts them on it.
    offsets = []
    if timed:
        for _, coords, when in read:
            if not coords or not when:
                continue
            best = min(
                ((haversine(coords[0], coords[1], p[0], p[1]), p) for p in timed),
                key=lambda pair: pair[0],
            )
            if best[0] <= ANCHOR_M:
                offsets.append((when - best[1][2]).total_seconds())

    # Two independent estimators, and each covers the other's weakness. The
    # anchors know the camera's own drift - a clock nineteen minutes slow is a
    # real nineteen minutes, and only the anchors can see it - but they are
    # derived from photo GPS, which a loop course can fool. The zone lookup
    # cannot see drift at all, but it cannot be fooled about the timezone.
    #
    # So: trust the anchors, unless they disagree with the zone by more than an
    # hour, at which point they are not measuring drift, they are wrong. That
    # is the Sado case - anchors scattered across a lap said +2.9 h where the
    # zone said +9 h - and it is caught here even when the clustering above
    # cannot separate it.
    anchor_offset, agreed = calibrate(offsets)
    zone = zone_offset(points, timed[0][2]) if timed else None

    offset, source = None, None
    if anchor_offset is not None and (zone is None or abs(anchor_offset - zone) <= 3600):
        offset, source = anchor_offset, "anchors"
    elif zone is not None:
        offset, source = zone, "zone"
        if anchor_offset is not None:
            print(f"  anchors said {anchor_offset / 3600:+.2f} h, more than an hour "
                  f"from the zone's {zone / 3600:+.2f} h - using the zone")
    elif anchor_offset is not None:
        offset, source = anchor_offset, "anchors"

    span = (timed[0][2], timed[-1][2]) if timed else None
    placed, skipped = [], []

    for index, (name, coords, when) in enumerate(read):
        hit = None
        how = None

        if offset is not None and when is not None:
            corrected = when - dt.timedelta(seconds=offset)
            slack = dt.timedelta(seconds=TIME_SLACK_S)
            if span[0] - slack <= corrected <= span[1] + slack:
                hit = min(range(len(points)),
                          key=lambda i: (abs((points[i][2] - corrected).total_seconds())
                                         if points[i][2] else math.inf))
                how = "clock"

        if hit is None and coords:
            near = min(range(len(points)),
                       key=lambda i: haversine(coords[0], coords[1],
                                               points[i][0], points[i][1]))
            if haversine(coords[0], coords[1], points[near][0], points[near][1]) <= GPS_ONLY_MAX_M:
                hit = near
                how = "gps"

        if hit is None:
            why = ("no GPS and no usable time" if not coords and not when
                   else "too far from the route")
            skipped.append((name, why))
            continue

        lat, lon = points[hit][0], points[hit][1]
        entry = {
            "file": name,
            "i": index,
            "km": round(kms[hit], 3),
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "by": how,
        }
        if window:
            px = pixel_of(lat, lon, window)
            if px:
                entry["x"], entry["y"] = px
        placed.append(entry)

    # Order along the ride, which is the order both figures want to walk them in.
    placed.sort(key=lambda e: e["km"])

    out = {
        "photos": placed,
        "totalKm": round(kms[-1], 3),
        "clockOffsetS": round(offset) if offset is not None else None,
        "clockFrom": source,
        "anchors": len(offsets),
        "anchorsAgreed": agreed,
    }

    print(f"{rel}: {len(placed)}/{len(files)} placed", end="")
    if source == "anchors":
        print(f", clock {offset / 3600:+.2f} h from {agreed}/{len(offsets)} "
              "agreeing anchor(s)", end="")
    elif source == "zone":
        print(f", clock {offset / 3600:+.2f} h from the timezone", end="")
    else:
        print(", track has no times - placed by GPS alone", end="")
    print()
    by_clock = sum(1 for e in placed if e["by"] == "clock")
    print(f"  by clock: {by_clock}   by gps: {len(placed) - by_clock}")
    for name, why in skipped:
        print(f"  SKIP {name}: {why}")

    if args.dry_run:
        return

    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "photos.json"), "w", encoding="utf-8") as fh:
        # Pretty-printed to match route.json rather than minified: a post has
        # tens of these, not thousands of track points, so the file stays small
        # and a placement that looks wrong can be read straight out of it.
        json.dump(out, fh, indent=2)
        fh.write("\n")
    print(f"  wrote {os.path.join(outdir, 'photos.json')}")


if __name__ == "__main__":
    sys.exit(main())
