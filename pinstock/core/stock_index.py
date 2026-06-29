"""한국 주식·ETF 전체 종목 마스터 로컬 인덱스 (중간 검색용).

네이버 자동완성 API는 접두/토큰 기반이라 이름 중간(substring) 검색이 약하다
(예: 'S&P500' → 결과 없음). 코스피·코스닥 전체 종목과 ETF 전체 목록을 한 번
받아 디스크에 캐시하고, 검색 시 로컬에서 부분일치로 후보를 보강한다.

수집은 앱 시작 시 백그라운드 스레드에서 1회 수행하고, 캐시는 하루 단위로
갱신한다. 네트워크 실패 시 조용히 비활성(검색은 기존 자동완성만 사용)된다.
"""

import json
import threading
from time import time

import requests

from .storage import _config_dir

_CACHE_FILE = _config_dir() / "kr_stock_index.json"
_CACHE_TTL = 24 * 3600   # 1일. 신규 상장/상폐 반영 주기로 충분.

_STOCK_URL = "https://m.stock.naver.com/api/stocks/marketValue/{market}?page={page}&pageSize=100"
_ETF_URL   = "https://finance.naver.com/api/sise/etfItemList.nhn"

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
})

# 메모리 인덱스: [{'code','name','name_key','market','exchange'}]
_index: list[dict] = []
_lock = threading.Lock()
_started = False   # 백그라운드 수집 중복 시작 방지


def _normalize_key(text: str) -> str:
    """검색 비교용 정규화 — 소문자화 + 공백 제거. 'TIGER 미국S&P500' 의 키는
    'tiger미국s&p500' 이 되어, 'S&P500'·'s&p 500' 등 표기/공백 차이를 흡수한다."""
    return "".join(str(text or "").lower().split())


def _fetch_stocks(market: str) -> list[dict]:
    """코스피/코스닥 전체 종목을 100개씩 페이지네이션으로 모은다(최대 pageSize=100)."""
    out: list[dict] = []
    seen: set[str] = set()
    page = 1
    while page <= 60:   # 안전 상한 (코스피 25 + 코스닥 19 페이지 수준)
        r = _SESSION.get(_STOCK_URL.format(market=market, page=page), timeout=8)
        if r.status_code != 200:
            break
        data = r.json()
        stocks = data.get("stocks") or []
        if not stocks:
            break
        for s in stocks:
            code = str(s.get("itemCode") or "").strip().upper()
            name = str(s.get("stockName") or "").strip()
            if not code or not name or code in seen:
                continue
            seen.add(code)
            out.append({
                "code": code, "name": name, "name_key": _normalize_key(name),
                "market": "KR", "exchange": market,
            })
        total = int(data.get("totalCount") or 0)
        if total and len(out) >= total:
            break
        page += 1
    return out


def _fetch_etfs() -> list[dict]:
    """네이버 ETF 전체 목록(1회 요청)."""
    r = _SESSION.get(_ETF_URL, timeout=8)
    if r.status_code != 200:
        return []
    items = (r.json().get("result") or {}).get("etfItemList") or []
    out: list[dict] = []
    seen: set[str] = set()
    for it in items:
        code = str(it.get("itemcode") or "").strip().upper()
        name = str(it.get("itemname") or "").strip()
        if not code or not name or code in seen:
            continue
        seen.add(code)
        out.append({
            "code": code, "name": name, "name_key": _normalize_key(name),
            "market": "KR", "exchange": "ETF",
        })
    return out


def _fetch_all() -> list[dict]:
    """코스피 + 코스닥 + ETF 전체. 코드 중복은 먼저 본 항목을 유지.
    순수 로컬 부분일치 순위에서 실제 종목(시총순)이 ETF보다 앞서도록
    코스피·코스닥을 먼저 넣는다(예: '하이닉스' → SK하이닉스가 ETF보다 위)."""
    merged: list[dict] = []
    seen: set[str] = set()
    for part in (_fetch_stocks("KOSPI"), _fetch_stocks("KOSDAQ"), _fetch_etfs()):
        for it in part:
            if it["code"] in seen:
                continue
            seen.add(it["code"])
            merged.append(it)
    return merged


def _load_cache() -> tuple[list[dict], float]:
    try:
        raw = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        items = raw.get("items") or []
        ts = float(raw.get("fetched_at") or 0)
        # name_key 누락(구버전 캐시) 보강
        for it in items:
            if "name_key" not in it:
                it["name_key"] = _normalize_key(it.get("name", ""))
        return items, ts
    except Exception:
        return [], 0.0


def _save_cache(items: list[dict]) -> None:
    try:
        _CACHE_FILE.write_text(
            json.dumps({"fetched_at": time(), "items": items}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[stock_index] 캐시 저장 실패: {e}")


def _refresh() -> None:
    """캐시가 신선하면 그대로 쓰고, 오래됐거나 없으면 새로 수집한다."""
    global _index
    items, ts = _load_cache()
    if items and (time() - ts) < _CACHE_TTL:
        with _lock:
            _index = items
        return
    # 캐시가 있으면 일단 그것으로 검색 가능하게 채워두고(첫 검색 빈손 방지),
    # 네트워크로 최신본을 받아 교체한다.
    if items:
        with _lock:
            _index = items
    try:
        fresh = _fetch_all()
    except Exception as e:
        print(f"[stock_index] 수집 실패(자동완성만 사용): {e}")
        return
    if fresh:
        with _lock:
            _index = fresh
        _save_cache(fresh)


def start_background_refresh() -> None:
    """앱 시작 시 1회 호출. 데몬 스레드에서 인덱스를 채운다(중복 시작 무시)."""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_refresh, name="stock-index-refresh", daemon=True).start()


def search(query: str, limit: int = 15) -> list[dict]:
    """이름/코드 부분일치로 로컬 인덱스를 검색. 접두 일치를 부분 일치보다 앞에 둔다.

    반환 항목: {'code','name','market','exchange'} (검색 결과 표준형, name_key 제외).
    인덱스가 아직 안 채워졌으면 빈 리스트(= 기존 자동완성만 노출).
    """
    q = _normalize_key(query)
    if not q:
        return []
    with _lock:
        snapshot = _index
    if not snapshot:
        return []
    starts: list[dict] = []
    contains: list[dict] = []
    for it in snapshot:
        key = it["name_key"]
        if key.startswith(q) or it["code"].lower().startswith(q):
            starts.append(it)
        elif q in key:
            contains.append(it)
        if len(starts) >= limit:
            break
    ranked = (starts + contains)[:limit]
    return [
        {"code": it["code"], "name": it["name"],
         "market": it["market"], "exchange": it["exchange"]}
        for it in ranked
    ]
