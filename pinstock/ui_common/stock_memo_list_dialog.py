"""종목별 메모 모아보기 다이얼로그.

메모(텍스트)가 있는 보유 종목을 한 창에서 카드 리스트로 보여준다. 각 카드는
종목명(제목) + 수정일, 그 아래 메모 첫 줄 미리보기로 구성된다. 카드를 클릭하면
on_select(code) 콜백으로 호출측(매니저)에 알리고, 매니저는 해당 종목의 기존
메모창(MemoDialog)을 띄운다 — 이 다이얼로그는 편집/저장을 직접 하지 않는다.

스타일은 메모장(MemoDialog)과 같은 프레임리스·모드리스·항상-위 둥근 카드를
따른다. 내용은 set_entries 로 갱신하므로 매니저가 인스턴스를 재사용할 수 있다.
"""

from datetime import datetime
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QPoint, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QPushButton,
    QScrollArea, QWidget, QApplication,
)

from ..ui_windows.theme import C


def _format_date(iso: Optional[str]) -> str:
    """ISO 수정시각 → 'M월 D일'(올해) / 'YYYY.MM.DD'(다른 해). 없으면 빈 문자열."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return ""
    if dt.year == datetime.now().year:
        return f"{dt.month}월 {dt.day}일"
    return f"{dt.year}.{dt.month:02d}.{dt.day:02d}"


def _first_line(text: str, limit: int = 34) -> str:
    """메모 본문의 첫 비어있지 않은 줄을 미리보기로. 너무 길면 말줄임."""
    for line in str(text or "").splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= limit else line[:limit].rstrip() + "…"
    return ""


class _MemoCard(QFrame):
    """클릭 가능한 메모 요약 카드 — 종목명 + 수정일 + 첫 줄 미리보기."""

    clicked = pyqtSignal(str)   # code

    def __init__(self, code: str, name: str, preview: str, date_text: str, parent=None):
        super().__init__(parent)
        self._code = code
        self.setObjectName("memo_summary_card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(f"""
            QFrame#memo_summary_card {{
                background: {C['surface']};
                border: none;
                border-radius: 9px;
            }}
            QFrame#memo_summary_card:hover {{ background: {C['surface2']}; }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(11, 9, 11, 9)
        root.setSpacing(3)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        name_lbl = QLabel(name or code)
        name_lbl.setStyleSheet(f"color: {C['text']}; font-size: 13px; font-weight: bold; background: transparent;")
        top.addWidget(name_lbl)
        top.addStretch(1)
        if date_text:
            date_lbl = QLabel(date_text)
            date_lbl.setStyleSheet(f"color: {C['subtext']}; font-size: 11px; background: transparent;")
            top.addWidget(date_lbl)
        root.addLayout(top)

        prev_lbl = QLabel(preview or "(빈 메모)")
        prev_lbl.setStyleSheet(f"color: {C['subtext']}; font-size: 12px; background: transparent;")
        prev_lbl.setWordWrap(False)
        root.addWidget(prev_lbl)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit(self._code)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class StockMemoListDialog(QDialog):
    """메모가 있는 종목을 카드 리스트로 모아보는 프레임리스 모드리스 항상-위 창."""

    RADIUS = 12
    DEFAULT_W = 360
    DEFAULT_H = 440

    def __init__(
        self,
        entries: Optional[list] = None,
        opacity: float = 1.0,
        on_select: Optional[Callable[[str], None]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._on_select = on_select
        self._drag_offset: Optional[QPoint] = None

        self.setWindowTitle("종목별 메모")
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(260, 220)
        self.resize(self.DEFAULT_W, self.DEFAULT_H)
        self.setWindowOpacity(self._clamp_opacity(opacity))

        self._build_ui()
        self.set_entries(entries or [])
        self._center_on_screen()

    # ── 투명도 ────────────────────────────────────────────────────────────
    @staticmethod
    def _clamp_opacity(opacity: float) -> float:
        try:
            return max(0.1, min(1.0, float(opacity)))
        except (TypeError, ValueError):
            return 1.0

    def set_opacity(self, opacity: float):
        self.setWindowOpacity(self._clamp_opacity(opacity))

    def _center_on_screen(self):
        screen = QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            self.move(
                avail.x() + (avail.width() - self.width()) // 2,
                avail.y() + (avail.height() - self.height()) // 2,
            )

    # ── UI ────────────────────────────────────────────────────────────────
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.card = QFrame(self)
        self.card.setObjectName("memo_list_card")
        self.card.setStyleSheet(f"""
            QFrame#memo_list_card {{
                background: {C['bg2']};
                border: 1px solid {C['border']};
                border-radius: {self.RADIUS}px;
            }}
        """)
        outer.addWidget(self.card)

        root = QVBoxLayout(self.card)
        root.setContentsMargins(12, 8, 12, 12)
        root.setSpacing(8)

        # 상단: 제목 + X 닫기. 이 줄을 드래그해 창을 옮긴다.
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        title_lbl = QLabel("종목별 메모")
        title_lbl.setStyleSheet(
            f"color: {C['subtext']}; font-size: 12px; font-weight: bold; padding-left: 2px;"
        )
        top_row.addWidget(title_lbl)
        top_row.addStretch(1)
        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedSize(26, 26)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setToolTip("닫기")
        self.btn_close.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: none; color: {C['subtext']}; font-size: 17px; padding: 0; }}
            QPushButton:hover {{ color: {C['text']}; }}
        """)
        self.btn_close.clicked.connect(self.close)
        top_row.addWidget(self.btn_close)
        root.addLayout(top_row)

        # 스크롤 영역 — 메모 카드 세로 목록.
        self.scroll = QScrollArea(self.card)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget { background: transparent; }")
        self.scroll.viewport().setStyleSheet("background: transparent;")

        self.list_host = QWidget()
        self.list_host.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(8)
        self.scroll.setWidget(self.list_host)
        root.addWidget(self.scroll, 1)

        # 메모가 하나도 없을 때 안내 (set_entries 에서 표시/숨김).
        self.empty_lbl = QLabel("메모가 있는 종목이 없습니다.\n종목을 우클릭해 메모를 적어보세요.")
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_lbl.setWordWrap(True)
        self.empty_lbl.setStyleSheet(f"color: {C['subtext']}; font-size: 12px; background: transparent;")
        root.addWidget(self.empty_lbl)
        self.empty_lbl.hide()

    # ── 내용 갱신 ───────────────────────────────────────────────────────────
    def set_entries(self, entries: list):
        """카드 목록을 다시 채운다. entries 항목: {code, name, text, updated_at}."""
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if not entries:
            self.scroll.hide()
            self.empty_lbl.show()
            return

        self.empty_lbl.hide()
        self.scroll.show()
        for e in entries:
            card = _MemoCard(
                code=str(e.get("code", "")),
                name=str(e.get("name", "")),
                preview=_first_line(e.get("text", "")),
                date_text=_format_date(e.get("updated_at")),
            )
            card.clicked.connect(self._on_card_clicked)
            self.list_layout.addWidget(card)
        self.list_layout.addStretch(1)

    def _on_card_clicked(self, code: str):
        if self._on_select is not None and code:
            self._on_select(code)

    # ── 창 이동 (프레임리스) ───────────────────────────────────────────────
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

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
