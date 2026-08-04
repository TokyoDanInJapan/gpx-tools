"""The elevation profile: grade banding, simplification, and the SVG itself.

No rendering library and no network - the profile is plain-text SVG built by
hand, which is why maps.mapgen can offer --elevation-only and regenerate every
post's chart offline.

GRADE_BANDS is the shared fact in here. The profile is coloured by it, the
route overlay in maps.mapgen bands its polylines by it, and charts.statcharts
reports against it. All three have to agree or the map and the chart beside it
would describe the same climb differently.
"""

import math

from common.gpxtools import haversine

# Gradient colour bands for the elevation profile, by absolute grade (%).
# Each entry: (upper bound exclusive, fill colour, legend label).
GRADE_BANDS = [
    (3.0, "#2ca02c", "0–3%"),  # green
    (6.0, "#ffd11a", "3–6%"),  # yellow
    (9.0, "#ff8c00", "6–9%"),  # amber
    (12.0, "#e60000", "9–12%"),  # red
    (float("inf"), "#8b0000", ">12%"),  # dark red
]

# Horizontal window (m) over which grade is averaged. The track is resampled to
# roughly this spacing first, so colours reflect real pitches rather than the
# point-to-point noise in GPS elevation.
ELEV_SMOOTH_M = 100.0


def grade_colour(grade_pct):
    """Return the band colour for a grade, using its magnitude (steepness)."""
    g = abs(grade_pct)
    for upper, colour, _ in GRADE_BANDS:
        if g < upper:
            return colour
    return GRADE_BANDS[-1][1]


def _resample_indices(dist_m, window):
    """Indices thinning a cumulative-distance series to ~window-metre spacing.

    Returns indices rather than values so a caller can pull any array running
    parallel to `dist_m`. The profile wants the elevations. The map overlay
    wants those same samples' coordinates, and the two have to land on exactly
    the same points or their grade bands would disagree.
    """
    keep = [0]
    for i in range(1, len(dist_m)):
        if dist_m[i] - dist_m[keep[-1]] >= window:
            keep.append(i)
    last = len(dist_m) - 1
    if dist_m[keep[-1]] != dist_m[last]:  # always keep the final point
        keep.append(last)
    return keep


def _segment_profile(seg, x_offset):
    """Resample one segment to (km, elevation, band-colours, coords), offset on x.

    Distance accumulates only within the segment, so teleport gaps between
    segments add no phantom kilometres. `x_offset` shifts this segment's
    distances to continue after the previous one. Returns
    (km, rs_e, colours, rs_ll) or None if the segment has fewer than two
    elevation points. `rs_ll` is the (lat, lon) of each resampled point - the
    profile itself doesn't need it, but the map overlay bands the route with
    these same samples.
    """
    dist_m = []  # cumulative metres at each point that has an elevation
    elevations = []
    coords = []
    cumulative = 0.0
    prev = None
    for lat, lon, ele in seg:
        if prev is not None:
            cumulative += haversine(prev[0], prev[1], lat, lon)
        prev = (lat, lon)
        if ele is not None:
            dist_m.append(cumulative)
            elevations.append(ele)
            coords.append((lat, lon))

    if len(elevations) < 2:
        return None

    # Resample to a steady spacing, then derive a colour per step from its
    # grade. Adjacent same-colour steps are later merged into runs so each fill
    # is one gap-free polygon that shares a boundary point with its neighbours.
    idx = _resample_indices(dist_m, ELEV_SMOOTH_M)
    rs_d = [dist_m[i] for i in idx]
    rs_e = [elevations[i] for i in idx]
    rs_ll = [coords[i] for i in idx]
    km = [x_offset + d / 1000.0 for d in rs_d]
    colours = []
    for i in range(len(rs_d) - 1):
        run = rs_d[i + 1] - rs_d[i]
        grade = (rs_e[i + 1] - rs_e[i]) / run * 100 if run else 0.0
        colours.append(grade_colour(grade))
    return km, rs_e, colours, rs_ll


def build_profiles(segments):
    """Resample every segment, chaining each one's distances after the last.

    The single source of truth for "where along the ride is this, and how steep
    is it" - the elevation chart draws it and the map overlay colours by it, so
    they cannot drift apart.
    """
    profiles = []
    x_offset = 0.0
    for seg in segments:
        prof = _segment_profile(seg, x_offset)
        if prof is None:
            continue
        profiles.append(prof)
        x_offset = prof[0][-1]  # next segment continues from this km
    return profiles


# Gradient-band runs shorter than this fraction of the ride's total distance
# get folded into a neighbouring run, so the fill reads as broad climbs/
# descents rather than a stripe pattern of GPS-noise-driven colour flickers.
MIN_BAND_FRACTION = 0.005


def min_band_km(profiles):
    """Shortest gradient-band run either graphic will draw, in km.

    Shared by the elevation profile and the map overlay so the two band the
    ride identically.
    """
    return MIN_BAND_FRACTION * max(km[-1] for km, _e, _c, _ll in profiles)


# Maximum distance, in viewBox units, that simplification may move the drawn
# line from where the full-resolution line would have been. The chart's viewBox
# is 1024 units wide and it renders at most ~1200 CSS px, so a unit is a bit
# over a pixel. A quarter of one stays under half a device pixel even at 2x.
SIMPLIFY_TOLERANCE = 0.25


def simplify_indices(xs, ys, lo, hi, tolerance):
    """Douglas-Peucker over points[lo..hi], returning the indices worth keeping.

    A long ride resamples to several thousand points, which is far more than a
    1024-unit-wide chart can show - noto-loop drew 6,102 of them, about seven
    per rendered pixel. Dropping the ones that don't move the line is pure
    saving: the profile SVG is inlined into the page (see ElevationChart.astro),
    so every redundant coordinate is bytes in the HTML of every ride post.

    Endpoints are always kept, which is what lets the caller run this per
    gradient-band run without shifting where one colour ends and the next
    begins. Iterative rather than recursive - the spans are thousands of points
    long and Python's stack is not.
    """
    if hi - lo < 2:
        return list(range(lo, hi + 1))

    keep = {lo, hi}
    stack = [(lo, hi)]
    while stack:
        start, end = stack.pop()
        if end - start < 2:
            continue

        # Perpendicular distance from the chord, computed as the cross product
        # scaled by the chord length. A zero-length chord degenerates to plain
        # distance from the shared endpoint.
        x0, y0, x1, y1 = xs[start], ys[start], xs[end], ys[end]
        dx, dy = x1 - x0, y1 - y0
        chord = math.hypot(dx, dy)

        worst, worst_i = 0.0, -1
        for i in range(start + 1, end):
            if chord:
                d = abs(dy * (xs[i] - x0) - dx * (ys[i] - y0)) / chord
            else:
                d = math.hypot(xs[i] - x0, ys[i] - y0)
            if d > worst:
                worst, worst_i = d, i

        if worst > tolerance:
            keep.add(worst_i)
            stack.append((start, worst_i))
            stack.append((worst_i, end))

    return sorted(keep)


def cluster_bands(dist, colours, min_len):
    """Group contiguous-colour runs into windows at least min_len long.

    `dist` and `colours` are a segment's resampled (km, colour-per-step)
    arrays from _segment_profile. Returns [(colour, i, j), ...] - each
    covering colours[i..j] inclusive (so points dist[i..j+1]) - already
    coalesced so no two adjacent windows share a colour.

    Scans left to right accumulating raw runs into a pending window. Once
    the window reaches min_len, it's committed under whichever colour covers
    the most distance within it (not simply its first or last run). This is
    a single forward pass, not a repeated "merge the shortest run into its
    longer neighbour": that approach lets one dominant run (for example a long flat
    stretch) cannibalise an entire adjacent short feature one run at a time,
    since each merge makes it longer and thus the winner of the next
    comparison too - silently erasing real short climbs/descents next to a
    long flat one. Committing a window the moment it reaches min_len bounds
    how much any single colour can absorb.
    """
    if not colours:
        return []
    raw = []
    i = 0
    while i < len(colours):
        j = i
        while j + 1 < len(colours) and colours[j + 1] == colours[i]:
            j += 1
        raw.append([colours[i], i, j])
        i = j + 1

    def run_len(r):
        return dist[r[2] + 1] - dist[r[1]]

    def dominant_colour(window):
        totals = {}
        for colour, start, end in window:
            totals[colour] = totals.get(colour, 0.0) + run_len([colour, start, end])
        return max(totals, key=totals.get)

    windows = []
    pending = []
    pending_len = 0.0
    for run in raw:
        pending.append(run)
        pending_len += run_len(run)
        if pending_len >= min_len:
            windows.append([dominant_colour(pending), pending[0][1], pending[-1][2]])
            pending = []
            pending_len = 0.0

    if pending:
        # A short leftover at the very end: fold it into the previous window
        # rather than let it stand alone under-threshold.
        if windows:
            windows[-1][2] = pending[-1][2]
        else:
            windows.append([dominant_colour(pending), pending[0][1], pending[-1][2]])

    # Two adjacent windows can still land on the same colour. Coalesce those.
    coalesced = [windows[0]]
    for w in windows[1:]:
        if w[0] == coalesced[-1][0]:
            coalesced[-1][2] = w[2]
        else:
            coalesced.append(w)
    return coalesced


def _nice_step(span, target_ticks=4):
    """Round a raw axis step up to a 'nice' 1/2/5-times-a-power-of-ten value."""
    raw = span / target_ticks
    if raw <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(raw))
    return min((1, 2, 5, 10), key=lambda m: abs(m * mag - raw)) * mag


def _nice_step_up(min_step):
    """Smallest 'nice' 1/2/5-times-a-power-of-ten value that is >= min_step.

    Used where a minimum interval is a hard constraint, such as x-axis ticks that
    must not be spaced closer than a label's width), so rounding must go up,
    never to the nearest, or labels could still collide.
    """
    if min_step <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(min_step))
    for m in (1, 2, 5, 10):
        if m * mag >= min_step:
            return m * mag
    return 10 * mag


def render_elevation_svg(segments, out_path, width=1024, height=350):
    """Write a distance-vs-elevation profile as plain, hand-built SVG markup.

    Filled polygons (one per contiguous gradient-band run) plus a single
    outline path per segment - the same picture matplotlib's JPEG produced,
    but as lean vector markup: no per-point clip paths, no rasterisation.
    Colours are pinned (grade bands are meaningful data, not decoration);
    grid/axis/legend text use CSS custom properties so an inlined chart can
    follow the site's dark-mode toggle (see src/components/common/
    ElevationChart.astro and the .dark override in the global stylesheet).

    Each segment is profiled and drawn independently, continuing along the x
    axis from where the previous one ended (teleport gaps are omitted, not
    drawn). Segments are not joined, so no line crosses a teleport.

    width/height set the aspect ratio only (the SVG has no fixed pixel size -
    it scales to whatever the page renders it at via its viewBox).

    Returns the plot's coordinate system (padding, plot box, and the km/metre
    ranges the axes span). Those numbers live only as closures in here, but a
    synced hover needs to invert them in the browser - see the `route-map`
    package (https://github.com/TokyoDanInJapan/route-map) and
    src/utils/route-hover.ts - so they go into route.json alongside the map's
    georeference.
    """
    profiles = build_profiles(segments)

    if not profiles:
        raise ValueError("No elevation data in GPX file; cannot draw a profile.")

    all_km = [k for km, _, _, _ in profiles for k in km]
    all_ele = [e for _, ele, _, _ in profiles for e in ele]
    km_min, km_max = 0.0, max(all_km)
    ele_min, ele_max = min(all_ele), max(all_ele)
    ele_pad = (ele_max - ele_min) * 0.08 or 10.0
    y_lo, y_hi = ele_min - ele_pad * 0.15, ele_max + ele_pad

    # Layout in viewBox units, scaled from the 1024x350 design baseline so a
    # caller-supplied width/height still looks right at other aspect ratios.
    scale = height / 350.0
    pad_l, pad_r, pad_t, pad_b = 42 * scale, 12 * scale, 28 * scale, 28 * scale
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    def x_of(km):
        return pad_l + (km - km_min) / (km_max - km_min) * plot_w

    def y_of(ele):
        return pad_t + (1 - (ele - y_lo) / (y_hi - y_lo)) * plot_h

    def fnum(n):
        return f"{n:.1f}".rstrip("0").rstrip(".")

    p = []
    p.append(
        f'<svg viewBox="0 0 {fnum(width)} {fnum(height)}" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="Elevation profile" class="elevation-chart" '
        f'preserveAspectRatio="xMidYMid meet">'
    )
    p.append(
        "<style>"
        ".elevation-chart{font-family:ui-sans-serif,system-ui,sans-serif}"
        ".elevation-chart .grid-line{stroke:var(--ec-grid,#ddd);stroke-width:1}"
        ".elevation-chart .axis-text{fill:var(--ec-text,#555)}"
        ".elevation-chart .trace{fill:none;stroke:var(--ec-outline,#333);"
        "stroke-width:1.1;stroke-linejoin:round}"
        ".elevation-chart .legend-text{fill:var(--ec-text,#333)}"
        ".elevation-chart .legend-bg{fill:var(--ec-legend-bg,#fff);opacity:.85}"
        "</style>"
    )

    axis_fs = 10 * scale
    legend_fs = 9 * scale

    # y grid + elevation labels, at a rounded step
    step = _nice_step(y_hi - y_lo)
    tick = math.ceil(y_lo / step) * step
    while tick <= y_hi:
        y = y_of(tick)
        p.append(f'<line class="grid-line" x1="{fnum(pad_l)}" y1="{fnum(y)}" '
                 f'x2="{fnum(width - pad_r)}" y2="{fnum(y)}"/>')
        p.append(f'<text class="axis-text" x="{fnum(pad_l - 6 * scale)}" y="{fnum(y + 3 * scale)}" '
                 f'text-anchor="end" font-size="{fnum(axis_fs)}">{int(round(tick))}</text>')
        tick += step

    # x ticks (distance, km): pick a 'nice' 1/2/5 interval wide enough that no
    # two labels can touch, whatever the ride length - a label is at most
    # len(str(km_max)) digits, so reserve that width plus a gap and fit as many
    # ticks as the plot is wide. Sits just below the plot area, leaving room
    # under it for the "Distance (km)" title.
    tick_y = height - pad_b + 12 * scale
    label_gap = (len(f"{km_max:g}") * 6 + 14) * scale  # widest label + breathing room
    x_step = _nice_step_up(label_gap * (km_max - km_min) / plot_w)
    d = 0
    while d <= km_max + 0.01:
        x = x_of(d)
        p.append(f'<text class="axis-text" x="{fnum(x)}" y="{fnum(tick_y)}" '
                  f'text-anchor="middle" font-size="{fnum(axis_fs)}">{d:g}</text>')
        d += x_step

    # filled polygons per clustered gradient-band run, then one crisp outline
    # path per segment on top (so the join between bands is seamless)
    base_y = y_of(y_lo)
    min_run_km = min_band_km(profiles)
    for km, ele, colours, _ll in profiles:
        # Project once, then thin. Each band run is simplified between its own
        # endpoints, so no colour boundary moves, and the union of the kept
        # indices draws the outline - fills and outline therefore sit on
        # exactly the same points and can't part company along the top edge.
        xs = [x_of(v) for v in km]
        ys = [y_of(v) for v in ele]

        bands = cluster_bands(km, colours, min_run_km)
        kept = set()
        for colour, i, j in bands:
            run_kept = simplify_indices(xs, ys, i, j + 1, SIMPLIFY_TOLERANCE)
            kept.update(run_kept)
            poly = " ".join(f"{fnum(xs[k])},{fnum(ys[k])}" for k in run_kept)
            p.append(f'<polygon points="{fnum(xs[i])},{fnum(base_y)} {poly} '
                      f'{fnum(xs[j + 1])},{fnum(base_y)}" fill="{colour}" stroke="none"/>')

        # A profile with no bands (too short to cluster) still needs an outline.
        trace_idx = (sorted(kept) if kept else
                     simplify_indices(xs, ys, 0, len(xs) - 1, SIMPLIFY_TOLERANCE))
        path_d = "M " + " L ".join(f"{fnum(xs[k])} {fnum(ys[k])}" for k in trace_idx)
        p.append(f'<path class="trace" d="{path_d}"/>')

    # axis titles
    p.append(f'<text class="axis-text" x="{fnum(pad_l + plot_w / 2)}" '
             f'y="{fnum(height - 2 * scale)}" '
              f'text-anchor="middle" font-size="{fnum(axis_fs)}">Distance (km)</text>')
    p.append(f'<text class="axis-text" '
             f'transform="rotate(-90 {fnum(10 * scale)} {fnum(pad_t + plot_h / 2)})" '
              f'x="{fnum(10 * scale)}" y="{fnum(pad_t + plot_h / 2)}" text-anchor="middle" '
              f'font-size="{fnum(axis_fs)}">Elevation (m)</text>')

    # legend, top-left over the chart, on a translucent backing so it stays
    # legible over any fill colour it happens to sit above
    legend_w = sum(12 * scale + 7 * scale * len(lbl) + 10 * scale for _, _, lbl in GRADE_BANDS)
    p.append(f'<rect class="legend-bg" x="{fnum(pad_l)}" y="{fnum(pad_t)}" '
              f'width="{fnum(legend_w)}" height="{fnum(16 * scale)}"/>')
    lx = pad_l + 4 * scale
    ly = pad_t + 4 * scale
    for _, colour, label in GRADE_BANDS:
        p.append(f'<rect x="{fnum(lx)}" y="{fnum(ly)}" width="{fnum(9 * scale)}" '
                  f'height="{fnum(9 * scale)}" fill="{colour}"/>')
        p.append(f'<text class="legend-text" x="{fnum(lx + 12 * scale)}" '
                 f'y="{fnum(ly + 8 * scale)}" '
                  f'font-size="{fnum(legend_fs)}">{label}</text>')
        lx += 12 * scale + 7 * scale * len(label) + 10 * scale

    p.append("</svg>")

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("".join(p))

    return {
        "w": width,
        "h": height,
        "padL": round(pad_l, 3),
        "plotW": round(plot_w, 3),
        "padT": round(pad_t, 3),
        "plotH": round(plot_h, 3),
        "kmMin": round(km_min, 4),
        "kmMax": round(km_max, 4),
        "yLo": round(y_lo, 3),
        "yHi": round(y_hi, 3),
    }
