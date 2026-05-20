"""다크 테마 색상 팔레트와 공용 Qt 스타일시트."""

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
QLineEdit, QSpinBox, QDoubleSpinBox {{
    background: {C['surface']};
    color: {C['text']};
    border: 1px solid {C['surface2']};
    border-radius: 7px;
    padding: 7px 10px;
    font-size: 13px;
    selection-background-color: {C['blue']};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {C['blue']};
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background: {C['surface2']};
    border: none;
    width: 22px;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ border-top-right-radius: 6px; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ border-bottom-right-radius: 6px; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {C['blue']};
}}
/* 화살표는 ArrowSpinBox.paintEvent에서 직접 그림 — Qt 기본 화살표는 숨김 */
QSpinBox::up-arrow, QSpinBox::down-arrow,
QDoubleSpinBox::up-arrow, QDoubleSpinBox::down-arrow {{
    image: none;
    width: 0;
    height: 0;
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
QTableWidget::item:focus, QTableWidget::item:selected {{
    outline: 0;
    border: none;
}}
QTableView {{ outline: 0; }}
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
