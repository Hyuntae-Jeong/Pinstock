"""포트폴리오 요약 마스터 위젯."""

from PyQt6.QtWidgets import (
    QWidget, QFrame, QLabel, QHBoxLayout, QVBoxLayout, QGridLayout, QApplication,
    QSlider,
)
from PyQt6.QtCore import Qt, QPoint, pyqtSignal

from .theme import C


# ─── 포트폴리오 요약 마스터 위젯 ─────────────────────────────────────────────
class MasterWidget(QWidget):
    """포트폴리오 전체 요약을 표시하는 마스터 위젯.
    총 매입금액 / 평가금액 / 평가손익 / 수익률 4개 지표를 2×2 그리드로 표시.
    개별 종목 위젯과 동일한 다크 카드 스타일이며 드래그로 이동 가능.
    우측 하단에 전체 위젯 투명도를 조절하는 슬라이더."""

    GRID_H   = 96    # 2×2 요약 그리드 영역 높이
    FOOTER_H = 20    # 우측 하단 투명도 슬라이더 영역 높이
    H        = GRID_H + FOOTER_H   # compact 카드 전체 높이
    RADIUS   = 13
    DRAG_THRESHOLD = 4

    # 투명도 슬라이더 범위 (퍼센트). Windows 는 macOS(60–100) 보다 넓게 10–100.
    OPACITY_MIN = 10
    OPACITY_MAX = 100

    opacity_changed = pyqtSignal(float)   # 0.6 ~ 1.0

    def __init__(self, width: int):
        super().__init__()
        # 가장 긴 종목명 기준 통일 폭과 동일하게 맞춤
        self.W = width
        self._drag_pos  = None
        self._press_pos = None
        self._moved     = False
        self.is_expanded: bool = False
        self.holdings: list[dict] = []   # [{"name", "profit", "profit_rate"}, ...]

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(self.W, self.H)

        self.card = QFrame(self)
        self.card.setObjectName("master_card")
        self.card.setGeometry(0, 0, self.W, self.H)
        self.card.setStyleSheet(f"""
            QFrame#master_card {{
                background: {C['bg']};
                border: 1px solid {C['border']};
                border-radius: {self.RADIUS}px;
            }}
        """)

        # 상단 compact: 2x2 그리드 (제목 없음, 1행/2행 사이를 살짝 띄움)
        self.compact = QWidget(self.card)
        self.compact.setGeometry(0, 0, self.W, self.GRID_H)
        self.compact.setStyleSheet("background: transparent;")
        grid = QGridLayout(self.compact)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(10)

        self.invest_val = self._make_cell(grid, 0, 0, "총 매입금액")
        self.eval_val   = self._make_cell(grid, 0, 1, "평가금액")
        self.profit_val = self._make_cell(grid, 1, 0, "평가손익", bold=True)
        self.prate_val  = self._make_cell(grid, 1, 1, "수익률",   bold=True)

        # 우측 하단 투명도 슬라이더 푸터
        self.footer = QWidget(self.card)
        self.footer.setGeometry(0, self.GRID_H, self.W, self.FOOTER_H)
        self.footer.setStyleSheet("background: transparent;")
        self._build_opacity_slider(self.footer)

        # 확장 패널 (클릭 시 종목별 손익 표시) — 초기 숨김
        self.expand_panel = QWidget(self.card)
        self.expand_panel.setStyleSheet("background: transparent;")
        self.expand_panel.hide()

    # ── 투명도 슬라이더 (우측 하단) ───────────────────────────────────────
    def _build_opacity_slider(self, parent: QWidget):
        hl = QHBoxLayout(parent)
        hl.setContentsMargins(14, 2, 14, 6)
        hl.setSpacing(6)
        hl.addStretch(1)

        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(self.OPACITY_MIN, self.OPACITY_MAX)
        self.opacity_slider.setValue(self.OPACITY_MAX)
        self.opacity_slider.setFixedWidth(90)
        self.opacity_slider.setToolTip("위젯 투명도")
        self.opacity_slider.setCursor(Qt.CursorShape.PointingHandCursor)
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
        hl.addWidget(self.opacity_slider, 0)

    def set_opacity(self, value: float):
        """외부(매니저)에서 초기값 동기화. 시그널은 emit 하지 않는다.
        창 자체의 투명도는 매니저가 일괄 적용한다."""
        pct = max(self.OPACITY_MIN, min(self.OPACITY_MAX, int(round(value * 100))))
        self.opacity_slider.blockSignals(True)
        self.opacity_slider.setValue(pct)
        self.opacity_slider.blockSignals(False)

    def _on_opacity_slider_changed(self, pct: int):
        opacity = pct / 100.0
        self.opacity_changed.emit(opacity)

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

    # ── 외부에서 너비 변경 (개별 위젯 통일 폭에 맞춰 갱신) ───────────────
    def set_uniform_width(self, base_w: int):
        if base_w == self.W:
            return
        self.W = base_w
        self.setFixedWidth(base_w)
        cur_h = self.height()
        self.card.setGeometry(0, 0, base_w, cur_h)
        self.compact.setGeometry(0, 0, base_w, self.GRID_H)
        self.footer.setGeometry(0, self.GRID_H, base_w, self.FOOTER_H)
        if self.is_expanded:
            panel_h = cur_h - self.H
            self.expand_panel.setGeometry(0, self.H, base_w, panel_h)

    # ── 지표 갱신 ────────────────────────────────────────────────────────
    def update_metrics(self, total_invest: int, total_eval: int):
        profit = total_eval - total_invest
        prate  = (profit / total_invest * 100.0) if total_invest else 0.0

        # 한국 시장 컨벤션과 일관: 이익=빨강, 손실=파랑
        if profit > 0:
            color = C['red']
            sign  = "+"
        elif profit < 0:
            color = C['blue']
            sign  = ""   # 음수면 자체적으로 '-' 가 붙음
        else:
            color = C['subtext']
            sign  = ""

        self.invest_val.setText(f"{total_invest:,} 원")
        self.eval_val.setText(f"{total_eval:,} 원")
        self.profit_val.setText(f"{sign}{profit:,} 원")
        self.profit_val.setStyleSheet(
            f"color: {color}; font-size: 13px; font-weight: bold;"
        )
        self.prate_val.setText(f"{sign}{prate:.2f}%")
        self.prate_val.setStyleSheet(
            f"color: {color}; font-size: 13px; font-weight: bold;"
        )

    def clear_metrics(self):
        """종목이 하나도 없을 때 0/빈 표시로 초기화."""
        self.invest_val.setText("0 원")
        self.eval_val.setText("0 원")
        self.profit_val.setText("─")
        self.profit_val.setStyleSheet(
            f"color: {C['subtext']}; font-size: 13px; font-weight: bold;"
        )
        self.prate_val.setText("─")
        self.prate_val.setStyleSheet(
            f"color: {C['subtext']}; font-size: 13px; font-weight: bold;"
        )
        self.holdings = []
        if self.is_expanded:
            self.collapse()

    # ── 보유 종목 목록 표시 ──────────────────────────────────────────────
    ROW_H        = 20    # 종목 1행 높이 (폰트 11 + 약간의 여유)
    ROW_SPACING  = 4
    PANEL_TOP    = 6
    PANEL_BOTTOM = 10

    def update_holdings(self, holdings: list[dict]):
        """holdings: [{"name": str, "profit": int, "profit_rate": float}, ...]
        펼친 상태면 즉시 다시 그리고 카드 높이도 재조정."""
        self.holdings = holdings
        if self.is_expanded:
            self._render_holdings()
            self._resize_to_expanded()

    def _calc_panel_height(self) -> int:
        n = len(self.holdings)
        if n == 0:
            return 0
        # 구분선(1px) + top/bottom padding + N행 + (N-1) row spacing
        return (
            self.PANEL_TOP + 1 + self.PANEL_TOP
            + n * self.ROW_H + max(0, n - 1) * self.ROW_SPACING
            + self.PANEL_BOTTOM
        )

    def _resize_to_expanded(self):
        panel_h = self._calc_panel_height()
        total_h = self.H + panel_h
        self.setFixedHeight(total_h)
        self.card.setGeometry(0, 0, self.W, total_h)
        self.expand_panel.setGeometry(0, self.H, self.W, panel_h)

    def _render_holdings(self):
        """expand_panel 안에 종목별 행 다시 그림 (기존 layout 폐기 후 재구성)."""
        # 기존 layout 정리 (dummy QWidget로 양도 → GC)
        old = self.expand_panel.layout()
        if old is not None:
            QWidget().setLayout(old)

        vl = QVBoxLayout(self.expand_panel)
        vl.setContentsMargins(14, self.PANEL_TOP, 14, self.PANEL_BOTTOM)
        vl.setSpacing(self.ROW_SPACING)

        # 상단 구분선
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background: {C['border']}; max-height: 1px; border: none;")
        vl.addWidget(sep)

        for h in self.holdings:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)

            name_lbl = QLabel(h["name"])
            name_lbl.setStyleSheet(f"color: {C['text']}; font-size: 11px;")
            row.addWidget(name_lbl, 1)

            profit = int(h["profit"])
            rate   = float(h["profit_rate"])
            if profit > 0:
                color, sign = C['red'], "+"
            elif profit < 0:
                color, sign = C['blue'], ""   # 음수는 자체 '-' 사용
            else:
                color, sign = C['subtext'], ""

            profit_lbl = QLabel(f"{sign}{profit:,} 원")
            profit_lbl.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")
            profit_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            profit_lbl.setFixedWidth(100)
            row.addWidget(profit_lbl)

            rate_lbl = QLabel(f"{sign}{rate:.2f}%")
            rate_lbl.setStyleSheet(f"color: {color}; font-size: 11px;")
            rate_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            rate_lbl.setFixedWidth(60)
            row.addWidget(rate_lbl)

            vl.addLayout(row)

    # ── 확장 / 축소 토글 ─────────────────────────────────────────────────
    def toggle_expand(self):
        if self.is_expanded:
            self.collapse()
        else:
            self.expand()

    def expand(self):
        if self.is_expanded or not self.holdings:
            return
        self.is_expanded = True
        self._render_holdings()
        self._resize_to_expanded()
        self.expand_panel.show()
        self._ensure_on_screen()   # 확장 후 화면 밖이면 위로 이동

    def collapse(self):
        if not self.is_expanded:
            return
        self.is_expanded = False
        self.expand_panel.hide()
        self.setFixedHeight(self.H)
        self.card.setGeometry(0, 0, self.W, self.H)
        self._restore_pre_expand_pos()

    SCREEN_MARGIN = 10

    def _ensure_on_screen(self):
        """확장 후 화면 하단을 넘어가면 위젯을 위로 이동."""
        x = self.x()
        y = self.y()
        h = self.height()   # 확장 후 실제 높이

        screen = QApplication.screenAt(QPoint(x, y))
        if screen is None:
            screen = QApplication.primaryScreen()
        geo = screen.availableGeometry()

        bottom = y + h
        max_y  = geo.y() + geo.height() - self.SCREEN_MARGIN
        if bottom <= max_y:
            return
        new_y = max_y - h
        new_y = max(geo.y() + self.SCREEN_MARGIN, new_y)
        self._pre_expand_y = y
        self.move(x, new_y)
        self.raise_()

    def _restore_pre_expand_pos(self):
        if getattr(self, "_pre_expand_y", None) is not None:
            self.move(self.x(), self._pre_expand_y)
            self._pre_expand_y = None

    # ── 드래그 이동 + 클릭 토글 (StockWidget 와 동일 패턴) ────────────────
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
        # 드래그가 아니었으면 = 클릭 → 종목 목록 토글
        if event.button() == Qt.MouseButton.LeftButton and not self._moved:
            self.toggle_expand()
        self._drag_pos  = None
        self._press_pos = None
        self._moved     = False
