# ⚾ KBO 리그 종합 대시보드

KBO(한국프로야구) 공식 기록을 직접 수집해서 만든 Streamlit 대시보드입니다. 팀·선수 현재 시즌 기록은 물론, 프로야구 원년(1982년)부터 지금까지의 역대 기록과 자체 계산한 세이버메트릭스(WAR, wRC+, FIP 등)까지 볼 수 있습니다.

## 주요 기능

- **팀 순위·추이**: 실시간 순위표, 일자별 순위/승률 변화 그래프
- **팀 기록 비교 / 종합 분석 / 심화 분석**: 피타고리안 기대승률, 홈·원정 스플릿, 상대전적, 매직넘버, 파크팩터, 클러치 지수 등
- **선수 기록 / 선수 검색**: 선수 개인 기록, 리그 내 백분위 프로필, 최근 폼(기간별 활약), 그리고 한 선수를 검색해 기본기록·통산기록(시즌별+합계)·경기별/일자별 기록·상황별 기록(홈원정·상대팀별)·수상 경력·1군 등록일수까지 한 번에 조회
- **세이버메트릭스 / 과거 세이버메트릭스**: KBO 공식 기본기록만으로 직접 계산한 wOBA·wRC+·FIP·WAR 등 (연도별로도 조회 가능)
- **역대 기록(1982~)**: 연도별 최종 순위, 역대 팀/선수 기록, 리그 득점 환경 트렌드, 역대 단일시즌 최고 기록, 통산 기록
- **경기 결과**: 경기별 스코어와 선발/승패투수 등 세부 정보

## 데이터 출처

- **KBO 공식 홈페이지** (koreabaseball.com): 순위, 팀/선수 기본기록, 수비/주루 기록, 수상 현황, 선수 등록일수 — 공식 API가 없어 HTML을 직접 파싱합니다.
- **네이버 스포츠** (api-gw.sports.naver.com): 경기 일정/결과, 경기별 박스스코어 — 로그인이나 봇 차단 없는 공개 JSON API를 사용합니다.
- Statiz·MyKBOStats는 로그인/Cloudflare 봇 차단이 있어 수집 대상에서 제외했습니다. 대신 세이버메트릭스는 KBO 공식 기본기록으로 이 프로젝트가 직접 계산합니다(근사치이며, 대시보드 내 각 탭 설명에 계산 방식과 한계를 명시해뒀습니다).

## 기술 스택

Python · Streamlit · SQLite · pandas · Plotly · BeautifulSoup

## 시작하기

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 최초 1회: 과거 기록 백필 (시간이 꽤 걸립니다)
python fetch_kbo.py --skip-daily --full-history 1982 2025
python fetch_kbo.py --skip-daily --season-start 2026-03-01
python fetch_kbo.py --skip-daily --awards-backfill

# 매일 최신 기록으로 갱신
python fetch_kbo.py

# 대시보드 실행
streamlit run dashboard.py
```

`fetch_kbo.py`의 다른 옵션은 파일 상단 docstring과 `python fetch_kbo.py --help`를 참고하세요.

## 웹 배포 (Streamlit Community Cloud)

`kbo.db`는 100MB를 넘어 저장소에 올릴 수 없어서, `publish_db.sh`가 압축(약 23MB)해 GitHub 릴리스(`data`)에 올리고 배포된 앱(`remote_db.py`)이 시작할 때·30분마다 그 파일을 내려받습니다. 로컬 `kbo.db`는 절대 덮어쓰지 않습니다.

1. [share.streamlit.io](https://share.streamlit.io)에서 GitHub로 로그인 → **Create app** → 이 저장소, 브랜치 `main`, 메인 파일 `dashboard.py` 선택 → Deploy
2. 기록을 갱신한 뒤 웹에도 반영하려면: `python fetch_kbo.py` → `./publish_db.sh`

## 참고

- `kbo.db`(SQLite)는 저장소에 포함하지 않습니다 — 위 명령으로 직접 생성해야 합니다.
- 일부 지표(WHIP, IBB, HLD, BSV 등)는 KBO 사이트 자체가 특정 연도 이전 값을 제공하지 않아 대시보드 내에서 결측 처리·안내됩니다.
