"""Resumo detalhado de cada partida (pontuacao, recursos, abates, unidades).

A API publica `/api/v0` nao expoe esses numeros. O proprio site do AoE4World usa
`https://www.aoe4world.com/players/{profile_id}/games/{game_id}/summary`, que responde
JSON e funciona sem assinatura para quem esta com o Match History publico. E esse
endpoint que alimenta as tabelas "Comparativo" e "Economia destruida".

Nem toda partida tem resumo (partidas antigas ou de perfis privados devolvem 404).
O status fica gravado em `game_summaries` para nao ficar refazendo a mesma chamada.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

SUMMARY_HOST = "https://www.aoe4world.com"
REQUEST_PAUSE = 0.6
MAX_RETRIES = 3

# ---------------------------------------------------------------- classificacao

# O icone do build order e a unica identificacao estavel da unidade:
# "icons/races/mongols/units/keshik_3" -> keshik
_SUFFIX = re.compile(r"(_age_?\d+|_\d+)$")

ECO_WORDS = ("villager", "trader", "caravan", "fishing")
SIEGE_WORDS = ("ram", "trebuchet", "mangonel", "springald", "bombard", "culverin",
               "scorpion", "siege", "nest_of_bees", "chierosiphon", "ribauldequin")
FAITH_WORDS = ("monk", "dervish", "imam", "scholar", "shaman", "prelate",
               "missionary", "priest", "warrior_monk")

# Rotulos em portugues das unidades economicas (o resto entra pelo nome cru).
ECO_LABELS = {
    "villager": "Aldeao",
    "trader": "Comerciante",
    "camel_trader": "Comerciante (camelo)",
    "trade_caravan": "Caravana",
    "caravan": "Caravana",
    "fishing_boat": "Barco de pesca",
    "fishing_ship": "Barco de pesca",
}


def unit_key(icon: str) -> str:
    """Nome canonico da unidade a partir do caminho do icone."""
    base = (icon or "").rstrip("/").split("/")[-1].lower()
    prev = None
    while prev != base:                      # keshik_3 -> keshik, atgeir_age1 -> atgeir
        prev = base
        base = _SUFFIX.sub("", base)
    return base or "desconhecido"


def categorize(key: str) -> str:
    if any(w in key for w in ECO_WORDS):
        return "eco"
    if "scout" in key:
        return "explorador"
    if any(w in key for w in SIEGE_WORDS):
        return "cerco"
    if any(w in key for w in FAITH_WORDS):
        return "religioso"
    return "militar"


def unit_label(key: str) -> str:
    if key in ECO_LABELS:
        return ECO_LABELS[key]
    return key.replace("_", " ").capitalize()


# ---------------------------------------------------------------- download

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch_summary(profile_id: int, game_id: int, user_agent: str) -> dict | None:
    """Baixa o resumo. Devolve None quando a API responde 404 (resumo inexistente)."""
    url = f"{SUMMARY_HOST}/players/{profile_id}/games/{game_id}/summary?camelize=true"
    req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "application/json"})
    delay = 1.0
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 403):
                return None
            if exc.code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
        except urllib.error.URLError:
            if attempt < MAX_RETRIES - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise RuntimeError(f"falha ao buscar {url}")


# ---------------------------------------------------------------- persistencia

def _num(value) -> int | None:
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def save_summary(conn: sqlite3.Connection, game_id: int, payload: dict) -> int:
    """Grava player_summaries + unit_stats. Devolve quantos jogadores foram gravados."""
    conn.execute("DELETE FROM player_summaries WHERE game_id = ?", (game_id,))
    conn.execute("DELETE FROM unit_stats WHERE game_id = ?", (game_id,))

    players = payload.get("players") or []
    for p in players:
        pid = p.get("profileId") or p.get("profile_id")
        if pid is None:
            continue
        st = p.get("_stats") or {}
        scores = p.get("scores") or {}
        spent = p.get("totalResourcesSpent") or {}
        gathered = p.get("totalResourcesGathered") or {}
        conn.execute(
            """INSERT INTO player_summaries (
                 game_id, profile_id, team, civilization, result, apm,
                 score_total, score_military, score_economy, score_technology, score_society,
                 spent_total, spent_food, spent_wood, spent_gold, spent_stone, spent_oliveoil,
                 gathered_total, gathered_food, gathered_wood, gathered_gold, gathered_stone,
                 gathered_oliveoil, kills, deaths, razed, buildings_made, buildings_lost,
                 units_made, upgrades, squad_kills, squad_lost)
               VALUES (?,?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?,?, ?,?,?,?,?,?, ?,?,?,?,?, ?,?,?,?)""",
            (
                game_id, int(pid), p.get("team"), p.get("civilization"), p.get("result"),
                _num(p.get("apm")),
                _num(scores.get("total")), _num(scores.get("military")), _num(scores.get("economy")),
                _num(scores.get("technology")), _num(scores.get("society")),
                _num(spent.get("total")), _num(spent.get("food")), _num(spent.get("wood")),
                _num(spent.get("gold")), _num(spent.get("stone")), _num(spent.get("oliveoil")),
                _num(gathered.get("total")), _num(gathered.get("food")), _num(gathered.get("wood")),
                _num(gathered.get("gold")), _num(gathered.get("stone")), _num(gathered.get("oliveoil")),
                # elitekill/edeaths sao os numeros que o proprio site mostra como Kills/Deaths
                _num(st.get("elitekill")), _num(st.get("edeaths")), _num(st.get("structdmg")),
                _num(st.get("bprod")), _num(st.get("blost")), _num(st.get("unitprod")),
                _num(st.get("upg")), _num(st.get("sqkill")), _num(st.get("sqlost")),
            ),
        )

        tally: dict[str, dict] = {}
        for item in p.get("buildOrder") or []:
            if item.get("type") != "Unit":
                continue
            key = unit_key(item.get("icon") or "")
            entry = tally.setdefault(key, {"made": 0, "lost": 0, "lost_at": []})
            entry["made"] += len(item.get("finished") or [])
            destroyed = item.get("destroyed") or []
            entry["lost"] += len(destroyed)
            # Segundo de jogo de cada perda: e o que permite a linha do tempo do raide.
            entry["lost_at"].extend(t for t in destroyed if isinstance(t, (int, float)))
        for key, entry in tally.items():
            conn.execute(
                """INSERT INTO unit_stats (game_id, profile_id, unit_key, category, made, lost, lost_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (game_id, int(pid), key, categorize(key), entry["made"], entry["lost"],
                 json.dumps(sorted(int(t) for t in entry["lost_at"])) if entry["lost_at"] else None),
            )
    return len(players)


def mark(conn, game_id: int, status: str, version=None, win_reason=None, error=None) -> None:
    conn.execute(
        """INSERT INTO game_summaries (game_id, fetched_at, status, version, win_reason, error)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(game_id) DO UPDATE SET
             fetched_at = excluded.fetched_at,
             status     = excluded.status,
             version    = excluded.version,
             win_reason = excluded.win_reason,
             error      = excluded.error""",
        (game_id, now_iso(), status, version, win_reason, error),
    )


def pending_games(conn: sqlite3.Connection, min_size: int = 2, redo_errors: bool = True,
                  redo_all: bool = False) -> list[tuple[int, int]]:
    """Partidas do grupo que ainda nao tem resumo: [(game_id, profile_id_para_consultar)].

    So vale a pena baixar resumo de partida elegivel (>= min_size monitorados no mesmo
    time) — e o unico recorte que aparece nas telas.
    """
    if redo_all:
        skip = "('missing')"          # rebaixa tudo que ja deu certo (mudanca de schema)
    else:
        skip = "('ok', 'missing')" if redo_errors else "('ok', 'missing', 'error')"
    rows = conn.execute(
        f"""
        WITH grp AS (
            SELECT gp.game_id, gp.team, COUNT(*) AS n
            FROM game_players gp
            JOIN players p ON p.profile_id = gp.profile_id AND p.tracked = 1
            GROUP BY gp.game_id, gp.team
            HAVING COUNT(*) >= ?
        )
        SELECT g.game_id AS game_id,
               MIN(gp.profile_id) AS profile_id
        FROM grp
        JOIN games g ON g.game_id = grp.game_id AND g.ongoing = 0 AND g.source = 'api'
        JOIN game_players gp ON gp.game_id = g.game_id
        JOIN players p ON p.profile_id = gp.profile_id AND p.tracked = 1
        LEFT JOIN game_summaries s ON s.game_id = g.game_id
        WHERE s.game_id IS NULL OR s.status NOT IN {skip}
        GROUP BY g.game_id
        ORDER BY g.started_at DESC
        """,
        (min_size,),
    ).fetchall()
    return [(r["game_id"], r["profile_id"]) for r in rows]


def sync_summaries(conn, user_agent: str, min_size: int = 2, limit: int | None = None,
                   redo_errors: bool = True, redo_all: bool = False, verbose: bool = True) -> dict:
    todo = pending_games(conn, min_size=min_size, redo_errors=redo_errors, redo_all=redo_all)
    if limit:
        todo = todo[:limit]
    done = missing = failed = 0
    for i, (game_id, profile_id) in enumerate(todo, 1):
        try:
            payload = fetch_summary(profile_id, game_id, user_agent)
            if payload is None:
                mark(conn, game_id, "missing")
                missing += 1
            else:
                n = save_summary(conn, game_id, payload)
                mark(conn, game_id, "ok", version=payload.get("summaryVersion"),
                     win_reason=payload.get("winReason"))
                done += 1
                if verbose:
                    print(f"  [{i}/{len(todo)}] {game_id}: {n} jogadores")
        except Exception as exc:
            mark(conn, game_id, "error", error=str(exc))
            failed += 1
            print(f"  [{i}/{len(todo)}] {game_id}: ERRO {exc}", file=sys.stderr)
        conn.commit()
        time.sleep(REQUEST_PAUSE)
    return {"total": len(todo), "ok": done, "missing": missing, "error": failed}
