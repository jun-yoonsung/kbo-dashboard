"""KBO 리그 종합 기록 대시보드 (Streamlit).

실행: streamlit run dashboard.py
데이터 원본: kbo.db (fetch_kbo.py가 매일 채워 넣음)

탭 구성: 팀 순위/추이, 팀 기록 비교, 선수 기록, 선수 검색(개인 딥다이브), 세이버메트릭스(근사치),
과거 세이버메트릭스(연도별), 경기 결과, 역대 기록(1982~), 뉴스, 팀 상세
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

import analysis
import sabermetrics

DB_PATH = Path(__file__).parent / "kbo.db"

STATUS_LABEL = {"RESULT": "종료", "BEFORE": "경기전", "LIVE": "진행중", "CANCEL": "취소"}
CATEGORY_LABEL = {
    "batting": "타격(기본)", "pitching": "투구(기본)", "baserunning": "주루(도루)",
    "batting_adv": "타격(고급 · 자체계산)", "pitching_adv": "투구(고급 · 자체계산)",
}

# 지표별 정렬 방향: "낮을수록 좋음" 목록. K%·BB%는 타자/투수 관점이 반대라 따로 관리한다
# (타자는 삼진 적을수록·볼넷 많을수록 좋고, 투수는 반대로 볼넷 적을수록·삼진 많을수록 좋음).
PITCHING_LOWER_BETTER = {"ERA", "FIP", "WHIP", "BB%"}
BATTING_LOWER_BETTER = {"K%"}


def sort_ascending(stat_name, is_pitching):
    """리더보드에서 이 지표를 오름차순(낮은 값이 1위)으로 정렬해야 하면 True."""
    return stat_name in (PITCHING_LOWER_BETTER if is_pitching else BATTING_LOWER_BETTER)


# 스탯 약어 -> 한글 설명. 타자/투수 문맥에 따라 같은 약어(AVG, 2B, BB, SO 등)의 의미가
# 달라서 따로 관리한다 (예: 투수의 AVG는 '피안타율', BB는 '허용 볼넷').
BATTING_STAT_LABELS = {
    "G": "경기수", "PA": "타석", "AB": "타수", "R": "득점", "H": "안타",
    "2B": "2루타", "3B": "3루타", "HR": "홈런", "TB": "루타", "RBI": "타점",
    "SAC": "희생번트", "SF": "희생플라이", "AVG": "타율", "BB": "볼넷",
    "IBB": "고의4구", "HBP": "사구(몸에 맞는 공)", "SO": "삼진", "GDP": "병살타",
    "SLG": "장타율", "OBP": "출루율", "OPS": "출루율+장타율(OBP+SLG)", "MH": "멀티히트",
    "RISP": "득점권 타율", "PH-BA": "대타 타율",
    "ISO": "순수 장타력 (SLG−AVG)", "BB%": "타석당 볼넷 비율", "K%": "타석당 삼진 비율",
    "wOBA": "가중출루율", "wRC+": "조정 득점생산력 (100=리그평균)", "WAR": "대체선수 대비 승리기여도(근사)",
    "SBA": "도루 시도", "SB": "도루 성공", "CS": "도루 실패(잡힘)", "SB%": "도루 성공률",
    "OOB": "주루사(도루 외 주루 아웃)", "PKO": "견제사",
    "Power-Speed": "파워-스피드 넘버 (홈런·도루 균형지표, 2×HR×SB/(HR+SB))",
    "SB런": "도루로 얻은/잃은 득점가치(근사, WAR에 포함됨)",
}
PITCHING_STAT_LABELS = {
    "G": "경기수", "W": "승리", "L": "패전", "SV": "세이브", "HLD": "홀드",
    "WPCT": "승률", "IP": "이닝", "H": "피안타", "HR": "피홈런", "BB": "볼넷(허용)",
    "HBP": "사구(허용)", "SO": "탈삼진", "R": "실점", "ER": "자책점", "WHIP": "이닝당 출루허용률",
    "ERA": "평균자책점", "CG": "완투", "SHO": "완봉", "QS": "퀄리티스타트",
    "BSV": "블론세이브", "TBF": "상대한 타자 수", "NP": "투구수", "AVG": "피안타율",
    "2B": "피2루타", "3B": "피3루타", "SAC": "허용 희생번트", "SF": "허용 희생플라이",
    "IBB": "고의4구", "WP": "폭투", "BK": "보크",
    "K%": "상대타자당 탈삼진 비율", "BB%": "상대타자당 볼넷 비율",
    "FIP": "수비무관 평균자책점(근사)", "WAR": "대체선수 대비 승리기여도(근사)",
}
# 선수 프로필(퍼센타일 랭킹)에 보여줄 지표 목록과 순서 — 타격/투구 각각 골고루 뽑았다.
PROFILE_STATS_BATTING = ["AVG", "OBP", "SLG", "OPS", "ISO", "BB%", "K%", "wOBA", "wRC+", "WAR"]
PROFILE_STATS_PITCHING = ["ERA", "WHIP", "K%", "BB%", "FIP", "WAR"]

# 역대(1982~) 단일시즌 최고기록에서 고를 수 있는 지표 목록
HIST_BATTING_LEADER_STATS = ["AVG", "OBP", "SLG", "OPS", "HR", "H", "RBI", "R", "TB", "2B", "3B", "BB", "SO"]
HIST_PITCHING_LEADER_STATS = ["ERA", "WHIP", "W", "SV", "HLD", "SO", "IP", "CG", "SHO"]
HIST_BATTING_ADV_LEADER_STATS = ["WAR", "wRC+", "wOBA", "ISO", "BB%", "K%"]
HIST_PITCHING_ADV_LEADER_STATS = ["WAR", "FIP", "WHIP", "K%", "BB%"]

# '선수 검색 > 통산 기록'의 시즌별+통산 합산표 컬럼 순서 (KBO 공식 선수 페이지 표기 순서를 따름).
CAREER_TABLE_COLS_BATTING = ["AVG", "G", "PA", "AB", "R", "H", "2B", "3B", "HR", "TB", "RBI",
                              "BB", "IBB", "HBP", "SO", "GDP", "SAC", "SF", "MH", "OBP", "SLG", "OPS", "WAR"]
CAREER_TABLE_COLS_PITCHING = ["ERA", "G", "W", "L", "SV", "HLD", "WPCT", "IP", "H", "HR", "BB",
                               "HBP", "SO", "R", "ER", "WHIP", "CG", "SHO", "QS", "BSV", "TBF", "WAR"]

STANDINGS_STAT_LABELS = {
    "게임차": "1위와의 승차", "최근10경기": "최근 10경기 성적", "연속": "현재 연속 승/패",
}


def stat_label(code, is_pitching):
    return (PITCHING_STAT_LABELS if is_pitching else BATTING_STAT_LABELS).get(code)


def stat_option_label(code, is_pitching):
    """selectbox 옵션에 약어 + 한글 설명을 같이 보여준다."""
    name = stat_label(code, is_pitching)
    return f"{code} · {name}" if name else code


def stat_column_config(columns, is_pitching):
    """st.dataframe(column_config=...)용: 아는 약어 컬럼에 한글 설명 툴팁을 단다."""
    return {
        c: st.column_config.Column(help=f"{stat_label(c, is_pitching)}")
        for c in columns if stat_label(c, is_pitching)
    }

def format_innings(value):
    """계산된 소수 이닝(1/3이닝=0.3333...)을 KBO 표기('1.1'=1이닝+1아웃)로 변환한다.

    parse_innings류 함수들은 '1 1/3이닝'을 1.3333...으로 저장하는데, 이건 계산(ERA·WHIP 등)에는
    필요하지만 그대로 반올림해서 보여주면(1.3333→'1.3') 야구에 없는 '0.3이닝'처럼 보여 혼동을
    준다 — 실제로 사용자가 '경기별 기록' 합계에서 '40.3이닝'을 보고 이상하다고 지적해 발견한
    문제다. 소수부를 아웃 수(0·1·2)로 되돌려 KBO 사이트와 같은 표기로 보여준다.
    """
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    whole = int(value)
    outs = int(round((value - whole) * 3))
    if outs >= 3:
        whole += 1
        outs -= 3
    return f"{whole}.{outs}"


# '시즌별 + 합계' 스타일 표(선수 검색 > 통산 기록·경기별 기록)에서 지표별 소수점 자리수.
RATE3_COLS = {"AVG", "OBP", "SLG", "OPS", "WPCT"}
RATE2_COLS = {"ERA", "WHIP", "WAR"}


def styled_summary_table(df, label_col, label_value, numeric_cols, border="top"):
    """카운팅·비율 지표가 섞인 표에서 라벨 행(통산/합계 등)을 굵게 강조하는 Styler를 만든다.
    numeric_cols는 pd.to_numeric으로 강제 변환한 뒤 지표 종류별로 소수점 자리수를 맞춰
    포맷한다 — 안 그러면 통산 합계처럼 다른 경로로 계산된 값이 시즌별 값과 자릿수가
    어긋나 보이는 문제가 생긴다(실제로 겪음: 합계 행만 '0.310000'처럼 6자리로 깨져 보였음).
    IP는 소수점 자릿수가 아니라 KBO 이닝 표기(아웃 수)로 별도 변환한다.
    """
    df = df.copy()
    fmt = {}
    for c in numeric_cols:
        if c == "IP":
            # 시즌별 행은 KBO 원본 문자열('128 1/3'), 합계 행은 우리가 계산한 소수(128.333..)라
            # 표기가 다르다 — parse_innings로 둘 다 먼저 소수로 통일한 뒤 KBO 표기로 되돌린다.
            df[c] = df[c].apply(lambda v: format_innings(sabermetrics.parse_innings(v)))
            continue
        df[c] = pd.to_numeric(df[c], errors="coerce")
        if c in RATE3_COLS:
            fmt[c] = "{:.3f}"
        elif c in RATE2_COLS:
            fmt[c] = "{:.2f}"
        else:
            fmt[c] = "{:.0f}"

    border_css = f"border-{border}: 2px solid rgba(128,128,128,0.6);"

    def _bold(row):
        style = f"font-weight: 700; {border_css}" if row[label_col] == label_value else ""
        return [style] * len(row)

    return df.style.apply(_bold, axis=1).format(fmt, na_rep="-")

# 구단 상징색(근사치) — 차트에서 팀마다 같은 색을 쓰기 위한 고정 매핑
TEAM_COLORS = {
    "KIA": "#EA0029",
    "삼성": "#1F6FB2",
    "LG": "#C30452",
    "두산": "#131230",
    "롯데": "#12355B",
    "한화": "#FF6600",
    "NC": "#315288",
    "SSG": "#CE0E2D",
    "KT": "#ED1C2E",
    "키움": "#820024",
    "나눔": "#888888",
    "드림": "#444444",
}

st.set_page_config(page_title="KBO 대시보드", page_icon="⚾", layout="wide")


@st.cache_data(ttl=300)
def load_table(query, params=None):
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df


def last_fetch_date(df, col="fetch_date"):
    return df[col].max() if not df.empty else None


if not DB_PATH.exists():
    st.error("kbo.db가 없습니다. 먼저 `python fetch_kbo.py`를 실행해 데이터를 수집해 주세요.")
    st.stop()

standings = load_table("SELECT * FROM standings")
team_stat = load_table("SELECT * FROM team_stat")
player_stat = load_table("SELECT * FROM player_stat")
player_game_stat = load_table("SELECT * FROM player_game_stat")
games = load_table("SELECT * FROM games")
history = load_table("SELECT * FROM historical_standings")
history_team_stat = load_table("SELECT * FROM historical_team_stat")
history_player_stat = load_table("SELECT * FROM historical_player_stat")
player_award = load_table("SELECT * FROM player_award")
player_season_reg = load_table("SELECT * FROM player_season_reg")
news = load_table("SELECT * FROM news ORDER BY bd_se DESC")
fetch_log = load_table("SELECT * FROM fetch_log ORDER BY run_at DESC LIMIT 30")

if standings.empty:
    st.warning("아직 수집된 데이터가 없습니다. `python fetch_kbo.py`를 실행해 주세요.")
    st.stop()

latest_date = last_fetch_date(standings)
latest_stat_date = last_fetch_date(team_stat)
latest_player_date = last_fetch_date(player_stat)
teams = sorted(standings["team"].unique().tolist())

st.title("⚾ KBO 리그 종합 대시보드")
last_ok = fetch_log[fetch_log["status"] == "ok"]["run_at"].max() if not fetch_log.empty else None
st.caption(f"최근 순위 기준일: {latest_date}" + (f"  ·  마지막 수집: {last_ok}" if last_ok else ""))
st.caption("Statiz·MyKBOStats는 로그인/봇차단이 걸려 있어 수집하지 않았습니다. 대신 WAR·wRC+를 포함한 세이버메트릭스는 "
           "공식 기본기록과 리그 전체 합산치로 이 대시보드가 직접 계산합니다 — 타자 WAR는 포지션 조정치와 도루 가치까지 "
           "반영하지만 개별 수비력·진루타·구장 보정은 여전히 근사치이니 '세이버메트릭스' 탭의 설명을 참고하세요.")

with st.expander("📖 스탯 용어 설명 (약어가 뭘 뜻하는지 헷갈릴 때)"):
    gcol1, gcol2 = st.columns(2)
    with gcol1:
        st.markdown("**타자 지표**")
        st.dataframe(
            pd.DataFrame({"약어": list(BATTING_STAT_LABELS.keys()), "설명": list(BATTING_STAT_LABELS.values())}),
            hide_index=True, use_container_width=True, height=300,
        )
    with gcol2:
        st.markdown("**투수 지표**")
        st.dataframe(
            pd.DataFrame({"약어": list(PITCHING_STAT_LABELS.keys()), "설명": list(PITCHING_STAT_LABELS.values())}),
            hide_index=True, use_container_width=True, height=300,
        )
    st.caption("표에서는 컬럼 제목에 마우스를 올리면 같은 설명이 툴팁으로 뜹니다.")

TAB_NAMES = ["팀 순위 · 추이", "팀 기록 비교", "종합 분석", "심화 분석", "선수 기록", "선수 검색",
             "세이버메트릭스", "과거 세이버메트릭스", "경기 결과", "역대 기록", "뉴스", "팀 상세"]
# st.tabs는 다른 위젯 조작으로 rerun될 때 항상 첫 탭으로 되돌아가는 알려진 문제가 있어,
# 세션 상태로 선택을 유지하는 segmented_control을 탭처럼 사용한다.
active_tab = st.segmented_control(
    "메뉴", TAB_NAMES, default=TAB_NAMES[0], key="active_tab", label_visibility="collapsed",
)
if not active_tab:
    active_tab = TAB_NAMES[0]
st.divider()

# ============================================================== 팀 순위 · 추이
if active_tab == "팀 순위 · 추이":
    st.subheader("팀 순위")
    today_standings = standings[standings["fetch_date"] == latest_date].sort_values("rank")
    st.dataframe(
        today_standings[[
            "rank", "team", "games", "wins", "losses", "draws", "win_pct",
            "games_behind", "last10", "streak", "home_record", "away_record",
        ]].rename(columns={
            "rank": "순위", "team": "팀", "games": "경기", "wins": "승", "losses": "패",
            "draws": "무", "win_pct": "승률", "games_behind": "게임차", "last10": "최근10경기",
            "streak": "연속", "home_record": "홈", "away_record": "방문",
        }),
        hide_index=True, use_container_width=True,
    )

    st.subheader("순위 · 승률 추이 (2026 정규시즌 개막일부터 전체)")
    st.caption("KBO 공식 순위표는 스냅샷만 제공해 매일 수집한 날짜만 남지만, 수집해 둔 전체 경기 결과를 "
               "날짜순으로 누적 계산해 개막일(3/28)부터의 순위·승률 변화를 복원했습니다.")
    season_hist = analysis.season_standings_history(games)
    col1, col2 = st.columns(2)
    with col1:
        fig_rank = px.line(
            season_hist.sort_values("date"), x="date", y="rank", color="team",
            color_discrete_map=TEAM_COLORS, title="일자별 순위 변화 (낮을수록 상위권)",
        )
        fig_rank.update_yaxes(autorange="reversed", dtick=1)
        fig_rank.update_xaxes(tickformat="%y/%m/%d", dtick=14 * 24 * 60 * 60 * 1000, hoverformat="%Y-%m-%d")
        st.plotly_chart(fig_rank, use_container_width=True)
    with col2:
        fig_wp = px.line(
            season_hist.sort_values("date"), x="date", y="win_pct", color="team",
            color_discrete_map=TEAM_COLORS, title="일자별 승률 변화",
        )
        fig_wp.update_xaxes(tickformat="%y/%m/%d", dtick=14 * 24 * 60 * 60 * 1000, hoverformat="%Y-%m-%d")
        st.plotly_chart(fig_wp, use_container_width=True)

# ============================================================== 팀 기록 비교
if active_tab == "팀 기록 비교":
    st.subheader("팀 기록 비교")
    category = st.radio("구분", ["batting", "pitching", "batting_adv", "pitching_adv"],
                         horizontal=True, format_func=lambda x: CATEGORY_LABEL[x], key="team_cat")
    cat_df = team_stat[(team_stat["category"] == category) & (team_stat["fetch_date"] == latest_stat_date)]
    available_stats = sorted(cat_df["stat_name"].unique().tolist())
    default_stat = "AVG" if category == "batting" and "AVG" in available_stats else (
        "ERA" if category == "pitching" and "ERA" in available_stats else (
            available_stats[0] if available_stats else None))
    if available_stats:
        is_pitching_cat = "pitching" in category
        stat_choice = st.selectbox("지표 선택", available_stats,
                                    index=available_stats.index(default_stat) if default_stat in available_stats else 0,
                                    format_func=lambda c: stat_option_label(c, is_pitching_cat), key="team_stat_choice")
        stat_df = cat_df[cat_df["stat_name"] == stat_choice].copy()
        if stat_choice == "IP":
            # KBO 원본 이닝 표기('1009 1/3')는 pd.to_numeric으로 못 읽어 막대가 통째로 사라진다.
            stat_df["stat_value_num"] = stat_df["stat_value"].apply(sabermetrics.parse_innings)
        else:
            stat_df["stat_value_num"] = pd.to_numeric(stat_df["stat_value"], errors="coerce")
        ascending = sort_ascending(stat_choice, is_pitching=is_pitching_cat)
        stat_df = stat_df.sort_values("stat_value_num", ascending=ascending)
        fig_stat = px.bar(stat_df, x="team", y="stat_value_num", color="team", color_discrete_map=TEAM_COLORS,
                           title=f"팀별 {stat_option_label(stat_choice, is_pitching_cat)}", text="stat_value")
        st.plotly_chart(fig_stat, use_container_width=True)
    else:
        st.info("아직 이 구분의 기록이 수집되지 않았습니다.")

# ============================================================== 종합 분석
if active_tab == "종합 분석":
    st.subheader("피타고리안 기대승률 (득점/실점 기반)")
    st.caption("득점·실점만으로 계산한 기대승률과 실제 승률의 차이. 실제가 기대보다 높으면 '단기적으로 운이 따른' 팀, "
               "낮으면 '내용에 비해 승수가 덜 쌓인' 팀으로 볼 수 있습니다.")
    pyth = analysis.pythagorean_win_pct(team_stat, standings, latest_stat_date)
    fig_pyth = px.bar(
        pyth, x="team", y="diff", color="diff", color_continuous_scale="RdBu",
        title="실제 승률 − 피타고리안 기대승률 (양수 = 실제가 더 좋음)",
        hover_data=["R", "RA", "pyth_win_pct", "win_pct"],
    )
    st.plotly_chart(fig_pyth, use_container_width=True)
    st.dataframe(
        pyth[["team", "R", "RA", "run_diff", "win_pct", "pyth_win_pct", "diff"]]
        .rename(columns={"team": "팀", "R": "득점", "RA": "실점", "run_diff": "득실차",
                          "win_pct": "실제승률", "pyth_win_pct": "기대승률", "diff": "차이"}),
        hide_index=True, use_container_width=True,
    )

    st.subheader("홈 · 원정 승률 스플릿")
    ha = analysis.home_away_split(standings, latest_stat_date)
    fig_ha = px.bar(
        ha.melt(id_vars="team", value_vars=["home_win_pct", "away_win_pct"], var_name="구분", value_name="승률"),
        x="team", y="승률", color="구분", barmode="group", title="홈 승률 vs 원정 승률",
    )
    st.plotly_chart(fig_ha, use_container_width=True)

    st.subheader("팀 간 상대전적 (정규시즌 누적, 승수 매트릭스)")
    st.caption("행(승리팀)이 열(패배팀)을 상대로 거둔 승수입니다.")
    h2h = analysis.head_to_head(games)
    st.dataframe(h2h, use_container_width=True)

    st.subheader("월별 팀 성적 추이")
    monthly = analysis.monthly_team_trend(games)
    if not monthly.empty:
        monthly_disp = monthly.copy()
        monthly_disp["month"] = pd.to_datetime(monthly_disp["month"], format="%Y%m").dt.strftime("%Y-%m")
        fig_month = px.line(monthly_disp, x="month", y="win_pct", color="team", markers=True,
                             color_discrete_map=TEAM_COLORS, title="월별 승률 추이")
        st.plotly_chart(fig_month, use_container_width=True)

    st.subheader("접전 · 대승/대패 기록")
    st.caption("1점차 승패, 5점차 이상 대승/대패 횟수. 접전에 강한 팀 vs 화력으로 압도하는 팀을 비교해볼 수 있습니다.")
    cb = analysis.close_and_blowout_games(games)
    st.dataframe(
        cb.rename(columns={"team": "팀", "one_run_w": "1점차 승", "one_run_l": "1점차 패",
                            "blowout_w": "5점차+ 대승", "blowout_l": "5점차+ 대패"}),
        hide_index=True, use_container_width=True,
    )

    st.subheader("부문별 1위 (타이틀레이스)")
    st.caption("규정타석(팀 경기수 × 3.1)/규정이닝(팀 경기수 × 1)을 채운 선수만 대상으로 합니다 — "
               "그렇지 않으면 극소표본 선수가 왜곡된 1위로 나올 수 있습니다.")
    qualified_for_title = (analysis.qualified_players(player_stat, standings, latest_player_date, "batting")
                            | analysis.qualified_players(player_stat, standings, latest_player_date, "pitching"))
    title_player_stat = player_stat[
        player_stat.apply(lambda r: (r["player"], r["team"]) in qualified_for_title, axis=1)
    ]
    title_leaders = analysis.title_leaders(title_player_stat, latest_player_date)
    tl_cols = st.columns(3)
    for i, (label, df) in enumerate(title_leaders.items()):
        with tl_cols[i % 3]:
            st.markdown(f"**{label}**")
            st.dataframe(df.rename(columns={"player": "선수", "team": "팀", "stat_value": "기록"}),
                         hide_index=True, use_container_width=True)

    st.subheader("어떤 스탯이 승리와 가장 관련 있을까? (상관계수)")
    ccol1, ccol2 = st.columns(2)
    with ccol1:
        st.markdown("**타격 고급지표 vs 승률**")
        corr_b = analysis.stat_win_correlation(team_stat, standings, latest_stat_date, "batting_adv")
        corr_b["stat"] = corr_b["stat"].apply(lambda c: stat_option_label(c, is_pitching=False))
        st.dataframe(corr_b.rename(columns={"stat": "지표", "corr_with_win_pct": "승률과의 상관계수"}),
                     hide_index=True, use_container_width=True)
    with ccol2:
        st.markdown("**투구 고급지표 vs 승률**")
        corr_p = analysis.stat_win_correlation(team_stat, standings, latest_stat_date, "pitching_adv")
        corr_p["stat"] = corr_p["stat"].apply(lambda c: stat_option_label(c, is_pitching=True))
        st.dataframe(corr_p.rename(columns={"stat": "지표", "corr_with_win_pct": "승률과의 상관계수"}),
                     hide_index=True, use_container_width=True)

# ============================================================== 심화 분석
if active_tab == "심화 분석":
    st.subheader("매직넘버")
    st.caption("표준 매직넘버 공식(= 총경기수+1 − 선두팀 승수 − 추격팀 패수)을 사용한 근사치입니다. "
               "KBO 무승부 규정을 정밀 반영한 공식 수치는 아닙니다.")
    mn = analysis.magic_number(standings, latest_stat_date)
    if mn:
        mcol1, mcol2 = st.columns(2)
        with mcol1:
            title_txt = "매직넘버 소멸(확정 전)" if mn["title_mn"] > 0 else "우승 매직넘버 0 (조건 충족)"
            st.metric(f"우승 매직넘버 — {mn['title_leader']} (2위 {mn['title_chaser']} 기준)",
                       mn["title_mn"], help=title_txt)
        with mcol2:
            st.metric(f"포스트시즌 매직넘버 — {mn['playoff_leader']} 5위 (6위 {mn['playoff_chaser']} 기준)",
                       mn["playoff_mn"])
    else:
        st.info("팀 수가 부족해 매직넘버를 계산할 수 없습니다.")

    st.subheader("시즌 최종 순위 예측 (현재 승률 유지 가정)")
    st.caption("잔여 경기에서도 지금까지의 승률이 그대로 유지된다고 가정한 단순 선형 예측입니다. 실제 결과와 다를 수 있습니다.")
    proj = analysis.season_projection(standings, latest_stat_date)
    proj_disp = proj.copy()
    proj_disp["proj_wins"] = proj_disp["proj_wins"].round(1)
    proj_disp["proj_losses"] = proj_disp["proj_losses"].round(1)
    proj_disp["proj_win_pct"] = proj_disp["proj_win_pct"].round(3)
    st.dataframe(
        proj_disp.rename(columns={
            "team": "팀", "rank": "현재순위", "games_remaining": "잔여경기", "wins": "현재승", "losses": "현재패",
            "proj_wins": "예상최종승", "proj_losses": "예상최종패", "proj_win_pct": "예상승률", "proj_rank": "예상순위",
        }),
        hide_index=True, use_container_width=True,
    )

    st.subheader("구장별 득점 환경 (간이 파크팩터)")
    st.caption("100 = 리그 평균 경기당 득점. 100보다 높으면 타자 친화적, 낮으면 투수 친화적 구장 환경입니다. "
               "구장을 쓰는 팀들의 전력 차이를 통제하지 않은 단순 평균이라 정밀 파크팩터는 아닙니다.")
    park = analysis.stadium_scoring_environment(games)
    if not park.empty:
        fig_park = px.bar(park, x="stadium", y="park_factor", text="park_factor",
                           title="구장별 간이 파크팩터 (100=리그 평균)")
        fig_park.add_hline(y=100, line_dash="dash", line_color="gray")
        st.plotly_chart(fig_park, use_container_width=True)

    st.subheader("요일별 팀 성적")
    weekday = analysis.weekday_performance(games)
    if not weekday.empty:
        wd_pivot = weekday.pivot_table(index="team", columns="weekday_kr", values="win_pct")
        wd_pivot = wd_pivot[[d for d in analysis.WEEKDAY_KR if d in wd_pivot.columns]]
        st.dataframe(wd_pivot.round(3).rename_axis("팀"), use_container_width=True)

    st.subheader("시즌 최다 연승 · 연패")
    streaks = analysis.longest_streaks(games)
    if not streaks.empty:
        streaks_melt = streaks.melt(id_vars="team", value_vars=["longest_win_streak", "longest_lose_streak"],
                                     var_name="구분", value_name="경기수")
        streaks_melt["구분"] = streaks_melt["구분"].map({"longest_win_streak": "최다연승", "longest_lose_streak": "최다연패"})
        fig_streak = px.bar(streaks_melt, x="team", y="경기수", color="구분", barmode="group",
                             title="팀별 시즌 최다 연승 · 연패")
        st.plotly_chart(fig_streak, use_container_width=True)

    st.subheader("클러치 지수 (득점권 타율 − 통산 타율)")
    st.caption("양수일수록 평소보다 찬스(득점권)에서 더 잘 치는 선수입니다. 표본이 적은 선수의 왜곡을 피하기 위해 "
               "규정타석(팀 경기수 × 3.1)을 채운 타자만 대상으로 합니다.")
    clutch = analysis.clutch_index(player_stat, latest_player_date)
    qualified_batters = analysis.qualified_players(player_stat, standings, latest_player_date, "batting")
    if not clutch.empty:
        clutch = clutch[clutch.apply(lambda r: (r["player"], r["team"]) in qualified_batters, axis=1)]
    if not clutch.empty:
        ccol1, ccol2 = st.columns(2)
        with ccol1:
            st.markdown("**클러치 TOP 10**")
            st.dataframe(
                clutch.head(10)[["player", "team", "AVG", "RISP", "clutch_diff"]]
                .rename(columns={"player": "선수", "team": "팀", "clutch_diff": "클러치지수"}),
                hide_index=True, use_container_width=True,
                column_config={
                    "AVG": st.column_config.Column(help=BATTING_STAT_LABELS["AVG"]),
                    "RISP": st.column_config.Column(help=BATTING_STAT_LABELS["RISP"]),
                    "클러치지수": st.column_config.Column(help="득점권 타율 − 통산 타율 (양수면 찬스에 강함)"),
                },
            )
        with ccol2:
            st.markdown("**클러치 BOTTOM 10**")
            st.dataframe(
                clutch.tail(10)[["player", "team", "AVG", "RISP", "clutch_diff"]]
                .sort_values("clutch_diff")
                .rename(columns={"player": "선수", "team": "팀", "clutch_diff": "클러치지수"}),
                hide_index=True, use_container_width=True,
                column_config={
                    "AVG": st.column_config.Column(help=BATTING_STAT_LABELS["AVG"]),
                    "RISP": st.column_config.Column(help=BATTING_STAT_LABELS["RISP"]),
                    "클러치지수": st.column_config.Column(help="득점권 타율 − 통산 타율 (양수면 찬스에 강함)"),
                },
            )

# ============================================================== 선수 기록
if active_tab == "선수 기록":
    st.subheader("선수 개인 기록")
    pcol1, pcol2, pcol3 = st.columns([1, 1, 2])
    with pcol1:
        p_category = st.radio("구분", ["batting", "pitching", "baserunning"], horizontal=True,
                               format_func=lambda x: {"batting": "타자", "pitching": "투수",
                                                       "baserunning": "주루(도루)"}[x], key="player_cat")
    qualify_category = "pitching" if p_category == "pitching" else "batting"
    p_df = player_stat[(player_stat["category"] == p_category) & (player_stat["fetch_date"] == latest_player_date)]
    with pcol2:
        p_team_filter = st.multiselect("팀 필터", teams, key="player_team_filter")
    with pcol3:
        p_search = st.text_input("선수명 검색", key="player_search")

    pivot = p_df.pivot_table(index=["player", "team"], columns="stat_name", values="stat_value",
                              aggfunc="first", sort=False).reset_index()
    if p_team_filter or p_search:
        # 팀/이름으로 특정해서 찾을 때는 규정 미달 선수(대타·백업 등)도 포함해 그 팀/이름 전체를 보여준다.
        if p_team_filter:
            pivot = pivot[pivot["team"].isin(p_team_filter)]
        if p_search:
            pivot = pivot[pivot["player"].str.contains(p_search, na=False)]
    else:
        qualified = analysis.qualified_players(player_stat, standings, latest_player_date, qualify_category)
        pivot = pivot[pivot.apply(lambda r: (r["player"], r["team"]) in qualified, axis=1)]
        st.caption(f"기본적으로 규정{'타석' if qualify_category == 'batting' else '이닝'} 이상인 선수만 표시합니다"
                   f"{'(주루는 타자 규정타석 기준)' if p_category == 'baserunning' else ''}. "
                   f"팀 필터나 이름 검색을 쓰면 규정 미달 선수를 포함한 전체 명단에서 찾을 수 있습니다.")

    st.caption(f"{len(pivot)}명 표시 (기준일: {latest_player_date}) — 표 헤더를 클릭하면 그 지표 기준으로 정렬할 수 있고, "
               f"헤더에 마우스를 올리면 약어 설명이 뜹니다.")
    st.dataframe(pivot.rename(columns={"player": "선수", "team": "팀"}), hide_index=True, use_container_width=True,
                 height=500, key=f"player_table_{p_category}_{tuple(sorted(p_team_filter))}_{p_search}",
                 column_config=stat_column_config(pivot.columns, is_pitching=p_category == "pitching"))

    if p_category in ("batting", "pitching"):
        st.divider()
        st.subheader("선수 프로필 (리그 내 백분위)")
        st.caption("규정타석/규정이닝을 채운 선수들 사이에서 이 선수의 각 지표가 몇 %ile인지 보여줍니다 "
                   "(100에 가까울수록 리그 최상위권). ERA·WHIP·K%(투수)처럼 낮을수록 좋은 지표는 방향을 "
                   "뒤집어 표시합니다 — 규정 미달 선수는 표본에 없어 선택할 수 없습니다. 두 번째 선수를 "
                   "고르면 나란히 비교해서 보여줍니다.")
        profile_qualified = analysis.qualified_players(player_stat, standings, latest_player_date, p_category)
        profile_names = sorted({p for p, t in profile_qualified})
        if profile_names:
            stat_order = PROFILE_STATS_PITCHING if p_category == "pitching" else PROFILE_STATS_BATTING
            stat_label_order = [stat_option_label(c, p_category == "pitching") for c in stat_order]
            lower_better = PITCHING_LOWER_BETTER if p_category == "pitching" else BATTING_LOWER_BETTER
            all_pct_df = analysis.player_percentiles(player_stat, standings, latest_player_date, p_category)

            def _player_profile(name):
                team = sorted(t for p, t in profile_qualified if p == name)[0]
                df = all_pct_df[(all_pct_df["player"] == name) & (all_pct_df["team"] == team)].copy()
                df = df[df["stat_name"].isin(stat_order)]
                df["display_pct"] = df.apply(
                    lambda r: 100 - r["percentile"] if r["stat_name"] in lower_better else r["percentile"], axis=1)
                df["stat_label"] = df["stat_name"].apply(lambda c: stat_option_label(c, p_category == "pitching"))
                df["선수"] = f"{name}({team})"
                return df

            pcol_a, pcol_b = st.columns(2)
            with pcol_a:
                profile_player_a = st.selectbox("선수 선택", profile_names, key="profile_player")
            with pcol_b:
                compare_options = ["(비교 안 함)"] + [p for p in profile_names if p != profile_player_a]
                profile_player_b = st.selectbox("비교할 선수 (선택)", compare_options, key="profile_player_b")

            if profile_player_b == "(비교 안 함)":
                pct_df = _player_profile(profile_player_a)
                fig_profile = px.bar(pct_df, x="display_pct", y="stat_label", orientation="h",
                                      range_x=[0, 100], text="value",
                                      color="display_pct", color_continuous_scale=["#3B82C4", "#DDDDDD", "#C30452"],
                                      range_color=[0, 100], title=f"{pct_df['선수'].iloc[0]} 백분위 프로필")
                fig_profile.update_layout(coloraxis_showscale=False)
            else:
                combined = pd.concat([_player_profile(profile_player_a), _player_profile(profile_player_b)])
                fig_profile = px.bar(combined, x="display_pct", y="stat_label", orientation="h",
                                      range_x=[0, 100], text="value", color="선수", barmode="group",
                                      title=f"{combined['선수'].iloc[0]} vs {combined['선수'].iloc[-1]} 백분위 비교")
            fig_profile.update_layout(yaxis={"categoryorder": "array", "categoryarray": stat_label_order[::-1]},
                                       xaxis_title="백분위 (%ile)", yaxis_title="")
            st.plotly_chart(fig_profile, use_container_width=True)
        else:
            st.info("규정타석/규정이닝을 채운 선수가 없습니다.")

        st.divider()
        st.subheader("최근 폼 (기간별 활약)")
        form_game_dates = sorted(player_game_stat[player_game_stat["category"] == p_category]["game_date"].unique().tolist())
        if not form_game_dates:
            st.info("아직 경기별 박스스코어가 없습니다. `fetch_kbo.py`를 실행하면 채워집니다.")
        else:
            st.caption("네이버 스포츠의 경기별 박스스코어를 구간별로 합산합니다 — KBO 공식 사이트는 특정 기간 "
                       "기준 선수기록을 조회하는 기능이 없어(월별 필터가 있는 것처럼 보이지만 실제로는 걸리지 "
                       "않고 시즌 누적치를 그대로 반환해서 대신 이 방식을 씁니다) 시즌 개막일부터의 경기 결과를 "
                       "직접 더합니다. 2루타·3루타·사구·희생타는 박스스코어에 없어 이닝별 결과 텍스트를 "
                       "파싱해 세는데, 공식 시즌 누적치와 대조해보니 대부분 일치하고 일부 선수는 ±1~2개 정도 "
                       "오차가 있습니다 — OBP·SLG·OPS도 이 값으로 계산하므로 같은 정도의 오차가 있을 수 "
                       "있습니다. 시즌 중 트레이드로 팀을 옮긴 선수는 팀별로 나뉘어 표시됩니다.")
            min_d, max_d = form_game_dates[0], form_game_dates[-1]
            preset = st.radio("기간", ["최근 7일", "최근 14일", "최근 30일", "시즌 전체", "직접 선택"],
                               horizontal=True, key="form_preset")
            max_dt = datetime.strptime(max_d, "%Y%m%d")
            preset_days = {"최근 7일": 6, "최근 14일": 13, "최근 30일": 29}
            if preset in preset_days:
                form_start = max(min_d, (max_dt - timedelta(days=preset_days[preset])).strftime("%Y%m%d"))
                form_end = max_d
            elif preset == "시즌 전체":
                form_start, form_end = min_d, max_d
            else:
                fcol1, fcol2 = st.columns(2)
                with fcol1:
                    form_start = st.selectbox("시작일", form_game_dates, index=0, key="form_start")
                with fcol2:
                    form_end = st.selectbox("종료일", form_game_dates, index=len(form_game_dates) - 1, key="form_end")
            if form_start > form_end:
                st.warning("시작일이 종료일보다 늦어 계산할 수 없습니다.")
            else:
                form_df = analysis.recent_form(player_game_stat, form_start, form_end, p_category)
                if form_df.empty:
                    st.info("이 구간에 출전기록이 있는 선수가 없습니다.")
                else:
                    form_stat_cols = [c for c in form_df.columns if c not in ("player", "team")]
                    form_stat = st.selectbox("지표", sorted(form_stat_cols),
                                              format_func=lambda c: stat_option_label(c, p_category == "pitching"),
                                              key="form_stat")
                    min_ab_ip = st.slider(f"최소 {'타수' if p_category == 'batting' else '이닝'}",
                                           0.0, float(form_df["AB" if p_category == "batting" else "IP"].max()),
                                           0.0, key="form_min")
                    form_shown = form_df[form_df["AB" if p_category == "batting" else "IP"] >= min_ab_ip].copy()
                    form_shown["value_num"] = pd.to_numeric(form_shown[form_stat], errors="coerce")
                    ascending = sort_ascending(form_stat, is_pitching=p_category == "pitching")
                    form_shown = form_shown.sort_values("value_num", ascending=ascending, na_position="last").head(20)
                    fig_form = px.bar(form_shown, x="player", y="value_num", color="team",
                                       color_discrete_map=TEAM_COLORS,
                                       category_orders={"player": form_shown["player"].tolist()},
                                       title=f"{form_start} ~ {form_end}  ·  {stat_option_label(form_stat, p_category == 'pitching')} TOP 20")
                    st.plotly_chart(fig_form, use_container_width=True)
                    form_disp = form_df.copy()
                    if "IP" in form_disp.columns:
                        # 계산된 소수 이닝(40.3333..)을 KBO 표기(40.1=40이닝+1아웃)로 되돌린다.
                        form_disp["IP"] = form_disp["IP"].apply(lambda v: format_innings(sabermetrics.parse_innings(v)))
                    st.dataframe(form_disp.rename(columns={"player": "선수", "team": "팀"}), hide_index=True,
                                 use_container_width=True, height=400,
                                 column_config=stat_column_config(form_disp.columns, is_pitching=p_category == "pitching"))

# ============================================================== 선수 검색
if active_tab == "선수 검색":
    st.subheader("선수 검색")
    st.caption(
        "이름으로 선수를 찾아 기본기록·통산기록(시즌별+통산 합계)·경기별/일자별 기록·상황별 기록·수상·등록일수를 "
        "한 번에 봅니다. 1982년부터의 통산·수상·등록일수 기록은 모두 player_id로 동명이인을 "
        "구분합니다(player_id가 없는 극히 일부 옛 시즌 로우는 수상·등록일수 조회에서 빠질 수 있습니다). "
        "경기별·일자별·상황별(홈·원정, 상대팀별) 기록은 네이버 박스스코어를 수집하기 시작한 이번 시즌 것만 "
        "있어 은퇴 선수나 과거 시즌은 비어 있을 수 있습니다."
    )

    search_name = st.text_input("선수 이름 검색 (부분 검색 가능, 예: '김도영')", key="psearch_name")
    if not search_name:
        st.info("이름을 입력해 검색하세요.")
    else:
        candidates = analysis.player_identity_search(history_player_stat, search_name)
        if candidates.empty:
            st.warning("일치하는 선수가 없습니다.")
        else:
            def _cand_label(row):
                cats = "/".join("타자" if c == "batting" else "투수" for c in row["categories"])
                return f"{row['player']} ({row['최근팀']} · {row['첫해']}~{row['막해']} · {cats})"

            candidates = candidates.copy()
            candidates["_label"] = candidates.apply(_cand_label, axis=1)
            pick_label = st.selectbox("선수 선택", candidates["_label"].tolist(), key=f"psearch_pick_{search_name}")
            picked = candidates[candidates["_label"] == pick_label].iloc[0]
            identity = picked["identity"]
            p_cats = list(picked["categories"])
            p_cat = (st.radio("구분", p_cats, horizontal=True, format_func=lambda x: "타자" if x == "batting" else "투수",
                               key=f"psearch_pos_{identity}") if len(p_cats) > 1 else p_cats[0])

            pid = analysis.identity_player_id(identity)
            if pid:
                cur_rows = player_stat[(player_stat["player_id"] == pid) & (player_stat["fetch_date"] == latest_player_date)]
            else:
                cur_rows = player_stat[(player_stat["player"] == picked["player"]) &
                                        (player_stat["team"] == picked["최근팀"]) &
                                        (player_stat["fetch_date"] == latest_player_date)]
            cur_team = cur_rows["team"].iloc[0] if not cur_rows.empty else picked["최근팀"]

            st.divider()
            PSEARCH_SECTIONS = ["기본 기록", "통산 기록", "경기별 기록", "일자별 기록",
                                 "상황별 기록", "수상 · 등록일수"]
            psearch_section = st.radio("항목", PSEARCH_SECTIONS, horizontal=True, key="psearch_section",
                                        label_visibility="collapsed")
            st.divider()

            # -------------------------------------------------- 기본 기록
            if psearch_section == "기본 기록":
                st.subheader(f"{picked['player']} — 기본 기록")
                if cur_rows.empty:
                    st.info(f"{latest_player_date} 기준 현재 시즌 기록이 없습니다 (은퇴 선수이거나 이번 시즌 미출전).")
                else:
                    st.caption(f"{picked['player']} · {cur_team} · 기준일 {latest_player_date}")
                    show_cats = [p_cat, f"{p_cat}_adv"] + (["defense", "baserunning"] if p_cat == "batting" else [])
                    for cat in show_cats:
                        cd = cur_rows[cur_rows["category"] == cat]
                        if cd.empty:
                            continue
                        wide = cd.pivot_table(index="player", columns="stat_name", values="stat_value",
                                               aggfunc="first").reset_index(drop=True)
                        st.markdown(f"**{CATEGORY_LABEL.get(cat, '수비(주 포지션)' if cat == 'defense' else cat)}**")
                        st.dataframe(wide, hide_index=True, use_container_width=True,
                                     column_config=stat_column_config(wide.columns, is_pitching=p_cat == "pitching"))

            # -------------------------------------------------- 통산 기록 (시즌별 + 통산 합계, KBO 공식 페이지 스타일)
            elif psearch_section == "통산 기록":
                st.subheader(f"{picked['player']} — 통산 기록 (1982~)")
                yb = analysis.player_year_by_year(history_player_stat, identity, p_cat)
                career_df = analysis.career_totals(history_player_stat, p_cat)
                career_row = career_df[career_df["identity"] == identity] if "identity" in career_df.columns else pd.DataFrame()
                if yb.empty:
                    st.info("기록이 없습니다.")
                else:
                    career_cols = CAREER_TABLE_COLS_BATTING if p_cat == "batting" else CAREER_TABLE_COLS_PITCHING
                    col_order = ["연도", "팀"] + [c for c in career_cols if c in yb.columns or c in career_row.columns]
                    yb_disp = yb.sort_values("year").rename(columns={"year": "연도", "team": "팀"})
                    for c in col_order:
                        if c not in yb_disp.columns:
                            yb_disp[c] = np.nan
                    yb_disp = yb_disp[col_order]

                    if not career_row.empty:
                        r = career_row.iloc[0]
                        total_row = {"연도": "통산", "팀": f"{int(r['연도수'])}시즌"}
                        for c in col_order[2:]:
                            total_row[c] = r[c] if c in career_row.columns else np.nan
                        combined = pd.concat([yb_disp, pd.DataFrame([total_row])[col_order]], ignore_index=True)
                    else:
                        combined = yb_disp

                    styled = styled_summary_table(combined, "연도", "통산", col_order[2:], border="top")
                    st.dataframe(styled, hide_index=True, use_container_width=True,
                                 height=min(600, 46 + 36 * len(combined)),
                                 column_config=stat_column_config(col_order, is_pitching=p_cat == "pitching"))
                    st.caption("SB·CS·E(도루·수비) 등은 과거 시즌 주루·수비 기록을 따로 수집하지 않아 이 표에는 "
                               "없습니다 — 이번 시즌 것만 '기본 기록' 항목에서 볼 수 있습니다. wOBA·wRC+ 같은 "
                               "세이버메트릭스 지표는 시즌을 단순 합산·평균하는 방식이 맞지 않아 이 표에서는 WAR만 "
                               "통산 합계로 표시합니다 — 연도별 추세는 '과거 세이버메트릭스' 탭에서 볼 수 있습니다.")

            # -------------------------------------------------- 경기별 기록 / 일자별 기록
            elif psearch_section in ("경기별 기록", "일자별 기록"):
                glog = analysis.player_game_log(player_game_stat, games, picked["player"], cur_team, p_cat)
                if glog.empty:
                    st.info("이번 시즌 경기별 박스스코어가 없습니다 (은퇴 선수이거나 아직 수집되지 않았을 수 있습니다 — "
                            "시즌 중 트레이드로 팀을 옮긴 선수는 가장 최근 소속팀 기준으로만 조회됩니다).")
                elif psearch_section == "경기별 기록":
                    st.subheader(f"{picked['player']} — 경기별 기록 ({cur_team}, 이번 시즌)")
                    drop_cols = {"game_id", "player", "team", "home_team", "away_team", "home_score", "away_score", "stadium"}
                    stat_cols = [c for c in glog.columns if c not in drop_cols and c not in ("game_date", "상대", "홈/원정")]
                    show_cols = ["game_date", "상대", "홈/원정"] + stat_cols
                    # KBO 공식 선수 페이지의 '최근 10경기' 표처럼 맨 위에 합계(굵게) 행을 붙인다.
                    totals = glog[stat_cols].apply(pd.to_numeric, errors="coerce").sum()
                    total_row = {**{c: totals[c] for c in stat_cols}, "game_date": "합계", "상대": "-", "홈/원정": "-"}
                    games_sorted = glog.sort_values("game_date", ascending=False)[show_cols]
                    disp = pd.concat([pd.DataFrame([total_row])[show_cols], games_sorted], ignore_index=True)
                    disp = disp.rename(columns={"game_date": "날짜"})

                    styled = styled_summary_table(disp, "날짜", "합계", stat_cols, border="bottom")
                    st.dataframe(styled, hide_index=True, use_container_width=True, height=450,
                                 column_config=stat_column_config(show_cols, is_pitching=p_cat == "pitching"))
                else:
                    st.subheader(f"{picked['player']} — 일자별 누적 추이 ({cur_team}, 이번 시즌)")
                    g2 = glog.sort_values("game_date").copy()
                    if p_cat == "batting":
                        for c in ("AB", "H", "2B", "3B", "HR", "BB", "HBP", "SF", "SO", "RBI"):
                            if c not in g2.columns:
                                g2[c] = 0
                        cum_cols = ["AB", "H", "2B", "3B", "HR", "BB", "HBP", "SF", "SO", "RBI"]
                        g2[cum_cols] = g2[cum_cols].fillna(0).cumsum()
                        tb = g2["H"] + g2["2B"] + 2 * g2["3B"] + 3 * g2["HR"]
                        g2["AVG"] = (g2["H"] / g2["AB"].replace(0, np.nan)).round(3)
                        obp = (g2["H"] + g2["BB"] + g2["HBP"]) / (g2["AB"] + g2["BB"] + g2["HBP"] + g2["SF"]).replace(0, np.nan)
                        g2["SLG"] = (tb / g2["AB"].replace(0, np.nan))
                        g2["OPS"] = (obp + g2["SLG"]).round(3)
                        trend_options = ["AVG", "OPS", "HR", "H", "RBI", "BB", "SO"]
                    else:
                        for c in ("IP", "H", "ER", "BB", "SO", "HR"):
                            if c not in g2.columns:
                                g2[c] = 0
                        cum_cols = ["IP", "H", "ER", "BB", "SO", "HR"]
                        g2[cum_cols] = g2[cum_cols].fillna(0).cumsum()
                        g2["ERA"] = (g2["ER"] * 9 / g2["IP"].replace(0, np.nan)).round(2)
                        g2["WHIP"] = ((g2["BB"] + g2["H"]) / g2["IP"].replace(0, np.nan)).round(2)
                        trend_options = ["ERA", "WHIP", "SO", "IP", "ER"]
                    trend_stat = st.selectbox("추이로 볼 지표 (경기 누적 기준)", trend_options,
                                               format_func=lambda c: stat_option_label(c, p_cat == "pitching"),
                                               key=f"pdaily_stat_{p_cat}")
                    # game_date를 문자열('20260828')로 그대로 넘기면 Plotly가 숫자축으로 오인해
                    # 눈금이 '20.2604M' 식으로 깨지므로 날짜형으로 변환한다(다른 날짜 차트와 동일한 처리).
                    g2["game_date_dt"] = pd.to_datetime(g2["game_date"], format="%Y%m%d")
                    fig_trend = px.line(g2, x="game_date_dt", y=trend_stat, markers=True,
                                         title=f"{picked['player']} 시즌 누적 {stat_option_label(trend_stat, p_cat == 'pitching')} 추이")
                    fig_trend.update_xaxes(tickformat="%y/%m/%d", title="날짜")
                    st.plotly_chart(fig_trend, use_container_width=True)

            # -------------------------------------------------- 상황별 기록
            elif psearch_section == "상황별 기록":
                st.subheader(f"{picked['player']} — 상황별 기록 (이번 시즌)")
                glog = analysis.player_game_log(player_game_stat, games, picked["player"], cur_team, p_cat)
                if glog.empty:
                    st.info("이번 시즌 경기별 박스스코어가 없어 홈/원정·상대팀별 기록을 계산할 수 없습니다.")
                else:
                    st.markdown("**홈 · 원정**")
                    home_away = analysis.player_split_summary(glog, p_cat, "홈/원정")
                    if "IP" in home_away.columns:
                        home_away["IP"] = home_away["IP"].apply(lambda v: format_innings(sabermetrics.parse_innings(v)))
                    st.dataframe(home_away, hide_index=True, use_container_width=True,
                                 column_config=stat_column_config(home_away.columns, is_pitching=p_cat == "pitching"))
                    st.markdown("**상대팀별**")
                    vs_team = analysis.player_split_summary(glog, p_cat, "상대").sort_values("G", ascending=False)
                    if "IP" in vs_team.columns:
                        vs_team["IP"] = vs_team["IP"].apply(lambda v: format_innings(sabermetrics.parse_innings(v)))
                    st.dataframe(vs_team.rename(columns={"상대": "상대팀"}), hide_index=True, use_container_width=True,
                                 column_config=stat_column_config(vs_team.columns, is_pitching=p_cat == "pitching"))
                if p_cat == "batting" and not cur_rows.empty:
                    risp = cur_rows[cur_rows["stat_name"].isin(["RISP", "PH-BA"])]
                    if not risp.empty:
                        st.markdown("**KBO 공식 집계 상황별 기록 (이번 시즌 누적)**")
                        risp_wide = risp.pivot_table(index="player", columns="stat_name", values="stat_value",
                                                      aggfunc="first").reset_index(drop=True)
                        st.dataframe(risp_wide, hide_index=True, use_container_width=True,
                                     column_config=stat_column_config(risp_wide.columns, is_pitching=False))
                        st.caption("RISP=득점권 타율, PH-BA=대타 타율 (KBO 공식 시즌 누적치, 경기별 세부 상황 데이터는 없습니다).")

            # -------------------------------------------------- 수상 · 등록일수
            elif psearch_section == "수상 · 등록일수":
                st.subheader(f"{picked['player']} — 수상 · 등록일수")
                if not pid:
                    st.info("player_id가 없는 선수라 수상·등록일수를 조회할 수 없습니다.")
                else:
                    st.markdown("**수상 경력**")
                    awards = analysis.player_awards(player_award, pid)
                    if awards.empty:
                        st.caption("수상 이력이 없습니다. (KBO MVP·신인상·골든글러브·KBO수비상만 집계되며, "
                                   "올스타전·한국시리즈 MVP는 KBO 사이트가 선수 개인 조회에 포함하지 않습니다.)")
                    else:
                        st.dataframe(awards.rename(columns={"year": "연도", "award": "수상"}),
                                     hide_index=True, use_container_width=True)

                    st.markdown("**KBO 리그 엔트리(1군) 등록일수**")
                    reg = analysis.player_season_reg(player_season_reg, pid)
                    if reg.empty:
                        st.caption("등록일수 기록이 없습니다 — 2001년 이전에 은퇴한 선수는 KBO 사이트 자체에 "
                                   "이 기록이 없는 경우가 많습니다(직접 확인: 2001년 이전 활동 선수 중 "
                                   "약 13%만 등록일수가 조회됨).")
                    else:
                        st.dataframe(
                            reg.rename(columns={"year": "연도", "team": "소속팀", "days": "등록일수", "note": "비고"}),
                            hide_index=True, use_container_width=True,
                            column_config={"등록일수": st.column_config.Column(help="그 해 KBO 리그 1군 엔트리에 등록되어 있던 일수")},
                        )
                        st.caption("비고는 국가대표 차출(WBC·APBC·프리미어12·아시안게임·올림픽 등) 기간이 있으면 표시됩니다.")

# ============================================================== 세이버메트릭스
if active_tab == "세이버메트릭스":
    st.subheader("간이 세이버메트릭스 (자체 계산)")
    st.caption(
        "KBO 공식 기본기록 + 리그 전체 합산치로 직접 계산한 근사 지표입니다. "
        "wOBA는 표준 선형가중치, wRC+는 (wOBA-리그wOBA)/wOBA스케일 + 리그득점/타석 을 100 기준으로 환산, "
        "FIP는 팀 합산 ERA로 역산한 리그 상수를 사용합니다. "
        "타자 WAR는 타격 성과에 KBO 공식 수비기록의 '주 포지션'으로 계산한 포지션 조정치(예: 포수 +12.5, "
        "1루수 −12.5 / 600타석 기준)와 공식 주루기록의 도루 성공/실패로 계산한 득점가치(SB런, 도루 1개 +0.2점 "
        "· 실패 1개 −0.4점 근사)까지 더했지만, 실제 수비 범위·프레이밍 같은 개별 수비력과 도루 외 진루타는 "
        "여전히 반영하지 못합니다. 투수 WAR는 FIP 기반입니다 "
        "(대체선수 수준·승당득점·포지션 조정치·도루 득점가치 모두 KBO로 재보정하지 않은 고정 근사 상수 사용). "
        "파워-스피드 넘버(2×HR×SB/(HR+SB))는 WAR와 무관한 참고용 보조지표입니다. "
        "따라서 Fangraphs·Statiz가 공표하는 공식 WAR·wRC+와는 값이 다를 수 있으니 추세 비교용 참고 지표로만 활용하세요."
    )
    scol1, scol2 = st.columns(2)
    with scol1:
        s_category = st.radio("구분", ["batting_adv", "pitching_adv"], horizontal=True,
                               format_func=lambda x: "타자" if x == "batting_adv" else "투수", key="saber_cat")
    s_df = player_stat[(player_stat["category"] == s_category) & (player_stat["fetch_date"] == latest_player_date)]
    saber_qualified = analysis.qualified_players(
        player_stat, standings, latest_player_date, "batting" if s_category == "batting_adv" else "pitching")
    s_df = s_df[s_df.apply(lambda r: (r["player"], r["team"]) in saber_qualified, axis=1)]
    st.caption("규정타석/규정이닝을 채운 선수만 대상으로 합니다 (표본이 적으면 비율 지표가 극단적으로 왜곡되기 때문).")
    stat_options = sorted(s_df["stat_name"].unique().tolist())
    saber_is_pitching = s_category == "pitching_adv"
    if stat_options:
        with scol2:
            saber_stat = st.selectbox("지표", stat_options,
                                       format_func=lambda c: stat_option_label(c, saber_is_pitching), key="saber_stat")
        lb = s_df[s_df["stat_name"] == saber_stat].copy()
        lb["value_num"] = pd.to_numeric(lb["stat_value"], errors="coerce")
        ascending = sort_ascending(saber_stat, is_pitching=saber_is_pitching)
        lb = lb.sort_values("value_num", ascending=ascending).head(20)
        fig_lb = px.bar(lb, x="player", y="value_num", color="team", color_discrete_map=TEAM_COLORS,
                         category_orders={"player": lb["player"].tolist()},
                         title=f"{stat_option_label(saber_stat, saber_is_pitching)} TOP 20", text="stat_value")
        st.plotly_chart(fig_lb, use_container_width=True)
    else:
        st.info("고급지표가 아직 계산되지 않았습니다.")

# ============================================================== 과거 세이버메트릭스
if active_tab == "과거 세이버메트릭스":
    st.subheader("과거 세이버메트릭스 (연도별)")
    st.caption(
        "지금 시즌만 보여주는 '세이버메트릭스' 탭과 같은 공식·같은 근사 상수로, 집계 가능한 과거 연도까지 "
        "확장해 연도를 골라 볼 수 있게 한 탭입니다. 규정타석/규정이닝(그 해 그 팀의 경기수 기준)을 채운 "
        "선수만 대상으로 하며, wOBA·wRC+·WAR·FIP 계산 방식과 한계는 '세이버메트릭스' 탭 설명을 그대로 "
        "따릅니다 — 타자 WAR는 포지션·도루 조정치를 반영하지만(현재 시즌 기준 수비기록으로 계산해 과거 "
        "시즌은 이 조정이 빠진 순수 타격 기여치에 가깝습니다), 개별 수비력·구장 보정은 여전히 근사치입니다."
    )
    if history_player_stat.empty:
        st.info("아직 과거 데이터가 없습니다. `python fetch_kbo.py --skip-daily --full-history 1982 2025` 로 백필하세요.")
    else:
        adv_years = sorted(history_player_stat[
            history_player_stat["category"].isin(["batting_adv", "pitching_adv"])
        ]["year"].unique().tolist())
        hscol1, hscol2, hscol3 = st.columns(3)
        with hscol1:
            hs_year = st.selectbox("연도", adv_years, index=len(adv_years) - 1, key="hsaber_year")
        with hscol2:
            hs_pos = st.radio("구분", ["batting", "pitching"], horizontal=True,
                               format_func=lambda x: "타자" if x == "batting" else "투수", key="hsaber_pos")
        hs_category = f"{hs_pos}_adv"
        hs_stat_options = HIST_BATTING_ADV_LEADER_STATS if hs_pos == "batting" else HIST_PITCHING_ADV_LEADER_STATS
        with hscol3:
            hs_stat = st.selectbox("지표", hs_stat_options,
                                    format_func=lambda c: stat_option_label(c, hs_pos == "pitching"),
                                    key=f"hsaber_stat_{hs_pos}")
        hs_lb = analysis.single_season_leaders(history_player_stat, history, hs_category, hs_stat,
                                                qualified_only=True, top_n=20, year=hs_year)
        if hs_lb.empty:
            st.info("이 연도/지표 조합에는 규정타석·이닝을 채운 선수가 없습니다.")
        else:
            fig_hs = px.bar(hs_lb, x="player", y="stat_value", color="team", color_discrete_map=TEAM_COLORS,
                             category_orders={"player": hs_lb["player"].tolist()},
                             title=f"{hs_year}년 {stat_option_label(hs_stat, hs_pos == 'pitching')} TOP {len(hs_lb)}")
            st.plotly_chart(fig_hs, use_container_width=True)
            st.caption(hs_lb.iloc[0]["참고"])
            st.dataframe(
                hs_lb.rename(columns={"year": "연도", "player": "선수", "team": "팀",
                                       "stat_value": hs_stat, "참고": "참고(오늘날과 비교)"}),
                hide_index=True, use_container_width=True,
            )

# ============================================================== 경기 결과
if active_tab == "경기 결과":
    st.subheader("경기 결과")
    sel_teams = st.multiselect("팀 필터 (미선택 시 전체)", teams, key="games_team_filter")
    g = games.copy()
    if sel_teams:
        g = g[g["home_team"].isin(sel_teams) | g["away_team"].isin(sel_teams)]
    g = g.sort_values("game_date", ascending=False).reset_index(drop=True)
    g["결과"] = g["status"].map(STATUS_LABEL).fillna(g["status"])
    g.loc[g["status"] != "RESULT", ["away_score", "home_score"]] = np.nan
    g_display = g.rename(columns={
        "game_date": "날짜", "away_team": "원정", "away_score": "원정점수",
        "home_team": "홈", "home_score": "홈점수", "stadium": "구장", "game_time": "시간",
    })[["날짜", "시간", "원정", "원정점수", "홈", "홈점수", "구장", "결과"]]
    g_display["원정점수"] = g_display["원정점수"].apply(lambda x: "" if pd.isna(x) else f"{x:.0f}")
    g_display["홈점수"] = g_display["홈점수"].apply(lambda x: "" if pd.isna(x) else f"{x:.0f}")
    st.dataframe(g_display, hide_index=True, use_container_width=True, height=450,
                 key=f"games_table_{tuple(sorted(sel_teams))}")

    st.subheader("경기 세부 정보")
    st.caption("날짜를 선택하면 그날 열린 모든 경기의 선발/승패투수, 중계 등 세부 정보가 표시됩니다.")

    date_options = sorted(g["game_date"].unique().tolist(), reverse=True)
    if date_options:
        def _date_label(d):
            n = len(g[g["game_date"] == d])
            return f"{d[:4]}-{d[4:6]}-{d[6:8]} ({n}경기)"

        date_pick = st.selectbox("날짜 선택", date_options, format_func=_date_label, key="game_detail_date")
        day_games = g[g["game_date"] == date_pick].sort_values("game_time")
        for _, row in day_games.iterrows():
            st.markdown(f"#### {row['away_team']} @ {row['home_team']}")
            dcol1, dcol2, dcol3 = st.columns(3)
            with dcol1:
                score_txt = f"{row['away_score']:.0f} : {row['home_score']:.0f}" if pd.notna(row["away_score"]) else "-"
                st.metric("스코어 (원정:홈)", score_txt)
                status_label = STATUS_LABEL.get(row["status"], row["status"])
                status_info = row.get("status_info")
                extra = f" · {status_info}" if pd.notna(status_info) and status_info and status_info != status_label else ""
                st.caption(f"상태: {status_label}{extra}")
            with dcol2:
                st.markdown(f"**선발투수**  \n원정 {row.get('away_starter') or '-'} · 홈 {row.get('home_starter') or '-'}")
                if row["status"] == "RESULT":
                    st.markdown(f"**승/패 투수**  \n승 {row.get('win_pitcher') or '-'} · 패 {row.get('lose_pitcher') or '-'}")
            with dcol3:
                st.markdown(f"**구장**  \n{row.get('stadium') or '-'}")
                st.markdown(f"**중계**  \n{row.get('broadcast') or '-'}")
            st.divider()

# ============================================================== 역대 기록
if active_tab == "역대 기록":
    st.subheader("역대 기록")
    st.caption("KBO 프로야구 원년(1982년)부터 지금까지의 팀·선수 기록을 모아뒀습니다. 아래에서 보고 싶은 항목을 고르세요.")

    if not history_player_stat.empty:
        hl_avg = analysis.single_season_leaders(history_player_stat, history, "batting", "AVG", top_n=1)
        hl_hr = analysis.single_season_leaders(history_player_stat, history, "batting", "HR", top_n=1)
        hl_era = analysis.single_season_leaders(history_player_stat, history, "pitching", "ERA", top_n=1)
        hl_bat_career = analysis.career_totals(history_player_stat, "batting")
        hl_pit_career = analysis.career_totals(history_player_stat, "pitching")
        hl_hr_c = hl_bat_career.sort_values("HR", ascending=False).iloc[0]
        hl_h_c = hl_bat_career.sort_values("H", ascending=False).iloc[0]
        hl_w_c = hl_pit_career.sort_values("W", ascending=False).iloc[0]
        hl_so_c = hl_pit_career.sort_values("SO", ascending=False).iloc[0]
        hl_war_bat_c = hl_bat_career.sort_values("WAR", ascending=False).iloc[0]
        hl_war_pit_c = hl_pit_career.sort_values("WAR", ascending=False).iloc[0]
        hl_wrc = analysis.single_season_leaders(history_player_stat, history, "batting_adv", "wRC+", top_n=1)
        hl_fip = analysis.single_season_leaders(history_player_stat, history, "pitching_adv", "FIP", top_n=1)

        hcol1, hcol2, hcol3, hcol4 = st.columns(4)
        with hcol1:
            st.metric("역대 단일시즌 최고 타율", f"{hl_avg.iloc[0]['stat_value']:.3f}",
                       f"{hl_avg.iloc[0]['player']} · {hl_avg.iloc[0]['year']}")
            st.caption(hl_avg.iloc[0]["참고"])
        with hcol2:
            st.metric("역대 단일시즌 최다 홈런", f"{int(hl_hr.iloc[0]['stat_value'])}개",
                       f"{hl_hr.iloc[0]['player']} · {hl_hr.iloc[0]['year']}")
            st.caption(hl_hr.iloc[0]["참고"])
        with hcol3:
            st.metric("역대 단일시즌 최저 ERA", f"{hl_era.iloc[0]['stat_value']:.2f}",
                       f"{hl_era.iloc[0]['player']} · {hl_era.iloc[0]['year']}")
            st.caption(hl_era.iloc[0]["참고"])
        with hcol4:
            st.metric("통산 홈런 1위", f"{int(hl_hr_c['HR'])}개", hl_hr_c["player"])
        hcol5, hcol6, hcol7, hcol8 = st.columns(4)
        with hcol5:
            st.metric("통산 안타 1위", f"{int(hl_h_c['H'])}개", hl_h_c["player"])
        with hcol6:
            st.metric("통산 다승 1위", f"{int(hl_w_c['W'])}승", hl_w_c["player"])
        with hcol7:
            st.metric("통산 탈삼진 1위", f"{int(hl_so_c['SO'])}개", hl_so_c["player"])
        with hcol8:
            st.metric("역대 백필 시즌 수", f"{len(sorted(history_player_stat['year'].unique()))}개 시즌",
                       f"{min(history_player_stat['year'])}~{max(history_player_stat['year'])}")
        hcol9, hcol10, hcol11, hcol12 = st.columns(4)
        with hcol9:
            st.metric("통산 WAR 1위 (타자)", f"{hl_war_bat_c['WAR']:.1f}", hl_war_bat_c["player"])
        with hcol10:
            st.metric("통산 WAR 1위 (투수)", f"{hl_war_pit_c['WAR']:.1f}", hl_war_pit_c["player"])
        with hcol11:
            st.metric("역대 단일시즌 최고 wRC+", f"{int(hl_wrc.iloc[0]['stat_value'])}",
                       f"{hl_wrc.iloc[0]['player']} · {hl_wrc.iloc[0]['year']}")
        with hcol12:
            st.metric("역대 단일시즌 최저 FIP", f"{hl_fip.iloc[0]['stat_value']:.2f}",
                       f"{hl_fip.iloc[0]['player']} · {hl_fip.iloc[0]['year']}")
        st.caption("WAR·wRC+·FIP는 KBO 공식 기본기록으로 저희가 직접 계산한 근사 세이버메트릭스입니다 "
                   "(세이버메트릭스 탭 설명 참고) — 타자 WAR는 옛 시즌일수록 포지션·주루 조정치가 빠진 "
                   "순수 타격 기여치에 가깝습니다.")
        st.divider()

    HIST_SECTIONS = ["시즌 순위", "팀 기록", "선수 기록", "리그 트렌드", "역대 단일시즌 최고", "통산 기록"]
    hist_section = st.radio("항목", HIST_SECTIONS, horizontal=True, key="hist_section", label_visibility="collapsed")
    st.divider()

    # -------------------------------------------------- 시즌 순위
    if hist_section == "시즌 순위":
        st.subheader("연도별 최종 순위")
        if history.empty:
            st.info("과거 시즌 데이터가 없습니다. `python fetch_kbo.py --skip-daily --history 2016 2025` 로 백필하세요.")
        else:
            years = sorted(history["year"].unique().tolist())
            year_pick = st.selectbox("연도", years, index=len(years) - 1, key="hist_year")
            h_year = history[history["year"] == year_pick].sort_values("rank")
            st.dataframe(
                h_year[["rank", "team", "games", "wins", "losses", "draws", "win_pct", "games_behind"]]
                .rename(columns={"rank": "순위", "team": "팀", "games": "경기", "wins": "승",
                                  "losses": "패", "draws": "무", "win_pct": "승률", "games_behind": "게임차"}),
                hide_index=True, use_container_width=True,
            )

            st.subheader("연도별 승률 추이 (팀 비교)")
            fig_hist = px.line(history.sort_values("year"), x="year", y="win_pct", color="team", markers=True,
                                color_discrete_map=TEAM_COLORS)
            st.plotly_chart(fig_hist, use_container_width=True)

    # -------------------------------------------------- 팀 기록
    if hist_section == "팀 기록":
        st.subheader("역대 팀 기록")
        st.caption("KBO 팀 기록(집계) 페이지 자체가 2001년부터만 연도 조회를 지원합니다.")
        if history_team_stat.empty:
            st.info("아직 없습니다. `python fetch_kbo.py --skip-daily --full-history 1982 2025` 로 백필하세요.")
        else:
            team_years = sorted(history_team_stat["year"].unique().tolist())
            thcol0, thcol1, thcol2 = st.columns(3)
            with thcol0:
                th_kind = st.radio("종류", ["기본기록", "세이버메트릭스"], horizontal=True, key="hist_team_kind")
            with thcol1:
                th_pos = st.radio("구분", ["batting", "pitching"], horizontal=True,
                                   format_func=lambda x: "타자" if x == "batting" else "투수", key="hist_team_cat")
            th_category = th_pos if th_kind == "기본기록" else f"{th_pos}_adv"
            with thcol2:
                th_year = st.selectbox("연도", team_years, index=len(team_years) - 1, key="hist_team_year")
            th_df = history_team_stat[(history_team_stat["year"] == th_year) & (history_team_stat["category"] == th_category)]
            th_pivot = th_df.pivot_table(index="team", columns="stat_name", values="stat_value",
                                          aggfunc="first", sort=False).reset_index()
            if th_pivot.empty:
                st.info("이 조합엔 계산된 기록이 없습니다.")
            else:
                st.dataframe(th_pivot.rename(columns={"team": "팀"}), hide_index=True, use_container_width=True,
                             column_config=stat_column_config(th_pivot.columns, is_pitching=th_pos == "pitching"))

    # -------------------------------------------------- 선수 기록
    if hist_section == "선수 기록":
        st.subheader("역대 선수 기록")
        st.caption("KBO 프로야구 원년(1982년)부터의 선수 개인기록입니다. KBO 사이트가 애초에 계산하지 않은 "
                   "지표는 그 이전 연도에 전부 0으로 채워져 있습니다 — 타자는 IBB·MH·PH-BA·RISP, 투수는 "
                   "피2루타·피3루타·BK·IBB·NP·QS·SAC·SF·WP·WHIP이 2001년부터, HLD는 2000년부터, BSV는 "
                   "2006년부터 실제 값입니다. AVG·ERA 등 나머지 지표는 1982년부터 정상입니다.")
        if history_player_stat.empty:
            st.info("아직 없습니다. `python fetch_kbo.py --skip-daily --full-history 1982 2025` 로 백필하세요.")
        else:
            player_years = sorted(history_player_stat["year"].unique().tolist())
            phcol0, phcol1, phcol2, phcol3 = st.columns([1, 1, 1, 2])
            with phcol0:
                ph_kind = st.radio("종류", ["기본기록", "세이버메트릭스"], horizontal=True, key="hist_player_kind")
            with phcol1:
                ph_pos = st.radio("구분", ["batting", "pitching"], horizontal=True,
                                   format_func=lambda x: "타자" if x == "batting" else "투수", key="hist_player_cat")
            ph_category = ph_pos if ph_kind == "기본기록" else f"{ph_pos}_adv"
            ph_is_pitching = ph_pos == "pitching"
            with phcol2:
                ph_year = st.selectbox("연도", player_years, index=len(player_years) - 1, key="hist_player_year")
            with phcol3:
                ph_search = st.text_input("선수명 검색", key="hist_player_search")

            ph_df = history_player_stat[(history_player_stat["year"] == ph_year) & (history_player_stat["category"] == ph_category)]
            ph_pivot = ph_df.pivot_table(index=["player", "team"], columns="stat_name", values="stat_value",
                                          aggfunc="first", sort=False).reset_index()
            if ph_search:
                ph_pivot = ph_pivot[ph_pivot["player"].str.contains(ph_search, na=False)]
            if ph_pivot.empty:
                st.info("이 조합엔 계산된 기록이 없습니다 (세이버메트릭스는 계산에 필요한 리그 데이터가 있는 "
                        "연도만 존재합니다).")
            else:
                st.caption(f"{len(ph_pivot)}명 표시 — 표 헤더를 클릭하면 그 지표 기준으로 정렬할 수 있습니다.")
                st.dataframe(ph_pivot.rename(columns={"player": "선수", "team": "팀"}), hide_index=True,
                             use_container_width=True, height=450,
                             key=f"hist_player_table_{ph_year}_{ph_category}_{ph_search}",
                             column_config=stat_column_config(ph_pivot.columns, is_pitching=ph_is_pitching))

                stat_options = sorted(ph_df["stat_name"].unique().tolist())
                if stat_options:
                    ph_stat = st.selectbox("지표 리더보드", stat_options,
                                            format_func=lambda c: stat_option_label(c, ph_is_pitching),
                                            key=f"hist_player_stat_{ph_category}")
                    lb = ph_df[ph_df["stat_name"] == ph_stat].copy()
                    if ph_stat == "IP":
                        lb["value_num"] = lb["stat_value"].apply(sabermetrics.parse_innings)
                    else:
                        lb["value_num"] = pd.to_numeric(lb["stat_value"], errors="coerce")
                    ascending = sort_ascending(ph_stat, is_pitching=ph_is_pitching)
                    lb = lb.sort_values("value_num", ascending=ascending, na_position="last").head(20)
                    fig_hlb = px.bar(lb, x="player", y="value_num", color="team", color_discrete_map=TEAM_COLORS,
                                      category_orders={"player": lb["player"].tolist()}, text="stat_value",
                                      title=f"{ph_year}년 {stat_option_label(ph_stat, ph_is_pitching)} TOP 20")
                    st.plotly_chart(fig_hlb, use_container_width=True)

    # -------------------------------------------------- 리그 트렌드
    if hist_section == "리그 트렌드":
        st.subheader("리그 득점 환경 추이 (타고투저 · 투고타저)")
        if history_player_stat.empty:
            st.info("아직 없습니다. `python fetch_kbo.py --skip-daily --full-history 1982 2025` 로 백필하세요.")
        else:
            st.caption("그 해 뛴 모든 선수의 기록을 리그 전체로 합산해 계산한 연도별 평균입니다 — 어느 시기가 "
                       "타자에게 유리했고(타고투저) 어느 시기가 투수에게 유리했는지(투고타저) 한눈에 보입니다.")
            era_trend = analysis.league_era_trend(history_player_stat)
            ecol1, ecol2 = st.columns(2)
            with ecol1:
                fig_era1 = px.line(era_trend, x="year", y=["lg_avg", "lg_ops"], markers=True,
                                    title="리그 평균 타율 · OPS 추이")
                st.plotly_chart(fig_era1, use_container_width=True)
            with ecol2:
                fig_era2 = px.line(era_trend, x="year", y="lg_era", markers=True, title="리그 평균자책점(ERA) 추이")
                st.plotly_chart(fig_era2, use_container_width=True)

    # -------------------------------------------------- 역대 단일시즌 최고
    if hist_section == "역대 단일시즌 최고":
        st.subheader("역대 단일시즌 최고 기록")
        if history_player_stat.empty:
            st.info("아직 없습니다. `python fetch_kbo.py --skip-daily --full-history 1982 2025` 로 백필하세요.")
        else:
            st.caption("1982년부터 지금까지 모든 시즌·선수를 통틀어 그 지표에서 가장 뛰어났던 시즌 TOP 15입니다. "
                       "타율·OPS·ERA 등 비율 지표는 그 해 규정타석/이닝을 채운 시즌만 대상으로 합니다. '참고' 열에 "
                       "비율 지표는 그 해와 최신연도의 리그 평균(타석/이닝 가중평균)을, 카운팅 스탯은 그 해 "
                       "경기수 기준 144경기 환산치를 붙여 지금 기준으로 어느 정도인지 가늠할 수 있게 했습니다 "
                       "(옛 시즌은 80~130경기로 지금(144경기)보다 짧아 그대로 비교하면 불리합니다).")
            sscol0, sscol1, sscol2 = st.columns(3)
            with sscol0:
                ssl_kind = st.radio("종류", ["기본기록", "세이버메트릭스"], horizontal=True, key="ssl_kind")
            with sscol1:
                ssl_pos = st.radio("구분", ["batting", "pitching"], horizontal=True,
                                    format_func=lambda x: "타자" if x == "batting" else "투수", key="ssl_cat")
            ssl_category = ssl_pos if ssl_kind == "기본기록" else f"{ssl_pos}_adv"
            if ssl_kind == "기본기록":
                ssl_options = HIST_BATTING_LEADER_STATS if ssl_pos == "batting" else HIST_PITCHING_LEADER_STATS
            else:
                ssl_options = HIST_BATTING_ADV_LEADER_STATS if ssl_pos == "batting" else HIST_PITCHING_ADV_LEADER_STATS
            with sscol2:
                ssl_stat = st.selectbox("지표", ssl_options,
                                         format_func=lambda c: stat_option_label(c, ssl_pos == "pitching"),
                                         key=f"ssl_stat_{ssl_kind}_{ssl_pos}")
            ssl_df = analysis.single_season_leaders(history_player_stat, history, ssl_category, ssl_stat)
            ssl_disp = ssl_df.copy()
            if ssl_stat == "IP":
                ssl_disp["stat_value"] = ssl_disp["stat_value"].apply(format_innings)
            st.dataframe(
                ssl_disp.rename(columns={"year": "연도", "player": "선수", "team": "팀", "stat_value": ssl_stat}),
                hide_index=True, use_container_width=True,
            )

    # -------------------------------------------------- 통산 기록
    if hist_section == "통산 기록":
        st.subheader("통산 기록 (1982~)")
        if history_player_stat.empty:
            st.info("아직 없습니다. `python fetch_kbo.py --skip-daily --full-history 1982 2025` 로 백필하세요.")
        else:
            st.caption("선수 목록 페이지에 담긴 KBO 내부 고유 선수번호로 동명이인을 구분해 합산합니다 — 예를 들어 "
                       "'박병호'라는 이름으로 1989~1996년 해태 소속 선수와 2005년 이후 활동한 유명 슬러거가 실제로는 "
                       "다른 사람인데 이름만 같아서 섞였던 것을 이 번호로 바로잡았습니다(김태균도 같은 문제가 있었습니다). "
                       "고유번호가 없는 극소수 옛날 기록만 이름으로 대체 구분하니, 연도수·첫해·막해를 같이 표시합니다. "
                       "IBB·희생타(SAC)·희생플라이(SF) 등 일부 지표는 KBO 사이트가 2001년 이전 값을 아예 제공하지 "
                       "않아, 그 이전에 뛴 시즌만큼은 통산 합계에서 빠져 있습니다(AVG·OPS·ERA·WHIP처럼 저희가 직접 "
                       "계산하는 지표는 영향 없습니다). WAR은 시즌별로 계산해 둔 값을 그대로 더한 통산 누적치입니다 "
                       "— 포지션·주루 조정치는 최근 시즌만 반영되므로(수비·주루 기록을 옛 시즌까지는 모으지 "
                       "못했습니다), 옛 시즌 비중이 큰 선수일수록 순수 타격/투구 기여만 담긴 근사치에 가깝습니다.")
            ctcol1, ctcol2 = st.columns(2)
            with ctcol1:
                ct_category = st.radio("구분", ["batting", "pitching"], horizontal=True,
                                        format_func=lambda x: "타자" if x == "batting" else "투수", key="ct_cat")
            ct_df = analysis.career_totals(history_player_stat, ct_category)
            ct_stat_options = [c for c in ct_df.columns if c not in ("player", "연도수", "첫해", "막해")]
            with ctcol2:
                ct_stat = st.selectbox("정렬 지표", sorted(ct_stat_options),
                                        format_func=lambda c: stat_option_label(c, ct_category == "pitching"),
                                        key="ct_stat")

            ct_rate_stats = ("AVG", "OBP", "SLG", "OPS") if ct_category == "batting" else ("ERA", "WHIP", "WPCT")
            ct_vol_col = "AB" if ct_category == "batting" else "IP"
            if ct_stat in ct_rate_stats:
                st.caption(f"'{ct_stat}'는 비율 지표라 통산 {'타수' if ct_category == 'batting' else '이닝'}가 "
                           f"적은 선수가 표본 왜곡으로 순위 위쪽에 뜰 수 있습니다 — 최소 {'타수' if ct_category == 'batting' else '이닝'}를 "
                           f"정해서 걸러내세요.")
                ct_default_min = 1000.0 if ct_category == "batting" else 500.0
                ct_max_vol = float(ct_df[ct_vol_col].max())
                ct_min_vol = st.slider(f"최소 {'타수(AB)' if ct_category == 'batting' else '이닝(IP)'}",
                                        0.0, ct_max_vol, min(ct_default_min, ct_max_vol),
                                        key=f"ct_min_vol_{ct_category}")
                ct_df_shown = ct_df[ct_df[ct_vol_col] >= ct_min_vol]
            else:
                ct_df_shown = ct_df

            ct_ascending = sort_ascending(ct_stat, is_pitching=ct_category == "pitching")
            ct_shown = ct_df_shown.sort_values(ct_stat, ascending=ct_ascending, na_position="last").head(30)
            st.caption(f"{len(ct_df_shown)}명 대상 중 상위 30명 표시.")
            ct_disp = ct_shown[["player", "연도수", "첫해", "막해"] + sorted(ct_stat_options)].rename(columns={"player": "선수"})
            if "IP" in ct_disp.columns:
                ct_disp["IP"] = ct_disp["IP"].apply(format_innings)
            st.dataframe(
                ct_disp, hide_index=True, use_container_width=True, height=450,
                column_config=stat_column_config(ct_stat_options, is_pitching=ct_category == "pitching"),
            )

# ============================================================== 뉴스
if active_tab == "뉴스":
    st.subheader("KBO 뉴스")
    n_search = st.text_input("제목/내용 검색 (예: 부상, 트레이드)", key="news_search")
    n = news.copy()
    if n_search:
        n = n[n["title"].str.contains(n_search, na=False) | n["summary"].str.contains(n_search, na=False)]
    for _, row in n.head(30).iterrows():
        st.markdown(f"**[{row['title']}]({row['url']})**  ·  {row['date']}")
        st.caption(row["summary"])
        st.divider()

# ============================================================== 팀 상세
if active_tab == "팀 상세":
    st.subheader("팀 상세 보기")
    pick = st.selectbox("팀 선택", teams, key="team_detail_pick")
    tcol1, tcol2 = st.columns(2)
    with tcol1:
        st.markdown(f"**{pick} 타격 기록(팀 합계)**")
        b = team_stat[(team_stat["category"] == "batting") & (team_stat["team"] == pick) & (team_stat["fetch_date"] == latest_stat_date)]
        b_t = b[["stat_name", "stat_value"]].set_index("stat_name").T.reset_index(drop=True)
        st.dataframe(b_t, hide_index=True, use_container_width=True,
                     column_config=stat_column_config(b_t.columns, is_pitching=False))
    with tcol2:
        st.markdown(f"**{pick} 투구 기록(팀 합계)**")
        p = team_stat[(team_stat["category"] == "pitching") & (team_stat["team"] == pick) & (team_stat["fetch_date"] == latest_stat_date)]
        p_t = p[["stat_name", "stat_value"]].set_index("stat_name").T.reset_index(drop=True)
        st.dataframe(p_t, hide_index=True, use_container_width=True,
                     column_config=stat_column_config(p_t.columns, is_pitching=True))

    st.markdown(f"**{pick} 소속 선수 (타자)**")
    pb = player_stat[(player_stat["category"] == "batting") & (player_stat["team"] == pick) & (player_stat["fetch_date"] == latest_player_date)]
    pb_pivot = pb.pivot_table(index="player", columns="stat_name", values="stat_value", aggfunc="first", sort=False).reset_index()
    st.dataframe(pb_pivot.rename(columns={"player": "선수"}), hide_index=True, use_container_width=True,
                 key=f"team_detail_batters_{pick}",
                 column_config=stat_column_config(pb_pivot.columns, is_pitching=False))

    st.markdown(f"**{pick} 소속 선수 (투수)**")
    pp = player_stat[(player_stat["category"] == "pitching") & (player_stat["team"] == pick) & (player_stat["fetch_date"] == latest_player_date)]
    pp_pivot = pp.pivot_table(index="player", columns="stat_name", values="stat_value", aggfunc="first", sort=False).reset_index()
    st.dataframe(pp_pivot.rename(columns={"player": "선수"}), hide_index=True, use_container_width=True,
                 key=f"team_detail_pitchers_{pick}",
                 column_config=stat_column_config(pp_pivot.columns, is_pitching=True))

    st.markdown(f"**{pick} 최근 경기**")
    tg = games[(games["home_team"] == pick) | (games["away_team"] == pick)].sort_values("game_date", ascending=False).head(10)
    tg["결과"] = tg["status"].map(STATUS_LABEL).fillna(tg["status"])
    tg.loc[tg["status"] != "RESULT", ["away_score", "home_score"]] = np.nan
    tg_display = tg.rename(columns={
        "game_date": "날짜", "away_team": "원정", "away_score": "원정점수",
        "home_team": "홈", "home_score": "홈점수", "stadium": "구장",
    })[["날짜", "원정", "원정점수", "홈", "홈점수", "구장", "결과"]]
    tg_display["원정점수"] = tg_display["원정점수"].apply(lambda x: "" if pd.isna(x) else f"{x:.0f}")
    tg_display["홈점수"] = tg_display["홈점수"].apply(lambda x: "" if pd.isna(x) else f"{x:.0f}")
    st.dataframe(tg_display, hide_index=True, use_container_width=True)

with st.expander("수집 로그"):
    st.dataframe(fetch_log, hide_index=True, use_container_width=True)
