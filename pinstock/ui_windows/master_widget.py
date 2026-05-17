"""포트폴리오 요약 마스터 위젯."""

import sys

from PyQt6.QtWidgets import (
    QWidget, QFrame, QLabel, QHBoxLayout, QVBoxLayout, QGridLayout, QApplication,
    QSlider, QStyle, QStyleOptionSlider,
)
from PyQt6.QtCore import Qt, QPoint, QRectF, pyqtSignal
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor

from .theme import C


# ─── Win11 DWM 자동 테두리/그림자/둥근 모서리 차단 ───────────────────────────
def _disable_win11_dwm_chrome(hwnd: int) -> None:
    """Windows 가 top-level 윈도우에 기본 적용하는
    그림자(드롭 섀도) · 얇은 테두리 · 둥근 모서리 효과를 끈다."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        # 1. 윈도우 클래스의 CS_DROPSHADOW 비트 제거 → 시스템 드롭 섀도 차단.
        #    (이 클래스의 다른 Qt 윈도우들도 함께 그림자 빠짐 — 일반적으로 무난)
        GCL_STYLE = -26
        CS_DROPSHADOW = 0x00020000
        user32 = ctypes.windll.user32
        try:
            get_long = user32.GetClassLongPtrW
            set_long = user32.SetClassLongPtrW
        except AttributeError:
            get_long = user32.GetClassLongW
            set_long = user32.SetClassLongW
        get_long.restype = ctypes.c_size_t
        set_long.restype = ctypes.c_size_t
        cur = get_long(hwnd, GCL_STYLE)
        if cur & CS_DROPSHADOW:
            set_long(hwnd, GCL_STYLE, cur & ~CS_DROPSHADOW)

        # 2. DWM NC 렌더링 정책 비활성화 — 일부 환경에서 추가 그림자/테두리 차단.
        # DWMWA_NCRENDERING_POLICY = 2, DWMNCRP_DISABLED = 1
        v = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 2, ctypes.byref(v), ctypes.sizeof(v)
        )
        # 3. Win11 둥근 모서리 끔
        # DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_DONOTROUND = 1
        v = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(v), ctypes.sizeof(v)
        )
        # 4. Win11 테두리 색 없음
        # DWMWA_BORDER_COLOR = 34, DWMWA_COLOR_NONE = 0xFFFFFFFE
        v = ctypes.c_uint32(0xFFFFFFFE)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 34, ctypes.byref(v), ctypes.sizeof(v)
        )
    except Exception:
        pass


# ─── 잠금 상태에서 핸들이 자물쇠로 변하는 슬라이더 ──────────────────────────
class _OpacitySlider(QSlider):
    """`set_locked(True)` 일 때 기본 동그란 핸들 대신 자물쇠 모양을 그린다.
    슬라이더 자체 상호작용은 그대로 유지 (사용자가 잠금 상태에서도 끌어올릴 수 있음)."""

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self._locked = False
        self._normal_qss = ""
        self._locked_qss = ""

    def set_styles(self, normal_qss: str, locked_qss: str):
        self._normal_qss = normal_qss
        self._locked_qss = locked_qss
        self.setStyleSheet(self._locked_qss if self._locked else self._normal_qss)

    def set_locked(self, locked: bool):
        if self._locked == locked:
            return
        self._locked = locked
        self.setStyleSheet(self._locked_qss if locked else self._normal_qss)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        # 자물쇠는 외부 _LockOverlay (별도 top-level 윈도우) 가 풀 opacity 로 그린다.
        # 슬라이더 자체에서는 그리지 않음 — 마스터 투명도로 흐려진 자물쇠 잔상이
        # 오버레이 자물쇠 가장자리에 옅은 윤곽으로 비쳐 보이는 문제를 막기 위함.

    @staticmethod
    def _draw_lock(painter: QPainter, rect):
        # 자물쇠 = shackle(U자, 윗부분) + body(둥근 사각형, 아랫부분).
        # 자물쇠 전체 세로 중심이 rect.center 와 일치하도록 body_y 를 보정.
        s = 14.0
        cx = rect.center().x()
        cy = rect.center().y()

        body_w = s * 0.78
        body_h = s * 0.48
        shackle_w = body_w * 0.62
        shackle_h = s * 0.45

        # 전체 세로 길이 = body_h + shackle_h*0.7. 그 중심이 cy 가 되도록.
        body_y = cy - (body_h - shackle_h * 0.7) / 2
        body_x = cx - body_w / 2
        shackle_x = cx - shackle_w / 2
        shackle_y = body_y - shackle_h * 0.7

        # 잠금(클릭 통과 모드) 표시는 빨간색 — 위젯이 흐려져도 한눈에 보이도록.
        lock_color = QColor(C['red'])
        painter.setBrush(QBrush(lock_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(body_x, body_y, body_w, body_h), 1.5, 1.5)

        pen = QPen(lock_color)
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(QRectF(shackle_x, shackle_y, shackle_w, shackle_h), 0, 180 * 16)


# ─── 잠금 모드 자물쇠 오버레이 ────────────────────────────────────────────────
class _LockOverlay(QWidget):
    """잠금 모드에서 슬라이더 핸들 위치에 떠 있는 빨간 자물쇠 오버레이.
    별도 top-level 윈도우라 마스터의 setWindowOpacity 영향을 받지 않아 위젯이
    반투명해져도 자물쇠는 항상 선명히 보인다.
    - 배경: `WA_TranslucentBackground` 로 완전 투명, 자물쇠 픽셀만 보임.
    - Win11 DWM 의 자동 테두리/둥근 모서리는 `_disable_win11_dwm_chrome` 으로 차단.
    - `WindowTransparentForInput` 이라 오버레이 위 클릭은 그대로 슬라이더에 떨어짐."""

    # 홀수 사이즈 — 픽셀 격자에 정확히 한가운데가 존재해야 자물쇠가 좌상단으로 안 밀림.
    # (짝수면 QRect.center() = (size//2 - 1, ...) 식으로 1px 어긋남)
    SIZE = 17

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFixedSize(self.SIZE, self.SIZE)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        _OpacitySlider._draw_lock(painter, self.rect())

    def showEvent(self, event):
        super().showEvent(event)
        # hide → show 사이에 Windows 가 per-window DWM 속성을 기본값으로 되돌리는
        # 경우가 있어서 (그림자/테두리 부활) 매 show 마다 다시 적용. 호출 비용은 매우 가벼움.
        _disable_win11_dwm_chrome(int(self.winId()))


# ─── 투명도 슬라이더 전용 윈도우 ──────────────────────────────────────────────
class _SliderWindow(QFrame):
    """투명도 슬라이더만 담는 별도 top-level 윈도우.
    마스터에 WindowTransparentForInput 가 켜져도 슬라이더는 별개 윈도우라
    그대로 클릭/조작 가능. setWindowOpacity 도 호출 안 해 항상 100% 불투명.
    배경 완전 투명 + DWM 그림자/테두리 차단."""

    WIDTH          = 90
    HEIGHT         = 20   # 풋터 높이와 동일 — 풋터 안에 자연스럽게 겹침
    PADDING_TOP    = 2
    PADDING_BOTTOM = 6

    def __init__(self, opacity_min: int, opacity_max: int):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setStyleSheet("background: transparent; border: none;")
        self.setFixedSize(self.WIDTH, self.HEIGHT)

        hl = QHBoxLayout(self)
        hl.setContentsMargins(0, self.PADDING_TOP, 0, self.PADDING_BOTTOM)
        hl.setSpacing(0)

        self.opacity_slider = _OpacitySlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(opacity_min, opacity_max)
        self.opacity_slider.setValue(opacity_max)
        self.opacity_slider.setFixedWidth(self.WIDTH)
        self.opacity_slider.setToolTip("위젯 투명도")
        self.opacity_slider.setCursor(Qt.CursorShape.PointingHandCursor)

        # 슬라이더 자체 배경도 투명 — 뒤로 마스터 카드(faded) 가 자연스럽게 비치게.
        groove = (
            "QSlider { background: transparent; }\n"
            + MasterWidget._GROOVE_QSS.format(**C)
        )
        self.opacity_slider.set_styles(
            normal_qss=groove + MasterWidget._NORMAL_HANDLE_QSS.format(**C),
            locked_qss=groove + MasterWidget._LOCKED_HANDLE_QSS.format(**C),
        )
        hl.addWidget(self.opacity_slider)

    def showEvent(self, event):
        super().showEvent(event)
        _disable_win11_dwm_chrome(int(self.winId()))


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
    # 이 값(퍼센트) 이하면 종목 위젯 + 마스터 카드가 클릭 통과 모드로 들어가고
    # 슬라이더 핸들이 빨간 자물쇠 아이콘으로 바뀜.
    LOCK_THRESHOLD = 50

    SLIDER_RIGHT_MARGIN = 14   # 카드 우측 여백과 동일 (슬라이더 윈도우 우측 정렬용)

    opacity_changed = pyqtSignal(float)   # 0.1 ~ 1.0

    def __init__(self, width: int):
        super().__init__()
        # 가장 긴 종목명 기준 통일 폭과 동일하게 맞춤
        self.W = width
        self._drag_pos  = None
        self._press_pos = None
        self._moved     = False
        self._drag_locked: bool = False   # True 면 본체 드래그로 위치 이동 불가 (잠금 모드)
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

        # 우측 하단 투명도 슬라이더 풋터 — 슬라이더 자체는 별도 top-level 윈도우로 분리.
        # (마스터가 click-through 상태여도 슬라이더는 그대로 조작 가능하도록.)
        self.footer = QWidget(self.card)
        self.footer.setGeometry(0, self.GRID_H, self.W, self.FOOTER_H)
        self.footer.setStyleSheet("background: transparent;")

        # 확장 패널 (클릭 시 종목별 손익 표시) — 초기 숨김
        self.expand_panel = QWidget(self.card)
        self.expand_panel.setStyleSheet("background: transparent;")
        self.expand_panel.hide()

        # 투명도 슬라이더 (별도 top-level, 마스터 click-through 영향 안 받음).
        self.slider_window = _SliderWindow(self.OPACITY_MIN, self.OPACITY_MAX)
        self.opacity_slider = self.slider_window.opacity_slider
        self.opacity_slider.valueChanged.connect(self._on_opacity_slider_changed)

        # 잠금 표시 자물쇠 오버레이 (별도 top-level 윈도우, 항상 100% 불투명).
        self.lock_overlay = _LockOverlay()

    # ── 투명도 슬라이더 (우측 하단) ───────────────────────────────────────
    _GROOVE_QSS = """
        QSlider::groove:horizontal {{
            height: 3px;
            background: {surface2};
            border-radius: 1px;
        }}
        QSlider::sub-page:horizontal {{
            background: {subtext};
            border-radius: 1px;
        }}
    """
    _NORMAL_HANDLE_QSS = """
        QSlider::handle:horizontal {{
            width: 10px;
            height: 10px;
            margin: -4px 0;
            background: {text};
            border-radius: 5px;
        }}
    """
    # 잠금 모드에서는 기본 핸들을 투명하게 숨기고, paintEvent 가 자물쇠를 그린다.
    _LOCKED_HANDLE_QSS = """
        QSlider::handle:horizontal {{
            width: 14px;
            height: 14px;
            margin: -6px 0;
            background: transparent;
            border: none;
        }}
    """

    def set_opacity(self, value: float):
        """외부(매니저)에서 초기값 동기화. 시그널은 emit 하지 않는다.
        창 자체의 투명도는 매니저가 일괄 적용한다."""
        pct = max(self.OPACITY_MIN, min(self.OPACITY_MAX, int(round(value * 100))))
        self.opacity_slider.blockSignals(True)
        self.opacity_slider.setValue(pct)
        self.opacity_slider.blockSignals(False)
        self._sync_lock_visual(pct)

    def _on_opacity_slider_changed(self, pct: int):
        self._sync_lock_visual(pct)
        opacity = pct / 100.0
        self.opacity_changed.emit(opacity)

    def _sync_lock_visual(self, pct: int):
        locked = pct <= self.LOCK_THRESHOLD
        self.opacity_slider.set_locked(locked)
        # 잠금 모드면 매니저가 마스터/종목 위젯을 click-through 로 토글한다.
        # _drag_locked 는 디바운스 직전(아직 플래그 적용 전) 짧은 사이의 방어.
        self._drag_locked = locked
        self._sync_lock_overlay()

    def _sync_lock_overlay(self):
        """잠금 오버레이 위치/표시 동기화. 잠금 + master 가 보이는 상태일 때만 노출."""
        if not hasattr(self, "lock_overlay"):
            return
        if not self._drag_locked or not self.isVisible():
            self.lock_overlay.hide()
            return
        opt = QStyleOptionSlider()
        self.opacity_slider.initStyleOption(opt)
        handle_rect = self.opacity_slider.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, opt,
            QStyle.SubControl.SC_SliderHandle, self.opacity_slider,
        )
        g = self.opacity_slider.mapToGlobal(handle_rect.center())
        sz = _LockOverlay.SIZE
        self.lock_overlay.move(g.x() - sz // 2, g.y() - sz // 2)
        self.lock_overlay.show()
        self.lock_overlay.raise_()

    def _sync_slider_window_pos(self):
        """슬라이더 윈도우를 풋터 우측 끝에 정렬."""
        if not hasattr(self, "slider_window"):
            return
        x = self.x() + self.W - self.SLIDER_RIGHT_MARGIN - _SliderWindow.WIDTH
        y = self.y() + self.GRID_H
        self.slider_window.move(x, y)

    # ── 마스터 이동/표시 변화에 맞춰 슬라이더 윈도우 + 자물쇠 오버레이 따라가기 ──
    def moveEvent(self, event):
        super().moveEvent(event)
        self._sync_slider_window_pos()
        self._sync_lock_overlay()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_slider_window_pos()
        self._sync_lock_overlay()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_slider_window_pos()
        if hasattr(self, "slider_window"):
            self.slider_window.show()
            self.slider_window.raise_()
        self._sync_lock_overlay()

    def hideEvent(self, event):
        super().hideEvent(event)
        if hasattr(self, "slider_window"):
            self.slider_window.hide()
        if hasattr(self, "lock_overlay"):
            self.lock_overlay.hide()

    def closeEvent(self, event):
        if hasattr(self, "slider_window"):
            self.slider_window.close()
        if hasattr(self, "lock_overlay"):
            self.lock_overlay.close()
        super().closeEvent(event)

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
        # 폭이 바뀌면 슬라이더 윈도우의 우측 정렬도 재계산
        self._sync_slider_window_pos()

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
            # 잠금 모드면 _moved 만 표시(릴리즈 시 토글 발화 차단)하고 실제 이동은 건너뜀
            if self._moved and not self._drag_locked:
                self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        # 드래그가 아니고 잠금 모드도 아닐 때만 클릭 → 종목 목록 토글.
        # 잠금 모드면 마스터 본체는 어떤 클릭에도 반응하지 않음 (슬라이더는 별개 위젯이라 영향 없음).
        if (event.button() == Qt.MouseButton.LeftButton
                and not self._moved
                and not self._drag_locked):
            self.toggle_expand()
        self._drag_pos  = None
        self._press_pos = None
        self._moved     = False
