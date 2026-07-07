"""미니 가격 차트 (sparkline) — 분봉 라인 + 일봉 캔들 두 모드."""

from datetime import datetime

from PyQt6.QtWidgets import QWidget, QFrame, QApplication, QPushButton
from PyQt6.QtCore import Qt, QPointF, QRectF, QPoint, QSize, pyqtSignal
from PyQt6.QtGui import (QPainter, QColor, QBrush, QPainterPath, QPen, QFont, QFontMetricsF,
                         QShortcut, QKeySequence)

from .theme import C, MA_COLORS


# ─── 일봉 → 주봉/월봉 집계 ────────────────────────────────────────────────────
def _period_key(date_str: str, unit: str):
    """캔들 날짜를 주/월 묶음 키로 변환. 주: ISO (연, 주차) / 월: 'YYYYMM'.
    'YYYYMMDD'·'YYYY-MM-DD' 모두 허용. 파싱 실패 시 8자리 원본으로 폴백."""
    digits = (date_str or "").replace("-", "")[:8]
    if unit == "month":
        return digits[:6] or date_str
    try:
        dt = datetime.strptime(digits, "%Y%m%d")
        y, w, _ = dt.isocalendar()
        return (y, w)
    except ValueError:
        return digits


def aggregate_candles(daily: list[dict], unit: str) -> list[dict]:
    """일봉 리스트(과거→최근)를 주봉/월봉으로 집계한다. unit='day'면 그대로 반환.
    시가=구간 첫날, 종가=끝날, 고=최대, 저=최소, 거래량=합산. 새 네트워크 호출 없음."""
    if unit == "day" or not daily:
        return daily
    groups: dict = {}
    order: list = []
    for c in daily:
        k = _period_key(c.get("date", ""), unit)
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(c)
    out: list[dict] = []
    for k in order:
        g = groups[k]
        out.append({
            "date":   g[-1].get("date", ""),
            "open":   g[0]["open"],
            "high":   max(c["high"] for c in g),
            "low":    min(c["low"]  for c in g),
            "close":  g[-1]["close"],
            "volume": sum((c.get("volume") or 0) for c in g),
        })
    return out


# ─── 가격 미니 차트 (sparkline) ───────────────────────────────────────────────
class SparklineWidget(QWidget):
    """미니 가격 차트. 두 가지 모드 지원.
    - line  : 당일 1분봉 라인 + area, 시초가 대비 색상 결정, 전일 종가 점선
    - candle: 최근 N일 일봉 캔들 (양봉=빨강, 음봉=파랑)"""

    W = 100   # 차트 너비 (기본값 — 인스턴스에서 재정의 가능)
    H = 40    # 차트 높이
    PRICE_AXIS_W = 40   # 우측 가격 보조축 라벨 폭 (확대 팝업, 가격축 ON 시)
    DATE_AXIS_H  = 14   # 하단 날짜 보조축 라벨 높이 (확대 팝업, 날짜축 ON 시)
    VOL_PANEL_RATIO = 0.24   # 거래량 패널이 차지하는 플롯 높이 비율 (거래량 표시 ON 시)
    VOL_GAP = 4              # 가격/거래량 패널 사이 여백(px)
    TOP_BAR_H = 22           # 확대 팝업 상단바 높이 (봉주기·이동평균 범례 · 종목명 · ✕)

    def __init__(self, parent=None, width: int | None = None, height: int | None = None):
        super().__init__(parent)
        # 인스턴스 크기를 주면 클래스 기본값을 덮어쓴다(작은 압축 행용).
        # paint 메서드가 self.W/self.H 를 참조하므로 좌표도 함께 맞춰진다.
        if width is not None:
            self.W = width
        if height is not None:
            self.H = height
        self.setFixedSize(self.W, self.H)
        self.mode: str = "line"
        self.prices: list[float] = []
        self.open_price: float = 0.0
        self.prev_close: float = 0.0   # 전일 종가 (가로 점선 표시용, line 모드 전용)
        self.candles: list[dict] = []  # OHLC dict 리스트 (candle 모드)
        self.ma_periods: tuple = ()    # 그릴 이동평균 기간들 (예: (5, 20, 60))
        self.display_count: int | None = None  # 표시할 최근 캔들 수 (None=전부)
        self.watermark_name: str = ""  # 캔들 뒤 배경에 은은히 깔 종목명 (확대 팝업 전용)
        # 확대 팝업 보조축 — 우측 가격(최고·평균·최저)·하단 날짜 눈금. 미니 차트는 항상 꺼짐.
        self.show_date_axis: bool = False
        self.show_price_axis: bool = False
        self.show_volume: bool = False    # 하단 거래량 패널 표시 (확대 팝업 전용)
        self.candle_unit: str = "day"     # 봉주기(day/week/month) — 이동평균 범례 라벨용
        # 고정 팝업 전용 — 봉 hover 시 세로 십자선 + OHLC 박스
        self.interactive: bool = False
        self.hover_index: int | None = None
        self._hit_left = 0.0     # 마우스 x → 캔들 인덱스 역산용 (paint 시 캐시)
        self._hit_slot = 0.0
        self._hit_n = 0
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def set_data(self, prices: list[float], open_price: float, prev_close: float = 0.0):
        self.mode = "line"
        self.prices = prices
        self.open_price = open_price
        self.prev_close = prev_close
        self.update()

    def set_candles(self, candles: list[dict], ma_periods=(), display_count: int | None = None,
                    show_date_axis: bool = False, show_price_axis: bool = False,
                    show_volume: bool = False, candle_unit: str = "day"):
        """일봉 캔들 표시.
        - candles: 전체 OHLC 이력 (이동평균은 표시 구간 밖 데이터까지 평균에 사용)
        - ma_periods: 그릴 이동평균 기간들 (예: (5, 20, 60)). 빈 값이면 안 그림.
        - display_count: 최근 N개만 캔들로 표시 (None=전부). 이동평균선은 표시 구간만.
        - show_date_axis/show_price_axis: 확대 팝업 보조축(하단 날짜·우측 가격) 표시 여부.
        - show_volume: 하단 거래량 패널 표시 여부(색은 캔들과 동일 — 종가≥시가 빨강, 아니면 파랑).
        - candle_unit: 봉주기(day/week/month) — 이동평균 범례 앞에 D/W/M 라벨로 표시.
        """
        self.mode = "candle"
        self.candles = candles
        self.ma_periods = tuple(ma_periods)
        self.display_count = display_count
        self.show_date_axis = show_date_axis
        self.show_price_axis = show_price_axis
        self.show_volume = show_volume
        self.candle_unit = candle_unit
        self.hover_index = None
        self.update()

    @staticmethod
    def _sma(values: list[float], period: int) -> list:
        """단순이동평균. out[i] = values[i-period+1..i] 평균, 데이터 부족하면 None."""
        n = len(values)
        out: list = [None] * n
        if period <= 0 or period > n:
            return out
        s = 0.0
        for i, v in enumerate(values):
            s += v
            if i >= period:
                s -= values[i - period]
            if i >= period - 1:
                out[i] = s / period
        return out

    def paintEvent(self, event):
        if self.mode == "candle":
            self._paint_candles()
        else:
            self._paint_line()

    # ── 봉 hover (고정 팝업에서 interactive=True 일 때만 동작) ──────────────────
    def mouseMoveEvent(self, event):
        if not self.interactive or self.mode != "candle" or self._hit_n <= 0 or self._hit_slot <= 0:
            return super().mouseMoveEvent(event)
        i = int((event.position().x() - self._hit_left) / self._hit_slot)
        i = max(0, min(self._hit_n - 1, i))
        if i != self.hover_index:
            self.hover_index = i
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self.interactive and self.hover_index is not None:
            self.hover_index = None
            self.update()
        super().leaveEvent(event)

    # ── 라인 모드 (당일 분봉) ────────────────────────────────────────────
    def _paint_line(self):
        prices = self.prices
        if not prices or len(prices) < 2:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        last = prices[-1]
        op = self.open_price or prices[0]
        color = QColor(C['red']) if last >= op else QColor(C['blue'])
        fill = QColor(color)
        fill.setAlpha(50)

        pad = 4
        w = self.W - 2 * pad
        h = self.H - 2 * pad

        # y범위: 가격뿐 아니라 전일 종가도 포함시켜 점선이 항상 영역 안에 들어오게
        y_values = list(prices)
        if self.prev_close > 0:
            y_values.append(self.prev_close)
        mn = min(y_values)
        mx = max(y_values)
        rng = (mx - mn) if mx > mn else 1.0

        def y_of(price: float) -> float:
            return pad + (1 - (price - mn) / rng) * h

        # x축은 거래시간 전체(09:00~15:30, 약 391분봉) 기준으로 절대 매핑.
        # 단, 장 초반에 너무 좁아 보이지 않도록 최소 가시 영역(15%) 보장.
        TOTAL_BARS = 391
        MIN_VISIBLE_RATIO = 0.15
        actual_ratio = (len(prices) - 1) / (TOTAL_BARS - 1)
        visible_ratio = min(max(actual_ratio, MIN_VISIBLE_RATIO), 1.0)

        n = len(prices)
        pts: list[QPointF] = []
        for i, p in enumerate(prices):
            x = pad + (i / (n - 1)) * visible_ratio * w
            pts.append(QPointF(x, y_of(p)))

        # area fill (라인 아래 반투명 채움)
        area = QPainterPath()
        area.moveTo(pts[0])
        for pt in pts[1:]:
            area.lineTo(pt)
        area.lineTo(pts[-1].x(), pad + h)
        area.lineTo(pts[0].x(), pad + h)
        area.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(fill))
        painter.drawPath(area)

        # 전일 종가 기준선 (가로 점선) — 촘촘한 dot, 살짝 흐린 색
        if self.prev_close > 0:
            line_y = y_of(self.prev_close)
            pen_color = QColor(C['subtext'])
            pen_color.setAlpha(180)         # 살짝 흐리게
            dotted = QPen(pen_color)
            dotted.setWidthF(0.8)            # 얇게
            dotted.setDashPattern([1, 2])    # 1px on, 2px off (거의 dot)
            painter.setPen(dotted)
            painter.drawLine(QPointF(pad, line_y), QPointF(pad + w, line_y))

        # line stroke (현재가 라인)
        line = QPainterPath()
        line.moveTo(pts[0])
        for pt in pts[1:]:
            line.lineTo(pt)
        painter.setPen(QPen(color, 1.3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(line)

        painter.end()

    # ── 캔들 모드 (일봉) ────────────────────────────────────────────────
    def _paint_candles(self):
        candles = self.candles
        if not candles:
            return

        # 표시 구간(최근 display_count개)과 이동평균 계산용 전체 구간을 분리한다.
        # 예) 3개월(약 63봉)만 그리되 60일선은 그 이전 데이터까지 평균에 사용.
        n_all = len(candles)
        dc = self.display_count or n_all
        dc = max(1, min(dc, n_all))
        start = n_all - dc                 # 표시 시작 글로벌 인덱스
        shown = candles[start:]

        painter = QPainter(self)
        # 캔들은 픽셀 정렬이 더 선명 — antialias off
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # 확대 팝업은 위에 상단바(범례·종목명·✕) 자리를 떼어 둔다. 미니 차트는 상단바 없음.
        base = 1
        bar_h = self.TOP_BAR_H if (self.ma_periods or self.watermark_name or self.interactive) else 0
        left = base
        top = base + bar_h
        right  = base + (self.PRICE_AXIS_W if self.show_price_axis else 0)
        bottom = base + (self.DATE_AXIS_H  if self.show_date_axis  else 0)
        w = self.W - left - right
        h = self.H - top - bottom

        # 거래량 패널 — 켜져 있고 거래량 데이터가 있으면 하단 일부 높이를 떼어 준다.
        # 가격 캔들·이동평균은 남은 위쪽(price_h)에만 그려 서로 겹치지 않게 한다.
        vols = [(c.get("volume") or 0) for c in shown]
        draw_vol = self.show_volume and any(v > 0 for v in vols)
        if draw_vol:
            vol_h = max(12, round(h * self.VOL_PANEL_RATIO))
            price_h = h - vol_h - self.VOL_GAP
        else:
            vol_h = 0
            price_h = h

        # 이동평균 시계열은 전체 구간 기준으로 계산 (표시 구간 왼쪽 끝에서도 값 존재)
        closes = [c["close"] for c in candles]
        ma_series = {p: self._sma(closes, p) for p in self.ma_periods}

        # y범위: 표시 캔들의 고저 + 표시 구간에 들어오는 이동평균값까지 포함
        mn = min(c["low"]  for c in shown)
        mx = max(c["high"] for c in shown)
        for series in ma_series.values():
            for gi in range(start, n_all):
                v = series[gi]
                if v is not None:
                    mn = min(mn, v)
                    mx = max(mx, v)
        rng = (mx - mn) if mx > mn else 1.0

        def y_of(price: float) -> float:
            return top + (1 - (price - mn) / rng) * price_h

        slot = w / dc
        body_w = max(1.5, slot * 0.7)
        # 마우스 x → 캔들 인덱스 역산에 필요한 값 캐시 (고정 팝업 봉 hover 용)
        self._hit_left, self._hit_slot, self._hit_n = left, slot, len(shown)

        red  = QColor(C['red'])
        blue = QColor(C['blue'])

        for i, c in enumerate(shown):
            cx = left + (i + 0.5) * slot
            up = c["close"] >= c["open"]
            color = red if up else blue

            # 심지(고가–저가)
            painter.setPen(QPen(color, 0.8))
            painter.drawLine(
                QPointF(cx, y_of(c["high"])),
                QPointF(cx, y_of(c["low"])),
            )

            # 몸통(시가↔종가)
            y_open  = y_of(c["open"])
            y_close = y_of(c["close"])
            body_top = min(y_open, y_close)
            body_h   = max(1.0, abs(y_close - y_open))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawRect(QRectF(cx - body_w / 2, body_top, body_w, body_h))

        # ── 거래량 막대 (하단 패널) — 색은 캔들과 동일(종가≥시가 빨강, 아니면 파랑) ──
        if draw_vol:
            vmax = max(vols) or 1.0
            vol_bottom = top + h
            # 가격·거래량 경계 옅은 구분선
            painter.setPen(QPen(QColor(C['border']), 1))
            sep_y = top + price_h + self.VOL_GAP / 2
            painter.drawLine(QPointF(left, sep_y), QPointF(left + w, sep_y))
            painter.setPen(Qt.PenStyle.NoPen)
            for i, c in enumerate(shown):
                v = vols[i]
                if v <= 0:
                    continue
                cx = left + (i + 0.5) * slot
                color = red if c["close"] >= c["open"] else blue
                bar_h = max(1.0, (v / vmax) * vol_h)
                painter.setBrush(QBrush(color))
                painter.drawRect(QRectF(cx - body_w / 2, vol_bottom - bar_h, body_w, bar_h))

        # ── 이동평균선 (표시 구간만, 부드럽게) ──────────────────────────────
        if self.ma_periods:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            for p in self.ma_periods:
                series = ma_series.get(p)
                if not series:
                    continue
                painter.setPen(QPen(QColor(MA_COLORS.get(p, C['subtext'])), 1.2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                path = QPainterPath()
                started = False
                for i in range(dc):
                    v = series[start + i]
                    if v is None:
                        started = False
                        continue
                    pt = QPointF(left + (i + 0.5) * slot, y_of(v))
                    if started:
                        path.lineTo(pt)
                    else:
                        path.moveTo(pt)
                        started = True
                painter.drawPath(path)

        # ── 보조축 (확대 팝업 전용) — 캔들·이동평균 위에 라벨을 얹는다 ─────────────
        if self.show_price_axis:
            self._draw_price_axis(painter, shown, mn, mx, left, top, w, price_h)
        if self.show_date_axis:
            self._draw_date_axis(painter, shown, left, top, w, h, slot)

        # ── 상단바 (봉주기·이동평균 범례 + 종목명) — 예약된 top strip (✕ 는 팝업이 얹음) ──
        if bar_h:
            self._draw_top_bar(painter, base, bar_h)

        # ── 봉 hover 십자선 + OHLC 박스 (고정 팝업 전용) — 최상단 레이어 ──────────
        if self.interactive and self.hover_index is not None:
            self._draw_hover_overlay(painter, shown, left, top, w, h, slot)

        painter.end()

    # ── 우측 가격 보조축 — 최고가(빨강)·평균(흐림)·최저가(파랑) 눈금 ──────────────
    @staticmethod
    def _fmt_axis_price(v: float) -> str:
        """가격 라벨 — 1,000 이상은 천단위 콤마 정수, 미만(소형 지수 등)은 소수 2자리."""
        return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:,.2f}"

    def _draw_price_axis(self, painter, shown, mn, mx, left, top, w, h):
        hi  = max(c["high"] for c in shown)
        lo  = min(c["low"]  for c in shown)
        avg = sum(c["close"] for c in shown) / len(shown)
        rng = (mx - mn) if mx > mn else 1.0
        plot_right = left + w

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setFont(QFont("Malgun Gothic", 8))
        fm = painter.fontMetrics()
        half = fm.height() / 2

        for price, key in ((hi, 'red'), (avg, 'subtext'), (lo, 'blue')):
            y = top + (1 - (price - mn) / rng) * h
            color = QColor(C[key])
            # 눈금 짧은 선
            painter.setPen(QPen(color, 1.0))
            painter.drawLine(QPointF(plot_right, y), QPointF(plot_right + 3, y))
            # 라벨 — 위/아래 끝에서 잘리지 않게 세로 위치를 살짝 가둔다
            yc = min(max(y, top + half), top + h - half)
            rect = QRectF(plot_right + 4, yc - half, self.W - (plot_right + 4) - 2, fm.height())
            painter.setPen(color)
            painter.drawText(
                rect,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                self._fmt_axis_price(price),
            )

    # ── 하단 날짜 보조축 — 표시 구간을 4등분해 대략적 날짜(M/D)를 찍는다 ────────────
    @staticmethod
    def _fmt_axis_date(s: str) -> str:
        """'YYYYMMDD' 또는 'YYYY-MM-DD' → 'M/D'. 파싱 불가하면 빈 문자열."""
        digits = (s or "").replace("-", "").strip()
        if len(digits) >= 8 and digits[:8].isdigit():
            return f"{int(digits[4:6])}/{int(digits[6:8])}"
        return ""

    def _draw_date_axis(self, painter, shown, left, top, w, h, slot):
        n = len(shown)
        if n == 0:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setFont(QFont("Malgun Gothic", 8))
        fm = painter.fontMetrics()
        painter.setPen(QColor(C['subtext']))
        y = top + h + self.DATE_AXIS_H - 2               # 날짜축 밴드 하단에 밀착
        for i in sorted({0, n // 3, 2 * n // 3, n - 1}):
            label = self._fmt_axis_date(shown[i].get("date", ""))
            if not label:
                continue
            tw = fm.horizontalAdvance(label)
            tx = left + (i + 0.5) * slot - tw / 2
            tx = min(max(tx, left), left + w - tw)   # 좌우 끝 클램프
            painter.drawText(QPointF(tx, y), label)

    # ── 봉 hover 십자선 + OHLC 박스 (고정 팝업 전용) ───────────────────────────
    def _draw_hover_overlay(self, painter, shown, left, top, w, h, slot):
        idx = self.hover_index
        if idx is None or idx < 0 or idx >= len(shown):
            return
        c = shown[idx]
        cx = left + (idx + 0.5) * slot
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 세로 십자선 (플롯 전체 높이)
        line_col = QColor(C['subtext']); line_col.setAlpha(150)
        pen = QPen(line_col); pen.setWidthF(0.9)
        painter.setPen(pen)
        painter.drawLine(QPointF(cx, top), QPointF(cx, top + h))
        # OHLC 박스 — 맨 위 날짜(M/D) + 종/시/고/저
        up = c["close"] >= c["open"]
        rows = [
            ("종", c["close"], C['red'] if up else C['blue']),
            ("시", c["open"],  C['text']),
            ("고", c["high"],  C['red']),
            ("저", c["low"],   C['blue']),
        ]
        date_label = self._fmt_axis_date(c.get("date", ""))   # 'M/D' (하단 날짜축과 동일 형식)
        font = QFont("Malgun Gothic", 8)
        font_b = QFont("Malgun Gothic", 8, QFont.Weight.Bold)
        painter.setFont(font)
        fm = painter.fontMetrics()
        line_h = fm.height() + 2
        label_w = fm.horizontalAdvance("종")
        val_w = max(fm.horizontalAdvance(self._fmt_axis_price(v)) for _, v, _ in rows)
        date_w = QFontMetricsF(font_b).horizontalAdvance(date_label) if date_label else 0
        pad, gap = 6, 8
        box_w = pad * 2 + max(label_w + gap + val_w, date_w)
        box_h = pad * 2 + line_h * (len(rows) + (1 if date_label else 0))
        # 커서 반대편에 배치 + 위젯 안으로 클램프
        bx = cx + 10 if cx < left + w / 2 else cx - 10 - box_w
        by = top + 4
        bx = max(left + 2, min(bx, left + w - box_w - 2))
        by = max(top + 2, min(by, top + h - box_h - 2))
        # 배경 카드
        bg = QColor(C['bg2']); bg.setAlpha(235)
        painter.setPen(QPen(QColor(C['border']), 1))
        painter.setBrush(QBrush(bg))
        painter.drawRoundedRect(QRectF(bx, by, box_w, box_h), 5, 5)
        # 텍스트 — 날짜(맨 위, 약간 밝게 볼드) → 종/시/고/저
        ty = by + pad + fm.ascent()
        if date_label:
            painter.setFont(font_b)
            painter.setPen(QColor(C['text']))
            painter.drawText(QPointF(bx + pad, ty), date_label)
            painter.setFont(font)
            ty += line_h
        for label, v, color in rows:
            painter.setPen(QColor(C['subtext']))
            painter.drawText(QPointF(bx + pad, ty), label)
            val = self._fmt_axis_price(v)
            painter.setPen(QColor(color))
            painter.drawText(QPointF(bx + box_w - pad - fm.horizontalAdvance(val), ty), val)
            ty += line_h

    # ── 배경 종목명 워터마크 — 캔들 뒤에 아주 은은하게 반투명으로 ────────────────
    # ── 상단바 (확대 팝업) — 좌: 봉주기·이동평균 범례 / 중: 종목명 / 우: ✕(팝업이 얹음) ──
    def _draw_top_bar(self, painter: QPainter, base: int, bar_h: int):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        y = base + 14                                    # 상단바 baseline (범례·종목명 공통)
        # 종목명 — 팝업 사각형 정중앙에 고정 + 범례보다 먼저 그려 뒤(behind)에 둔다.
        # 긴 이름이 범례와 겹치면 범례가 위로 덮는다.
        name = (self.watermark_name or "").strip()
        if name:
            painter.setFont(QFont("Malgun Gothic", 10))
            fm = painter.fontMetrics()
            max_w = max(40, self.W - 120)                 # 좌우 여백(범례·✕ 자리)
            elided = fm.elidedText(name, Qt.TextElideMode.ElideRight, max_w)
            tw = fm.horizontalAdvance(elided)
            painter.setPen(QColor(C['subtext']))
            painter.drawText(QPointF(self.W / 2 - tw / 2, y), elided)   # 팝업 정중앙
        # 좌: 봉주기·이동평균 범례 (종목명 위에 그림)
        self._draw_ma_legend(painter, base + 5, y)

    # ── 이동평균 범례 — 봉주기(D/W/M) + 5·20·60 (색 숫자). 오른쪽 끝 x 를 반환. ──
    def _draw_ma_legend(self, painter: QPainter, x0: float, y: float) -> float:
        painter.setFont(QFont("Malgun Gothic", 9, QFont.Weight.DemiBold))
        fm = painter.fontMetrics()
        x = x0
        # 봉주기 라벨(D/W/M) — 5·20·60 이 어느 단위(일/주/월봉)인지 알려준다.
        unit_label = {"day": "D", "week": "W", "month": "M"}.get(self.candle_unit, "")
        if unit_label:
            painter.setPen(QColor(C['subtext']))
            painter.drawText(int(x), int(y), unit_label)
            x += fm.horizontalAdvance(unit_label + "  ")
        sep = QColor("#6c7086")            # 기간 사이 점(·) — 흐린 회보라
        for i, p in enumerate(self.ma_periods):
            if i > 0:
                dot = " · "
                painter.setPen(sep)
                painter.drawText(int(x), int(y), dot)
                x += fm.horizontalAdvance(dot)
            painter.setPen(QColor(MA_COLORS.get(p, C['subtext'])))
            label = str(p)
            painter.drawText(int(x), int(y), label)
            x += fm.horizontalAdvance(label)
        return x


# ─── 확대 일봉 hover 팝업 (Windows 떠있는 위젯 · macOS 팝오버 공용) ───────────────
class ChartPopup(QWidget):
    """미니 일봉 차트에 마우스를 올리면 뜨는 확대 미리보기.

    이미 읽어둔 캔들을 그대로 크게 다시 그릴 뿐 새 네트워크 호출은 하지 않는다.
    입력 통과(WindowTransparentForInput) 윈도우라 hover 가 끊기지 않는다.
    """

    PAD = 2   # 차트 둘레 여백 — 차트가 팝업에 꽉 차도록 작게

    close_requested = pyqtSignal()   # ✕ 클릭 (고정 모드 전용)

    def __init__(self, chart_w: int, chart_h: int, parent=None, interactive: bool = False):
        super().__init__(parent)
        # 고정(interactive) 모드는 입력을 받아 봉 hover·✕ 닫기가 동작한다.
        # hover 모드는 기존처럼 입력 통과(WindowTransparentForInput)라 hover 가 안 끊긴다.
        flags = (
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        if not interactive:
            flags |= Qt.WindowType.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.interactive = interactive

        w = chart_w + 2 * self.PAD
        h = chart_h + 2 * self.PAD
        self.setFixedSize(w, h)

        self.card = QFrame(self)
        self.card.setObjectName("popcard")
        self.card.setGeometry(0, 0, w, h)
        border_col = C['blue'] if interactive else C['border']   # 고정은 accent 테두리로 구분
        self.card.setStyleSheet(f"""
            QFrame#popcard {{
                background: {C['bg']};
                border: 1px solid {border_col};
                border-radius: 8px;
            }}
        """)
        self.chart = SparklineWidget(self.card, width=chart_w, height=chart_h)
        self.chart.move(self.PAD, self.PAD)

        # 고정 모드 전용 — 봉 hover 활성 + ✕ 닫기 버튼
        if interactive:
            self.chart.interactive = True
            self.chart.setMouseTracking(True)
            # Esc 로 닫기 — 앱 어디서 눌러도 (고정 팝업이 떠 있는 동안만 유효, ✕ 와 동일 경로)
            esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
            esc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            esc.activated.connect(self.close_requested.emit)
            self.close_btn = QPushButton("✕", self.card)
            self.close_btn.setFixedSize(18, 18)
            self.close_btn.move(w - 18 - self.PAD, self.PAD)
            self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.close_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C['subtext']};
                    border: none; font-size: 13px;
                }}
                QPushButton:hover {{ color: {C['text']}; }}
            """)
            self.close_btn.clicked.connect(self.close_requested.emit)
            self.close_btn.raise_()

    def show_with(self, candles: list, anchor_tl: QPoint, anchor_size: QSize,
                  ma_periods=(), display_count: int | None = None, name: str = "",
                  show_date_axis: bool = False, show_price_axis: bool = False,
                  show_volume: bool = False, candle_unit: str = "day"):
        """기존 캔들로 확대 차트를 그리고 소스 차트(anchor) 위쪽에 띄운다."""
        self.chart.watermark_name = name or ""   # 배경 종목명 (빈 값이면 안 그림)
        self.chart.set_candles(candles, ma_periods=ma_periods, display_count=display_count,
                               show_date_axis=show_date_axis, show_price_axis=show_price_axis,
                               show_volume=show_volume, candle_unit=candle_unit)
        self._position(anchor_tl, anchor_size)
        self.show()
        self.raise_()

    def _position(self, anchor_tl: QPoint, anchor_size: QSize):
        ax, ay = anchor_tl.x(), anchor_tl.y()
        aw, ah = anchor_size.width(), anchor_size.height()
        x = ax + aw // 2 - self.width() // 2          # 소스 차트 가로 중앙
        y = ay - self.height() - 6                     # 기본: 차트 위쪽
        screen = QApplication.screenAt(QPoint(ax, ay)) or QApplication.primaryScreen()
        geo = screen.availableGeometry()
        if y < geo.y() + 4:                            # 위 공간이 없으면 아래로
            y = ay + ah + 6
        x = max(geo.x() + 4, min(x, geo.x() + geo.width() - self.width() - 4))
        self.move(x, y)


# ─── 확대 팝업 고정/hover 조율자 (한 번에 팝업 1개만) ──────────────────────────
class PinController:
    """관심종목 확대 팝업의 고정/hover 를 한 곳에서 조율한다 (Windows·macOS 공용).

    팝업은 각 행이 소유하고, 이 컨트롤러는 pin 상태만 들고 규칙을 강제한다:
    - hover 는 고정이 없을 때만
    - 고정 중엔 다른 종목 hover 무시
    - 다른 행 클릭 시 고정 전환, 같은 행 재클릭/✕ 시 해제(→ hover 재개)

    행이 구현해야 하는 인터페이스: show_chart_popup(pinned: bool), hide_chart_popup().
    """

    def __init__(self):
        self._pinned = None   # 고정된 행 (or None)
        self._hover = None    # 현재 hover 로 떠 있는 행 (고정 없을 때만)

    @staticmethod
    def _alive(row) -> bool:
        """행(위젯)이 아직 살아있는지 — 파괴된 C++ 객체면 False (dangling 방어)."""
        if row is None:
            return False
        try:
            row.isVisible()
            return True
        except RuntimeError:
            return False

    def on_enter(self, row):
        if not self._alive(self._pinned):
            self._pinned = None
        if self._pinned is not None:
            return                                   # 고정 중 → 다른 hover 무시
        if self._hover is not row and self._alive(self._hover):
            self._hover.hide_chart_popup()           # 이전 hover 정리 (팝업 1개 보장)
        self._hover = row
        row.show_chart_popup(pinned=False)

    def on_leave(self, row):
        if not self._alive(self._pinned):
            self._pinned = None
        if self._pinned is not None:
            return
        if self._hover is row:
            row.hide_chart_popup()
            self._hover = None

    def on_click(self, row):
        if not self._alive(self._pinned):
            self._pinned = None
        if self._pinned is row:
            self._unpin()                            # 같은 행 재클릭 → 해제
        elif self._pinned is not None:
            self._pinned.hide_chart_popup()          # 다른 행 클릭 → 전환
            self._pinned = row
            row.show_chart_popup(pinned=True)
        else:
            if self._alive(self._hover):             # 고정 없음 → 이 행 고정
                self._hover.hide_chart_popup()
            self._hover = None
            self._pinned = row
            row.show_chart_popup(pinned=True)

    def on_close(self, row):
        if self._pinned is row:                      # ✕ — 그 행이 고정 중일 때만
            self._unpin()

    def _unpin(self):
        r, self._pinned = self._pinned, None
        if self._alive(r):
            r.hide_chart_popup()

    def forget(self, row):
        """행이 파괴/정지될 때 호출 — dangling 참조 정리."""
        if self._pinned is row:
            self._pinned = None
        if self._hover is row:
            self._hover = None
