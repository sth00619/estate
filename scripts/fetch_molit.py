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

KEY_RAW = os.environ.get("MOLIT_API_KEY", "")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "data")
URL  = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"

def normalize_key(raw):
    """
    data.go.kr issues a key in two equivalent forms:
      - "Encoding" key: already percent-encoded (contains %2B, %2F, %3D, ...)
      - "Decoding" key / "일반 인증키": raw, may contain literal +, /, =
    Either one works here as long as we encode it for the URL exactly once.
    If it already looks percent-encoded (has a '%' followed by 2 hex digits),
    we assume it's the Encoding key and use it AS-IS in the URL. Otherwise we
    percent-encode it ourselves. This makes the script work no matter which
    of the two key types the user copied from the portal.
    """
    import re
    if re.search(r"%[0-9A-Fa-f]{2}", raw):
        return raw  # already encoded — splice in as-is
    return urllib.parse.quote(raw, safe="")  # raw/decoding key — encode once

KEY = normalize_key(KEY_RAW)

MONTHS_BACK   = int(os.environ.get("MOLIT_MONTHS", "3"))
PRICE_CAP     = 400   # max raw prices stored per 동 (histogram is enough)
PER_PY_DIVISOR = 3.305785  # ㎡ per 평

# Official data.go.kr error codes (from 기술문서 Ⅱ. OPENAPI 에러 코드정리).
# Surfaced verbatim so a failed run tells you exactly what to check, instead
# of a bare "API error 30".
ERROR_HINTS = {
    "01": "제공기관 서비스 장애 — 잠시 후 다시 시도하세요.",
    "02": "제공기관 DB 장애 — 잠시 후 다시 시도하세요.",
    "03": "해당 조건에 데이터가 없습니다 (LAWD_CD/DEAL_YMD 확인).",
    "04": "제공기관 HTTP 오류 — 잠시 후 다시 시도하세요.",
    "05": "제공기관 서비스 타임아웃 — 잠시 후 다시 시도하세요.",
    "10": "serviceKey 파라미터가 누락되었습니다. URL을 확인하세요.",
    "11": "필수 요청 파라미터가 누락되었습니다 (LAWD_CD/DEAL_YMD).",
    "12": "요청 URL이 잘못되었거나 서비스가 폐기되었습니다.",
    "20": "활용신청이 아직 승인되지 않았습니다. data.go.kr 마이페이지에서 "
          "승인상태를 확인하세요 (보통 신청 후 2~3일, 승인 시 가입 이메일로 통지).",
    "22": "일일 트래픽(기본 10,000건) 초과. 다음 날 재시도하거나 운영계정 전환을 신청하세요.",
    "30": "서비스키가 등록되지 않았거나 URL 인코딩 상태가 맞지 않습니다. "
          "MOLIT_API_KEY 값을 data.go.kr 마이페이지의 키와 다시 비교하세요.",
    "31": "서비스키 사용기간이 만료되었습니다. 활용연장 신청이 필요합니다.",
    "32": "활용신청 시 등록한 도메인/IP와 호출 서버가 다릅니다. "
          "GitHub Actions에서 호출 중이라면 IP 제한 없이 신청했는지 확인하세요.",
}

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
    # IMPORTANT: data.go.kr issues TWO key formats — "Encoding" (already
    # percent-encoded, e.g. contains %2B/%2F/%3D) and "Decoding" (raw).
    # Whichever one the user has, we must NOT percent-encode it a second
    # time, or '%' becomes '%25' and the key is rejected
    # (SERVICE_KEY_IS_NOT_REGISTERED_ERROR). So: splice serviceKey into the
    # URL untouched, and urlencode only the remaining plain params.
    other = urllib.parse.urlencode({
        "LAWD_CD": lawd, "DEAL_YMD": ymd,
        "pageNo": str(page), "numOfRows": "1000",
    })
    url = f"{URL}?serviceKey={KEY}&{other}"
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
        # not XML at all -- often an HTML error page or truncated response
        snippet = xml_text[:200].replace("\n", " ")
        raise RuntimeError(f"non-XML response (truncated/HTML?): {snippet}")
    header = root.find(".//resultCode")
    if header is not None and header.text not in ("00", "000", None):
        code = (header.text or "").strip()
        msg = root.find(".//resultMsg")
        msgtext = msg.text if msg is not None else ""
        hint = ERROR_HINTS.get(code, "data.go.kr 활용신청 상태와 서비스키를 확인하세요.")
        raise RuntimeError(f"API error {code} ({msgtext}) — {hint}")

    # data.go.kr's gateway sometimes returns auth failures in a different
    # shape entirely (no resultCode at all), e.g.:
    #   <cmmMsgHeader><returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg></cmmMsgHeader>
    # Catch that here so it doesn't silently look like "0 results".
    auth_msg = root.find(".//returnAuthMsg")
    if auth_msg is not None and auth_msg.text:
        raise RuntimeError(
            f"gateway auth error: {auth_msg.text} — 서비스키가 등록되지 않았거나 아직 "
            "승인되지 않았습니다. data.go.kr 마이페이지에서 활용신청 승인상태와 키 값을 확인하세요."
        )

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
    if not KEY_RAW:
        print("ERROR: MOLIT_API_KEY not set. Keeping existing trades_latest.json.")
        sys.exit(0)

    lawd = json.load(open(os.path.join(DATA, "lawd_list.json"), encoding="utf-8"))
    months = recent_yyyymm(MONTHS_BACK)
    print(f"Fetching {len(lawd)} 시군구 × {MONTHS_BACK} months {months} ...")

    emd_acc = defaultdict(list)   # (sgg, umd) -> [ (price_man, per_py_man) ]
    sgg_acc = defaultdict(list)
    sgg_name = {x["lawd"]: x["name"] for x in lawd}

    done = 0
    FATAL_CODES = ("20", "30", "31", "32", "10")  # same root cause every call; no point retrying per-district
    for entry in lawd:
        code = entry["lawd"]
        try:
            rows = fetch_lawd_months(code, months)
        except Exception as e:
            msg = str(e)
            print(f"  {code} {entry['name']}: error {e}")
            if any(f"API error {fc} " in msg for fc in FATAL_CODES) or "gateway auth error" in msg:
                print("\nSTOPPING: this error applies to every request, not just this district.")
                print("Fix the key/approval issue above, then re-run. Keeping existing trades_latest.json.")
                sys.exit(0)
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
