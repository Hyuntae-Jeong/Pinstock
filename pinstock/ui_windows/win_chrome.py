"""Win11 DWM 자동 테두리/그림자/둥근 모서리 차단.

Windows 11 은 top-level 윈도우에 시스템이 직접 얇은 테두리와 둥근 모서리를 그린다.
프레임리스 + 투명 배경으로 직접 카드를 그리는 위젯에서는 이게 카드 모서리 바깥에
회색 실선으로 남아, 우리가 그린 둥근 모서리와 어긋나 보인다.

마스터 위젯 쪽에서만 쓰던 것을 모든 떠 있는 창이 함께 쓰도록 여기로 옮겼다.
"""

import sys


def disable_win11_dwm_chrome(hwnd: int) -> None:
    """Windows 가 top-level 윈도우에 기본 적용하는
    그림자(드롭 섀도) · 얇은 테두리 · 둥근 모서리 효과를 끈다.

    hide → show 사이에 Windows 가 per-window DWM 속성을 기본값으로 되돌리는 경우가
    있고, setWindowFlag 로 윈도우가 재생성되면 hwnd 자체가 바뀐다. 그래서 한 번만
    부르지 말고 showEvent 마다 다시 적용한다 — 호출 비용은 매우 가볍다.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        # 1. 윈도우 클래스의 CS_DROPSHADOW 비트 제거 → 시스템 드롭 섀도 차단.
        #    (이 클래스의 다른 Qt 윈도우들도 함께 그림자 빠짐 — 일반적으로 무난)
        GCL_STYLE = -26
        CS_DROPSHADOW = 0x00020000
        user32 = ctypes.windll.user32
        try:
            get_long = user32.GetClassLongPtrW
            set_long = user32.SetClassLongPtrW
        except AttributeError:
            get_long = user32.GetClassLongW
            set_long = user32.SetClassLongW
        get_long.restype = ctypes.c_size_t
        set_long.restype = ctypes.c_size_t
        cur = get_long(hwnd, GCL_STYLE)
        if cur & CS_DROPSHADOW:
            set_long(hwnd, GCL_STYLE, cur & ~CS_DROPSHADOW)

        # 2. DWM NC 렌더링 정책 비활성화 — 일부 환경에서 추가 그림자/테두리 차단.
        # DWMWA_NCRENDERING_POLICY = 2, DWMNCRP_DISABLED = 1
        v = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 2, ctypes.byref(v), ctypes.sizeof(v)
        )
        # 3. Win11 둥근 모서리 끔
        # DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_DONOTROUND = 1
        v = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(v), ctypes.sizeof(v)
        )
        # 4. Win11 테두리 색 없음
        # DWMWA_BORDER_COLOR = 34, DWMWA_COLOR_NONE = 0xFFFFFFFE
        v = ctypes.c_uint32(0xFFFFFFFE)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 34, ctypes.byref(v), ctypes.sizeof(v)
        )
    except Exception:
        pass
