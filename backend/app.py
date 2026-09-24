"""API HTTP + servidor dos arquivos estaticos do site."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import db
import insights
import stats

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"

app = FastAPI(title="Agezada", docs_url="/api/docs", redoc_url=None)


def get_conn():
    conn = db.connect()
    db.init(conn)
    return conn


def parse_filters(
    preset: str,
    players: str | None,
    min_size: int,
    date_from: str | None,
    date_to: str | None,
    season: int | None,
    map_name: str | None,
) -> stats.Filters:
    required = []
    if players:
        for chunk in players.split(","):
            chunk = chunk.strip()
            if chunk.isdigit():
                required.append(int(chunk))
    return stats.Filters(
        preset=preset,
        required=required,
        min_size=min_size,
        date_from=date_from,
        date_to=date_to,
        season=season,
        map_name=map_name,
    )


COMMON = dict(
    preset=Query("tg", description="all | tg | tg_ranked | tg_qm | ffa | custom"),
    players=Query(None, description="profile_ids que precisam estar juntos, separados por virgula"),
    min_size=Query(2, ge=1, le=8, description="minimo de jogadores nossos no mesmo time"),
    date_from=Query(None, alias="from"),
    date_to=Query(None, alias="to"),
    season=Query(None),
    map_name=Query(None, alias="map"),
)


@app.get("/api/facets")
def api_facets():
    conn = get_conn()
    try:
        return stats.facets(conn)
    finally:
        conn.close()


@app.get("/api/stats")
def api_stats(
    preset: str = COMMON["preset"],
    players: str | None = COMMON["players"],
    min_size: int = COMMON["min_size"],
    date_from: str | None = COMMON["date_from"],
    date_to: str | None = COMMON["date_to"],
    season: int | None = COMMON["season"],
    map_name: str | None = COMMON["map_name"],
    min_games: int = Query(3, ge=1, description="minimo de partidas para aparecer em civ/mapa"),
    cmp_mode: str = Query("avg", description="comparativo: avg (media por partida) ou sum (total)"),
):
    """Todos os agregados de uma vez (o frontend faz uma chamada so)."""
    f = parse_filters(preset, players, min_size, date_from, date_to, season, map_name)
    conn = get_conn()
    try:
        return {
            "filters": {
                "preset": f.preset, "required": f.required, "min_size": f.min_size,
                "from": f.date_from, "to": f.date_to, "season": f.season, "map": f.map_name,
            },
            "summary": stats.summary(conn, f),
            "by_kind": stats.by_kind(conn, f),
            "by_map": stats.by_map(conn, f, min_games),
            "by_civ": stats.by_civ(conn, f, min_games),
            "vs_civ": stats.vs_civ(conn, f, min_games),
            "by_player": stats.by_player(conn, f),
            "by_lineup": stats.by_lineup(conn, f),
            "by_duration": stats.by_duration(conn, f),
            "timeline": stats.timeline(conn, f),
            "comparison": stats.comparison(conn, f, cmp_mode),
            "eco_kills": stats.eco_kills(conn, f),
        }
    finally:
        conn.close()


@app.get("/api/comparison")
def api_comparison(
    preset: str = COMMON["preset"],
    players: str | None = COMMON["players"],
    min_size: int = COMMON["min_size"],
    date_from: str | None = COMMON["date_from"],
    date_to: str | None = COMMON["date_to"],
    season: int | None = COMMON["season"],
    map_name: str | None = COMMON["map_name"],
    mode: str = Query("avg", description="avg (media por partida) ou sum (total)"),
):
    """Matriz jogador x metrica do resumo detalhado (pontuacao, recursos, combate)."""
    f = parse_filters(preset, players, min_size, date_from, date_to, season, map_name)
    conn = get_conn()
    try:
        return {"comparison": stats.comparison(conn, f, mode), "eco_kills": stats.eco_kills(conn, f)}
    finally:
        conn.close()


VIEWS = ("overview", "players", "civs", "economy", "combat", "records")


@app.get("/api/view/{view}")
def api_view(
    view: str,
    preset: str = COMMON["preset"],
    players: str | None = COMMON["players"],
    min_size: int = COMMON["min_size"],
    date_from: str | None = COMMON["date_from"],
    date_to: str | None = COMMON["date_to"],
    season: int | None = COMMON["season"],
    map_name: str | None = COMMON["map_name"],
    min_games: int = Query(3, ge=1, le=50, description="minimo de partidas para aparecer nos rankings"),
    cmp_mode: str = Query("avg", description="comparativo: avg (media por partida) ou sum (total)"),
    pid: int | None = Query(None, description="profile_id para a view player"),
):
    """Os dados de uma aba do site. O frontend so pede a aba que esta aberta."""
    f = parse_filters(preset, players, min_size, date_from, date_to, season, map_name)
    conn = get_conn()
    try:
        if view == "overview":
            return {
                "summary": stats.summary(conn, f),
                "timeline": stats.timeline(conn, f),
                "by_kind": stats.by_kind(conn, f),
                "by_duration": stats.by_duration(conn, f),
                "win_reasons": insights.win_reasons(conn, f),
                "mmr_gap": insights.mmr_gap(conn, f),
                "tilt": insights.tilt(conn, f),
            }
        if view == "players":
            return {
                "by_player": stats.by_player(conn, f),
                "by_lineup": stats.by_lineup(conn, f, min_games),
                "partnerships": insights.partnerships(conn, f, min_games),
                "comparison": stats.comparison(conn, f, cmp_mode),
            }
        if view == "civs":
            return {
                "by_civ": stats.by_civ(conn, f, min_games),
                "vs_civ": stats.vs_civ(conn, f, min_games),
                "civ_combos": insights.civ_combos(conn, f, min_games),
                "by_map": stats.by_map(conn, f, min_games),
            }
        if view == "economy":
            return {
                "resources": insights.resources(conn, f),
                "eco_kills": stats.eco_kills(conn, f),
                "early_eco": insights.early_eco(conn, f),
                "first_villager": insights.first_villager(conn, f),
            }
        if view == "combat":
            return {"army": insights.army(conn, f)}
        if view == "records":
            return {"records": insights.records(conn, f), "summary": stats.summary(conn, f)}
        if view == "player":
            data = insights.player_profile(conn, f, pid, min_games) if pid is not None else None
            if data is None:
                return JSONResponse({"error": "jogador nao encontrado"}, status_code=404)
            return data
        return JSONResponse({"error": f"view desconhecida; use {', '.join(VIEWS)} ou player"}, status_code=404)
    finally:
        conn.close()


@app.get("/api/games")
def api_games(
    preset: str = COMMON["preset"],
    players: str | None = COMMON["players"],
    min_size: int = COMMON["min_size"],
    date_from: str | None = COMMON["date_from"],
    date_to: str | None = COMMON["date_to"],
    season: int | None = COMMON["season"],
    map_name: str | None = COMMON["map_name"],
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    f = parse_filters(preset, players, min_size, date_from, date_to, season, map_name)
    conn = get_conn()
    try:
        return stats.games_list(conn, f, limit, offset)
    finally:
        conn.close()


@app.get("/api/games/{game_id}")
def api_game_detail(game_id: int):
    """Detalhe de uma partida: aldeoes perdidos por jogador dos dois times + comparativo."""
    conn = get_conn()
    try:
        data = stats.game_detail(conn, game_id)
        if data is None:
            return JSONResponse({"error": "partida nao encontrada"}, status_code=404)
        return data
    finally:
        conn.close()


@app.get("/api/health")
def api_health():
    conn = get_conn()
    try:
        row = conn.execute("SELECT COUNT(*) AS games FROM games").fetchone()
        last = conn.execute("SELECT MAX(ran_at) AS ts FROM sync_log").fetchone()["ts"]
        summaries = conn.execute(
            "SELECT COUNT(*) AS n FROM game_summaries WHERE status = 'ok'").fetchone()["n"]
        return {"ok": True, "games": row["games"], "summaries": summaries, "last_sync": last}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    finally:
        conn.close()


if WEB_DIR.is_dir():
    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
