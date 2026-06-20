"""투자 메모장 다이얼로그.

투자와 관련된 내용을 자유롭게 적어두는 단일 메모 공간이다. 메모는 앱 전체에서
1개만 존재하며 stocks.json 의 memo 키에 보존된다.

- 프레임리스 + 모드리스 + 항상 위: 군더더기 없이 메모 칸만 둥근 카드로 띄운다.
  위젯/팝오버를 보면서 열어둔 채로 메모할 수 있다. 제목 표시줄이 없으므로 상단
  영역(X 가 있는 줄)을 드래그해 창을 옮긴다.
- 자동 저장: 입력이 멈추면 잠시 뒤 자동 저장하고, 창을 닫을 때도 한 번 더 저장한다.

저장 위치/시점 관리(stocks.json 기록)는 호출측(매니저)이 on_save 콜백으로 담당한다 —
다이얼로그는 디스크를 직접 건드리지 않는다 (About 다이얼로그가 updater 를 직접
호출하지 않는 것과 같은 구조).
"""

from typing import Callable, Optional

from PyQt6.QtCore import Qt, QTimer, QPoint
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFrame, QPushButton, QPlainTextEdit,
)

from ..ui_windows.theme import C


class MemoDialog(QDialog):
    """단일 투자 메모를 편집하는 프레임리스 모드리스 항상-위 창."""

    AUTOSAVE_DELAY_MS = 1000   # 입력이 멈춘 뒤 이 시간이 지나면 자동 저장 (디바운스)
    RADIUS = 12

    def __init__(
        self,
        initial_text: str = "",
        on_save: Optional[Callable[[str], None]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._on_save = on_save
        self._last_saved_text = initial_text or ""
        self._drag_offset: Optional[QPoint] = None   # 프레임리스 창 드래그 이동용

        self.setWindowTitle("Pinstock 메모")
        # 프레임리스 + 모드리스 + 항상 위. 둥근 카드를 보여주려고 창 배경을 투명 처리
        # (팝오버와 동일 방식). 제목 표시줄이 없어 상단을 드래그해 창을 옮긴다.
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(360, 440)
        self.setMinimumSize(240, 200)

        # 입력이 멈춘 뒤 자동 저장하는 디바운스 타이머
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(self._save)

        self._build_ui(initial_text or "")

    def _build_ui(self, initial_text: str):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # 둥근 카드 = 보이는 창 본체이자 메모칸의 테두리.
        self.card = QFrame(self)
        self.card.setObjectName("memo_card")
        self.card.setStyleSheet(f"""
            QFrame#memo_card {{
                background: {C['bg2']};
                border: 1px solid {C['border']};
                border-radius: {self.RADIUS}px;
            }}
        """)
        outer.addWidget(self.card)

        root = QVBoxLayout(self.card)
        root.setContentsMargins(12, 8, 12, 12)
        root.setSpacing(6)

        # 상단: 오른쪽 X 닫기 버튼 (배경 없음, 글리프만). 이 줄이 드래그 이동 손잡이.
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.addStretch(1)
        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedSize(26, 26)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setToolTip("닫기")
        self.btn_close.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                color: {C['subtext']};
                font-size: 17px;
                padding: 0;
            }}
            QPushButton:hover {{ color: {C['text']}; }}
        """)
        self.btn_close.clicked.connect(self.close)
        top_row.addWidget(self.btn_close)
        root.addLayout(top_row)

        # 메모 입력칸 — 카드가 테두리를 제공하므로 자체 테두리 없이 투명 배경.
        self.editor = QPlainTextEdit()
        self.editor.setPlainText(initial_text)
        self.editor.setPlaceholderText(
            "투자 관련 메모를 자유롭게 적어두세요.\n자동으로 저장됩니다."
        )
        self.editor.setFrameShape(QFrame.Shape.NoFrame)
        self.editor.setStyleSheet(f"""
            QPlainTextEdit {{
                background: transparent;
                color: {C['text']};
                border: none;
                font-size: 14px;
                selection-background-color: {C['blue']};
            }}
        """)
        self.editor.textChanged.connect(self._on_text_changed)
        root.addWidget(self.editor, 1)

    # ── 자동 저장 ─────────────────────────────────────────────────────────
    def _on_text_changed(self):
        # 입력이 멈출 때까지 저장을 미룬다 (타이머 재시작 = 디바운스)
        self._autosave_timer.start(self.AUTOSAVE_DELAY_MS)

    def _save(self):
        self._autosave_timer.stop()
        if self._on_save is None:
            return
        text = self.editor.toPlainText()
        if text == self._last_saved_text:
            return   # 변경 없음 — 불필요한 디스크 쓰기 방지
        self._last_saved_text = text
        self._on_save(text)

    # ── 창 이동 (프레임리스라 직접 드래그) ─────────────────────────────────
    # 입력칸/X 버튼이 차지하지 않은 상단 영역을 누르면 이 핸들러가 받는다.
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

    # ── 키 / 닫기 ─────────────────────────────────────────────────────────
    def keyPressEvent(self, event):
        # ESC 도 close() 경로로 통일해 닫을 때 저장이 보장되도록 한다
        # (QDialog 기본 reject 는 closeEvent 를 거치지 않을 수 있음).
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        # 닫을 때 마지막으로 한 번 더 저장 (디바운스 대기 중인 변경 포함)
        self._save()
        super().closeEvent(event)
