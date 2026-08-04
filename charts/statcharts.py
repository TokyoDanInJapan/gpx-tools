#!/usr/bin/env python3
"""
Aggregate many GPX tracks into bar charts of where riding time and distance go.

Given any number of GPX files, this writes four images, each pairing the riding
TIME and DISTANCE charts for one category:

  elevation.jpg    time & distance by elevation band
  grade.jpg        time & distance by gradient band
  prefecture.jpg   time & distance by prefecture (Japan)
  season.jpg       time & distance by season (each ride placed by its start date)

It also writes totals.json (rides, distance_km, time_hours, ascent_m, ascent_km,
high_point_m, prefectures, and a high_points top-5 list with per-ride title/url/
ele_m) so a page can show headline totals alongside the charts.

Usage:
  gpx-statcharts <file.gpx> [<file.gpx> ...] [-o OUTDIR] [--prefectures GEOJSON]
      [--url-root DIR] [--title-from EXT]

How the numbers are built
  Every consecutive pair of track points contributes its great-circle distance
  and (where timestamps exist) its elapsed time to one band. Elevation uses the
  pair's mid elevation. Gradient uses a ~100 m distance-smoothed slope so GPS
  noise doesn't spike the steep bands. "Riding" time excludes stops and GPS
  jumps: a pair counts toward time only when its speed is between ~1 and ~90
  km/h. Distance counts whenever there is real movement (>0.5 m).

  Prefecture is resolved by point-in-polygon against a GeoJSON of the 47
  Japanese prefectures, which ships with the package, sampled every ~200 m.
  Points outside Japan (overseas trips) resolve to nothing and are simply absent
  from the prefecture charts. They still count elsewhere.

  The high-points list in totals.json names each ride. --url-root and
  --title-from say where those names come from. Without them a ride is named by
  its filename and linked as /<filename>.

  Each chart shows percentages relative to its own total. Time charts are
  measured against total riding time and distance charts against total
  distance. The prefecture charts normalise over the in-Japan total, so their
  bars sum to about 100%.

Dependencies: matplotlib, unless --skip-images is given. The prefecture GeoJSON
is optional - without it, prefecture.jpg is left empty with a note.

  gpx-statcharts rides/**/*.gpx -o out/ --url-root rides --title-from .md
"""

import argparse
import bisect
import json
import math
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

# The repository root, so the packages resolve when this is run straight from a
# checkout as `python3 charts/statcharts.py`. An install puts them on the path
# already, and this is then a no-op.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.gpxtools import haversine
from common.prefectures import (
    DEFAULT_GEOJSON,
    PREF_CHECK_M,
    PrefectureLocator,
)
from common.rides import (
    ASCENT_THRESHOLD_M,
    MAX_SPEED_MS,
    MIN_DIST_M,
    MIN_SPEED_MS,
    read_timed_segments,
    smooth_elevation,
)

# Share the elevation-profile generator's gradient bands so both images use the
# same brackets and colours.
from terrain.elevation import GRADE_BANDS

# --- bands ------------------------------------------------------------------

# Elevation band upper edges (m), in 500 m steps. The last band is above 1500 m.
ELEV_EDGES = [500, 1000, 1500]
ELEV_LABELS = ["0–500", "500–1000", "1000–1500", "1500 m+"]

# Gradient bands taken straight from the elevation-profile generator: by
# absolute grade (magnitude), with matching upper edges, labels and colours.
GRADE_EDGES = [upper for upper, _, _ in GRADE_BANDS[:-1]]   # [3, 6, 9, 12]
GRADE_LABELS = [label for _, _, label in GRADE_BANDS]
GRADE_COLORS = [colour for _, colour, _ in GRADE_BANDS]

# Meteorological seasons (Northern Hemisphere - these rides are Japan-based).
SEASON_LABELS = ["Spring", "Summer", "Autumn", "Winter"]
_MONTH_SEASON = {12: 3, 1: 3, 2: 3, 3: 0, 4: 0, 5: 0,
                 6: 1, 7: 1, 8: 1, 9: 2, 10: 2, 11: 2}
JST_OFFSET_S = 9 * 3600  # GPX times are UTC (Z); shift to Japan local for dates


# --- aggregation ------------------------------------------------------------


class Accumulator:
    """Sums distance and riding time into elevation, gradient, prefecture and season bins."""

    def __init__(self, locator):
        self.locator = locator
        self.elev_dist = [0.0] * len(ELEV_LABELS)
        self.elev_time = [0.0] * len(ELEV_LABELS)
        self.grade_dist = [0.0] * len(GRADE_LABELS)
        self.grade_time = [0.0] * len(GRADE_LABELS)
        self.pref_dist = {}
        self.pref_time = {}
        self.pref_ascent = {}
        # Rides that touched each prefecture. Counted per file rather than per
        # segment: a ride crossing a border and coming back is one ride there,
        # not two. end_ride() folds the set in.
        self.pref_rides = {}
        self._ride_prefs = set()
        self.total_dist = 0.0
        self.total_time = 0.0
        self.total_ascent = 0.0
        self.high_point_m = None
        self.dist_with_time = 0.0
        # A whole ride's distance/time go to the season of its start date. Rides
        # with no timestamps land in the undated buckets.
        self.season_dist = [0.0] * len(SEASON_LABELS)
        self.season_time = [0.0] * len(SEASON_LABELS)
        self.undated_dist = 0.0
        self.undated_time = 0.0
        # Distance and ascent per calendar year (a ride's start year, JST);
        # rides with no timestamps land under the None key.
        self.year_dist = {}
        self.year_ascent = {}

    @staticmethod
    def season_of(first_time):
        """Return the season index for a ride's start timestamp, or None."""
        if first_time is None:
            return None
        # Read the JST wall-clock month: shift the instant, view it as UTC.
        month = datetime.fromtimestamp(first_time + JST_OFFSET_S, tz=timezone.utc).month
        return _MONTH_SEASON[month]

    @staticmethod
    def year_of(first_time):
        """Return the JST calendar year for a ride's start timestamp, or None."""
        if first_time is None:
            return None
        return datetime.fromtimestamp(first_time + JST_OFFSET_S, tz=timezone.utc).year

    def end_ride(self):
        """Close off one GPX file, crediting each prefecture it touched once."""
        for name in self._ride_prefs:
            self.pref_rides[name] = self.pref_rides.get(name, 0) + 1
        self._ride_prefs.clear()

    def add_segment(self, seg, season_idx, year=None):
        n = len(seg)
        lat = [p[0] for p in seg]
        lon = [p[1] for p in seg]
        ele = [p[2] for p in seg]
        tt = [p[3] for p in seg]

        cum = [0.0] * n
        for k in range(1, n):
            cum[k] = cum[k - 1] + haversine(lat[k - 1], lon[k - 1], lat[k], lon[k])
        se = smooth_elevation(cum, ele)

        have_ele = [k for k in range(n) if ele[k] is not None]
        if have_ele:
            seg_max = max(ele[k] for k in have_ele)
            self.high_point_m = (seg_max if self.high_point_m is None
                                 else max(self.high_point_m, seg_max))

        # Where the rider was at each point, filled in as the loop below
        # resolves it. The ascent pass reads this back so a climb is credited to
        # the prefecture it was climbed in - it cannot resolve its own, having
        # no distance axis of its own to pace the lookups by.
        pref_at = [None] * n

        current_pref = None
        dist_since_check = math.inf
        for k in range(n - 1):
            d = cum[k + 1] - cum[k]
            if d < MIN_DIST_M:
                continue
            self.total_dist += d

            # riding time (excludes stops and GPS spikes)
            move_t = 0.0
            if tt[k] is not None and tt[k + 1] is not None:
                dt = tt[k + 1] - tt[k]
                if dt > 0:
                    self.dist_with_time += d
                    if MIN_SPEED_MS <= d / dt <= MAX_SPEED_MS:
                        move_t = dt
                        self.total_time += dt

            # season (the whole ride's distance/time, by its start date)
            if season_idx is None:
                self.undated_dist += d
                self.undated_time += move_t
            else:
                self.season_dist[season_idx] += d
                self.season_time[season_idx] += move_t

            # year (same convention: the whole ride goes to its start year)
            self.year_dist[year] = self.year_dist.get(year, 0.0) + d

            # elevation + gradient (need elevation on both ends)
            if ele[k] is not None and ele[k + 1] is not None:
                eb = bisect.bisect_right(ELEV_EDGES, (ele[k] + ele[k + 1]) / 2)
                self.elev_dist[eb] += d
                self.elev_time[eb] += move_t
                grade = (se[k + 1] - se[k]) / d * 100
                gb = bisect.bisect_right(GRADE_EDGES, abs(grade))
                self.grade_dist[gb] += d
                self.grade_time[gb] += move_t

            # prefecture, resolved periodically and cached between checks
            if self.locator is not None:
                dist_since_check += d
                if current_pref is None or dist_since_check >= PREF_CHECK_M:
                    current_pref = self.locator.locate(lat[k], lon[k], current_pref)
                    dist_since_check = 0.0
                pref_at[k] = current_pref
                if current_pref is not None:
                    self.pref_dist[current_pref] = self.pref_dist.get(current_pref, 0.0) + d
                    self.pref_time[current_pref] = self.pref_time.get(current_pref, 0.0) + move_t
                    self._ride_prefs.add(current_pref)

        # Points the loop stepped over - stationary ones, and the last point,
        # which has no step after it - are wherever the last resolved point was.
        for k in range(1, n):
            if pref_at[k] is None:
                pref_at[k] = pref_at[k - 1]

        # Total ascent: threshold-filtered rises within this segment only.
        #
        # Runs over indices rather than the elevations alone, so each rise can be
        # placed. The sequence of elevations it sees, and so every total it
        # produces, is exactly what the value-only version produced.
        ref = ele[have_ele[0]] if have_ele else None
        for k in have_ele[1:]:
            delta = ele[k] - ref
            if delta >= ASCENT_THRESHOLD_M:
                self.total_ascent += delta
                self.year_ascent[year] = self.year_ascent.get(year, 0.0) + delta
                if pref_at[k] is not None:
                    self.pref_ascent[pref_at[k]] = self.pref_ascent.get(pref_at[k], 0.0) + delta
                ref = ele[k]
            elif delta <= -ASCENT_THRESHOLD_M:
                ref = ele[k]


# --- plotting ---------------------------------------------------------------

def _pct(values, total):
    return [100.0 * v / total for v in values] if total > 0 else [0.0] * len(values)


def _bar_panel(ax, labels, values, colors, title, ylabel):
    bars = ax.bar(range(len(labels)), values, color=colors, edgecolor="white", linewidth=0.5)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=0, fontsize=8)
    ax.set_title(title, fontsize=11, fontweight="bold")
    ax.set_ylabel(ylabel, fontsize=8)
    ax.grid(axis="y", color="#e6e6e6", linewidth=0.6)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    top = max(values) if values and max(values) > 0 else 1
    for b, v in zip(bars, values):
        if v > 0.5:
            ax.text(b.get_x() + b.get_width() / 2, v + top * 0.01, f"{v:.0f}",
                    ha="center", va="bottom", fontsize=7)
    ax.set_ylim(0, top * 1.15)
    if all(v == 0 for v in values):
        ax.text(0.5, 0.5, "no data", transform=ax.transAxes,
                ha="center", va="center", color="#999999", fontsize=11)


def _pref_panel(ax, names, values, title):
    ax.barh(range(len(names)), values, color="#3b78b0", edgecolor="white", linewidth=0.5)
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=7)
    ax.invert_yaxis()  # largest at top
    ax.set_title(title, fontsize=11, fontweight="bold")
    ax.set_xlabel("% of total", fontsize=8)
    ax.grid(axis="x", color="#e6e6e6", linewidth=0.6)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    if names:
        right = max(values)
        for i, v in enumerate(values):
            ax.text(v + right * 0.01, i, f"{v:.1f}", va="center", fontsize=6)
        ax.set_xlim(0, right * 1.12)
    else:
        ax.text(0.5, 0.5, "no prefecture data", transform=ax.transAxes,
                ha="center", va="center", color="#999999", fontsize=11)


# Each chart is (title, labels, percentages). Charts are paired (time, distance)
# per category and rendered two-to-an-image.
CHART_TITLES = [
    "Riding time by elevation",
    "Distance by elevation",
    "Riding time by gradient (%)",
    "Distance by gradient (%)",
    "Riding time by prefecture",
    "Distance by prefecture",
    "Riding time by season",
    "Distance by season",
]


def chart_data(acc):
    """Return the eight (title, labels, pct) charts from an accumulator."""
    # Prefectures sorted by distance (descending), same order in both panels.
    pref_names = sorted(acc.pref_dist, key=lambda n: acc.pref_dist[n], reverse=True)
    pref_dist_total = sum(acc.pref_dist.values())
    pref_time_total = sum(acc.pref_time.values())
    pref_time_pct = [100.0 * acc.pref_time.get(n, 0) / pref_time_total if pref_time_total else 0
                     for n in pref_names]
    pref_dist_pct = [100.0 * acc.pref_dist[n] / pref_dist_total if pref_dist_total else 0
                     for n in pref_names]

    # Season time & distance. An "Undated" bar appears only when there is data.
    season_labels = list(SEASON_LABELS)
    season_time_vals = list(acc.season_time)
    season_dist_vals = list(acc.season_dist)
    if acc.undated_dist > 0 or acc.undated_time > 0:
        season_labels.append("Undated")
        season_time_vals.append(acc.undated_time)
        season_dist_vals.append(acc.undated_dist)

    return [
        (CHART_TITLES[0], ELEV_LABELS, _pct(acc.elev_time, acc.total_time)),
        (CHART_TITLES[1], ELEV_LABELS, _pct(acc.elev_dist, acc.total_dist)),
        (CHART_TITLES[2], GRADE_LABELS, _pct(acc.grade_time, acc.total_time)),
        (CHART_TITLES[3], GRADE_LABELS, _pct(acc.grade_dist, acc.total_dist)),
        (CHART_TITLES[4], pref_names, pref_time_pct),
        (CHART_TITLES[5], pref_names, pref_dist_pct),
        (CHART_TITLES[6], season_labels, _pct(season_time_vals, acc.total_time)),
        (CHART_TITLES[7], season_labels, _pct(season_dist_vals, acc.total_dist)),
    ]


def prefecture_areas_km2(locator):
    """Approximate each prefecture's land area (km²) from its GeoJSON rings.

    Planar shoelace on an equirectangular projection scaled at each ring's mid
    latitude - within ~1% at prefecture scale, plenty for a relative chart.
    Exterior rings only (holes are rare and immaterial).
    """
    areas = {}
    for name, polys in locator.prefs:
        total = 0.0
        for _bbox, ring in polys:
            lats = [p[1] for p in ring]
            mid = math.radians(sum(lats) / len(lats))
            kx = 111.320 * math.cos(mid)  # km per degree of longitude
            ky = 110.574                  # km per degree of latitude
            s = 0.0
            for i in range(len(ring) - 1):
                x1, y1 = ring[i][0] * kx, ring[i][1] * ky
                x2, y2 = ring[i + 1][0] * kx, ring[i + 1][1] * ky
                s += x1 * y2 - x2 * y1
            total += abs(s) / 2
        areas[name] = total
    return areas


def year_series(per_year):
    """Expand a {year_or_None: value} dict to (labels, values), zero-filling
    missing years and appending an "Undated" bucket when present."""
    years = sorted(y for y in per_year if y is not None)
    if not years:
        return [], []
    labels = [str(y) for y in range(years[0], years[-1] + 1)]
    values = [per_year.get(y, 0.0) for y in range(years[0], years[-1] + 1)]
    if per_year.get(None):
        labels.append("Undated")
        values.append(per_year[None])
    return labels, values


SEASON_PALETTE = {"Spring": "#4caf50", "Summer": "#f9a825",
                  "Autumn": "#e65100", "Winter": "#1976d2", "Undated": "#9e9e9e"}


def render_images(acc, outdir):
    """Write four images (elevation, grade, prefecture, season) into outdir.

    Each image pairs the time and distance charts for one category. Returns the
    list of paths written.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    elev_colors = [plt.cm.YlOrBr(0.25 + 0.6 * i / (len(ELEV_LABELS) - 1))
                   for i in range(len(ELEV_LABELS))]
    grade_colors = GRADE_COLORS
    c = chart_data(acc)
    written = []

    def _save(fig, name):
        path = os.path.join(outdir, name)
        fig.tight_layout()
        fig.savefig(path, format="jpeg", dpi=110)
        plt.close(fig)
        written.append(path)

    # elevation
    fig, ax = plt.subplots(1, 2, figsize=(15, 6))
    _bar_panel(ax[0], c[0][1], c[0][2], elev_colors, c[0][0], "% of riding time")
    _bar_panel(ax[1], c[1][1], c[1][2], elev_colors, c[1][0], "% of distance")
    _save(fig, "elevation.jpg")

    # gradient
    fig, ax = plt.subplots(1, 2, figsize=(15, 6))
    _bar_panel(ax[0], c[2][1], c[2][2], grade_colors, c[2][0], "% of riding time")
    _bar_panel(ax[1], c[3][1], c[3][2], grade_colors, c[3][0], "% of distance")
    _save(fig, "grade.jpg")

    # prefecture, as horizontal bars in a taller frame to fit the labels
    fig, ax = plt.subplots(1, 2, figsize=(15, 9))
    _pref_panel(ax[0], c[4][1], c[4][2], c[4][0])
    _pref_panel(ax[1], c[5][1], c[5][2], c[5][0])
    _save(fig, "prefecture.jpg")

    # season
    season_colors = [SEASON_PALETTE.get(name, "#777777") for name in c[6][1]]
    fig, ax = plt.subplots(1, 2, figsize=(15, 6))
    _bar_panel(ax[0], c[6][1], c[6][2], season_colors, c[6][0], "% of riding time")
    _bar_panel(ax[1], c[7][1], c[7][2], season_colors, c[7][0], "% of distance")
    _save(fig, "season.jpg")

    return written


def track_url(gpx_path, root=None):
    """Map a GPX path to the URL of whatever publishes it.

    With a `root`, a track below it is named by its path within it, extension
    dropped - so `<root>/cycling/touring/2019/bandai.gpx` becomes
    `/cycling/touring/2019/bandai`. Without one, the file's own name is all
    there is to go on.

    This is the whole of what the tool assumes about a website: that a track's
    path below some directory is its address. Anything more - which site, which
    directory - is the caller's to say.
    """
    p = gpx_path.replace(os.sep, "/")
    if root:
        marker = root.replace(os.sep, "/").rstrip("/") + "/"
        i = p.find(marker)
        rel = p[i + len(marker):] if i >= 0 else os.path.basename(p)
    else:
        rel = os.path.basename(p)
    return "/" + os.path.splitext(rel)[0]


def sidecar_title(gpx_path, ext):
    """Return the `title:` from the track's sibling `ext` file, or None.

    Reads a YAML frontmatter block - the leading `---` fence - which is what
    Markdown, MDX and most static site generators put a title in. Nothing here
    needs a YAML parser: one key is wanted and a regex reads it without adding a
    dependency to a package that otherwise has none.
    """
    if not ext:
        return None
    path = os.path.splitext(gpx_path)[0] + ext
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    m = re.search(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    fm = m.group(1) if m else ""
    tm = re.search(r"^title:\s*(.+?)\s*$", fm, re.MULTILINE)
    return tm.group(1).strip().strip("\"'") if tm else None


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Aggregate many GPX tracks into ride-statistics bar charts."
    )
    ap.add_argument("gpx", nargs="+", help="paths to .gpx files")
    ap.add_argument(
        "-o", "--outdir", default=".",
        help="directory for the output images (elevation/grade/prefecture/season.jpg)",
    )
    ap.add_argument(
        "--totals", default=None,
        help="path for totals.json (default: <outdir>/totals.json)",
    )
    ap.add_argument(
        "--charts-json", default=None,
        help="also write the per-category chart data (labels + time/distance "
             "percentages) as JSON to this path, for SVG rendering on the page",
    )
    ap.add_argument(
        "--skip-images", action="store_true",
        help="don't render the .jpg charts (use with --charts-json when the "
             "page draws its own SVG charts; also drops the matplotlib dependency)",
    )
    ap.add_argument(
        "--prefectures", default=DEFAULT_GEOJSON,
        help="GeoJSON of prefecture polygons (prefecture.jpg); skipped if missing",
    )
    # The two things the highest-points list in totals.json needs to name a
    # ride. Both are about whatever publishes these tracks, so both are stated
    # by the caller rather than assumed here.
    ap.add_argument(
        "--url-root", default=None,
        help="tracks below this directory are linked by their path within it "
             "such as rides/. Without it, a track is linked by its filename",
    )
    ap.add_argument(
        "--title-from", default=None,
        help="extension of a sibling file whose YAML frontmatter carries the "
             "ride's title, such as .mdx or .md. Without it, the filename is used",
    )
    args = ap.parse_args(argv)

    locator = None
    if os.path.exists(args.prefectures):
        try:
            locator = PrefectureLocator(args.prefectures)
        except (OSError, ValueError, KeyError) as exc:
            print(f"Warning: prefecture data unusable ({exc}); prefecture.jpg will be empty.",
                  file=sys.stderr)
    else:
        print(f"Warning: {args.prefectures} not found; prefecture.jpg will be empty.",
              file=sys.stderr)

    acc = Accumulator(locator)
    used = 0
    file_highs = []  # (path, max_elevation_m) per ride, for the highest-points list
    for path in args.gpx:
        try:
            segments = read_timed_segments(path)
        except (ET.ParseError, FileNotFoundError) as exc:
            print(f"Skipping {path}: {exc}", file=sys.stderr)
            continue
        first_time = next(
            (p[3] for seg in segments for p in seg if p[3] is not None), None
        )
        season_idx = Accumulator.season_of(first_time)
        year = Accumulator.year_of(first_time)
        for seg in segments:
            acc.add_segment(seg, season_idx, year)
        acc.end_ride()
        fmax = max((p[2] for seg in segments for p in seg if p[2] is not None), default=None)
        if fmax is not None:
            file_highs.append((path, fmax))
        used += 1

    if acc.total_dist <= 0:
        print("Error: no usable track data found.", file=sys.stderr)
        sys.exit(1)

    time_cov = 100.0 * acc.dist_with_time / acc.total_dist

    os.makedirs(os.path.abspath(args.outdir), exist_ok=True)
    written = []
    if not args.skip_images:
        try:
            written = render_images(acc, args.outdir)
        except ImportError as exc:
            print(f"Error: missing dependency ({exc.name}); install matplotlib.",
                  file=sys.stderr)
            sys.exit(1)

    if args.charts_json:
        # Reshape chart_data()'s eight (title, labels, pct) tuples into one
        # object per category, pairing the time and distance percentages.
        charts = chart_data(acc)
        by_category = {}
        for key, (time_idx, dist_idx) in {
            "elevation": (0, 1), "gradient": (2, 3),
            "prefecture": (4, 5), "season": (6, 7),
        }.items():
            _, labels, time_pct = charts[time_idx]
            _, _, dist_pct = charts[dist_idx]
            by_category[key] = {
                "labels": labels,
                "time_pct": [round(v, 1) for v in time_pct],
                "dist_pct": [round(v, 1) for v in dist_pct],
            }

        # Absolute per-prefecture figures alongside the percentages the bar
        # charts use. A share of the total answers "where do I ride most". The
        # map is pointed at one prefecture at a time, where the question is
        # "what did I actually do here", and a percentage cannot say.
        pref_labels = by_category["prefecture"]["labels"]
        by_category["prefecture"].update({
            "rides": [acc.pref_rides.get(n, 0) for n in pref_labels],
            "dist_km": [round(acc.pref_dist.get(n, 0.0) / 1000.0, 1) for n in pref_labels],
            "time_hours": [round(acc.pref_time.get(n, 0.0) / 3600.0, 1) for n in pref_labels],
            "ascent_m": [round(acc.pref_ascent.get(n, 0.0)) for n in pref_labels],
        })

        # Distance relative to prefecture size: km ridden per 1,000 km² of
        # prefecture, visited prefectures only, densest first.
        if locator is not None and acc.pref_dist:
            areas = prefecture_areas_km2(locator)
            density = sorted(
                ((n, (d / 1000.0) / areas[n] * 1000.0)
                 for n, d in acc.pref_dist.items() if areas.get(n)),
                key=lambda nd: nd[1], reverse=True,
            )
            by_category["pref_density"] = {
                "labels": [n for n, _ in density],
                "km_per_1000km2": [round(v, 1) for _, v in density],
            }

        # Distance and climbing per calendar year (zero-filled timeline).
        dist_labels, dist_vals = year_series(acc.year_dist)
        by_category["year_dist"] = {
            "labels": dist_labels,
            "km": [round(v / 1000.0) for v in dist_vals],
        }
        asc_labels, asc_vals = year_series(acc.year_ascent)
        by_category["year_ascent"] = {
            "labels": asc_labels,
            "ascent_m": [round(v) for v in asc_vals],
        }

        os.makedirs(os.path.dirname(os.path.abspath(args.charts_json)), exist_ok=True)
        with open(args.charts_json, "w", encoding="utf-8") as fh:
            json.dump(by_category, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        written.append(args.charts_json)

    # Top five rides by their high point, each linked back to the post.
    file_highs.sort(key=lambda fh: fh[1], reverse=True)
    high_points = [
        {
            "title": sidecar_title(p, args.title_from)
                     or os.path.splitext(os.path.basename(p))[0],
            "ele_m": round(e),
            "url": track_url(p, args.url_root),
        }
        for p, e in file_highs[:5]
    ]

    # Headline totals, written as JSON so the stats page can render them.
    totals = {
        "rides": used,
        "distance_km": round(acc.total_dist / 1000),
        "time_hours": round(acc.total_time / 3600),
        "ascent_m": round(acc.total_ascent),
        "ascent_km": round(acc.total_ascent / 1000, 1),
        "high_point_m": round(acc.high_point_m) if acc.high_point_m is not None else None,
        "prefectures": len(acc.pref_dist),
        "high_points": high_points,
    }
    totals_path = args.totals or os.path.join(args.outdir, "totals.json")
    os.makedirs(os.path.dirname(os.path.abspath(totals_path)), exist_ok=True)
    with open(totals_path, "w", encoding="utf-8") as fh:
        json.dump(totals, fh, indent=2)
        fh.write("\n")
    written.append(totals_path)

    for path in written:
        print(f"Wrote {path}")
    print(f"  {used} files, {acc.total_dist / 1000:.1f} km, "
          f"{acc.total_time / 3600:.1f} h riding, {acc.total_ascent:.0f} m ascent, "
          f"{time_cov:.0f}% timestamped")
    if locator is not None:
        print(f"  {len(acc.pref_dist)} prefectures visited")


if __name__ == "__main__":
    sys.exit(main())
