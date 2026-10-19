#!/usr/bin/env python3
"""Pilot historical LINZ KML comparisons to Herenga a Nuku region tracks/trails.

Candidate regional services are tested; layer provenance and edits are recorded.
A spatial overlap is NOT proof of legal public access, track condition, or opening.
Dev/cache services may lag. Do not infer ownership or route completion.
"""
import argparse,json,pathlib,sys,time,xml.etree.ElementTree as ET
from datetime import datetime,timezone
import requests
from pyproj import Transformer
from shapely.geometry import LineString
from shapely.ops import transform,unary_union

ORG="https://services2.arcgis.com/b5ADKIcWivL5vNaV/ArcGIS/rest/services"
SOURCES=[ORG+"/Auckland/FeatureServer", ORG+"/Auckland_dev/FeatureServer"]
LAYERS={16:"Walking",20:"Shared Use",18:"Te Araroa"}
TO_NZTM=Transformer.from_crs(4326,2193,always_xy=True).transform
S=requests.Session()
S.headers["User-Agent"]="auckland-walks-tracks-trails-reconciliation/0.1"
def get_json(url,params=None,post=False):
    last=None
    for n in range(3):
        try:
            response=S.post(url,data=params,timeout=75) if post else S.get(url,params=params,timeout=45)
            response.raise_for_status()
            obj=response.json()
            if "error" in obj:raise RuntimeError(str(obj["error"]))
            return obj
        except Exception as e:
            last=e
            if n<2:time.sleep(n+1)
    raise last
def resolve():
    findings=[]
    for base in SOURCES:
        try:
            data=get_json(base,{"f":"json"})
            ids={layer["id"]:layer["name"] for layer in data.get("layers",[])}
            missing=[i for i in LAYERS if i not in ids]
            if missing:raise RuntimeError(f"Missing layer IDs {missing}")
            metadata={}
            for id,label in LAYERS.items():
                layer=get_json(f"{base}/{id}",{"f":"json"})
                if layer.get("geometryType")!="esriGeometryPolyline":
                    raise RuntimeError(f"Layer {id} not polyline")
                count=get_json(f"{base}/{id}/query",{"where":"1=1","returnCountOnly":"true","f":"json"}).get("count")
                if not isinstance(count,int):raise RuntimeError(f"No count in {id}")
                metadata[label]={"layer_id":id,"global_count":count,"editingInfo":layer.get("editingInfo"),"fields":[x["name"] for x in layer.get("fields",[])],
                                 "extent":layer.get("extent")}
            findings.append({"service":base,"ok":True,"layers":metadata})
            if any(d["global_count"]>0 for d in metadata.values()):
                return base,metadata,findings
        except Exception as e:
            findings.append({"service":base,"ok":False,"error":str(e)})
    raise RuntimeError("No usable OAC regional tracks service: "+json.dumps(findings))
def route(row):
    root=ET.parse(pathlib.Path(f"map-data/auckland-walks/clusters/row-{row}.kml")).getroot()
    lines=[]
    for el in root.iter():
        if el.tag.split("}")[-1]!="LineString":continue
        c=next((e for e in el.iter() if e.tag.split("}")[-1]=="coordinates"),None)
        if c is None or not c.text:continue
        xy=[tuple(map(float,p.split(",")[:2])) for p in c.text.split()]
        if len(xy)>1:lines.append(transform(TO_NZTM,LineString(xy)))
    if not lines:raise RuntimeError(f"No lines for {row}")
    return unary_union(lines)
def request_features(service,layer,geom):
    x1,y1,x2,y2=geom.bounds
    box=[x1-125,y1-125,x2+125,y2+125]
    args={"where":"1=1","geometry":",".join(f"{x:.1f}" for x in box),
          "geometryType":"esriGeometryEnvelope","inSR":"2193","outSR":"2193","spatialRel":"esriSpatialRelIntersects",
          "outFields":"*","returnGeometry":"true","f":"json","resultOffset":0,"resultRecordCount":1000}
    found=[]
    while True:
        payload=get_json(f"{service}/{layer}/query",args,post=True)
        batch=payload.get("features",[])
        found.extend(batch)
        if not payload.get("exceededTransferLimit"):break
        if not batch:raise RuntimeError("Pagination claimed more without features")
        args["resultOffset"]+=len(batch)
        if len(found)>12000:raise RuntimeError("Too many features within small bbox")
    return found
def lines_from_feature(f):
    return [LineString([p[:2] for p in path]) for path in f.get("geometry",{}).get("paths",[]) if len(path)>1]
def analyze(row,service,meta):
    line=route(row);length=line.length
    layers={}
    for id,label in LAYERS.items():
        features=request_features(service,id,line)
        segments=[];names=[]
        for feature in features:
            line_features=lines_from_feature(feature)
            if not line_features:continue
            geom=unary_union(line_features)
            segments.extend(line_features)
            attrs=feature.get("attributes",{})
            near=round(geom.distance(line),1)
            if near<60:
                display={k:attrs.get(k) for k in ("Name","name","Name_","TYPE","track_type","OBJECTID","Source","source","Status","status","Managed_By","Data_Source","Description") if k in attrs}
                names.append({"attributes":display,"distance_m":near,"within_20m_m":round(line.intersection(geom.buffer(20)).length,1)})
        if segments:
            joined=unary_union(segments)
            match={str(d):round(100*min(1,line.intersection(joined.buffer(d)).length/length),1) for d in (10,20,30)}
        else: match={str(d):0 for d in (10,20,30)}
        names.sort(key=lambda x:-x["within_20m_m"])
        layers[label]={"source_layer":id,"matched_in_bbox":len(features),"within_10_20_30m_pct":match,"nearby_features":names[:12]}
    return {"row":row,"historical_m":round(length,1),"checked_utc":datetime.now(timezone.utc).isoformat(),"service":service,"layers":layers,
            "note":"This is regional track-line cartography only; dev service may be stale. Not current legal title/permission or track condition."}
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--rows",required=True)
    p.add_argument("--name",default="pilot")
    a=p.parse_args()
    rows=[int(x) for x in a.rows.split(",") if x.strip()]
    if not 1<=len(rows)<=7:raise RuntimeError("1-7 rows per job")
    results=[];errors=[];discovery=[]
    try:
        service,meta,discovery=resolve()
        print("REGIONAL_LAYER_INFO="+json.dumps({"chosen":service,"discovery":discovery},default=str),flush=True)
        for row in rows:
            try:
                result=analyze(row,service,meta)
                results.append(result)
                print("OAC_MATCH_JSON="+json.dumps(result),flush=True)
            except Exception as e:
                errors.append({"row":row,"error":str(e)})
                print("OAC_MATCH_ERROR="+json.dumps(errors[-1]),flush=True)
    except Exception as e:
        errors.append({"source_error":str(e)})
        print("OAC_SOURCE_ERROR="+json.dumps(errors[-1]),flush=True)
    pathlib.Path("oac_match_"+a.name+".json").write_text(json.dumps({"discovery":discovery,"results":results,"errors":errors},indent=2,default=str))
    if errors:sys.exit(1)
if __name__=="__main__":main()
