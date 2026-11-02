"""관심종목 전용 환율 카탈로그 (원/달러 · 원/100엔 · 원/유로 · 원/위안).

지수(indices.py)처럼 보유 종목에는 추가할 수 없고 관심종목에서만 검색/추가한다.
평단가/수량/손익 개념이 없고, 후보가 몇 개뿐이라 고정 카탈로그로 관리한다.

각 항목:
  - code:   저장·중복검사·메모 키로 쓰는 식별자 (USDKRW 등)
  - yahoo:  Yahoo Finance 심볼 (시세·일봉 기본 경로)
  - naver:  네이버 환율 코드 (Yahoo 실패 시 폴백)
  - scale:  Yahoo 값에 곱할 배수. 엔화는 Yahoo 가 1엔 기준이라 100 을 곱해
            국내 표기(100엔당 원)에 맞춘다. 네이버는 이미 100엔 기준이다.
  - yahoo_daily_inverse: 일봉만 이 역방향 쌍을 뒤집어(1/x) 쓴다. Yahoo 의
            CNYKRW=X 는 현재가는 정상이지만 일봉 이력이 1개뿐이라 KRWCNY=X 로 대신한다.
모든 쌍은 원화 표시라 market/currency 는 KR/KRW 로 저장한다.
"""

FX_CATALOG = [
    {"code": "USDKRW", "name": "원/달러", "yahoo": "USDKRW=X", "naver": "FX_USDKRW",
     "scale": 1,
     "aliases": ["원달러", "달러", "달러환율", "원달러환율", "미국달러", "usd", "usdkrw",
                 "dollar"]},
    {"code": "JPYKRW", "name": "원/100엔", "yahoo": "JPYKRW=X", "naver": "FX_JPYKRW",
     "scale": 100,
     "aliases": ["원엔", "엔화", "엔환율", "원엔환율", "일본엔", "jpy", "jpykrw", "yen"]},
    {"code": "EURKRW", "name": "원/유로", "yahoo": "EURKRW=X", "naver": "FX_EURKRW",
     "scale": 1,
     "aliases": ["원유로", "유로", "유로화", "유로환율", "eur", "eurkrw", "euro"]},
    {"code": "CNYKRW", "name": "원/위안", "yahoo": "CNYKRW=X", "naver": "FX_CNYKRW",
     "scale": 1, "yahoo_daily_inverse": "KRWCNY=X",
     "aliases": ["원위안", "위안", "위안화", "위안환율", "중국위안", "cny", "cnykrw", "yuan"]},
]

# '환율'만 입력하면 모든 통화쌍을 후보로 보여준다 (검색 전용, 정확일치엔 안 씀).
_GENERIC_KEYWORD = "환율"


def _key(text: str) -> str:
    """비교용 정규화 — 소문자화 + 공백·'/'·'-' 제거. 'USD/KRW'·'원/달러'·'원 달러'
    같은 표기 차이를 흡수한다."""
    return "".join(ch for ch in str(text or "").lower() if ch not in " /-")


def fx_meta(code: str) -> dict | None:
    """저장된 code 로 카탈로그 원본 항목(심볼·배수 포함)을 찾는다. 시세 조회용."""
    target = str(code or "").strip().upper()
    if not target:
        return None
    for fx in FX_CATALOG:
        if fx["code"] == target:
            return fx
    return None


def _as_result(fx: dict) -> dict:
    """카탈로그 항목을 종목 검색 결과와 같은 형태(+type='fx')로 변환."""
    return {
        "code":     fx["code"],
        "name":     fx["name"],
        "market":   "KR",
        "currency": "KRW",
        "type":     "fx",
    }


def fx_by_code(code: str) -> dict | None:
    """저장된 code(USDKRW 등)로 카탈로그 항목을 검색 결과 형태로 반환."""
    fx = fx_meta(code)
    return _as_result(fx) if fx else None


def _names(fx: dict) -> list[str]:
    return [_key(fx["code"]), _key(fx["name"]), *(_key(a) for a in fx["aliases"])]


def search_fx(query: str, limit: int = 5) -> list[dict]:
    """이름/코드/별칭 부분일치로 환율을 검색 (드롭다운 후보용).

    시장(한국/미국)과 무관하게 항상 후보에 넣는다. '원달러 환율'처럼 별칭을 포함한
    입력도 잡도록 양방향 포함을 본다. 특정 통화가 안 잡히고 '환율'만 들어 있으면
    전체 통화쌍을 돌려준다.
    """
    q = _key(query)
    if not q:
        return []
    results: list[dict] = []
    for fx in FX_CATALOG:
        if any(q in n or (len(n) >= 2 and n in q) for n in _names(fx)):
            results.append(_as_result(fx))
    if not results and _GENERIC_KEYWORD in q:
        results = [_as_result(fx) for fx in FX_CATALOG]
    return results[:limit]


def fx_exact_match(query: str) -> dict | None:
    """입력이 환율의 코드/이름/별칭과 정확히 일치하면 해당 환율을 반환.

    드롭다운에서 고르지 않고 '달러'·'엔화'처럼 그대로 입력하고 확인했을 때의
    안전망. 정확일치만 보므로 '달러'가 들어간 ETF 이름을 환율로 오인하지 않는다.
    영문 별칭(usd·eur 등)은 미국 티커와 겹칠 수 있어(예: ProShares 'USD' ETF)
    검색 후보에만 쓰고, 여기서는 코드와 한글 이름·별칭만 본다.
    """
    q = _key(query)
    if not q:
        return None
    for fx in FX_CATALOG:
        names = [_key(fx["code"]), _key(fx["name"]),
                 *(_key(a) for a in fx["aliases"] if not a.isascii())]
        if q in names:
            return _as_result(fx)
    return None
