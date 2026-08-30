"""제목 표시줄 없는 다이얼로그 뼈대.

QDialog 는 최상위 창이라 OS 가 제목 표시줄을 얹어 준다 — macOS 는 트래픽 라이트가
달린 검은 띠다. Pinstock 의 다른 창(메모·팝오버·종목 위젯)은 이미 프레임리스라,
관리 창들만 그 띠를 달고 있으면 같은 앱의 창처럼 보이지 않는다.

제목줄을 떼면 OS 가 대신 해 주던 세 가지를 직접 채워야 한다.

  1. 배경과 둥근 모서리 — 창 배경을 투명으로 두고 QFrame 카드를 그린다
  2. 창 이동 — 자식 위젯이 가져가지 않은 빈 곳을 끌면 창이 따라온다
  3. 크기 조절 — 오른쪽 아래 QSizeGrip (고정 크기 창은 붙이지 않는다)

닫기 버튼은 각 창의 '취소'와 Esc 가 대신한다. 내용은 self.card 안에 담는다 —
서브클래스는 QVBoxLayout(self) 가 아니라 QVBoxLayout(self.card) 로 시작한다.
"""

from PyQt6.QtWidgets import QDialog, QFrame, QVBoxLayout, QSizeGrip
from PyQt6.QtGui import QPainter, QPen, QColor
from PyQt6.QtCore import Qt, QPoint

from ..ui_windows.theme import C

CARD_RADIUS = 14

# 보이는 창 본체. 창 자체는 투명이라 이 카드가 배경·테두리·모서리를 전부 그린다.
FRAMELESS_CARD_STYLE = f"""
QFrame#framelessCard {{
    background: {C['bg']};
    border: 1px solid {C['surface2']};
    border-radius: {CARD_RADIUS}px;
}}
"""

_GRIP = 16        # 크기 조절 그립 한 변
_GRIP_INSET = 24  # 카드 모서리에서 그립까지 (둥근 모서리 안쪽에 놓이게)


class _CornerGrip(QSizeGrip):
    """오른쪽 아래 크기 조절 손잡이.

    macOS 의 QSizeGrip 은 아무것도 그리지 않는다 — 동작은 하지만 보이지 않으니
    사용자 입장에서는 없는 것과 같다. 그래서 대각선 세 줄을 직접 찍는다.
    크기 조절 동작 자체는 QSizeGrip 이 하던 그대로 쓴다.
    """

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(C['surface2']), 2, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        w, h = self.width(), self.height()
        for off in (4, 9, 14):
            p.drawLine(w - off, h - 2, w - 2, h - off)


class FramelessDialog(QDialog):
    """제목 표시줄 없이 둥근 카드로 뜨는 다이얼로그.

    resizable=False 는 setFixedSize 를 쓰는 창용 — 늘릴 수 없는 창에 그립을
    달아 두면 잡히지도 않는 손잡이만 보인다.

    stay_on_top 은 기본이 False 다. 종목 위젯이 늘 위에 떠 있어 켜 두고 싶지만,
    이 창이 또 다른 창(종목 추가·색상 선택 …)을 띄우면 그 자식이 부모 뒤로 숨어
    앱이 멈춘 것처럼 보인다. 자식 창을 띄우지 않는 창만 켠다.
    """

    def __init__(self, parent=None, *, resizable: bool = True,
                 stay_on_top: bool = False):
        super().__init__(parent)
        self._drag_offset: QPoint | None = None

        flags = Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
        if stay_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.card = QFrame(self)
        self.card.setObjectName("framelessCard")
        outer.addWidget(self.card)

        # 그립은 카드의 자식이라 카드 위에 그려진다 (레이아웃 밖 = 겹쳐 배치)
        self._size_grip = None
        if resizable:
            self._size_grip = _CornerGrip(self.card)
            self._size_grip.setFixedSize(_GRIP, _GRIP)
            self._size_grip.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._size_grip is not None:
            self._size_grip.move(self.width() - _GRIP_INSET,
                                 self.height() - _GRIP_INSET)

    # ── 창 이동 ───────────────────────────────────────────────────────────
    # 표·버튼·입력칸은 자기 마우스 이벤트를 스스로 처리하므로 여기까지 오지 않는다.
    # 즉 '빈 곳을 끌면 움직인다' 가 된다 — 표 안의 행 드래그와 부딪히지 않는다.
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)
