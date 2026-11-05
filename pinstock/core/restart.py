"""앱 재시작 — 순수 로직 (UI 의존 없음).

트레이/메뉴바 메뉴의 '재시작' 이 쓴다. 흐름:
  1. launch_relaunch()    → 지금과 같은 실행 명령으로 새 프로세스를 분리(detached)
                            실행하면서 `--restart-wait-pid=<내 PID>` 를 붙여 넘긴다
  2. 호출측은 곧바로 app.quit() → aboutToQuit 에서 stocks.json 을 저장하고 끝난다
  3. 새 프로세스는 main() 맨 앞에서 pop_wait_pid_arg() 로 그 인자를 떼어 내고,
     wait_for_exit() 로 옛 프로세스가 완전히 끝난 것을 확인한 뒤에야 기동한다

왜 새 프로세스가 기다리는가:
  - 옛 프로세스는 종료 직전(aboutToQuit)에 stocks.json 을 저장한다. 새 프로세스가
    그보다 먼저 읽으면 직전 변경(위젯 위치 등)이 빠진 설정으로 뜨고, 그 상태로
    다시 저장하면 옛 프로세스의 마지막 저장을 덮어쓴다.
  - 단일 인스턴스 잠금이 없어서, 겹쳐 뜨면 트레이 아이콘·위젯이 두 벌이 된다.
  updater 처럼 별도 .cmd/.sh 헬퍼를 두지 않은 건, 교체할 파일이 없으니 같은 실행
  파일이 스스로 기다리면 충분하기 때문이다.

옛 프로세스가 제한 시간 안에 끝나지 않으면 새 프로세스는 뜨지 않고 그냥 끝난다 —
두 벌이 동시에 떠서 서로의 저장을 덮어쓰는 것보다 낫다. 옛 프로세스는 그대로
살아 있으니 사용자가 앱을 잃지는 않는다.

개발 모드(`python -m pinstock`)에서도 동작한다 — 인터프리터 옵션까지 원래 명령
그대로(sys.orig_argv) 다시 실행한다.
"""

import os
import subprocess
import sys
import time
from typing import Optional


WAIT_PID_FLAG = "--restart-wait-pid"
WAIT_TIMEOUT_SEC = 30.0     # updater 헬퍼의 메인 종료 대기와 같은 값


# ─── 실행 명령 ────────────────────────────────────────────────────────────
def _strip_wait_flag(args: list[str]) -> list[str]:
    """이전 재시작에서 붙은 대기 인자를 걷어낸다 (재시작을 거듭해도 쌓이지 않게)."""
    return [a for a in args if not a.startswith(WAIT_PID_FLAG)]


def relaunch_command(pid: Optional[int] = None) -> list[str]:
    """새 프로세스를 띄울 명령. 끝에 `--restart-wait-pid=<pid>` 가 붙는다.

    - 설치본(frozen): 실행 파일 하나. 앱은 명령줄 인자를 받지 않으므로 넘길 것이
      없고, 예전 macOS 가 Finder 실행 때 붙이던 `-psn_…` 같은 인자를 옮기지 않는다.
      macOS 도 `open` 이 아니라 .app 안의 바이너리를 직접 실행한다 — `open` 은 같은
      앱이 아직 떠 있으면 새로 띄우지 않고 그 앱을 앞으로 가져올 뿐이고, 자동 실행
      (LaunchAgent) 도 같은 바이너리를 직접 실행해 검증된 경로다.
    - 개발 모드: sys.orig_argv 의 인터프리터 옵션·`-m pinstock`·스크립트 경로를 그대로.
      실행 파일 자리만 sys.executable 로 바꾼다(orig_argv[0] 은 입력한 그대로의
      'python' 일 수 있어 PATH 에 따라 다른 인터프리터가 잡힌다).
    """
    pid = os.getpid() if pid is None else pid
    if getattr(sys, "frozen", False):
        cmd = [sys.executable]
    else:
        rest = list(getattr(sys, "orig_argv", [])[1:]) or ["-m", "pinstock"]
        cmd = [sys.executable, *_strip_wait_flag(rest)]
    return [*cmd, f"{WAIT_PID_FLAG}={pid}"]


def launch_relaunch() -> None:
    """새 프로세스를 분리 실행. 호출 직후 메인 앱은 종료해야 한다.

    stdout/stderr 는 물려준다 — 설치본은 원래 콘솔이 없고, 개발 모드에서는 같은
    터미널에 로그가 이어서 찍혀야 재시작 뒤 동작을 볼 수 있다.
    Windows 의 CREATE_NEW_PROCESS_GROUP 은 updater 헬퍼와 같은 이유(부모가 끝나도
    살아남게), POSIX 의 start_new_session 은 setsid() 로 부모 세션에서 떼어 낸다.
    """
    kwargs: dict = {
        "cwd": os.getcwd(),
        "close_fds": True,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        kwargs["creationflags"] = CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(relaunch_command(), **kwargs)


# ─── 새 프로세스 쪽: 옛 프로세스 종료 대기 ─────────────────────────────────
def pop_wait_pid_arg(argv: list[str]) -> Optional[int]:
    """argv 에서 `--restart-wait-pid=<pid>` 를 떼어 내고(제자리 수정) pid 를 돌려준다.

    QApplication(sys.argv) 보다 먼저 불러야 한다. 값이 숫자가 아니거나 0 이하면
    인자만 지우고 None — POSIX 에서 kill(0)/kill(-1) 은 프로세스 그룹·전체를
    가리켜 '아직 살아 있음' 으로 잘못 읽힌다.
    """
    pid: Optional[int] = None
    keep = []
    for i, a in enumerate(argv):
        if i > 0 and a.startswith(WAIT_PID_FLAG + "="):
            try:
                v = int(a.split("=", 1)[1])
                pid = v if v > 0 else None
            except ValueError:
                pid = None
            continue
        keep.append(a)
    argv[:] = keep
    return pid


def wait_for_exit(pid: int, timeout: float = WAIT_TIMEOUT_SEC) -> bool:
    """pid 프로세스가 끝나면 True, timeout 안에 안 끝나면 False."""
    if pid <= 0 or pid == os.getpid():
        return True
    if sys.platform == "win32":
        return _wait_for_exit_windows(pid, timeout)
    return _wait_for_exit_posix(pid, timeout)


def _wait_for_exit_posix(pid: int, timeout: float) -> bool:
    # macOS 는 PID 를 순차 증가로 배정해 몇 초 안에 재사용될 일이 없다.
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            # 다른 사용자의 프로세스가 그 PID 를 물려받았다 = 옛 프로세스는 끝났다
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def _wait_for_exit_windows(pid: int, timeout: float) -> bool:
    """프로세스 핸들을 열어 WaitForSingleObject 로 기다린다 (폴링 없음).

    Windows 는 PID 를 금방 재사용한다. 옛 프로세스가 새 프로세스의 기동(수백 ms)
    보다 먼저 끝나 그 PID 를 다른 프로그램이 가져갔다면, 그 프로그램을 30초
    기다리다 재시작이 취소된다. 그래서 대상의 실행 파일 경로가 나와 다르면 이미
    끝난 것으로 본다. sys.executable 이 아니라 '지금 실제로 돌고 있는 이미지'
    끼리 비교한다 — 개발 모드의 venv python.exe 는 기본 인터프리터를 자식으로
    띄우는 런처라, 실제 PID 의 이미지는 sys.executable 과 다르다.
    """
    import ctypes
    from ctypes import wintypes

    SYNCHRONIZE = 0x00100000
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    WAIT_OBJECT_0 = 0x00000000

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    k32.CloseHandle.restype = wintypes.BOOL
    k32.GetCurrentProcess.argtypes = ()
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    k32.QueryFullProcessImageNameW.restype = wintypes.BOOL

    def _image(handle) -> Optional[str]:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        if k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.normcase(buf.value)
        return None

    handle = k32.OpenProcess(
        SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    if not handle:
        return True      # 그런 PID 가 없다 = 이미 끝났다
    try:
        target, mine = _image(handle), _image(k32.GetCurrentProcess())
        if target and mine and target != mine:
            return True
        return k32.WaitForSingleObject(handle, int(timeout * 1000)) == WAIT_OBJECT_0
    finally:
        k32.CloseHandle(handle)
