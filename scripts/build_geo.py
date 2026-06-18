#!/usr/bin/env python3
"""
build_geo.py — regenerate수도권 boundary files from vuski/admdongkor.

Downloads the latest 행정동 경계 GeoJSON, extracts 수도권 (서울11/인천28/경기41),
simplifies, and writes:
  data/geo_emd.json   (동 단위, 드릴다운)
  data/geo_sgg.json   (시군구, dissolve)
  data/lawd_list.json (시군구 법정동코드 목록)

Run when boundaries change (e.g. new 행정구역 개편). Requires `shapely`.

Usage:
    python3 scripts/build_geo.py [ver]   # ver like 20260401 (default: auto-detect latest)
"""
import os, sys, json, urllib.request
from collections import defaultdict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "data")
RAW  = "https://raw.githubusercontent.com/vuski/admdongkor/master"
SUDO = {"11", "28", "41"}  # 서울, 인천, 경기 (real 법정동 sido codes)

def latest_version():
    import datetime
    d = datetime.date.today()
    for _ in range(24):
        v = f"{d.year}{d.month:02d}01"
        url = f"{RAW}/ver{v}/HangJeongDong_ver{v}.geojson"
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "estate"})
            with urllib.request.urlopen(req, timeout=20):
                return v
        except Exception:
            pass
        # step back a month
        if d.month == 1: d = d.replace(year=d.year-1, month=12, day=1)
        else: d = d.replace(month=d.month-1, day=1)
    raise RuntimeError("no admdongkor version found")

def main():
    from shapely.geometry import shape, mapping
    from shapely.ops import unary_union
    ver = sys.argv[1] if len(sys.argv) > 1 else latest_version()
    url = f"{RAW}/ver{ver}/HangJeongDong_ver{ver}.geojson"
    print("downloading", url)
    raw = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "estate"}), timeout=120).read()
    d = json.loads(raw)
    feats = [f for f in d["features"] if f["properties"]["sido"] in SUDO]
    print("수도권 동 features:", len(feats))

    emd = []
    for f in feats:
        g = shape(f["geometry"]).simplify(0.0010, preserve_topology=True)
        if g.is_empty: continue
        p = f["properties"]
        emd.append({"type":"Feature",
            "properties":{"adm_nm":p["adm_nm"],"emd":p["adm_nm"].split()[-1],
                          "sgg":p["sgg"],"sggnm":p["sggnm"],"sido":p["sido"],"sidonm":p["sidonm"]},
            "geometry":mapping(g)})
    json.dump({"type":"FeatureCollection","features":emd},
              open(os.path.join(DATA,"geo_emd.json"),"w"), ensure_ascii=False, separators=(",",":"))

    by = defaultdict(list)
    for f in feats: by[f["properties"]["sgg"]].append(f)
    sgg = []
    for code, grp in by.items():
        u = unary_union([shape(g["geometry"]) for g in grp]).simplify(0.0008, preserve_topology=True)
        p = grp[0]["properties"]
        sgg.append({"type":"Feature",
            "properties":{"sgg":code,"sggnm":p["sggnm"],"sido":p["sido"],"sidonm":p["sidonm"],
                          "full_nm":p["sidonm"]+" "+p["sggnm"]},
            "geometry":mapping(u)})
    json.dump({"type":"FeatureCollection","features":sgg},
              open(os.path.join(DATA,"geo_sgg.json"),"w"), ensure_ascii=False, separators=(",",":"))

    lawd = sorted(set((f["properties"]["sgg"], f["properties"]["sidonm"]+" "+f["properties"]["sggnm"]) for f in feats))
    json.dump([{"lawd":c,"name":n} for c,n in lawd],
              open(os.path.join(DATA,"lawd_list.json"),"w"), ensure_ascii=False, indent=1)
    print(f"wrote geo_emd({len(emd)}), geo_sgg({len(sgg)}), lawd_list({len(lawd)}) from ver{ver}")

if __name__ == "__main__":
    main()
