"""저장한 파일을 OS 파일 관리자(macOS Finder / Windows 파일 탐색기)에서 보여 주기.

폴더만 여는 게 아니라 그 파일을 선택한 채로 연다 — 같은 폴더에 날짜만 다른
pinstock_holdings_*.xlsx 가 쌓여 있어도 방금 저장한 파일이 바로 눈에 띈다.
"""

import os
import subprocess
import sys


def reveal_in_file_manager(path: str) -> bool:
    """path 를 선택한 채로 파일 관리자 창을 띄운다. 띄우지 못했으면 False.

    파일 관리자는 따로 도는 프로세스라 끝나기를 기다리지 않는다(Popen).
    """
    path = os.path.abspath(path)
    try:
        if sys.platform == "darwin":
            # -R(reveal): 담긴 폴더를 열고 그 파일을 선택한다
            subprocess.Popen(
                ["open", "-R", path],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif sys.platform == "win32":
            # explorer 는 /select, 뒤를 자기 방식으로 파싱해서, 경로만 따옴표로 감싼
            # 명령줄 문자열이 공백·쉼표가 든 경로에서도 가장 확실하다. 성공해도
            # 종료 코드가 1 이라 결과는 보지 않는다.
            subprocess.Popen(
                f'explorer /select,"{os.path.normpath(path)}"',
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            return False
    except OSError:
        return False
    return True
