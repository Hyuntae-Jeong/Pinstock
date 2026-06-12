"""앱 내 자동 업데이트 다이얼로그.

다이얼로그를 열면 상태(state) 를 바꿔가며 동일 모달 안에서 흐름을 진행한다:

    CHECKING → (UP_TO_DATE | UPDATE_AVAILABLE) → DOWNLOADING → ...
                                                              → 헬퍼 실행 + 앱 종료
                                                              ↓
                                                          (ERROR)

릴리즈 노트는 표시하지 않는다(향후 GitHub 접근 경로 차단 대비). UPDATE_AVAILABLE
상태는 현재/최신 버전과 '업데이트' / '이 버전에서는 업데이트를 하지 않음' 두 선택지만
보여준다. 자동 체크가 이미 받아둔 릴리즈가 있으면 prefetched_release 로 주입해
API 재호출 없이 곧장 UPDATE_AVAILABLE 로 진입한다(수동 체크는 직접 조회).
"""

import threading
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QObject, pyqtSignal, QTimer
from PyQt6.QtWidgets import (
    QDialog, QLabel, QVBoxLayout, QHBoxLayout, QPushButton,
    QProgressBar, QApplication, QMessageBox,
)

from ..__version__ import __version__
from ..core import updater
from ..ui_windows.theme import C, DIALOG_STYLE


# ─── 백그라운드 통신 신호 (메인 스레드로 안전하게 넘기기) ─────────────────
class _Signals(QObject):
    release_fetched = pyqtSignal(object, object)     # ReleaseInfo|None, FetchError|None
    download_progress = pyqtSignal(int, int)         # done, total
    download_done = pyqtSignal(bool, object)         # success, dest Path


# ─── 상태 ──────────────────────────────────────────────────────────────────
_S_CHECKING = "checking"
_S_UP_TO_DATE = "up_to_date"
_S_UPDATE_AVAILABLE = "update_available"
_S_DOWNLOADING = "downloading"
_S_ERROR = "error"


def show_topmost_message(icon: QMessageBox.Icon, title: str, text: str) -> None:
    """업데이트 완료/실패 안내를 바탕화면 위젯(WindowStaysOnTopHint) 위로 띄운다.
    정적 QMessageBox.information/warning 은 창 플래그를 줄 수 없어, 인스턴스로 만들어
    최상단 플래그를 건 뒤 표시한다."""
    box = QMessageBox()
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    box.exec()


def _fetch_error_message(error: Optional[updater.FetchError]) -> str:
    """FetchError 종류별 한글 안내. 사용자가 다음에 뭘 해야 하는지가 분명하도록.
    예전엔 모든 실패가 같은 '네트워크 상태를 확인해주세요' 였는데, rate-limit 처럼
    네트워크는 멀쩡한 경우엔 사용자를 헷갈리게 했다."""
    if error is None:
        # 호환성 안전망 — 새 코드 경로는 항상 error 를 채워서 emit 함
        return "최신 버전 정보를 가져오지 못했습니다. 잠시 후 다시 시도해주세요."
    if error.kind == "rate_limit":
        sec = error.reset_in_seconds
        if sec is None or sec <= 0:
            wait = "잠시 후"
        elif sec < 60:
            wait = f"{sec}초 후"
        else:
            wait = f"약 {(sec + 59) // 60}분 후"
        return (
            f"GitHub 업데이트 서버의 호출 한도를 초과했습니다. {wait}에 다시 시도해주세요.\n"
            f"(네트워크는 정상 — 잠깐만 기다리면 자동으로 풀립니다.)"
        )
    if error.kind == "network":
        return "GitHub 업데이트 서버에 연결하지 못했습니다. 네트워크 상태를 확인해주세요."
    if error.kind == "no_asset":
        return (
            "최신 릴리즈에서 이 OS 용 설치 파일을 찾지 못했습니다.\n"
            "릴리즈 페이지에서 직접 확인해주세요."
        )
    if error.kind == "http":
        return f"업데이트 서버 응답이 비정상입니다. ({error.detail.splitlines()[0][:80]})"
    # bad_tag / parse / 기타
    return "최신 버전 정보를 해석하지 못했습니다. 잠시 후 다시 시도해주세요."


class UpdateDialog(QDialog):
    """업데이트 확인 + 다운로드 + 적용을 한 모달에서 처리."""

    def __init__(
        self,
        parent=None,
        on_release_seen: Optional[Callable[[updater.ReleaseInfo], None]] = None,
        on_skip_version: Optional[Callable[[str], None]] = None,
        prefetched_release: Optional[updater.ReleaseInfo] = None,
    ):
        """on_release_seen: API 조회 성공 시 호출되는 콜백. manager 가 오늘자 체크 기록
            (last_check_date) 을 갱신할 때 사용. 수동 체크 경로에서만 의미가 있다.
        on_skip_version: '이 버전에서는 업데이트를 하지 않음' 선택 시 호출되는 콜백.
            인자는 건너뛸 버전(예: "0.1.5"). manager 가 skipped_version 으로 저장해
            같은 버전을 자동으로 다시 묻지 않게 한다.
        prefetched_release: 자동 체크가 이미 받아둔 릴리즈. 주어지면 API 재호출 없이
            곧장 UPDATE_AVAILABLE 로 진입한다(manager 가 새 버전일 때만 넘기므로)."""
        super().__init__(parent)
        self.setWindowTitle("업데이트 확인")
        self.setMinimumWidth(460)
        self.setStyleSheet(DIALOG_STYLE)
        # 항상 최상단 — 바탕화면 위젯들이 WindowStaysOnTopHint 라, 업데이트 안내가
        # 그 아래로 가려지지 않게 같은 최상단 밴드로 올린다(show 때 앞으로 끌어옴).
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

        self._signals = _Signals()
        self._signals.release_fetched.connect(self._on_release_fetched)
        self._signals.download_progress.connect(self._on_download_progress)
        self._signals.download_done.connect(self._on_download_done)

        self._on_release_seen = on_release_seen
        self._on_skip_version = on_skip_version
        self._prefetched = prefetched_release
        self._release: Optional[updater.ReleaseInfo] = None
        self._cancel_event = threading.Event()
        self._worker: Optional[threading.Thread] = None

        self._build_ui()
        self._set_state(_S_CHECKING)

        # 다이얼로그가 열리는 순간 조회 시작 (prefetched 면 재호출 없이 바로 표시)
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
        self.version_label.setStyleSheet(f"color: {C['subtext']}; font-size: 12px;")
        self.version_label.setWordWrap(True)
        root.addWidget(self.version_label)

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

        self.btn_skip = QPushButton("이 버전에서는 업데이트를 하지 않음")
        self.btn_skip.setProperty("flat", "true")
        self.btn_skip.clicked.connect(self._skip_this_version)

        self.btn_update_now = QPushButton("업데이트")
        self.btn_update_now.clicked.connect(self._start_download)

        self.btn_close = QPushButton("닫기")
        self.btn_close.setProperty("flat", "true")
        self.btn_close.clicked.connect(self.reject)

        self.btn_cancel_dl = QPushButton("취소")
        self.btn_cancel_dl.setProperty("flat", "true")
        self.btn_cancel_dl.clicked.connect(self._cancel_download)

        for b in (self.btn_skip, self.btn_update_now,
                  self.btn_cancel_dl, self.btn_close):
            self.btn_row.addWidget(b)

        root.addLayout(self.btn_row)

    # ── 상태 전환 ──────────────────────────────────────────────────────────
    def _set_state(self, state: str):
        self._state = state
        # 공통: 일단 모두 숨기고 상태별로 켠다
        self.progress_bar.hide()
        for b in (self.btn_skip, self.btn_update_now,
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
            cur = f"v{__version__}"
            lat = self._release.tag or f"v{self._release.version}"
            if updater.can_self_update():
                # 사용자가 요청한 두 선택지만 — 릴리즈 노트 없음.
                self.status_label.setText("새 릴리즈를 다운로드하시겠습니까?")
                self.version_label.setText(f"현재 버전: {cur}\n최신 버전: {lat}")
                self.btn_update_now.show()
                self.btn_skip.show()
            else:
                # 개발 빌드 등 자동 업데이트 불가 — 안내만 하고 닫기.
                self.status_label.setText("새 버전이 있습니다.")
                self.version_label.setText(
                    f"현재 버전: {cur}\n최신 버전: {lat}\n"
                    "(현재 빌드에서는 자동 업데이트가 지원되지 않습니다.)"
                )
                self.btn_close.show()

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
        # 자동 체크가 이미 받아둔 릴리즈가 있으면 재조회 없이 곧장 표시.
        # manager 는 새 버전일 때만 prefetched 를 넘기므로 UPDATE_AVAILABLE 로 직행한다.
        if self._prefetched is not None:
            self._release = self._prefetched
            self._set_state(_S_UPDATE_AVAILABLE)
            return
        def worker():
            rel, err = updater.fetch_latest_release_with_error()
            self._signals.release_fetched.emit(rel, err)
        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _on_release_fetched(
        self,
        release: Optional[updater.ReleaseInfo],
        error: Optional[updater.FetchError],
    ):
        if release is None:
            self._show_error(_fetch_error_message(error))
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
            # 안전망 — 자동 업데이트 불가 빌드에서는 '업데이트' 버튼 자체가 안 뜬다.
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
        # 교체된 새 버전이 다음 실행 때 '업데이트 완료' 안내를 띄울 수 있게 목표 버전 기록.
        updater.mark_update_pending(self._release.version)
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
    def _skip_this_version(self):
        """'이 버전에서는 업데이트를 하지 않음' — manager 에 건너뛸 버전을 알리고 닫는다.
        같은 버전은 자동 체크에서 다시 묻지 않는다(수동 체크에서는 계속 보임)."""
        if self._release is not None and self._on_skip_version is not None:
            try:
                self._on_skip_version(self._release.version)
            except Exception as e:
                print(f"[update_dialog] on_skip_version 콜백 오류: {e}")
        self.reject()

    def _show_error(self, message: str):
        self._set_state(_S_ERROR)
        self.status_label.setText(message)
        self.version_label.setText("")

    # ── 표시 시 최상단으로 끌어오기 ──────────────────────────────────────
    def showEvent(self, event):
        super().showEvent(event)
        # WindowStaysOnTopHint 만으로는 같은 최상단 위젯들 사이 순서가 보장되지 않아,
        # 표시 시점에 명시적으로 맨 앞 + 포커스로 끌어온다.
        self.raise_()
        self.activateWindow()

    # ── 닫힘 시 진행 중인 다운로드 안전하게 종료 ──────────────────────────
    def closeEvent(self, event):
        self._cancel_event.set()
        super().closeEvent(event)

    def reject(self):
        self._cancel_event.set()
        super().reject()
