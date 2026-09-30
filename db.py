import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "kbo.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS standings (
    fetch_date TEXT NOT NULL,
    rank INTEGER,
    team TEXT NOT NULL,
    games INTEGER,
    wins INTEGER,
    losses INTEGER,
    draws INTEGER,
    win_pct REAL,
    games_behind TEXT,
    last10 TEXT,
    streak TEXT,
    home_record TEXT,
    away_record TEXT,
    PRIMARY KEY (fetch_date, team)
);

CREATE TABLE IF NOT EXISTS team_stat (
    fetch_date TEXT NOT NULL,
    category TEXT NOT NULL,   -- 'batting' or 'pitching'
    team TEXT NOT NULL,
    stat_name TEXT NOT NULL,
    stat_value TEXT,
    PRIMARY KEY (fetch_date, category, team, stat_name)
);

CREATE TABLE IF NOT EXISTS games (
    game_date TEXT NOT NULL,
    away_team TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_score INTEGER,
    home_score INTEGER,
    status TEXT,
    status_info TEXT,
    stadium TEXT,
    game_time TEXT,
    away_starter TEXT,
    home_starter TEXT,
    win_pitcher TEXT,
    lose_pitcher TEXT,
    broadcast TEXT,
    PRIMARY KEY (game_date, away_team, home_team)
);

CREATE TABLE IF NOT EXISTS fetch_log (
    run_at TEXT NOT NULL,
    step TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS player_stat (
    fetch_date TEXT NOT NULL,
    category TEXT NOT NULL,   -- 'batting' | 'pitching' | 'batting_adv' | 'pitching_adv'
    team TEXT NOT NULL,
    player TEXT NOT NULL,
    stat_name TEXT NOT NULL,
    stat_value TEXT,
    PRIMARY KEY (fetch_date, category, team, player, stat_name)
);

CREATE TABLE IF NOT EXISTS historical_standings (
    year TEXT NOT NULL,
    rank INTEGER,
    team TEXT NOT NULL,
    games INTEGER,
    wins INTEGER,
    losses INTEGER,
    draws INTEGER,
    win_pct REAL,
    games_behind TEXT,
    last10 TEXT,
    streak TEXT,
    home_record TEXT,
    away_record TEXT,
    PRIMARY KEY (year, team)
);

CREATE TABLE IF NOT EXISTS news (
    bd_se TEXT PRIMARY KEY,
    title TEXT,
    summary TEXT,
    date TEXT,
    url TEXT,
    collected_at TEXT
);

CREATE TABLE IF NOT EXISTS player_game_stat (
    game_id TEXT NOT NULL,
    game_date TEXT NOT NULL,
    category TEXT NOT NULL,   -- 'batting' | 'pitching'
    team TEXT NOT NULL,
    player TEXT NOT NULL,
    stat_name TEXT NOT NULL,
    stat_value TEXT,
    PRIMARY KEY (game_id, category, team, player, stat_name)
);

CREATE TABLE IF NOT EXISTS historical_team_stat (
    year TEXT NOT NULL,
    category TEXT NOT NULL,   -- 'batting' | 'pitching'
    team TEXT NOT NULL,
    stat_name TEXT NOT NULL,
    stat_value TEXT,
    PRIMARY KEY (year, category, team, stat_name)
);

CREATE TABLE IF NOT EXISTS historical_player_stat (
    year TEXT NOT NULL,
    category TEXT NOT NULL,   -- 'batting' | 'pitching'
    team TEXT NOT NULL,
    player TEXT NOT NULL,
    stat_name TEXT NOT NULL,
    stat_value TEXT,
    PRIMARY KEY (year, category, team, player, stat_name)
);

CREATE TABLE IF NOT EXISTS player_award (
    player_id TEXT NOT NULL,
    year TEXT NOT NULL,
    award TEXT NOT NULL,     -- 'KBO MVP', 'KBO 신인상', 'KBO 골든글러브 3루수 부문', 'KBO 수비상 포수 부문' 등
    PRIMARY KEY (player_id, year, award)
);

CREATE TABLE IF NOT EXISTS player_season_reg (
    player_id TEXT NOT NULL,
    team TEXT NOT NULL,
    year TEXT NOT NULL,
    days INTEGER,             -- KBO 리그 엔트리(1군) 등록일수
    note TEXT,                -- 국가대표 차출 등 비고(예: 'APBC 10(준우승)')
    PRIMARY KEY (player_id, team, year)
);
"""


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


MIGRATION_COLUMNS = {
    "games": {
        "status_info": "TEXT",
        "away_starter": "TEXT",
        "home_starter": "TEXT",
        "win_pitcher": "TEXT",
        "lose_pitcher": "TEXT",
        "broadcast": "TEXT",
        "game_id": "TEXT",
    },
    "player_stat": {
        "player_id": "TEXT",  # KBO 내부 선수 고유번호 — 동명이인 구분용(없으면 빈 문자열)
    },
    "historical_player_stat": {
        "player_id": "TEXT",
    },
}


def _migrate(conn):
    """CREATE TABLE IF NOT EXISTS는 기존 테이블에 새 컬럼을 추가해주지 않으므로 직접 보강한다."""
    for table, columns in MIGRATION_COLUMNS.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for col, coltype in columns.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"DB initialized at {DB_PATH}")
