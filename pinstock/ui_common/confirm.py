"""확인 팝업 — 앱 테마로 통일한 커스텀 확인창.

QMessageBox 를 쓰면 세 가지가 딸려 온다. question() 이 자동으로 붙이는 큼직한
물음표 아이콘, Yes/No 영문 버튼(Qt 한국어 번역 파일을 함께 싣지 않는다), 그리고
플랫폼 버튼박스가 정해 버리는 배치다. 아이콘은 "이건 질문입니다" 말고 알려 주는
게 없고, 배치는 손댈 수가 없어 macOS 에서 두 버튼이 창 양끝으로 벌어졌다.

그래서 QDialog 로 직접 만든다. 앱의 DIALOG_STYLE 을 그대로 입혀 버튼 색·모양이
다른 창과 같고, 버튼들은 같은 폭으로 가운데에 붙는다. 라벨 길이가 제각각이어도
(삭제 / 가져오기 / 태그만 해제 …) 창이 한쪽으로 기울지 않는다.

제목 표시줄도 뗀다(FramelessDialog). 확인창의 제목("삭제 확인")은 본문("… 삭제할
까요?")과 같은 말을 두 번 하는 것이라 없어도 잃는 정보가 없다. 대신 닫기 버튼이
사라져 취소·Esc 가 유일한 탈출구이고, 제목줄이 없으니 빈 곳을 끌어서 옮긴다.
"""

from PyQt6.QtWidgets import QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QWidget
from PyQt6.QtCore import Qt

from ..ui_windows.theme import C, DIALOG_STYLE
from .frameless import FramelessDialog, FRAMELESS_CARD_STYLE

# DIALOG_STYLE 의 QLabel 은 폼 라벨용(작은 회색)이라 본문에는 따로 준다.
_EXTRA_STYLE = f"""
QLabel#confirmMsg {{
    color: {C['text']};
    font-size: 14px;
    font-weight: bold;
}}
QLabel#confirmSub {{
    color: {C['subtext']};
    font-size: 12px;
}}
"""

_BTN_MIN_W = 104     # '삭제'·'취소' 같은 두 글자도 누르기 좋은 폭
_BTN_GAP = 8
_DIALOG_MIN_W = 330


class ConfirmDialog(FramelessDialog):
    """가운데에 같은 폭 버튼을 나란히 놓는 확인창.

    buttons 는 (key, label, primary) 를 표시 순서대로 받는다. primary 인 것만
    파랑이고 나머지는 회색이다. 취소는 호출측이 넣지 않아도 맨 왼쪽에 자동으로
    붙고, 취소·Esc·창 닫기는 모두 result_key() 가 None 이다 — 어떻게 빠져나가든
    '아무 일도 하지 않음'이 기본값이다.
    """

    def __init__(self, parent: QWidget | None, title: str, text: str,
                 informative: str = "", buttons: tuple = (),
                 cancel_label: str = "취소", default_key=None):
        # 확인창은 자식 창을 띄우지 않으므로 '항상 위'를 켠다 — 늘 위에 떠 있는
        # 종목 위젯 뒤로 숨으면 모달이라 앱이 멈춘 것처럼 보인다.
        super().__init__(parent, resizable=False, stay_on_top=True)
        self._result_key = None
        self.setWindowTitle(title)     # 화면엔 안 보이지만 접근성용으로 남긴다
        self.setStyleSheet(DIALOG_STYLE + FRAMELESS_CARD_STYLE + _EXTRA_STYLE)
        self.setModal(True)

        root = QVBoxLayout(self.card)
        root.setContentsMargins(26, 24, 26, 20)
        root.setSpacing(12)

        msg = QLabel(text)
        msg.setObjectName("confirmMsg")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setWordWrap(True)
        root.addWidget(msg)

        if informative:
            sub = QLabel(informative)
            sub.setObjectName("confirmSub")
            # 여러 줄(주로 항목 나열)은 왼쪽 정렬이어야 줄머리가 맞는다.
            sub.setAlignment(Qt.AlignmentFlag.AlignLeft if "\n" in informative
                             else Qt.AlignmentFlag.AlignCenter)
            sub.setWordWrap(True)
            root.addWidget(sub)

        root.addSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(_BTN_GAP)
        row.addStretch()
        self._buttons: list[tuple] = []
        for key, label, primary in ((None, cancel_label, False), *buttons):
            btn = QPushButton(label)
            if not primary:
                btn.setProperty("flat", "true")
            btn.setAutoDefault(False)
            btn.clicked.connect(lambda _checked, k=key: self._pick(k))
            row.addWidget(btn)
            self._buttons.append((key, btn))
        row.addStretch()
        root.addLayout(row)

        width = max([_BTN_MIN_W] + [b.sizeHint().width() for _, b in self._buttons])
        for _key, btn in self._buttons:
            btn.setFixedWidth(width)

        # Enter 로 눌리는 버튼. 지정이 없으면 취소다 — 무심코 친 Enter 가
        # 되돌릴 수 없는 동작을 실행해서는 안 된다.
        focus = next((b for k, b in self._buttons if k == default_key),
                     self._buttons[0][1])
        focus.setAutoDefault(True)
        focus.setDefault(True)
        focus.setFocus()

        self.setMinimumWidth(_DIALOG_MIN_W)

    def _pick(self, key):
        self._result_key = key
        if key is None:
            self.reject()
        else:
            self.accept()

    def result_key(self):
        """누른 버튼의 key. 취소·Esc·창 닫기는 None."""
        return self._result_key


def confirm(parent: QWidget | None, title: str, text: str, ok_label: str = "확인",
            informative: str = "", cancel_label: str = "취소",
            default_ok: bool = False) -> bool:
    """예/아니오 확인창. 사용자가 ok_label 버튼을 눌렀을 때만 True."""
    dlg = ConfirmDialog(parent, title, text, informative,
                        buttons=(("ok", ok_label, True),),
                        cancel_label=cancel_label,
                        default_key="ok" if default_ok else None)
    dlg.exec()
    return dlg.result_key() == "ok"


def confirm_delete(parent: QWidget | None, title: str, text: str,
                   ok_label: str = "삭제", informative: str = "") -> bool:
    """삭제 확인 — Enter 는 취소라 실수로 지워지지 않는다."""
    return confirm(parent, title, text, ok_label=ok_label, informative=informative)


def choose(parent: QWidget | None, title: str, text: str, options: list,
           informative: str = "", default=None):
    """선택지가 셋 이상인 확인창 (예: 계좌 삭제 — 이동 / 함께 삭제 / 취소).

    options 는 (key, label) 을 표시 순서대로. default 로 준 key 가 파랑(권장)
    버튼이 되고 Enter 도 그 버튼을 누른다. 취소·Esc 는 None 을 돌려준다.
    """
    keys = [k for k, _label in options]
    primary = default if default in keys else keys[-1]
    dlg = ConfirmDialog(parent, title, text, informative,
                        buttons=tuple((k, label, k == primary) for k, label in options),
                        default_key=primary)
    dlg.exec()
    return dlg.result_key()
