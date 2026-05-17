# 🔄 업데이트 가이드

이미 Pinstock 을 쓰고 있는 사용자가 최신 버전으로 옮겨가는 방법.

> 💾 **데이터는 자동 보존됩니다.** 종목 리스트와 위젯 위치(`stocks.json`)는 앱 폴더가 아니라 OS 의 사용자 데이터 폴더에 저장되므로, 앱을 새로 설치해도 그대로 유지됩니다.
> - macOS: `~/Library/Application Support/Pinstock/`
> - Windows: `%APPDATA%\Pinstock\`

---

## 🍎 macOS

1. **기존 앱 종료** — 메뉴바 Pinstock 아이콘 클릭 → 우클릭/메뉴에서 "종료"
2. **기존 앱 제거** — Finder → 응용 프로그램(또는 이전 버전을 둔 위치) 에서 `Pinstock.app` 휴지통으로 이동
3. **새 버전 다운로드** — [최신 릴리즈](https://github.com/Hyuntae-Jeong/Pinstock/releases/latest) 에서 `Pinstock-mac-vX.Y.Z.zip` 받기
4. **설치** — 압축을 풀고 `Pinstock.app` 을 Finder 의 **응용 프로그램** 폴더로 드래그
5. **첫 실행** — `Pinstock.app` 더블클릭 → "확인되지 않은 개발자" 경고가 뜸 → **시스템 설정 → 개인정보 보호 및 보안** 으로 이동 → 맨 아래까지 스크롤 → **"그래도 열기"** 클릭 → 암호/Touch ID 인증
   - 자세한 첫 실행 절차는 [README 의 macOS 설치 안내](README.md#macos) 참고

---

## 🪟 Windows

1. **기존 앱 종료** — 시스템 트레이 Pinstock 아이콘 우클릭 → "종료" (또는 작업 관리자에서 `Pinstock.exe` 종료)
2. **기존 폴더 제거(권장) 또는 백업** — 압축을 풀어둔 `Pinstock\` 폴더 삭제. 덮어쓰기보다 새 폴더로 푸는 것이 안전합니다
3. **새 버전 다운로드** — [최신 릴리즈](https://github.com/Hyuntae-Jeong/Pinstock/releases/latest) 에서 `Pinstock-win-vX.Y.Z.zip` 받기
4. **설치** — 압축을 원하는 위치(예: `Documents\Pinstock`) 에 풀기
5. **실행** — 폴더 안 `Pinstock.exe` (또는 콘솔창 없이 실행하려면 `Pinstock.vbs`) 더블클릭
   - 첫 실행 시 Windows SmartScreen 경고가 뜨면 **"추가 정보" → "실행"** 클릭

---

## 📌 버전별로 알아둘 점

### v0.1.1 / v0.1.2 → v0.1.3 (macOS)
- macOS 에서 Dock 에도 Pinstock 아이콘이 표시되도록 바뀌었습니다 (기존에는 메뉴바 전용). 동작에는 영향 없지만 Dock 정리를 사용 중이라면 새로 잡아주세요.

---

## ❓ 문제가 생겼을 때

- **종목 데이터가 사라졌어요** — 위 데이터 보존 안내의 OS 사용자 폴더 경로에서 `stocks.json` 이 그대로 있는지 확인해주세요.
- **새 버전이 안 뜨거나 옛 버전이 실행돼요** — 기존 앱이 완전히 종료됐는지(트레이/메뉴바에서 아이콘이 사라졌는지) 확인 후 다시 시도해주세요.
- 그 외 — [Issues](https://github.com/Hyuntae-Jeong/Pinstock/issues) 에 남겨주세요.
