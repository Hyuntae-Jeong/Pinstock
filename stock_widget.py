#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
한국 주식 위젯 v1.0
- 종목별 실시간 현재가 표시 (네이버 금융 API)
- ▼ 버튼 클릭 시 평단가·수량·손익 확장, 5초 후 자동 축소
- 시스템 트레이에서 종목 추가/제거
- 위치는 자동 저장
"""

import sys
import json
import os
import copy
import requests

from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QDialog, QFormLayout,
    QLineEdit, QSpinBox, QDialogButtonBox,
    QSystemTrayIcon, QMenu, QFrame, QMessageBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics, QColor, QPixmap, QPainter, QIcon, QAction, QBrush, QPen

# ─── 설정 파일 경로 ────────────────────────────────────────────────────────────
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stocks.json")

# ─── 색상 테마 (다크 / Catppuccin Mocha 계열) ────────────────────────────────
C = {
    "bg":       "#1e1e2e",
    "bg2":      "#181825",
    "surface":  "#313244",
    "surface2": "#45475a",
    "text":     "#cdd6f4",
    "subtext":  "#a6adc8",
    "blue":     "#89b4fa",
    "red":      "#f38ba8",
    "green":    "#a6e3a1",
    "border":   "#313244",
}

TRAY_MENU_STYLE = f"""
QMenu {{
    background: {C['bg']};
    color: {C['text']};
    border: 1px solid {C['border']};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{
    padding: 7px 20px;
    border-radius: 5px;
}}
QMenu::item:selected {{
    background: {C['surface']};
}}
QMenu::separator {{
    height: 1px;
    background: {C['border']};
    margin: 4px 8px;
}}
"""

DIALOG_STYLE = f"""
QDialog {{
    background: {C['bg']};
    color: {C['text']};
}}
QLabel {{
    color: {C['subtext']};
    font-size: 12px;
}}
QLineEdit, QSpinBox {{
    background: {C['surface']};
    color: {C['text']};
    border: 1px solid {C['surface2']};
    border-radius: 7px;
    padding: 7px 10px;
    font-size: 13px;
    selection-background-color: {C['blue']};
}}
QLineEdit:focus, QSpinBox:focus {{
    border: 1px solid {C['blue']};
}}
QSpinBox::up-button, QSpinBox::down-button {{
    background: {C['surface2']};
    border: none;
    border-radius: 3px;
    width: 16px;
}}
QPushButton {{
    background: {C['blue']};
    color: {C['bg']};
    border: none;
    border-radius: 7px;
    padding: 8px 20px;
    font-size: 13px;
    font-weight: bold;
}}
QPushButton:hover {{
    background: #b4befe;
}}
QPushButton[flat="true"] {{
    background: {C['surface']};
    color: {C['text']};
}}
QPushButton[flat="true"]:hover {{
    background: {C['surface2']};
}}
QTableWidget {{
    background: {C['bg2']};
    color: {C['text']};
    border: 1px solid {C['surface2']};
    border-radius: 7px;
    gridline-color: {C['surface']};
    selection-background-color: {C['surface2']};
    selection-color: {C['text']};
    font-size: 12px;
}}
QTableWidget::item {{
    padding: 6px 8px;
    border: none;
}}
QHeaderView::section {{
    background: {C['surface']};
    color: {C['subtext']};
    border: none;
    border-right: 1px solid {C['bg2']};
    padding: 6px 8px;
    font-size: 11px;
    font-weight: bold;
}}
QHeaderView::section:last {{
    border-right: none;
}}
"""


# ─── 네이버 금융 API ───────────────────────────────────────────────────────────
def fetch_stock(code: str) -> dict | None:
    """네이버 금융 모바일 API로 현재가 조회"""
    url = f"https://m.stock.naver.com/api/stock/{code}/basic"
    try:
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=5,
        )
        if r.status_code != 200:
            return None
        d = r.json()
        return {
            "name":         d.get("stockName", code),
            "price":        int(str(d.get("closePrice", "0")).replace(",", "")),
            "change_rate":  float(d.get("fluctuationsRatio", 0)),
            "change_price": int(str(d.get("compareToPreviousClosePrice", "0")).replace(",", "")),
        }
    except Exception as e:
        print(f"[fetch_stock] {code} 오류: {e}")
        return None


# ─── 종목 추가 / 수정 다이얼로그 ──────────────────────────────────────────────
class StockDialog(QDialog):
    def __init__(self, parent=None, data: dict | None = None):
        super().__init__(parent)
        self.is_edit = data is not None
        self.setWindowTitle("종목 수정" if self.is_edit else "종목 추가")
        self.setFixedSize(320, 230)
        self.setStyleSheet(DIALOG_STYLE)

        layout = QFormLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # 종목코드
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("예: 005930  (삼성전자)")
        layout.addRow("종목 코드", self.code_edit)

        # 평단가
        self.avg_spin = QSpinBox()
        self.avg_spin.setRange(1, 10_000_000)
        self.avg_spin.setSingleStep(100)
        self.avg_spin.setSuffix("  원")
        layout.addRow("평단가", self.avg_spin)

        # 수량
        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(1, 1_000_000)
        self.qty_spin.setSuffix("  주")
        layout.addRow("수  량", self.qty_spin)

        # 기존 데이터 채우기
        if self.is_edit:
            self.code_edit.setText(data["code"])
            self.code_edit.setReadOnly(True)
            self.avg_spin.setValue(int(data.get("avg_price", 0)))
            self.qty_spin.setValue(int(data.get("quantity", 1)))

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

    def get_data(self) -> dict:
        return {
            "code":      self.code_edit.text().strip(),
            "avg_price": self.avg_spin.value(),
            "quantity":  self.qty_spin.value(),
        }


# ─── 종목 일괄 관리 다이얼로그 ────────────────────────────────────────────────
class ManageStocksDialog(QDialog):
    """현재 보유 종목들을 표 형태로 일괄 관리하는 다이얼로그."""

    COLS = ["종목명", "종목코드", "평단가", "수량"]

    def __init__(self, stocks: list[dict], parent=None):
        super().__init__(parent)
        self._stocks: list[dict] = stocks   # 호출측에서 deepcopy 해서 전달
        self.setWindowTitle("종목 관리")
        self.setMinimumSize(520, 400)
        self.setStyleSheet(DIALOG_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(12)

        # ── 표 ─────────────────────────────────────────────────────────────
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
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
        hdr.setStretchLastSection(False)

        # 더블클릭으로도 수정 가능
        self.table.doubleClicked.connect(lambda _: self._edit_selected())

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
        # rowsMoved 신호가 재구성 중에 발화되지 않도록 일시 차단
        self.table.model().rowsMoved.disconnect(self._on_rows_moved)
        try:
            self.table.setRowCount(0)
            for s in self._stocks:
                row = self.table.rowCount()
                self.table.insertRow(row)
                self._fill_row(row, s)
        finally:
            self.table.model().rowsMoved.connect(self._on_rows_moved)

        if select_row is not None and 0 <= select_row < self.table.rowCount():
            self.table.selectRow(select_row)

    def _fill_row(self, row: int, s: dict):
        name  = s.get("name", s["code"])
        code  = s["code"]
        avg   = f"{int(s.get('avg_price', 0)):,} 원"
        qty   = f"{int(s.get('quantity', 0)):,} 주"
        cells = [name, code, avg, qty]
        for col, text in enumerate(cells):
            item = QTableWidgetItem(text)
            # 평단가/수량은 우측 정렬
            if col in (2, 3):
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            else:
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            # 드래그-드롭 가능, 직접 편집 불가
            item.setFlags(
                Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDragEnabled
            )
            self.table.setItem(row, col, item)

    # ── 드래그 정렬 핸들러 ────────────────────────────────────────────────
    def _on_rows_moved(self, parent, start, end, dest_parent, dest_row):
        # 단일 행만 이동(SingleSelection) — 한 항목을 옮긴 결과를 self._stocks 에 반영
        # Qt 의 dest_row 는 "이동 전 좌표계" 기준이므로 보정 필요
        item = self._stocks.pop(start)
        insert_at = dest_row if dest_row < start else dest_row - 1
        insert_at = max(0, min(insert_at, len(self._stocks)))
        self._stocks.insert(insert_at, item)
        # 표는 Qt 가 이미 옮긴 상태이므로 재구성 불필요

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


# ─── 개별 주식 위젯 ───────────────────────────────────────────────────────────
class StockWidget(QWidget):
    """화면에 떠있는 하나의 주식 위젯"""

    deleted = pyqtSignal(str)   # code 전달
    edited  = pyqtSignal(str)   # 수정 완료 후 저장 요청

    MIN_W      = 240    # 기본(최소) 가로폭
    COMPACT_H  = 58     # 축소 높이 (2줄 레이아웃, 압축)
    EXPAND_H   = 214    # 확장 높이 (compact + 상세 패널 156)
    RADIUS     = 13     # 모서리 반지름

    def __init__(self, stock_data: dict, width: int | None = None):
        super().__init__()
        self.data = stock_data          # code, name, avg_price, quantity, pos
        self.current_price: int = 0
        self.is_expanded: bool = False
        self._drag_pos = None
        self._press_pos = None    # 좌클릭 시작 위치 (드래그/클릭 구분용)
        self._moved: bool = False # 일정 거리 이상 움직였는지

        # 외부에서 통일 너비를 받지 않으면 종목명 기준 자체 계산
        name = self.data.get("name", self.data["code"])
        self.W = width if width else self.calc_width_for_name(name)

        # 5초 자동 축소 타이머
        self.collapse_timer = QTimer(singleShot=True)
        self.collapse_timer.timeout.connect(self.collapse)

        # 30초마다 시세 갱신
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self._fetch)
        self.refresh_timer.start(30_000)

        self._build_ui()
        self._fetch()   # 즉시 한 번 조회

    # ── 종목명에 맞춰 가로폭 계산 ─────────────────────────────────────────
    @staticmethod
    def calc_width_for_name(name: str) -> int:
        """종목명 픽셀 폭을 측정해 위젯 가로폭을 결정. 최소 MIN_W."""
        font = QFont("Malgun Gothic",8, QFont.Weight.Bold)
        fm = QFontMetrics(font)
        name_w = fm.horizontalAdvance(name)
        # 좌마진(14) + 우마진(14) + 여유(6) = 34
        OVERHEAD = 34
        return max(StockWidget.MIN_W, name_w + OVERHEAD)

    # ── UI 구성 ────────────────────────────────────────────────────────────
    def _build_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(self.W, self.COMPACT_H)

        # ── 카드 배경 프레임
        self.card = QFrame(self)
        self.card.setObjectName("card")
        self.card.setGeometry(0, 0, self.W, self.COMPACT_H)
        self.card.setStyleSheet(f"""
            QFrame#card {{
                background: {C['bg']};
                border: 1px solid {C['border']};
                border-radius: {self.RADIUS}px;
            }}
        """)

        # ── 상단 compact 영역 (2줄 레이아웃) ────────────────────────────
        self.compact = QWidget(self.card)
        self.compact.setGeometry(0, 0, self.W, self.COMPACT_H)
        self.compact.setStyleSheet("background: transparent;")

        vl = QVBoxLayout(self.compact)
        vl.setContentsMargins(14, 5, 14, 5)
        vl.setSpacing(1)

        # 1행: 종목명
        self.name_lbl = QLabel(self.data.get("name", self.data["code"]))
        self.name_lbl.setFont(QFont("Malgun Gothic",8, QFont.Weight.Bold))
        self.name_lbl.setStyleSheet(f"color: {C['subtext']};")
        vl.addWidget(self.name_lbl)

        # 2행: 가격 + 등락률
        price_row = QHBoxLayout()
        price_row.setContentsMargins(0, 0, 0, 0)
        price_row.setSpacing(8)

        self.price_lbl = QLabel("─")
        self.price_lbl.setFont(QFont("Malgun Gothic",11, QFont.Weight.Bold))
        self.price_lbl.setStyleSheet(f"color: {C['text']};")
        price_row.addWidget(self.price_lbl)

        self.rate_lbl = QLabel("")
        self.rate_lbl.setFont(QFont("Malgun Gothic",9))
        self.rate_lbl.setStyleSheet(f"color: {C['subtext']};")
        price_row.addWidget(self.rate_lbl)
        price_row.addStretch()

        vl.addLayout(price_row)

        # ── 확장 패널 ────────────────────────────────────────────────────
        panel_h = self.EXPAND_H - self.COMPACT_H
        self.expand_panel = QWidget(self.card)
        self.expand_panel.setGeometry(0, self.COMPACT_H, self.W, panel_h)
        self.expand_panel.setStyleSheet("background: transparent;")
        self.expand_panel.hide()

        vl = QVBoxLayout(self.expand_panel)
        vl.setContentsMargins(14, 2, 14, 12)
        vl.setSpacing(2)

        # 구분선
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background: {C['border']}; max-height: 1px; border: none;")
        vl.addWidget(sep)
        vl.addSpacing(2)

        # 상세 행 생성
        self.avg_val    = self._make_row(vl, "평단가")
        self.qty_val    = self._make_row(vl, "보유수량")
        self.invest_val = self._make_row(vl, "투자원금")
        self.eval_val   = self._make_row(vl, "평가금액")

        # 손익 (강조)
        self.profit_val = self._make_row(vl, "평가손익", bold=True)
        self.prate_val  = self._make_row(vl, "수익률",   bold=True)

    # ── 외부에서 위젯 너비 변경 (통일 너비 적용용) ────────────────────
    def set_width(self, new_w: int):
        if new_w == self.W:
            return
        self.W = new_w
        cur_h = self.EXPAND_H if self.is_expanded else self.COMPACT_H
        self.setFixedWidth(new_w)
        self.card.setGeometry(0, 0, new_w, cur_h)
        self.compact.setGeometry(0, 0, new_w, self.COMPACT_H)
        panel_h = self.EXPAND_H - self.COMPACT_H
        self.expand_panel.setGeometry(0, self.COMPACT_H, new_w, panel_h)

    def _make_row(self, parent_layout, key_text: str, bold=False) -> QLabel:
        """키-값 한 줄 생성, 값 QLabel 반환"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)

        key_lbl = QLabel(key_text)
        key_lbl.setStyleSheet(f"color: {C['subtext']}; font-size: 10px;")
        key_lbl.setFixedWidth(58)

        val_lbl = QLabel("─")
        style = f"color: {C['text']}; font-size: 11px;"
        if bold:
            style += " font-weight: bold;"
        val_lbl.setStyleSheet(style)
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)

        row.addWidget(key_lbl)
        row.addWidget(val_lbl)
        parent_layout.addLayout(row)
        return val_lbl

    # ── 데이터 갱신 ────────────────────────────────────────────────────────
    def _fetch(self):
        result = fetch_stock(self.data["code"])
        if result:
            self.data["name"] = result["name"]
            self.name_lbl.setText(result["name"])
            self.current_price = result["price"]
            self._apply_price(result)

    def _apply_price(self, result: dict):
        price = result["price"]
        rate  = result["change_rate"]

        self.price_lbl.setText(f"{price:,}")

        if rate > 0:
            color = C["red"]
            sign  = "▲"
        elif rate < 0:
            color = C["blue"]
            sign  = "▼"
        else:
            color = C["subtext"]
            sign  = "  "

        self.price_lbl.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")
        self.rate_lbl.setText(f"{sign}{abs(rate):.2f}%")
        self.rate_lbl.setStyleSheet(f"color: {color}; font-size: 9px;")

        self._update_detail(price)

    def _update_detail(self, price: int):
        avg    = self.data.get("avg_price", 0)
        qty    = self.data.get("quantity", 0)
        invest = avg * qty
        eval_  = price * qty
        profit = eval_ - invest
        prate  = (profit / invest * 100) if invest else 0

        sign  = "+" if profit >= 0 else ""
        color = C["red"] if profit >= 0 else C["blue"]

        self.avg_val.setText(f"{avg:,} 원")
        self.qty_val.setText(f"{qty:,} 주")
        self.invest_val.setText(f"{invest:,} 원")
        self.eval_val.setText(f"{eval_:,} 원")

        self.profit_val.setText(f"{sign}{profit:,} 원")
        self.profit_val.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")
        self.prate_val.setText(f"{sign}{prate:.2f}%")
        self.prate_val.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")

    # ── 확장 / 축소 ────────────────────────────────────────────────────────
    def toggle_expand(self):
        if self.is_expanded:
            self.collapse()
        else:
            self.expand()

    def expand(self):
        self.is_expanded = True
        self.expand_panel.show()
        self.setFixedHeight(self.EXPAND_H)
        self.card.setGeometry(0, 0, self.W, self.EXPAND_H)
        self.collapse_timer.start(5_000)   # 5초 뒤 자동 축소

    def collapse(self):
        self.is_expanded = False
        self.expand_panel.hide()
        self.setFixedHeight(self.COMPACT_H)
        self.card.setGeometry(0, 0, self.W, self.COMPACT_H)
        self.collapse_timer.stop()

    # ── 드래그 이동 + 클릭 토글 ──────────────────────────────────────────
    DRAG_THRESHOLD = 4   # 이 거리 이상 움직이면 드래그로 간주

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos  = event.globalPosition().toPoint() - self.pos()
            self._press_pos = event.globalPosition().toPoint()
            self._moved     = False

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos:
            if not self._moved and self._press_pos:
                delta = event.globalPosition().toPoint() - self._press_pos
                if abs(delta.x()) > self.DRAG_THRESHOLD or abs(delta.y()) > self.DRAG_THRESHOLD:
                    self._moved = True
            if self._moved:
                self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        # 드래그가 아니었으면(거의 안 움직임) = 클릭 → 확장/축소 토글
        if event.button() == Qt.MouseButton.LeftButton and not self._moved:
            self.toggle_expand()
        self._drag_pos  = None
        self._press_pos = None
        self._moved     = False

    # ── 우클릭 메뉴 ────────────────────────────────────────────────────────
    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(TRAY_MENU_STYLE)
        edit_act = menu.addAction("✏️   수정")
        menu.addSeparator()
        del_act  = menu.addAction("🗑️   삭제")

        action = menu.exec(event.globalPos())
        if action == edit_act:
            self._open_edit()
        elif action == del_act:
            self.deleted.emit(self.data["code"])
            self.close()

    def _open_edit(self):
        dlg = StockDialog(data=self.data)
        if dlg.exec():
            new = dlg.get_data()
            self.data["avg_price"] = new["avg_price"]
            self.data["quantity"]  = new["quantity"]
            if self.current_price:
                self._update_detail(self.current_price)
            self.edited.emit(self.data["code"])


# ─── 전체 위젯 관리자 ─────────────────────────────────────────────────────────
class WidgetManager:
    def __init__(self, app: QApplication):
        self.app = app
        self.stocks: list[dict] = []
        self.widgets: dict[str, StockWidget] = {}
        self.uniform_w: int = StockWidget.MIN_W
        self.is_hidden: bool = False    # 위젯 전체 숨김 상태

        self._load_config()
        self._setup_tray()
        self._spawn_all()

    # ── 전체 위젯 표시/숨김 토글 ─────────────────────────────────────────
    def toggle_visibility(self):
        self.is_hidden = not self.is_hidden
        for w in self.widgets.values():
            w.hide() if self.is_hidden else w.show()
        self.toggle_act.setText("👀   표시하기" if self.is_hidden else "🙈   숨기기")

    # ── 위치 초기화 ───────────────────────────────────────────────────────
    def reset_positions(self):
        """각 위젯을 현재 위치한 모니터의 우상단에 세로 정렬."""
        MARGIN_X = 20   # 화면 우측 여백
        MARGIN_Y = 60   # 화면 상단 여백
        GAP      = 4    # 위젯 간 세로 간격

        # 위젯을 현재 속한 모니터별로 그룹화 (stocks 순서 보존)
        groups: dict = {}
        for s in self.stocks:
            w = self.widgets.get(s["code"])
            if not w:
                continue
            center = w.frameGeometry().center()
            screen = QApplication.screenAt(center) or QApplication.primaryScreen()
            groups.setdefault(screen, []).append((s, w))

        # 모니터별 우상단 정렬
        for screen, items in groups.items():
            geo = screen.availableGeometry()
            for i, (s, w) in enumerate(items):
                x = geo.x() + geo.width() - w.width() - MARGIN_X
                y = geo.y() + MARGIN_Y + i * (StockWidget.COMPACT_H + GAP)
                w.move(x, y)
                s["pos"] = [x, y]

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

    # ── 트레이 ─────────────────────────────────────────────────────────────
    def _setup_tray(self):
        icon = self._make_tray_icon()
        self.tray = QSystemTrayIcon(icon)
        self.tray.setToolTip("한국 주식 위젯")

        menu = QMenu()
        menu.setStyleSheet(TRAY_MENU_STYLE)

        add_act    = QAction("➕   종목 추가",   menu)
        manage_act = QAction("📋   종목 관리",   menu)
        self.toggle_act = QAction("🙈   숨기기", menu)
        reset_act  = QAction("📐   위치 초기화", menu)
        quit_act   = QAction("❌   종료",        menu)
        add_act.triggered.connect(self.open_add_dialog)
        manage_act.triggered.connect(self.open_manage_dialog)
        self.toggle_act.triggered.connect(self.toggle_visibility)
        reset_act.triggered.connect(self.reset_positions)
        quit_act.triggered.connect(self.app.quit)

        menu.addAction(add_act)
        menu.addAction(manage_act)
        menu.addAction(self.toggle_act)
        menu.addAction(reset_act)
        menu.addSeparator()
        menu.addAction(quit_act)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        # 트레이 아이콘 좌클릭(Trigger) 시 표시/숨김 빠른 토글
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_visibility()

    @staticmethod
    def _make_tray_icon() -> QIcon:
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
    def _load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    self.stocks = json.load(f)
            except Exception:
                self.stocks = []

    def _save_config(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.stocks, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[save] 오류: {e}")

    def save_positions(self):
        for s in self.stocks:
            w = self.widgets.get(s["code"])
            if w:
                pos = w.pos()
                s["pos"] = [pos.x(), pos.y()]
        self._save_config()

    # ── 위젯 생성 ──────────────────────────────────────────────────────────
    def _spawn_all(self):
        self.uniform_w = self._calc_uniform_width()
        for i, s in enumerate(self.stocks):
            default_x = 60
            default_y = 60 + i * (StockWidget.COMPACT_H + 12)
            self._spawn_widget(s, default_x, default_y)

    def _spawn_widget(self, stock: dict, def_x=60, def_y=60):
        code = stock["code"]
        w = StockWidget(stock, width=self.uniform_w)
        w.deleted.connect(self._on_delete)
        w.edited.connect(lambda _: self._save_config())

        pos = stock.get("pos", [def_x, def_y])
        w.move(pos[0], pos[1])
        w.show()
        self.widgets[code] = w

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

        # 새 위젯 위치: 기존 위젯들 아래
        ny = 60 + len(self.widgets) * (StockWidget.COMPACT_H + 12)
        self._spawn_widget(d, 60, ny)

        # 숨김 상태에서 새 종목을 추가한 경우 자동으로 표시 상태로 전환
        if self.is_hidden:
            self.toggle_visibility()

    # ── 종목 일괄 관리 ────────────────────────────────────────────────────
    def open_manage_dialog(self):
        dlg = ManageStocksDialog(stocks=copy.deepcopy(self.stocks))
        if not dlg.exec():
            return
        new_stocks = dlg.get_stocks()

        old_map = {s["code"]: s for s in self.stocks}
        new_map = {s["code"]: s for s in new_stocks}

        # 삭제된 종목: 위젯 닫고 제거
        for code in list(old_map):
            if code not in new_map:
                w = self.widgets.pop(code, None)
                if w:
                    w.close()

        # 추가된 종목: 위젯 생성 (기본 위치)
        for s in new_stocks:
            if s["code"] not in old_map:
                ny = 60 + len(self.widgets) * (StockWidget.COMPACT_H + 12)
                self._spawn_widget(s, 60, ny)

        # 기존 종목: 평단가/수량 변경 반영
        for s in new_stocks:
            code = s["code"]
            if code in old_map and code in self.widgets:
                w = self.widgets[code]
                w.data["avg_price"] = s["avg_price"]
                w.data["quantity"]  = s["quantity"]
                if w.current_price:
                    w._update_detail(w.current_price)

        # 순서 + 저장 + 너비 재계산
        self.stocks = new_stocks
        self._apply_uniform_width()
        self._save_config()

        # 숨김 상태에서 변경된 종목이 있으면 자동으로 표시 상태로 전환
        if self.is_hidden and self.widgets:
            self.toggle_visibility()

    # ── 종목 삭제 ──────────────────────────────────────────────────────────
    def _on_delete(self, code: str):
        self.stocks = [s for s in self.stocks if s["code"] != code]
        self.widgets.pop(code, None)
        self._save_config()
        # 가장 긴 종목이 삭제된 경우 남은 위젯들도 줄어들도록
        self._apply_uniform_width()


# ─── 진입점 ───────────────────────────────────────────────────────────────────
def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # 트레이만 있어도 계속 실행

    manager = WidgetManager(app)
    app.aboutToQuit.connect(manager.save_positions)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
