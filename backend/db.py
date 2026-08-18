"""Camada de acesso ao SQLite (schema + helpers)."""
import json
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("AOE4_DB", ROOT / "data" / "aoe4.db"))
CONFIG_PATH = Path(os.environ.get("AOE4_CONFIG", ROOT / "players.json"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    profile_id     INTEGER PRIMARY KEY,
    name           TEXT,
    alias          TEXT,
    steam_id       TEXT,
    country        TEXT,
    tracked        INTEGER NOT NULL DEFAULT 1,
    last_synced_at TEXT
);

CREATE TABLE IF NOT EXISTS games (
    game_id      INTEGER PRIMARY KEY,
    started_at   TEXT,
    updated_at   TEXT,
    duration     INTEGER,
    map          TEXT,
    kind         TEXT,
    leaderboard  TEXT,
    season       INTEGER,
    patch        INTEGER,
    server       TEXT,
    average_mmr  INTEGER,
    ongoing      INTEGER NOT NULL DEFAULT 0,
    source       TEXT NOT NULL DEFAULT 'api'
);

CREATE TABLE IF NOT EXISTS game_players (
    game_id       INTEGER NOT NULL,
    profile_id    INTEGER NOT NULL,
    name          TEXT,
    team          INTEGER NOT NULL,
    result        TEXT,
    civilization  TEXT,
    rating        INTEGER,
    rating_diff   INTEGER,
    mmr           INTEGER,
    mmr_diff      INTEGER,
    input_type    TEXT,
    PRIMARY KEY (game_id, profile_id),
    FOREIGN KEY (game_id) REFERENCES games(game_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_games_started  ON games(started_at);
CREATE INDEX IF NOT EXISTS idx_games_kind     ON games(kind);
CREATE INDEX IF NOT EXISTS idx_gp_profile     ON game_players(profile_id);
CREATE INDEX IF NOT EXISTS idx_gp_game_team   ON game_players(game_id, team);

-- Resumo detalhado por partida (endpoint /players/{id}/games/{gid}/summary).
-- status: ok = baixado, missing = a API nao tem resumo, error = falha temporaria.
CREATE TABLE IF NOT EXISTS game_summaries (
    game_id     INTEGER PRIMARY KEY,
    fetched_at  TEXT NOT NULL,
    status      TEXT NOT NULL,
    version     INTEGER,
    win_reason  TEXT,
    error       TEXT,
    FOREIGN KEY (game_id) REFERENCES games(game_id) ON DELETE CASCADE
);

-- Uma linha por jogador por partida com os numeros do resumo.
CREATE TABLE IF NOT EXISTS player_summaries (
    game_id            INTEGER NOT NULL,
    profile_id         INTEGER NOT NULL,
    team               INTEGER,
    civilization       TEXT,
    result             TEXT,
    apm                INTEGER,
    score_total        INTEGER,
    score_military     INTEGER,
    score_economy      INTEGER,
    score_technology   INTEGER,
    score_society      INTEGER,
    spent_total        INTEGER,
    spent_food         INTEGER,
    spent_wood         INTEGER,
    spent_gold         INTEGER,
    spent_stone        INTEGER,
    spent_oliveoil     INTEGER,
    gathered_total     INTEGER,
    gathered_food      INTEGER,
    gathered_wood      INTEGER,
    gathered_gold      INTEGER,
    gathered_stone     INTEGER,
    gathered_oliveoil  INTEGER,
    kills              INTEGER,
    deaths             INTEGER,
    razed              INTEGER,
    buildings_made     INTEGER,
    buildings_lost     INTEGER,
    units_made         INTEGER,
    upgrades           INTEGER,
    squad_kills        INTEGER,
    squad_lost         INTEGER,
    PRIMARY KEY (game_id, profile_id),
    FOREIGN KEY (game_id) REFERENCES games(game_id) ON DELETE CASCADE
);

-- Producao e perdas por tipo de unidade (derivado do build order do resumo).
-- category: eco | militar | cerco | religioso | explorador | outro
CREATE TABLE IF NOT EXISTS unit_stats (
    game_id     INTEGER NOT NULL,
    profile_id  INTEGER NOT NULL,
    unit_key    TEXT NOT NULL,
    category    TEXT NOT NULL,
    made        INTEGER NOT NULL DEFAULT 0,
    lost        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (game_id, profile_id, unit_key),
    FOREIGN KEY (game_id) REFERENCES games(game_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ps_profile   ON player_summaries(profile_id);
CREATE INDEX IF NOT EXISTS idx_us_game      ON unit_stats(game_id);
CREATE INDEX IF NOT EXISTS idx_us_cat       ON unit_stats(category);

CREATE TABLE IF NOT EXISTS sync_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at      TEXT NOT NULL,
    profile_id  INTEGER,
    mode        TEXT,
    fetched     INTEGER,
    inserted    INTEGER,
    error       TEXT
);
"""


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def tracked_ids(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute("SELECT profile_id FROM players WHERE tracked = 1").fetchall()
    return [r["profile_id"] for r in rows]
