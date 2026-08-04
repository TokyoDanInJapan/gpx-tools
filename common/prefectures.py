"""Which Japanese prefecture a point falls in.

Two tools ask this - the coverage map that shades the prefectures a set of
tracks passed through, and the aggregate charts that bin distance by prefecture
- and the boundaries they ask against have to be the same ones, or the map and
the bar chart disagree about the same ride.

The boundaries ship with the package (`common/data/japan_prefectures.geojson`),
so nothing here needs a network or a key.
"""

import json
import os

#: How often to re-resolve the prefecture along a track, in metres. Point-in-
#: polygon over 47 prefectures is not free, and consecutive track points are
#: metres apart. Checking every 200 m is indistinguishable in the output, and
#: orders of magnitude cheaper.
PREF_CHECK_M = 200.0

#: The boundaries that ship with the package.
DEFAULT_GEOJSON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "data", "japan_prefectures.geojson")


def clean_name(nam):
    """Turn dataset names like 'Kyoto Fu' / 'Hokkai Do' into display names."""
    special = {"Hokkai Do": "Hokkaido", "Tokyo To": "Tokyo",
               "Osaka Fu": "Osaka", "Kyoto Fu": "Kyoto"}
    if nam in special:
        return special[nam]
    return nam[:-4] if nam.endswith(" Ken") else nam


class PrefectureLocator:
    """Point-in-polygon lookup over Japanese prefecture boundaries.

    Each prefecture is stored as a list of (bbox, exterior_ring). A bbox
    prefilter skips most rings cheaply, and locate() tries a caller-supplied
    hint (the previous prefecture) first, since consecutive track points almost
    always fall in the same one.
    """

    def __init__(self, geojson_path=DEFAULT_GEOJSON):
        with open(geojson_path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.prefs = []  # (name, [(bbox, ring), ...])
        for ft in data["features"]:
            name = clean_name(ft["properties"]["nam"])
            geom = ft["geometry"]
            if geom["type"] == "Polygon":
                raw = [geom["coordinates"]]
            elif geom["type"] == "MultiPolygon":
                raw = geom["coordinates"]
            else:
                continue
            polys = []
            for poly in raw:
                ring = poly[0]  # exterior ring. Holes are ignored: rare, and immaterial
                xs = [c[0] for c in ring]
                ys = [c[1] for c in ring]
                polys.append(((min(xs), min(ys), max(xs), max(ys)), ring))
            self.prefs.append((name, polys))
        self._by_name = {n: p for n, p in self.prefs}

    @staticmethod
    def _in_ring(x, y, ring):
        """Ray-casting test: is (x=lon, y=lat) inside this ring?"""
        inside = False
        n = len(ring)
        j = n - 1
        for i in range(n):
            xi, yi = ring[i][0], ring[i][1]
            xj, yj = ring[j][0], ring[j][1]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
        return inside

    def _hits(self, name, polys, x, y):
        for (minx, miny, maxx, maxy), ring in polys:
            if minx <= x <= maxx and miny <= y <= maxy and self._in_ring(x, y, ring):
                return True
        return False

    def locate(self, lat, lon, hint=None):
        """Return the prefecture name containing (lat, lon), or None."""
        x, y = lon, lat
        if hint is not None and self._hits(hint, self._by_name[hint], x, y):
            return hint
        for name, polys in self.prefs:
            if name == hint:
                continue
            if self._hits(name, polys, x, y):
                return name
        return None
