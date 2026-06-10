"""Windows 환경 위젯 오케스트레이션."""

import os
import sys
import json
import copy
import shutil
import threading
from pathlib import Path
from datetime import datetime, timedelta

from PyQt6.QtWidgets import (
    QApplication, QMenu, QSystemTrayIcon, QMessageBox, QFileDialog,
)
from PyQt6.QtCore import Qt, QTimer, QObject, pyqtSignal
from PyQt6.QtGui import (
    QIcon, QAction, QPixmap, QPainter, QFont, QColor, QBrush, QPen,
)


# ─── 자동 업데이트 체크 설정 ──────────────────────────────────────────────
_AUTO_CHECK_INTERVAL = timedelta(hours=24)
_AUTO_CHECK_STARTUP_DELAY_MS = 10 * 1000      # 앱 시작 후 10초 뒤
_PREV_ERROR_CHECK_DELAY_MS = 1500              # 시작 직후 1.5초


class _UpdateCheckSignals(QObject):
    """백그라운드 fetch 결과를 메인 스레드로 안전하게 옮기는 통로."""
    done = pyqtSignal(object)   # ReleaseInfo or None


def _resolve_app_icon() -> QIcon:
    # PyInstaller 번들이면 sys._MEIPASS/assets, 개발 모드면 레포 루트/assets
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        base = Path(meipass) / "assets"
    else:
        base = Path(__file__).resolve().parent.parent.parent / "assets"
    ico = base / "Pinstock.ico"
    if ico.exists():
        return QIcon(str(ico))
    return QIcon()

from ..__version__ import __version__
from ..core import updater
from ..core.autostart import autostart_supported, is_autostart_enabled, set_autostart
from ..core.api import fetch_usd_krw_rate
from ..core.portfolio import is_us_stock, portfolio_totals
from ..core.storage import (
    CONFIG_FILE, BACKUP_FILE,
    export_stocks_to_excel, import_stocks_from_excel, normalize_stocks_schema,
    normalize_watchlist_schema, normalize_tags, prune_watch_tags,
)
from .theme import C, TRAY_MENU_STYLE
from .floating_widget import StockWidget, WatchWidget
from .master_widget import MasterWidget
from .manage_dialog import (
    StockDialog, ManageStocksDialog, ManageWatchlistDialog, ImportModeDialog,
    fetch_quote_for_stock,
)
from ..ui_common.update_dialog import UpdateDialog
from ..ui_common.help_dialog import HelpDialog
from ..ui_common.about_dialog import AboutDialog


# ─── 전체 위젯 관리자 ─────────────────────────────────────────────────────────
class WidgetManager:
    def __init__(self, app: QApplication):
        self.app = app
        self.stocks: list[dict] = []
        self.watchlist: list[dict] = []   # 관심종목 — 보유와 독립된 별도 목록
        self.watch_tags: list[dict] = []  # 관심종목 태그 레지스트리 {id,name,color}
        self.widgets: dict[str, StockWidget] = {}
        self.watch_widgets: dict[str, WatchWidget] = {}   # 관심종목 플로팅 위젯
        self.uniform_w: int = StockWidget.MIN_W
        self.uniform_watch_w: int = WatchWidget.MIN_W   # 관심 위젯 통일 너비 (보유와 별개)
        self.is_hidden: bool = False    # 위젯 전체 숨김 상태
        # 마스터 위젯 (포트폴리오 요약)
        self.master_widget: MasterWidget | None = None
        # 마스터 위젯의 데이터를 표시할지 여부. False 면 위젯은 그대로 화면에 있지만
        # 4지표/종목 손익이 ••••• 로 마스킹된다 (macOS '자산 정보 숨기기' 와 동등).
        # stocks.json 의 master.visible 키와 1:1 매핑 — 기존 사용자 설정 호환을 위해
        # 의미만 재정의하고 키/변수명은 유지한다.
        self.master_visible: bool = True
        self.master_pos: list | None = None   # None → 기본 위치
        # macOS 팝오버에서 쓰는 자산 정보 숨김 / 팝오버 투명도 — Windows 에서는
        # UI 노출은 없고 round-trip 보존만 한다 (한쪽에서 저장하면 다른쪽에서도 유지되도록).
        self.assets_hidden: bool = False
        self.popover_opacity: float = 1.0
        self.usd_krw_rate: float | None = None
        self.market_filter: str = "ALL"

        # 투명도 슬라이더가 멈춘 뒤에만 click-through 토글 + 설정 저장 — 50% 경계를
        # 지날 때 setWindowFlag 로 윈도우가 재생성되며 발생하던 멈칫을 없앤다.
        self._opacity_settle_timer = QTimer()
        self._opacity_settle_timer.setSingleShot(True)
        self._opacity_settle_timer.timeout.connect(self._on_opacity_settle)

        self.fx_timer = QTimer()
        self.fx_timer.timeout.connect(self._fetch_usd_krw_rate)
        self._layout_reflow_pending: bool = False
        # 자동 업데이트 체크 상태
        self.update_last_check_at: datetime | None = None
        self._cached_release: updater.ReleaseInfo | None = None
        self._update_signals = _UpdateCheckSignals()
        self._update_signals.done.connect(self._on_auto_check_done)

        self._load_config()
        self._setup_tray()
        self._spawn_all()
        self._spawn_all_watch()
        self._sync_fx_timer()

        # 시작 직후 — 이전 업데이트 실패 로그가 있으면 안내
        QTimer.singleShot(_PREV_ERROR_CHECK_DELAY_MS, self._check_previous_update_error)
        # 시작 10초 뒤 — 자동 업데이트 체크 (throttle/can_self_update 검사 후 실제 호출)
        QTimer.singleShot(_AUTO_CHECK_STARTUP_DELAY_MS, self._maybe_run_auto_update_check)

    # ── 전체 위젯 표시/숨김 토글 ─────────────────────────────────────────
    def toggle_visibility(self):
        self.is_hidden = not self.is_hidden
        # 표시 복귀 시 종목별 hidden 상태와 시장 필터를 함께 보존
        stock_by_code = {s["code"]: s for s in self.stocks}
        for code, w in self.widgets.items():
            if self.is_hidden:
                w.hide()
            elif self._is_stock_visible(stock_by_code.get(code, {})):
                w.show()
            else:
                w.hide()
        # 관심 위젯도 전체 토글에 함께 따름 (개별 hidden/시장 필터 상태 보존)
        watch_by_code = {it["code"]: it for it in self.watchlist}
        for code, w in self.watch_widgets.items():
            if self.is_hidden:
                w.hide()
            elif self._is_watch_visible(watch_by_code.get(code, {})):
                w.show()
            else:
                w.hide()
        # 마스터 위젯도 전체 토글에 함께 따름. master_visible 은 위젯 표시 여부가
        # 아니라 데이터 마스킹 여부라 여기서는 신경 쓰지 않는다 (마스킹 상태는 별개).
        if self.master_widget:
            if self.is_hidden:
                self.master_widget.hide()
            else:
                self.master_widget.show()
        self.toggle_act.setText("👀   표시하기" if self.is_hidden else "🙈   숨기기")

    # ── 위치 초기화 ───────────────────────────────────────────────────────
    def reset_positions(self):
        """각 위젯을 현재 위치한 모니터의 우상단부터 column-wrap 방식으로 정렬.
        - 첫 column이 화면 세로 영역을 넘어가면 그 왼쪽에 새 column을 시작
        - 마스터 위젯이 표시 중이면 자기 모니터의 우상단 첫 자리에 두고,
          모든 column은 마스터 아래 y부터 시작 (마스터보다 위로는 가지 않음)"""
        MARGIN_X      = 20   # 화면 우측 여백
        MARGIN_Y      = 60   # 화면 상단 여백
        MARGIN_BOTTOM = 20   # 화면 하단 여백 (이 안쪽으로만 위젯 배치)
        GAP           = 4    # 같은 column 내 위젯 간 세로 간격
        COL_GAP       = 8    # column 사이 가로 간격

        # 마스터 위젯이 표시 중인 모니터 파악
        master_screen = None
        master_offset = 0
        if self.master_widget and self.master_widget.isVisible():
            mc = self.master_widget.frameGeometry().center()
            master_screen = QApplication.screenAt(mc) or QApplication.primaryScreen()
            mgeo = master_screen.availableGeometry()
            mx = mgeo.x() + mgeo.width() - self.master_widget.width() - MARGIN_X
            my = mgeo.y() + MARGIN_Y
            self.master_widget.move(mx, my)
            self.master_pos = [mx, my]
            master_offset = self.master_widget.height() + GAP

        # 위젯을 현재 속한 모니터별로 그룹화 (stocks 순서 보존).
        # 숨김(hidden=True) 종목은 자리를 차지하지 않도록 제외 — 빈 슬롯 방지.
        groups: dict = {}
        for s in self.stocks:
            if not self._is_stock_visible(s):
                continue
            w = self.widgets.get(s["code"])
            if not w:
                continue
            center = w.frameGeometry().center()
            screen = QApplication.screenAt(center) or QApplication.primaryScreen()
            groups.setdefault(screen, []).append((s, w))

        widget_w = self.uniform_w

        for screen, items in groups.items():
            geo = screen.availableGeometry()
            col_top_y = geo.y() + MARGIN_Y + (master_offset if screen is master_screen else 0)
            first_col_x = geo.x() + geo.width() - widget_w - MARGIN_X
            bottom_y = geo.y() + geo.height() - MARGIN_BOTTOM
            self._place_widgets_in_columns(
                items, first_col_x, col_top_y, bottom_y, GAP, COL_GAP
            )

        self._save_config()
        # 숨김 상태라면 자동으로 다시 표시
        if self.is_hidden:
            self.toggle_visibility()

    # ── 관심 위젯 위치 초기화 (좌상단) ─────────────────────────────────────
    def reset_watch_positions(self):
        """관심 위젯을 현재 모니터의 좌상단부터 column-wrap(오른쪽으로 확장) 정렬.
        보유 reset_positions() 는 우상단→왼쪽이라 방향이 반대 — 관심은 별도 동작.
        숨김(hidden)·시장 필터로 안 보이는 항목은 자리를 차지하지 않게 제외."""
        MARGIN_X      = 20   # 화면 좌측 여백
        MARGIN_Y      = 60   # 화면 상단 여백
        MARGIN_BOTTOM = 20   # 화면 하단 여백
        GAP           = 4    # 같은 column 내 세로 간격
        COL_GAP       = 8    # column 사이 가로 간격

        # 관심 위젯을 현재 속한 모니터별로 그룹화 (watchlist 순서 보존)
        groups: dict = {}
        for item in self.watchlist:
            if not self._is_watch_visible(item):
                continue
            w = self.watch_widgets.get(item["code"])
            if not w:
                continue
            center = w.frameGeometry().center()
            screen = QApplication.screenAt(center) or QApplication.primaryScreen()
            groups.setdefault(screen, []).append((item, w))

        for screen, items in groups.items():
            geo = screen.availableGeometry()
            col_top_y   = geo.y() + MARGIN_Y
            first_col_x = geo.x() + MARGIN_X
            bottom_y    = geo.y() + geo.height() - MARGIN_BOTTOM
            # direction=+1 → 좌→우로 새 column 진행 (보유는 기본값 -1 로 우→좌)
            self._place_widgets_in_columns(
                items, first_col_x, col_top_y, bottom_y, GAP, COL_GAP, direction=1
            )

        self._save_config()
        # 숨김 상태라면 자동으로 다시 표시
        if self.is_hidden:
            self.toggle_visibility()

    # ── 마스터 화면에 모든 위젯 모으기 ─────────────────────────────────────
    def gather_to_master_screen(self):
        """모든 위젯을 마스터 위젯이 있는 화면으로 끌어모아 column-wrap 정렬.
        멀티 모니터에 분산된 위젯을 한 화면에 모을 때 사용.
        - 마스터 표시 중: 그 모니터 우상단에 마스터를 두고 아래로 column-wrap
        - 마스터 없음/숨김: 주 모니터 우상단부터 column-wrap (fallback)"""
        MARGIN_X      = 20
        MARGIN_Y      = 60
        MARGIN_BOTTOM = 20
        GAP           = 4
        COL_GAP       = 8

        # 대상 화면 결정: 마스터 표시 중이면 그 모니터, 아니면 주 모니터
        master_active = bool(self.master_widget and self.master_widget.isVisible())
        if master_active:
            mc = self.master_widget.frameGeometry().center()
            target_screen = QApplication.screenAt(mc) or QApplication.primaryScreen()
        else:
            target_screen = QApplication.primaryScreen()

        geo = target_screen.availableGeometry()
        widget_w = self.uniform_w

        # 마스터 위젯을 대상 화면 우상단 첫자리에 (표시 중일 때만)
        master_offset = 0
        if master_active:
            mx = geo.x() + geo.width() - self.master_widget.width() - MARGIN_X
            my = geo.y() + MARGIN_Y
            self.master_widget.move(mx, my)
            self.master_pos = [mx, my]
            master_offset = self.master_widget.height() + GAP

        # 표시 종목만 stocks 순서대로 column-wrap 정렬 (숨김은 빈 슬롯 방지를 위해 제외)
        visible_items = [
            (s, self.widgets[s["code"]])
            for s in self.stocks
            if not s.get("hidden", False) and s["code"] in self.widgets
        ]
        col_top_y = geo.y() + MARGIN_Y + master_offset
        bottom_y = geo.y() + geo.height() - MARGIN_BOTTOM
        first_col_x = geo.x() + geo.width() - widget_w - MARGIN_X

        self._place_widgets_in_columns(
            visible_items, first_col_x, col_top_y, bottom_y, GAP, COL_GAP
        )

        self._save_config()
        # 숨김 상태라면 자동으로 다시 표시
        if self.is_hidden:
            self.toggle_visibility()

    # ── 통일 너비 계산/적용 ───────────────────────────────────────────────
    def _calc_uniform_width(self) -> int:
        """모든 종목명 중 가장 긴 이름 기준 통일 너비."""
        w = StockWidget.MIN_W
        for s in self.stocks:
            name = s.get("name", s["code"])
            w = max(w, StockWidget.calc_width_for_name(name))
        return w

    def _apply_uniform_width(self):
        """현재 너비를 재계산해 모든 위젯에 적용."""
        new_w = self._calc_uniform_width()
        if new_w == self.uniform_w:
            return
        self.uniform_w = new_w
        for w in self.widgets.values():
            w.set_width(new_w)
        if self.master_widget:
            self.master_widget.set_uniform_width(new_w)

    def _matches_market_filter(self, stock: dict) -> bool:
        if self.market_filter == "ALL":
            return True
        market = "US" if is_us_stock(stock) else "KR"
        return market == self.market_filter

    def _is_stock_visible(self, stock: dict) -> bool:
        if not stock:
            return False
        return not stock.get("hidden", False) and self._matches_market_filter(stock)

    # ── 관심 위젯 통일 너비 / 표시 여부 (보유와 동일 패턴, 별개 상태) ──────
    def _calc_uniform_watch_width(self) -> int:
        w = WatchWidget.MIN_W
        for item in self.watchlist:
            name = item.get("name", item["code"])
            w = max(w, WatchWidget.calc_width_for_name(name))
        return w

    def _apply_uniform_watch_width(self):
        new_w = self._calc_uniform_watch_width()
        if new_w == self.uniform_watch_w:
            return
        self.uniform_watch_w = new_w
        for w in self.watch_widgets.values():
            w.set_width(new_w)

    def _is_watch_visible(self, item: dict) -> bool:
        # 관심도 보유와 동일하게 시장 필터를 적용 (macOS 팝오버 관심 뷰와 일관)
        if not item:
            return False
        return not item.get("hidden", False) and self._matches_market_filter(item)

    def _default_watch_pos(self, idx: int) -> tuple[int, int]:
        """저장된 pos 가 없을 때의 기본 관심 위젯 위치 — 주 모니터 좌상단 아래로 stack."""
        geo = QApplication.primaryScreen().availableGeometry()
        x = geo.x() + 20
        y = geo.y() + 60 + idx * (WatchWidget.COMPACT_H + 12)
        return x, y

    @staticmethod
    def _place_widgets_in_columns(
        items: list[tuple[dict, StockWidget]],
        first_col_x: int,
        col_top_y: int,
        bottom_y: int,
        gap: int,
        col_gap: int,
        direction: int = -1,
    ):
        """column-wrap 배치. direction=-1 이면 새 column 이 왼쪽으로(보유, 우상단 시작),
        +1 이면 오른쪽으로(관심, 좌상단 시작) 진행한다."""
        x = first_col_x
        y = col_top_y
        for s, w in items:
            h = w.height() or StockWidget.COMPACT_H
            if y > col_top_y and y + h > bottom_y:
                x += direction * (w.width() + col_gap)
                y = col_top_y
            w.move(x, y)
            s["pos"] = [x, y]
            y += h + gap

    def _on_market_filter_changed(self, market: str):
        self.market_filter = market if market in {"ALL", "KR", "US"} else "ALL"
        self._apply_market_filter()
        self._recompute_master()

    def _apply_market_filter(self):
        for s in self.stocks:
            w = self.widgets.get(s["code"])
            if not w:
                continue
            if self.is_hidden or not self._is_stock_visible(s):
                w.hide()
            else:
                w.show()
        # 관심 위젯도 같은 시장 필터를 적용 (제자리 표시/숨김 — 재정렬은 '관심 위치
        # 초기화' 로. 보유처럼 compact 재정렬은 하지 않아 단순함을 유지한다)
        for item in self.watchlist:
            w = self.watch_widgets.get(item["code"])
            if not w:
                continue
            if self.is_hidden or not self._is_watch_visible(item):
                w.hide()
            else:
                w.show()
        self._compact_visible_widgets()
        if self.master_widget:
            self.master_widget.set_market_filter(self.market_filter)
            self.master_widget.sync_aux_windows()

    # ── 트레이 ─────────────────────────────────────────────────────────────
    def _setup_tray(self):
        icon = self._make_tray_icon()
        self.tray = QSystemTrayIcon(icon)
        self.tray.setToolTip("Pinstock")

        menu = QMenu()
        menu.setStyleSheet(TRAY_MENU_STYLE)

        add_act    = QAction("➕   종목 추가",   menu)
        manage_act = QAction("📋   종목 관리",   menu)
        watch_add_act    = QAction("⭐   관심종목 추가", menu)
        watch_manage_act = QAction("⭐   관심종목 관리", menu)
        export_act = QAction("📤   Excel로 내보내기", menu)
        import_act = QAction("📥   Excel에서 가져오기", menu)
        self.toggle_act = QAction("🙈   숨기기", menu)
        self.master_toggle_act = QAction(self._master_toggle_text(), menu)
        reset_act  = QAction("📐   위치 초기화", menu)
        watch_reset_act = QAction("📐   관심 위치 초기화", menu)
        gather_act = QAction("🎯   마스터 화면에 정렬", menu)
        self.autostart_act = QAction("🚀   시작 시 자동 실행", menu)
        self.autostart_act.setCheckable(True)
        help_act   = QAction("❓   도움말",      menu)
        self.about_act = QAction("ℹ️   앱 정보",  menu)
        quit_act   = QAction("❌   종료",        menu)
        add_act.triggered.connect(self.open_add_dialog)
        manage_act.triggered.connect(self.open_manage_dialog)
        watch_add_act.triggered.connect(self.open_add_watch_dialog)
        watch_manage_act.triggered.connect(self.open_manage_watch_dialog)
        export_act.triggered.connect(self.open_export_dialog)
        import_act.triggered.connect(self.open_import_dialog)
        self.toggle_act.triggered.connect(self.toggle_visibility)
        self.master_toggle_act.triggered.connect(self.toggle_master_visibility)
        reset_act.triggered.connect(self.reset_positions)
        watch_reset_act.triggered.connect(self.reset_watch_positions)
        gather_act.triggered.connect(self.gather_to_master_screen)
        self.autostart_act.triggered.connect(self.toggle_autostart)
        help_act.triggered.connect(self.open_help_dialog)
        self.about_act.triggered.connect(self.open_about_dialog)
        quit_act.triggered.connect(self.app.quit)

        menu.addAction(add_act)
        menu.addAction(manage_act)
        menu.addSeparator()
        # 관심종목 — 보유와 구분선으로 분리 (별도 저장·독립 동작)
        menu.addAction(watch_add_act)
        menu.addAction(watch_manage_act)
        menu.addSeparator()
        menu.addAction(export_act)
        menu.addAction(import_act)
        menu.addSeparator()
        menu.addAction(self.toggle_act)
        menu.addAction(self.master_toggle_act)
        menu.addAction(reset_act)
        menu.addAction(watch_reset_act)
        menu.addAction(gather_act)
        if autostart_supported():
            menu.addSeparator()
            self.autostart_act.setChecked(is_autostart_enabled())
            menu.addAction(self.autostart_act)
        menu.addSeparator()
        menu.addAction(help_act)
        menu.addAction(self.about_act)
        menu.addAction(quit_act)

        self.context_menu = menu   # 마스터 위젯 우클릭에서도 같은 메뉴 재사용
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        # 업데이트 토스트 클릭 → 업데이트 다이얼로그
        # (현재 showMessage 는 업데이트 알림 한 종류뿐이라 단일 핸들러로 충분)
        self.tray.messageClicked.connect(self.open_update_dialog)
        self.tray.show()

    def _on_tray_activated(self, reason):
        # 트레이 아이콘 좌클릭(Trigger) 시 표시/숨김 빠른 토글
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_visibility()

    def _show_context_menu(self, global_pos):
        # 마스터 위젯 우클릭 → 트레이와 동일한 컨텍스트 메뉴를 커서 위치에 표시
        self.context_menu.popup(global_pos)

    @staticmethod
    def _make_tray_icon() -> QIcon:
        # assets/Pinstock.ico 를 우선 사용. 못 찾으면 기존 파란 원+₩ 폴백.
        icon = _resolve_app_icon()
        if not icon.isNull():
            return icon
        px = QPixmap(32, 32)
        px.fill(QColor(0, 0, 0, 0))
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(QColor(C["blue"])))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(1, 1, 30, 30)
        p.setFont(QFont("Malgun Gothic",14, QFont.Weight.Bold))
        p.setPen(QPen(QColor(C["bg"])))
        p.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, "₩")
        p.end()
        return QIcon(px)

    # ── 설정 파일 ──────────────────────────────────────────────────────────
    # 스키마 변천:
    #   v1 (구버전): JSON 루트가 list — 종목 dict 의 배열
    #   v2 (현재):   JSON 루트가 dict — {"stocks": [...], "master": {"visible": bool, "pos": [x,y]|null}}
    # 로드는 둘 다 받아주고, 저장은 항상 v2 로 한다 (한 번 저장되면 자동 마이그레이트).
    def _load_config(self):
        if not os.path.exists(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return

        if isinstance(data, list):
            # v1 → 종목만 있음, 마스터 설정은 기본값
            self.stocks = normalize_stocks_schema(data)
        elif isinstance(data, dict):
            self.stocks = normalize_stocks_schema(data.get("stocks", []) or [])
            self.watchlist = normalize_watchlist_schema(data.get("watchlist", []) or [])
            self.watch_tags = normalize_tags(data.get("watch_tags", []) or [])
            prune_watch_tags(self.watchlist, self.watch_tags)
            master = data.get("master") or {}
            self.master_visible = bool(master.get("visible", True))
            pos = master.get("pos")
            if isinstance(pos, list) and len(pos) == 2:
                try:
                    self.master_pos = [int(pos[0]), int(pos[1])]
                except (TypeError, ValueError):
                    self.master_pos = None
            self.assets_hidden = bool(data.get("assets_hidden", False))
            try:
                opacity = float(data.get("popover_opacity", 1.0))
                # Windows 는 10–100% 까지 허용 (macOS 는 자체적으로 60% 미만은 60% 로 clamp).
                self.popover_opacity = max(0.1, min(1.0, opacity))
            except (TypeError, ValueError):
                self.popover_opacity = 1.0
            # 자동 업데이트 메타 — last_check_at 만 (24h throttle 용)
            upd = data.get("update") or {}
            last_at = upd.get("last_check_at")
            if isinstance(last_at, str):
                try:
                    self.update_last_check_at = datetime.fromisoformat(last_at)
                except ValueError:
                    self.update_last_check_at = None

    def _save_config(self):
        self.stocks = normalize_stocks_schema(self.stocks)
        self.watchlist = normalize_watchlist_schema(self.watchlist)
        self.watch_tags = normalize_tags(self.watch_tags)
        data = {
            "stocks": self.stocks,
            "watchlist": self.watchlist,
            "watch_tags": self.watch_tags,
            "master": {
                "visible": self.master_visible,
                "pos": self.master_pos,
            },
            "assets_hidden": self.assets_hidden,
            "popover_opacity": self.popover_opacity,
        }
        if self.update_last_check_at is not None:
            data["update"] = {
                "last_check_at": self.update_last_check_at.isoformat(timespec="seconds"),
            }
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[save] 오류: {e}")

    def save_positions(self):
        for s in self.stocks:
            w = self.widgets.get(s["code"])
            if w:
                pos = w.pos()
                s["pos"] = [pos.x(), pos.y()]
        for item in self.watchlist:
            w = self.watch_widgets.get(item["code"])
            if w:
                pos = w.pos()
                item["pos"] = [pos.x(), pos.y()]
        if self.master_widget:
            mpos = self.master_widget.pos()
            self.master_pos = [mpos.x(), mpos.y()]
        self._save_config()

    # ── 위젯 생성 ──────────────────────────────────────────────────────────
    def _spawn_all(self):
        self.uniform_w = self._calc_uniform_width()
        visible_idx = 0
        for s in self.stocks:
            default_x = 60
            default_y = 60 + visible_idx * (StockWidget.COMPACT_H + 12)
            self._spawn_widget(s, default_x, default_y, stagger_idx=visible_idx)
            if self._is_stock_visible(s):
                visible_idx += 1
        self._sync_fx_timer()
        self._spawn_master()

    # ── 관심 위젯 생성 ─────────────────────────────────────────────────────
    def _spawn_all_watch(self):
        """저장된 관심종목으로 위젯 생성. 저장된 pos 사용, 없으면 좌상단 기본 배치.
        stagger 는 0부터 — 보유와 독립적으로 분산(macOS 와 동일)."""
        self.uniform_watch_w = self._calc_uniform_watch_width()
        visible_idx = 0
        for i, item in enumerate(self.watchlist):
            nx, ny = self._default_watch_pos(visible_idx)
            self._spawn_watch_widget(item, nx, ny, stagger_idx=i)
            if not item.get("hidden", False):
                visible_idx += 1

    def _spawn_watch_widget(self, item: dict, def_x=20, def_y=60, stagger_idx: int = 0):
        code = item["code"]
        w = WatchWidget(item, width=self.uniform_watch_w, stagger_idx=stagger_idx)
        w.deleted.connect(self._on_watch_delete)
        pos = item.get("pos", [def_x, def_y])
        w.move(pos[0], pos[1])
        # 보유 위젯과 동일한 투명도 / 클릭 통과 정책 적용
        w.setWindowOpacity(self.popover_opacity)
        if self._is_click_through_opacity(self.popover_opacity):
            w.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        if not self.is_hidden and self._is_watch_visible(item):
            w.show()
        self.watch_widgets[code] = w

    def _on_watch_delete(self, code: str):
        """관심 위젯 우클릭 삭제 — watchlist 에서 제거 + 저장 + 너비 재계산."""
        self.watchlist = [w for w in self.watchlist if w["code"] != code]
        self.watch_widgets.pop(code, None)
        self._save_config()
        self._apply_uniform_watch_width()

    def _compact_visible_widgets(self):
        if not self.widgets:
            return
        visible = [s for s in self.stocks if self._is_stock_visible(s)]
        if not visible:
            return
        anchor_widget = None
        for s in visible:
            w = self.widgets.get(s["code"])
            if w:
                anchor_widget = w
                break
        anchor_pos = anchor_widget.pos() if anchor_widget else None
        anchor_screen = (
            QApplication.screenAt(anchor_widget.frameGeometry().center())
            if anchor_widget else None
        )
        base_x = anchor_pos.x() if anchor_pos else 60
        base_y = anchor_pos.y() if anchor_pos else 60
        top_y = None
        for s in self.stocks:
            if s.get("hidden", False):
                continue
            w = self.widgets.get(s["code"])
            if not w:
                continue
            screen = QApplication.screenAt(w.frameGeometry().center())
            if anchor_screen is not None and screen is not anchor_screen:
                continue
            y = w.pos().y()
            top_y = y if top_y is None else min(top_y, y)
        if top_y is not None:
            base_y = top_y
        # GAP은 reset_positions()의 같은 column 내 세로 간격(4)과 일치해야
        # 필터 변경 후에도 위치 초기화로 맞춘 간격이 유지된다.
        gap = 4
        y = base_y
        for s in visible:
            w = self.widgets.get(s["code"])
            if not w:
                continue
            x = base_x
            w.move(x, y)
            s["pos"] = [x, y]
            y += w.height() + gap

    def _schedule_visible_widgets_reflow(self):
        """프리/애프터 가격 표시 여부로 위젯 높이가 바뀌면 현재 컬럼을 재정렬한다."""
        if self._layout_reflow_pending:
            return
        self._layout_reflow_pending = True
        QTimer.singleShot(0, self._reflow_visible_widgets)

    def _reflow_visible_widgets(self):
        self._layout_reflow_pending = False
        if self.is_hidden or not self.widgets:
            return

        GAP = 4
        COLUMN_TOLERANCE = 12

        groups: dict = {}
        for s in self.stocks:
            if not self._is_stock_visible(s):
                continue
            w = self.widgets.get(s["code"])
            if not w or not w.isVisible():
                continue
            screen = QApplication.screenAt(w.frameGeometry().center()) or QApplication.primaryScreen()
            groups.setdefault(screen, []).append((s, w))

        for _screen, items in groups.items():
            columns: list[list[tuple[dict, StockWidget]]] = []
            column_xs: list[int] = []

            for s, w in sorted(items, key=lambda item: (item[1].x(), item[1].y())):
                matched_idx = None
                for i, x in enumerate(column_xs):
                    if abs(w.x() - x) <= COLUMN_TOLERANCE:
                        matched_idx = i
                        break
                if matched_idx is None:
                    column_xs.append(w.x())
                    columns.append([])
                    matched_idx = len(columns) - 1
                columns[matched_idx].append((s, w))

            for column in columns:
                column.sort(key=lambda item: item[1].y())
                if not column:
                    continue
                x = column[0][1].x()
                y = min(w.y() for _s, w in column)
                for s, w in column:
                    w.move(x, y)
                    s["pos"] = [x, y]
                    y += w.height() + GAP

        self._save_config()

    def _spawn_widget(self, stock: dict, def_x=60, def_y=60, stagger_idx: int = 0):
        code = stock["code"]
        w = StockWidget(stock, width=self.uniform_w, stagger_idx=stagger_idx)
        w.deleted.connect(self._on_delete)
        w.edited.connect(self._on_edited)
        w.price_updated.connect(lambda _: self._recompute_master())
        w.layout_changed.connect(lambda _: self._schedule_visible_widgets_reflow())
        w.set_usd_krw_rate(self.usd_krw_rate)

        pos = stock.get("pos", [def_x, def_y])
        w.move(pos[0], pos[1])
        w.setWindowOpacity(self.popover_opacity)
        # 투명도 50% 이하면 클릭이 통과되는 모드로 (show 전이라 flag 만 set 해두면 됨)
        if self._is_click_through_opacity(self.popover_opacity):
            w.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        # 종목별 hidden 표시 + 시장 필터 + 전체 숨김 상태를 함께 고려
        if self._is_stock_visible(stock) and not self.is_hidden:
            w.show()
        self.widgets[code] = w

    def _on_edited(self, _code: str):
        """개별 위젯에서 평단가/수량을 수정한 경우. 저장 + 마스터 갱신."""
        self._save_config()
        self._recompute_master()

    # ── 마스터 위젯 생성/표시 ─────────────────────────────────────────────
    def _spawn_master(self):
        if self.master_widget is None:
            self.master_widget = MasterWidget(width=self.uniform_w)
            self.master_widget.set_opacity(self.popover_opacity)
            self.master_widget.opacity_changed.connect(self._on_opacity_changed)
            self.master_widget.market_filter_changed.connect(self._on_market_filter_changed)
            self.master_widget.context_menu_requested.connect(self._show_context_menu)
            self.master_widget.set_market_filter(self.market_filter)
            # 시작 시 저장된 투명도가 임계치 이하면 show 전에 미리 click-through 활성화
            # (슬라이더는 별도 윈도우라 영향 없음).
            if self._is_click_through_opacity(self.popover_opacity):
                self.master_widget.setWindowFlag(
                    Qt.WindowType.WindowTransparentForInput, True
                )

        # 위치: 저장된 위치가 있으면 사용, 없으면 종목 위젯들 위에 적당히 둠
        if self.master_pos:
            self.master_widget.move(self.master_pos[0], self.master_pos[1])
        else:
            self.master_widget.move(60, 20)

        # 마스터 위젯은 전체 숨김이 아닌 한 항상 보이고, master_visible 은
        # 데이터 마스킹 여부로만 작용.
        if self.is_hidden:
            self.master_widget.hide()
        else:
            self.master_widget.show()
        self.master_widget.set_assets_hidden(not self.master_visible)

        # 마스터 자체에 저장된 투명도 적용 (set_opacity는 슬라이더만 동기화함)
        self.master_widget.setWindowOpacity(self.popover_opacity)

        # 초기 표시: 현재가 아직 없으면 0/─ 으로 둠 → 30초 이내 자동 갱신
        self._recompute_master()

    # ── 투명도 동기화 ─────────────────────────────────────────────────────
    # 이 임계값 이하면 종목 위젯이 클릭 통과 모드로 (MasterWidget.LOCK_THRESHOLD 와 일치).
    CLICK_THROUGH_OPACITY = 0.5

    def _is_click_through_opacity(self, opacity: float) -> bool:
        return opacity <= self.CLICK_THROUGH_OPACITY

    def _apply_opacity_to_all(self, opacity: float):
        """마스터 + 모든 종목/관심 위젯에 동일 투명도 적용."""
        if self.master_widget:
            self.master_widget.setWindowOpacity(opacity)
        for w in self.widgets.values():
            w.setWindowOpacity(opacity)
        for w in self.watch_widgets.values():
            w.setWindowOpacity(opacity)

    def _apply_click_through(self, opacity: float):
        """종목 위젯 + 마스터 카드에 OS-레벨 click-through 토글.
        슬라이더는 별도 top-level 윈도우라 마스터가 통과 상태여도 그대로 조작 가능,
        자물쇠 오버레이는 항상 WindowTransparentForInput 라 변동 없음."""
        enabled = self._is_click_through_opacity(opacity)
        flag = Qt.WindowType.WindowTransparentForInput

        targets = list(self.widgets.values()) + list(self.watch_widgets.values())
        if self.master_widget:
            targets.append(self.master_widget)

        for w in targets:
            if bool(w.windowFlags() & flag) == enabled:
                continue
            # 플래그 변경은 윈도우를 재생성하므로 위치/표시를 복원해줘야 한다.
            was_visible = w.isVisible()
            pos = w.pos()
            w.setWindowFlag(flag, enabled)
            w.move(pos)
            if was_visible:
                w.show()

    def _on_opacity_changed(self, opacity: float):
        self.popover_opacity = opacity
        # 투명도 자체는 즉시 반영 (가벼움).
        self._apply_opacity_to_all(opacity)
        # click-through 토글(setWindowFlag 로 윈도우 재생성)과 디스크 저장은
        # 슬라이더가 멈춘 뒤로 미뤄 50% 경계에서의 멈칫을 제거.
        self._opacity_settle_timer.start(180)

    def _on_opacity_settle(self):
        self._apply_click_through(self.popover_opacity)
        self._save_config()

    def _master_toggle_text(self) -> str:
        return "💰   자산 숨기기" if self.master_visible else "💰   자산 표시하기"

    def toggle_master_visibility(self):
        """마스터 위젯의 자산 데이터 표시 ↔ 마스킹 토글. 위젯 자체는 그대로 둔다
        (macOS '자산 정보 숨기기' 와 같은 동작)."""
        self.master_visible = not self.master_visible
        if self.master_widget:
            self.master_widget.set_assets_hidden(not self.master_visible)
        self.master_toggle_act.setText(self._master_toggle_text())
        self._save_config()

    def toggle_autostart(self):
        """시스템 시작(로그인) 시 자동 실행 등록/해제. 실제 반영된 상태로
        체크박스를 동기화한다 (레지스트리 쓰기 실패 시 원복)."""
        desired = self.autostart_act.isChecked()
        applied = set_autostart(desired)
        self.autostart_act.setChecked(applied)

    def _recompute_master(self):
        """모든 종목 위젯의 current_price 를 모아 마스터 4지표 및 보유 종목 상세를 갱신."""
        if not self.master_widget:
            return
        if not self.stocks:
            self.master_widget.clear_metrics()
            return

        current_prices = {
            code: w.current_price
            for code, w in self.widgets.items()
            if w.current_price
        }
        totals = portfolio_totals(
            [s for s in self.stocks if self._matches_market_filter(s)],
            current_prices=current_prices,
            usd_krw_rate=self.usd_krw_rate,
        )
        self.master_widget.update_metrics(totals["total_invest"], totals["total_eval"])
        self.master_widget.update_holdings(totals["holdings"])

    def _fetch_usd_krw_rate(self):
        result = fetch_usd_krw_rate()
        if not result:
            return
        self.usd_krw_rate = float(result["rate"])
        for w in self.widgets.values():
            w.set_usd_krw_rate(self.usd_krw_rate)
        self._recompute_master()

    def _sync_fx_timer(self):
        if any(is_us_stock(s) for s in self.stocks):
            if not self.fx_timer.isActive():
                self.fx_timer.start(60_000)
            if self.usd_krw_rate is None:
                self._fetch_usd_krw_rate()
        else:
            self.fx_timer.stop()
            self.usd_krw_rate = None
            for w in self.widgets.values():
                w.set_usd_krw_rate(None)

    # ── 종목 추가 ──────────────────────────────────────────────────────────
    def open_add_dialog(self):
        dlg = StockDialog()
        if not dlg.exec():
            return
        d = dlg.get_data()
        code = d["code"]

        if not code:
            return
        if code in self.widgets:
            QMessageBox.information(None, "알림", f"'{code}'는 이미 추가되어 있습니다.")
            return

        # 종목명 미리 조회
        result = fetch_quote_for_stock(d)
        if not result:
            QMessageBox.warning(None, "조회 실패", f"종목코드 '{code}'를 찾을 수 없습니다.\n코드를 다시 확인해 주세요.")
            return

        d["name"] = result["name"]
        self.stocks.append(d)
        self._save_config()
        self._sync_fx_timer()

        # 새 종목명이 더 길면 모든 위젯 너비 재조정 (새 위젯도 이 값으로 생성됨)
        self._apply_uniform_width()

        # 새 위젯 위치: 현재 표시 필터에서 보이는 위젯들 아래.
        visible_count = sum(
            1 for s in self.stocks
            if s["code"] != code and self._is_stock_visible(s)
        )
        ny = 60 + visible_count * (StockWidget.COMPACT_H + 12)
        self._spawn_widget(d, 60, ny, stagger_idx=0)

        self._recompute_master()

        # 숨김 상태에서 새 종목을 추가한 경우 자동으로 표시 상태로 전환
        if self.is_hidden:
            self.toggle_visibility()

    # ── 관심종목 추가 ──────────────────────────────────────────────────────
    def open_add_watch_dialog(self):
        """관심종목 추가 — 보유와 독립. 평단가/수량 없이 코드·시장만 받는다.
        같은 종목이 보유에 있어도 관심에 따로 추가 가능(중복 검사는 관심목록 안에서만)."""
        dlg = StockDialog(watch_mode=True)
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
            QMessageBox.warning(None, "조회 실패", f"종목코드 '{code}'를 찾을 수 없습니다.\n코드를 다시 확인해 주세요.")
            return

        d["name"] = result["name"]
        self.watchlist.append(d)
        self._save_config()
        # 새 관심종목명이 더 길면 관심 위젯 너비 재조정 (새 위젯도 이 값으로 생성됨)
        self._apply_uniform_watch_width()

        # 새 위젯 위치: 현재 보이는 관심 위젯들 아래(좌상단 stack)
        visible_count = sum(
            1 for w in self.watchlist
            if w["code"] != code and self._is_watch_visible(w)
        )
        nx, ny = self._default_watch_pos(visible_count)
        self._spawn_watch_widget(d, nx, ny, stagger_idx=0)

        # 숨김 상태에서 추가한 경우 자동으로 표시 상태로 전환
        if self.is_hidden:
            self.toggle_visibility()

    # ── 관심종목 일괄 관리 ──────────────────────────────────────────────────
    def open_manage_watch_dialog(self):
        """관심종목 일괄 관리 — 추가/삭제/표시 토글. old/new diff 로 위젯 추가·삭제."""
        dlg = ManageWatchlistDialog(
            watchlist=copy.deepcopy(self.watchlist),
            tags=copy.deepcopy(self.watch_tags),
        )
        if not dlg.exec():
            return
        new_items = normalize_watchlist_schema(dlg.get_watchlist())
        self.watch_tags = normalize_tags(dlg.get_tags())
        prune_watch_tags(new_items, self.watch_tags)
        old_map = {w["code"]: w for w in self.watchlist}
        new_map = {w["code"]: w for w in new_items}

        # 삭제된 관심종목: 위젯 닫고 제거
        for code in list(old_map):
            if code not in new_map:
                w = self.watch_widgets.pop(code, None)
                if w:
                    w.close()

        # 추가된 관심종목: 위젯 생성 (좌상단 stack)
        base = sum(
            1 for it in new_items
            if it["code"] in old_map and self._is_watch_visible(it)
        )
        added_idx = 0
        for item in new_items:
            if item["code"] not in old_map:
                nx, ny = self._default_watch_pos(base + added_idx)
                self._spawn_watch_widget(item, nx, ny, stagger_idx=added_idx)
                added_idx += 1

        # 기존 관심종목: hidden(표시) 변경 반영
        for item in new_items:
            code = item["code"]
            if code in old_map and code in self.watch_widgets:
                w = self.watch_widgets[code]
                w.data.update(item)
                if self.is_hidden or not self._is_watch_visible(item):
                    w.hide()
                else:
                    w.show()

        self.watchlist = new_items
        self._apply_uniform_watch_width()
        self._save_config()

        # 숨김 상태에서 변경한 경우 자동으로 표시 상태로 전환
        if self.is_hidden and self.watch_widgets:
            self.toggle_visibility()

    # ── 도움말 / 앱 정보 ──────────────────────────────────────────────────
    def open_help_dialog(self):
        HelpDialog().exec()

    def open_about_dialog(self):
        # 업데이트 확인은 About 다이얼로그 내부 버튼에서 트리거 — manager 의
        # 캐시/throttle 흐름을 그대로 재사용하도록 콜백을 그쪽으로 전달한다.
        # 개발 빌드(0.0.0+dev)에서도 콜백을 넘긴다 — UpdateDialog 가 내부에서
        # can_self_update() 를 검사해 다운로드 버튼 대신 '릴리즈 페이지 열기'
        # 로 폴백하므로, 사용자는 새 버전 확인 자체는 가능하다.
        AboutDialog(
            on_check_update=self.open_update_dialog,
            has_update=self._has_pending_update(),
        ).exec()

    # ── 업데이트 확인 ─────────────────────────────────────────────────────
    def open_update_dialog(self):
        # 수동 체크 — 다이얼로그가 fetch 한 결과를 manager 캐시에도 반영해서
        # 트레이 뱃지/throttle 이 즉시 갱신되도록 콜백 전달
        dlg = UpdateDialog(on_release_seen=self._on_release_seen)
        dlg.exec()

    def _on_release_seen(self, release: updater.ReleaseInfo):
        """UpdateDialog 가 API 조회에 성공했을 때 호출."""
        self.update_last_check_at = datetime.now()
        self._cached_release = release
        self._save_config()
        self._refresh_update_badge()

    def _maybe_run_auto_update_check(self):
        """시작 시/주기적 체크 진입점. throttle + can_self_update 검사."""
        if not updater.can_self_update():
            return
        now = datetime.now()
        if (
            self.update_last_check_at is not None
            and (now - self.update_last_check_at) < _AUTO_CHECK_INTERVAL
        ):
            return
        # 백그라운드 fetch
        def worker():
            rel = updater.fetch_latest_release()
            self._update_signals.done.emit(rel)
        threading.Thread(target=worker, daemon=True).start()

    def _on_auto_check_done(self, release):
        """백그라운드 fetch 완료 — 메인 스레드에서 호출됨."""
        if release is None:
            # 실패는 silent. last_check_at 갱신 안 함 → 다음 실행 때 재시도.
            return
        self.update_last_check_at = datetime.now()
        self._cached_release = release
        self._save_config()
        self._refresh_update_badge()
        if updater.is_newer(__version__, release.version):
            self._show_update_toast(release)

    def _refresh_update_badge(self):
        """트레이 메뉴의 '앱 정보' 액션 텍스트에 새 버전 표시 점 토글.
        업데이트 확인 진입점은 About 다이얼로그 내부 버튼이므로, 배지도 그 진입점인
        '앱 정보' 항목에 표시한다."""
        if not hasattr(self, "about_act"):
            return
        suffix = "  ●" if self._has_pending_update() else ""
        self.about_act.setText("ℹ️   앱 정보" + suffix)

    def _has_pending_update(self) -> bool:
        return (
            self._cached_release is not None
            and updater.is_newer(__version__, self._cached_release.version)
        )

    def _show_update_toast(self, release: updater.ReleaseInfo):
        """Windows 트레이 토스트 — 클릭하면 messageClicked → open_update_dialog."""
        self.tray.showMessage(
            "Pinstock 업데이트 가능",
            f"새 버전 {release.tag} 가 있습니다. 클릭하여 확인하세요.",
            QSystemTrayIcon.MessageIcon.Information,
            7000,
        )

    def _check_previous_update_error(self):
        """이전 실행에서 헬퍼가 남긴 에러 로그가 있으면 사용자에게 한 번 보여주고 삭제."""
        log = updater.read_and_clear_last_error()
        if not log:
            return
        QMessageBox.warning(
            None,
            "이전 업데이트 실패",
            updater.humanize_error(log) + "\n\n오류 원문:\n" + log,
        )

    # ── 종목 일괄 관리 ────────────────────────────────────────────────────
    def open_manage_dialog(self):
        # 평가손익 계산용 현재가 스냅샷
        current_prices = {
            code: w.current_price
            for code, w in self.widgets.items()
            if w.current_price
        }
        dlg = ManageStocksDialog(
            stocks=copy.deepcopy(self.stocks),
            current_prices=current_prices,
            usd_krw_rate=self.usd_krw_rate,
        )
        if not dlg.exec():
            return
        new_stocks = dlg.get_stocks()
        new_stocks = normalize_stocks_schema(new_stocks)

        old_map = {s["code"]: s for s in self.stocks}
        new_map = {s["code"]: s for s in new_stocks}

        # 삭제된 종목: 위젯 닫고 제거
        for code in list(old_map):
            if code not in new_map:
                w = self.widgets.pop(code, None)
                if w:
                    w.close()

        # 추가된 종목: 위젯 생성 (기본 위치) — 다수 추가 시 stagger로 분산
        added_idx = 0
        for s in new_stocks:
            if s["code"] not in old_map:
                visible_count = sum(
                    1 for stock in new_stocks
                    if stock["code"] in self.widgets and self._is_stock_visible(stock)
                )
                ny = 60 + visible_count * (StockWidget.COMPACT_H + 12)
                self._spawn_widget(s, 60, ny, stagger_idx=added_idx)
                added_idx += 1

        # 기존 종목: 평단가/수량/hidden 변경 반영
        for s in new_stocks:
            code = s["code"]
            if code in old_map and code in self.widgets:
                w = self.widgets[code]
                w.data.update(s)
                if w.current_price:
                    w._update_detail(w.current_price)
                # hidden 상태와 현재 시장 필터를 함께 반영
                if self.is_hidden or not self._is_stock_visible(s):
                    w.hide()
                else:
                    w.show()

        # 순서 + 저장 + 너비 재계산
        self.stocks = new_stocks
        self._sync_fx_timer()
        self._apply_uniform_width()
        self._apply_market_filter()
        self._save_config()
        self._recompute_master()

        # 숨김 상태에서 변경된 종목이 있으면 자동으로 표시 상태로 전환
        if self.is_hidden and self.widgets:
            self.toggle_visibility()

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

        # 마스터 위젯과 동일한 4지표를 시트 하단에 포함시키기 위해 현재가 dict 전달
        current_prices = {
            code: w.current_price
            for code, w in self.widgets.items()
            if w.current_price
        }

        try:
            export_stocks_to_excel(self.stocks, path, current_prices, self.usd_krw_rate)
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

        # 모드 선택
        mode_dlg = ImportModeDialog()
        if not mode_dlg.exec():
            return
        mode = mode_dlg.mode

        # 미리보기 / 최종 확인
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

        # 적용 직전에 위치 저장 (병합 모드에서 기존 위치 보존하려면 최신 좌표가 필요)
        self.save_positions()

        # stocks.json 백업
        if os.path.exists(CONFIG_FILE):
            try:
                shutil.copy2(CONFIG_FILE, BACKUP_FILE)
            except Exception as e:
                print(f"[backup] 오류: {e}")

        # 새 stocks 리스트 구성
        if mode == "overwrite":
            new_stocks = normalize_stocks_schema(imported)   # pos 없음 → 다시 spawn 시 기본 위치
        else:
            by_code = {s["code"]: s for s in self.stocks}
            new_stocks = []
            for s in imported:
                # 기존 항목이 있으면 pos 등 부가 정보 보존
                base = dict(by_code.get(s["code"], {}))
                base.update(s)   # 평단가/수량/이름은 Excel 값으로 갱신
                new_stocks.append(base)
            # Excel 에 없는 기존 종목은 뒤에 그대로 유지
            imported_codes = {s["code"] for s in imported}
            for s in self.stocks:
                if s["code"] not in imported_codes:
                    new_stocks.append(s)
            new_stocks = normalize_stocks_schema(new_stocks)

        self._rebuild_widgets(new_stocks)

        QMessageBox.information(
            None, "가져오기 완료",
            f"총 {len(new_stocks)}개 종목이 적용되었습니다.\n"
            f"이전 데이터는 다음에 백업되었습니다:\n{BACKUP_FILE}"
        )

    # ── 종목 리스트 전체 교체 후 위젯 재구성 ─────────────────────────────
    def _rebuild_widgets(self, new_stocks: list[dict]):
        """기존 위젯을 모두 닫고 new_stocks 기준으로 위젯을 다시 생성한다."""
        for w in list(self.widgets.values()):
            w.close()
        self.widgets.clear()

        self.stocks = normalize_stocks_schema(new_stocks)
        self.uniform_w = self._calc_uniform_width()

        for i, s in enumerate(self.stocks):
            default_x = 60
            default_y = 60 + i * (StockWidget.COMPACT_H + 12)
            self._spawn_widget(s, default_x, default_y, stagger_idx=i)

        # 마스터 위젯도 새 너비에 맞춰 갱신
        if self.master_widget:
            self.master_widget.set_uniform_width(self.uniform_w)

        self._save_config()
        self._recompute_master()

        # 위치 정보가 없는 종목들이 있으면 자동으로 정렬
        if any("pos" not in s for s in self.stocks):
            self.reset_positions()

        if self.is_hidden and self.widgets:
            self.toggle_visibility()

    # ── 종목 삭제 ──────────────────────────────────────────────────────────
    def _on_delete(self, code: str):
        self.stocks = [s for s in self.stocks if s["code"] != code]
        self.widgets.pop(code, None)
        self._save_config()
        self._sync_fx_timer()
        # 가장 긴 종목이 삭제된 경우 남은 위젯들도 줄어들도록
        self._apply_uniform_width()
        self._recompute_master()
