#!/usr/bin/env python3
"""
discover_rone.py — Probe R-ONE Open API to confirm current STATBL_ID codes
and region (CLS) codes for 주간아파트가격동향.

Run this ONCE after issuing your key to verify the statistic table IDs,
because R-ONE occasionally renumbers them. Prints candidates; copy the
working STATBL_ID into fetch_rone.py if the defaults stop returning data.

Usage:
    RONE_API_KEY=xxxx python3 scripts/discover_rone.py
"""
import os, sys, json, urllib.request, urllib.parse

KEY = os.environ.get("RONE_API_KEY", "sample")
BASE = "https://www.reb.or.kr/r-one/openapi"

# 주간아파트가격동향 commonly-used statistic table IDs (매매/전세 변동률 & 지수).
# These are the historical IDs; discover mode confirms which still respond.
CANDIDATES = [
    "A_2024_00178",  # 주간 매매가격지수
    "A_2024_00179",  # 주간 전세가격지수
    "A_2024_00045",  # 주간 매매가격 변동률 (legacy)
    "A_2024_00046",  # 주간 전세가격 변동률 (legacy)
    "T_1_0001",      # generic fallback
]

def call(path, params):
    qs = urllib.parse.urlencode(params)
    url = f"{BASE}/{path}?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "estate-bot"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw[:500]}

def probe_list():
    """List available statistic tables."""
    print("=== StatTblList probe ===")
    try:
        data = call("StatTblList.do", {"KEY": KEY, "Type": "json", "pIndex": 1, "pSize": 100})
        print(json.dumps(data, ensure_ascii=False, indent=1)[:3000])
    except Exception as e:
        print("StatTblList failed:", e)

def probe_table(statbl_id):
    print(f"\n=== probing STATBL_ID={statbl_id} ===")
    try:
        data = call("SttsApiTblData.do", {
            "KEY": KEY, "Type": "json", "pIndex": 1, "pSize": 5,
            "STATBL_ID": statbl_id, "DTACYCLE_CD": "WW",
        })
        s = json.dumps(data, ensure_ascii=False)
        if "row" in s.lower() or "SttsApiTblData" in s:
            print("  RESPONDS. sample:", s[:800])
        else:
            print("  no rows. msg:", s[:300])
    except Exception as e:
        print("  error:", e)

if __name__ == "__main__":
    if KEY == "sample":
        print("WARNING: using sample key (10-row limit). Set RONE_API_KEY for full access.\n")
    probe_list()
    for cid in CANDIDATES:
        probe_table(cid)
    print("\nDone. Copy a responding STATBL_ID into fetch_rone.py STATBL_TRADE / STATBL_JEONSE.")
