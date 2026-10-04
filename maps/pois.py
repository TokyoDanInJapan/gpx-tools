"""Finding the places a write-up talks about, and putting them on the map.

The places are the Markdown links in the text. Each is classified from its
wording and the section it sits under, then geocoded against the route's
bounding box so a name that also exists elsewhere snaps to the corridor.

Geocoding is a network call to Nominatim and is rate-limited by their usage
policy, so results are cached in a hand-editable pois.json beside the map. A
place geocoding can't place is left with a null lat/lon for a human to fill in;
that is a normal outcome, not a failure.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from common.net import user_agent

# A Markdown inline link, such as [Lake Miyagase (宮ヶ瀬湖)](https://…). Only
# http(s) links are matched, so site-relative cross-references (/cycling/…) and
# #anchors are ignored - they are not places.
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
# A parenthetical (half- or full-width brackets), used to split a label's
# Japanese name from its English name.
_PAREN_RE = re.compile(r"[（(]([^）)]*)[）)]")
# Any CJK / Japanese (kanji, kana, full-width) character.
_CJK_RE = re.compile(r"[　-ヿ㐀-鿿＀-￯]")
# The opening tag of a Japanese-translation div: <div class="lang-ja" lang="ja">,
# or the older class="aw-lang-ja".
_JA_DIV_RE = re.compile(r'<div\b[^>]*\blang(?:-ja\b|="ja")')
# A Markdown heading line, such as "## Sights on the Way".
_HEADING_RE = re.compile(r"\s{0,3}#{1,6}\s+(.*\S)")
# Keywords that mark a place (or its section) as a campsite or an onsen. Tested
# against both the English label and its Japanese name, so either form matches.
_CAMP_RE = re.compile(r"camp(site|ground)?|キャンプ|オートキャンプ|野営", re.I)
_ONSEN_RE = re.compile(r"onsen|hot spring|spa|湯|温泉|スパ", re.I)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def categorise(label, section):
    """Classify a place as 'camp', 'onsen' or 'poi'.

    The label's own wording wins (a "… Campsite" or "… Onsen" is unambiguous);
    failing that we fall back to the section it sits under, so a bare name in a
    "Campsites & Onsens" list still gets typed. A mixed camp-and-onsen heading
    can't disambiguate a keyword-less item, so it defaults to onsen.
    """
    if _CAMP_RE.search(label):
        return "camp"
    if _ONSEN_RE.search(label):
        return "onsen"
    section = section or ""
    if _CAMP_RE.search(section) and not _ONSEN_RE.search(section):
        return "camp"
    if _ONSEN_RE.search(section):
        return "onsen"
    return "poi"


def extract_pois(post_path):
    """Pull places of interest (the markdown links) from a post body.

    Scans line by line so each link is tagged with the heading it falls under,
    which (with the label's wording) decides its category. Returns an ordered,
    de-duplicated list of {name, query, url, category}:
      name      the English label, with any parenthetical stripped
      query     the geocoding query - the Japanese name in parentheses when the
                label carries one (far more reliable in Japan), else the label
      url       the link target (kept in the cache for reference, not drawn)
      category  'camp', 'onsen' or 'poi' (general place of interest)
    """
    pois, seen = [], set()
    section = None
    in_ja = False  # inside a Japanese translation block
    with open(post_path, encoding="utf-8") as fh:
        for line in fh:
            # Bilingual posts repeat every place inside a Japanese-translation
            # div (<div class="lang-ja" lang="ja"> … </div>). Those links point
            # at the same POIs as the English ones, so skip them or every marker
            # would be plotted twice. The English/neutral prose is the source.
            #
            # The match takes the lang="ja" attribute or a lang-ja class, with
            # or without the aw- prefix that older posts carry. Matching one
            # literal class name broke silently when the site renamed it, and
            # every bilingual map came out with each marker twice.
            if _JA_DIV_RE.search(line):
                in_ja = True
                continue
            if in_ja:
                if "</div>" in line:
                    in_ja = False
                continue
            heading = _HEADING_RE.match(line)
            if heading:
                section = heading.group(1).strip()
                continue
            for label, url in _LINK_RE.findall(line):
                label = label.strip()
                name = _PAREN_RE.sub("", label).strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                paren = _PAREN_RE.search(label)
                query = (
                    paren.group(1).strip()
                    if paren and _CJK_RE.search(paren.group(1))
                    else name
                )
                pois.append({
                    "name": name,
                    "query": query,
                    "url": url,
                    "category": categorise(label, section),
                })
    return pois


def geocode(query, viewbox, agent=None):
    """Forward-geocode a place name, biased to the route's bounding box.

    `viewbox` is (min_lon, min_lat, max_lon, max_lat). Results are bounded to it
    so a name shared with somewhere else snaps to the route corridor. Returns
    (lat, lon) or None. Callers must space live calls out (~1 req/s) to respect
    the public Nominatim usage policy.
    """
    params = {
        "q": query,
        "format": "jsonv2",
        "limit": "1",
        "accept-language": "en",
        "viewbox": ",".join(f"{v:.6f}" for v in viewbox),
        "bounded": "1",
    }
    req = urllib.request.Request(
        NOMINATIM_URL + "?" + urllib.parse.urlencode(params),
        headers={"User-Agent": agent or user_agent()},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, ValueError, TimeoutError):
        return None
    if not data:
        return None
    return float(data[0]["lat"]), float(data[0]["lon"])


def resolve_pois(post_path, viewbox, cache_path, refresh=False):
    """Resolve each post POI to coordinates, with a JSON cache as override.

    The cache maps a place name to {query, url, category, lat, lon}. It is read
    first so already-resolved (or hand-corrected) coordinates and categories are
    reused without hitting the network. Names not in it are geocoded and written
    back. Geocoding can't place everything - edit the cache by hand to fix or
    fill a place (a null lat/lon is preserved, so that place is simply skipped
    until you supply one) or to correct its category.

    Returns the POIs that have coordinates, as {name, lat, lon, category}, in
    post order.
    """
    cache = {}
    if os.path.exists(cache_path) and not refresh:
        try:
            with open(cache_path, encoding="utf-8") as fh:
                cache = json.load(fh)
        except (OSError, ValueError):
            cache = {}

    resolved, missing, fetched = [], [], False
    for poi in extract_pois(post_path):
        name = poi["name"]
        entry = cache.get(name)
        if entry is None:
            if fetched:
                time.sleep(1.1)  # be polite to Nominatim between live calls
            coords = geocode(poi["query"], viewbox)
            fetched = True
            entry = {
                "query": poi["query"],
                "url": poi["url"],
                "category": poi["category"],
                "lat": coords[0] if coords else None,
                "lon": coords[1] if coords else None,
            }
            cache[name] = entry
        else:
            entry.setdefault("category", poi["category"])  # backfill older caches
        if entry.get("lat") is not None and entry.get("lon") is not None:
            resolved.append({
                "name": name,
                "lat": entry["lat"],
                "lon": entry["lon"],
                "category": entry.get("category", poi["category"]),
                # Carried for the overlay's legend, which can link each place
                # the way the post body does. A baked-in legend could not.
                "url": entry.get("url") or poi.get("url"),
            })
        else:
            missing.append(name)

    try:
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    except OSError:
        pass

    if missing:
        print(
            "Note: no coordinates for: " + ", ".join(missing)
            + f"\n      add lat/lon by hand in {cache_path} and re-run.",
            file=sys.stderr,
        )
    return resolved
