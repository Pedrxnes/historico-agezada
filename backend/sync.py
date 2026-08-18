"""Sincroniza partidas da API do aoe4world para o SQLite local.

Uso:
    python sync.py                 # incremental (padrao)
    python sync.py --full          # backfill completo de todo o historico
    python sync.py --import-custom partidas.json   # partidas personalizadas manuais
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import db

API = "https://aoe4world.com/api/v0"
PAGE_SIZE = 50          # teto real da API
REQUEST_PAUSE = 0.5     # segundos entre requests (uso educado da API)
MAX_RETRIES = 4


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Client:
    def __init__(self, user_agent: str):
        self.user_agent = user_agent

    def get(self, path: str, **params) -> dict:
        url = f"{API}{path}"
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
        delay = 1.0
        for attempt in range(MAX_RETRIES):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as exc:
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


def upsert_player(conn, profile_id: int, alias, client: Client) -> None:
    name = steam_id = country = None
    try:
        prof = client.get(f"/players/{profile_id}")
        name = prof.get("name")
        steam_id = prof.get("steam_id") or None
        country = prof.get("country")
    except Exception as exc:  # perfil pode estar privado/indisponivel; nao aborta o sync
        print(f"  aviso: perfil {profile_id} nao lido ({exc})", file=sys.stderr)
    conn.execute(
        """INSERT INTO players (profile_id, name, alias, steam_id, country, tracked)
           VALUES (?, ?, ?, ?, ?, 1)
           ON CONFLICT(profile_id) DO UPDATE SET
             name     = COALESCE(excluded.name, players.name),
             alias    = COALESCE(excluded.alias, players.alias),
             steam_id = COALESCE(excluded.steam_id, players.steam_id),
             country  = COALESCE(excluded.country, players.country),
             tracked  = 1""",
        (profile_id, name, alias, steam_id, country),
    )
    conn.commit()


def save_game(conn, game: dict, source: str = "api") -> bool:
    """Grava a partida. Retorna True se era inedita."""
    game_id = game["game_id"]
    existed = conn.execute("SELECT 1 FROM games WHERE game_id = ?", (game_id,)).fetchone() is not None
    conn.execute(
        """INSERT INTO games (game_id, started_at, updated_at, duration, map, kind, leaderboard,
                              season, patch, server, average_mmr, ongoing, source)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(game_id) DO UPDATE SET
             updated_at  = excluded.updated_at,
             duration    = excluded.duration,
             ongoing     = excluded.ongoing,
             average_mmr = excluded.average_mmr""",
        (
            game_id, game.get("started_at"), game.get("updated_at"), game.get("duration"),
            game.get("map"), game.get("kind"), game.get("leaderboard"), game.get("season"),
            game.get("patch"), game.get("server"), game.get("average_mmr"),
            1 if game.get("ongoing") else 0, source,
        ),
    )
    for team_index, team in enumerate(game.get("teams") or []):
        for slot in team:
            p = slot.get("player") or {}
            if p.get("profile_id") is None:
                continue
            conn.execute(
                """INSERT INTO game_players (game_id, profile_id, name, team, result, civilization,
                                             rating, rating_diff, mmr, mmr_diff, input_type)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(game_id, profile_id) DO UPDATE SET
                     result      = excluded.result,
                     rating      = excluded.rating,
                     rating_diff = excluded.rating_diff,
                     mmr         = excluded.mmr,
                     mmr_diff    = excluded.mmr_diff""",
                (
                    game_id, p["profile_id"], p.get("name"), team_index, p.get("result"),
                    p.get("civilization"), p.get("rating"), p.get("rating_diff"),
                    p.get("mmr"), p.get("mmr_diff"), p.get("input_type"),
                ),
            )
    return not existed


def sync_player(conn, client: Client, profile_id: int, full: bool):
    """Baixa partidas do jogador. Incremental para quando a pagina nao traz novidade."""
    watermark = conn.execute(
        """SELECT MAX(g.started_at) AS ts FROM games g
           JOIN game_players gp ON gp.game_id = g.game_id
           WHERE gp.profile_id = ? AND g.source = 'api'""",
        (profile_id,),
    ).fetchone()["ts"]
    fetched = inserted = 0
    page = 1
    while True:
        data = client.get(f"/players/{profile_id}/games", limit=PAGE_SIZE, page=page)
        games = data.get("games") or []
        if not games:
            break
        new_here = 0
        for game in games:
            fetched += 1
            if save_game(conn, game):
                new_here += 1
        conn.commit()
        inserted += new_here
        total = data.get("total_count") or 0
        oldest = min((g.get("started_at") or "") for g in games)
        # Incremental: nada novo nesta pagina e ja passamos do ultimo jogo conhecido -> para.
        if not full and watermark and new_here == 0 and oldest <= watermark:
            break
        if page * PAGE_SIZE >= total:
            break
        page += 1
        time.sleep(REQUEST_PAUSE)
    conn.execute("UPDATE players SET last_synced_at = ? WHERE profile_id = ?", (now_iso(), profile_id))
    conn.commit()
    return fetched, inserted


def import_custom(conn, path: str) -> int:
    """Importa partidas personalizadas de um JSON manual (mesmo formato da API).

    Formato minimo por partida:
      {"game_id": -1, "started_at": "2026-08-01T20:00:00Z", "duration": 1800,
       "map": "Dry Arabia", "kind": "custom_3v3",
       "teams": [[{"player": {"profile_id": 24270406, "result": "win",
                              "civilization": "french"}}], [...]]}
    game_id negativo evita colisao com ids da API.
    """
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    games = payload if isinstance(payload, list) else payload.get("games", [])
    count = 0
    for game in games:
        game.setdefault("leaderboard", "custom")
        game.setdefault("kind", "custom")
        if save_game(conn, game, source="manual"):
            count += 1
    conn.commit()
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="Sincroniza partidas do aoe4world")
    parser.add_argument("--full", action="store_true", help="backfill completo (ignora watermark)")
    parser.add_argument("--import-custom", metavar="ARQUIVO", help="importa partidas personalizadas de um JSON")
    args = parser.parse_args()

    config = db.load_config()
    client = Client(config.get("user_agent", "aoe4-friends-stats/1.0"))
    conn = db.connect()
    db.init(conn)

    if args.import_custom:
        n = import_custom(conn, args.import_custom)
        print(f"partidas personalizadas importadas: {n}")
        return 0

    for entry in config["players"]:
        pid, alias = entry["profile_id"], entry.get("alias")
        upsert_player(conn, pid, alias, client)
        try:
            fetched, inserted = sync_player(conn, client, pid, args.full)
            conn.execute(
                "INSERT INTO sync_log (ran_at, profile_id, mode, fetched, inserted) VALUES (?,?,?,?,?)",
                (now_iso(), pid, "full" if args.full else "incremental", fetched, inserted),
            )
            print(f"{alias or pid}: {fetched} lidas, {inserted} novas")
        except Exception as exc:
            conn.execute(
                "INSERT INTO sync_log (ran_at, profile_id, mode, fetched, inserted, error) VALUES (?,?,?,?,?,?)",
                (now_iso(), pid, "full" if args.full else "incremental", 0, 0, str(exc)),
            )
            print(f"{alias or pid}: ERRO {exc}", file=sys.stderr)
        conn.commit()
        time.sleep(REQUEST_PAUSE)

    total = conn.execute("SELECT COUNT(*) c FROM games").fetchone()["c"]
    print(f"total de partidas no banco: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
