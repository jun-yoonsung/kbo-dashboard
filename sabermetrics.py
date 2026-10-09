"""KBO 공식 기록만으로 계산 가능한 간이 세이버메트릭스.

Statiz/MyKBOStats는 로그인 및 Cloudflare 봇 차단이 걸려 있어 자동 수집 대상에서
제외했다. 대신 공식 기본기록(안타, 홈런, 볼넷, 삼진, 이닝 등)과 리그 전체 합산치로
직접 계산 가능한 근사 지표를 제공한다.

- 타자: ISO, BB%, K%, wOBA, wRC+(근사), 파워-스피드 넘버, WAR(근사, 타격+주루+포지션 조정)
- 투수: FIP, K%, BB%, WHIP, WAR(근사, FIP 기반)

주의(반드시 UI에 caveat으로 노출할 것):
- wOBA 스케일 상수, 대체선수 수준, 승리당 득점(runs/win), 포지션 조정치, 도루 득점가치는
  KBO 리그로 재보정한 값이 아니라 세이버메트릭스에서 흔히 쓰이는 고정 근사 상수를 그대로 가져온 것이다.
- 타자 WAR는 KBO 공식 수비기록 페이지의 '주 포지션'으로 포지션 조정치를, 공식 주루기록의
  도루/도루실패로 주루(도루) 가치를 반영하지만, 실제 수비 범위·프레이밍 등 개별 수비력과
  진루타(도루 외 주루) 가치는 여전히 반영하지 못한다. 즉 '타격 + 도루 + 포지션 가치' 근사치이며
  Fangraphs·Statiz의 공식 WAR(수비 범위·전체 주루까지 포함)와는 값이 다를 수 있다.
"""

LEAGUE_AVG_ERA_FALLBACK = 4.50

WOBA_SCALE = 1.15                 # 근사 고정 상수 (MLB 세이버메트릭스 관행값)
RUNS_PER_WIN = 10.0                # 근사 고정 상수 (득점환경 1승 = 약 10득점)
REPLACEMENT_RUNS_PER_600PA = 20.0  # 근사 고정 상수 (대체선수 수준, 600타석 기준)
REPLACEMENT_FIP_OFFSET = 0.80      # 근사 고정 상수 (대체선수 투수는 리그평균보다 FIP가 나쁨)

# 포지션 조정치(600타석 기준, run) — 수비 부담이 큰 포지션일수록 WAR에 가산한다.
# Fangraphs 등에서 흔히 쓰이는 근사값을 그대로 가져온 것으로, KBO 리그로 재보정한 값은 아니다.
POSITION_ADJ_PER_600PA = {
    "포수": 12.5, "유격수": 7.5, "2루수": 3.0, "3루수": 2.5,
    "중견수": 2.5, "좌익수": -7.5, "우익수": -7.5, "1루수": -12.5,
    "지명타자": -17.5,
}

# 도루 득점가치(run) — 세이버메트릭스에서 흔히 쓰이는 근사 상수(Tom Tango 등).
# KBO 리그로 재보정한 값은 아니다.
SB_RUN_VALUE = 0.2
CS_RUN_VALUE = -0.4


def _to_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def parse_innings(ip_str):
    """'991 1/3' 같은 KBO 이닝 표기를 소수(991.333...)로 변환."""
    if not ip_str:
        return 0.0
    s = str(ip_str).strip()
    if " " in s:
        whole, frac = s.split(" ", 1)
        whole = _to_float(whole)
        num, _, den = frac.partition("/")
        frac_val = _to_float(num) / _to_float(den, 1) if den else 0.0
        return whole + frac_val
    return _to_float(s)


def format_innings(value):
    """계산된 소수 이닝(1/3이닝=0.3333...)을 KBO 표기('1.1'=1이닝+1아웃)로 변환한다.
    그냥 반올림하면(1.3333→'1.3') 야구에 없는 '.3이닝'처럼 보여 혼동을 준다."""
    if value is None or value != value:
        return ""
    whole = int(value)
    outs = int(round((value - whole) * 3))
    if outs >= 3:
        whole += 1
        outs -= 3
    return f"{whole}.{outs}"


def _woba_components(ab, bb, ibb, hbp, sf, h, b2, b3, hr):
    singles = h - b2 - b3 - hr
    den = ab + bb - ibb + sf + hbp
    if den <= 0:
        return None
    num = (0.69 * (bb - ibb) + 0.72 * hbp + 0.89 * singles +
           1.27 * b2 + 1.62 * b3 + 2.10 * hr)
    return num / den


def batting_advanced(stats: dict, league_ctx: dict = None, position: str = None, run_stats: dict = None):
    """long-format으로 모은 stat_name->value(str) 딕셔너리에서 타자 고급지표를 계산.
    league_ctx가 주어지면 wRC+, WAR(근사)도 함께 계산한다. position(수비 주 포지션)을
    함께 주면 WAR에 포지션 조정치를 더한다 — 예: 포수는 보너스, 1루수는 페널티.
    run_stats(도루/도루실패가 담긴 딕셔너리)를 함께 주면 도루 득점가치를 WAR에 더하고,
    HR·SB로 파워-스피드 넘버도 계산한다.
    """
    ab = _to_float(stats.get("AB"))
    pa = _to_float(stats.get("PA"))
    h = _to_float(stats.get("H"))
    b2 = _to_float(stats.get("2B"))
    b3 = _to_float(stats.get("3B"))
    hr = _to_float(stats.get("HR"))
    bb = _to_float(stats.get("BB"))
    ibb = _to_float(stats.get("IBB"))
    hbp = _to_float(stats.get("HBP"))
    sf = _to_float(stats.get("SF"))
    so = _to_float(stats.get("SO"))
    avg = _to_float(stats.get("AVG"))
    slg = _to_float(stats.get("SLG"))

    sb = _to_float((run_stats or {}).get("SB"))
    cs = _to_float((run_stats or {}).get("CS"))

    out = {}
    if slg and avg:
        out["ISO"] = round(slg - avg, 3)
    if pa:
        out["BB%"] = round(bb / pa * 100, 1)
        out["K%"] = round(so / pa * 100, 1)

    if run_stats and (hr > 0 or sb > 0):
        out["Power-Speed"] = round((2 * hr * sb) / (hr + sb), 2) if (hr + sb) > 0 else 0.0

    woba = _woba_components(ab, bb, ibb, hbp, sf, h, b2, b3, hr)
    if woba is not None:
        out["wOBA"] = round(woba, 3)

    if league_ctx and woba is not None and pa > 0:
        lg_woba = league_ctx.get("lg_woba")
        lg_r_pa = league_ctx.get("lg_r_per_pa")
        scale = league_ctx.get("woba_scale", WOBA_SCALE)
        if lg_woba is not None and lg_r_pa:
            wraa = ((woba - lg_woba) / scale) * pa
            wrc_per_pa = (woba - lg_woba) / scale + lg_r_pa
            out["wRC+"] = round(100 * wrc_per_pa / lg_r_pa)

            sb_runs = sb * SB_RUN_VALUE + cs * CS_RUN_VALUE
            if run_stats:
                out["SB런"] = round(sb_runs, 2)

            replacement_runs = REPLACEMENT_RUNS_PER_600PA * (pa / 600)
            pos_adj = POSITION_ADJ_PER_600PA.get(position, 0.0) * (pa / 600)
            runs_per_win = league_ctx.get("runs_per_win", RUNS_PER_WIN)
            out["WAR"] = round((wraa + sb_runs + pos_adj + replacement_runs) / runs_per_win, 2)
    return out


def pitching_advanced(stats: dict, league_fip_const: float, league_ctx: dict = None):
    ip = parse_innings(stats.get("IP"))
    hr = _to_float(stats.get("HR"))
    bb = _to_float(stats.get("BB"))
    hbp = _to_float(stats.get("HBP"))
    so = _to_float(stats.get("SO"))
    h = _to_float(stats.get("H"))
    tbf = _to_float(stats.get("TBF"))

    out = {}
    fip = None
    if ip > 0:
        fip = ((13 * hr) + (3 * (bb + hbp)) - (2 * so)) / ip + league_fip_const
        out["FIP"] = round(fip, 2)
        out["WHIP"] = round((bb + h) / ip, 2)
    if tbf:
        out["K%"] = round(so / tbf * 100, 1)
        out["BB%"] = round(bb / tbf * 100, 1)

    if league_ctx and fip is not None and ip > 0:
        lg_fip = league_ctx.get("lg_fip")
        runs_per_win = league_ctx.get("runs_per_win", RUNS_PER_WIN)
        if lg_fip is not None:
            replacement_fip = lg_fip + REPLACEMENT_FIP_OFFSET
            out["WAR"] = round(((replacement_fip - fip) * (ip / 9)) / runs_per_win, 2)
    return out


def _league_pitching_totals(pitching_rows):
    """(team, stat, value) 또는 (player, stat, value) 어느 쪽이든 상관없이 합산한다 —
    팀 10개를 합쳐도, 선수 수백 명을 합쳐도 결국 리그 전체 총합은 같아야 하기 때문이다.
    ERA는 절대 개별 로우끼리 단순평균하지 않는다 — 몇 이닝만 던진 선수의 극단적 ERA에
    끌려 리그 평균이 크게 부풀려지는 걸 실제로 겪었다(자책점·이닝을 각각 더한 뒤
    ER*9/IP로 계산해야 표본 크기가 자연히 가중치가 된다).
    """
    totals = {"ER": 0.0, "HR": 0.0, "BB": 0.0, "HBP": 0.0, "SO": 0.0, "IP": 0.0}
    for _, name, value in pitching_rows:
        if name == "IP":
            totals["IP"] += parse_innings(value)
        elif name in totals:
            totals[name] += _to_float(value)
    return totals


def league_fip_constant(pitching_rows):
    """합산 기록으로 그 시즌의 FIP 상수를 역산한다.
    FIP상수 = 리그ERA - ((13*HR + 3*(BB+HBP) - 2*SO) / IP)
    pitching_rows: [(team_또는_player, stat_name, stat_value), ...] (category='pitching')
    """
    totals = _league_pitching_totals(pitching_rows)
    if totals["IP"] <= 0:
        return LEAGUE_AVG_ERA_FALLBACK
    league_era = totals["ER"] * 9 / totals["IP"]
    raw = ((13 * totals["HR"]) + (3 * (totals["BB"] + totals["HBP"])) - (2 * totals["SO"])) / totals["IP"]
    return round(league_era - raw, 2)


def league_context(batting_rows, pitching_rows):
    """wRC+ · WAR(근사) 계산에 쓰는 리그 전체 합산 평균값.
    batting_rows / pitching_rows: [(team_또는_player, stat_name, stat_value), ...]
    (팀 합산 기록이든 선수 개인기록 전체 합산이든 상관없이 쓸 수 있다 — 리그 전체 총합은 같다.)
    """
    bt = {"AB": 0.0, "PA": 0.0, "H": 0.0, "2B": 0.0, "3B": 0.0, "HR": 0.0,
          "BB": 0.0, "IBB": 0.0, "HBP": 0.0, "SF": 0.0, "R": 0.0}
    for _, name, value in batting_rows:
        if name in bt:
            bt[name] += _to_float(value)

    lg_woba = _woba_components(bt["AB"], bt["BB"], bt["IBB"], bt["HBP"], bt["SF"],
                                bt["H"], bt["2B"], bt["3B"], bt["HR"])
    lg_r_per_pa = (bt["R"] / bt["PA"]) if bt["PA"] else None

    p_totals = _league_pitching_totals(pitching_rows)
    lg_era = (p_totals["ER"] * 9 / p_totals["IP"]) if p_totals["IP"] > 0 else LEAGUE_AVG_ERA_FALLBACK
    fip_const = league_fip_constant(pitching_rows)

    return {
        "lg_woba": lg_woba,
        "lg_r_per_pa": lg_r_per_pa,
        "lg_fip": lg_era,  # FIP 상수를 lgERA에 맞춰 역산했으므로 리그 평균 FIP == 리그 평균 ERA
        "fip_const": fip_const,
        "woba_scale": WOBA_SCALE,
        "runs_per_win": RUNS_PER_WIN,
    }
