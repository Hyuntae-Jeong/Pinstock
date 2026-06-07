"""python -m pinstock 진입점."""

import sys

from PyQt6.QtWidgets import QApplication

from .core.autostart import reconcile_autostart
from .core.storage import migrate_legacy_config


def main():
    migrate_legacy_config()
    reconcile_autostart()   # 죽은 자동 실행 잔재(없어진 exe 경로) 정리

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # 트레이만 있어도 계속 실행

    if sys.platform == "darwin":
        from .ui_macos.manager import MacAppManager
        manager = MacAppManager(app)
        app.aboutToQuit.connect(manager._save_config)
    else:
        # Windows 작업표시줄에서 python.exe 가 아닌 Pinstock 으로 그룹/아이콘이 잡히게.
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "com.hyuntae.pinstock"
                )
            except Exception:
                pass

        from .ui_windows.manager import WidgetManager, _resolve_app_icon
        app.setWindowIcon(_resolve_app_icon())   # 모든 창/다이얼로그 기본 아이콘
        manager = WidgetManager(app)
        app.aboutToQuit.connect(manager.save_positions)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
