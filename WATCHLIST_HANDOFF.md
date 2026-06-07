# 관심종목(워치리스트) #29 — Windows 작업 핸드오프

> macOS 쪽(저장계층 + 팝오버 관심 뷰 + 일봉 폴러)은 완료. **Windows = 플로팅 위젯 부분만 남음(Step 3).**
> 이 문서를 Windows 환경의 Claude 에게 그대로 전달하면 됨.

## 0. 시작 전 (필수)
- 브랜치 **`#29-watchlist`** 를 pull 해서 그 위에서 이어 작업. (브랜치명에 `#` 있으니 셸에서 따옴표 필요: `git checkout '#29-watchlist'`)
- 커밋 규칙: 제목/본문 초안 먼저 보여주고 승인 대기. 본문은 산문 말고 대시(`-`) 불릿. 끝에 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- push/머지/브랜치삭제는 **사용자 승인 먼저**.
- 단계별로 작은 커밋(macOS 도 Step 단위로 쪼개 커밋했음).

## 1. 확정 스펙 (사용자와 합의됨 — 바꾸지 말 것)
- 보유 ↔ 관심 **완전 독립** 별도 저장. 같은 종목이 보유·관심에 동시 존재 가능. **전환 기능 없음**.
- 관심 = **바탕화면 플로팅 위젯**(Windows), **간소 전용**: 종목명 + 일봉 현재가 + 전일대비% + 미니 일봉 캔들. **손익/평단가/수량 없음**.
- 관심 ON/OFF = 위젯 표시/숨김 (`hidden` 플래그).
- 시세 = **일봉 기준**, 폴링 **느리게(60초)**. 분봉 안 씀.
- 추가/관리 = 메뉴 "관심종목 추가" / "관심종목 관리" **별도 항목**(보유 메뉴와 구분선으로 분리) + 전용 다이얼로그(이미 공용 구현됨).
- **위치 초기화: 관심 위젯은 별도 동작, 화면 좌상단부터** column-wrap(오른쪽으로 확장). 보유는 우상단(그대로 둘 것).
- 태그(사용자 추가 + 색상 자유)는 **Phase 2** — 이번 범위 아님. (관심 항목에 `tags: []` 만 보존됨.)
- 이번 플랫폼: **Windows 만**.

## 2. 이미 끝난 것 (공용 — 재사용만, 다시 만들지 말 것)
- `pinstock/core/storage.py`: `normalize_watch_item` / `normalize_watchlist_schema`. 설정 v2 에 `watchlist` 최상위 키.
- `pinstock/ui_windows/manager.py`: `self.watchlist` **이미 load/save 됨**(Step 1, `_load_config`/`_save_config`).
- `pinstock/ui_windows/manage_dialog.py` (공용):
  - `StockDialog(watch_mode=True)` — 평단가/수량 행 접힘, `get_data()` 는 `{code, market, currency, tags}` 만 반환.
  - `ManageWatchlistDialog(watchlist=[...])` — 종목명/코드/시장 표 + 표시(ON/OFF) 토글 + 추가/삭제, `get_watchlist()` 반환.
  → Windows 는 이 둘을 **그대로** 쓰면 됨. (단 매니저에서 `ManageWatchlistDialog` import 추가 필요 — 현재 import 줄에 없음.)

## 3. Windows 에서 할 일 (Step 3)
구조 차이: **macOS=팝오버 행 / Windows=바탕화면 플로팅 위젯**. macOS 로직을 위젯 모델로 옮기는 것.

### 3-1. 메뉴 + 핸들러 — `ui_windows/manager.py`
- 트레이 메뉴(`_build_menu` 부근)에 "관심종목 추가" / "관심종목 관리" 추가. 보유 항목과 **구분선으로 분리**.
- `open_add_watch_dialog`: `StockDialog(watch_mode=True)` → `get_data()` → `fetch_quote_for_stock(d)` 로 이름 → 중복검사(**watchlist 안에서만**) → `self.watchlist.append(d)` → `_save_config()` → 위젯 spawn.
- `open_manage_watch_dialog`: `ManageWatchlistDialog(deepcopy(self.watchlist))` → `get_watchlist()` → old/new diff 로 위젯 추가/삭제 → `_save_config()`.
- **미러 대상**: `ui_macos/manager.py` 의 `open_add_watch_dialog` / `open_manage_watch_dialog` 가 거의 그대로의 로직.

### 3-2. 관심 전용 위젯 — `ui_windows/floating_widget.py` (또는 새 파일)
- 보유 `StockWidget` 참고하되 **간소 버전**(새 `WatchWidget` 권장):
  - 표시: 종목명 + 현재가 + 전일대비% + 미니 일봉 스파크라인. **손익/평단가/수량/확장 없음**.
  - **자체 폴링 일봉만, 60초.** (Windows 는 위젯이 직접 `_fetch_price`/`_fetch_chart` 함 → 관심 위젯도 직접 일봉 폴링. macOS 의 `WatchFetcher` 60초 로직 참고.)
  - `pos` 저장/복원, `hidden`(표시 토글)로 show/hide.
- 매니저에 `self.watch_widgets: dict[str, WatchWidget]` + spawn/kill (보유 `self.widgets` 패턴).

### 3-3. 위치 초기화 (좌상단) — `ui_windows/manager.py`
- 보유 `reset_positions()` = **우상단 → 왼쪽** wrap (그대로 둘 것). 내부 `_place_widgets_in_columns` 사용.
- 관심 전용 `reset_watch_positions()` 신설: **좌상단 → 오른쪽** wrap. (시작 x = `geo.x() + MARGIN`, 컬럼은 오른쪽으로 진행. `_place_widgets_in_columns` 를 좌→우 버전으로 일반화하거나 별도 배치.)
- 메뉴에 "관심 위치 초기화" 별도 항목.

### 3-4. 저장/복원 — `ui_windows/manager.py`
- 관심 위젯 위치도 `save_positions()` / `_save_config()` 시 watchlist 항목의 `pos` 에 저장(보유 패턴 동일).
- 시작 시 `watchlist` 로 위젯 spawn(저장된 pos 사용, 없으면 좌상단 기본 배치).

## 4. 검증
- dev: `python -m pinstock` (Windows). 헤드리스 단위검증은 `QT_QPA_PLATFORM=offscreen` 로 위젯/다이얼로그 구성 확인 가능.
- 체크: 추가→위젯 뜸 / 관리 삭제→위젯 사라짐 / 표시 토글 / **좌상단 위치 초기화** / 재시작 후 유지 / **보유와 독립**(같은 종목이면 위젯 2개).

## 5. 참고할 macOS 구현 (미러 대상)
- `ui_macos/manager.py`: `WatchFetcher`(일봉 60초), `_spawn_watch_fetcher`/`_kill_watch_fetcher`, `_on_watch_price_updated`/`_on_watch_daily_updated`, `open_add_watch_dialog`, `open_manage_watch_dialog`, 메뉴 구분선.
- `ui_macos/popover.py`: `WatchRow`(표시 형식·일봉 적용 참고).

## 6. 현재 커밋 (이 브랜치)
```
03ba932 feat(watchlist): 관심종목 일봉 폴러 — 팝오버 관심 행에 실제 시세 (2b-2)
9aeae7c feat(watchlist): macOS 팝오버 보유/관심 뷰 토글 + 관심 행 (2b-1)
6dadccb feat(watchlist): 관심종목 관리 다이얼로그 + macOS 메뉴 (2a-2)
02bc03e feat(watchlist): 관심종목 추가 다이얼로그 + macOS 메뉴 (2a-1)
ec5fc93 feat(watchlist): 관심종목 저장 계층 추가 (보유와 독립)
```
