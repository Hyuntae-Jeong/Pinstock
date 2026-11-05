"""python -m pinstock 진입점."""

import sys

from PyQt6.QtWidgets import QApplication

from .core.autostart import reconcile_autostart
from .core.restart import pop_wait_pid_arg, wait_for_exit
from .core.storage import migrate_legacy_config


def main():
    # 메뉴의 '재시작' 으로 뜬 경우: 옛 프로세스가 stocks.json 저장을 마치고 끝날
    # 때까지 기다린다. 설정을 읽거나 쓰기 전이어야 해서 맨 앞에 둔다.
    wait_pid = pop_wait_pid_arg(sys.argv)
    if wait_pid is not None and not wait_for_exit(wait_pid):
        print(f"[restart] 이전 Pinstock(pid={wait_pid})이 끝나지 않아 재시작을 취소합니다")
        return

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
