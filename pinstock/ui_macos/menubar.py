"""macOS 메뉴바 아이콘 + 팝오버 토글 트리거.

NSImage 템플릿 이미지로 등록해 메뉴바 배경(=바탕화면 톤)에 맞춰
시스템이 자동으로 밝게/어둡게 색을 반전하도록 한다.
"""

import sys
from pathlib import Path

from PyQt6.QtCore import QObject, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QBrush, QPen, QFont
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QSystemTrayIcon, QApplication

from ..ui_windows.theme import C


def _resolve_icons_dir() -> Path:
    # PyInstaller 번들에서는 sys._MEIPASS 가 리소스 루트.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "icons"
    # 개발 모드: 레포 루트의 icons/.
    return Path(__file__).resolve().parent.parent.parent / "icons"


_ICONS_DIR = _resolve_icons_dir()
# 단색(검정) SVG. setIsMask(True) 를 통해 NSImage 템플릿으로 등록되면
# 시스템이 알파만 보존한 채 배경 톤에 맞춰 색을 자동 처리한다.
_ICON_TEMPLATE = _ICONS_DIR / "menubar_light.svg"


# ─── 메뉴바 아이콘 ──────────────────────────────────────────────────────────
class MenuBarIcon(QObject):
    """macOS 메뉴바 캔들스틱 아이콘 트리거.

    좌/우 클릭 모두 popover 토글로 처리한다 (Mac 메뉴바 native 패턴).
    종료는 popover 안의 ❌ 버튼으로.
    """

    toggle_popover_requested = pyqtSignal(QPoint, int)   # anchor_global_pos, anchor_width
    notification_clicked     = pyqtSignal()              # 토스트 클릭 (업데이트 알림 등)

    def __init__(self, app: QApplication, parent: QObject | None = None):
        super().__init__(parent)
        self.app = app
        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip("Pinstock")
        self.tray.activated.connect(self._on_activated)
        self.tray.messageClicked.connect(self.notification_clicked.emit)

        if _ICON_TEMPLATE.exists():
            icon = self._render_svg_icon(_ICON_TEMPLATE)
            icon.setIsMask(True)   # NSImage 템플릿 등록 → 메뉴바 배경에 맞춰 자동 반전
            self.tray.setIcon(icon)
        else:
            # SVG 누락 시 fallback: 기존 ₩ 원형 아이콘
            self.tray.setIcon(self._make_fallback_icon())

        self.tray.show()

    # ── 아이콘 렌더링 ─────────────────────────────────────────────────────
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

    # ── 알림 토스트 ───────────────────────────────────────────────────────
    def show_notification(self, title: str, body: str, duration_ms: int = 7000):
        """macOS 알림센터로 표시되는 토스트. 클릭 시 notification_clicked emit.

        QSystemTrayIcon.showMessage 는 macOS 에서 자동으로 알림센터 항목으로
        포워딩되므로 별도 Notification API 를 쓸 필요가 없다. 시스템 설정의
        "알림 허용" 이 꺼져 있으면 조용히 무시된다.
        """
        self.tray.showMessage(
            title, body,
            QSystemTrayIcon.MessageIcon.Information,
            duration_ms,
        )

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
