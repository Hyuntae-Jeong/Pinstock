"""PyInstaller 진입점.

`python -m pinstock` 와 똑같이 동작하지만, PyInstaller 가 `__main__.py` 의
상대 import (`from .core.storage import ...`) 를 해석하지 못하는 문제를
피하기 위해 top-level 스크립트에서 패키지 함수를 호출한다.

개발 시에는 여전히 `python -m pinstock` 으로 실행하면 된다.
"""

from pinstock.__main__ import main


if __name__ == "__main__":
    main()
