"""종목 관리 다이얼로그 모음."""

from PyQt6.QtWidgets import (
    QDialog, QLabel, QVBoxLayout, QHBoxLayout, QFormLayout,
    QSpinBox, QDialogButtonBox, QPushButton, QMessageBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QStyledItemDelegate, QRadioButton, QButtonGroup, QWidget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from ..core.api import fetch_stock
from ..core.portfolio import stock_metrics
from .theme import C, DIALOG_STYLE
from .form_widgets import (
    AutoSelectLineEdit, AutoSelectSpinBox, ArrowSpinBox, ToggleSwitch,
)


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
        self.setFixedSize(340, 270)
        self.setStyleSheet(DIALOG_STYLE)

        layout = QFormLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 24, 24, 20)
        # 라벨과 입력 위젯의 세로 중심을 일치시킴 (이슈 #2)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # 종목코드 (포커스 시 자동 전체선택)
        self.code_edit = AutoSelectLineEdit()
        self.code_edit.setPlaceholderText("예: 005930  (삼성전자)")
        self.code_edit.editingFinished.connect(self._preview_name)
        layout.addRow(self._row_label("종목 코드"), self.code_edit)

        # 종목명 미리보기 (코드 입력 후 자동 조회, 이슈 #2)
        self.preview_lbl = QLabel("─")
        self._set_preview_neutral()
        layout.addRow(self._row_label("종목명"), self.preview_lbl)

        # 평단가 (화살표 버튼 제거 + 포커스 시 자동 전체선택)
        self.avg_spin = AutoSelectSpinBox()
        self.avg_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.avg_spin.setRange(1, 10_000_000)
        self.avg_spin.setSingleStep(100)
        self.avg_spin.setSuffix("  원")
        layout.addRow(self._row_label("평단가"), self.avg_spin)

        # 수량 (paintEvent로 ▲▼ 화살표 직접 그림)
        self.qty_spin = ArrowSpinBox()
        self.qty_spin.setRange(1, 1_000_000)
        self.qty_spin.setSuffix("  주")
        layout.addRow(self._row_label("수  량"), self.qty_spin)

        # 기존 데이터 채우기
        if self.is_edit:
            self.code_edit.setText(data["code"])
            self.code_edit.setReadOnly(True)
            self.avg_spin.setValue(int(data.get("avg_price", 0)))
            self.qty_spin.setValue(int(data.get("quantity", 1)))
            if data.get("name"):
                self._set_preview_found(data["name"])

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
        lbl.setMinimumHeight(34)   # QLineEdit/QSpinBox 높이와 매칭
        return lbl

    # ── 종목명 자동 미리보기 ─────────────────────────────────────────────
    def _preview_name(self):
        code = self.code_edit.text().strip().upper()
        if not code:
            self._set_preview_neutral()
            return
        if len(code) != 6 or not code.isalnum():
            self._set_preview_hint("6자리 코드를 입력하세요 (숫자/영문)")
            return
        self._set_preview_hint("조회 중...")
        self.preview_lbl.repaint()
        result = fetch_stock(code)
        if result:
            self._set_preview_found(result["name"])
        else:
            self._set_preview_error("찾을 수 없는 종목")

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

    def get_data(self) -> dict:
        return {
            "code":      self.code_edit.text().strip().upper(),
            "market":    "KR",
            "currency":  "KRW",
            "avg_price": self.avg_spin.value(),
            "quantity":  self.qty_spin.value(),
        }


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

    COLS = ["종목명", "종목코드", "평단가", "수량", "평가손익", "표시"]

    def __init__(self, stocks: list[dict], current_prices: dict | None = None,
                 usd_krw_rate: float | None = None, parent=None):
        super().__init__(parent)
        self._stocks: list[dict] = stocks   # 호출측에서 deepcopy 해서 전달
        self._current_prices: dict = current_prices or {}   # {code: 현재가}
        self._usd_krw_rate = usd_krw_rate
        self._suppress_change: bool = False   # itemChanged 재귀 차단용

        self.setWindowTitle("종목 관리")
        self.setMinimumSize(700, 400)
        self.setStyleSheet(DIALOG_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(12)

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

    # ── 표 동기화 ─────────────────────────────────────────────────────────
    def _rebuild_table(self, select_row: int | None = None):
        """self._stocks 기준으로 표를 다시 그림."""
        # rowsMoved / itemChanged 신호가 재구성 중에 발화되지 않도록 일시 차단
        self.table.model().rowsMoved.disconnect(self._on_rows_moved)
        self._suppress_change = True
        try:
            self.table.setRowCount(0)
            for s in self._stocks:
                row = self.table.rowCount()
                self.table.insertRow(row)
                self._fill_row(row, s)
        finally:
            self._suppress_change = False
            self.table.model().rowsMoved.connect(self._on_rows_moved)

        if select_row is not None and 0 <= select_row < self.table.rowCount():
            self.table.selectRow(select_row)

    def _fill_row(self, row: int, s: dict):
        name  = s.get("name", s["code"])
        code  = s["code"]
        avg_p = int(s.get("avg_price", 0))
        qty_n = int(s.get("quantity", 0))
        avg   = f"{avg_p:,} 원"
        qty   = f"{qty_n:,} 주"

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
            lambda checked, r=row: self._on_visibility_toggled(r, checked)
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
        if row < 0 or row >= len(self._stocks):
            return

        if col not in (2, 3):
            return

        # 사용자가 입력한 텍스트에서 숫자만 추출
        text = item.text().strip()
        digits = "".join(c for c in text if c.isdigit())
        s = self._stocks[row]

        if not digits or int(digits) <= 0:
            # 잘못된 입력 → 원래 값으로 복원
            self._suppress_change = True
            if col == 2:
                item.setText(f"{int(s.get('avg_price', 0)):,} 원")
            else:
                item.setText(f"{int(s.get('quantity', 0)):,} 주")
            self._suppress_change = False
            return

        value = int(digits)
        if col == 2:
            s["avg_price"] = value
            suffix = "원"
        else:
            s["quantity"] = value
            suffix = "주"

        # 표시 형식 (쉼표 + 단위) 재포맷
        self._suppress_change = True
        item.setText(f"{value:,} {suffix}")
        self._suppress_change = False

        # 평가손익 셀 즉시 갱신
        self._refresh_profit_cell(row)

    def _refresh_profit_cell(self, row: int):
        s = self._stocks[row]
        code = s["code"]
        avg = int(s.get("avg_price", 0))
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
            avg = int(s.get("avg_price", 0))
            metrics = stock_metrics(s, self._current_prices.get(s["code"], avg), self._usd_krw_rate)
            return metrics["profit"]
        self._stocks.sort(key=key_for, reverse=True)
        self._rebuild_table()

    # ── 드래그 정렬 핸들러 ────────────────────────────────────────────────
    def _on_rows_moved(self, parent, start, end, dest_parent, dest_row):
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

        result = fetch_stock(code)
        if not result:
            QMessageBox.warning(
                self, "조회 실패",
                f"종목코드 '{code}'를 찾을 수 없습니다.\n코드를 다시 확인해 주세요."
            )
            return

        d["name"] = result["name"]
        self._stocks.append(d)
        # 현재가도 캐시해 두면 평가손익이 즉시 계산됨
        self._current_prices[code] = int(result["price"])
        self._rebuild_table(select_row=len(self._stocks) - 1)

    def _edit_selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._stocks):
            return
        dlg = StockDialog(parent=self, data=self._stocks[row])
        if not dlg.exec():
            return
        new = dlg.get_data()
        self._stocks[row]["avg_price"] = new["avg_price"]
        self._stocks[row]["quantity"]  = new["quantity"]
        self._rebuild_table(select_row=row)

    def _delete_selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._stocks):
            return
        name = self._stocks[row].get("name", self._stocks[row]["code"])
        ret = QMessageBox.question(
            self, "삭제 확인",
            f"'{name}' 을(를) 삭제할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        self._stocks.pop(row)
        next_sel = min(row, len(self._stocks) - 1) if self._stocks else None
        self._rebuild_table(select_row=next_sel)

    def get_stocks(self) -> list[dict]:
        return self._stocks
