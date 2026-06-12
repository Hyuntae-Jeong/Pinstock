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
import time
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


# ─── 실패 사유 (UI 가 사용자에게 정확한 메시지를 보여줄 수 있게 분류) ──────
# 예전엔 모든 실패가 "네트워크 상태를 확인해주세요" 한 줄로 통일돼서
# 실제로는 GitHub API rate-limit 인 경우(네트워크는 멀쩡)에도 같은 메시지가
# 떴다. UI 가 케이스별로 다른 안내를 보여줄 수 있게 사유를 구조화한다.
@dataclass(frozen=True)
class FetchError:
    kind: str                              # "rate_limit"|"network"|"http"|"no_asset"|"bad_tag"|"parse"
    detail: str = ""                       # 디버깅용 원문 (HTTP body 일부, 예외 메시지 등)
    reset_in_seconds: Optional[int] = None # rate_limit 일 때만, X-RateLimit-Reset - now


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
    """현재 설치본의 "교체 단위" 경로를 돌려준다.

    - Windows: Pinstock.exe 가 들어있는 폴더 (Pinstock-win-vX.Y.Z/).
    - macOS:   Pinstock.app 번들 그 자체 (예: /Applications/Pinstock.app).
               PyInstaller frozen 빌드에서 sys.executable 은
               `.../Pinstock.app/Contents/MacOS/Pinstock` 이므로 위로 거슬러 올라가
               `.app` suffix 디렉토리를 찾아 반환한다.

    is_frozen_build() == False 인 상태에서 호출하면 파이썬 인터프리터 폴더가 잡힌다 —
    실수로 인터프리터 폴더를 건드리지 않도록 호출 전에 can_self_update() 를 확인할 것.
    """
    exe = Path(sys.executable)
    if sys.platform == "darwin":
        p = exe.parent
        while p != p.parent:
            if p.suffix == ".app":
                return p
            p = p.parent
        # frozen 빌드인데 .app 을 못 찾으면 (예: 비표준 PyInstaller 배치)
        # 최소한 인터프리터 폴더를 반환해 호출측이 sanity 체크할 수 있게 함.
        return exe.parent
    return exe.parent


# ─── 플랫폼 자산 매칭 ─────────────────────────────────────────────────────
def _asset_name_for(version: str) -> str:
    if sys.platform == "win32":
        return f"Pinstock-win-v{version}.zip"
    if sys.platform == "darwin":
        return f"Pinstock-mac-v{version}.zip"
    raise RuntimeError(f"지원되지 않는 플랫폼: {sys.platform}")


# ─── 릴리즈 조회 ──────────────────────────────────────────────────────────
def fetch_latest_release_with_error(
    timeout: float = 5.0,
) -> tuple[Optional[ReleaseInfo], Optional[FetchError]]:
    """GitHub Releases API 호출. 성공 시 (release, None), 실패 시 (None, error).

    `/releases/latest` 엔드포인트는 prerelease 를 자동 제외하므로 안정 버전만 들어온다.
    호출측이 사용자에게 사유별로 다른 안내를 보여줄 수 있도록 실패를 구조화한다
    (특히 비인증 GitHub API 의 IP 당 60req/h rate-limit 을 네트워크 오류와 구분).
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
    except requests.RequestException as e:
        print(f"[updater] releases API 네트워크 오류: {e}")
        return None, FetchError(kind="network", detail=str(e))

    if r.status_code != 200:
        # 403 + X-RateLimit-Remaining=0 = GitHub API rate-limit. reset 시각도 함께 보존.
        remaining = r.headers.get("X-RateLimit-Remaining")
        if r.status_code == 403 and remaining == "0":
            reset_at = r.headers.get("X-RateLimit-Reset")
            try:
                reset_in = max(0, int(reset_at) - int(time.time())) if reset_at else None
            except (TypeError, ValueError):
                reset_in = None
            print(f"[updater] releases API rate-limit (reset_in={reset_in}s)")
            return None, FetchError(
                kind="rate_limit",
                detail=r.text[:200],
                reset_in_seconds=reset_in,
            )
        print(f"[updater] releases API status={r.status_code}")
        return None, FetchError(kind="http", detail=f"HTTP {r.status_code}: {r.text[:200]}")

    try:
        data = r.json()
    except ValueError as e:
        print(f"[updater] releases API 파싱 오류: {e}")
        return None, FetchError(kind="parse", detail=str(e))

    tag = data.get("tag_name", "")
    if not tag.startswith("v"):
        return None, FetchError(kind="bad_tag", detail=f"tag_name={tag!r}")
    version = tag.lstrip("v")

    expected = _asset_name_for(version)
    asset = next(
        (a for a in data.get("assets", []) if a.get("name") == expected),
        None,
    )
    if asset is None:
        print(f"[updater] 자산을 찾을 수 없음: {expected}")
        return None, FetchError(kind="no_asset", detail=expected)

    release = ReleaseInfo(
        tag=tag,
        version=version,
        body=data.get("body", "") or "",
        html_url=data.get("html_url", ""),
        asset_url=asset["browser_download_url"],
        asset_name=asset["name"],
        asset_size=int(asset.get("size", 0)),
    )
    return release, None


def fetch_latest_release(timeout: float = 5.0) -> Optional[ReleaseInfo]:
    """fetch_latest_release_with_error 의 얇은 래퍼 — 호출측이 실패 사유가 필요 없을 때
    (예: manager 의 백그라운드 자동 체크) 사용."""
    release, _ = fetch_latest_release_with_error(timeout=timeout)
    return release


# ─── 임시 폴더 ───────────────────────────────────────────────────────────
def _temp_dir() -> Path:
    base = Path(os.environ.get("TEMP") or os.environ.get("TMPDIR") or "/tmp")
    d = base / "pinstock-update"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _error_log_path() -> Path:
    """헬퍼 스크립트가 실패 시 남기는 로그. 다음 실행 시 메인 앱이 확인."""
    return _temp_dir() / "update-error.log"


def _pending_update_path() -> Path:
    """업데이트 적용 직전에 목표 버전을 남기는 마커. 교체된 새 버전이 다음 실행 때
    읽어 '업데이트 완료' 안내를 띄운다."""
    return _temp_dir() / "update-pending.txt"


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

REM 외부 명령은 시스템 절대 경로로 박는다. 사용자 PATH 에 Git Bash / MSYS2 의 GNU
REM 동명 도구(find, tar, timeout 등) 가 먼저 잡히면 동작이 완전히 달라져서 헬퍼가
REM 무한 hang 하거나 ZIP 추출이 실패한다.
set "WIN_TASKLIST=%SystemRoot%\System32\tasklist.exe"
set "WIN_FIND=%SystemRoot%\System32\find.exe"
set "WIN_PING=%SystemRoot%\System32\ping.exe"
set "WIN_TAR=%SystemRoot%\System32\tar.exe"

set "MAIN_PID=%~1"
set "INSTALL_DIR=%~2"
set "NEW_ZIP=%~3"
set "ERR_LOG=%~4"
set "OLD_DIR=%INSTALL_DIR%.old"

REM cwd 를 install_dir 밖으로 옮긴다. cmd.exe 가 install_dir 을 cwd 로 들고
REM 있으면 Windows 가 그 폴더의 rename 자체를 거부해서(ERROR_SHARING_VIOLATION)
REM 2단계 move 가 ERR_BACKUP_RENAME 으로 떨어진다. Pinstock.exe 를 install_dir
REM 안에서 더블클릭으로 실행하면 부모 cwd 가 install_dir 이라 그대로 상속된다.
cd /d "%TEMP%"

REM 1) wait for main process to exit (max 30s)
set /a TRIES=0
:wait
"%WIN_TASKLIST%" /FI "PID eq %MAIN_PID%" 2>NUL | "%WIN_FIND%" "%MAIN_PID%" >NUL
if errorlevel 1 goto exited
set /a TRIES+=1
if !TRIES! GEQ 30 (
    >"%ERR_LOG%" echo ERR_WAIT_TIMEOUT pid=%MAIN_PID%
    exit /b 1
)
REM ping 으로 ~1초 대기. timeout 은 stdin 리다이렉트 시 즉시 종료(입력 리디렉션
REM 미지원) 라서 헬퍼가 콘솔 없이 실행될 때 sleep 으로 안 쓰임.
"%WIN_PING%" -n 2 127.0.0.1 >NUL
goto wait

:exited
REM extra delay for file handles to release
"%WIN_PING%" -n 2 127.0.0.1 >NUL

REM 2) backup current install dir to .old
if exist "%OLD_DIR%" rmdir /s /q "%OLD_DIR%"
move "%INSTALL_DIR%" "%OLD_DIR%" >NUL
if errorlevel 1 (
    >"%ERR_LOG%" echo ERR_BACKUP_RENAME install_dir=%INSTALL_DIR%
    exit /b 1
)

REM 3) recreate install dir and extract zip
mkdir "%INSTALL_DIR%"
"%WIN_TAR%" -xf "%NEW_ZIP%" -C "%INSTALL_DIR%"
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


# ─── macOS 헬퍼 스크립트 ──────────────────────────────────────────────────
# args: $1=MAIN_PID  $2=INSTALL_PATH (Pinstock.app 절대경로)  $3=NEW_ZIP  $4=ERR_LOG
#
# 흐름:
#   1) MAIN_PID 가 사라질 때까지 대기 (최대 30초)
#   2) INSTALL_PATH → INSTALL_PATH.old 로 mv (백업)
#   3) ditto 로 ZIP 풀기 (Pinstock.app 의 부모 디렉토리에)
#   4) Pinstock.app/Contents/MacOS 가 존재하면 성공 → quarantine 제거, .old 정리, 재실행
#   5) 어디서든 실패하면 .old 복원 + ERR_LOG 기록
# 주의:
#   - unzip 절대 금지. CI 가 `ditto -c -k --keepParent` 로 만든 ZIP 은 .app 번들의
#     확장속성/실행권한/심볼릭링크를 보존하는 형식이라 unzip 으로 풀면 깨진다.
#   - ERR_LOG 는 ASCII 토큰만 (콘솔 codepage mismatch 회피용). 한글은 humanize_error 가 담당.
_MACOS_UPDATER_SH = r"""#!/bin/bash
# args: $1=MAIN_PID  $2=INSTALL_PATH  $3=NEW_ZIP  $4=ERR_LOG

MAIN_PID="$1"
INSTALL_PATH="$2"
NEW_ZIP="$3"
ERR_LOG="$4"
OLD_PATH="${INSTALL_PATH}.old"

# 1) 메인 프로세스 종료 대기 (최대 30s)
TRIES=0
while kill -0 "$MAIN_PID" 2>/dev/null; do
    TRIES=$((TRIES + 1))
    if [ "$TRIES" -ge 30 ]; then
        echo "ERR_WAIT_TIMEOUT pid=$MAIN_PID" > "$ERR_LOG"
        exit 1
    fi
    sleep 1
done
# 파일 핸들 해제 여유
sleep 1

# 2) 백업
if [ -e "$OLD_PATH" ]; then
    rm -rf "$OLD_PATH"
fi
if ! mv "$INSTALL_PATH" "$OLD_PATH"; then
    echo "ERR_BACKUP_RENAME install_path=$INSTALL_PATH" > "$ERR_LOG"
    exit 1
fi

# 3) ditto 로 풀기. ZIP 내부가 Pinstock.app/... 형태이므로 부모 디렉토리에 풀면 됨.
PARENT_DIR="$(dirname "$INSTALL_PATH")"
if ! ditto -xk "$NEW_ZIP" "$PARENT_DIR"; then
    echo "ERR_EXTRACT zip=$NEW_ZIP" > "$ERR_LOG"
    if [ -e "$INSTALL_PATH" ]; then
        rm -rf "$INSTALL_PATH"
    fi
    mv "$OLD_PATH" "$INSTALL_PATH"
    exit 1
fi

# 4) sanity check
if [ ! -d "$INSTALL_PATH/Contents/MacOS" ]; then
    echo "ERR_MISSING_EXE" > "$ERR_LOG"
    if [ -e "$INSTALL_PATH" ]; then
        rm -rf "$INSTALL_PATH"
    fi
    mv "$OLD_PATH" "$INSTALL_PATH"
    exit 1
fi

# Gatekeeper quarantine 속성 제거 (방어적 — Python requests 다운로드에는 보통
# com.apple.quarantine 이 안 붙지만, 외부 도구로 다운받은 케이스를 대비).
xattr -dr com.apple.quarantine "$INSTALL_PATH" 2>/dev/null

# 5) 재실행
open "$INSTALL_PATH"

# 6) 정리
rm -rf "$OLD_PATH"
rm -f "$NEW_ZIP"
rm -f "$0"
exit 0
"""


# 에러 코드 → 한글 메시지 매핑. updater 가 ERR_LOG 를 읽어 GUI 에 노출할 때 사용.
# Windows / macOS 가 같은 ERR_ 토큰을 공유 (ERR_MISSING_EXE 는 양쪽이 의미만 미세하게
# 다르되 메시지는 통합).
ERROR_MESSAGES: dict[str, str] = {
    "ERR_WAIT_TIMEOUT":   "메인 앱이 30초 안에 종료되지 않아 업데이트를 중단했습니다.",
    "ERR_BACKUP_RENAME":  "설치 폴더 이름을 바꾸지 못했습니다. 쓰기 권한이 없거나, "
                          "다른 프로세스가 폴더를 잡고 있을 수 있습니다.",
    "ERR_EXTRACT":        "새 ZIP 파일을 압축 해제하지 못했습니다.",
    "ERR_MISSING_EXE":    "새 설치본에서 실행 파일을 찾지 못했습니다. ZIP 이 손상되었을 수 있습니다.",
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
    """헬퍼 .cmd 를 분리 실행. 호출 직후 메인 앱은 즉시 종료해야 함.

    플래그 선택 근거:
      CREATE_NO_WINDOW          — 콘솔 창 표시 X (콘솔 자체는 할당되므로 내부의
                                  tasklist/find/ping 같은 콘솔 앱은 정상 동작).
      CREATE_NEW_PROCESS_GROUP  — 부모(Pinstock.exe) 가 quit 해도 헬퍼는 살아남음.

    DETACHED_PROCESS 는 콘솔 자체를 제거하여 tasklist/find 파이프가 hang 하는
    이슈가 있어 사용하지 않는다.
    """
    cmd_path = _write_windows_updater_script()
    pid = os.getpid()
    err_log = _error_log_path()

    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_NO_WINDOW = 0x08000000

    # cwd 를 install_dir 밖으로 명시. Pinstock.exe 가 install_dir 안에서
    # 더블클릭으로 실행됐을 때 부모 cwd 가 install_dir 이라 그대로 상속되면
    # 헬퍼 cmd.exe 가 install_dir 을 잡고 있게 되어 move 가 거부된다.
    subprocess.Popen(
        [
            "cmd.exe", "/c",
            str(cmd_path),
            str(pid),
            str(install_dir),
            str(new_zip),
            str(err_log),
        ],
        cwd=str(_temp_dir()),
        creationflags=CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _write_macos_updater_script() -> Path:
    sh_path = _temp_dir() / "pinstock-update.sh"
    sh_path.write_text(_MACOS_UPDATER_SH, encoding="utf-8")
    sh_path.chmod(0o755)
    return sh_path


def launch_updater_macos(install_dir: Path, new_zip: Path) -> None:
    """헬퍼 .sh 를 분리 실행. 호출 직후 메인 앱은 즉시 종료해야 함.

    `start_new_session=True` 는 POSIX setsid() 와 동일 — 부모(Pinstock.app) 가
    종료돼도 헬퍼는 살아남는다. stdin/stdout/stderr 는 DEVNULL 로 막아 부모의
    파이프가 끊겼을 때 SIGPIPE 로 죽지 않게 한다.

    install_dir 은 `Pinstock.app` 의 절대경로 (current_install_dir() 가 .app
    번들을 반환하므로 호출측은 그대로 넘기면 됨).
    """
    sh_path = _write_macos_updater_script()
    pid = os.getpid()
    err_log = _error_log_path()

    subprocess.Popen(
        [
            "/bin/bash",
            str(sh_path),
            str(pid),
            str(install_dir),
            str(new_zip),
            str(err_log),
        ],
        start_new_session=True,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def launch_updater(install_dir: Path, new_zip: Path) -> None:
    """현재 플랫폼에 맞춰 헬퍼 실행. 호출 직후 메인 앱은 즉시 종료해야 함."""
    if sys.platform == "win32":
        launch_updater_windows(install_dir, new_zip)
    elif sys.platform == "darwin":
        launch_updater_macos(install_dir, new_zip)
    else:
        raise RuntimeError(f"지원되지 않는 플랫폼: {sys.platform}")


# ─── 업데이트 완료 마커 (성공 안내용) ────────────────────────────────────
def mark_update_pending(version: str) -> None:
    """헬퍼 실행 직전에 호출. 교체된 새 버전이 다음 실행 때 read_and_clear_pending_update()
    로 읽어 '버전 X 로 업데이트되었습니다' 안내를 띄운다. 실패해도 업데이트 자체는
    진행되어야 하므로 예외는 삼킨다."""
    try:
        _pending_update_path().write_text(version, encoding="utf-8")
    except OSError as e:
        print(f"[updater] pending 마커 기록 실패: {e}")


def read_and_clear_pending_update() -> Optional[str]:
    """직전에 적용 시도한 업데이트의 목표 버전을 읽고 삭제. 없으면 None.

    호출측은 반환값을 현재 __version__ 과 비교해, 실제로 그 버전으로 올라왔을 때만
    완료 안내를 띄운다 (롤백되어 옛 버전이 다시 떴다면 버전이 달라 안내하지 않음)."""
    p = _pending_update_path()
    if not p.is_file():
        return None
    try:
        content = p.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    try:
        p.unlink()
    except OSError:
        pass
    return content or None


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
