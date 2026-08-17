# 여러 계좌 기능 (#34) — Windows UI 인수인계

브랜치: `#34-multi-account`
맥(macOS) 쪽 구현은 끝났고, **Windows UI 만 남았다.** 이 문서는 그 작업을 이어받는
사람이 알아야 할 계약·결정·함정을 정리한 것이다.

> 이 문서는 작업 인수인계용이다. PR 을 머지하기 전에 지워도 된다.

---

## 1. 이 기능이 하려는 것

증권 계좌(주계좌/연금/ISA 등)를 나눠서 종목을 관리한다.

- 계좌는 **전체 또는 1개**만 선택한다. 여러 계좌를 동시에 켜는 조합은 없다 —
  상단 요약 숫자가 무엇의 합인지 흐려진다.
- **같은 종목을 여러 계좌가 각자 다른 평단가·수량으로** 보유할 수 있다.
  이게 이 기능의 핵심이고, 아래 2번의 uid 가 필요한 이유다.
- **관심종목은 계좌를 타지 않는다.** 전 계좌가 하나의 목록을 공유한다.

---

## 2. 데이터 모델 계약 (OS 공용, 이미 구현됨)

`pinstock/core/storage.py` 에 전부 들어 있다. Windows 는 **그대로 쓰면 된다.**

### stocks.json 에 추가된 것

```jsonc
{
  "accounts": [                                  // 계좌 레지스트리 (항상 1개 이상)
    {"id": "a1b2c3d4", "name": "주계좌", "color": "#89b4fa"}
  ],
  "selected_account": "ALL",                     // "ALL" 또는 계좌 id
  "stocks": [
    {
      "code": "005930",
      "uid": "f4732cc541ec",                     // 보유 항목 고유 키 (신규)
      "account_id": "a1b2c3d4",                  // 소속 계좌 (신규)
      "avg_price": 70000, "quantity": 10
    }
  ],
  "detached": { "account_filter": "ALL" }        // 분리 창의 독립 계좌 필터 (macOS 전용)
}
```

### 반드시 지켜야 할 불변식 3가지

1. **계좌는 항상 1개 이상** — 0개면 종목을 둘 곳이 없다
2. **모든 보유 종목의 `account_id` 는 실재하는 계좌를 가리킨다**
3. **`uid` 는 전역 유일**

셋 다 `ensure_accounts(stocks, accounts)` 가 보장한다. **idempotent** 라 로드/저장
양쪽에서 몇 번을 불러도 계좌 id 나 uid 가 갈리지 않는다.

### 쓸 수 있는 함수

| 함수 | 용도 |
|---|---|
| `ensure_accounts(stocks, accounts) -> list` | 불변식 재적용. stocks 는 제자리 수정, 계좌 목록을 반환 |
| `normalize_accounts(list) -> list` | 무효/중복 id 제거, 이름 6자 절단, 색 폴백 |
| `normalize_account_filter(value, accounts) -> str` | 삭제된 계좌를 가리키면 `"ALL"` 로 폴백 |
| `stock_in_account(stock, account_filter) -> bool` | 계좌 필터 통과 여부 (`"ALL"` 이면 전부 통과) |
| `account_map(accounts) -> dict` | `{id: account}` 조회용 |
| `new_account_id()` / `new_holding_uid()` | id 발급 |
| `ACCOUNT_FILTER_ALL`, `ACCOUNT_NAME_MAX`(=6), `DEFAULT_ACCOUNT_COLOR` | 상수 |

### 구버전 파일 마이그레이션

계좌 개념이 없던 stocks.json 은 `ensure_accounts` 가 **"기본 계좌" 하나를 만들어
전 종목을 배정**한다. 별도 코드 불필요.

---

## 3. 가장 중요한 것 — 정체성이 `code` 에서 `uid` 로 바뀌었다

이 기능의 실제 작업량 대부분이 여기다.

기존 코드는 **"종목의 정체성 = code"** 를 전 계층에서 가정했다. 계좌 A·B 가 같은
종목을 보유하는 순간 dict 키가 충돌해 **위젯 하나가 사라지고, 편집/삭제가 어느
보유분을 건드릴지 결정할 수 없다.**

### 두 축을 분리해서 생각할 것

| 축 | 키 | 이유 |
|---|---|---|
| **보유 항목** — 위젯, 편집/삭제/추가매수, 위젯 위치 | `uid` | 계좌마다 다른 보유분 |
| **시세** — 폴러, `current_prices`, 차트 캐시 | `code` | 시장 데이터라 계좌와 무관 |
| **종목 메모** — `stock_memos` | `code` | 메모는 보유분이 아니라 종목에 붙는다 |

시세를 code 로 두는 게 중요하다. **같은 종목을 두 계좌가 보유해도 폴러는 1개만
돈다** — API 호출이 늘지 않는다. 대신 한 번 받은 시세를 `code → 위젯 목록`
역인덱스로 여러 위젯에 뿌려야 한다.

### macOS 에서 실제로 한 것 (참고 구현)

`pinstock/ui_macos/popover.py`, `pinstock/ui_macos/manager.py` 의
커밋 `74bf582` 를 보면 된다.

```python
# popover.py — 행은 uid 키, 시세 분배용 역인덱스를 따로 둔다
self.rows: dict[str, StockRow] = {}              # uid → 행
self._rows_by_code: dict[str, list[StockRow]] = {}   # code → 행 목록

def update_stock_price(self, code, result):
    self._price_cache[code] = result
    for row in self._rows_by_code.get(code, ()):   # 같은 종목의 모든 행에
        row.apply_price(result)
```

```python
# manager.py — 폴러는 code 당 1개, 정리는 참조 카운팅으로
def _spawn_fetcher(self, stock, stagger_idx=0):
    code = stock["code"]
    if code in self.fetchers:
        return                      # 다른 계좌가 이미 폴링 중

def _prune_fetchers(self):
    """'삭제한 종목의 폴러를 끈다'가 아니라 '아무도 안 쓰는 폴러를 끈다'."""
    live = {s.get("code") for s in self.stocks}
    for code in [c for c in self.fetchers if c not in live]:
        self._kill_fetcher(code)
        self.current_prices.pop(code, None)

def _holding(self, uid):
    return next((s for s in self.stocks if s.get("uid") == uid), None)
```

---

## 4. 이미 만들어져 있어 그대로 쓰면 되는 UI (`ui_windows/manage_dialog.py`)

Windows 파일에 들어 있지만 **아직 Windows 매니저는 안 쓰고 있다.** 인자만 넘기면
켜진다.

| 클래스 | 설명 |
|---|---|
| `AccountManagerDialog(accounts, stocks)` | 계좌 추가/수정/순서/삭제. `get_accounts()` / `get_stocks()` |
| `AccountEditDialog` | 계좌명(6자)·색상 |
| `AccountPickDialog` | 계좌 삭제 시 이동 대상 선택 |

```python
# 종목 추가/수정 창에 계좌 선택 행이 생긴다 (계좌 2개 이상일 때만)
StockDialog(accounts=self.accounts, default_account=<지금 보고 있는 계좌>)
StockDialog(data=target, accounts=self.accounts)   # 수정 = 계좌 이동 가능

# 종목 관리 표에 계좌 컬럼 + 계좌 필터가 생긴다 (계좌 2개 이상일 때만)
ManageStocksDialog(stocks=..., accounts=self.accounts, account_filter=...)
```

**계좌 삭제 정책** (다이얼로그 안에서 끝난다):
- 종목이 남아 있으면 반드시 물어본다 — `[다른 계좌로 이동] / [종목도 함께 삭제] / [취소]`
- 갈 곳이 하나뿐이면 대상은 묻지 않는다
- 마지막 계좌는 삭제를 막는다

macOS 매니저의 `open_account_dialog()` 가 호출 예시다 — 결과를 받은 뒤
`_reconcile_accounts()` → `_prune_fetchers()` 순으로 뒷정리한다.

---

## 5. 남은 작업 — Windows

### 5-1. `pinstock/ui_windows/manager.py`

| 지점 | 할 일 |
|---|---|
| `_load_config` (580) | `accounts` / `selected_account` 읽기 |
| `_save_config` (686) | `_reconcile_accounts()` 호출 + 두 키 저장 |
| `self.widgets[code] = w` (1005) | **uid 키로 전환 — 핵심** |
| `widgets[s["code"]]` (342, 1567) | 같이 uid 로 |
| `stock_by_code` (167) | 중복 code 면 하나만 남는다 → uid 기준으로 |
| `_spawn_widget` (984) | 위젯 1개 = 보유분 1개. 폴링은 code 당 1개로 분리 |
| `StockDialog()` (1218) | `accounts=` / `default_account=` 추가 |
| `ManageStocksDialog(` (1531) | `accounts=` / `account_filter=` 추가 |
| Excel 병합 `by_code` (1706) | 기존 보유분의 uid 유지 (macOS `open_import_dialog` 참고) |
| 종목 추가 중복 검사 | `(code, account_id)` 조합으로 — 다른 계좌엔 같은 종목 허용 |
| 트레이 메뉴 | "계좌 관리" 항목 추가 |

macOS 매니저에 그대로 대응되는 메서드가 있다:
`_reconcile_accounts`, `_holding`, `_prune_fetchers`, `_default_account_for_add`,
`_sync_accounts_to_windows`, `open_account_dialog`.

### 5-2. `pinstock/ui_windows/master_widget.py`

계좌 필터 UI. **드롭다운 추천** — `FOOTER_H` 가 25px 라 버튼을 늘어놓을 공간이 없다.
(맥 팝오버는 폭 360px 을 가로 스크롤로 풀었지만 여기는 상황이 다르다.)

- 기존 `market_filter_changed` 시그널(238) / `market_filter_buttons`(307) 옆에
  `account_filter_changed` 를 같은 패턴으로 추가
- 계좌가 1개면 감춘다 (맥과 같은 규칙 — 아래 6번)

### 5-3. `pinstock/ui_windows/floating_widget.py`

"전체" 보기일 때 어느 계좌 것인지 구분. **좌측 계좌색 세로 바 추천** —
위젯이 작아 맥처럼 pill 뱃지를 넣을 자리가 없다.

특정 계좌를 보는 중이면 전 위젯이 같은 계좌라 표시하지 않는다.

### 5-4. 공짜로 따라오는 것

위젯 위치 `pos` 가 종목 dict 안에 있어서, **uid 키로 바꾸는 순간
"계좌1 삼성전자"와 "계좌2 삼성전자"가 각자 다른 자리에 뜬다.** 추가 작업 없음.

---

## 6. 확정된 설계 결정 (맥과 일관되게 갈 것)

| 결정 | 이유 |
|---|---|
| 계좌 선택은 전체 or 1개 | 다중 선택이면 요약 숫자가 무엇의 합인지 흐려진다 |
| 전체 보기에서 **합산하지 않고** 계좌별로 각각 표시 | 합산 행은 편집/삭제 시 어느 계좌인지 정할 수 없다. 합계는 요약 카드가 담당 |
| **계좌가 1개면 계좌 UI 를 통째로 감춘다** | `[전체][기본 계좌]` 는 아무 정보도 안 주면서 공간만 먹는다. 계좌를 안 나눠 쓰는 사용자는 화면이 이전과 완전히 동일하다 |
| 계좌 × 시장 필터는 **AND** | 서로 독립된 축 |
| 요약은 **화면에 보이는 것의 합** | 두 필터를 다 통과한 종목만 더한다 |
| 계좌명 6자 제한 | 맥 팝오버 버튼 한 줄(360px) 기준. Windows 도 같은 상한을 쓴다 |
| 계좌 색은 선택 UI 와 행 표시에 같은 색 | 어느 계좌인지 눈으로 이어진다 |
| 환율 폴링은 **필터와 무관하게 전 계좌 기준** | 계좌1 보는 중에 계좌2 에 미국 주식이 있으면 환율은 계속 돌아야 전환 시 즉시 맞다 |
| 삭제된 계좌를 가리키는 필터는 "전체"로 폴백 | 그대로 두면 아무것도 안 보이는 빈 화면이 된다 |

---

## 7. 맥 작업에서 실제로 물린 함정들

1. **`setItemDelegateForColumn(2, ...)` 같은 하드코딩된 컬럼 번호**
   계좌 컬럼이 끼어들면서 delegate 가 조용히 엉뚱한 칸으로 밀렸고, 그 delegate 의
   `+15px` 확장이 계좌 콤보를 옆 칸 위로 덮어 숫자를 가렸다.
   → `ManageStocksDialog.COL_NAME…COL_SHOW` 상수를 쓸 것.

2. **표 셀에 넣는 QComboBox 는 스타일을 따로 줘야 한다**
   셀 위젯은 `QTableWidget::item` 의 padding 만큼 안쪽으로 들어가 높이가 18px 만
   남는데, `DIALOG_STYLE` 의 QComboBox 는 세로 padding 때문에 29px 를 요구해
   찌그러진다. 배경·테두리를 지우고 세로 padding 을 0 으로 준다
   (`_make_account_combo` / 기존 `_make_tag_combo` 참고).

3. **콤보는 `activated` 로 받을 것**
   `currentIndexChanged` 는 표를 다시 그릴 때의 `setCurrentIndex` 로도 울려
   헛된 이동 처리가 생긴다.

4. **`DIALOG_STYLE` 의 `padding: 8px 20px`**
   좁은 버튼(폭 34px 등)에 그대로 걸리면 글자가 밀려 나가 **빈 버튼처럼 보인다.**
   순서 버튼(▲▼)이 여기 걸렸다. 좁은 버튼은 padding 을 따로 줄 것.

5. **종목 삭제 시 폴러를 무조건 끄면 안 된다**
   계좌1 삼성전자를 지워도 계좌2 가 들고 있으면 시세는 여전히 필요하다.
   `_prune_fetchers()` 방식으로.

6. **`_on_price_updated` 의 종목명 동기화에서 `break` 를 빼야 한다**
   같은 code 의 보유분이 여러 개다.

7. **Excel 병합에서 uid 가 갈린다**
   Excel 행마다 새 uid 가 발급되므로, 병합 시 기존 보유분의 uid 를 유지하지 않으면
   같은 보유분이 매번 다른 항목이 돼 **위젯 위치가 초기화된다.**

---

## 8. 검증

```bash
python scripts/smoke_accounts.py
```

16개 케이스가 돈다 (Windows 에서도 그대로 실행된다). 구성:

- 1~9: 스키마·마이그레이션·불변식·필터 폴백·라운드트립 (OS 무관)
- 10~11: macOS 매니저 저장 경로
- 12~13: macOS 팝오버 (행 매핑, 계좌 줄, 뱃지, 필터 AND)
- 14~15: 계좌 관리 다이얼로그 (삭제 3분기, 마지막 계좌 보호), 종목 창 계좌 선택
- 16: 종목 관리 표 (계좌 컬럼, 필터, 드래그 잠금)

**Windows 매니저용 케이스를 같은 방식으로 추가할 것.** 케이스 10 이 참고가 된다 —
Qt 인스턴스화 없이 가짜 `self` 로 `_save_config` 을 직접 부른다.

```bash
python scripts/smoke_config_io.py   # 기존 저장/복구 회귀도 같이 확인
```

---

## 9. 커밋 관례

- 단계별로 커밋을 나눈다. 특히 **uid 리팩터링은 UI 변경과 분리해서 단독 커밋** —
  이 단계가 끝난 시점에 동작이 이전과 100% 같아야 정상이다.
- 커밋 메시지는 제목/본문 초안을 먼저 보여주고 승인받은 뒤 커밋한다.
- 본문은 "왜 이렇게 했는지"를 남긴다 (이 저장소의 기존 커밋 스타일).
- 브랜치 `#34-multi-account` 에 이어서 커밋한다. 전부 끝나면 PR 을 연다.

---

## 10. 지금까지의 커밋

```
dc6c7b6  feat: 종목 관리 표에 계좌 컬럼 + 계좌 필터
cd14f6f  feat: 계좌 관리 다이얼로그 + 종목 추가·수정에 계좌 선택
0542ac7  feat: macOS 팝오버에 계좌 선택 줄 + 행 계좌 뱃지
74bf582  refactor: macOS 보유 항목 키를 code → uid 로 전환 + 계좌 저장/복원
c8304c9  feat: 여러 계좌 데이터 모델 — 계좌 레지스트리 + 보유 항목 uid
```

각 커밋 본문에 그 단계의 판단 근거가 들어 있다. `git show <hash>` 로 볼 것.
