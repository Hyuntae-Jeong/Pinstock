# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 빌드 스펙 — macOS / Windows 공용.

빌드 방법:
    .venv/bin/pyinstaller pinstock.spec --noconfirm
산출물:
    macOS   → dist/Pinstock.app
    Windows → dist/Pinstock/Pinstock.exe (그리고 폴더 자체)
"""

import sys
from pathlib import Path

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

APP_NAME = "Pinstock"
APP_VERSION = "0.1.0"
BUNDLE_ID = "com.hyuntae.pinstock"

# spec 파일이 위치한 디렉토리를 프로젝트 루트로 본다.
ROOT = Path(SPECPATH).resolve()

ICON_MAC = str(ROOT / "assets" / "Pinstock.icns")
ICON_WIN = str(ROOT / "assets" / "Pinstock.ico")

# 런타임에 필요한 데이터 파일 — 메뉴바 SVG 아이콘들 + 앱 아이콘(.ico/.icns)
datas = [
    (str(ROOT / "icons"), "icons"),
    (str(ROOT / "assets"), "assets"),
]

block_cipher = None

a = Analysis(
    [str(ROOT / "run_pinstock.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,         # GUI 앱: 터미널 창 없음
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_MAC if IS_MAC else (ICON_WIN if IS_WIN else None),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)

if IS_MAC:
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=ICON_MAC,
        bundle_identifier=BUNDLE_ID,
        version=APP_VERSION,
        info_plist={
            # 메뉴바 전용 앱 — Dock 에 아이콘/이름 안 띄움
            "LSUIElement": False,
            # Retina 대응
            "NSHighResolutionCapable": True,
            # 시스템 다크 모드 따라가게
            "NSRequiresAquaSystemAppearance": False,
            # 사용자에게 보이는 메타데이터
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            # 네이버 금융 API 접근 — App Sandbox 안 쓰지만 명시
            "NSAppTransportSecurity": {
                "NSAllowsArbitraryLoads": False,
                "NSExceptionDomains": {
                    "naver.com": {
                        "NSIncludesSubdomains": True,
                        "NSTemporaryExceptionAllowsInsecureHTTPLoads": False,
                    },
                },
            },
        },
    )
