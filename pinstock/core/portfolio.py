"""포트폴리오 평가금액/손익 계산."""


def is_us_stock(stock: dict) -> bool:
    market = str(stock.get("market") or "").strip().upper()
    currency = str(stock.get("currency") or "").strip().upper()
    return market == "US" or currency == "USD"


def is_index(item: dict) -> bool:
    """관심종목 항목이 지수(코스피/코스닥/해외 지수)인지 여부.

    지수는 관심종목에만 존재하며 가격 표시 포맷·시세 라우팅이 종목과 다르다.
    """
    return str(item.get("type") or "").strip().lower() == "index"


def _to_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _to_int(value, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return default


def stock_metrics(
    stock: dict,
    current_price: float | int | None = None,
    usd_krw_rate: float | int | None = None,
) -> dict:
    """단일 종목의 원화 기준 투자/평가/손익을 계산한다.

    한국 주식은 기존처럼 평단가/현재가를 원화로 계산한다. 미국 주식은
    avg_price/current_price 를 USD 로 보고, 매수환율(buy_exchange_rate)과
    현재환율(usd_krw_rate)을 적용해 원화 기준 손익을 계산한다.
    """
    avg_price = _to_float(stock.get("avg_price"))
    quantity = _to_float(stock.get("quantity"))
    price = _to_float(current_price, avg_price) or avg_price

    if not is_us_stock(stock):
        invest = avg_price * quantity
        eval_ = price * quantity
        profit = eval_ - invest
        profit_rate = (profit / invest * 100.0) if invest else 0.0
        return {
            "invest": round(invest),
            "eval": round(eval_),
            "profit": round(profit),
            "profit_rate": profit_rate,
            "profit_rate_stock": profit_rate,
            "stock_profit": round(profit),
            "fx_profit": 0,
            "current_rate": 1.0,
            "buy_rate": 1.0,
        }

    current_rate = _to_float(usd_krw_rate)
    buy_rate = _to_float(stock.get("buy_exchange_rate"), current_rate)
    if current_rate <= 0:
        current_rate = buy_rate
    if buy_rate <= 0:
        buy_rate = current_rate
    if current_rate <= 0:
        current_rate = 1.0
    if buy_rate <= 0:
        buy_rate = 1.0

    invest = avg_price * quantity * buy_rate
    eval_ = price * quantity * current_rate
    profit = eval_ - invest
    stock_profit = (price - avg_price) * quantity * buy_rate
    fx_profit = price * quantity * (current_rate - buy_rate)
    profit_rate = (profit / invest * 100.0) if invest else 0.0
    profit_rate_stock = ((price - avg_price) / avg_price * 100.0) if avg_price else 0.0

    return {
        "invest": round(invest),
        "eval": round(eval_),
        "profit": round(profit),
        "profit_rate": profit_rate,
        "profit_rate_stock": profit_rate_stock,
        "stock_profit": round(stock_profit),
        "fx_profit": round(fx_profit),
        "current_rate": current_rate,
        "buy_rate": buy_rate,
    }


def buy_preview(
    stock: dict,
    add_quantity: float | int,
    current_price: float | int | None = None,
    usd_krw_rate: float | int | None = None,
) -> dict:
    """현재가로 추가 매수했을 때의 예상 보유 상태와 손익 지표를 계산한다."""
    avg_price = _to_float(stock.get("avg_price"))
    quantity = _to_float(stock.get("quantity"))
    add_qty = max(0.0, _to_float(add_quantity))
    price = _to_float(current_price, avg_price) or avg_price
    new_quantity = quantity + add_qty
    if new_quantity > 0:
        new_avg_price = ((avg_price * quantity) + (price * add_qty)) / new_quantity
    else:
        new_avg_price = avg_price

    preview_stock = dict(stock)
    preview_stock["avg_price"] = new_avg_price
    preview_stock["quantity"] = new_quantity
    if is_us_stock(stock) and add_qty > 0 and new_avg_price > 0 and new_quantity > 0:
        current_metrics = stock_metrics(stock, price, usd_krw_rate)
        current_rate = current_metrics["current_rate"]
        old_invest = avg_price * quantity * current_metrics["buy_rate"]
        add_invest = price * add_qty * current_rate
        new_buy_rate = (old_invest + add_invest) / (new_avg_price * new_quantity)
        if new_buy_rate > 0:
            preview_stock["buy_exchange_rate"] = round(new_buy_rate, 4)

    current_metrics = stock_metrics(stock, price, usd_krw_rate)
    preview_metrics = stock_metrics(preview_stock, price, usd_krw_rate)
    return {
        "stock": preview_stock,
        "current_metrics": current_metrics,
        "preview_metrics": preview_metrics,
        "current_avg_price": avg_price,
        "preview_avg_price": new_avg_price,
        "current_quantity": quantity,
        "preview_quantity": new_quantity,
        "current_price": price,
        "avg_price_delta": new_avg_price - avg_price,
        "profit_rate_delta": (
            preview_metrics["profit_rate"] - current_metrics["profit_rate"]
        ),
    }


def portfolio_totals(
    stocks: list[dict],
    current_prices: dict | None = None,
    usd_krw_rate: float | int | None = None,
    *,
    include_hidden: bool = False,
) -> dict:
    prices = current_prices or {}
    total_invest = 0
    total_eval = 0
    holdings: list[dict] = []

    for stock in stocks:
        if not include_hidden and stock.get("hidden", False):
            continue
        code = stock.get("code")
        metrics = stock_metrics(stock, prices.get(code), usd_krw_rate)
        total_invest += metrics["invest"]
        total_eval += metrics["eval"]
        holdings.append({
            "name":        stock.get("name", code),
            "profit":      metrics["profit"],
            "profit_rate": metrics["profit_rate"],
            "stock":       stock,
            "metrics":     metrics,
        })

    profit = total_eval - total_invest
    profit_rate = (profit / total_invest * 100.0) if total_invest else 0.0
    return {
        "total_invest": total_invest,
        "total_eval": total_eval,
        "profit": profit,
        "profit_rate": profit_rate,
        "holdings": holdings,
    }
