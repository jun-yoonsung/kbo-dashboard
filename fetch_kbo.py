"""KBO 기록을 가져와 kbo.db(SQLite)에 저장하는 일일 배치 스크립트.

사용법:
  python fetch_kbo.py                   오늘자 순위/팀·선수 타격·투구 기록/경기 결과/뉴스 수집 + 고급지표 계산
  python fetch_kbo.py --backfill 14     최근 14일치 경기 결과를 추가로 채워 넣음(최초 1회 권장)
  python fetch_kbo.py --history 2016 2026   지정 범위 연도의 과거 시즌 최종 순위를 채워 넣음(최초 1회 권장)
  python fetch_kbo.py --skip-daily --season-start 2026-03-01   올해 개막일부터 오늘까지 경기 전체를 백필(최초 1회 권장)
  python fetch_kbo.py --skip-daily --awards-backfill   역대 전체 선수의 수상·등록일수를 백필(최초 1회 권장, 시간 소요)
"""

import argparse
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

import db
import kbo_scraper
import sabermetrics

KST = ZoneInfo("Asia/Seoul")


def today_kst():
    return datetime.now(KST).date()


def log(conn, step, status, detail=""):
    conn.execute(
        "INSERT INTO fetch_log (run_at, step, status, detail) VALUES (?, ?, ?, ?)",
        (datetime.now(KST).isoformat(), step, status, detail),
    )


def save_standings(conn, fetch_date):
    rows = kbo_scraper.fetch_standings()
    for r in rows:
        conn.execute(
            """INSERT OR REPLACE INTO standings
               (fetch_date, rank, team, games, wins, losses, draws, win_pct,
                games_behind, last10, streak, home_record, away_record)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fetch_date, r["rank"], r["team"], r["games"], r["wins"], r["losses"],
                r["draws"], r["win_pct"], r["games_behind"], r["last10"], r["streak"],
                r["home_record"], r["away_record"],
            ),
        )
    return len(rows)


def save_team_stat(conn, fetch_date, category, rows):
    for team, stat_name, stat_value in rows:
        conn.execute(
            """INSERT OR REPLACE INTO team_stat
               (fetch_date, category, team, stat_name, stat_value)
               VALUES (?, ?, ?, ?, ?)""",
            (fetch_date, category, team, stat_name, stat_value),
        )
    return len(rows)


def save_player_stat(conn, fetch_date, category, rows):
    for row in rows:
        player, team, stat_name, stat_value, player_id = row if len(row) == 5 else (*row, None)
        conn.execute(
            """INSERT OR REPLACE INTO player_stat
               (fetch_date, category, team, player, stat_name, stat_value, player_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (fetch_date, category, team, player, stat_name, stat_value, player_id),
        )
    return len(rows)


def save_games(conn, game_date_str):
    rows = kbo_scraper.fetch_games(game_date_str)
    for g in rows:
        conn.execute(
            """INSERT OR REPLACE INTO games
               (game_date, away_team, home_team, away_score, home_score, status, status_info,
                stadium, game_time, away_starter, home_starter, win_pitcher, lose_pitcher, broadcast, game_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                game_date_str, g["away_team"], g["home_team"], g["away_score"],
                g["home_score"], g["status"], g.get("status_info"), g["stadium"], g["game_time"],
                g.get("away_starter"), g.get("home_starter"), g.get("win_pitcher"),
                g.get("lose_pitcher"), g.get("broadcast"), g.get("game_id"),
            ),
        )
    return len(rows)


def save_player_game_stat(conn, game_id, game_date, category, rows):
    for player, team, stat_name, stat_value in rows:
        conn.execute(
            """INSERT OR REPLACE INTO player_game_stat
               (game_id, game_date, category, team, player, stat_name, stat_value)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (game_id, game_date, category, team, player, stat_name, stat_value),
        )
    return len(rows)


def fetch_missing_boxscores(conn, limit=None):
    """player_game_stat에 아직 기록이 없는 'RESULT' 상태 경기의 선수별 박스스코어를 채운다.
    '최근 폼' 분석(analysis.recent_form_by_game)이 이 테이블을 사용한다 — 매일 실행할 때마다
    아직 못 채운 과거 경기까지 자동으로 채워지므로 별도 백필 스크립트가 필요 없다.
    """
    rows = conn.execute(
        """SELECT game_id, game_date FROM games
           WHERE status='RESULT' AND game_id IS NOT NULL
           AND game_id NOT IN (SELECT DISTINCT game_id FROM player_game_stat)"""
    ).fetchall()
    if limit:
        rows = rows[:limit]
    n = 0
    for game_id, game_date in rows:
        try:
            batting, pitching = kbo_scraper.fetch_game_boxscore(game_id)
            n += save_player_game_stat(conn, game_id, game_date, "batting", batting)
            n += save_player_game_stat(conn, game_id, game_date, "pitching", pitching)
            conn.commit()
        except Exception as e:
            log(conn, "player_game_stat", "error", f"{game_id}: {e}")
    return n


def save_news(conn, rows):
    now = datetime.now(KST).isoformat()
    n = 0
    for item in rows:
        cur = conn.execute("SELECT 1 FROM news WHERE bd_se = ?", (item["bd_se"],))
        if cur.fetchone():
            continue
        conn.execute(
            """INSERT INTO news (bd_se, title, summary, date, url, collected_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (item["bd_se"], item["title"], item["summary"], item["date"], item["url"], now),
        )
        n += 1
    return n


def save_historical_standings(conn, year, rows):
    for r in rows:
        conn.execute(
            """INSERT OR REPLACE INTO historical_standings
               (year, rank, team, games, wins, losses, draws, win_pct,
                games_behind, last10, streak, home_record, away_record)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(year), r["rank"], r["team"], r["games"], r["wins"], r["losses"],
                r["draws"], r["win_pct"], r["games_behind"], r["last10"], r["streak"],
                r["home_record"], r["away_record"],
            ),
        )
    return len(rows)


def compute_advanced_stats(conn, fetch_date):
    """이날 수집한 기본기록으로 팀/선수 고급지표(FIP, wOBA 등)를 계산해 저장한다."""
    team_pitching = conn.execute(
        "SELECT team, stat_name, stat_value FROM team_stat WHERE fetch_date=? AND category='pitching'",
        (fetch_date,),
    ).fetchall()
    team_batting_all = conn.execute(
        "SELECT team, stat_name, stat_value FROM team_stat WHERE fetch_date=? AND category='batting'",
        (fetch_date,),
    ).fetchall()
    fip_const = sabermetrics.league_fip_constant(team_pitching)
    league_ctx = sabermetrics.league_context(team_batting_all, team_pitching)

    def stats_by_entity(rows):
        grouped = {}
        for team, name, value in rows:
            grouped.setdefault(team, {})[name] = value
        return grouped

    n = 0
    for team, stats in stats_by_entity(team_batting_all).items():
        for name, value in sabermetrics.batting_advanced(stats, league_ctx).items():
            conn.execute(
                """INSERT OR REPLACE INTO team_stat (fetch_date, category, team, stat_name, stat_value)
                   VALUES (?, 'batting_adv', ?, ?, ?)""",
                (fetch_date, team, name, value),
            )
            n += 1
    for team, stats in stats_by_entity(team_pitching).items():
        for name, value in sabermetrics.pitching_advanced(stats, fip_const, league_ctx).items():
            conn.execute(
                """INSERT OR REPLACE INTO team_stat (fetch_date, category, team, stat_name, stat_value)
                   VALUES (?, 'pitching_adv', ?, ?, ?)""",
                (fetch_date, team, name, value),
            )
            n += 1

    def player_stats_by_entity(category):
        rows = conn.execute(
            "SELECT player, team, stat_name, stat_value FROM player_stat WHERE fetch_date=? AND category=?",
            (fetch_date, category),
        ).fetchall()
        grouped = {}
        for player, team, name, value in rows:
            grouped.setdefault((player, team), {})[name] = value
        return grouped

    positions = {
        (player, team): stats.get("POS")
        for (player, team), stats in player_stats_by_entity("defense").items()
    }
    baserunning = player_stats_by_entity("baserunning")

    for (player, team), stats in player_stats_by_entity("batting").items():
        position = positions.get((player, team))
        run_stats = baserunning.get((player, team))
        for name, value in sabermetrics.batting_advanced(stats, league_ctx, position=position, run_stats=run_stats).items():
            conn.execute(
                """INSERT OR REPLACE INTO player_stat
                   (fetch_date, category, team, player, stat_name, stat_value)
                   VALUES (?, 'batting_adv', ?, ?, ?, ?)""",
                (fetch_date, team, player, name, value),
            )
            n += 1
    for (player, team), stats in player_stats_by_entity("pitching").items():
        for name, value in sabermetrics.pitching_advanced(stats, fip_const, league_ctx).items():
            conn.execute(
                """INSERT OR REPLACE INTO player_stat
                   (fetch_date, category, team, player, stat_name, stat_value)
                   VALUES (?, 'pitching_adv', ?, ?, ?, ?)""",
                (fetch_date, team, player, name, value),
            )
            n += 1
    return n


def compute_historical_advanced_stats(conn, year):
    """지정 연도의 KBO 기본기록으로 세이버메트릭스(wOBA·wRC+·FIP·WAR 등)를 계산해
    historical_team_stat/historical_player_stat에 'batting_adv'/'pitching_adv'로 저장한다.

    팀 집계 기록(historical_team_stat)이 있는 해(2001~)는 그걸로 리그 평균을 낸다. 그 이전
    (1982~2000)은 팀 집계 자체가 없으므로 그 해 선수 전체 개인기록을 합산해 리그 평균을
    대신 낸다 — sabermetrics.league_context()는 팀/선수 어느 쪽 합계를 넣어도 되게 만들었다
    (리그 전체 총합은 같아야 하므로).

    타자 WAR는 포지션·도루 조정치가 빠진다 — 수비 포지션/주루 기록은 지금 시즌만 수집해서
    옛 시즌엔 없기 때문이다(타격+대체선수 수준만 반영된 근사치).
    """
    ys = str(year)
    team_pitching = conn.execute(
        "SELECT team, stat_name, stat_value FROM historical_team_stat WHERE year=? AND category='pitching'", (ys,)
    ).fetchall()
    team_batting = conn.execute(
        "SELECT team, stat_name, stat_value FROM historical_team_stat WHERE year=? AND category='batting'", (ys,)
    ).fetchall()

    if team_batting and team_pitching:
        batting_ctx_rows, pitching_ctx_rows = team_batting, team_pitching
    else:
        batting_ctx_rows = conn.execute(
            "SELECT player, stat_name, stat_value FROM historical_player_stat WHERE year=? AND category='batting'", (ys,)
        ).fetchall()
        pitching_ctx_rows = conn.execute(
            "SELECT player, stat_name, stat_value FROM historical_player_stat WHERE year=? AND category='pitching'", (ys,)
        ).fetchall()

    fip_const = sabermetrics.league_fip_constant(pitching_ctx_rows)
    league_ctx = sabermetrics.league_context(batting_ctx_rows, pitching_ctx_rows)

    n = 0
    if team_batting and team_pitching:
        def team_stats_by_entity(rows):
            grouped = {}
            for team, name, value in rows:
                grouped.setdefault(team, {})[name] = value
            return grouped

        for team, stats in team_stats_by_entity(team_batting).items():
            for name, value in sabermetrics.batting_advanced(stats, league_ctx).items():
                conn.execute(
                    """INSERT OR REPLACE INTO historical_team_stat (year, category, team, stat_name, stat_value)
                       VALUES (?, 'batting_adv', ?, ?, ?)""",
                    (ys, team, name, value),
                )
                n += 1
        for team, stats in team_stats_by_entity(team_pitching).items():
            for name, value in sabermetrics.pitching_advanced(stats, fip_const, league_ctx).items():
                conn.execute(
                    """INSERT OR REPLACE INTO historical_team_stat (year, category, team, stat_name, stat_value)
                       VALUES (?, 'pitching_adv', ?, ?, ?)""",
                    (ys, team, name, value),
                )
                n += 1

    def player_stats_by_entity(category):
        rows = conn.execute(
            "SELECT player, team, stat_name, stat_value FROM historical_player_stat WHERE year=? AND category=?",
            (ys, category),
        ).fetchall()
        grouped = {}
        for player, team, name, value in rows:
            grouped.setdefault((player, team), {})[name] = value
        return grouped

    for (player, team), stats in player_stats_by_entity("batting").items():
        for name, value in sabermetrics.batting_advanced(stats, league_ctx).items():
            conn.execute(
                """INSERT OR REPLACE INTO historical_player_stat
                   (year, category, team, player, stat_name, stat_value) VALUES (?, 'batting_adv', ?, ?, ?, ?)""",
                (ys, team, player, name, value),
            )
            n += 1
    for (player, team), stats in player_stats_by_entity("pitching").items():
        for name, value in sabermetrics.pitching_advanced(stats, fip_const, league_ctx).items():
            conn.execute(
                """INSERT OR REPLACE INTO historical_player_stat
                   (year, category, team, player, stat_name, stat_value) VALUES (?, 'pitching_adv', ?, ?, ?, ?)""",
                (ys, team, player, name, value),
            )
            n += 1
    return n


def _current_season_player_ids(conn, fetch_date):
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT player_id FROM player_stat WHERE fetch_date = ? AND player_id IS NOT NULL AND player_id != ''",
        (fetch_date,),
    ).fetchall()]


def run_daily(conn, target_date):
    fetch_date = target_date.isoformat()

    steps = [
        ("standings", lambda: save_standings(conn, fetch_date)),
        ("team_batting", lambda: save_team_stat(conn, fetch_date, "batting", kbo_scraper.fetch_team_batting())),
        ("team_pitching", lambda: save_team_stat(conn, fetch_date, "pitching", kbo_scraper.fetch_team_pitching())),
        ("player_batting", lambda: save_player_stat(conn, fetch_date, "batting", kbo_scraper.fetch_player_batting())),
        ("player_pitching", lambda: save_player_stat(conn, fetch_date, "pitching", kbo_scraper.fetch_player_pitching())),
        ("player_defense", lambda: save_player_stat(conn, fetch_date, "defense", kbo_scraper.fetch_player_defense())),
        ("player_baserunning", lambda: save_player_stat(conn, fetch_date, "baserunning", kbo_scraper.fetch_player_baserunning())),
        ("games", lambda: save_games(conn, target_date.strftime("%Y%m%d"))),
        ("player_game_stat", lambda: fetch_missing_boxscores(conn)),
        ("news", lambda: save_news(conn, kbo_scraper.fetch_news(max_pages=3))),
        ("advanced_stats", lambda: compute_advanced_stats(conn, fetch_date)),
        # 역대 기록(1982~)에도 올해분이 매일 최신 상태로 반영되도록 오늘 연도만 다시 갱신한다.
        ("historical_current_year", lambda: backfill_historical_year(conn, target_date.year)),
        # 이번 시즌 현역 선수는 등록일수가 매일 늘어나므로 skip_done=False로 매번 다시 조회한다
        # (수상은 선수당 몇 건 안 돼 매일 다시 조회해도 부담이 적다). 과거 은퇴 선수는
        # --awards-backfill로 최초 1회만 채우면 되므로 여기서는 건드리지 않는다.
        ("current_player_awards_reg", lambda: backfill_player_awards_and_reg(
            conn, player_ids=_current_season_player_ids(conn, fetch_date), skip_done=False)),
    ]
    for step, fn in steps:
        try:
            n = fn()
            log(conn, step, "ok", f"{n} rows")
            print(f"[ok] {step}: {n} rows")
        except Exception as e:
            log(conn, step, "error", str(e))
            print(f"[error] {step}: {e}", file=sys.stderr)
        conn.commit()


def run_backfill(conn, days):
    target = today_kst()
    for i in range(days):
        d = target - timedelta(days=i)
        try:
            n = save_games(conn, d.strftime("%Y%m%d"))
            log(conn, "games_backfill", "ok", f"{d}: {n} rows")
            print(f"[ok] backfill {d}: {n} rows")
        except Exception as e:
            log(conn, "games_backfill", "error", f"{d}: {e}")
            print(f"[error] backfill {d}: {e}", file=sys.stderr)
        conn.commit()


def run_season_backfill(conn, start_date_str):
    """지정한 시즌 개막일부터 오늘까지 하루씩 경기 결과를 채운다 (시즌 전체 경기 로그 확보용)."""
    start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    end = today_kst()
    d = start
    total = 0
    while d <= end:
        try:
            n = save_games(conn, d.strftime("%Y%m%d"))
            total += n
            if n:
                print(f"[ok] season {d}: {n} rows")
        except Exception as e:
            log(conn, "games_season_backfill", "error", f"{d}: {e}")
            print(f"[error] season backfill {d}: {e}", file=sys.stderr)
        conn.commit()
        d += timedelta(days=1)
    log(conn, "games_season_backfill", "ok", f"{start}~{end}: {total} rows total")
    print(f"[ok] season backfill {start}~{end}: {total} rows total")
    conn.commit()


def run_history(conn, start_year, end_year):
    for year in range(start_year, end_year + 1):
        try:
            rows = kbo_scraper.fetch_historical_standings(year)
            n = save_historical_standings(conn, year, rows)
            log(conn, "history", "ok", f"{year}: {n} rows")
            print(f"[ok] history {year}: {n} rows")
        except Exception as e:
            log(conn, "history", "error", f"{year}: {e}")
            print(f"[error] history {year}: {e}", file=sys.stderr)
        conn.commit()


def save_historical_team_stat(conn, year, category, rows):
    for team, stat_name, stat_value in rows:
        conn.execute(
            """INSERT OR REPLACE INTO historical_team_stat
               (year, category, team, stat_name, stat_value) VALUES (?, ?, ?, ?, ?)""",
            (str(year), category, team, stat_name, stat_value),
        )
    return len(rows)


def save_historical_player_stat(conn, year, category, rows):
    for row in rows:
        player, team, stat_name, stat_value, player_id = row if len(row) == 5 else (*row, None)
        conn.execute(
            """INSERT OR REPLACE INTO historical_player_stat
               (year, category, team, player, stat_name, stat_value, player_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (str(year), category, team, player, stat_name, stat_value, player_id),
        )
    return len(rows)


def save_player_awards(conn, player_id, rows):
    conn.execute("DELETE FROM player_award WHERE player_id = ?", (player_id,))
    for year, award in rows:
        conn.execute(
            "INSERT OR REPLACE INTO player_award (player_id, year, award) VALUES (?, ?, ?)",
            (player_id, year, award),
        )
    return len(rows)


def save_player_season_reg(conn, player_id, rows):
    conn.execute("DELETE FROM player_season_reg WHERE player_id = ?", (player_id,))
    for team, year, days, note in rows:
        conn.execute(
            "INSERT OR REPLACE INTO player_season_reg (player_id, team, year, days, note) VALUES (?, ?, ?, ?, ?)",
            (player_id, team, year, days, note),
        )
    return len(rows)


def fetch_and_save_player_awards_and_reg(session, conn, player_id):
    """한 선수의 수상·등록일수를 조회해 저장하고 저장한 행 수를 반환한다."""
    n = save_player_awards(conn, player_id, kbo_scraper.fetch_player_awards(session, player_id))
    n += save_player_season_reg(conn, player_id, kbo_scraper.fetch_player_season_reg(session, player_id))
    return n


def backfill_player_awards_and_reg(conn, player_ids=None, skip_done=True):
    """historical_player_stat에 등장하는 모든 player_id의 수상·등록일수를 채운다.
    세션 쿠키 하나로 여러 선수를 조회할 수 있어(kbo_scraper.prime_player_detail_session 참고)
    선수마다 새 세션을 만들 필요 없이 순회한다. skip_done=True면 이미 등록일수가 있는 선수는
    건너뛴다(선수당 2회 요청 × 수천 명이라 중단 후 재실행을 지원하기 위함) — 단, 이번 시즌
    현역 선수를 매일 갱신할 때는 skip_done=False로 호출해 등록일수가 계속 늘어나게 한다.
    """
    if player_ids is None:
        player_ids = [r[0] for r in conn.execute(
            "SELECT DISTINCT player_id FROM historical_player_stat WHERE player_id IS NOT NULL AND player_id != ''"
        ).fetchall()]
    if skip_done:
        done = {r[0] for r in conn.execute("SELECT DISTINCT player_id FROM player_season_reg").fetchall()}
        player_ids = [p for p in player_ids if p not in done]
    if not player_ids:
        return 0

    session = requests.Session()
    kbo_scraper.prime_player_detail_session(session, player_ids[0])
    total = 0
    for i, pid in enumerate(player_ids):
        try:
            total += fetch_and_save_player_awards_and_reg(session, conn, pid)
        except Exception as e:
            log(conn, "player_awards_reg", "error", f"{pid}: {e}")
            print(f"[error] player_awards_reg {pid}: {e}", file=sys.stderr)
        if (i + 1) % 200 == 0:
            conn.commit()
            print(f"  ...player_awards_reg {i + 1}/{len(player_ids)}")
        time.sleep(0.1)
    conn.commit()
    log(conn, "player_awards_reg", "ok", f"{len(player_ids)} players")
    print(f"[ok] player_awards_reg: {len(player_ids)} players")
    return total


TEAM_STAT_MIN_YEAR = 2001  # KBO 팀 기록(집계) 페이지 자체가 2001년부터만 연도 선택을 제공한다.


def backfill_historical_year(conn, year, skip_standings=False, skip_team=False, skip_player=False):
    """한 연도치 역대 팀 순위·팀 기록·선수 개인기록을 (다시) 가져와 저장하고 총 저장 행 수를 반환한다.
    팀 집계 기록은 KBO 사이트 자체 한계로 2001년부터만 가능하다.
    run_full_history(여러 해 백필)와 run_daily(오늘 연도만 매일 갱신) 양쪽에서 함께 쓴다.
    """
    total = 0
    if not skip_standings:
        try:
            n = save_historical_standings(conn, year, kbo_scraper.fetch_historical_standings(year))
            total += n
            log(conn, "historical_standings", "ok", f"{year}: {n} rows")
            print(f"[ok] {year} historical_standings: {n} rows")
        except Exception as e:
            log(conn, "historical_standings", "error", f"{year}: {e}")
            print(f"[error] {year} historical_standings: {e}", file=sys.stderr)
        conn.commit()

    if year >= TEAM_STAT_MIN_YEAR and not skip_team:
        for category, fetch_fn in (("batting", kbo_scraper.fetch_team_batting),
                                    ("pitching", kbo_scraper.fetch_team_pitching)):
            try:
                n = save_historical_team_stat(conn, year, category, fetch_fn(year=year))
                total += n
                log(conn, "historical_team_stat", "ok", f"{year} {category}: {n} rows")
                print(f"[ok] {year} historical_team_{category}: {n} rows")
            except Exception as e:
                log(conn, "historical_team_stat", "error", f"{year} {category}: {e}")
                print(f"[error] {year} historical_team_{category}: {e}", file=sys.stderr)
            conn.commit()

    if not skip_player:
        for category, fetch_fn in (("batting", kbo_scraper.fetch_player_batting),
                                    ("pitching", kbo_scraper.fetch_player_pitching)):
            try:
                n = save_historical_player_stat(conn, year, category, fetch_fn(year=year))
                total += n
                log(conn, "historical_player_stat", "ok", f"{year} {category}: {n} rows")
                print(f"[ok] {year} historical_player_{category}: {n} rows")
            except Exception as e:
                log(conn, "historical_player_stat", "error", f"{year} {category}: {e}")
                print(f"[error] {year} historical_player_{category}: {e}", file=sys.stderr)
            conn.commit()

    try:
        n = compute_historical_advanced_stats(conn, year)
        total += n
        log(conn, "historical_advanced_stats", "ok", f"{year}: {n} rows")
        print(f"[ok] {year} historical_advanced_stats: {n} rows")
    except Exception as e:
        log(conn, "historical_advanced_stats", "error", f"{year}: {e}")
        print(f"[error] {year} historical_advanced_stats: {e}", file=sys.stderr)
    conn.commit()

    return total


def run_full_history(conn, start_year, end_year, skip_done=True):
    """프로야구 원년(1982)부터 지정 범위까지 팀 순위·팀 기록·선수 개인기록을 전부 백필한다.
    skip_done=True면 이미 채워진 연도는 건너뛴다(중단 후 재실행 시 이어서 하기 위함).
    """
    done_standings = {r[0] for r in conn.execute("SELECT DISTINCT year FROM historical_standings")}
    done_team = {r[0] for r in conn.execute("SELECT DISTINCT year FROM historical_team_stat")}
    done_player = {r[0] for r in conn.execute("SELECT DISTINCT year FROM historical_player_stat")}

    for year in range(start_year, end_year + 1):
        ys = str(year)
        backfill_historical_year(
            conn, year,
            skip_standings=skip_done and ys in done_standings,
            skip_team=skip_done and ys in done_team,
            skip_player=skip_done and ys in done_player,
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", type=int, default=0, help="최근 N일치 경기 결과를 추가로 수집")
    parser.add_argument("--history", nargs=2, type=int, metavar=("START", "END"),
                         help="과거 시즌 순위 백필: 시작연도 끝연도 (예: 2016 2026)")
    parser.add_argument("--full-history", nargs=2, type=int, metavar=("START", "END"),
                         help="프로야구 원년부터 팀 순위·팀 기록·선수 개인기록 전부 백필 (예: 1982 2025)")
    parser.add_argument("--season-start", metavar="YYYY-MM-DD",
                         help="시즌 개막일부터 오늘까지 경기 결과 전체를 백필 (예: 2026-03-01)")
    parser.add_argument("--skip-daily", action="store_true", help="오늘자 일일 수집을 건너뜀 (backfill/history만 실행)")
    parser.add_argument("--awards-backfill", action="store_true",
                         help="역대(1982~) 전체 선수의 수상·등록일수를 최초 1회 백필 (선수당 2회 요청, 수천 명 규모라 시간이 걸림)")
    args = parser.parse_args()

    db.init_db()
    conn = db.get_conn()

    if not args.skip_daily:
        run_daily(conn, today_kst())
    if args.backfill > 0:
        run_backfill(conn, args.backfill)
    if args.history:
        run_history(conn, args.history[0], args.history[1])
    if args.full_history:
        run_full_history(conn, args.full_history[0], args.full_history[1])
    if args.season_start:
        run_season_backfill(conn, args.season_start)
    if args.awards_backfill:
        backfill_player_awards_and_reg(conn)

    conn.close()


if __name__ == "__main__":
    main()
