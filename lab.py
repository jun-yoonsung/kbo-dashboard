"""데이터 분석실 — 1982년부터의 시즌 기록과 이번 시즌 경기별 기록을 자유롭게 파고드는 분석 도구 모음.

대시보드의 다른 탭이 '정해진 질문에 대한 답'이라면, 이 탭은 분석가가 직접 질문을 만드는 곳이다:
어떤 지표끼리 관련이 있는지(산점도·상관관계), 분포가 어떤 모양인지, 리그 환경이 시대별로 어떻게
변했는지, 선수 커리어를 시대 보정해서 비교하면 어떤지, 투수 등판 간격·투구수·불펜 부하는 어떤지.
"""

import os
import sqlite3

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import analysis
import sabermetrics

TOOLS = ["산점도", "분포", "상관관계", "시대별 추이", "선수 비교", "투수 등판 분석", "팀 매치업"]

# 값이 낮을수록 좋은 지표 — '상위 몇 %' 계산과 정렬 방향에 쓴다.
LOWER_BETTER = {
    "batting": {"K%", "GDP"},
    "pitching": {"ERA", "FIP", "WHIP", "BB%", "BB/9", "HR/9", "H/9", "RA9", "BABIP", "ERA-", "FIP-", "WHIP-",
                 "P/IP", "P/BF", "BB", "H", "HR", "R", "ER", "L", "BSV"},
}

DERIVED_LABELS = {
    "batting": {
        "BABIP": "인플레이 타구 타율 (H−HR)/(AB−SO−HR+SF)",
        "HR%": "타석당 홈런 비율(%)", "XBH": "장타 수 (2B+3B+HR)", "PA/G": "경기당 타석",
        "AVG+": "타율 지수 (그 해 리그=100)", "OBP+": "출루율 지수 (그 해 리그=100)",
        "SLG+": "장타율 지수 (그 해 리그=100)", "OPS+": "OPS 지수 (그 해 리그=100, 구장 보정 없음)",
    },
    "pitching": {
        "K/9": "9이닝당 탈삼진", "BB/9": "9이닝당 볼넷", "HR/9": "9이닝당 피홈런", "H/9": "9이닝당 피안타",
        "K/BB": "탈삼진/볼넷 비", "P/IP": "이닝당 투구수 (2001~)", "P/BF": "타자당 투구수 (2001~)",
        "IP/G": "경기당 이닝", "BABIP": "피BABIP (근사)", "LOB%": "잔루율 (근사, FIP식)",
        "RA9": "9이닝당 실점 (비자책 포함)", "ERA-": "ERA 지수 (리그=100, 낮을수록 좋음)",
        "FIP-": "FIP 지수 (리그=100, 낮을수록 좋음)", "WHIP-": "WHIP 지수 (리그=100, 낮을수록 좋음)",
    },
}

PREFERRED = {
    "batting": ["OPS", "AVG", "OBP", "SLG", "wOBA", "wRC+", "WAR", "OPS+", "HR", "RBI", "H", "R", "BB", "SO",
                "ISO", "BABIP", "BB%", "K%", "PA", "HR%"],
    "pitching": ["ERA", "FIP", "WHIP", "WAR", "ERA-", "K/9", "BB/9", "HR/9", "K%", "BB%", "BABIP", "LOB%",
                 "IP", "SO", "W", "SV", "HLD", "QS"],
}
NON_STAT = {"year", "팀경기수", "규정"}


# ============================================================ 데이터 준비
def _div(a, b):
    return a / b.where(b != 0)


def build_season_wide(db_path, position):
    """선수-시즌 한 줄짜리 넓은 표(숫자형) + 파생지표 + 시대보정 지수.
    KBO가 값을 아예 제공하지 않아 0으로 채워둔 옛 연도 지표는 NaN으로 바꿔 분석에서 빠지게 한다."""
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT year, category, team, player, stat_name, stat_value, player_id FROM historical_player_stat "
        "WHERE category IN (?, ?)", conn, params=(position, position + "_adv"))
    standings = pd.read_sql_query("SELECT year, team, games FROM historical_standings", conn)
    conn.close()

    base = df[df["category"] == position]
    wide = base.pivot_table(index=["year", "team", "player"], columns="stat_name",
                            values="stat_value", aggfunc="first")
    adv = df[df["category"] == position + "_adv"].pivot_table(
        index=["year", "team", "player"], columns="stat_name", values="stat_value", aggfunc="first")
    adv = adv[[c for c in adv.columns if c not in wide.columns]]
    pid = base.dropna(subset=["player_id"]).groupby(["year", "team", "player"])["player_id"].first()
    wide = wide.join(adv, how="left").join(pid, how="left").reset_index()

    wide["year"] = wide["year"].astype(int)
    for c in [c for c in wide.columns if c not in ("year", "team", "player", "player_id")]:
        wide[c] = wide[c].map(sabermetrics.parse_innings) if c == "IP" else pd.to_numeric(wide[c], errors="coerce")

    for (cat, stat), y0 in analysis.STAT_AVAILABLE_FROM.items():
        if cat == position and stat in wide.columns:
            wide.loc[wide["year"] < y0, stat] = np.nan
    if position == "pitching" and {"BB", "H", "IP"} <= set(wide.columns):
        calc = _div(wide["BB"] + wide["H"], wide["IP"])
        wide["WHIP"] = wide["WHIP"].fillna(calc) if "WHIP" in wide.columns else calc

    st_df = standings.assign(year=standings["year"].astype(int)).rename(columns={"games": "팀경기수"})
    wide = wide.merge(st_df[["year", "team", "팀경기수"]], on=["year", "team"], how="left")
    wide["연대"] = (wide["year"] // 10 * 10).astype(str) + "년대"

    if position == "batting":
        wide["규정"] = wide["PA"] >= 3.1 * wide["팀경기수"]
        wide = _batting_derived(wide)
    else:
        wide["규정"] = wide["IP"] >= wide["팀경기수"]
        wide = _pitching_derived(wide)
    return wide


def _col(df, name):
    return df[name] if name in df.columns else pd.Series(np.nan, index=df.index)


def _batting_derived(w):
    h, ab, hr, so, sf = (_col(w, c) for c in ("H", "AB", "HR", "SO", "SF"))
    w["BABIP"] = _div(h - hr, ab - so - hr + sf.fillna(0))
    w["HR%"] = _div(hr, _col(w, "PA")) * 100
    w["XBH"] = _col(w, "2B") + _col(w, "3B") + hr
    w["PA/G"] = _div(_col(w, "PA"), _col(w, "G"))
    cnt = w.groupby("year")[["H", "AB", "BB", "HBP", "SF", "TB"]].sum()
    lg_avg = cnt["H"] / cnt["AB"]
    lg_obp = (cnt["H"] + cnt["BB"] + cnt["HBP"]) / (cnt["AB"] + cnt["BB"] + cnt["HBP"] + cnt["SF"])
    lg_slg = cnt["TB"] / cnt["AB"]
    y = w["year"]
    w["AVG+"] = 100 * w["AVG"] / y.map(lg_avg)
    w["OBP+"] = 100 * w["OBP"] / y.map(lg_obp)
    w["SLG+"] = 100 * w["SLG"] / y.map(lg_slg)
    w["OPS+"] = 100 * w["OPS"] / y.map(lg_obp + lg_slg)
    return w


def _pitching_derived(w):
    ip, so, bb, h, hr, tbf = (_col(w, c) for c in ("IP", "SO", "BB", "H", "HR", "TBF"))
    hbp, r, er, np_ = _col(w, "HBP"), _col(w, "R"), _col(w, "ER"), _col(w, "NP")
    w["K/9"] = _div(so * 9, ip)
    w["BB/9"] = _div(bb * 9, ip)
    w["HR/9"] = _div(hr * 9, ip)
    w["H/9"] = _div(h * 9, ip)
    w["K/BB"] = _div(so, bb)
    w["RA9"] = _div(r * 9, ip)
    w["P/IP"] = _div(np_, ip)
    w["P/BF"] = _div(np_, tbf)
    w["IP/G"] = _div(ip, _col(w, "G"))
    bip = tbf - so - bb - hbp.fillna(0) - hr - _col(w, "SAC").fillna(0) - _col(w, "SF").fillna(0)
    w["BABIP"] = _div(h - hr, bip)
    w["LOB%"] = _div(h + bb + hbp.fillna(0) - r, h + bb + hbp.fillna(0) - 1.4 * hr) * 100
    w["역할"] = pd.cut(w["IP/G"], [-0.01, 1.5, 4.0, 100], labels=["불펜형", "중간·롱", "선발형"]).astype(object)
    w.loc[w["IP/G"].isna(), "역할"] = np.nan
    cnt = w.groupby("year")[["ER", "IP", "BB", "H"]].sum()
    lg_era = cnt["ER"] * 9 / cnt["IP"]
    lg_whip = (cnt["BB"] + cnt["H"]) / cnt["IP"]
    y = w["year"]
    w["ERA-"] = 100 * w["ERA"] / y.map(lg_era)
    w["WHIP-"] = 100 * w["WHIP"] / y.map(lg_whip)
    if "FIP" in w.columns:
        w["FIP-"] = 100 * w["FIP"] / y.map(lg_era)  # 리그 평균 FIP는 상수 보정으로 리그 ERA와 같다
    return w


def build_team_wide(db_path, position):
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT year, category, team, stat_name, stat_value FROM historical_team_stat WHERE category IN (?, ?)",
        conn, params=(position, position + "_adv"))
    conn.close()
    wide = df.pivot_table(index=["year", "team"], columns="stat_name", values="stat_value", aggfunc="first")
    wide = wide.reset_index()
    wide["year"] = wide["year"].astype(int)
    for c in [c for c in wide.columns if c not in ("year", "team")]:
        wide[c] = wide[c].map(sabermetrics.parse_innings) if c == "IP" else pd.to_numeric(wide[c], errors="coerce")
    return wide


def build_appearances(db_path):
    """이번 시즌 투수 등판 한 건당 한 줄 — 선발 여부·휴식일·게임스코어를 붙인다."""
    conn = sqlite3.connect(db_path)
    pgs = pd.read_sql_query(
        "SELECT game_id, game_date, team, player, stat_name, stat_value FROM player_game_stat "
        "WHERE category='pitching'", conn)
    games = pd.read_sql_query(
        "SELECT game_id, home_team, away_team, home_starter, away_starter FROM games WHERE game_id IS NOT NULL", conn)
    conn.close()
    if pgs.empty:
        return pd.DataFrame()
    pgs["stat_value"] = pd.to_numeric(pgs["stat_value"], errors="coerce")
    a = pgs.pivot_table(index=["game_id", "game_date", "team", "player"], columns="stat_name",
                        values="stat_value", aggfunc="first").reset_index()
    for c in ("IP", "H", "ER", "R", "BB", "SO", "HR", "TBF", "NP"):
        if c not in a.columns:
            a[c] = 0.0
    a = a.merge(games, on="game_id", how="left")
    is_home = a["team"] == a["home_team"]
    a["상대"] = np.where(is_home, a["away_team"], a["home_team"])
    a["선발"] = a["player"] == np.where(is_home, a["home_starter"], a["away_starter"])
    a["date"] = pd.to_datetime(a["game_date"], format="%Y%m%d")
    a = a.sort_values(["player", "team", "date"]).reset_index(drop=True)
    prev = a.groupby(["player", "team"])["date"].shift(1)
    a["휴식일"] = (a["date"] - prev).dt.days - 1
    outs = (a["IP"] * 3).round()
    a["게임스코어"] = (50 + outs + 2 * np.maximum(0, np.floor(a["IP"]) - 4) + a["SO"]
                  - 2 * a["H"] - 4 * a["ER"] - 2 * (a["R"] - a["ER"]) - a["BB"])
    a["QS"] = a["선발"] & (a["IP"] >= 6) & (a["ER"] <= 3)
    return a


def build_team_games(db_path):
    conn = sqlite3.connect(db_path)
    g = pd.read_sql_query(
        "SELECT game_date, away_team, home_team, away_score, home_score, stadium FROM games WHERE status='RESULT'", conn)
    conn.close()
    if g.empty:
        return pd.DataFrame()
    home = pd.DataFrame({"game_date": g.game_date, "team": g.home_team, "상대": g.away_team,
                         "득점": g.home_score, "실점": g.away_score, "홈": True})
    away = pd.DataFrame({"game_date": g.game_date, "team": g.away_team, "상대": g.home_team,
                         "득점": g.away_score, "실점": g.home_score, "홈": False})
    t = pd.concat([home, away], ignore_index=True)
    t["결과"] = np.select([t["득점"] > t["실점"], t["득점"] < t["실점"]], ["승", "패"], "무")
    t["시즌"] = t["game_date"].str[:4]
    return t


@st.cache_data(show_spinner="분석용 데이터를 준비하는 중...", max_entries=4)
def load_wide(db_path, mtime, position):
    return build_season_wide(db_path, position)


@st.cache_data(show_spinner=False, max_entries=4)
def load_team_wide(db_path, mtime, position):
    return build_team_wide(db_path, position)


@st.cache_data(show_spinner=False, max_entries=2)
def load_appearances(db_path, mtime):
    return build_appearances(db_path)


@st.cache_data(show_spinner=False, max_entries=2)
def load_team_games(db_path, mtime):
    return build_team_games(db_path)


# ============================================================ 공통 UI 부품
def numeric_stats(wide, position):
    cols = [c for c in wide.columns
            if c not in NON_STAT and pd.api.types.is_numeric_dtype(wide[c]) and wide[c].dtype != bool
            and wide[c].notna().sum() >= 30]
    pref = [c for c in PREFERRED[position] if c in cols]
    return pref + sorted(c for c in cols if c not in pref)


def make_label(position, base_labels):
    derived = DERIVED_LABELS[position]

    def label(code):
        desc = derived.get(code) or base_labels.get(code)
        return f"{code} · {desc}" if desc else code
    return label


def _idx(options, name, fallback=0):
    return options.index(name) if name in options else min(fallback, len(options) - 1)


def filter_panel(wide, position, key, default_last_year_only=True):
    years = sorted(wide["year"].unique())
    c1, c2, c3 = st.columns([3, 1.2, 2])
    with c1:
        default = (years[-1], years[-1]) if default_last_year_only else (years[0], years[-1])
        yr = st.slider("연도 범위", int(years[0]), int(years[-1]), (int(default[0]), int(default[1])), key=f"{key}_yr")
    with c2:
        q = st.checkbox("규정 충족만", value=True, key=f"{key}_q",
                        help="타자는 팀 경기수×3.1타석, 투수는 팀 경기수×1이닝 이상. 끄면 표본이 적은 선수의 극단값이 섞입니다.")
    with c3:
        teams = st.multiselect("팀 (비우면 전체)", sorted(wide["team"].unique()), key=f"{key}_team")
    df = wide[(wide["year"] >= yr[0]) & (wide["year"] <= yr[1])]
    if q:
        df = df[df["규정"]]
    if teams:
        df = df[df["team"].isin(teams)]
    if position == "pitching" and not q:
        roles = st.multiselect("역할(IP/G로 추정)", ["선발형", "중간·롱", "불펜형"], key=f"{key}_role")
        if roles:
            df = df[df["역할"].isin(roles)]
    return df, yr


def csv_button(df, name, key):
    st.download_button("표를 CSV로 내려받기", df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=name, mime="text/csv", key=key)


def _round_df(df):
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_float_dtype(out[c]):
            out[c] = out[c].round(3)
    return out


# ============================================================ 1. 산점도
def tool_scatter(wide, position, label, colors):
    df, yr = filter_panel(wide, position, f"sc_{position}")
    stats = numeric_stats(wide, position)
    c1, c2, c3, c4 = st.columns(4)
    dx, dy = ("OBP", "SLG") if position == "batting" else ("K/9", "ERA")
    with c1:
        x = st.selectbox("X축", stats, index=_idx(stats, dx), format_func=label, key=f"sc_x_{position}")
    with c2:
        y = st.selectbox("Y축", stats, index=_idx(stats, dy, 1), format_func=label, key=f"sc_y_{position}")
    color_opts = ["팀", "연대", "없음"] + (["역할"] if position == "pitching" else [])
    with c3:
        color_by = st.selectbox("색상", color_opts, key=f"sc_c_{position}")
    with c4:
        size_by = st.selectbox("점 크기", ["없음", "PA" if position == "batting" else "IP"], key=f"sc_s_{position}")
    o1, o2 = st.columns(2)
    with o1:
        trend = st.checkbox("추세선 + 상관계수", value=True, key=f"sc_t_{position}")
    with o2:
        n_label = st.slider("추세선에서 가장 멀리 떨어진 선수 이름 표시", 0, 20, 8, key=f"sc_n_{position}")

    d = df.dropna(subset=[x, y]).copy()
    if len(d) < 3:
        st.info("이 조건에는 표시할 선수가 거의 없습니다. 연도 범위를 넓히거나 '규정 충족만'을 꺼보세요.")
        return
    d["이름"] = d["player"] + " " + d["year"].astype(str)
    fit = None
    if trend and x != y:
        slope, icpt = np.polyfit(d[x], d[y], 1)
        r = np.corrcoef(d[x], d[y])[0, 1]
        d["_resid"] = (d[y] - (slope * d[x] + icpt)).abs()
        fit = (slope, icpt, r)
    d["_label"] = ""
    if n_label and len(d) <= 4000:
        top = d["_resid"].nlargest(n_label).index if fit else d[y].abs().nlargest(n_label).index
        d.loc[top, "_label"] = d.loc[top, "이름"]

    color_col = {"팀": "team", "연대": "연대", "역할": "역할", "없음": None}[color_by]
    size_col = None
    if size_by != "없음":
        d["_size"] = d[size_by].fillna(0).clip(lower=1)
        size_col = "_size"
    hover = {c: True for c in [x, y] if c in d.columns}
    fig = px.scatter(d, x=x, y=y, color=color_col, size=size_col, hover_name="이름", hover_data=hover,
                     text="_label", color_discrete_map=colors if color_col == "team" else None,
                     render_mode="webgl" if len(d) > 4000 else "auto", size_max=16, opacity=0.8)
    fig.update_traces(textposition="top center", textfont_size=11)
    if fit:
        xs = np.array([d[x].min(), d[x].max()])
        fig.add_trace(go.Scatter(x=xs, y=fit[0] * xs + fit[1], mode="lines", name="추세선",
                                 line=dict(color="#888", dash="dash")))
    fig.update_layout(height=560, xaxis_title=label(x), yaxis_title=label(y), legend_title_text=color_by)
    st.plotly_chart(fig, use_container_width=True)

    if fit:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("표본", f"{len(d):,}개 시즌")
        m2.metric("상관계수 r", f"{fit[2]:.3f}")
        m3.metric("설명력 R²", f"{fit[2] ** 2:.3f}")
        m4.metric("기울기", f"{fit[0]:.4g}", help=f"X가 1 늘 때 Y가 평균 {fit[0]:.4g} 변함")
        st.caption("상관은 인과가 아닙니다. 같은 시즌 안에서도 구장·팀 환경의 영향이 섞여 있습니다.")
    show = ["year", "team", "player", x, y] + (["PA"] if position == "batting" else ["IP"])
    show = list(dict.fromkeys(c for c in show if c in d.columns))
    table = _round_df(d.sort_values(y, ascending=position == "pitching" and y in LOWER_BETTER[position])[show])
    st.dataframe(table.rename(columns={"year": "연도", "team": "팀", "player": "선수"}),
                 hide_index=True, use_container_width=True, height=320)
    csv_button(table, f"scatter_{position}_{x}_{y}.csv".replace("/", "-").replace("%", "pct"), f"sc_dl_{position}")


# ============================================================ 2. 분포
def tool_distribution(wide, position, label, colors):
    df, yr = filter_panel(wide, position, f"ds_{position}")
    stats = numeric_stats(wide, position)
    c1, c2, c3 = st.columns(3)
    with c1:
        stat = st.selectbox("지표", stats, index=_idx(stats, "OPS" if position == "batting" else "ERA"),
                            format_func=label, key=f"ds_s_{position}")
    group_opts = ["없음", "팀", "연도", "연대"] + (["역할"] if position == "pitching" else [])
    with c2:
        group = st.selectbox("나눠서 보기", group_opts, key=f"ds_g_{position}")
    with c3:
        kind = st.radio("차트", ["박스플롯", "히스토그램"], horizontal=True, key=f"ds_k_{position}")
    d = df.dropna(subset=[stat]).copy()
    if len(d) < 5:
        st.info("표본이 너무 적습니다. 연도 범위를 넓히거나 '규정 충족만'을 꺼보세요.")
        return
    gcol = {"없음": None, "팀": "team", "연도": "year", "연대": "연대", "역할": "역할"}[group]
    if gcol == "year":
        d["year"] = d["year"].astype(str)
    if kind == "박스플롯":
        fig = px.box(d, x=gcol, y=stat, color=gcol if gcol in ("team", "연대", "역할") else None, points="outliers",
                     hover_name="player", color_discrete_map=colors if gcol == "team" else None)
        fig.update_layout(showlegend=False, xaxis_title=group if gcol else "")
    else:
        fig = px.histogram(d, x=stat, color=gcol, nbins=40, marginal="box", barmode="overlay", opacity=0.65,
                           color_discrete_map=colors if gcol == "team" else None)
    fig.update_layout(height=480, yaxis_title=label(stat) if kind == "박스플롯" else "시즌 수")
    st.plotly_chart(fig, use_container_width=True)

    qs = [0.1, 0.25, 0.5, 0.75, 0.9]
    summ = (d.groupby(gcol)[stat] if gcol else d[stat]).describe(percentiles=qs)
    if not gcol:
        summ = summ.to_frame().T
        summ.index = ["전체"]
    summ = summ.rename(columns={"count": "표본", "mean": "평균", "std": "표준편차", "min": "최소", "10%": "하위10%",
                                "25%": "25%", "50%": "중앙값", "75%": "75%", "90%": "상위10%", "max": "최대"})
    summ["표본"] = summ["표본"].astype(int)
    st.dataframe(_round_df(summ), use_container_width=True)

    name = st.text_input("선수 이름으로 이 분포에서의 위치 보기", key=f"ds_p_{position}")
    if name:
        hit = d[d["player"].str.contains(name, na=False)].copy()
        if hit.empty:
            st.caption("지금 조건(연도·규정·팀)의 표본에 그 선수가 없습니다.")
        else:
            better_low = stat in LOWER_BETTER[position]
            hit["상위 %"] = hit[stat].map(lambda v: ((d[stat] < v).mean() if better_low else (d[stat] > v).mean()) * 100)
            st.dataframe(_round_df(hit[["year", "team", "player", stat, "상위 %"]].rename(
                columns={"year": "연도", "team": "팀", "player": "선수"})), hide_index=True, use_container_width=True)
            st.caption(f"상위 % = 이 표본에서 {'더 낮은' if better_low else '더 높은'} 값을 가진 시즌의 비율입니다 (작을수록 상위권).")


# ============================================================ 3. 상관관계
def tool_correlation(wide, position, label, colors):
    df, yr = filter_panel(wide, position, f"co_{position}", default_last_year_only=False)
    stats = numeric_stats(wide, position)
    default = (["AVG", "OBP", "SLG", "HR", "BB%", "K%", "BABIP", "wOBA", "ISO", "WAR"] if position == "batting"
               else ["ERA", "FIP", "WHIP", "K/9", "BB/9", "HR/9", "BABIP", "LOB%", "WAR"])
    c1, c2 = st.columns([4, 1])
    with c1:
        picks = st.multiselect("지표 선택 (2개 이상)", stats, default=[s for s in default if s in stats],
                               format_func=label, key=f"co_p_{position}")
    with c2:
        method = st.radio("방식", ["pearson", "spearman"], key=f"co_m_{position}",
                          help="spearman은 순위 기반이라 극단값에 덜 민감합니다.")
    if len(picks) >= 2:
        corr = df[picks].corr(method=method, min_periods=30)
        fig = px.imshow(corr, text_auto=".2f", color_continuous_scale="RdBu_r", zmin=-1, zmax=1, aspect="auto")
        fig.update_layout(height=max(420, 48 * len(picks)))
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"표본 {len(df):,}개 시즌 · {yr[0]}~{yr[1]}. 파랑=함께 오르내림이 반대, 빨강=같은 방향.")
    else:
        st.info("지표를 2개 이상 고르면 상관 행렬이 나옵니다.")

    st.markdown("**하나의 지표와 가장 같이 움직이는 지표**")
    target = st.selectbox("목표 지표", stats, index=_idx(stats, "WAR" if "WAR" in stats else stats[0]),
                          format_func=label, key=f"co_t_{position}")
    sub = df[stats]
    cor = sub.corrwith(df[target], method=method).drop(labels=[target], errors="ignore")
    cnt = sub.notna().mul(df[target].notna(), axis=0).sum().reindex(cor.index)
    cor = cor[cnt >= 30].dropna()
    top = cor.reindex(cor.abs().sort_values(ascending=False).index).head(15)
    if top.empty:
        st.info("표본이 부족합니다.")
        return
    bar = px.bar(top[::-1], orientation="h", labels={"value": f"{target}와의 상관계수", "index": ""})
    bar.update_layout(showlegend=False, height=420)
    st.plotly_chart(bar, use_container_width=True)
    st.caption("WAR·wRC+·FIP처럼 다른 지표를 재료로 계산한 값은 당연히 재료와 높게 나옵니다(순환). "
               "기본 지표끼리의 관계를 볼 때 더 의미가 있습니다.")


# ============================================================ 4. 시대별 추이
def tool_trend(wide, position, label, colors, db_path, mtime):
    scope = st.radio("대상", ["선수 전체(1982~)", "팀 기록(2001~)"], horizontal=True, key=f"tr_scope_{position}")
    if scope.startswith("팀"):
        team_trend(position, label, colors, db_path, mtime)
        return
    c1, c2, c3 = st.columns(3)
    stats = numeric_stats(wide, position)
    with c1:
        stat = st.selectbox("지표", stats, index=_idx(stats, "OPS" if position == "batting" else "ERA"),
                            format_func=label, key=f"tr_s_{position}")
    with c2:
        agg = st.selectbox("대표값", ["가중평균(타석/이닝)", "중앙값", "상위 10%선", "하위 10%선"], key=f"tr_a_{position}")
    with c3:
        q = st.checkbox("규정 충족 선수만", value=True, key=f"tr_q_{position}")
    d = wide[wide["규정"]] if q else wide
    d = d.dropna(subset=[stat])
    wcol = "PA" if position == "batting" else "IP"
    if d.empty:
        st.info("데이터가 없습니다.")
        return
    d = d.assign(_w=d[wcol].fillna(0).clip(lower=0.001))
    d["_sw"] = d[stat] * d["_w"]
    g = d.groupby("year")
    out = pd.DataFrame({
        "표본": g.size(),
        "가중평균": g["_sw"].sum() / g["_w"].sum(),
        "중앙값": g[stat].median(), "하위10%선": g[stat].quantile(0.1), "25%": g[stat].quantile(0.25),
        "75%": g[stat].quantile(0.75), "상위10%선": g[stat].quantile(0.9),
    })
    main = {"가중평균(타석/이닝)": "가중평균", "중앙값": "중앙값", "상위 10%선": "상위10%선", "하위 10%선": "하위10%선"}[agg]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=out.index, y=out["75%"], mode="lines", line=dict(width=0), showlegend=False,
                             hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=out.index, y=out["25%"], mode="lines", line=dict(width=0), fill="tonexty",
                             fillcolor="rgba(120,120,120,0.18)", name="25~75% 구간", hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=out.index, y=out[main], mode="lines+markers", name=agg,
                             line=dict(color="#C30452", width=3)))
    fig.update_layout(height=480, xaxis_title="연도", yaxis_title=label(stat), hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True)
    st.caption("회색 띠는 그 해 선수들의 가운데 50% 구간입니다. 지수 지표(OPS+·ERA- 등)는 정의상 리그 평균이 100이라 "
               "시대 비교용이고, 원지표(OPS·ERA)로 보면 투고타저·타고투저 흐름이 보입니다.")
    st.dataframe(_round_df(out.reset_index().rename(columns={"year": "연도"})), hide_index=True,
                 use_container_width=True, height=300)
    csv_button(out.reset_index(), f"trend_{position}_{stat}.csv".replace("/", "-").replace("%", "pct"), f"tr_dl_{position}")


def team_trend(position, label, colors, db_path, mtime):
    tw = load_team_wide(db_path, mtime, position)
    stats = [c for c in tw.columns if c not in ("year", "team") and tw[c].notna().sum() >= 20]
    c1, c2 = st.columns([1, 3])
    with c1:
        stat = st.selectbox("팀 지표", stats, index=_idx(stats, "OPS" if position == "batting" else "ERA"),
                            format_func=label, key=f"tt_s_{position}")
    with c2:
        teams = st.multiselect("팀", sorted(tw["team"].unique()), default=sorted(tw["team"].unique())[:5],
                               key=f"tt_t_{position}")
    d = tw[tw["team"].isin(teams)] if teams else tw
    fig = px.line(d.sort_values("year"), x="year", y=stat, color="team", markers=True, color_discrete_map=colors)
    fig.update_layout(height=480, xaxis_title="연도", yaxis_title=label(stat))
    st.plotly_chart(fig, use_container_width=True)
    lg = tw.groupby("year")[stat].mean().round(3).rename("10개 팀 평균").reset_index()
    st.caption("KBO 팀 기록 페이지가 2001년부터만 연도 조회를 지원해 2001년 이후만 볼 수 있습니다.")
    st.dataframe(lg.rename(columns={"year": "연도"}), hide_index=True, use_container_width=True, height=240)


# ============================================================ 5. 선수 비교
def tool_compare(wide, position, label, colors):
    st.caption("이름 일부로 검색해 최대 6명을 골라 같은 지표의 시즌별 흐름을 겹쳐 봅니다. 시대가 다른 선수는 "
               "OPS+·ERA-처럼 리그 평균을 100으로 맞춘 지수 지표로 비교하는 게 공정합니다.")
    w = wide.assign(identity=np.where(wide["player_id"].notna(), "ID:" + wide["player_id"].astype(str),
                                      "NAME:" + wide["player"]))
    q = st.text_input("선수 이름 검색 (예: 이종범, 선동열)", key=f"cp_q_{position}")
    if not q:
        st.info("이름을 입력하세요.")
        return
    hits = w[w["player"].str.contains(q, na=False)]
    if hits.empty:
        st.warning("일치하는 선수가 없습니다.")
        return
    meta = hits.groupby("identity").agg(player=("player", "first"), first=("year", "min"), last=("year", "max"),
                                         team=("team", lambda s: s.mode().iat[0]))
    meta["label"] = meta.apply(lambda r: f"{r.player} ({r.team} · {r['first']}~{r['last']})", axis=1)
    picked_labels = st.multiselect("비교할 선수 (최대 6명)", meta["label"].tolist(), key=f"cp_sel_{position}_{q}")
    if not picked_labels:
        return
    picked = meta[meta["label"].isin(picked_labels[:6])]
    stats = numeric_stats(wide, position)
    c1, c2, c3 = st.columns(3)
    with c1:
        stat = st.selectbox("지표", stats, index=_idx(stats, "OPS+" if position == "batting" else "ERA-"),
                            format_func=label, key=f"cp_s_{position}")
    with c2:
        xaxis = st.radio("가로축", ["달력 연도", "커리어 N년차"], horizontal=True, key=f"cp_x_{position}")
    with c3:
        min_w = st.number_input("최소 " + ("타석" if position == "batting" else "이닝"), 0, 700, 0,
                                key=f"cp_m_{position}", help="표본이 적은 시즌을 그래프에서 뺍니다.")
    wcol = "PA" if position == "batting" else "IP"
    d = w[w["identity"].isin(picked.index)].sort_values(wcol, ascending=False).drop_duplicates(["identity", "year"])
    d = d[d[wcol].fillna(0) >= min_w].dropna(subset=[stat]).sort_values(["identity", "year"])
    if d.empty:
        st.info("조건에 맞는 시즌이 없습니다.")
        return
    d["선수"] = d["identity"].map(picked["label"])
    d["커리어 N년차"] = d.groupby("identity").cumcount() + 1
    xcol = "year" if xaxis == "달력 연도" else "커리어 N년차"
    fig = px.line(d, x=xcol, y=stat, color="선수", markers=True, hover_data=["team"])
    fig.update_layout(height=480, xaxis_title=xaxis, yaxis_title=label(stat))
    st.plotly_chart(fig, use_container_width=True)
    summ = d.groupby("선수").agg(시즌수=(stat, "size"), 평균=(stat, "mean"), 최고=(stat, "max"), 최저=(stat, "min"))
    best = d.loc[d.groupby("선수")[stat].idxmax(), ["선수", "year"]].set_index("선수")["year"]
    summ["최고 시즌"] = best
    st.dataframe(_round_df(summ.reset_index()), hide_index=True, use_container_width=True)
    pivot = d.pivot_table(index="year", columns="선수", values=stat).round(3).reset_index().rename(columns={"year": "연도"})
    with st.expander("시즌별 표"):
        st.dataframe(pivot, hide_index=True, use_container_width=True)
        csv_button(pivot, "compare.csv", f"cp_dl_{position}")


# ============================================================ 6. 투수 등판 분석
def tool_pitching(db_path, mtime):
    a = load_appearances(db_path, mtime)
    if a.empty:
        st.info("이번 시즌 투수 박스스코어가 아직 없습니다.")
        return
    st.caption("이번 시즌 네이버 박스스코어(등판 1건=1행)로 계산합니다. 선발은 경기 정보의 선발투수 이름과 맞춰 판정하고, "
               "게임스코어는 빌 제임스 공식(50 + 아웃 + 5회 이후 이닝×2 + 탈삼진 − 2×피안타 − 4×자책 − 2×비자책 − 볼넷)입니다.")
    dmin, dmax = a["date"].min().date(), a["date"].max().date()
    c1, c2 = st.columns([2, 2])
    with c1:
        rng = st.date_input("기간", (dmin, dmax), min_value=dmin, max_value=dmax, key="pl_dates")
    with c2:
        teams = st.multiselect("팀 (비우면 전체)", sorted(a["team"].unique()), key="pl_teams")
    if isinstance(rng, tuple) and len(rng) == 2:
        a = a[(a["date"] >= pd.Timestamp(rng[0])) & (a["date"] <= pd.Timestamp(rng[1]))]
    if teams:
        a = a[a["team"].isin(teams)]
    sub = st.radio("분석", ["선발 리더보드", "휴식일별 성적", "투구수와 게임스코어", "불펜 부하"], horizontal=True, key="pl_sub")
    s = a[a["선발"]]

    if sub == "선발 리더보드":
        if s.empty:
            st.info("선발 등판이 없습니다.")
            return
        g = s.groupby(["player", "team"]).agg(
            선발=("game_id", "nunique"), IP=("IP", "sum"), ER=("ER", "sum"), H=("H", "sum"), BB=("BB", "sum"),
            SO=("SO", "sum"), HR=("HR", "sum"), 평균투구수=("NP", "mean"), 최다투구수=("NP", "max"), QS=("QS", "sum"),
            평균게임스코어=("게임스코어", "mean"), 평균휴식일=("휴식일", "mean")).reset_index()
        min_gs = st.slider("최소 선발 등판", 1, int(max(g["선발"].max(), 2)), min(5, int(g["선발"].max())), key="pl_mings")
        g = g[g["선발"] >= min_gs].copy()
        g["ERA"] = g["ER"] * 9 / g["IP"]
        g["WHIP"] = (g["BB"] + g["H"]) / g["IP"]
        g["K/9"] = g["SO"] * 9 / g["IP"]
        g["BB/9"] = g["BB"] * 9 / g["IP"]
        g["이닝/선발"] = g["IP"] / g["선발"]
        g["QS%"] = g["QS"] / g["선발"] * 100
        g["이닝"] = g["IP"].map(sabermetrics.format_innings)
        cols = ["player", "team", "선발", "이닝", "이닝/선발", "ERA", "WHIP", "K/9", "BB/9", "QS", "QS%",
                "평균투구수", "최다투구수", "평균게임스코어", "평균휴식일"]
        out = _round_df(g.sort_values("평균게임스코어", ascending=False)[cols].rename(columns={"player": "선수", "team": "팀"}))
        st.dataframe(out, hide_index=True, use_container_width=True, height=460)
        csv_button(out, "starters.csv", "pl_dl_starters")
    elif sub == "휴식일별 성적":
        r = s.dropna(subset=["휴식일"]).copy()
        if r.empty:
            st.info("휴식일을 계산할 선발 등판이 부족합니다.")
            return
        r["구간"] = pd.cut(r["휴식일"], [-1, 3, 4, 5, 6, 100], labels=["3일 이하", "4일", "5일", "6일", "7일 이상"])
        g = r.groupby("구간", observed=True).agg(등판=("game_id", "size"), IP=("IP", "sum"), ER=("ER", "sum"),
                                              SO=("SO", "sum"), BB=("BB", "sum"), H=("H", "sum"),
                                              평균투구수=("NP", "mean"), 평균게임스코어=("게임스코어", "mean")).reset_index()
        g["ERA"] = g["ER"] * 9 / g["IP"]
        g["K/9"] = g["SO"] * 9 / g["IP"]
        g["이닝/선발"] = g["IP"] / g["등판"]
        m = st.radio("차트 지표", ["평균게임스코어", "ERA", "이닝/선발", "평균투구수"], horizontal=True, key="pl_rest_m")
        fig = px.bar(g, x="구간", y=m, text=g["등판"].map(lambda n: f"n={n}"))
        fig.update_layout(height=420, xaxis_title="직전 등판 후 쉰 날", yaxis_title=m)
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(_round_df(g.drop(columns=["IP", "ER", "SO", "BB", "H"])), hide_index=True, use_container_width=True)
        st.caption("구간별 표본(n)이 작으면 차이가 우연일 수 있습니다. 휴식일이 짧은 선발은 대개 에이스라 "
                   "단순 비교에는 선발 선택 편향이 섞입니다.")
    elif sub == "투구수와 게임스코어":
        if s.empty:
            st.info("선발 등판이 없습니다.")
            return
        d = s.copy()
        d["이름"] = d["player"] + " " + d["game_date"]
        fig = px.scatter(d, x="NP", y="게임스코어", color="team", hover_name="이름", opacity=0.75,
                         hover_data=["IP", "ER", "SO"])
        if len(d) >= 3:
            slope, icpt = np.polyfit(d["NP"], d["게임스코어"], 1)
            xs = np.array([d["NP"].min(), d["NP"].max()])
            fig.add_trace(go.Scatter(x=xs, y=slope * xs + icpt, mode="lines", name="추세선", line=dict(dash="dash", color="#888")))
        fig.update_layout(height=520, xaxis_title="투구수", yaxis_title="게임스코어")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("투구수가 많은 날은 오래 던졌다는 뜻이라 게임스코어가 같이 오르는 게 자연스럽습니다. "
                   "추세선에서 아래쪽으로 멀리 떨어진 점은 많이 던지고도 부진했던 등판입니다.")
    else:
        n = st.radio("최근 며칠", [3, 5, 7, 14], index=2, horizontal=True, key="pl_bull_n")
        end = a["date"].max()
        w = a[(~a["선발"]) & (a["date"] > end - pd.Timedelta(days=n))]
        if w.empty:
            st.info("해당 기간에 구원 등판이 없습니다.")
            return
        g = w.groupby(["player", "team"]).agg(등판=("game_id", "size"), IP=("IP", "sum"), 투구수=("NP", "sum"),
                                              연투=("휴식일", lambda x: int((x == 0).sum())),
                                              마지막=("date", "max")).reset_index()
        g["마지막 등판"] = (end - g["마지막"]).dt.days.map(lambda d: "오늘/마지막 경기일" if d == 0 else f"{d}일 전")
        g["이닝"] = g["IP"].map(sabermetrics.format_innings)
        g = g.sort_values(["투구수", "등판"], ascending=False)
        top = g.head(15)
        fig = px.bar(top[::-1], x="투구수", y="player", color="team", orientation="h", text="등판", hover_data=["이닝", "연투"])
        fig.update_layout(height=520, yaxis_title="", xaxis_title=f"최근 {n}일 투구수 (막대 안 숫자=등판 횟수)")
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(g[["player", "team", "등판", "이닝", "투구수", "연투", "마지막 등판"]].rename(
            columns={"player": "선수", "team": "팀"}), hide_index=True, use_container_width=True, height=360)
        st.caption(f"기준일 {end.date()} 포함 최근 {n}일. 연투=전날에도 등판한 횟수입니다. 시즌 박스스코어 기반이라 "
                   "퓨처스·불펜 대기(등판 안 한 날)는 반영되지 않습니다.")


# ============================================================ 7. 팀 매치업
def tool_matchup(db_path, mtime, colors):
    t = load_team_games(db_path, mtime)
    if t.empty:
        st.info("종료된 경기가 없습니다.")
        return
    seasons = sorted(t["시즌"].unique())
    c1, c2 = st.columns([1, 3])
    with c1:
        season = st.selectbox("시즌", seasons, index=len(seasons) - 1, key="mu_season")
    t = t[t["시즌"] == season]
    sub = st.radio("분석", ["상대전적 히트맵", "득점별 승률", "홈·원정"], horizontal=True, key="mu_sub")

    if sub == "상대전적 히트맵":
        piv = t.pivot_table(index="team", columns="상대", values="결과", aggfunc=lambda x: (x == "승").sum() / max(((x == "승") | (x == "패")).sum(), 1) * 100)
        order = (t.assign(w=(t["결과"] == "승")).groupby("team")["w"].mean().sort_values(ascending=False).index)
        piv = piv.reindex(index=order, columns=order)
        rec = t.groupby(["team", "상대"])["결과"].agg(lambda x: f"{(x == '승').sum()}-{(x == '패').sum()}-{(x == '무').sum()}")
        text = piv.copy().astype(object)
        for r in piv.index:
            for cc in piv.columns:
                text.loc[r, cc] = rec.get((r, cc), "")
        fig = go.Figure(go.Heatmap(z=piv.values, x=list(piv.columns), y=list(piv.index), text=text.values,
                                   texttemplate="%{text}", colorscale="RdBu", zmid=50, zmin=20, zmax=80,
                                   colorbar=dict(title="승률(%)")))
        fig.update_layout(height=560, xaxis_title="상대팀", yaxis_title="", yaxis_autorange="reversed")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("행 팀의 열 팀 상대 승-패-무와 승률입니다. 빨강=강세, 파랑=약세. 팀은 시즌 승률 순입니다.")
    elif sub == "득점별 승률":
        teams = st.multiselect("팀", sorted(t["team"].unique()), default=[], key="mu_teams")
        rows = []
        for k in range(1, 11):
            sel = t[t["득점"] >= k]
            for team, g in [("리그 전체", sel)] + [(tm, sel[sel["team"] == tm]) for tm in teams]:
                w, l = (g["결과"] == "승").sum(), (g["결과"] == "패").sum()
                rows.append({"팀": team, "득점 ≥": k, "승률": w / (w + l) * 100 if w + l else np.nan, "경기": len(g)})
        d = pd.DataFrame(rows)
        fig = px.line(d, x="득점 ≥", y="승률", color="팀", markers=True, hover_data=["경기"], color_discrete_map=colors)
        fig.update_layout(height=460, yaxis_title="승률(%)", xaxis=dict(dtick=1))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("'N점 이상 냈을 때' 승률입니다. 곡선이 가파르게 올라가는 지점이 이 리그에서 이기기 시작하는 득점선입니다.")
        runs = t.groupby("득점")["결과"].agg(경기="size", 승=lambda x: (x == "승").sum()).reset_index()
        runs["승률"] = (runs["승"] / runs["경기"] * 100).round(1)
        with st.expander("득점별(정확히 N점) 표"):
            st.dataframe(runs.rename(columns={"득점": "득점(정확히)"}), hide_index=True, use_container_width=True)
    else:
        g = t.groupby(["team", "홈"]).agg(경기=("결과", "size"), 승=("결과", lambda x: (x == "승").sum()),
                                         패=("결과", lambda x: (x == "패").sum()), 득점=("득점", "mean"),
                                         실점=("실점", "mean")).reset_index()
        g["승률"] = g["승"] / (g["승"] + g["패"]) * 100
        g["구분"] = np.where(g["홈"], "홈", "원정")
        fig = px.bar(g, x="team", y="승률", color="구분", barmode="group", hover_data=["경기", "득점", "실점"],
                     color_discrete_map={"홈": "#C30452", "원정": "#315288"})
        fig.update_layout(height=440, xaxis_title="", yaxis_title="승률(%)")
        st.plotly_chart(fig, use_container_width=True)
        wide_g = g.pivot(index="team", columns="구분", values=["승률", "득점", "실점"]).round(2)
        wide_g.columns = [f"{b} {a}" for a, b in wide_g.columns]
        wide_g["홈 어드밴티지(승률 차)"] = (wide_g["홈 승률"] - wide_g["원정 승률"]).round(1)
        st.dataframe(wide_g.reset_index().rename(columns={"team": "팀"}).sort_values("홈 어드밴티지(승률 차)", ascending=False),
                     hide_index=True, use_container_width=True)


# ============================================================ 진입점
def render(db_path, bat_labels, pit_labels, team_colors):
    db_path = str(db_path)
    mtime = os.path.getmtime(db_path)
    st.subheader("데이터 분석실")
    st.caption("정해진 화면이 아니라 직접 질문을 만드는 곳입니다 — 지표를 골라 관계를 보고(산점도·상관관계), "
               "분포와 시대 흐름을 확인하고, 선수를 시대 보정해 비교하고, 이번 시즌 투수 운용과 팀 상성을 파고들 수 있습니다. "
               "모든 표는 CSV로 내려받을 수 있습니다. 옛 시즌에 KBO가 제공하지 않아 0으로 채워진 지표는 자동으로 결측 처리됩니다.")
    tool = st.radio("분석 도구", TOOLS, horizontal=True, key="lab_tool", label_visibility="collapsed")
    st.divider()

    if tool in ("투수 등판 분석", "팀 매치업"):
        if tool == "투수 등판 분석":
            tool_pitching(db_path, mtime)
        else:
            tool_matchup(db_path, mtime, team_colors)
        return

    position = st.radio("구분", ["batting", "pitching"], horizontal=True,
                        format_func=lambda x: "타자" if x == "batting" else "투수", key="lab_pos")
    wide = load_wide(db_path, mtime, position)
    label = make_label(position, bat_labels if position == "batting" else pit_labels)
    if tool == "산점도":
        tool_scatter(wide, position, label, team_colors)
    elif tool == "분포":
        tool_distribution(wide, position, label, team_colors)
    elif tool == "상관관계":
        tool_correlation(wide, position, label, team_colors)
    elif tool == "시대별 추이":
        tool_trend(wide, position, label, team_colors, db_path, mtime)
    else:
        tool_compare(wide, position, label, team_colors)
