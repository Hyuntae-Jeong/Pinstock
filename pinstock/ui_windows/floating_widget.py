"""화면에 떠있는 단일 종목 위젯."""

from PyQt6.QtWidgets import (
    QWidget, QFrame, QLabel, QHBoxLayout, QVBoxLayout, QMenu, QApplication,
    QPushButton,
)
from PyQt6.QtCore import Qt, QTimer, QPoint, QSize, QEvent, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics, QCursor
from datetime import datetime

from ..core.api import (
    fetch_stock, fetch_minute_chart, fetch_daily_chart,
    fetch_us_stock, fetch_us_minute_chart, fetch_us_daily_chart,
    fetch_watch_quote, fetch_watch_daily, WATCH_POPUP_CANDLES,
)
from ..core.portfolio import is_us_stock, is_index, stock_metrics
from .theme import C, TRAY_MENU_STYLE
from .chart_widget import SparklineWidget, ChartPopup
from .manage_dialog import StockDialog


def format_quantity(value) -> str:
    try:
        qty = float(value)
    except (TypeError, ValueError):
        qty = 0.0
    text = f"{qty:,.3f}".rstrip("0").rstrip(".")
    return text or "0"


# ─── 개별 주식 위젯 ───────────────────────────────────────────────────────────
class StockWidget(QWidget):
    """화면에 떠있는 하나의 주식 위젯"""

    deleted        = pyqtSignal(str)   # code 전달
    edited         = pyqtSignal(str)   # 수정 완료 후 저장 요청
    buy_requested  = pyqtSignal(str)   # 추가 매수 예상/확정 요청
    memo_requested = pyqtSignal(str)   # 종목별 메모 팝업 요청
    price_updated  = pyqtSignal(str)   # 현재가 갱신 시 (마스터 위젯 재집계용)
    layout_changed = pyqtSignal(str)   # compact 높이 변경 시 재정렬 요청

    MIN_W      = 240    # 기본(최소) 가로폭
    COMPACT_H  = 58     # 축소 높이 (2줄 레이아웃, 압축)
    EXTENDED_COMPACT_H = 72
    EXPAND_H_KR = 214
    EXPAND_H_US = 268
    EXPAND_H   = EXPAND_H_KR
    RADIUS     = 13     # 모서리 반지름

    def __init__(self, stock_data: dict, width: int | None = None, stagger_idx: int = 0):
        super().__init__()
        self.data = stock_data          # code, name, avg_price, quantity, pos
        self.current_price: float = 0
        self.usd_krw_rate: float | None = None
        self.us_return_basis: str = "krw"   # 미국 주식 수익률 표시 기준 (krw|usd)
        self.is_expanded: bool = False
        self._drag_pos = None
        self._press_pos = None    # 좌클릭 시작 위치 (드래그/클릭 구분용)
        self._moved: bool = False # 일정 거리 이상 움직였는지
        self._stagger_idx = stagger_idx   # 동시 호출 분산용 인덱스
        self._compact_height = self.COMPACT_H

        # 외부에서 통일 너비를 받지 않으면 종목명 기준 자체 계산
        name = self.data.get("name", self.data["code"])
        self.W = width if width else self.calc_width_for_name(name)

        # 종목 타입(국내/미국)에 따라 확장 높이 결정 — 첫 fetch 전에 펼쳐도 패널 높이가 맞도록
        self.EXPAND_H = self.EXPAND_H_US if is_us_stock(self.data) else self.EXPAND_H_KR

        # 5초 자동 축소 타이머
        self.collapse_timer = QTimer(singleShot=True)
        self.collapse_timer.timeout.connect(self.collapse)

        # 가격은 5초마다, sparkline은 60초마다 갱신
        # (분봉 데이터는 1분 단위 생성이라 더 자주 호출해도 같은 데이터)
        self._prev_close: float = 0.0

        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self._fetch_price)

        self.chart_timer = QTimer()
        self.chart_timer.timeout.connect(self._fetch_chart)

        self._build_ui()

        # 타이머/첫 fetch를 stagger 인덱스만큼 지연시켜 시작.
        # 여러 위젯이 거의 같은 시점에 동시 HTTP 호출하지 않도록 분산.
        STAGGER_MS = 600   # 위젯당 약 0.6초 간격
        delay = self._stagger_idx * STAGGER_MS
        QTimer.singleShot(delay, self._start_fetching)

    def _start_fetching(self):
        """타이머 가동 + 즉시 1회 fetch (stagger 지연 후 호출)."""
        self.refresh_timer.start(5_000)
        self.chart_timer.start(60_000)
        self._fetch_price()
        self._fetch_chart()

    # ── 종목명에 맞춰 가로폭 계산 ─────────────────────────────────────────
    @staticmethod
    def calc_width_for_name(name: str) -> int:
        """종목명 픽셀 폭을 측정해 위젯 가로폭을 결정. 최소 MIN_W."""
        font = QFont("Malgun Gothic",8, QFont.Weight.Bold)
        fm = QFontMetrics(font)
        name_w = fm.horizontalAdvance(name)
        # 좌마진(14) + 정보~sparkline spacing(8) + sparkline(100) + 우마진(10) + 여유(6) = 138
        OVERHEAD = 138
        return max(StockWidget.MIN_W, name_w + OVERHEAD)

    # ── UI 구성 ────────────────────────────────────────────────────────────
    def _build_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(self.W, self.COMPACT_H)

        # ── 카드 배경 프레임
        self.card = QFrame(self)
        self.card.setObjectName("card")
        self.card.setGeometry(0, 0, self.W, self.COMPACT_H)
        self.card.setStyleSheet(f"""
            QFrame#card {{
                background: {C['bg']};
                border: 1px solid {C['border']};
                border-radius: {self.RADIUS}px;
            }}
        """)

        # ── 상단 compact 영역 (좌: 정보 / 우: 당일 sparkline) ──────────
        self.compact = QWidget(self.card)
        self.compact.setGeometry(0, 0, self.W, self.COMPACT_H)
        self.compact.setStyleSheet("background: transparent;")

        hl = QHBoxLayout(self.compact)
        hl.setContentsMargins(14, 5, 10, 5)
        hl.setSpacing(8)

        # 좌측: 종목명 + 가격 행
        info = QVBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(1)

        # 1행: 종목명
        self.name_lbl = QLabel(self.data.get("name", self.data["code"]))
        self.name_lbl.setFont(QFont("Malgun Gothic",8, QFont.Weight.Bold))
        self.name_lbl.setStyleSheet(f"color: {C['subtext']};")
        info.addWidget(self.name_lbl)

        # 2행: 가격 + 등락률
        price_row = QHBoxLayout()
        price_row.setContentsMargins(0, 0, 0, 0)
        price_row.setSpacing(8)

        self.price_lbl = QLabel("─")
        self.price_lbl.setFont(QFont("Malgun Gothic",11, QFont.Weight.Bold))
        self.price_lbl.setStyleSheet(f"color: {C['text']};")
        price_row.addWidget(self.price_lbl)

        self.rate_lbl = QLabel("")
        self.rate_lbl.setFont(QFont("Malgun Gothic",9))
        self.rate_lbl.setStyleSheet(f"color: {C['subtext']};")
        price_row.addWidget(self.rate_lbl)
        price_row.addStretch()

        info.addLayout(price_row)

        extended_row = QHBoxLayout()
        extended_row.setContentsMargins(0, 0, 0, 0)
        extended_row.setSpacing(8)

        self.extended_price_lbl = QLabel("")
        self.extended_price_lbl.setFont(self.price_lbl.font())
        self.extended_price_lbl.setStyleSheet(
            f"color: {C['subtext']}; font-size: 11px; font-weight: bold;"
        )
        self.extended_price_lbl.setMinimumHeight(16)
        extended_row.addWidget(self.extended_price_lbl)

        self.extended_rate_lbl = QLabel("")
        self.extended_rate_lbl.setFont(self.rate_lbl.font())
        self.extended_rate_lbl.setStyleSheet(f"color: {C['subtext']}; font-size: 9px;")
        self.extended_rate_lbl.setMinimumHeight(16)
        extended_row.addWidget(self.extended_rate_lbl)

        self.extended_icon_lbl = QLabel("")
        self.extended_icon_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.extended_icon_lbl.setFixedHeight(16)
        self.extended_icon_lbl.setStyleSheet("font-size: 8px; line-height: 16px;")
        extended_row.addWidget(self.extended_icon_lbl)
        extended_row.addStretch()

        self.extended_widgets = [self.extended_price_lbl, self.extended_rate_lbl, self.extended_icon_lbl]
        for widget in self.extended_widgets:
            widget.hide()
        info.addSpacing(2)
        info.addLayout(extended_row)
        hl.addLayout(info, 1)

        # 우측: 당일 sparkline 미니 차트
        self.sparkline = SparklineWidget(self.compact)
        hl.addWidget(self.sparkline, 0, Qt.AlignmentFlag.AlignVCenter)

        # ── 확장 패널 ────────────────────────────────────────────────────
        panel_h = self.EXPAND_H - self.COMPACT_H
        self.expand_panel = QWidget(self.card)
        self.expand_panel.setGeometry(0, self.COMPACT_H, self.W, panel_h)
        self.expand_panel.setStyleSheet("background: transparent;")
        self.expand_panel.hide()

        vl = QVBoxLayout(self.expand_panel)
        vl.setContentsMargins(14, 2, 14, 12)
        vl.setSpacing(2)

        # 구분선
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background: {C['border']}; max-height: 1px; border: none;")
        vl.addWidget(sep)
        vl.addSpacing(2)

        # 상세 행 생성
        self.avg_row, self.avg_key, self.avg_val = self._make_row(vl, "평단가")
        self.fx_row, self.fx_key, self.fx_val = self._make_row(vl, "매수환율")
        self.qty_row, self.qty_key, self.qty_val = self._make_row(vl, "보유수량")
        self.invest_row, self.invest_key, self.invest_val = self._make_row(vl, "투자원금")
        self.eval_row, self.eval_key, self.eval_val = self._make_row(vl, "평가금액")

        # 손익 (강조)
        self.profit_row, self.profit_key, self.profit_val = self._make_row(vl, "평가손익", bold=True)
        self.fx_profit_row, self.fx_profit_key, self.fx_profit_val = self._make_row(vl, "환차손익")
        self.total_profit_row, self.total_profit_key, self.total_profit_val = self._make_row(vl, "총 평가손익", bold=True)
        self.prate_row, self.prate_key, self.prate_val = self._make_row(vl, "수익률", bold=True)

    # ── 외부에서 위젯 너비 변경 (통일 너비 적용용) ────────────────────
    def set_width(self, new_w: int):
        if new_w == self.W:
            return
        self.W = new_w
        cur_h = self._expanded_height() if self.is_expanded else self._compact_height
        self.setFixedWidth(new_w)
        self.card.setGeometry(0, 0, new_w, cur_h)
        self.compact.setGeometry(0, 0, new_w, self._compact_height)
        panel_h = self.EXPAND_H - self.COMPACT_H
        self.expand_panel.setGeometry(0, self._compact_height, new_w, panel_h)

    def _make_row(self, parent_layout, key_text: str, bold=False) -> tuple[QHBoxLayout, QLabel, QLabel]:
        """키-값 한 줄 생성, 값 QLabel 반환"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)

        key_lbl = QLabel(key_text)
        key_lbl.setStyleSheet(f"color: {C['subtext']}; font-size: 10px;")
        # 미국 주식에서 노출되는 '달러 매입단가' (가장 긴 라벨) 가 잘리지 않을 폭.
        key_lbl.setFixedWidth(72)
        key_lbl.setFixedHeight(16)

        val_lbl = QLabel("─")
        style = f"color: {C['text']}; font-size: 11px;"
        if bold:
            style += " font-weight: bold;"
        val_lbl.setStyleSheet(style)
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        val_lbl.setFixedHeight(16)

        row.addWidget(key_lbl)
        row.addWidget(val_lbl)
        parent_layout.addLayout(row)
        return row, key_lbl, val_lbl

    @staticmethod
    def _set_row_visible(row: QHBoxLayout, visible: bool):
        for i in range(row.count()):
            item = row.itemAt(i)
            widget = item.widget()
            if widget:
                widget.setVisible(visible)

    @staticmethod
    def _local_session_icon() -> str:
        hour = datetime.now().hour
        return "☀️" if 5 <= hour < 17 else "🌙"

    @staticmethod
    def _extended_session_icon(extended: dict) -> str:
        session = str(extended.get("session") or "").upper()
        if session == "PRE":
            return "☀️"
        if session == "POST":
            return "🌙"
        return StockWidget._local_session_icon()

    # ── 데이터 갱신 ────────────────────────────────────────────────────────
    def _fetch_price(self):
        """현재가/등락률 갱신 (5초 주기)."""
        result = fetch_us_stock(self.data["code"]) if is_us_stock(self.data) else fetch_stock(self.data["code"])
        if result:
            self.data["name"] = result["name"]
            self.name_lbl.setText(result["name"])
            self.current_price = result["price"]
            self._prev_close = float(result["price"] - result["change_price"])
            self._apply_price(result)
            self.price_updated.emit(self.data["code"])

    def set_usd_krw_rate(self, rate: float | None):
        self.usd_krw_rate = rate
        if self.current_price:
            self._update_detail(self.current_price)

    def set_us_return_basis(self, basis: str):
        self.us_return_basis = "usd" if basis == "usd" else "krw"
        if self.current_price:
            self._update_detail(self.current_price)

    def _fetch_chart(self):
        """sparkline 갱신 (60초 주기) — 당일 분봉 우선, 비어있으면 최근 일봉 폴백."""
        if is_us_stock(self.data):
            chart = fetch_us_minute_chart(self.data["code"])
        else:
            chart = fetch_minute_chart(self.data["code"])
        if chart and len(chart["prices"]) >= 2:
            # 분봉 모드: 전일 종가 점선(=현재가 - 전일대비)도 함께 표시
            self.sparkline.set_data(chart["prices"], chart["open"], self._prev_close)
        else:
            # 일봉 모드: 최근 N일 캔들 차트로 폴백
            daily = fetch_us_daily_chart(self.data["code"]) if is_us_stock(self.data) else fetch_daily_chart(self.data["code"])
            if daily:
                self.sparkline.set_candles(daily["candles"])

    def _apply_price(self, result: dict):
        price = result["price"]
        rate  = result["change_rate"]
        display_price = price
        display_rate = rate
        extended = result.get("extended")
        regular_price = float(result.get("regular_price") or 0.0)
        if extended and regular_price > 0 and self._prev_close > 0:
            display_price = regular_price
            display_rate = (regular_price - self._prev_close) / self._prev_close * 100.0

        self.price_lbl.setText(
            f"{display_price:,.4f}" if is_us_stock(self.data) else f"{display_price:,.0f}"
        )

        if display_rate > 0:
            color = C["red"]
            sign  = "▲"
        elif display_rate < 0:
            color = C["blue"]
            sign  = "▼"
        else:
            color = C["subtext"]
            sign  = "  "

        self.price_lbl.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")
        self.rate_lbl.setText(f"{sign}{abs(display_rate):.2f}%")
        self.rate_lbl.setStyleSheet(f"color: {color}; font-size: 9px;")
        self._apply_extended_price(result)

        self._update_detail(price)

    def _apply_extended_price(self, result: dict):
        extended = result.get("extended")
        if not extended:
            for widget in self.extended_widgets:
                widget.hide()
                widget.setText("")
            self._set_compact_height(self.COMPACT_H)
            return
        rate = float(extended.get("change_rate", 0.0))
        price = float(extended.get("price", 0.0))
        if price <= 0:
            for widget in self.extended_widgets:
                widget.hide()
                widget.setText("")
            self._set_compact_height(self.COMPACT_H)
            return
        if rate > 0:
            color, sign = C["red"], "▲"
        elif rate < 0:
            color, sign = C["blue"], "▼"
        else:
            color, sign = C["subtext"], " "
        session_icon = self._extended_session_icon(extended)
        self.extended_price_lbl.setText(f"{price:,.4f}" if is_us_stock(self.data) else f"{price:,.0f}")
        self.extended_price_lbl.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")
        self.extended_rate_lbl.setText(f"{sign}{abs(rate):.2f}%")
        self.extended_rate_lbl.setStyleSheet(f"color: {color}; font-size: 9px;")
        self.extended_icon_lbl.setText(session_icon)
        for widget in self.extended_widgets:
            widget.show()
        self._set_compact_height(self.EXTENDED_COMPACT_H)

    def _set_compact_height(self, height: int):
        old_height = self._compact_height
        if old_height == height:
            return
        self._compact_height = height
        if self.is_expanded:
            self.setFixedHeight(self._expanded_height())
            self.card.setGeometry(0, 0, self.W, self._expanded_height())
            self.compact.setGeometry(0, 0, self.W, height)
            self.expand_panel.setGeometry(0, height, self.W, self.expand_panel.height())
            self.layout_changed.emit(self.data["code"])
            return
        self.setFixedHeight(height)
        self.card.setGeometry(0, 0, self.W, height)
        self.compact.setGeometry(0, 0, self.W, height)
        self.layout_changed.emit(self.data["code"])

    def _expanded_height(self) -> int:
        return self.EXPAND_H + max(0, self._compact_height - self.COMPACT_H)

    def _update_detail(self, price: float):
        avg = self.data.get("avg_price", 0)
        qty = self.data.get("quantity", 0)
        metrics = stock_metrics(self.data, price, self.usd_krw_rate)
        invest = metrics["invest"]
        eval_ = metrics["eval"]
        profit = metrics["profit"]
        prate = metrics["profit_rate"]

        sign  = "+" if profit >= 0 else ""
        color = C["red"] if profit >= 0 else C["blue"]

        if is_us_stock(self.data):
            self.EXPAND_H = self.EXPAND_H_US
            self._set_row_visible(self.fx_row, True)
            self._set_row_visible(self.fx_profit_row, True)
            self._set_row_visible(self.total_profit_row, True)
            self.avg_key.setText("달러 매입단가")
            self.avg_val.setText(f"{float(avg):,.4f} USD")
            self.fx_val.setText(f"{metrics['buy_rate']:,.2f} 원/USD")
            stock_profit = metrics["stock_profit"]
            fx_profit = metrics["fx_profit"]
            stock_sign = "+" if stock_profit >= 0 else ""
            stock_color = C["red"] if stock_profit >= 0 else C["blue"]
            fx_sign = "+" if fx_profit >= 0 else ""
            fx_color = C["red"] if fx_profit >= 0 else C["blue"]
            self.profit_val.setText(f"{stock_sign}{stock_profit:,} 원")
            self.profit_val.setStyleSheet(f"color: {stock_color}; font-size: 11px; font-weight: bold;")
            self.fx_profit_val.setText(f"{fx_sign}{fx_profit:,} 원")
            self.fx_profit_val.setStyleSheet(f"color: {fx_color}; font-size: 11px;")
            self.total_profit_val.setText(f"{sign}{profit:,} 원")
            self.total_profit_val.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")
            if self.us_return_basis == "usd":
                prate = metrics["profit_rate_stock"]
                self.prate_key.setText("수익률 (달러)")
            else:
                prate = metrics["profit_rate"]
                self.prate_key.setText("수익률 (원화)")
        else:
            self.EXPAND_H = self.EXPAND_H_KR
            self._set_row_visible(self.fx_row, False)
            self._set_row_visible(self.fx_profit_row, False)
            self._set_row_visible(self.total_profit_row, False)
            self.avg_key.setText("평단가")
            self.avg_val.setText(f"{int(avg):,} 원")
            self.fx_val.setText("─")
            self.fx_profit_val.setText("─")
            self.fx_profit_val.setStyleSheet(f"color: {C['text']}; font-size: 11px;")
            self.total_profit_val.setText("─")
            self.total_profit_val.setStyleSheet(f"color: {C['text']}; font-size: 11px;")
            self.profit_val.setText(f"{sign}{profit:,} 원")
            self.profit_val.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")
        self.qty_val.setText(f"{format_quantity(qty)} 주")
        self.invest_val.setText(f"{invest:,} 원")
        self.eval_val.setText(f"{eval_:,} 원")
        prate_sign = "+" if prate >= 0 else ""
        prate_color = C["red"] if prate >= 0 else C["blue"]
        self.prate_val.setText(f"{prate_sign}{prate:.2f}%")
        self.prate_val.setStyleSheet(f"color: {prate_color}; font-size: 11px; font-weight: bold;")
        # 패널 높이를 종목 타입(EXPAND_H)에 맞춰 동기화 — 패널 바닥이 카드 바닥과 일치해야 마지막 행이 안 잘림
        self.expand_panel.setGeometry(0, self._compact_height, self.W, self.EXPAND_H - self.COMPACT_H)
        if self.is_expanded:
            expanded_h = self._expanded_height()
            self.setFixedHeight(expanded_h)
            self.card.setGeometry(0, 0, self.W, expanded_h)

    # ── 확장 / 축소 ────────────────────────────────────────────────────────
    def toggle_expand(self):
        if self.is_expanded:
            self.collapse()
        else:
            self.expand()

    SCREEN_MARGIN = 10   # 확장 위젯의 화면 가장자리 여백

    def expand(self):
        self.is_expanded = True
        self.expand_panel.show()
        expanded_h = self._expanded_height()
        self.setFixedHeight(expanded_h)
        self.card.setGeometry(0, 0, self.W, expanded_h)
        self.compact.setGeometry(0, 0, self.W, self._compact_height)
        self.expand_panel.setGeometry(0, self._compact_height, self.W, self.expand_panel.height())
        self.collapse_timer.start(5_000)   # 5초 뒤 자동 축소
        self._ensure_on_screen()           # 화면 밖이면 위로 이동

    def collapse(self):
        self.is_expanded = False
        self.expand_panel.hide()
        self.setFixedHeight(self._compact_height)
        self.card.setGeometry(0, 0, self.W, self._compact_height)
        self.compact.setGeometry(0, 0, self.W, self._compact_height)
        self.collapse_timer.stop()
        self._restore_pre_expand_pos()     # 임시 이동했으면 원위치

    def _ensure_on_screen(self):
        """확장 후 화면 하단을 넘어가면 위젯을 위로 이동.
        축소 시 _restore_pre_expand_pos() 에서 원위치 복귀."""
        x = self.x()
        y = self.y()
        h = self.height()   # 확장 후 실제 높이 (setFixedHeight 직후라 EXPAND_H 와 동일)

        # 위젯이 속한 모니터: frameGeometry().center() 는 막 확장된 직후라 늦게
        # 업데이트될 수 있어, 좌상단 점 기준으로 결정한다.
        screen = QApplication.screenAt(QPoint(x, y))
        if screen is None:
            screen = QApplication.primaryScreen()
        geo = screen.availableGeometry()

        bottom = y + h
        max_y  = geo.y() + geo.height() - self.SCREEN_MARGIN
        if bottom <= max_y:
            return  # 화면 안에 들어옴 — 이동 불필요
        new_y = max_y - h
        new_y = max(geo.y() + self.SCREEN_MARGIN, new_y)   # 위쪽도 화면 안에
        self._pre_expand_y = y
        self.move(x, new_y)
        self.raise_()    # 다른 위젯과 겹쳐도 위에 표시

    def _restore_pre_expand_pos(self):
        if getattr(self, "_pre_expand_y", None) is not None:
            self.move(self.x(), self._pre_expand_y)
            self._pre_expand_y = None

    # ── 드래그 이동 + 클릭 토글 ──────────────────────────────────────────
    DRAG_THRESHOLD = 4   # 이 거리 이상 움직이면 드래그로 간주

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos  = event.globalPosition().toPoint() - self.pos()
            self._press_pos = event.globalPosition().toPoint()
            self._moved     = False

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos:
            if not self._moved and self._press_pos:
                delta = event.globalPosition().toPoint() - self._press_pos
                if abs(delta.x()) > self.DRAG_THRESHOLD or abs(delta.y()) > self.DRAG_THRESHOLD:
                    self._moved = True
            if self._moved:
                self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        # 드래그가 아니었으면(거의 안 움직임) = 클릭 → 확장/축소 토글
        if event.button() == Qt.MouseButton.LeftButton and not self._moved:
            self.toggle_expand()
        self._drag_pos  = None
        self._press_pos = None
        self._moved     = False

    # ── 우클릭 메뉴 ────────────────────────────────────────────────────────
    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(TRAY_MENU_STYLE)
        metrics = stock_metrics(
            self.data,
            self.current_price or self.data.get("avg_price", 0),
            self.usd_krw_rate,
        )
        buy_label = "💧   물타기" if metrics["profit_rate"] < 0 else "🔥   불타기"
        buy_act  = menu.addAction(buy_label)
        edit_act = menu.addAction("✏️   수정")
        memo_act = menu.addAction("📝   메모")
        del_act  = menu.addAction("🗑️   삭제")

        action = menu.exec(event.globalPos())
        if action == buy_act:
            self.buy_requested.emit(self.data["code"])
        elif action == edit_act:
            self._open_edit()
        elif action == memo_act:
            self.memo_requested.emit(self.data["code"])
        elif action == del_act:
            self.deleted.emit(self.data["code"])
            self.close()

    def _open_edit(self):
        dlg = StockDialog(data=self.data)
        if dlg.exec():
            new = dlg.get_data()
            self.data["avg_price"] = new["avg_price"]
            self.data["quantity"]  = new["quantity"]
            if "buy_exchange_rate" in new:
                self.data["buy_exchange_rate"] = new["buy_exchange_rate"]
            else:
                self.data.pop("buy_exchange_rate", None)
            if self.current_price:
                self._update_detail(self.current_price)
            self.edited.emit(self.data["code"])


# ─── 폭이 모자라면 …로 줄이고, 줄였을 때만 hover 툴팁으로 전체 표시하는 라벨 ──
class ElidedLabel(QLabel):
    """할당된 폭을 넘는 텍스트는 끝을 …로 줄여 표시하고, 줄여진 경우에만 마우스를
    올렸을 때(hover) 전체 텍스트를 툴팁으로 보여준다. 위젯 폭을 고정해도 긴 이름이
    레이아웃을 밀지 않도록 minimumSizeHint 를 0 으로 둬 폭이 줄어들 수 있게 한다."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setMinimumWidth(0)
        self.setTextFull(text)

    def setTextFull(self, text: str):
        self._full = text or ""
        self._apply_elide()

    def fullText(self) -> str:
        return self._full

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:
        # 원하는 폭은 '전체 텍스트' 기준 (현재 줄여진 텍스트가 아니라) — 레이아웃이
        # 공간이 있으면 전부 보여주고, 모자라면 stretch/max 로 줄여 …로 만든다.
        h = super().sizeHint().height()
        return QSize(self.fontMetrics().horizontalAdvance(self._full) + 2, h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self):
        fm = self.fontMetrics()
        elided = fm.elidedText(self._full, Qt.TextElideMode.ElideRight, max(0, self.width()))
        super().setText(elided)
        self.setToolTip(self._full if elided != self._full else "")


# ─── 관심종목 압축 행 (태그 그룹 안에 들어감) ─────────────────────────────────
class CompactWatchRow(QWidget):
    """태그 그룹 위젯이 펼쳐질 때 아래로 나오는 초압축 관심종목 한 행.

    한 줄에 종목명 + 현재가 + 등락률 + 미니 일봉 스파크라인만 담는다. 보유
    StockWidget 보다 훨씬 작다. 자체적으로 일봉 기준 시세를 60초마다 폴링한다.
    """

    ROW_H   = 38
    SPARK_W = 80
    SPARK_H = 30
    POLL_MS = 60_000
    STAGGER_MS = 500
    POPUP_SCALE = 7.5            # hover 시 확대 팝업 배율 (기존 2.5의 3배)
    POPUP_BASE_MONTHS = 3        # 기준 기간(이 기간에서 팝업 가로폭 = SPARK_W*POPUP_SCALE)
    TRADING_DAYS_PER_MONTH = 21  # 1개월 ≈ 21 거래일 (표시 캔들 수 환산)
    MINI_CANDLES = 30            # 행에 박힌 미니 차트에 표시할 일봉 수

    def __init__(self, item: dict, stagger_idx: int = 0, parent=None, ma_settings: dict | None = None):
        super().__init__(parent)
        self.data = item
        self.current_price: float = 0.0
        self._chart_popup: ChartPopup | None = None
        # 확대 팝업 표시 설정(이동평균선·종목명·표시 기간) — 매니저가 넘긴 공유 dict 참조(제자리 갱신).
        self._ma_settings = ma_settings if ma_settings is not None else {
            "ma5": True, "ma20": True, "ma60": True, "show_name": True, "popup_months": 3,
            "axis_date": False, "axis_price": False,
        }
        self.setFixedHeight(self.ROW_H)
        self._build_ui()
        # 일봉 차트 위 hover → 확대 팝업 (이벤트 필터로 Enter/Leave 감지)
        self.sparkline.installEventFilter(self)
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._fetch)
        QTimer.singleShot(stagger_idx * self.STAGGER_MS, self._start)

    def _start(self):
        self.poll_timer.start(self.POLL_MS)
        self._fetch()

    def stop(self):
        self.poll_timer.stop()
        self._hide_chart_popup()

    # ── 일봉 차트 hover 확대 팝업 (기존 캔들 재사용, 네트워크 X) ──────────────
    def eventFilter(self, obj, event):
        if obj is self.sparkline:
            if event.type() == QEvent.Type.Enter:
                self._show_chart_popup()
            elif event.type() == QEvent.Type.Leave:
                self._hide_chart_popup()
        return super().eventFilter(obj, event)

    def _active_ma_periods(self) -> tuple:
        """관리창 체크 상태에 따라 표시할 이동평균 기간들 (예: (5, 20, 60))."""
        s = self._ma_settings or {}
        return tuple(p for p, key in ((5, "ma5"), (20, "ma20"), (60, "ma60")) if s.get(key, True))

    def _popup_months(self) -> int:
        """확대 차트 표시 기간(1~6개월). 관리창 슬라이더 값(공유 dict)을 따른다."""
        s = self._ma_settings or {}
        return max(1, min(6, int(s.get("popup_months", self.POPUP_BASE_MONTHS) or self.POPUP_BASE_MONTHS)))

    def _show_chart_popup(self):
        # 미니 차트가 보유한 전체 일봉 이력을 재사용 — 새 네트워크 호출은 하지 않는다
        candles = getattr(self.sparkline, "candles", None)
        if not candles or self.sparkline.mode != "candle":
            return
        # 기간이 늘수록 캔들 크기(밀도)·세로 높이는 그대로 두고 가로 폭만 비례해 넓힌다.
        months = self._popup_months()
        display_count = months * self.TRADING_DAYS_PER_MONTH
        chart_w = round(self.SPARK_W * self.POPUP_SCALE * months / self.POPUP_BASE_MONTHS)
        chart_h = round(self.SPARK_H * self.POPUP_SCALE)
        # 기간 변경 시 폭이 달라지므로(ChartPopup 은 고정 크기) 필요하면 새로 만든다.
        if self._chart_popup is None or self._chart_popup.chart.W != chart_w:
            if self._chart_popup is not None:
                self._chart_popup.hide()
                self._chart_popup.deleteLater()
            self._chart_popup = ChartPopup(chart_w, chart_h, parent=self)
        # '종목명표시'가 켜져 있으면 차트 배경에 깔 종목명을 넘긴다(꺼져 있으면 빈 값).
        s = self._ma_settings or {}
        show_name = bool(s.get("show_name", True))
        name = (self.data.get("name") or self.data.get("code", "")) if show_name else ""
        self._chart_popup.show_with(
            candles, self.sparkline.mapToGlobal(QPoint(0, 0)), self.sparkline.size(),
            ma_periods=self._active_ma_periods(),
            display_count=display_count,
            name=name,
            show_date_axis=bool(s.get("axis_date", False)),
            show_price_axis=bool(s.get("axis_price", False)),
        )

    def _hide_chart_popup(self):
        if self._chart_popup is not None:
            self._chart_popup.hide()

    def _build_ui(self):
        hl = QHBoxLayout(self)
        hl.setContentsMargins(10, 1, 8, 1)
        hl.setSpacing(6)

        # 이름은 왼쪽에 두고, 남는 폭(아래 addStretch)을 이름과 가격 사이로 보내
        # 가격·등락률·일봉 차트를 오른쪽에 뭉쳐 붙인다. 폭이 모자라면(긴 이름)
        # 이름이 …로 줄고 hover 시 전체 표시.
        self.name_lbl = ElidedLabel(self.data.get("name", self.data["code"]))
        self.name_lbl.setFont(QFont("Malgun Gothic", 9))
        self.name_lbl.setStyleSheet(f"color: {C['subtext']};")
        hl.addWidget(self.name_lbl)

        # 남는 폭은 여기로 — 가격/등락률/차트가 오른쪽에 붙는다
        hl.addStretch()

        self.price_lbl = QLabel("─")
        self.price_lbl.setFont(QFont("Malgun Gothic", 10, QFont.Weight.Bold))
        self.price_lbl.setStyleSheet(f"color: {C['text']};")
        hl.addWidget(self.price_lbl)

        self.rate_lbl = QLabel("")
        self.rate_lbl.setFont(QFont("Malgun Gothic", 9))
        self.rate_lbl.setStyleSheet(f"color: {C['subtext']};")
        hl.addWidget(self.rate_lbl)

        self.sparkline = SparklineWidget(self, width=self.SPARK_W, height=self.SPARK_H)
        hl.addWidget(self.sparkline, 0, Qt.AlignmentFlag.AlignVCenter)

    def _fetch(self):
        # 지수/국내/해외를 타입·시장에 맞게 라우팅 (fetch_watch_* 가 분기)
        result = fetch_watch_quote(self.data)
        if result:
            self._apply_price(result)
        # 확대 팝업의 3개월·이동평균선까지 그릴 수 있게 긴 이력을 받되,
        # 미니 차트에는 최근 일부(MINI_CANDLES)만 표시한다.
        daily = fetch_watch_daily(self.data, max_candles=WATCH_POPUP_CANDLES)
        if daily and daily.get("candles"):
            self.sparkline.set_candles(daily["candles"], display_count=self.MINI_CANDLES)

    def _apply_price(self, result: dict):
        self.data["name"] = result["name"]
        self.name_lbl.setTextFull(result["name"])
        self.current_price = result["price"]
        price = result["price"]
        rate  = result["change_rate"]
        if is_index(self.data):
            self.price_lbl.setText(f"{price:,.2f}")   # 지수는 소수 2자리
        elif is_us_stock(self.data):
            self.price_lbl.setText(f"{price:,.4f}")
        else:
            self.price_lbl.setText(f"{price:,.0f}")
        if rate > 0:
            color, sign = C["red"], "▲"
        elif rate < 0:
            color, sign = C["blue"], "▼"
        else:
            color, sign = C["subtext"], "  "
        self.price_lbl.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: bold;")
        self.rate_lbl.setText(f"{sign}{abs(rate):.2f}%")
        self.rate_lbl.setStyleSheet(f"color: {color}; font-size: 10px;")


# ─── 태그 그룹 헤더 (드래그 이동 + 클릭 펼침/접힘 + 고정 버튼) ────────────────
class _GroupHeader(QWidget):
    """태그 그룹 위젯의 헤더. 색 점 + 태그명 + 개수 + 펼침 표시(▸/▾) + 고정(📌).

    헤더를 드래그하면 그룹 창이 이동하고, 클릭(이동 없음)하면 펼침/접힘 토글.
    고정 버튼은 자식 QPushButton 이라 클릭이 헤더로 전파되지 않는다(펼침 토글과
    분리). 고정 버튼은 펼쳐진 상태에서만 보인다.
    """

    DRAG_THRESHOLD = 4

    def __init__(self, group, title: str, color: str, parent=None):
        super().__init__(parent)
        self._group = group
        self._drag_off = None
        self._press = None
        self._moved = False
        self.setFixedHeight(group.HEADER_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        hl = QHBoxLayout(self)
        hl.setContentsMargins(12, 0, 6, 0)
        hl.setSpacing(7)

        self.dot = QLabel()
        self.dot.setFixedSize(9, 9)
        self.dot.setStyleSheet(f"background: {color}; border-radius: 4px;")
        hl.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)

        # 태그명도 길면 …로 줄이고 hover 시 전체 표시 (고정 폭에서 넘침 방지)
        self.title_lbl = ElidedLabel(title)
        self.title_lbl.setFont(QFont("Malgun Gothic", 10, QFont.Weight.Bold))
        self.title_lbl.setStyleSheet(f"color: {C['text']};")
        self.title_lbl.setMaximumWidth(max(40, group.W - 115))
        hl.addWidget(self.title_lbl)

        self.count_lbl = QLabel("")
        self.count_lbl.setFont(QFont("Malgun Gothic", 9))
        self.count_lbl.setStyleSheet(f"color: {C['subtext']};")
        hl.addWidget(self.count_lbl)
        hl.addStretch()

        self.chev = QLabel("▸")
        self.chev.setStyleSheet(f"color: {C['subtext']}; font-size: 10px;")
        hl.addWidget(self.chev, 0, Qt.AlignmentFlag.AlignVCenter)

        self.pin_btn = QPushButton("📌")
        self.pin_btn.setFixedSize(22, 22)
        self.pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pin_btn.setToolTip("고정 — 마우스가 벗어나도 계속 펼침")
        self.pin_btn.clicked.connect(group.toggle_pin)
        hl.addWidget(self.pin_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.pin_btn.hide()
        self.set_pinned(False)

    def set_count(self, n: int):
        self.count_lbl.setText(f"({n})")

    def set_appearance(self, title: str, color: str):
        """태그 이름/색만 제자리 갱신 (멤버 재조회 없이)."""
        self.title_lbl.setTextFull(title)
        self.dot.setStyleSheet(f"background: {color}; border-radius: 4px;")

    def set_expanded(self, expanded: bool):
        self.chev.setText("▾" if expanded else "▸")
        self.pin_btn.setVisible(expanded)

    def set_pinned(self, pinned: bool):
        if pinned:
            self.pin_btn.setStyleSheet(
                f"QPushButton {{ background: {C['blue']}; border: none; border-radius: 6px; }}"
            )
        else:
            self.pin_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; border: none; border-radius: 6px; }}"
                f"QPushButton:hover {{ background: {C['surface']}; }}"
            )

    # ── 드래그 이동 + 클릭 토글 ──────────────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_off = event.globalPosition().toPoint() - self._group.pos()
            self._press = event.globalPosition().toPoint()
            self._moved = False

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_off is not None:
            if not self._moved and self._press is not None:
                delta = event.globalPosition().toPoint() - self._press
                if abs(delta.x()) > self.DRAG_THRESHOLD or abs(delta.y()) > self.DRAG_THRESHOLD:
                    self._moved = True
            if self._moved:
                self._group.move(event.globalPosition().toPoint() - self._drag_off)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self._moved:
            self._group.toggle_expand()
        self._drag_off = None
        self._press = None
        self._moved = False


# ─── 태그 그룹 위젯 (관심종목을 태그별로 묶는 떠있는 위젯) ─────────────────────
class TagGroupWidget(QWidget):
    """태그(또는 '태그 없음') 하나에 대응하는 떠있는 그룹 위젯.

    접힘 상태에서는 헤더(색 점 + 태그명 + 개수)만 보이고, 헤더를 클릭하면 해당
    태그의 관심종목들이 아래로 펼쳐진다. 고정(📌)하면 마우스가 벗어나도 계속
    펼쳐져 있고, 고정하지 않으면 마우스가 위젯을 벗어날 때 자동으로 접힌다.
    """

    pin_toggled      = pyqtSignal(str, bool)   # group_key, pinned
    manage_requested = pyqtSignal()

    # 고정 폭 — 가장 긴 이름에 맞춰 늘리지 않고 모두 같은 폭. 짧은 이름(지수·국내
    # 종목)은 그대로 보이고, 폭을 넘는 긴 이름만 …로 줄여 hover 시 전체 표시.
    # 값: 지수명(다우존스 등) + 긴 지수값(50,571.96) + 등락률 + 미니차트가 간격
    # 없이 들어가는 선.
    WIDTH    = 324
    HEADER_H = 38
    RADIUS   = 13
    PANEL_TOP = 4
    PANEL_BOTTOM = 8
    COLLAPSE_POLL_MS = 250
    SCREEN_MARGIN = 10

    def __init__(self, group_key: str, title: str, color: str, items: list[dict],
                 width: int | None = None, pinned: bool = False, stagger_base: int = 0,
                 ma_settings: dict | None = None):
        super().__init__()
        self.group_key = group_key
        self.title = title
        self.color = color
        self.items = items
        self.ma_settings = ma_settings   # 확대 팝업 이동평균선 표시 설정 (공유 dict)
        self.pinned = bool(pinned)
        self.is_expanded = self.pinned
        self.W = width or self.WIDTH
        self.rows: list[CompactWatchRow] = []
        self._pre_expand_y = None

        # 고정 안 한 상태에서 마우스가 벗어나면 접도록 주기적으로 커서 위치를 확인.
        # (자식 위젯 위로 이동할 때의 leaveEvent 오작동을 피하려 폴링 방식 사용)
        self._hover_timer = QTimer(self)
        self._hover_timer.timeout.connect(self._check_hover)

        self._build_ui(title, stagger_base)
        self._relayout()
        if self.pinned:
            self.header.set_pinned(True)

    def _panel_h(self) -> int:
        return self.PANEL_TOP + len(self.rows) * CompactWatchRow.ROW_H + self.PANEL_BOTTOM

    # ── UI 구성 ────────────────────────────────────────────────────────────
    def _build_ui(self, title: str, stagger_base: int):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.card = QFrame(self)
        self.card.setObjectName("card")
        self.card.setStyleSheet(f"""
            QFrame#card {{
                background: {C['bg']};
                border: 1px solid {C['border']};
                border-radius: {self.RADIUS}px;
            }}
        """)

        # 헤더
        self.header = _GroupHeader(self, title, self.color, parent=self.card)
        self.header.set_count(len(self.items))

        # 펼침 패널
        self.panel = QWidget(self.card)
        self.panel.setStyleSheet("background: transparent;")
        pv = QVBoxLayout(self.panel)
        pv.setContentsMargins(0, 0, 0, self.PANEL_BOTTOM - 2)
        pv.setSpacing(0)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background: {C['border']}; max-height: 1px; border: none;")
        pv.addWidget(sep)
        pv.addSpacing(self.PANEL_TOP - 1)
        for i, item in enumerate(self.items):
            row = CompactWatchRow(item, stagger_idx=stagger_base + i, parent=self.panel,
                                  ma_settings=self.ma_settings)
            self.rows.append(row)
            pv.addWidget(row)
        self.panel.hide()

    # ── 레이아웃(현재 펼침 상태에 맞춰 크기·자식 배치) ────────────────────
    def _relayout(self):
        total_h = self.HEADER_H + (self._panel_h() if self.is_expanded else 0)
        self.setFixedSize(self.W, total_h)
        self.card.setGeometry(0, 0, self.W, total_h)
        self.header.setGeometry(0, 0, self.W, self.HEADER_H)
        self.panel.setGeometry(0, self.HEADER_H, self.W, self._panel_h())
        self.panel.setVisible(self.is_expanded)
        self.header.set_expanded(self.is_expanded)

    def set_width(self, new_w: int):
        if new_w == self.W:
            return
        self.W = new_w
        self._relayout()

    def set_appearance(self, title: str, color: str):
        """멤버는 그대로 둔 채 태그 이름/색만 갱신 — 재생성·재조회 없음."""
        self.title = title
        self.color = color
        self.header.set_appearance(title, color)

    # ── 펼침 / 접힘 / 고정 ────────────────────────────────────────────────
    def toggle_expand(self):
        if self.is_expanded:
            self.collapse()
            if self.pinned:
                # 펼친 그룹을 직접 접으면 고정도 해제
                self.pinned = False
                self.header.set_pinned(False)
                self.pin_toggled.emit(self.group_key, False)
        else:
            self.expand()

    def expand(self):
        if self.is_expanded:
            return
        self.is_expanded = True
        self._relayout()
        self._ensure_on_screen()
        self.raise_()
        if not self.pinned:
            self._hover_timer.start(self.COLLAPSE_POLL_MS)

    def collapse(self):
        if not self.is_expanded:
            return
        self.is_expanded = False
        self._hover_timer.stop()
        self._relayout()
        self._restore_pre_expand_pos()

    def toggle_pin(self):
        self.pinned = not self.pinned
        self.header.set_pinned(self.pinned)
        if self.pinned:
            if not self.is_expanded:
                self.expand()
            self._hover_timer.stop()
        else:
            if self.is_expanded:
                self._hover_timer.start(self.COLLAPSE_POLL_MS)
        self.pin_toggled.emit(self.group_key, self.pinned)

    def _check_hover(self):
        if self.pinned or not self.is_expanded:
            self._hover_timer.stop()
            return
        if not self.frameGeometry().contains(QCursor.pos()):
            self.collapse()

    # ── 펼침 시 화면 밖이면 위로 이동, 접힐 때 원위치 ─────────────────────
    def _ensure_on_screen(self):
        x, y, h = self.x(), self.y(), self.height()
        screen = QApplication.screenAt(QPoint(x, y)) or QApplication.primaryScreen()
        geo = screen.availableGeometry()
        max_y = geo.y() + geo.height() - self.SCREEN_MARGIN
        if y + h <= max_y:
            return
        new_y = max(geo.y() + self.SCREEN_MARGIN, max_y - h)
        self._pre_expand_y = y
        self.move(x, new_y)

    def _restore_pre_expand_pos(self):
        if self._pre_expand_y is not None:
            self.move(self.x(), self._pre_expand_y)
            self._pre_expand_y = None

    # ── 우클릭: 관심종목 관리 ─────────────────────────────────────────────
    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(TRAY_MENU_STYLE)
        manage_act = menu.addAction("⭐   관심종목 관리")
        if menu.exec(event.globalPos()) == manage_act:
            # 관리 → 그룹 재구성(이 위젯 파괴 가능)이 contextMenu 처리 중에 일어나
            # 크래시하지 않도록, 이벤트 루프로 넘겨 안전하게 연다.
            QTimer.singleShot(0, self.manage_requested.emit)
