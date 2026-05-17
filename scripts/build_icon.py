"""SVG 앱 아이콘을 macOS .icns / Windows .ico 로 변환.

사용법:
    python scripts/build_icon.py            # 두 포맷 모두 생성
    python scripts/build_icon.py --macos    # .icns 만
    python scripts/build_icon.py --windows  # .ico 만

원본 SVG (`pinstock_icon.svg`) 는 정사각형 viewBox 이므로 그대로 렌더링한다.
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication


REPO = Path(__file__).resolve().parent.parent
SVG = REPO / "pinstock_icon.svg"
ASSETS = REPO / "assets"
OUT_ICNS = ASSETS / "Pinstock.icns"
OUT_ICO = ASSETS / "Pinstock.ico"

# macOS .icns 가 요구하는 iconset 파일 이름과 픽셀 크기
ICNS_SIZES = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]

# Windows .ico 는 다중 해상도 PNG 를 한 파일에 담는 포맷
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


def render_square(renderer: QSvgRenderer, size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    renderer.render(painter)
    painter.end()
    return img


def build_icns(renderer: QSvgRenderer) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "Pinstock.iconset"
        iconset.mkdir()
        for fname, size in ICNS_SIZES:
            img = render_square(renderer, size)
            img.save(str(iconset / fname), "PNG")
        OUT_ICNS.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(OUT_ICNS)],
            check=True,
        )
    print(f"Wrote {OUT_ICNS}")


def build_ico(renderer: QSvgRenderer) -> None:
    OUT_ICO.parent.mkdir(parents=True, exist_ok=True)
    images = [render_square(renderer, s) for s in ICO_SIZES]
    # QImage.save 는 한 번에 하나만 저장 가능하므로 Pillow 가 없으면 PNG 들을
    # 모아서 ImageWriter 기반으로 ICO 를 직접 쓴다. PyQt 의 ICO 핸들러가
    # 다중 해상도를 지원하지 않을 수 있으므로 가장 큰 사이즈 한 장만 .ico 로
    # 저장 (Windows 가 자동 스케일링 해줌).
    largest = images[-1]
    largest.save(str(OUT_ICO), "ICO")
    print(f"Wrote {OUT_ICO} (single-size; for multi-resolution install Pillow)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--macos", action="store_true", help=".icns 만 생성")
    parser.add_argument("--windows", action="store_true", help=".ico 만 생성")
    args = parser.parse_args()
    do_macos = args.macos or not args.windows
    do_windows = args.windows or not args.macos

    if not SVG.is_file():
        print(f"원본 SVG 를 찾을 수 없습니다: {SVG}", file=sys.stderr)
        return 1

    _ = QApplication.instance() or QApplication([])
    renderer = QSvgRenderer(str(SVG))

    if do_macos:
        if sys.platform != "darwin":
            print("경고: .icns 빌드는 macOS 의 iconutil 이 필요합니다. 건너뜁니다.")
        else:
            build_icns(renderer)
    if do_windows:
        build_ico(renderer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
