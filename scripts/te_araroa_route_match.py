#!/usr/bin/env python3
"""Match historic Auckland LINZ track KMLs to Te Araroa's official GPX line.

Geometry only. This is not legal access, current condition, or a navigation guarantee.
Prefer latest season from Te Araroa's current notes/maps page and retain URL/edition.
"""
import argparse, io, json, pathlib, re, sys, zipfile, xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin
import requests
from pyproj import Transformer
from shapely.geometry import LineString, box
from shapely.ops import unary_union, transform

SITE="https://www.teararoa.org.nz/notes-and-maps/"
OLD="https://www.teararoa.org.nz/trail-maps/"
USER_AGENT="auckland-walks-route-validation/1.0 (public official GPX; no access conclusions)"
TRANS=Transformer.from_crs("EPSG:4326","EPSG:2193",always_xy=True).transform
class AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links=[];self.href=None;self.parts=[]
    def handle_starttag(self, tag, attrs):
        if tag=="a":
            self.href=dict(attrs).get("href")
            self.parts=[]
    def handle_data(self,data):
        if self.href is not None:self.parts.append(data)
    def handle_endtag(self,tag):
        if tag=="a" and self.href is not None:
            self.links.append((self.href, " ".join(self.parts).strip()))
            self.href=None

def locate_gpx(session):
    pages=[]
    for page in (SITE,OLD):
        resp=session.get(page,timeout=35)
        resp.raise_for_status()
        html=resp.text
        parser=AnchorParser(); parser.feed(html)
        links=[(urljoin(resp.url,uri), label) for uri,label in parser.links]
        season=re.findall(r"2026\s*[-–/]\s*27|2025\s*[-–/]\s*26|v45|v44",html,re.I)
        print("OFFICIAL_PAGE="+json.dumps({"url":resp.url,"status":resp.status_code,"season_text":season[:8],
            "gpx_links":[{"url":a,"label":b} for a,b in links if "gpx" in (a+" "+b).lower()][:16]}),flush=True)
        for url,label in links:
            descriptor=(url+" "+label).lower()
            if "gpx" not in descriptor or ("km" in descriptor and "point" in descriptor):
                continue
            if any(s in descriptor for s in ("kilometre point","km_points","km-only","km_only")):
                continue
            if "track" in descriptor or "trail" in descriptor:
                score=0
                if "2026" in descriptor or "2026-27" in html or "2026–27" in html:score+=10
                if "tracks gpx" in descriptor or "track_gpx" in descriptor:score+=5
                if "section" in descriptor:score-=3
                pages.append((score,url,label,resp.url))
    dedup={}
    for v in sorted(pages,reverse=True):
        dedup.setdefault(v[1],v)
    if not dedup:
        raise RuntimeError("Official site returned no discoverable GPX download links. Do not substitute unverified map.")
    picks=sorted(dedup.values(),reverse=True)
    for score,url,label,page in picks[:8]:
        try:
            r=session.get(url,timeout=55)
            r.raise_for_status()
            z=zipfile.ZipFile(io.BytesIO(r.content))
            members=[m for m in z.namelist() if m.lower().endswith(".gpx")]
            if members:
                return {"zip":z,"url":r.url,"page":page,"label":label,
                        "members":members,"zip_bytes":len(r.content),
                        "v45_current_on_page":"2026" in label or "2026" in url}
            print("NO_GPX_IN_ARCHIVE "+r.url,flush=True)
        except Exception as e:
            print("CANDIDATE_FAILED "+json.dumps({"url":url,"error":str(e)}),flush=True)
    raise RuntimeError("No usable official GPX archive from available links")

def parse_lines(zipf,members):
    lines=[]
    for name in members:
        root=ET.fromstring(zipf.read(name))
        seen=0
        for elem in root.iter():
            if elem.tag.split("}")[-1] not in ("trkseg","rte"):continue
            pts=[]
            for point in elem:
                if point.tag.split("}")[-1] not in ("trkpt","rtept"):continue
                try:pts.append((float(point.attrib["lon"]),float(point.attrib["lat"])))
                except (KeyError,ValueError):pass
            if len(pts)>1:
                lines.append(transform(TRANS,LineString(pts)));seen+=1
        print("GPX_FILE "+json.dumps({"filename":name,"lines":seen}),flush=True)
    if not lines:raise RuntimeError("Archive contains no usable GPX lines")
    return lines

def source_track(row):
    path=pathlib.Path("map-data/auckland-walks/clusters")/f"row-{row}.kml"
    root=ET.parse(path).getroot()
    lines=[]
    for item in root.iter():
        if item.tag.split("}")[-1]!="LineString":continue
        coords=next((c for c in item.iter() if c.tag.split("}")[-1]=="coordinates"),None)
        if coords is None or not coords.text:continue
        p=[tuple(map(float,x.split(",")[:2])) for x in coords.text.split()]
        if len(p)>=2:lines.append(transform(TRANS,LineString(p)))
    if not lines:raise RuntimeError(f"No valid KML in {path}")
    return unary_union(lines)

def compare(row,ta_lines,source):
    route=source_track(row);xmin,ymin,xmax,ymax=route.bounds
    env=box(xmin-250,ymin-250,xmax+250,ymax+250)
    local=[g.intersection(env) for g in ta_lines if g.intersects(env)]
    local=[g for g in local if not g.is_empty]
    length=route.length
    if local:
        ta=unary_union(local)
        nearest=round(route.distance(ta),1)
        coverage={str(d):round(min(100,100*route.intersection(ta.buffer(d)).length/length),1) for d in (10,20,30)}
    else:
        nearest=None;coverage={str(d):0 for d in (10,20,30)}
    return {"Sheet1_row":row,"historic_m":round(length,1),"te_araroa_nearest_m":nearest,
            "ta_lines_within_250m":len(local),"coverage_by_buffer_m_pct":coverage,
            "source_archive":source["url"],"source_webpage":source["page"],
            "assessment":"Geometric overlap only; only current official route and up-to-date alerts support a walk.",
            "queried_utc":datetime.now(timezone.utc).isoformat()}
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--rows",required=True)
    parser.add_argument("--name",default="pilot")
    args=parser.parse_args()
    rows=[int(v) for v in args.rows.split(",") if v.strip()]
    if not 1<=len(rows)<=7:raise RuntimeError("One to seven rows per job")
    session=requests.Session()
    session.headers.update({"User-Agent":USER_AGENT})
    source=locate_gpx(session)
    print("CHOSEN_ARCHIVE="+json.dumps({k:v for k,v in source.items() if k!="zip"}),flush=True)
    lines=parse_lines(source["zip"],source["members"])
    results=[];errors=[]
    for row in rows:
        try:
            r=compare(row,lines,source)
            results.append(r)
            print("TA_MATCH_JSON="+json.dumps(r),flush=True)
        except Exception as e:
            errors.append({"row":row,"error":str(e)})
            print("TA_MATCH_ERROR="+json.dumps(errors[-1]),flush=True)
    pathlib.Path("ta_match_"+args.name+".json").write_text(json.dumps({"source":{k:v for k,v in source.items() if k!="zip"},"results":results,"errors":errors},indent=2))
    if errors:raise SystemExit(f"{len(errors)} failed")
if __name__=="__main__":main()
