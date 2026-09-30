"""수집된 KBO 데이터로 계산하는 2차 분석 지표.

DB에 새로 저장하지 않고, 대시보드가 이미 불러온 DataFrame으로부터 그때그때
계산한다 (표준 세이버메트릭스 공식과 KBO 표기 관례를 따른 파생 지표들).
"""

import numpy as np
import pandas as pd

import sabermetrics

PA_PER_GAME = 3.1  # KBO 규정타석: 팀 경기수 x 3.1
IP_PER_GAME = 1.0  # KBO 규정이닝: 팀 경기수 x 1


def qualified_players(player_stat: pd.DataFrame, standings: pd.DataFrame, fetch_date: str, category: str):
    """규정타석(타자)/규정이닝(투수)을 채운 (player, team) 집합을 반환한다."""
    stat_name = "PA" if category == "batting" else "IP"
    per_game = PA_PER_GAME if category == "batting" else IP_PER_GAME

    df = player_stat[(player_stat.fetch_date == fetch_date) & (player_stat.category == category) &
                      (player_stat.stat_name == stat_name)][["player", "team", "stat_value"]].copy()
    if df.empty:
        return set()
    if category == "batting":
        df["value"] = pd.to_numeric(df["stat_value"], errors="coerce")
    else:
        df["value"] = df["stat_value"].apply(sabermetrics.parse_innings)

    g = standings[standings.fetch_date == fetch_date][["team", "games"]]
    df = df.merge(g, on="team", how="left")
    df["min_required"] = df["games"] * per_game
    qualified = df[df["value"] >= df["min_required"]]
    return set(zip(qualified["player"], qualified["team"]))


def recent_form(player_game_stat: pd.DataFrame, start_date: str, end_date: str, category: str):
    """start_date~end_date(포함) 구간에 열린 경기들의 선수별 박스스코어를 모두 합산해 그 구간의 활약을 계산.

    KBO 공식 사이트는 선수기록을 '시즌 누적'으로만 보여주고 특정 기간 기준 조회 기능이 없다
    (사이트에 있는 월별 필터를 시도해봤지만 실제로는 필터링되지 않고 시즌 누적치를 그대로 반환해
    신뢰할 수 없었다). 대신 네이버 스포츠의 경기별 박스스코어(player_game_stat, fetch_kbo.py가
    매일 새로 열린 경기를 채워 넣는다)를 구간별로 직접 합산한다 — 시즌 개막일부터의 모든 경기를
    갖고 있어서 처음부터 임의의 기간을 조회할 수 있다 (일별 누적 스냅샷을 여러 날 모아야 했던
    이전 방식과 달리 대기 시간이 필요 없다).

    category: 'batting' 또는 'pitching'.
    반환: [player, team, G, <카운팅 스탯 합계>, <비율 스탯>] wide-format DataFrame

    타자의 2B·3B·HBP·SF·SAC는 박스스코어 JSON에 직접 없어, 이닝별 결과 텍스트('좌2'=2루타,
    '사구'=몸에 맞는 공 등)를 파싱해 센다 — 공식 KBO 시즌 누적치와 대조 검증했고(2루타 기준
    334명 중 313명 완전 일치, 나머지는 대부분 ±1~2개 오차), 트레이드로 팀이 바뀐 선수는 팀별로
    쪼개져 보이지만 합산하면 공식치와 정확히 맞는다.
    """
    df = player_game_stat[
        (player_game_stat.category == category) &
        (player_game_stat.game_date >= start_date) & (player_game_stat.game_date <= end_date)
    ].copy()
    if df.empty:
        return pd.DataFrame()

    df["stat_value"] = pd.to_numeric(df["stat_value"], errors="coerce")
    g_count = df.groupby(["player", "team"])["game_id"].nunique().rename("G")
    totals = df.pivot_table(index=["player", "team"], columns="stat_name", values="stat_value", aggfunc="sum")
    totals = totals.join(g_count)

    def _safe_div(num, den):
        return (num / den.replace(0, np.nan)).round(3)

    if category == "batting":
        for c in ("2B", "3B", "HBP", "SF", "SAC"):
            if c not in totals.columns:
                totals[c] = 0
        pa = totals["AB"] + totals["BB"] + totals["HBP"] + totals["SF"] + totals["SAC"]
        tb = totals["H"] + totals["2B"] + 2 * totals["3B"] + 3 * totals["HR"]
        totals["AVG"] = _safe_div(totals["H"], totals["AB"])
        totals["OBP"] = _safe_div(totals["H"] + totals["BB"] + totals["HBP"], totals["AB"] + totals["BB"] + totals["HBP"] + totals["SF"])
        totals["SLG"] = _safe_div(tb, totals["AB"])
        totals["OPS"] = (totals["OBP"] + totals["SLG"]).round(3)
        totals["BB%"] = (totals["BB"] / pa.replace(0, np.nan) * 100).round(1)
        totals["K%"] = (totals["SO"] / pa.replace(0, np.nan) * 100).round(1)
        totals = totals[totals["AB"] > 0]
    else:
        totals["ERA"] = (totals["ER"] * 9 / totals["IP"].replace(0, np.nan)).round(2)
        totals["WHIP"] = _safe_div(totals["BB"] + totals["H"], totals["IP"])
        totals["K%"] = (totals["SO"] / totals["TBF"].replace(0, np.nan) * 100).round(1)
        totals["BB%"] = (totals["BB"] / totals["TBF"].replace(0, np.nan) * 100).round(1)
        totals = totals[totals["IP"] > 0]
        totals["IP"] = totals["IP"].round(1)

    return totals.reset_index()


def player_percentiles(player_stat: pd.DataFrame, standings: pd.DataFrame, fetch_date: str, category: str):
    """규정타석/규정이닝을 채운 선수들 사이에서, 각 지표별로 그 선수 값이 리그 내 몇 %ile인지 계산.
    (Statiz 선수 개인 페이지의 '2026 KBO Percentile Rankings' 차트에서 착안 — 백분위는 우리가
    가진 기본+자체계산 고급지표만으로 직접 산출한다.)

    category: 'batting'(batting+batting_adv 합산) 또는 'pitching'(pitching+pitching_adv 합산)
    반환: long-format DataFrame [player, team, stat_name, value, percentile(0~100, 표본 내 순위 백분위)]
    percentile은 값이 클수록 높게만 계산한다 — ERA·K% 처럼 낮을수록 좋은 지표의 방향 반전은
    호출하는 쪽(대시보드)에서 처리한다.
    """
    qualified = qualified_players(player_stat, standings, fetch_date, category)
    if not qualified:
        return pd.DataFrame(columns=["player", "team", "stat_name", "value", "percentile"])

    adv_category = f"{category}_adv"
    df = player_stat[(player_stat.fetch_date == fetch_date) & (player_stat.category.isin([category, adv_category]))]
    df = df[df.apply(lambda r: (r.player, r.team) in qualified, axis=1)]
    if df.empty:
        return pd.DataFrame(columns=["player", "team", "stat_name", "value", "percentile"])

    pivot = df.pivot_table(index=["player", "team"], columns="stat_name", values="stat_value", aggfunc="first")
    pivot = pivot.apply(pd.to_numeric, errors="coerce")
    pct = (pivot.rank(pct=True) * 100).round(1)

    value_long = pivot.reset_index().melt(id_vars=["player", "team"], var_name="stat_name", value_name="value")
    pct_long = pct.reset_index().melt(id_vars=["player", "team"], var_name="stat_name", value_name="percentile")
    return value_long.merge(pct_long, on=["player", "team", "stat_name"]).dropna(subset=["value"])


def season_standings_history(games: pd.DataFrame):
    """경기 로그를 날짜순으로 누적해 시즌 전체의 일자별 순위·승률 추이를 복원한다.
    (KBO 공식 순위표는 스냅샷만 제공해 매일 수집분밖에 없으므로, 이미 수집해 둔
    전체 경기 결과로 개막일부터의 순위 변화를 직접 재구성한다.)
    """
    g = games[games.status == "RESULT"].copy()
    if g.empty:
        return pd.DataFrame(columns=["date", "team", "wins", "losses", "draws", "win_pct", "rank"])
    g["winner"] = g.apply(_winner, axis=1)
    g = g.sort_values("game_date")

    teams = pd.unique(g[["home_team", "away_team"]].values.ravel())
    dates = sorted(g["game_date"].unique())
    record = {t: {"W": 0, "L": 0, "D": 0} for t in teams}

    rows = []
    for d in dates:
        for _, row in g[g["game_date"] == d].iterrows():
            h, a, w = row["home_team"], row["away_team"], row["winner"]
            if w == "DRAW":
                record[h]["D"] += 1
                record[a]["D"] += 1
            elif w == h:
                record[h]["W"] += 1
                record[a]["L"] += 1
            else:
                record[a]["W"] += 1
                record[h]["L"] += 1
        for t in teams:
            wv, lv, dv = record[t]["W"], record[t]["L"], record[t]["D"]
            win_pct = wv / (wv + lv) if (wv + lv) > 0 else 0.0
            rows.append({"date": d, "team": t, "wins": wv, "losses": lv, "draws": dv, "win_pct": win_pct})

    hist = pd.DataFrame(rows)
    hist["rank"] = hist.groupby("date")["win_pct"].rank(ascending=False, method="min")
    hist["date"] = pd.to_datetime(hist["date"], format="%Y%m%d")  # 문자열('20260328')로 두면 Plotly가
    # 축을 숫자로 오인해 눈금이 '20.2604M' 식으로 깨지므로 날짜형으로 명시 변환한다.
    return hist


def pythagorean_win_pct(team_stat: pd.DataFrame, standings: pd.DataFrame, fetch_date: str, exponent=1.83):
    """득점/실점 기반 피타고리안 기대승률과 실제 승률을 비교해 '운' 정도를 가늠한다."""
    r = team_stat[(team_stat.fetch_date == fetch_date) & (team_stat.category == "batting") &
                  (team_stat.stat_name == "R")][["team", "stat_value"]].rename(columns={"stat_value": "R"})
    ra = team_stat[(team_stat.fetch_date == fetch_date) & (team_stat.category == "pitching") &
                   (team_stat.stat_name == "R")][["team", "stat_value"]].rename(columns={"stat_value": "RA"})
    df = r.merge(ra, on="team")
    df["R"] = pd.to_numeric(df["R"])
    df["RA"] = pd.to_numeric(df["RA"])
    df["pyth_win_pct"] = df["R"] ** exponent / (df["R"] ** exponent + df["RA"] ** exponent)

    st = standings[standings.fetch_date == fetch_date][["team", "win_pct", "wins", "losses"]]
    df = df.merge(st, on="team")
    df["run_diff"] = df["R"] - df["RA"]
    df["diff"] = df["win_pct"] - df["pyth_win_pct"]
    return df.sort_values("diff", ascending=False)


def _parse_wdl(s):
    """'31-1-20' (승-무-패) -> (w, d, l, win_pct)"""
    try:
        w, d, l = [int(x) for x in str(s).split("-")]
    except (ValueError, AttributeError):
        return None, None, None, None
    win_pct = w / (w + l) if (w + l) > 0 else None
    return w, d, l, win_pct


def home_away_split(standings: pd.DataFrame, fetch_date: str):
    st = standings[standings.fetch_date == fetch_date].copy()
    home = st["home_record"].apply(_parse_wdl)
    away = st["away_record"].apply(_parse_wdl)
    st["home_w"], st["home_d"], st["home_l"], st["home_win_pct"] = zip(*home)
    st["away_w"], st["away_d"], st["away_l"], st["away_win_pct"] = zip(*away)
    st["home_adv"] = st["home_win_pct"] - st["away_win_pct"]
    return st[["team", "home_win_pct", "away_win_pct", "home_adv",
               "home_w", "home_d", "home_l", "away_w", "away_d", "away_l"]].sort_values("home_adv", ascending=False)


def _winner(row):
    if row["status"] != "RESULT" or pd.isna(row["home_score"]) or pd.isna(row["away_score"]):
        return None
    if row["home_score"] > row["away_score"]:
        return row["home_team"]
    if row["away_score"] > row["home_score"]:
        return row["away_team"]
    return "DRAW"


def head_to_head(games: pd.DataFrame):
    """팀 간 상대전적 승수 매트릭스 (행: 팀, 열: 상대팀이 그 팀에게 거둔 승수)."""
    g = games[games.status == "RESULT"].copy()
    g["winner"] = g.apply(_winner, axis=1)
    g = g[g["winner"].notna() & (g["winner"] != "DRAW")]
    g["loser"] = np.where(g["winner"] == g["home_team"], g["away_team"], g["home_team"])
    matrix = pd.crosstab(g["winner"], g["loser"])
    matrix.index.name = "승리팀"
    matrix.columns.name = "패배팀"
    return matrix


def monthly_team_trend(games: pd.DataFrame):
    g = games[games.status == "RESULT"].copy()
    g["month"] = g["game_date"].str[:6]
    g["winner"] = g.apply(_winner, axis=1)

    rows = []
    for _, row in g.iterrows():
        for team in (row["home_team"], row["away_team"]):
            if row["winner"] == "DRAW":
                result = "D"
            elif row["winner"] == team:
                result = "W"
            else:
                result = "L"
            rows.append({"month": row["month"], "team": team, "result": result})
    long_df = pd.DataFrame(rows)
    if long_df.empty:
        return long_df
    summary = long_df.groupby(["month", "team", "result"]).size().unstack(fill_value=0).reset_index()
    for col in ("W", "L", "D"):
        if col not in summary.columns:
            summary[col] = 0
    summary["games"] = summary["W"] + summary["L"] + summary["D"]
    summary["win_pct"] = summary["W"] / (summary["W"] + summary["L"]).replace(0, np.nan)
    return summary.sort_values(["month", "team"])


def close_and_blowout_games(games: pd.DataFrame):
    """팀별 1점차 승패, 5점차 이상 대승/대패 기록."""
    g = games[games.status == "RESULT"].copy()
    g["winner"] = g.apply(_winner, axis=1)
    g = g[g["winner"].notna() & (g["winner"] != "DRAW")]
    g["margin"] = (g["home_score"] - g["away_score"]).abs()

    rows = []
    for _, row in g.iterrows():
        loser = row["away_team"] if row["winner"] == row["home_team"] else row["home_team"]
        rows.append({"team": row["winner"], "margin": row["margin"], "result": "W"})
        rows.append({"team": loser, "margin": row["margin"], "result": "L"})
    long_df = pd.DataFrame(rows)
    if long_df.empty:
        return pd.DataFrame()

    out = []
    for team, grp in long_df.groupby("team"):
        one_run = grp[grp.margin == 1]
        blowout = grp[grp.margin >= 5]
        out.append({
            "team": team,
            "one_run_w": int((one_run.result == "W").sum()),
            "one_run_l": int((one_run.result == "L").sum()),
            "blowout_w": int((blowout.result == "W").sum()),
            "blowout_l": int((blowout.result == "L").sum()),
        })
    return pd.DataFrame(out).sort_values("team")


TITLE_CATEGORIES = {
    "타율왕 (AVG)": ("batting", "AVG", False),
    "홈런왕 (HR)": ("batting", "HR", False),
    "타점왕 (RBI)": ("batting", "RBI", False),
    "출루율 (OBP)": ("batting", "OBP", False),
    "장타율 (SLG)": ("batting", "SLG", False),
    "다승왕 (W)": ("pitching", "W", False),
    "평균자책점왕 (ERA)": ("pitching", "ERA", True),
    "세이브왕 (SV)": ("pitching", "SV", False),
    "탈삼진왕 (SO)": ("pitching", "SO", False),
}


def title_leaders(player_stat: pd.DataFrame, fetch_date: str, top_n=5):
    out = {}
    for label, (category, stat_name, ascending) in TITLE_CATEGORIES.items():
        df = player_stat[(player_stat.fetch_date == fetch_date) & (player_stat.category == category) &
                          (player_stat.stat_name == stat_name)].copy()
        if df.empty:
            continue
        df["value_num"] = pd.to_numeric(df["stat_value"], errors="coerce")
        if not ascending and df["value_num"].max() <= 0:
            continue  # 수집된 선수 범위 안에 의미 있는 1위가 없음 (예: 규정 미달 마무리투수의 세이브)
        df = df.sort_values("value_num", ascending=ascending).head(top_n)
        out[label] = df[["player", "team", "stat_value"]].reset_index(drop=True)
    return out


TOTAL_SEASON_GAMES = 144  # KBO 정규시즌 팀당 경기 수


def magic_number(standings: pd.DataFrame, fetch_date: str, total_games=TOTAL_SEASON_GAMES):
    """우승 매직넘버(1위 vs 2위)와 포스트시즌 진출 매직넘버(5위 vs 6위)를 계산한다.
    표준 공식: MN = (총경기수+1) - 선두팀 승수 - 추격팀 패수. 0 이하면 이미 확정.
    KBO의 무승부 규정까지 정밀 반영한 공식 매직넘버는 아니며, 널리 쓰이는 단순화 버전이다.
    """
    st = standings[standings.fetch_date == fetch_date].sort_values("rank").reset_index(drop=True)
    if len(st) < 6:
        return None

    def mn(leader_row, chaser_row):
        value = total_games + 1 - leader_row["wins"] - chaser_row["losses"]
        return max(int(value), 0)

    first, second = st.iloc[0], st.iloc[1]
    fifth, sixth = st.iloc[4], st.iloc[5]
    return {
        "title_leader": first["team"], "title_chaser": second["team"], "title_mn": mn(first, second),
        "playoff_leader": fifth["team"], "playoff_chaser": sixth["team"], "playoff_mn": mn(fifth, sixth),
    }


def season_projection(standings: pd.DataFrame, fetch_date: str, total_games=TOTAL_SEASON_GAMES):
    """현재 승률이 잔여 경기에도 유지된다는 가정 하의 시즌 최종 성적 단순 예측."""
    st = standings[standings.fetch_date == fetch_date].copy()
    st["games_remaining"] = (total_games - st["games"]).clip(lower=0)
    st["proj_wins"] = st["wins"] + st["win_pct"] * st["games_remaining"]
    st["proj_losses"] = st["losses"] + (1 - st["win_pct"]) * st["games_remaining"]
    st["proj_win_pct"] = st["proj_wins"] / (st["proj_wins"] + st["proj_losses"])
    st = st.sort_values("proj_win_pct", ascending=False).reset_index(drop=True)
    st["proj_rank"] = st.index + 1
    return st[["team", "rank", "games_remaining", "wins", "losses", "proj_wins", "proj_losses",
               "proj_win_pct", "proj_rank"]]


def stadium_scoring_environment(games: pd.DataFrame):
    """구장별 평균 득점으로 본 득점 환경(간이 파크팩터). 100=리그 평균, 높을수록 타고 구장.
    팀 전력 차이를 통제하지 않은 단순 평균이라 정밀 파크팩터는 아니다.
    """
    g = games[(games.status == "RESULT") & games["stadium"].notna() & (games["stadium"] != "")].copy()
    if g.empty:
        return pd.DataFrame()
    g["total_runs"] = g["home_score"] + g["away_score"]
    league_avg = g["total_runs"].mean()
    out = g.groupby("stadium").agg(games=("total_runs", "size"), avg_runs=("total_runs", "mean")).reset_index()
    out["park_factor"] = (out["avg_runs"] / league_avg * 100).round(1)
    return out.sort_values("park_factor", ascending=False)


WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]


def weekday_performance(games: pd.DataFrame):
    """요일별 팀 성적."""
    g = games[games.status == "RESULT"].copy()
    if g.empty:
        return pd.DataFrame()
    g["winner"] = g.apply(_winner, axis=1)
    g["weekday"] = pd.to_datetime(g["game_date"], format="%Y%m%d").dt.dayofweek

    rows = []
    for _, row in g.iterrows():
        for team in (row["home_team"], row["away_team"]):
            result = "D" if row["winner"] == "DRAW" else ("W" if row["winner"] == team else "L")
            rows.append({"weekday": row["weekday"], "team": team, "result": result})
    long_df = pd.DataFrame(rows)
    summary = long_df.groupby(["weekday", "team", "result"]).size().unstack(fill_value=0).reset_index()
    for col in ("W", "L", "D"):
        if col not in summary.columns:
            summary[col] = 0
    summary["games"] = summary["W"] + summary["L"] + summary["D"]
    summary["win_pct"] = summary["W"] / (summary["W"] + summary["L"]).replace(0, np.nan)
    summary["weekday_kr"] = summary["weekday"].map(lambda i: WEEKDAY_KR[i])
    return summary.sort_values(["weekday", "team"])


def longest_streaks(games: pd.DataFrame):
    """팀별 시즌 최다 연승·연패 기록."""
    g = games[games.status == "RESULT"].copy()
    if g.empty:
        return pd.DataFrame()
    g["winner"] = g.apply(_winner, axis=1)
    g = g.sort_values("game_date")

    teams = pd.unique(g[["home_team", "away_team"]].values.ravel())
    cur_win = {t: 0 for t in teams}
    cur_lose = {t: 0 for t in teams}
    best_win = {t: 0 for t in teams}
    best_lose = {t: 0 for t in teams}

    for _, row in g.iterrows():
        for team in (row["home_team"], row["away_team"]):
            if row["winner"] == "DRAW":
                cur_win[team] = 0
                cur_lose[team] = 0
            elif row["winner"] == team:
                cur_win[team] += 1
                cur_lose[team] = 0
                best_win[team] = max(best_win[team], cur_win[team])
            else:
                cur_lose[team] += 1
                cur_win[team] = 0
                best_lose[team] = max(best_lose[team], cur_lose[team])

    return pd.DataFrame([
        {"team": t, "longest_win_streak": best_win[t], "longest_lose_streak": best_lose[t]}
        for t in teams
    ]).sort_values("longest_win_streak", ascending=False)


def clutch_index(player_stat: pd.DataFrame, fetch_date: str, min_pa=None):
    """득점권 타율(RISP) - 통산 타율(AVG)로 본 '클러치 지수'. 양수일수록 찬스에 강함."""
    df = player_stat[(player_stat.fetch_date == fetch_date) & (player_stat.category == "batting") &
                      (player_stat.stat_name.isin(["AVG", "RISP", "PH-BA", "PA"]))]
    pivot = df.pivot_table(index=["player", "team"], columns="stat_name", values="stat_value", aggfunc="first").reset_index()
    if "AVG" not in pivot.columns or "RISP" not in pivot.columns:
        return pd.DataFrame()
    pivot["AVG"] = pd.to_numeric(pivot["AVG"], errors="coerce")
    pivot["RISP"] = pd.to_numeric(pivot["RISP"], errors="coerce")
    pivot = pivot.dropna(subset=["AVG", "RISP"])
    pivot["clutch_diff"] = (pivot["RISP"] - pivot["AVG"]).round(3)
    return pivot.sort_values("clutch_diff", ascending=False)


def stat_win_correlation(team_stat: pd.DataFrame, standings: pd.DataFrame, fetch_date: str, category: str):
    """팀 스탯(카테고리별)과 승률의 상관계수. 어떤 지표가 승리와 가장 밀접한지 확인."""
    st = standings[standings.fetch_date == fetch_date][["team", "win_pct"]]
    cat = team_stat[(team_stat.fetch_date == fetch_date) & (team_stat.category == category)]
    pivot = cat.pivot_table(index="team", columns="stat_name", values="stat_value", aggfunc="first").reset_index()
    merged = pivot.merge(st, on="team")

    results = []
    for col in pivot.columns:
        if col == "team":
            continue
        vals = pd.to_numeric(merged[col], errors="coerce")
        if vals.notna().sum() < 3 or vals.nunique() < 2:
            continue
        corr = vals.corr(merged["win_pct"])
        if pd.notna(corr):
            results.append({"stat": col, "corr_with_win_pct": round(corr, 3)})
    return pd.DataFrame(results).sort_values("corr_with_win_pct", key=lambda s: s.abs(), ascending=False)


# ===================== 역대(1982~) 기록 분석 — historical_player_stat 기반 =====================
# 선수명만으로 연도를 넘나들며 합산하므로, 서로 다른 시대에 동명이인이 있으면(드물지만 가능)
# 섞일 수 있다는 한계가 있다 — 시즌 내 동명이인은 이미 (연도,팀) 조합으로 걸러지지만,
# 완전히 다른 두 사람이 커리어 내내 우연히 같은 이름을 쓰는 경우까지는 구분하지 못한다.

HIST_BATTING_COUNT_COLS = ["G", "PA", "AB", "R", "H", "2B", "3B", "HR", "TB", "RBI",
                           "SAC", "SF", "BB", "IBB", "HBP", "SO", "GDP", "MH"]
HIST_PITCHING_COUNT_COLS = ["G", "W", "L", "SV", "HLD", "H", "HR", "BB", "HBP", "SO", "R", "ER",
                            "TBF", "SAC", "SF", "IBB", "BK", "WP", "CG", "SHO", "QS", "BSV", "NP"]


def career_totals(history_player_stat: pd.DataFrame, category: str):
    """1982년부터 지금까지 모아둔 시즌별 기록을 선수별로 합산해 통산 기록을 계산한다.
    비율 지표(AVG·OBP·SLG·OPS·ERA·WHIP)는 합산한 카운팅 스탯으로 새로 계산한다.

    KBO 선수 목록 페이지의 선수명 링크에 담긴 내부 고유번호(player_id)로 동명이인을 구분한다
    (예: '박병호'라는 이름의 1989~1996년 해태 선수와 2005년 이후 활동한 유명 슬러거는 실제로는
    다른 사람인데 player_id가 각각 89630/75125로 달라 더 이상 하나로 합쳐지지 않는다).
    player_id가 없는 극히 일부 행(옛 페이지 형식 등)은 이름으로만 구분해 전과 동일하게 처리한다.

    반환: [player, 연도수, 첫해, 막해, <카운팅 스탯 합계>, <통산 비율 스탯>] wide-format
    """
    df = history_player_stat[history_player_stat["category"] == category].copy()
    if df.empty:
        return pd.DataFrame()
    count_cols = HIST_BATTING_COUNT_COLS if category == "batting" else HIST_PITCHING_COUNT_COLS

    if "player_id" not in df.columns:
        df["player_id"] = None
    has_id = df["player_id"].notna() & (df["player_id"].astype(str) != "")
    df["identity"] = np.where(has_id, "ID:" + df["player_id"].astype(str), "NAME:" + df["player"])

    meta = df.groupby("identity").agg(player=("player", "first"), 연도수=("year", "nunique"),
                                       첫해=("year", "min"), 막해=("year", "max"))

    pivot = df.pivot_table(index=["identity", "year", "team"], columns="stat_name",
                            values="stat_value", aggfunc="first").reset_index()
    for c in count_cols:
        if c not in pivot.columns:
            pivot[c] = 0
    pivot[count_cols] = pivot[count_cols].apply(pd.to_numeric, errors="coerce").fillna(0)
    if category == "pitching":
        pivot["IP"] = pivot["IP"].apply(sabermetrics.parse_innings) if "IP" in pivot.columns else 0.0

    agg_cols = count_cols + (["IP"] if category == "pitching" else [])
    totals = pivot.groupby("identity")[agg_cols].sum()
    totals = totals.join(meta)

    def _safe_div(num, den):
        return (num / den.replace(0, np.nan)).round(3)

    if category == "batting":
        totals["AVG"] = _safe_div(totals["H"], totals["AB"])
        totals["OBP"] = _safe_div(totals["H"] + totals["BB"] + totals["HBP"],
                                   totals["AB"] + totals["BB"] + totals["HBP"] + totals["SF"])
        totals["SLG"] = _safe_div(totals["TB"], totals["AB"])
        totals["OPS"] = (totals["OBP"] + totals["SLG"]).round(3)
    else:
        totals["IP"] = totals["IP"].round(1)
        totals["ERA"] = (totals["ER"] * 9 / totals["IP"].replace(0, np.nan)).round(2)
        totals["WHIP"] = _safe_div(totals["BB"] + totals["H"], totals["IP"])
        totals["WPCT"] = _safe_div(totals["W"], totals["W"] + totals["L"])

    # WAR은 시즌별로 이미 계산해 둔 값(batting_adv/pitching_adv)을 그대로 더한다 — 세이버메트릭스에서
    # WAR은 시즌을 넘어 합산해도 되는 지표로 취급하는 게 일반적이다(wOBA·wRC+처럼 비율인 지표와 달리).
    adv_category = f"{category}_adv"
    war_rows = history_player_stat[
        (history_player_stat["category"] == adv_category) & (history_player_stat["stat_name"] == "WAR")
    ][["year", "team", "player", "stat_value"]].copy()
    if not war_rows.empty:
        war_rows["stat_value"] = pd.to_numeric(war_rows["stat_value"], errors="coerce")
        id_map = df[["year", "team", "player", "identity"]].drop_duplicates()
        war_rows = war_rows.merge(id_map, on=["year", "team", "player"], how="left").dropna(subset=["identity"])
        war_by_identity = war_rows.groupby("identity")["stat_value"].sum().round(2).rename("WAR")
        totals = totals.join(war_by_identity)

    # index('identity')를 컬럼으로 남겨둔다 — '선수 검색' 탭에서 이름이 같은 동명이인을 구분해
    # 특정 한 명만 골라내려면 player_id 기반 identity로 다시 걸러야 하기 때문이다.
    return totals.reset_index()


RATE_STATS = ("AVG", "OBP", "SLG", "OPS", "ERA", "WHIP")
# 저희가 직접 계산하는 고급지표(batting_adv/pitching_adv 카테고리) 중 표본 왜곡 방지를 위해
# 규정타석/이닝 필터를 적용할 지표. wOBA·FIP는 리그평균과 비교할 수 있어 '참고'에도 쓴다.
ADV_CONTEXT_STATS = ("wOBA", "FIP")
QUALIFY_STATS = RATE_STATS + ADV_CONTEXT_STATS + ("WAR", "wRC+")

# KBO 사이트가 아예 계산·제공하지 않아 옛 시즌에 '0'으로만 채워진 지표와, 실제로 데이터가
# 시작되는 연도. single_season_leaders의 0-필터링과 '역대 선수 기록' 화면의 안내문에 함께 쓴다.
# (2026-08-27 전수조사로 확인 — 표본 20명 이상인데 값이 전부 0인 (지표,연도) 조합을 찾았다.)
STAT_AVAILABLE_FROM = {
    ("batting", "IBB"): 2001, ("batting", "MH"): 2001, ("batting", "PH-BA"): 2001, ("batting", "RISP"): 2001,
    ("pitching", "2B"): 2001, ("pitching", "3B"): 2001, ("pitching", "BK"): 2001, ("pitching", "IBB"): 2001,
    ("pitching", "NP"): 2001, ("pitching", "QS"): 2001, ("pitching", "SAC"): 2001, ("pitching", "SF"): 2001,
    ("pitching", "WP"): 2001, ("pitching", "WHIP"): 2001, ("pitching", "HLD"): 2000, ("pitching", "BSV"): 2006,
}


def single_season_leaders(history_player_stat: pd.DataFrame, history_standings: pd.DataFrame,
                           category: str, stat: str, qualified_only: bool = True, top_n: int = 15,
                           year: str = None):
    """1982년부터 지금까지의 모든 시즌·선수를 통틀어 특정 지표의 '한 시즌' 최고 기록 TOP N.
    qualified_only=True면 비율 지표 왜곡을 막기 위해 그 해 규정타석/이닝(그 해 그 팀의 경기수 기준)
    이상인 시즌만 포함한다 — 카운팅 스탯(HR·안타·다승 등)은 애초에 적게 뛰면 순위에 들 수 없어
    필터를 걸지 않아도 된다.

    year를 주면 전체 역대가 아니라 그 해 한 시즌만 대상으로 TOP N을 뽑는다(예: '과거 세이버메트릭스'
    탭에서 연도별 리더보드를 보여줄 때 씀) — league_stat_average는 어차피 history_player_stat
    전체로 계산하므로 '참고' 열의 최신연도 비교는 year를 줘도 그대로 유지된다.

    '참고' 열에 오늘날과 비교할 수 있는 맥락을 덧붙인다 — 비율 지표는 그 해 리그 평균과 최신
    연도 리그 평균을, 카운팅 스탯은 그 해 팀 경기수 기준 144경기 환산치를 보여준다(옛 시즌은
    80~130경기로 지금(144경기)보다 훨씬 짧았다).
    """
    base_category = "batting" if category.startswith("batting") else "pitching"
    df = history_player_stat[
        (history_player_stat["category"] == category) & (history_player_stat["stat_name"] == stat)
    ][["year", "player", "team", "stat_value"]].copy()
    if year is not None:
        df = df[df["year"] == year]
    if stat == "IP":
        # KBO 원본 이닝 표기('284 2/3')는 pd.to_numeric으로 못 읽어 NaN이 되고 dropna에 걸려
        # 통째로 빠져버린다 — 실제로 최동원의 1984년 284⅔이닝(역대 최다이닝 시즌)이 이 때문에
        # 역대 단일시즌 최고 이닝 리더보드에서 통째로 누락되는 걸 발견해 고쳤다.
        df["stat_value"] = df["stat_value"].apply(sabermetrics.parse_innings)
    else:
        df["stat_value"] = pd.to_numeric(df["stat_value"], errors="coerce")
    df = df.dropna(subset=["stat_value"])

    if stat in RATE_STATS:
        # KBO 사이트가 옛날 시즌 일부 지표를 실제로는 계산하지 않고 '0.00'으로 채워 넣은 경우가
        # 있다(예: WHIP은 2001년 이전 전원이 0.00) — 규정타석/이닝을 채운 시즌에서 이 지표가
        # 진짜 0일 수는 없으므로 안전하게 걸러낸다. (wOBA·WAR·wRC+·FIP는 저희가 직접 계산해서
        # 이런 결측 placeholder가 없다.)
        df = df[df["stat_value"] != 0]

    if qualified_only and stat in QUALIFY_STATS:
        pa_col = "PA" if base_category == "batting" else "IP"
        per_game = PA_PER_GAME if base_category == "batting" else IP_PER_GAME
        pa_df = history_player_stat[
            (history_player_stat["category"] == base_category) & (history_player_stat["stat_name"] == pa_col)
        ][["year", "player", "team", "stat_value"]].rename(columns={"stat_value": pa_col})
        if base_category == "pitching":
            pa_df[pa_col] = pa_df[pa_col].apply(sabermetrics.parse_innings)
        else:
            pa_df[pa_col] = pd.to_numeric(pa_df[pa_col], errors="coerce")
        g = history_standings.rename(columns={"games": "team_games"})[["year", "team", "team_games"]]
        df = df.merge(pa_df, on=["year", "player", "team"], how="left").merge(g, on=["year", "team"], how="left")
        df = df[df[pa_col] >= df["team_games"] * per_game]

    ascending = stat in ("ERA", "WHIP", "FIP")
    df = df.sort_values("stat_value", ascending=ascending).head(top_n)[["year", "player", "team", "stat_value"]].copy()
    if df.empty:
        df["참고"] = []
        return df

    if stat in RATE_STATS + ADV_CONTEXT_STATS:
        league_avg = league_stat_average(history_player_stat, category, stat)
        latest_year = history_player_stat["year"].max()
        latest_avg = league_avg.get(latest_year)
        def _note(r):
            yr_avg = league_avg.get(r["year"])
            if yr_avg is None or latest_avg is None:
                return ""
            return f"{r['year']}년 리그평균 {yr_avg:.3f} · {latest_year}년 리그평균 {latest_avg:.3f}"
    elif stat == "wRC+":
        # wRC+는 정의상 100이 항상 리그 평균이라 별도 비교가 필요 없다.
        def _note(r):
            return "100 = 그 해 리그 평균 (정의상 항상 100)"
    else:
        team_games = history_standings.groupby("year")["games"].max()
        def _note(r):
            tg = team_games.get(r["year"])
            if not tg:
                return ""
            pace = r["stat_value"] / tg * 144
            return f"{r['year']}년은 {tg}경기 체제(현재 144경기) · 144경기 환산 시 약 {pace:.1f}"

    df["참고"] = df.apply(_note, axis=1)
    return df


def league_stat_average(history_player_stat: pd.DataFrame, category: str, stat: str):
    """연도별 리그 평균 — 역대 기록에 맥락을 붙일 때 쓴다.

    단순 평균을 내면 안 된다: 한두 이닝만 던지고 자책점을 몰아 맞은 구원투수의 ERA가 40.00
    같은 극단치라도 다른 규정이닝급 선수와 똑같이 1표로 취급돼 리그 평균이 크게 부풀려진다
    (실제로 이렇게 계산해보니 ERA 리그 평균이 7점대까지 나와 명백히 틀렸다). 그래서 타석/이닝으로
    가중평균한다 — 이러면 표본이 많은 선수의 비중이 커져 실제 리그 전체 수치(총득점/총타석 등)에
    근접한다.
    """
    df = history_player_stat[
        (history_player_stat["category"] == category) & (history_player_stat["stat_name"] == stat)
    ][["year", "player", "team", "stat_value"]].copy()
    df["v"] = pd.to_numeric(df["stat_value"], errors="coerce")
    if stat in RATE_STATS:
        df = df[df["v"] != 0]

    base_category = "batting" if category.startswith("batting") else "pitching"
    weight_col = "AB" if base_category == "batting" else "IP"
    w_df = history_player_stat[
        (history_player_stat["category"] == base_category) & (history_player_stat["stat_name"] == weight_col)
    ][["year", "player", "team", "stat_value"]].rename(columns={"stat_value": "w"})
    if base_category == "pitching":
        w_df["w"] = w_df["w"].apply(sabermetrics.parse_innings)
    else:
        w_df["w"] = pd.to_numeric(w_df["w"], errors="coerce")

    merged = df.merge(w_df, on=["year", "player", "team"], how="left").dropna(subset=["v", "w"])
    merged = merged[merged["w"] > 0]
    merged["vw"] = merged["v"] * merged["w"]
    g = merged.groupby("year")
    return (g["vw"].sum() / g["w"].sum())


def league_era_trend(history_player_stat: pd.DataFrame):
    """연도별 리그 전체 득점 환경(타율·OPS·평균자책점) 변화 — 투고타저/타고투저 흐름을 보여준다.
    그 해 뛴 모든 선수의 기록을 리그 전체로 합산해 계산한다 (팀 집계 페이지는 2001년 이후만
    있지만, 선수 개인기록은 1982년부터 있으므로 이걸 합산하면 1982년부터 전부 계산할 수 있다).
    """
    bat = history_player_stat[history_player_stat["category"] == "batting"]
    bat_pivot = bat.pivot_table(index=["year", "player", "team"], columns="stat_name",
                                 values="stat_value", aggfunc="first").reset_index()
    for c in ("AB", "H", "BB", "HBP", "SF", "TB"):
        if c not in bat_pivot.columns:
            bat_pivot[c] = 0
        bat_pivot[c] = pd.to_numeric(bat_pivot[c], errors="coerce").fillna(0)
    by_year_b = bat_pivot.groupby("year")[["AB", "H", "BB", "HBP", "SF", "TB"]].sum()
    by_year_b["lg_avg"] = (by_year_b["H"] / by_year_b["AB"]).round(3)
    obp = (by_year_b["H"] + by_year_b["BB"] + by_year_b["HBP"]) / (by_year_b["AB"] + by_year_b["BB"] + by_year_b["HBP"] + by_year_b["SF"])
    slg = by_year_b["TB"] / by_year_b["AB"]
    by_year_b["lg_ops"] = (obp + slg).round(3)

    pit = history_player_stat[history_player_stat["category"] == "pitching"]
    pit_pivot = pit.pivot_table(index=["year", "player", "team"], columns="stat_name",
                                 values="stat_value", aggfunc="first").reset_index()
    for c in ("ER", "IP"):
        if c not in pit_pivot.columns:
            pit_pivot[c] = 0
    pit_pivot["ER"] = pd.to_numeric(pit_pivot["ER"], errors="coerce").fillna(0)
    pit_pivot["IP"] = pit_pivot["IP"].apply(sabermetrics.parse_innings)
    by_year_p = pit_pivot.groupby("year")[["ER", "IP"]].sum()
    by_year_p["lg_era"] = (by_year_p["ER"] * 9 / by_year_p["IP"].replace(0, np.nan)).round(2)

    out = by_year_b[["lg_avg", "lg_ops"]].join(by_year_p[["lg_era"]]).reset_index()
    return out.sort_values("year")


# ===================== 선수 검색 (선수 한 명 딥다이브) =====================
# 통산 기록과 같은 player_id 기반 identity를 그대로 써서 동명이인을 구분한다.

def _with_identity(df: pd.DataFrame):
    df = df.copy()
    if "player_id" not in df.columns:
        df["player_id"] = None
    has_id = df["player_id"].notna() & (df["player_id"].astype(str) != "")
    df["identity"] = np.where(has_id, "ID:" + df["player_id"].astype(str), "NAME:" + df["player"])
    return df


def identity_player_id(identity: str):
    """'ID:12345' -> '12345', 'NAME:홍길동' -> None (player_id가 없어 이름으로만 구분된 경우)."""
    return identity.split("ID:", 1)[1] if identity.startswith("ID:") else None


def player_identity_search(history_player_stat: pd.DataFrame, query: str):
    """이름에 query가 포함된 선수를 찾아 identity 단위(동명이인 구분)로 후보 목록을 만든다.
    타자/투수 기록이 모두 있으면 categories에 둘 다 담아 반환한다.
    반환: [identity, player, 첫해, 막해, 최근팀, categories(set)]

    batting_adv/pitching_adv는 player_id를 저장하지 않으므로(자체계산 지표라 원본 로우의
    player_id를 들고 있지 않음) 반드시 raw 카테고리(batting/pitching)만으로 identity를 잡아야
    한다 — 안 그러면 같은 선수의 adv 로우가 'NAME:이름'이라는 별도 identity로 갈라져 나가면서
    동명이인 취급을 받고, 그 가짜 identity가 실제로는 다른 시대 동명이인의 시즌들과 뒤섞여
    보이는 문제가 생긴다(직접 확인함).
    """
    if not query or history_player_stat.empty:
        return pd.DataFrame()
    base = history_player_stat[history_player_stat["category"].isin(["batting", "pitching"])]
    df = _with_identity(base)
    matched = df[df["player"].str.contains(query, case=False, na=False)]
    if matched.empty:
        return pd.DataFrame()
    ids = matched["identity"].unique()
    sub = df[df["identity"].isin(ids)]

    meta = sub.groupby("identity").agg(player=("player", "first"), 첫해=("year", "min"), 막해=("year", "max"))
    last_team = (sub.sort_values("year").groupby("identity").tail(1)
                 .set_index("identity")["team"].rename("최근팀"))
    cats = sub.groupby("identity")["category"].apply(
        lambda s: tuple(sorted({c.replace("_adv", "") for c in s}))).rename("categories")
    meta = meta.join(last_team).join(cats).reset_index()
    return meta.sort_values(["player", "첫해"])


def player_year_by_year(history_player_stat: pd.DataFrame, identity: str, category: str):
    """특정 identity(동명이인 구분 완료)의 연도별 시즌 기록 — 기본기록 + 그 해 계산한 고급지표를
    한 표에 합쳐서 보여준다. 고급지표(_adv)는 player_id가 없어 (연도,팀,선수명)으로 다시 매칭한다
    (career_totals의 WAR 합산과 같은 방식) — 그 선수의 실제 출전 시즌 범위 안에서만 조인하므로
    동명이인이 섞일 위험은 낮다.
    """
    df = _with_identity(history_player_stat[history_player_stat["category"] == category])
    sub = df[df["identity"] == identity]
    if sub.empty:
        return pd.DataFrame()
    basic = sub.pivot_table(index=["year", "team", "player"], columns="stat_name",
                             values="stat_value", aggfunc="first").reset_index()

    adv = history_player_stat[history_player_stat["category"] == f"{category}_adv"]
    keys = basic[["year", "team", "player"]].drop_duplicates()
    adv = adv.merge(keys, on=["year", "team", "player"], how="inner")
    if not adv.empty:
        adv_pivot = adv.pivot_table(index=["year", "team", "player"], columns="stat_name",
                                     values="stat_value", aggfunc="first").reset_index()
        # WHIP는 KBO 원본 기록(basic)에도 있고 저희가 자체 계산한 값(pitching_adv)에도 있어
        # 이름이 겹친다 — merge가 자동으로 'WHIP_x'/'WHIP_y'로 쪼개면서 정작 'WHIP' 컬럼
        # 자체가 없어져 '통산 기록' 표에 WHIP이 통째로 None으로 보이는 문제가 있었다.
        # 겹치는 컬럼은 KBO 원본(basic) 쪽을 그대로 쓰고 adv 쪽 중복분은 버린다.
        overlap = [c for c in adv_pivot.columns if c in basic.columns and c not in ("year", "team", "player")]
        adv_pivot = adv_pivot.drop(columns=overlap)
        basic = basic.merge(adv_pivot, on=["year", "team", "player"], how="left")

    return basic.sort_values("year")


def player_game_log(player_game_stat: pd.DataFrame, games: pd.DataFrame, player: str, team: str, category: str):
    """이 선수의 (이번 시즌) 경기별 박스스코어 로그 — 날짜·상대팀·홈/원정 정보를 붙인다.
    player_game_stat은 네이버 박스스코어를 수집하기 시작한 시즌분만 있어 최신 시즌만 나온다.
    player_game_stat엔 player_id가 없어 (선수명,팀)으로 매칭한다 — '최근 폼' 섹션과 같은 방식.
    """
    df = player_game_stat[(player_game_stat["category"] == category) &
                           (player_game_stat["player"] == player) &
                           (player_game_stat["team"] == team)].copy()
    if df.empty:
        return pd.DataFrame()
    df["stat_value"] = pd.to_numeric(df["stat_value"], errors="coerce")
    pivot = df.pivot_table(index=["game_id", "game_date"], columns="stat_name",
                            values="stat_value", aggfunc="first").reset_index()

    g = games[["game_id", "home_team", "away_team", "home_score", "away_score", "stadium"]].drop_duplicates("game_id")
    pivot = pivot.merge(g, on="game_id", how="left")
    pivot["홈/원정"] = np.where(pivot["home_team"] == team, "홈", "원정")
    pivot["상대"] = np.where(pivot["home_team"] == team, pivot["away_team"], pivot["home_team"])
    return pivot.sort_values("game_date")


def player_awards(player_award: pd.DataFrame, player_id: str):
    """이 선수(player_id)의 수상 목록(최신순). KBO MVP·신인상·골든글러브·KBO수비상만 있고
    올스타전/한국시리즈 MVP는 KBO 사이트 자체가 선수 개인 조회에 포함하지 않는다."""
    if not player_id or player_award.empty:
        return pd.DataFrame(columns=["year", "award"])
    df = player_award[player_award["player_id"] == player_id][["year", "award"]]
    return df.sort_values("year", ascending=False).reset_index(drop=True)


def player_season_reg(player_season_reg_df: pd.DataFrame, player_id: str):
    """이 선수(player_id)의 KBO 리그 엔트리(1군) 등록일수 — 연도·소속팀별(최신순)."""
    if not player_id or player_season_reg_df.empty:
        return pd.DataFrame(columns=["year", "team", "days", "note"])
    df = player_season_reg_df[player_season_reg_df["player_id"] == player_id].copy()
    df["days"] = pd.to_numeric(df["days"], errors="coerce")
    return df.sort_values("year", ascending=False)[["year", "team", "days", "note"]].reset_index(drop=True)


def player_split_summary(game_log: pd.DataFrame, category: str, split_col: str):
    """경기 로그를 split_col(예: '홈/원정', '상대') 기준으로 묶어 그 구간 성적을 계산한다."""
    if game_log.empty:
        return pd.DataFrame()
    if category == "batting":
        for c in ("2B", "3B", "HBP", "SF", "SAC"):
            if c not in game_log.columns:
                game_log[c] = 0
        g = game_log.groupby(split_col)[["AB", "H", "2B", "3B", "HR", "BB", "HBP", "SF", "SAC", "SO", "RBI", "R"]].sum()
        tb = g["H"] + g["2B"] + 2 * g["3B"] + 3 * g["HR"]
        g["AVG"] = (g["H"] / g["AB"].replace(0, np.nan)).round(3)
        g["OBP"] = ((g["H"] + g["BB"] + g["HBP"]) /
                    (g["AB"] + g["BB"] + g["HBP"] + g["SF"]).replace(0, np.nan)).round(3)
        g["SLG"] = (tb / g["AB"].replace(0, np.nan)).round(3)
        g["OPS"] = (g["OBP"] + g["SLG"]).round(3)
        g["G"] = game_log.groupby(split_col)["game_id"].nunique()
    else:
        g = game_log.groupby(split_col)[["IP", "H", "ER", "BB", "SO", "HR", "TBF"]].sum()
        g["ERA"] = (g["ER"] * 9 / g["IP"].replace(0, np.nan)).round(2)
        g["WHIP"] = ((g["BB"] + g["H"]) / g["IP"].replace(0, np.nan)).round(2)
        g["G"] = game_log.groupby(split_col)["game_id"].nunique()
    return g.reset_index()
