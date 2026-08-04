# The bundled outlines

Two GeoJSON files ship with this package so the tools that need country and
prefecture boundaries need no network and no API key. Neither is this project's
work, and the MIT licence on the code does not cover them.

## japan_prefectures.geojson

The 47 Japanese prefectures, used by `gpx-prefecture-map`, `gpx-statcharts` and
`gpx-stamps`.

Derived from **Global Map Japan (地球地図日本), © 国土地理院 (Geospatial
Information Authority of Japan)** — <https://www.gsi.go.jp/kankyochiri/gm_jpn.html>
— and obtained through [dataofjapan/land](https://github.com/dataofjapan/land).

**Credit the source if you use it.** The distributor asks for that, and it is
why this file exists rather than a link. Anything drawn from these polygons
should carry the GSI credit where a reader can find it.

**Commercial use asks for more.** The distributor's terms also ask commercial
users to file a usage report with GSI. This package is MIT, which permits
commercial use of the *code*; it cannot and does not grant that for this data.
If you are using it commercially, that report is yours to make.

## uk_nz_outlines.geojson

The United Kingdom and New Zealand, used by `gpx-stamps`.

Trimmed from [Natural Earth](https://www.naturalearthdata.com/)'s 1:50m
Admin 0 countries. Natural Earth is **public domain** and asks for no credit:
"you may use the maps in any manner, including modifying the content and design,
electronic dissemination, and offset printing". Credited here anyway, because a
reader deciding whether they may redistribute this file should not have to work
out which half came from where.
