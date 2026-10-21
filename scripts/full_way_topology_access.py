#!/usr/bin/env python3
"""Read-only full OSM way topology and WAMS legal-corridor intersection audit.

This is an evidential test, NOT a legal access or public walking recommendation.
Use actual common OSM node IDs on un-clipped ways for graph connectivity.
A line crossing without a common OSM node is not considered connected.
"""
import argparse
import json
import pathlib
import time
import xml.etree.ElementTree as ET
from collections import defaultdict

import requests
from shapely.geometry import LineString
from shapely.ops import unary_union, transform
from land_access_primary_parcels import lines_for, poly_from_rings, WGS_TO_NZTM
from land_access_herenga_pilot import LAYERS, request_features

OSM_URL = "https://api.openstreetmap.org/api/0.6/map"
GOOD_HIGHWAYS = {"footway","path","pedestrian","steps","track","bridleway","cycleway"}
ACCESS_DENIED = {"private","no","agricultural","forestry"}
PERMITTED_CYCLE_FOOT = {"yes","designated","permissive","official"}

def load_full_osm(bbox):
    west,south,east,north = bbox
    pad=.00045
    bounds=[west-pad,south-pad,east+pad,north+pad]
    response=requests.get(
        OSM_URL,
        params={"bbox":",".join(f"{v:.7f}" for v in bounds)},
        headers={"User-Agent":"AucklandWalks/1.0 (bounded read-only topology validation)"},
        timeout=70
    )
    response.raise_for_status()
    if len(response.content)>15000000:
        raise RuntimeError("OSM area response too large; no negative inference")
    root=ET.fromstring(response.content)
    nodes={}
    for n in root.findall("node"):
        try: nodes[n.attrib["id"]]=(float(n.attrib["lon"]),float(n.attrib["lat"]))
        except (KeyError,ValueError): pass
    full=[]
    for w in root.findall("way"):
        tags={x.attrib["k"]:x.attrib["v"] for x in w.findall("tag")}
        highway=tags.get("highway","")
        if highway not in GOOD_HIGHWAYS: continue
        foot=tags.get("foot","").lower()
        access=tags.get("access","").lower()
        if foot in ACCESS_DENIED or access in {"no","private"}:continue
        if highway=="cycleway" and foot not in PERMITTED_CYCLE_FOOT:continue
        node_ids=[nd.attrib["ref"] for nd in w.findall("nd")]
        present=[(nodeid,nodes[nodeid]) for nodeid in node_ids if nodeid in nodes]
        if len(present)<2:continue
        line=transform(WGS_TO_NZTM,LineString([xy for _,xy in present]))
        full.append({"way":w.attrib["id"],"tags":tags,"nodes":set(nodeid for nodeid,_ in present),
                     "geometry":line,"truncated_nodes":len(node_ids)-len(present)})
    return full,{"url":OSM_URL,"nodes":len(nodes),"eligible_ways":len(full),
                 "response_bytes":len(response.content)}

def grouped(full,old):
    corridor=old.buffer(15)
    nearby=[w for w in full if w["geometry"].distance(old)<=15]
    # Preserve full un-clipped ways, including internal shared OSM node references.
    parent=list(range(len(nearby)))
    def root(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]]
            i=parent[i]
        return i
    def join(a,b):
        a,b=root(a),root(b)
        if a!=b:parent[max(a,b)]=min(a,b)
    owner={}
    for i,w in enumerate(nearby):
        for node in w["nodes"]:
            if node in owner:join(i,owner[node])
            else:owner[node]=i
    groups=defaultdict(list)
    for i,w in enumerate(nearby):groups[root(i)].append(w)
    result=[]
    for g in groups.values():
        modern=unary_union([x["geometry"] for x in g])
        # Full way topology for connection, corridor-only length for coverage.
        portion=modern.intersection(corridor)
        if portion.is_empty or portion.length<1:continue
        overlap=old.intersection(modern.buffer(10)).length
        result.append({"ways":[x["way"] for x in g], "node_truncations":sum(x["truncated_nodes"] for x in g),
                       "part_count":len(g),"full_total_m":round(sum(x["geometry"].length for x in g),1),
                       "corridor_m":round(portion.length,1),
                       "old_within10m_pct":round(100*overlap/old.length,1),
                       "_portion":portion,"_modern":modern})
    result.sort(key=lambda x:(-x["old_within10m_pct"],-x["corridor_m"]))
    return result,len(nearby)

def wams_for(bbox,old):
    polys=defaultdict(list)
    metadata=defaultdict(list)
    for layer,label in LAYERS.items():
        for f in request_features(layer,bbox):
            attr=f.get("attributes",{})
            poly=poly_from_rings(f.get("geometry",{}).get("rings",[]))
            if poly is None or poly.is_empty or not poly.intersects(old.buffer(30)):continue
            flag=str(attr.get("public_access") or "missing").lower()
            if flag=="yes":polys[label].append(poly)
            metadata[label].append({"kind":attr.get("paa_type"),"access":flag,"name":attr.get("common_name"),
                                   "managed_by":attr.get("managed_by"),"objectid":attr.get("OBJECTID")})
    merged={key:unary_union(g) for key,g in polys.items() if g}
    yes=unary_union(list(merged.values())) if merged else None
    return merged,yes,{k:v[:8] for k,v in metadata.items()}

def run_one(row):
    source=lines_for(row)
    if not source:raise ValueError("No LINZ historical line")
    vertices=[xy for ln in source for xy in ln.coords]
    bbox=(min(x for x,y in vertices),min(y for x,y in vertices),max(x for x,y in vertices),max(y for x,y in vertices))
    old=unary_union([transform(WGS_TO_NZTM,ln) for ln in source])
    full,osm_meta=load_full_osm(bbox)
    groups,count=grouped(full,old)
    polygons,yes,layer_sources=wams_for(bbox,old)
    for g in groups:
        p=g.pop("_portion")
        g.pop("_modern")
        g["mapped_yes_pct"]=round(100*p.intersection(yes).length/p.length,1) if yes is not None else 0.
        g["mapped_by_public_access_layer_pct"]={k:round(100*p.intersection(poly).length/p.length,1) for k,poly in polygons.items() if p.intersects(poly)}
        g["geometry_correspondence_candidate"]=bool(g["old_within10m_pct"]>=75 and
                g["mapped_yes_pct"]>=90 and g["corridor_m"]>=old.length*.5)
    return {"row":row,"historical_m":round(old.length,1),"historical_bbox":bbox,
            "osm_fetch":osm_meta,"nearby_full_ways":count,"full_node_connected_groups":len(groups),
            "best_full_way_groups":groups[:8],"wams_sources":layer_sources,
            "interpretation":"OSM full-way shared-node geometry plus Herenga a Nuku public-access-area overlap only. Does not certify current formed route, unrestricted legal public pedestrian access, entrances, restrictions, or personal walk completion."}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--rows",required=True)
    p.add_argument("--name",default="full-ways")
    a=p.parse_args()
    rows=[int(v) for v in a.rows.split(",") if v.strip()]
    if len(rows)>4:raise ValueError("Max four bounded source rows per job")
    successes=[];failures=[]
    for row in rows:
        try:
            d=run_one(row)
            successes.append(d);print("FULL_WAY_JSON="+json.dumps(d,separators=(",",":")),flush=True)
        except Exception as ex:
            d={"row":row,"error":type(ex).__name__+": "+str(ex)}
            failures.append(d);print("FULL_WAY_ERROR="+json.dumps(d),flush=True)
    path=pathlib.Path("full_way_"+a.name+".json")
    path.write_text(json.dumps({"results":successes,"errors":failures},indent=2),encoding="utf-8")
    print("FULL_WAY_SUMMARY="+json.dumps({"requested":len(rows),"completed":len(successes),"errors":len(failures)}),flush=True)
    if failures:raise SystemExit(1)

if __name__=="__main__":
    main()
