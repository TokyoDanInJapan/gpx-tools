# photos/

Placing a gallery's photographs along the track they were taken on. One tool,
`gpx-photo-points`, needing Pillow and timezonefinder (`pip install ".[photos]"`).

```bash
gpx-photo-points ride.gpx --originals ~/photos \
    --track-root rides --outroot build/rides
```

It reads the gallery manifest beside the track - a JSON file with an `images`
array - finds each of those files in the originals tree, and writes
`photos.json` giving every photograph a distance along the ride.

That distance is deliberately the only position recorded. A renderer drawing
the route on a map and one flying it over a terrain model both derive their own
coordinates from the same number, so the two can never disagree about where a
photograph was taken.

## EXIF comes from the originals

Published copies are usually stripped of their metadata, which is the right
thing to do with the coordinates of where somebody lives. So the timestamps
this needs exist only in the full-size tree, and `--originals` is where that
tree is.

Where the tool looks inside it is the same relative path the track has below
`--track-root`, and the output lands in the same relative place below
`--outroot`. Given neither, the photographs are looked for in
`<originals>/<track name>` and the output lands beside the track.

## How a photograph gets its position

Four things, three of which were wrong answers first.

- **The clock places it. The GPS only calibrates.** Photo GPS is far worse than
  it looks - phones record cell-tower fixes happily, and on one hike here the
  median photograph sits 720 m off the route it was taken on, with one landing
  7.5 km away. `DateTimeOriginal` against the track's `<time>` is accurate to the
  sampling interval. What GPS is good for is saying what the camera's clock read
  at a known moment, since EXIF carries no timezone.
- **The anchor estimator is a mode, not a median.** On a lap course the same
  stretch of road is nearest to photographs taken hours apart: one race here
  produced anchors from -11.8 h to +19.2 h whose median was +2.9 h, a value no
  anchor claimed. `calibrate()` takes the largest group agreeing to within half
  an hour instead.
- **The timezone is looked up, not guessed.** `zone_offset()` uses
  `timezonefinder` and `zoneinfo` for an exact offset on the day in question. It
  replaced guessing from longitude, which agreed on every Japanese track and was
  wrong on all ten of the others - a New Zealand hike placed at +11 h when the
  answer was +13, silently.
- **The two estimators cross-check.** Only the anchors can see the camera's own
  drift, and only the zone cannot be fooled about the timezone. The anchors win
  unless they disagree with the zone by more than an hour.

A photograph with no usable time falls back to its own GPS, if that is near
enough to mean anything. One with neither is not placed - and is printed rather
than dropped silently, because a photograph can fail to place for the perfectly
good reason that it was taken on a day the track does not cover, and a silent
drop looks identical to a bug.

## What photos.json holds

| key | what it holds |
| --- | --- |
| `photos` | per photograph: `file`, `i` (its index in the gallery), `km`, `lat`, `lon`, `by`, and `x`/`y` if a `route.json` was found to place it in |
| `totalKm` | the ride's length, so a reader can position without re-measuring |
| `clockOffsetS`, `clockFrom` | the correction applied, and which estimator won |
| `anchors`, `anchorsAgreed` | how many anchors there were, and how many agreed |

`by` says how each photograph was placed - `clock` or `gps` - which is what
makes a doubtful placement checkable rather than merely wrong.
