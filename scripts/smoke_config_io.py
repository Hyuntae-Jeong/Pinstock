"""Smoke test — stocks.json 원자적 저장 / 손상 복구 / 저장 가드.

2026-08-12 데이터 유실 사고의 회귀 방지용. 당시 사슬은 이랬다:

  1. 저장이 `open(CONFIG_FILE, "w")` 라 여는 즉시 파일이 0바이트가 됨
  2. 윈도우 업데이트 강제 종료가 truncate 와 flush 사이를 끊음 → 파일 손상
  3. 다음 실행에서 로드가 `except: return` 으로 조용히 실패 → 종목 0개로 시작
  4. 시작 5초 뒤 자동 업데이트 체크의 저장이 그 빈 상태를 원본에 확정 → 영구 유실

아래 7개 케이스가 각 고리를 하나씩 막는지 확인한다.
"""

import os
import sys
import json
import shutil
import tempfile
import traceback
from pathlib import Path
from datetime import date, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 한국어 Windows 기본 콘솔은 cp949 라 로그를 그대로 print 하면 깨진다.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

_LOG_PATH = _REPO_ROOT / "smoke_config_io.log"

SAMPLE = {
    "stocks": [
        {"code": "379810", "name": "KODEX 미국나스닥100", "avg_price": 29479, "quantity": 335},
        {"code": "000660", "name": "SK하이닉스", "avg_price": 2148000, "quantity": 1},
    ],
    "watchlist": [],
    "master": {"visible": True, "pos": [60, 20]},
}


def _run(log_fp):
    def log(msg: str) -> None:
        log_fp.write(msg + "\n")
        log_fp.flush()

    from pinstock.core import storage

    tmpdir = Path(tempfile.mkdtemp(prefix="pinstock-cfgtest-"))
    cfg = tmpdir / "stocks.json"

    # 모듈 전역을 임시 경로로 갈아끼운다. write_config_atomic/read_config 는
    # 호출 시점에 전역을 읽으므로 이 패치만으로 충분하다.
    storage.CONFIG_FILE = str(cfg)
    storage.PREV_FILE = str(cfg) + ".prev"
    storage.BACKUP_FILE = str(cfg) + ".bak"
    storage._config_dir = lambda: tmpdir

    def leftovers():
        return [p.name for p in tmpdir.glob("*.tmp*")]

    # ── 1. 최초 실행 — 파일 없음은 '실패'가 아니다 ──────────────────────────
    data, warn = storage.read_config()
    assert data is None and warn is None, f"최초 실행 기대 (None, None), 실제 {(data, warn)}"
    log("[ok] 1. 파일 없음 → (None, None), 예외 없음")

    # ── 2. 정상 저장 → 로드 round-trip ─────────────────────────────────────
    storage.write_config_atomic(SAMPLE)
    data, warn = storage.read_config()
    assert warn is None, f"경고가 없어야 하는데 {warn!r}"
    assert data == SAMPLE, "round-trip 내용 불일치"
    assert not leftovers(), f"임시파일 잔여물: {leftovers()}"
    log("[ok] 2. 저장→로드 round-trip 일치, 임시파일 잔여물 없음")

    # ── 3. 저장 중 실패해도 원본은 온전 (원자성의 핵심) ─────────────────────
    # json.dump 는 직렬화 불가 값을 만나기 전까지 이미 파일에 쓴 뒤 예외를 던진다.
    # 옛 코드('w' 로 원본 직접 열기)였다면 여기서 원본이 반쪽이 된다.
    before = cfg.read_bytes()
    try:
        storage.write_config_atomic({"stocks": [1, 2, 3], "bad": object()})
        raise AssertionError("직렬화 실패인데 예외가 안 났다")
    except TypeError:
        pass
    assert cfg.read_bytes() == before, "저장 실패인데 원본이 변경됐다"
    assert json.loads(cfg.read_text(encoding="utf-8")) == SAMPLE, "원본이 파싱 불가 상태"
    assert not leftovers(), f"실패 후 임시파일이 남았다: {leftovers()}"
    log("[ok] 3. 저장 중 예외 → 원본 온전 + 임시파일 정리됨")

    # ── 4. 본 파일 손상 + .prev 있음 → 폴백 복구 ────────────────────────────
    assert Path(storage.PREV_FILE).is_file(), ".prev 가 만들어지지 않았다"
    cfg.write_text('{"stocks": [{"code": "3798', encoding="utf-8")   # 반쪽 JSON
    data, warn = storage.read_config()
    assert data == SAMPLE, ".prev 폴백 내용이 다르다"
    assert warn and "손상" in warn, f"복구 안내문이 비어 있다: {warn!r}"
    log("[ok] 4. 본 파일 손상 → .prev 폴백 성공 + 안내문 생성")

    # ── 5. 본 파일 0바이트 + .prev 없음 → ConfigLoadError ───────────────────
    Path(storage.PREV_FILE).unlink()
    cfg.write_text("", encoding="utf-8")        # 사고 당시와 같은 0바이트 상태
    try:
        storage.read_config()
        raise AssertionError("ConfigLoadError 가 발생해야 한다")
    except storage.ConfigLoadError:
        pass
    log("[ok] 5. 손상 + .prev 없음 → ConfigLoadError 발생")

    # ── 6. 저장 가드 — 로드 실패 세션은 파일을 건드리지 않는다 ──────────────
    # 실제 WidgetManager._save_config 을 가짜 self 로 호출한다 (Qt 인스턴스화 없이).
    from types import SimpleNamespace
    from pinstock.ui_windows.manager import WidgetManager

    cfg.write_text("BROKEN-DO-NOT-TOUCH", encoding="utf-8")
    before = cfg.read_bytes()

    blocked = SimpleNamespace(config_load_failed=True)
    WidgetManager._save_config(blocked)          # 가드에 막혀 즉시 반환해야 함
    assert cfg.read_bytes() == before, "로드 실패 세션인데 파일이 덮어써졌다"
    log("[ok] 6a. config_load_failed=True → 저장 거부, 손상 파일 보존")

    # 같은 함수가 플래그만 내리면 정상 저장하는지 (가드가 과하지 않은지)
    allowed = SimpleNamespace(
        config_load_failed=False,
        stocks=[dict(s) for s in SAMPLE["stocks"]],
        watchlist=[], watch_tags=[], watch_ma={"ma5": True}, watch_group_state={},
        master_visible=True, master_pos=[60, 20], assets_hidden=False,
        watch_visible=True, us_return_basis="krw", popover_opacity=1.0,
        memo={"text": "", "updated_at": None}, stock_memos={},
        update_last_check_date=None, update_skipped_version=None,
        _snapshot_watch_group_state=lambda: None,
    )
    WidgetManager._save_config(allowed)
    saved, warn = storage.read_config()
    assert [s["code"] for s in saved["stocks"]] == ["379810", "000660"], "정상 저장 실패"
    log("[ok] 6b. config_load_failed=False → 정상 저장됨 (가드가 과하지 않음)")

    # ── 7. 일일 세대 백업 — 최근 7개만 유지 ────────────────────────────────
    for i in range(1, 11):                       # 오래된 가짜 백업 10개
        d = (date.today() - timedelta(days=i)).isoformat()
        (tmpdir / f"stocks.{d}.json").write_text("{}", encoding="utf-8")
    storage.rotate_daily_backup()
    kept = sorted(p.name for p in tmpdir.glob("stocks.20*.json"))
    assert len(kept) == storage.DAILY_BACKUP_KEEP, f"보관 개수 {len(kept)} != 7: {kept}"
    assert f"stocks.{date.today().isoformat()}.json" in kept, "오늘 백업이 없다"
    assert cfg.exists() and json.loads(cfg.read_text(encoding="utf-8")), "원본이 훼손됐다"
    log(f"[ok] 7. 일일 백업 7개 유지 + 오늘치 생성: {kept}")

    shutil.rmtree(tmpdir, ignore_errors=True)
    log("\n[PASS] 7개 케이스 전부 통과")


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
