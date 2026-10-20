#!/usr/bin/env python3
"""Locate *exact* missing WAMS access-overlay segments for historic LINZ lines.

Not a legal-access determination. Access polygon GIS may be incomplete; a
mapped legal road can be unformed; parcels show geometry/intent, not ownership.
The output is a targeted desk-review queue, never a walking recommendation.
"""
import argparse
import json
import pathlib
import sys
from shapely.geometry import LineString
from shapely.ops import transform, unary_union
from pyproj import Transformer
from land_access_herenga_pilot import request_features, LAYERS
from land_access_primary_parcels import lines_for, poly_from_rings, load_parcels, WGS_TO_NZTM

BACK_TO_WGS = Transformer.from_crs("EPSG:2193","EPSG:4326",always_xy=True).transform

def lines(g):
    if g.is_empty: return []
    if g.geom_type == "LineString": return [g]
    if hasattr(g,"geoms"):
        return [line for part in g.geoms for line in lines(part)]
    return []

def bounds_of(raw):
    pts=[v for l in raw for v in l.coords]
    return (min(x for x,y in pts),min(y for x,y in pts),max(x for x,y in pts),max(y for x,y in pts))

def check(row):
    raw=lines_for(row)
    if not raw: raise RuntimeError("Missing historical KML lines")
    b=bounds_of(raw)
    route=unary_union([transform(WGS_TO_NZTM,l) for l in raw])
    yes=[];not_yes=[];layer_details=[];errors=[]
    for n,label in LAYERS.items():
        fs=request_features(n,b)
        for f in fs:
            geom=poly_from_rings(f.get("geometry",{}).get("rings",[]))
            if geom is None or geom.is_empty or not geom.intersects(route): continue
            section=route.intersection(geom)
            if section.length < .02: continue
            props=f.get("attributes",{})
            typ=str(props.get("public_access") or "missing").lower()
            item={"layer":label,"access":typ,"objectid":props.get("OBJECTID"),"name":props.get("common_name"),"type":props.get("paa_type"),"covered_m":round(section.length,2)}
            if typ=="yes": yes.append(section)
            else: not_yes.append((geom,item))
            layer_details.append(item)
    mapped=unary_union(yes) if yes else None
    missing=route.difference(mapped) if mapped else route
    parcels=load_parcels(b)
    parsed=[]
    for f in parcels:
        poly=poly_from_rings(f.get("geometry",{}).get("rings",[]))
        if poly is None or poly.is_empty: continue
        parsed.append((poly,f.get("attributes",{})))
    segments=[]
    for line in lines(missing):
        if line.length<.5: continue
        mid=line.interpolate(.5,normalized=True)
        a=transform(BACK_TO_WGS,mid)
        end0=transform(BACK_TO_WGS,LineString([line.coords[0],line.coords[0]]))
        end1=transform(BACK_TO_WGS,LineString([line.coords[-1],line.coords[-1]]))
        near=[]
        for polygon,attr in parsed:
            if polygon.distance(mid)>.75: continue
            intersection=line.intersection(polygon).length
            if intersection<.05:continue
            near.append({"id":attr.get("id"),"intent":attr.get("parcel_intent"),"appellation":attr.get("appellation"),"titles":attr.get("titles"),"gap_overlap_m":round(intersection,1)})
        near.sort(key=lambda x:-x["gap_overlap_m"])
        nonyes=[p for poly,p in not_yes if poly.distance(mid)<.75]
        segments.append({"missing_m":round(line.length,2),"mid_lon":round(a.x,7),"mid_lat":round(a.y,7),
           "endpoints_wgs84":[[round(end0.coords[0][0],7),round(end0.coords[0][1],7)],
                                [round(end1.coords[0][0],7),round(end1.coords[0][1],7)]],
           "parcel_intersections":near[:8],"non_yes_wams":nonyes[:6]})
    segments.sort(key=lambda x:-x["missing_m"])
    return {"row":row,"old_m":round(route.length,2),
            "yes_m":round(route.intersection(mapped).length if mapped else 0,2),
            "yes_pct":round(100*(route.intersection(mapped).length if mapped else 0)/route.length,2),
            "unmapped_segments":segments,"gap_count":len(segments),
            "unmapped_total_m":round(sum(x["missing_m"] for x in segments),2),
            "mapped_layer_examples":layer_details[:12],
            "caution":"Missing WAMS overlay not proof of private land; parcel geometry and intent not proof of right to walk."}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--rows",required=True)
    p.add_argument("--name",default="pilot")
    args=p.parse_args()
    result=[];errors=[]
    rows=[int(x) for x in args.rows.split(",") if x]
    for r in rows:
        try:
            obj=check(r)
            print("GAP_DETAIL_JSON="+json.dumps(obj,separators=(",",":")),flush=True)
            result.append(obj)
        except Exception as e:
            errors.append({"row":r,"error":str(e)})
            print("GAP_ERROR="+json.dumps(errors[-1]),flush=True)
    pathlib.Path(f"wams_access_gap_{args.name}.json").write_text(json.dumps({"results":result,"errors":errors},indent=2),encoding="utf-8")
    if errors or len(result)!=len(rows):sys.exit(1)

if __name__=="__main__":main()
