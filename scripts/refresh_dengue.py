#!/usr/bin/env python3
"""
Refreshes the DENGUE_CLUSTERS array embedded in FloodWatch-SG.html from NEA's
"Dengue Clusters (GEOJSON)" dataset on data.gov.sg (d_dbfabf16158d1b0e1c420627c0819168).

Two-step fetch, per data.gov.sg's own documented pattern:
  1. GET the dataset's poll-download endpoint -> returns a short-lived
     presigned S3 URL in a JSON envelope.
  2. GET that URL -> the actual GEOJSON body.

This script is meant to run in CI (see .github/workflows/refresh-dengue.yml,
on a schedule), where step 2 works fine with plain `requests`. It can also be
run by hand: `python3 scripts/refresh_dengue.py`.
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import requests

DATASET_ID = "d_dbfabf16158d1b0e1c420627c0819168"
POLL_URL = f"https://api-open.data.gov.sg/v1/public/api/datasets/{DATASET_ID}/poll-download"
HTML_PATH = Path(__file__).resolve().parent.parent / "FloodWatch-SG.html"


def fetch_geojson():
    meta = requests.get(POLL_URL, timeout=30).json()
    if meta.get("code") != 0:
        raise RuntimeError(f"poll-download failed: {meta.get('errorMsg')}")
    file_url = meta["data"]["url"]
    resp = requests.get(file_url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def build_clusters(geojson):
    clusters = []
    latest = None
    for feat in geojson["features"]:
        props = feat["properties"]
        geom = feat["geometry"]
        # NEA's file has been one simple Polygon ring per feature so far;
        # handle MultiPolygon defensively in case that ever changes.
        if geom["type"] == "Polygon":
            outer_rings = [geom["coordinates"][0]]
        elif geom["type"] == "MultiPolygon":
            outer_rings = [poly[0] for poly in geom["coordinates"]]
        else:
            continue
        ring = [[round(lon, 5), round(lat, 5)] for lon, lat in outer_rings[0]]
        locality = (props.get("LOCALITY") or "").strip()
        cases = props.get("CASE_SIZE")
        clusters.append({"locality": locality, "cases": cases, "ring": ring})

        upd = props.get("FMEL_UPD_D")
        if upd and (latest is None or str(upd) > str(latest)):
            latest = str(upd)
    return clusters, latest


def format_label(fmel_upd_d):
    # FMEL_UPD_D looks like "20260904150617" -> "4 Sep 2026"
    dt = datetime.strptime(fmel_upd_d[:8], "%Y%m%d")
    return f"{dt.day} {dt.strftime('%b %Y')}"


def splice(html, clusters, label):
    array_json = json.dumps(clusters, separators=(",", ":"))

    new_html, n = re.subn(
        r'const DENGUE_CLUSTERS = \[.*\];$',
        f"const DENGUE_CLUSTERS = {array_json};",
        html, count=1, flags=re.MULTILINE,
    )
    if n != 1:
        raise RuntimeError("Could not find the DENGUE_CLUSTERS array to replace")

    # Three date mentions specific to the dengue dataset. Anchored on text
    # unique to each spot so this can't accidentally touch the unrelated
    # PUB flood-alerts-spec date or the ECDA snapshot date elsewhere in the
    # same file.
    patterns = [
        (r'(Snapshot taken )\d{1,2} \w{3} \d{4}( from the "Dengue Clusters)', "intro comment"),
        (r'(NEA, snapshot )\d{1,2} \w{3} \d{4}(\))', "map popup"),
        (r'(NEA — Dengue Clusters GEOJSON, via data\.gov\.sg, snapshot )\d{1,2} \w{3} \d{4}', "footer credit"),
    ]
    for pattern, name in patterns:
        new_html, n = re.subn(pattern, rf"\g<1>{label}\g<2>" if pattern.count("(") > 1 else rf"\g<1>{label}", new_html, count=1)
        if n != 1:
            print(f"Warning: '{name}' date label not updated (pattern not found)", file=sys.stderr)

    return new_html


def main():
    geojson = fetch_geojson()
    clusters, latest = build_clusters(geojson)
    if not clusters:
        print("No clusters parsed from the feed — refusing to overwrite.", file=sys.stderr)
        sys.exit(1)

    label = format_label(latest) if latest else datetime.utcnow().strftime("%-d %b %Y")
    html = HTML_PATH.read_text(encoding="utf-8")
    new_html = splice(html, clusters, label)

    if new_html == html:
        print(f"No change — already current ({label}, {len(clusters)} clusters).")
        return

    HTML_PATH.write_text(new_html, encoding="utf-8")
    print(f"Updated DENGUE_CLUSTERS: {len(clusters)} clusters, snapshot label '{label}'.")


if __name__ == "__main__":
    main()
