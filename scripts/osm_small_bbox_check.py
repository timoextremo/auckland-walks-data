#!/usr/bin/env python3
"""One-shot read-only OSM API map/ways vs historical LINZ Topo50 KMLs.

OpenStreetMap contributors ODbL; geometry may be stale or wrong. NEVER use
crowdsourced highway=path to assert public legal access or current opening.
Does not fetch or redistribute Auckland Council Akl Paths restricted data.
"""
import argparse,json,pathlib,time,xml.etree.ElementTree as ET
from datetime import datetime,timezone
import requests
from pyproj import Transformer
from shapely.geometry import LineString,box
from shapely.ops import transform,unary_union
TX=Transformer.from_crs(4326,2193,always_xy=True).transform
HIGHWAY={"path","footway","track","steps","pedestrian","bridleway","cycleway"}
URL="https://api.openstreetmap.org/api/0.6/map"
def load_old(row):
    root=ET.parse(f"map-data/auckland-walks/clusters/row-{row}.kml").getroot()
    pts=[];ls=[]
    for node in root.iter():
        if node.tag.split("}")[-1]!="LineString":continue
        coord=next((p for p in node.iter() if p.tag.split("}")[-1]=="coordinates"),None)
        if coord is None or not coord.text:continue
        part=[tuple(map(float,s.split(",")[:2])) for s in coord.text.strip().split()]
        if len(part)>1:pts.extend(part);ls.append(transform(TX,LineString(part)))
    if not ls:raise RuntimeError("No LINE KML")
    return unary_union(ls),(min(p[0] for p in pts),min(p[1] for p in pts),max(p[0] for p in pts),max(p[1] for p in pts))
def fetch_osm(bbox):
    w,s,e,n=bbox
    # Large inner-city extracts can trigger the OSM API's per-map node limit.
    # For a large bbox split into small tiles without Overpass or broad query.
    nx=3 if e-w>.022 else 1
    ny=2 if n-s>.007 else 1
    session=requests.Session()
    session.headers["User-Agent"]="AucklandWalks/1.0 (hobby noncommercial historical trail validation, bounded map requests)"
    nodes={};elements={};nbytes=0;tiles=0
    for i in range(nx):
        for j in range(ny):
            pad=.00025
            bb=(w+(e-w)*i/nx-pad,s+(n-s)*j/ny-pad,
                w+(e-w)*(i+1)/nx+pad,s+(n-s)*(j+1)/ny+pad)
            params={"bbox":",".join(f"{v:.7f}" for v in bb)}
            resp=session.get(URL,params=params,timeout=55)
            resp.raise_for_status()
            if len(resp.content)>13000000:raise RuntimeError("OSM tile response too large")
            root=ET.fromstring(resp.content)
            nbytes+=len(resp.content);tiles+=1
            for node in root.findall("node"):
                try:nodes[node.attrib["id"]]=(float(node.attrib["lon"]),float(node.attrib["lat"]))
                except KeyError:pass
            for way in root.findall("way"):
                elements[way.attrib["id"]]=way
            time.sleep(.22)
    ways=[]
    for key,way in elements.items():
        tags={t.attrib["k"]:t.attrib["v"] for t in way.findall("tag")}
        if tags.get("highway") not in HIGHWAY:continue
        xy=[nodes[n.attrib["ref"]] for n in way.findall("nd") if n.attrib["ref"] in nodes]
        if len(xy)>1:
            ways.append((key,tags,transform(TX,LineString(xy))))
    return ways,{"api":URL,"nodes":len(nodes),"ways_path":len(ways),"bytes":nbytes,"tiles":tiles}

def analyze(row):
    old,bbox=load_old(row)
    ways,meta=fetch_osm(bbox)
    lines=[w[2] for w in ways]
    near=[]
    for id,t,g in ways:
        dist=g.distance(old)
        if dist<=55:
            near.append({"way":id,"name":t.get("name"),"highway":t.get("highway"),"access":t.get("access"),
                "foot":t.get("foot"),"surface":t.get("surface"),"operator":t.get("operator"),
                "nearest_m":round(dist,1),"within_20m_length_m":round(old.intersection(g.buffer(20)).length,1)})
    near.sort(key=lambda x:-x["within_20m_length_m"])
    if lines:
        union=unary_union(lines)
        covers={str(m):round(old.intersection(union.buffer(m)).length/old.length*100,1) for m in (5,10,20)}
        dist=round(old.distance(union),1)
    else:
        covers={"5":0,"10":0,"20":0};dist=None
    return {"source_row":row,"old_m":round(old.length,1),**meta,"coverage_pct":covers,"nearest_m":dist,
        "nearby_ways":near[:18],"checked_utc":datetime.now(timezone.utc).isoformat(),
        "interpretation":"OSM mapped walkway only; this does NOT prove lawful access, safety, current signage or any route completion."}
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--rows",required=True)
    p.add_argument("--name",default="pilot")
    a=p.parse_args()
    ids=[int(q.strip()) for q in a.rows.split(",") if q.strip()]
    if len(ids)>4:raise RuntimeError("Max four routes in one short job")
    results=[];fail=[]
    for row in ids:
        try:
            r=analyze(row);results.append(r)
            print("MAP_RESULT="+json.dumps(r,separators=(",",":")),flush=True)
        except Exception as e:
            fail.append({"source_row":row,"error":type(e).__name__+": "+str(e)})
            print("MAP_ERROR="+json.dumps(fail[-1]),flush=True)
    pathlib.Path("local_osm_"+a.name+".json").write_text(json.dumps({"results":results,"failures":fail},indent=2),encoding="utf-8")
    if fail:raise SystemExit("One or more OSM small-bbox API calls failed; no negative inference.")
if __name__=="__main__":main()
