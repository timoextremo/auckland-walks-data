#!/usr/bin/env python3
"""Read-only CRoSL spatial overlap for the Auckland unnamed LINZ track audit.

Reports only CRoSL state-land RECORD matches and their Managed_By attributes.
CRoSL has incomplete coverage and does not confirm public right of entry,
physical track condition, or title ownership beyond the published record.
"""
import argparse, json, pathlib, sys, time
from collections import defaultdict
import requests
from shapely.ops import transform, unary_union
from land_access_primary_parcels import lines_for, poly_from_rings, WGS_TO_NZTM

BASE="https://services.arcgis.com/xdsHIIxuCWByZiCB/arcgis/rest/services/CRoSL_Layer_N/FeatureServer/0"
S=requests.Session()
S.headers["User-Agent"]="auckland-walks-data/1.0 (public CRoSL state land spatial audit)"
FIELDS="OBJECTID,Parcel_ID,Title_No,Managed_By,Region,Common_Name,Gov_Type,Date_Updated,Update_Source,Enquiry_Contact,Applicable_Act,Statutory_Actions"
def request_features(bounds):
    # CRoSL geometry is in EPSG:2193, query envelope input is WGS84 EPSG:4326.
    xmin,ymin,xmax,ymax=bounds
    env={"xmin":xmin-0.0004,"ymin":ymin-0.0004,"xmax":xmax+0.0004,"ymax":ymax+0.0004,
         "spatialReference":{"wkid":4326}}
    args={"where":"1=1","geometry":json.dumps(env,separators=(",",":")),
        "geometryType":"esriGeometryEnvelope","inSR":4326,"spatialRel":"esriSpatialRelIntersects",
        "outFields":FIELDS,"returnGeometry":"true","outSR":2193,"f":"json",
        "resultOffset":0,"resultRecordCount":1000}
    found=[]
    while True:
        last=None
        for attempt in range(4):
            try:
                response=S.post(BASE+"/query",data=args,timeout=85)
                response.raise_for_status()
                body=response.json()
                if "error" in body:raise RuntimeError(str(body["error"]))
                break
            except Exception as exc:
                last=exc;time.sleep(2*(attempt+1))
        else:raise RuntimeError(f"CRoSL API request failed: {last}")
        batch=body.get("features",[])
        found.extend(batch)
        if not body.get("exceededTransferLimit") or not batch:break
        args["resultOffset"]+=len(batch)
        if len(found)>16000:raise RuntimeError("Too many CRoSL features in this track envelope")
    return found

def run(row):
    raw=lines_for(row)
    if not raw:raise RuntimeError(f"No input lines for row {row}")
    pts=[p for ln in raw for p in ln.coords]
    bounds=(min(p[0] for p in pts),min(p[1] for p in pts),
            max(p[0] for p in pts),max(p[1] for p in pts))
    line=unary_union([transform(WGS_TO_NZTM,ln) for ln in raw])
    length=line.length
    features=request_features(bounds)
    cuts=[];groups=defaultdict(list);matched=[]
    for record in features:
        a=record.get("attributes",{})
        poly=poly_from_rings(record.get("geometry",{}).get("rings",[]))
        if poly is None or poly.is_empty or not poly.intersects(line):continue
        overlap=line.intersection(poly)
        if overlap.is_empty or overlap.length<0.5:continue
        m=overlap.length
        cuts.append(overlap)
        manager=str(a.get("Managed_By") or "(not listed)").strip()
        groups[manager].append(overlap)
        matched.append({"objectid":a.get("OBJECTID"),"parcel_id":a.get("Parcel_ID"),
            "title_no":a.get("Title_No"),"managed_by":manager,"common_name":a.get("Common_Name"),
            "gov_type":a.get("Gov_Type"),"applicable_act":a.get("Applicable_Act"),
            "statutory_actions":str(a.get("Statutory_Actions") or "")[:180],
            "update_source":a.get("Update_Source"),"date_updated":a.get("Date_Updated"),
            "enquiry_contact":a.get("Enquiry_Contact"),"m":round(m,1),
            "pct":round(100*m/length,1)})
    matched.sort(key=lambda a:-a["m"])
    union=unary_union(cuts).length if cuts else 0
    group_data=[{"manager":name,"meters":round(unary_union(polys).length,1),
        "percent":round(100*unary_union(polys).length/length,1)}
        for name,polys in groups.items()]
    group_data.sort(key=lambda x:-x["meters"])
    return {"row":row,"track_m":round(length,1),"source":BASE,
        "bbox_features":len(features),"matching_records":len(matched),
        "record_coverage_m":round(union,1),"record_coverage_pct":round(100*min(union,length)/length,1),
        "no_record_m":round(max(0,length-union),1),
        "managers":group_data,"top_records":matched[:15],
        "record_overlap_warning":sum(x["meters"] for x in group_data)>union+2,
        "interpretation":"CRoSL records show listed agencies for overlapping state/council land; no CRoSL match is not proof of private ownership or lack of public access"}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--rows",required=True)
    args=p.parse_args()
    rows=[int(x) for x in args.rows.split(",") if x]
    results=[]
    for row in rows:
        try:
            record=run(row)
            print("CROSL_JSON="+json.dumps(record,separators=(",",":")),flush=True)
            results.append(record)
        except Exception as exc:
            print("CROSL_ERROR="+json.dumps({"row":row,"error":str(exc)}),flush=True)
    path=pathlib.Path(f"crosl-batch-{rows[0]}.json")
    path.write_text(json.dumps(results,indent=2),encoding="utf-8")
    print("CROSL_SUMMARY="+json.dumps({"attempted":len(rows),"completed":len(results),
        "errors":len(rows)-len(results),"output":str(path)}),flush=True)
    if len(results)!=len(rows):sys.exit(1)

if __name__=="__main__":main()
