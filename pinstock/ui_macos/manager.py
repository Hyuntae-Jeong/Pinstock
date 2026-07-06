"""macOS 환경의 메인 오케스트레이션.

- stocks.json 로드/저장
- 종목별 시세/차트 백그라운드 폴링
- 메뉴바 아이콘 → 팝오버 토글
- 종목 추가/관리/Excel 다이얼로그는 ui_windows 모듈 재사용
"""

import os
import json
import copy
import shutil
import threading
from datetime import datetime, date

from PyQt6.QtCore import Qt, QObject, QTimer, QEvent, QPoint, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox, QFileDialog, QMenu

from ..__version__ import __version__
from ..core import updater, stock_index
from ..core.api import (
    fetch_stock, fetch_minute_chart, fetch_daily_chart,
    fetch_us_stock, fetch_us_minute_chart, fetch_us_daily_chart,
    fetch_usd_krw_rate, fetch_watch_quote, fetch_watch_daily, WATCH_POPUP_CANDLES,
)
from ..core.autostart import autostart_supported, is_autostart_enabled, set_autostart
from ..core.portfolio import is_us_stock, portfolio_totals
from ..core.storage import (
    CONFIG_FILE, BACKUP_FILE,
    export_stocks_to_excel, import_stocks_from_excel, normalize_stocks_schema,
    normalize_watchlist_schema, normalize_tags, prune_watch_tags, normalize_memo,
    normalize_stock_memos, normalize_detached,
)
from ..ui_windows.manage_dialog import (
    BuyPreviewDialog, StockDialog, ManageStocksDialog, ManageWatchlistDialog,
    ImportModeDialog, fetch_quote_for_stock,
)
from ..ui_common.update_dialog import UpdateDialog, show_topmost_message
from ..ui_common.help_dialog import HelpDialog
from ..ui_common.memo_dialog import MemoDialog
from ..ui_common.stock_memo_list_dialog import StockMemoListDialog

from .popover import Popover
from .menubar import MenuBarIcon


# ─── 자동 업데이트 체크 설정 (Windows 매니저와 동일) ──────────────────────
# 하루 1회 — 오늘 이미 확인했으면(last_check_date == 오늘) 껐다 켜도 다시 확인하지 않는다.
_AUTO_CHECK_STARTUP_DELAY_MS = 5 * 1000        # 앱 시작 후 5초 뒤 (차트 다 뜬 뒤)
_PREV_ERROR_CHECK_DELAY_MS = 1500              # 시작 직후 1.5초


# ─── 메뉴 체크 표시 ───────────────────────────────────────────────────────────
# 네이티브 체크는 별도 인디케이터 칸에 시스템 스타일로 그려져 라벨과 따로 논다.
# 대신 라벨 앞에 단색 ✓ 를 넣으면 메뉴 폰트·색(다크모드 포함)을 그대로 물려받아
# 글자와 자연스럽게 붙는다. 켜짐이면 ✓, 꺼짐이면 마커 없이 텍스트만.
_CHECK_PREFIX = "✓   "
_LABEL_ASSETS_HIDDEN = "자산 정보 숨기기"
_LABEL_AUTOSTART = "시작 시 자동 실행"


def _checkmark_text(label: str, checked: bool) -> str:
    return (_CHECK_PREFIX if checked else "") + label


class _UpdateCheckSignals(QObject):
    """백그라운드 fetch 결과를 메인 스레드로 안전하게 옮기는 통로."""
    done = pyqtSignal(object)   # ReleaseInfo or None


# ─── 종목별 시세/차트 폴링 워커 ───────────────────────────────────────────────
class StockFetcher(QObject):
    """한 종목의 가격(5초)/차트(60초) 폴링.
    Windows StockWidget 안에 있던 _fetch_price/_fetch_chart 로직과 같다."""

    price_updated  = pyqtSignal(str, dict)            # code, result
    minute_updated = pyqtSignal(str, list, float)     # code, prices, open_price
    daily_updated  = pyqtSignal(str, list)            # code, candles

    STAGGER_MS = 600

    def __init__(self, stock: dict, stagger_idx: int = 0, parent: QObject | None = None):
        super().__init__(parent)
        self.stock = stock
        self.code = stock["code"]
        self._prev_change_price: int = 0

        self.price_timer = QTimer(self)
        self.price_timer.timeout.connect(self._fetch_price)

        self.chart_timer = QTimer(self)
        self.chart_timer.timeout.connect(self._fetch_chart)

        QTimer.singleShot(stagger_idx * self.STAGGER_MS, self._start)

    def _start(self):
        self.price_timer.start(5_000)
        self.chart_timer.start(60_000)
        self._fetch_price()
        self._fetch_chart()

    def _fetch_price(self):
        result = fetch_us_stock(self.code) if is_us_stock(self.stock) else fetch_stock(self.code)
        if result:
            self._prev_change_price = int(result.get("change_price", 0))
            self.price_updated.emit(self.code, result)

    def _fetch_chart(self):
        if is_us_stock(self.stock):
            chart = fetch_us_minute_chart(self.code)
        else:
            chart = fetch_minute_chart(self.code)
        if chart and len(chart["prices"]) >= 2:
            self.minute_updated.emit(self.code, chart["prices"], chart["open"])
        else:
            daily = fetch_us_daily_chart(self.code) if is_us_stock(self.stock) else fetch_daily_chart(self.code)
            if daily:
                self.daily_updated.emit(self.code, daily["candles"])

    def stop(self):
        self.price_timer.stop()
        self.chart_timer.stop()


# ─── 관심종목 일봉 폴링 워커 ───────────────────────────────────────────────────
class WatchFetcher(QObject):
    """한 관심종목의 일봉 기준 시세 폴링 (60초). 보유 워커(StockFetcher)와 달리
    분봉을 쓰지 않고 현재가(전일대비) + 일봉 캔들만 가져온다."""

    price_updated = pyqtSignal(str, dict)    # code, result (현재가 + 전일대비)
    daily_updated = pyqtSignal(str, list)    # code, candles

    STAGGER_MS = 600
    INTERVAL_MS = 60_000

    def __init__(self, item: dict, stagger_idx: int = 0, parent: QObject | None = None):
        super().__init__(parent)
        self.item = item
        self.code = item["code"]
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._fetch)
        QTimer.singleShot(stagger_idx * self.STAGGER_MS, self._start)

    def _start(self):
        self.timer.start(self.INTERVAL_MS)
        self._fetch()

    def _fetch(self):
        # 지수/국내/해외를 타입·시장에 맞게 라우팅 (fetch_watch_* 가 분기)
        result = fetch_watch_quote(self.item)
        if result:
            self.price_updated.emit(self.code, result)
        # 확대 팝업의 3개월·이동평균선까지 그릴 수 있게 긴 이력을 받는다.
        # (미니 차트에는 행/팝업이 최근 일부만 표시)
        daily = fetch_watch_daily(self.item, max_candles=WATCH_POPUP_CANDLES)
        if daily and daily.get("candles"):
            self.daily_updated.emit(self.code, daily["candles"])

    def stop(self):
        self.timer.stop()


# ─── 매니저 ─────────────────────────────────────────────────────────────────
class MacAppManager(QObject):
    """macOS Pinstock 메인 매니저."""

    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        # popover 가 현재 떠 있는지 명시적으로 추적하는 단일 진실값.
        # 토글/표시에서 직접 갱신하며, isVisible() 에 의존하지 않는다.
        # 이유: macOS 에서 Qt.Tool(NSPanel) 은 앱이 inactive 가 되면
        # hidesOnDeactivate 로 Cocoa 레벨에서 숨겨지지만 Qt 의 isVisible() 은
        # True 로 남는 desync 가 있다 — 그 상태로 토글하면 "이미 열림 → hide"
        # 로만 읽혀 외부 클릭 후 아이콘이 먹통 된다. 그래서 외부 클릭(=앱
        # 비활성)으로 popover 가 사라지면 이 플래그를 False 로 맞춰, 다음 트레이
        # 클릭이 정상적으로 "열기" 로 동작하게 한다.
        self._popover_shown: bool = False
        app.installEventFilter(self)
        app.applicationStateChanged.connect(self._on_app_state_changed)

        self.stocks: list[dict] = []
        self.watchlist: list[dict] = []   # 관심종목 — 보유와 독립된 별도 목록
        self.watch_tags: list[dict] = []  # 관심종목 태그 레지스트리 {id,name,color}
        # 확대 일봉 팝업 이동평균선 표시 설정 — 관심 행/hover 팝업이 공유(제자리 갱신).
        self.watch_ma: dict = {"ma5": True, "ma20": True, "ma60": True,
                               "show_name": True, "popup_months": 3,
                               "axis_date": False, "axis_price": False,
                               "show_volume": False}
        self.fetchers: dict[str, StockFetcher] = {}
        self.watch_fetchers: dict[str, WatchFetcher] = {}   # 관심종목 일봉 폴러
        self.current_prices: dict[str, float] = {}
        self.usd_krw_rate: float | None = None
        # 마지막 폴링 결과 캐시. set_stocks() 가 행 위젯을 폐기·재생성하면
        # 차트가 다음 60초 폴링 전까지 비어보여서, 직후에 다시 주입해 채운다.
        self.last_price_result: dict[str, dict] = {}
        self.last_minute_data:  dict[str, tuple[list, float]] = {}
        self.last_daily_data:   dict[str, list] = {}
        # 관심종목 캐시 — 분리/도킹으로 관심 뷰가 창을 옮길 때 즉시 재주입해 '─' 깜빡임 제거.
        self.last_watch_price:  dict[str, dict] = {}
        self.last_watch_daily:  dict[str, list] = {}

        # 설정 로드 (Windows 와 동일 스키마)
        self.master_visible: bool = True
        self.master_pos: list | None = None
        self.assets_hidden: bool = False
        self.us_return_basis: str = "krw"   # 미국 주식 수익률 표시 기준 (krw|usd)
        self.popover_opacity: float = 1.0
        self.popover_height: int | None = None
        self.popover_offset: list[int] | None = None
        self.pinned: bool = False
        self.market_filter: str = "ALL"
        # 분리(detach) 상태 — 보유/관심 중 하나를 독립 창으로 분리(한쪽만 분리 정책).
        # detached_view 가 None 이면 둘 다 메인 팝오버에 탭으로 있다.
        self.detached_view: str | None = None
        self.detached_window: Popover | None = None
        self.detached_pos: list[int] | None = None
        self.detached_height: int | None = None
        self.detached_pinned: bool = False
        self.detached_opacity: float = 1.0
        self.detached_market_filter: str = "ALL"
        # 투자 메모장 — 앱 전체 단일 메모 {text, updated_at}. 모드리스 창은 1개만 띄운다.
        self.memo: dict = {"text": "", "updated_at": None}
        self._memo_dialog: MemoDialog | None = None
        # 종목별 메모 — 종목 코드별 모드리스 창. 여러 종목 메모를 동시에 띄울 수 있다.
        self._stock_memo_dialogs: dict[str, MemoDialog] = {}
        # 종목별 메모 저장소 — {code: {text, updated_at, geometry, name}}. 종목 dict 와
        # 분리돼 있어 종목을 삭제해도 메모가 남고, 같은 코드 종목을 다시 추가하면
        # 자동으로 다시 붙는다. 삭제된 종목의 메모는 목록에 빨간색으로 표시한다.
        self.stock_memos: dict[str, dict] = {}
        # 종목별 메모 모아보기 — 메모 있는 종목을 카드 리스트로. 창은 1개만 띄운다.
        self._memo_list_dialog: StockMemoListDialog | None = None
        # 자동 업데이트 체크 상태 — 하루 1회 체크(날짜) + 건너뛴 버전 기억
        self.update_last_check_date: date | None = None
        self.update_skipped_version: str | None = None
        self._update_signals = _UpdateCheckSignals()
        self._update_signals.done.connect(self._on_auto_check_done)
        self._load_config()

        self.fx_timer = QTimer(self)
        self.fx_timer.timeout.connect(self._fetch_usd_krw_rate)

        # UI
        self.popover = Popover()
        self.menubar = MenuBarIcon(app, parent=self)
        self._build_tray_menu()

        # 시그널 연결
        self.menubar.toggle_popover_requested.connect(self._on_toggle_popover)
        self.menubar.context_menu_requested.connect(self._on_tray_context_menu)
        self.popover.toggle_assets_requested.connect(self._toggle_assets_hidden)
        self.popover.context_menu_requested.connect(self._on_tray_context_menu)
        self.popover.buy_requested.connect(self._on_buy_request)
        self.popover.edit_requested.connect(self._on_edit_request)
        self.popover.memo_requested.connect(self.open_stock_memo_dialog)
        self.popover.delete_requested.connect(self._on_delete_request)
        self.popover.manage_watch_requested.connect(self.open_manage_watch_dialog)
        self.popover.market_filter_changed.connect(self._on_market_filter_changed)
        self.popover.opacity_changed.connect(self._on_opacity_changed)
        self.popover.height_changed.connect(self._on_height_changed)
        self.popover.position_offset_changed.connect(self._on_position_offset_changed)
        self.popover.pinned_changed.connect(self._on_pinned_changed)
        self.popover.closed_by_user.connect(self._on_popover_closed_by_user)
        self.popover.detach_requested.connect(self._detach_view)

        # 로드한 자산 숨김 / 팝오버 투명도 상태를 팝오버에 한 번 주입
        self.popover.set_assets_hidden(self.assets_hidden)
        self.popover.set_us_return_basis(self.us_return_basis)
        self.tray_assets_action.setText(_checkmark_text(_LABEL_ASSETS_HIDDEN, self.assets_hidden))
        self.popover.set_opacity(self.popover_opacity)
        self.popover.set_preferred_height(self.popover_height)
        self.popover.set_position_offset(self.popover_offset)
        self.popover.set_pinned(self.pinned)
        self.popover.set_market_filter(self.market_filter)
        # 확대 일봉 팝업 이동평균선 설정 — 공유 dict 참조를 주입(이후 제자리 갱신 반영)
        self.popover.set_watch_ma(self.watch_ma)

        # 초기 데이터 푸시
        self._sync_popover_stocks()
        self._sync_popover_watchlist()
        self._recompute_summary()

        # 저장된 분리 상태 복원 — 메인 호스팅 뷰는 즉시 줄이고 분리 창은 잠시 뒤 생성.
        self._apply_restored_detach()

        # 종목별 폴링 시작
        for i, s in enumerate(self.stocks):
            self._spawn_fetcher(s, stagger_idx=i)
        # 관심종목 일봉 폴링 시작
        for i, w in enumerate(self.watchlist):
            self._spawn_watch_fetcher(w, stagger_idx=i)
        self._sync_fx_timer()

        # 시작 직후 — 이전 업데이트 실패 로그 / 직전 업데이트 완료 여부 안내
        QTimer.singleShot(_PREV_ERROR_CHECK_DELAY_MS, self._check_previous_update_error)
        QTimer.singleShot(_PREV_ERROR_CHECK_DELAY_MS, self._check_update_completed)
        # 시작 5초 뒤 — 자동 업데이트 체크 (오늘 체크 여부/can_self_update 검사 후 실제 호출)
        QTimer.singleShot(_AUTO_CHECK_STARTUP_DELAY_MS, self._maybe_run_auto_update_check)

        # 시작 시 위젯(팝오버) 즉시 표시 — 트레이 아이콘 geometry 가 잡힌 뒤에
        # 띄워야 "아이콘 바로 밑" 위치로 정확히 뜬다 (준비 전이면 화면 우상단
        # 추정 위치로 폴백돼 어긋남).
        QTimer.singleShot(300, self._show_popover_initial)

        # 종목 검색 중간(substring) 보강용 로컬 인덱스 — 백그라운드 1회 수집
        stock_index.start_background_refresh()

    # ── 트레이 아이콘 우클릭 컨텍스트 메뉴 ────────────────────────────────
    def _build_tray_menu(self):
        """메뉴바 아이콘 우클릭 컨텍스트 메뉴 — 종목 추가/관리, Excel,
        자산 숨김, 도움말, 종료. 메뉴바 전용 앱(LSUIElement)이라 상단
        네이티브 메뉴바가 없으므로, 모든 메뉴 액션의 단일 진입점이다.
        """
        menu = QMenu()
        menu.addAction("종목 추가", self.open_add_dialog)
        menu.addAction("종목 관리", self.open_manage_dialog)
        menu.addSeparator()
        menu.addAction("관심종목 추가", self.open_add_watch_dialog)
        menu.addAction("관심종목 관리", self.open_manage_watch_dialog)
        menu.addSeparator()
        menu.addAction("메모장", self.open_memo_dialog)
        menu.addAction("종목별 메모", self.open_stock_memo_list_dialog)
        menu.addSeparator()
        self.tray_assets_action = menu.addAction(
            _LABEL_ASSETS_HIDDEN, self._toggle_assets_hidden
        )
        self.tray_us_basis_action = menu.addAction(
            self._us_basis_text(), self.toggle_us_return_basis
        )
        if autostart_supported():
            self.autostart_action = menu.addAction(
                _checkmark_text(_LABEL_AUTOSTART, is_autostart_enabled()),
                self.toggle_autostart,
            )
        menu.addSeparator()
        menu.addAction("Excel 내보내기", self.open_export_dialog)
        menu.addAction("Excel 가져오기", self.open_import_dialog)
        menu.addSeparator()
        menu.addAction("도움말", self.open_help_dialog)
        menu.addSeparator()
        menu.addAction("종료", self.app.quit)
        self.tray_menu = menu

    def _on_tray_context_menu(self, anchor_pos):
        # 우클릭 메뉴는 팝오버 표시 상태를 바꾸지 않는다. 팝오버 닫기는
        # 아이콘 좌클릭 토글, ESC, 비고정 상태의 앱 비활성화 경로에서 처리한다.
        self.tray_menu.popup(anchor_pos)

    # ── 앱 inactive 트랜지션 ──────────────────────────────────────────────
    # 외부 클릭 등으로 앱이 비활성화되면 NSPanel 이 Cocoa 레벨에서 자동으로
    # 숨겨진다. 그때 우리의 _popover_shown 플래그를 False 로 맞춰, 다음 트레이
    # 클릭이 "열기" 로 동작하게 한다. 시그널과 eventFilter 두 경로 모두에서
    # 처리한다 (belt-and-suspenders) — macOS/Qt 버전에 따라 한쪽만 발화하는
    # 경우가 있어 한쪽이라도 잡히면 상태가 어긋나지 않게 한다.
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.ApplicationDeactivate:
            self._on_app_deactivated()
        return False

    def _on_app_state_changed(self, state):
        if state == Qt.ApplicationState.ApplicationInactive:
            self._on_app_deactivated()

    def _on_app_deactivated(self):
        """앱 비활성화(외부 클릭 등) → 고정 안 된 창을 숨긴다. 메인/분리 창은 각자
        자기 핀 상태를 따른다 (둘 다 핀 해제면 둘 다 숨김, 핀 된 창은 유지)."""
        if self.detached_window is not None and not self.detached_pinned:
            self.detached_window.hide()
        if self.pinned:
            return
        self.popover.hide()
        self._popover_shown = False

    # ── 팝오버 표시/숨김 ──────────────────────────────────────────────────
    # 분리 상태여도 아이콘 토글/명시적 닫기는 메인+분리 두 창을 한 단위로 함께
    # 표시/숨김한다. 한 창만 보고 싶으면 합치기(도킹)로 한 창으로 만든다.
    def _show_popover(self, anchor_pos, anchor_w):
        self.popover.show_below(anchor_pos, anchor_w)
        self.popover.raise_()
        self.popover.activateWindow()
        if self.detached_window is not None:
            self.detached_window.show_at(self._detached_show_pos())
        self._popover_shown = True

    def _hide_popover(self):
        """명시적 닫기(아이콘 토글/ESC) — 핀과 무관하게 메인+분리 모두 숨긴다."""
        self.popover.hide()
        if self.detached_window is not None:
            self.detached_window.hide()
        self._popover_shown = False

    def _show_popover_initial(self, attempts: int = 0):
        """앱 시작 직후 팝오버 자동 표시. 트레이 아이콘 geometry 가 잡힐 때까지
        잠깐 기다렸다 띄워, 저장된 offset 이 없으면 아이콘 바로 밑에 뜨게 한다.
        (geometry 가 끝내 안 잡히면 최대 ~2초 뒤 추정 위치로라도 표시.)"""
        geo = self.menubar.tray.geometry()
        if (geo.width() <= 0 or geo.height() <= 0) and attempts < 20:
            QTimer.singleShot(100, lambda: self._show_popover_initial(attempts + 1))
            return
        anchor_pos, anchor_w = self.menubar._anchor_position()
        self._show_popover(anchor_pos, anchor_w)

    def _on_popover_closed_by_user(self):
        # ESC 등으로 메인을 직접 닫음 → 분리 창도 함께 숨긴다 (두 창은 한 단위).
        if self.detached_window is not None:
            self.detached_window.hide()
        self._popover_shown = False

    # ── 팝오버 토글 ───────────────────────────────────────────────────────
    def _on_toggle_popover(self, anchor_pos, anchor_w):
        if self._popover_shown:
            self._hide_popover()
        else:
            self._show_popover(anchor_pos, anchor_w)

    # ── 폴링 워커 관리 ─────────────────────────────────────────────────────
    def _spawn_fetcher(self, stock: dict, stagger_idx: int = 0):
        code = stock["code"]
        f = StockFetcher(stock, stagger_idx, parent=self)
        f.price_updated.connect(self._on_price_updated)
        f.minute_updated.connect(self._on_minute_updated)
        f.daily_updated.connect(self._on_daily_updated)
        self.fetchers[code] = f

    def _kill_fetcher(self, code: str):
        f = self.fetchers.pop(code, None)
        if f:
            f.stop()
            f.deleteLater()
        self.last_price_result.pop(code, None)
        self.last_minute_data.pop(code, None)
        self.last_daily_data.pop(code, None)

    # ── 뷰별 대상 창 라우팅 ────────────────────────────────────────────────
    # 보유/관심 데이터는 그 뷰를 호스팅 중인 창(메인 팝오버 또는 분리 창)으로 보낸다.
    def _holdings_window(self) -> "Popover":
        if self.detached_view == "holdings" and self.detached_window is not None:
            return self.detached_window
        return self.popover

    def _watch_window(self) -> "Popover":
        if self.detached_view == "watch" and self.detached_window is not None:
            return self.detached_window
        return self.popover

    def _all_windows(self) -> list:
        wins = [self.popover]
        if self.detached_window is not None:
            wins.append(self.detached_window)
        return wins

    def _on_price_updated(self, code: str, result: dict):
        # stocks 의 name 도 동기화 (네이버에서 이름 받아오면)
        for s in self.stocks:
            if s["code"] == code:
                s["name"] = result["name"]
                break
        self.current_prices[code] = float(result["price"])
        self.last_price_result[code] = result
        self._holdings_window().update_stock_price(code, result)
        self._recompute_summary()

    def _on_minute_updated(self, code: str, prices: list, open_price: float):
        self.last_minute_data[code] = (prices, open_price)
        self.last_daily_data.pop(code, None)
        self._holdings_window().update_stock_minute(code, prices, open_price)

    def _on_daily_updated(self, code: str, candles: list):
        self.last_daily_data[code] = candles
        self.last_minute_data.pop(code, None)
        self._holdings_window().update_stock_daily(code, candles)

    # ── 관심종목 폴링 워커 관리 ─────────────────────────────────────────────
    def _spawn_watch_fetcher(self, item: dict, stagger_idx: int = 0):
        code = item["code"]
        f = WatchFetcher(item, stagger_idx, parent=self)
        f.price_updated.connect(self._on_watch_price_updated)
        f.daily_updated.connect(self._on_watch_daily_updated)
        self.watch_fetchers[code] = f

    def _kill_watch_fetcher(self, code: str):
        f = self.watch_fetchers.pop(code, None)
        if f:
            f.stop()
            f.deleteLater()

    def _on_watch_price_updated(self, code: str, result: dict):
        for w in self.watchlist:
            if w["code"] == code:
                w["name"] = result["name"]
                break
        self.last_watch_price[code] = result
        self._watch_window().update_watch_price(code, result)

    def _on_watch_daily_updated(self, code: str, candles: list):
        self.last_watch_daily[code] = candles
        self._watch_window().update_watch_daily(code, candles)

    def _toggle_assets_hidden(self):
        """자산 숨김 토글 — 우클릭 메뉴 / 팝오버 상단 카드 클릭 양쪽에서 호출.
        팝오버와 메뉴 체크 상태를 함께 동기화하고 설정에 저장한다."""
        self.assets_hidden = not self.assets_hidden
        for win in self._all_windows():
            win.set_assets_hidden(self.assets_hidden)
        self.tray_assets_action.setText(_checkmark_text(_LABEL_ASSETS_HIDDEN, self.assets_hidden))
        self._save_config()

    def _us_basis_text(self) -> str:
        label = "달러" if self.us_return_basis == "usd" else "원화"
        return f"미국 수익률 기준: {label}"

    def toggle_us_return_basis(self):
        """미국 주식 상세의 수익률(%)을 원화 기준(환율 포함) ↔ 달러 기준(주가만) 전환."""
        self.us_return_basis = "usd" if self.us_return_basis == "krw" else "krw"
        for win in self._all_windows():
            win.set_us_return_basis(self.us_return_basis)
        self.tray_us_basis_action.setText(self._us_basis_text())
        self._save_config()

    def toggle_autostart(self):
        """로그인 시 자동 실행 등록/해제 (LaunchAgent). 실제 반영된 상태로
        라벨의 ✓ 체크를 동기화한다 (plist 쓰기 실패 시 원복)."""
        applied = set_autostart(not is_autostart_enabled())
        self.autostart_action.setText(_checkmark_text(_LABEL_AUTOSTART, applied))

    def _on_opacity_changed(self, opacity: float):
        self.popover_opacity = opacity
        # 메모창도 같은 투명도를 따른다 (열려 있을 때만 실시간 반영).
        if self._memo_dialog is not None and self._memo_dialog.isVisible():
            self._memo_dialog.set_opacity(opacity)
        for dlg in self._stock_memo_dialogs.values():
            if dlg is not None and dlg.isVisible():
                dlg.set_opacity(opacity)
        if self._memo_list_dialog is not None and self._memo_list_dialog.isVisible():
            self._memo_list_dialog.set_opacity(opacity)
        self._save_config()

    def _on_height_changed(self, height: int):
        self.popover_height = height
        self._save_config()

    def _on_position_offset_changed(self, x: int, y: int):
        self.popover_offset = [int(x), int(y)]
        self._save_config()

    def _on_pinned_changed(self, pinned: bool):
        self.pinned = bool(pinned)
        if self.pinned and self.popover.isVisible():
            self._popover_shown = True
        self._save_config()

    # ── 분리 / 도킹 (탭을 독립 창으로 분리, 다시 합치기) ─────────────────────
    def _wire_detached_window(self, win):
        """분리 창의 시그널을 매니저 슬롯에 연결한다. buy/edit/delete/assets 는 메인과
        동일(code-keyed/전역), 투명도·필터·고정·지오메트리·도킹은 분리 전용 슬롯."""
        win.toggle_assets_requested.connect(self._toggle_assets_hidden)
        win.context_menu_requested.connect(self._on_tray_context_menu)
        win.buy_requested.connect(self._on_buy_request)
        win.edit_requested.connect(self._on_edit_request)
        win.memo_requested.connect(self.open_stock_memo_dialog)
        win.delete_requested.connect(self._on_delete_request)
        win.manage_watch_requested.connect(self.open_manage_watch_dialog)
        win.market_filter_changed.connect(self._on_detached_market_filter_changed)
        win.opacity_changed.connect(self._on_detached_opacity_changed)
        win.height_changed.connect(self._on_detached_height_changed)
        win.pinned_changed.connect(self._on_detached_pinned_changed)
        win.dock_requested.connect(self._dock_view)
        win.detached_geometry_changed.connect(self._on_detached_geometry_changed)

    def _detached_show_pos(self, drop_pos=None):
        """분리 창 표시 좌표. 저장값 우선, 없으면 드롭 위치(살짝 보정), 그것도 없으면
        메인 팝오버 오른쪽에 나란히."""
        if self.detached_pos and len(self.detached_pos) == 2:
            return QPoint(int(self.detached_pos[0]), int(self.detached_pos[1]))
        if drop_pos is not None:
            # 커서가 탭 근처에 오도록 살짝 좌상향 보정
            return QPoint(drop_pos.x() - 30, drop_pos.y() - 10)
        g = self.popover.frameGeometry()
        return QPoint(g.right() + 12, g.top())

    def _detach_view(self, view: str, drop_pos=None):
        """뷰(보유/관심)를 메인 팝오버에서 떼어 독립 창으로 분리한다 (드래그 경로).
        한쪽만 분리 정책 — 이미 분리된 뷰가 있으면 무시한다."""
        if view not in ("holdings", "watch") or self.detached_view is not None:
            return
        other = "watch" if view == "holdings" else "holdings"
        # 새로 분리할 때는 저장된 위치를 비워 드롭 위치에 뜨게 한다.
        self.detached_pos = None
        self.popover.set_hosted_views([other])
        self._create_detached_window(view, drop_pos)

    def _create_detached_window(self, view: str, drop_pos=None):
        """분리 창을 실제로 생성·배선·표시한다 (드래그 분리 / 시작 시 복원 공용)."""
        self.detached_view = view
        if not self.detached_market_filter:
            self.detached_market_filter = self.market_filter
        win = Popover(detached=True, hosted_views=[view])
        self.detached_window = win
        self._wire_detached_window(win)

        # 현재 전역/표시 상태 주입
        win.set_assets_hidden(self.assets_hidden)
        win.set_us_return_basis(self.us_return_basis)
        win.set_usd_krw_rate(self.usd_krw_rate)
        win.set_watch_ma(self.watch_ma)
        win.set_market_filter(self.detached_market_filter)
        win.set_opacity(self.detached_opacity)
        if self.detached_height is not None:
            win.set_preferred_height(self.detached_height)

        # 데이터 주입 — 라우팅 헬퍼가 보유/관심을 각자의 창으로 보낸다(+캐시 재생).
        self._sync_popover_stocks()
        self._sync_popover_watchlist()
        self._recompute_summary()

        # 고정 상태는 show 전에 적용해 플래그 재설정 깜빡임을 줄인다 (기본은 핀 On).
        if win.is_pinned() != self.detached_pinned:
            win.set_pinned(self.detached_pinned)
        pos = self._detached_show_pos(drop_pos)
        win.show_at(pos)
        # 위치/높이를 즉시 저장해 이동 전에 종료해도 복원되게 한다.
        self.detached_pos = [win.x(), win.y()]
        self.detached_height = win.height()
        self._save_config()

    def _apply_restored_detach(self):
        """시작 시 저장된 분리 상태를 복원한다. 메인 호스팅 뷰는 즉시 줄여(탭 깜빡임
        방지) 두고, 분리 창 생성·표시는 트레이 준비 시점(팝오버 표시 직후)으로 미룬다."""
        view = self.detached_view
        if view not in ("holdings", "watch"):
            self.detached_view = None
            return
        other = "watch" if view == "holdings" else "holdings"
        self.popover.set_hosted_views([other])
        # detached_view 는 창이 실제로 만들어질 때 _create_detached_window 에서 다시 세팅.
        self.detached_view = None
        QTimer.singleShot(320, lambda v=view: self._create_detached_window(v))

    def _dock_view(self):
        """분리 창을 메인 팝오버로 다시 합친다."""
        if self.detached_window is None or self.detached_view is None:
            return
        redocked = self.detached_view
        win = self.detached_window
        self.detached_pos = [win.x(), win.y()]
        self.detached_height = win.height()
        win.hide()
        win.deleteLater()
        self.detached_window = None
        self.detached_view = None
        # 메인이 두 뷰를 다시 호스팅하고, 재결합한 뷰를 활성 탭으로 둔다.
        self.popover.set_hosted_views(["holdings", "watch"], active=redocked)
        self._sync_popover_stocks()
        self._sync_popover_watchlist()
        self._recompute_summary()
        self._save_config()

    def _on_detached_opacity_changed(self, opacity: float):
        self.detached_opacity = opacity
        self._save_config()

    def _on_detached_height_changed(self, height: int):
        self.detached_height = height
        self._save_config()

    def _on_detached_pinned_changed(self, pinned: bool):
        self.detached_pinned = bool(pinned)
        self._save_config()

    def _on_detached_geometry_changed(self, x: int, y: int, w: int, h: int):
        self.detached_pos = [int(x), int(y)]
        self.detached_height = int(h)
        self._save_config()

    def _reapply_cached_data(self, win=None):
        """set_stocks() 이후 새로 만들어진 보유 행에 캐시된 가격/차트를 즉시 다시
        넣어 차트가 비어 보이는 시간을 없앤다. win 미지정 시 보유 호스팅 창."""
        win = win or self._holdings_window()
        for code, result in self.last_price_result.items():
            win.update_stock_price(code, result)
        for code, (prices, open_price) in self.last_minute_data.items():
            win.update_stock_minute(code, prices, open_price)
        for code, candles in self.last_daily_data.items():
            win.update_stock_daily(code, candles)

    def _reapply_watch_cached_data(self, win=None):
        """관심 행에 캐시된 시세/일봉을 즉시 다시 넣는다 (분리/도킹 직후 '─' 깜빡임 제거)."""
        win = win or self._watch_window()
        for code, result in self.last_watch_price.items():
            win.update_watch_price(code, result)
        for code, candles in self.last_watch_daily.items():
            win.update_watch_daily(code, candles)

    def _sync_popover_watchlist(self):
        win = self._watch_window()
        win.set_watch_tags(self.watch_tags)
        win.set_watchlist(self.watchlist)
        self._reapply_watch_cached_data(win)

    def _sync_popover_stocks(self):
        win = self._holdings_window()
        win.set_stocks(self.stocks)
        self._reapply_cached_data(win)

    # ── 포트폴리오 요약 재계산 ───────────────────────────────────────────
    def _recompute_summary(self):
        win = self._holdings_window()
        if not self.stocks:
            win.update_summary(0, 0)
            return
        market = self._holdings_filter()
        stocks = [s for s in self.stocks if self._matches_filter(s, market)]
        totals = portfolio_totals(
            stocks,
            current_prices=self.current_prices,
            usd_krw_rate=self.usd_krw_rate,
        )
        win.update_summary(totals["total_invest"], totals["total_eval"])

    def _holdings_filter(self) -> str:
        """보유 뷰를 호스팅 중인 창의 시장 필터 (요약 계산 기준)."""
        return self.detached_market_filter if self.detached_view == "holdings" else self.market_filter

    def _matches_filter(self, stock: dict, market: str) -> bool:
        if market == "ALL":
            return True
        return ("US" if is_us_stock(stock) else "KR") == market

    def _matches_market_filter(self, stock: dict) -> bool:
        return self._matches_filter(stock, self.market_filter)

    def _on_market_filter_changed(self, market: str):
        """메인 팝오버의 시장 필터 변경 — 메인이 호스팅 중인 뷰만 다시 렌더한다.
        (분리 창은 자기 필터를 따로 가진다.)"""
        self.market_filter = market if market in {"ALL", "KR", "US"} else "ALL"
        if self.detached_view != "holdings":   # 메인이 보유를 호스팅
            self._sync_popover_stocks()
            self._recompute_summary()
        if self.detached_view != "watch":      # 메인이 관심을 호스팅
            self._sync_popover_watchlist()
        self._save_config()

    def _on_detached_market_filter_changed(self, market: str):
        """분리 창의 시장 필터 변경 — 분리 창이 호스팅 중인 뷰만 다시 렌더한다."""
        self.detached_market_filter = market if market in {"ALL", "KR", "US"} else "ALL"
        if self.detached_view == "holdings":
            self._sync_popover_stocks()
            self._recompute_summary()
        elif self.detached_view == "watch":
            self._sync_popover_watchlist()
        self._save_config()

    def _fetch_usd_krw_rate(self):
        result = fetch_usd_krw_rate()
        if not result:
            return
        self.usd_krw_rate = float(result["rate"])
        for win in self._all_windows():
            win.set_usd_krw_rate(self.usd_krw_rate)
        self._recompute_summary()

    def _sync_fx_timer(self):
        if any(is_us_stock(s) for s in self.stocks):
            if not self.fx_timer.isActive():
                self.fx_timer.start(60_000)
            if self.usd_krw_rate is None:
                self._fetch_usd_krw_rate()
        else:
            self.fx_timer.stop()
            self.usd_krw_rate = None
            for win in self._all_windows():
                win.set_usd_krw_rate(None)

    # ── 설정 파일 ──────────────────────────────────────────────────────────
    def _load_config(self):
        if not os.path.exists(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return

        if isinstance(data, list):
            self.stocks = normalize_stocks_schema(data)
        elif isinstance(data, dict):
            self.stocks = normalize_stocks_schema(data.get("stocks", []) or [])
            self.watchlist = normalize_watchlist_schema(data.get("watchlist", []) or [])
            self.watch_tags = normalize_tags(data.get("watch_tags", []) or [])
            prune_watch_tags(self.watchlist, self.watch_tags)
            # 이동평균선 등 표시 설정 — 공유 dict 를 제자리 갱신(참조 유지).
            # popup_months 만 정수(1~6)로, 나머지 키는 불리언으로 받는다.
            ma = data.get("watch_ma")
            if isinstance(ma, dict):
                for k in ("ma5", "ma20", "ma60", "show_name", "axis_date", "axis_price", "show_volume"):
                    if k in ma:
                        self.watch_ma[k] = bool(ma[k])
                pm = ma.get("popup_months")
                if isinstance(pm, (int, float)):
                    self.watch_ma["popup_months"] = max(1, min(6, int(pm)))
            master = data.get("master") or {}
            self.master_visible = bool(master.get("visible", True))
            pos = master.get("pos")
            if isinstance(pos, list) and len(pos) == 2:
                try:
                    self.master_pos = [int(pos[0]), int(pos[1])]
                except (TypeError, ValueError):
                    self.master_pos = None
            self.assets_hidden = bool(data.get("assets_hidden", False))
            self.us_return_basis = "usd" if data.get("us_return_basis") == "usd" else "krw"
            self.memo = normalize_memo(data.get("memo"))
            self.stock_memos = normalize_stock_memos(data.get("stock_memos"))
            try:
                opacity = float(data.get("popover_opacity", 1.0))
                self.popover_opacity = max(0.1, min(1.0, opacity))
            except (TypeError, ValueError):
                self.popover_opacity = 1.0
            try:
                height = data.get("popover_height")
                self.popover_height = (
                    max(Popover.MIN_H, int(height))
                    if height is not None else None
                )
            except (TypeError, ValueError):
                self.popover_height = None
            offset = data.get("popover_offset")
            if isinstance(offset, list) and len(offset) == 2:
                try:
                    self.popover_offset = [int(offset[0]), int(offset[1])]
                except (TypeError, ValueError):
                    self.popover_offset = None
            self.pinned = bool(data.get("pinned", False))
            # 분리(detach) 상태 복원 — 어느 뷰가 분리됐는지 + 그 창의 위치/높이/고정/투명도/필터
            det = normalize_detached(data.get("detached"))
            self.detached_view = det["view"]
            self.detached_pos = det["pos"]
            self.detached_height = det["height"]
            self.detached_pinned = det["pinned"]
            self.detached_opacity = det["opacity"]
            self.detached_market_filter = det["market_filter"]
            # 자동 업데이트 메타 — 오늘 체크했는지(날짜) + 건너뛴 버전
            upd = data.get("update") or {}
            last_date = upd.get("last_check_date")
            if isinstance(last_date, str):
                try:
                    self.update_last_check_date = date.fromisoformat(last_date)
                except ValueError:
                    self.update_last_check_date = None
            skipped = upd.get("skipped_version")
            if isinstance(skipped, str) and skipped:
                self.update_skipped_version = skipped
        self._migrate_legacy_stock_memos()

    def _migrate_legacy_stock_memos(self):
        """구버전: 종목 dict 안(stock["memo"])에 있던 메모를 코드별 저장소로 1회 이전.

        이전 후 종목 dict 에서 memo 키를 제거해 이중 저장을 막는다. 저장소에 이미
        같은 코드 항목이 있으면(신버전 우선) 건너뛴다. 마이그레이션 후 저장되면
        stocks.json 에는 더 이상 종목 안에 memo 가 남지 않는다."""
        for s in self.stocks:
            legacy = s.pop("memo", None)
            if legacy is None:
                continue
            code = s.get("code")
            if not code or code in self.stock_memos:
                continue
            base = normalize_memo(legacy)
            if not (base.get("text") or "").strip():
                continue
            base["name"] = s.get("name", "") or ""
            self.stock_memos[code] = base

    def _save_config(self):
        # Windows 와 호환되는 스키마 — Mac 에서는 의미 없는 필드도 보존만 함
        self.stocks = normalize_stocks_schema(self.stocks)
        self.watchlist = normalize_watchlist_schema(self.watchlist)
        self.watch_tags = normalize_tags(self.watch_tags)
        self.memo = normalize_memo(self.memo)
        self.stock_memos = normalize_stock_memos(self.stock_memos)
        data = {
            "stocks": self.stocks,
            "watchlist": self.watchlist,
            "watch_tags": self.watch_tags,
            "watch_ma": self.watch_ma,
            "master": {
                "visible": self.master_visible,
                "pos": self.master_pos,
            },
            "assets_hidden": self.assets_hidden,
            "us_return_basis": self.us_return_basis,
            "popover_opacity": self.popover_opacity,
            "popover_height": self.popover_height,
            "popover_offset": self.popover_offset,
            "pinned": self.pinned,
            "memo": self.memo,
            "stock_memos": self.stock_memos,
            "detached": {
                "view": self.detached_view,
                "pos": self.detached_pos,
                "height": self.detached_height,
                "pinned": self.detached_pinned,
                "opacity": self.detached_opacity,
                "market_filter": self.detached_market_filter,
            },
        }
        upd: dict = {}
        if self.update_last_check_date is not None:
            upd["last_check_date"] = self.update_last_check_date.isoformat()
        if self.update_skipped_version is not None:
            upd["skipped_version"] = self.update_skipped_version
        if upd:
            data["update"] = upd
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[save] 오류: {e}")

    # ── 종목 추가 ──────────────────────────────────────────────────────────
    def open_add_dialog(self):
        dlg = StockDialog()
        if not dlg.exec():
            return
        d = dlg.get_data()
        code = d["code"]
        if not code:
            return
        if any(s["code"] == code for s in self.stocks):
            QMessageBox.information(None, "알림", f"'{code}'는 이미 추가되어 있습니다.")
            return

        result = fetch_quote_for_stock(d)
        if not result:
            QMessageBox.warning(
                None, "조회 실패",
                f"종목코드 '{code}'를 찾을 수 없습니다.\n코드를 다시 확인해 주세요."
            )
            return

        d["name"] = result["name"]
        self.stocks.append(d)
        self.current_prices[code] = float(result["price"])
        self._save_config()
        self._sync_fx_timer()

        # 팝오버 재구성 + 폴링 시작
        self._sync_popover_stocks()
        self._spawn_fetcher(d, stagger_idx=0)
        self._recompute_summary()

    # ── 관심종목 추가 ──────────────────────────────────────────────────────
    def open_add_watch_dialog(self):
        """관심종목 추가 — 보유와 독립. 평단가/수량 없이 코드·종목명만 받는다.
        같은 종목이 보유에 있어도 관심에 따로 추가할 수 있다(중복 검사는 관심목록 안에서만).
        표시(팝오버 관심 뷰)·일봉 폴러 연결은 Step 2b 에서."""
        dlg = StockDialog(watch_mode=True, tags=self.watch_tags)
        if not dlg.exec():
            return
        d = dlg.get_data()
        code = d["code"]
        if not code:
            return
        if any(w["code"] == code for w in self.watchlist):
            QMessageBox.information(None, "알림", f"'{code}'는 이미 관심종목에 있습니다.")
            return

        result = fetch_quote_for_stock(d)
        if not result:
            QMessageBox.warning(
                None, "조회 실패",
                f"종목코드 '{code}'를 찾을 수 없습니다.\n코드를 다시 확인해 주세요."
            )
            return

        d["name"] = result["name"]
        self.watchlist.append(d)
        self._save_config()
        self._sync_popover_watchlist()
        self._spawn_watch_fetcher(d, stagger_idx=0)

    # ── 관심종목 일괄 관리 ──────────────────────────────────────────────────
    def open_manage_watch_dialog(self):
        """관심종목 일괄 관리 — 추가/삭제/표시 토글. 표시(팝오버 관심 뷰)·일봉
        폴러 갱신은 Step 2b 에서 연결한다."""
        dlg = ManageWatchlistDialog(
            watchlist=copy.deepcopy(self.watchlist),
            tags=copy.deepcopy(self.watch_tags),
            ma_settings=dict(self.watch_ma),
            holdings=copy.deepcopy(self.stocks),
        )
        if not dlg.exec():
            return
        old_codes = {w["code"] for w in self.watchlist}
        self.watchlist = normalize_watchlist_schema(dlg.get_watchlist())
        self.watch_tags = normalize_tags(dlg.get_tags())
        self.watch_ma.update(dlg.get_ma_settings())
        prune_watch_tags(self.watchlist, self.watch_tags)
        new_codes = {w["code"] for w in self.watchlist}
        # 삭제된 관심종목: 폴러 정지 / 추가된 관심종목: 폴러 시작
        for code in old_codes - new_codes:
            self._kill_watch_fetcher(code)
        added_idx = 0
        for w in self.watchlist:
            if w["code"] not in old_codes:
                self._spawn_watch_fetcher(w, stagger_idx=added_idx)
                added_idx += 1
        self._save_config()
        self._sync_popover_watchlist()

    # ── 종목 일괄 관리 ────────────────────────────────────────────────────
    def open_manage_dialog(self):
        current_prices = dict(self.current_prices)
        dlg = ManageStocksDialog(
            stocks=copy.deepcopy(self.stocks),
            current_prices=current_prices,
            usd_krw_rate=self.usd_krw_rate,
        )
        if not dlg.exec():
            return
        new_stocks = dlg.get_stocks()
        new_stocks = normalize_stocks_schema(new_stocks)

        old_codes = {s["code"] for s in self.stocks}
        new_codes = {s["code"] for s in new_stocks}

        # 삭제된 종목: fetcher 정지
        for code in old_codes - new_codes:
            self._kill_fetcher(code)
            self.current_prices.pop(code, None)

        # 추가된 종목: fetcher 시작 (stagger)
        added_idx = 0
        for s in new_stocks:
            if s["code"] not in old_codes:
                self._spawn_fetcher(s, stagger_idx=added_idx)
                added_idx += 1

        self.stocks = new_stocks
        self._sync_fx_timer()
        self._save_config()
        self._sync_popover_stocks()
        self._recompute_summary()

    # ── 종목 행 우클릭: 추가 매수 ─────────────────────────────────────────
    def _on_buy_request(self, code: str):
        target = next((s for s in self.stocks if s["code"] == code), None)
        if target is None:
            return

        current_price = self.current_prices.get(code)
        if not current_price:
            result = fetch_quote_for_stock(target)
            if result:
                current_price = float(result["price"])
                self.current_prices[code] = current_price
                self.last_price_result[code] = result
                self.popover.update_stock_price(code, result)
        if not current_price:
            QMessageBox.warning(
                None,
                "현재가 없음",
                "현재가를 확인할 수 없어 예상 평단가를 계산할 수 없습니다.",
            )
            return
        if is_us_stock(target) and not self.usd_krw_rate:
            rate_result = fetch_usd_krw_rate()
            if rate_result:
                self.usd_krw_rate = float(rate_result["rate"])
                self.popover.set_usd_krw_rate(self.usd_krw_rate)

        dlg = BuyPreviewDialog(
            stock=copy.deepcopy(target),
            current_price=current_price,
            usd_krw_rate=self.usd_krw_rate,
        )
        if not dlg.exec():
            return

        updated = dlg.get_data()
        target["avg_price"] = updated["avg_price"]
        target["quantity"] = updated["quantity"]
        if "buy_exchange_rate" in updated:
            target["buy_exchange_rate"] = updated["buy_exchange_rate"]
        else:
            target.pop("buy_exchange_rate", None)
        self._save_config()
        self._sync_popover_stocks()
        self._recompute_summary()

    # ── 종목 행 우클릭: 수정 ──────────────────────────────────────────────
    def _on_edit_request(self, code: str):
        target = next((s for s in self.stocks if s["code"] == code), None)
        if target is None:
            return
        dlg = StockDialog(data=target)
        if not dlg.exec():
            return
        new = dlg.get_data()
        target["avg_price"] = new["avg_price"]
        target["quantity"]  = new["quantity"]
        if "buy_exchange_rate" in new:
            target["buy_exchange_rate"] = new["buy_exchange_rate"]
        else:
            target.pop("buy_exchange_rate", None)
        self._save_config()
        self._sync_popover_stocks()
        self._recompute_summary()

    # ── 종목 행 우클릭: 삭제 ──────────────────────────────────────────────
    def _on_delete_request(self, code: str):
        target = next((s for s in self.stocks if s["code"] == code), None)
        if target is None:
            return
        name = target.get("name", code)
        ret = QMessageBox.question(
            None, "삭제 확인",
            f"'{name}' 을(를) 삭제할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        self.stocks = [s for s in self.stocks if s["code"] != code]
        memo_dlg = self._stock_memo_dialogs.pop(code, None)
        if memo_dlg is not None:
            memo_dlg.close()
        self._kill_fetcher(code)
        self.current_prices.pop(code, None)
        self._sync_fx_timer()
        self._save_config()
        self._sync_popover_stocks()
        self._recompute_summary()
        self._refresh_memo_list_if_open()

    # ── Excel 내보내기 ────────────────────────────────────────────────────
    def open_export_dialog(self):
        if not self.stocks:
            QMessageBox.information(None, "알림", "내보낼 보유 종목이 없습니다.")
            return

        default_name = f"pinstock_holdings_{datetime.now().strftime('%Y%m%d')}.xlsx"
        path, _ = QFileDialog.getSaveFileName(
            None, "보유 종목 Excel로 내보내기",
            os.path.join(os.path.expanduser("~"), default_name),
            "Excel 파일 (*.xlsx)"
        )
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"

        try:
            export_stocks_to_excel(self.stocks, path, self.current_prices, self.usd_krw_rate)
        except ImportError:
            QMessageBox.critical(
                None, "라이브러리 없음",
                "openpyxl 패키지가 필요합니다.\n\n터미널에서 다음을 실행하세요:\n    pip install openpyxl"
            )
            return
        except Exception as e:
            QMessageBox.critical(None, "내보내기 실패", f"파일을 저장할 수 없습니다.\n\n{e}")
            return

        QMessageBox.information(
            None, "내보내기 완료",
            f"{len(self.stocks)}개 종목을 저장했습니다.\n\n{path}"
        )

    # ── Excel 가져오기 ────────────────────────────────────────────────────
    def open_import_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            None, "Excel에서 보유 종목 가져오기",
            os.path.expanduser("~"),
            "Excel 파일 (*.xlsx)"
        )
        if not path:
            return

        try:
            imported = import_stocks_from_excel(path)
        except ImportError:
            QMessageBox.critical(
                None, "라이브러리 없음",
                "openpyxl 패키지가 필요합니다.\n\n터미널에서 다음을 실행하세요:\n    pip install openpyxl"
            )
            return
        except ValueError as e:
            QMessageBox.critical(None, "가져오기 실패", str(e))
            return
        except Exception as e:
            QMessageBox.critical(None, "가져오기 실패", f"파일을 읽을 수 없습니다.\n\n{e}")
            return

        mode_dlg = ImportModeDialog()
        if not mode_dlg.exec():
            return
        mode = mode_dlg.mode

        if mode == "overwrite":
            msg = (
                f"덮어쓰기 모드입니다.\n\n"
                f"기존 {len(self.stocks)}개 종목이 모두 삭제되고\n"
                f"Excel의 {len(imported)}개 종목으로 교체됩니다.\n\n"
                "계속할까요?"
            )
        else:
            new_codes = {s["code"] for s in imported}
            existing_codes = {s["code"] for s in self.stocks}
            updated = len(new_codes & existing_codes)
            added = len(new_codes - existing_codes)
            msg = (
                f"병합 모드입니다.\n\n"
                f"• 갱신: {updated}개 (기존 종목 평단가/수량 업데이트)\n"
                f"• 추가: {added}개 (새 종목)\n"
                f"• 유지: {len(existing_codes - new_codes)}개 (Excel에 없는 기존 종목)\n\n"
                "계속할까요?"
            )
        ret = QMessageBox.question(
            None, "가져오기 확인", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        # stocks.json 백업
        if os.path.exists(CONFIG_FILE):
            try:
                shutil.copy2(CONFIG_FILE, BACKUP_FILE)
            except Exception as e:
                print(f"[backup] 오류: {e}")

        # 새 stocks 구성
        if mode == "overwrite":
            new_stocks = normalize_stocks_schema(imported)
        else:
            by_code = {s["code"]: s for s in self.stocks}
            new_stocks = []
            for s in imported:
                base = dict(by_code.get(s["code"], {}))
                base.update(s)
                new_stocks.append(base)
            imported_codes = {s["code"] for s in imported}
            for s in self.stocks:
                if s["code"] not in imported_codes:
                    new_stocks.append(s)
            new_stocks = normalize_stocks_schema(new_stocks)

        self._rebuild(new_stocks)

        QMessageBox.information(
            None, "가져오기 완료",
            f"총 {len(new_stocks)}개 종목이 적용되었습니다.\n"
            f"이전 데이터는 다음에 백업되었습니다:\n{BACKUP_FILE}"
        )

    # ── 종목 리스트 전체 교체 ─────────────────────────────────────────────
    def _rebuild(self, new_stocks: list[dict]):
        for code in list(self.fetchers):
            self._kill_fetcher(code)
        self.current_prices.clear()

        self.stocks = normalize_stocks_schema(new_stocks)
        self._sync_fx_timer()
        self._save_config()
        self._sync_popover_stocks()
        self._recompute_summary()

        for i, s in enumerate(self.stocks):
            self._spawn_fetcher(s, stagger_idx=i)

    # ── 메모장 ────────────────────────────────────────────────────────────
    def open_memo_dialog(self):
        """투자 메모장 — 모드리스 항상-위 창. 이미 떠 있으면 새로 만들지 않고 앞으로 가져온다."""
        if self._memo_dialog is not None and self._memo_dialog.isVisible():
            self._memo_dialog.raise_()
            self._memo_dialog.activateWindow()
            return
        self._memo_dialog = MemoDialog(
            initial_text=self.memo.get("text", ""),
            initial_geometry=self.memo.get("geometry"),
            opacity=self.popover_opacity,
            on_change=self._on_memo_changed,
        )
        self._memo_dialog.show()
        self._memo_dialog.raise_()
        self._memo_dialog.activateWindow()

    def _on_memo_changed(self, text: str, geometry: list):
        """메모 다이얼로그 콜백 — 텍스트/위치/크기 변경을 저장. 텍스트가 바뀐 경우에만
        수정 시각을 갱신한다(위치·크기 변경은 시각에 영향 없음)."""
        prev = self.memo or {}
        updated_at = prev.get("updated_at")
        if text != prev.get("text", ""):
            updated_at = datetime.now().isoformat(timespec="seconds")
        self.memo = {"text": text, "updated_at": updated_at, "geometry": geometry}
        self._save_config()

    # ── 종목별 메모 ────────────────────────────────────────────────────────
    def _memo_entries(self) -> list:
        """메모가 있는 종목을 최근 수정순으로. 항목: {code,name,text,updated_at,deleted}.

        보유목록에 없는(삭제된) 종목의 메모도 포함하며 deleted=True 로 표시한다.
        이름은 종목이 살아 있으면 현재 종목명, 삭제됐으면 저장소에 남은 마지막 이름."""
        live = {s.get("code"): s for s in self.stocks}
        items = []
        for code, m in self.stock_memos.items():
            text = m.get("text", "")
            if not (text or "").strip():
                continue
            stock = live.get(code)
            name = (stock.get("name") if stock else None) or m.get("name") or code
            items.append({
                "code": code,
                "name": name,
                "text": text,
                "updated_at": m.get("updated_at"),
                "deleted": stock is None,
            })
        items.sort(key=lambda e: e.get("updated_at") or "", reverse=True)
        return items

    def open_stock_memo_list_dialog(self):
        """메모가 있는 보유 종목을 카드 리스트로 모아본다. 카드 클릭 시 해당 종목의
        메모창을 띄운다. 이미 떠 있으면 내용만 갱신하고 앞으로 가져온다."""
        entries = self._memo_entries()
        if self._memo_list_dialog is not None and self._memo_list_dialog.isVisible():
            self._memo_list_dialog.set_entries(entries)
            self._memo_list_dialog.raise_()
            self._memo_list_dialog.activateWindow()
            return
        self._memo_list_dialog = StockMemoListDialog(
            entries=entries,
            opacity=self.popover_opacity,
            on_select=self.open_stock_memo_dialog,
        )
        self._memo_list_dialog.show()
        self._memo_list_dialog.raise_()
        self._memo_list_dialog.activateWindow()

    def open_stock_memo_dialog(self, code: str):
        """종목별 메모 — 모드리스 항상-위 창을 종목마다 띄운다. 코드로 저장소를 조회하므로
        삭제된 종목의 메모도 열 수 있다(제목이 빨간 '종목명 (미보유)'). 이미 떠 있으면 앞으로 가져온다."""
        if not code:
            return
        existing = self._stock_memo_dialogs.get(code)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return
        memo = normalize_memo(self.stock_memos.get(code))
        stock = next((s for s in self.stocks if s.get("code") == code), None)
        name = (stock.get("name") if stock else None) or (self.stock_memos.get(code) or {}).get("name") or code
        deleted = stock is None
        dlg = MemoDialog(
            initial_text=memo.get("text", ""),
            initial_geometry=memo.get("geometry"),
            opacity=self.popover_opacity,
            on_change=lambda text, geom, c=code: self._on_stock_memo_changed(c, text, geom),
            title=f"📝 {name} (미보유)" if deleted else f"📝 {name}",
            deleted=deleted,
        )
        self._stock_memo_dialogs[code] = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_stock_memo_changed(self, code: str, text: str, geometry: list):
        """종목별 메모 콜백 — 코드별 저장소(stock_memos)를 갱신·저장한다.

        텍스트가 비면 항목을 통째로 지운다(메모창을 비우면 사라지는 동작). 저장소는
        종목 삭제와 무관하므로, 같은 코드 종목을 다시 추가하면 메모가 자동으로 다시 붙는다."""
        prev = normalize_memo(self.stock_memos.get(code))
        updated_at = prev.get("updated_at")
        if text != prev.get("text", ""):
            updated_at = datetime.now().isoformat(timespec="seconds")
        if (text or "").strip():
            stock = next((s for s in self.stocks if s.get("code") == code), None)
            name = (stock.get("name") if stock else None) or (self.stock_memos.get(code) or {}).get("name") or code
            self.stock_memos[code] = {
                "text": text, "updated_at": updated_at, "geometry": geometry, "name": name,
            }
        else:
            self.stock_memos.pop(code, None)
        self._save_config()
        self._refresh_memo_list_if_open()

    def _refresh_memo_list_if_open(self):
        """종목별 메모 모아보기 창이 떠 있으면 최신 목록으로 자동 갱신한다."""
        if self._memo_list_dialog is not None and self._memo_list_dialog.isVisible():
            self._memo_list_dialog.set_entries(self._memo_entries())

    # ── 도움말 ────────────────────────────────────────────────────────────
    def open_help_dialog(self):
        # 예전 'Pinstock 정보' 메뉴를 도움말로 흡수했다 — 업데이트 확인 콜백을
        # 넘겨 도움말 'Pinstock 정보' 섹션의 '🔄 업데이트 확인' 링크를 살린다.
        HelpDialog(on_check_update=self.open_update_dialog).exec()

    # ── 업데이트 확인 ─────────────────────────────────────────────────────
    # Windows WidgetManager 와 1:1 대응되는 패턴 — 다이얼로그가 결과를 manager 에
    # 반영하도록 콜백을 넘겨주고, 하루 1회 체크/건너뛴 버전은 manager 가 책임진다.
    def open_update_dialog(self):
        dlg = UpdateDialog(
            on_release_seen=self._on_release_seen,
            on_skip_version=self._on_skip_version,
        )
        dlg.exec()

    def _open_update_prompt(self, release: updater.ReleaseInfo):
        """자동 체크가 새 버전을 찾았을 때 곧장 띄우는 모달 — 이미 받아둔 릴리즈를
        넘겨 API 재호출을 막는다."""
        UpdateDialog(
            on_release_seen=self._on_release_seen,
            on_skip_version=self._on_skip_version,
            prefetched_release=release,
        ).exec()

    def _on_release_seen(self, release: updater.ReleaseInfo):
        """UpdateDialog 가 API 조회에 성공했을 때 호출 — 오늘 체크한 것으로 기록."""
        self.update_last_check_date = date.today()
        self._save_config()

    def _on_skip_version(self, version: str):
        """'이 버전에서는 업데이트를 하지 않음' — 해당 버전은 자동 체크에서 다시 묻지 않음."""
        self.update_skipped_version = version
        self._save_config()

    def _maybe_run_auto_update_check(self):
        """시작 시 1회 진입점. 오늘 이미 확인했으면(껐다 켜도) 건너뛴다."""
        if not updater.can_self_update():
            return
        if self.update_last_check_date == date.today():
            return

        def worker():
            rel = updater.fetch_latest_release()
            self._update_signals.done.emit(rel)
        threading.Thread(target=worker, daemon=True).start()

    def _on_auto_check_done(self, release):
        """백그라운드 fetch 완료 — 메인 스레드에서 호출됨."""
        if release is None:
            # 실패는 silent. 날짜 기록 안 함 → 다음 실행 때 재시도.
            return
        # 조회에 성공했으니 오늘 체크한 것으로 기록 (껐다 켜도 오늘은 재확인 안 함).
        self.update_last_check_date = date.today()
        self._save_config()
        if (
            updater.is_newer(__version__, release.version)
            and release.version != self.update_skipped_version
        ):
            self._open_update_prompt(release)

    def _check_update_completed(self):
        """직전 실행에서 적용한 업데이트가 실제로 반영됐으면 완료 안내를 한 번 띄운다."""
        pending = updater.read_and_clear_pending_update()
        if pending and pending == __version__:
            show_topmost_message(
                QMessageBox.Icon.Information,
                "업데이트 완료",
                f"버전 v{pending} 으로 업데이트되었습니다.",
            )

    def _check_previous_update_error(self):
        """이전 실행에서 헬퍼가 남긴 에러 로그가 있으면 사용자에게 한 번 보여주고 삭제."""
        log = updater.read_and_clear_last_error()
        if not log:
            return
        show_topmost_message(
            QMessageBox.Icon.Warning,
            "이전 업데이트 실패",
            updater.humanize_error(log) + "\n\n오류 원문:\n" + log,
        )
