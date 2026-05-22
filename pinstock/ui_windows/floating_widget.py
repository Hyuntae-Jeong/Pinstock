"""화면에 떠있는 단일 종목 위젯."""

from PyQt6.QtWidgets import (
    QWidget, QFrame, QLabel, QHBoxLayout, QVBoxLayout, QMenu, QApplication,
)
from PyQt6.QtCore import Qt, QTimer, QPoint, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics

from ..core.api import (
    fetch_stock, fetch_minute_chart, fetch_daily_chart,
    fetch_us_stock, fetch_us_minute_chart, fetch_us_daily_chart,
)
from ..core.portfolio import is_us_stock, stock_metrics
from .theme import C, TRAY_MENU_STYLE
from .chart_widget import SparklineWidget
from .manage_dialog import StockDialog


# ─── 개별 주식 위젯 ───────────────────────────────────────────────────────────
class StockWidget(QWidget):
    """화면에 떠있는 하나의 주식 위젯"""

    deleted        = pyqtSignal(str)   # code 전달
    edited         = pyqtSignal(str)   # 수정 완료 후 저장 요청
    price_updated  = pyqtSignal(str)   # 현재가 갱신 시 (마스터 위젯 재집계용)

    MIN_W      = 240    # 기본(최소) 가로폭
    COMPACT_H  = 58     # 축소 높이 (2줄 레이아웃, 압축)
    EXPAND_H_KR = 214
    EXPAND_H_US = 268
    EXPAND_H   = EXPAND_H_KR
    RADIUS     = 13     # 모서리 반지름

    def __init__(self, stock_data: dict, width: int | None = None, stagger_idx: int = 0):
        super().__init__()
        self.data = stock_data          # code, name, avg_price, quantity, pos
        self.current_price: float = 0
        self.usd_krw_rate: float | None = None
        self.is_expanded: bool = False
        self._drag_pos = None
        self._press_pos = None    # 좌클릭 시작 위치 (드래그/클릭 구분용)
        self._moved: bool = False # 일정 거리 이상 움직였는지
        self._stagger_idx = stagger_idx   # 동시 호출 분산용 인덱스

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
        cur_h = self.EXPAND_H if self.is_expanded else self.COMPACT_H
        self.setFixedWidth(new_w)
        self.card.setGeometry(0, 0, new_w, cur_h)
        self.compact.setGeometry(0, 0, new_w, self.COMPACT_H)
        panel_h = self.EXPAND_H - self.COMPACT_H
        self.expand_panel.setGeometry(0, self.COMPACT_H, new_w, panel_h)

    def _make_row(self, parent_layout, key_text: str, bold=False) -> tuple[QHBoxLayout, QLabel, QLabel]:
        """키-값 한 줄 생성, 값 QLabel 반환"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)

        key_lbl = QLabel(key_text)
        key_lbl.setStyleSheet(f"color: {C['subtext']}; font-size: 10px;")
        key_lbl.setFixedWidth(58)
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

        self.price_lbl.setText(f"{price:,}")

        if rate > 0:
            color = C["red"]
            sign  = "▲"
        elif rate < 0:
            color = C["blue"]
            sign  = "▼"
        else:
            color = C["subtext"]
            sign  = "  "

        self.price_lbl.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")
        self.rate_lbl.setText(f"{sign}{abs(rate):.2f}%")
        self.rate_lbl.setStyleSheet(f"color: {color}; font-size: 9px;")

        self._update_detail(price)

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
            self.avg_val.setText(f"{float(avg):,.2f} USD")
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
        else:
            self.EXPAND_H = self.EXPAND_H_KR
            self._set_row_visible(self.fx_row, False)
            self._set_row_visible(self.fx_profit_row, False)
            self._set_row_visible(self.total_profit_row, False)
            self.avg_val.setText(f"{int(avg):,} 원")
            self.fx_val.setText("─")
            self.fx_profit_val.setText("─")
            self.fx_profit_val.setStyleSheet(f"color: {C['text']}; font-size: 11px;")
            self.total_profit_val.setText("─")
            self.total_profit_val.setStyleSheet(f"color: {C['text']}; font-size: 11px;")
            self.profit_val.setText(f"{sign}{profit:,} 원")
            self.profit_val.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")
        self.qty_val.setText(f"{qty:,} 주")
        self.invest_val.setText(f"{invest:,} 원")
        self.eval_val.setText(f"{eval_:,} 원")
        self.prate_val.setText(f"{sign}{prate:.2f}%")
        self.prate_val.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")
        # 패널 높이를 종목 타입(EXPAND_H)에 맞춰 동기화 — 패널 바닥이 카드 바닥과 일치해야 마지막 행이 안 잘림
        self.expand_panel.setGeometry(0, self.COMPACT_H, self.W, self.EXPAND_H - self.COMPACT_H)
        if self.is_expanded:
            self.setFixedHeight(self.EXPAND_H)
            self.card.setGeometry(0, 0, self.W, self.EXPAND_H)

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
        self.setFixedHeight(self.EXPAND_H)
        self.card.setGeometry(0, 0, self.W, self.EXPAND_H)
        self.collapse_timer.start(5_000)   # 5초 뒤 자동 축소
        self._ensure_on_screen()           # 화면 밖이면 위로 이동

    def collapse(self):
        self.is_expanded = False
        self.expand_panel.hide()
        self.setFixedHeight(self.COMPACT_H)
        self.card.setGeometry(0, 0, self.W, self.COMPACT_H)
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
        edit_act = menu.addAction("✏️   수정")
        menu.addSeparator()
        del_act  = menu.addAction("🗑️   삭제")

        action = menu.exec(event.globalPos())
        if action == edit_act:
            self._open_edit()
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
            if self.current_price:
                self._update_detail(self.current_price)
            self.edited.emit(self.data["code"])
