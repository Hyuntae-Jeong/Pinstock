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
from datetime import datetime, timedelta

from PyQt6.QtCore import Qt, QObject, QTimer, QEvent, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox, QFileDialog

from ..__version__ import __version__
from ..core import updater
from ..core.api import (
    fetch_stock, fetch_minute_chart, fetch_daily_chart,
    fetch_us_stock, fetch_us_minute_chart, fetch_us_daily_chart,
    fetch_usd_krw_rate,
)
from ..core.portfolio import is_us_stock, portfolio_totals
from ..core.storage import (
    CONFIG_FILE, BACKUP_FILE,
    export_stocks_to_excel, import_stocks_from_excel, normalize_stocks_schema,
)
from ..ui_windows.manage_dialog import (
    StockDialog, ManageStocksDialog, ImportModeDialog, fetch_quote_for_stock,
)
from ..ui_common.update_dialog import UpdateDialog

from .popover import Popover
from .menubar import MenuBarIcon


# ─── 자동 업데이트 체크 설정 (Windows 매니저와 동일) ──────────────────────
_AUTO_CHECK_INTERVAL = timedelta(hours=24)
_AUTO_CHECK_STARTUP_DELAY_MS = 10 * 1000      # 앱 시작 후 10초 뒤
_PREV_ERROR_CHECK_DELAY_MS = 1500              # 시작 직후 1.5초


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


# ─── 매니저 ─────────────────────────────────────────────────────────────────
class MacAppManager(QObject):
    """macOS Pinstock 메인 매니저."""

    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        # 사용자가 popover 를 "보고 싶어 하는" 상태인지 명시적으로 추적.
        # 트레이로 열면 True, 트레이 토글/ESC 로 닫으면 False. NSPanel 의
        # hidesOnDeactivate 같은 시스템 자동 hide 는 건드리지 않는다.
        # 앱이 다시 active 가 될 때 이 값이 True 이면 popover 를 강제 복귀시켜
        # macOS 의 "inactive 앱 첫 클릭은 앱 깨우기로만 소비" 동작으로 인한
        # "씹힘" 을 우회한다. (isVisible() 시점 의존이면 NSPanel 자동 hide 가
        # ApplicationDeactivate 이벤트보다 먼저 발화해 항상 False 로 읽혀서
        # 불안정함 — 그래서 사용자 의도 기반으로 분리)
        self._user_wants_popover_visible: bool = False
        self._popover_just_re_shown: bool = False
        app.installEventFilter(self)
        app.applicationStateChanged.connect(self._on_app_state_changed)

        self.stocks: list[dict] = []
        self.fetchers: dict[str, StockFetcher] = {}
        self.current_prices: dict[str, float] = {}
        self.usd_krw_rate: float | None = None
        # 마지막 폴링 결과 캐시. set_stocks() 가 행 위젯을 폐기·재생성하면
        # 차트가 다음 60초 폴링 전까지 비어보여서, 직후에 다시 주입해 채운다.
        self.last_price_result: dict[str, dict] = {}
        self.last_minute_data:  dict[str, tuple[list, float]] = {}
        self.last_daily_data:   dict[str, list] = {}

        # 설정 로드 (Windows 와 동일 스키마)
        self.master_visible: bool = True
        self.master_pos: list | None = None
        self.hide_all_btn_pos: list | None = None
        self.hide_master_btn_pos: list | None = None
        self.assets_hidden: bool = False
        self.popover_opacity: float = 1.0
        self.popover_height: int | None = None
        self.market_filter: str = "ALL"
        # 자동 업데이트 체크 상태
        self.update_last_check_at: datetime | None = None
        self._cached_release: updater.ReleaseInfo | None = None
        self._update_signals = _UpdateCheckSignals()
        self._update_signals.done.connect(self._on_auto_check_done)
        self._load_config()

        self.fx_timer = QTimer(self)
        self.fx_timer.timeout.connect(self._fetch_usd_krw_rate)

        # UI
        self.popover = Popover()
        self.menubar = MenuBarIcon(app, parent=self)

        # 시그널 연결
        self.menubar.toggle_popover_requested.connect(self._on_toggle_popover)
        self.menubar.notification_clicked.connect(self.open_update_dialog)
        self.popover.add_stock_requested.connect(self.open_add_dialog)
        self.popover.manage_stocks_requested.connect(self.open_manage_dialog)
        self.popover.export_requested.connect(self.open_export_dialog)
        self.popover.import_requested.connect(self.open_import_dialog)
        self.popover.check_update_requested.connect(self.open_update_dialog)
        self.popover.quit_requested.connect(self.app.quit)
        self.popover.edit_requested.connect(self._on_edit_request)
        self.popover.delete_requested.connect(self._on_delete_request)
        self.popover.market_filter_changed.connect(self._on_market_filter_changed)
        self.popover.assets_hidden_changed.connect(self._on_assets_hidden_changed)
        self.popover.opacity_changed.connect(self._on_opacity_changed)
        self.popover.height_changed.connect(self._on_height_changed)
        self.popover.closed_by_user.connect(self._on_popover_closed_by_user)

        # 로드한 자산 숨김 / 팝오버 투명도 상태를 팝오버에 한 번 주입
        self.popover.set_assets_hidden(self.assets_hidden)
        self.popover.set_opacity(self.popover_opacity)
        self.popover.set_preferred_height(self.popover_height)
        self.popover.set_market_filter(self.market_filter)

        # 초기 데이터 푸시
        self._sync_popover_stocks()
        self._recompute_summary()

        # 종목별 폴링 시작
        for i, s in enumerate(self.stocks):
            self._spawn_fetcher(s, stagger_idx=i)
        self._sync_fx_timer()

        # 시작 직후 — 이전 업데이트 실패 로그가 있으면 안내
        QTimer.singleShot(_PREV_ERROR_CHECK_DELAY_MS, self._check_previous_update_error)
        # 시작 10초 뒤 — 자동 업데이트 체크 (throttle/can_self_update 검사 후 실제 호출)
        QTimer.singleShot(_AUTO_CHECK_STARTUP_DELAY_MS, self._maybe_run_auto_update_check)

    # ── 앱 active/inactive 트랜지션 ───────────────────────────────────────
    # ApplicationActivate 이벤트와 applicationStateChanged 시그널 양쪽에
    # 같은 핸들러를 걸어 belt-and-suspenders. macOS Qt 버전 / 활성화 경로
    # (트레이 클릭 vs cmd+tab) 에 따라 둘 중 하나만 발화하는 경우가 있어
    # 한쪽이라도 잡히면 사용자가 "씹힘" 을 경험하지 않게 한다.
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.ApplicationActivate:
            self._maybe_re_show_popover()
        return False

    def _on_app_state_changed(self, state):
        if state == Qt.ApplicationState.ApplicationActive:
            self._maybe_re_show_popover()
        elif state == Qt.ApplicationState.ApplicationInactive:
            # NSPanel 의 hidesOnDeactivate 가 Cocoa 레벨에서 popover 를
            # 시각적으로 숨기는데, Qt 의 isVisible() 은 그걸 모르고 True 인
            # 채로 남는다 (desync). 그 상태에서 다음 트레이 클릭이 토글로
            # 오면 isVisible=True 로 읽혀 "이미 열려있네 → HIDE" 로 잘못
            # 동작 (사용자에겐 "씹힘"). 우리가 명시적으로 hide() 를 호출해
            # Qt state 를 Cocoa 와 강제 동기화한다. _user_wants_popover_visible
            # 은 건드리지 않으므로 다음 Active 전환 시 자동 복귀가 가능.
            if self.popover.isVisible():
                self.popover.hide()

    def _maybe_re_show_popover(self):
        if not self._user_wants_popover_visible:
            return
        if self.popover.isVisible() or self._popover_just_re_shown:
            return   # 이미 떠있거나 방금 한 번 재표시했음 (중복 호출 방지)
        anchor_pos, anchor_w = self.menubar._anchor_position()
        self.popover.show_below(anchor_pos, anchor_w)
        # 같은 트레이 클릭이 늦게 activated 를 fire 시키면 토글-닫기로
        # 동작해버리니, 짧은 시간 동안 토글을 가드한다.
        self._popover_just_re_shown = True
        QTimer.singleShot(200, self._clear_just_re_shown)

    def _clear_just_re_shown(self):
        self._popover_just_re_shown = False

    def _on_popover_closed_by_user(self):
        # ESC 등으로 사용자가 직접 닫음 → 다음 ApplicationActivate 에서 자동
        # 복귀하지 않도록 의도 클리어.
        self._user_wants_popover_visible = False

    # ── 팝오버 토글 ───────────────────────────────────────────────────────
    def _on_toggle_popover(self, anchor_pos, anchor_w):
        if self._popover_just_re_shown:
            # ApplicationActivate 핸들러가 방금 popover 를 띄웠음 — 같은
            # 트레이 클릭의 activated 가 토글-닫기로 동작하지 않도록 무시.
            return
        if self.popover.isVisible():
            self.popover.hide()
            self._user_wants_popover_visible = False
        else:
            self.popover.show_below(anchor_pos, anchor_w)
            self._user_wants_popover_visible = True

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

    def _on_price_updated(self, code: str, result: dict):
        # stocks 의 name 도 동기화 (네이버에서 이름 받아오면)
        for s in self.stocks:
            if s["code"] == code:
                s["name"] = result["name"]
                break
        self.current_prices[code] = float(result["price"])
        self.last_price_result[code] = result
        self.popover.update_stock_price(code, result)
        self._recompute_summary()

    def _on_minute_updated(self, code: str, prices: list, open_price: float):
        self.last_minute_data[code] = (prices, open_price)
        self.last_daily_data.pop(code, None)
        self.popover.update_stock_minute(code, prices, open_price)

    def _on_daily_updated(self, code: str, candles: list):
        self.last_daily_data[code] = candles
        self.last_minute_data.pop(code, None)
        self.popover.update_stock_daily(code, candles)

    def _on_assets_hidden_changed(self, hidden: bool):
        self.assets_hidden = hidden
        self._save_config()

    def _on_opacity_changed(self, opacity: float):
        self.popover_opacity = opacity
        self._save_config()

    def _on_height_changed(self, height: int):
        self.popover_height = height
        self._save_config()

    def _reapply_cached_data(self):
        """popover.set_stocks() 이후 새로 만들어진 행에 캐시된 가격/차트를 즉시 다시
        넣어 차트가 비어 보이는 시간을 없앤다."""
        for code, result in self.last_price_result.items():
            self.popover.update_stock_price(code, result)
        for code, (prices, open_price) in self.last_minute_data.items():
            self.popover.update_stock_minute(code, prices, open_price)
        for code, candles in self.last_daily_data.items():
            self.popover.update_stock_daily(code, candles)

    def _sync_popover_stocks(self):
        self.popover.set_stocks(self.stocks)
        self._reapply_cached_data()

    # ── 포트폴리오 요약 재계산 ───────────────────────────────────────────
    def _recompute_summary(self):
        if not self.stocks:
            self.popover.update_summary(0, 0)
            return

        stocks = [s for s in self.stocks if self._matches_market_filter(s)]
        totals = portfolio_totals(
            stocks,
            current_prices=self.current_prices,
            usd_krw_rate=self.usd_krw_rate,
        )
        self.popover.update_summary(totals["total_invest"], totals["total_eval"])

    def _matches_market_filter(self, stock: dict) -> bool:
        if self.market_filter == "ALL":
            return True
        market = "US" if is_us_stock(stock) else "KR"
        return market == self.market_filter

    def _on_market_filter_changed(self, market: str):
        self.market_filter = market if market in {"ALL", "KR", "US"} else "ALL"
        self._sync_popover_stocks()
        self._recompute_summary()

    def _fetch_usd_krw_rate(self):
        result = fetch_usd_krw_rate()
        if not result:
            return
        self.usd_krw_rate = float(result["rate"])
        self.popover.set_usd_krw_rate(self.usd_krw_rate)
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
            self.popover.set_usd_krw_rate(None)

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
            master = data.get("master") or {}
            self.master_visible = bool(master.get("visible", True))
            pos = master.get("pos")
            if isinstance(pos, list) and len(pos) == 2:
                try:
                    self.master_pos = [int(pos[0]), int(pos[1])]
                except (TypeError, ValueError):
                    self.master_pos = None
            toggles = data.get("toggles") or {}
            for key, attr in (("hide_all_pos", "hide_all_btn_pos"),
                              ("hide_master_pos", "hide_master_btn_pos")):
                p = toggles.get(key)
                if isinstance(p, list) and len(p) == 2:
                    try:
                        setattr(self, attr, [int(p[0]), int(p[1])])
                    except (TypeError, ValueError):
                        pass
            self.assets_hidden = bool(data.get("assets_hidden", False))
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
            # 자동 업데이트 메타 — last_check_at 만 (24h throttle 용)
            upd = data.get("update") or {}
            last_at = upd.get("last_check_at")
            if isinstance(last_at, str):
                try:
                    self.update_last_check_at = datetime.fromisoformat(last_at)
                except ValueError:
                    self.update_last_check_at = None

    def _save_config(self):
        # Windows 와 호환되는 스키마 — Mac 에서는 의미 없는 필드도 보존만 함
        self.stocks = normalize_stocks_schema(self.stocks)
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
            "popover_height": self.popover_height,
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
        self._kill_fetcher(code)
        self.current_prices.pop(code, None)
        self._sync_fx_timer()
        self._save_config()
        self._sync_popover_stocks()
        self._recompute_summary()

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

    # ── 업데이트 확인 ─────────────────────────────────────────────────────
    # Windows WidgetManager 와 1:1 대응되는 패턴 — 다이얼로그가 manager 캐시를
    # 갱신하도록 콜백을 넘겨주고, throttle/뱃지/토스트는 manager 가 책임진다.
    def open_update_dialog(self):
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
        """팝오버 🔄 버튼에 새 버전 표시 토글."""
        has_update = (
            self._cached_release is not None
            and updater.is_newer(__version__, self._cached_release.version)
        )
        self.popover.set_update_available(has_update)

    def _show_update_toast(self, release: updater.ReleaseInfo):
        """macOS 알림센터 토스트 — 클릭하면 menubar.notification_clicked → open_update_dialog."""
        self.menubar.show_notification(
            "Pinstock 업데이트 가능",
            f"새 버전 {release.tag} 가 있습니다. 클릭하여 확인하세요.",
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
