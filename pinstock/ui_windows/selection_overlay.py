"""드래그로 화면 영역을 고르는 오버레이 — 위젯 묶어 옮기기용.

위젯은 각자 top-level 윈도우라 하나의 부모 위에서 고무줄 선택을 할 수가 없다.
그래서 전 모니터를 덮는 투명 창을 잠깐 띄워 마우스를 가로채고, 드래그가 끝나면
그 사각형(전역 좌표)만 알려 준 뒤 사라진다.
"""

from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QRect, QPoint, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPen, QFont

from .theme import C
from .win_chrome import disable_win11_dwm_chrome


class RegionSelectOverlay(QWidget):
    """전 모니터를 덮는 반투명 오버레이. 드래그한 영역을 전역 좌표로 돌려준다."""

    region_selected = pyqtSignal(QRect)   # 확정된 영역 (전역 좌표)
    cancelled = pyqtSignal()

    DIM_ALPHA = 70          # 선택 모드에 들어왔다는 걸 알리는 정도로만 어둡게
    MIN_DRAG = 8            # 이보다 작으면 그냥 클릭으로 보고 취소
    HINT_TOP = 40

    def __init__(self, hint: str = "드래그해서 위젯을 묶으세요    ·    Esc 취소"):
        super().__init__()
        self._hint = hint
        self._origin: QPoint | None = None
        self._current: QPoint | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setGeometry(self._virtual_geometry())

    @staticmethod
    def _virtual_geometry() -> QRect:
        """모든 모니터를 합친 영역 — 멀티 모니터에서도 화면을 가로질러 선택할 수 있게."""
        geo = QRect()
        for screen in QApplication.screens():
            geo = geo.united(screen.geometry())
        return geo

    # ── 표시 ──────────────────────────────────────────────────────────────
    def showEvent(self, event):
        super().showEvent(event)
        disable_win11_dwm_chrome(int(self.winId()))
        # 위젯들도 항상 위 속성이라 z-order 를 명시적으로 끌어올려야 가려지지 않는다
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    # ── 마우스 ────────────────────────────────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            self._finish(None)
            return
        self._origin = event.position().toPoint()
        self._current = self._origin
        self.update()

    def mouseMoveEvent(self, event):
        if self._origin is None:
            return
        self._current = event.position().toPoint()
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._origin is None:
            return
        rect = self._local_rect()
        # 너무 작으면 선택 의도가 아니라 그냥 클릭이다 (실수로 모드에 갇히지 않게)
        if rect.width() < self.MIN_DRAG or rect.height() < self.MIN_DRAG:
            self._finish(None)
            return
        self._finish(rect.translated(self.geometry().topLeft()))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._finish(None)
            return
        super().keyPressEvent(event)

    def _finish(self, rect: QRect | None):
        self.hide()
        if rect is None:
            self.cancelled.emit()
        else:
            self.region_selected.emit(rect)
        self.deleteLater()

    def _local_rect(self) -> QRect:
        if self._origin is None or self._current is None:
            return QRect()
        return QRect(self._origin, self._current).normalized()

    # ── 그리기 ────────────────────────────────────────────────────────────
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(0, 0, 0, self.DIM_ALPHA))

        rect = self._local_rect()
        if not rect.isNull() and rect.width() and rect.height():
            # 선택 영역만 원래 밝기로 되돌려 안에 든 위젯이 그대로 보이게 한다
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            p.fillRect(rect, Qt.GlobalColor.transparent)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            pen = QPen(QColor(C["blue"]))
            pen.setWidth(1)
            p.setPen(pen)
            p.drawRect(rect.adjusted(0, 0, -1, -1))

        self._draw_hint(p)
        p.end()

    def _draw_hint(self, p: QPainter):
        """안내문은 주 모니터 위쪽에 — 십자 커서만으로는 무슨 모드인지 알 수 없다."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.geometry().translated(-self.geometry().topLeft())
        p.setFont(QFont("Malgun Gothic", 10, QFont.Weight.Bold))
        metrics = p.fontMetrics()
        tw = metrics.horizontalAdvance(self._hint)
        th = metrics.height()
        pad_x, pad_y = 16, 8
        box = QRect(
            geo.x() + (geo.width() - tw) // 2 - pad_x,
            geo.y() + self.HINT_TOP,
            tw + pad_x * 2,
            th + pad_y * 2,
        )
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C["bg"]))
        p.drawRoundedRect(box, 8, 8)
        p.setPen(QPen(QColor(C["border"])))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(box, 8, 8)
        p.setPen(QPen(QColor(C["text"])))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, self._hint)
