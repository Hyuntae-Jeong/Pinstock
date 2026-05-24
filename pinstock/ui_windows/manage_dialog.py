"""종목 관리 다이얼로그 모음."""

from PyQt6.QtWidgets import (
    QDialog, QLabel, QVBoxLayout, QHBoxLayout, QFormLayout,
    QSpinBox, QDialogButtonBox, QPushButton, QMessageBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QStyledItemDelegate, QRadioButton, QButtonGroup, QWidget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from ..core.api import fetch_stock, fetch_us_stock, search_us_stocks
from ..core.portfolio import is_us_stock, stock_metrics
from ..core.storage import MARKET_KR, MARKET_US, CURRENCY_KRW, CURRENCY_USD
from .theme import C, DIALOG_STYLE
from .form_widgets import (
    AutoSelectDoubleSpinBox, AutoSelectLineEdit, QuantitySpinBox, ToggleSwitch,
)


def fetch_quote_for_stock(stock: dict) -> dict | None:
    market = str(stock.get("market") or MARKET_KR).upper()
    code = str(stock.get("code") or "").strip().upper()
    if market == MARKET_US:
        return fetch_us_stock(code)
    return fetch_stock(code)


def format_quantity(value) -> str:
    try:
        qty = float(value)
    except (TypeError, ValueError):
        qty = 0.0
    text = f"{qty:,.3f}".rstrip("0").rstrip(".")
    return text or "0"


# ─── Excel import 모드 선택 다이얼로그 ───────────────────────────────────────
class ImportModeDialog(QDialog):
    """덮어쓰기 / 병합 모드 선택. accept 시 self.mode 에 'overwrite' 또는 'merge'."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("가져오기 모드")
        self.setFixedSize(360, 220)
        self.setStyleSheet(DIALOG_STYLE)
        self.mode: str = "merge"

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 18)
        root.setSpacing(10)

        title = QLabel("가져오기 방식을 선택하세요")
        title.setStyleSheet(f"color: {C['text']}; font-size: 13px; font-weight: bold;")
        root.addWidget(title)

        desc = QLabel(
            "기존 stocks.json 은 자동으로 stocks.json.bak 에 백업됩니다."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {C['subtext']}; font-size: 11px;")
        root.addWidget(desc)

        root.addSpacing(4)

        radio_style = (
            f"QRadioButton {{ color: {C['text']}; font-size: 12px; padding: 4px 0; }}"
            f"QRadioButton::indicator {{ width: 14px; height: 14px; }}"
        )

        self.merge_rb = QRadioButton("병합 — 같은 종목코드는 Excel 값으로 갱신, 나머지는 유지")
        self.overwrite_rb = QRadioButton("덮어쓰기 — 기존 종목을 모두 삭제하고 Excel 내용으로 교체")
        self.merge_rb.setStyleSheet(radio_style)
        self.overwrite_rb.setStyleSheet(radio_style)
        self.merge_rb.setChecked(True)

        group = QButtonGroup(self)
        group.addButton(self.merge_rb)
        group.addButton(self.overwrite_rb)

        root.addWidget(self.merge_rb)
        root.addWidget(self.overwrite_rb)
        root.addStretch()

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("가져오기")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setProperty("flat", "true")
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _on_ok(self):
        self.mode = "overwrite" if self.overwrite_rb.isChecked() else "merge"
        self.accept()


# ─── 종목 추가 / 수정 다이얼로그 ──────────────────────────────────────────────
class StockDialog(QDialog):
    def __init__(self, parent=None, data: dict | None = None):
        super().__init__(parent)
        self.is_edit = data is not None
        self.setWindowTitle("종목 수정" if self.is_edit else "종목 추가")
        self.setFixedSize(380, 360)
        self.setStyleSheet(DIALOG_STYLE)
        self._preview_result: dict | None = None

        layout = QFormLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 24, 24, 20)
        # 라벨과 입력 위젯의 세로 중심을 일치시킴 (이슈 #2)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        market_widget = QWidget()
        market_widget.setMinimumHeight(34)
        market_row = QHBoxLayout(market_widget)
        market_row.setContentsMargins(0, 0, 0, 0)
        market_row.setSpacing(15)
        radio_style = (
            f"QRadioButton {{ color: {C['text']}; font-size: 12px; padding: 4px 0 4px 6px; }}"
            f"QRadioButton::indicator {{ width: 14px; height: 14px; margin-left: 2px; margin-right: 5px; }}"
        )
        self.kr_radio = QRadioButton("한국")
        self.us_radio = QRadioButton("미국")
        self.kr_radio.setStyleSheet(radio_style)
        self.us_radio.setStyleSheet(radio_style)
        self.kr_radio.setChecked(True)
        self.market_group = QButtonGroup(self)
        self.market_group.addButton(self.kr_radio)
        self.market_group.addButton(self.us_radio)
        self.kr_radio.toggled.connect(self._on_market_changed)
        self.us_radio.toggled.connect(self._on_market_changed)
        market_row.addWidget(self.kr_radio, 0, Qt.AlignmentFlag.AlignVCenter)
        market_row.addWidget(self.us_radio, 0, Qt.AlignmentFlag.AlignVCenter)
        market_row.addStretch()
        layout.addRow(self._row_label("시장"), market_widget)

        # 종목코드 (포커스 시 자동 전체선택)
        self.code_edit = AutoSelectLineEdit()
        self.code_edit.setPlaceholderText("예: 005930  (삼성전자)")
        self.code_edit.editingFinished.connect(self._preview_name)
        layout.addRow(self._row_label("종목 코드"), self.code_edit)

        # 종목명 미리보기 (코드 입력 후 자동 조회, 이슈 #2)
        self.preview_lbl = QLabel("─")
        self._set_preview_neutral()
        layout.addRow(self._row_label("종목명"), self.preview_lbl)

        # 매입단가 (화살표 버튼 제거 + 포커스 시 자동 전체선택)
        self.avg_label = self._row_label("평단가")
        self.avg_spin = AutoSelectDoubleSpinBox()
        self.avg_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.avg_spin.setRange(0.01, 10_000_000)
        self.avg_spin.setSingleStep(100)
        self.avg_spin.setDecimals(0)
        self.avg_spin.setSuffix("  원")
        layout.addRow(self.avg_label, self.avg_spin)

        self.krw_avg_label = self._row_label("원화 매입단가")
        self.krw_avg_spin = AutoSelectDoubleSpinBox()
        self.krw_avg_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.krw_avg_spin.setRange(1, 1_000_000_000)
        self.krw_avg_spin.setSingleStep(1000)
        self.krw_avg_spin.setDecimals(0)
        self.krw_avg_spin.setSuffix("  원/주")
        layout.addRow(self.krw_avg_label, self.krw_avg_spin)

        # 수량 (paintEvent로 ▲▼ 화살표 직접 그림)
        # 정수면 '1주', 사용자가 소수점 입력하면 '1.5주'처럼 trailing zero 없이 표시
        self.qty_spin = QuantitySpinBox()
        self.qty_spin.setRange(0.001, 1_000_000)
        self.qty_spin.setSingleStep(1)
        self.qty_spin.setDecimals(3)
        self.qty_spin.setSuffix("  주")
        self.qty_spin.setValue(1)
        layout.addRow(self._row_label("수  량"), self.qty_spin)

        # 기존 데이터 채우기
        if self.is_edit:
            market = str(data.get("market") or MARKET_KR).upper()
            self.us_radio.setChecked(market == MARKET_US)
            self.kr_radio.setChecked(market != MARKET_US)
            self.kr_radio.setEnabled(False)
            self.us_radio.setEnabled(False)
            self.code_edit.setText(data["code"])
            self.code_edit.setReadOnly(True)
            self.avg_spin.setValue(float(data.get("avg_price", 0)))
            self.qty_spin.setValue(float(data.get("quantity", 1)))
            if data.get("buy_exchange_rate"):
                krw_avg = float(data.get("avg_price", 0)) * float(data.get("buy_exchange_rate", 0))
                self.krw_avg_spin.setValue(krw_avg)
            if data.get("name"):
                self._set_preview_found(data["name"])

        self._on_market_changed()

        # 버튼
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("확인")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setProperty("flat", "true")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

    # ── 라벨 생성기 (입력 위젯과 세로 중심 정렬, 이슈 #2) ────────────────
    @staticmethod
    def _row_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lbl.setFixedWidth(96)
        lbl.setMinimumHeight(34)   # QLineEdit/QSpinBox 높이와 매칭
        return lbl

    # ── 종목명 자동 미리보기 ─────────────────────────────────────────────
    def _preview_name(self):
        code = self.code_edit.text().strip().upper()
        self._preview_result = None
        if not code:
            self._set_preview_neutral()
            return
        market = self.market()
        if market == MARKET_KR and (len(code) != 6 or not code.isalnum()):
            self._set_preview_hint("6자리 코드를 입력하세요 (숫자/영문)")
            return
        self._set_preview_hint("조회 중...")
        self.preview_lbl.repaint()
        if market == MARKET_US:
            result = fetch_us_stock(code)
            if not result:
                matches = search_us_stocks(code, limit=1)
                if matches:
                    if matches[0].get("symbol"):
                        self.code_edit.setText(matches[0]["symbol"])
                    result = {"name": matches[0]["name"]}
                    self._preview_result = matches[0]
        else:
            result = fetch_stock(code)
        if result:
            self._set_preview_found(result["name"])
            if self._preview_result is None:
                self._preview_result = result
        else:
            self._set_preview_error("찾을 수 없는 종목")

    def market(self) -> str:
        return MARKET_US if self.us_radio.isChecked() else MARKET_KR

    def _on_market_changed(self):
        market = self.market()
        self._preview_result = None
        if not self.is_edit:
            self._set_preview_neutral()
        if market == MARKET_US:
            self.code_edit.setPlaceholderText("예: AAPL  (Apple)")
            self.avg_label.setText("달러 매입단가")
            self.avg_spin.setDecimals(4)
            self.avg_spin.setSingleStep(1)
            self.avg_spin.setSuffix("  USD")
            if not self.is_edit:
                self.avg_spin.setValue(1.0000)
            self.krw_avg_spin.setVisible(True)
            self.krw_avg_label.setVisible(True)
        else:
            self.code_edit.setPlaceholderText("예: 005930  (삼성전자)")
            self.avg_label.setText("평단가")
            self.avg_spin.setDecimals(0)
            self.avg_spin.setSingleStep(100)
            self.avg_spin.setSuffix("  원")
            self.krw_avg_spin.setVisible(False)
            self.krw_avg_label.setVisible(False)

    def _set_preview_neutral(self):
        self.preview_lbl.setText("─")
        self.preview_lbl.setStyleSheet(
            f"color: {C['subtext']}; font-size: 12px; padding-left: 4px;"
        )

    def _set_preview_hint(self, msg: str):
        self.preview_lbl.setText(msg)
        self.preview_lbl.setStyleSheet(
            f"color: {C['subtext']}; font-size: 12px; font-style: italic; padding-left: 4px;"
        )

    def _set_preview_found(self, name: str):
        self.preview_lbl.setText(name)
        self.preview_lbl.setStyleSheet(
            f"color: {C['text']}; font-size: 13px; font-weight: bold; padding-left: 4px;"
        )

    def _set_preview_error(self, msg: str):
        self.preview_lbl.setText(msg)
        self.preview_lbl.setStyleSheet(
            f"color: {C['red']}; font-size: 12px; font-style: italic; padding-left: 4px;"
        )

    def accept(self):
        self._preview_name()
        if self._preview_result is None:
            QMessageBox.warning(
                self,
                "조회 실패",
                "종목을 찾을 수 없습니다.\n코드 또는 티커를 다시 확인해 주세요.",
            )
            return
        super().accept()

    def get_data(self) -> dict:
        market = self.market()
        avg_price = self.avg_spin.value()
        data = {
            "code":      self.code_edit.text().strip().upper(),
            "market":    market,
            "currency":  CURRENCY_USD if market == MARKET_US else CURRENCY_KRW,
            "avg_price": round(avg_price, 4) if market == MARKET_US else int(round(avg_price)),
            "quantity":  round(self.qty_spin.value(), 3),
        }
        if market == MARKET_US:
            data["buy_exchange_rate"] = round(self.krw_avg_spin.value() / avg_price, 4)
        return data


# ─── 좁은 셀에서도 입력값이 잘리지 않도록 editor 폭을 약간만 늘리는 delegate ──
class WideEditorDelegate(QStyledItemDelegate):
    """편집 진입 시 editor 가로폭을 셀 폭 + PADDING 으로 임시 확장.
    셀 자체 너비는 그대로, editor 만 약간 넓어져 cursor·입력값이 잘리지 않게."""

    PADDING = 15   # 셀 폭에 추가할 여유 (cursor + 한두 자 입력 공간)

    def updateEditorGeometry(self, editor, option, index):
        rect = option.rect
        new_w = rect.width() + self.PADDING
        editor.setGeometry(rect.x(), rect.y(), new_w, rect.height())


# ─── 종목 일괄 관리 다이얼로그 ────────────────────────────────────────────────
class ManageStocksDialog(QDialog):
    """현재 보유 종목들을 표 형태로 일괄 관리하는 다이얼로그."""

    COLS = ["종목명", "종목코드", "매입단가", "수량", "평가손익", "표시"]

    def __init__(self, stocks: list[dict], current_prices: dict | None = None,
                 usd_krw_rate: float | None = None, parent=None):
        super().__init__(parent)
        self._stocks: list[dict] = stocks   # 호출측에서 deepcopy 해서 전달
        self._current_prices: dict = current_prices or {}   # {code: 현재가}
        self._usd_krw_rate = usd_krw_rate
        self._suppress_change: bool = False   # itemChanged 재귀 차단용
        self._market_filter: str = "ALL"
        self._row_stock_indexes: list[int] = []

        self.setWindowTitle("종목 관리")
        self.setMinimumSize(700, 400)
        self.setStyleSheet(DIALOG_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(12)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)
        filter_row.addWidget(self._make_filter_btn("전체", "ALL"))
        filter_row.addWidget(self._make_filter_btn("한국", MARKET_KR))
        filter_row.addWidget(self._make_filter_btn("미국", MARKET_US))
        filter_row.addStretch()
        root.addLayout(filter_row)
        self._update_filter_button_styles()

        # ── 표 ─────────────────────────────────────────────────────────────
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        # 더블클릭/EditKey/AnyKey 로 셀 인라인 편집 진입 (편집 가능 셀은 _fill_row 에서 지정)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)

        # 드래그로 행 순서 변경
        self.table.setDragEnabled(True)
        self.table.setAcceptDrops(True)
        self.table.viewport().setAcceptDrops(True)
        self.table.setDropIndicatorShown(True)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.table.setDragDropOverwriteMode(False)

        # 컬럼 너비 정책
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)         # 종목명
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        # 표시 컬럼은 ToggleSwitch 가 잘리지 않게 고정 폭
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(5, 64)
        hdr.setStretchLastSection(False)

        # 헤더 클릭 자동 정렬은 사용하지 않음 (명시적 "정렬" 버튼으로 대체)
        hdr.setSectionsClickable(False)
        hdr.setSortIndicatorShown(False)

        # 평단가/수량 인라인 편집 시 editor 폭을 키워서 입력값이 잘리지 않게
        self._wide_delegate = WideEditorDelegate(self)
        self.table.setItemDelegateForColumn(2, self._wide_delegate)
        self.table.setItemDelegateForColumn(3, self._wide_delegate)

        # 더블클릭: 평단가/수량 셀은 Qt 가 인라인 편집을 처리하므로 패스,
        # 그 외 셀에서는 기존처럼 종목 수정 팝업을 띄움
        self.table.doubleClicked.connect(self._on_double_clicked)

        # 인라인 편집 결과 반영
        self.table.itemChanged.connect(self._on_item_changed)

        # 드래그 정렬: 모델의 rowsMoved 시그널로 self._stocks 순서 동기화
        self.table.model().rowsMoved.connect(self._on_rows_moved)

        root.addWidget(self.table, 1)

        # ── 행 액션 버튼 (추가 / 수정 / 삭제) ─────────────────────────────
        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        add_btn = QPushButton("➕  추가")
        add_btn.clicked.connect(self._add)
        action_row.addWidget(add_btn)

        edit_btn = QPushButton("✏  수정")
        edit_btn.setProperty("flat", "true")
        edit_btn.clicked.connect(self._edit_selected)
        action_row.addWidget(edit_btn)

        del_btn = QPushButton("🗑  삭제")
        del_btn.setProperty("flat", "true")
        del_btn.clicked.connect(self._delete_selected)
        action_row.addWidget(del_btn)

        action_row.addStretch()

        # 평가손익 내림차순 정렬 (명시적 버튼, 자동 정렬은 안 함)
        sort_btn = QPushButton("📊  평가손익 정렬")
        sort_btn.setProperty("flat", "true")
        sort_btn.clicked.connect(self._sort_by_profit_desc)
        action_row.addWidget(sort_btn)

        root.addLayout(action_row)

        # ── 확인 / 취소 ────────────────────────────────────────────────────
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("확인")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setProperty("flat", "true")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        self._rebuild_table()

    def _make_filter_btn(self, text: str, market: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setProperty("flat", "true")
        btn.clicked.connect(lambda _, m=market: self._set_market_filter(m))
        if market == self._market_filter:
            btn.setChecked(True)
        if not hasattr(self, "_filter_buttons"):
            self._filter_buttons: dict[str, QPushButton] = {}
        self._filter_buttons[market] = btn
        return btn

    def _set_market_filter(self, market: str):
        self._market_filter = market
        self._update_filter_button_styles()
        filtered = market != "ALL"
        self.table.setDragEnabled(not filtered)
        self.table.setAcceptDrops(not filtered)
        self.table.viewport().setAcceptDrops(not filtered)
        self.table.setDragDropMode(
            QAbstractItemView.DragDropMode.NoDragDrop
            if filtered else QAbstractItemView.DragDropMode.InternalMove
        )
        self._rebuild_table()

    def _update_filter_button_styles(self):
        for key, btn in self._filter_buttons.items():
            active = key == self._market_filter
            btn.setChecked(active)
            if active:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {C['blue']};
                        color: {C['bg']};
                        border: none;
                        border-radius: 7px;
                        padding: 8px 16px;
                        font-size: 13px;
                        font-weight: bold;
                    }}
                    QPushButton:hover {{ background: #b4befe; }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {C['surface']};
                        color: {C['text']};
                        border: none;
                        border-radius: 7px;
                        padding: 8px 16px;
                        font-size: 13px;
                        font-weight: bold;
                    }}
                    QPushButton:hover {{ background: {C['surface2']}; }}
                """)

    def _matches_filter(self, stock: dict) -> bool:
        if self._market_filter == "ALL":
            return True
        market = MARKET_US if is_us_stock(stock) else MARKET_KR
        return market == self._market_filter

    def _stock_index_for_row(self, row: int) -> int | None:
        if row < 0 or row >= len(self._row_stock_indexes):
            return None
        return self._row_stock_indexes[row]

    # ── 표 동기화 ─────────────────────────────────────────────────────────
    def _rebuild_table(self, select_row: int | None = None):
        """self._stocks 기준으로 표를 다시 그림."""
        # rowsMoved / itemChanged 신호가 재구성 중에 발화되지 않도록 일시 차단
        self.table.model().rowsMoved.disconnect(self._on_rows_moved)
        self._suppress_change = True
        try:
            self.table.setRowCount(0)
            self._row_stock_indexes = []
            for stock_idx, s in enumerate(self._stocks):
                if not self._matches_filter(s):
                    continue
                row = self.table.rowCount()
                self.table.insertRow(row)
                self._row_stock_indexes.append(stock_idx)
                self._fill_row(row, s, stock_idx)
        finally:
            self._suppress_change = False
            self.table.model().rowsMoved.connect(self._on_rows_moved)

        if select_row is not None and select_row in self._row_stock_indexes:
            self.table.selectRow(self._row_stock_indexes.index(select_row))

    def _fill_row(self, row: int, s: dict, stock_idx: int):
        name  = s.get("name", s["code"])
        code  = s["code"]
        us_stock = is_us_stock(s)
        avg_p = float(s.get("avg_price", 0))
        qty_n = float(s.get("quantity", 0))
        avg   = f"{avg_p:,.4f} USD" if us_stock else f"{int(avg_p):,} 원"
        qty   = f"{format_quantity(qty_n)} 주"

        metrics = stock_metrics(s, self._current_prices.get(code, avg_p), self._usd_krw_rate)
        profit = metrics["profit"]
        if profit > 0:
            profit_text  = f"+{profit:,} 원"
            profit_color = C['red']    # 이익 = 빨강 (한국 컨벤션)
        elif profit < 0:
            profit_text  = f"{profit:,} 원"     # 음수면 자체 '-' 표시
            profit_color = C['blue']   # 손실 = 파랑
        else:
            profit_text  = "0 원"
            profit_color = None

        cells = [name, code, avg, qty, profit_text]
        for col, text in enumerate(cells):
            item = QTableWidgetItem(text)
            # 평단가/수량/평가손익은 우측 정렬
            if col in (2, 3, 4):
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            else:
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            # 평가손익 셀에 색상 적용
            if col == 4 and profit_color is not None:
                item.setForeground(QColor(profit_color))
                f = item.font()
                f.setBold(True)
                item.setFont(f)
            # 평단가(2)/수량(3) 셀은 인라인 편집 가능
            base_flags = (
                Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsDragEnabled
            )
            if col in (2, 3):
                base_flags |= Qt.ItemFlag.ItemIsEditable
            item.setFlags(base_flags)
            self.table.setItem(row, col, item)

        # 6번째: 표시 토글 스위치 (ON=표시, OFF=숨김)
        # setCellWidget 사용해 셀에 위젯을 직접 배치 — item 이 없으므로
        # 이전 체크박스에서 발생하던 "0" inline-edit 잔영 문제 회피
        hidden = bool(s.get("hidden", False))
        toggle = ToggleSwitch(checked=not hidden)
        toggle.toggled.connect(
            lambda checked, idx=stock_idx: self._on_visibility_toggled(idx, checked)
        )
        # 셀 가운데 정렬용 컨테이너 (드래그-드롭 정렬 시 시각 일관성 유지)
        container = QWidget()
        hl = QHBoxLayout(container)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addStretch()
        hl.addWidget(toggle)
        hl.addStretch()
        # 셀에 비선택 빈 item 을 깔아 토글 옆 영역 클릭 시 focus/selection
        # 표시가 그려지지 않게 차단 (ItemIsSelectable 제외)
        placeholder = QTableWidgetItem("")
        placeholder.setFlags(
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDragEnabled
        )
        self.table.setItem(row, 5, placeholder)
        self.table.setCellWidget(row, 5, container)

    # ── 더블클릭: 평단가/수량/표시는 인라인 처리, 그 외는 종목 수정 팝업 ─
    def _on_double_clicked(self, index):
        if index.column() in (2, 3, 5):   # 5: 표시 체크박스 (Qt 가 토글 처리)
            return
        self._edit_selected()

    # ── 표시 토글 스위치 변경 → self._stocks[row].hidden 갱신 ────────────
    def _on_visibility_toggled(self, row: int, checked: bool):
        if 0 <= row < len(self._stocks):
            self._stocks[row]["hidden"] = not checked

    # ── 인라인 편집 결과 반영 ────────────────────────────────────────────
    def _on_item_changed(self, item):
        if self._suppress_change or item is None:
            return
        row, col = item.row(), item.column()
        stock_idx = self._stock_index_for_row(row)
        if stock_idx is None:
            return

        if col not in (2, 3):
            return

        # 사용자가 입력한 텍스트에서 숫자만 추출
        text = item.text().strip()
        s = self._stocks[stock_idx]
        us_stock = is_us_stock(s)
        if us_stock and col == 2:
            cleaned = "".join(c for c in text if c.isdigit() or c == ".")
        else:
            cleaned = "".join(c for c in text if c.isdigit() or c == ".")

        try:
            value = float(cleaned) if col in (2, 3) else int(cleaned)
        except ValueError:
            value = 0
        if value <= 0:
            # 잘못된 입력 → 원래 값으로 복원
            self._suppress_change = True
            if col == 2:
                if us_stock:
                    item.setText(f"{float(s.get('avg_price', 0)):,.4f} USD")
                else:
                    item.setText(f"{int(float(s.get('avg_price', 0))):,} 원")
            else:
                item.setText(f"{format_quantity(s.get('quantity', 0))} 주")
            self._suppress_change = False
            return

        if col == 2:
            s["avg_price"] = round(value, 4) if us_stock else int(value)
            suffix = "USD" if us_stock else "원"
        else:
            s["quantity"] = round(value, 3)
            suffix = "주"

        # 표시 형식 (쉼표 + 단위) 재포맷
        self._suppress_change = True
        if col == 2 and us_stock:
            item.setText(f"{value:,.4f} {suffix}")
        elif col == 3:
            item.setText(f"{format_quantity(value)} {suffix}")
        else:
            item.setText(f"{int(value):,} {suffix}")
        self._suppress_change = False

        # 평가손익 셀 즉시 갱신
        self._refresh_profit_cell(row)

    def _refresh_profit_cell(self, row: int):
        stock_idx = self._stock_index_for_row(row)
        if stock_idx is None:
            return
        s = self._stocks[stock_idx]
        code = s["code"]
        avg = float(s.get("avg_price", 0))
        metrics = stock_metrics(s, self._current_prices.get(code, avg), self._usd_krw_rate)
        profit = metrics["profit"]

        if profit > 0:
            text, color = f"+{profit:,} 원", C['red']
        elif profit < 0:
            text, color = f"{profit:,} 원", C['blue']
        else:
            text, color = "0 원", None

        item = self.table.item(row, 4)
        if item is None:
            return
        self._suppress_change = True
        item.setText(text)
        if color:
            item.setForeground(QColor(color))
            f = item.font()
            f.setBold(True)
            item.setFont(f)
        else:
            item.setForeground(QColor(C['text']))
            f = item.font()
            f.setBold(False)
            item.setFont(f)
        self._suppress_change = False

    # ── 평가손익 내림차순 정렬 (명시적 버튼) ─────────────────────────────
    def _sort_by_profit_desc(self):
        def key_for(s: dict):
            avg = float(s.get("avg_price", 0))
            metrics = stock_metrics(s, self._current_prices.get(s["code"], avg), self._usd_krw_rate)
            return metrics["profit"]
        self._stocks.sort(key=key_for, reverse=True)
        self._rebuild_table()

    # ── 드래그 정렬 핸들러 ────────────────────────────────────────────────
    def _on_rows_moved(self, parent, start, end, dest_parent, dest_row):
        if self._market_filter != "ALL":
            self._rebuild_table()
            return
        # 단일 행만 이동(SingleSelection) — 한 항목을 옮긴 결과를 self._stocks 에 반영
        # Qt 의 dest_row 는 "이동 전 좌표계" 기준이므로 보정 필요
        item = self._stocks.pop(start)
        insert_at = dest_row if dest_row < start else dest_row - 1
        insert_at = max(0, min(insert_at, len(self._stocks)))
        self._stocks.insert(insert_at, item)
        # cell widget (ToggleSwitch) 은 drag-drop 시 자동으로 같이 옮겨지지 않으므로
        # 표를 다시 그려서 스위치 위치와 lambda 의 row 캡처를 동기화한다
        self._rebuild_table()

    # ── 액션 ───────────────────────────────────────────────────────────────
    def _add(self):
        dlg = StockDialog(parent=self)
        if not dlg.exec():
            return
        d = dlg.get_data()
        code = d["code"]
        if not code:
            return
        if any(s["code"] == code for s in self._stocks):
            QMessageBox.information(self, "알림", f"'{code}'는 이미 추가되어 있습니다.")
            return

        result = fetch_quote_for_stock(d)
        if not result:
            QMessageBox.warning(
                self, "조회 실패",
                f"종목코드 '{code}'를 찾을 수 없습니다.\n코드를 다시 확인해 주세요."
            )
            return

        d["name"] = result["name"]
        d["hidden"] = False
        self._stocks.append(d)
        # 현재가도 캐시해 두면 평가손익이 즉시 계산됨
        self._current_prices[code] = float(result["price"])
        self._rebuild_table(select_row=len(self._stocks) - 1)

    def _edit_selected(self):
        row = self.table.currentRow()
        stock_idx = self._stock_index_for_row(row)
        if stock_idx is None:
            return
        dlg = StockDialog(parent=self, data=self._stocks[stock_idx])
        if not dlg.exec():
            return
        new = dlg.get_data()
        self._stocks[stock_idx]["avg_price"] = new["avg_price"]
        self._stocks[stock_idx]["quantity"]  = new["quantity"]
        if "buy_exchange_rate" in new:
            self._stocks[stock_idx]["buy_exchange_rate"] = new["buy_exchange_rate"]
        self._rebuild_table(select_row=stock_idx)

    def _delete_selected(self):
        row = self.table.currentRow()
        stock_idx = self._stock_index_for_row(row)
        if stock_idx is None:
            return
        name = self._stocks[stock_idx].get("name", self._stocks[stock_idx]["code"])
        ret = QMessageBox.question(
            self, "삭제 확인",
            f"'{name}' 을(를) 삭제할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        self._stocks.pop(stock_idx)
        next_sel = min(stock_idx, len(self._stocks) - 1) if self._stocks else None
        self._rebuild_table(select_row=next_sel)

    def get_stocks(self) -> list[dict]:
        return self._stocks
