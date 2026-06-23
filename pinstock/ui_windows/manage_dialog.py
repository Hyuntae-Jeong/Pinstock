"""종목 관리 다이얼로그 모음."""

import copy

from PyQt6.QtWidgets import (
    QDialog, QLabel, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QSpinBox, QDialogButtonBox, QPushButton, QMessageBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QStyledItemDelegate, QRadioButton, QButtonGroup, QWidget,
    QCompleter, QComboBox, QColorDialog, QFrame, QCheckBox,
)
from PyQt6.QtCore import Qt, QTimer, QModelIndex, QSize, QRectF, pyqtSignal
from PyQt6.QtGui import (
    QColor, QFont, QPainter, QPen, QStandardItemModel, QStandardItem, QPixmap, QIcon,
    QValidator,
)

from ..core.api import (
    fetch_stock, fetch_us_stock, fetch_index,
    search_us_stocks, search_korean_stocks,
)
from ..core.indices import index_by_code, search_indices, index_exact_match
from ..core.portfolio import buy_preview, is_us_stock, stock_metrics
from ..core.storage import (
    MARKET_KR, MARKET_US, CURRENCY_KRW, CURRENCY_USD,
    DEFAULT_TAG_COLOR, new_tag_id, normalize_tags, prune_watch_tags,
)
from .theme import C, DIALOG_STYLE, SEARCH_POPUP_STYLE, TAG_PALETTE, MA_COLORS
from .form_widgets import (
    ArrowDoubleSpinBox, AutoSelectDoubleSpinBox, AutoSelectLineEdit, SearchLineEdit,
    QuantitySpinBox, ToggleSwitch,
)

_NUMBER_FONT_FAMILY = "Arial"


# ─── 태그 색상 유틸 ───────────────────────────────────────────────────────────
def _is_hex_color(value) -> bool:
    return isinstance(value, str) and len(value) == 7 and value.startswith("#")


def _contrast_text(hex_color: str) -> str:
    """배경색 위에 읽기 좋은 글자색(검정/흰색)을 휘도로 고른다."""
    if not _is_hex_color(hex_color):
        return "#000000"
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    return "#11111b" if luminance > 0.6 else "#ffffff"


def _color_icon(color: str, size: int = 12) -> QIcon:
    """단색 둥근 사각형 아이콘 (콤보박스/표의 태그 색 표시용)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    from PyQt6.QtGui import QPainter
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color if _is_hex_color(color) else DEFAULT_TAG_COLOR))
    p.drawRoundedRect(0, 0, size, size, 3, 3)
    p.end()
    return QIcon(pm)


class _NoScrollComboBox(QComboBox):
    """드롭다운이 닫힌 상태에서는 마우스 휠로 항목이 바뀌지 않게 한다.

    표(관심종목 목록)를 휠로 스크롤하다 마우스가 콤보 위를 지날 때 태그가
    제멋대로 바뀌는 사고를 막는다. 휠 이벤트를 무시해 부모(표)로 넘기므로 표
    스크롤은 정상 동작하고, 드롭다운이 열려 있을 때는 팝업 리스트가 자체적으로
    휠을 처리한다(콤보 본체로는 휠이 오지 않음)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # 휠로 포커스를 가로채 값이 바뀌지 않도록 강포커스로 제한
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        event.ignore()


class _StockSearchCompleter(QCompleter):
    """라인 에디트에 써넣을 값을 표시 라벨이 아닌 종목 코드/티커로 고정한다.
    KR·US 두 시장에서 공통으로 사용."""

    def pathFromIndex(self, index: QModelIndex) -> str:
        data = index.data(Qt.ItemDataRole.UserRole)
        if isinstance(data, dict) and data.get("code"):
            return str(data["code"])
        return super().pathFromIndex(index)


def fetch_quote_for_stock(stock: dict) -> dict | None:
    code = str(stock.get("code") or "").strip().upper()
    if str(stock.get("type") or "").strip().lower() == "index":
        return fetch_index(code, stock.get("market"))
    market = str(stock.get("market") or MARKET_KR).upper()
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


class _AmountSpinBox(ArrowDoubleSpinBox):
    """천 단위 쉼표를 표시/입력 모두에서 허용하는 금액 입력 스핀박스."""

    def textFromValue(self, value: float) -> str:
        if self.decimals() <= 0:
            return f"{int(round(value)):,}"
        text = f"{value:,.{self.decimals()}f}".rstrip("0").rstrip(".")
        return text or "0"

    def valueFromText(self, text: str) -> float:
        cleaned = (
            text.replace(",", "")
            .replace("원", "")
            .replace("USD", "")
            .strip()
        )
        if cleaned in {"", ".", "-"}:
            return 0.0
        try:
            return float(cleaned)
        except ValueError:
            return 0.0

    def validate(self, text: str, pos: int):
        cleaned = (
            text.replace(",", "")
            .replace("원", "")
            .replace("USD", "")
            .strip()
        )
        if cleaned in {"", ".", "-"}:
            return QValidator.State.Intermediate, text, pos
        try:
            float(cleaned)
        except ValueError:
            return QValidator.State.Invalid, text, pos
        return QValidator.State.Acceptable, text, pos


class _CurrencySegmentControl(QWidget):
    """USD/원 두 모드를 한 버튼처럼 보여주는 작은 세그먼트 토글."""

    currency_changed = pyqtSignal(str)

    def __init__(self, currency: str = "usd", parent=None):
        super().__init__(parent)
        self._currency = "krw" if currency == "krw" else "usd"
        self.setFixedSize(150, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_currency(self, currency: str):
        currency = "krw" if currency == "krw" else "usd"
        if self._currency == currency:
            return
        self._currency = currency
        self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        pos = event.position().x()
        next_currency = "usd" if pos < self.width() / 2 else "krw"
        if next_currency == self._currency:
            next_currency = "krw" if self._currency == "usd" else "usd"
        self._currency = next_currency
        self.update()
        self.currency_changed.emit(self._currency)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        outer = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        painter.setPen(QPen(QColor(C["surface2"]), 1))
        painter.setBrush(QColor(C["surface"]))
        painter.drawRoundedRect(outer, 7, 7)

        pad = 3
        half_w = (self.width() - pad * 2) / 2
        left = QRectF(pad, pad, half_w, self.height() - pad * 2)
        right = QRectF(pad + half_w, pad, half_w, self.height() - pad * 2)
        active_rect = left if self._currency == "usd" else right
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(C["blue"]))
        painter.drawRoundedRect(active_rect, 5, 5)

        painter.setFont(QFont(_NUMBER_FONT_FAMILY, 10, QFont.Weight.Bold))
        painter.setPen(QColor(C["bg"] if self._currency == "usd" else C["text"]))
        painter.drawText(left, Qt.AlignmentFlag.AlignCenter, "USD")
        painter.setPen(QColor(C["bg"] if self._currency == "krw" else C["text"]))
        painter.drawText(right, Qt.AlignmentFlag.AlignCenter, "원")
        painter.end()


class BuyPreviewDialog(QDialog):
    """보유 종목의 현재가 기준 추가 매수 예상 평단가/수익률을 보여준다."""

    def __init__(
        self,
        stock: dict,
        current_price: float | int | None = None,
        usd_krw_rate: float | int | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.stock = dict(stock)
        self.current_price = current_price
        self.usd_krw_rate = usd_krw_rate
        self._preview: dict | None = None
        self._syncing_buy_inputs = False
        self._amount_currency = "usd" if is_us_stock(self.stock) else "krw"

        profit_rate = stock_metrics(stock, current_price, usd_krw_rate)["profit_rate"]
        action_word = "물타기" if profit_rate < 0 else "불타기"
        action_icon = "💧" if profit_rate < 0 else "🔥"
        self.setWindowTitle(f"{action_icon} {action_word} 매수")
        self.setFixedSize(560, 430)
        self.setStyleSheet(DIALOG_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 18)
        root.setSpacing(12)

        name = stock.get("name") or stock.get("code", "")
        code = stock.get("code", "")
        title = QLabel(f"{action_icon} {name} ({code}) 추가 매수")
        title.setStyleSheet(
            f"color: {C['text']}; font-size: 14px; font-weight: bold;"
        )
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.addWidget(title)
        title_row.addStretch()
        root.addLayout(title_row)

        input_grid = QGridLayout()
        input_grid.setContentsMargins(0, 0, 0, 0)
        input_grid.setHorizontalSpacing(8)
        input_grid.setVerticalSpacing(8)
        input_grid.setColumnMinimumWidth(0, 58)
        input_grid.setColumnMinimumWidth(1, 160)
        input_grid.setColumnMinimumWidth(2, 12)
        input_grid.setColumnMinimumWidth(3, 92)
        input_grid.setColumnMinimumWidth(4, 150)
        root.addLayout(input_grid)

        self.price_val = self._value_label()
        input_grid.addWidget(self._row_header_label("현재가"), 0, 0)
        input_grid.addWidget(self.price_val, 0, 1)

        self.add_qty_spin = QuantitySpinBox()
        self.add_qty_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.add_qty_spin.setRange(0, 1_000_000)
        self.add_qty_spin.setSingleStep(1)
        self.add_qty_spin.setDecimals(0)
        self.add_qty_spin.setSuffix("  주")
        self.add_qty_spin.setValue(1)
        self.add_qty_spin.setFixedSize(150, 34)
        input_grid.addWidget(self._label("추가 매수 수량"), 0, 3, Qt.AlignmentFlag.AlignRight)
        input_grid.addWidget(self.add_qty_spin, 0, 4, Qt.AlignmentFlag.AlignLeft)

        self.add_amount_spin = _AmountSpinBox()
        self.add_amount_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.add_amount_spin.setRange(0, 1_000_000_000_000)
        self.add_amount_spin.setValue(0)
        self.add_amount_spin.setFixedSize(150, 34)
        input_grid.addWidget(self._label("추가 매수 금액"), 1, 3, Qt.AlignmentFlag.AlignRight)
        input_grid.addWidget(self.add_amount_spin, 1, 4, Qt.AlignmentFlag.AlignLeft)
        self.amount_currency_btn = None
        if is_us_stock(self.stock):
            self.amount_currency_btn = _CurrencySegmentControl(self._amount_currency)
            self.amount_currency_btn.currency_changed.connect(self._set_amount_currency)
            input_grid.addWidget(self._label("통화 선택"), 2, 3, Qt.AlignmentFlag.AlignRight)
            input_grid.addWidget(self.amount_currency_btn, 2, 4, Qt.AlignmentFlag.AlignLeft)

        self.add_qty_spin.valueChanged.connect(self._on_add_qty_changed)
        self.add_amount_spin.valueChanged.connect(self._on_add_amount_changed)
        self._sync_amount_spin_format()
        self._sync_amount_from_quantity()

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background: {C['border']}; max-height: 1px; border: none;")
        root.addWidget(sep)

        compare_grid = QGridLayout()
        compare_grid.setHorizontalSpacing(10)
        compare_grid.setVerticalSpacing(9)
        compare_grid.setColumnMinimumWidth(0, 58)
        compare_grid.setColumnMinimumWidth(1, 180)
        compare_grid.setColumnMinimumWidth(2, 220)
        compare_grid.setColumnStretch(0, 0)
        compare_grid.setColumnStretch(1, 0)
        compare_grid.setColumnStretch(2, 0)
        root.addLayout(compare_grid)

        compare_grid.addWidget(self._header_label("항목"), 0, 0)
        compare_grid.addWidget(self._header_label("현재", C["blue"]), 0, 1)
        compare_grid.addWidget(self._header_label("예상", C["green"]), 0, 2)

        self.qty_val = self._value_label()
        self.avg_val = self._value_label()
        self.rate_val = self._value_label()
        self.invest_val = self._value_label()
        self.eval_val = self._value_label()
        self.preview_qty_val = self._value_label()
        self.preview_avg_val = self._value_label()
        self.preview_rate_val = self._value_label()
        self.preview_invest_val = self._value_label()
        self.preview_eval_val = self._value_label()

        rows = [
            ("보유수량", self.qty_val, self.preview_qty_val),
            ("평단가", self.avg_val, self.preview_avg_val),
            ("수익률", self.rate_val, self.preview_rate_val),
            ("투자원금", self.invest_val, self.preview_invest_val),
            ("평가금액", self.eval_val, self.preview_eval_val),
        ]
        for row_idx, (label, current, preview) in enumerate(rows, start=1):
            compare_grid.addWidget(self._row_header_label(label), row_idx, 0)
            compare_grid.addWidget(current, row_idx, 1)
            compare_grid.addWidget(preview, row_idx, 2)

        root.addStretch()

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("매수 확정")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setProperty("flat", "true")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        self._render_current()
        self._update_preview()

    @staticmethod
    def _label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: {C['subtext']}; font-size: 12px;")
        return lbl

    @staticmethod
    def _header_label(text: str, color: str | None = None) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {color or C['subtext']}; font-size: 13px; font-weight: bold;"
        )
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return lbl

    @staticmethod
    def _row_header_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: {C['subtext']}; font-size: 12px;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return lbl

    def _apply_currency_button_style(self):
        if self.amount_currency_btn is not None:
            self.amount_currency_btn.set_currency(self._amount_currency)

    @staticmethod
    def _value_label() -> QLabel:
        lbl = QLabel("─")
        lbl.setFont(QFont(_NUMBER_FONT_FAMILY, 13, QFont.Weight.Bold))
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lbl.setStyleSheet(f"color: {C['text']}; font-size: 13px; font-weight: bold;")
        return lbl

    def _price_text(self, value: float | int | None) -> str:
        price = float(value or 0)
        if is_us_stock(self.stock):
            return f"{price:,.4f} USD"
        return f"{int(round(price)):,} 원"

    def _current_price_text(self) -> str:
        price = self._current_price_value()
        if is_us_stock(self.stock) and self._amount_currency == "krw":
            return f"{int(round(price * self._current_fx_rate())):,} 원"
        return self._price_text(price)

    def _avg_price_value(self, stock: dict, metrics: dict) -> float:
        avg_price = float(stock.get("avg_price", 0) or 0)
        if is_us_stock(stock) and self._amount_currency == "krw":
            return avg_price * float(metrics.get("buy_rate", 1.0) or 1.0)
        return avg_price

    def _avg_price_text(self, stock: dict, metrics: dict) -> str:
        value = self._avg_price_value(stock, metrics)
        if is_us_stock(stock) and self._amount_currency == "krw":
            return f"{int(round(value)):,} 원"
        return self._price_text(value)

    def _avg_price_text_for_delta(self, value: float) -> str:
        if is_us_stock(self.stock) and self._amount_currency == "krw":
            return f"{int(round(value)):,} 원"
        return self._price_text(value)

    @staticmethod
    def _rate_text(value: float) -> str:
        sign = "+" if value >= 0 else ""
        return f"{sign}{value:.2f}%"

    @staticmethod
    def _won_text(value: float | int) -> str:
        return f"{int(round(value)):,} 원"

    def _set_rate_label(self, label: QLabel, value: float):
        color = C["red"] if value >= 0 else C["blue"]
        label.setText(self._rate_text(value))
        label.setStyleSheet(f"color: {color}; font-size: 13px; font-weight: bold;")

    def _delta_text(self, delta: float, formatter) -> tuple[str, str]:
        if delta > 0:
            return f"▲ {formatter(delta)}", C["red"]
        if delta < 0:
            return f"▼ {formatter(abs(delta))}", C["blue"]
        return f"─ {formatter(0)}", C["subtext"]

    def _effective_add_quantity(self) -> int:
        return int(self.add_qty_spin.value())

    def _current_price_value(self) -> float:
        return float(self.current_price or self.stock.get("avg_price", 0) or 0)

    def _current_fx_rate(self) -> float:
        rate = float(self.usd_krw_rate or self.stock.get("buy_exchange_rate", 0) or 0)
        return rate if rate > 0 else 1.0

    def _amount_unit_price(self) -> float:
        price = self._current_price_value()
        if is_us_stock(self.stock) and self._amount_currency == "krw":
            return price * self._current_fx_rate()
        return price

    def _amount_step_value(self) -> float:
        price = abs(self._amount_unit_price())
        if price <= 0:
            return 1.0
        integer_part = int(price)
        if integer_part <= 0:
            return 1.0
        digits = len(str(integer_part))
        return float(10 ** max(0, digits - 1))

    def _sync_amount_spin_format(self):
        is_krw_amount = self._amount_currency == "krw"
        self.add_amount_spin.setDecimals(0 if is_krw_amount else 4)
        self.add_amount_spin.setSingleStep(self._amount_step_value())
        self.add_amount_spin.setSuffix("  원" if is_krw_amount else "  USD")
        self._apply_currency_button_style()

    def _set_amount_currency(self, currency: str):
        currency = "krw" if currency == "krw" else "usd"
        if self._amount_currency == currency:
            return
        self._amount_currency = currency
        self._sync_amount_spin_format()
        self._sync_amount_from_quantity()
        self._render_current()
        self._update_preview()

    def _sync_amount_from_quantity(self):
        self._syncing_buy_inputs = True
        amount = self._effective_add_quantity() * self._amount_unit_price()
        self.add_amount_spin.setValue(amount)
        self._syncing_buy_inputs = False

    def _on_add_qty_changed(self, _value: float):
        if self._syncing_buy_inputs:
            return
        self._sync_amount_from_quantity()
        self._update_preview()

    def _on_add_amount_changed(self, value: float):
        if self._syncing_buy_inputs:
            return
        price = self._amount_unit_price()
        quantity = int(float(value) // price) if price > 0 else 0
        self._syncing_buy_inputs = True
        self.add_qty_spin.setValue(quantity)
        self._syncing_buy_inputs = False
        self._update_preview()

    def _render_current(self):
        metrics = stock_metrics(self.stock, self.current_price, self.usd_krw_rate)
        self.qty_val.setText(f"{format_quantity(self.stock.get('quantity', 0))} 주")
        self.avg_val.setText(self._avg_price_text(self.stock, metrics))
        self._set_rate_label(self.rate_val, metrics["profit_rate"])
        self.price_val.setText(self._current_price_text())
        self.invest_val.setText(self._won_text(metrics["invest"]))
        self.eval_val.setText(self._won_text(metrics["eval"]))

    def _update_preview(self):
        self._preview = buy_preview(
            self.stock,
            self._effective_add_quantity(),
            self.current_price,
            self.usd_krw_rate,
        )
        preview_stock = self._preview["stock"]
        current_metrics = self._preview["current_metrics"]
        metrics = self._preview["preview_metrics"]
        rate_delta = self._preview["profit_rate_delta"]
        has_addition = self._effective_add_quantity() > 0

        self.preview_qty_val.setText(f"{format_quantity(preview_stock.get('quantity', 0))} 주")

        avg_text = self._avg_price_text(preview_stock, metrics)
        avg_delta = (
            self._avg_price_value(preview_stock, metrics)
            - self._avg_price_value(self.stock, current_metrics)
        )
        avg_delta_text, avg_color = self._delta_text(avg_delta, self._avg_price_text_for_delta)
        self.preview_avg_val.setText(
            f"{avg_text}  ({avg_delta_text})" if has_addition else avg_text
        )
        if not has_addition:
            avg_color = C["text"]
        self.preview_avg_val.setStyleSheet(
            f"color: {avg_color}; font-size: 13px; font-weight: bold;"
        )

        rate_text = self._rate_text(metrics["profit_rate"])
        rate_delta_text, rate_color = self._delta_text(
            rate_delta, lambda v: f"{v:.2f}%"
        )
        self.preview_rate_val.setText(
            f"{rate_text}  ({rate_delta_text})" if has_addition else rate_text
        )
        if not has_addition:
            rate_color = C["red"] if metrics["profit_rate"] >= 0 else C["blue"]
        self.preview_rate_val.setStyleSheet(
            f"color: {rate_color}; font-size: 13px; font-weight: bold;"
        )

        self.preview_invest_val.setText(self._won_text(metrics["invest"]))
        self.preview_invest_val.setStyleSheet(
            f"color: {C['text']}; font-size: 13px; font-weight: bold;"
        )
        self.preview_eval_val.setText(self._won_text(metrics["eval"]))
        self.preview_eval_val.setStyleSheet(
            f"color: {C['text']}; font-size: 13px; font-weight: bold;"
        )

    def accept(self):
        if self._effective_add_quantity() <= 0:
            QMessageBox.information(
                self,
                "매수 수량 확인",
                "추가 매수 수량이 1주 이상이 되도록 수량 또는 금액을 입력하세요.",
            )
            return
        super().accept()

    def get_data(self) -> dict:
        if self._preview is None:
            self._update_preview()
        stock = dict(self._preview["stock"])
        if is_us_stock(stock):
            stock["avg_price"] = round(float(stock.get("avg_price", 0)), 4)
        else:
            stock["avg_price"] = int(round(float(stock.get("avg_price", 0))))
        stock["quantity"] = round(float(stock.get("quantity", 0)), 3)
        return stock


# ─── 종목 추가 / 수정 다이얼로그 ──────────────────────────────────────────────
class StockDialog(QDialog):
    def __init__(self, parent=None, data: dict | None = None, watch_mode: bool = False,
                 tags: list[dict] | None = None):
        super().__init__(parent)
        self.is_edit = data is not None
        self.watch_mode = watch_mode   # 관심종목 모드: 평단가/수량 입력 숨김
        self._tags = tags or []        # 관심종목 태그 레지스트리 (추가/수정 시 태그 지정용)
        if watch_mode:
            self.setWindowTitle("관심종목 수정" if self.is_edit else "관심종목 추가")
        else:
            self.setWindowTitle("종목 수정" if self.is_edit else "종목 추가")
        self.setFixedSize(380 if watch_mode else 410, 270 if watch_mode else 360)
        self.setStyleSheet(DIALOG_STYLE)
        self._preview_result: dict | None = None

        layout = QFormLayout(self)
        self.form_layout = layout   # _collapse_price_fields 에서 행 접기용
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
        self.code_edit = SearchLineEdit()
        self.code_edit.setPlaceholderText("예: 삼성전자 / 005930")
        self.code_edit.editingFinished.connect(self._preview_name)
        # textComposed: 확정 텍스트뿐 아니라 IME 조합 중인 글자 변화까지 알린다
        self.code_edit.textComposed.connect(self._on_code_text_edited)
        # 종목명/코드 칸에서 Enter: 종목을 바로 추가하지 않고 검색 결과만 띄운다
        self.code_edit.searchRequested.connect(self._search_now)
        layout.addRow(self._row_label("종목 코드"), self.code_edit)

        # 종목 이름/티커 검색용 드롭다운 자동완성 (KR·US 공용, 항상 부착)
        self._search_model = QStandardItemModel(self)
        self._search_completer = _StockSearchCompleter(self._search_model, self)
        self._search_completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self._search_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._search_completer.activated[QModelIndex].connect(self._on_search_activated)
        self.code_edit.setCompleter(self._search_completer)
        # 드롭다운 팝업을 앱 다크 테마와 통일
        self._search_completer.popup().setStyleSheet(SEARCH_POPUP_STYLE)
        # 디바운스: 타이핑이 0.5초 멈춘 뒤 한 번만 검색
        # (한글 IME 조합 중인 마지막 글자는 SearchLineEdit.composedText()로 포함)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(500)
        self._search_timer.timeout.connect(self._run_search)
        self._last_search_query: str = ""

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

        # 미국 주식 매수 기준: 증권사마다 보유 화면에 보여주는 값이 달라(원화 단가가
        # 아예 안 보이는 곳도 있음), 무엇을 입력하든 매수 환율(buy_exchange_rate)
        # 하나로 환산해 저장한다. '모름'이면 저장하지 않고 계산 시 현재 환율로 폴백.
        self.basis_label = self._row_label("매수 기준")
        self.basis_combo = _NoScrollComboBox()
        self.basis_combo.addItem("원화 매입단가", "krw_unit")
        self.basis_combo.addItem("매수 환율", "fx_rate")
        self.basis_combo.addItem("원화 매입금액", "krw_total")
        self.basis_combo.addItem("모름", "unknown")
        self.basis_combo.setFixedWidth(120)
        self.basis_combo.setToolTip("증권사 보유 화면에 보이는 값에 맞춰 선택하세요")

        self.basis_spin = AutoSelectDoubleSpinBox()
        self.basis_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.basis_spin.setRange(0, 1_000_000_000_000)

        self.basis_hint = QLabel("현재 환율로 계산")
        self.basis_hint.setStyleSheet(f"color: {C['subtext']}; font-size: 11px;")

        self.basis_field = QWidget()
        basis_row = QHBoxLayout(self.basis_field)
        basis_row.setContentsMargins(0, 0, 0, 0)
        basis_row.setSpacing(8)
        basis_row.addWidget(self.basis_combo)
        basis_row.addWidget(self.basis_spin, 1)
        basis_row.addWidget(self.basis_hint, 1)
        layout.addRow(self.basis_label, self.basis_field)

        self.basis_combo.currentIndexChanged.connect(self._on_basis_changed)
        self._on_basis_changed()

        # 수량 (paintEvent로 ▲▼ 화살표 직접 그림)
        # 정수면 '1주', 사용자가 소수점 입력하면 '1.5주'처럼 trailing zero 없이 표시
        self.qty_spin = QuantitySpinBox()
        self.qty_spin.setRange(0.001, 1_000_000)
        self.qty_spin.setSingleStep(1)
        self.qty_spin.setDecimals(3)
        self.qty_spin.setSuffix("  주")
        self.qty_spin.setValue(1)
        self.qty_label = self._row_label("수  량")
        layout.addRow(self.qty_label, self.qty_spin)

        # 관심종목 모드: 태그 선택 — 추가/수정 시 바로 태그를 지정한다.
        if self.watch_mode:
            self.tag_combo = _NoScrollComboBox()
            self.tag_combo.addItem("없음", "")
            cur_tag = str((data or {}).get("tag") or "")
            sel = 0
            for i, t in enumerate(self._tags, start=1):
                self.tag_combo.addItem(
                    _color_icon(t.get("color", DEFAULT_TAG_COLOR)), t.get("name", ""), t["id"]
                )
                if t["id"] == cur_tag:
                    sel = i
            self.tag_combo.setCurrentIndex(sel)
            self.tag_combo.setIconSize(QSize(12, 12))
            layout.addRow(self._row_label("태그"), self.tag_combo)

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
            # 매수 기준(매수 환율) 프리필은 시장 적용(_on_market_changed) 이후에 한다.
            if data.get("name"):
                self._set_preview_found(data["name"])

        self._on_market_changed()

        # 매수 기준 프리필 (편집 모드·미국 주식): 저장된 매수 환율을 그대로 표시
        if self.is_edit and self.market() == MARKET_US:
            saved_rate = float((data or {}).get("buy_exchange_rate") or 0)
            if saved_rate > 0:
                self.basis_combo.setCurrentIndex(1)   # 매수 환율
                self.basis_spin.setValue(saved_rate)
            else:
                self.basis_combo.setCurrentIndex(3)   # 모름

        # 관심종목 모드: 평단가/원화단가/수량 행을 접어 코드·종목명만 입력받는다
        if self.watch_mode:
            self._collapse_price_fields()

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
        raw = self.code_edit.composedText().strip()
        code = raw.upper()
        self._preview_result = None
        if not code:
            self._set_preview_neutral()
            return
        market = self.market()
        self._set_preview_hint("조회 중...")
        self.preview_lbl.repaint()
        # 관심종목 모드: 입력이 지수(코드 또는 이름/별칭 정확일치)면 지수로 검증.
        # 드롭다운에서 고른 경우 code 가 지수 코드(KOSPI/^GSPC)라 index_by_code 로,
        # 직접 '코스피'/'나스닥' 등을 타이핑한 경우 index_exact_match 로 잡는다.
        if self.watch_mode:
            idx = index_by_code(code) or index_exact_match(raw, market)
            if idx:
                if fetch_index(idx["code"], idx["market"]):
                    self.code_edit.blockSignals(True)
                    self.code_edit.setText(idx["code"])
                    self.code_edit.blockSignals(False)
                    self._set_preview_found(idx["name"])
                    self._preview_result = idx
                else:
                    self._set_preview_error("지수 조회 실패")
                return
        # 1) 입력을 그대로 코드/티커로 보고 시세 API 호출.
        # 2) 실패하면 이름 검색으로 폴백해 첫 매칭의 코드/티커로 자동 채움.
        #    (사용자가 드롭다운에서 안 고르고 그냥 엔터/포커스 아웃 한 경우 안전망)
        if market == MARKET_US:
            result = fetch_us_stock(code)
            if not result:
                matches = search_us_stocks(raw, limit=1)
                if matches:
                    ticker = matches[0].get("symbol") or matches[0].get("code")
                    if ticker:
                        self.code_edit.setText(ticker)
                    result = {"name": matches[0]["name"]}
                    self._preview_result = matches[0]
        else:
            if len(code) == 6 and code.isdigit():
                result = fetch_stock(code)
            else:
                matches = search_korean_stocks(raw, limit=1)
                if matches:
                    self.code_edit.setText(matches[0]["code"])
                    self._preview_result = matches[0]
                    result = fetch_stock(matches[0]["code"]) or {"name": matches[0]["name"]}
                else:
                    result = None
        if result:
            self._set_preview_found(result["name"])
            if self._preview_result is None:
                self._preview_result = result
        else:
            self._set_preview_error("찾을 수 없는 종목")

    # ── 종목 이름/티커 자동완성 (KR·US 공용) ─────────────────────────────
    def _on_code_text_edited(self):
        """입력(확정/조합)이 바뀔 때마다 호출. 디바운스 후 현재 시장에 맞는 API 로 검색."""
        query = self.code_edit.composedText().strip()
        if not query:
            self._clear_search()
            return
        # 순수 6자리 숫자(= 한국 종목코드 직접 입력)만 드롭다운을 띄우지 않는다.
        # 글자가 섞이면(예: 6자 종목명 '카카오게임즈') 종목명 검색으로 본다.
        if self.market() == MARKET_KR and len(query) == 6 and query.isdigit():
            self._clear_search()
            return
        self._search_timer.start()

    def _clear_search(self):
        """드롭다운 후보·디바운스·중복검색 캐시를 모두 비운다.
        _last_search_query 까지 비워야, 검색어를 지웠다가 같은 종목명을 다시
        입력했을 때 _run_search 의 중복검색 가드(query == _last_search_query)에
        걸리지 않고 드롭다운이 다시 뜬다."""
        self._search_timer.stop()
        self._search_model.clear()
        self._last_search_query = ""

    def _search_now(self):
        """Enter 로 요청된 즉시 검색. 디바운스를 건너뛰고 바로 드롭다운을 띄운다.
        _last_search_query 를 비워, 직전과 같은 검색어여도 중복검색 가드에 막히지
        않고 결과가 다시 뜨게 한다."""
        self._search_timer.stop()
        self._last_search_query = ""
        self._run_search()

    def _run_search(self):
        query = self.code_edit.composedText().strip()
        if not query:
            return
        if query == self._last_search_query:
            return
        self._last_search_query = query
        market = self.market()
        if market == MARKET_US:
            matches = search_us_stocks(query, limit=10)
        else:
            matches = search_korean_stocks(query, limit=10)
        # 관심종목 모드: 현재 시장의 지수도 후보 맨 앞에 더한다 (보유엔 지수 없음)
        if self.watch_mode:
            matches = search_indices(query, market=market) + matches
        self._search_model.clear()
        for m in matches:
            code = m.get("code") or m.get("symbol")
            if not code:
                continue
            item = QStandardItem(f"{m.get('name', code)}  ({code})")
            # UserRole 에 코드 키를 정규화해서 저장 (US 응답은 'symbol' 만 있을 수 있음)
            data = dict(m)
            data["code"] = code
            item.setData(data, Qt.ItemDataRole.UserRole)
            self._search_model.appendRow(item)
        if self._search_model.rowCount() and self.code_edit.hasFocus():
            self._resize_search_popup()
            self._search_completer.complete()

    def _resize_search_popup(self):
        """드롭다운 팝업 폭을 가장 긴 후보에 맞춰 넓힌다.
        기본 QCompleter 팝업은 입력창 너비에 고정돼(showPopup 이 setGeometry 를
        line edit 폭으로 호출) 긴 종목명이 '...' 로 잘린다. 내용에 맞춘 최소 폭을
        주면 setGeometry 가 minimumWidth 로 클램프돼 팝업이 넓어진다."""
        popup = self._search_completer.popup()
        n = self._search_model.rowCount()
        if popup is None or n == 0:
            return
        # 항목 1개를 잘림 없이 담을 폭. 글꼴 실측(fm, 항목 좌우 padding 10px*2 포함)과
        # delegate 여백을 반영한 sizeHintForColumn 중 큰 값을 써서 프록시 갱신
        # 타이밍과 무관하게 안전.
        fm = popup.fontMetrics()
        text_w = max(fm.horizontalAdvance(self._search_model.item(r).text())
                     for r in range(n))
        item_w = max(text_w + 24, popup.sizeHintForColumn(0))
        # 뷰 자체 padding(4px*2) + 프레임 + 약간의 안전 여유
        width = item_w + 8 + 2 * popup.frameWidth() + 4
        # 항목이 많아 세로 스크롤바가 생기면 그 폭만큼 추가
        if n > self._search_completer.maxVisibleItems():
            width += popup.verticalScrollBar().sizeHint().width()
        # 화면 밖으로 넘치지 않게 상한
        screen = popup.screen()
        if screen is not None:
            width = min(width, screen.availableGeometry().width() - 40)
        popup.setMinimumWidth(width)

    def _on_search_activated(self, index: QModelIndex):
        data = index.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        self.code_edit.blockSignals(True)
        self.code_edit.setText(data["code"])
        self.code_edit.blockSignals(False)
        self._preview_name()

    def market(self) -> str:
        return MARKET_US if self.us_radio.isChecked() else MARKET_KR

    def _on_market_changed(self):
        market = self.market()
        self._preview_result = None
        if not self.is_edit:
            self._set_preview_neutral()
        # 시장이 바뀌면 이전 시장의 후보 목록·캐시된 쿼리를 비운다
        self._clear_search()
        if market == MARKET_US:
            self.code_edit.setPlaceholderText("예: Apple / AAPL")
            self.avg_label.setText("달러 매입단가")
            self.avg_spin.setDecimals(4)
            self.avg_spin.setSingleStep(1)
            self.avg_spin.setSuffix("  USD")
            if not self.is_edit:
                self.avg_spin.setValue(1.0000)
            self.basis_label.setVisible(True)
            self.basis_field.setVisible(True)
            self._on_basis_changed()
        else:
            self.code_edit.setPlaceholderText("예: 삼성전자 / 005930")
            self.avg_label.setText("평단가")
            self.avg_spin.setDecimals(0)
            self.avg_spin.setSingleStep(100)
            self.avg_spin.setSuffix("  원")
            self.basis_label.setVisible(False)
            self.basis_field.setVisible(False)
        # 관심종목 모드에서는 시장이 바뀌어도 가격/수량 행을 항상 접어둔다
        if getattr(self, "watch_mode", False):
            self._collapse_price_fields()

    def _on_basis_changed(self):
        """매수 기준 선택에 따라 값 입력칸의 단위/소수점/표시를 바꾼다."""
        mode = self.basis_combo.currentData()
        if mode == "fx_rate":
            self.basis_spin.setDecimals(2)
            self.basis_spin.setSingleStep(10)
            self.basis_spin.setRange(0, 100_000)
            self.basis_spin.setSuffix("  원/$")
        elif mode == "krw_total":
            self.basis_spin.setDecimals(0)
            self.basis_spin.setSingleStep(10_000)
            self.basis_spin.setRange(0, 1_000_000_000_000)
            self.basis_spin.setSuffix("  원")
        else:  # krw_unit
            self.basis_spin.setDecimals(0)
            self.basis_spin.setSingleStep(1_000)
            self.basis_spin.setRange(0, 1_000_000_000)
            self.basis_spin.setSuffix("  원/주")
        unknown = mode == "unknown"
        self.basis_spin.setVisible(not unknown)
        self.basis_hint.setVisible(unknown)

    def _buy_rate_from_basis(self, avg_price: float, quantity: float) -> float | None:
        """선택한 매수 기준 입력값을 매수 환율(원/$)로 환산한다.

        값이 비었거나(0) 환산이 불가능하면 None — 매수 환율을 저장하지 않아
        계산 시 현재 환율로 폴백한다('모름'과 동일).
        """
        mode = self.basis_combo.currentData()
        if mode == "unknown":
            return None
        value = self.basis_spin.value()
        if value <= 0 or avg_price <= 0:
            return None
        if mode == "fx_rate":
            rate = value
        elif mode == "krw_total":
            if quantity <= 0:
                return None
            rate = value / (avg_price * quantity)
        else:  # krw_unit
            rate = value / avg_price
        return round(rate, 4) if rate > 0 else None

    def _collapse_price_fields(self):
        """관심종목 다이얼로그: 평단가/원화단가/수량 행을 레이아웃에서 접는다."""
        for w in (self.avg_spin, self.basis_field, self.qty_spin):
            self.form_layout.setRowVisible(w, False)

    def _selected_tag(self) -> str:
        """관심종목 모드에서 콤보로 고른 태그 id (없음이면 "")."""
        if self.watch_mode and hasattr(self, "tag_combo"):
            return self.tag_combo.currentData() or ""
        return ""

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
        if self.watch_mode:
            code = self.code_edit.text().strip().upper()
            # 지수면 카탈로그 메타(코드/이름/시장/통화)를 그대로 저장 — 라디오 시장이
            # 아니라 카탈로그가 진실값이다. _preview_result 는 이름 정확일치로 잡힌 경우.
            idx = index_by_code(code)
            if idx is None and isinstance(self._preview_result, dict) \
                    and self._preview_result.get("type") == "index":
                idx = self._preview_result
            if idx:
                return {
                    "code":     idx["code"],
                    "name":     idx["name"],
                    "market":   idx["market"],
                    "currency": idx["currency"],
                    "type":     "index",
                    "tag":      self._selected_tag(),
                }
            # 관심종목(개별 종목): 평단가/수량/손익 없음 — 코드·시장·태그만.
            # 종목명은 매니저가 조회해 채운다.
            return {
                "code":     code,
                "market":   market,
                "currency": CURRENCY_USD if market == MARKET_US else CURRENCY_KRW,
                "type":     "stock",
                "tag":      self._selected_tag(),
            }
        avg_price = self.avg_spin.value()
        quantity = round(self.qty_spin.value(), 3)
        data = {
            "code":      self.code_edit.text().strip().upper(),
            "market":    market,
            "currency":  CURRENCY_USD if market == MARKET_US else CURRENCY_KRW,
            "avg_price": round(avg_price, 4) if market == MARKET_US else int(round(avg_price)),
            "quantity":  quantity,
        }
        if market == MARKET_US:
            rate = self._buy_rate_from_basis(avg_price, quantity)
            if rate is not None:
                data["buy_exchange_rate"] = rate
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
        else:
            self._stocks[stock_idx].pop("buy_exchange_rate", None)
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


# ─── 색상 선택 창 ─────────────────────────────────────────────────────────────
class ColorPickerDialog(QDialog):
    """프리셋 스와치 그리드로 색을 고르고, 필요하면 '직접 선택'으로 임의 색까지.

    선택 결과는 selected_color() 로 '#rrggbb' 소문자 문자열을 돌려준다.
    """

    SWATCH = 30
    COLS = 8

    def __init__(self, current: str = DEFAULT_TAG_COLOR, parent=None):
        super().__init__(parent)
        self.setWindowTitle("색상 선택")
        self.setStyleSheet(DIALOG_STYLE)
        self._color = current.lower() if _is_hex_color(current) else DEFAULT_TAG_COLOR

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(14)

        title = QLabel("태그 색상을 선택하세요")
        title.setStyleSheet(f"color: {C['text']}; font-size: 13px; font-weight: bold;")
        root.addWidget(title)

        grid = QGridLayout()
        grid.setSpacing(8)
        self._swatches: dict[str, QPushButton] = {}
        for i, color in enumerate(TAG_PALETTE):
            color = color.lower()
            btn = QPushButton()
            btn.setFixedSize(self.SWATCH, self.SWATCH)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, c=color: self._select(c))
            grid.addWidget(btn, i // self.COLS, i % self.COLS)
            self._swatches[color] = btn
        root.addLayout(grid)

        # 미리보기 + 직접 선택
        prev_row = QHBoxLayout()
        prev_row.setSpacing(10)
        self.preview = QFrame()
        self.preview.setFixedSize(28, 28)
        prev_row.addWidget(self.preview)
        self.hex_lbl = QLabel()
        self.hex_lbl.setStyleSheet(f"color: {C['subtext']}; font-size: 12px;")
        prev_row.addWidget(self.hex_lbl)
        prev_row.addStretch()
        custom_btn = QPushButton("직접 선택…")
        custom_btn.setProperty("flat", "true")
        custom_btn.clicked.connect(self._pick_custom)
        prev_row.addWidget(custom_btn)
        root.addLayout(prev_row)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("확인")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setProperty("flat", "true")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        self._select(self._color)

    def _swatch_style(self, color: str, selected: bool) -> str:
        border = f"2px solid {C['text']}" if selected else f"1px solid {C['surface2']}"
        return f"QPushButton {{ background: {color}; border: {border}; border-radius: 6px; }}"

    def _select(self, color: str):
        self._color = color.lower()
        for c, b in self._swatches.items():
            b.setStyleSheet(self._swatch_style(c, c == self._color))
        self.preview.setStyleSheet(
            f"background: {self._color}; border-radius: 6px; border: 1px solid {C['surface2']};"
        )
        self.hex_lbl.setText(self._color.upper())

    def _pick_custom(self):
        col = QColorDialog.getColor(QColor(self._color), self, "색상 직접 선택")
        if col.isValid():
            self._select(col.name())

    def selected_color(self) -> str:
        return self._color


# ─── 태그 추가/수정 창 ────────────────────────────────────────────────────────
class TagEditDialog(QDialog):
    """태그명 + 색상(색상 선택 창 연동)을 입력받는다."""

    def __init__(self, parent=None, tag: dict | None = None):
        super().__init__(parent)
        self.is_edit = tag is not None
        self.setWindowTitle("태그 수정" if self.is_edit else "태그 추가")
        self.setFixedSize(340, 190)
        self.setStyleSheet(DIALOG_STYLE)
        self._color = (tag or {}).get("color", DEFAULT_TAG_COLOR)
        if not _is_hex_color(self._color):
            self._color = DEFAULT_TAG_COLOR

        layout = QFormLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(24, 24, 24, 18)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.name_edit = AutoSelectLineEdit()
        self.name_edit.setPlaceholderText("예: 반도체")
        if self.is_edit:
            self.name_edit.setText(str(tag.get("name", "")))
        layout.addRow(self._label("태그명"), self.name_edit)

        self.color_btn = QPushButton()
        self.color_btn.setFixedHeight(32)
        self.color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.color_btn.clicked.connect(self._choose_color)
        layout.addRow(self._label("색상"), self.color_btn)
        self._update_color_btn()

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("확인")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setProperty("flat", "true")
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

    @staticmethod
    def _label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lbl.setFixedWidth(70)
        lbl.setMinimumHeight(32)
        return lbl

    def _choose_color(self):
        dlg = ColorPickerDialog(self._color, self)
        if dlg.exec():
            self._color = dlg.selected_color()
            self._update_color_btn()

    def _update_color_btn(self):
        self.color_btn.setText(self._color.upper())
        self.color_btn.setStyleSheet(
            f"QPushButton {{ background: {self._color}; color: {_contrast_text(self._color)};"
            f" border: 1px solid {C['surface2']}; border-radius: 7px; font-weight: bold; }}"
        )

    def _on_ok(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "입력 오류", "태그명을 입력하세요.")
            return
        self.accept()

    def get_data(self) -> dict:
        return {"name": self.name_edit.text().strip(), "color": self._color}


# ─── 태그 관리 창 ─────────────────────────────────────────────────────────────
class TagManagerDialog(QDialog):
    """태그 신규 추가 / 수정(이름·색상) / 삭제. get_tags() 로 갱신된 목록 반환."""

    COLS = ["색상", "태그명"]

    def __init__(self, tags: list[dict], watchlist: list[dict] | None = None, parent=None):
        super().__init__(parent)
        self._tags: list[dict] = tags   # 호출측에서 deepcopy 해서 전달
        # 태그 삭제 시 '종목도 삭제' vs '태그만 해제'를 적용할 대상(딥카피 — 취소 시 원복).
        self._watchlist: list[dict] = watchlist if watchlist is not None else []

        self.setWindowTitle("태그 관리")
        self.setMinimumSize(360, 380)
        self.setStyleSheet(DIALOG_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(12)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)     # 색상
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)   # 태그명
        self.table.setColumnWidth(0, 64)
        hdr.setStretchLastSection(False)
        hdr.setSectionsClickable(False)
        self.table.doubleClicked.connect(lambda _: self._edit_selected())
        root.addWidget(self.table, 1)

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
        root.addLayout(action_row)

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

    def _rebuild_table(self, select_row: int | None = None):
        self.table.setRowCount(0)
        for i, tag in enumerate(self._tags):
            self.table.insertRow(i)
            self._fill_row(i, tag)
        if select_row is not None and 0 <= select_row < self.table.rowCount():
            self.table.selectRow(select_row)

    def _fill_row(self, row: int, tag: dict):
        # 색상 스와치 (가운데 정렬 컨테이너)
        swatch = QFrame()
        swatch.setFixedSize(18, 18)
        swatch.setStyleSheet(
            f"background: {tag.get('color', DEFAULT_TAG_COLOR)};"
            f" border-radius: 5px; border: 1px solid {C['surface2']};"
        )
        container = QWidget()
        hl = QHBoxLayout(container)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addStretch()
        hl.addWidget(swatch)
        hl.addStretch()
        placeholder = QTableWidgetItem("")
        placeholder.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.table.setItem(row, 0, placeholder)
        self.table.setCellWidget(row, 0, container)

        name_item = QTableWidgetItem(str(tag.get("name", "")))
        name_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        name_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
        self.table.setItem(row, 1, name_item)

    def _add(self):
        dlg = TagEditDialog(parent=self)
        if not dlg.exec():
            return
        data = dlg.get_data()
        self._tags.append({"id": new_tag_id(), "name": data["name"], "color": data["color"]})
        self._rebuild_table(select_row=len(self._tags) - 1)

    def _edit_selected(self):
        row = self.table.currentRow()
        if not (0 <= row < len(self._tags)):
            return
        dlg = TagEditDialog(parent=self, tag=self._tags[row])
        if not dlg.exec():
            return
        data = dlg.get_data()
        self._tags[row]["name"] = data["name"]
        self._tags[row]["color"] = data["color"]
        self._rebuild_table(select_row=row)

    def _delete_selected(self):
        row = self.table.currentRow()
        if not (0 <= row < len(self._tags)):
            return
        tag = self._tags[row]
        tag_id = str(tag.get("id") or "")
        name = tag.get("name", "")
        members = [w for w in self._watchlist if str(w.get("tag") or "") == tag_id]
        cnt = len(members)

        if cnt == 0:
            # 부여된 관심종목이 없으면 단순 확인만
            ret = QMessageBox.question(
                self, "태그 삭제",
                f"태그 '{name}' 을(를) 삭제할까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return
        else:
            # 부여된 관심종목 처리 방식을 묻는다: 종목도 삭제 / 태그만 해제 / 취소
            box = QMessageBox(self)
            box.setWindowTitle("태그 삭제")
            box.setIcon(QMessageBox.Icon.Question)
            box.setText(f"태그 '{name}' 을(를) 삭제합니다.")
            box.setInformativeText(f"이 태그가 부여된 관심종목 {cnt}개를 어떻게 할까요?")
            del_btn = box.addButton("관심종목도 삭제", QMessageBox.ButtonRole.DestructiveRole)
            untag_btn = box.addButton("태그만 해제 (종목 유지)", QMessageBox.ButtonRole.AcceptRole)
            cancel_btn = box.addButton("취소", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(untag_btn)
            box.exec()
            clicked = box.clickedButton()
            if clicked is cancel_btn or clicked is None:
                return
            if clicked is del_btn:
                # 태그가 부여된 관심종목까지 함께 삭제
                self._watchlist[:] = [
                    w for w in self._watchlist if str(w.get("tag") or "") != tag_id
                ]
            else:
                # 종목은 유지하고 태그만 해제 ('태그 없음')
                for w in members:
                    w["tag"] = ""

        self._tags.pop(row)
        next_sel = min(row, len(self._tags) - 1) if self._tags else None
        self._rebuild_table(select_row=next_sel)

    def get_tags(self) -> list[dict]:
        return normalize_tags(self._tags)

    def get_watchlist(self) -> list[dict]:
        """태그 삭제 시 선택(종목 삭제/태그 해제)이 반영된 관심종목 목록."""
        return self._watchlist


# ─── 관심종목 관리 다이얼로그 ─────────────────────────────────────────────────
class ManageWatchlistDialog(QDialog):
    """관심종목을 표로 관리 — 추가 / 삭제 / 표시(ON·OFF) 토글 / 태그 부여.

    보유 관리(ManageStocksDialog)와 달리 평단가/수량/평가손익이 없다. 시세는
    일봉 기준이라 종목명/코드/시장·태그·표시 여부만 다룬다. 태그는 종목당 1개이며
    '태그 관리' 버튼으로 태그 자체(이름·색상)를 추가/수정/삭제한다.
    """

    # 0번 칸 헤더는 비워두고(라벨 ""), 그 자리에 '전체 선택' 체크박스를 올린다.
    COLS = ["", "종목명", "종목코드", "시장", "태그", "표시"]
    FILTER_ALL = "__ALL__"   # 태그 필터 '전체' 센티넬 (빈 문자열 ''은 '태그 없음'을 뜻함)

    def __init__(self, watchlist: list[dict], tags: list[dict] | None = None,
                 ma_settings: dict | None = None, holdings: list[dict] | None = None,
                 parent=None):
        super().__init__(parent)
        self._items: list[dict] = watchlist          # 호출측에서 deepcopy 해서 전달
        self._tags: list[dict] = tags or []          # 태그 레지스트리 (deepcopy)
        self._holdings: list[dict] = holdings or []   # 보유 종목 (보유중 태그 동기화용, 읽기 전용)
        self._check_boxes: list = []                  # 행별 선택 체크박스 (여러 개 한 번에 삭제용)
        self._tag_filter: str = self.FILTER_ALL       # 태그 필터 ("__ALL__"=전체, ""=태그없음, 그 외=tag id)
        self._row_item_indexes: list[int] = []        # 표의 행 → self._items 인덱스 매핑(필터 때문에 불일치)
        # 확대 일봉 팝업 표시 설정 (이동평균선 + 배경 종목명 + 표시 기간(개월))
        # popup_months 만 정수(1~6), 나머지는 on/off 불리언이다.
        self._ma_settings = {"ma5": True, "ma20": True, "ma60": True,
                             "show_name": True, "popup_months": 3}
        if isinstance(ma_settings, dict):
            for k in ("ma5", "ma20", "ma60", "show_name"):
                if k in ma_settings:
                    self._ma_settings[k] = bool(ma_settings[k])
            pm = ma_settings.get("popup_months")
            if isinstance(pm, (int, float)):
                self._ma_settings["popup_months"] = max(1, min(6, int(pm)))

        self.setWindowTitle("관심종목 관리")
        # 긴 종목명(예: State Street SPDR S&P …)이 잘리지 않게 기본 폭을 넉넉히.
        self.setMinimumSize(560, 400)
        self.resize(700, 470)
        self.setStyleSheet(DIALOG_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(12)

        # ── 태그 필터 (전체 / 태그 없음 / 각 태그) ─────────────────────────
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        filter_lbl = QLabel("태그 필터")
        filter_lbl.setStyleSheet(f"color: {C['subtext']}; font-size: 12px;")
        filter_row.addWidget(filter_lbl)
        self.filter_combo = _NoScrollComboBox()
        self.filter_combo.setMinimumWidth(170)
        self.filter_combo.setIconSize(QSize(12, 12))
        self.filter_combo.activated.connect(self._on_filter_changed)
        filter_row.addWidget(self.filter_combo)
        filter_row.addStretch()
        root.addLayout(filter_row)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        # 태그 콤보 글자(한글)가 위아래로 잘리지 않도록 행 높이를 충분히 준다
        self.table.verticalHeader().setDefaultSectionSize(38)

        # 드래그로 행 순서 변경 (보유 관리와 동일 방식). 태그 필터가 걸리면 표가
        # 부분 목록이라 전체 순서 동기화가 모호해 _update_drag_enabled 에서 끈다.
        self.table.setDragEnabled(True)
        self.table.setAcceptDrops(True)
        self.table.viewport().setAcceptDrops(True)
        self.table.setDropIndicatorShown(True)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.table.setDragDropOverwriteMode(False)
        # 드래그 정렬: 모델의 rowsMoved 시그널로 self._items 순서를 동기화
        self.table.model().rowsMoved.connect(self._on_rows_moved)

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)             # 선택 체크박스
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)            # 종목명
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)   # 코드
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)   # 시장
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)             # 태그 콤보
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)             # 표시 토글
        self.table.setColumnWidth(0, 44)
        self.table.setColumnWidth(4, 130)
        self.table.setColumnWidth(5, 64)
        hdr.setStretchLastSection(False)
        hdr.setSectionsClickable(False)
        root.addWidget(self.table, 1)

        # '선택' 헤더 칸에 올려두는 전체 선택 체크박스 (밑의 버튼 대신 헤더에서 토글).
        # 헤더는 위젯 배치를 직접 지원하지 않아 오버레이로 얹고 위치를 따라 맞춘다.
        self._header_check = QCheckBox()
        self._header_check.setToolTip("전체 선택 / 해제")
        self._header_check.setStyleSheet("QCheckBox::indicator { width: 16px; height: 16px; }")
        self._header_check.toggled.connect(self._toggle_all_checks)
        # 행 체크박스(_centered)와 똑같은 stretch-가운데 레이아웃으로 감싸 셀 체크박스와
        # 좌우 정렬을 정확히 맞춘다. (move+sizeHint 픽셀 계산은 macOS 네이티브 체크박스
        # 메트릭과 어긋나 헤더 체크박스만 살짝 삐뚤어 보였다.)
        self._header_check_holder = self._centered(self._header_check)
        self._header_check_holder.setParent(hdr)
        hdr.sectionResized.connect(lambda *a: self._reposition_header_check())
        hdr.geometriesChanged.connect(self._reposition_header_check)

        # ── 확대 일봉 팝업 이동평균선 표시 토글 (5·20·60일) ─────────────────
        # 관심종목 위에 마우스를 올리면 뜨는 확대 차트에 그릴 이동평균선을 고른다.
        ma_row = QHBoxLayout()
        ma_row.setSpacing(14)
        ma_lbl = QLabel("확대 차트 이동평균선")
        ma_lbl.setStyleSheet(f"color: {C['subtext']}; font-size: 12px;")
        ma_row.addWidget(ma_lbl)
        self._ma_checks: dict[str, QCheckBox] = {}
        for period, key in ((5, "ma5"), (20, "ma20"), (60, "ma60")):
            cb = QCheckBox(f"{period}일선")
            cb.setChecked(bool(self._ma_settings.get(key, True)))
            color = MA_COLORS.get(period, C['text'])
            cb.setStyleSheet(
                f"QCheckBox {{ color: {color}; font-size: 12px; font-weight: bold; spacing: 6px; }}"
                f"QCheckBox::indicator {{ width: 15px; height: 15px; }}"
            )
            self._ma_checks[key] = cb
            ma_row.addWidget(cb)
        # 확대 차트 배경에 종목명을 은은하게 깔지 여부 (이동평균선과 별개 옵션)
        self._show_name_check = QCheckBox("종목명표시")
        self._show_name_check.setChecked(bool(self._ma_settings.get("show_name", True)))
        self._show_name_check.setStyleSheet(
            f"QCheckBox {{ color: {C['text']}; font-size: 12px; spacing: 6px; }}"
            f"QCheckBox::indicator {{ width: 15px; height: 15px; }}"
        )
        ma_row.addWidget(self._show_name_check)

        # 확대 차트 표시 기간 (1~6개월) — 캔들 크기·세로는 그대로, 가로 폭만 비례 확장.
        ma_row.addSpacing(8)
        period_lbl = QLabel("기간")
        period_lbl.setStyleSheet(f"color: {C['subtext']}; font-size: 12px;")
        ma_row.addWidget(period_lbl)
        self._period_combo = _NoScrollComboBox()
        self._period_combo.setToolTip("확대 차트에 표시할 기간 — 길수록 차트가 가로로 넓어집니다")
        for m in range(1, 7):
            self._period_combo.addItem(f"{m}개월", m)
        cur = int(self._ma_settings.get("popup_months", 3))
        idx = self._period_combo.findData(cur)
        self._period_combo.setCurrentIndex(idx if idx >= 0 else 2)
        self._period_combo.setFixedWidth(84)
        ma_row.addWidget(self._period_combo)
        ma_row.addStretch()
        root.addLayout(ma_row)

        # ── 행 액션 (추가 / 삭제 / 태그 관리) ──────────────────────────────
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        add_btn = QPushButton("➕  추가")
        add_btn.clicked.connect(self._add)
        action_row.addWidget(add_btn)
        del_btn = QPushButton("🗑  삭제")
        del_btn.setProperty("flat", "true")
        del_btn.setToolTip("체크한 항목을 모두 삭제합니다. 체크가 없으면 선택한 행을 삭제합니다.")
        del_btn.clicked.connect(self._delete_selected)
        action_row.addWidget(del_btn)
        action_row.addStretch()
        # 보유 종목을 '보유중' 태그로 한 번에 동기화 (보유 목록을 받은 경우에만 노출)
        if self._holdings:
            sync_btn = QPushButton("📥  보유종목 동기화")
            sync_btn.setProperty("flat", "true")
            sync_btn.setToolTip("현재 보유 중인 종목을 '보유중' 태그로 관심종목에 추가/정리합니다.")
            sync_btn.clicked.connect(self._sync_holdings)
            action_row.addWidget(sync_btn)
        tag_btn = QPushButton("🏷  태그 관리")
        tag_btn.setProperty("flat", "true")
        tag_btn.clicked.connect(self._open_tag_manager)
        action_row.addWidget(tag_btn)
        root.addLayout(action_row)

        # ── 확인 / 취소 ───────────────────────────────────────────────────
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("확인")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setProperty("flat", "true")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        self._populate_filter_combo()
        # self._items 를 그룹 순서로 정규화('전체' 뷰 = floating 위젯과 같은 그룹 조합)
        self._regroup_items()
        # 초기 필터는 '전체'라 드래그는 꺼진 상태로 시작
        self._update_drag_enabled()
        self._rebuild_table()

    # ── 태그 필터 ─────────────────────────────────────────────────────────
    def _populate_filter_combo(self):
        """필터 콤보를 (전체 / 태그 없음 / 각 태그)로 다시 채운다.
        현재 필터가 삭제된 태그를 가리키면 '전체'로 되돌린다."""
        combo = self.filter_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("전체", self.FILTER_ALL)
        combo.addItem("— 태그 없음 —", "")
        for tag in self._tags:
            combo.addItem(
                _color_icon(tag.get("color", DEFAULT_TAG_COLOR)), tag.get("name", ""), tag["id"]
            )
        valid = {self.FILTER_ALL, ""} | {t["id"] for t in self._tags}
        if self._tag_filter not in valid:
            self._tag_filter = self.FILTER_ALL
        idx = combo.findData(self._tag_filter)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _on_filter_changed(self, _idx: int):
        self._tag_filter = self.filter_combo.currentData()
        self._update_drag_enabled()
        self._rebuild_table()

    def _update_drag_enabled(self):
        """드래그 순서 변경은 특정 태그(또는 '태그 없음')로 필터링했을 때만 허용한다.
        관심종목은 floating 위젯에서 태그 그룹 단위로 보이므로 순서도 그룹 안에서만
        의미가 있다. '전체'는 각 그룹 순서를 이어 붙여 보여주기만 하고 드래그는 끈다."""
        per_tag = self._tag_filter != self.FILTER_ALL
        self.table.setDragEnabled(per_tag)
        self.table.setAcceptDrops(per_tag)
        self.table.viewport().setAcceptDrops(per_tag)
        self.table.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove
            if per_tag else QAbstractItemView.DragDropMode.NoDragDrop
        )

    def _regroup_items(self):
        """self._items 를 태그 레지스트리 순서로 묶는다(각 그룹 내부의 상대 순서는 유지).
        floating 위젯의 그룹 배치(_compute_watch_groups: 태그 등록순 + '태그 없음' 맨 뒤)와
        같은 순서라 '전체' 뷰가 실제 떠 있는 관심 그룹 순서와 일치한다. 그룹 내부 순서는
        특정 태그 필터에서 드래그로 정한 self._items 상대 순서가 그대로 보존된다."""
        tag_ids = [t["id"] for t in self._tags]
        buckets: dict[str, list] = {tid: [] for tid in tag_ids}
        untagged: list = []
        for it in self._items:
            tag = str(it.get("tag") or "")
            (buckets[tag] if tag in buckets else untagged).append(it)
        ordered: list = []
        for tid in tag_ids:
            ordered.extend(buckets[tid])
        ordered.extend(untagged)
        self._items = ordered

    def _on_rows_moved(self, parent, start, end, dest_parent, dest_row):
        """드래그로 행을 옮기면 같은 태그 그룹 안에서 self._items 순서를 갱신한다.
        드래그는 특정 태그 필터일 때만 켜지므로(전체는 비활성) 여기 도달하면 표에는
        같은 태그 항목만 보인다. _row_item_indexes(표 행 → self._items 인덱스)로 그
        그룹이 점유한 슬롯들을 찾아 새 순서로 다시 채운다 — 다른 그룹은 건드리지 않는다.
        SingleSelection 이라 한 번에 한 행만 이동하며, Qt 의 dest_row 는 '이동 전
        좌표계' 기준이라 아래로 끌 때 한 칸 보정한다."""
        order = list(self._row_item_indexes)        # 이 그룹 항목들의 self._items 인덱스(표시 순서)
        if not (0 <= start < len(order)):
            self._rebuild_table()
            return
        moved = order.pop(start)
        insert_at = dest_row if dest_row < start else dest_row - 1
        insert_at = max(0, min(insert_at, len(order)))
        order.insert(insert_at, moved)
        # 이 그룹이 차지하던 self._items 슬롯(위치)에 새 순서대로 항목을 재배치
        slots = sorted(self._row_item_indexes)
        reordered = [self._items[i] for i in order]
        for slot, it in zip(slots, reordered):
            self._items[slot] = it
        # cell widget(체크박스/태그 콤보/토글)은 행 이동 시 자동으로 따라오지 않으므로
        # 표를 다시 그려 위치와 콜백의 인덱스 캡처를 동기화한다
        self._rebuild_table()

    def _matches_tag_filter(self, item: dict) -> bool:
        if self._tag_filter == self.FILTER_ALL:
            return True
        return str(item.get("tag") or "") == self._tag_filter

    def _item_index_for_row(self, row: int) -> int | None:
        if 0 <= row < len(self._row_item_indexes):
            return self._row_item_indexes[row]
        return None

    def _rebuild_table(self, select_row: int | None = None):
        """select_row 는 self._items 인덱스. 현재 필터에 보이면 그 행을 선택한다."""
        self._check_boxes = []
        self._row_item_indexes = []
        # 재구성 중 setRowCount/insertRow 가 rowsMoved 핸들러를 깨우지 않도록 차단
        self.table.model().rowsMoved.disconnect(self._on_rows_moved)
        try:
            self.table.setRowCount(0)
            for item_idx, item in enumerate(self._items):
                if not self._matches_tag_filter(item):
                    continue
                row = self.table.rowCount()
                self.table.insertRow(row)
                self._row_item_indexes.append(item_idx)
                self._fill_row(row, item, item_idx)
        finally:
            self.table.model().rowsMoved.connect(self._on_rows_moved)
        # 표를 다시 그리면 모든 체크가 풀리므로 헤더 '전체 선택'도 초기화(신호 차단)
        if getattr(self, "_header_check", None) is not None:
            self._header_check.blockSignals(True)
            self._header_check.setChecked(False)
            self._header_check.blockSignals(False)
        if select_row is not None and select_row in self._row_item_indexes:
            self.table.selectRow(self._row_item_indexes.index(select_row))

    def _reposition_header_check(self):
        """전체 선택 체크박스 오버레이를 헤더 0번 칸과 같은 영역에 맞춘다.
        오버레이 내부 _centered 레이아웃이 체크박스를 가운데로 둬 행 체크박스와
        좌우가 정확히 정렬된다 (sizeHint 의존 픽셀 계산을 쓰지 않는다)."""
        holder = getattr(self, "_header_check_holder", None)
        if holder is None:
            return
        hdr = self.table.horizontalHeader()
        x = hdr.sectionViewportPosition(0)
        w = hdr.sectionSize(0)
        holder.setGeometry(x, 0, w, hdr.height())
        holder.show()

    def showEvent(self, event):
        super().showEvent(event)
        self._reposition_header_check()

    @staticmethod
    def _centered(widget: QWidget) -> QWidget:
        """셀 위젯을 가운데 정렬해 감싸는 컨테이너."""
        box = QWidget()
        hl = QHBoxLayout(box)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addStretch()
        hl.addWidget(widget)
        hl.addStretch()
        return box

    def _fill_row(self, row: int, item: dict, item_idx: int):
        # 0번: 선택 체크박스 (여러 개 골라 한 번에 삭제)
        check = QCheckBox()
        check.setStyleSheet("QCheckBox::indicator { width: 16px; height: 16px; }")
        ph_check = QTableWidgetItem("")
        ph_check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDragEnabled)
        self.table.setItem(row, 0, ph_check)
        self.table.setCellWidget(row, 0, self._centered(check))
        self._check_boxes.append(check)

        name = item.get("name", item.get("code", ""))
        code = item.get("code", "")
        market = "미국" if str(item.get("market", "")).upper() == MARKET_US else "한국"
        for offset, text in enumerate([name, code, market]):
            col = offset + 1                    # 0번은 체크박스 칸이라 한 칸 밀림
            cell = QTableWidgetItem(text)
            align = Qt.AlignmentFlag.AlignLeft if col == 1 else Qt.AlignmentFlag.AlignCenter
            cell.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
            cell.setFlags(
                Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsDragEnabled
            )
            if col == 1:
                cell.setToolTip(name)           # 폭이 좁아 잘려도 hover 로 전체 이름 확인
            self.table.setItem(row, col, cell)

        # 태그 콤보박스 (col 4) — — 없음 — + 등록된 태그들, 종목당 1개.
        # 필터 때문에 행 인덱스 != 항목 인덱스 → 콜백엔 항목 인덱스(item_idx)를 넘긴다.
        combo = self._make_tag_combo(item.get("tag", ""))
        combo.activated.connect(lambda _, idx=item_idx, c=combo: self._on_tag_changed(idx, c))
        placeholder_tag = QTableWidgetItem("")
        placeholder_tag.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDragEnabled)
        self.table.setItem(row, 4, placeholder_tag)
        self.table.setCellWidget(row, 4, combo)

        # 표시 토글 스위치 (col 5, ON=표시 OFF=숨김)
        hidden = bool(item.get("hidden", False))
        toggle = ToggleSwitch(checked=not hidden)
        toggle.toggled.connect(lambda checked, idx=item_idx: self._on_visibility_toggled(idx, checked))
        placeholder = QTableWidgetItem("")
        placeholder.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDragEnabled)
        self.table.setItem(row, 5, placeholder)
        self.table.setCellWidget(row, 5, self._centered(toggle))

    def _make_tag_combo(self, current_tag_id: str) -> QComboBox:
        """태그 선택 콤보. 첫 항목은 '없음'(빈 id), 이어서 색 아이콘+이름.
        닫힌 상태에서 휠로 값이 안 바뀌도록 _NoScrollComboBox 사용."""
        combo = _NoScrollComboBox()
        combo.addItem("없음", "")
        select_idx = 0
        for i, tag in enumerate(self._tags, start=1):
            combo.addItem(_color_icon(tag.get("color", DEFAULT_TAG_COLOR)), tag.get("name", ""), tag["id"])
            if tag["id"] == current_tag_id:
                select_idx = i
        combo.setCurrentIndex(select_idx)
        combo.setIconSize(QSize(12, 12))
        # 뒤 배경/테두리 없이 깔끔하게(셀에 녹아들게). 세로 패딩 0 으로 글자가
        # 옆 칸들과 같은 높이에서 중앙 정렬되게 한다(셀 위젯을 직접 배치).
        combo.setStyleSheet(f"""
            QComboBox {{
                background: transparent;
                border: none;
                padding: 0px 6px;
                color: {C['text']};
                font-size: 12px;
            }}
            QComboBox::drop-down {{
                border: none;
                width: 16px;
            }}
            QComboBox QAbstractItemView {{
                background: {C['bg']};
                color: {C['text']};
                border: 1px solid {C['surface2']};
                border-radius: 6px;
                padding: 4px;
                outline: 0;
                selection-background-color: {C['surface']};
                selection-color: {C['text']};
            }}
        """)
        return combo

    def _on_tag_changed(self, item_idx: int, combo: QComboBox):
        if 0 <= item_idx < len(self._items):
            self._items[item_idx]["tag"] = combo.currentData() or ""
            # 태그가 바뀌면 소속 그룹이 달라지므로 그룹 순서를 다시 정규화하고 표를 갱신한다.
            # (특정 태그로 필터 중이면 그 항목이 목록에서 빠지고, '전체'면 새 그룹 위치로 이동)
            self._regroup_items()
            self._rebuild_table()

    def _on_visibility_toggled(self, item_idx: int, checked: bool):
        if 0 <= item_idx < len(self._items):
            self._items[item_idx]["hidden"] = not checked

    def _open_tag_manager(self):
        dlg = TagManagerDialog(
            tags=copy.deepcopy(self._tags),
            watchlist=copy.deepcopy(self._items),
            parent=self,
        )
        if not dlg.exec():
            return
        self._tags = dlg.get_tags()
        # 태그 삭제 시 고른 처리(종목 삭제 / 태그만 해제)가 반영된 목록을 받는다
        self._items = dlg.get_watchlist()
        # 혹시 남은 dangling 태그 참조는 비워 표시(콤보)를 안전하게 갱신 (안전망)
        prune_watch_tags(self._items, self._tags)
        # 태그 추가/삭제/재정렬이 있었을 수 있으니 그룹 순서를 다시 정규화
        self._regroup_items()
        # 태그가 추가/삭제됐을 수 있으니 필터 콤보도 다시 채운다(없어진 태그면 전체로)
        self._populate_filter_combo()
        self._update_drag_enabled()  # 필터가 전체로 되돌려졌을 수 있어 드래그 상태 갱신
        self._rebuild_table()

    # ── 보유 종목 → '보유중' 태그 동기화 ──────────────────────────────────
    HOLDING_TAG_NAME = "보유중"
    HOLDING_TAG_COLOR = "#a6e3a1"   # 초록 — 보유 종목 묶음 표시용

    def _ensure_holding_tag(self) -> str:
        """'보유중' 태그를 확보해 id 를 돌려준다 — 같은 이름 있으면 재사용, 없으면 생성.
        실제로 붙일 종목이 생긴 시점에만 호출해 빈 태그가 만들어지지 않게 한다."""
        tag = next((t for t in self._tags if t.get("name") == self.HOLDING_TAG_NAME), None)
        if tag is None:
            tag = {"id": new_tag_id(), "name": self.HOLDING_TAG_NAME, "color": self.HOLDING_TAG_COLOR}
            self._tags.append(tag)
        return tag["id"]

    def _sync_holdings(self):
        """보유 종목 중 '태그가 없는 것'만 '보유중' 태그로 채운다(비파괴, 옮기지 않음).
        - 관심종목에 없는 보유 종목: '보유중' 태그로 추가
        - 이미 있지만 태그가 없는 보유 종목: '보유중' 태그 지정
        - 이미 다른 태그가 있는 종목: 그대로 둠(옮기지 않음)
        보유에서 빠진 종목을 관심에서 지우진 않는다(수동 정리)."""
        if not self._holdings:
            QMessageBox.information(self, "보유종목 동기화", "보유 중인 종목이 없습니다.")
            return

        ret = QMessageBox.question(
            self, "보유종목 동기화",
            f"보유 종목 중 태그가 없는 종목을 '{self.HOLDING_TAG_NAME}' 태그로 추가합니다.\n\n"
            f"· 관심종목에 없으면 추가\n"
            f"· 이미 있고 태그가 없으면 '{self.HOLDING_TAG_NAME}' 지정\n"
            f"· 이미 다른 태그가 있으면 그대로 둠\n\n진행할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        by_code = {str(w.get("code") or "").upper(): w for w in self._items}
        tag_id = ""
        added = tagged = 0
        for s in self._holdings:
            code = str(s.get("code") or "").strip().upper()
            if not code:
                continue
            existing = by_code.get(code)
            # 이미 있고 태그가 붙어 있으면 옮기지 않는다
            if existing is not None and existing.get("tag"):
                continue
            if not tag_id:                       # 붙일 게 생긴 순간에만 태그 확보
                tag_id = self._ensure_holding_tag()
            if existing is None:
                market = str(s.get("market") or MARKET_KR).upper()
                self._items.append({
                    "code":     code,
                    "name":     s.get("name", code),
                    "market":   market,
                    "currency": CURRENCY_USD if is_us_stock(s) else CURRENCY_KRW,
                    "type":     "stock",
                    "tag":      tag_id,
                })
                added += 1
            else:                                # 있지만 태그가 비어 있던 경우
                existing["tag"] = tag_id
                tagged += 1

        # 동기화 결과(신규/지정)가 가려지지 않도록 '전체'로 보여주고 필터 콤보 갱신
        self._tag_filter = self.FILTER_ALL
        self._regroup_items()        # 새로 추가/태그지정된 항목을 그룹 순서로 정렬
        self._populate_filter_combo()
        self._update_drag_enabled()  # '전체'로 돌아왔으니 드래그는 끈다
        self._rebuild_table()
        QMessageBox.information(
            self, "보유종목 동기화",
            f"'{self.HOLDING_TAG_NAME}' 태그로 추가했습니다.\n신규 추가 {added}개 · 태그 지정 {tagged}개",
        )

    def _add(self):
        dlg = StockDialog(watch_mode=True, parent=self, tags=self._tags)
        if not dlg.exec():
            return
        d = dlg.get_data()
        code = d["code"]
        if not code:
            return
        if any(w["code"] == code for w in self._items):
            QMessageBox.information(self, "알림", f"'{code}'는 이미 관심종목에 있습니다.")
            return
        result = fetch_quote_for_stock(d)
        if not result:
            QMessageBox.warning(
                self, "조회 실패",
                f"종목코드 '{code}'를 찾을 수 없습니다.\n코드를 다시 확인해 주세요.",
            )
            return
        d["name"] = result["name"]
        self._items.append(d)
        self._regroup_items()        # 새 항목을 제 태그 그룹 위치로 정렬
        # 새 항목이 현재 태그 필터에 가려지지 않도록 '전체'로 보여준다
        self._tag_filter = self.FILTER_ALL
        self._populate_filter_combo()
        self._update_drag_enabled()  # '전체'로 돌아왔으니 드래그는 끈다
        sel = next((i for i, w in enumerate(self._items) if w is d), None)
        self._rebuild_table(select_row=sel)

    def _toggle_all_checks(self, checked: bool):
        for cb in self._check_boxes:
            cb.setChecked(checked)

    def _delete_selected(self):
        # 체크된 행 → 항목 인덱스. 없으면 현재 선택된 행 1개를 대상으로 한다(필터로 행≠항목).
        targets = []
        for row, cb in enumerate(self._check_boxes):
            if cb.isChecked():
                idx = self._item_index_for_row(row)
                if idx is not None:
                    targets.append(idx)
        if not targets:
            idx = self._item_index_for_row(self.table.currentRow())
            if idx is not None:
                targets = [idx]
        targets = sorted(set(targets))
        if not targets:
            QMessageBox.information(self, "삭제", "삭제할 항목을 체크하거나 선택하세요.")
            return

        if len(targets) == 1:
            nm = self._items[targets[0]].get("name", self._items[targets[0]].get("code", ""))
            msg = f"'{nm}' 을(를) 관심종목에서 삭제할까요?"
        else:
            msg = f"선택한 관심종목 {len(targets)}개를 삭제할까요?"
        ret = QMessageBox.question(
            self, "삭제 확인", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        for i in sorted(targets, reverse=True):   # 뒤에서부터 지워 인덱스 밀림 방지
            if 0 <= i < len(self._items):
                self._items.pop(i)
        self._rebuild_table()

    def get_watchlist(self) -> list[dict]:
        return self._items

    def get_tags(self) -> list[dict]:
        return self._tags

    def get_ma_settings(self) -> dict:
        """확대 일봉 팝업 표시 설정 {'ma5','ma20','ma60','show_name': bool, 'popup_months': int}."""
        out = {key: cb.isChecked() for key, cb in self._ma_checks.items()}
        out["show_name"] = self._show_name_check.isChecked()
        out["popup_months"] = int(self._period_combo.currentData() or 3)
        return out
