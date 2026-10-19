#!/usr/bin/env python3
"""LINZ primary-parcel overlay against the repository's unmapped walking-route KML files.
Uses the public LINZ ArcGIS mirror (same source layer as the user's GeoPackage).
This reports parcel intent and descriptions, NOT legal ownership or a public-access right.
"""
import argparse, json, math, pathlib, sys, time
import xml.etree.ElementTree as ET
from collections import defaultdict
import requests
from pyproj import Transformer
from shapely.geometry import LineString, Polygon
from shapely.ops import transform, unary_union

SERVICE = "https://services.arcgis.com/xdsHIIxuCWByZiCB/arcgis/rest/services/LINZ_NZ_Primary_Parcels/FeatureServer/0/query"
DATASET = "https://data.linz.govt.nz/layer/50772-nz-primary-parcels/"
SOURCE = "https://services.arcgis.com/xdsHIIxuCWByZiCB/arcgis/rest/services/LINZ_NZ_Primary_Parcels/FeatureServer/0"
WGS_TO_NZTM = Transformer.from_crs("EPSG:4326", "EPSG:2193", always_xy=True).transform
SESS = requests.Session()
SESS.headers["User-Agent"] = "auckland-walks-land-access-audit/0.1 (public GIS verification)"

def lines_for(row):
    path = pathlib.Path("map-data/auckland-walks/clusters") / f"row-{row}.kml"
    root = ET.parse(path).getroot()
    result = []
    for x in root.iter():
        if not x.tag.endswith("LineString"):
            continue
        cs = next((e for e in x.iter() if e.tag.endswith("coordinates")), None)
        if cs is None or not cs.text:
            continue
        pairs = [tuple(map(float, p.split(",")[:2])) for p in cs.text.strip().split()]
        if len(pairs)>1:
            result.append(LineString(pairs))
    return result

def poly_from_rings(rings):
    # ArcGIS polygon format: clockwise exterior rings, counterclockwise holes.
    if not rings: return None
    def signed(ring):
        return sum((ring[j][0]*ring[(j+1)%len(ring)][1] - ring[(j+1)%len(ring)][0]*ring[j][1]) for j in range(len(ring)))/2
    exterior = [Polygon(r) for r in rings if len(r)>=4 and signed(r)<0]
    holes = [Polygon(r) for r in rings if len(r)>=4 and signed(r)>=0]
    if not exterior:
        exterior=[Polygon(r) for r in rings if len(r)>=4]
        holes=[]
    geom=unary_union([g if g.is_valid else g.buffer(0) for g in exterior])
    if holes:
        geom=geom.difference(unary_union([g for g in holes if g.is_valid]))
    return geom if geom.is_valid else geom.buffer(0)

def load_parcels(bounds):
    # Query a bounding envelope. The service returns NZTM2000 polygon vertices.
    x1,y1,x2,y2=bounds
    params={"where":"1=1","geometry":json.dumps({"xmin":x1-0.00025,"ymin":y1-0.00025,"xmax":x2+0.00025,"ymax":y2+0.00025,"spatialReference":{"wkid":4326}}),
        "geometryType":"esriGeometryEnvelope","inSR":4326,"spatialRel":"esriSpatialRelIntersects",
        "outFields":"id,appellation,parcel_intent,titles,statutory_actions",
        "returnGeometry":"true","outSR":2193,"f":"json",
        "resultOffset":0,"resultRecordCount":1000}
    result=[]
    while True:
        err=None
        for attempt in range(4):
            try:
                res=SESS.get(SERVICE,params=params,timeout=60)
                res.raise_for_status()
                body=res.json()
                if "error" in body: raise RuntimeError(str(body["error"]))
                break
            except Exception as e:
                err=e;time.sleep(2*(attempt+1))
        else:
            raise RuntimeError(f"ArcGIS parcel query failed: {err}")
        result.extend(body.get("features",[]))
        if not body.get("exceededTransferLimit") or not body.get("features"):
            break
        params["resultOffset"]+=len(body["features"])
        if len(result)>12000: raise RuntimeError("Excessive parcel features for small pilot bbox")
    return result

def audit(row):
    raw=lines_for(row)
    if not raw: raise RuntimeError("No LineString KML geometry")
    ll=[pt for ln in raw for pt in ln.coords]
    bounds=(min(p[0] for p in ll), min(p[1] for p in ll),max(p[0] for p in ll),max(p[1] for p in ll))
    route=unary_union([transform(WGS_TO_NZTM,l) for l in raw])
    length=route.length
    features=load_parcels(bounds)
    cuts=[]; stats=defaultdict(float); pieces=[]
    for p in features:
        a=p["attributes"];geom=poly_from_rings(p.get("geometry",{}).get("rings",[]))
        if geom is None or geom.is_empty or not geom.intersects(route):continue
        c=route.intersection(geom)
        if c.is_empty or c.length<0.5: continue
        m=c.length; cuts.append(c)
        intent=a.get("parcel_intent") or "(blank)"
        stats[intent]+=m
        pieces.append({"parcel_id":a.get("id"),"appellation":a.get("appellation"),"intent":intent,"metres":round(m,1),"pct":round(m/length*100,1),
                       "titles":(a.get("titles") or "")[:100]})
    pieces.sort(key=lambda x:-x["metres"])
    unique_len=unary_union(cuts).length if cuts else 0
    return {"row":row,"route_m":round(length,1),"parcel_match_pct":round(min(100,100*unique_len/length),1),
        "matched_m":round(unique_len,1),"parcels":len(pieces),"envelope_features":len(features),
        "intents":[{"intent":k,"metres":round(v,1),"pct":round(100*v/length,1)} for k,v in sorted(stats.items(),key=lambda x:-x[1])],
        "top_parcels":pieces[:12], "overlap_warning":sum(stats.values())>unique_len+2,
        "source":SOURCE, "dataset":DATASET}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--rows",default="811,812,813,814,815,816,818,819,820,821,822,823,824,825,826,827,828,829,830,831,832")
    args=ap.parse_args()
    rows=[int(s) for s in args.rows.split(",") if s]
    results=[]
    for row in rows:
        try:
            result=audit(row)
            results.append(result)
            print("PARCEL_JSON="+json.dumps(result,separators=(",",":")),flush=True)
        except Exception as e:
            print("PARCEL_ERROR="+json.dumps({"row":row,"error":str(e)}),flush=True)
    pathlib.Path("primary_parcel_pilot.json").write_text(json.dumps(results,indent=2))
    print("PARCEL_SUMMARY="+json.dumps({"requested":len(rows),"succeeded":len(results),"failed":len(rows)-len(results),"rows":[r["row"] for r in results]}),flush=True)
    if len(results)!=len(rows):sys.exit(1)
if __name__=="__main__": main()
