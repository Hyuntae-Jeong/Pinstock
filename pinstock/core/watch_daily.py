"""관심종목 일봉 이력 캐시 — 5년치 이력은 가끔, 최근 구간만 자주 받아 이어 붙인다.

관심종목 폴러는 60초마다 돈다(Windows 는 UI 스레드에서 동기 호출). 매번 5년치(약 1,260봉)를
받으면 종목이 많을 때 화면이 잠깐씩 멈출 수 있으므로:
- 처음 / FULL_REFRESH_SEC 가 지났을 때만 전체 이력(WATCH_HISTORY_CANDLES)을 받고
- 그 사이에는 최근 WATCH_RECENT_CANDLES 개만 받아 캐시 꼬리에 덮어 붙인다.

캐시는 모듈 전역(종목 키별)이라 관심종목 목록이 다시 그려져 행이 새로 만들어져도 유지된다.
"""

import time

from .api import fetch_watch_daily

WATCH_HISTORY_CANDLES = 1260     # 5년 ≈ 252거래일 × 5 — 확대 팝업 일봉 스크롤·주/월봉 집계용
WATCH_RECENT_CANDLES = 30        # 매 폴링마다 받는 최근 구간 (오늘 봉 갱신 + 며칠 공백 메움)
FULL_REFRESH_SEC = 30 * 60       # 전체 이력 재조회 주기 — 액면분할·수정주가 반영도 이 주기로

_cache: dict[tuple, dict] = {}   # key → {"candles": list[dict], "full_at": float}


def _key(item: dict) -> tuple:
    return (
        str(item.get("type") or "").strip().lower(),
        str(item.get("market") or "").strip().upper(),
        str(item.get("code") or "").strip().upper(),
    )


def _date(c: dict) -> str:
    return str(c.get("date") or "").replace("-", "")


def merge_daily(history: list[dict], recent: list[dict]) -> list[dict] | None:
    """history 꼬리를 recent 로 교체한 새 리스트. 두 구간이 날짜로 이어지지 않으면 None
    (그 사이 빠진 봉이 생기므로 호출측이 전체를 다시 받아야 한다)."""
    if not history:
        return None
    if not recent:
        return list(history)
    first = _date(recent[0])
    if not first or not _date(history[-1]) or first > _date(history[-1]):
        return None   # 겹치는 날이 없음 → 공백 가능
    head = [c for c in history if _date(c) < first]
    return head + list(recent)


def get_watch_daily(item: dict, now: float | None = None) -> list[dict] | None:
    """관심종목 일봉(과거→최근, 집계 전)을 돌려준다. 조회가 모두 실패하면 캐시된 이력,
    그것도 없으면 None. 반환 리스트는 매번 새 객체라 호출측이 보관해도 안전하다."""
    now = time.time() if now is None else now
    key = _key(item)
    entry = _cache.get(key)

    if entry and now - entry["full_at"] < FULL_REFRESH_SEC:
        recent = (fetch_watch_daily(item, max_candles=WATCH_RECENT_CANDLES) or {}).get("candles")
        if not recent:
            return list(entry["candles"])          # 일시 실패 → 기존 이력 유지
        merged = merge_daily(entry["candles"], recent)
        if merged is not None:
            entry["candles"] = merged
            return list(merged)
        # 이어지지 않음(오래 잠들었다 깨어남 등) → 아래에서 전체 재조회

    full = (fetch_watch_daily(item, max_candles=WATCH_HISTORY_CANDLES) or {}).get("candles")
    if full:
        _cache[key] = {"candles": list(full), "full_at": now}
        return list(full)
    return list(entry["candles"]) if entry else None


def clear_cache() -> None:
    _cache.clear()
