"""주식 시세/차트 API 호출."""

import requests
from datetime import datetime, timedelta
from time import time
from urllib.parse import quote

from .indices import index_by_code
from .portfolio import is_us_stock

# ─── 공용 HTTP 세션 (TCP/TLS 연결 재사용으로 호출당 100~300ms 절감) ─────────
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
})


def _to_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _epoch_to_date(ts) -> str:
    """Yahoo epoch(초) → 'YYYYMMDD' 거래일 문자열. 변환 실패 시 빈 문자열.
    일봉 timestamp 는 거래소 개장시각(UTC) 기준이라 UTC 날짜가 곧 거래일이다."""
    try:
        if not ts:
            return ""
        return datetime.utcfromtimestamp(int(ts)).strftime("%Y%m%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _signed_naver_value(value, compare_to_previous: dict | None, default: float = 0.0) -> float:
    number = _to_float(value, default)
    compare = compare_to_previous or {}
    code = str(compare.get("code") or "").strip().upper()
    name = str(compare.get("name") or compare.get("text") or "").strip().upper()
    if code in {"5"} or "FALL" in name or "하락" in name:
        return -abs(number)
    return number


def _normalize_us_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper()


def _normalize_naver_trading_session(session_type: str | None) -> str:
    session = str(session_type or "").strip().upper()
    if not session:
        return ""
    if "AFTER" in session or "POST" in session:
        return "POST"
    if "PRE" in session or "BEFORE" in session:
        return "PRE"
    if "REGULAR" in session:
        return "REGULAR"
    return session


def _naver_over_market_session(data: dict) -> dict | None:
    info = data.get("overMarketPriceInfo") or {}
    session = _normalize_naver_trading_session(info.get("tradingSessionType"))
    if session not in {"PRE", "POST"}:
        return None

    price = _to_float(info.get("overPrice"))
    regular_price = _to_float(data.get("closePrice"))
    if price <= 0 or regular_price <= 0:
        return None

    change_price = price - regular_price
    return {
        "session":      session,
        "price":        price,
        "change_price": change_price,
        "change_rate":  change_price / regular_price * 100.0,
        "source":       "NXT",
    }


def _yahoo_market_session(meta: dict, now_ts: int | None = None) -> str:
    """Yahoo chart meta 의 거래 시간대 기준으로 현재 세션을 판정한다."""
    now_ts = int(now_ts if now_ts is not None else time())
    periods = meta.get("currentTradingPeriod") or {}
    for key, session in (("pre", "PRE"), ("regular", "REGULAR"), ("post", "POST")):
        period = periods.get(key) or {}
        start = int(period.get("start") or 0)
        end = int(period.get("end") or 0)
        if start <= now_ts < end:
            return session
    return "CLOSED"


def _last_yahoo_close(result: dict, period_key: str | None = None) -> float:
    quote_data = (result.get("indicators", {}).get("quote") or [{}])[0]
    timestamps = result.get("timestamp") or []
    closes = quote_data.get("close") or []
    start = end = None
    if period_key:
        period = ((result.get("meta") or {}).get("currentTradingPeriod") or {}).get(period_key) or {}
        start = int(period.get("start") or 0)
        end = int(period.get("end") or 0)
    for ts, close in reversed(list(zip(timestamps, closes))):
        price = _to_float(close)
        if price <= 0:
            continue
        if start is not None and not (start <= int(ts) < end):
            continue
        return price
    return 0.0


def _yahoo_extended_session(result: dict, session: str) -> dict | None:
    """프리/애프터마켓 가격이 있으면 표시용 세션 정보를 만든다."""
    meta = result.get("meta", {}) or {}
    # 프리/애프터 표시는 직전 정규장 종가 대비 등락률로 계산한다.
    regular_close = _to_float(meta.get("regularMarketPrice"))
    if regular_close <= 0:
        regular_close = _to_float(meta.get("previousClose") or meta.get("chartPreviousClose"))
    if session == "PRE":
        price = _last_yahoo_close(result, "pre")
    elif session == "POST":
        price = _last_yahoo_close(result, "post")
    else:
        return None
    if price <= 0 or regular_close <= 0:
        return None
    change_price = price - regular_close
    return {
        "session": session,
        "price": price,
        "change_price": change_price,
        "change_rate": change_price / regular_close * 100.0,
    }


def _select_yahoo_market_price(result: dict, session: str) -> float:
    meta = result.get("meta", {}) or {}
    if session == "PRE":
        price = _last_yahoo_close(result, "pre")
        if price > 0:
            return price
    if session == "POST":
        price = _last_yahoo_close(result, "post")
        if price > 0:
            return price
    return _to_float(meta.get("regularMarketPrice"))


def _parse_yahoo_chart(
    symbol: str,
    range_: str = "1d",
    interval: str = "5m",
    include_prepost: bool = False,
) -> dict | None:
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}"
        f"?range={range_}&interval={interval}"
    )
    if include_prepost:
        url += "&includePrePost=true"
    r = _SESSION.get(url, timeout=5)
    if r.status_code != 200:
        return None
    payload = r.json().get("chart", {})
    if payload.get("error"):
        return None
    return (payload.get("result") or [None])[0]


# ─── 네이버 금융 API ───────────────────────────────────────────────────────────
def fetch_stock(code: str) -> dict | None:
    """네이버 금융 모바일 API로 현재가 조회"""
    url = f"https://m.stock.naver.com/api/stock/{code}/basic"
    try:
        r = _SESSION.get(url, timeout=3)
        if r.status_code != 200:
            return None
        d = r.json()
        compare = d.get("compareToPreviousPrice") or {}
        regular_price = _to_float(d.get("closePrice"))
        extended = _naver_over_market_session(d)
        price = extended["price"] if extended else regular_price
        if extended:
            change_price = _signed_naver_value(
                (d.get("overMarketPriceInfo") or {}).get("compareToPreviousClosePrice"),
                (d.get("overMarketPriceInfo") or {}).get("compareToPreviousPrice"),
            )
            change_rate = _signed_naver_value(
                (d.get("overMarketPriceInfo") or {}).get("fluctuationsRatio"),
                (d.get("overMarketPriceInfo") or {}).get("compareToPreviousPrice"),
            )
        else:
            change_price = _signed_naver_value(d.get("compareToPreviousClosePrice"), compare)
            change_rate = _signed_naver_value(d.get("fluctuationsRatio"), compare)
        return {
            "name":         d.get("stockName", code),
            "price":        price,
            "change_rate":  change_rate,
            "change_price": change_price,
            "currency":     "KRW",
            "regular_price": regular_price,
            "market_state":  _normalize_naver_trading_session(
                ((d.get("overMarketPriceInfo") or {}).get("tradingSessionType"))
            ) or "REGULAR",
            "extended":      extended,
        }
    except Exception as e:
        print(f"[fetch_stock] {code} 오류: {e}")
        return None


def search_korean_stocks(query: str, limit: int = 10) -> list[dict]:
    """네이버 금융 자동완성 API로 한국 주식/ETF 후보를 조회.

    반환 항목:
    {'code', 'name', 'market': 'KR', 'exchange'}
    """
    query = str(query or "").strip()
    if not query:
        return []
    url = f"https://ac.stock.naver.com/ac?q={quote(query)}&target=stock"
    try:
        r = _SESSION.get(url, timeout=5)
        if r.status_code != 200:
            return []
        items = r.json().get("items") or []
        results: list[dict] = []
        seen: set[str] = set()
        for item in items:
            if str(item.get("nationCode") or "").upper() != "KOR":
                continue
            code = str(item.get("code") or "").strip().upper()
            if not code or code in seen:
                continue
            seen.add(code)
            results.append({
                "code":     code,
                "name":     item.get("name") or code,
                "market":   "KR",
                "exchange": item.get("typeName") or item.get("typeCode") or "",
            })
            if len(results) >= limit:
                break
        return results
    except Exception as e:
        print(f"[search_korean_stocks] {query} 오류: {e}")
        return []


# ─── 네이버 금융 분봉 차트 API ───────────────────────────────────────────────
def fetch_minute_chart(code: str) -> dict | None:
    """네이버 금융 분봉 API로 당일 1분봉 시계열 조회.
    반환: {'prices': [float, ...], 'open': float} or None"""
    url = f"https://api.stock.naver.com/chart/domestic/item/{code}/minute"
    try:
        r = _SESSION.get(url, timeout=3)
        if r.status_code != 200:
            return None
        data = r.json()
        if not data:
            return None
        return {
            "prices": [float(d["currentPrice"]) for d in data],
            "open":   float(data[0]["openPrice"]),
        }
    except Exception as e:
        print(f"[fetch_minute_chart] {code} 오류: {e}")
        return None


# ─── 네이버 금융 일봉 차트 API (장 외 시간 폴백용) ──────────────────────────
def fetch_daily_chart(code: str, days: int = 45, max_candles: int = 30) -> dict | None:
    """최근 N 캘린더일 일봉 OHLC 시계열 조회.
    분봉이 비어있는 장 외 시간/주말/공휴일에 캔들 차트로 표시할 용도.
    반환: {'candles': [{'open','high','low','close','volume'}, ...]} or None"""
    end = datetime.now()
    start = end - timedelta(days=days)
    url = (
        f"https://api.stock.naver.com/chart/domestic/item/{code}/day"
        f"?startDateTime={start.strftime('%Y%m%d')}"
        f"&endDateTime={end.strftime('%Y%m%d')}"
    )
    try:
        r = _SESSION.get(url, timeout=3)
        if r.status_code != 200:
            return None
        data = r.json()
        if not data:
            return None
        candles = [
            {
                "date":  str(d.get("localDate") or ""),
                "open":  float(d["openPrice"]),
                "high":  float(d["highPrice"]),
                "low":   float(d["lowPrice"]),
                "close": float(d["closePrice"]),
                "volume": _to_float(d.get("accumulatedTradingVolume")),
            }
            for d in data
        ]
        if max_candles > 0:
            candles = candles[-max_candles:]
        return {"candles": candles}
    except Exception as e:
        print(f"[fetch_daily_chart] {code} 오류: {e}")
        return None


# ─── Yahoo Finance 원달러 환율 API ─────────────────────────────────────────
def fetch_usd_krw_rate() -> dict | None:
    """Yahoo Finance API로 원달러 환율(USD/KRW)을 조회한다.

    반환:
    {'pair', 'rate', 'change_rate', 'change_price', 'currency', 'source'}
    """
    try:
        result = _parse_yahoo_chart("USDKRW=X", range_="1d", interval="5m")
        if not result:
            return _fetch_usd_krw_rate_naver()
        meta = result.get("meta", {}) or {}
        rate = _to_float(meta.get("regularMarketPrice"))
        prev_close = _to_float(meta.get("previousClose") or meta.get("chartPreviousClose"))
        if rate <= 0:
            closes = [
                _to_float(v)
                for v in (result.get("indicators", {}).get("quote", [{}])[0].get("close") or [])
                if v is not None
            ]
            closes = [v for v in closes if v > 0]
            rate = closes[-1] if closes else 0.0
        if rate <= 0:
            return _fetch_usd_krw_rate_naver()

        change_price = rate - prev_close if prev_close else 0.0
        change_rate = (change_price / prev_close * 100.0) if prev_close else 0.0
        return {
            "pair":         "USD/KRW",
            "rate":         rate,
            "change_rate":  change_rate,
            "change_price": change_price,
            "currency":     "KRW",
            "source":       "Yahoo Finance",
        }
    except Exception as e:
        print(f"[fetch_usd_krw_rate] 오류: {e}")
        return _fetch_usd_krw_rate_naver()


def fetch_usd_krw_chart(range_: str = "1d", interval: str = "5m") -> dict | None:
    """Yahoo Finance API로 원달러 환율 시계열을 조회한다.
    반환: {'rates': [float, ...], 'open': float} or None"""
    try:
        result = _parse_yahoo_chart("USDKRW=X", range_=range_, interval=interval)
        if not result:
            return None
        quote_data = (result.get("indicators", {}).get("quote") or [{}])[0]
        rates = [_to_float(v) for v in (quote_data.get("close") or []) if v is not None]
        opens = [_to_float(v) for v in (quote_data.get("open") or []) if v is not None]
        rates = [v for v in rates if v > 0]
        opens = [v for v in opens if v > 0]
        if len(rates) < 2:
            return None
        return {
            "rates": rates,
            "open":  opens[0] if opens else rates[0],
        }
    except Exception as e:
        print(f"[fetch_usd_krw_chart] 오류: {e}")
        return None


def _fetch_usd_krw_rate_naver() -> dict | None:
    url = "https://api.stock.naver.com/marketindex/exchange/FX_USDKRW"
    try:
        r = _SESSION.get(url, timeout=5)
        if r.status_code != 200:
            return None
        info = (r.json().get("exchangeInfo") or {})
        rate = _to_float(info.get("closePrice"))
        if rate <= 0:
            return None
        change_price = _to_float(info.get("fluctuations"))
        change_rate = _to_float(info.get("fluctuationsRatio"))
        fluct_type = (info.get("fluctuationsType") or {}).get("code")
        if str(fluct_type) in {"5", "FALLING"}:
            change_price = -abs(change_price)
            change_rate = -abs(change_rate)
        return {
            "pair":         "USD/KRW",
            "rate":         rate,
            "change_rate":  change_rate,
            "change_price": change_price,
            "currency":     "KRW",
            "source":       "Naver Finance",
        }
    except Exception as e:
        print(f"[_fetch_usd_krw_rate_naver] 오류: {e}")
        return None


# ─── Yahoo Finance 미국 주식 API ────────────────────────────────────────────
def search_us_stocks(query: str, limit: int = 10) -> list[dict]:
    """Yahoo Finance 검색 API로 미국 주식/ETF 후보를 조회한다.

    반환 항목:
    {'symbol', 'name', 'exchange', 'market', 'currency'}
    """
    query = str(query or "").strip()
    if not query:
        return []

    url = (
        "https://query2.finance.yahoo.com/v1/finance/search"
        f"?q={quote(query)}&quotesCount={int(limit)}&newsCount=0"
    )
    try:
        r = _SESSION.get(url, timeout=5)
        if r.status_code != 200:
            return _search_us_stocks_naver(query, limit)
        data = r.json()
        results: list[dict] = []
        seen: set[str] = set()
        for item in data.get("quotes", []):
            symbol = _normalize_us_symbol(item.get("symbol"))
            if not symbol or symbol in seen:
                continue
            quote_type = str(item.get("quoteType") or "").upper()
            if quote_type and quote_type not in {"EQUITY", "ETF"}:
                continue
            exchange = str(item.get("exchange") or item.get("exchDisp") or "").upper()
            if exchange and exchange not in {"NMS", "NYQ", "ASE", "NGM", "NCM", "PCX", "BTS"}:
                continue
            seen.add(symbol)
            results.append({
                "symbol":   symbol,
                "code":     symbol,
                "name":     item.get("shortname") or item.get("longname") or symbol,
                "exchange": item.get("exchDisp") or exchange,
                "market":   "US",
                "currency": item.get("currency") or "USD",
            })
            if len(results) >= limit:
                break
        return results or _search_us_stocks_naver(query, limit)
    except Exception as e:
        print(f"[search_us_stocks] {query} 오류: {e}")
        return _search_us_stocks_naver(query, limit)


def fetch_us_stock(symbol: str) -> dict | None:
    """Yahoo Finance chart API로 미국 주식 현재가 조회.

    반환 형태는 국내 fetch_stock 과 맞춘다.
    {'name', 'price', 'change_rate', 'change_price'}
    """
    symbol = _normalize_us_symbol(symbol)
    if not symbol:
        return None

    try:
        result = _parse_yahoo_chart(symbol, range_="1d", interval="5m", include_prepost=True)
        if not result:
            return _fetch_us_stock_naver(symbol)
        meta = result.get("meta", {}) or {}
        session = _yahoo_market_session(meta)
        price = _select_yahoo_market_price(result, session)
        prev_close = _to_float(meta.get("previousClose") or meta.get("chartPreviousClose"))
        if price <= 0:
            price = _last_yahoo_close(result)
        if price <= 0:
            return _fetch_us_stock_naver(symbol)

        change_price = price - prev_close if prev_close else 0.0
        change_rate = (change_price / prev_close * 100.0) if prev_close else 0.0
        name = meta.get("shortName") or meta.get("longName") or symbol
        return {
            "name":         name,
            "price":        price,
            "change_rate":  change_rate,
            "change_price": change_price,
            "currency":     meta.get("currency") or "USD",
            "regular_price": _to_float(meta.get("regularMarketPrice")),
            "market_state":  session,
            "extended":      _yahoo_extended_session(result, session),
        }
    except Exception as e:
        print(f"[fetch_us_stock] {symbol} 오류: {e}")
        return _fetch_us_stock_naver(symbol)


def fetch_us_minute_chart(symbol: str) -> dict | None:
    """Yahoo Finance 1분봉 API로 당일 정규장 시계열 조회.
    반환: {'prices': [float, ...], 'open': float} or None"""
    symbol = _normalize_us_symbol(symbol)
    if not symbol:
        return None

    try:
        result = _parse_yahoo_chart(symbol, range_="1d", interval="1m")
        if not result:
            return _fetch_us_minute_chart_naver(symbol)
        quote_data = (result.get("indicators", {}).get("quote") or [{}])[0]
        prices = [_to_float(v) for v in (quote_data.get("close") or []) if v is not None]
        opens = [_to_float(v) for v in (quote_data.get("open") or []) if v is not None]
        prices = [p for p in prices if p > 0]
        opens = [p for p in opens if p > 0]
        if len(prices) < 2:
            return _fetch_us_minute_chart_naver(symbol)
        return {
            "prices": prices,
            "open":   opens[0] if opens else prices[0],
        }
    except Exception as e:
        print(f"[fetch_us_minute_chart] {symbol} 오류: {e}")
        return _fetch_us_minute_chart_naver(symbol)


def fetch_us_daily_chart(symbol: str, range_: str = "3mo", max_candles: int = 30) -> dict | None:
    """Yahoo Finance 일봉 OHLC 시계열 조회.
    반환: {'candles': [{'open','high','low','close','volume'}, ...]} or None"""
    symbol = _normalize_us_symbol(symbol)
    if not symbol:
        return None

    try:
        result = _parse_yahoo_chart(symbol, range_=range_, interval="1d")
        if not result:
            return _fetch_us_daily_chart_naver(symbol, days=_days_for_candles(max_candles), max_candles=max_candles)
        quote_data = (result.get("indicators", {}).get("quote") or [{}])[0]
        timestamps = result.get("timestamp") or []
        candles = []
        volumes = quote_data.get("volume") or []
        for i, (open_, high, low, close) in enumerate(zip(
            quote_data.get("open") or [],
            quote_data.get("high") or [],
            quote_data.get("low") or [],
            quote_data.get("close") or [],
        )):
            if None in {open_, high, low, close}:
                continue
            candle = {
                "date":  _epoch_to_date(timestamps[i] if i < len(timestamps) else None),
                "open":  _to_float(open_),
                "high":  _to_float(high),
                "low":   _to_float(low),
                "close": _to_float(close),
                "volume": _to_float(volumes[i]) if i < len(volumes) else 0.0,
            }
            if candle["open"] > 0 and candle["high"] > 0 and candle["low"] > 0 and candle["close"] > 0:
                candles.append(candle)
        if not candles:
            return _fetch_us_daily_chart_naver(symbol, days=_days_for_candles(max_candles), max_candles=max_candles)
        if max_candles > 0:
            candles = candles[-max_candles:]
        return {"candles": candles}
    except Exception as e:
        print(f"[fetch_us_daily_chart] {symbol} 오류: {e}")
        return _fetch_us_daily_chart_naver(symbol, days=_days_for_candles(max_candles), max_candles=max_candles)


# ─── 네이버 해외 주식 API 폴백 ─────────────────────────────────────────────
def _search_us_stocks_naver(query: str, limit: int = 10) -> list[dict]:
    urls = [
        "https://m.stock.naver.com/api/search/all"
        f"?keyword={quote(query)}&page=1&pageSize={int(limit)}",
        "https://api.stock.naver.com/stock/search"
        f"?keyword={quote(query)}&page=1&pageSize={int(limit)}",
    ]
    for url in urls:
        try:
            r = _SESSION.get(url, timeout=5)
            if r.status_code != 200:
                continue
            payload = r.json()
            items = payload.get("stocks") or payload.get("items") or payload.get("result") or []
            if isinstance(items, dict):
                items = items.get("stocks") or items.get("items") or []
            results = []
            for item in items:
                symbol = _normalize_us_symbol(
                    item.get("symbolCode") or item.get("symbol") or item.get("code")
                )
                reuters_code = _normalize_us_symbol(item.get("reutersCode") or item.get("reuters_code"))
                name = item.get("stockName") or item.get("name") or item.get("korName") or symbol
                exchange = item.get("exchangeName") or item.get("exchange") or ""
                if not symbol:
                    continue
                results.append({
                    "symbol":       symbol,
                    "code":         symbol,
                    "reuters_code": reuters_code or symbol,
                    "name":         name,
                    "exchange":     exchange,
                    "market":       "US",
                    "currency":     "USD",
                })
                if len(results) >= limit:
                    break
            if results:
                return results
        except Exception as e:
            print(f"[_search_us_stocks_naver] {query} 오류: {e}")
    return []


def _naver_us_code_candidates(symbol: str) -> list[str]:
    symbol = _normalize_us_symbol(symbol)
    candidates = [symbol]
    if "." not in symbol:
        candidates.extend([f"{symbol}.O", f"{symbol}.K", f"{symbol}.N", f"{symbol}.A"])
    return candidates


def _fetch_us_stock_naver(symbol: str) -> dict | None:
    for code in _naver_us_code_candidates(symbol):
        for base in ("https://m.stock.naver.com/api/stock", "https://api.stock.naver.com/stock"):
            try:
                r = _SESSION.get(f"{base}/{quote(code)}/basic", timeout=5)
                if r.status_code != 200:
                    continue
                d = r.json()
                price = _to_float(d.get("closePrice") or d.get("lastPrice") or d.get("now"))
                if price <= 0:
                    continue
                return {
                    "name":         d.get("stockName") or d.get("symbolName") or symbol,
                    "price":        price,
                    "change_rate":  _to_float(d.get("fluctuationsRatio") or d.get("compareToPreviousClosePriceRate")),
                    "change_price": _to_float(d.get("compareToPreviousClosePrice") or d.get("compareToPreviousClose")),
                    "currency":     "USD",
                }
            except Exception as e:
                print(f"[_fetch_us_stock_naver] {code} 오류: {e}")
    return None


def _fetch_us_minute_chart_naver(symbol: str) -> dict | None:
    for code in _naver_us_code_candidates(symbol):
        for namespace in ("foreign", "worldstock"):
            url = f"https://api.stock.naver.com/chart/{namespace}/item/{quote(code)}/minute"
            try:
                r = _SESSION.get(url, timeout=5)
                if r.status_code != 200:
                    continue
                data = r.json()
                if not data:
                    continue
                prices = [_to_float(d.get("currentPrice") or d.get("closePrice")) for d in data]
                prices = [p for p in prices if p > 0]
                if len(prices) < 2:
                    continue
                open_price = _to_float(data[0].get("openPrice"), prices[0])
                return {"prices": prices, "open": open_price}
            except Exception as e:
                print(f"[_fetch_us_minute_chart_naver] {code} 오류: {e}")
    return None


def _fetch_us_daily_chart_naver(symbol: str, days: int = 90, max_candles: int = 30) -> dict | None:
    end = datetime.now()
    start = end - timedelta(days=days)
    for code in _naver_us_code_candidates(symbol):
        for namespace in ("foreign", "worldstock"):
            url = (
                f"https://api.stock.naver.com/chart/{namespace}/item/{quote(code)}/day"
                f"?startDateTime={start.strftime('%Y%m%d')}"
                f"&endDateTime={end.strftime('%Y%m%d')}"
            )
            try:
                r = _SESSION.get(url, timeout=5)
                if r.status_code != 200:
                    continue
                data = r.json()
                if not data:
                    continue
                candles = []
                for d in data:
                    candle = {
                        "date":  str(d.get("localDate") or ""),
                        "open":  _to_float(d.get("openPrice")),
                        "high":  _to_float(d.get("highPrice")),
                        "low":   _to_float(d.get("lowPrice")),
                        "close": _to_float(d.get("closePrice") or d.get("currentPrice")),
                        "volume": _to_float(d.get("accumulatedTradingVolume")),
                    }
                    if candle["open"] > 0 and candle["high"] > 0 and candle["low"] > 0 and candle["close"] > 0:
                        candles.append(candle)
                if candles:
                    if max_candles > 0:
                        candles = candles[-max_candles:]
                    return {"candles": candles}
            except Exception as e:
                print(f"[_fetch_us_daily_chart_naver] {code} 오류: {e}")
    return None


# ─── 지수 시세 (코스피/코스닥 = 네이버, 해외지수 = Yahoo) ──────────────────────
# 지수는 관심종목 전용이다. 국내 지수는 네이버 '지수' API(주식과 엔드포인트가
# 다름)를, 해외 지수는 Yahoo 심볼(^GSPC 등)을 그대로 쓰는 기존 미국 주식 경로를
# 재사용한다.
def _fetch_kr_index(code: str) -> dict | None:
    """네이버 금융 지수 API로 국내 지수(코스피/코스닥) 현재가 조회.
    반환 형태는 fetch_stock 과 동일하게 맞춘다."""
    url = f"https://m.stock.naver.com/api/index/{quote(code)}/basic"
    try:
        r = _SESSION.get(url, timeout=3)
        if r.status_code != 200:
            return None
        d = r.json()
        price = _to_float(d.get("closePrice"))
        if price <= 0:
            return None
        compare = d.get("compareToPreviousPrice") or {}
        return {
            "name":          d.get("stockName") or code,
            "price":         price,
            "change_rate":   _signed_naver_value(d.get("fluctuationsRatio"), compare),
            "change_price":  _signed_naver_value(d.get("compareToPreviousClosePrice"), compare),
            "currency":      "KRW",
            "regular_price": price,
            "market_state":  "REGULAR",
            "extended":      None,
        }
    except Exception as e:
        print(f"[_fetch_kr_index] {code} 오류: {e}")
        return None


def _fetch_kr_index_daily(code: str, days: int = 45, max_candles: int = 30) -> dict | None:
    """네이버 국내 지수 일봉 OHLC 시계열 (fetch_daily_chart 의 지수판)."""
    end = datetime.now()
    start = end - timedelta(days=days)
    url = (
        f"https://api.stock.naver.com/chart/domestic/index/{quote(code)}/day"
        f"?startDateTime={start.strftime('%Y%m%d')}"
        f"&endDateTime={end.strftime('%Y%m%d')}"
    )
    try:
        r = _SESSION.get(url, timeout=3)
        if r.status_code != 200:
            return None
        data = r.json()
        if not data:
            return None
        candles = [
            {
                "date":  str(d.get("localDate") or ""),
                "open":  _to_float(d.get("openPrice")),
                "high":  _to_float(d.get("highPrice")),
                "low":   _to_float(d.get("lowPrice")),
                "close": _to_float(d.get("closePrice")),
                "volume": _to_float(d.get("accumulatedTradingVolume")),
            }
            for d in data
        ]
        candles = [
            c for c in candles
            if c["open"] > 0 and c["high"] > 0 and c["low"] > 0 and c["close"] > 0
        ]
        if not candles:
            return None
        if max_candles > 0:
            candles = candles[-max_candles:]
        return {"candles": candles}
    except Exception as e:
        print(f"[_fetch_kr_index_daily] {code} 오류: {e}")
        return None


def _index_is_us(code: str, market: str | None) -> bool:
    catalog = index_by_code(code)
    resolved = str(market or (catalog or {}).get("market") or "KR").strip().upper()
    return resolved == "US"


def fetch_index(code: str, market: str | None = None) -> dict | None:
    """지수 현재가 — 국내는 네이버, 해외(^GSPC 등)는 Yahoo 경로로 라우팅.
    카탈로그에 있으면 표시명을 카탈로그 한글 이름으로 통일한다."""
    if _index_is_us(code, market):
        result = fetch_us_stock(code)
    else:
        result = _fetch_kr_index(code)
    catalog = index_by_code(code)
    if result and catalog:
        result["name"] = catalog["name"]
    return result


# 관심종목 hover 확대 팝업용 일봉 개수 — 최대 6개월(약 126거래일) 표시 + 60일선 lookback 여유
WATCH_POPUP_CANDLES = 190

# 주봉·월봉 표시용 — 더 긴 일봉 이력(약 2년). 봉주기가 주/월일 때만 이 길이로 조회한다.
WATCH_POPUP_CANDLES_LONG = 500


def _days_for_candles(max_candles: int) -> int:
    """필요 거래일 수 → 조회 달력일 수(주말·공휴일 고려 약 1.5배), 최소 45일."""
    return max(45, round(max_candles * 1.5))


def _yahoo_range_for_candles(max_candles: int) -> str:
    """필요 캔들 수 → Yahoo range 버킷."""
    if max_candles <= 22:
        return "1mo"
    if max_candles <= 66:
        return "3mo"
    if max_candles <= 126:
        return "6mo"
    if max_candles <= 252:
        return "1y"
    return "2y"


def fetch_index_daily(code: str, market: str | None = None, max_candles: int = 30) -> dict | None:
    """지수 일봉 캔들 — 국내는 네이버, 해외는 Yahoo 경로로 라우팅."""
    if _index_is_us(code, market):
        return fetch_us_daily_chart(
            code, range_=_yahoo_range_for_candles(max_candles), max_candles=max_candles
        )
    return _fetch_kr_index_daily(
        code, days=_days_for_candles(max_candles), max_candles=max_candles
    )


# ─── 관심종목 시세 디스패치 (지수/국내/해외 통합) ─────────────────────────────
def fetch_watch_quote(item: dict) -> dict | None:
    """관심종목 한 항목의 현재가를 타입·시장에 맞게 조회한다 (폴러 공용)."""
    code = str(item.get("code") or "").strip()
    if str(item.get("type") or "").strip().lower() == "index":
        return fetch_index(code, item.get("market"))
    return fetch_us_stock(code) if is_us_stock(item) else fetch_stock(code)


def fetch_watch_daily(item: dict, max_candles: int = 30) -> dict | None:
    """관심종목 한 항목의 일봉 캔들을 타입·시장에 맞게 조회한다 (폴러 공용).
    max_candles 를 키우면 더 긴 이력(확대 팝업의 3개월 + 이동평균선용)을 받는다."""
    code = str(item.get("code") or "").strip()
    if str(item.get("type") or "").strip().lower() == "index":
        return fetch_index_daily(code, item.get("market"), max_candles=max_candles)
    if is_us_stock(item):
        return fetch_us_daily_chart(
            code, range_=_yahoo_range_for_candles(max_candles), max_candles=max_candles
        )
    return fetch_daily_chart(code, days=_days_for_candles(max_candles), max_candles=max_candles)
