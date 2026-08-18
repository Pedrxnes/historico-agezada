"""API HTTP + servidor dos arquivos estaticos do site."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import db
import stats

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"

app = FastAPI(title="AoE4 Squad Stats", docs_url="/api/docs", redoc_url=None)


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
        }
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


@app.get("/api/health")
def api_health():
    conn = get_conn()
    try:
        row = conn.execute("SELECT COUNT(*) AS games FROM games").fetchone()
        last = conn.execute("SELECT MAX(ran_at) AS ts FROM sync_log").fetchone()["ts"]
        return {"ok": True, "games": row["games"], "last_sync": last}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    finally:
        conn.close()


if WEB_DIR.is_dir():
    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
