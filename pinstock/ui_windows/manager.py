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
from ..core.api import fetch_stock
from ..core.storage import (
    CONFIG_FILE, BACKUP_FILE,
    export_stocks_to_excel, import_stocks_from_excel,
)
from .theme import C, TRAY_MENU_STYLE
from .floating_widget import StockWidget
from .master_widget import MasterWidget
from .toggle_button import ToggleButton
from .manage_dialog import StockDialog, ManageStocksDialog, ImportModeDialog
from .update_dialog import UpdateDialog


# ─── 전체 위젯 관리자 ─────────────────────────────────────────────────────────
class WidgetManager:
    def __init__(self, app: QApplication):
        self.app = app
        self.stocks: list[dict] = []
        self.widgets: dict[str, StockWidget] = {}
        self.uniform_w: int = StockWidget.MIN_W
        self.is_hidden: bool = False    # 위젯 전체 숨김 상태
        # 마스터 위젯 (포트폴리오 요약)
        self.master_widget: MasterWidget | None = None
        self.master_visible: bool = True
        self.master_pos: list | None = None   # None → 기본 위치
        # 토글 버튼 (몰컴 모드용 빠른 숨기기/표시)
        self.hide_all_btn: ToggleButton | None = None
        self.hide_master_btn: ToggleButton | None = None
        self.hide_all_btn_pos: list | None = None
        self.hide_master_btn_pos: list | None = None
        # macOS 팝오버에서 쓰는 자산 정보 숨김 / 팝오버 투명도 — Windows 에서는
        # UI 노출은 없고 round-trip 보존만 한다 (한쪽에서 저장하면 다른쪽에서도 유지되도록).
        self.assets_hidden: bool = False
        self.popover_opacity: float = 1.0

        # 투명도 슬라이더가 멈춘 뒤에만 click-through 토글 + 설정 저장 — 50% 경계를
        # 지날 때 setWindowFlag 로 윈도우가 재생성되며 발생하던 멈칫을 없앤다.
        self._opacity_settle_timer = QTimer()
        self._opacity_settle_timer.setSingleShot(True)
        self._opacity_settle_timer.timeout.connect(self._on_opacity_settle)

        # 자동 업데이트 체크 상태
        self.update_last_check_at: datetime | None = None
        self._cached_release: updater.ReleaseInfo | None = None
        self._update_signals = _UpdateCheckSignals()
        self._update_signals.done.connect(self._on_auto_check_done)

        self._load_config()
        self._setup_tray()
        self._spawn_all()

        # 시작 직후 — 이전 업데이트 실패 로그가 있으면 안내
        QTimer.singleShot(_PREV_ERROR_CHECK_DELAY_MS, self._check_previous_update_error)
        # 시작 10초 뒤 — 자동 업데이트 체크 (throttle/can_self_update 검사 후 실제 호출)
        QTimer.singleShot(_AUTO_CHECK_STARTUP_DELAY_MS, self._maybe_run_auto_update_check)

    # ── 전체 위젯 표시/숨김 토글 ─────────────────────────────────────────
    def toggle_visibility(self):
        self.is_hidden = not self.is_hidden
        # 표시 복귀 시 종목별 hidden 상태는 보존 (hidden=True 종목은 계속 숨김)
        hidden_by_code = {s["code"]: bool(s.get("hidden", False)) for s in self.stocks}
        for code, w in self.widgets.items():
            if self.is_hidden:
                w.hide()
            elif not hidden_by_code.get(code, False):
                w.show()
        # 마스터 위젯도 전체 토글에 함께 따름. 단, 마스터 개별 숨김 상태는 보존.
        if self.master_widget:
            if self.is_hidden:
                self.master_widget.hide()
            elif self.master_visible:
                self.master_widget.show()
        # 토글 버튼도 함께 표시/숨김 (다시 켜기는 트레이로만 가능)
        if self.hide_all_btn:
            self.hide_all_btn.hide() if self.is_hidden else self.hide_all_btn.show()
        if self.hide_master_btn:
            show_master_btn = self.master_visible and not self.is_hidden
            self.hide_master_btn.show() if show_master_btn else self.hide_master_btn.hide()
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
            if s.get("hidden", False):
                continue
            w = self.widgets.get(s["code"])
            if not w:
                continue
            center = w.frameGeometry().center()
            screen = QApplication.screenAt(center) or QApplication.primaryScreen()
            groups.setdefault(screen, []).append((s, w))

        widget_w = self.uniform_w
        step_y   = StockWidget.COMPACT_H + GAP

        for screen, items in groups.items():
            geo = screen.availableGeometry()
            col_top_y = geo.y() + MARGIN_Y + (master_offset if screen is master_screen else 0)
            # 한 column에 들어가는 위젯 수 (하단 여백까지 고려)
            avail_h = geo.y() + geo.height() - MARGIN_BOTTOM - col_top_y
            max_per_col = max(1, avail_h // step_y)

            first_col_x = geo.x() + geo.width() - widget_w - MARGIN_X
            for i, (s, w) in enumerate(items):
                col_idx = i // max_per_col
                row_idx = i %  max_per_col
                x = first_col_x - col_idx * (widget_w + COL_GAP)
                y = col_top_y + row_idx * step_y
                w.move(x, y)
                s["pos"] = [x, y]

        # 토글 버튼 위치:
        # 1) 마스터 표시 중 → 마스터 왼쪽에 위/아래
        # 2) 마스터 없거나 숨김 + 표시 종목 있음 → 마지막 표시 종목 위젯 왼쪽에 위/아래
        # 3) 둘 다 없음 → 화면 우상단 fallback
        btn_size = ToggleButton.SIZE
        last_visible = next(
            (s for s in reversed(self.stocks) if not s.get("hidden", False)),
            None,
        )
        if self.master_widget and self.master_widget.isVisible():
            btn_x = mx - btn_size - GAP
            top_y = my
            bot_y = my + btn_size + GAP
        elif last_visible:
            # 마지막 표시 종목 위젯 위치 (방금 위에서 pos 가 갱신됨)
            last_pos = last_visible.get("pos") or [0, 0]
            btn_x = last_pos[0] - btn_size - GAP
            top_y = last_pos[1]
            bot_y = top_y + btn_size + GAP
        else:
            primary = QApplication.primaryScreen()
            pgeo = primary.availableGeometry()
            btn_x = pgeo.x() + pgeo.width() - btn_size - MARGIN_X
            top_y = pgeo.y() + MARGIN_Y
            bot_y = top_y + btn_size + GAP

        if self.hide_all_btn:
            self.hide_all_btn.move(btn_x, top_y)
            self.hide_all_btn_pos = [btn_x, top_y]
            # 전체 토글은 항상 보임 (전체 숨김 상태가 아닌 한)
            if not self.is_hidden:
                self.hide_all_btn.show()
        if self.hide_master_btn:
            self.hide_master_btn.move(btn_x, bot_y)
            self.hide_master_btn_pos = [btn_x, bot_y]
            # 마스터 토글은 마스터 표시 상태에 따름
            if self.master_visible and not self.is_hidden:
                self.hide_master_btn.show()
            else:
                self.hide_master_btn.hide()

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
        mx = my = None
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
        step_y    = StockWidget.COMPACT_H + GAP
        avail_h   = geo.y() + geo.height() - MARGIN_BOTTOM - col_top_y
        max_per_col = max(1, avail_h // step_y)
        first_col_x = geo.x() + geo.width() - widget_w - MARGIN_X

        for i, (s, w) in enumerate(visible_items):
            col_idx = i // max_per_col
            row_idx = i %  max_per_col
            x = first_col_x - col_idx * (widget_w + COL_GAP)
            y = col_top_y + row_idx * step_y
            w.move(x, y)
            s["pos"] = [x, y]

        # 토글 버튼: 마스터 옆 > 마지막 표시 종목 옆 > 대상 화면 우상단 fallback
        btn_size = ToggleButton.SIZE
        last_visible = next(
            (s for s in reversed(self.stocks) if not s.get("hidden", False)),
            None,
        )
        if master_active:
            btn_x = mx - btn_size - GAP
            top_y = my
            bot_y = my + btn_size + GAP
        elif last_visible:
            last_pos = last_visible.get("pos") or [0, 0]
            btn_x = last_pos[0] - btn_size - GAP
            top_y = last_pos[1]
            bot_y = top_y + btn_size + GAP
        else:
            btn_x = geo.x() + geo.width() - btn_size - MARGIN_X
            top_y = geo.y() + MARGIN_Y
            bot_y = top_y + btn_size + GAP

        if self.hide_all_btn:
            self.hide_all_btn.move(btn_x, top_y)
            self.hide_all_btn_pos = [btn_x, top_y]
            if not self.is_hidden:
                self.hide_all_btn.show()
        if self.hide_master_btn:
            self.hide_master_btn.move(btn_x, bot_y)
            self.hide_master_btn_pos = [btn_x, bot_y]
            if self.master_visible and not self.is_hidden:
                self.hide_master_btn.show()
            else:
                self.hide_master_btn.hide()

        self._save_config()
        # 숨김 상태라면 자동으로 다시 표시
        if self.is_hidden:
            self.toggle_visibility()

    # ── 숨김 해제 종목 맨 밑 배치 ─────────────────────────────────────────
    def _append_below_last_visible(self, newly_visible: list[dict]):
        """hidden=True → False 로 막 전환된 종목들을, 현재 표시 중인 마지막
        위젯 아래(같은 column → 화면 밑단 넘으면 왼쪽 새 column)에 순서대로 배치.
        기존 표시 위젯이 하나도 없으면 reset_positions 로 fallback."""
        MARGIN_Y      = 60
        MARGIN_BOTTOM = 20
        GAP           = 4
        COL_GAP       = 8
        step_y   = StockWidget.COMPACT_H + GAP
        widget_w = self.uniform_w

        # 이번에 새로 표시되는 종목 자신은 anchor 후보에서 제외
        new_codes = {s["code"] for s in newly_visible}
        anchor = None
        for s in reversed(self.stocks):
            if s.get("hidden", False) or s["code"] in new_codes:
                continue
            w = self.widgets.get(s["code"])
            if w:
                anchor = (s, w)
                break

        if anchor is None:
            # 기존에 표시 위젯이 없으면 전체 정렬로 fallback
            self.reset_positions()
            return

        s_anchor, w_anchor = anchor
        pos_anchor = s_anchor.get("pos") or [w_anchor.x(), w_anchor.y()]
        screen = (
            QApplication.screenAt(w_anchor.frameGeometry().center())
            or QApplication.primaryScreen()
        )
        geo = screen.availableGeometry()
        bottom_limit = geo.y() + geo.height() - MARGIN_BOTTOM

        x, y = pos_anchor[0], pos_anchor[1]
        for s in newly_visible:
            w = self.widgets.get(s["code"])
            if not w:
                continue
            y = y + step_y
            if y + StockWidget.COMPACT_H > bottom_limit:
                # 다음 column (왼쪽으로)
                x = x - widget_w - COL_GAP
                y = geo.y() + MARGIN_Y
            w.move(x, y)
            s["pos"] = [x, y]

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

    # ── 트레이 ─────────────────────────────────────────────────────────────
    def _setup_tray(self):
        icon = self._make_tray_icon()
        self.tray = QSystemTrayIcon(icon)
        self.tray.setToolTip("한국 주식 위젯")

        menu = QMenu()
        menu.setStyleSheet(TRAY_MENU_STYLE)

        add_act    = QAction("➕   종목 추가",   menu)
        manage_act = QAction("📋   종목 관리",   menu)
        export_act = QAction("📤   Excel로 내보내기", menu)
        import_act = QAction("📥   Excel에서 가져오기", menu)
        self.toggle_act = QAction("🙈   숨기기", menu)
        self.master_toggle_act = QAction(self._master_toggle_text(), menu)
        reset_act  = QAction("📐   위치 초기화", menu)
        gather_act = QAction("🎯   마스터 화면에 정렬", menu)
        self.update_act = QAction("🔄   업데이트 확인", menu)
        quit_act   = QAction("❌   종료",        menu)
        add_act.triggered.connect(self.open_add_dialog)
        manage_act.triggered.connect(self.open_manage_dialog)
        export_act.triggered.connect(self.open_export_dialog)
        import_act.triggered.connect(self.open_import_dialog)
        self.toggle_act.triggered.connect(self.toggle_visibility)
        self.master_toggle_act.triggered.connect(self.toggle_master_visibility)
        reset_act.triggered.connect(self.reset_positions)
        gather_act.triggered.connect(self.gather_to_master_screen)
        self.update_act.triggered.connect(self.open_update_dialog)
        quit_act.triggered.connect(self.app.quit)

        menu.addAction(add_act)
        menu.addAction(manage_act)
        menu.addSeparator()
        menu.addAction(export_act)
        menu.addAction(import_act)
        menu.addSeparator()
        menu.addAction(self.toggle_act)
        menu.addAction(self.master_toggle_act)
        menu.addAction(reset_act)
        menu.addAction(gather_act)
        menu.addSeparator()
        menu.addAction(self.update_act)
        menu.addAction(quit_act)

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
            self.stocks = data
        elif isinstance(data, dict):
            self.stocks = data.get("stocks", []) or []
            master = data.get("master") or {}
            self.master_visible = bool(master.get("visible", True))
            pos = master.get("pos")
            if isinstance(pos, list) and len(pos) == 2:
                try:
                    self.master_pos = [int(pos[0]), int(pos[1])]
                except (TypeError, ValueError):
                    self.master_pos = None
            # 토글 버튼 위치
            toggles = data.get("toggles") or {}
            for key, attr in (("hide_all_pos", "hide_all_btn_pos"),
                              ("hide_master_pos", "hide_master_btn_pos")):
                pos = toggles.get(key)
                if isinstance(pos, list) and len(pos) == 2:
                    try:
                        setattr(self, attr, [int(pos[0]), int(pos[1])])
                    except (TypeError, ValueError):
                        pass
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
        data = {
            "stocks": self.stocks,
            "master": {
                "visible": self.master_visible,
                "pos": self.master_pos,
            },
            "toggles": {
                "hide_all_pos":    self.hide_all_btn_pos,
                "hide_master_pos": self.hide_master_btn_pos,
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
        if self.master_widget:
            mpos = self.master_widget.pos()
            self.master_pos = [mpos.x(), mpos.y()]
        if self.hide_all_btn:
            p = self.hide_all_btn.pos()
            self.hide_all_btn_pos = [p.x(), p.y()]
        if self.hide_master_btn:
            p = self.hide_master_btn.pos()
            self.hide_master_btn_pos = [p.x(), p.y()]
        self._save_config()

    # ── 위젯 생성 ──────────────────────────────────────────────────────────
    def _spawn_all(self):
        self.uniform_w = self._calc_uniform_width()
        for i, s in enumerate(self.stocks):
            default_x = 60
            default_y = 60 + i * (StockWidget.COMPACT_H + 12)
            self._spawn_widget(s, default_x, default_y, stagger_idx=i)
        self._spawn_master()
        self._spawn_toggle_buttons()

    def _spawn_toggle_buttons(self):
        """몰컴 모드용 빠른 숨기기 토글 버튼 두 개를 화면에 띄움."""
        # 전체 위젯 숨기기/표시
        self.hide_all_btn = ToggleButton("🙈", "위젯 전체 숨기기/표시")
        self.hide_all_btn.clicked.connect(self.toggle_visibility)

        # 마스터 위젯 숨기기/표시
        self.hide_master_btn = ToggleButton("👑", "마스터 위젯 숨기기/표시")
        self.hide_master_btn.clicked.connect(self.toggle_master_visibility)

        # 위치: 저장된 위치 우선, 없으면 화면 우상단 (마스터 위젯과 안 겹치게 좌측으로)
        primary = QApplication.primaryScreen()
        geo = primary.availableGeometry()
        margin = 12
        btn_size = ToggleButton.SIZE
        # 기본 위치: 우상단 모서리에 가로로 나란히 (전체 토글이 더 우측)
        default_all_x    = geo.x() + geo.width() - btn_size - margin
        default_all_y    = geo.y() + margin
        default_master_x = default_all_x - btn_size - margin
        default_master_y = default_all_y

        pa = self.hide_all_btn_pos or [default_all_x, default_all_y]
        pm = self.hide_master_btn_pos or [default_master_x, default_master_y]
        self.hide_all_btn.move(pa[0], pa[1])
        self.hide_master_btn.move(pm[0], pm[1])
        self.hide_all_btn.setWindowOpacity(self.popover_opacity)
        self.hide_master_btn.setWindowOpacity(self.popover_opacity)
        # 전체 토글은 항상 보임 (전체 숨김 상태일 때만 같이 숨겨짐)
        if not self.is_hidden:
            self.hide_all_btn.show()
        # 마스터 토글은 마스터 위젯이 표시 중일 때만 보임
        if self.master_visible and not self.is_hidden:
            self.hide_master_btn.show()

    def _spawn_widget(self, stock: dict, def_x=60, def_y=60, stagger_idx: int = 0):
        code = stock["code"]
        w = StockWidget(stock, width=self.uniform_w, stagger_idx=stagger_idx)
        w.deleted.connect(self._on_delete)
        w.edited.connect(self._on_edited)
        w.price_updated.connect(lambda _: self._recompute_master())

        pos = stock.get("pos", [def_x, def_y])
        w.move(pos[0], pos[1])
        w.setWindowOpacity(self.popover_opacity)
        # 투명도 50% 이하면 클릭이 통과되는 모드로 (show 전이라 flag 만 set 해두면 됨)
        if self._is_click_through_opacity(self.popover_opacity):
            w.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        # 종목별 hidden 표시 + 전체 숨김 상태 둘 다 고려
        if not stock.get("hidden", False) and not self.is_hidden:
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

        if self.master_visible and not self.is_hidden:
            self.master_widget.show()
        else:
            self.master_widget.hide()

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
        """마스터 + 모든 종목 위젯 + 토글 버튼에 동일 투명도 적용."""
        if self.master_widget:
            self.master_widget.setWindowOpacity(opacity)
        for w in self.widgets.values():
            w.setWindowOpacity(opacity)
        if self.hide_all_btn:
            self.hide_all_btn.setWindowOpacity(opacity)
        if self.hide_master_btn:
            self.hide_master_btn.setWindowOpacity(opacity)

    def _apply_click_through(self, opacity: float):
        """종목 위젯 + 마스터 카드에 OS-레벨 click-through 토글.
        슬라이더는 별도 top-level 윈도우라 마스터가 통과 상태여도 그대로 조작 가능,
        자물쇠 오버레이는 항상 WindowTransparentForInput 라 변동 없음.
        토글 버튼은 항상 클릭 가능."""
        enabled = self._is_click_through_opacity(opacity)
        flag = Qt.WindowType.WindowTransparentForInput

        targets = list(self.widgets.values())
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
        return "📊   마스터 위젯 숨기기" if self.master_visible else "📊   마스터 위젯 표시"

    def toggle_master_visibility(self):
        self.master_visible = not self.master_visible
        show_master = self.master_visible and not self.is_hidden
        if self.master_widget:
            self.master_widget.show() if show_master else self.master_widget.hide()
        # 마스터 토글 버튼도 마스터 위젯 표시 상태에 따름
        if self.hide_master_btn:
            self.hide_master_btn.show() if show_master else self.hide_master_btn.hide()
        self.master_toggle_act.setText(self._master_toggle_text())
        self._save_config()

    def _recompute_master(self):
        """모든 종목 위젯의 current_price 를 모아 마스터 4지표 및 보유 종목 상세를 갱신."""
        if not self.master_widget:
            return
        if not self.stocks:
            self.master_widget.clear_metrics()
            return

        total_invest = 0
        total_eval   = 0
        holdings: list[dict] = []
        for s in self.stocks:
            # 숨김 종목은 합산/holdings 모두 제외 (엑셀 내보내기엔 포함됨)
            if s.get("hidden", False):
                continue
            avg = int(s.get("avg_price", 0))
            qty = int(s.get("quantity", 0))
            invest = avg * qty
            total_invest += invest

            w = self.widgets.get(s["code"])
            # 현재가가 아직 안 잡힌 종목은 평가금액에서 평단가로 임시 사용
            price = w.current_price if (w and w.current_price) else avg
            eval_v = price * qty
            total_eval += eval_v

            profit = eval_v - invest
            rate   = (profit / invest * 100.0) if invest else 0.0
            holdings.append({
                "name":        s.get("name", s["code"]),
                "profit":      profit,
                "profit_rate": rate,
            })

        self.master_widget.update_metrics(total_invest, total_eval)
        self.master_widget.update_holdings(holdings)

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
        result = fetch_stock(code)
        if not result:
            QMessageBox.warning(None, "조회 실패", f"종목코드 '{code}'를 찾을 수 없습니다.\n코드를 다시 확인해 주세요.")
            return

        d["name"] = result["name"]
        self.stocks.append(d)
        self._save_config()

        # 새 종목명이 더 길면 모든 위젯 너비 재조정 (새 위젯도 이 값으로 생성됨)
        self._apply_uniform_width()

        # 새 위젯 위치: 기존 위젯들 아래. 추가된 위젯이라 stagger 필요 없음(즉시 시작)
        ny = 60 + len(self.widgets) * (StockWidget.COMPACT_H + 12)
        self._spawn_widget(d, 60, ny, stagger_idx=0)

        self._recompute_master()

        # 숨김 상태에서 새 종목을 추가한 경우 자동으로 표시 상태로 전환
        if self.is_hidden:
            self.toggle_visibility()

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
        """트레이 메뉴의 '업데이트 확인' 액션 텍스트에 새 버전 표시 점 토글."""
        if not hasattr(self, "update_act"):
            return
        has_update = (
            self._cached_release is not None
            and updater.is_newer(__version__, self._cached_release.version)
        )
        suffix = "  ●" if has_update else ""
        self.update_act.setText("🔄   업데이트 확인" + suffix)

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
            code: int(w.current_price)
            for code, w in self.widgets.items()
            if w.current_price
        }
        dlg = ManageStocksDialog(
            stocks=copy.deepcopy(self.stocks),
            current_prices=current_prices,
        )
        if not dlg.exec():
            return
        new_stocks = dlg.get_stocks()

        old_map = {s["code"]: s for s in self.stocks}
        new_map = {s["code"]: s for s in new_stocks}
        # 변경 전 hidden 스냅샷 — hidden=True → False 전환을 잡아내기 위함
        prev_hidden = {s["code"]: bool(s.get("hidden", False)) for s in self.stocks}

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
                ny = 60 + len(self.widgets) * (StockWidget.COMPACT_H + 12)
                self._spawn_widget(s, 60, ny, stagger_idx=added_idx)
                added_idx += 1

        # 기존 종목: 평단가/수량/hidden 변경 반영
        newly_visible: list[dict] = []   # 이번에 hidden=True→False 로 다시 표시된 종목
        for s in new_stocks:
            code = s["code"]
            if code in old_map and code in self.widgets:
                w = self.widgets[code]
                w.data["avg_price"] = s["avg_price"]
                w.data["quantity"]  = s["quantity"]
                w.data["hidden"]    = bool(s.get("hidden", False))
                if w.current_price:
                    w._update_detail(w.current_price)
                # hidden 상태에 따라 표시 토글 (전체 숨김 모드면 건드리지 않음)
                if not self.is_hidden:
                    if w.data["hidden"]:
                        w.hide()
                    else:
                        w.show()
                # 숨김 → 표시 전환 추적 (정렬 직후 빈 자리에 끼우지 않고 맨 밑에 배치)
                if prev_hidden.get(code, False) and not w.data["hidden"]:
                    newly_visible.append(s)

        # 순서 + 저장 + 너비 재계산
        self.stocks = new_stocks
        self._apply_uniform_width()
        # 다시 표시된 종목들은 마지막 표시 위젯 아래로 배치 (사이 빈 칸에 끼우지 않음)
        if newly_visible:
            self._append_below_last_visible(newly_visible)
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
            export_stocks_to_excel(self.stocks, path, current_prices)
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
            new_stocks = imported   # pos 없음 → 다시 spawn 시 기본 위치
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

        self.stocks = new_stocks
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
        # 가장 긴 종목이 삭제된 경우 남은 위젯들도 줄어들도록
        self._apply_uniform_width()
        self._recompute_master()
