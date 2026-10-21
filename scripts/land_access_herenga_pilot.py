#!/usr/bin/env python3
"""Overlay the first unnamed LINZ foot tracks with Herenga ā Nuku WAMS access polygons.
No claim of walkability, unimpeded entry, current opening or ownership is made.
Only exact LineString ∩ public access polygons are measured. No distance buffer.
"""
import argparse, json, pathlib, sys, time
from collections import defaultdict
import requests
from shapely.ops import unary_union, transform
from land_access_primary_parcels import lines_for, poly_from_rings, WGS_TO_NZTM

BASE="https://services2.arcgis.com/b5ADKIcWivL5vNaV/ArcGIS/rest/services/Public_Access_Areas/FeatureServer"
LAYERS={1:"Road Parcels",2:"Easements",3:"Reserve Land",4:"Public Access Conservation Land",5:"Other Parks & Reserves",6:"Other Public Access Areas"}
S=requests.Session()
S.headers["User-Agent"]="auckland-walks-data/1.0 (public access spatial audit)"
def request_features(layer,bounds):
    # Each query is bounded to the line's WGS84 extent. Includes 35 m safety envelope to
    # recover near-edge polygons; intersection is always calculated on the EXACT line.
    b=bounds; geom={"xmin":b[0]-0.0004,"ymin":b[1]-0.0004,"xmax":b[2]+0.0004,"ymax":b[3]+0.0004,
                   "spatialReference":{"wkid":4326}}
    args={"where":"1=1","geometry":json.dumps(geom,separators=(",",":")),
          "geometryType":"esriGeometryEnvelope","inSR":"4326","spatialRel":"esriSpatialRelIntersects",
          "outFields":"*","returnGeometry":"true","outSR":"2193","f":"json",
          "resultOffset":0,"resultRecordCount":1000}
    found=[]
    while True:
        last=None
        for attempt in range(4):
            try:
                resp=S.post(f"{BASE}/{layer}/query",data=args,timeout=75)
                resp.raise_for_status()
                body=resp.json()
                if "error" in body: raise RuntimeError(str(body["error"]))
                break
            except Exception as exc:
                last=exc; time.sleep((attempt+1)*2)
        else: raise RuntimeError(f"WAMS layer {layer}: {last}")
        batch=body.get("features",[])
        found.extend(batch)
        if not body.get("exceededTransferLimit") or not batch:break
        args["resultOffset"]+=len(batch)
        if len(found)>18000:raise RuntimeError(f"Too many features for layer {layer} within route bounds")
    return found
def run_row(row):
    raw=lines_for(row)
    if not raw: raise RuntimeError(f"No KML line segments for row {row}")
    vertices=[p for line in raw for p in line.coords]
    bounds=(min(p[0] for p in vertices),min(p[1] for p in vertices),
            max(p[0] for p in vertices),max(p[1] for p in vertices))
    line=unary_union([transform(WGS_TO_NZTM,ln) for ln in raw])
    meters=line.length
    overall=[]
    all_layers=[]
    categories=defaultdict(list)
    all_features=0
    for index,label in LAYERS.items():
        features=request_features(index,bounds)
        all_features+=len(features)
        clipped=[]
        sample=[]
        values=defaultdict(float)
        for f in features:
            a=f.get("attributes",{})
            p=poly_from_rings(f.get("geometry",{}).get("rings",[]))
            if p is None or p.is_empty or not p.intersects(line): continue
            section=line.intersection(p)
            if section.is_empty or section.length < .5: continue
            m=section.length
            clipped.append(section)
            status=str(a.get("public_access") or "missing").lower()
            values[status]+=m
            sample.append({"id":a.get("id"),"objectid":a.get("OBJECTID"),
               "type":a.get("paa_type"),"public_access":status,
               "managed_by":a.get("managed_by"),"name":a.get("common_name"),
               "source":a.get("source_dataset"),"metres":round(m,1)})
            if status=="yes": categories["mapped_yes"].append(section)
            elif status in ["maybe","conditional"]:categories["mapped_uncertain"].append(section)
        unique=unary_union(clipped).length if clipped else 0
        if clipped:overall.extend(clipped)
        sample.sort(key=lambda x:-x["metres"])
        all_layers.append({"layer":index,"category":label,"features_in_bbox":len(features),
            "intersecting_features":len(sample),
            "covered_m":round(unique,1),
            "covered_pct":round(100*min(unique,meters)/meters,1),
            "public_access_values_m":{k:round(v,1) for k,v in values.items()},
            "examples":sample[:7]})
    mapped=unary_union(overall).length if overall else 0
    yes=unary_union(categories["mapped_yes"]).length if categories["mapped_yes"] else 0
    uncertain=unary_union(categories["mapped_uncertain"]).length if categories["mapped_uncertain"] else 0
    return {"row":row,"track_m":round(meters,1),"source":BASE,
      "total_features_returned":all_features,
      "mapped_pct":round(100*min(mapped,meters)/meters,1),
      "mapped_m":round(mapped,1),"not_mapped_m":round(max(0,meters-mapped),1),
      "mapped_yes_pct":round(100*min(yes,meters)/meters,1),
      "mapped_uncertain_pct":round(100*min(uncertain,meters)/meters,1),
      "layers":all_layers,
      "interpretation":"Mapped public access evidence only; does not certify walkability, route accessibility, restrictions or land ownership"}
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--rows",required=True,help="Comma-separated Sheet1 source rows")
    args=ap.parse_args()
    rows=[int(x) for x in args.rows.split(",") if x]
    completed=[]
    for row in rows:
        try:
            result=run_row(row)
            print("WAMS_JSON="+json.dumps(result,separators=(",",":")),flush=True)
            completed.append(result)
        except Exception as exc:
            print("WAMS_ERROR="+json.dumps({"row":row,"error":str(exc)}),flush=True)
    outfile=pathlib.Path("wams-batch-"+str(rows[0])+".json")
    outfile.write_text(json.dumps(completed,indent=2),encoding="utf-8")
    print("WAMS_SUMMARY="+json.dumps({"attempted":len(rows),"completed":len(completed),"errors":len(rows)-len(completed),"output":str(outfile)}),flush=True)
    if len(completed)!=len(rows):sys.exit(1)
if __name__=="__main__":main()
