"""투자 메모장 다이얼로그.

투자와 관련된 내용을 자유롭게 적어두는 단일 메모 공간이다. 메모는 앱 전체에서
1개만 존재하며 stocks.json 의 memo 키에 보존된다.

- 모드리스 + 항상 위: 위젯/팝오버를 보면서 열어둔 채로 메모할 수 있다.
- 자동 저장: 입력이 멈추면 잠시 뒤 자동 저장하고, 창을 닫을 때도 한 번 더 저장한다.

저장 위치/시점 관리(stocks.json 기록)는 호출측(매니저)이 on_save 콜백으로 담당한다 —
다이얼로그는 디스크를 직접 건드리지 않는다 (About 다이얼로그가 updater 를 직접
호출하지 않는 것과 같은 구조).
"""

from typing import Callable, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QPlainTextEdit,
)

from ..ui_windows.theme import C, DIALOG_STYLE


class MemoDialog(QDialog):
    """단일 투자 메모를 편집하는 모드리스 항상-위 창."""

    AUTOSAVE_DELAY_MS = 1000   # 입력이 멈춘 뒤 이 시간이 지나면 자동 저장 (디바운스)

    def __init__(
        self,
        initial_text: str = "",
        on_save: Optional[Callable[[str], None]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._on_save = on_save
        self._last_saved_text = initial_text or ""

        self.setWindowTitle("Pinstock 메모")
        # 모드리스 + 항상 위 — 위젯/팝오버 옆에 띄워두고 보면서 메모.
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.resize(360, 440)
        self.setMinimumSize(260, 220)
        self.setStyleSheet(DIALOG_STYLE)

        # 입력이 멈춘 뒤 자동 저장하는 디바운스 타이머
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(self._save)

        self._build_ui(initial_text or "")

    def _build_ui(self, initial_text: str):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(10)

        self.editor = QPlainTextEdit()
        self.editor.setPlainText(initial_text)
        self.editor.setPlaceholderText(
            "투자 관련 메모를 자유롭게 적어두세요.\n자동으로 저장됩니다."
        )
        self.editor.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {C['bg2']};
                color: {C['text']};
                border: 1px solid {C['border']};
                border-radius: 8px;
                padding: 10px;
                font-size: 14px;
                selection-background-color: {C['blue']};
            }}
        """)
        self.editor.textChanged.connect(self._on_text_changed)
        root.addWidget(self.editor, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_close = QPushButton("닫기")
        self.btn_close.setProperty("flat", "true")
        self.btn_close.clicked.connect(self.close)
        btn_row.addWidget(self.btn_close)
        root.addLayout(btn_row)

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

    def closeEvent(self, event):
        # 닫을 때 마지막으로 한 번 더 저장 (디바운스 대기 중인 변경 포함)
        self._save()
        super().closeEvent(event)
