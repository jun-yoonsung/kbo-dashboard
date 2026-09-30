"""KBO 공식 홈페이지(koreabaseball.com)에서 팀 순위/타격/투구 기록과
일별 경기 결과를 가져오는 스크레이퍼.

공식 API가 없어 공개된 HTML 페이지를 파싱한다. 사이트 구조가 바뀌면
깨질 수 있으니 fetch_kbo.py 실행 시 오류는 fetch_log 테이블에 남긴다.
"""

import re

import requests
from bs4 import BeautifulSoup

BASE = "https://www.koreabaseball.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
TIMEOUT = 15

POSTBACK_RE = re.compile(r"__doPostBack\('([^']+)'")

STANDINGS_KEYS = [
    "rank", "team", "games", "wins", "losses", "draws",
    "win_pct", "games_behind", "last10", "streak", "home_record", "away_record",
]


def _get(url, params=None):
    resp = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text


HIDDEN_INPUT_RE = re.compile(
    r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"'
    r'|<input[^>]*type="hidden"[^>]*value="([^"]*)"[^>]*name="([^"]+)"'
)


def _form_state(html, soup):
    """현재 페이지의 hidden/select 필드 값을 그대로 담아 postback 폼 데이터를 만든다.

    __VIEWSTATE처럼 매우 긴 value를 가진 hidden input을 html.parser가 간혹
    놓치는 문제가 있어, hidden 필드는 정규식으로 원본 HTML에서 직접 추출한다.
    """
    data = {}
    for m in HIDDEN_INPUT_RE.finditer(html):
        if m.group(1):
            data[m.group(1)] = m.group(2)
        else:
            data[m.group(4)] = m.group(3)
    for sel in soup.select("select"):
        name = sel.get("name")
        if not name:
            continue
        opt = sel.select_one("option[selected]") or sel.select_one("option")
        data[name] = opt.get("value", "") if opt else ""
    return data


def _postback(session, url, html, event_target, extra_fields=None):
    """ASP.NET WebForms 포스트백(페이지네이션, 연도 선택 등)을 흉내낸다."""
    soup = BeautifulSoup(html, "html.parser")
    data = _form_state(html, soup)
    data["__EVENTTARGET"] = event_target
    data["__EVENTARGUMENT"] = ""
    if extra_fields:
        data.update(extra_fields)
    post_headers = dict(HEADERS)
    post_headers["Referer"] = url
    post_headers["Origin"] = BASE
    resp = session.post(url, data=data, headers=post_headers, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _next_page_target(html):
    """페이징 영역에서 '다음 페이지'로 이동하는 postback target을 찾는다. 없으면 None."""
    soup = BeautifulSoup(html, "html.parser")
    pager = soup.select_one("div.paging")
    if not pager:
        return None

    current = pager.select_one("a.on")
    current_num = int(current.get_text(strip=True)) if current and current.get_text(strip=True).isdigit() else None

    if current_num is not None:
        for a in pager.select("a"):
            txt = a.get_text(strip=True)
            if txt.isdigit() and int(txt) == current_num + 1:
                m = POSTBACK_RE.search(a.get("href") or "")
                if m:
                    return m.group(1)

    for a in pager.select("a"):
        if "btnNext" in (a.get("id") or ""):
            m = POSTBACK_RE.search(a.get("href") or "")
            if m:
                return m.group(1)
    return None


def _fetch_all_pages(url, row_parser, max_pages=100):
    """postback 페이지네이션이 걸린 기록 페이지를 끝까지 순회하며 row_parser(html)로 파싱한 행을 모은다."""
    session = requests.Session()
    resp = session.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    html = resp.text

    rows = []
    seen_targets = set()
    for _ in range(max_pages):
        rows.extend(row_parser(html))
        target = _next_page_target(html)
        if not target or target in seen_targets:
            break
        seen_targets.add(target)
        html = _postback(session, url, html, target)
    return rows


TEAM_FIELD = "ctl00$ctl00$ctl00$cphContents$cphContents$cphContents$ddlTeam$ddlTeam"
TEAM_CODES = ["KT", "SS", "LG", "HT", "OB", "LT", "HH", "NC", "SK", "WO"]
SEASON_FIELD = "ctl00$ctl00$ctl00$cphContents$cphContents$cphContents$ddlSeason$ddlSeason"


def _team_codes_for_season(session, url, html_after_season_select):
    """연도 선택 후 팀 드롭다운에 뜨는 팀 코드 목록을 읽는다 (그 해에 실제로 존재했던 팀만 남는다
    — 예: 1982년엔 OB·삼성·MBC·해태·롯데·삼미 6개뿐이다).
    """
    soup = BeautifulSoup(html_after_season_select, "html.parser")
    sel = soup.select_one("select[name$='ddlTeam$ddlTeam']")
    if not sel:
        return list(TEAM_CODES)
    return [o.get("value") for o in sel.find_all("option") if o.get("value")]


def _fetch_all_pages_by_team(url, row_parser, year=None, max_pages_per_team=15):
    """팀 필터를 하나씩 적용해 각 팀의 등록선수 전체를 모은다.

    필터 없는 기본 목록은 리그 전체 상위 30명 안팎으로 잘려서 대타/백업 등
    출전이 적은 선수가 빠진다. 팀별로 걸러서 끝까지 페이지네이션하면
    1군에서 한 경기라도 뛴(기록이 조회되는) 선수까지 전부 모을 수 있다.

    year를 주면 그 시즌 기록을 가져온다 — 연도 선택만으로는 결과 그리드가 갱신되지 않고
    (드롭다운 상태만 바뀐 것처럼 보이지만 실제로는 시즌 전체 누적치를 그대로 반환하는 KBO 사이트의
    또 다른 버그였다) 그 다음 팀 선택까지 이어서 걸어야 실제로 그 연도 데이터로 바뀐다는 것을
    확인했다. 팀 목록도 연도별로 다르므로(과거엔 팀이 6~8개뿐) 연도 선택 후 매번 새로 읽는다.
    """
    session = requests.Session()
    rows = []
    team_codes = TEAM_CODES
    if year is not None:
        # 그 연도에 실제로 존재한 팀 목록을 먼저 확인한다 (연도별로 팀 수가 다르다).
        resp = session.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        html = _postback(session, url, resp.text, SEASON_FIELD, {SEASON_FIELD: str(year)})
        team_codes = _team_codes_for_season(session, url, html) or TEAM_CODES

    for team_code in team_codes:
        # 팀마다 기본(1페이지) 상태에서 새로 시작해야 한다. 직전 팀의 페이지 위치(hfPage)를
        # 그대로 이어받으면 새 팀 필터에 이전 페이지 번호가 적용되어 결과가 누락된다.
        resp = session.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        html = resp.text
        if year is not None:
            html = _postback(session, url, html, SEASON_FIELD, {SEASON_FIELD: str(year)})
        html = _postback(session, url, html, TEAM_FIELD, {TEAM_FIELD: team_code})
        seen_targets = set()
        for _ in range(max_pages_per_team):
            rows.extend(row_parser(html))
            target = _next_page_target(html)
            if not target or target in seen_targets:
                break
            seen_targets.add(target)
            html = _postback(session, url, html, target)
    return rows


PLAYER_ID_RE = re.compile(r"playerId=(\d+)")


def _parse_player_stat_table(html):
    """선수 개인기록 페이지(순위,선수명,팀명,스탯...) 한 페이지를 long-format으로 파싱.
    선수명 셀의 링크(예: '/Record/Player/HitterDetail/Basic.aspx?playerId=62404')에서 KBO 내부
    고유 선수번호(player_id)도 함께 뽑는다 — 이름만으로는 44년치를 넘나들 때 동명이인을
    구분할 수 없어서다(예: '박병호'라는 이름의 1989~1996년 해태 선수와 2005년 이후 활동한
    유명 슬러거는 실제로 다른 사람인데 이름만 같다 — player_id는 각각 89630, 75125로 다르다).
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.tData01, table.tData")
    if not table:
        return []
    headers = [th.get_text(strip=True) for th in table.select("thead th")]
    stat_names = headers[3:]  # 순위, 선수명, 팀명 다음부터가 실제 스탯

    out = []
    for tr in table.select("tbody tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 3:
            continue
        player, team = cells[1], cells[2]
        if not player or player in ("-",):
            continue
        name_link = tr.find_all("td")[1].select_one("a")
        m = PLAYER_ID_RE.search(name_link.get("href", "")) if name_link else None
        player_id = m.group(1) if m else ""
        for name, value in zip(stat_names, cells[3:]):
            out.append((player, team, name, value, player_id))
    return out


def fetch_player_batting(year=None):
    """전체 타자 개인기록(1군 등록선수 전체, long format): [(player, team, stat_name, stat_value), ...]
    year를 주면 그 시즌 기록(1982년부터 조회 가능), 안 주면 현재 시즌.
    """
    out = []
    out += _fetch_all_pages_by_team(f"{BASE}/Record/Player/HitterBasic/Basic1.aspx", _parse_player_stat_table, year=year)
    out += _fetch_all_pages_by_team(f"{BASE}/Record/Player/HitterBasic/Basic2.aspx", _parse_player_stat_table, year=year)
    return out


def fetch_player_pitching(year=None):
    """전체 투수 개인기록(1군 등록선수 전체, long format): [(player, team, stat_name, stat_value), ...]
    year를 주면 그 시즌 기록(1982년부터 조회 가능), 안 주면 현재 시즌.
    """
    out = []
    out += _fetch_all_pages_by_team(f"{BASE}/Record/Player/PitcherBasic/Basic1.aspx", _parse_player_stat_table, year=year)
    out += _fetch_all_pages_by_team(f"{BASE}/Record/Player/PitcherBasic/Basic2.aspx", _parse_player_stat_table, year=year)
    return out


def fetch_player_defense():
    """선수별 '주 포지션' 수비기록(long format): [(player, team, stat_name, stat_value), ...]
    stat_name에 'POS'(수비 포지션), G, GS, IP, E, PO, A, DP, FPCT 등이 포함된다.

    KBO 원본은 한 선수가 여러 포지션을 소화하면 포지션마다 별도 행으로 나온다. player_stat
    테이블의 기본키가 (날짜,구분,팀,선수,지표)라 포지션별로 따로 저장할 수 없으므로,
    수비 이닝(IP)이 가장 많은 '주 포지션' 한 줄만 남긴다 — WAR 포지션 보정에는 이것으로 충분하다.
    """
    raw = _fetch_all_pages_by_team(f"{BASE}/Record/Player/Defense/Basic.aspx", _parse_player_stat_table)

    # (player, team) -> 포지션별 stat dict 목록으로 재구성. 각 포지션 블록은 'POS' 항목으로 시작한다.
    grouped = {}
    current_key = None
    for player, team, name, value, _player_id in raw:
        if name == "POS":
            current_key = (player, team, value)
            grouped.setdefault(current_key, {})
        if current_key and current_key[0] == player and current_key[1] == team:
            grouped[current_key][name] = value

    best_by_player = {}
    for (player, team, pos), stats in grouped.items():
        ip = parse_innings_local(stats.get("IP"))
        key = (player, team)
        if key not in best_by_player or ip > best_by_player[key][0]:
            best_by_player[key] = (ip, pos, stats)

    out = []
    for (player, team), (_, pos, stats) in best_by_player.items():
        for name, value in stats.items():
            out.append((player, team, name, value))
    return out


def fetch_player_baserunning():
    """선수별 도루/주루기록(long format): [(player, team, stat_name, stat_value), ...]
    stat_name에 G, SBA(도루시도), SB(도루성공), CS(도루실패), SB%(성공률), OOB(주루사), PKO(견제사) 포함.
    """
    raw = _fetch_all_pages_by_team(f"{BASE}/Record/Player/Runner/Basic.aspx", _parse_player_stat_table)
    return [(player, team, name, value) for player, team, name, value, _player_id in raw]


def parse_innings_local(ip_str):
    """'991 1/3' 같은 KBO 이닝 표기를 소수로 변환 (sabermetrics와 중복 없이 스크레이퍼 내부용)."""
    if not ip_str:
        return 0.0
    s = str(ip_str).strip()
    try:
        if " " in s:
            whole, frac = s.split(" ", 1)
            num, _, den = frac.partition("/")
            return float(whole) + (float(num) / float(den) if den else 0.0)
        return float(s)
    except ValueError:
        return 0.0


YEAR_FIELD = "ctl00$ctl00$ctl00$cphContents$cphContents$cphContents$ddlYear"


def fetch_historical_standings(year):
    """지정한 연도(예: '2023')의 정규시즌 최종 팀 순위를 반환한다."""
    url = f"{BASE}/record/teamrank/teamrank.aspx"
    session = requests.Session()
    resp = session.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    html = _postback(session, url, resp.text, YEAR_FIELD, {YEAR_FIELD: str(year)})

    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.tData")
    if not table:
        return []
    out = []
    for tr in table.select("tbody tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < len(STANDINGS_KEYS):
            continue
        row = dict(zip(STANDINGS_KEYS, cells))
        row["rank"] = int(row["rank"]) if row["rank"].isdigit() else None
        row["games"] = int(row["games"]) if row["games"].isdigit() else None
        row["wins"] = int(row["wins"]) if row["wins"].isdigit() else None
        row["losses"] = int(row["losses"]) if row["losses"].isdigit() else None
        row["draws"] = int(row["draws"]) if row["draws"].isdigit() else None
        try:
            row["win_pct"] = float(row["win_pct"])
        except ValueError:
            row["win_pct"] = None
        out.append(row)
    return out


def _parse_news_page(html):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for li in soup.select("#contents li"):
        title_a = li.select_one("div.txt strong a")
        if not title_a:
            continue
        href = title_a.get("href", "")
        m = re.search(r"bdSe=(\d+)", href)
        if not m:
            continue
        summary_p = li.select_one("div.txt p")
        date_span = None
        if summary_p:
            date_span = summary_p.select_one("span.date")
            if date_span:
                date_span.extract()
        out.append({
            "bd_se": m.group(1),
            "title": title_a.get_text(strip=True),
            "summary": summary_p.get_text(strip=True) if summary_p else "",
            "date": date_span.get_text(strip=True) if date_span else "",
            "url": f"{BASE}/MediaNews/News/BreakingNews/{href}" if not href.startswith("http") else href,
        })
    return out


def fetch_news(max_pages=3):
    """KBO 공식 속보 뉴스 목록을 최신순으로 가져온다 (부상/트레이드 등 이슈 포함)."""
    url = f"{BASE}/MediaNews/News/BreakingNews/List.aspx"
    return _fetch_all_pages(url, _parse_news_page, max_pages=max_pages)


def fetch_standings():
    """정규시즌 팀 순위표를 반환한다. [{rank, team, games, wins, ...}, ...]"""
    html = _get(f"{BASE}/record/teamrank/teamrank.aspx")
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.tData")
    rows = table.select("tbody tr")

    out = []
    for tr in rows:
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < len(STANDINGS_KEYS):
            continue
        row = dict(zip(STANDINGS_KEYS, cells))
        row["rank"] = int(row["rank"]) if row["rank"].isdigit() else None
        row["games"] = int(row["games"]) if row["games"].isdigit() else None
        row["wins"] = int(row["wins"]) if row["wins"].isdigit() else None
        row["losses"] = int(row["losses"]) if row["losses"].isdigit() else None
        row["draws"] = int(row["draws"]) if row["draws"].isdigit() else None
        try:
            row["win_pct"] = float(row["win_pct"])
        except ValueError:
            row["win_pct"] = None
        out.append(row)
    return out


def _fetch_team_stat_page(url, year=None):
    """팀 기록 페이지(타격/투구) 한 페이지를 long-format [(team, stat_name, stat_value)]로 반환.
    year를 주면 그 시즌 기록 — 팀 기록 페이지는 KBO 사이트 자체가 2001년 이전은 제공하지 않는다
    (선수 개인기록 페이지는 1982년까지 있지만 팀 집계 페이지는 연도 드롭다운이 2001년부터 시작).
    """
    if year is not None:
        session = requests.Session()
        resp = session.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        html = _postback(session, url, resp.text, SEASON_FIELD, {SEASON_FIELD: str(year)})
    else:
        html = _get(url)
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.tData")
    headers = [th.get_text(strip=True) for th in table.select("thead th")]
    # headers[0] == '순위', headers[1] == '팀명', 나머지가 실제 스탯
    stat_names = headers[2:]

    out = []
    for tr in table.select("tbody tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 2:
            continue
        team = cells[1]
        values = cells[2:]
        for name, value in zip(stat_names, values):
            out.append((team, name, value))
    return out


def fetch_team_batting(year=None):
    """팀 타격 기록(long format)을 반환: [(team, stat_name, stat_value), ...]
    year를 주면 그 시즌 기록(2001년부터 조회 가능 — KBO 팀 기록 페이지 자체의 한계), 안 주면 현재 시즌.
    """
    out = []
    out += _fetch_team_stat_page(f"{BASE}/Record/Team/Hitter/Basic1.aspx", year=year)
    out += _fetch_team_stat_page(f"{BASE}/Record/Team/Hitter/Basic2.aspx", year=year)
    return out


def fetch_team_pitching(year=None):
    """팀 투구 기록(long format)을 반환: [(team, stat_name, stat_value), ...]
    year를 주면 그 시즌 기록(2001년부터 조회 가능), 안 주면 현재 시즌.
    """
    out = []
    out += _fetch_team_stat_page(f"{BASE}/Record/Team/Pitcher/Basic1.aspx", year=year)
    out += _fetch_team_stat_page(f"{BASE}/Record/Team/Pitcher/Basic2.aspx", year=year)
    return out


NAVER_SPORTS_API = "https://api-gw.sports.naver.com/schedule/games"


def fetch_games(game_date):
    """지정한 날짜(YYYYMMDD)의 KBO 경기 결과를 반환한다.
    [{away_team, home_team, away_score, home_score, status, stadium, game_time, game_id}, ...]

    KBO 공식 홈페이지의 일별 스코어보드 페이지는 ASP.NET 포스트백 기반이라
    querstring만으로는 날짜별 데이터를 가져올 수 없어, 실제 경기 결과가
    JSON으로 노출되는 네이버 스포츠 게이트웨이 API를 사용한다.
    """
    d = f"{game_date[0:4]}-{game_date[4:6]}-{game_date[6:8]}"
    data = requests.get(
        NAVER_SPORTS_API,
        params={
            "fields": "basic,schedule,baseball",
            "fromDate": d,
            "toDate": d,
            "categoryId": "kbo",
        },
        headers=HEADERS,
        timeout=TIMEOUT,
    ).json()

    games = []
    for g in data.get("result", {}).get("games", []):
        if g.get("roundCode") != "kbo_r":
            continue  # 시범경기(kbo_e)·올스타전(kbo_as) 등은 제외, 정규시즌만
        game_dt = g.get("gameDateTime") or ""
        games.append({
            "game_id": g.get("gameId"),
            "away_team": g.get("awayTeamName"),
            "home_team": g.get("homeTeamName"),
            "away_score": g.get("awayTeamScore"),
            "home_score": g.get("homeTeamScore"),
            "status": g.get("statusCode"),
            "status_info": g.get("statusInfo"),
            "stadium": g.get("stadium"),
            "game_time": game_dt[11:16] if len(game_dt) >= 16 else None,
            "away_starter": g.get("awayStarterName"),
            "home_starter": g.get("homeStarterName"),
            "win_pitcher": g.get("winPitcherName"),
            "lose_pitcher": g.get("losePitcherName"),
            "broadcast": g.get("broadChannel"),
        })
    return games


NAVER_RECORD_API = "https://api-gw.sports.naver.com/schedule/games/{game_id}/record"

BATTING_BOX_FIELDS = {"ab": "AB", "hit": "H", "hr": "HR", "rbi": "RBI", "run": "R", "sb": "SB",
                      "bb": "BB", "kk": "SO"}
# 네이버 박스스코어 투수 로우는 이번 경기 기록과 시즌 누적 기록(gameCount·w·l·era 등)이 한 객체에
# 섞여 있다 — 'bf'는 이름이 '타자수'처럼 보이지만 실제로는 이번 경기 '투구수'다(직접 확인: 7이닝
# 무실점 경기에서 bf=102, 다른 지표는 시즌 10승과 함께 season 누적으로 남아있어 헷갈렸다 —
# 실제 타자수는 'pa' 필드였다: ab+사구=pa가 정확히 맞아떨어짐). TBF는 반드시 'pa'로 채운다.
PITCHING_BOX_FIELDS = {"er": "ER", "hit": "H", "r": "R", "bb": "BB", "kk": "SO", "hr": "HR",
                        "pa": "TBF", "bf": "NP"}


def _parse_naver_innings(s):
    """네이버 박스스코어의 이닝 표기('5', '0 ⅓', '6 ⅔')를 소수로 변환."""
    if not s:
        return 0.0
    s = str(s).strip()
    frac = 0.0
    if "⅓" in s:
        frac, s = 1 / 3, s.replace("⅓", "").strip()
    elif "⅔" in s:
        frac, s = 2 / 3, s.replace("⅔", "").strip()
    try:
        whole = float(s) if s else 0.0
    except ValueError:
        whole = 0.0
    return round(whole + frac, 4)


INNING_FIELD_RE = re.compile(r"^inn\d+$")


def _parse_play_by_play(row):
    """타자 박스스코어의 이닝별 결과 텍스트(예: '좌2'=좌익수 2루타, '사구'=몸에 맞는 공)를 세어
    2B·3B·HBP·SF·SAC를 센다. 공식 KBO 기록 사이트에는 없는 필드라 네이버의 이닝별 텍스트에서
    직접 추출한다 — '숫자로 끝나면 N루타, 안/홈으로 끝나면 단타/홈런' 규칙은 여러 경기의 전체
    코드 집합을 대조해 확인했다(수비 포지션 번호는 항상 뒤에 결과 글자가 붙어서 겹치지 않는다).
    """
    counts = {"2B": 0, "3B": 0, "HBP": 0, "SF": 0, "SAC": 0}
    for key, val in row.items():
        if not (INNING_FIELD_RE.match(key) and val):
            continue
        val = str(val)
        if val == "사구":
            counts["HBP"] += 1
        elif val.endswith("희비"):
            counts["SF"] += 1
        elif val.endswith("희번"):
            counts["SAC"] += 1
        elif val[-1] == "2":
            counts["2B"] += 1
        elif val[-1] == "3":
            counts["3B"] += 1
    return counts


def fetch_game_boxscore(game_id):
    """한 경기(game_id)의 선수별 타격/투구 박스스코어를 가져온다.
    네이버 스포츠 게이트웨이 API 사용(KBO 공식 사이트의 일별 스코어보드는 포스트백 기반이라
    개별 경기 박스스코어를 안정적으로 가져올 수 없다). 로그인/봇차단 없는 공개 JSON API.

    반환: (batting_rows, pitching_rows), 각각 [(player, team, stat_name, stat_value), ...]
    """
    resp = requests.get(NAVER_RECORD_API.format(game_id=game_id), headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    rd = data.get("result", {}).get("recordData", {})
    info = rd.get("gameInfo", {})
    team_by_side = {"home": info.get("hName"), "away": info.get("aName")}

    batting_rows = []
    for side, team in team_by_side.items():
        for row in rd.get("battersBoxscore", {}).get(side, []):
            player = row.get("name")
            if not player:
                continue
            for key, stat_name in BATTING_BOX_FIELDS.items():
                if key in row:
                    batting_rows.append((player, team, stat_name, row[key]))
            for stat_name, value in _parse_play_by_play(row).items():
                batting_rows.append((player, team, stat_name, value))

    pitching_rows = []
    for side, team in team_by_side.items():
        for row in rd.get("pitchersBoxscore", {}).get(side, []):
            player = row.get("name")
            if not player:
                continue
            for key, stat_name in PITCHING_BOX_FIELDS.items():
                if key in row:
                    pitching_rows.append((player, team, stat_name, row[key]))
            pitching_rows.append((player, team, "IP", _parse_naver_innings(row.get("inn"))))

    return batting_rows, pitching_rows


# ===================== 선수 개인 수상 · 등록일수 =====================
# KBO 선수 개인 상세페이지(Record/Player/HitterDetail/*.aspx?playerId=X)의 '수상'/'등록일수' 탭은
# 서버 HTML에는 빈 <tbody>만 있고 실제 표는 페이지 로드 후 JS가 /ws/Record.asmx/GetAward,
# /ws/Record.asmx/GetSeasonReg 를 POST로 호출해 채운다(브라우저 네트워크 탭으로 직접 확인).
# 이 두 웹서비스는 세션 쿠키 없이 바로 POST하면 401을 반환하지만, 아무 선수 상세페이지든 먼저
# 한 번 GET해서 ASP.NET 세션 쿠키를 만들면 그 세션으로 여러 선수의 POST 조회를 재사용할 수
# 있다(직접 확인 — 선수마다 새로 GET할 필요 없음). HitterDetail 경로로 GET해도 투수 선수에
# 대한 조회가 똑같이 동작한다(직접 확인 — 이 API는 타자/투수 구분 없이 pId만 본다).
AWARD_PAGE_URL = f"{BASE}/Record/Player/HitterDetail/Award.aspx"
GET_AWARD_API = f"{BASE}/ws/Record.asmx/GetAward"
GET_SEASON_REG_API = f"{BASE}/ws/Record.asmx/GetSeasonReg"


def prime_player_detail_session(session, player_id):
    """수상·등록일수 AJAX 조회 전에 한 번만 호출 — 세션 쿠키를 만든다."""
    session.get(f"{AWARD_PAGE_URL}?playerId={player_id}", headers=HEADERS, timeout=TIMEOUT)


def _record_asmx_rows(session, api_url, player_id):
    post_headers = dict(HEADERS)
    post_headers["Referer"] = f"{AWARD_PAGE_URL}?playerId={player_id}"
    post_headers["X-Requested-With"] = "XMLHttpRequest"
    resp = session.post(api_url, headers=post_headers, data={"pId": player_id}, timeout=TIMEOUT)
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError:
        return []
    return [[c.get("Text", "") for c in row.get("row", [])] for row in data.get("rows") or []]


def fetch_player_awards(session, player_id):
    """선수 개인 수상 목록: [(year, award_name), ...].
    KBO MVP·신인상·골든글러브·KBO수비상만 나온다 — 올스타전/한국시리즈 MVP는 KBO 사이트 자체가
    선수 개인 상세페이지의 '수상' 탭에는 반영하지 않는다(별도 수상현황 페이지에서만 조회 가능).
    """
    rows = _record_asmx_rows(session, GET_AWARD_API, player_id)
    return [(cells[0], cells[1]) for cells in rows if len(cells) >= 2]


def fetch_player_season_reg(session, player_id):
    """선수 개인 KBO 리그 엔트리(1군) 등록일수: [(team, year, days, note), ...]."""
    rows = _record_asmx_rows(session, GET_SEASON_REG_API, player_id)
    return [(cells[0], cells[1], cells[2], cells[3] if len(cells) > 3 else "") for cells in rows if len(cells) >= 3]


if __name__ == "__main__":
    import json
    print(json.dumps(fetch_standings(), ensure_ascii=False, indent=2)[:1000])
