#!/usr/bin/env python3
"""
fetch_rone.py — Phase 1
Fetch 주간아파트가격동향 (weekly apartment price trend) from Korea Real Estate
Board R-ONE Open API for 수도권 시군구, build data/weekly.json.

Output shape (consumed by index.html):
{
  "updated": "2026-06-18",
  "weeks": ["2026-03-31", ... up to ~26 most recent],   # ISO week-anchor dates
  "regions": {
     "11110": {"name":"서울특별시 종로구",
               "trade": [0.02, 0.05, ...],   # weekly % change aligned to weeks[]
               "jeonse":[0.01, 0.03, ...]},
     ...
  }
}

Robustness:
- If STATBL_ID returns nothing, the run prints a clear hint to use discover_rone.py.
- Network/parse errors do NOT crash the workflow; we keep any prior weekly.json.

Usage:
    RONE_API_KEY=xxxx python3 scripts/fetch_rone.py
"""
import os, sys, json, time, datetime, urllib.request, urllib.parse
from collections import defaultdict

KEY  = os.environ.get("RONE_API_KEY", "sample")
BASE = "https://www.reb.or.kr/r-one/openapi"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "data")

# Statistic table IDs — confirm with discover_rone.py if data stops returning.
STATBL_TRADE  = os.environ.get("RONE_STATBL_TRADE",  "A_2024_00178")  # 매매 변동률/지수
STATBL_JEONSE = os.environ.get("RONE_STATBL_JEONSE", "A_2024_00179")  # 전세 변동률/지수

WEEKS_KEEP = 26  # keep the most recent N weeks

def call(params, retries=3):
    qs = urllib.parse.urlencode(params)
    url = f"{BASE}/SttsApiTblData.do?{qs}"
    last = None
    for _ in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "estate-bot"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            last = e
            time.sleep(2)
    raise last

def extract_rows(payload):
    """R-ONE returns {'SttsApiTblData':[{head...},{'row':[...]}]} or similar."""
    if not isinstance(payload, dict):
        return []
    node = payload.get("SttsApiTblData")
    if isinstance(node, list):
        for part in node:
            if isinstance(part, dict) and "row" in part:
                rows = part["row"]
                return rows if isinstance(rows, list) else [rows]
    # some variants nest differently
    for v in payload.values():
        if isinstance(v, dict) and "row" in v:
            rows = v["row"]
            return rows if isinstance(rows, list) else [rows]
    return []

def fetch_series(statbl_id):
    """Return {sgg_code: {week_date: value}} for weekly % change by 시군구."""
    out = defaultdict(dict)
    page = 1
    got = 0
    while page <= 40:
        payload = call({
            "KEY": KEY, "Type": "json", "pIndex": page, "pSize": 1000,
            "STATBL_ID": statbl_id, "DTACYCLE_CD": "WW",
        })
        rows = extract_rows(payload)
        if not rows:
            break
        for row in rows:
            # Field names vary; CLS_ID/CLS_NM = region, WRTTIME_DESC = week, DTA_VAL = value
            cls_id = str(row.get("CLS_ID") or row.get("CLS_FULLNM") or "").strip()
            wtime  = str(row.get("WRTTIME_IDTFR_ID") or row.get("WRTTIME_DESC") or "").strip()
            val    = row.get("DTA_VAL")
            if not cls_id or not wtime or val in (None, ""):
                continue
            # CLS_ID for 시군구 is the 5-digit code in many tables; keep last 5 digits if longer
            code = cls_id[-5:] if cls_id[-5:].isdigit() else cls_id
            try:
                out[code][wtime] = float(val)
            except ValueError:
                continue
        got += len(rows)
        if len(rows) < 1000:
            break
        page += 1
        time.sleep(0.3)
    print(f"  STATBL {statbl_id}: {got} rows, {len(out)} regions")
    return out

def normalize_week(w):
    """Convert R-ONE week identifier to an ISO date string when possible."""
    w = str(w)
    # formats seen: '20260331', '2026년 3월 5주', '202613' (year+weekno)
    if len(w) == 8 and w.isdigit():
        return f"{w[:4]}-{w[4:6]}-{w[6:8]}"
    return w  # leave as-is; frontend shows label directly

def main():
    if KEY == "sample":
        print("WARNING: RONE_API_KEY not set — sample mode returns only 10 rows.")

    lawd = json.load(open(os.path.join(DATA, "lawd_list.json"), encoding="utf-8"))
    names = {x["lawd"]: x["name"] for x in lawd}
    sudo_codes = set(names.keys())

    try:
        trade  = fetch_series(STATBL_TRADE)
        jeonse = fetch_series(STATBL_JEONSE)
    except Exception as e:
        print("ERROR fetching R-ONE:", e)
        print("Run scripts/discover_rone.py to confirm STATBL_IDs. Keeping existing weekly.json.")
        sys.exit(0)  # don't fail the workflow

    if not trade and not jeonse:
        print("No data returned. Check STATBL_IDs via discover_rone.py. Keeping existing weekly.json.")
        sys.exit(0)

    # union of week keys across regions, sorted, keep most recent N
    all_weeks = set()
    for series in (trade, jeonse):
        for code, wk in series.items():
            all_weeks.update(wk.keys())
    weeks_sorted = sorted(all_weeks)[-WEEKS_KEEP:]
    weeks_norm = [normalize_week(w) for w in weeks_sorted]

    regions = {}
    for code in sudo_codes:
        t = [trade.get(code, {}).get(w) for w in weeks_sorted]
        j = [jeonse.get(code, {}).get(w) for w in weeks_sorted]
        if any(v is not None for v in t) or any(v is not None for v in j):
            regions[code] = {"name": names.get(code, code), "trade": t, "jeonse": j}

    out = {
        "updated": datetime.date.today().isoformat(),
        "weeks": weeks_norm,
        "regions": regions,
    }
    path = os.path.join(DATA, "weekly.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"Wrote {path}: {len(regions)} regions, {len(weeks_norm)} weeks")

if __name__ == "__main__":
    main()
