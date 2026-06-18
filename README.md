# 닥터마빈 부동산 리포트 — 수도권 시세 지도

서울·경기·인천 전역의 아파트 시세를 지도와 차트로 보여주는 정적 웹사이트입니다.
GitHub Pages로 배포되며, 데이터는 GitHub Actions가 공공 API에서 자동 수집합니다.

## 구조

```
estate/
├── index.html              # 메인 페이지 (지도 + 차트, 의존성 전부 CDN/인라인)
├── data/
│   ├── geo_sgg.json         # 수도권 82개 시군구 경계
│   ├── geo_emd.json         # 수도권 1,187개 동(읍면동) 경계 (드릴다운)
│   ├── lawd_list.json       # 시군구 법정동코드 목록 (수집 스크립트용)
│   ├── weekly.json          # [Phase 1] R-ONE 주간 매매·전세 변동률
│   └── trades_latest.json   # [Phase 2] MOLIT 아파트 실거래 집계 (동·시군구)
├── scripts/
│   ├── discover_rone.py     # R-ONE 통계코드 진단 (키 발급 후 1회 실행 권장)
│   ├── fetch_rone.py        # 주간 변동률 수집 → weekly.json
│   ├── fetch_molit.py       # 실거래가 수집·집계 → trades_latest.json
│   └── build_geo.py         # (참고) 경계 파일 재생성 스크립트
└── .github/workflows/
    └── update-data.yml      # 자동 수집·커밋 (목요일 R-ONE, 매월 2일 MOLIT)
```

현재 `weekly.json`·`trades_latest.json`은 **샘플(seed) 데이터**입니다. 페이지 상단에
"샘플 데이터" 배지가 보이며, 아래 절차로 실데이터를 채우면 배지가 사라집니다.

## API 키 (GitHub Secrets)

두 개의 무료 공공 API 키가 필요합니다. 저장소 **Settings → Secrets and variables →
Actions → New repository secret** 에서 등록하세요.

| Secret 이름 | 발급처 | 용도 |
|---|---|---|
| `RONE_API_KEY` | https://www.reb.or.kr/r-one/ → Open API → 인증키 신청 | 주간 변동률 (Phase 1) |
| `MOLIT_API_KEY` | https://www.data.go.kr → "아파트 매매 실거래가" 활용신청 | 실거래가 (Phase 2) |

> MOLIT 키는 **디코딩(Decoding) 키**를 사용하세요. 인코딩 키를 넣으면 이중 인코딩으로
> 인증 오류가 납니다.

## 데이터 채우기 (최초 1회)

1. 위 두 Secret 등록
2. **Actions 탭 → "Update real-estate data" → Run workflow**
   - `run_rone`, `run_molit` 둘 다 켠 채 실행
3. 약 2~5분 후 `data/*.json`이 실데이터로 자동 커밋되고 Pages가 재배포됩니다
4. (선택) R-ONE이 데이터를 안 주면 `scripts/discover_rone.py`를 로컬에서 실행해
   현재 유효한 `STATBL_ID`를 확인하고, 워크플로 환경변수
   `RONE_STATBL_TRADE` / `RONE_STATBL_JEONSE`로 덮어쓰세요.

이후로는 자동입니다: **매주 목요일** R-ONE 주간동향, **매월 2일** MOLIT 실거래가.

## GitHub Pages 배포

Settings → Pages → Source: **Deploy from a branch** → `main` / `/ (root)` → Save.
주소: `https://sth00619.github.io/estate/`

## 기능

- **드릴다운 지도**: 시군구 클릭 → 동(읍면동) 단위로 확대. 좌측 상단 "시군구 보기"
  칩 또는 빈 곳 클릭으로 복귀.
- **3가지 지표 토글**: 매매 변동률 / 전세 변동률 / 실거래 중앙값.
- **단지 거래 표시**: 동 단위에서 거래 밀집도를 클러스터 마커로 표시.
- **사이드 패널**: 선택 지역의 변동률·중앙값·평당가·거래건수 + 12주 미니 추이.
- **차트**: 상승/하락 TOP 10 막대, 가격대별 거래 분포 히스토그램.
- **지도 도구**: 부드러운 줌/스크롤, 풀스크린, 미니맵.

## 데이터 출처와 한계

- R-ONE 주간동향은 **시군구가 최하위 단위**입니다. 동 단위 변동률은 제공되지 않아,
  동 단위에서는 MOLIT 실거래 중앙값으로 표시됩니다.
- MOLIT 실거래가는 신고 기준이라 최근 1개월은 누락분이 있을 수 있습니다.
- 좌표가 없는 실거래는 동 중심점(centroid)에 집계해 표시합니다(단지별 정확 위치 아님).
- 통계는 참고용이며 실제 시세와 차이가 있을 수 있습니다.
