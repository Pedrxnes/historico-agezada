"""Agregacoes de estatisticas sobre as partidas jogadas em grupo.

Regra central: uma partida so conta quando pelo menos `min_size` jogadores
monitorados estao NO MESMO TIME. Assim o winrate reflete o desempenho do grupo,
nao o de cada um jogando sozinho.
"""
from __future__ import annotations

import sqlite3

# Presets de modo de jogo -> fragmento SQL sobre a tabela games (alias g)
PRESETS: dict[str, str] = {
    "all": "1=1",
    "tg": "(g.kind LIKE '%2v2%' OR g.kind LIKE '%3v3%' OR g.kind LIKE '%4v4%')",
    "tg_ranked": "(g.kind LIKE 'rm_%' AND (g.kind LIKE '%2v2%' OR g.kind LIKE '%3v3%' OR g.kind LIKE '%4v4%'))",
    "tg_qm": "(g.kind LIKE 'qm_%' AND (g.kind LIKE '%2v2%' OR g.kind LIKE '%3v3%' OR g.kind LIKE '%4v4%'))",
    "ffa": "g.kind LIKE '%ffa%'",
    "custom": "(g.source = 'manual' OR g.kind LIKE 'custom%')",
}


class Filters:
    def __init__(
        self,
        preset: str = "tg",
        required: list[int] | None = None,
        min_size: int = 2,
        date_from: str | None = None,
        date_to: str | None = None,
        season: int | None = None,
        map_name: str | None = None,
    ):
        self.preset = preset if preset in PRESETS else "tg"
        self.required = sorted(set(required or []))
        self.min_size = max(1, min_size)
        self.date_from = date_from
        self.date_to = date_to
        self.season = season
        self.map_name = map_name


def _base_cte(conn: sqlite3.Connection, f: Filters) -> tuple[str, list]:
    """Monta a CTE `base`: uma linha por partida elegivel, com o resultado do grupo."""
    tracked = [r["profile_id"] for r in conn.execute("SELECT profile_id FROM players WHERE tracked = 1")]
    if not tracked:
        tracked = [-1]
    required = [pid for pid in f.required if pid in tracked]

    tracked_ph = ",".join("?" for _ in tracked)
    req_ph = ",".join("?" for _ in required) if required else "NULL"
    params: list = list(tracked)
    if required:
        params += required

    where = [PRESETS[f.preset], "g.ongoing = 0"]
    if f.date_from:
        where.append("g.started_at >= ?")
    if f.date_to:
        where.append("g.started_at <= ?")
    if f.season is not None:
        where.append("g.season = ?")
    if f.map_name:
        where.append("g.map = ?")

    sql = f"""
    WITH grp AS (
        SELECT gp.game_id,
               gp.team,
               COUNT(*) AS n_tracked,
               SUM(CASE WHEN gp.profile_id IN ({req_ph}) THEN 1 ELSE 0 END) AS n_required,
               MAX(CASE WHEN gp.result IN ('win','loss') THEN gp.result END) AS result
        FROM game_players gp
        WHERE gp.profile_id IN ({tracked_ph})
        GROUP BY gp.game_id, gp.team
    ),
    base AS (
        SELECT g.game_id, g.started_at, g.duration, g.map, g.kind, g.leaderboard,
               g.season, g.server, g.average_mmr, g.source,
               grp.team AS grp_team, grp.n_tracked, grp.result AS grp_result
        FROM grp
        JOIN games g ON g.game_id = grp.game_id
        WHERE grp.n_tracked >= ?
          AND grp.n_required = ?
          AND {' AND '.join(where)}
    )
    """
    # ordem dos parametros: req_ph, tracked_ph, min_size, len(required), depois os WHERE
    ordered: list = []
    ordered += required            # req_ph
    ordered += tracked             # tracked_ph
    ordered.append(f.min_size)
    ordered.append(len(required))
    if f.date_from:
        ordered.append(f.date_from)
    if f.date_to:
        ordered.append(f.date_to)
    if f.season is not None:
        ordered.append(f.season)
    if f.map_name:
        ordered.append(f.map_name)
    return sql, ordered


def _rate(wins: int, losses: int) -> float | None:
    total = wins + losses
    return round(100.0 * wins / total, 1) if total else None


def _wl_query(conn, f: Filters, select_expr: str, group_expr: str, extra_join: str = "", having_min: int = 1):
    cte, params = _base_cte(conn, f)
    sql = f"""{cte}
    SELECT {select_expr} AS label,
           SUM(CASE WHEN b.grp_result = 'win'  THEN 1 ELSE 0 END) AS wins,
           SUM(CASE WHEN b.grp_result = 'loss' THEN 1 ELSE 0 END) AS losses,
           COUNT(*) AS games
    FROM base b
    {extra_join}
    GROUP BY {group_expr}
    HAVING wins + losses >= ?
    ORDER BY games DESC, wins DESC
    """
    rows = conn.execute(sql, params + [having_min]).fetchall()
    out = []
    for r in rows:
        if r["label"] is None:
            continue
        out.append({
            "label": r["label"],
            "wins": r["wins"],
            "losses": r["losses"],
            "games": r["wins"] + r["losses"],
            "win_rate": _rate(r["wins"], r["losses"]),
        })
    return out


def summary(conn: sqlite3.Connection, f: Filters) -> dict:
    cte, params = _base_cte(conn, f)
    row = conn.execute(f"""{cte}
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN grp_result = 'win'  THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN grp_result = 'loss' THEN 1 ELSE 0 END) AS losses,
               SUM(CASE WHEN grp_result IS NULL  THEN 1 ELSE 0 END) AS sem_resultado,
               AVG(duration) AS avg_duration,
               MIN(started_at) AS first_game,
               MAX(started_at) AS last_game,
               AVG(n_tracked) AS avg_group_size
        FROM base""", params).fetchone()

    seq = conn.execute(f"""{cte}
        SELECT grp_result FROM base
        WHERE grp_result IN ('win','loss')
        ORDER BY started_at""", params).fetchall()
    results = [r["grp_result"] for r in seq]

    best_win = best_loss = cur = 0
    cur_kind = None
    for res in results:
        if res == cur_kind:
            cur += 1
        else:
            cur_kind, cur = res, 1
        if cur_kind == "win":
            best_win = max(best_win, cur)
        else:
            best_loss = max(best_loss, cur)
    streak = 0 if not results else (cur if cur_kind == "win" else -cur)

    wins = row["wins"] or 0
    losses = row["losses"] or 0
    return {
        "games": row["total"] or 0,
        "wins": wins,
        "losses": losses,
        "no_result": row["sem_resultado"] or 0,
        "win_rate": _rate(wins, losses),
        "avg_duration_min": round((row["avg_duration"] or 0) / 60.0, 1),
        "avg_group_size": round(row["avg_group_size"] or 0, 2),
        "first_game": row["first_game"],
        "last_game": row["last_game"],
        "current_streak": streak,
        "best_win_streak": best_win,
        "worst_loss_streak": best_loss,
    }


def by_map(conn, f: Filters, min_games: int = 3):
    return _wl_query(conn, f, "b.map", "b.map", having_min=min_games)


def by_kind(conn, f: Filters):
    return _wl_query(conn, f, "b.kind", "b.kind")


def by_server(conn, f: Filters):
    return _wl_query(conn, f, "b.server", "b.server")


def by_civ(conn, f: Filters, min_games: int = 3):
    """Winrate por civilizacao jogada por membros do grupo (uma linha por civ)."""
    cte, params = _base_cte(conn, f)
    sql = f"""{cte}
    SELECT gp.civilization AS label,
           SUM(CASE WHEN gp.result = 'win'  THEN 1 ELSE 0 END) AS wins,
           SUM(CASE WHEN gp.result = 'loss' THEN 1 ELSE 0 END) AS losses
    FROM base b
    JOIN game_players gp ON gp.game_id = b.game_id AND gp.team = b.grp_team
    JOIN players p ON p.profile_id = gp.profile_id AND p.tracked = 1
    GROUP BY gp.civilization
    HAVING wins + losses >= ?
    ORDER BY wins + losses DESC
    """
    rows = conn.execute(sql, params + [min_games]).fetchall()
    return [
        {"label": r["label"], "wins": r["wins"], "losses": r["losses"],
         "games": r["wins"] + r["losses"], "win_rate": _rate(r["wins"], r["losses"])}
        for r in rows if r["label"]
    ]


def vs_civ(conn, f: Filters, min_games: int = 3):
    """Winrate do grupo contra cada civilizacao inimiga (uma linha por civ enfrentada)."""
    cte, params = _base_cte(conn, f)
    sql = f"""{cte}
    SELECT gp.civilization AS label,
           SUM(CASE WHEN b.grp_result = 'win'  THEN 1 ELSE 0 END) AS wins,
           SUM(CASE WHEN b.grp_result = 'loss' THEN 1 ELSE 0 END) AS losses
    FROM base b
    JOIN game_players gp ON gp.game_id = b.game_id AND gp.team <> b.grp_team
    GROUP BY gp.civilization
    HAVING wins + losses >= ?
    ORDER BY wins + losses DESC
    """
    rows = conn.execute(sql, params + [min_games]).fetchall()
    return [
        {"label": r["label"], "wins": r["wins"], "losses": r["losses"],
         "games": r["wins"] + r["losses"], "win_rate": _rate(r["wins"], r["losses"])}
        for r in rows if r["label"]
    ]


def by_player(conn, f: Filters):
    """Winrate individual de cada membro dentro das partidas do grupo."""
    cte, params = _base_cte(conn, f)
    sql = f"""{cte}
    SELECT COALESCE(p.alias, p.name, CAST(p.profile_id AS TEXT)) AS label,
           p.profile_id AS profile_id,
           SUM(CASE WHEN gp.result = 'win'  THEN 1 ELSE 0 END) AS wins,
           SUM(CASE WHEN gp.result = 'loss' THEN 1 ELSE 0 END) AS losses,
           AVG(gp.rating) AS avg_rating,
           SUM(COALESCE(gp.rating_diff, 0)) AS rating_delta
    FROM base b
    JOIN game_players gp ON gp.game_id = b.game_id AND gp.team = b.grp_team
    JOIN players p ON p.profile_id = gp.profile_id AND p.tracked = 1
    GROUP BY p.profile_id
    ORDER BY wins + losses DESC
    """
    rows = conn.execute(sql, params).fetchall()
    return [
        {"label": r["label"], "profile_id": r["profile_id"], "wins": r["wins"], "losses": r["losses"],
         "games": r["wins"] + r["losses"], "win_rate": _rate(r["wins"], r["losses"]),
         "avg_rating": round(r["avg_rating"]) if r["avg_rating"] else None,
         "rating_delta": r["rating_delta"]}
        for r in rows
    ]


def by_lineup(conn, f: Filters, min_games: int = 1):
    """Winrate por composicao do grupo (dupla, trio, ...) que estava no time."""
    cte, params = _base_cte(conn, f)
    sql = f"""{cte}
    SELECT b.game_id, b.grp_result AS result,
           GROUP_CONCAT(COALESCE(p.alias, p.name), ' + ') AS lineup
    FROM base b
    JOIN game_players gp ON gp.game_id = b.game_id AND gp.team = b.grp_team
    JOIN players p ON p.profile_id = gp.profile_id AND p.tracked = 1
    GROUP BY b.game_id
    """
    tally: dict[str, dict] = {}
    for row in conn.execute(sql, params):
        names = " + ".join(sorted((row["lineup"] or "").split(" + ")))
        entry = tally.setdefault(names, {"label": names, "wins": 0, "losses": 0})
        if row["result"] == "win":
            entry["wins"] += 1
        elif row["result"] == "loss":
            entry["losses"] += 1
    out = []
    for entry in tally.values():
        games = entry["wins"] + entry["losses"]
        if games < min_games:
            continue
        entry["games"] = games
        entry["win_rate"] = _rate(entry["wins"], entry["losses"])
        out.append(entry)
    return sorted(out, key=lambda e: -e["games"])


def timeline(conn, f: Filters, bucket: str = "month"):
    """Serie temporal: vitorias/derrotas por mes (ou dia) + winrate acumulado."""
    fmt = "%Y-%m" if bucket == "month" else "%Y-%m-%d"
    cte, params = _base_cte(conn, f)
    sql = f"""{cte}
    SELECT strftime('{fmt}', b.started_at) AS label,
           SUM(CASE WHEN b.grp_result = 'win'  THEN 1 ELSE 0 END) AS wins,
           SUM(CASE WHEN b.grp_result = 'loss' THEN 1 ELSE 0 END) AS losses
    FROM base b
    GROUP BY label
    ORDER BY label
    """
    rows = conn.execute(sql, params).fetchall()
    out, cw, cl = [], 0, 0
    for r in rows:
        cw += r["wins"]
        cl += r["losses"]
        out.append({
            "label": r["label"], "wins": r["wins"], "losses": r["losses"],
            "games": r["wins"] + r["losses"], "win_rate": _rate(r["wins"], r["losses"]),
            "cumulative_win_rate": _rate(cw, cl),
        })
    return out


def by_duration(conn, f: Filters):
    """Winrate por faixa de duracao da partida."""
    cte, params = _base_cte(conn, f)
    sql = f"""{cte}
    SELECT CASE
             WHEN b.duration < 900  THEN '0-15 min'
             WHEN b.duration < 1500 THEN '15-25 min'
             WHEN b.duration < 2100 THEN '25-35 min'
             WHEN b.duration < 3000 THEN '35-50 min'
             ELSE '50+ min'
           END AS label,
           MIN(b.duration) AS ord,
           SUM(CASE WHEN b.grp_result = 'win'  THEN 1 ELSE 0 END) AS wins,
           SUM(CASE WHEN b.grp_result = 'loss' THEN 1 ELSE 0 END) AS losses
    FROM base b
    WHERE b.duration IS NOT NULL
    GROUP BY label
    ORDER BY ord
    """
    rows = conn.execute(sql, params).fetchall()
    return [
        {"label": r["label"], "wins": r["wins"], "losses": r["losses"],
         "games": r["wins"] + r["losses"], "win_rate": _rate(r["wins"], r["losses"])}
        for r in rows
    ]


def games_list(conn, f: Filters, limit: int = 50, offset: int = 0):
    cte, params = _base_cte(conn, f)
    total = conn.execute(f"{cte} SELECT COUNT(*) AS c FROM base", params).fetchone()["c"]
    rows = conn.execute(f"""{cte}
        SELECT * FROM base ORDER BY started_at DESC LIMIT ? OFFSET ?""",
        params + [limit, offset]).fetchall()
    games = []
    for r in rows:
        allies, enemies = [], []
        for gp in conn.execute(
            """SELECT gp.profile_id, gp.name, gp.team, gp.civilization, gp.result,
                      gp.rating, gp.rating_diff, p.alias, p.tracked
               FROM game_players gp
               LEFT JOIN players p ON p.profile_id = gp.profile_id
               WHERE gp.game_id = ?""", (r["game_id"],)):
            item = {
                "profile_id": gp["profile_id"],
                "name": gp["alias"] or gp["name"],
                "civilization": gp["civilization"],
                "rating": gp["rating"],
                "rating_diff": gp["rating_diff"],
                "tracked": bool(gp["tracked"]),
            }
            (allies if gp["team"] == r["grp_team"] else enemies).append(item)
        games.append({
            "game_id": r["game_id"],
            "started_at": r["started_at"],
            "duration_min": round((r["duration"] or 0) / 60.0, 1),
            "map": r["map"],
            "kind": r["kind"],
            "server": r["server"],
            "average_mmr": r["average_mmr"],
            "source": r["source"],
            "result": r["grp_result"],
            "group_size": r["n_tracked"],
            "allies": allies,
            "enemies": enemies,
            "url": f"https://aoe4world.com/players/{allies[0]['profile_id']}/games/{r['game_id']}" if allies else None,
        })
    return {"total": total, "limit": limit, "offset": offset, "games": games}


def facets(conn) -> dict:
    """Valores disponiveis para montar os filtros no frontend."""
    players = [dict(r) for r in conn.execute(
        """SELECT profile_id, COALESCE(alias, name) AS label, name, country, last_synced_at
           FROM players WHERE tracked = 1 ORDER BY label""")]
    kinds = [r["kind"] for r in conn.execute("SELECT DISTINCT kind FROM games ORDER BY kind")]
    seasons = [r["season"] for r in conn.execute(
        "SELECT DISTINCT season FROM games WHERE season IS NOT NULL ORDER BY season DESC")]
    last_sync = conn.execute("SELECT MAX(ran_at) AS ts FROM sync_log WHERE error IS NULL").fetchone()["ts"]
    return {"players": players, "kinds": kinds, "seasons": seasons, "last_sync": last_sync}
