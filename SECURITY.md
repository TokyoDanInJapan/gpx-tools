# Security

## Reporting something

Use **[Report a vulnerability](https://github.com/TokyoDanInJapan/gpx-tools/security/advisories/new)** on the
Security tab. That is private: it opens an advisory only you and the maintainer can see, so a problem can be
fixed before it is described in public.

If that page is unavailable to you, open an ordinary issue saying only that you have something to report and
asking for a private channel. Do not put the details in it - a public issue discloses the problem in the act
of reporting it.

Expect an acknowledgement within a week. This is a small toolbox maintained by one person, so a fix may take
longer than that; you will be told either way rather than left waiting.

## What is supported

The latest patch version, currently `1.0.x`. Older versions are not patched. Consumers pin a tag in their
dependency specifier, so upgrading is a one-line change - see the release notes for anything that changed.

## The threat model, stated plainly

These are command-line tools you point at your own files. They are not a service, they take no untrusted
input over a network, and they run with whatever permissions you have. Three consequences are worth naming,
because two of them look like vulnerabilities and are not.

- **GPX is parsed with `xml.etree.ElementTree`,** which the Python documentation is explicit about: it is not
  hardened against maliciously constructed XML. A file crafted to expand exponentially can exhaust memory.
  That is accepted rather than overlooked - the input is a track off your own recorder or a route you chose to
  download. If you plan to run these over files from strangers, parse them with `defusedxml` first.
- **The tools write where you tell them to.** `--outdir`, `--outroot` and `--write` all take a path and use
  it. `gpx-metadata` and `gpx-split` rewrite their input in place. There is no sandbox, and there is not meant
  to be one.
- **API keys are read, never written.** A tile provider's key comes from the environment or a `.env`, and it
  goes into a request URL and nowhere else - not into an image, not into an error message, and not into the
  tile cache path, which is keyed on the layer's name for exactly that reason. A key leaking into any output
  would be a real finding, and there are tests asserting it does not.

Everything the tools fetch - map tiles, elevation tiles, geocoding - goes to providers named in
`maps/layers.py` and `terrain/voxel.py` over HTTPS. Nothing else is contacted, and nothing that is fetched is
ever executed.
