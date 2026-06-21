"""Offscreen smoke test — HelpDialog.

예전 AboutDialog(버전·라이선스·업데이트 확인)를 도움말 'Pinstock 정보'
섹션으로 흡수했다. 그 섹션이 콜백 유무에 따라 업데이트 확인 링크를 노출하고,
링크 클릭이 콜백으로 연결되는지까지 검증한다.
"""

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

    from PyQt6.QtCore import QUrl
    from PyQt6.QtWidgets import QApplication

    app = QApplication([])
    log("[step] QApplication ok")

    from pinstock.__version__ import __version__
    from pinstock.ui_common.help_dialog import HelpDialog, HELP_SECTIONS
    log("[step] imports ok")

    # ── HelpDialog — 정적 카테고리 + 동적 'Pinstock 정보' 섹션 ──
    # HELP_SECTIONS 는 정적 11개. 'Pinstock 정보' 는 HelpDialog 가 런타임에
    # 한 개 더 붙이므로 다이얼로그의 실제 섹션(_sections)은 12개가 된다.
    assert len(HELP_SECTIONS) == 11
    help_dlg = HelpDialog(on_check_update=lambda: None)
    log("[step] HelpDialog() ok")
    assert len(help_dlg._sections) == 12
    assert help_dlg.category_list.count() == len(help_dlg._sections)
    for i, (sidebar, body_h2, _body) in enumerate(help_dlg._sections):
        help_dlg.category_list.setCurrentRow(i)
        html = help_dlg.content_view.toHtml()
        # 본문 상단 h2 의 한국어 키워드(이모지 제거 후) 가 들어갔는지 확인
        keyword = body_h2.split(" ", 1)[-1]
        assert keyword in html, f"row={i} body_h2='{body_h2}' 본문 누락"
        # 사이드바 라벨도 비어있지 않아야 함 (시각 확인은 따로)
        assert sidebar.strip(), f"row={i} 사이드바 라벨 비어있음"
    log(f"[OK] HelpDialog — 카테고리 {len(help_dlg._sections)}개 모두 본문 표시")

    # ── 'Pinstock 정보' 섹션 — 버전 / 라이선스 토큰 ──
    about_label, _h2, about_body = help_dlg._sections[-1]
    assert "Pinstock 정보" in about_label
    assert __version__ in about_body, "버전 문자열 누락"
    for token in ("PyQt6", "requests", "openpyxl", "MIT", "Apache"):
        assert token in about_body, f"라이선스 토큰 {token} 누락"
    log("[OK] HelpDialog 'Pinstock 정보' — 버전·라이선스 토큰 포함")

    # ── 콜백 있으면 업데이트 확인 링크 노출 + 클릭 시 콜백 호출 ──
    assert "pinstock:check-update" in about_body, "업데이트 확인 링크 누락"
    called = []
    help_cb = HelpDialog(on_check_update=lambda: called.append("u"))
    help_cb._on_anchor_clicked(QUrl("pinstock:check-update"))
    assert called == ["u"], f"콜백 미호출: {called}"
    log("[OK] HelpDialog — 업데이트 확인 링크 클릭 시 콜백 호출")

    # ── 콜백 없으면 업데이트 확인 링크 비노출 (개발 빌드 등) ──
    help_nocb = HelpDialog()
    assert len(help_nocb._sections) == 12
    assert "pinstock:check-update" not in help_nocb._sections[-1][2]
    log("[OK] HelpDialog(콜백 없음) — 업데이트 확인 링크 비노출")

    # ── manager 메서드 노출 확인 (정보 메뉴 제거 후 open_about_dialog 없어야 함) ──
    from pinstock.ui_windows import manager as win_mgr
    from pinstock.ui_macos import manager as mac_mgr
    assert hasattr(win_mgr.WidgetManager, "open_help_dialog")
    assert hasattr(mac_mgr.MacAppManager, "open_help_dialog")
    assert not hasattr(win_mgr.WidgetManager, "open_about_dialog"), "open_about_dialog 잔존"
    assert not hasattr(mac_mgr.MacAppManager, "open_about_dialog"), "open_about_dialog 잔존"
    log("[OK] manager — open_help_dialog 노출 / open_about_dialog 제거됨")

    # ── 스크린샷 — sanity check 용 시각 보고 ──
    help_dlg.show()
    app.processEvents()
    shot_help = _REPO_ROOT / "smoke_help.png"
    help_dlg.grab().save(str(shot_help))
    log(f"[shot] {shot_help}")

    # 'Pinstock 정보' 섹션을 띄운 상태로 한 장 더
    help_dlg.category_list.setCurrentRow(len(help_dlg._sections) - 1)
    app.processEvents()
    shot_about = _REPO_ROOT / "smoke_about.png"
    help_dlg.grab().save(str(shot_about))
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
