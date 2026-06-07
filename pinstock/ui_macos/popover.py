"""macOS 메뉴바 팝오버 패널.

메뉴바 ₩ 아이콘을 클릭하면 이 패널이 펼쳐진다.
구성: 포트폴리오 요약 + 종목 리스트(스크롤) + 설정 바.
"""

from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QMenu, QSlider, QApplication,
)
from PyQt6.QtCore import Qt, QPoint, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics, QScreen

from ..ui_windows.theme import C, TRAY_MENU_STYLE
from ..ui_windows.chart_widget import SparklineWidget
from ..core.portfolio import is_us_stock, stock_metrics


# macOS 시스템 한글 폰트 (Malgun Gothic 의 Mac 대체)
_FONT_FAMILY = "Apple SD Gothic Neo"
_NUMBER_FONT_FAMILY = "Arial"


def format_quantity(value) -> str:
    try:
        qty = float(value)
    except (TypeError, ValueError):
        qty = 0.0
    text = f"{qty:,.3f}".rstrip("0").rstrip(".")
    return text or "0"


# ─── 종목 한 행 ────────────────────────────────────────────────────────────
class StockRow(QWidget):
    """팝오버 안의 한 종목 행.
    - 좌클릭: 확장 (평단/수량/투자/평가/손익/수익률)
    - 우클릭: 수정/삭제 메뉴
    """

    expanded_toggled = pyqtSignal(str)   # code
    edit_requested   = pyqtSignal(str)   # code
    delete_requested = pyqtSignal(str)   # code

    COMPACT_H = 52
    EXTENDED_COMPACT_H = 68
    EXPAND_H_KR = 168
    EXPAND_H_US = 222
    EXPAND_H  = EXPAND_H_KR

    def __init__(self, stock_data: dict, parent=None):
        super().__init__(parent)
        self.data = stock_data
        self.current_price: float = 0
        self.usd_krw_rate: float | None = None
        self.is_expanded: bool = False
        self._prev_close: float = 0.0
        self.assets_hidden: bool = False
        self._compact_height = self.COMPACT_H
        self.setFixedHeight(self.COMPACT_H)
        self._build_ui()

    def _build_ui(self):
        self.setStyleSheet(f"""
            StockRow {{
                background: {C['bg']};
            }}
            StockRow:hover {{
                background: {C['surface']};
            }}
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── 상단 compact 행: 종목명/가격/등락 | sparkline ──────────────
        self.compact = QWidget(self)
        self.compact.setFixedHeight(self.COMPACT_H)
        self.compact.setStyleSheet("background: transparent;")
        hl = QHBoxLayout(self.compact)
        hl.setContentsMargins(14, 6, 14, 6)
        hl.setSpacing(10)

        # 좌측: 종목명 + 가격행
        info = QVBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(1)

        self.name_lbl = QLabel(self.data.get("name", self.data["code"]))
        self.name_lbl.setFont(QFont(_FONT_FAMILY, 12, QFont.Weight.Medium))
        self.name_lbl.setStyleSheet(f"color: {C['subtext']};")
        info.addWidget(self.name_lbl)

        price_row = QHBoxLayout()
        price_row.setContentsMargins(0, 0, 0, 0)
        price_row.setSpacing(8)

        self.price_lbl = QLabel("─")
        self.price_lbl.setFont(QFont(_NUMBER_FONT_FAMILY, 13, QFont.Weight.Bold))
        self.price_lbl.setStyleSheet(f"color: {C['text']};")
        price_row.addWidget(self.price_lbl)

        self.rate_lbl = QLabel("")
        self.rate_lbl.setFont(QFont(_NUMBER_FONT_FAMILY, 11))
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
            f"color: {C['subtext']}; font-size: 13px; font-weight: bold;"
        )
        self.extended_price_lbl.setMinimumHeight(18)
        extended_row.addWidget(self.extended_price_lbl)

        self.extended_rate_lbl = QLabel("")
        self.extended_rate_lbl.setFont(self.rate_lbl.font())
        self.extended_rate_lbl.setStyleSheet(f"color: {C['subtext']}; font-size: 11px;")
        self.extended_rate_lbl.setMinimumHeight(18)
        extended_row.addWidget(self.extended_rate_lbl)

        self.extended_icon_lbl = QLabel("")
        self.extended_icon_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.extended_icon_lbl.setFixedHeight(18)
        self.extended_icon_lbl.setStyleSheet("font-size: 9px; line-height: 18px;")
        extended_row.addWidget(self.extended_icon_lbl)
        extended_row.addStretch()

        self.extended_widgets = [self.extended_price_lbl, self.extended_rate_lbl, self.extended_icon_lbl]
        for widget in self.extended_widgets:
            widget.hide()
        info.addSpacing(2)
        info.addLayout(extended_row)
        hl.addLayout(info, 1)

        # 우측: sparkline
        self.sparkline = SparklineWidget(self.compact)
        hl.addWidget(self.sparkline, 0, Qt.AlignmentFlag.AlignVCenter)

        outer.addWidget(self.compact)

        # ── 확장 패널 (초기 숨김) ────────────────────────────────────────
        self.expand_panel = QWidget(self)
        self.expand_panel.setStyleSheet("background: transparent;")
        self.expand_panel.hide()

        vl = QVBoxLayout(self.expand_panel)
        vl.setContentsMargins(14, 2, 14, 10)
        vl.setSpacing(2)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background: {C['border']}; max-height: 1px; border: none;")
        vl.addWidget(sep)
        vl.addSpacing(2)

        self.avg_row, self.avg_key, self.avg_val = self._make_detail_row(vl, "평단가")
        self.fx_row, self.fx_key, self.fx_val = self._make_detail_row(vl, "매수환율")
        self.qty_row, self.qty_key, self.qty_val = self._make_detail_row(vl, "보유수량")
        self.invest_row, self.invest_key, self.invest_val = self._make_detail_row(vl, "투자원금")
        self.eval_row, self.eval_key, self.eval_val = self._make_detail_row(vl, "평가금액")
        self.profit_row, self.profit_key, self.profit_val = self._make_detail_row(vl, "평가손익", bold=True)
        self.fx_profit_row, self.fx_profit_key, self.fx_profit_val = self._make_detail_row(vl, "환차손익")
        self.total_profit_row, self.total_profit_key, self.total_profit_val = self._make_detail_row(vl, "총 평가손익", bold=True)
        self.prate_row, self.prate_key, self.prate_val = self._make_detail_row(vl, "수익률", bold=True)

        outer.addWidget(self.expand_panel)

    def _make_detail_row(self, parent_layout, key_text: str, bold: bool = False) -> tuple[QHBoxLayout, QLabel, QLabel]:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)

        key_lbl = QLabel(key_text)
        key_lbl.setStyleSheet(f"color: {C['subtext']}; font-size: 10px;")
        key_lbl.setFixedWidth(64)
        key_lbl.setFixedHeight(16)

        val_lbl = QLabel("─")
        val_lbl.setFont(QFont(_NUMBER_FONT_FAMILY, 11, QFont.Weight.Bold if bold else QFont.Weight.Normal))
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
        return StockRow._local_session_icon()

    # ── 데이터 적용 ───────────────────────────────────────────────────────
    def apply_price(self, result: dict):
        self.data["name"] = result["name"]
        self.name_lbl.setText(result["name"])
        self.current_price = result["price"]
        self._prev_close = float(result["price"] - result["change_price"])

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
            color, sign = C["red"], "▲"
        elif display_rate < 0:
            color, sign = C["blue"], "▼"
        else:
            color, sign = C["subtext"], "  "

        self.price_lbl.setStyleSheet(f"color: {color}; font-size: 13px; font-weight: bold;")
        self.rate_lbl.setText(f"{sign}{abs(display_rate):.2f}%")
        self.rate_lbl.setStyleSheet(f"color: {color}; font-size: 11px;")
        self._apply_extended_price(result)

        self._refresh_detail()

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
        self.extended_price_lbl.setStyleSheet(f"color: {color}; font-size: 13px; font-weight: bold;")
        self.extended_rate_lbl.setText(f"{sign}{abs(rate):.2f}%")
        self.extended_rate_lbl.setStyleSheet(f"color: {color}; font-size: 11px;")
        self.extended_icon_lbl.setText(session_icon)
        for widget in self.extended_widgets:
            widget.show()
        self._set_compact_height(self.EXTENDED_COMPACT_H)

    def _set_compact_height(self, height: int):
        self._compact_height = height
        if self.is_expanded:
            self.setFixedHeight(self._expanded_height())
            return
        if self.height() == height:
            return
        self.setFixedHeight(height)
        self.compact.setFixedHeight(height)

    def _expanded_height(self) -> int:
        return self.EXPAND_H + max(0, self._compact_height - self.COMPACT_H)

    def set_usd_krw_rate(self, rate: float | None):
        self.usd_krw_rate = rate
        self._refresh_detail()

    def apply_minute(self, prices: list, open_price: float):
        self.sparkline.set_data(prices, open_price, self._prev_close)

    def apply_daily(self, candles: list):
        self.sparkline.set_candles(candles)

    def _refresh_detail(self):
        avg    = float(self.data.get("avg_price", 0))
        qty    = float(self.data.get("quantity", 0))
        price  = self.current_price or avg
        metrics = stock_metrics(self.data, price, self.usd_krw_rate)
        invest = metrics["invest"]
        eval_ = metrics["eval"]
        profit = metrics["profit"]
        prate = metrics["profit_rate"]

        sign  = "+" if profit >= 0 else ""
        color = C["red"] if profit >= 0 else C["blue"]

        if self.data.get("market") == "US" or self.data.get("currency") == "USD":
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
        self.prate_val.setText(f"{sign}{prate:.2f}%")
        self.prate_val.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")
        if self.is_expanded:
            self.setFixedHeight(self._expanded_height())

    def set_assets_hidden(self, hidden: bool):
        self.assets_hidden = hidden
        # 숨김 진입 시 이미 펼쳐있던 행은 자동으로 접는다.
        if hidden and self.is_expanded:
            self.is_expanded = False
            self.expand_panel.hide()
            self.setFixedHeight(self._compact_height)

    # ── 확장 / 축소 ───────────────────────────────────────────────────────
    def toggle_expand(self):
        self.is_expanded = not self.is_expanded
        if self.is_expanded:
            self.expand_panel.show()
            self.setFixedHeight(self._expanded_height())
        else:
            self.expand_panel.hide()
            self.setFixedHeight(self._compact_height)
        self.expanded_toggled.emit(self.data["code"])

    # ── 마우스 이벤트 ────────────────────────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.assets_hidden:
                return
            self.toggle_expand()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(TRAY_MENU_STYLE)
        edit_act = menu.addAction("✏️   수정")
        menu.addSeparator()
        del_act  = menu.addAction("🗑️   삭제")
        action = menu.exec(event.globalPos())
        if action == edit_act:
            self.edit_requested.emit(self.data["code"])
        elif action == del_act:
            self.delete_requested.emit(self.data["code"])


# ─── 포트폴리오 요약 카드 ───────────────────────────────────────────────────
class PortfolioSummary(QWidget):
    """팝오버 상단의 4지표 카드.
    총 매입금액 / 평가금액 / 평가손익 / 수익률 을 2×2 그리드로 표시."""

    H = 92
    MASK = "•••••"

    clicked = pyqtSignal()   # 카드 클릭 → 자산 숨김 토글
    drag_started = pyqtSignal(QPoint)
    drag_moved = pyqtSignal(QPoint)
    drag_finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.H)
        self.setStyleSheet("background: transparent;")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("클릭하여 자산 정보 숨기기 / 표시")
        self._total_invest: int = 0
        self._total_eval: int = 0
        self._has_data: bool = False
        self._assets_hidden: bool = False
        self._press_global_pos: QPoint | None = None
        self._dragging: bool = False

        grid = QGridLayout(self)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(8)

        self.invest_val = self._make_cell(grid, 0, 0, "총 매입금액")
        self.eval_val   = self._make_cell(grid, 0, 1, "평가금액")
        self.profit_val = self._make_cell(grid, 1, 0, "평가손익", bold=True)
        self.prate_val  = self._make_cell(grid, 1, 1, "수익률",   bold=True)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global_pos = event.globalPosition().toPoint()
            self._dragging = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_global_pos is None:
            super().mouseMoveEvent(event)
            return
        pos = event.globalPosition().toPoint()
        if not self._dragging and (pos - self._press_global_pos).manhattanLength() >= 4:
            self._dragging = True
            self.drag_started.emit(self._press_global_pos)
        if self._dragging:
            self.drag_moved.emit(pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._press_global_pos is not None:
            if self._dragging:
                self.drag_finished.emit()
            else:
                self.clicked.emit()
            self._press_global_pos = None
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _make_cell(self, grid: QGridLayout, row: int, col: int,
                   key_text: str, bold: bool = False) -> QLabel:
        cell = QVBoxLayout()
        cell.setContentsMargins(0, 0, 0, 0)
        cell.setSpacing(0)

        key_lbl = QLabel(key_text)
        key_lbl.setStyleSheet(f"color: {C['subtext']}; font-size: 10px;")
        cell.addWidget(key_lbl)

        style = f"color: {C['text']}; font-size: 13px;"
        if bold:
            style += " font-weight: bold;"
        val_lbl = QLabel("─")
        val_lbl.setFont(QFont(_NUMBER_FONT_FAMILY, 13, QFont.Weight.Bold if bold else QFont.Weight.Normal))
        val_lbl.setStyleSheet(style)
        cell.addWidget(val_lbl)

        grid.addLayout(cell, row, col)
        return val_lbl

    def update_metrics(self, total_invest: int, total_eval: int):
        self._total_invest = total_invest
        self._total_eval   = total_eval
        self._has_data     = True
        self._render()

    def clear_metrics(self):
        self._total_invest = 0
        self._total_eval   = 0
        self._has_data     = False
        self._render()

    def set_assets_hidden(self, hidden: bool):
        self._assets_hidden = hidden
        self._render()

    def _render(self):
        muted = f"color: {C['subtext']}; font-size: 13px; font-weight: bold;"

        if self._assets_hidden:
            mask = self.MASK
            self.invest_val.setText(mask)
            self.eval_val.setText(mask)
            self.profit_val.setText(mask)
            self.profit_val.setStyleSheet(muted)
            self.prate_val.setText(mask)
            self.prate_val.setStyleSheet(muted)
            return

        if not self._has_data:
            self.invest_val.setText("0 원")
            self.eval_val.setText("0 원")
            self.profit_val.setText("─")
            self.profit_val.setStyleSheet(muted)
            self.prate_val.setText("─")
            self.prate_val.setStyleSheet(muted)
            return

        total_invest = self._total_invest
        total_eval   = self._total_eval
        profit = total_eval - total_invest
        prate  = (profit / total_invest * 100.0) if total_invest else 0.0

        if profit > 0:
            color, sign = C['red'], "+"
        elif profit < 0:
            color, sign = C['blue'], ""
        else:
            color, sign = C['subtext'], ""

        self.invest_val.setText(f"{total_invest:,} 원")
        self.eval_val.setText(f"{total_eval:,} 원")
        self.profit_val.setText(f"{sign}{profit:,} 원")
        self.profit_val.setStyleSheet(f"color: {color}; font-size: 13px; font-weight: bold;")
        self.prate_val.setText(f"{sign}{prate:.2f}%")
        self.prate_val.setStyleSheet(f"color: {color}; font-size: 13px; font-weight: bold;")


# ─── 관심종목 행 ─────────────────────────────────────────────────────────────
class WatchRow(QWidget):
    """관심종목 한 행 — 일봉 기준 간소 표시 (손익/평단가/수량 없음).

    종목명 + 현재가 + 전일대비% + 미니 일봉 스파크라인만 보여준다. 보유 행
    (StockRow)과 달리 확장 패널·연장거래 표시가 없다.
    """

    COMPACT_H = 52

    def __init__(self, watch_data: dict, parent=None):
        super().__init__(parent)
        self.data = watch_data
        self.current_price: float = 0
        self._prev_close: float = 0.0
        self.setFixedHeight(self.COMPACT_H)
        self._build_ui()

    def _build_ui(self):
        self.setStyleSheet(f"""
            WatchRow {{ background: {C['bg']}; }}
            WatchRow:hover {{ background: {C['surface']}; }}
        """)
        hl = QHBoxLayout(self)
        hl.setContentsMargins(14, 6, 14, 6)
        hl.setSpacing(10)

        info = QVBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(1)

        self.name_lbl = QLabel(self.data.get("name", self.data.get("code", "")))
        self.name_lbl.setFont(QFont(_FONT_FAMILY, 12, QFont.Weight.Medium))
        self.name_lbl.setStyleSheet(f"color: {C['subtext']};")
        info.addWidget(self.name_lbl)

        price_row = QHBoxLayout()
        price_row.setContentsMargins(0, 0, 0, 0)
        price_row.setSpacing(8)
        self.price_lbl = QLabel("─")
        self.price_lbl.setFont(QFont(_NUMBER_FONT_FAMILY, 13, QFont.Weight.Bold))
        self.price_lbl.setStyleSheet(f"color: {C['text']};")
        price_row.addWidget(self.price_lbl)
        self.rate_lbl = QLabel("")
        self.rate_lbl.setFont(QFont(_NUMBER_FONT_FAMILY, 11))
        self.rate_lbl.setStyleSheet(f"color: {C['subtext']};")
        price_row.addWidget(self.rate_lbl)
        price_row.addStretch()
        info.addLayout(price_row)
        hl.addLayout(info, 1)

        self.sparkline = SparklineWidget(self)
        hl.addWidget(self.sparkline, 0, Qt.AlignmentFlag.AlignVCenter)

    def apply_price(self, result: dict):
        self.data["name"] = result["name"]
        self.name_lbl.setText(result["name"])
        self.current_price = result["price"]
        self._prev_close = float(result["price"] - result["change_price"])
        price = result["price"]
        rate = result["change_rate"]
        self.price_lbl.setText(
            f"{price:,.4f}" if is_us_stock(self.data) else f"{price:,.0f}"
        )
        if rate > 0:
            color, sign = C["red"], "▲"
        elif rate < 0:
            color, sign = C["blue"], "▼"
        else:
            color, sign = C["subtext"], "  "
        self.price_lbl.setStyleSheet(f"color: {color}; font-size: 13px; font-weight: bold;")
        self.rate_lbl.setText(f"{sign}{abs(rate):.2f}%")
        self.rate_lbl.setStyleSheet(f"color: {color}; font-size: 11px;")

    def apply_daily(self, candles: list):
        self.sparkline.set_candles(candles)


# ─── 팝오버 메인 ─────────────────────────────────────────────────────────────
class Popover(QWidget):
    """메뉴바 아이콘 아래에 펼쳐지는 팝오버 패널.

    Qt.Tool + WindowStaysOnTopHint. macOS 에서 Qt.Tool 은 NSPanel 로 매핑되어
    앱이 inactive 가 되면 자동 숨김 → 외부 영역 클릭 시 popover 가 닫히는
    원하는 UX 가 자연스럽게 동작한다.
    (참고: 외부클릭 닫힘 직후 트레이 첫 클릭이 macOS 의 "inactive 앱 깨우기"
    동작에 소비되어 한 번 씹히는 현상이 있지만, NSStatusItem 기반 메뉴바 앱의
    표준 동작이라 받아들임. 두 번째 클릭에서 정상 오픈.)
    명시적 닫기 경로:
      - 트레이 아이콘 재클릭 (토글)
      - ESC 키
    """

    W        = 360
    MIN_H    = 420    # 종목이 적어도 시원하게 — 빈 상태에도 안내문이 잘 보이게
    VIEW_ROW_H = 32   # 보유/관심 뷰 토글 행 높이
    RADIUS   = 12
    OUTER_M  = 8      # 카드 바깥 마진 (그림자/여백)
    CONTROLS_H = 34   # 하단 설정(필터/투명도 슬라이더) 행 높이
    RESIZE_MARGIN = 10

    toggle_assets_requested  = pyqtSignal()      # 상단 요약 카드 클릭 → 자산 숨김 토글
    edit_requested           = pyqtSignal(str)   # code
    delete_requested         = pyqtSignal(str)   # code
    market_filter_changed    = pyqtSignal(str)   # ALL / KR / US
    opacity_changed          = pyqtSignal(float)   # 0.1 ~ 1.0
    height_changed           = pyqtSignal(int)     # px
    position_offset_changed  = pyqtSignal(int, int) # 메뉴바 기준 x/y offset
    pinned_changed           = pyqtSignal(bool)
    closed_by_user           = pyqtSignal()      # ESC 등 사용자 명시적 닫기

    OPACITY_MIN = 10   # 슬라이더 정수 단위 (퍼센트).
    OPACITY_MAX = 100

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Tool |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.rows: dict[str, StockRow] = {}
        self.watch_rows: dict[str, WatchRow] = {}
        self._stocks: list[dict] = []        # 보유 캐시 (뷰 전환 시 재구성용)
        self._watchlist: list[dict] = []     # 관심 캐시
        self._view: str = "holdings"         # "holdings" | "watch"
        self._price_cache: dict[str, dict] = {}    # code → 마지막 apply_price 결과
        self._minute_cache: dict[str, tuple] = {}  # code → (prices, open_price)
        self._daily_cache: dict[str, list] = {}    # code → 일봉 candles
        self._assets_hidden: bool = False
        self._usd_krw_rate: float | None = None
        self._market_filter: str = "ALL"
        self._preferred_height: int | None = None
        self._pinned: bool = False
        self._position_offset = QPoint(0, 0)
        # 외부 창 관리 앱(Rectangle 등)이 키보드 단축키로 팝오버를 옮기는 것을
        # 막기 위한 상태. 우리가 의도한 마지막 위치를 기록해 두고, moveEvent 에서
        # 그와 다른 이동(=외부 이동)이면 되돌린다. 크기는 setFixedSize 로 이미
        # NSWindow min==max 가 걸려 외부 리사이즈가 막히므로 위치만 잠그면 된다.
        self._intended_pos: QPoint | None = None
        self._reverting_move: bool = False
        self._last_anchor_pos: QPoint | None = None
        self._last_anchor_width: int = 0
        self._move_start_global_pos: QPoint | None = None
        self._move_start_window_pos: QPoint | None = None
        self._height_resizing: bool = False
        self._resize_start_y: int = 0
        self._resize_start_h: int = 0
        self.setMouseTracking(True)
        self._build_ui()

    def _build_ui(self):
        # ── 카드 배경 ────────────────────────────────────────────────────
        self.card = QFrame(self)
        self.card.setObjectName("popover_card")
        self.card.setStyleSheet(f"""
            QFrame#popover_card {{
                background: {C['bg']};
                border: 1px solid {C['border']};
                border-radius: {self.RADIUS}px;
            }}
        """)
        root_outer = QVBoxLayout(self)
        root_outer.setContentsMargins(8, 8, 8, 8)
        root_outer.addWidget(self.card)

        root = QVBoxLayout(self.card)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 뷰 토글: 보유 / 관심 ─────────────────────────────────────────
        self.view_row = QWidget(self.card)
        self.view_row.setStyleSheet("background: transparent;")
        self.view_row.setFixedHeight(self.VIEW_ROW_H)
        vr = QHBoxLayout(self.view_row)
        vr.setContentsMargins(10, 6, 10, 2)
        vr.setSpacing(6)
        self.view_buttons: dict[str, QPushButton] = {}
        for _text, _view in (("보유", "holdings"), ("관심", "watch")):
            vr.addWidget(self._make_view_btn(_text, _view))
        vr.addStretch()
        root.addWidget(self.view_row)

        # ── 상단: 포트폴리오 요약 ────────────────────────────────────────
        self.summary = PortfolioSummary(self.card)
        self.summary.clicked.connect(self.toggle_assets_requested.emit)
        self.summary.drag_started.connect(self._start_position_drag)
        self.summary.drag_moved.connect(self._move_position_drag)
        self.summary.drag_finished.connect(self._finish_position_drag)
        root.addWidget(self.summary)

        self.pin_btn = QPushButton("📌", self.card)
        self.pin_btn.setCheckable(True)
        self.pin_btn.setFixedSize(24, 22)
        self.pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pin_btn.clicked.connect(self._on_pin_clicked)
        self._sync_pin_button()
        self.pin_btn.move(self.W - 34, 9)
        self.pin_btn.raise_()

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setStyleSheet(f"background: {C['border']}; max-height: 1px; border: none;")
        root.addWidget(sep1)

        # ── 중단: 종목 리스트 (스크롤) ───────────────────────────────────
        self.scroll = QScrollArea(self.card)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet(f"""
            QScrollArea {{ background: {C['bg']}; border: none; }}
            QScrollBar:vertical {{ background: {C['bg']}; width: 8px; }}
            QScrollBar::handle:vertical {{
                background: {C['surface2']}; border-radius: 4px;
            }}
            QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
        """)

        self.rows_container = QWidget()
        self.rows_container.setStyleSheet(f"background: {C['bg']};")
        self.rows_layout = QVBoxLayout(self.rows_container)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(0)
        self.rows_layout.addStretch()   # 종목이 없을 때 빈 공간 차지

        self.empty_lbl = QLabel("종목이 없습니다.\n아래 ➕ 추가 버튼으로 시작하세요.")
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_lbl.setStyleSheet(
            f"color: {C['subtext']}; font-size: 12px; padding: 30px;"
        )
        self.rows_layout.insertWidget(0, self.empty_lbl)

        self.scroll.setWidget(self.rows_container)
        root.addWidget(self.scroll, 1)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"background: {C['border']}; max-height: 1px; border: none;")
        root.addWidget(sep2)

        # ── 설정 바: 투명도 슬라이더 ──────────────────────────────────────
        controls_row = QWidget(self.card)
        controls_row.setStyleSheet("background: transparent;")
        controls_row.setFixedHeight(self.CONTROLS_H)
        ch = QHBoxLayout(controls_row)
        ch.setContentsMargins(14, 7, 14, 7)
        ch.setSpacing(8)

        self.market_filter_buttons: dict[str, QPushButton] = {}
        for text, market in (("전체", "ALL"), ("한국", "KR"), ("미국", "US")):
            btn = self._make_market_filter_btn(text, market)
            ch.addWidget(btn)

        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(self.OPACITY_MIN, self.OPACITY_MAX)
        self.opacity_slider.setValue(self.OPACITY_MAX)
        self.opacity_slider.setToolTip("팝오버 투명도")
        self.opacity_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 3px;
                background: {C['surface2']};
                border-radius: 1px;
            }}
            QSlider::sub-page:horizontal {{
                background: {C['subtext']};
                border-radius: 1px;
            }}
            QSlider::handle:horizontal {{
                width: 10px;
                height: 10px;
                margin: -4px 0;
                background: {C['text']};
                border-radius: 5px;
            }}
        """)
        self.opacity_slider.valueChanged.connect(self._on_opacity_slider_changed)
        ch.addStretch(1)
        ch.addWidget(self.opacity_slider, 1)

        root.addWidget(controls_row)

    def _on_pin_clicked(self):
        self.set_pinned(self.pin_btn.isChecked())
        self.pinned_changed.emit(self._pinned)

    def _sync_pin_button(self):
        active = self._pinned
        bg = C["blue"] if active else "transparent"
        fg = C["bg"] if active else C["subtext"]
        hover = "#b4befe" if active else C["surface"]
        self.pin_btn.setChecked(active)
        self.pin_btn.setToolTip("상단 고정 해제" if active else "상단 고정")
        self.pin_btn.setStyleSheet(f"""
            QPushButton {{
                background: {bg};
                color: {fg};
                border: none;
                border-radius: 5px;
                font-size: 12px;
                padding: 0;
            }}
            QPushButton:hover {{ background: {hover}; }}
        """)

    def _apply_window_flags(self):
        flags = (
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.NoDropShadowWindowHint
        )
        if self._pinned:
            flags |= Qt.WindowType.Window
        else:
            flags |= Qt.WindowType.Tool
        was_visible = self.isVisible()
        pos = self.pos()
        self.setWindowFlags(flags)
        self._move_window(pos)
        if was_visible:
            self.show()
            self.raise_()

    def set_pinned(self, pinned: bool):
        self._pinned = bool(pinned)
        self._sync_pin_button()
        self._apply_window_flags()

    def is_pinned(self) -> bool:
        return self._pinned

    def _make_market_filter_btn(self, text: str, market: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda _, m=market: self._set_market_filter(m, emit=True))
        self.market_filter_buttons[market] = btn
        active = market == self._market_filter
        btn.setChecked(active)
        self._apply_market_filter_btn_style(btn, active)
        return btn

    def _apply_market_filter_btn_style(self, btn: QPushButton, active: bool):
        if active:
            bg = C["blue"]
            fg = C["bg"]
            hover = "#b4befe"
        else:
            bg = "transparent"
            fg = C["subtext"]
            hover = C["surface"]
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {bg};
                color: {fg};
                border: none;
                border-radius: 5px;
                padding: 3px 7px;
                font-size: 10px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background: {hover}; }}
        """)

    def _set_market_filter(self, market: str, *, emit: bool = False):
        if market not in {"ALL", "KR", "US"}:
            market = "ALL"
        self._market_filter = market
        for key, btn in self.market_filter_buttons.items():
            active = key == market
            btn.setChecked(active)
            self._apply_market_filter_btn_style(btn, active)
        if emit:
            self.market_filter_changed.emit(market)

    def set_market_filter(self, market: str):
        self._set_market_filter(market, emit=False)

    def _matches_market_filter(self, stock: dict) -> bool:
        if self._market_filter == "ALL":
            return True
        market = "US" if is_us_stock(stock) else "KR"
        return market == self._market_filter

    # ── 보유 / 관심 뷰 토글 ───────────────────────────────────────────────
    def _make_view_btn(self, text: str, view: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda _, v=view: self._set_view(v))
        self.view_buttons[view] = btn
        active = view == self._view
        btn.setChecked(active)
        self._apply_market_filter_btn_style(btn, active)   # 토글 버튼 스타일 공유
        return btn

    def _set_view(self, view: str):
        if view not in {"holdings", "watch"}:
            view = "holdings"
        self._view = view
        for key, btn in self.view_buttons.items():
            active = key == view
            btn.setChecked(active)
            self._apply_market_filter_btn_style(btn, active)
        # 관심 뷰에서는 손익 요약 카드를 숨긴다 (관심은 손익 무관)
        self.summary.setVisible(view == "holdings")
        self._render()
        self._apply_content_height()

    # ── 데이터 동기화 ────────────────────────────────────────────────────
    def set_stocks(self, stocks: list[dict]):
        """보유 종목 캐시 갱신. 보유 뷰일 때만 즉시 재구성."""
        self._stocks = stocks
        if self._view == "holdings":
            self._render()

    def set_watchlist(self, items: list[dict]):
        """관심종목 캐시 갱신. 관심 뷰일 때만 즉시 재구성."""
        self._watchlist = items
        if self._view == "watch":
            self._render()

    def _clear_all_rows(self):
        for row in list(self.rows.values()) + list(self.watch_rows.values()):
            self.rows_layout.removeWidget(row)
            row.deleteLater()
        self.rows.clear()
        self.watch_rows.clear()

    def _render(self):
        """현재 뷰(_view)에 맞는 행으로 리스트를 재구성."""
        self._clear_all_rows()
        if self._view == "holdings":
            self._render_holdings()
        else:
            self._render_watch()

    def _render_holdings(self):
        visible_stocks = [
            s for s in self._stocks
            if not s.get("hidden", False) and self._matches_market_filter(s)
        ]
        if not visible_stocks:
            self.empty_lbl.setText("종목이 없습니다.\n메뉴 → 종목 추가 로 시작하세요.")
            self.empty_lbl.show()
            return
        self.empty_lbl.hide()
        for s in visible_stocks:
            row = StockRow(s)
            row.assets_hidden = self._assets_hidden
            row.set_usd_krw_rate(self._usd_krw_rate)
            row.edit_requested.connect(self.edit_requested.emit)
            row.delete_requested.connect(self.delete_requested.emit)
            row.expanded_toggled.connect(self._on_row_expanded)
            self.rows[s["code"]] = row
            self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
            self._apply_cached(s["code"], row)

    def _render_watch(self):
        visible_items = [
            w for w in self._watchlist
            if not w.get("hidden", False) and self._matches_market_filter(w)
        ]
        if not visible_items:
            self.empty_lbl.setText("관심종목이 없습니다.\n메뉴 → 관심종목 추가 로 시작하세요.")
            self.empty_lbl.show()
            return
        self.empty_lbl.hide()
        for w in visible_items:
            row = WatchRow(w)
            self.watch_rows[w["code"]] = row
            self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
            self._apply_cached(w["code"], row)

    def _apply_cached(self, code: str, row):
        """뷰 전환/재구성 직후 캐시된 마지막 시세를 새 행에 즉시 반영해
        '─' 깜빡임을 막는다 (다음 폴링 전까지의 공백 메움)."""
        result = self._price_cache.get(code)
        if result:
            row.apply_price(result)
        if hasattr(row, "apply_minute") and code in self._minute_cache:
            prices, open_price = self._minute_cache[code]
            row.apply_minute(prices, open_price)
        elif code in self._daily_cache:
            row.apply_daily(self._daily_cache[code])

    def update_summary(self, total_invest: int, total_eval: int):
        if total_invest == 0 and total_eval == 0:
            self.summary.clear_metrics()
        else:
            self.summary.update_metrics(total_invest, total_eval)

    def update_stock_price(self, code: str, result: dict):
        self._price_cache[code] = result
        row = self.rows.get(code)
        if row:
            row.apply_price(result)

    def set_usd_krw_rate(self, rate: float | None):
        self._usd_krw_rate = rate
        for row in self.rows.values():
            row.set_usd_krw_rate(rate)

    def update_stock_minute(self, code: str, prices: list, open_price: float):
        self._minute_cache[code] = (prices, open_price)
        row = self.rows.get(code)
        if row:
            row.apply_minute(prices, open_price)

    def update_stock_daily(self, code: str, candles: list):
        self._daily_cache[code] = candles
        row = self.rows.get(code)
        if row:
            row.apply_daily(candles)

    # ── 관심종목 시세 (일봉 기준) ─────────────────────────────────────────
    def update_watch_price(self, code: str, result: dict):
        self._price_cache[code] = result
        row = self.watch_rows.get(code)
        if row:
            row.apply_price(result)

    def update_watch_daily(self, code: str, candles: list):
        self._daily_cache[code] = candles
        row = self.watch_rows.get(code)
        if row:
            row.apply_daily(candles)

    # ── 행 확장 시 자동 스크롤 ────────────────────────────────────────────
    def _on_row_expanded(self, code: str):
        """종목 행이 펼쳐지면 펼친 내용이 스크롤 영역 아래로 잘리지 않도록
        해당 행 전체가 보이는 위치까지 자동 스크롤한다 (접을 때는 무시)."""
        row = self.rows.get(code)
        if row is None or not row.is_expanded:
            return
        # 늘어난 행 높이가 레이아웃에 반영된 다음에 스크롤해야 위치가 맞다.
        QTimer.singleShot(0, lambda: self._ensure_row_visible(code))

    def _ensure_row_visible(self, code: str):
        row = self.rows.get(code)
        if row is None or not row.is_expanded:
            return
        self.scroll.ensureWidgetVisible(row, 0, 8)

    # ── 위치/표시 ────────────────────────────────────────────────────────
    def _calc_content_height(self) -> int:
        """현재 종목 수/확장 상태에 맞춘 컨텐츠 영역 높이 계산.
        스크롤이 필요한 경우 현재 모니터 높이 안에서 잘리고 스크롤바가 뜬다."""
        active_rows = self.rows if self._view == "holdings" else self.watch_rows
        if active_rows:
            rows_h = sum(r.height() for r in active_rows.values())
        else:
            rows_h = 120   # empty_lbl 안내 영역
        # 관심 뷰에서는 손익 요약 카드를 숨기므로 높이에서 제외
        summary_h = PortfolioSummary.H if self._view == "holdings" else 0

        # 뷰 토글 + (요약) + 구분선 2개 + 종목 영역 + 설정 바
        # + 카드 위/아래 outer margin (각각 OUTER_M)
        return (
            self.VIEW_ROW_H + summary_h + 1 + rows_h + 1
            + self.CONTROLS_H
            + self.OUTER_M * 2
        )

    def _apply_content_height(self):
        """뷰 전환 등으로 컨텐츠 높이가 바뀌었을 때 열려 있는 팝오버를 자동 높이로
        다시 맞춘다. 사용자가 수동으로 높이를 고정(_preferred_height)했으면 둔다."""
        if self._preferred_height is not None or not self.isVisible():
            return
        self.setFixedHeight(self._clamp_height(self._calc_content_height()))

    # ── 자산 정보 숨김 ────────────────────────────────────────────────────
    def set_assets_hidden(self, hidden: bool):
        """자산 표시/숨김 상태 적용. 시그널은 emit 하지 않는다.
        토글은 매니저가 중앙에서 처리하며 (메뉴 / 상단 카드 클릭) 이 메서드로 반영한다."""
        if self._assets_hidden == hidden:
            return
        self._assets_hidden = hidden
        self.summary.set_assets_hidden(hidden)
        for row in self.rows.values():
            row.set_assets_hidden(hidden)

    # ── 투명도 ────────────────────────────────────────────────────────────
    def set_opacity(self, value: float):
        """외부(매니저)에서 초기값 동기화. 시그널은 emit 하지 않는다."""
        pct = max(self.OPACITY_MIN, min(self.OPACITY_MAX, int(round(value * 100))))
        self.opacity_slider.blockSignals(True)
        self.opacity_slider.setValue(pct)
        self.opacity_slider.blockSignals(False)
        self.setWindowOpacity(pct / 100.0)

    def _on_opacity_slider_changed(self, pct: int):
        opacity = pct / 100.0
        self.setWindowOpacity(opacity)
        self.opacity_changed.emit(opacity)

    def set_preferred_height(self, height: int | None):
        """외부(매니저)에서 초기 높이 설정을 동기화. None 이면 자동 높이."""
        if height is None:
            self._preferred_height = None
            return
        self._preferred_height = self._clamp_height(int(height))
        if self.isVisible():
            self.setFixedHeight(self._preferred_height)

    def set_position_offset(self, offset: list | tuple | None):
        """메뉴바 기본 위치에서 사용자가 드래그한 상대 offset 을 적용한다."""
        if isinstance(offset, (list, tuple)) and len(offset) == 2:
            try:
                self._position_offset = QPoint(int(offset[0]), int(offset[1]))
                return
            except (TypeError, ValueError):
                pass
        self._position_offset = QPoint(0, 0)

    def _base_pos_for_anchor(
        self,
        anchor_global_pos: QPoint,
        anchor_width: int,
        target_w: int,
        target_h: int,
        screen: QScreen | None = None,
    ) -> QPoint:
        screen = screen or QApplication.screenAt(anchor_global_pos) or QApplication.primaryScreen()
        sg = screen.availableGeometry()
        x = anchor_global_pos.x() + anchor_width // 2 - target_w // 2
        y = anchor_global_pos.y() + 10
        x = max(sg.x() + 4, min(x, sg.x() + sg.width() - target_w - 4))
        y = max(sg.y() + 4, min(y, sg.y() + sg.height() - target_h - 4))
        return QPoint(x, y)

    def _clamp_position(self, pos: QPoint, preferred_screen: QScreen | None = None) -> QPoint:
        """좌표가 화면 밖으로 나가지 않게 보정한다.
        멀티 모니터 대응: 대상 좌표(pos)가 위치한 모니터를 찾아 그 화면 경계로 가둔다.
        만약 어느 모니터에도 걸쳐있지 않다면(모니터 연결 해제 등) 기본 screen 가이드를 따른다.
        """
        # 1. 대상 좌표가 현재 어느 스크린에 있는지 확인
        target_screen = QApplication.screenAt(pos)
        
        # 2. 해당 좌표에 스크린이 있다면 그 스크린의 경계를 사용, 없다면 전달받은 가이드나 주 모니터 사용
        screen = target_screen or preferred_screen or QApplication.primaryScreen()
        sg = screen.availableGeometry()

        x = max(sg.x() + 4, min(pos.x(), sg.x() + sg.width() - self.width() - 4))
        y = max(sg.y() + 4, min(pos.y(), sg.y() + sg.height() - self.height() - 4))
        return QPoint(x, y)

    def show_below(self, anchor_global_pos: QPoint, anchor_width: int = 0):
        """anchor_global_pos 아래에 팝오버를 표시. 화면 우상단 메뉴바 아이콘 기준."""
        target_w = self.W + self.OUTER_M * 2
        screen = QApplication.screenAt(anchor_global_pos) or QApplication.primaryScreen()
        max_h = self._max_height_for_screen(screen)
        content_h = self._calc_content_height()
        auto_h = max(self.MIN_H, min(content_h, max_h))
        target_h = self._clamp_height(self._preferred_height or auto_h, screen)

        self.setFixedSize(target_w, target_h)
        self._last_anchor_pos = QPoint(anchor_global_pos)
        self._last_anchor_width = int(anchor_width)

        # 메뉴바 아이콘 가운데 아래로 떨어뜨림 (Qt 트레이는 geometry 가 비어있는 경우가
        # 있어 anchor 좌표 기준으로 보정). 메뉴바와 살짝 떨어뜨리기 위해 10px 갭.
        base_pos = self._base_pos_for_anchor(anchor_global_pos, anchor_width, target_w, target_h, screen)
        target_pos = self._clamp_position(base_pos + self._position_offset, screen)

        self._move_window(target_pos)
        if not self.isVisible():
            self.show()
            # macOS Qt bug workaround: top-level 윈도우(특히 Qt.Window)는 show() 이후에
            # 다시 move()를 해줘야 저장된 위치에 정확히 박히는 경우가 있음.
            self._move_window(target_pos)
        else:
            self.raise_()
            self.activateWindow()

        self.pin_btn.raise_()

    def _move_window(self, pos: QPoint):
        """창을 옮기는 단일 통로. 의도한 위치를 기록해 둔다.
        우리 코드(show_below / 마우스 드래그 / 플래그 전환)의 모든 이동은 이
        메서드를 거치므로, moveEvent 에서 이 값과 다른 이동은 외부(Rectangle 등)
        가 일으킨 것으로 보고 되돌릴 수 있다."""
        self._intended_pos = QPoint(pos)
        self.move(pos)

    def _start_position_drag(self, global_pos: QPoint):
        self._move_start_global_pos = QPoint(global_pos)
        self._move_start_window_pos = self.pos()
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def _move_position_drag(self, global_pos: QPoint):
        if self._move_start_global_pos is None or self._move_start_window_pos is None:
            return
        delta = global_pos - self._move_start_global_pos
        self._move_window(self._clamp_position(self._move_start_window_pos + delta))

    def _finish_position_drag(self):
        self.unsetCursor()
        self._move_start_global_pos = None
        self._move_start_window_pos = None
        if self._last_anchor_pos is None:
            return
        base_pos = self._base_pos_for_anchor(
            self._last_anchor_pos,
            self._last_anchor_width,
            self.width(),
            self.height(),
        )
        self._position_offset = self.pos() - base_pos
        self.position_offset_changed.emit(self._position_offset.x(), self._position_offset.y())

    def _max_height_for_screen(self, screen: QScreen | None = None) -> int:
        screen = screen or QApplication.screenAt(self.frameGeometry().center())
        screen = screen or QApplication.primaryScreen()
        return max(self.MIN_H, screen.availableGeometry().height())

    def _clamp_height(self, height: int, screen: QScreen | None = None) -> int:
        return max(self.MIN_H, min(int(height), self._max_height_for_screen(screen)))

    def _in_height_resize_zone(self, pos) -> bool:
        return self.height() - self.RESIZE_MARGIN <= int(pos.y()) <= self.height()

    def mousePressEvent(self, event):
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._in_height_resize_zone(event.position())
        ):
            self._height_resizing = True
            self._resize_start_y = int(event.globalPosition().y())
            self._resize_start_h = self.height()
            self.setCursor(Qt.CursorShape.SizeVerCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._height_resizing:
            delta = int(event.globalPosition().y()) - self._resize_start_y
            height = self._clamp_height(self._resize_start_h + delta)
            self._preferred_height = height
            self.setFixedHeight(height)
            event.accept()
            return
        if self._in_height_resize_zone(event.position()):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._height_resizing and event.button() == Qt.MouseButton.LeftButton:
            self._height_resizing = False
            self.unsetCursor()
            self.height_changed.emit(self.height())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        if not self._height_resizing:
            self.unsetCursor()
        super().leaveEvent(event)

    def moveEvent(self, event):
        """Rectangle 같은 창 관리 앱이 control+option+방향키 등으로 팝오버를
        옮기는 것을 막는다. 우리(show_below / 드래그)의 이동은 _move_window 로
        _intended_pos 를 먼저 갱신하므로 현재 위치와 일치 → 통과. 그 외(=외부
        이동)는 마지막 의도 위치로 되돌려, 팝오버 이동을 마우스 드래그로만
        제한한다."""
        super().moveEvent(event)
        if self._intended_pos is None or self._reverting_move:
            return
        if self.pos() != self._intended_pos:
            self._reverting_move = True
            self.move(self._intended_pos)
            self._reverting_move = False

    # ── 키보드 ────────────────────────────────────────────────────────────
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.closed_by_user.emit()
            self.hide()
            return
        super().keyPressEvent(event)
