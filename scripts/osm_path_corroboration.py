#!/usr/bin/env python3
"""Read-only OSM path/track corroboration for old LINZ historical foot tracks.

OSM is volunteered cartography; NOT verified land rights, permissions, closures,
or authoritative physical condition. Positive geometry is supporting evidence only.
"""
import argparse, json, pathlib, sys, time, xml.etree.ElementTree as ET
from datetime import datetime,timezone
import requests
from pyproj import Transformer
from shapely.geometry import LineString, box
from shapely.ops import transform, unary_union

API=["https://overpass.kumi.systems/api/interpreter","https://overpass.private.coffee/api/interpreter","https://overpass.nchc.org.tw/api/interpreter"]
TO_NZTM=Transformer.from_crs("EPSG:4326","EPSG:2193",always_xy=True).transform
def old_kml(row):
    root=ET.parse(pathlib.Path(f"map-data/auckland-walks/clusters/row-{row}.kml")).getroot()
    lines=[];points=[]
    for node in root.iter():
        if not node.tag.endswith("LineString"):continue
        c=next((a for a in node.iter() if a.tag.endswith("coordinates")),None)
        if c is None or not c.text:continue
        xy=[tuple(map(float,v.split(",")[:2])) for v in c.text.strip().split()]
        if len(xy)<2:continue
        points+=xy
        lines.append(transform(TO_NZTM,LineString(xy)))
    if not lines:raise RuntimeError(f"No KML line for {row}")
    return unary_union(lines), (min(p[0] for p in points),min(p[1] for p in points),
                                max(p[0] for p in points),max(p[1] for p in points))
def osm_ways(bbox):
    w,s,e,n=bbox
    padding=.002
    south=s-padding;west=w-padding;north=n+padding;east=e+padding
    query=f'[out:json][timeout:40];way["highway"~"^(footway|path|track|steps|pedestrian|bridleway)$"]({south:.7f},{west:.7f},{north:.7f},{east:.7f});out geom;'
    session=requests.Session()
    session.headers["User-Agent"]="auckland-walks-route-check/0.1 (noncommercial OSM corroboration)"
    errs=[]
    for host in API:
        try:
            resp=session.post(host,data={"data":query},timeout=60)
            resp.raise_for_status()
            payload=resp.json()
            if "elements" not in payload:raise RuntimeError(str(payload)[:250])
            return host,payload["elements"]
        except Exception as exc:
            errs.append(f"{host}: {type(exc).__name__}: {exc}")
    raise RuntimeError("All OSM Overpass sources failed: "+ "; ".join(errs))
def analyze(row):
    route,bbox=old_kml(row)
    host,elements=osm_ways(bbox)
    paths=[];features=[]
    for elt in elements:
        if elt.get("type")!="way":continue
        pts=[(p.get("lon"),p.get("lat")) for p in elt.get("geometry",[]) if isinstance(p.get("lon"),(int,float)) and isinstance(p.get("lat"),(int,float))]
        if len(pts)<2:continue
        geom=transform(TO_NZTM,LineString(pts))
        tags=elt.get("tags",{})
        dist=route.distance(geom)
        paths.append(geom)
        if dist<=50:
            feats.append({"osm_way_id":elt.get("id"),"name":tags.get("name"),"highway":tags.get("highway"),
             "access":tags.get("access"),"foot":tags.get("foot"),"operator":tags.get("operator"),
             "surface":tags.get("surface"),"sac_scale":tags.get("sac_scale"),
             "distance_m":round(dist,1),
             "within20m_m":round(route.intersection(geom.buffer(20)).length,1)})
    if paths:
        union=unary_union(paths)
        coverage={f"within_{d}m_pct":round(100*route.intersection(union.buffer(d)).length/route.length,1) for d in (10,20,30)}
        near=round(route.distance(union),1)
    else:
        coverage={f"within_{d}m_pct":0 for d in (10,20,30)}
        near=None
    feats.sort(key=lambda x:-x["within20m_m"])
    return {"source_row":row,"historical_m":round(route.length,1),"overpass_host":host,
        "osm_ways_in_bbox":len(elements),"nearest_osm_path_m":near,**coverage,
        "nearest_features":feats[:20],"checked_utc":datetime.now(timezone.utc).isoformat(),
        "caution":"OpenStreetMap volunteered track data, NOT verified lawful access, current formation or condition."}
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--rows",required=True)
    p.add_argument("--name",default="pilot")
    a=p.parse_args()
    rows=[int(s) for s in a.rows.split(",") if s]
    if not 1<=len(rows)<=5:raise ValueError("Use 1 to 5 rows per job")
    results=[];errors=[]
    for row in rows:
        try:
            r=analyze(row)
            results.append(r)
            print("OSM_CORROBORATION="+json.dumps(r,separators=(",",":")),flush=True)
        except Exception as exc:
            errors.append({"source_row":row,"error":str(exc)})
            print("OSM_CORROBORATION_ERROR="+json.dumps(errors[-1]),flush=True)
    pathlib.Path("osm_path_"+a.name+".json").write_text(json.dumps({"results":results,"errors":errors},indent=2),encoding="utf-8")
    if errors:sys.exit(1)
if __name__=="__main__":main()
