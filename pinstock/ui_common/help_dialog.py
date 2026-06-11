"""앱 내 도움말 다이얼로그.

좌측 카테고리 리스트 → 우측 본문 HTML. 콘텐츠는 모듈 상수에 임베드되어
있어 외부 리소스 없이 동작한다 (PyInstaller 번들에서도 그대로 표시됨).
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QTextBrowser, QPushButton,
)

from ..ui_windows.theme import C, DIALOG_STYLE


# ─── 카테고리별 본문 ─────────────────────────────────────────────────────────
# 각 항목 = (sidebar_label, body_h2, body_html)
# sidebar_label 은 좌측 리스트용 짧은 라벨, body_h2 는 우측 본문 상단 헤더.
# 두 텍스트를 분리해야 좌측은 컴팩트하게, 우측은 풍부한 제목으로 표시할 수 있다.
HELP_SECTIONS: list[tuple[str, str, str]] = [
    (
        "🚀  시작하기",
        "🚀 시작하기",
        """
        <p>Pinstock 은 한국·미국 주식의 현재가를 데스크탑에 항상 띄워두는 미니 위젯입니다.</p>
        <ul>
            <li><b>Windows</b>: 화면 우상단에 종목별 위젯이 세로로 정렬됩니다. 드래그로 어디든 옮길 수 있어요.</li>
            <li><b>macOS</b>: 메뉴바의 Pinstock 아이콘을 클릭하면 종목 리스트가 팝오버로 펼쳐집니다.</li>
        </ul>
        <p>시세는 5초마다, 미니 차트는 60초마다 자동 갱신됩니다.
        (네이버 금융 API 사용 / 인터넷 연결 필요)</p>
        """,
    ),
    (
        "➕  종목 관리",
        "➕ 종목 추가 · 수정 · 삭제",
        """
        <p>트레이 아이콘 우클릭 → <b>종목 추가</b> 로 다이얼로그를 엽니다.</p>
        <ul>
            <li><b>한국 주식</b>: <b>종목명</b> 으로 검색해 후보 드롭다운에서 선택하거나,
                6자리 종목 코드를 직접 입력
                (예: <code>삼성전자</code>, <code>카카오</code>, <code>005930</code>)</li>
            <li><b>미국 주식</b>: 시장 선택을 <i>미국</i> 으로 바꾸고
                <b>영문 티커 또는 종목명</b> 입력
                (예: <code>AAPL</code>, <code>Apple</code>, <code>Tesla</code>)</li>
            <li>평단가와 수량을 함께 입력하면 평가손익이 자동 계산됩니다.</li>
        </ul>
        <p>수정·삭제는 위젯/팝오버의 종목 행을 <b>우클릭</b> 하면 메뉴가 뜹니다.</p>
        """,
    ),
    (
        "📋  일괄 편집",
        "📋 종목 관리 (일괄 편집)",
        """
        <p>트레이 메뉴 → <b>종목 관리</b> 에서 모든 종목을 한 화면에서 정리할 수 있습니다.</p>
        <ul>
            <li><b>드래그</b> 로 종목 순서 변경</li>
            <li><b>표시 토글</b> 로 특정 종목을 숨김 처리 (데이터는 유지)</li>
            <li><b>📊 평가손익 정렬</b> 로 손익 내림차순으로 자동 정렬</li>
            <li><b>확인</b> 을 눌러야 변경사항이 저장됩니다.
                <b>취소</b> 면 원래대로 복원됩니다.</li>
        </ul>
        """,
    ),
    (
        "⭐  관심종목",
        "⭐ 관심종목 (보유와 별개로 지켜보기)",
        """
        <p>관심종목은 <b>보유 종목과 완전히 별개</b>인 워치리스트입니다. 평단가·수량·손익
        개념이 없고 <b>일봉 기준</b> 시세만 가볍게 따라갑니다. 같은 종목을 보유와 관심에
        동시에 둘 수도 있어요.</p>
        <ul>
            <li>트레이(또는 마스터 위젯) 메뉴 → <b>⭐ 관심종목 추가</b> / <b>⭐ 관심종목 관리</b></li>
            <li><b>⭐ 관심종목 켜기/끄기</b> 로 관심 위젯 전체를 보유와 별개로 표시/숨김</li>
            <li><b>📐 관심 위치 초기화</b> 로 흩어진 그룹 위치를 정렬</li>
        </ul>

        <h3>🏷 태그로 묶어 보기</h3>
        <ul>
            <li>종목마다 태그를 <b>1개</b> 달 수 있고, 같은 태그끼리 그룹으로 모여 표시됩니다.</li>
            <li>관리창의 <b>🏷 태그 관리</b> 에서 태그 이름·색상을 추가·수정·삭제합니다.</li>
            <li><b>태그 필터</b>(관리창 상단)로 특정 태그의 종목만 골라 볼 수 있습니다.</li>
            <li><b>태그 삭제</b> 시 그 태그가 달린 종목을 <b>함께 삭제</b>할지
                <b>태그만 해제</b>(종목 유지)할지 물어봅니다.</li>
        </ul>

        <h3>📥 보유종목 동기화</h3>
        <p>관리창의 <b>📥 보유종목 동기화</b> 는 현재 보유 중인 종목 가운데 <b>태그가 없는 것</b>을
        <b>'보유중'</b> 태그로 관심종목에 한 번에 추가합니다. 이미 다른 태그가 달린 종목은 그대로
        둡니다(옮기지 않음).</p>

        <h3>🗑 여러 개 한 번에 정리</h3>
        <ul>
            <li>행 왼쪽 <b>체크박스</b>로 여러 종목을 고른 뒤 <b>🗑 삭제</b> 로 한꺼번에 삭제합니다.</li>
            <li><b>선택</b> 열 <b>헤더의 체크박스</b>로 (현재 필터에 보이는) 전체를 선택/해제합니다.</li>
            <li><b>표시 토글</b>로 특정 종목만 숨김(데이터 유지). <b>확인</b> 을 눌러야 저장됩니다.</li>
        </ul>

        <h3>🪟 태그 그룹 위젯 (Windows)</h3>
        <ul>
            <li>관심종목은 화면에 <b>태그별 그룹</b>으로 떠 있습니다. 헤더(색 점 + 태그명 + 개수)를
                <b>클릭</b>하면 종목들이 아래로 펼쳐집니다.</li>
            <li><b>📌 고정</b>하면 마우스가 벗어나도 펼친 상태가 유지됩니다.</li>
        </ul>

        <h3>📈 마우스 올리면 커지는 일봉 차트 (Windows)</h3>
        <ul>
            <li>관심종목 행의 미니 차트에 <b>마우스를 올리면</b> 최근 <b>3개월 일봉</b> 차트가 크게 떠
                <b>5 · 20 · 60일 이동평균선</b>과 함께 보입니다.</li>
            <li>이동평균선 표시 여부는 관리창의 <b>확대 차트 이동평균선</b> 체크박스(5·20·60일)로 켜고 끕니다.</li>
        </ul>
        """,
    ),
    (
        "📊  포트폴리오",
        "📊 포트폴리오 요약",
        """
        <p>마스터 위젯(Windows) / 팝오버 상단(macOS) 에서 전체 자산 현황을 확인할 수 있습니다.</p>
        <ul>
            <li><b>총 매입금액 · 평가금액 · 평가손익 · 수익률</b> 이 한눈에 표시됩니다.</li>
            <li>한국·미국 종목 합산이며, 미국 종목은 현재 환율로 원화 환산됩니다.</li>
        </ul>
        """,
    ),
    (
        "📈  차트",
        "📈 차트 보기",
        """
        <p>종목 옆 미니 차트로 시세 흐름을 빠르게 볼 수 있습니다.</p>
        <ul>
            <li><b>장중</b>: 당일 분봉 sparkline (실시간 흐름)</li>
            <li><b>장 외 시간 · 주말 · 공휴일</b>: 최근 30일 일봉 캔들 (자동 폴백)</li>
        </ul>
        <p>상승 시 빨강, 하락 시 파랑으로 표시됩니다 (한국식 색상).</p>
        """,
    ),
    (
        "📤  Excel 입출력",
        "📤 Excel 내보내기 · 📥 가져오기",
        """
        <p>트레이 메뉴에서 종목 데이터를 Excel(.xlsx) 로 백업하거나
        다른 PC 로 옮길 수 있습니다.</p>
        <ul>
            <li><b>내보내기</b>: 현재 종목 전체를 Excel 파일로 저장</li>
            <li><b>가져오기</b>: Excel 파일에서 종목을 불러옴 (덮어쓰기 / 추가 모드 선택)</li>
        </ul>
        <p>컴퓨터를 바꾸거나 백업이 필요할 때 유용합니다.</p>
        """,
    ),
    (
        "🔄  자동 업데이트",
        "🔄 자동 업데이트",
        """
        <p>새 버전이 나오면 <b>앱 정보</b> 창의 <b>🔄 업데이트 확인</b> 버튼으로
        바로 다운로드·교체·재시작이 됩니다.</p>
        <ul>
            <li>앱 시작 시 자동으로 새 버전을 한 번 확인합니다.</li>
            <li>새 버전이 있으면 OS 알림 토스트로 안내됩니다.</li>
            <li>토스트를 클릭하면 업데이트 다이얼로그가 바로 열립니다.</li>
            <li>수동 확인은 트레이 메뉴 → <b>앱 정보</b> → <b>업데이트 확인</b>
                순서로 가능합니다.</li>
        </ul>
        """,
    ),
    (
        "🚀  자동 실행",
        "🚀 시작 시 자동 실행",
        """
        <p>PC를 켜거나 다시 시작했을 때 Pinstock 이 자동으로 뜨도록 설정할 수 있습니다.</p>
        <ul>
            <li>트레이(메뉴바) 메뉴 → <b>🚀 시작 시 자동 실행</b> 을 체크하면 켜집니다.</li>
            <li>다시 누르면 해제됩니다. 별도 관리자 권한은 필요 없습니다.</li>
            <li>Windows 는 현재 사용자 로그인 시, macOS 는 로그인 항목으로 실행됩니다.</li>
            <li>설치된 정식 빌드에서만 표시됩니다 (개발 모드 실행 시에는 숨겨짐).</li>
        </ul>
        """,
    ),
    (
        "🖱️  위젯 조작",
        "🖱️ 위젯 · 팝오버 조작법",
        """
        <h3>Windows 위젯</h3>
        <ul>
            <li><b>종목 위젯 좌클릭</b>: 평단가·수량·평가손익 등 상세 정보 펼치기
                (5초 후 자동 축소)</li>
            <li><b>종목 위젯 드래그</b>: 위젯을 원하는 위치로 이동</li>
            <li><b>종목 위젯 우클릭</b>: 해당 종목 수정 / 삭제 메뉴</li>
            <li><b>마스터 위젯 우클릭</b>: 트레이 메뉴와 동일한 전체 메뉴
                (종목 추가·관리·Excel·정렬 등)</li>
            <li><b>트레이 아이콘 좌클릭</b>: 모든 위젯 표시 / 숨김 토글</li>
            <li><b>트레이 아이콘 우클릭</b>: 전체 메뉴</li>
        </ul>
        <h3>macOS 팝오버</h3>
        <ul>
            <li><b>메뉴바 아이콘 좌클릭</b>: 팝오버 펼침 / 접기</li>
            <li><b>메뉴바 아이콘 우클릭</b>: 종목 추가·관리·Excel·앱 정보 등
                컨텍스트 메뉴</li>
            <li>화면 <b>상단 왼쪽 앱 메뉴바</b>(종목/파일/보기/도움말…) 에도
                같은 항목들이 있어요. 메뉴바 아이콘이 안 보일 때 백업 진입로로 활용하세요.</li>
            <li><b>팝오버의 종목 행 좌클릭</b>: 상세 정보 펼치기</li>
            <li><b>팝오버의 종목 행 우클릭</b>: 수정 / 삭제 메뉴</li>
            <li><b>팝오버 밖 클릭</b>: 자동으로 닫힘</li>
        </ul>
        """,
    ),
]


def _content_default_style() -> str:
    """QTextBrowser.document().setDefaultStyleSheet() 로 적용되어
    본문 HTML 의 색·간격을 다크 테마에 맞춘다."""
    return f"""
        body {{
            color: {C['text']};
            font-size: 14px;
            line-height: 1.6;
        }}
        h2 {{
            color: {C['blue']};
            font-size: 18px;
            margin-top: 0;
            margin-bottom: 10px;
        }}
        h3 {{
            color: {C['blue']};
            font-size: 15px;
            margin-top: 14px;
            margin-bottom: 6px;
        }}
        ul {{ margin-left: 16px; padding-left: 0; }}
        li {{ margin-bottom: 7px; }}
        code {{
            background: {C['bg2']};
            color: {C['blue']};
            padding: 2px 5px;
            border-radius: 4px;
            font-family: 'Consolas', 'Menlo', monospace;
        }}
    """


class HelpDialog(QDialog):
    """좌측 카테고리 + 우측 본문의 단일 도움말 모달."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pinstock 도움말")
        self.resize(780, 560)
        self.setStyleSheet(DIALOG_STYLE)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(10)

        # 좌측: 카테고리 리스트 — 짧은 라벨로 통일해 컴팩트하게.
        # 가로 스크롤은 사이드바 UX 에 어울리지 않으므로 끄고, 라벨이
        # 길어질 경우엔 ellipsis(…) 로 잘려 표시되도록 한다.
        self.category_list = QListWidget()
        self.category_list.setFixedWidth(200)
        self.category_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.category_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.category_list.setHorizontalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.category_list.setStyleSheet(self._list_style())
        for sidebar_label, _h2, _body in HELP_SECTIONS:
            QListWidgetItem(sidebar_label, self.category_list)

        # 우측: 본문
        self.content_view = QTextBrowser()
        self.content_view.setOpenExternalLinks(True)
        self.content_view.document().setDefaultStyleSheet(_content_default_style())
        self.content_view.setStyleSheet(
            f"QTextBrowser {{ background: {C['bg2']}; color: {C['text']}; "
            f"border: 1px solid {C['border']}; border-radius: 8px; padding: 14px; }}"
        )

        body_row = QHBoxLayout()
        body_row.setSpacing(10)
        body_row.addWidget(self.category_list)
        body_row.addWidget(self.content_view, 1)
        root.addLayout(body_row, 1)

        # 하단 닫기 버튼
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_close = QPushButton("닫기")
        self.btn_close.setProperty("flat", "true")
        self.btn_close.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_close)
        root.addLayout(btn_row)

        # 시그널 + 초기 선택
        self.category_list.currentRowChanged.connect(self._show_section)
        self.category_list.setCurrentRow(0)

    def _show_section(self, row: int):
        if not (0 <= row < len(HELP_SECTIONS)):
            return
        _sidebar, body_title, body_html = HELP_SECTIONS[row]
        html = f"<h2>{body_title}</h2>\n{body_html}"
        self.content_view.setHtml(html)
        self.content_view.verticalScrollBar().setValue(0)

    def _list_style(self) -> str:
        return f"""
            QListWidget {{
                background: {C['bg2']};
                color: {C['text']};
                border: 1px solid {C['border']};
                border-radius: 8px;
                padding: 4px;
                font-size: 13px;
                outline: 0;
            }}
            QListWidget::item {{
                padding: 8px 10px;
                border-radius: 5px;
            }}
            QListWidget::item:hover {{ background: {C['surface']}; }}
            QListWidget::item:selected {{
                background: {C['blue']};
                color: {C['bg']};
                font-weight: bold;
            }}
        """
