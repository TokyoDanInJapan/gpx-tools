#!/usr/bin/env python3
"""
Generate an OpenStreetMap route map and an elevation profile from a GPX track.

Usage:
  gpx-mapgen <file.gpx> [--outdir DIR] [--layer LAYER]
      [--map-width PX] [--map-height PX] [--elev-width PX] [--elev-height PX]
      [--post POST.mdx] [--refresh-pois]

Writes two files into the output directory (the GPX file's directory by
default):
  - map.jpg        the route drawn over a tiled map layer
  - elevation.svg  distance-vs-elevation profile, as plain vector markup
                    (filled polygons + one outline path - no matplotlib), so
                    it's crisp at any zoom and small enough to inline.

With --overlay the route is left off map.jpg and a third file is written:
  - route.json     the route as polylines in the image's own pixel space,
                    banded by the same gradient colours as the profile, plus
                    the georeference and the chart's coordinate system.

The page then draws the route as SVG over the tiles - the `route-map` package
does that - so it
stays crisp at any pixel density, and because every point carries how far along
the ride it is, hovering either graphic can mark the same spot on both.

With --post, the places of interest linked in that post's body (the markdown
[Name](https://…) links - sights, onsens, campsites, a recommended cafe) are
added to the map with a name legend. Each is classified from its wording and
the section it sits under: campsites get a tent glyph, onsens a hot-spring (♨)
glyph, and general sights a numbered pin, each in its own colour. Every place
is geocoded against the route's bounding box (so a name that exists elsewhere
snaps to the corridor) and cached in a hand-editable pois.json in the output
directory (lat/lon and category can be corrected there). Geocoding can't place
everything. For any miss, fill in lat/lon by hand and re-run (use
--refresh-pois to re-geocode from scratch).

Reads GPX 1.0 or 1.1. Namespace-agnostic so it copes with most exporters
(Ride with GPS, Strava, Garmin and the rest).

The map layer is selectable with --layer (default: opencyclemap). Several
layers need a free provider API key, read from an environment variable or a
gitignored .env at the repo root (or overridden with --api-key):

  opencyclemap, transport, landscape, outdoors  Thunderforest, key
      THUNDERFOREST_API_KEY (https://www.thunderforest.com/)
  maptiler                                       English labels, key
      MAPTILER_API_KEY (https://www.maptiler.com/)
  geoapify                                       English labels, key
      GEOAPIFY_API_KEY (https://www.geoapify.com/)
  osm, opentopomap                               open, no key

Most layers render place names in the local script, such as Japanese in Japan.
The maptiler and geoapify layers request English labels (language=en), shown
wherever OpenStreetMap has a name:en tag. Features without one stay local.

Each map is stamped with the provider's required attribution text. Note that
the maptiler and geoapify free tiers ask for more than a static JPEG can carry:
their credits should be clickable links, and MapTiler's free tier also wants
its logo shown. When publishing these images, the surrounding page must supply
the linked attribution (and the MapTiler logo) to be fully compliant.

Dependencies (not in the Python stdlib):
  staticmap   fetches OSM tiles and draws the route polyline

The elevation profile is plain-text SVG built by hand (see render_elevation_svg
in terrain.elevation) - no rendering library needed for it.

This file is the map: fetching tiles, drawing the route and its places over
them, and emitting the vector overlay that describes what it drew. The parts
that are not about drawing a map live beside it, because other tools need them
without needing this one:

  common/gpxtools.py     reading points out of a GPX, and distance along them
  maps/layers.py     the tile layer table and its API keys  (maps.multimap)
  maps/pois.py       finding and geocoding the places a post links
  terrain/elevation.py  the profile: grade bands, simplification, SVG
                                                            (charts.statcharts)

staticmap is not installable system-wide on PEP 668 ("externally managed")
machines, so install it into a virtualenv:

  python3 -m venv .venv
  .venv/bin/pip install staticmap
  gpx-mapgen <file.gpx>

Use --elevation-only to regenerate just elevation.svg from the GPX track,
skipping the map (and its network/API-key requirements) entirely - handy for
bulk-regenerating every post's chart after a change to the renderer.
"""

import argparse
import json
import math
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# The repository root, so the packages resolve when this is run straight from a
# checkout as `python3 maps/mapgen.py`. An install puts them on the path already,
# and this is then a no-op.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.gpxtools import haversine, read_segments
from maps.layers import DEFAULT_LAYER, LAYERS, resolve_layer
from maps.pois import resolve_pois
from terrain.elevation import (
    GRADE_BANDS,
    build_profiles,
    cluster_bands,
    min_band_km,
    render_elevation_svg,
    simplify_indices,
)


def _fit_map(smap, segments):
    """Let staticmap pick the zoom and centre that frame the track, and pin them.

    The lines are added purely so the auto-fit has something to fit - the
    caller decides whether any of them are actually drawn. A tiles-only render
    has no features left to fit itself around, so the chosen centre is returned
    to be handed back to `render()` explicitly.
    """
    from staticmap import Line

    for seg in segments:
        smap.add_line(Line([(lon, lat) for lat, lon, _ in seg], "white", 7))
    smap.zoom = smap._calculate_zoom()
    extent = smap.determine_extent(zoom=smap.zoom)
    smap.lines = []
    return ((extent[0] + extent[2]) / 2, (extent[1] + extent[3]) / 2)


def _number_pois(pois):
    """Assign each general sight its legend number, in order.

    Onsens and campsites are identified by their glyph rather than a number,
    so they are skipped in the count. Shared by the baked-in renderer and the
    overlay emitter so a place keeps the same number either way.
    """
    number = 0
    for poi in pois:
        if poi.get("category", "poi") == "poi":
            number += 1
            poi["_num"] = number
        else:
            poi["_num"] = None
    return pois


def render_map(segments, out_path, width, height, url_template, attribution,
               tile_size=256, pois=None, draw_route=True, draw_pois=True):
    """Draw the tile layer, optionally with the route burnt in, and save a JPEG.

    Segments are drawn as separate polylines, so a track split at teleports
    does not get a straight line drawn across the gaps. Any `pois` (a list of
    {name, lat, lon}) are drawn as numbered pins with a name legend.

    With `draw_route=False` (and usually `draw_pois=False` alongside it) the
    JPEG is just the framed tiles, for pages that draw the route and its places
    as SVG instead (see build_route_overlay). The framing is identical either
    way, so the two renders are interchangeable. Returns the StaticMap, whose
    zoom and centre the overlay needs in order to land on the same pixels.
    """
    from staticmap import Line, StaticMap

    # Add padding (in pixels) to ensure the route fits properly in the frame
    # This prevents the route from being cut off at the edges
    smap = StaticMap(width, height, padding_x=40, padding_y=40,
                     url_template=url_template, tile_size=tile_size)
    center = _fit_map(smap, segments)
    if draw_route:
        for seg in segments:
            # staticmap takes coordinates as (lon, lat). A white casing under
            # the red line keeps it legible over busy map detail.
            coords = [(lon, lat) for lat, lon, _ in seg]
            smap.add_line(Line(coords, "white", 7))
            smap.add_line(Line(coords, "#cc0000", 4))

    image = smap.render(zoom=smap.zoom, center=center)
    image = image.convert("RGB")
    if pois and draw_pois:
        _draw_pois(image, smap, pois)
    _draw_attribution(image, attribution)
    image.save(out_path, "JPEG", quality=90)
    return smap


# Per-category marker styling. General sights get a numbered blue pin. Onsens
# and campsites get their own colour and a drawn glyph (a hot-spring mark and a
# tent) so they read at a glance without needing the legend.
POI_STYLES = {
    "poi": {"fill": "#1565c0", "icon": "number"},
    "onsen": {"fill": "#c2185b", "icon": "onsen"},
    "camp": {"fill": "#2e7d32", "icon": "camp"},
}


def _draw_onsen_icon(draw, cx, cy, r):
    """Draw the hot-spring mark (♨): a bowl with three rising steam wisps."""
    # Bowl: a shallow arc across the lower third of the badge.
    by = cy + r * 0.45
    draw.arc((cx - r * 0.6, by - r * 0.45, cx + r * 0.6, by + r * 0.35),
             start=0, end=180, fill="white", width=2)
    # Three wavy steam lines rising from the bowl, the middle one a touch taller.
    for dx, top in ((-r * 0.38, 0.05), (0.0, -0.1), (r * 0.38, 0.05)):
        pts = []
        for k in range(11):
            t = k / 10.0
            y = (cy + r * 0.2) - t * (r * (0.7 - top))
            x = cx + dx + math.sin(t * math.pi * 2) * r * 0.12
            pts.append((x, y))
        draw.line(pts, fill="white", width=2, joint="curve")


def _draw_camp_icon(draw, cx, cy, r):
    """Draw a tent: a triangle with a centre seam."""
    apex = (cx, cy - r * 0.6)
    left = (cx - r * 0.72, cy + r * 0.55)
    right = (cx + r * 0.72, cy + r * 0.55)
    draw.line([left, apex, right, left], fill="white", width=2, joint="curve")
    draw.line([apex, (cx, cy + r * 0.55)], fill="white", width=2)


def _draw_badge(draw, cx, cy, r, category, number, numfont):
    """Draw one category marker: a coloured disc with its glyph or number."""
    style = POI_STYLES.get(category, POI_STYLES["poi"])
    draw.ellipse((cx - r, cy - r, cx + r, cy + r),
                 fill=style["fill"], outline="white", width=2)
    if style["icon"] == "onsen":
        _draw_onsen_icon(draw, cx, cy, r)
    elif style["icon"] == "camp":
        _draw_camp_icon(draw, cx, cy, r)
    else:  # numbered general POI
        text = str(number)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=numfont)
        draw.text((cx - (right - left) / 2 - left, cy - (bottom - top) / 2 - top),
                  text, fill="white", font=numfont)


def _draw_pois(image, smap, pois):
    """Draw a category marker for each POI plus a top-left name legend.

    Points are projected with the same zoom/centre staticmap chose for the
    render, so markers sit on the route. Sights get numbered blue pins. Onsens
    and campsites get a hot-spring or tent glyph in their own colour. Names go
    in the legend rather than beside each marker, so clustered places stay
    readable. The matching badge (number or glyph) ties each row to its marker.
    """
    from PIL import ImageDraw, ImageFont
    from staticmap.staticmap import _lat_to_y, _lon_to_x

    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 14)
        numfont = ImageFont.truetype("DejaVuSans-Bold.ttf", 12)
        chipfont = ImageFont.truetype("DejaVuSans-Bold.ttf", 11)
    except OSError:
        font = numfont = chipfont = ImageFont.load_default()

    # Only general sights are numbered. The glyph identifies onsens and camps.
    _number_pois(pois)

    radius = 13
    for poi in pois:
        x = smap._x_to_px(_lon_to_x(poi["lon"], smap.zoom))
        y = smap._y_to_px(_lat_to_y(poi["lat"], smap.zoom))
        _draw_badge(draw, x, y, radius, poi.get("category", "poi"), poi["_num"], numfont)

    # Legend: matching badge + name per row, on an opaque plate top-left (the
    # attribution sits bottom-right, so the corners don't clash).
    margin, pad, row_h, chip = 8, 6, 22, 9
    text_w = max(draw.textlength(poi["name"], font=font) for poi in pois)
    box_w = pad + 2 * chip + 6 + text_w + pad
    box_h = pad + row_h * len(pois) + pad
    draw.rectangle((margin, margin, margin + box_w, margin + box_h), fill=(255, 255, 255))
    for k, poi in enumerate(pois):
        cy = margin + pad + row_h * k + row_h / 2
        cx = margin + pad + chip
        _draw_badge(draw, cx, cy, chip, poi.get("category", "poi"), poi["_num"], chipfont)
        _, top, _, bottom = draw.textbbox((0, 0), poi["name"], font=font)
        draw.text((cx + chip + 6, cy - (bottom - top) / 2 - top), poi["name"],
                  fill=(40, 40, 40), font=font)


def _draw_attribution(image, text):
    """Stamp an attribution label into the bottom-right corner (OSM policy)."""
    from PIL import ImageDraw, ImageFont

    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 13)
    except OSError:
        font = ImageFont.load_default()

    pad = 4
    margin = 5
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    tw, th = right - left, bottom - top
    x = image.width - tw - margin - pad
    y = image.height - th - margin - pad

    # Semi-opaque white plate behind the text keeps it readable over any tile.
    draw.rectangle(
        (x - pad, y - pad, x + tw + pad, y + th + pad),
        fill=(255, 255, 255),
    )
    draw.text((x - left, y - top), text, fill=(50, 50, 50), font=font)



# --- Vector route overlay ---------------------------------------------------
#
# map.jpg used to be a finished picture with the route burnt into it. Emitting
# the route's geometry separately instead lets the page draw it as SVG over the
# tiles: crisp at any pixel density, coloured by the same grade bands as the
# elevation profile, and - because every point carries its distance along the
# ride - hoverable in step with that profile.

# Douglas-Peucker tolerance for the overlay, in image pixels. The overlay is
# drawn in the image's own pixel coordinates and displayed at roughly 0.7x, so
# 0.4 px stays under half a device pixel even on a 2x screen.
MAP_SIMPLIFY_TOLERANCE = 0.4


def mercator_bounds(smap):
    """The Web Mercator unit-square rectangle a rendered StaticMap covers.

    staticmap places pixels at (tile_coord - centre) * tile_size + size / 2.
    Dividing out the zoom's tile count restates that as a plain rectangle in
    normalised Mercator space (x and y both running 0..1), which a browser can
    invert with nothing but the standard projection formula - no zoom levels,
    no tile arithmetic, and no dependency on staticmap having been the thing
    that drew the image.
    """
    n = 2 ** smap.zoom
    return {
        "u0": (smap.x_center - (smap.width / 2) / smap.tile_size) / n,
        "u1": (smap.x_center + (smap.width / 2) / smap.tile_size) / n,
        "v0": (smap.y_center - (smap.height / 2) / smap.tile_size) / n,
        "v1": (smap.y_center + (smap.height / 2) / smap.tile_size) / n,
    }


def _projector(bounds, width, height):
    """(lat, lon) -> image pixel, via the bounds the browser will also use.

    Deliberately goes through `bounds` rather than staticmap's own helpers:
    projecting here the same way the page projects there is what guarantees the
    overlay cannot drift off the tiles it is drawn over.
    """
    du = bounds["u1"] - bounds["u0"]
    dv = bounds["v1"] - bounds["v0"]

    def project(lat, lon):
        rad = math.radians(lat)
        u = (lon + 180.0) / 360.0
        v = (1 - math.log(math.tan(rad) + 1 / math.cos(rad)) / math.pi) / 2
        return ((u - bounds["u0"]) / du * width, (v - bounds["v0"]) / dv * height)

    return project


def _band_table(profiles):
    """Flatten every segment's clustered bands into [(km_start, km_end, colour)].

    Ascending in km across the whole ride, so a walk over the full-resolution
    track can pick up each point's colour with a moving pointer.

    Clusters on distance with the profile's own threshold rather than anything
    map-specific. A pixel-based minimum would read better on a switchback, but
    it would also let the map call a stretch yellow that the chart calls green
    - and the whole point of a synced hover is that the two graphics are the
    same data seen twice.
    """
    min_run_km = min_band_km(profiles)
    table = []
    for km, _ele, colours, _ll in profiles:
        for colour, i, j in cluster_bands(km, colours, min_run_km):
            table.append((km[i], km[j + 1], colour))
    return table


def build_route_overlay(segments, smap, pois=None):
    """Grade-banded route polylines in image pixels, plus the profile series.

    Returns {"map": …, "route": [{"c", "seg", "pts": [[x, y, km], …]}, …],
    "hover": [[km, ele, x, y, seg], …], "pois": [{name, category, url, x, y,
    num}, …]}.

    Places go out as coordinates rather than painted pins so the page can draw
    them as real elements: legible at any size (a baked legend shrinks with the
    image and is unreadable on a phone), and each one able to link to the page
    the post already links, which pixels cannot do.

    `route` is what gets drawn. `hover` is the lookup behind the synced cursor:
    one row per profile sample, carrying everything a hover needs to answer
    "where is this on the other graphic" in either direction. It is deliberately
    the coarse 100 m series - the cursor only has to land within a few pixels,
    and this is the resolution the chart itself was drawn at.

    The route keeps full track resolution (simplified only where that moves the
    line less than a pixel), because a map has to show switchbacks the 100 m
    profile resampling would cut the corners off. Colour comes from the profile
    instead, looked up by distance - so the two graphics band identically
    without the map inheriting the profile's coarseness.

    Consecutive runs share their boundary point, so the drawn line has no gaps
    where a colour changes. Each run carries its segment index: a track split
    at a teleport must not have a hover slide across the gap.
    """
    bounds = mercator_bounds(smap)
    project = _projector(bounds, smap.width, smap.height)

    profiles = build_profiles(segments)
    if not profiles:
        raise ValueError("No elevation data in GPX file; cannot band the route.")

    table = _band_table(profiles)
    default_colour = table[0][2] if table else GRADE_BANDS[0][1]

    runs = []
    for seg_i, (prof, seg) in enumerate(zip(profiles, segments)):
        # Distances accumulate exactly as _segment_profile accumulated them, so
        # a full-resolution point and a profile sample at the same place agree
        # on how far along the ride they are.
        offset_km = prof[0][0]
        xs, ys, kms = [], [], []
        cumulative = 0.0
        prev = None
        for lat, lon, _ele in seg:
            if prev is not None:
                cumulative += haversine(prev[0], prev[1], lat, lon)
            prev = (lat, lon)
            x, y = project(lat, lon)
            xs.append(x)
            ys.append(y)
            kms.append(offset_km + cumulative / 1000.0)

        colours = []
        band = 0
        for km in kms:
            while band + 1 < len(table) and km >= table[band][1]:
                band += 1
            colours.append(table[band][2] if table else default_colour)

        # Split at colour changes, simplify each run between its own endpoints
        # (so no boundary moves), and repeat the boundary point in the next run.
        start = 0
        for i in range(1, len(colours) + 1):
            if i < len(colours) and colours[i] == colours[start]:
                continue
            end = min(i, len(colours) - 1)
            if end > start:
                kept = simplify_indices(xs, ys, start, end, MAP_SIMPLIFY_TOLERANCE)
                runs.append({
                    "c": colours[start],
                    "seg": seg_i,
                    "pts": [[round(xs[k], 1), round(ys[k], 1), round(kms[k], 4)] for k in kept],
                })
            start = i

    hover = []
    for seg_i, (km, ele, _colours, coords) in enumerate(profiles):
        for k, e, (lat, lon) in zip(km, ele, coords):
            x, y = project(lat, lon)
            hover.append([round(k, 3), round(e), round(x, 1), round(y, 1), seg_i])

    marks = []
    for poi in _number_pois(list(pois or [])):
        x, y = project(poi["lat"], poi["lon"])
        marks.append({
            "name": poi["name"],
            "category": poi.get("category", "poi"),
            "url": poi.get("url"),
            "num": poi["_num"],
            "x": round(x, 1),
            "y": round(y, 1),
        })

    return {
        "map": {
            "w": smap.width,
            "h": smap.height,
            "u0": bounds["u0"],
            "u1": bounds["u1"],
            "v0": bounds["v0"],
            "v1": bounds["v1"],
        },
        "route": runs,
        "hover": hover,
        "pois": marks,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Generate an OSM route map and elevation profile from a GPX track."
    )
    ap.add_argument("gpx", help="path to the .gpx file")
    ap.add_argument(
        "--outdir",
        default=None,
        help="directory for map.jpg and elevation.svg (default: the GPX file's directory)",
    )
    ap.add_argument(
        "--layer",
        default=DEFAULT_LAYER,
        choices=sorted(LAYERS),
        help=f"map tile layer (default: {DEFAULT_LAYER})",
    )
    ap.add_argument(
        "--api-key",
        default=None,
        help="override the selected layer's API key (else its env var or .env)",
    )
    ap.add_argument("--map-width", type=int, default=1024, help="map width in pixels")
    ap.add_argument("--map-height", type=int, default=1280, help="map height in pixels")
    ap.add_argument(
        "--elev-width", type=int, default=1024, help="elevation profile aspect-ratio width"
    )
    ap.add_argument(
        "--elev-height", type=int, default=350, help="elevation profile aspect-ratio height"
    )
    ap.add_argument(
        "--post",
        default=None,
        help="a post .mdx whose linked places of interest are added to the map",
    )
    ap.add_argument(
        "--refresh-pois",
        action="store_true",
        help="re-geocode all places, ignoring the cached pois.json",
    )
    ap.add_argument(
        "--elevation-only",
        action="store_true",
        help="write only elevation.svg; skip the map (no network/API key needed)",
    )
    ap.add_argument(
        "--overlay",
        action="store_true",
        help="leave the route off map.jpg and write route.json for the page to "
             "draw it as SVG (grade-banded, hoverable with the elevation profile)",
    )
    args = ap.parse_args(argv)

    outdir = args.outdir or os.path.dirname(os.path.abspath(args.gpx))
    os.makedirs(outdir, exist_ok=True)
    map_path = os.path.join(outdir, "map.jpg")
    elevation_path = os.path.join(outdir, "elevation.svg")
    route_path = os.path.join(outdir, "route.json")

    try:
        segments = [seg for seg in read_segments(args.gpx) if len(seg) >= 2]
        if not segments:
            raise ValueError("Need at least two track points to draw a route.")

        if args.overlay and args.elevation_only:
            raise ValueError("--overlay needs the map render; drop --elevation-only.")

        smap = None
        if not args.elevation_only:
            url_template, attribution, tile_size = resolve_layer(args.layer, args.api_key)
            pois = None
            if args.post:
                lats = [lat for seg in segments for lat, _, _ in seg]
                lons = [lon for seg in segments for _, lon, _ in seg]
                viewbox = (min(lons), min(lats), max(lons), max(lats))
                cache_path = os.path.join(outdir, "pois.json")
                pois = resolve_pois(args.post, viewbox, cache_path, args.refresh_pois)
            smap = render_map(
                segments,
                map_path,
                args.map_width,
                args.map_height,
                url_template,
                attribution,
                tile_size,
                pois,
                draw_route=not args.overlay,
                draw_pois=not args.overlay,
            )
        plot = render_elevation_svg(segments, elevation_path, args.elev_width, args.elev_height)

        if args.overlay:
            # The chart's coordinate system rides along with the map's: a synced
            # hover has to invert both, and shipping them together keeps them
            # from ever describing different renders of the same track.
            overlay = build_route_overlay(segments, smap, pois)
            overlay["plot"] = plot
            with open(route_path, "w", encoding="utf-8") as fh:
                json.dump(overlay, fh, separators=(",", ":"))
    except (ET.ParseError, ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except ImportError as exc:
        print(
            f"Error: missing dependency ({exc.name}). Install with: pip install staticmap",
            file=sys.stderr,
        )
        sys.exit(1)

    if not args.elevation_only:
        print(f"Written {map_path}")
    print(f"Written {elevation_path}")
    if args.overlay:
        print(f"Written {route_path}")


if __name__ == "__main__":
    sys.exit(main())
