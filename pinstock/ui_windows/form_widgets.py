"""다이얼로그용 폼 위젯: AutoSelect 라인에디트/스핀박스, ArrowSpinBox, ToggleSwitch."""

from PyQt6.QtWidgets import (
    QWidget, QLineEdit, QSpinBox, QDoubleSpinBox, QStyle, QStyleOptionSpinBox,
)
from PyQt6.QtCore import Qt, QTimer, QPoint, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QBrush, QPolygon

from .theme import C


# ─── 포커스 진입 시 자동 전체선택 ────────────────────────────────────────────
class _SelectAllOnFocus:
    """Mixin: 사용자가 마우스/탭으로 포커스를 주면 내용을 자동 전체 선택.
    selectAll() 메서드가 있는 위젯(QLineEdit·QSpinBox 등)과 혼합해 사용.

    focusInEvent 직후 Qt 내부에서 selection이 해제될 수 있어
    QTimer.singleShot(0, ...)으로 다음 이벤트 루프 tick에 호출한다.

    단, 자동완성(QCompleter) 팝업이 닫히며 되돌아오는 포커스
    (PopupFocusReason·ActiveWindowFocusReason 등)에서는 전체 선택하지 않는다.
    검색 드롭다운이 갱신될 때마다 입력 중이던 텍스트가 통째로 선택돼,
    이어 누른 글자에 덮어써지는 문제가 있기 때문이다."""

    # 사용자가 '직접' 진입한 것으로 볼 포커스 사유만 전체 선택 대상
    _SELECT_ALL_REASONS = (
        Qt.FocusReason.MouseFocusReason,
        Qt.FocusReason.TabFocusReason,
        Qt.FocusReason.BacktabFocusReason,
        Qt.FocusReason.ShortcutFocusReason,
    )

    def focusInEvent(self, event):
        super().focusInEvent(event)
        if event.reason() in self._SELECT_ALL_REASONS:
            QTimer.singleShot(0, self.selectAll)


class AutoSelectLineEdit(_SelectAllOnFocus, QLineEdit):
    pass


class SearchLineEdit(AutoSelectLineEdit):
    """IME 조합 중(preedit)인 글자까지 추적하는 검색용 라인에디트.

    한글처럼 IME 로 입력하는 글자는 마지막 음절이 '조합 중'(preedit) 상태로
    남아 text()/textEdited 에 잡히지 않는다. 예) '삼성전자'를 입력하면 마지막
    '자'가 조합 중일 때 text()는 '삼성전'만 돌려준다. 검색어에는 이 글자까지
    포함해야 하므로 preedit 를 따로 들고 있다가 composedText()로 합쳐서 주고,
    확정 텍스트든 조합 중 텍스트든 바뀔 때마다 textComposed 시그널을 쏜다."""

    textComposed = pyqtSignal()
    # 검색창에서 Enter 를 눌렀을 때(자동완성 팝업이 닫힌 상태) 방출.
    # 다이얼로그 기본 버튼(확인) 대신 '즉시 검색'으로 연결하는 용도.
    searchRequested = pyqtSignal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._preedit: str = ""
        # 확정 텍스트 변경(영문·붙여넣기·백스페이스·음절 확정)도 동일하게 알림
        self.textEdited.connect(self._on_text_edited)

    def _on_text_edited(self, _text: str = ""):
        self.textComposed.emit()

    def inputMethodEvent(self, event):
        super().inputMethodEvent(event)
        preedit = event.preeditString()
        if preedit != self._preedit:
            self._preedit = preedit
            self.textComposed.emit()

    def composedText(self) -> str:
        """확정 텍스트에 조합 중인 글자를 합친, 화면에 보이는 전체 문자열."""
        text = self.text()
        if not self._preedit:
            return text
        pos = self.cursorPosition()
        return text[:pos] + self._preedit + text[pos:]

    def setText(self, text: str):
        # 프로그램이 값을 직접 넣으면 조합 상태도 초기화한다
        self._preedit = ""
        super().setText(text)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        # QLineEdit 은 포커스를 받을 때마다 QCompleter 의 두 시그널을 내부 슬롯에
        # 다시 연결한다:
        #   activated(str)  → setText(str)               (엔터·클릭 확정)
        #   highlighted(str)→ _q_completionHighlighted(str) → setText(str)  (방향키 탐색)
        # 방향키로 드롭다운을 훑기만 해도 두 번째 연결 때문에 입력창이 강조된
        # 종목코드로 즉시 바뀌어 버린다(+UnfilteredPopup 모드에선 첫 방향키가
        # 현재 항목을 강제 선택). 엔터·클릭 전까지는 입력값을 유지하도록
        # highlighted→setText 연결만 끊는다. activated(확정) 연결은 그대로 둔다.
        completer = self.completer()
        if completer is not None:
            try:
                completer.highlighted[str].disconnect()
            except (TypeError, RuntimeError):
                # 연결이 아직(혹은 이미) 없으면 disconnect 가 예외를 던진다 — 무시
                pass

    def keyPressEvent(self, event):
        # Enter 처리는 자동완성 팝업이 떠 있는지로 갈린다:
        #   · 팝업이 떠 있으면 → 기본 동작(QLineEdit+QCompleter)에 맡겨, 방향키로
        #     고른 항목이 그대로 선택되게 한다.
        #   · 팝업이 닫혀 있으면 → 다이얼로그 기본 버튼(확인)으로 흘려보내 곧장
        #     추가되는 걸 막고, 검색 요청 신호만 쏜 뒤 이벤트를 소비한다.
        # 읽기전용(편집 모드)일 땐 손대지 않아 Enter 로 그대로 확정되게 둔다.
        if (not self.isReadOnly()
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)):
            completer = self.completer()
            popup = completer.popup() if completer is not None else None
            if popup is None or not popup.isVisible():
                self.searchRequested.emit()
                event.accept()
                return
        super().keyPressEvent(event)


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


class ArrowDoubleSpinBox(AutoSelectDoubleSpinBox):
    """소수 입력용 스핀박스에 ▲▼ 화살표를 직접 그린다."""

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.buttonSymbols() == QSpinBox.ButtonSymbols.NoButtons:
            return

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

        cx, cy = up_rect.center().x(), up_rect.center().y()
        painter.drawPolygon(QPolygon([
            QPoint(cx,     cy - 3),
            QPoint(cx - 4, cy + 2),
            QPoint(cx + 4, cy + 2),
        ]))

        cx, cy = down_rect.center().x(), down_rect.center().y()
        painter.drawPolygon(QPolygon([
            QPoint(cx - 4, cy - 2),
            QPoint(cx + 4, cy - 2),
            QPoint(cx,     cy + 3),
        ]))
        painter.end()


class QuantitySpinBox(ArrowDoubleSpinBox):
    """수량 입력용 스핀박스. 값이 정수면 '1', 소수면 '1.5'처럼
    trailing zero 없이 표시한다. 입력은 setDecimals 자릿수까지 허용."""

    def textFromValue(self, value: float) -> str:
        text = f"{value:.{self.decimals()}f}"
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"


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
