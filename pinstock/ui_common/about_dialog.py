"""앱 정보(About) 다이얼로그.

버전 · 업데이트 확인 · 라이선스 정보를 한 화면에서 보여준다.
업데이트 확인 버튼은 콜백(on_check_update)을 받아 호출측의 흐름과 연결된다 —
다이얼로그 자체는 updater 와 직접 통신하지 않는다.
"""

import sys
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextBrowser,
)

from ..__version__ import __version__
from ..ui_windows.theme import C, DIALOG_STYLE


# ─── 의존성 라이브러리 — (이름, 라이선스, 홈페이지 URL) ────────────────────
# 표기 의도: 사용자에게 어떤 라이브러리가 쓰였고 어느 라이선스인지 한눈에
# 보여주는 것. 라이선스 전문은 각 프로젝트 페이지에서 직접 확인하도록 안내.
_DEPENDENCIES: list[tuple[str, str, str]] = [
    ("PyQt6",              "GPL v3 (또는 상용)", "https://www.riverbankcomputing.com/software/pyqt/"),
    ("Qt 6",               "LGPL v3",            "https://www.qt.io/"),
    ("requests",           "Apache 2.0",         "https://github.com/psf/requests"),
    ("openpyxl",           "MIT",                "https://openpyxl.readthedocs.io/"),
    ("certifi",            "MPL 2.0",            "https://github.com/certifi/python-certifi"),
    ("urllib3",            "MIT",                "https://github.com/urllib3/urllib3"),
    ("charset-normalizer", "MIT",                "https://github.com/jawah/charset_normalizer"),
    ("idna",               "BSD",                "https://github.com/kjd/idna"),
]

_APP_LICENSE_LINE = "MIT License — 자유롭게 사용·수정·재배포할 수 있습니다."


class AboutDialog(QDialog):
    """버전 / 업데이트 / 라이선스 단일 모달."""

    def __init__(
        self,
        parent=None,
        on_check_update: Optional[Callable[[], None]] = None,
    ):
        """
        on_check_update: '업데이트 확인' 버튼이 호출할 콜백. None 이면
            버튼은 비활성화된다 — 다만 일반 흐름에서는 manager 가 항상
            전달하므로 비활성화 케이스는 사실상 발생하지 않는다.
        """
        super().__init__(parent)
        self.setWindowTitle("Pinstock 정보")
        self.resize(480, 580)
        self.setStyleSheet(DIALOG_STYLE)
        self._on_check_update = on_check_update
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(10)

        # ── 헤더: 아이콘 + 이름 + 버전 + 한줄 설명 ─────────────────────────
        header_row = QHBoxLayout()
        header_row.setSpacing(14)

        icon = self._load_app_icon()
        if not icon.isNull():
            icon_lbl = QLabel()
            icon_lbl.setPixmap(icon.pixmap(64, 64))
            icon_lbl.setFixedSize(64, 64)
            header_row.addWidget(icon_lbl)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)

        name_lbl = QLabel("Pinstock")
        name_lbl.setStyleSheet(
            f"color: {C['text']}; font-size: 24px; font-weight: bold;"
        )
        title_col.addWidget(name_lbl)

        version_lbl = QLabel(f"버전 {__version__}")
        version_lbl.setStyleSheet(f"color: {C['subtext']}; font-size: 13px;")
        title_col.addWidget(version_lbl)

        desc_lbl = QLabel("한국·미국 주식 미니 위젯")
        desc_lbl.setStyleSheet(f"color: {C['subtext']}; font-size: 12px;")
        title_col.addWidget(desc_lbl)

        title_col.addStretch()
        header_row.addLayout(title_col, 1)
        root.addLayout(header_row)

        # ── 업데이트 확인 버튼 ─────────────────────────────────────────────
        self.btn_check_update = QPushButton("🔄  업데이트 확인")
        self.btn_check_update.setFixedHeight(36)
        if self._on_check_update is not None:
            self.btn_check_update.clicked.connect(self._handle_check_update)
        else:
            self.btn_check_update.setEnabled(False)
        root.addWidget(self.btn_check_update)

        # ── 라이선스 섹션 ─────────────────────────────────────────────────
        sep_lbl = QLabel("라이선스")
        sep_lbl.setStyleSheet(
            f"color: {C['blue']}; font-size: 14px; font-weight: bold; "
            f"margin-top: 6px;"
        )
        root.addWidget(sep_lbl)

        self.license_view = QTextBrowser()
        self.license_view.setOpenExternalLinks(True)
        self.license_view.document().setDefaultStyleSheet(self._license_style())
        self.license_view.setStyleSheet(
            f"QTextBrowser {{ background: {C['bg2']}; color: {C['text']}; "
            f"border: 1px solid {C['border']}; border-radius: 8px; padding: 10px; }}"
        )
        self.license_view.setHtml(self._build_license_html())
        root.addWidget(self.license_view, 1)

        # ── 닫기 ──────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_close = QPushButton("닫기")
        self.btn_close.setProperty("flat", "true")
        self.btn_close.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_close)
        root.addLayout(btn_row)

    # ── 업데이트 버튼 ───────────────────────────────────────────────────────
    def _handle_check_update(self):
        # About 다이얼로그를 닫고 업데이트 흐름을 위임 — UpdateDialog 가
        # 모달로 떠 있을 때 About 다이얼로그가 뒤에 남으면 시각적으로 산만함.
        if self._on_check_update is not None:
            self.accept()
            self._on_check_update()

    # ── 아이콘 로드 ─────────────────────────────────────────────────────────
    def _load_app_icon(self) -> QIcon:
        """repo/assets/Pinstock.ico 우선 → PyInstaller 번들의 _MEIPASS 보조."""
        candidates: list[Path] = []
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "assets" / "Pinstock.ico")
        candidates.append(
            Path(__file__).resolve().parent.parent.parent / "assets" / "Pinstock.ico"
        )
        for p in candidates:
            if p.exists():
                return QIcon(str(p))
        return QIcon()

    # ── 라이선스 HTML ───────────────────────────────────────────────────────
    def _build_license_html(self) -> str:
        deps_html = "\n".join(
            f'<li><b>{name}</b> — {license_name} '
            f'<a href="{url}">홈페이지</a></li>'
            for name, license_name, url in _DEPENDENCIES
        )
        return f"""
            <p><b>Pinstock</b> — {_APP_LICENSE_LINE}</p>
            <p><b>사용 중인 오픈소스 라이브러리</b></p>
            <ul>
                {deps_html}
            </ul>
        """

    def _license_style(self) -> str:
        return f"""
            body {{
                color: {C['text']};
                font-size: 13px;
                line-height: 1.55;
            }}
            a {{ color: {C['blue']}; text-decoration: none; }}
            ul {{ margin-left: 12px; padding-left: 0; }}
            li {{ margin-bottom: 6px; }}
        """
