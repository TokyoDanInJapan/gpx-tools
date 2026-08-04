#!/usr/bin/env python3
"""Add or refresh the <metadata> block of GPX files from their sibling post.

For each GPX file it finds the post with the same basename, so `bandai.gpx`
takes its text from `bandai.mdx`. It reads the frontmatter and writes a
metadata block in this shape:

    <metadata>
      <name>TITLE from frontmatter</name>
      <desc>EXCERPT from frontmatter</desc>
      <author>
        <name>Daniel Hebberd</name>
        <email id="you" domain="example.com" />
        <link href="https://example.com/cycling/touring/2019/bandai">
          <text>Mount Bandai Tour</text>
        </link>
      </author>
      <copyright>
        <year>YEAR from publishDate</year>
        <license>https://creativecommons.org/licenses/by-nc/4.0/</license>
      </copyright>
    </metadata>

The author <link> points at the track's own page, derived from where the file
sits: the path below `posts.root`, extension dropped, hung off `site`.

Who the author is, which site this is, and where the posts live are all
properties of a publication rather than of a GPX file, so they are read from a
config file rather than baked in here. `gpx-tools.yaml` in the working
directory is picked up automatically; --config names another.

    author:  {name: A Rider, email: you@example.com}
    site:    https://example.com
    license: https://creativecommons.org/licenses/by-nc/4.0/
    posts:   {root: rides, sidecar: .md}

Existing <link>/<time>/<keywords>/<bounds>/<extensions> children are preserved
and re-emitted in GPX 1.1 schema order, except the boilerplate
<link><text>Garmin Connect</text></link> that exporters add, which is dropped.
Any pre-existing name/desc/author/copyright are replaced. A GPX with no sibling
post is skipped.

Usage:
    gpx-metadata <file.gpx> [<file.gpx> ...]
    gpx-metadata --all          # every gpx below posts.root
"""

import argparse
import glob
import os
import re
import sys
from xml.sax.saxutils import escape, quoteattr

import yaml

#: Where the publication's details are read from, when --config is not given.
DEFAULT_CONFIG = "gpx-tools.yaml"

#: What a config file is expected to carry. Nothing is invented for a missing
#: value: a block cannot honestly claim an author or a licence it was not told.
CONFIG_DEFAULTS = {
    "author": {"name": None, "email": None},
    "site": None,
    "license": None,
    "posts": {"root": ".", "sidecar": ".mdx"},
}

# Children we always rebuild ourselves. Anything else in an old block is kept.
OWNED = ("name", "desc", "author", "copyright")
# Schema order for the children we preserve, emitted after the ones we own.
KEEP_ORDER = ("link", "time", "keywords", "bounds", "extensions")


def load_config(path=None):
    """Read the publication's details, falling back to the defaults.

    With no path and no `gpx-tools.yaml` to hand, the defaults stand: every
    field empty but the post root, which becomes the working directory. The
    tool then still runs and simply writes less into the block.
    """
    if path is None and os.path.exists(DEFAULT_CONFIG):
        path = DEFAULT_CONFIG
    config = {k: (dict(v) if isinstance(v, dict) else v)
              for k, v in CONFIG_DEFAULTS.items()}
    if path is None:
        return config
    with open(path, encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}
    for key, value in loaded.items():
        if isinstance(config.get(key), dict) and isinstance(value, dict):
            config[key].update(value)
        else:
            config[key] = value
    return config


def frontmatter(path):
    """Return the post's YAML frontmatter as a dict, or {} if it has none."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}
    return yaml.safe_load(m.group(1)) or {}


def year_of(data):
    """The four-digit year from the post's publishDate, or None."""
    m = re.search(r"\b(\d{4})\b", str(data.get("publishDate")))
    return m.group(1) if m else None


def post_url(gpx_path, config):
    """The track's page: `site` plus its path below `posts.root`, extension dropped.

    Returns None without a site, since a relative href in a GPX file that will
    be downloaded and opened anywhere points at nothing.
    """
    site = config.get("site")
    if not site:
        return None
    rel = os.path.relpath(gpx_path, config["posts"]["root"] or ".")
    rel = os.path.splitext(rel)[0].replace(os.sep, "/")
    return f"{site.rstrip('/')}/{rel}"


def build_block(title, excerpt, year, url, config):
    """The lines of the block we own, in GPX 1.1 schema order."""
    author = config.get("author") or {}
    name, email = author.get("name"), author.get("email")
    licence = config.get("license")

    lines = ["  <metadata>"]
    lines.append(f"    <name>{escape(title)}</name>")
    if excerpt:
        lines.append(f"    <desc>{escape(excerpt)}</desc>")
    if name or email or url:
        lines.append("    <author>")
        if name:
            lines.append(f"      <name>{escape(name)}</name>")
        if email:
            eid, _, edomain = email.partition("@")
            lines.append(f"      <email id={quoteattr(eid)} domain={quoteattr(edomain)} />")
        if url:
            lines.append(f"      <link href={quoteattr(url)}>")
            lines.append(f"        <text>{escape(title)}</text>")
            lines.append("      </link>")
        lines.append("    </author>")
    if year or licence:
        lines.append("    <copyright>")
        if year:
            lines.append(f"      <year>{escape(year)}</year>")
        if licence:
            lines.append(f"      <license>{escape(licence)}</license>")
        lines.append("    </copyright>")
    return lines


def reindent_element(block, base=4, step=2):
    """Re-emit one XML element block with canonical indentation.

    Existing whitespace between tags is discarded and rebuilt from the nesting
    depth (so the top element sits at `base` spaces, each child `step` deeper),
    and a leaf with only text stays on one line (``<text>Bandai</text>``). This
    is what makes preservation idempotent: the result depends only on the
    element's structure, not on whatever indentation it arrived with - so a
    block that picked up stray indentation from an earlier run is normalised
    rather than shifted again. Assumes no ``>`` inside attribute values, which
    holds for the GPX metadata children we keep (link/time/keywords/bounds)."""
    flat = re.sub(r">\s+<", "><", block.strip())
    tokens = re.findall(r"<[^>]+>|[^<]+", flat)
    out, depth, i = [], 0, 0
    pad = lambda d: " " * (base + step * d)
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("</"):
            depth -= 1
            out.append(pad(depth) + tok)
            i += 1
        elif tok.endswith("/>"):
            out.append(pad(depth) + tok)
            i += 1
        elif tok.startswith("<"):  # opening tag
            # collapse a text-only leaf (<tag>text</tag>) onto one line
            if i + 2 < len(tokens) and not tokens[i + 1].startswith("<") \
                    and tokens[i + 2].startswith("</"):
                out.append(pad(depth) + tok + tokens[i + 1] + tokens[i + 2])
                i += 3
            elif i + 1 < len(tokens) and tokens[i + 1].startswith("</"):  # empty
                out.append(pad(depth) + tok + tokens[i + 1])
                i += 2
            else:
                out.append(pad(depth) + tok)
                depth += 1
                i += 1
        else:  # bare text (shouldn't occur at top level, kept for safety)
            out.append(pad(depth) + tok.strip())
            i += 1
    return "\n".join(out)


def preserved_children(old_inner):
    """Return preserved child blocks (link/time/keywords/bounds/extensions),
    stripped of any name/desc/author/copyright, re-indented canonically."""
    if not old_inner:
        return []
    inner = old_inner
    # drop the elements we own (handle both <x>..</x> and self-closing)
    for tag in OWNED:
        inner = re.sub(rf"<{tag}\b.*?</{tag}>", "", inner, flags=re.DOTALL)
        inner = re.sub(rf"<{tag}\b[^>]*/>", "", inner)
    kept = []
    for tag in KEEP_ORDER:
        for m in re.finditer(rf"(<{tag}\b.*?</{tag}>|<{tag}\b[^>]*/>)", inner, re.DOTALL):
            block = m.group(1)
            # drop the boilerplate Garmin Connect link exporters add
            if tag == "link" and re.search(r"<text>\s*Garmin Connect\s*</text>", block):
                continue
            kept.append(reindent_element(block))
    return kept


def update_file(gpx_path, config):
    """Rewrite one file's metadata block. Returns (status, detail)."""
    sidecar = os.path.splitext(gpx_path)[0] + (config["posts"]["sidecar"] or ".mdx")
    if not os.path.exists(sidecar):
        return "skip", f"no sibling {config['posts']['sidecar']}"
    data = frontmatter(sidecar)
    title = data.get("title")
    if not title:
        return "skip", "no title in frontmatter"
    block = build_block(title, data.get("excerpt"), year_of(data),
                        post_url(gpx_path, config), config)

    with open(gpx_path, encoding="utf-8") as fh:
        text = fh.read()
    old = re.search(r"[ \t]*<metadata>(.*?)</metadata>", text, re.DOTALL)
    kept = preserved_children(old.group(1) if old else "")
    new_block = "\n".join(block + kept + ["  </metadata>"])

    if old:
        new = text[:old.start()] + new_block + text[old.end():]
    else:
        new = re.sub(r"(<gpx\b[^>]*>)", r"\1\n" + new_block, text,
                     count=1, flags=re.DOTALL)
    if new == text:
        return "skip", "no change"
    with open(gpx_path, "w", encoding="utf-8") as fh:
        fh.write(new)
    return "ok", title


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Files with no sibling post, or no title in its frontmatter, are skipped.")
    ap.add_argument("gpx", nargs="*", help="paths to .gpx files")
    ap.add_argument("--all", action="store_true",
                    help="every .gpx below posts.root, instead of named files")
    ap.add_argument("--config", default=None,
                    help=f"publication details (default: ./{DEFAULT_CONFIG} if present)")
    args = ap.parse_args(argv)

    if not args.gpx and not args.all:
        ap.error("give some .gpx files, or --all")

    config = load_config(args.config)
    if args.all:
        root = config["posts"]["root"] or "."
        files = sorted(glob.glob(os.path.join(root, "**", "*.gpx"), recursive=True))
    else:
        files = args.gpx

    n_ok = 0
    for f in files:
        status, info = update_file(f, config)
        if status == "ok":
            n_ok += 1
            print(f"  updated  {f}  ->  {info}")
        else:
            print(f"  skipped  {f}  ({info})")
    print(f"\nUpdated {n_ok} / {len(files)} files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
