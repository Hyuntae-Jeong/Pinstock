"""관심종목 전용 지수 카탈로그 (코스피/코스닥/해외 주요 지수).

지수는 보유 종목에는 추가할 수 없고 관심종목에서만 검색/추가한다. 종목과 달리
평단가/수량/손익 개념이 없으며 시세는 일봉 기준으로 본다. 후보 수가 적어 외부
검색 API 대신 고정 카탈로그로 관리하고, 이름/코드/별칭 일치로 찾는다.

각 항목의 code 는 시세 조회에 그대로 쓰는 식별자다:
  - 국내 지수: 네이버 금융 지수 코드 (KOSPI, KOSDAQ)
  - 해외 지수: Yahoo Finance 심볼 (^GSPC, ^IXIC, ^DJI)
market/currency 는 표시 포맷·시세 라우팅에 쓴다.
"""

INDEX_CATALOG = [
    {"code": "KOSPI",  "name": "코스피",   "market": "KR", "currency": "KRW",
     "aliases": ["코스피", "코스피지수", "kospi"]},
    {"code": "KOSDAQ", "name": "코스닥",   "market": "KR", "currency": "KRW",
     "aliases": ["코스닥", "코스닥지수", "kosdaq"]},
    {"code": "^GSPC",  "name": "S&P 500",  "market": "US", "currency": "USD",
     "aliases": ["s&p 500", "s&p500", "sp500", "snp", "에스앤피", "gspc"]},
    {"code": "^IXIC",  "name": "나스닥",   "market": "US", "currency": "USD",
     "aliases": ["나스닥", "나스닥종합", "nasdaq", "ixic"]},
    {"code": "^DJI",   "name": "다우존스", "market": "US", "currency": "USD",
     "aliases": ["다우", "다우존스", "다우지수", "dow", "dow jones", "dowjones", "dji"]},
]


def _as_result(idx: dict) -> dict:
    """카탈로그 항목을 종목 검색 결과와 같은 형태(+type='index')로 변환."""
    return {
        "code":     idx["code"],
        "name":     idx["name"],
        "market":   idx["market"],
        "currency": idx["currency"],
        "type":     "index",
    }


def index_by_code(code: str) -> dict | None:
    """저장된 code(네이버 지수 코드 / Yahoo 심볼)로 카탈로그 항목을 찾는다."""
    target = str(code or "").strip().upper()
    if not target:
        return None
    for idx in INDEX_CATALOG:
        if idx["code"].upper() == target:
            return _as_result(idx)
    return None


def search_indices(query: str, market: str | None = None, limit: int = 5) -> list[dict]:
    """이름/코드/별칭 부분일치로 카탈로그 지수를 검색 (드롭다운 후보용).

    market 을 주면 해당 시장만 거른다. 반환 항목은 _as_result 형태.
    """
    q = str(query or "").strip().lower()
    if not q:
        return []
    market = str(market).strip().upper() if market else None
    results: list[dict] = []
    for idx in INDEX_CATALOG:
        if market and idx["market"] != market:
            continue
        haystacks = [idx["code"].lower(), idx["name"].lower(), *idx["aliases"]]
        if any(q in h or h in q for h in haystacks):
            results.append(_as_result(idx))
            if len(results) >= limit:
                break
    return results


def index_exact_match(query: str, market: str | None = None) -> dict | None:
    """입력이 지수의 코드/이름/별칭과 정확히 일치하면 해당 지수를 반환.

    드롭다운에서 고르지 않고 '코스피'·'나스닥' 처럼 그대로 입력하고 확인했을 때의
    안전망. 부분일치(search_indices)와 달리 정확히 일치할 때만 매칭하므로 이름에
    별칭이 포함된 실제 종목(예: '다우데이타')을 지수로 오인하지 않는다.
    """
    q = str(query or "").strip().lower()
    if not q:
        return None
    market = str(market).strip().upper() if market else None
    for idx in INDEX_CATALOG:
        if market and idx["market"] != market:
            continue
        names = [idx["code"].lower(), idx["name"].lower(), *[a.lower() for a in idx["aliases"]]]
        if q in names:
            return _as_result(idx)
    return None
