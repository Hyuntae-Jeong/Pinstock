"""macOS 메뉴바 아이콘 + 팝오버 토글 트리거.

시스템 라이트/다크 모드에 따라 적절한 SVG 아이콘으로 자동 전환.
"""

from pathlib import Path

from PyQt6.QtCore import QObject, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QBrush, QPen, QFont
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QSystemTrayIcon, QApplication

from ..ui_windows.theme import C


# 레포 루트의 icons/ 디렉토리. pinstock/ui_macos/menubar.py 의 두 단계 부모.
_ICONS_DIR = Path(__file__).resolve().parent.parent.parent / "icons"
_ICON_LIGHT = _ICONS_DIR / "menubar_light.svg"   # 라이트 모드: 검정 단색
_ICON_DARK  = _ICONS_DIR / "menubar_dark.svg"    # 다크 모드:   흰색 단색


# ─── 메뉴바 아이콘 ──────────────────────────────────────────────────────────
class MenuBarIcon(QObject):
    """macOS 메뉴바 캔들스틱 아이콘 트리거.

    좌/우 클릭 모두 popover 토글로 처리한다 (Mac 메뉴바 native 패턴).
    종료는 popover 안의 ❌ 버튼으로.
    """

    toggle_popover_requested = pyqtSignal(QPoint, int)   # anchor_global_pos, anchor_width

    def __init__(self, app: QApplication, parent: QObject | None = None):
        super().__init__(parent)
        self.app = app
        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip("Pinstock")
        self.tray.activated.connect(self._on_activated)

        # 시스템 테마 따라 아이콘 결정
        self._apply_icon_for_current_scheme()

        # 시스템 모드 변경 감지 (Qt 6.5+) — 실시간 전환
        try:
            app.styleHints().colorSchemeChanged.connect(
                self._on_color_scheme_changed
            )
        except (AttributeError, TypeError):
            pass   # Qt 6.5 미만: 시작 시점 모드만 반영

        self.tray.show()

    # ── 아이콘 ────────────────────────────────────────────────────────────
    def _on_color_scheme_changed(self, *_args):
        self._apply_icon_for_current_scheme()

    def _apply_icon_for_current_scheme(self):
        is_dark = self._is_dark_mode()
        svg_path = _ICON_DARK if is_dark else _ICON_LIGHT
        if svg_path.exists():
            self.tray.setIcon(self._render_svg_icon(svg_path))
        else:
            # SVG 누락 시 fallback: 기존 ₩ 원형 아이콘
            self.tray.setIcon(self._make_fallback_icon())

    @staticmethod
    def _is_dark_mode() -> bool:
        """Qt styleHints 기반 시스템 다크 모드 판정.
        Qt 6.5 미만이거나 결과가 Unknown 이면 기본값으로 다크 가정."""
        try:
            scheme = QApplication.instance().styleHints().colorScheme()
            if scheme == Qt.ColorScheme.Light:
                return False
            if scheme == Qt.ColorScheme.Dark:
                return True
        except (AttributeError, TypeError):
            pass
        return True   # Unknown 이면 다크 (대부분의 사용자 환경)

    @staticmethod
    def _render_svg_icon(svg_path: Path) -> QIcon:
        """SVG 를 22pt/44pt 높이 기준으로 렌더링해 QIcon 반환 (Retina 대응).
        픽맵 너비는 SVG viewBox aspect ratio 를 따라가서 가로로 긴 모양도
        세로로 뭉개지지 않게 함."""
        icon = QIcon()
        renderer = QSvgRenderer(str(svg_path))
        vb = renderer.viewBoxF()
        aspect = (vb.width() / vb.height()) if vb.height() > 0 else 1.0
        for h in (22, 44):
            w = max(1, int(round(h * aspect)))
            px = QPixmap(w, h)
            px.fill(Qt.GlobalColor.transparent)
            painter = QPainter(px)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            renderer.render(painter)
            painter.end()
            icon.addPixmap(px)
        return icon

    @staticmethod
    def _make_fallback_icon() -> QIcon:
        """SVG 가 없을 때 쓰이는 기존 ₩ 원형 아이콘."""
        px = QPixmap(32, 32)
        px.fill(QColor(0, 0, 0, 0))
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(QColor(C["blue"])))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(1, 1, 30, 30)
        p.setFont(QFont("Apple SD Gothic Neo", 14, QFont.Weight.Bold))
        p.setPen(QPen(QColor(C["bg"])))
        p.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, "₩")
        p.end()
        return QIcon(px)

    # ── 클릭 핸들링 ───────────────────────────────────────────────────────
    def _on_activated(self, reason):
        """좌/우 클릭 모두 popover 토글로 처리."""
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.Context,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            anchor_pos, anchor_w = self._anchor_position()
            self.toggle_popover_requested.emit(anchor_pos, anchor_w)

    def _anchor_position(self) -> tuple[QPoint, int]:
        """팝오버를 아래에 띄울 기준 좌표(= 트레이 아이콘 하단 중앙).
        tray.geometry() 가 비어 있으면 화면 우상단 메뉴바 추정 위치로 폴백."""
        geo = self.tray.geometry()
        if geo.width() > 0 and geo.height() > 0:
            return geo.bottomLeft(), geo.width()
        screen = QApplication.primaryScreen()
        sg = screen.geometry()
        avail = screen.availableGeometry()
        fallback_y = avail.y()
        fallback_x = sg.x() + sg.width() - 60
        return QPoint(fallback_x, fallback_y), 22
