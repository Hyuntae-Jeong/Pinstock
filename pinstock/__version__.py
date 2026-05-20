"""앱 버전 — 단일 진실값.

리포에 커밋된 값은 placeholder 이고, 실제 배포 버전은 빌드 시 GitHub Actions
가 git 태그(`vX.Y.Z`)에서 추출해 이 파일을 덮어씁니다.
(`.github/workflows/release.yml` 의 "Inject version from tag" step 참조)

따라서 `0.0.0+dev` 라는 값을 보면 "릴리즈 빌드가 아닌 개발 빌드" 라는 뜻이며,
자동 업데이트 기능은 이 경우 비활성화됩니다.
"""

__version__ = "0.0.0+dev"
