"""Offscreen smoke test — HelpDialog / AboutDialog."""

import os
import sys
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

_LOG_PATH = _REPO_ROOT / "smoke_help_about.log"


def _run(log_fp):
    def log(msg: str) -> None:
        log_fp.write(msg + "\n")
        log_fp.flush()

    log("[step] enter")

    from PyQt6.QtWidgets import QApplication

    app = QApplication([])
    log("[step] QApplication ok")

    from pinstock.ui_common.help_dialog import HelpDialog, HELP_SECTIONS
    from pinstock.ui_common.about_dialog import AboutDialog
    log("[step] imports ok")

    # HelpDialog
    assert len(HELP_SECTIONS) == 10
    help_dlg = HelpDialog()
    log("[step] HelpDialog() ok")
    assert help_dlg.category_list.count() == len(HELP_SECTIONS)
    for i, (sidebar, body_h2, _body) in enumerate(HELP_SECTIONS):
        help_dlg.category_list.setCurrentRow(i)
        html = help_dlg.content_view.toHtml()
        # 본문 상단 h2 의 한국어 키워드(이모지 제거 후) 가 들어갔는지 확인
        keyword = body_h2.split(" ", 1)[-1]
        assert keyword in html, f"row={i} body_h2='{body_h2}' 본문 누락"
        # 사이드바 라벨도 비어있지 않아야 함 (시각 확인은 따로)
        assert sidebar.strip(), f"row={i} 사이드바 라벨 비어있음"
    log(f"[OK] HelpDialog — 카테고리 {len(HELP_SECTIONS)}개 모두 본문 표시")

    # AboutDialog — 개발 빌드 (콜백 없음)
    dev_about = AboutDialog()
    assert not dev_about.btn_check_update.isEnabled()
    assert dev_about.btn_check_update.text() == "🔄  업데이트 확인"
    log("[OK] AboutDialog(개발 빌드) — 업데이트 버튼 비활성")

    # AboutDialog — 콜백 정상
    called = []
    about_with_cb = AboutDialog(on_check_update=lambda: called.append("u"))
    assert about_with_cb.btn_check_update.isEnabled()
    about_with_cb.btn_check_update.click()
    assert called == ["u"], f"콜백 미호출: {called}"
    log("[OK] AboutDialog(콜백 연결) — 클릭 시 콜백 호출")

    # AboutDialog — 업데이트 버튼 라벨은 항상 고정 (새 버전 배지 제거됨)
    about_shot = AboutDialog(on_check_update=lambda: None)
    assert about_shot.btn_check_update.text() == "🔄  업데이트 확인"
    log("[OK] AboutDialog — 업데이트 버튼 라벨 고정 (배지 없음)")

    # 라이선스 HTML — 다이얼로그 인스턴스를 변수에 잡아둬야 임시 GC 로
    # license_view (QTextBrowser) 가 함께 해제되는 RuntimeError 를 피한다.
    lic_dlg = AboutDialog()
    lic_html = lic_dlg.license_view.toHtml()
    for token in ("PyQt6", "requests", "openpyxl", "MIT", "Apache"):
        assert token in lic_html, f"라이선스 HTML 에 {token} 누락"
    log("[OK] AboutDialog — 라이선스 섹션 토큰 포함")

    # manager 메서드 노출 확인
    from pinstock.ui_windows import manager as win_mgr
    from pinstock.ui_macos import manager as mac_mgr
    assert hasattr(win_mgr.WidgetManager, "open_help_dialog")
    assert hasattr(win_mgr.WidgetManager, "open_about_dialog")
    assert hasattr(mac_mgr.MacAppManager, "open_help_dialog")
    assert hasattr(mac_mgr.MacAppManager, "open_about_dialog")
    log("[OK] manager — open_help_dialog / open_about_dialog 노출")

    # 스크린샷 — sanity check 용 시각 보고
    help_dlg.show()
    app.processEvents()
    shot_help = _REPO_ROOT / "smoke_help.png"
    help_dlg.grab().save(str(shot_help))
    log(f"[shot] {shot_help}")

    about_shot.show()
    app.processEvents()
    shot_about = _REPO_ROOT / "smoke_about.png"
    about_shot.grab().save(str(shot_about))
    log(f"[shot] {shot_about}")

    log("\n전체 통과 OK")


if __name__ == "__main__":
    with _LOG_PATH.open("w", encoding="utf-8") as fp:
        try:
            _run(fp)
            rc = 0
        except Exception:
            fp.write("[FAIL] 예외 발생:\n")
            fp.write(traceback.format_exc())
            rc = 1
    raise SystemExit(rc)
