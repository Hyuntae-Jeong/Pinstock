"""미니 가격 차트 (sparkline) — 분봉 라인 + 일봉 캔들 두 모드."""

from PyQt6.QtWidgets import QWidget, QFrame, QApplication
from PyQt6.QtCore import Qt, QPointF, QRectF, QPoint, QSize
from PyQt6.QtGui import QPainter, QColor, QBrush, QPainterPath, QPen, QFont, QFontMetricsF

from .theme import C, MA_COLORS


# ─── 가격 미니 차트 (sparkline) ───────────────────────────────────────────────
class SparklineWidget(QWidget):
    """미니 가격 차트. 두 가지 모드 지원.
    - line  : 당일 1분봉 라인 + area, 시초가 대비 색상 결정, 전일 종가 점선
    - candle: 최근 N일 일봉 캔들 (양봉=빨강, 음봉=파랑)"""

    W = 100   # 차트 너비 (기본값 — 인스턴스에서 재정의 가능)
    H = 40    # 차트 높이
    PRICE_AXIS_W = 40   # 우측 가격 보조축 라벨 폭 (확대 팝업, 가격축 ON 시)
    DATE_AXIS_H  = 14   # 하단 날짜 보조축 라벨 높이 (확대 팝업, 날짜축 ON 시)

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
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def set_data(self, prices: list[float], open_price: float, prev_close: float = 0.0):
        self.mode = "line"
        self.prices = prices
        self.open_price = open_price
        self.prev_close = prev_close
        self.update()

    def set_candles(self, candles: list[dict], ma_periods=(), display_count: int | None = None,
                    show_date_axis: bool = False, show_price_axis: bool = False):
        """일봉 캔들 표시.
        - candles: 전체 OHLC 이력 (이동평균은 표시 구간 밖 데이터까지 평균에 사용)
        - ma_periods: 그릴 이동평균 기간들 (예: (5, 20, 60)). 빈 값이면 안 그림.
        - display_count: 최근 N개만 캔들로 표시 (None=전부). 이동평균선은 표시 구간만.
        - show_date_axis/show_price_axis: 확대 팝업 보조축(하단 날짜·우측 가격) 표시 여부.
        """
        self.mode = "candle"
        self.candles = candles
        self.ma_periods = tuple(ma_periods)
        self.display_count = display_count
        self.show_date_axis = show_date_axis
        self.show_price_axis = show_price_axis
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
        # 종목명 워터마크를 캔들보다 먼저 그려 제일 하단 레이어로 깐다 (확대 팝업 전용)
        if self.watermark_name:
            self._draw_watermark_name(painter)
        # 캔들은 픽셀 정렬이 더 선명 — antialias off
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # 보조축이 켜지면 우측(가격)·하단(날짜)에 라벨 자리를 확보하고 플롯을 줄인다.
        base = 3
        left = top = base
        right  = base + (self.PRICE_AXIS_W if self.show_price_axis else 0)
        bottom = base + (self.DATE_AXIS_H  if self.show_date_axis  else 0)
        w = self.W - left - right
        h = self.H - top - bottom

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
            return top + (1 - (price - mn) / rng) * h

        slot = w / dc
        body_w = max(1.5, slot * 0.7)

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
            self._draw_ma_legend(painter, base)

        # ── 보조축 (확대 팝업 전용) — 캔들·이동평균 위에 라벨을 얹는다 ─────────────
        if self.show_price_axis:
            self._draw_price_axis(painter, shown, mn, mx, left, top, w, h)
        if self.show_date_axis:
            self._draw_date_axis(painter, shown, left, top, w, h, slot)

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
        y = top + h + fm.ascent() + 1
        for i in sorted({0, n // 3, 2 * n // 3, n - 1}):
            label = self._fmt_axis_date(shown[i].get("date", ""))
            if not label:
                continue
            tw = fm.horizontalAdvance(label)
            tx = left + (i + 0.5) * slot - tw / 2
            tx = min(max(tx, left), left + w - tw)   # 좌우 끝 클램프
            painter.drawText(QPointF(tx, y), label)

    # ── 배경 종목명 워터마크 — 캔들 뒤에 아주 은은하게 반투명으로 ────────────────
    def _draw_watermark_name(self, painter: QPainter):
        """확대 차트 중앙에 종목명을 반투명 큰 글씨로 깐다(제일 하단 레이어).
        위젯 폭을 넘으면 폰트를 줄여 한 줄에 들어오게 한다."""
        name = (self.watermark_name or "").strip()
        if not name:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(8, 6, self.W - 16, self.H - 12)
        px = max(11, int(self.H * 0.16))
        font = QFont("Malgun Gothic")
        font.setBold(True)
        while px > 11:
            font.setPixelSize(px)
            if QFontMetricsF(font).horizontalAdvance(name) <= rect.width():
                break
            px -= 1
        font.setPixelSize(px)
        painter.setFont(font)
        color = QColor(C['text'])
        color.setAlpha(42)                 # 아주 은은하게 (다크 배경 위 반투명)
        painter.setPen(color)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, name)
        painter.restore()

    # ── 이동평균 범례 (좌상단) — 색 숫자 미니멀: 5 · 20 · 60 ──────────────────
    def _draw_ma_legend(self, painter: QPainter, pad: int):
        painter.setFont(QFont("Malgun Gothic", 9, QFont.Weight.DemiBold))
        fm = painter.fontMetrics()
        x = pad + 4
        y = pad + 2 + fm.ascent()
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


# ─── 확대 일봉 hover 팝업 (Windows 떠있는 위젯 · macOS 팝오버 공용) ───────────────
class ChartPopup(QWidget):
    """미니 일봉 차트에 마우스를 올리면 뜨는 확대 미리보기.

    이미 읽어둔 캔들을 그대로 크게 다시 그릴 뿐 새 네트워크 호출은 하지 않는다.
    입력 통과(WindowTransparentForInput) 윈도우라 hover 가 끊기지 않는다.
    """

    PAD = 6   # 차트 둘레 여백 — 차트가 팝업에 꽉 차도록 작게

    def __init__(self, chart_w: int, chart_h: int, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        w = chart_w + 2 * self.PAD
        h = chart_h + 2 * self.PAD
        self.setFixedSize(w, h)

        self.card = QFrame(self)
        self.card.setObjectName("popcard")
        self.card.setGeometry(0, 0, w, h)
        self.card.setStyleSheet(f"""
            QFrame#popcard {{
                background: {C['bg']};
                border: 1px solid {C['border']};
                border-radius: 8px;
            }}
        """)
        self.chart = SparklineWidget(self.card, width=chart_w, height=chart_h)
        self.chart.move(self.PAD, self.PAD)

    def show_with(self, candles: list, anchor_tl: QPoint, anchor_size: QSize,
                  ma_periods=(), display_count: int | None = None, name: str = "",
                  show_date_axis: bool = False, show_price_axis: bool = False):
        """기존 캔들로 확대 차트를 그리고 소스 차트(anchor) 위쪽에 띄운다."""
        self.chart.watermark_name = name or ""   # 배경 종목명 (빈 값이면 안 그림)
        self.chart.set_candles(candles, ma_periods=ma_periods, display_count=display_count,
                               show_date_axis=show_date_axis, show_price_axis=show_price_axis)
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
