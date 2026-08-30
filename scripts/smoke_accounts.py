"""Smoke test — 계좌 레지스트리 / 보유 uid / 계좌 필터 / 팝오버 행 매핑.

여러 계좌 기능(#34)의 회귀 방지용. 지켜야 할 불변식은 셋이다:

  1. 계좌는 항상 1개 이상 — 계좌가 0개면 종목을 둘 곳이 없다
  2. 모든 보유 종목의 account_id 는 실재하는 계좌를 가리킨다
  3. 보유 항목의 uid 는 전역 유일 — 여기가 깨지면 "계좌1 삼성전자"와
     "계좌2 삼성전자"가 같은 행으로 뭉개지고 편집/삭제가 엉뚱한 쪽을 건드린다

구버전(계좌 개념 없음) stocks.json 의 마이그레이션, macOS 매니저의 실제 저장
경로, 같은 종목을 두 계좌가 보유할 때 팝오버가 행 2개를 만들고 한 번 들어온 시세를
양쪽에 뿌리는지, 그리고 Windows 매니저가 위젯을 uid 로 가르는지까지 확인한다.
"""

import os
import sys
import shutil
import tempfile
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 한국어 Windows 기본 콘솔은 cp949 라 로그를 그대로 print 하면 깨진다.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

_LOG_PATH = _REPO_ROOT / "smoke_accounts.log"

# 계좌 개념이 없던 v2 스키마 — 이 이슈 이전 사용자의 실제 파일 모양
LEGACY = {
    "stocks": [
        {"code": "379810", "name": "KODEX 미국나스닥100", "avg_price": 29479, "quantity": 335},
        {"code": "000660", "name": "SK하이닉스", "avg_price": 2148000, "quantity": 1},
    ],
    "watchlist": [{"code": "005930", "name": "삼성전자"}],
    "master": {"visible": True, "pos": [60, 20]},
}


def _run(log_fp):
    def log(msg: str) -> None:
        log_fp.write(msg + "\n")
        log_fp.flush()

    from pinstock.core import storage

    tmpdir = Path(tempfile.mkdtemp(prefix="pinstock-accttest-"))
    cfg = tmpdir / "stocks.json"
    storage.CONFIG_FILE = str(cfg)
    storage.PREV_FILE = str(cfg) + ".prev"
    storage.BACKUP_FILE = str(cfg) + ".bak"
    storage._config_dir = lambda: tmpdir

    # ── 1. 구버전 파일 → 기본 계좌 1개 생성 + 전 종목 배정 ──────────────────
    stocks = storage.normalize_stocks_schema(LEGACY["stocks"])
    accounts = storage.ensure_accounts(stocks, [])
    assert len(accounts) == 1, f"기본 계좌 1개여야 하는데 {len(accounts)}개"
    assert accounts[0]["name"] == storage.DEFAULT_ACCOUNT_NAME, accounts[0]
    default_id = accounts[0]["id"]
    assert all(s["account_id"] == default_id for s in stocks), "기본 계좌 배정 누락"
    assert all(s["uid"] for s in stocks), "uid 발급 누락"
    assert len({s["uid"] for s in stocks}) == len(stocks), "uid 가 겹친다"
    # 기존 필드는 그대로 살아 있어야 한다 (마이그레이션이 데이터를 건드리면 안 됨)
    assert stocks[0]["avg_price"] == 29479 and stocks[1]["quantity"] == 1, "기존 값 훼손"
    log("[ok] 1. 계좌 없는 구버전 → 기본 계좌 생성 + 전 종목 배정 + uid 발급")

    # ── 2. idempotent — 두 번 돌려도 계좌/uid 가 바뀌지 않는다 ───────────────
    # 로드와 저장 양쪽에서 부르므로, 부를 때마다 uid 가 갈리면 위젯 위치·메모가
    # 매 저장마다 초기화된다.
    uids_before = [s["uid"] for s in stocks]
    accounts2 = storage.ensure_accounts(stocks, accounts)
    assert accounts2 == accounts, f"계좌가 재생성됐다: {accounts} → {accounts2}"
    assert [s["uid"] for s in stocks] == uids_before, "uid 가 재발급됐다"
    # 정규화를 다시 태워도 마찬가지여야 한다 (_save_config 경로)
    stocks = storage.normalize_stocks_schema(stocks)
    assert [s["uid"] for s in stocks] == uids_before, "정규화가 uid 를 갈아치웠다"
    log("[ok] 2. ensure_accounts / normalize 재실행에도 계좌·uid 불변 (idempotent)")

    # ── 3. 삭제된 계좌를 가리키는 유령 종목 → 첫 계좌로 회수 ─────────────────
    orphan = storage.normalize_stock_schema(
        {"code": "005930", "name": "삼성전자", "avg_price": 70000,
         "quantity": 10, "account_id": "deadbeef"}
    )
    pool = stocks + [orphan]
    storage.ensure_accounts(pool, accounts)
    assert orphan["account_id"] == default_id, f"유령 계좌 회수 실패: {orphan['account_id']}"
    log("[ok] 3. 삭제된 계좌 참조 → 첫 계좌로 회수 (종목이 사라지지 않음)")

    # ── 4. uid 중복 → 재발급 (손으로 고친 JSON / 복붙 사고 방어) ─────────────
    dup_a = storage.normalize_stock_schema({"code": "005930", "avg_price": 70000, "quantity": 10})
    dup_b = storage.normalize_stock_schema({"code": "005930", "avg_price": 80000, "quantity": 5})
    dup_b["uid"] = dup_a["uid"]              # 같은 uid 를 강제로 심는다
    storage.ensure_accounts([dup_a, dup_b], accounts)
    assert dup_a["uid"] != dup_b["uid"], "중복 uid 가 그대로 남았다"
    assert dup_a["avg_price"] == 70000 and dup_b["avg_price"] == 80000, "값이 섞였다"
    log("[ok] 4. uid 중복 → 재발급, 두 보유분의 평단가가 섞이지 않음")

    # ── 5. 같은 종목을 두 계좌가 각자 보유 ──────────────────────────────────
    accounts = storage.normalize_accounts([
        {"id": "acc1", "name": "주계좌", "color": "#89b4fa"},
        {"id": "acc2", "name": "연금", "color": "#a6e3a1"},
    ])
    a1 = storage.normalize_stock_schema(
        {"code": "005930", "name": "삼성전자", "avg_price": 70000, "quantity": 10,
         "account_id": "acc1"})
    a2 = storage.normalize_stock_schema(
        {"code": "005930", "name": "삼성전자", "avg_price": 80000, "quantity": 5,
         "account_id": "acc2"})
    both = [a1, a2]
    storage.ensure_accounts(both, accounts)
    assert a1["account_id"] == "acc1" and a2["account_id"] == "acc2", "계좌 소속이 바뀌었다"
    assert a1["uid"] != a2["uid"], "같은 code 인데 uid 가 같다"
    assert a1["code"] == a2["code"] == "005930"
    log("[ok] 5. 같은 종목 2계좌 보유 — 계좌 소속 유지 + uid 로 구분됨")

    # ── 6. 계좌 정규화 — 무효/중복/긴 이름/잘못된 색 ────────────────────────
    messy = storage.normalize_accounts([
        {"id": "acc1", "name": "주계좌", "color": "#89b4fa"},
        {"id": "acc1", "name": "중복 id", "color": "#ffffff"},   # 중복 → 제거
        {"id": "", "name": "id 없음"},                            # 무효 → 제거
        {"id": "acc3", "name": ""},                               # 이름 없음 → 제거
        {"id": "acc4", "name": "일곱글자짜리이름", "color": "보라색"},  # 절단 + 기본색
        "문자열",                                                  # 형식 파손 → 제거
    ])
    assert [a["id"] for a in messy] == ["acc1", "acc4"], messy
    assert messy[0]["name"] == "주계좌"
    assert len(messy[1]["name"]) == storage.ACCOUNT_NAME_MAX, messy[1]["name"]
    assert messy[1]["color"] == storage.DEFAULT_ACCOUNT_COLOR, messy[1]["color"]
    amap = storage.account_map(messy)
    assert amap["acc1"]["name"] == "주계좌" and set(amap) == {"acc1", "acc4"}
    log(f"[ok] 6. 계좌 정규화 — 중복/무효 제거, 이름 {storage.ACCOUNT_NAME_MAX}자 절단, 색 폴백")

    # ── 7. 계좌 필터 — 전체 / 특정 계좌 / 삭제된 계좌 폴백 ───────────────────
    ALL = storage.ACCOUNT_FILTER_ALL
    assert storage.normalize_account_filter(None, accounts) == ALL
    assert storage.normalize_account_filter("acc2", accounts) == "acc2"
    assert storage.normalize_account_filter("deadbeef", accounts) == ALL, "삭제 계좌 폴백 실패"
    assert storage.stock_in_account(a1, ALL) and storage.stock_in_account(a2, ALL)
    assert storage.stock_in_account(a1, "acc1") and not storage.stock_in_account(a2, "acc1")
    log("[ok] 7. 계좌 필터 — 전체 통과 / 계좌별 분리 / 삭제된 계좌는 전체로 폴백")

    # ── 8. 분리 창의 독립 계좌 필터 ─────────────────────────────────────────
    det = storage.normalize_detached({"view": "holdings", "market_filter": "US",
                                      "account_filter": "acc2"})
    assert det["account_filter"] == "acc2" and det["market_filter"] == "US", det
    assert storage.normalize_detached({})["account_filter"] == ALL, "기본값이 전체가 아니다"
    assert storage.normalize_detached(None)["account_filter"] == ALL, "구버전 폴백 실패"
    log("[ok] 8. 분리 창 account_filter — 저장값 유지 + 구버전은 전체로 폴백")

    # ── 9. 저장 → 로드 라운드트립에서 계좌/uid 보존 ─────────────────────────
    storage.write_config_atomic({**LEGACY, "stocks": both, "accounts": accounts})
    data, warn = storage.read_config()
    assert warn is None, f"경고가 없어야 하는데 {warn!r}"
    loaded = storage.normalize_stocks_schema(data["stocks"])
    loaded_accounts = storage.ensure_accounts(loaded, data["accounts"])
    assert [a["id"] for a in loaded_accounts] == ["acc1", "acc2"], loaded_accounts
    assert [s["uid"] for s in loaded] == [a1["uid"], a2["uid"]], "uid 가 라운드트립에서 바뀌었다"
    assert [s["account_id"] for s in loaded] == ["acc1", "acc2"], "계좌 소속이 바뀌었다"
    # 관심종목은 계좌를 타지 않는다 — 전 계좌 공유
    watch = storage.normalize_watchlist_schema(data["watchlist"])
    assert "account_id" not in watch[0], "관심종목에 계좌가 붙었다"
    log("[ok] 9. 저장→로드 라운드트립 — 계좌/uid/소속 보존, 관심종목은 계좌 무관")

    # ── 10. 실제 macOS 매니저의 저장 경로 ───────────────────────────────────
    # Qt 인스턴스화 없이 가짜 self 로 _save_config 을 직접 부른다
    # (smoke_config_io.py 가 Windows 매니저에 쓰는 방식과 같다).
    from types import SimpleNamespace
    from pinstock.ui_macos.manager import MacAppManager

    fresh = [
        {"code": "005930", "name": "삼성전자", "avg_price": 70000, "quantity": 10},
        {"code": "005930", "name": "삼성전자", "avg_price": 80000, "quantity": 5},
    ]
    mgr = SimpleNamespace(
        config_load_failed=False,
        stocks=fresh, accounts=[], account_filter="없는계좌",
        detached_account_filter=storage.ACCOUNT_FILTER_ALL,
        watchlist=[], watch_tags=[], watch_ma={"ma5": True},
        master_visible=True, master_pos=[60, 20], assets_hidden=False,
        us_return_basis="krw", popover_opacity=1.0, popover_height=None,
        popover_offset=None, pinned=False,
        memo={"text": "", "updated_at": None}, stock_memos={},
        detached_view=None, detached_pos=None, detached_height=None,
        detached_pinned=False, detached_opacity=1.0, detached_market_filter="ALL",
        update_last_check_date=None, update_skipped_version=None,
    )
    mgr._reconcile_accounts = lambda: MacAppManager._reconcile_accounts(mgr)
    MacAppManager._save_config(mgr)

    saved, warn = storage.read_config()
    assert warn is None, f"경고가 없어야 하는데 {warn!r}"
    assert len(saved["accounts"]) == 1, f"기본 계좌가 저장되지 않았다: {saved['accounts']}"
    acc_id = saved["accounts"][0]["id"]
    assert saved["selected_account"] == storage.ACCOUNT_FILTER_ALL, \
        f"없는 계좌 필터가 전체로 폴백되지 않았다: {saved['selected_account']}"
    assert saved["detached"]["account_filter"] == storage.ACCOUNT_FILTER_ALL
    assert all(s["account_id"] == acc_id for s in saved["stocks"]), "저장본에 계좌 소속 없음"
    uids = [s["uid"] for s in saved["stocks"]]
    assert len(set(uids)) == 2, f"같은 종목 2보유분의 uid 가 겹친다: {uids}"
    log("[ok] 10. macOS 매니저 _save_config — 계좌/선택/uid 저장 + 없는 필터 폴백")

    # 같은 매니저로 한 번 더 저장해도 계좌·uid 가 그대로여야 한다.
    # (저장 때마다 갈리면 재시작마다 계좌 소속이 초기화된다)
    MacAppManager._save_config(mgr)
    resaved, _ = storage.read_config()
    assert [a["id"] for a in resaved["accounts"]] == [acc_id], "재저장에 계좌가 재생성됐다"
    assert [s["uid"] for s in resaved["stocks"]] == uids, "재저장에 uid 가 갈렸다"
    log("[ok] 11. 재저장 — 계좌 id / uid 불변")

    # ── 12. 팝오버 — 같은 종목 2계좌면 행도 2개, 시세는 양쪽 다 갱신 ─────────
    # 시세 폴러는 code 당 1개라 갱신도 code 로 한 번만 들어온다. 행이 uid 키가 된
    # 뒤로는 code → 행 역인덱스로 뿌리지 않으면 한쪽 행만 값이 차고 다른 쪽은
    # '─' 로 남는다.
    from PyQt6.QtWidgets import QApplication
    from pinstock.ui_macos.popover import Popover

    app = QApplication.instance() or QApplication([])
    pop = Popover()
    pop.set_stocks(both)                      # 005930 을 acc1/acc2 가 각각 보유
    assert len(pop.rows) == 2, f"행이 2개여야 하는데 {len(pop.rows)}개 (uid 키가 아님)"
    assert set(pop.rows) == {a1["uid"], a2["uid"]}, "행 키가 uid 가 아니다"

    pop.update_stock_price("005930", {
        "name": "삼성전자", "price": 75000, "change_price": 1000, "change_rate": 1.35,
    })
    prices = [r.current_price for r in pop.rows.values()]
    assert prices == [75000, 75000], f"두 행 모두 갱신돼야 하는데 {prices}"

    # 행에서 올라오는 편집/삭제 요청은 uid 여야 한다 — code 면 어느 계좌의 보유분을
    # 고칠지 결정할 수 없다.
    got: list[str] = []
    pop.edit_requested.connect(got.append)
    pop.rows[a2["uid"]].edit_requested.emit(pop.rows[a2["uid"]].uid)
    assert got == [a2["uid"]], f"편집 시그널이 uid 가 아니다: {got}"
    pop.deleteLater()
    log("[ok] 12. 팝오버 — 같은 종목 2계좌 → 행 2개, 시세 동시 갱신, 시그널은 uid")

    # ── 13. 계좌 줄 / 계좌 필터 / 뱃지 ──────────────────────────────────────
    accounts3 = storage.normalize_accounts([
        {"id": "acc1", "name": "주계좌", "color": "#89b4fa"},
        {"id": "acc2", "name": "연금", "color": "#a6e3a1"},
        {"id": "acc3", "name": "ISA", "color": "#f9e2af"},
    ])
    mixed = storage.normalize_stocks_schema([
        {"code": "005930", "name": "삼성전자", "avg_price": 70000, "quantity": 10,
         "account_id": "acc1"},
        {"code": "005930", "name": "삼성전자", "avg_price": 80000, "quantity": 5,
         "account_id": "acc2"},
        {"code": "NVDA", "name": "NVIDIA", "market": "US", "avg_price": 120,
         "quantity": 3, "account_id": "acc1"},
    ])
    storage.ensure_accounts(mixed, accounts3)

    pop = Popover()
    pop.set_accounts(accounts3)
    pop.set_stocks(mixed)

    assert pop._account_bar_shown(), "계좌 2개 이상인데 계좌 줄이 안 뜬다"
    assert set(pop.account_bar.buttons) == {ALL, "acc1", "acc2", "acc3"}, \
        f"계좌 버튼 구성이 다르다: {sorted(pop.account_bar.buttons)}"
    assert len(pop.rows) == 3, f"전체 보기 행 3개여야 하는데 {len(pop.rows)}"
    assert all(not r.account_lbl.isHidden() for r in pop.rows.values()), "전체 보기인데 뱃지가 없다"

    pop._set_account_filter("acc1")
    assert len(pop.rows) == 2, f"acc1 행 2개여야 하는데 {len(pop.rows)}"
    assert all(r.account_lbl.isHidden() for r in pop.rows.values()), \
        "특정 계좌를 보는 중인데 뱃지가 남았다"

    # 계좌 × 시장은 AND — acc1 의 미국 종목만
    pop.set_market_filter("US")
    pop._render()
    assert [r.data["code"] for r in pop.rows.values()] == ["NVDA"], \
        f"계좌×시장 AND 실패: {[r.data['code'] for r in pop.rows.values()]}"
    pop.set_market_filter("ALL")
    pop._render()

    # 빈 계좌는 어느 계좌가 비었는지 알려 준다 (종목을 다 잃은 줄 알면 안 된다)
    pop._set_account_filter("acc3")
    assert not pop.rows and not pop.empty_lbl.isHidden(), "빈 계좌인데 행이 남았다"
    assert "ISA" in pop.empty_lbl.text(), f"빈 계좌 안내에 계좌명이 없다: {pop.empty_lbl.text()!r}"

    # 관심 탭에서는 계좌 줄이 사라지고 핀이 그만큼 위로 붙는다
    pin_with_bar = pop.pin_btn.y()
    pop._set_view("watch")
    assert not pop._account_bar_shown(), "관심 뷰인데 계좌 줄이 남았다"
    assert pop.pin_btn.y() < pin_with_bar, "계좌 줄이 사라졌는데 핀이 안 올라왔다"
    pop._set_view("holdings")
    assert pop.pin_btn.y() == pin_with_bar, "보유로 돌아왔는데 핀 위치가 안 맞다"

    # 계좌가 하나뿐이면 줄 자체를 감춘다 (계좌를 안 나눠 쓰는 사용자에게는 군더더기)
    pop.set_accounts(accounts3[:1])
    assert not pop._account_bar_shown(), "계좌 1개인데 계좌 줄이 떴다"
    pop.deleteLater()
    log("[ok] 13. 계좌 줄 표시 조건 / 계좌×시장 AND / 뱃지 / 빈 계좌 안내 / 핀 위치")

    # ── 14. 계좌 관리 다이얼로그 — 순서 변경 / 삭제 시 종목 처리 ─────────────
    # 삭제는 되돌릴 수 없는 유일한 경로라(평단가/수량은 일일 백업에서만 복구),
    # '이동'과 '함께 삭제'가 각각 정확히 동작하는지 모달을 흉내 내 확인한다.
    from pinstock.ui_windows import manage_dialog as md

    _orig_exec = md.QMessageBox.exec
    _orig_clicked = md.QMessageBox.clickedButton
    _orig_question = md.QMessageBox.question
    _orig_info = md.QMessageBox.information

    def _press(label: str):
        """다음 QMessageBox 에서 label 이 들어간 버튼을 누른 것으로 처리."""
        md.QMessageBox.exec = lambda self: 0
        md.QMessageBox.clickedButton = lambda self: next(
            (b for b in self.buttons() if label in b.text()), None
        )

    md.QMessageBox.question = classmethod(
        lambda cls, *a, **k: md.QMessageBox.StandardButton.Yes
    )
    info_calls: list[str] = []
    md.QMessageBox.information = classmethod(
        lambda cls, _p, _t, text, *a, **k: info_calls.append(text)
    )
    try:
        def fresh_dialog():
            accounts = [dict(a) for a in accounts3]
            stocks = [dict(s) for s in mixed]
            return md.AccountManagerDialog(accounts, stocks), accounts, stocks

        # 순서 변경 = 팝오버 계좌 버튼 순서
        dlg, _, _ = fresh_dialog()
        dlg.table.selectRow(2)
        dlg._move_selected(-1)
        assert [a["id"] for a in dlg.get_accounts()] == ["acc1", "acc3", "acc2"], \
            [a["id"] for a in dlg.get_accounts()]
        assert dlg.table.currentRow() == 1, "이동 후 선택이 따라오지 않았다"
        dlg.deleteLater()

        # 종목 있는 계좌 삭제 → '다른 계좌로 이동' (남은 계좌가 2개라 대상은 물어본다)
        dlg, _, _ = fresh_dialog()
        md.AccountPickDialog.exec = lambda self: 1
        md.AccountPickDialog.selected_id = lambda self: "acc3"
        dlg.table.selectRow(0)                      # acc1 — 종목 2개
        _press("이동")
        dlg._delete_selected()
        left = dlg.get_stocks()
        assert [a["id"] for a in dlg.get_accounts()] == ["acc2", "acc3"], dlg.get_accounts()
        assert len(left) == 3, f"이동인데 종목이 사라졌다: {len(left)}"
        assert {s["account_id"] for s in left} == {"acc2", "acc3"}, \
            {s["account_id"] for s in left}
        dlg.deleteLater()

        # 같은 상황에서 '종목도 함께 삭제'
        dlg, _, _ = fresh_dialog()
        dlg.table.selectRow(0)
        _press("함께 삭제")
        dlg._delete_selected()
        left = dlg.get_stocks()
        assert len(left) == 1 and left[0]["account_id"] == "acc2", left
        dlg.deleteLater()

        # 취소하면 계좌도 종목도 그대로
        dlg, _, _ = fresh_dialog()
        dlg.table.selectRow(0)
        _press("취소")
        dlg._delete_selected()
        assert len(dlg.get_accounts()) == 3 and len(dlg.get_stocks()) == 3, "취소인데 바뀌었다"
        dlg.deleteLater()

        # 마지막 한 개는 지울 수 없다 (계좌 0개면 종목을 둘 곳이 없다)
        solo = md.AccountManagerDialog([dict(accounts3[0])], [])
        solo.table.selectRow(0)
        info_calls.clear()
        solo._delete_selected()
        assert len(solo.get_accounts()) == 1, "마지막 계좌가 삭제됐다"
        assert info_calls and "최소 1개" in info_calls[0], info_calls
        solo.deleteLater()
    finally:
        md.QMessageBox.exec = _orig_exec
        md.QMessageBox.clickedButton = _orig_clicked
        md.QMessageBox.question = _orig_question
        md.QMessageBox.information = _orig_info
    log("[ok] 14. 계좌 관리 — 순서 변경 / 삭제 시 이동·함께삭제·취소 / 마지막 계좌 보호")

    # ── 15. 종목 추가·수정 창의 계좌 선택 ───────────────────────────────────
    # 계좌가 1개면 고를 게 없으니 행 자체를 넣지 않는다 (그때는 매니저가 채운다).
    solo_dlg = md.StockDialog(accounts=accounts3[:1], default_account="acc1")
    assert solo_dlg.account_combo is None, "계좌 1개인데 계좌 행이 생겼다"
    assert "account_id" not in solo_dlg.get_data(), "계좌 행이 없는데 값을 내보냈다"
    solo_dlg.deleteLater()

    add_dlg = md.StockDialog(accounts=accounts3, default_account="acc2")
    assert add_dlg.account_combo is not None, "계좌 3개인데 계좌 행이 없다"
    assert add_dlg.get_data()["account_id"] == "acc2", "지금 보는 계좌가 기본값이 아니다"
    add_dlg.deleteLater()

    edit_dlg = md.StockDialog(data=dict(mixed[1]), accounts=accounts3)   # acc2 보유분
    assert edit_dlg.account_combo.currentData() == "acc2", "수정 창 기본값이 그 보유분의 계좌가 아니다"
    edit_dlg.account_combo.setCurrentIndex(2)                           # acc3 로 이동
    assert edit_dlg.get_data()["account_id"] == "acc3", "계좌 이동이 반영되지 않았다"
    edit_dlg.deleteLater()
    log("[ok] 15. 종목 창 계좌 선택 — 1개면 숨김 / 추가 기본값 / 수정 시 계좌 이동")

    # ── 16. 종목 관리 다이얼로그 — 계좌 컬럼 / 계좌 필터 ────────────────────
    table_stocks = [dict(s) for s in mixed]
    dlg = md.ManageStocksDialog(table_stocks, accounts=accounts3)
    assert not dlg.table.isColumnHidden(dlg.COL_ACCOUNT), "계좌 3개인데 계좌 컬럼이 숨겨졌다"
    assert dlg.account_filter_combo is not None, "계좌 필터 콤보가 없다"
    assert dlg.table.rowCount() == 3, dlg.table.rowCount()
    INTERNAL = md.QAbstractItemView.DragDropMode.InternalMove
    assert dlg.table.dragDropMode() == INTERNAL, "필터가 없는데 순서 드래그가 막혔다"

    # 표에서 계좌 이동 — acc1 의 NVDA 를 acc3 로
    row = next(r for r in range(dlg.table.rowCount())
               if dlg.table.item(r, dlg.COL_CODE).text() == "NVDA")
    combo = dlg.table.cellWidget(row, dlg.COL_ACCOUNT)
    assert combo.currentData() == "acc1", combo.currentData()
    # 콤보는 activated(사용자 선택)에만 반응한다 — 사용자가 고른 것과 같게 흉내 낸다
    combo.setCurrentIndex(2)                                  # acc3
    combo.activated.emit(2)
    moved = next(s for s in dlg.get_stocks() if s["code"] == "NVDA")
    assert moved["account_id"] == "acc3", f"표에서 계좌 이동이 반영 안 됨: {moved['account_id']}"

    # 계좌 필터 → 그 계좌 행만, 순서 드래그는 잠근다
    dlg.account_filter_combo.setCurrentIndex(
        dlg.account_filter_combo.findData("acc3"))
    assert [dlg.table.item(r, dlg.COL_CODE).text() for r in range(dlg.table.rowCount())] \
        == ["NVDA"], "계좌 필터가 안 걸렸다"
    assert dlg.table.dragDropMode() != INTERNAL, "걸러진 표인데 순서 드래그가 열려 있다"
    dlg.account_filter_combo.setCurrentIndex(0)               # 전체
    assert dlg.table.dragDropMode() == INTERNAL, "전체로 돌아왔는데 드래그가 안 열렸다"
    dlg.deleteLater()

    # 계좌가 1개면 컬럼·필터를 통째로 감춘다
    solo_table = md.ManageStocksDialog([dict(s) for s in mixed], accounts=accounts3[:1])
    assert solo_table.table.isColumnHidden(solo_table.COL_ACCOUNT), "계좌 1개인데 컬럼이 보인다"
    assert solo_table.account_filter_combo is None, "계좌 1개인데 필터 콤보가 있다"
    solo_table.deleteLater()

    # 표 안의 '추가'/'수정' 도 계좌 선택 행을 띄운다 — 표의 계좌 콤보로만 옮길 수
    # 있으면 같은 창 안에서 경로마다 규칙이 달라진다.
    dlg2 = md.ManageStocksDialog([dict(s) for s in mixed], accounts=accounts3,
                                 account_filter="acc3")
    seen_kw: dict = {}
    _orig_sd_init = md.StockDialog.__init__

    def _spy_init(self, *a, **kw):
        seen_kw.clear()
        seen_kw.update(kw)
        _orig_sd_init(self, *a, **kw)

    md.StockDialog.__init__ = _spy_init
    md.StockDialog.exec = lambda self: 0
    try:
        dlg2._add()
        assert seen_kw.get("accounts") is accounts3, "추가 창에 계좌 목록이 안 넘어갔다"
        assert seen_kw.get("default_account") == "acc3",             f"걸러 보는 계좌가 기본값이 아니다: {seen_kw.get('default_account')}"
        dlg2.table.selectRow(0)
        dlg2._edit_selected()
        assert seen_kw.get("accounts") is accounts3, "수정 창에 계좌 목록이 안 넘어갔다"
    finally:
        md.StockDialog.__init__ = _orig_sd_init
        del md.StockDialog.exec
    dlg2.deleteLater()
    log("[ok] 16. 종목 관리 — 계좌 컬럼으로 이동 / 계좌 필터 / 표 안 추가·수정도 계좌 선택")

    # ── 17. Windows 매니저 — 보유 항목 키가 uid 인지 (실제 WidgetManager 기동) ──
    # 여기가 code 로 되돌아가면 "계좌1 삼성전자"가 "계좌2 삼성전자"에 덮여 위젯
    # 하나가 사라지고, 삭제가 어느 보유분을 지울지 결정할 수 없다.
    import json
    import pinstock.ui_windows.manager as WM
    import pinstock.ui_windows.floating_widget as WFW

    wcfg = tmpdir / "win_stocks.json"
    storage.CONFIG_FILE = str(wcfg)
    storage.PREV_FILE = str(wcfg) + ".prev"
    storage.BACKUP_FILE = str(wcfg) + ".bak"
    WM.CONFIG_FILE = str(wcfg)
    WM.BACKUP_FILE = str(wcfg) + ".bak"
    wcfg.write_text(json.dumps({
        "accounts": [{"id": "acc1", "name": "주계좌", "color": "#89b4fa"},
                     {"id": "acc2", "name": "연금", "color": "#a6e3a1"}],
        "selected_account": "acc2",
        "stocks": [
            {"code": "005930", "uid": "u1", "account_id": "acc1", "name": "삼성전자",
             "avg_price": 70000, "quantity": 10, "pos": [100, 100]},
            {"code": "005930", "uid": "u2", "account_id": "acc2", "name": "삼성전자",
             "avg_price": 60000, "quantity": 5, "pos": [300, 300]},
        ],
    }, ensure_ascii=False), encoding="utf-8")

    # 실제 HTTP 는 나가지 않게 — 폴링/환율만 막고 나머지는 진짜 코드로 돈다
    WM.fetch_usd_krw_rate = lambda: None
    WM.stock_index.start_background_refresh = lambda: None
    for _n in ("fetch_stock", "fetch_us_stock", "fetch_minute_chart",
               "fetch_daily_chart", "fetch_us_minute_chart", "fetch_us_daily_chart"):
        setattr(WFW, _n, lambda _c: None)

    wmgr = WM.WidgetManager(app)
    assert sorted(wmgr.widgets) == ["u1", "u2"],         f"같은 종목 2계좌인데 위젯이 uid 로 갈라지지 않았다: {sorted(wmgr.widgets)}"
    assert wmgr.widgets["u1"].pos().x() == 100 and wmgr.widgets["u2"].pos().x() == 300,         "보유분별 위젯 위치가 따로 복원되지 않았다"
    assert wmgr.widgets["u1"].data["avg_price"] == 70000
    assert wmgr.widgets["u2"].data["avg_price"] == 60000
    assert wmgr.account_filter == "acc2", "선택 계좌가 복원되지 않았다"

    # 한쪽만 삭제 — 다른 계좌 보유분은 그대로 남아야 한다
    wmgr._on_delete("u1")
    assert sorted(wmgr.widgets) == ["u2"], f"엉뚱한 보유분이 지워졌다: {sorted(wmgr.widgets)}"
    assert [s["uid"] for s in wmgr.stocks] == ["u2"]

    wsaved, _ = storage.read_config()
    assert [a["id"] for a in wsaved["accounts"]] == ["acc1", "acc2"], "계좌가 갈렸다"
    assert wsaved["selected_account"] == "acc2"
    assert wsaved["stocks"][0]["uid"] == "u2", "저장에 uid 가 갈렸다"
    wmgr.dispose()
    for _w in list(wmgr.widgets.values()):
        _w.close()
    log("[ok] 17. Windows 매니저 — 위젯이 uid 키 / 계좌·선택 복원 / 한쪽만 삭제")

    # ── 18. Windows 폴러 — code 당 1개, 중계, 폴러 삭제 시 승계 ────────────
    # Windows 는 위젯이 각자 타이머로 시세를 가져온다. 그대로 두면 같은 종목을 두
    # 계좌가 보유할 때 HTTP 호출이 종목당 2배로 늘고, 동기 호출이라 그만큼 UI 가
    # 멎는다. 폴러는 code 당 1개만 두고 받아온 값을 나머지 위젯에 중계한다.
    from PyQt6.QtCore import QEventLoop, QTimer

    pcfg = tmpdir / "poll_stocks.json"
    storage.CONFIG_FILE = str(pcfg)
    storage.PREV_FILE = str(pcfg) + ".prev"
    storage.BACKUP_FILE = str(pcfg) + ".bak"
    WM.CONFIG_FILE = str(pcfg)
    WM.BACKUP_FILE = str(pcfg) + ".bak"
    pcfg.write_text(json.dumps({
        "accounts": [{"id": "acc1", "name": "주계좌", "color": "#89b4fa"},
                     {"id": "acc2", "name": "연금", "color": "#a6e3a1"}],
        "selected_account": "ALL",
        "stocks": [
            {"code": "005930", "uid": "p1", "account_id": "acc1", "name": "삼성전자",
             "avg_price": 70000, "quantity": 10, "pos": [100, 100]},
            {"code": "005930", "uid": "p2", "account_id": "acc2", "name": "삼성전자",
             "avg_price": 60000, "quantity": 5, "pos": [300, 300]},
            {"code": "000660", "uid": "p3", "account_id": "acc1", "name": "SK하이닉스",
             "avg_price": 150000, "quantity": 1, "pos": [500, 100]},
        ],
    }, ensure_ascii=False), encoding="utf-8")

    price_calls: list = []

    def _fake_quote(code):
        price_calls.append(code)
        return {"name": "삼성전자" if code == "005930" else "SK하이닉스",
                "price": 80000.0, "change_price": 1000.0, "change_rate": 1.27}

    WFW.fetch_stock = _fake_quote
    WFW.fetch_minute_chart = lambda _c: {"prices": [1, 2, 3], "open": 1}

    pmgr = WM.WidgetManager(app)
    assert [pmgr.widgets[u].is_polling for u in ("p1", "p2", "p3")] == [True, False, True],         "같은 종목의 두 보유분이 각자 폴링하고 있다"

    loop = QEventLoop()                       # stagger 지연(위젯당 0.6초) 통과
    QTimer.singleShot(2500, loop.quit)
    loop.exec()
    assert price_calls.count("005930") == 1, f"같은 종목을 두 번 폴링했다: {price_calls}"
    assert pmgr.widgets["p2"].current_price == 80000.0, "중계로 시세가 들어오지 않았다"

    # 폴러였던 보유분을 지우면 같은 종목의 남은 위젯이 승계해 즉시 다시 돈다
    price_calls.clear()
    pmgr._on_delete("p1")
    assert pmgr.widgets["p2"].is_polling, "폴러가 삭제됐는데 승계되지 않았다"
    assert "005930" in price_calls, "승계한 위젯이 곧바로 폴링을 시작하지 않았다"
    pmgr.dispose()
    for _w in list(pmgr.widgets.values()):
        _w.close()
    log("[ok] 18. Windows 폴러 — code 당 1개 / 시세 중계 / 폴러 삭제 시 승계")

    # ── 19. Windows 매니저 ↔ 계좌 다이얼로그 연결 ──────────────────────────
    # 다이얼로그는 깊은 복사본을 다룬다. 결과를 통째로 갈아끼우면 widget.data 와
    # identity 가 끊겨 이후 위젯에서 한 수정이 저장에 반영되지 않고, 위젯을 다시
    # 세우면 위치와 시세가 초기화된다. 결과는 원본 dict 에 제자리로 옮겨야 한다.
    acfg = tmpdir / "acct_stocks.json"
    storage.CONFIG_FILE = str(acfg)
    storage.PREV_FILE = str(acfg) + ".prev"
    storage.BACKUP_FILE = str(acfg) + ".bak"
    WM.CONFIG_FILE = str(acfg)
    WM.BACKUP_FILE = str(acfg) + ".bak"
    acfg.write_text(json.dumps({
        "accounts": [{"id": "acc1", "name": "주계좌", "color": "#89b4fa"}],
        "selected_account": "ALL",
        "stocks": [
            {"code": "005930", "uid": "a1", "account_id": "acc1", "name": "삼성전자",
             "avg_price": 70000, "quantity": 10, "pos": [100, 100]},
            {"code": "000660", "uid": "a2", "account_id": "acc1", "name": "SK하이닉스",
             "avg_price": 150000, "quantity": 1, "pos": [300, 100]},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    for _n in ("fetch_stock", "fetch_us_stock", "fetch_minute_chart",
               "fetch_daily_chart", "fetch_us_minute_chart", "fetch_us_daily_chart"):
        setattr(WFW, _n, lambda _c: None)

    amgr = WM.WidgetManager(app)
    w_a1 = amgr.widgets["a1"]
    d_a1 = amgr.stocks[0]

    # 계좌를 하나 더 만들고 삼성전자를 그쪽으로 옮긴다 (실제 다이얼로그의 결과 계약)
    _orig_acct_exec = md.AccountManagerDialog.exec

    def _add_and_move(self):
        self._accounts.append({"id": "acc2", "name": "연금", "color": "#a6e3a1"})
        for s in self._stocks:
            if s["code"] == "005930":
                s["account_id"] = "acc2"
        return 1

    md.AccountManagerDialog.exec = _add_and_move
    try:
        amgr.open_account_dialog()
    finally:
        md.AccountManagerDialog.exec = _orig_acct_exec

    assert [a["id"] for a in amgr.accounts] == ["acc1", "acc2"], amgr.accounts
    assert amgr.widgets["a1"] is w_a1, "계좌만 바뀌었는데 위젯이 다시 만들어졌다"
    assert amgr.stocks[0] is d_a1, "self.stocks 와 widget.data 의 identity 가 끊겼다"
    assert w_a1.pos().x() == 100, "위젯 위치가 초기화됐다"
    assert d_a1["account_id"] == "acc2", "계좌 이동이 원본에 반영되지 않았다"
    assert len(w_a1._accounts) == 2, "위젯 수정 창에 쓸 계좌 목록이 갱신되지 않았다"

    # 계좌를 지우면서 소속 종목도 함께 삭제 → 위젯도 같이 사라져야 한다
    def _delete_with_stocks(self):
        self._accounts[:] = [a for a in self._accounts if a["id"] != "acc2"]
        self._stocks[:] = [s for s in self._stocks if s["account_id"] != "acc2"]
        return 1

    md.AccountManagerDialog.exec = _delete_with_stocks
    try:
        amgr.open_account_dialog()
    finally:
        md.AccountManagerDialog.exec = _orig_acct_exec

    assert [a["id"] for a in amgr.accounts] == ["acc1"], amgr.accounts
    assert list(amgr.widgets) == ["a2"], f"함께 삭제된 종목의 위젯이 남았다: {list(amgr.widgets)}"
    assert [s["uid"] for s in amgr.stocks] == ["a2"]

    # 종목 추가 창에는 계좌 목록과 '지금 보고 있는 계좌'가 기본값으로 넘어간다
    _seen: dict = {}
    _orig_stock_dlg = WM.StockDialog

    class _SpyStockDialog:
        def __init__(self, **kw):
            _seen.update(kw)

        def exec(self):
            return 0

    WM.StockDialog = _SpyStockDialog
    try:
        amgr.account_filter = "acc1"
        amgr.open_add_dialog()
    finally:
        WM.StockDialog = _orig_stock_dlg
    assert _seen.get("default_account") == "acc1", _seen
    assert [a["id"] for a in _seen.get("accounts", [])] == ["acc1"], _seen

    asaved, _ = storage.read_config()
    assert [a["id"] for a in asaved["accounts"]] == ["acc1"]
    assert asaved["stocks"][0]["uid"] == "a2"
    amgr.dispose()
    for _w in list(amgr.widgets.values()):
        _w.close()
    log("[ok] 19. Windows 매니저 ↔ 계좌 다이얼로그 — 이동 시 위젯 유지 / 함께 삭제 / 추가 기본 계좌")

    # ── 20. 계좌 이동으로 겹친 보유분 합치기 ────────────────────────────────
    # 종목 추가는 (code, account_id) 중복을 막지만 계좌 이동은 그 규칙을 우회한다.
    # 그대로 두면 한 계좌의 같은 종목이 두 줄로 남아 요약과 표가 어긋난다.
    from pinstock.core.storage import merge_account_duplicates

    pair = [
        {"code": "005930", "uid": "m1", "account_id": "acc1", "name": "삼성전자",
         "avg_price": 70000, "quantity": 10, "pos": [100, 100]},
        {"code": "005930", "uid": "m2", "account_id": "acc1", "name": "삼성전자",
         "avg_price": 60000, "quantity": 5, "pos": [300, 300]},
        {"code": "000660", "uid": "m3", "account_id": "acc1", "name": "SK하이닉스",
         "avg_price": 100, "quantity": 1},
    ]
    out, records = merge_account_duplicates([dict(s) for s in pair], preferred_uids={"m2"})
    assert [s["uid"] for s in out] == ["m2", "m3"], [s["uid"] for s in out]
    kept = out[0]
    assert kept["avg_price"] == 66667, kept["avg_price"]      # (70000*10+60000*5)/15
    assert kept["quantity"] == 15, kept["quantity"]
    assert kept["pos"] == [300, 300], "흡수하는 쪽(안 움직인 쪽)의 위치가 유지돼야 한다"
    assert records[0]["kept_uid"] == "m2" and records[0]["dropped_uid"] == "m1"

    # 미국 주식 — 매수환율은 매입원가(원화) 기준으로 다시 잡는다
    us_pair = [
        {"code": "NVDA", "uid": "n1", "account_id": "acc1", "market": "US",
         "currency": "USD", "avg_price": 100.0, "quantity": 10, "buy_exchange_rate": 1300.0},
        {"code": "NVDA", "uid": "n2", "account_id": "acc1", "market": "US",
         "currency": "USD", "avg_price": 200.0, "quantity": 10, "buy_exchange_rate": 1400.0},
    ]
    us_out, _ = merge_account_duplicates([dict(s) for s in us_pair], preferred_uids={"n1"})
    assert len(us_out) == 1
    assert us_out[0]["avg_price"] == 150.0, us_out[0]["avg_price"]
    # (100*10*1300 + 200*10*1400) / (150*20) = 4_100_000 / 3_000 ≈ 1366.6667
    assert abs(us_out[0]["buy_exchange_rate"] - 1366.6667) < 0.001, us_out[0]

    # 한쪽에만 매수환율이 있으면 통째로 버린다 — 반쪽 환율로 전체를 환산하면
    # 기록 없는 쪽 매입원가가 그만큼 틀어진다.
    half = [dict(us_pair[0]), {k: v for k, v in us_pair[1].items()
                               if k != "buy_exchange_rate"}]
    half_out, _ = merge_account_duplicates(half, preferred_uids={"n1"})
    assert "buy_exchange_rate" not in half_out[0], half_out[0]

    # ── Windows 매니저 — 확인 창에서 취소하면 이동 자체를 되돌린다 ──────────
    mcfg = tmpdir / "merge_stocks.json"
    storage.CONFIG_FILE = str(mcfg)
    storage.PREV_FILE = str(mcfg) + ".prev"
    storage.BACKUP_FILE = str(mcfg) + ".bak"
    WM.CONFIG_FILE = str(mcfg)
    WM.BACKUP_FILE = str(mcfg) + ".bak"
    mcfg.write_text(json.dumps({
        "accounts": [{"id": "acc1", "name": "주계좌", "color": "#89b4fa"},
                     {"id": "acc2", "name": "연금", "color": "#a6e3a1"}],
        "selected_account": "ALL",
        "stocks": [
            {"code": "005930", "uid": "k1", "account_id": "acc1", "name": "삼성전자",
             "avg_price": 70000, "quantity": 10, "pos": [100, 100]},
            {"code": "005930", "uid": "k2", "account_id": "acc2", "name": "삼성전자",
             "avg_price": 60000, "quantity": 5, "pos": [300, 300]},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    for _n in ("fetch_stock", "fetch_us_stock", "fetch_minute_chart",
               "fetch_daily_chart", "fetch_us_minute_chart", "fetch_us_daily_chart"):
        setattr(WFW, _n, lambda _c: None)

    mmgr = WM.WidgetManager(app)
    _YES = md.QMessageBox.StandardButton.Yes
    _NO = md.QMessageBox.StandardButton.No
    _orig_wm_question = WM.QMessageBox.question

    wk2 = mmgr.widgets["k2"]
    WM.QMessageBox.question = staticmethod(lambda *a, **k: _NO)
    try:
        wk2.prev_account_id = "acc2"
        wk2.data["account_id"] = "acc1"          # 수정 창에서 계좌를 옮긴 상태
        mmgr._on_edited("k2")
        assert sorted(mmgr.widgets) == ["k1", "k2"], "취소했는데 위젯이 사라졌다"
        assert wk2.data["account_id"] == "acc2", "취소했는데 계좌가 되돌아가지 않았다"

        WM.QMessageBox.question = staticmethod(lambda *a, **k: _YES)
        wk2.prev_account_id = "acc2"
        wk2.data["account_id"] = "acc1"
        mmgr._on_edited("k2")
    finally:
        WM.QMessageBox.question = _orig_wm_question

    assert list(mmgr.widgets) == ["k1"], f"흡수된 위젯이 안 닫혔다: {list(mmgr.widgets)}"
    assert mmgr.stocks[0]["avg_price"] == 66667 and mmgr.stocks[0]["quantity"] == 15
    assert mmgr.widgets["k1"].pos().x() == 100, "안 움직인 쪽 위젯 위치가 바뀌었다"
    msaved, _ = storage.read_config()
    assert len(msaved["stocks"]) == 1 and msaved["stocks"][0]["uid"] == "k1"
    mmgr.dispose()
    for _w in list(mmgr.widgets.values()):
        _w.close()
    log("[ok] 20. 계좌 이동 중복 — 가중평균 병합 / 매수환율 재계산 / 취소 시 이동 되돌리기")

    # ── 21. Windows 마스터 위젯 계좌 필터 ──────────────────────────────────
    # 풋터(25px)는 우측 90px 를 투명도 슬라이더에 내주고 있어 최소 폭에서 시장 버튼
    # 3개로 이미 꽉 찬다. 계좌 필터는 줄을 따로 두되, 계좌가 1개면 높이를 0 으로
    # 접어 카드 크기가 계좌 기능 이전과 완전히 같아야 한다.
    fcfg = tmpdir / "filter_stocks.json"
    storage.CONFIG_FILE = str(fcfg)
    storage.PREV_FILE = str(fcfg) + ".prev"
    storage.BACKUP_FILE = str(fcfg) + ".bak"
    WM.CONFIG_FILE = str(fcfg)
    WM.BACKUP_FILE = str(fcfg) + ".bak"
    fcfg.write_text(json.dumps({
        "accounts": [{"id": "acc1", "name": "주계좌", "color": "#89b4fa"},
                     {"id": "acc2", "name": "연금", "color": "#a6e3a1"}],
        "selected_account": "ALL",
        "stocks": [
            {"code": "005930", "uid": "f1", "account_id": "acc1", "name": "삼성전자",
             "avg_price": 70000, "quantity": 10, "pos": [100, 100]},
            {"code": "000660", "uid": "f2", "account_id": "acc2", "name": "SK하이닉스",
             "avg_price": 100000, "quantity": 2, "pos": [300, 100]},
            {"code": "NVDA", "uid": "f3", "account_id": "acc2", "name": "NVIDIA",
             "market": "US", "currency": "USD", "avg_price": 100.0, "quantity": 1,
             "pos": [500, 100]},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    for _n in ("fetch_stock", "fetch_us_stock", "fetch_minute_chart",
               "fetch_daily_chart", "fetch_us_minute_chart", "fetch_us_daily_chart"):
        setattr(WFW, _n, lambda _c: None)
    WM.fetch_usd_krw_rate = lambda: {"rate": 1400.0}

    fmgr = WM.WidgetManager(app)
    master = fmgr.master_widget
    master.show()
    for _w in fmgr.widgets.values():
        _w.show()

    def _visible():
        return sorted(u for u, w in fmgr.widgets.items() if w.isVisible())

    from pinstock.ui_windows.master_widget import MasterWidget as _MW
    assert master.account_row.isVisible(), "계좌 2개인데 계좌 줄이 안 보인다"
    assert master.H == _MW.GRID_H + _MW.ACCOUNT_H + _MW.FOOTER_H, master.H
    assert master.footer.y() == _MW.GRID_H + _MW.ACCOUNT_H, "풋터가 계좌 줄 아래로 안 내려갔다"
    assert [master.account_combo.itemText(i)
            for i in range(master.account_combo.count())] == ["전체", "주계좌", "연금"]
    assert _visible() == ["f1", "f2", "f3"]

    # 계좌 필터 — 그 계좌 종목만, 선택은 저장까지
    fmgr._on_account_filter_changed("acc2")
    assert _visible() == ["f2", "f3"], _visible()
    assert storage.read_config()[0]["selected_account"] == "acc2"

    # 계좌 × 시장은 AND, 요약은 둘 다 통과한 것만 더한다
    fmgr._on_market_filter_changed("US")
    assert _visible() == ["f3"], _visible()
    summed = [s for s in fmgr.stocks
              if fmgr._matches_market_filter(s)
              and WM.stock_in_account(s, fmgr.account_filter)]
    assert [s["uid"] for s in summed] == ["f3"], summed
    fmgr._on_market_filter_changed("ALL")

    # 환율 폴링은 필터와 무관하게 전 계좌 기준 — 다른 계좌를 보는 중에 계좌를
    # 되돌리면 환율이 이미 맞아 있어야 한다
    fmgr._on_account_filter_changed("acc1")
    assert _visible() == ["f1"], _visible()
    assert fmgr.fx_timer.isActive(), "미국 종목이 다른 계좌에 있는데 환율이 멈췄다"

    # 계좌가 1개로 줄면 줄을 도로 접고 필터는 '전체'로 폴백한다
    _orig_acct_exec2 = md.AccountManagerDialog.exec

    def _keep_one(self):
        self._accounts[:] = [a for a in self._accounts if a["id"] == "acc2"]
        for s in self._stocks:
            s["account_id"] = "acc2"
        return 1

    md.AccountManagerDialog.exec = _keep_one
    _orig_q = WM.QMessageBox.question
    WM.QMessageBox.question = staticmethod(lambda *a, **k: md.QMessageBox.StandardButton.Yes)
    try:
        fmgr.open_account_dialog()
    finally:
        md.AccountManagerDialog.exec = _orig_acct_exec2
        WM.QMessageBox.question = _orig_q

    assert not master.account_row.isVisible(), "계좌 1개인데 계좌 줄이 남아 있다"
    assert master.H == _MW.GRID_H + _MW.FOOTER_H, f"카드 높이가 원래대로 안 돌아왔다: {master.H}"
    assert fmgr.account_filter == "ALL", fmgr.account_filter
    assert len(_visible()) == 3, "필터가 전체로 돌아왔는데 숨은 위젯이 있다"
    fmgr.dispose()
    master.close()
    for _w in list(fmgr.widgets.values()):
        _w.close()
    log("[ok] 21. 마스터 계좌 필터 — 계좌 1개면 줄 접힘 / 계좌×시장 AND / 환율은 전 계좌 기준")

    # ── 22. Windows 위젯 좌측 계좌색 막대 ──────────────────────────────────
    # '전체' 보기에서 어느 계좌 것인지 구분하는 용도다. 특정 계좌를 보는 중이거나
    # 계좌가 1개면 전 위젯이 같은 계좌라 색이 아무 정보도 주지 않는다 — 감춘다.
    bcfg = tmpdir / "bar_stocks.json"
    storage.CONFIG_FILE = str(bcfg)
    storage.PREV_FILE = str(bcfg) + ".prev"
    storage.BACKUP_FILE = str(bcfg) + ".bak"
    WM.CONFIG_FILE = str(bcfg)
    WM.BACKUP_FILE = str(bcfg) + ".bak"
    bcfg.write_text(json.dumps({
        "accounts": [{"id": "acc1", "name": "주계좌", "color": "#89b4fa"},
                     {"id": "acc2", "name": "연금", "color": "#a6e3a1"}],
        "selected_account": "ALL",
        "stocks": [
            {"code": "005930", "uid": "b1", "account_id": "acc1", "name": "삼성전자",
             "avg_price": 70000, "quantity": 10, "pos": [100, 100]},
            {"code": "000660", "uid": "b2", "account_id": "acc2", "name": "SK하이닉스",
             "avg_price": 100000, "quantity": 2, "pos": [300, 100]},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    for _n in ("fetch_stock", "fetch_us_stock", "fetch_minute_chart",
               "fetch_daily_chart", "fetch_us_minute_chart", "fetch_us_daily_chart"):
        setattr(WFW, _n, lambda _c: None)
    WM.fetch_usd_krw_rate = lambda: None

    bmgr = WM.WidgetManager(app)
    for _w in bmgr.widgets.values():
        _w.show()

    def _bar(uid):
        b = bmgr.widgets[uid].account_bar
        return b._color, b.isVisible()

    assert _bar("b1") == ("#89b4fa", True), _bar("b1")
    assert _bar("b2") == ("#a6e3a1", True), _bar("b2")

    # 특정 계좌를 보는 중이면 감춘다
    bmgr._on_account_filter_changed("acc1")
    assert _bar("b1") == ("", False), _bar("b1")
    bmgr._on_account_filter_changed(storage.ACCOUNT_FILTER_ALL)
    assert _bar("b1") == ("#89b4fa", True), _bar("b1")

    # 프리/애프터 표시로 카드가 높아지면 막대도 같이 길어진다
    wb = bmgr.widgets["b1"]
    wb._set_compact_height(wb.EXTENDED_COMPACT_H)
    assert wb.account_bar.height() == wb.EXTENDED_COMPACT_H - WFW._AccountBar.V_INSET * 2,         wb.account_bar.height()
    wb._set_compact_height(wb.COMPACT_H)

    # 계좌가 1개뿐이면 색을 쓰지 않는다
    bmgr.accounts = [{"id": "acc1", "name": "주계좌", "color": "#89b4fa"}]
    for _s in bmgr.stocks:
        _s["account_id"] = "acc1"
    bmgr._sync_account_bars()
    assert all(_bar(u) == ("", False) for u in ("b1", "b2")), "계좌 1개인데 색이 남았다"

    bmgr.dispose()
    for _w in list(bmgr.widgets.values()):
        _w.close()
    log("[ok] 22. 위젯 계좌색 막대 — 전체 보기에서만 / 카드 높이 따라감 / 계좌 1개면 숨김")

    # ── 23. Excel — 계좌별 시트 내보내기/가져오기 ──────────────────────────
    # 계좌를 나눠 쓰기 전에는 1번 시트 하나뿐이었다. 그 상태로 두면 같은 종목을 두
    # 계좌가 보유할 때 같은 코드가 두 행으로 나가고, 다시 가져올 때 '중복' 으로
    # 거부돼 백업/복원이 통째로 깨진다.
    from openpyxl import Workbook, load_workbook
    from pinstock.core.storage import (
        export_stocks_to_excel, import_stocks_from_excel, reconcile_imported_accounts,
    )

    xdir = tmpdir / "excel"
    xdir.mkdir(exist_ok=True)
    accs2 = [{"id": "acc1", "name": "주계좌", "color": "#89b4fa"},
             {"id": "acc2", "name": "연금", "color": "#a6e3a1"}]
    xstocks = [
        {"code": "005930", "uid": "x1", "account_id": "acc1", "name": "삼성전자",
         "avg_price": 70000, "quantity": 10, "market": "KR", "currency": "KRW"},
        {"code": "005930", "uid": "x2", "account_id": "acc2", "name": "삼성전자",
         "avg_price": 60000, "quantity": 5, "market": "KR", "currency": "KRW"},
        {"code": "NVDA", "uid": "x3", "account_id": "acc2", "name": "NVIDIA",
         "avg_price": 100.0, "quantity": 2, "market": "US", "currency": "USD",
         "buy_exchange_rate": 1300.0},
    ]
    multi_path = str(xdir / "multi.xlsx")
    export_stocks_to_excel(xstocks, multi_path, {"005930": 80000, "NVDA": 150.0},
                           1400.0, accounts=accs2)

    wb = load_workbook(multi_path)
    assert wb.sheetnames == ["보유종목", "주계좌", "연금"], wb.sheetnames
    main_header = [c.value for c in wb["보유종목"][1]]
    assert "계좌" in main_header, main_header
    # 계좌 시트는 시트 하나가 곧 한 계좌라 계좌 컬럼을 넣지 않는다
    assert "계좌" not in [c.value for c in wb["연금"][1]]
    flat = [str(c.value) for row in wb["보유종목"].iter_rows() for c in row]
    assert "계좌별 요약" in flat, "1번 시트에 계좌별 소계가 없다"
    assert "계좌 정보" in [str(c.value) for row in wb["연금"].iter_rows() for c in row]

    got, got_accs = import_stocks_from_excel(multi_path)
    assert [a["id"] for a in got_accs] == ["acc1", "acc2"], got_accs
    assert [a["color"] for a in got_accs] == ["#89b4fa", "#a6e3a1"], "계좌 색이 안 돌아왔다"
    assert len(got) == 3 and sum(1 for s in got if s["code"] == "005930") == 2,         "같은 종목 2계좌가 라운드트립에서 사라졌다"
    nv = next(s for s in got if s["code"] == "NVDA")
    assert nv["account_id"] == "acc2" and nv["buy_exchange_rate"] == 1300.0

    # 계좌가 1개면 계좌 기능 이전과 완전히 같은 파일이어야 한다
    solo_path = str(xdir / "solo.xlsx")
    export_stocks_to_excel([dict(xstocks[0])], solo_path,
                           accounts=[{"id": "acc1", "name": "기본 계좌", "color": "#89b4fa"}])
    swb = load_workbook(solo_path)
    assert swb.sheetnames == ["보유종목"], swb.sheetnames
    assert "계좌" not in [c.value for c in swb.active[1]]
    solo_stocks, solo_accs = import_stocks_from_excel(solo_path)
    assert solo_accs == [] and solo_stocks[0]["account_id"] == ""

    # 계좌 시트를 지운 파일 — 1번 시트의 계좌 컬럼으로 소속을 되살린다
    wb2 = load_workbook(multi_path)
    for t in ("주계좌", "연금"):
        del wb2[t]
    no_sheets = str(xdir / "no_sheets.xlsx")
    wb2.save(no_sheets)
    ns_stocks, ns_accs = import_stocks_from_excel(no_sheets)
    assert [a["name"] for a in ns_accs] == ["주계좌", "연금"], ns_accs
    ns_name = {a["id"]: a["name"] for a in ns_accs}
    assert sorted(ns_name[s["account_id"]] for s in ns_stocks) == ["연금", "연금", "주계좌"]

    # 계좌 개념이 아예 없던 구버전 파일
    legacy_path = str(xdir / "legacy.xlsx")
    lw = Workbook(); lws = lw.active; lws.title = "보유종목"
    lws.append(["종목코드", "종목명", "평단가", "수량"])
    lws.append(["005930", "삼성전자", 70000, 10])
    lw.save(legacy_path)
    lg_stocks, lg_accs = import_stocks_from_excel(legacy_path)
    assert lg_accs == [] and lg_stocks[0]["account_id"] == ""

    # reconcile — id 매칭 / 이름 매칭 / 새로 만들기 / 계좌 정보 없을 때 기본 계좌
    tgt = [{"code": "A", "account_id": "acc1"}, {"code": "B", "account_id": "zzz"}]
    after, created = reconcile_imported_accounts(
        tgt,
        [{"id": "acc1", "name": "이름바뀜", "color": "#89b4fa"},
         {"id": "zzz", "name": "새계좌", "color": "#f9e2af"}],
        accs2, default_account_id="acc1")
    assert tgt[0]["account_id"] == "acc1", "id 가 같으면 기존 계좌에 붙어야 한다"
    assert [a["id"] for a in created] == ["zzz"], created
    assert len(after) == 3, after

    by_name = [{"code": "A", "account_id": "other"}]
    after2, created2 = reconcile_imported_accounts(
        by_name, [{"id": "other", "name": "연금", "color": "#f9e2af"}],
        accs2, default_account_id="acc1")
    assert by_name[0]["account_id"] == "acc2", "이름이 같으면 그 계좌에 붙어야 한다"
    assert created2 == [] and len(after2) == 2

    no_acc = [{"code": "A", "account_id": ""}]
    after3, created3 = reconcile_imported_accounts(no_acc, [], accs2,
                                                   default_account_id="acc2")
    assert no_acc[0]["account_id"] == "acc2", "계좌 정보가 없으면 기본 계좌로 가야 한다"
    assert created3 == []
    log("[ok] 23. Excel 계좌 시트 — 라운드트립 / 계좌 1개면 이전과 동일 / 구버전 / 계좌 매칭")

    # ── 24. Windows 매니저 — 전 종목 삭제 후 Excel 로 계좌까지 복원 ────────
    # 실제로 보고된 흐름이다: 내보내기 → 보유 종목 전부 삭제 → 그 파일을 가져오기.
    # 계좌 정보가 파일에 없던 시절에는 전부 한 계좌로 뭉쳐 들어왔다.
    ecfg = tmpdir / "excel_stocks.json"
    storage.CONFIG_FILE = str(ecfg)
    storage.PREV_FILE = str(ecfg) + ".prev"
    storage.BACKUP_FILE = str(ecfg) + ".bak"
    WM.CONFIG_FILE = str(ecfg)
    WM.BACKUP_FILE = str(ecfg) + ".bak"
    ecfg.write_text(json.dumps({
        "accounts": [{"id": "acc1", "name": "주계좌", "color": "#89b4fa"},
                     {"id": "acc2", "name": "연금", "color": "#a6e3a1"}],
        "selected_account": "ALL",
        "stocks": [
            {"code": "005930", "uid": "e1", "account_id": "acc1", "name": "삼성전자",
             "avg_price": 70000, "quantity": 10, "pos": [100, 100]},
            {"code": "005930", "uid": "e2", "account_id": "acc2", "name": "삼성전자",
             "avg_price": 60000, "quantity": 5, "pos": [300, 300]},
            {"code": "000660", "uid": "e3", "account_id": "acc2", "name": "SK하이닉스",
             "avg_price": 100000, "quantity": 2, "pos": [500, 100]},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    for _n in ("fetch_stock", "fetch_us_stock", "fetch_minute_chart",
               "fetch_daily_chart", "fetch_us_minute_chart", "fetch_us_daily_chart"):
        setattr(WFW, _n, lambda _c: None)
    WM.fetch_usd_krw_rate = lambda: None

    emgr = WM.WidgetManager(app)
    out_xlsx = str(tmpdir / "roundtrip.xlsx")
    seen_msgs: list = []

    class _FakeFileDialog:
        @staticmethod
        def getSaveFileName(*a, **k):
            return (out_xlsx, "")

        @staticmethod
        def getOpenFileName(*a, **k):
            return (out_xlsx, "")

    class _FakeMsgBox:
        Icon = md.QMessageBox.Icon
        StandardButton = md.QMessageBox.StandardButton

        @staticmethod
        def question(_p, _t, m, *a, **k):
            seen_msgs.append(m)
            return md.QMessageBox.StandardButton.Yes

        @staticmethod
        def information(*a, **k):
            pass

        @staticmethod
        def critical(_p, t, m, *a, **k):
            raise AssertionError(f"{t}: {m}")

        @staticmethod
        def warning(*a, **k):
            pass

    class _FakeImportMode:
        mode = "merge"

        def exec(self):
            return 1

    _orig = (WM.QFileDialog, WM.QMessageBox, WM.ImportModeDialog)
    WM.QFileDialog, WM.QMessageBox, WM.ImportModeDialog = (
        _FakeFileDialog, _FakeMsgBox, _FakeImportMode)
    try:
        emgr.open_export_dialog()
        assert load_workbook(out_xlsx).sheetnames == ["보유종목", "주계좌", "연금"]

        # 연금 계좌의 삼성전자 평단가만 바꿔 '갱신' 으로 잡히는지 확인
        rwb = load_workbook(out_xlsx)
        rws = rwb["연금"]
        for _r in range(2, rws.max_row + 1):
            if rws.cell(row=_r, column=1).value == "005930":
                rws.cell(row=_r, column=3, value=55000)
        rwb.save(out_xlsx)

        emgr._rebuild_widgets([])          # 보유 종목 전부 삭제
        assert emgr.stocks == []
        emgr.open_import_dialog()
    finally:
        WM.QFileDialog, WM.QMessageBox, WM.ImportModeDialog = _orig

    assert "주계좌" in seen_msgs[-1] and "연금" in seen_msgs[-1],         f"확인 메시지가 계좌별로 나뉘지 않았다: {seen_msgs[-1]}"
    assert [a["id"] for a in emgr.accounts] == ["acc1", "acc2"], "계좌가 중복 생성됐다"
    assert len(emgr.stocks) == 3, emgr.stocks
    assert sum(1 for s in emgr.stocks if s["code"] == "005930") == 2,         "같은 종목 2계좌가 한 계좌로 뭉쳤다"
    moved = next(s for s in emgr.stocks
                 if s["code"] == "005930" and s["account_id"] == "acc2")
    assert moved["avg_price"] == 55000, moved
    emgr.dispose()
    for _w in list(emgr.widgets.values()):
        _w.close()
    log("[ok] 24. Windows Excel 라운드트립 — 전 종목 삭제 후에도 계좌별로 복원")

    # ── 25. 위젯 묶어 옮기기 ───────────────────────────────────────────────
    # 위젯은 각자 top-level 윈도우라 부모 하나 위에서 고무줄 선택을 할 수 없다.
    # 오버레이가 영역만 알려 주고, 묶는 것과 함께 옮기는 것은 매니저가 한다.
    from PyQt6.QtCore import Qt, QRect, QPoint, QPointF

    gcfg = tmpdir / "group_stocks.json"
    storage.CONFIG_FILE = str(gcfg)
    storage.PREV_FILE = str(gcfg) + ".prev"
    storage.BACKUP_FILE = str(gcfg) + ".bak"
    WM.CONFIG_FILE = str(gcfg)
    WM.BACKUP_FILE = str(gcfg) + ".bak"
    gcfg.write_text(json.dumps({
        "accounts": [{"id": "a1", "name": "기본 계좌", "color": "#89b4fa"}],
        "selected_account": "ALL",
        "stocks": [
            {"code": "005930", "uid": "g1", "account_id": "a1", "name": "삼성전자",
             "avg_price": 70000, "quantity": 10, "pos": [100, 100]},
            {"code": "000660", "uid": "g2", "account_id": "a1", "name": "SK하이닉스",
             "avg_price": 100000, "quantity": 2, "pos": [100, 170]},
            {"code": "035420", "uid": "g3", "account_id": "a1", "name": "NAVER",
             "avg_price": 200000, "quantity": 1, "pos": [600, 400]},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    for _n in ("fetch_stock", "fetch_us_stock", "fetch_minute_chart",
               "fetch_daily_chart", "fetch_us_minute_chart", "fetch_us_daily_chart"):
        setattr(WFW, _n, lambda _c: None)
    WM.fetch_usd_krw_rate = lambda: None

    gmgr = WM.WidgetManager(app)
    for _w in gmgr.widgets.values():
        _w.show()

    # 영역에 걸친 위젯만 묶인다 (완전히 감싸지 않아도 잡힌다)
    gmgr._on_region_selected(QRect(80, 80, 260, 160))
    assert sorted(gmgr._selected_uids) == ["g1", "g2"], sorted(gmgr._selected_uids)
    assert gmgr.widgets["g1"].is_selected and not gmgr.widgets["g3"].is_selected

    pos_before = {u: (w.pos().x(), w.pos().y()) for u, w in gmgr.widgets.items()}
    gmgr._on_widget_dragged("g1", QPoint(40, 25))
    pos_after = {u: (w.pos().x(), w.pos().y()) for u, w in gmgr.widgets.items()}
    assert pos_after["g2"] == (pos_before["g2"][0] + 40, pos_before["g2"][1] + 25),         "묶인 위젯이 같은 이동량만큼 따라오지 않았다"
    assert pos_after["g3"] == pos_before["g3"], "묶이지 않은 위젯이 움직였다"
    # 드래그 대상은 위젯이 스스로 움직인다 — 매니저가 또 옮기면 이동량이 두 배가 된다
    assert pos_after["g1"] == pos_before["g1"]

    # 드래그가 끝나면 바로 저장한다 (여러 개를 한꺼번에 옮긴 뒤라 유실이 아깝다)
    gmgr.widgets["g1"].move(pos_before["g1"][0] + 40, pos_before["g1"][1] + 25)
    gmgr._on_widget_drag_finished("g1")
    gsaved, _ = storage.read_config()
    gpos = {s["uid"]: s["pos"] for s in gsaved["stocks"]}
    assert gpos["g2"] == [pos_before["g2"][0] + 40, pos_before["g2"][1] + 25], gpos

    # 묶음 밖 위젯을 누르면 풀린다 — 판단은 앱 전역 이벤트 필터가 한다.
    # (위젯별 시그널로는 자식 라벨·차트로 가는 클릭을 흘리는 경로가 있었다)
    from PyQt6.QtGui import QMouseEvent, QKeyEvent

    def _press(widget):
        pos = QPointF(widget.width() / 2, widget.height() / 2)
        return QMouseEvent(
            QMouseEvent.Type.MouseButtonPress, pos,
            QPointF(widget.mapToGlobal(pos.toPoint())),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier)

    # 묶인 위젯 위를 누르면 유지된다 — 여기서 풀리면 끌려고 누르는 순간 묶음이
    # 날아가 그룹 이동 자체가 동작하지 않는다.
    app.sendEvent(gmgr.widgets["g1"], _press(gmgr.widgets["g1"]))
    assert gmgr._selected_uids, "묶인 위젯을 눌렀는데 풀렸다"
    # 같은 누름이 자식·네이티브 창으로도 전달된다. 어느 객체가 받든 판단이 같아야 한다.
    app.sendEvent(gmgr.widgets["g1"].name_lbl, _press(gmgr.widgets["g1"]))
    assert gmgr._selected_uids, "자식으로 전달된 누름에서 묶음이 풀렸다"
    app.sendEvent(gmgr.widgets["g1"].window(), _press(gmgr.widgets["g1"]))
    assert gmgr._selected_uids, "창 객체로 전달된 누름에서 묶음이 풀렸다"
    # 묶음 밖 위젯을 누르면 풀린다 (자식으로 가는 클릭도 마찬가지)
    app.sendEvent(gmgr.widgets["g3"].name_lbl, _press(gmgr.widgets["g3"]))
    assert not gmgr._selected_uids and not gmgr.widgets["g1"].is_selected,         "묶음 밖 위젯을 눌렀는데 안 풀렸다"

    # Esc 로도 풀린다
    gmgr._on_region_selected(QRect(80, 80, 900, 900))
    assert gmgr._selected_uids
    app.sendEvent(gmgr.widgets["g1"], QKeyEvent(
        QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert not gmgr._selected_uids, "Esc 로 묶음이 풀리지 않았다"

    # 영역에 아무것도 없으면 안내만 하고 묶지 않는다
    gmsgs: list = []
    _orig_info = WM.QMessageBox.information
    WM.QMessageBox.information = staticmethod(
        lambda _p, _t, m, *a, **k: gmsgs.append(m))
    try:
        gmgr._on_region_selected(QRect(4000, 4000, 50, 50))
    finally:
        WM.QMessageBox.information = _orig_info
    assert not gmgr._selected_uids and gmsgs, gmsgs

    # 자동 배치(위치 초기화)가 돌면 묶음은 의미가 없어지므로 푼다
    gmgr._on_region_selected(QRect(80, 80, 900, 900))
    assert gmgr._selected_uids
    gmgr.reset_positions()
    assert not gmgr._selected_uids, "자동 배치 뒤에도 묶음이 남았다"

    gmgr.dispose()
    for _w in list(gmgr.widgets.values()):
        _w.close()
    log("[ok] 25. 위젯 묶어 옮기기 — 영역 선택 / 같은 이동량으로 따라옴 / 해제 조건")

    # ── 26. 종목 관리 — 체크박스로 여러 종목 선택 = 삭제 전용 ────────────────
    # 여러 종목을 골라 놓고 '수정'이나 인라인 편집이 열려 있으면 어느 보유분에
    # 적용되는지 정할 수 없다. 그래서 2개 이상 체크한 동안에는 삭제만 남긴다.
    EDIT_ON = (md.QAbstractItemView.EditTrigger.DoubleClicked
               | md.QAbstractItemView.EditTrigger.EditKeyPressed
               | md.QAbstractItemView.EditTrigger.AnyKeyPressed)
    NO_EDIT = md.QAbstractItemView.EditTrigger.NoEditTriggers

    ms = md.ManageStocksDialog([dict(s) for s in mixed])   # acc1·acc2·acc3 각 1종목
    assert len(ms._check_boxes) == ms.table.rowCount() == 3, len(ms._check_boxes)
    assert ms.table.cellWidget(0, ms.COL_CHECK) is not None, "행 체크박스가 없다"
    assert ms.add_btn.isEnabled() and ms.edit_btn.isEnabled() and ms.sort_btn.isEnabled()
    assert ms.table.editTriggers() == EDIT_ON, "체크가 없는데 인라인 편집이 잠겼다"

    ms._check_boxes[0].setChecked(True)                    # 1개 = 평소대로
    assert ms.edit_btn.isEnabled() and ms.table.editTriggers() == EDIT_ON
    ms._check_boxes[2].setChecked(True)                    # 2개 = 삭제만
    assert not ms.add_btn.isEnabled(),  "여러 개 선택인데 추가가 열려 있다"
    assert not ms.edit_btn.isEnabled(), "여러 개 선택인데 수정이 열려 있다"
    assert not ms.sort_btn.isEnabled(), "여러 개 선택인데 정렬이 열려 있다"
    assert ms.del_btn.isEnabled(),      "삭제까지 잠겼다"
    assert ms.table.editTriggers() == NO_EDIT, "여러 개 선택인데 인라인 편집이 열려 있다"
    assert ms.table.dragDropMode() != INTERNAL, "체크 중엔 순서 드래그를 잠가야 한다"

    # 버튼만 잠그면 더블클릭/키보드로 새어 들어간다 — 코드 경로도 막혔는지 확인
    _opened: list = []
    md.StockDialog.exec = lambda self: _opened.append(1) or 0
    _q, _i = md.QMessageBox.question, md.QMessageBox.information
    md.QMessageBox.question = classmethod(lambda cls, *a, **k: md.QMessageBox.StandardButton.Yes)
    md.QMessageBox.information = classmethod(lambda cls, *a, **k: None)
    try:
        ms._edit_selected()
        assert not _opened, "여러 개 선택인데 수정 창이 떴다"

        # 헤더 체크박스 = 전체 선택 / 해제
        ms._header_check.setChecked(True)
        assert ms._checked_stock_indexes() == [0, 1, 2], ms._checked_stock_indexes()
        ms._header_check.setChecked(False)
        assert ms._checked_count() == 0

        # 시장 필터가 걸려도 '표의 행'이 아니라 '종목'을 지운다
        ms._set_market_filter(md.MARKET_US)                # mixed 중 NVDA 1건
        assert [ms.table.item(r, ms.COL_CODE).text()
                for r in range(ms.table.rowCount())] == ["NVDA"]
        ms._check_boxes[0].setChecked(True)
        ms._delete_selected()
        assert "NVDA" not in [s["code"] for s in ms.get_stocks()], \
            [s["code"] for s in ms.get_stocks()]
        assert len(ms.get_stocks()) == 2

        # 삭제하고 나면 체크가 풀리고 버튼도 되돌아온다
        assert ms._checked_count() == 0 and not ms._header_check.isChecked()
        assert ms.add_btn.isEnabled() and ms.table.editTriggers() == EDIT_ON

        # 체크가 하나도 없으면 예전처럼 '선택한 행' 하나만 지운다
        ms._set_market_filter("ALL")
        before = [s["uid"] for s in ms.get_stocks()]
        ms.table.setCurrentCell(0, ms.COL_NAME)            # selectRow 는 current 를 안 옮긴다
        ms._delete_selected()
        assert [s["uid"] for s in ms.get_stocks()] == before[1:], \
            "체크가 없을 때 선택한 행 하나만 지워지지 않았다"
    finally:
        del md.StockDialog.exec
        md.QMessageBox.question, md.QMessageBox.information = _q, _i
    ms.deleteLater()
    log("[ok] 26. 종목 관리 — 체크 여러 개면 삭제만 / 체크 삭제 / 체크 없으면 선택 행")

    shutil.rmtree(tmpdir, ignore_errors=True)
    log("\n[PASS] 26개 케이스 전부 통과")


def main() -> int:
    with open(_LOG_PATH, "w", encoding="utf-8") as log_fp:
        try:
            _run(log_fp)
        except Exception:
            log_fp.write("\n[FAIL]\n" + traceback.format_exc())
            log_fp.flush()
            print(_LOG_PATH.read_text(encoding="utf-8"))
            return 1
    print(_LOG_PATH.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
