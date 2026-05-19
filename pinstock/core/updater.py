"""앱 내 자동 업데이트 — 순수 로직 (UI 의존 없음).

흐름:
  1. fetch_latest_release()        → GitHub Releases API 로 최신 stable 조회
  2. is_newer(current, latest)     → 새 버전 있는지 비교
  3. download_zip(...)             → 새 ZIP 을 %TEMP% 에 받기 (진행 콜백 + 취소 지원)
  4. launch_updater(...)           → 분리(detached) 헬퍼 스크립트 실행 + 메인 즉시 종료
                                     → 헬퍼: 메인 종료 대기 → .old 백업 → 추출 → 재실행

개발 환경(`python -m pinstock`)이나 placeholder 버전("+dev")에서는 `can_self_update()`
가 False 를 반환하여 모든 업데이트 동작이 비활성화된다.
"""

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import requests

from ..__version__ import __version__


GITHUB_OWNER = "Hyuntae-Jeong"
GITHUB_REPO = "Pinstock"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
USER_AGENT = f"Pinstock/{__version__}"


@dataclass(frozen=True)
class ReleaseInfo:
    tag: str            # e.g. "v0.1.4"
    version: str        # e.g. "0.1.4"
    body: str           # 릴리즈 노트 (마크다운)
    html_url: str       # 릴리즈 페이지 URL
    asset_url: str      # 현재 OS 용 ZIP 직접 다운로드 URL
    asset_name: str     # e.g. "Pinstock-win-v0.1.4.zip"
    asset_size: int     # bytes


# ─── 버전 비교 ────────────────────────────────────────────────────────────
def is_dev_build(version: str) -> bool:
    """개발 빌드 = PEP 440 local part("+xxx") 가 붙어있음. 자동 업데이트 비활성."""
    return "+" in version


def _parse(version: str) -> tuple[int, ...]:
    return tuple(int(x) for x in version.split("."))


def is_newer(current: str, latest: str) -> bool:
    """latest 가 current 보다 높은 버전이면 True. 순수 버전 비교.

    PEP 440 로컬 식별자(+xxx)는 비교에서 제외 — placeholder("0.0.0+dev") 도
    자연스럽게 0.0.0 으로 본다. "자동 업데이트를 트리거해도 되는가" 는 별개
    질문이므로 호출측에서 `can_self_update()` 와 함께 사용할 것.
    """
    cur = current.split("+", 1)[0]
    lat = latest.split("+", 1)[0]
    try:
        return _parse(lat) > _parse(cur)
    except ValueError:
        return False


# ─── 설치 환경 판별 ───────────────────────────────────────────────────────
def is_frozen_build() -> bool:
    """PyInstaller 로 묶인 바이너리에서 실행 중인지."""
    return getattr(sys, "frozen", False)


def can_self_update() -> bool:
    """자동 업데이트를 실행해도 안전한 환경인지.
    PyInstaller 빌드 + 정상 버전(+dev 아님) 일 때만 True."""
    return is_frozen_build() and not is_dev_build(__version__)


def current_install_dir() -> Path:
    """현재 실행 중인 Pinstock.exe (또는 .app) 가 들어있는 폴더.

    is_frozen_build() == False 인 상태에서 호출하면 파이썬 인터프리터 폴더가 잡힌다 —
    실수로 인터프리터 폴더를 건드리지 않도록 호출 전에 can_self_update() 를 확인할 것.
    """
    return Path(sys.executable).parent


# ─── 플랫폼 자산 매칭 ─────────────────────────────────────────────────────
def _asset_name_for(version: str) -> str:
    if sys.platform == "win32":
        return f"Pinstock-win-v{version}.zip"
    if sys.platform == "darwin":
        return f"Pinstock-mac-v{version}.zip"
    raise RuntimeError(f"지원되지 않는 플랫폼: {sys.platform}")


# ─── 릴리즈 조회 ──────────────────────────────────────────────────────────
def fetch_latest_release(timeout: float = 5.0) -> Optional[ReleaseInfo]:
    """GitHub Releases API 호출. 네트워크 오류/rate limit 등은 조용히 None 반환.

    `/releases/latest` 엔드포인트는 prerelease 를 자동 제외하므로 안정 버전만 들어온다.
    """
    try:
        r = requests.get(
            RELEASES_API,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/vnd.github+json",
            },
            timeout=timeout,
        )
        if r.status_code != 200:
            print(f"[updater] releases API status={r.status_code}")
            return None
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        print(f"[updater] releases API 오류: {e}")
        return None

    tag = data.get("tag_name", "")
    if not tag.startswith("v"):
        return None
    version = tag.lstrip("v")

    expected = _asset_name_for(version)
    asset = next(
        (a for a in data.get("assets", []) if a.get("name") == expected),
        None,
    )
    if asset is None:
        print(f"[updater] 자산을 찾을 수 없음: {expected}")
        return None

    return ReleaseInfo(
        tag=tag,
        version=version,
        body=data.get("body", "") or "",
        html_url=data.get("html_url", ""),
        asset_url=asset["browser_download_url"],
        asset_name=asset["name"],
        asset_size=int(asset.get("size", 0)),
    )


# ─── 임시 폴더 ───────────────────────────────────────────────────────────
def _temp_dir() -> Path:
    base = Path(os.environ.get("TEMP") or os.environ.get("TMPDIR") or "/tmp")
    d = base / "pinstock-update"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _error_log_path() -> Path:
    """헬퍼 스크립트가 실패 시 남기는 로그. 다음 실행 시 메인 앱이 확인."""
    return _temp_dir() / "update-error.log"


def download_path_for(release: ReleaseInfo) -> Path:
    return _temp_dir() / release.asset_name


# ─── 다운로드 ────────────────────────────────────────────────────────────
def download_zip(
    url: str,
    dest: Path,
    on_progress: Optional[Callable[[int, int], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    chunk_size: int = 64 * 1024,
) -> bool:
    """ZIP 스트리밍 다운로드.

    on_progress(done, total): 청크마다 호출. total=0 이면 Content-Length 미상.
    cancel_check(): True 반환 시 부분 파일 삭제 후 False 반환.
    성공 시 True.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with requests.get(
            url,
            stream=True,
            timeout=10,
            headers={"User-Agent": USER_AGENT},
        ) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length", 0))
            done = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=chunk_size):
                    if cancel_check is not None and cancel_check():
                        f.close()
                        dest.unlink(missing_ok=True)
                        return False
                    if chunk:
                        f.write(chunk)
                        done += len(chunk)
                        if on_progress is not None:
                            on_progress(done, total)
        return True
    except (requests.RequestException, OSError) as e:
        print(f"[updater] 다운로드 오류: {e}")
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        return False


# ─── Windows 헬퍼 스크립트 ────────────────────────────────────────────────
# args: %1=MAIN_PID  %2=INSTALL_DIR  %3=NEW_ZIP  %4=ERR_LOG
#
# 흐름:
#   1) MAIN_PID 가 사라질 때까지 대기 (최대 30초)
#   2) INSTALL_DIR → INSTALL_DIR.old 로 rename (백업)
#   3) 빈 INSTALL_DIR 새로 만들고 tar 로 ZIP 풀기
#   4) Pinstock.exe 가 존재하면 성공 → .old 정리 + 재실행 + 자기 자신 삭제
#   5) 어디서든 실패하면 .old 복원 + ERR_LOG 기록
# 주의: 에러 메시지는 ASCII 영문으로만. ERR_LOG 는 Python 이 다시 읽어서 GUI 에
# 한글로 표시하므로, 콘솔 코드페이지 mismatch 로 깨지지 않게 ID 기반으로 통신.
_WINDOWS_UPDATER_CMD = r"""@echo off
setlocal EnableDelayedExpansion

set "MAIN_PID=%~1"
set "INSTALL_DIR=%~2"
set "NEW_ZIP=%~3"
set "ERR_LOG=%~4"
set "OLD_DIR=%INSTALL_DIR%.old"

REM 1) wait for main process to exit (max 30s)
set /a TRIES=0
:wait
tasklist /FI "PID eq %MAIN_PID%" 2>NUL | find "%MAIN_PID%" >NUL
if errorlevel 1 goto exited
set /a TRIES+=1
if !TRIES! GEQ 30 (
    >"%ERR_LOG%" echo ERR_WAIT_TIMEOUT pid=%MAIN_PID%
    exit /b 1
)
timeout /t 1 /nobreak >NUL
goto wait

:exited
REM extra delay for file handles to release
timeout /t 1 /nobreak >NUL

REM 2) backup current install dir to .old
if exist "%OLD_DIR%" rmdir /s /q "%OLD_DIR%"
move "%INSTALL_DIR%" "%OLD_DIR%" >NUL
if errorlevel 1 (
    >"%ERR_LOG%" echo ERR_BACKUP_RENAME install_dir=%INSTALL_DIR%
    exit /b 1
)

REM 3) recreate install dir and extract zip
REM   Windows 10 1803+ 내장 tar.exe(bsdtar) 만 ZIP 을 지원. PATH 가 Git Bash 의 GNU tar
REM   를 먼저 잡는 경우가 있어 시스템 tar 의 절대 경로로 박는다.
mkdir "%INSTALL_DIR%"
"%SystemRoot%\System32\tar.exe" -xf "%NEW_ZIP%" -C "%INSTALL_DIR%"
if errorlevel 1 (
    >"%ERR_LOG%" echo ERR_EXTRACT zip=%NEW_ZIP%
    goto rollback
)

REM 4) sanity check
if not exist "%INSTALL_DIR%\Pinstock.exe" (
    >"%ERR_LOG%" echo ERR_MISSING_EXE
    goto rollback
)

REM 5) launch new version
start "" "%INSTALL_DIR%\Pinstock.exe"

REM 6) cleanup: .old, temp zip, self
rmdir /s /q "%OLD_DIR%"
del /q "%NEW_ZIP%"
(goto) 2>nul & del "%~f0"
exit /b 0

:rollback
if exist "%INSTALL_DIR%" rmdir /s /q "%INSTALL_DIR%"
move "%OLD_DIR%" "%INSTALL_DIR%" >NUL
exit /b 1
"""

# 에러 코드 → 한글 메시지 매핑. updater 가 ERR_LOG 를 읽어 GUI 에 노출할 때 사용.
ERROR_MESSAGES: dict[str, str] = {
    "ERR_WAIT_TIMEOUT":   "메인 앱이 30초 안에 종료되지 않아 업데이트를 중단했습니다.",
    "ERR_BACKUP_RENAME":  "설치 폴더 이름을 바꾸지 못했습니다. 권한이 없거나(예: Program Files), "
                          "탐색기 등 다른 프로세스가 폴더를 잡고 있을 수 있습니다.",
    "ERR_EXTRACT":        "새 ZIP 파일을 압축 해제하지 못했습니다.",
    "ERR_MISSING_EXE":    "새 설치본에서 Pinstock.exe 를 찾지 못했습니다. ZIP 이 손상되었을 수 있습니다.",
}


def humanize_error(log_content: str) -> str:
    """ERR_LOG 의 첫 토큰을 보고 한글 메시지로 변환. 매핑 없으면 원문 그대로."""
    if not log_content:
        return ""
    first_token = log_content.split(None, 1)[0]
    return ERROR_MESSAGES.get(first_token, log_content)


def _write_windows_updater_script() -> Path:
    cmd_path = _temp_dir() / "pinstock-update.cmd"
    cmd_path.write_text(_WINDOWS_UPDATER_CMD, encoding="utf-8")
    return cmd_path


def launch_updater_windows(install_dir: Path, new_zip: Path) -> None:
    """헬퍼 .cmd 를 분리 실행. 호출 직후 메인 앱은 즉시 종료해야 함."""
    cmd_path = _write_windows_updater_script()
    pid = os.getpid()
    err_log = _error_log_path()

    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_NO_WINDOW = 0x08000000

    subprocess.Popen(
        [
            "cmd.exe", "/c",
            str(cmd_path),
            str(pid),
            str(install_dir),
            str(new_zip),
            str(err_log),
        ],
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def launch_updater_macos(install_dir: Path, new_zip: Path) -> None:
    raise NotImplementedError("macOS 자동 업데이트는 Step 6 에서 구현 예정")


def launch_updater(install_dir: Path, new_zip: Path) -> None:
    """현재 플랫폼에 맞춰 헬퍼 실행. 호출 직후 메인 앱은 즉시 종료해야 함."""
    if sys.platform == "win32":
        launch_updater_windows(install_dir, new_zip)
    elif sys.platform == "darwin":
        launch_updater_macos(install_dir, new_zip)
    else:
        raise RuntimeError(f"지원되지 않는 플랫폼: {sys.platform}")


# ─── 이전 업데이트 실패 로그 확인 ────────────────────────────────────────
def read_and_clear_last_error() -> Optional[str]:
    """다음 실행 시 '이전 업데이트 실패' 알림을 띄울 수 있도록 로그를 읽고 삭제."""
    log = _error_log_path()
    if not log.is_file():
        return None
    try:
        content = log.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    try:
        log.unlink()
    except OSError:
        pass
    return content or None
