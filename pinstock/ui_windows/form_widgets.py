"""다이얼로그용 폼 위젯: AutoSelect 라인에디트/스핀박스, ArrowSpinBox, ToggleSwitch."""

from PyQt6.QtWidgets import (
    QWidget, QLineEdit, QSpinBox, QDoubleSpinBox, QStyle, QStyleOptionSpinBox,
)
from PyQt6.QtCore import Qt, QTimer, QPoint, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QBrush, QPolygon

from .theme import C


# ─── 포커스 진입 시 자동 전체선택 ────────────────────────────────────────────
class _SelectAllOnFocus:
    """Mixin: 포커스가 들어오면 내용을 자동으로 전체 선택.
    selectAll() 메서드가 있는 위젯(QLineEdit·QSpinBox 등)과 혼합해 사용.

    focusInEvent 직후 Qt 내부에서 selection이 해제될 수 있어
    QTimer.singleShot(0, ...)으로 다음 이벤트 루프 tick에 호출한다."""

    def focusInEvent(self, event):
        super().focusInEvent(event)
        QTimer.singleShot(0, self.selectAll)


class AutoSelectLineEdit(_SelectAllOnFocus, QLineEdit):
    pass


class AutoSelectSpinBox(_SelectAllOnFocus, QSpinBox):
    pass


class AutoSelectDoubleSpinBox(_SelectAllOnFocus, QDoubleSpinBox):
    pass


# ─── 화살표를 직접 그리는 QSpinBox ───────────────────────────────────────────
class ArrowSpinBox(AutoSelectSpinBox):
    """다크 stylesheet 환경에서 ▲▼ 화살표를 paintEvent로 직접 그림.
    PyQt6의 ::up-arrow / ::down-arrow가 CSS triangle·inline SVG 모두
    안 먹는 이슈를 회피한다. 포커스 시 자동 전체선택은 부모(AutoSelectSpinBox)
    에서 처리."""

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.buttonSymbols() == QSpinBox.ButtonSymbols.NoButtons:
            return

        # 정확한 up/down 버튼 영역 얻기
        opt = QStyleOptionSpinBox()
        self.initStyleOption(opt)
        style = self.style()
        up_rect = style.subControlRect(
            QStyle.ComplexControl.CC_SpinBox, opt,
            QStyle.SubControl.SC_SpinBoxUp, self)
        down_rect = style.subControlRect(
            QStyle.ComplexControl.CC_SpinBox, opt,
            QStyle.SubControl.SC_SpinBoxDown, self)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(QColor(C['text'])))
        painter.setPen(Qt.PenStyle.NoPen)

        # 위 ▲
        cx, cy = up_rect.center().x(), up_rect.center().y()
        painter.drawPolygon(QPolygon([
            QPoint(cx,     cy - 3),
            QPoint(cx - 4, cy + 2),
            QPoint(cx + 4, cy + 2),
        ]))
        # 아래 ▼
        cx, cy = down_rect.center().x(), down_rect.center().y()
        painter.drawPolygon(QPolygon([
            QPoint(cx - 4, cy - 2),
            QPoint(cx + 4, cy - 2),
            QPoint(cx,     cy + 3),
        ]))
        painter.end()


# ─── ON/OFF 슬라이딩 토글 스위치 ─────────────────────────────────────────────
class ToggleSwitch(QWidget):
    """슬라이딩 토글 스위치. ON=녹색 트랙, OFF=회색 트랙. 핸들은 흰 원."""

    toggled = pyqtSignal(bool)

    W = 36
    H = 18

    def __init__(self, checked: bool = True, parent=None):
        super().__init__(parent)
        self._checked = bool(checked)
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # focus 받을 때 그려지는 native outline 차단
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, value: bool):
        value = bool(value)
        if self._checked != value:
            self._checked = value
            self.update()
            self.toggled.emit(value)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        # 트랙 (둥근 막대) — ON: 파랑 / OFF: 어두운 회색
        track = QColor(C['blue']) if self._checked else QColor(C['surface2'])
        painter.setBrush(QBrush(track))
        painter.drawRoundedRect(0, 0, self.W, self.H, self.H / 2, self.H / 2)

        # 핸들 (흰 원)
        handle_size = self.H - 4
        margin = 2
        hx = self.W - handle_size - margin if self._checked else margin
        painter.setBrush(QBrush(QColor("white")))
        painter.drawEllipse(hx, margin, handle_size, handle_size)

        painter.end()
