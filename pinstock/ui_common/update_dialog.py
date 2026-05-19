"""앱 내 자동 업데이트 다이얼로그.

다이얼로그를 열면 백그라운드 스레드로 GitHub Releases API 를 조회하여
상태(state) 를 바꿔가며 동일 모달 안에서 흐름을 진행한다:

    CHECKING → (UP_TO_DATE | UPDATE_AVAILABLE) → DOWNLOADING → ...
                                                              → 헬퍼 실행 + 앱 종료
                                                              ↓
                                                          (ERROR)
"""

import threading
import webbrowser
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QObject, pyqtSignal, QTimer
from PyQt6.QtWidgets import (
    QDialog, QLabel, QVBoxLayout, QHBoxLayout, QPushButton,
    QProgressBar, QTextEdit, QDialogButtonBox, QApplication,
)

from ..__version__ import __version__
from ..core import updater
from ..ui_windows.theme import C, DIALOG_STYLE


# ─── 백그라운드 통신 신호 (메인 스레드로 안전하게 넘기기) ─────────────────
class _Signals(QObject):
    release_fetched = pyqtSignal(object)            # ReleaseInfo | None
    download_progress = pyqtSignal(int, int)         # done, total
    download_done = pyqtSignal(bool, object)         # success, dest Path


# ─── 상태 ──────────────────────────────────────────────────────────────────
_S_CHECKING = "checking"
_S_UP_TO_DATE = "up_to_date"
_S_UPDATE_AVAILABLE = "update_available"
_S_DOWNLOADING = "downloading"
_S_ERROR = "error"


class UpdateDialog(QDialog):
    """업데이트 확인 + 다운로드 + 적용을 한 모달에서 처리."""

    def __init__(
        self,
        parent=None,
        on_release_seen: Optional[Callable[[updater.ReleaseInfo], None]] = None,
    ):
        """on_release_seen: API 조회 성공 시 호출되는 콜백. manager 가 last_check_at /
        cached_release 를 갱신하여 트레이 뱃지/throttle 을 업데이트할 때 사용."""
        super().__init__(parent)
        self.setWindowTitle("업데이트 확인")
        self.setMinimumWidth(460)
        self.setStyleSheet(DIALOG_STYLE)

        self._signals = _Signals()
        self._signals.release_fetched.connect(self._on_release_fetched)
        self._signals.download_progress.connect(self._on_download_progress)
        self._signals.download_done.connect(self._on_download_done)

        self._on_release_seen = on_release_seen
        self._release: Optional[updater.ReleaseInfo] = None
        self._cancel_event = threading.Event()
        self._worker: Optional[threading.Thread] = None

        self._build_ui()
        self._set_state(_S_CHECKING)

        # 다이얼로그가 열리는 순간 백그라운드 조회 시작
        QTimer.singleShot(0, self._start_check)

    # ── UI 구성 ────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 16)
        root.setSpacing(10)

        self.status_label = QLabel("최신 버전 확인 중...")
        self.status_label.setStyleSheet(
            f"color: {C['text']}; font-size: 14px; font-weight: bold;"
        )
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self.version_label = QLabel()
        self.version_label.setStyleSheet(f"color: {C['subtext']}; font-size: 11px;")
        root.addWidget(self.version_label)

        # 릴리즈 노트
        self.notes_view = QTextEdit()
        self.notes_view.setReadOnly(True)
        self.notes_view.setMinimumHeight(180)
        self.notes_view.setStyleSheet(
            f"QTextEdit {{ background: {C['bg2']}; color: {C['text']}; "
            f"border: 1px solid {C['border']}; border-radius: 7px; padding: 8px; "
            f"font-size: 11px; }}"
        )
        root.addWidget(self.notes_view)

        # 진행률 바
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)   # 처음엔 indeterminate
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet(
            f"QProgressBar {{ background: {C['surface']}; color: {C['text']}; "
            f"border: none; border-radius: 6px; padding: 1px; height: 16px; "
            f"text-align: center; font-size: 11px; }}"
            f"QProgressBar::chunk {{ background: {C['blue']}; border-radius: 5px; }}"
        )
        root.addWidget(self.progress_bar)

        # 버튼들 (상태에 따라 visibility 토글)
        self.btn_row = QHBoxLayout()
        self.btn_row.setSpacing(8)
        self.btn_row.addStretch()

        self.btn_release_page = QPushButton("릴리즈 페이지 열기")
        self.btn_release_page.setProperty("flat", "true")
        self.btn_release_page.clicked.connect(self._open_release_page)

        self.btn_update_now = QPushButton("지금 업데이트")
        self.btn_update_now.clicked.connect(self._start_download)

        self.btn_close = QPushButton("닫기")
        self.btn_close.setProperty("flat", "true")
        self.btn_close.clicked.connect(self.reject)

        self.btn_cancel_dl = QPushButton("취소")
        self.btn_cancel_dl.setProperty("flat", "true")
        self.btn_cancel_dl.clicked.connect(self._cancel_download)

        for b in (self.btn_release_page, self.btn_update_now,
                  self.btn_cancel_dl, self.btn_close):
            self.btn_row.addWidget(b)

        root.addLayout(self.btn_row)

    # ── 상태 전환 ──────────────────────────────────────────────────────────
    def _set_state(self, state: str):
        self._state = state
        # 공통: 일단 모두 숨기고 상태별로 켠다
        self.notes_view.hide()
        self.progress_bar.hide()
        for b in (self.btn_release_page, self.btn_update_now,
                  self.btn_cancel_dl, self.btn_close):
            b.hide()

        if state == _S_CHECKING:
            self.status_label.setText("최신 버전 확인 중...")
            self.version_label.setText(f"현재 버전: {__version__}")
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setFormat("")
            self.progress_bar.show()
            self.btn_close.show()

        elif state == _S_UP_TO_DATE:
            self.status_label.setText("최신 버전을 사용 중입니다.")
            self.version_label.setText(f"현재 버전: {__version__}")
            self.btn_close.show()

        elif state == _S_UPDATE_AVAILABLE:
            assert self._release is not None
            self.status_label.setText(f"새 버전 {self._release.tag} 가 있습니다.")
            self.version_label.setText(
                f"현재 버전: {__version__}    →    최신 버전: {self._release.version}"
            )
            self.notes_view.setPlainText(self._release.body or "(릴리즈 노트 없음)")
            self.notes_view.show()
            # 개발 빌드면 자동 업데이트 비활성, 페이지 열기만
            if updater.can_self_update():
                self.btn_update_now.show()
            else:
                self.btn_release_page.show()
            self.btn_close.show()
            self.btn_close.setText("나중에")

        elif state == _S_DOWNLOADING:
            assert self._release is not None
            self.status_label.setText("다운로드 중...")
            mb = self._release.asset_size / (1024 * 1024)
            self.version_label.setText(
                f"{self._release.asset_name}  ({mb:.1f} MB)"
            )
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("0%")
            self.progress_bar.show()
            self.btn_cancel_dl.show()

        elif state == _S_ERROR:
            # 메시지는 호출측에서 status_label 에 직접 설정
            self.btn_close.show()

    # ── 비동기 흐름 ────────────────────────────────────────────────────────
    def _start_check(self):
        def worker():
            rel = updater.fetch_latest_release()
            self._signals.release_fetched.emit(rel)
        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _on_release_fetched(self, release: Optional[updater.ReleaseInfo]):
        if release is None:
            self._show_error("최신 버전 정보를 가져오지 못했습니다. 네트워크 상태를 확인해주세요.")
            return
        self._release = release
        # manager 의 캐시/throttle 갱신
        if self._on_release_seen is not None:
            try:
                self._on_release_seen(release)
            except Exception as e:
                print(f"[update_dialog] on_release_seen 콜백 오류: {e}")
        if updater.is_newer(__version__, release.version):
            self._set_state(_S_UPDATE_AVAILABLE)
        else:
            self._set_state(_S_UP_TO_DATE)

    def _start_download(self):
        assert self._release is not None
        if not updater.can_self_update():
            # 안전망 — 버튼이 표시되지 않아야 하지만 혹시 모를 경로
            self._open_release_page()
            return
        self._set_state(_S_DOWNLOADING)
        self._cancel_event.clear()

        release = self._release

        def worker():
            dest = updater.download_path_for(release)
            ok = updater.download_zip(
                release.asset_url,
                dest,
                on_progress=lambda d, t: self._signals.download_progress.emit(d, t),
                cancel_check=self._cancel_event.is_set,
            )
            self._signals.download_done.emit(ok, dest)

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _on_download_progress(self, done: int, total: int):
        if total <= 0:
            return
        pct = int(done * 100 / total)
        self.progress_bar.setValue(pct)
        mb_done = done / (1024 * 1024)
        mb_total = total / (1024 * 1024)
        self.progress_bar.setFormat(f"{pct}%  ({mb_done:.1f} / {mb_total:.1f} MB)")

    def _on_download_done(self, success: bool, dest: Path):
        if self._cancel_event.is_set():
            # 사용자가 닫음 / 취소 → 다이얼로그 자체가 이미 닫혔거나 닫는 중
            return
        if not success:
            self._show_error("다운로드에 실패했습니다. 네트워크 상태를 확인하고 다시 시도해주세요.")
            return
        # 다운로드 완료 → 헬퍼 실행 + 즉시 앱 종료
        self.status_label.setText("재시작 중...")
        self.progress_bar.setRange(0, 0)   # indeterminate
        self.progress_bar.setFormat("")
        self.btn_cancel_dl.hide()
        QApplication.processEvents()
        try:
            updater.launch_updater(updater.current_install_dir(), Path(dest))
        except Exception as e:
            self._show_error(f"업데이트 실행에 실패했습니다: {e}")
            return
        # 모달 다이얼로그의 exec() 를 먼저 종료해야 nested event loop 가 풀린다.
        # app.quit() 만 호출하면 외부 loop 만 종료 예약되고 modal 은 그대로 살아있어
        # 프로세스가 끝나지 않음 → 헬퍼가 PID wait timeout 으로 떨어지는 원인.
        self.accept()
        QTimer.singleShot(0, QApplication.instance().quit)

    def _cancel_download(self):
        self._cancel_event.set()
        self.reject()

    # ── 보조 ──────────────────────────────────────────────────────────────
    def _open_release_page(self):
        if self._release and self._release.html_url:
            webbrowser.open(self._release.html_url)
        self.accept()

    def _show_error(self, message: str):
        self._set_state(_S_ERROR)
        self.status_label.setText(message)
        self.version_label.setText("")

    # ── 닫힘 시 진행 중인 다운로드 안전하게 종료 ──────────────────────────
    def closeEvent(self, event):
        self._cancel_event.set()
        super().closeEvent(event)

    def reject(self):
        self._cancel_event.set()
        super().reject()
