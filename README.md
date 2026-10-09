# ⚾ KBO 리그 종합 대시보드

**라이브 데모: https://kbo-dashboard-sample.streamlit.app/**

KBO(한국프로야구) 공식 기록을 직접 수집해서 만든 Streamlit 대시보드입니다. 팀·선수 현재 시즌 기록은 물론, 프로야구 원년(1982년)부터 지금까지의 역대 기록과 자체 계산한 세이버메트릭스(WAR, wRC+, FIP 등)까지 볼 수 있습니다.

## 주요 기능

- **팀 순위·추이**: 실시간 순위표, 일자별 순위/승률 변화 그래프
- **팀 기록 비교 / 종합 분석 / 심화 분석**: 피타고리안 기대승률, 홈·원정 스플릿, 상대전적, 매직넘버, 파크팩터, 클러치 지수 등
- **선수 기록 / 선수 검색**: 선수 개인 기록, 리그 내 백분위 프로필, 최근 폼(기간별 활약), 그리고 한 선수를 검색해 기본기록·통산기록(시즌별+합계)·경기별/일자별 기록·상황별 기록(홈원정·상대팀별)·수상 경력·1군 등록일수까지 한 번에 조회
- **세이버메트릭스 / 과거 세이버메트릭스**: KBO 공식 기본기록만으로 직접 계산한 wOBA·wRC+·FIP·WAR 등 (연도별로도 조회 가능)
- **역대 기록(1982~)**: 연도별 최종 순위, 역대 팀/선수 기록, 리그 득점 환경 트렌드, 역대 단일시즌 최고 기록, 통산 기록
- **데이터 분석실**: 지표를 직접 골라 파고드는 분석 도구 — 산점도(추세선·상관계수·이상치 라벨), 분포(박스플롯/히스토그램·분위수·내 선수의 위치), 상관 행렬, 시대별 추이(리그·팀), 시대 보정 지수(OPS+·ERA-)로 선수 커리어 비교, 이번 시즌 선발 리더보드·휴식일별 성적·불펜 부하, 팀 상대전적 히트맵·득점별 승률. 모든 표 CSV 다운로드
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

## 매일 자동 업데이트

`.github/workflows/daily-update.yml`(GitHub Actions)이 **매일 23:30 KST**에 릴리스의 DB를 받아 최근 4일치 경기 결과를 다시 확인하고 `fetch_kbo.py`로 순위·팀/선수 기록·박스스코어·세이버메트릭스를 갱신한 뒤, DB를 릴리스에 다시 올립니다. 배포된 앱은 30분 안에 새 DB를 받아갑니다(내 컴퓨터가 꺼져 있어도 동작, 소요 약 9분). Actions 탭 → *매일 기록 업데이트* → **Run workflow**로 수동 실행도 됩니다. 실패하면 GitHub가 이메일로 알려줍니다.

- 이제 정본 DB는 릴리스(`data`)에 있습니다. 로컬 대시보드에서 최신 기록을 보려면 `gh release download data -p kbo.db.gz --clobber && gunzip -f kbo.db.gz`로 받아오세요(로컬 `kbo.db`를 덮어씁니다).
- `./publish_db.sh`는 로컬 DB를 릴리스에 강제로 올리는 수동 도구입니다. 클라우드가 더 최신일 수 있으니 쓰기 전에 위 명령으로 먼저 받아오세요.

## 참고

- `kbo.db`(SQLite)는 저장소에 포함하지 않습니다 — 위 명령으로 직접 생성해야 합니다.
- 일부 지표(WHIP, IBB, HLD, BSV 등)는 KBO 사이트 자체가 특정 연도 이전 값을 제공하지 않아 대시보드 내에서 결측 처리·안내됩니다.
