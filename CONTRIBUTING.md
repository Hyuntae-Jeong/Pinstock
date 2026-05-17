# Contributing to Pinstock

Pinstock 은 취미로 만드는 사이드 프로젝트입니다. 누구나 환영해요 — 처음 오픈소스에 기여해보시는 분도 부담 없이 들러주세요. [`good first issue`](https://github.com/Hyuntae-Jeong/Pinstock/labels/good%20first%20issue) 라벨이 붙은 이슈부터 살펴보시면 좋습니다.

**사용하시다가 "이게 좀 불편한데" 싶거나 "이런 기능이 있으면 좋겠다" 싶은 게 떠오르면 망설이지 말고 이슈를 남겨주세요.** 작은 불편함 하나가 다음 기능의 출발점이 됩니다. 코드 한 줄도 좋고, 아이디어만 던져주셔도 좋습니다 🙌

---

## 1. 이슈 등록

먼저 [Issues 탭](https://github.com/Hyuntae-Jeong/Pinstock/issues) 에서 동일/유사 이슈가 있는지 검색해 주세요. 없으면 새로 등록:

- 🐛 **버그 발견** → [Bug Report 템플릿](https://github.com/Hyuntae-Jeong/Pinstock/issues/new?template=bug_report.yml)
- 💡 **기능 제안** → [Feature Request 템플릿](https://github.com/Hyuntae-Jeong/Pinstock/issues/new?template=feature_request.yml)
- 🔒 **보안 이슈** — 공개 이슈로 올리지 마시고 **latissimuscle@gmail.com** 으로 비공개 신고 부탁드립니다.

### 새 기능을 작업하시기 전에 — 이슈를 먼저 남겨주세요

여러 분이 같은 기능을 동시에 만들고 있는 경우를 막기 위함입니다. 작업 시작 전에:

1. Feature Request 이슈를 등록하거나 기존 이슈에 댓글로 "제가 작업해보겠습니다" 라고 알려주세요.
2. 메인테이너가 방향성을 확인해드린 뒤 작업에 들어가시면 됩니다 (보통 하루 이내 회신).
3. 오타 수정/문서 보완 같은 작은 변경은 이슈 없이 바로 PR 주셔도 됩니다.

---

## 2. 개발 환경 셋업

**요구 사항**: Python 3.10+

```bash
git clone https://github.com/Hyuntae-Jeong/Pinstock.git
cd Pinstock
pip install -r requirements.txt
python -m pinstock
```

플랫폼별 주의사항:
- **Windows** — 트레이 아이콘이 보이지 않으면 시스템 트레이 설정에서 Pinstock 을 "표시" 로 변경.
- **macOS** — 메뉴바에 Pinstock 아이콘이 나타나는지 확인. 첫 실행 시 접근 권한 팝업이 뜰 수 있습니다.

---

## 3. 프로젝트 구조 한눈에

어디를 수정해야 할지 빠르게 잡으시라고 정리했습니다:

| 경로 | 담당 |
|---|---|
| `pinstock/core/` | API 호출, 설정 저장 등 OS 공통 로직 |
| `pinstock/ui_windows/` | Windows 부동 위젯 UI |
| `pinstock/ui_macos/` | macOS 메뉴바 팝오버 UI |
| `assets/` | 아이콘 등 정적 리소스 |

`stocks.json` 스키마는 두 플랫폼이 공유하므로, 한쪽을 바꾸시면 다른 쪽도 영향 받을 수 있는지 확인 부탁드립니다.

---

## 4. PR 워크플로우

### 브랜치 네이밍

```
#<이슈번호>-<짧은-설명>
```

예) `#5-windows-opacity-slider`, `#12-fix-tray-icon`

> 💡 셸에서 `#` 는 주석으로 해석될 수 있으니, 명령어에서는 따옴표로 감싸주세요:
> ```bash
> git checkout -b "#5-windows-opacity-slider"
> ```

### 커밋 메시지

기존 히스토리는 [Conventional Commits](https://www.conventionalcommits.org/) 형식을 따르고 있어요. 동일하게 맞춰주시면 좋습니다.

- `feat:` 새 기능
- `fix:` 버그 수정
- `refactor:` 동작 변화 없는 리팩토링
- `docs:` 문서만 변경
- `chore:` 빌드 / 설정 / 메타 변경

제목/본문은 한국어 OK 입니다.

### PR 제출

1. Fork → 브랜치 생성 → 커밋 → push
2. PR 생성 시 [PR 템플릿](.github/PULL_REQUEST_TEMPLATE.md) 의 체크리스트를 채워주세요.
3. PR 제목에 관련 이슈를 `Closes #5` 처럼 명시하면 머지 시 자동 close 됩니다.

---

## 5. 코드 스타일 & 테스트

- **주석/문서 언어**: 한국어 OK (기존 코드 컨벤션).
- **포매터 강제 없음**, PEP 8 정도만 지켜주시면 됩니다.
- **자동 테스트는 아직 없습니다.** UI 변경 시 직접 실행해서 골든패스를 확인해주세요:
  - 위젯 추가/삭제, 종목 갱신, 위젯 드래그/숨김/표시 등 평소 자주 쓰는 흐름.
- UI 변경은 가능하면 **Windows + macOS 양쪽** 확인 부탁드립니다. 한쪽만 가능하시면 PR 에 어떤 OS 만 확인했는지 적어주세요 — 다른 쪽 검증은 메인테이너가 도와드립니다.

---

## 6. 라이선스

이 프로젝트는 [MIT 라이선스](LICENSE) 입니다. PR 을 제출하시면 본인의 기여 또한 MIT 하에 배포되는 데에 동의하시는 것으로 간주합니다.

---

질문이 있으시면 언제든 이슈로 남겨주세요. 작은 기여도 환영입니다 🙌
