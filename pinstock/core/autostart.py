"""시스템 시작(로그인) 시 Pinstock 자동 실행 등록/해제.

플랫폼별 표준 방식만 사용한다 (관리자 권한 불필요):
  - Windows: HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run 레지스트리 값.
  - macOS:   ~/Library/LaunchAgents/<bundle id>.plist (LaunchAgent).

자동 실행은 "설치된(frozen) 빌드" 에서만 의미가 있다. 개발 모드
(`python -m pinstock`) 에서는 안정적으로 가리킬 실행 파일이 없으므로
지원하지 않는다 — `autostart_supported()` 가 False 를 돌려준다.

── 등록 슬롯 공유 설계 ──────────────────────────────────────────────────
Windows 값 이름(`Pinstock`) 과 macOS plist 파일명(`<bundle id>.plist`) 은
빌드와 무관하게 고정이다. 따라서 dev 빌드와 정식 빌드가 같은 "슬롯 하나" 를
공유한다 → 동시에 둘 다 자동 실행되는 일은 구조적으로 없고, 마지막에 ON 한
빌드가 슬롯을 차지한다(last-writer-wins).

이 공유 슬롯의 부작용(체크 상태가 다른 빌드로 넘어가 보이는 것, 죽은 경로
잔재)을 다음 두 장치로 보강한다:
  1. `is_autostart_enabled()` 는 단순 존재가 아니라 등록 경로가 *현재 exe* 와
     일치하는지까지 확인한다 → 빌드별 체크 상태가 정확하다.
  2. `reconcile_autostart()` 는 앱 시작 시 죽은 경로(없어진 exe)를 가리키는
     잔재를 정리한다 (살아있는 다른 빌드 등록은 건드리지 않는다).
"""

import os
import sys
from pathlib import Path

APP_NAME = "Pinstock"
BUNDLE_ID = "com.hyuntae.pinstock"

# Windows 자동 실행 레지스트리 경로/값 이름
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = APP_NAME


def _is_frozen() -> bool:
    """PyInstaller 로 묶인 설치본에서 실행 중인지."""
    return bool(getattr(sys, "frozen", False))


def autostart_supported() -> bool:
    """현재 환경에서 자동 실행 토글을 제공해도 되는지.

    설치된 빌드(frozen) + Windows/macOS 일 때만 True. 개발 모드에서는
    파이썬 인터프리터를 등록하게 되어 동작이 불안정하므로 False.
    """
    return _is_frozen() and sys.platform in ("win32", "darwin")


def _current_exe() -> str:
    """자동 실행에 등록할 현재 실행 파일 경로."""
    return sys.executable


def _same_path(a: str, b: str) -> bool:
    """두 실행 파일 경로가 같은 대상을 가리키는지.

    따옴표 제거 + 경로 구분자/대소문자 정규화(`normcase` 는 Windows 에서만
    소문자화하고 POSIX 에서는 그대로 둔다) 후 비교한다.
    """
    def _norm(p: str) -> str:
        p = (p or "").strip().strip('"')
        try:
            return os.path.normcase(os.path.normpath(p))
        except Exception:
            return p

    return bool(a) and bool(b) and _norm(a) == _norm(b)


def _macos_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{BUNDLE_ID}.plist"


# ─── Windows ────────────────────────────────────────────────────────────────
def _win_registered_command() -> str | None:
    """Run 키에 등록된 명령 문자열. 없으면 None."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, _RUN_VALUE)
            return value or None
    except FileNotFoundError:
        return None
    except OSError:
        return None


def _win_set(enabled: bool) -> None:
    import winreg

    if enabled:
        # 경로에 공백이 있어도 안전하도록 따옴표로 감싼다.
        command = f'"{_current_exe()}"'
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, _RUN_VALUE, 0, winreg.REG_SZ, command)
    else:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, _RUN_VALUE)
        except FileNotFoundError:
            pass  # 이미 등록되어 있지 않음 — 멱등하게 통과.


# ─── macOS ──────────────────────────────────────────────────────────────────
def _macos_registered_command() -> str | None:
    """LaunchAgent plist 의 ProgramArguments[0]. 파일이 없거나 비정상이면 None."""
    import plistlib

    plist = _macos_plist_path()
    if not plist.is_file():
        return None
    try:
        with open(plist, "rb") as f:
            data = plistlib.load(f)
        args = data.get("ProgramArguments") or []
        return args[0] if args else None
    except Exception:
        return None


def _macos_set(enabled: bool) -> None:
    import plistlib

    plist = _macos_plist_path()
    if enabled:
        plist.parent.mkdir(parents=True, exist_ok=True)
        # frozen 빌드에서 sys.executable 은 .app/Contents/MacOS/Pinstock 바이너리.
        # 이를 ProgramArguments 로 등록하면 로그인 시 launchd 가 앱을 실행한다.
        data = {
            "Label": BUNDLE_ID,
            "ProgramArguments": [_current_exe()],
            "RunAtLoad": True,
        }
        with open(plist, "wb") as f:
            plistlib.dump(data, f)
    else:
        plist.unlink(missing_ok=True)


# ─── 플랫폼 디스패치 ──────────────────────────────────────────────────────────
def _registered_command() -> str | None:
    if sys.platform == "win32":
        return _win_registered_command()
    if sys.platform == "darwin":
        return _macos_registered_command()
    return None


def _set(enabled: bool) -> None:
    if sys.platform == "win32":
        _win_set(enabled)
    elif sys.platform == "darwin":
        _macos_set(enabled)


# ─── 공개 API ────────────────────────────────────────────────────────────────
def is_autostart_enabled() -> bool:
    """현재 '이 실행 파일' 이 자동 실행에 등록돼 있는지.

    단순 존재 여부가 아니라 등록된 경로가 현재 exe 와 일치하는지까지 확인한다.
    덕분에 dev/정식 빌드가 같은 등록 슬롯을 공유해도 각 빌드의 체크 상태가
    '나를 가리키고 있는가' 로 정확히 표시된다. 지원하지 않는 환경이면 False.
    """
    if not autostart_supported():
        return False
    try:
        cmd = _registered_command()
        return _same_path(cmd or "", _current_exe())
    except Exception as e:
        print(f"[autostart] 상태 조회 오류: {e}")
        return False


def set_autostart(enabled: bool) -> bool:
    """자동 실행을 등록(True)/해제(False) 한다.

    Returns:
        쓰기 후 다시 읽은 실제 상태. 쓰기에 실패하면 변경 전 상태가 그대로
        반환되므로(자동 원복 효과), 호출 측은 이 반환값으로 체크박스를
        동기화하면 된다.
    """
    if not autostart_supported():
        return False
    try:
        _set(enabled)
    except Exception as e:
        print(f"[autostart] 설정 오류: {e}")
    return is_autostart_enabled()


def reconcile_autostart() -> None:
    """앱 시작 시 1회 호출: 죽은 자동 실행 잔재를 정리한다.

    등록 슬롯이 *존재하지 않는 실행 파일* 을 가리키면 제거한다. dev 빌드로
    테스트하며 ON 해둔 뒤 그 빌드를 지운 경우처럼, 부팅 때 없는 exe 를
    실행하려다 조용히 실패하는 잔재를 없앤다.

    살아있는 다른 빌드의 정상 등록은 건드리지 않으며, 자동으로 새로 등록하지도
    않는다 (사용자가 원치 않는데 자동 실행이 켜지는 일 방지).
    """
    if not autostart_supported():
        return
    try:
        cmd = _registered_command()
        if not cmd:
            return
        target = cmd.strip().strip('"')
        if target and not os.path.exists(target):
            _set(False)
            print(f"[autostart] 죽은 자동 실행 항목 정리: {target}")
    except Exception as e:
        print(f"[autostart] reconcile 오류: {e}")
