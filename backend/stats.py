"""Agregacoes de estatisticas sobre as partidas jogadas em grupo.

Regra central: uma partida so conta quando pelo menos `min_size` jogadores
monitorados estao NO MESMO TIME. Assim o winrate reflete o desempenho do grupo,
nao o de cada um jogando sozinho.
"""
from __future__ import annotations

import json
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


# ---------------------------------------------------------------------------
# Resumo detalhado (tabelas "Comparativo" e "Economia destruida").
# Depende de `python backend/sync.py --summaries`; sem isso as tabelas vem vazias.
# ---------------------------------------------------------------------------

# Grupos e colunas do comparativo. `tone` so define a cor da barra no front.
COMPARISON_GROUPS = [
    {"label": "Pontuação", "columns": [
        {"key": "score_total", "label": "Total", "tone": "score"},
        {"key": "score_military", "label": "Militar", "tone": "score"},
        {"key": "score_economy", "label": "Econ.", "tone": "score"},
        {"key": "score_technology", "label": "Tecn.", "tone": "score"},
        {"key": "score_society", "label": "Social", "tone": "score"},
    ]},
    {"label": "Recursos gastos", "columns": [
        {"key": "spent_total", "label": "Total", "tone": "neutral"},
        {"key": "spent_food", "label": "Comida", "tone": "food"},
        {"key": "spent_wood", "label": "Madeira", "tone": "wood"},
        {"key": "spent_gold", "label": "Ouro", "tone": "gold"},
        {"key": "spent_stone", "label": "Pedra", "tone": "stone"},
        {"key": "spent_oliveoil", "label": "Azeite", "tone": "oil"},
    ]},
    {"label": "Produção", "columns": [
        {"key": "units_made", "label": "Unidades", "tone": "neutral"},
        {"key": "villagers_made", "label": "Aldeões", "tone": "neutral"},
        {"key": "buildings_made", "label": "Construções", "tone": "neutral"},
        {"key": "upgrades", "label": "Pesquisas", "tone": "neutral"},
    ]},
    {"label": "Combate", "columns": [
        {"key": "kills", "label": "Abates", "tone": "good"},
        {"key": "deaths", "label": "Perdas", "tone": "bad"},
        {"key": "kd", "label": "A/P", "tone": "good", "decimals": 2},
        {"key": "razed", "label": "Arrasados", "tone": "good"},
        {"key": "buildings_lost", "label": "Prédios perd.", "tone": "bad"},
    ]},
    {"label": "Ritmo", "columns": [
        {"key": "apm", "label": "APM", "tone": "neutral"},
    ]},
]

# Rotulos em portugues das unidades economicas.
ECO_LABELS_PT = {
    "villager": "Aldeões",
    "gilded_villager": "Aldeões dourados (Zhu Xi)",
    "mounted_villager": "Aldeões montados",
    "jeanne_villager": "Aldeões (Jeanne)",
    "treasure_caravan": "Caravanas do tesouro",
    "reindeer_trader": "Comerciantes (rena)",
    "trader": "Comerciantes",
    "camel_trader": "Comerciantes (camelo)",
    "trade_caravan": "Caravanas",
    "caravan": "Caravanas",
    "fishing_boat": "Barcos de pesca",
    "fishing_ship": "Barcos de pesca",
}


def _eco_label(key: str) -> str:
    return ECO_LABELS_PT.get(key, key.replace("_", " ").capitalize())


def _summary_coverage(conn, f: Filters) -> dict:
    """Quantas partidas do recorte ja tem resumo detalhado baixado."""
    cte, params = _base_cte(conn, f)
    row = conn.execute(f"""{cte}
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN s.status = 'ok'      THEN 1 ELSE 0 END) AS with_summary,
               SUM(CASE WHEN s.status = 'missing' THEN 1 ELSE 0 END) AS without_summary
        FROM base b
        LEFT JOIN game_summaries s ON s.game_id = b.game_id
    """, params).fetchone()
    return {
        "games": row["total"] or 0,
        "with_summary": row["with_summary"] or 0,
        "without_summary": row["without_summary"] or 0,
    }


def comparison(conn, f: Filters, mode: str = "avg") -> dict:
    """Matriz jogador x metrica com os numeros do resumo detalhado.

    mode = "avg" (media por partida, padrao) ou "sum" (soma do periodo).
    So entram partidas do recorte que ja tem resumo baixado.
    """
    mode = "sum" if mode == "sum" else "avg"
    cte, params = _base_cte(conn, f)
    agg = "SUM" if mode == "sum" else "AVG"
    sql = f"""{cte},
    units AS (
        SELECT us.game_id, us.profile_id,
               SUM(CASE WHEN us.unit_key LIKE '%villager%' THEN us.made ELSE 0 END) AS villagers_made
        FROM unit_stats us
        GROUP BY us.game_id, us.profile_id
    )
    SELECT COALESCE(p.alias, p.name, CAST(p.profile_id AS TEXT)) AS label,
           p.profile_id AS profile_id,
           COUNT(*) AS games,
           {agg}(ps.score_total) AS score_total,
           {agg}(ps.score_military) AS score_military,
           {agg}(ps.score_economy) AS score_economy,
           {agg}(ps.score_technology) AS score_technology,
           {agg}(ps.score_society) AS score_society,
           {agg}(ps.spent_total) AS spent_total,
           {agg}(ps.spent_food) AS spent_food,
           {agg}(ps.spent_wood) AS spent_wood,
           {agg}(ps.spent_gold) AS spent_gold,
           {agg}(ps.spent_stone) AS spent_stone,
           {agg}(COALESCE(ps.spent_oliveoil, 0)) AS spent_oliveoil,
           {agg}(ps.units_made) AS units_made,
           {agg}(COALESCE(u.villagers_made, 0)) AS villagers_made,
           {agg}(ps.buildings_made) AS buildings_made,
           {agg}(ps.upgrades) AS upgrades,
           {agg}(COALESCE(ps.kills, 0)) AS kills,
           {agg}(COALESCE(ps.deaths, 0)) AS deaths,
           {agg}(COALESCE(ps.razed, 0)) AS razed,
           {agg}(COALESCE(ps.buildings_lost, 0)) AS buildings_lost,
           {agg}(ps.apm) AS apm,
           SUM(COALESCE(ps.kills, 0)) AS kills_sum,
           SUM(COALESCE(ps.deaths, 0)) AS deaths_sum
    FROM base b
    JOIN game_summaries s ON s.game_id = b.game_id AND s.status = 'ok'
    JOIN game_players gp ON gp.game_id = b.game_id AND gp.team = b.grp_team
    JOIN players p ON p.profile_id = gp.profile_id AND p.tracked = 1
    JOIN player_summaries ps ON ps.game_id = b.game_id AND ps.profile_id = gp.profile_id
    LEFT JOIN units u ON u.game_id = b.game_id AND u.profile_id = gp.profile_id
    GROUP BY p.profile_id
    ORDER BY games DESC, label
    """
    keys = [c["key"] for g in COMPARISON_GROUPS for c in g["columns"]]
    rows = []
    for r in conn.execute(sql, params):
        values = {}
        for key in keys:
            if key == "kd":
                deaths = r["deaths_sum"] or 0
                values["kd"] = round((r["kills_sum"] or 0) / deaths, 2) if deaths else None
                continue
            raw = r[key]
            if raw is None:
                values[key] = None
            elif mode == "avg":
                values[key] = round(raw, 1) if raw < 100 else round(raw)
            else:
                values[key] = round(raw)
        rows.append({
            "label": r["label"], "profile_id": r["profile_id"],
            "games": r["games"], "values": values,
        })
    return {
        "mode": mode,
        "groups": COMPARISON_GROUPS,
        "rows": rows,
        "coverage": _summary_coverage(conn, f),
    }


def eco_kills(conn, f: Filters) -> dict:
    """Unidades economicas destruidas: o que o grupo eliminou e o que perdeu.

    A API nao diz *quem* deu o abate: o resumo registra, para cada jogador, as
    unidades que ele perdeu. Entao "eliminadas" e a soma das perdas economicas do
    time adversario nas partidas do recorte (credito do time, nao de um jogador),
    e "perdidas" sai por jogador direto do resumo dele.
    """
    cte, params = _base_cte(conn, f)

    # 1) eliminadas pelo grupo = perdas economicas do time adversario, por tipo
    killed = []
    for r in conn.execute(f"""{cte}
            SELECT us.unit_key AS unit_key, SUM(us.lost) AS total,
                   COUNT(DISTINCT b.game_id) AS games
            FROM base b
            JOIN game_summaries s ON s.game_id = b.game_id AND s.status = 'ok'
            JOIN game_players gp ON gp.game_id = b.game_id AND gp.team <> b.grp_team
            JOIN unit_stats us ON us.game_id = b.game_id AND us.profile_id = gp.profile_id
            WHERE us.category = 'eco' AND us.lost > 0
            GROUP BY us.unit_key
            ORDER BY total DESC
        """, params):
        killed.append({"key": r["unit_key"], "label": _eco_label(r["unit_key"]),
                       "total": r["total"] or 0, "games": r["games"] or 0})

    # 2) perdidas por cada membro do grupo, por tipo de unidade
    per_player: dict[int, dict] = {}
    for r in conn.execute(f"""{cte}
            SELECT p.profile_id AS profile_id,
                   COALESCE(p.alias, p.name, CAST(p.profile_id AS TEXT)) AS label,
                   us.unit_key AS unit_key,
                   SUM(us.lost) AS lost,
                   SUM(us.made) AS made,
                   COUNT(DISTINCT b.game_id) AS games
            FROM base b
            JOIN game_summaries s ON s.game_id = b.game_id AND s.status = 'ok'
            JOIN game_players gp ON gp.game_id = b.game_id AND gp.team = b.grp_team
            JOIN players p ON p.profile_id = gp.profile_id AND p.tracked = 1
            JOIN unit_stats us ON us.game_id = b.game_id AND us.profile_id = gp.profile_id
            WHERE us.category = 'eco'
            GROUP BY p.profile_id, us.unit_key
        """, params):
        entry = per_player.setdefault(r["profile_id"], {
            "label": r["label"], "profile_id": r["profile_id"],
            "games": 0, "made": 0, "lost": 0, "by_unit": {},
        })
        entry["games"] = max(entry["games"], r["games"] or 0)
        entry["made"] += r["made"] or 0
        entry["lost"] += r["lost"] or 0
        if r["lost"]:
            entry["by_unit"][_eco_label(r["unit_key"])] = r["lost"]

    players = []
    for entry in per_player.values():
        games = entry["games"] or 0
        entry["lost_per_game"] = round(entry["lost"] / games, 1) if games else None
        entry["made_per_game"] = round(entry["made"] / games, 1) if games else None
        entry["survival"] = round(100.0 * (entry["made"] - entry["lost"]) / entry["made"], 1) if entry["made"] else None
        players.append(entry)
    players.sort(key=lambda e: -e["lost"])

    # 3) eliminadas por civilizacao adversaria (onde a eco inimiga mais cai)
    by_enemy_civ = []
    for r in conn.execute(f"""{cte}
            SELECT gp.civilization AS label,
                   SUM(us.lost) AS total,
                   COUNT(DISTINCT b.game_id) AS games
            FROM base b
            JOIN game_summaries s ON s.game_id = b.game_id AND s.status = 'ok'
            JOIN game_players gp ON gp.game_id = b.game_id AND gp.team <> b.grp_team
            JOIN unit_stats us ON us.game_id = b.game_id AND us.profile_id = gp.profile_id
            WHERE us.category = 'eco'
            GROUP BY gp.civilization
            HAVING COUNT(DISTINCT b.game_id) >= 3
            ORDER BY SUM(us.lost) * 1.0 / COUNT(DISTINCT b.game_id) DESC
        """, params):
        if not r["label"]:
            continue
        by_enemy_civ.append({
            "label": r["label"], "total": r["total"] or 0, "games": r["games"],
            "per_game": round((r["total"] or 0) / r["games"], 1) if r["games"] else None,
        })

    coverage = _summary_coverage(conn, f)
    games = coverage["with_summary"]
    eliminated = sum(k["total"] for k in killed)
    lost = sum(p["lost"] for p in players)
    return {
        "coverage": coverage,
        "totals": {
            "eliminated": eliminated,
            "lost": lost,
            "balance": eliminated - lost,
            "eliminated_per_game": round(eliminated / games, 1) if games else None,
            "lost_per_game": round(lost / games, 1) if games else None,
        },
        "by_unit": killed,
        "by_player": players,
        "by_enemy_civ": by_enemy_civ,
    }


# Metricas mostradas no comparativo de uma partida so (mesma leitura da matriz geral).
GAME_COLUMNS = [
    {"label": "Pontuação", "columns": [
        {"key": "score_total", "label": "Total", "tone": "score"},
        {"key": "score_military", "label": "Militar", "tone": "score"},
        {"key": "score_economy", "label": "Econ.", "tone": "score"},
        {"key": "score_technology", "label": "Tecn.", "tone": "score"},
    ]},
    {"label": "Recursos gastos", "columns": [
        {"key": "spent_total", "label": "Total", "tone": "neutral"},
        {"key": "spent_food", "label": "Comida", "tone": "food"},
        {"key": "spent_wood", "label": "Madeira", "tone": "wood"},
        {"key": "spent_gold", "label": "Ouro", "tone": "gold"},
        {"key": "spent_stone", "label": "Pedra", "tone": "stone"},
    ]},
    {"label": "Produção", "columns": [
        {"key": "units_made", "label": "Unidades", "tone": "neutral"},
        {"key": "villagers_made", "label": "Aldeões", "tone": "neutral"},
        {"key": "buildings_made", "label": "Construções", "tone": "neutral"},
        {"key": "upgrades", "label": "Pesquisas", "tone": "neutral"},
    ]},
    {"label": "Combate", "columns": [
        {"key": "kills", "label": "Abates", "tone": "good"},
        {"key": "deaths", "label": "Perdas", "tone": "bad"},
        {"key": "razed", "label": "Arrasados", "tone": "good"},
        {"key": "buildings_lost", "label": "Prédios perd.", "tone": "bad"},
    ]},
    {"label": "Ritmo", "columns": [
        {"key": "apm", "label": "APM", "tone": "neutral"},
    ]},
]


def game_detail(conn, game_id: int) -> dict | None:
    """Detalhe de uma partida: aldeões perdidos por jogador dos dois times + comparativo.

    Os aldeões perdidos saem do build order de cada jogador (`destroyed`), que traz o
    segundo de jogo de cada perda — por isso dá para montar a linha do tempo do raide.
    A API nao registra quem deu o abate, so quem perdeu a unidade.
    """
    game = conn.execute(
        """SELECT game_id, started_at, duration, map, kind, leaderboard, season,
                  server, average_mmr, source
           FROM games WHERE game_id = ?""", (game_id,)).fetchone()
    if game is None:
        return None

    summary_row = conn.execute(
        "SELECT status, win_reason FROM game_summaries WHERE game_id = ?", (game_id,)).fetchone()
    status = summary_row["status"] if summary_row else None

    players = [dict(r) for r in conn.execute(
        """SELECT gp.profile_id, gp.team, gp.result, gp.civilization, gp.rating, gp.rating_diff,
                  COALESCE(p.alias, p.name, gp.name, CAST(gp.profile_id AS TEXT)) AS name,
                  COALESCE(p.tracked, 0) AS tracked
           FROM game_players gp
           LEFT JOIN players p ON p.profile_id = gp.profile_id
           WHERE gp.game_id = ?
           ORDER BY gp.team, name""", (game_id,))]
    if not players:
        return None

    stats_by_pid = {r["profile_id"]: dict(r) for r in conn.execute(
        "SELECT * FROM player_summaries WHERE game_id = ?", (game_id,))}

    # Aldeões e demais unidades economicas, com os instantes das perdas.
    eco_by_pid: dict[int, dict] = {}
    for r in conn.execute(
            """SELECT profile_id, unit_key, made, lost, lost_at
               FROM unit_stats WHERE game_id = ? AND category = 'eco'""", (game_id,)):
        entry = eco_by_pid.setdefault(r["profile_id"], {
            "villagers_made": 0, "villagers_lost": 0, "eco_made": 0, "eco_lost": 0,
            "lost_at": [], "by_unit": {},
        })
        is_villager = "villager" in r["unit_key"]
        entry["eco_made"] += r["made"] or 0
        entry["eco_lost"] += r["lost"] or 0
        if is_villager:
            entry["villagers_made"] += r["made"] or 0
            entry["villagers_lost"] += r["lost"] or 0
        if r["lost"]:
            entry["by_unit"][_eco_label(r["unit_key"])] = r["lost"]
        if r["lost_at"]:
            try:
                entry["lost_at"].extend(json.loads(r["lost_at"]))
            except (TypeError, ValueError):
                pass

    duration = game["duration"] or 0
    minutes = max(1, int(round(duration / 60.0)) or 1)

    # Nosso time = onde estao os jogadores monitorados.
    tracked_by_team: dict[int, int] = {}
    for p in players:
        if p["tracked"]:
            tracked_by_team[p["team"]] = tracked_by_team.get(p["team"], 0) + 1
    our_team = max(tracked_by_team, key=tracked_by_team.get) if tracked_by_team else None

    teams: dict[int, dict] = {}
    for p in players:
        eco = eco_by_pid.get(p["profile_id"], {})
        ps = stats_by_pid.get(p["profile_id"], {})
        lost_at = sorted(eco.get("lost_at") or [])
        # Um balde por minuto de jogo: mostra quando a economia caiu.
        buckets = [0] * (minutes + 1)
        for t in lost_at:
            idx = min(minutes, int(t // 60))
            buckets[idx] += 1
        worst = max(range(len(buckets)), key=lambda i: buckets[i]) if lost_at else None

        made = eco.get("villagers_made", 0)
        lost = eco.get("villagers_lost", 0)
        row = {
            "profile_id": p["profile_id"],
            "name": p["name"],
            "civilization": p["civilization"],
            "tracked": bool(p["tracked"]),
            "result": p["result"],
            "rating": p["rating"],
            "rating_diff": p["rating_diff"],
            "villagers_made": made,
            "villagers_lost": lost,
            "villagers_alive": made - lost,
            "loss_pct": round(100.0 * lost / made, 1) if made else None,
            "eco_lost": eco.get("eco_lost", 0),
            "eco_by_unit": eco.get("by_unit", {}),
            "lost_at": lost_at,
            "per_minute": buckets,
            "worst_minute": {"minute": worst, "count": buckets[worst]} if worst is not None else None,
            "stats": {k: ps.get(k) for k in (
                "score_total", "score_military", "score_economy", "score_technology",
                "spent_total", "spent_food", "spent_wood", "spent_gold", "spent_stone",
                "units_made", "buildings_made", "upgrades",
                "kills", "deaths", "razed", "buildings_lost", "apm")},
        }
        row["stats"]["villagers_made"] = made
        team = teams.setdefault(p["team"], {
            "team": p["team"],
            "is_ours": p["team"] == our_team,
            "result": None,
            "villagers_made": 0,
            "villagers_lost": 0,
            "players": [],
        })
        team["players"].append(row)
        team["villagers_made"] += made
        team["villagers_lost"] += lost
        if p["result"] in ("win", "loss"):
            team["result"] = p["result"]

    ordered = sorted(teams.values(), key=lambda t: (not t["is_ours"], t["team"]))
    for t in ordered:
        t["players"].sort(key=lambda r: -r["villagers_lost"])

    return {
        "game": {
            "game_id": game["game_id"],
            "started_at": game["started_at"],
            "duration_min": round(duration / 60.0, 1),
            "duration_minutes": minutes,
            "map": game["map"],
            "kind": game["kind"],
            "server": game["server"],
            "average_mmr": game["average_mmr"],
            "win_reason": summary_row["win_reason"] if summary_row else None,
            "url": f"https://aoe4world.com/players/{players[0]['profile_id']}/games/{game['game_id']}",
        },
        "summary_status": status,
        "has_summary": status == "ok",
        "columns": GAME_COLUMNS,
        "teams": ordered,
    }
