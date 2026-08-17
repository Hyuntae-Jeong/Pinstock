"""Smoke test — 계좌 레지스트리 / 보유 uid / 계좌 필터 / 팝오버 행 매핑.

여러 계좌 기능(#34)의 회귀 방지용. 지켜야 할 불변식은 셋이다:

  1. 계좌는 항상 1개 이상 — 계좌가 0개면 종목을 둘 곳이 없다
  2. 모든 보유 종목의 account_id 는 실재하는 계좌를 가리킨다
  3. 보유 항목의 uid 는 전역 유일 — 여기가 깨지면 "계좌1 삼성전자"와
     "계좌2 삼성전자"가 같은 행으로 뭉개지고 편집/삭제가 엉뚱한 쪽을 건드린다

구버전(계좌 개념 없음) stocks.json 의 마이그레이션, macOS 매니저의 실제 저장
경로, 그리고 같은 종목을 두 계좌가 보유할 때 팝오버가 행 2개를 만들고 한 번 들어온
시세를 양쪽에 뿌리는지까지 확인한다.
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

    shutil.rmtree(tmpdir, ignore_errors=True)
    log("\n[PASS] 13개 케이스 전부 통과")


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
