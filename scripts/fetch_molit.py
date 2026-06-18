#!/usr/bin/env python3
"""
fetch_molit.py — Phase 2
Fetch 아파트 매매 실거래가 (apartment sale transactions) from MOLIT via
data.go.kr, aggregate to 동(법정동) and 시군구 levels, build data/trades_latest.json.

Aggregation (per 동 and per 시군구):
  - count:       number of transactions in the window
  - median_man:  median deal price in 만원 (KRW 10k)
  - median_per_py: median price per 평(3.3㎡) in 만원  (거래금액 / (전용면적/3.3))
  - p25_man / p75_man: price quartiles (for box plots / histograms later)
  - prices_man:  raw price list (capped) for histogram building on the client

Window: most recent N months (default 3) summed together.

Endpoint: getRTMSDataSvcAptTradeDev (상세) — fields incl. umdNm(법정동), excluUseAr(전용면적),
dealAmount(거래금액 만원), dealYear/Month/Day, aptNm, jibun.

Trafffic: dev key = 10,000/day. 82 시군구 × 3 months = 246 calls (well under cap),
plus pagination. We throttle and retry.

Usage:
    MOLIT_API_KEY=xxxx python3 scripts/fetch_molit.py
"""
import os, sys, json, time, datetime, statistics, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from collections import defaultdict

KEY  = os.environ.get("MOLIT_API_KEY", "")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "data")
# data.go.kr decoded key goes in serviceKey; http (not https) avoids some SSL issues.
URL  = "http://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"

MONTHS_BACK   = int(os.environ.get("MOLIT_MONTHS", "3"))
PRICE_CAP     = 400   # max raw prices stored per 동 (histogram is enough)
PER_PY_DIVISOR = 3.305785  # ㎡ per 평

def recent_yyyymm(n):
    today = datetime.date.today()
    out = []
    y, m = today.year, today.month
    for _ in range(n):
        out.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            m = 12; y -= 1
    return out

def call(lawd, ymd, page, retries=3):
    params = {
        "serviceKey": KEY, "LAWD_CD": lawd, "DEAL_YMD": ymd,
        "pageNo": str(page), "numOfRows": "1000",
    }
    url = URL + "?" + urllib.parse.urlencode(params, safe="/+=")
    last = None
    for _ in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "estate-bot"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            time.sleep(2)
    raise last

def parse_items(xml_text):
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items, 0
    # error check
    header = root.find(".//resultCode")
    if header is not None and header.text not in ("00", "000", None):
        msg = root.find(".//resultMsg")
        raise RuntimeError(f"API error {header.text}: {msg.text if msg is not None else ''}")
    total_el = root.find(".//totalCount")
    total = int(total_el.text) if total_el is not None and total_el.text else 0
    for it in root.findall(".//item"):
        def g(tag):
            e = it.find(tag)
            return e.text.strip() if e is not None and e.text else ""
        items.append({
            "umd": g("umdNm"),
            "apt": g("aptNm"),
            "amount": g("dealAmount").replace(",", ""),
            "area": g("excluUseAr"),
            "y": g("dealYear"), "m": g("dealMonth"), "d": g("dealDay"),
        })
    return items, total

def fetch_lawd_months(lawd, months):
    rows = []
    for ymd in months:
        page = 1
        while page <= 20:
            xml_text = call(lawd, ymd, page)
            items, total = parse_items(xml_text)
            rows.extend(items)
            if page * 1000 >= total or not items:
                break
            page += 1
            time.sleep(0.15)
        time.sleep(0.15)
    return rows

def median_or_none(xs):
    return round(statistics.median(xs), 1) if xs else None

def quantile(xs, q):
    if not xs: return None
    s = sorted(xs); idx = min(len(s) - 1, int(q * (len(s) - 1)))
    return round(s[idx], 1)

def main():
    if not KEY:
        print("ERROR: MOLIT_API_KEY not set. Keeping existing trades_latest.json.")
        sys.exit(0)

    lawd = json.load(open(os.path.join(DATA, "lawd_list.json"), encoding="utf-8"))
    months = recent_yyyymm(MONTHS_BACK)
    print(f"Fetching {len(lawd)} 시군구 × {MONTHS_BACK} months {months} ...")

    emd_acc = defaultdict(list)   # (sgg, umd) -> [ (price_man, per_py_man) ]
    sgg_acc = defaultdict(list)
    sgg_name = {x["lawd"]: x["name"] for x in lawd}

    done = 0
    for entry in lawd:
        code = entry["lawd"]
        try:
            rows = fetch_lawd_months(code, months)
        except Exception as e:
            print(f"  {code} {entry['name']}: error {e} (skip)")
            continue
        for r in rows:
            try:
                price = float(r["amount"])           # 만원
                area  = float(r["area"]) if r["area"] else 0
            except ValueError:
                continue
            if price <= 0:
                continue
            per_py = price / (area / PER_PY_DIVISOR) if area > 0 else None
            emd_acc[(code, r["umd"])].append((price, per_py))
            sgg_acc[code].append((price, per_py))
        done += 1
        if done % 10 == 0:
            print(f"  ...{done}/{len(lawd)} 시군구 done")
        time.sleep(0.1)

    def pack(pairs):
        prices = [p for p, _ in pairs]
        perpy  = [pp for _, pp in pairs if pp is not None]
        return {
            "count": len(prices),
            "median_man": median_or_none(prices),
            "p25_man": quantile(prices, 0.25),
            "p75_man": quantile(prices, 0.75),
            "median_per_py": median_or_none(perpy),
            "prices_man": sorted(prices)[:PRICE_CAP],
        }

    emd_out = {}
    for (code, umd), pairs in emd_acc.items():
        emd_out[f"{code}|{umd}"] = {"sgg": code, "umd": umd, **pack(pairs)}
    sgg_out = {}
    for code, pairs in sgg_acc.items():
        sgg_out[code] = {"name": sgg_name.get(code, code), **pack(pairs)}

    out = {
        "updated": datetime.date.today().isoformat(),
        "months": months,
        "type": "apartment_sale",
        "sgg": sgg_out,
        "emd": emd_out,
    }
    path = os.path.join(DATA, "trades_latest.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"Wrote {path}: {len(sgg_out)} 시군구, {len(emd_out)} 동, "
          f"{sum(v['count'] for v in sgg_out.values())} total trades")

if __name__ == "__main__":
    main()
