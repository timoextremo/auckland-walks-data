#!/usr/bin/env python3
"""Read-only historical LINZ KML vs DOC *mapped* open managed track centreline pilot.

This is GEOMETRY RECONCILIATION ONLY. It does not establish legal entry, track
condition, current closures, permission, ownership, or user completion.
"""
import argparse
import json
import pathlib
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests
from pyproj import Transformer
from shapely.geometry import LineString
from shapely.ops import transform, unary_union

SERVICE = "https://mapserver.doc.govt.nz/arcgis/rest/services/DOCMaps/DOCMaps/MapServer/4"
FIELDS = "OBJECTID,DESCRIPTION,STATUS,CATEGORY_DESCRIPTION,OBJECT_TYPE_DESCRIPTION"
REPO_DIR = pathlib.Path("map-data/auckland-walks/clusters")
TO_NZTM = Transformer.from_crs("EPSG:4326", "EPSG:2193", always_xy=True).transform

def load_kml(row):
    path = REPO_DIR / f"row-{row}.kml"
    root = ET.parse(path).getroot()
    lines = []
    for node in root.iter():
        if not node.tag.endswith("LineString"):
            continue
        coord = next((c for c in node.iter() if c.tag.endswith("coordinates")), None)
        if coord is None or not coord.text:
            continue
        pts = [tuple(map(float, token.split(",")[:2])) for token in coord.text.split()]
        if len(pts) > 1:
            lines.append(LineString(pts))
    if not lines:
        raise ValueError(f"No LineString in {path}")
    projected = [transform(TO_NZTM, line) for line in lines]
    return unary_union(projected), path.as_posix()

def fetch_doc_tracks(route):
    xmin, ymin, xmax, ymax = route.bounds
    # Geometry envelope is in metres; 120m expands selection beyond 30m match radius.
    margin = 120
    geom = ",".join(f"{n:.1f}" for n in (xmin-margin, ymin-margin, xmax+margin, ymax+margin))
    params = {
        "where": "1=1",
        "geometry": geom,
        "geometryType": "esriGeometryEnvelope",
        "inSR": 2193,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": FIELDS,
        "returnGeometry": "true",
        "outSR": 2193,
        "f": "json",
        "resultRecordCount": 1000,
        "resultOffset": 0,
    }
    all_features = []
    session = requests.Session()
    session.headers["User-Agent"] = "auckland-walks-doc-kml-match/1.0 (read-only)"
    while True:
        for attempt in range(4):
            try:
                response = session.get(SERVICE + "/query", params=params, timeout=65)
                response.raise_for_status()
                payload = response.json()
                if "error" in payload:
                    raise RuntimeError(str(payload["error"]))
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(2 * (attempt + 1))
        features = payload.get("features", [])
        all_features.extend(features)
        if len(all_features) > 10000:
            raise RuntimeError("Exceeded safe DOC feature limit; narrow geographic search")
        if not payload.get("exceededTransferLimit"):
            break
        if not features:
            raise RuntimeError("DOC service reported more results but yielded no features")
        params["resultOffset"] += len(features)
    return all_features

def feature_lines(feat):
    result = []
    for path in feat.get("geometry", {}).get("paths", []):
        if len(path) >= 2:
            result.append(LineString([p[:2] for p in path]))
    return result

def match(row):
    route, kml = load_kml(row)
    length = route.length
    feats = fetch_doc_tracks(route)
    all_lines = []
    near_features = []
    for feat in feats:
        lines = feature_lines(feat)
        if not lines:
            continue
        geom = unary_union(lines)
        all_lines.extend(lines)
        distance = geom.distance(route)
        if distance <= 50:
            props = feat.get("attributes", {})
            near_features.append({
                "OBJECTID": props.get("OBJECTID"),
                "name": props.get("DESCRIPTION"),
                "status": props.get("STATUS"),
                "category": props.get("CATEGORY_DESCRIPTION"),
                "type": props.get("OBJECT_TYPE_DESCRIPTION"),
                "nearest_m": round(distance, 1),
                "match_within_20m_m": round(route.intersection(geom.buffer(20)).length, 1),
            })
    if all_lines:
        active_doc = unary_union(all_lines)
        ratios = {f"within_{dist}m_pct": round(
            min(100, route.intersection(active_doc.buffer(dist)).length / length * 100), 1)
            for dist in [10, 20, 30]}
        closest = round(route.distance(active_doc), 1)
    else:
        ratios = {f"within_{dist}m_pct": 0.0 for dist in [10, 20, 30]}
        closest = None
    near_features.sort(key=lambda x: (-x["match_within_20m_m"], x["nearest_m"]))
    return {
        "Sheet1_row": row, "source_kml": kml, "historic_route_m": round(length, 1),
        "doc_layer": SERVICE, "source_description": "DOC managed track centrelines (closed tracks not shown)",
        "queried_utc": datetime.now(timezone.utc).isoformat(),
        "total_doc_features_in_envelope": len(feats), "doc_nearest_m": closest,
        **ratios, "nearby_doc_features": near_features[:20],
        "caution": "Match within metric buffers is spatial evidence only; no legal access/closure/current condition conclusion."
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", required=True, help="comma-separated Sheet1 rows, <=7 per call")
    parser.add_argument("--name", default="pilot")
    args = parser.parse_args()
    rows = [int(t.strip()) for t in args.rows.split(",") if t.strip()]
    if not (1 <= len(rows) <= 7):
        raise SystemExit("Use 1-7 rows per GitHub job")
    results = []
    errors = []
    for row in rows:
        try:
            result = match(row)
            results.append(result)
            print(json.dumps({k: result[k] for k in ["Sheet1_row", "historic_route_m", "total_doc_features_in_envelope", "doc_nearest_m", "within_10m_pct", "within_20m_pct", "within_30m_pct"]}), flush=True)
        except Exception as exc:
            errors.append({"Sheet1_row": row, "error": f"{type(exc).__name__}: {exc}"})
            print(f"FAILED Sheet1 row {row}: {exc}", flush=True)
    pathlib.Path("doc_match_" + args.name + ".json").write_text(
        json.dumps({"results": results, "errors": errors}, indent=2), encoding="utf-8")
    if errors:
        raise SystemExit(f"{len(errors)} route(s) failed - see artifact")

if __name__ == "__main__":
    main()
