#!/usr/bin/env python3
"""Merge several GPX files into one track.

Each input file becomes its own <trkseg> in the output, in the order given.
That is the whole point of the tool rather than an implementation detail: a
merged ride is several legs with gaps between them, and keeping the segment
boundaries is what stops anything downstream measuring, drawing or flying
across the gap between the end of one day and the start of the next.

Routes (<rte>) are read as well as tracks, and their points are copied in as
track points, so a planned route and a recorded ride can be merged together.

Usage:
  gpx-merge <file1.gpx> <file2.gpx> [...] [-o merged.gpx] [--name NAME]
"""

import argparse
import sys
import xml.etree.ElementTree as ET

NS = "http://www.topografix.com/GPX/1/1"
NSDATA = "http://www.cluetrust.com/XML/GPXDATA/1/0"
XSI = "http://www.w3.org/2001/XMLSchema-instance"

ET.register_namespace("", NS)
ET.register_namespace("gpxdata", NSDATA)
ET.register_namespace("xsi", XSI)


def merge(paths, name=None):
    """Return an ElementTree of every input file's points, one segment each."""
    root = ET.Element(f"{{{NS}}}gpx", {
        f"{{{XSI}}}schemaLocation": (
            f"{NS} http://www.topografix.com/GPX/1/1/gpx.xsd "
            f"{NSDATA} http://www.cluetrust.com/Schemas/gpxdata10.xsd"
        ),
        "version": "1.1",
    })
    if name:
        meta = ET.SubElement(root, f"{{{NS}}}metadata")
        ET.SubElement(meta, f"{{{NS}}}name").text = name
    trk = ET.SubElement(root, f"{{{NS}}}trk")
    if name:
        ET.SubElement(trk, f"{{{NS}}}name").text = name

    for path in paths:
        src = ET.parse(path).getroot()
        trkseg = ET.SubElement(trk, f"{{{NS}}}trkseg")
        for child in src:
            tag = child.tag.split("}")[-1]
            if tag == "trk":
                for seg in child.findall(f"{{{NS}}}trkseg"):
                    for pt in seg.findall(f"{{{NS}}}trkpt"):
                        trkseg.append(pt)
            elif tag == "rte":
                for pt in child.findall(f"{{{NS}}}rtept"):
                    trkpt = ET.SubElement(trkseg, f"{{{NS}}}trkpt", pt.attrib)
                    for child_el in pt:
                        trkpt.append(child_el)

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    return tree


def write(tree, path):
    """Write the merged track, declaration first."""
    with open(path, "wb") as fh:
        fh.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
        tree.write(fh, encoding="utf-8", xml_declaration=False)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Each input file becomes one <trkseg>, so the joins stay visible.")
    ap.add_argument("gpx", nargs="+", help="the files to merge, in order")
    ap.add_argument("-o", "--out", default="merged.gpx", help="output path")
    ap.add_argument("--name", default=None, help="name for the merged track")
    args = ap.parse_args(argv)

    write(merge(args.gpx, args.name), args.out)
    print(f"Written {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
