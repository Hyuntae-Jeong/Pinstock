"""투자 메모장 다이얼로그.

투자와 관련된 내용을 자유롭게 적어두는 단일 메모 공간이다. 메모는 앱 전체에서
1개만 존재하며 stocks.json 의 memo 키에 보존된다.

- 프레임리스 + 모드리스 + 항상 위: 군더더기 없이 메모 칸만 둥근 카드로 띄운다.
  위젯/팝오버를 보면서 열어둔 채로 메모할 수 있다. 제목 표시줄이 없으므로 상단
  영역(X 가 있는 줄)을 드래그해 창을 옮기고, 우하단 그립으로 크기를 조절한다.
- 투명도: 위젯 공용 투명도(슬라이더 값)를 그대로 적용받는다.
- 기억: 마지막 위치/크기를 저장해 다음에 열 때 복원한다.
- 자동 저장: 텍스트·위치·크기가 바뀌면 잠시 뒤 자동 저장, 창을 닫을 때도 저장한다.

텍스트/기하 변경은 on_change(text, geometry) 콜백으로 호출측(매니저)에 전달하며,
저장 위치(stocks.json 기록)는 매니저가 담당한다 — 다이얼로그는 디스크를 직접
건드리지 않는다 (About 다이얼로그가 updater 를 직접 호출하지 않는 것과 같은 구조).
"""

from typing import Callable, Optional

from PyQt6.QtCore import Qt, QTimer, QPoint, QRect
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QPushButton, QPlainTextEdit,
    QSizeGrip, QApplication,
)

from ..ui_windows.theme import C


class MemoDialog(QDialog):
    """단일 투자 메모를 편집하는 프레임리스 모드리스 항상-위 창."""

    PERSIST_DELAY_MS = 1000   # 텍스트/기하 변경 후 이 시간이 지나면 저장 (디바운스)
    RADIUS = 12
    DEFAULT_W = 360
    DEFAULT_H = 440

    def __init__(
        self,
        initial_text: str = "",
        initial_geometry: Optional[list] = None,
        opacity: float = 1.0,
        on_change: Optional[Callable[[str, list], None]] = None,
        title: str = "",
        deleted: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._on_change = on_change
        self._title = title or ""                  # 종목 메모면 종목명, 전역 메모면 빈 문자열
        self._title_deleted = deleted             # 삭제된 종목의 메모면 제목을 빨간색으로
        self._ready = False                       # 초기 기하 적용 중 발생하는 이벤트 무시용
        self._drag_offset: Optional[QPoint] = None  # 프레임리스 창 드래그 이동용

        self.setWindowTitle(self._title or "Pinstock 메모")
        # 프레임리스 + 모드리스 + 항상 위. 둥근 카드를 보여주려고 창 배경을 투명 처리
        # (팝오버와 동일 방식). 제목 표시줄이 없어 상단을 드래그해 창을 옮긴다.
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(240, 200)
        self.setWindowOpacity(self._clamp_opacity(opacity))

        self._persist_timer = QTimer(self)
        self._persist_timer.setSingleShot(True)
        self._persist_timer.timeout.connect(self._persist)

        self._build_ui(initial_text or "")
        self._apply_initial_geometry(initial_geometry)

        # 초기 텍스트/기하를 '마지막 저장값'으로 잡아 첫 표시 직후 불필요한 저장 방지
        self._last_text = self.editor.toPlainText()
        self._last_geom = self._current_geometry()
        self._ready = True

    # ── 초기 기하 / 투명도 ────────────────────────────────────────────────
    @staticmethod
    def _clamp_opacity(opacity: float) -> float:
        try:
            return max(0.1, min(1.0, float(opacity)))
        except (TypeError, ValueError):
            return 1.0

    def set_opacity(self, opacity: float):
        """슬라이더 변경 시 매니저가 호출 — 창 투명도를 실시간 반영."""
        self.setWindowOpacity(self._clamp_opacity(opacity))

    def _apply_initial_geometry(self, geom: Optional[list]):
        if (
            isinstance(geom, (list, tuple)) and len(geom) == 4
            and self._geometry_on_screen(geom)
        ):
            x, y, w, h = (int(v) for v in geom)
            self.resize(max(w, self.minimumWidth()), max(h, self.minimumHeight()))
            self.move(x, y)
            return
        # 저장된 위치가 없거나 화면 밖이면 기본 크기 + 주 화면 중앙
        self.resize(self.DEFAULT_W, self.DEFAULT_H)
        screen = QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            self.move(
                avail.x() + (avail.width() - self.DEFAULT_W) // 2,
                avail.y() + (avail.height() - self.DEFAULT_H) // 2,
            )

    @staticmethod
    def _geometry_on_screen(geom) -> bool:
        """저장된 사각형이 어느 화면과도 겹치지 않으면(=화면 밖) False — 모니터 구성이
        바뀌어 창이 보이지 않는 곳에 복원되는 것을 막는다."""
        try:
            x, y, w, h = (int(v) for v in geom)
        except (TypeError, ValueError):
            return False
        rect = QRect(x, y, max(1, w), max(1, h))
        return any(s.availableGeometry().intersects(rect) for s in QApplication.screens())

    # ── UI ────────────────────────────────────────────────────────────────
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

        # 상단: (종목 메모면) 왼쪽에 종목명 + 오른쪽 X 닫기 버튼. 이 줄이 드래그 이동 손잡이.
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        if self._title:
            title_lbl = QLabel(self._title)
            title_lbl.setStyleSheet(
                f"color: {C['red'] if self._title_deleted else C['subtext']}; "
                f"font-size: 12px; font-weight: bold; padding-left: 2px;"
            )
            top_row.addWidget(title_lbl)
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
            "이 종목 메모를 적어두세요.\n자동으로 저장됩니다." if self._title
            else "투자 관련 메모를 자유롭게 적어두세요.\n자동으로 저장됩니다."
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
        self.editor.textChanged.connect(self._schedule_persist)
        root.addWidget(self.editor, 1)

        # 우하단 크기 조절 그립 (프레임리스라 직접 제공). 카드 모서리에 겹쳐 배치한다.
        self.size_grip = QSizeGrip(self.card)
        self.size_grip.setFixedSize(16, 16)
        self.size_grip.raise_()

    # ── 저장(텍스트 + 위치/크기) 디바운스 ─────────────────────────────────
    def _schedule_persist(self):
        self._persist_timer.start(self.PERSIST_DELAY_MS)

    def _current_geometry(self) -> list:
        return [self.x(), self.y(), self.width(), self.height()]

    def _persist(self):
        self._persist_timer.stop()
        if self._on_change is None:
            return
        text = self.editor.toPlainText()
        geom = self._current_geometry()
        if text == self._last_text and geom == self._last_geom:
            return   # 변경 없음 — 불필요한 디스크 쓰기 방지
        self._last_text = text
        self._last_geom = geom
        self._on_change(text, geom)

    # ── 창 이동 / 크기 (프레임리스) ───────────────────────────────────────
    def mousePressEvent(self, event):
        # 입력칸/X 버튼/그립이 차지하지 않은 상단 영역을 누르면 창 드래그 이동.
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

    def moveEvent(self, event):
        super().moveEvent(event)
        if self._ready:
            self._schedule_persist()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 카드 우하단 모서리에 그립 재배치 (카드는 마진 0 으로 창을 꽉 채움 → 창 좌표와 동일)
        if hasattr(self, "size_grip"):
            self.size_grip.move(self.width() - 20, self.height() - 20)
        if self._ready:
            self._schedule_persist()

    # ── 키 / 닫기 ─────────────────────────────────────────────────────────
    def keyPressEvent(self, event):
        # ESC 도 close() 경로로 통일해 닫을 때 저장이 보장되도록 한다
        # (QDialog 기본 reject 는 closeEvent 를 거치지 않을 수 있음).
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        # 표시 직후 창 활성화 + 입력칸 포커스를 다음 이벤트 루프 틱에 수행한다.
        # 우클릭 컨텍스트 메뉴에서 열면 메뉴가 닫히는 도중 show 돼 즉시 activateWindow
        # 가 먹지 않으므로(메뉴가 키 포커스를 들고 있음), 메뉴가 완전히 닫힌 뒤로
        # 미뤄야 마우스로 한 번 더 클릭하지 않아도 바로 타이핑할 수 있다.
        QTimer.singleShot(0, self._focus_editor)

    def _focus_editor(self):
        self.raise_()
        self.activateWindow()
        self.editor.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        # 커서를 기존 메모 맨 끝으로 — 이어서 바로 입력할 수 있게.
        self.editor.moveCursor(QTextCursor.MoveOperation.End)

    def closeEvent(self, event):
        # 닫을 때 마지막으로 한 번 더 저장 (디바운스 대기 중인 변경 포함)
        self._persist()
        super().closeEvent(event)
