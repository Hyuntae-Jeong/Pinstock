"""macOS 메뉴바 팝오버 패널.

메뉴바 ₩ 아이콘을 클릭하면 이 패널이 펼쳐진다.
구성: 포트폴리오 요약 + 종목 리스트(스크롤) + 액션 바.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QMenu, QSlider, QApplication,
)
from PyQt6.QtCore import Qt, QPoint, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics, QScreen

from ..ui_windows.theme import C, TRAY_MENU_STYLE
from ..ui_windows.chart_widget import SparklineWidget


# macOS 시스템 한글 폰트 (Malgun Gothic 의 Mac 대체)
_FONT_FAMILY = "Apple SD Gothic Neo"


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
    EXPAND_H  = 168

    def __init__(self, stock_data: dict, parent=None):
        super().__init__(parent)
        self.data = stock_data
        self.current_price: int = 0
        self.is_expanded: bool = False
        self._prev_close: float = 0.0
        self.assets_hidden: bool = False
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
        self.price_lbl.setFont(QFont(_FONT_FAMILY, 13, QFont.Weight.Bold))
        self.price_lbl.setStyleSheet(f"color: {C['text']};")
        price_row.addWidget(self.price_lbl)

        self.rate_lbl = QLabel("")
        self.rate_lbl.setFont(QFont(_FONT_FAMILY, 11))
        self.rate_lbl.setStyleSheet(f"color: {C['subtext']};")
        price_row.addWidget(self.rate_lbl)
        price_row.addStretch()

        info.addLayout(price_row)
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

        self.avg_val    = self._make_detail_row(vl, "평단가")
        self.qty_val    = self._make_detail_row(vl, "보유수량")
        self.invest_val = self._make_detail_row(vl, "투자원금")
        self.eval_val   = self._make_detail_row(vl, "평가금액")
        self.profit_val = self._make_detail_row(vl, "평가손익", bold=True)
        self.prate_val  = self._make_detail_row(vl, "수익률",   bold=True)

        outer.addWidget(self.expand_panel)

    def _make_detail_row(self, parent_layout, key_text: str, bold: bool = False) -> QLabel:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)

        key_lbl = QLabel(key_text)
        key_lbl.setStyleSheet(f"color: {C['subtext']}; font-size: 10px;")
        key_lbl.setFixedWidth(64)

        val_lbl = QLabel("─")
        style = f"color: {C['text']}; font-size: 11px;"
        if bold:
            style += " font-weight: bold;"
        val_lbl.setStyleSheet(style)
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)

        row.addWidget(key_lbl)
        row.addWidget(val_lbl)
        parent_layout.addLayout(row)
        return val_lbl

    # ── 데이터 적용 ───────────────────────────────────────────────────────
    def apply_price(self, result: dict):
        self.data["name"] = result["name"]
        self.name_lbl.setText(result["name"])
        self.current_price = result["price"]
        self._prev_close = float(result["price"] - result["change_price"])

        price = result["price"]
        rate  = result["change_rate"]

        self.price_lbl.setText(f"{price:,}")

        if rate > 0:
            color, sign = C["red"], "▲"
        elif rate < 0:
            color, sign = C["blue"], "▼"
        else:
            color, sign = C["subtext"], "  "

        self.price_lbl.setStyleSheet(f"color: {color}; font-size: 13px; font-weight: bold;")
        self.rate_lbl.setText(f"{sign}{abs(rate):.2f}%")
        self.rate_lbl.setStyleSheet(f"color: {color}; font-size: 11px;")

        self._refresh_detail()

    def apply_minute(self, prices: list, open_price: float):
        self.sparkline.set_data(prices, open_price, self._prev_close)

    def apply_daily(self, candles: list):
        self.sparkline.set_candles(candles)

    def _refresh_detail(self):
        avg    = int(self.data.get("avg_price", 0))
        qty    = int(self.data.get("quantity", 0))
        price  = self.current_price or avg
        invest = avg * qty
        eval_  = price * qty
        profit = eval_ - invest
        prate  = (profit / invest * 100) if invest else 0

        sign  = "+" if profit >= 0 else ""
        color = C["red"] if profit >= 0 else C["blue"]

        self.avg_val.setText(f"{avg:,} 원")
        self.qty_val.setText(f"{qty:,} 주")
        self.invest_val.setText(f"{invest:,} 원")
        self.eval_val.setText(f"{eval_:,} 원")
        self.profit_val.setText(f"{sign}{profit:,} 원")
        self.profit_val.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")
        self.prate_val.setText(f"{sign}{prate:.2f}%")
        self.prate_val.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")

    def set_assets_hidden(self, hidden: bool):
        self.assets_hidden = hidden
        # 숨김 진입 시 이미 펼쳐있던 행은 자동으로 접는다.
        if hidden and self.is_expanded:
            self.is_expanded = False
            self.expand_panel.hide()
            self.setFixedHeight(self.COMPACT_H)

    # ── 확장 / 축소 ───────────────────────────────────────────────────────
    def toggle_expand(self):
        self.is_expanded = not self.is_expanded
        if self.is_expanded:
            self.expand_panel.show()
            self.setFixedHeight(self.EXPAND_H)
        else:
            self.expand_panel.hide()
            self.setFixedHeight(self.COMPACT_H)
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.H)
        self.setStyleSheet("background: transparent;")
        self._total_invest: int = 0
        self._total_eval: int = 0
        self._has_data: bool = False
        self._assets_hidden: bool = False

        grid = QGridLayout(self)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(8)

        self.invest_val = self._make_cell(grid, 0, 0, "총 매입금액")
        self.eval_val   = self._make_cell(grid, 0, 1, "평가금액")
        self.profit_val = self._make_cell(grid, 1, 0, "평가손익", bold=True)
        self.prate_val  = self._make_cell(grid, 1, 1, "수익률",   bold=True)

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
      - ❌ 종료 버튼
    """

    W        = 360
    MAX_H    = 640
    MIN_H    = 420    # 종목이 적어도 시원하게 — 빈 상태에도 안내문이 잘 보이게
    RADIUS   = 12
    OUTER_M  = 8      # 카드 바깥 마진 (그림자/여백)
    ACTION_H   = 44   # 하단 액션 바 높이
    CONTROLS_H = 28   # 액션 바 위 설정(투명도 슬라이더) 행 높이

    add_stock_requested      = pyqtSignal()
    manage_stocks_requested  = pyqtSignal()
    export_requested         = pyqtSignal()
    import_requested         = pyqtSignal()
    quit_requested           = pyqtSignal()
    edit_requested           = pyqtSignal(str)   # code
    delete_requested         = pyqtSignal(str)   # code
    assets_hidden_changed    = pyqtSignal(bool)
    opacity_changed          = pyqtSignal(float)   # 0.6 ~ 1.0

    OPACITY_MIN = 60   # 슬라이더 정수 단위 (퍼센트). 60% 미만은 가독성 저하로 차단.
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
        self._assets_hidden: bool = False
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

        # ── 상단: 포트폴리오 요약 ────────────────────────────────────────
        self.summary = PortfolioSummary(self.card)
        root.addWidget(self.summary)

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
        ch.setContentsMargins(14, 4, 14, 4)
        ch.setSpacing(8)

        opacity_caption = QLabel("투명도")
        opacity_caption.setFont(QFont(_FONT_FAMILY, 10))
        opacity_caption.setStyleSheet(f"color: {C['subtext']}; font-size: 10px;")
        ch.addWidget(opacity_caption)

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
        ch.addWidget(self.opacity_slider, 1)

        root.addWidget(controls_row)

        # ── 하단: 액션 바 ────────────────────────────────────────────────
        action_row = QWidget(self.card)
        action_row.setStyleSheet("background: transparent;")
        action_row.setFixedHeight(44)
        hl = QHBoxLayout(action_row)
        hl.setContentsMargins(8, 6, 8, 6)
        hl.setSpacing(4)

        def make_btn(label: str, tooltip: str, slot, *,
                     color: str | None = None,
                     weight: str = "normal",
                     size: int = 16,
                     padding: str = "6px 10px") -> QPushButton:
            b = QPushButton(label)
            b.setToolTip(tooltip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {color or C['text']};
                    border: none;
                    border-radius: 6px;
                    padding: {padding};
                    font-size: {size}px;
                    font-weight: {weight};
                }}
                QPushButton:hover {{ background: {C['surface']}; }}
            """)
            b.clicked.connect(slot)
            return b

        # "+"는 텍스트 글리프라 emoji 색이 안 먹는다 → 초록색 강제로 "추가" 의미 강조
        # padding 비대칭(top<bottom)으로 수학 축에 걸친 "+"의 시각 중심을 위로 보정
        hl.addWidget(make_btn("+", "종목 추가", self.add_stock_requested.emit,
                              color=C['green'], weight="bold", size=28,
                              padding="4px 10px 8px 10px"))
        hl.addWidget(make_btn("📋", "종목 관리",   self.manage_stocks_requested.emit))
        hl.addWidget(make_btn("📤", "Excel 내보내기", self.export_requested.emit))
        hl.addWidget(make_btn("📥", "Excel 가져오기", self.import_requested.emit))

        hl.addStretch()

        self.toggle_assets_btn = make_btn("👁", "자산 정보 숨기기", self._on_toggle_assets)
        hl.addWidget(self.toggle_assets_btn)

        hl.addWidget(make_btn("❌", "종료", self.quit_requested.emit))

        root.addWidget(action_row)

    # ── 데이터 동기화 ────────────────────────────────────────────────────
    def set_stocks(self, stocks: list[dict]):
        """종목 리스트로 행 재구성. 기존 행 모두 폐기."""
        # 기존 행 제거
        for row in self.rows.values():
            self.rows_layout.removeWidget(row)
            row.deleteLater()
        self.rows.clear()

        if not stocks:
            self.empty_lbl.show()
            return
        self.empty_lbl.hide()

        # 새 행 추가 (insertWidget 으로 stretch 앞에 삽입)
        for i, s in enumerate(stocks):
            if s.get("hidden", False):
                continue
            row = StockRow(s)
            row.assets_hidden = self._assets_hidden
            row.edit_requested.connect(self.edit_requested.emit)
            row.delete_requested.connect(self.delete_requested.emit)
            self.rows[s["code"]] = row
            self.rows_layout.insertWidget(i, row)

    def update_summary(self, total_invest: int, total_eval: int):
        if total_invest == 0 and total_eval == 0:
            self.summary.clear_metrics()
        else:
            self.summary.update_metrics(total_invest, total_eval)

    def update_stock_price(self, code: str, result: dict):
        row = self.rows.get(code)
        if row:
            row.apply_price(result)

    def update_stock_minute(self, code: str, prices: list, open_price: float):
        row = self.rows.get(code)
        if row:
            row.apply_minute(prices, open_price)

    def update_stock_daily(self, code: str, candles: list):
        row = self.rows.get(code)
        if row:
            row.apply_daily(candles)

    # ── 위치/표시 ────────────────────────────────────────────────────────
    def _calc_content_height(self) -> int:
        """현재 종목 수/확장 상태에 맞춘 컨텐츠 영역 높이 계산.
        스크롤이 필요한 경우 MAX_H 안에서 잘리고 스크롤바가 뜬다."""
        if self.rows:
            rows_h = sum(
                (r.EXPAND_H if r.is_expanded else r.COMPACT_H)
                for r in self.rows.values()
            )
        else:
            rows_h = 120   # empty_lbl 안내 영역

        # PortfolioSummary + 구분선 2개 + 종목 영역 + 설정 바 + 액션 바
        # + 카드 위/아래 outer margin (각각 OUTER_M)
        return (
            PortfolioSummary.H + 1 + rows_h + 1
            + self.CONTROLS_H + self.ACTION_H
            + self.OUTER_M * 2
        )

    # ── 자산 정보 숨김 ────────────────────────────────────────────────────
    def set_assets_hidden(self, hidden: bool):
        """외부(매니저)에서 상태 동기화. 시그널은 emit 하지 않는다."""
        if self._assets_hidden == hidden:
            self._apply_assets_btn_visual()
            return
        self._assets_hidden = hidden
        self.summary.set_assets_hidden(hidden)
        for row in self.rows.values():
            row.set_assets_hidden(hidden)
        self._apply_assets_btn_visual()

    def _on_toggle_assets(self):
        self._assets_hidden = not self._assets_hidden
        self.summary.set_assets_hidden(self._assets_hidden)
        for row in self.rows.values():
            row.set_assets_hidden(self._assets_hidden)
        self._apply_assets_btn_visual()
        self.assets_hidden_changed.emit(self._assets_hidden)

    def _apply_assets_btn_visual(self):
        if self._assets_hidden:
            self.toggle_assets_btn.setText("🙈")
            self.toggle_assets_btn.setToolTip("자산 정보 표시")
        else:
            self.toggle_assets_btn.setText("👁")
            self.toggle_assets_btn.setToolTip("자산 정보 숨기기")

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

    def show_below(self, anchor_global_pos: QPoint, anchor_width: int = 0):
        """anchor_global_pos 아래에 팝오버를 표시. 화면 우상단 메뉴바 아이콘 기준."""
        target_w = self.W + self.OUTER_M * 2
        content_h = self._calc_content_height()
        target_h = max(self.MIN_H, min(content_h, self.MAX_H))

        self.setFixedSize(target_w, target_h)

        # 메뉴바 아이콘 가운데 아래로 떨어뜨림 (Qt 트레이는 geometry 가 비어있는 경우가
        # 있어 anchor 좌표 기준으로 보정). 메뉴바와 살짝 떨어뜨리기 위해 10px 갭.
        x = anchor_global_pos.x() + anchor_width // 2 - target_w // 2
        y = anchor_global_pos.y() + 10

        # 화면 경계 안으로 보정
        screen = QApplication.screenAt(anchor_global_pos) or QApplication.primaryScreen()
        sg = screen.availableGeometry()
        x = max(sg.x() + 4, min(x, sg.x() + sg.width() - target_w - 4))
        y = max(sg.y() + 4, y)

        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

    # ── 키보드 ────────────────────────────────────────────────────────────
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)
