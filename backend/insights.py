"""Agregacoes das abas novas: tilt, parcerias, combos de civ, exercito, recordes...

Tudo parte da mesma CTE `base` de stats.py (uma linha por partida elegivel, com o
time do grupo em `grp_team` e o resultado do grupo em `grp_result`). O que depende do
resumo detalhado so enxerga partidas com `game_summaries.status = 'ok'`.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from itertools import combinations

import stats
from stats import Filters, _base_cte, _rate

# Nova sessao de jogo quando o intervalo entre o fim de uma partida e o inicio da
# proxima passa disso.
SESSION_GAP = timedelta(minutes=60)


def _wl(label, wins: int, losses: int, **extra) -> dict:
    row = {"label": label, "wins": wins, "losses": losses,
           "games": wins + losses, "win_rate": _rate(wins, losses)}
    row.update(extra)
    return row


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _json_list(raw) -> list:
    if not raw:
        return []
    try:
        out = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return out if isinstance(out, list) else []


def _with_player(f: Filters, pid: int) -> Filters:
    """Mesmo recorte, exigindo que `pid` esteja no time."""
    return Filters(preset=f.preset, required=list(f.required) + [pid], min_size=f.min_size,
                   date_from=f.date_from, date_to=f.date_to, season=f.season, map_name=f.map_name)


def _our_players(conn, f: Filters, summary_only: bool = False) -> list:
    """(game_id, profile_id, label, civ, result do grupo, started_at) de cada membro no time."""
    cte, params = _base_cte(conn, f)
    join = "JOIN game_summaries s ON s.game_id = b.game_id AND s.status = 'ok'" if summary_only else ""
    return conn.execute(f"""{cte}
        SELECT b.game_id, b.grp_result, b.started_at, b.duration,
               gp.profile_id, gp.civilization,
               COALESCE(p.alias, p.name, CAST(p.profile_id AS TEXT)) AS label
        FROM base b
        {join}
        JOIN game_players gp ON gp.game_id = b.game_id AND gp.team = b.grp_team
        JOIN players p ON p.profile_id = gp.profile_id AND p.tracked = 1
        ORDER BY b.started_at""", params).fetchall()


# ---------------------------------------------------------------- visao geral

WIN_REASONS = {
    "Surrender": "Rendição",
    "Conquest": "Conquista (marcos)",
    "Elimination": "Eliminação",
    "Religious": "Religiosa",
    "Annihilation": "Aniquilação",
    "Wonder": "Maravilha",
    "Culture": "Cultural",
    "Regicide": "Regicídio",
}


def win_reasons(conn, f: Filters) -> list[dict]:
    """Como as partidas terminaram, separado entre vitorias e derrotas do grupo."""
    cte, params = _base_cte(conn, f)
    rows = conn.execute(f"""{cte}
        SELECT s.win_reason AS reason,
               SUM(CASE WHEN b.grp_result = 'win'  THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN b.grp_result = 'loss' THEN 1 ELSE 0 END) AS losses
        FROM base b
        JOIN game_summaries s ON s.game_id = b.game_id AND s.status = 'ok'
        GROUP BY s.win_reason
        ORDER BY wins + losses DESC""", params).fetchall()
    return [_wl(WIN_REASONS.get(r["reason"], r["reason"] or "Não informado"), r["wins"], r["losses"],
                key=r["reason"])
            for r in rows if r["wins"] + r["losses"]]


MMR_BUCKETS = [
    (-10**9, -100, "Inimigo 100+ acima"),
    (-100, -30, "Inimigo 30–100 acima"),
    (-30, 30, "Equilibrado (±30)"),
    (30, 100, "Nós 30–100 acima"),
    (100, 10**9, "Nós 100+ acima"),
]


def mmr_gap(conn, f: Filters) -> dict:
    """Winrate pela diferenca de MMR medio entre o nosso time e o adversario."""
    cte, params = _base_cte(conn, f)
    rows = conn.execute(f"""{cte}
        SELECT b.game_id, b.grp_result,
               AVG(CASE WHEN gp.team =  b.grp_team THEN COALESCE(gp.mmr, gp.rating) END) AS ours,
               AVG(CASE WHEN gp.team <> b.grp_team THEN COALESCE(gp.mmr, gp.rating) END) AS theirs
        FROM base b
        JOIN game_players gp ON gp.game_id = b.game_id
        WHERE b.grp_result IN ('win', 'loss')
        GROUP BY b.game_id""", params).fetchall()
    tally = {label: [0, 0] for _, _, label in MMR_BUCKETS}
    diffs = {"win": [], "loss": []}
    for r in rows:
        if r["ours"] is None or r["theirs"] is None:
            continue
        diff = r["ours"] - r["theirs"]
        diffs[r["grp_result"]].append(diff)
        for lo, hi, label in MMR_BUCKETS:
            if lo <= diff < hi:
                tally[label][0 if r["grp_result"] == "win" else 1] += 1
                break
    avg = lambda xs: round(sum(xs) / len(xs)) if xs else None
    return {
        "buckets": [_wl(label, *tally[label]) for _, _, label in MMR_BUCKETS if sum(tally[label])],
        "avg_diff_win": avg(diffs["win"]),
        "avg_diff_loss": avg(diffs["loss"]),
        "games": len(diffs["win"]) + len(diffs["loss"]),
    }


def tilt(conn, f: Filters) -> dict:
    """Resultado depois de vitoria/derrota e pela posicao da partida na sessao da noite."""
    cte, params = _base_cte(conn, f)
    rows = conn.execute(f"""{cte}
        SELECT started_at, duration, grp_result FROM base
        WHERE grp_result IN ('win', 'loss')
        ORDER BY started_at""", params).fetchall()

    after = {"win": [0, 0], "loss": [0, 0], "loss2": [0, 0]}
    by_index: dict[str, list[int]] = {}
    sessions = 0
    prev_end = None
    index = 0
    streak_loss = 0
    prev_result = None
    for r in rows:
        start = _parse_ts(r["started_at"])
        if start is None:
            continue
        new_session = prev_end is None or start - prev_end > SESSION_GAP
        if new_session:
            sessions += 1
            index = 0
            prev_result = None
            streak_loss = 0
        index += 1
        slot = 0 if r["grp_result"] == "win" else 1
        if prev_result is not None:
            after[prev_result][slot] += 1
            if streak_loss >= 2:
                after["loss2"][slot] += 1
        key = f"{index}ª" if index < 5 else "5ª+"
        by_index.setdefault(key, [0, 0])[slot] += 1

        prev_result = r["grp_result"]
        streak_loss = streak_loss + 1 if prev_result == "loss" else 0
        prev_end = start + timedelta(seconds=r["duration"] or 0)

    order = ["1ª", "2ª", "3ª", "4ª", "5ª+"]
    return {
        "after": [
            _wl("Depois de uma vitória", *after["win"]),
            _wl("Depois de uma derrota", *after["loss"]),
            _wl("Depois de 2+ derrotas seguidas", *after["loss2"]),
        ],
        "by_index": [_wl(f"{k} partida da sessão", *by_index[k]) for k in order if k in by_index],
        "sessions": sessions,
        "games_per_session": round(len(rows) / sessions, 1) if sessions else None,
    }


# ---------------------------------------------------------------- jogadores

def partnerships(conn, f: Filters, min_games: int = 3) -> list[dict]:
    """Winrate de cada dupla de membros no mesmo time, contra a media individual dos dois."""
    games: dict[int, dict] = {}
    for r in _our_players(conn, f):
        g = games.setdefault(r["game_id"], {"result": r["grp_result"], "players": {}})
        g["players"][r["profile_id"]] = r["label"]

    solo: dict[int, list[int]] = {}
    pairs: dict[tuple, dict] = {}
    for g in games.values():
        if g["result"] not in ("win", "loss"):
            continue
        slot = 0 if g["result"] == "win" else 1
        for pid in g["players"]:
            solo.setdefault(pid, [0, 0])[slot] += 1
        for a, b in combinations(sorted(g["players"]), 2):
            entry = pairs.setdefault((a, b), {"names": (g["players"][a], g["players"][b]), "wl": [0, 0]})
            entry["wl"][slot] += 1

    out = []
    for (a, b), entry in pairs.items():
        wins, losses = entry["wl"]
        if wins + losses < min_games:
            continue
        rate = _rate(wins, losses)
        base_a, base_b = _rate(*solo[a]), _rate(*solo[b])
        expected = round((base_a + base_b) / 2, 1) if base_a is not None and base_b is not None else None
        out.append(_wl(" + ".join(entry["names"]), wins, losses,
                       ids=[a, b], names=list(entry["names"]), expected=expected,
                       synergy=round(rate - expected, 1) if expected is not None else None))
    return sorted(out, key=lambda e: -(e["synergy"] if e["synergy"] is not None else -999))


# ---------------------------------------------------------------- civs

def civ_combos(conn, f: Filters, min_games: int = 3) -> list[dict]:
    """Winrate por par de civs jogadas por membros do grupo no mesmo time."""
    games: dict[int, dict] = {}
    for r in _our_players(conn, f):
        g = games.setdefault(r["game_id"], {"result": r["grp_result"], "civs": []})
        if r["civilization"]:
            g["civs"].append(r["civilization"])
    tally: dict[tuple, list[int]] = {}
    for g in games.values():
        if g["result"] not in ("win", "loss"):
            continue
        slot = 0 if g["result"] == "win" else 1
        # Duas pessoas com a mesma civ contam como um par (civ, civ).
        for pair in set(combinations(sorted(g["civs"]), 2)):
            tally.setdefault(pair, [0, 0])[slot] += 1
    out = [_wl(" + ".join(pair), w, l, civs=list(pair))
           for pair, (w, l) in tally.items() if w + l >= min_games]
    return sorted(out, key=lambda e: (-e["games"], -(e["win_rate"] or 0)))


def player_civs(conn, f: Filters, pid: int, min_games: int = 1) -> list[dict]:
    cte, params = _base_cte(conn, f)
    rows = conn.execute(f"""{cte}
        SELECT gp.civilization AS label,
               SUM(CASE WHEN gp.result = 'win'  THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN gp.result = 'loss' THEN 1 ELSE 0 END) AS losses
        FROM base b
        JOIN game_players gp ON gp.game_id = b.game_id AND gp.team = b.grp_team AND gp.profile_id = ?
        GROUP BY gp.civilization
        HAVING wins + losses >= ?
        ORDER BY wins + losses DESC""", params + [pid, min_games]).fetchall()
    return [_wl(r["label"], r["wins"], r["losses"]) for r in rows if r["label"]]


# ---------------------------------------------------------------- economia

RESOURCES = ("food", "wood", "gold", "stone", "oliveoil")


def resources(conn, f: Filters) -> dict:
    """Coletado x gasto por jogador, e quanto o time coleta em vitorias x derrotas."""
    cte, params = _base_cte(conn, f)
    cols = ", ".join(f"AVG(COALESCE(ps.gathered_{r}, 0)) AS g_{r}, AVG(COALESCE(ps.spent_{r}, 0)) AS s_{r}"
                     for r in RESOURCES)
    rows = conn.execute(f"""{cte}
        SELECT p.profile_id, COALESCE(p.alias, p.name, CAST(p.profile_id AS TEXT)) AS label,
               COUNT(*) AS games,
               AVG(ps.gathered_total) AS gathered, AVG(ps.spent_total) AS spent, {cols}
        FROM base b
        JOIN game_summaries s ON s.game_id = b.game_id AND s.status = 'ok'
        JOIN game_players gp ON gp.game_id = b.game_id AND gp.team = b.grp_team
        JOIN players p ON p.profile_id = gp.profile_id AND p.tracked = 1
        JOIN player_summaries ps ON ps.game_id = b.game_id AND ps.profile_id = gp.profile_id
        GROUP BY p.profile_id
        ORDER BY gathered DESC""", params).fetchall()
    players = []
    for r in rows:
        gathered, spent = r["gathered"] or 0, r["spent"] or 0
        players.append({
            "profile_id": r["profile_id"], "label": r["label"], "games": r["games"],
            "gathered": round(gathered), "spent": round(spent),
            "unspent": round(gathered - spent),
            "spent_pct": round(100.0 * spent / gathered, 1) if gathered else None,
            "gathered_by": {res: round(r[f"g_{res}"] or 0) for res in RESOURCES},
            "spent_by": {res: round(r[f"s_{res}"] or 0) for res in RESOURCES},
        })

    by_result = conn.execute(f"""{cte}
        SELECT b.grp_result AS result, AVG(ps.gathered_total) AS gathered, AVG(ps.spent_total) AS spent,
               AVG(b.duration) AS duration
        FROM base b
        JOIN game_summaries s ON s.game_id = b.game_id AND s.status = 'ok'
        JOIN game_players gp ON gp.game_id = b.game_id AND gp.team = b.grp_team
        JOIN players p ON p.profile_id = gp.profile_id AND p.tracked = 1
        JOIN player_summaries ps ON ps.game_id = b.game_id AND ps.profile_id = gp.profile_id
        WHERE b.grp_result IN ('win', 'loss')
        GROUP BY b.grp_result""", params).fetchall()
    result = {}
    for r in by_result:
        minutes = (r["duration"] or 0) / 60.0
        result[r["result"]] = {
            "gathered": round(r["gathered"] or 0),
            "spent": round(r["spent"] or 0),
            "per_minute": round((r["gathered"] or 0) / minutes) if minutes else None,
        }
    return {"players": players, "by_result": result}


CHECKPOINTS = (5, 10, 15)


def early_eco(conn, f: Filters, pid: int | None = None) -> dict:
    """Aldeoes produzidos ate 5/10/15 min (tempo de cada aldeao sai do build order)."""
    cte, params = _base_cte(conn, f)
    extra = "AND gp.profile_id = ?" if pid else ""
    rows = conn.execute(f"""{cte}
        SELECT b.game_id, b.grp_result, b.duration, gp.profile_id,
               COALESCE(p.alias, p.name, CAST(p.profile_id AS TEXT)) AS label,
               us.made_at, us.lost_at
        FROM base b
        JOIN game_summaries s ON s.game_id = b.game_id AND s.status = 'ok'
        JOIN game_players gp ON gp.game_id = b.game_id AND gp.team = b.grp_team {extra}
        JOIN players p ON p.profile_id = gp.profile_id AND p.tracked = 1
        JOIN unit_stats us ON us.game_id = b.game_id AND us.profile_id = gp.profile_id
        WHERE us.unit_key LIKE '%villager%' AND us.made_at IS NOT NULL""",
        params + ([pid] if pid else [])).fetchall()

    # Uma entrada por jogador-partida (pode haver mais de um tipo de aldeao).
    pg: dict[tuple, dict] = {}
    for r in rows:
        e = pg.setdefault((r["game_id"], r["profile_id"]), {
            "label": r["label"], "pid": r["profile_id"], "result": r["grp_result"],
            "duration": r["duration"] or 0, "made": [], "lost": []})
        e["made"].extend(_json_list(r["made_at"]))
        e["lost"].extend(_json_list(r["lost_at"]))

    def tally():
        return {m: [0, 0] for m in CHECKPOINTS} | {"lost10": [0, 0]}

    per_player: dict[int, dict] = {}
    by_result = {"win": tally(), "loss": tally()}
    for e in pg.values():
        pl = per_player.setdefault(e["pid"], {"label": e["label"], "profile_id": e["pid"],
                                              "games": 0, "acc": tally()})
        pl["games"] += 1
        buckets = [pl["acc"]]
        if e["result"] in by_result:
            buckets.append(by_result[e["result"]])
        for m in CHECKPOINTS:
            if e["duration"] < m * 60:
                continue          # partida acabou antes: nao entra na media desse minuto
            n = sum(1 for t in e["made"] if t <= m * 60)
            for acc in buckets:
                acc[m][0] += n
                acc[m][1] += 1
        if e["duration"] >= 600:
            n = sum(1 for t in e["lost"] if t <= 600)
            for acc in buckets:
                acc["lost10"][0] += n
                acc["lost10"][1] += 1

    def avg(acc):
        return {str(k): (round(v[0] / v[1], 1) if v[1] else None) for k, v in acc.items()}

    players = [{"label": p["label"], "profile_id": p["profile_id"], "games": p["games"], **avg(p["acc"])}
               for p in per_player.values()]
    players.sort(key=lambda p: -(p["10"] or 0))
    return {
        "players": players,
        "by_result": {k: avg(v) for k, v in by_result.items()},
        "player_games": len(pg),
    }


FIRST_BUCKETS = [(0, 5, "antes dos 5 min"), (5, 10, "entre 5 e 10 min"),
                 (10, 15, "entre 10 e 15 min"), (15, 10**6, "depois dos 15 min")]


def first_villager(conn, f: Filters) -> dict:
    """Quem perdeu o primeiro aldeao e quando, cruzado com o resultado."""
    cte, params = _base_cte(conn, f)
    rows = conn.execute(f"""{cte}
        SELECT b.game_id, b.grp_result, (gp.team = b.grp_team) AS ours, us.lost_at
        FROM base b
        JOIN game_summaries s ON s.game_id = b.game_id AND s.status = 'ok'
        JOIN game_players gp ON gp.game_id = b.game_id
        JOIN unit_stats us ON us.game_id = b.game_id AND us.profile_id = gp.profile_id
        WHERE us.unit_key LIKE '%villager%' AND us.lost_at IS NOT NULL
          AND b.grp_result IN ('win', 'loss')""", params).fetchall()
    games: dict[int, dict] = {}
    for r in rows:
        times = _json_list(r["lost_at"])
        if not times:
            continue
        g = games.setdefault(r["game_id"], {"result": r["grp_result"], "ours": None, "theirs": None})
        side = "ours" if r["ours"] else "theirs"
        first = min(times)
        g[side] = first if g[side] is None else min(g[side], first)

    who = {"Inimigo perdeu primeiro": [0, 0], "Nós perdemos primeiro": [0, 0]}
    when = {label: [0, 0] for _, _, label in FIRST_BUCKETS}
    ours_first, theirs_first = [], []
    for g in games.values():
        slot = 0 if g["result"] == "win" else 1
        if g["ours"] is not None:
            ours_first.append(g["ours"])
        if g["theirs"] is not None:
            theirs_first.append(g["theirs"])
            minute = g["theirs"] / 60.0
            for lo, hi, label in FIRST_BUCKETS:
                if lo <= minute < hi:
                    when[label][slot] += 1
                    break
        if g["theirs"] is not None and (g["ours"] is None or g["theirs"] < g["ours"]):
            who["Inimigo perdeu primeiro"][slot] += 1
        elif g["ours"] is not None:
            who["Nós perdemos primeiro"][slot] += 1

    avg_min = lambda xs: round(sum(xs) / len(xs) / 60.0, 1) if xs else None
    return {
        "who": [_wl(k, *v) for k, v in who.items() if sum(v)],
        "when": [_wl(label.capitalize(), *when[label]) for _, _, label in FIRST_BUCKETS
                 if sum(when[label])],
        "avg_first_kill_min": avg_min(theirs_first),
        "avg_first_loss_min": avg_min(ours_first),
        "games": len(games),
    }


# ---------------------------------------------------------------- combate

NON_COMBAT = ("cattle", "pilgrim", "trade", "imperial_official", "yatai", "rus_tribute", "emissary",
              "transport", "worker_elephant", "garrisoncommand", "healer_elephant", "mehter")
NAVAL = ("galley", "ship", "galleass", "dromon", "junk", "baghlah", "hulk", "carrack", "dhow", "sampan")
SIEGE_EXTRA = ("cannon", "ozutsu", "fortress", "tower_of_the_sultan", "ballista", "deployed_", "bed_crossbow")
CAVALRY = ("horse", "knight", "lancer", "keshik", "khan", "torguud", "szlachta", "mounted", "rider",
           "camel", "ghulam", "riddari", "cataphract", "mangudai", "mameluke", "elephant", "sipahi",
           "hobelar", "chevalier", "kipchak", "iron_pagoda", "genitour", "sofa", "raider", "firelancer")
RANGED = ("archer", "crossbow", "arbaletrier", "handcannon", "javelin", "yumi", "tanegashima", "musofadi",
          "grenadier", "gunner", "streltsy", "jannisary", "janissary", "yeoman", "longbow", "ranged",
          "ranger", "skirmisher", "zhuge", "eruptor")

ARMY_CLASSES = ["Infantaria", "À distância", "Cavalaria", "Cerco", "Religioso", "Naval"]

UNIT_LABELS = {
    "manatarms": "Man-at-Arms", "handcannon": "Handcannoneer", "jannisary": "Janissary",
    "horsearcher": "Horse Archer", "landskrecht": "Landsknecht",
}


def army_class(key: str, category: str) -> str | None:
    """Classe de combate da unidade; None para o que nao luta (gado, peregrino, comercio)."""
    if category == "eco" or any(w in key for w in NON_COMBAT):
        return None
    if category == "explorador":
        return None
    if any(w in key for w in NAVAL) or key == "fireship":
        return "Naval"
    if category == "cerco" or any(w in key for w in SIEGE_EXTRA):
        return "Cerco"
    if category == "religioso":
        return "Religioso"
    if any(w in key for w in CAVALRY):
        return "Cavalaria"
    if any(w in key for w in RANGED):
        return "À distância"
    return "Infantaria"


def unit_name(key: str) -> str:
    return UNIT_LABELS.get(key, key.replace("_", " ").capitalize())


def army(conn, f: Filters, pid: int | None = None) -> dict:
    """Composicao do exercito por jogador e winrate por estilo dominante."""
    cte, params = _base_cte(conn, f)
    extra = "AND gp.profile_id = ?" if pid else ""
    rows = conn.execute(f"""{cte}
        SELECT b.game_id, b.grp_result, gp.profile_id,
               COALESCE(p.alias, p.name, CAST(p.profile_id AS TEXT)) AS label,
               us.unit_key, us.category, us.made
        FROM base b
        JOIN game_summaries s ON s.game_id = b.game_id AND s.status = 'ok'
        JOIN game_players gp ON gp.game_id = b.game_id AND gp.team = b.grp_team {extra}
        JOIN players p ON p.profile_id = gp.profile_id AND p.tracked = 1
        JOIN unit_stats us ON us.game_id = b.game_id AND us.profile_id = gp.profile_id
        WHERE us.made > 0""", params + ([pid] if pid else [])).fetchall()

    per_game: dict[tuple, dict] = {}
    for r in rows:
        cls = army_class(r["unit_key"], r["category"])
        e = per_game.setdefault((r["game_id"], r["profile_id"]), {
            "label": r["label"], "pid": r["profile_id"], "result": r["grp_result"], "classes": {}})
        if cls is None:
            continue
        e["classes"][cls] = e["classes"].get(cls, 0) + r["made"]
        per_unit = e.setdefault("units", {})
        per_unit[r["unit_key"]] = per_unit.get(r["unit_key"], 0) + r["made"]

    players: dict[int, dict] = {}
    styles: dict[str, list[int]] = {}
    for e in per_game.values():
        pl = players.setdefault(e["pid"], {"label": e["label"], "profile_id": e["pid"], "games": 0,
                                           "classes": {}, "units": {}})
        pl["games"] += 1
        total = sum(e["classes"].values())
        for cls, n in e["classes"].items():
            pl["classes"][cls] = pl["classes"].get(cls, 0) + n
        for key, n in e.get("units", {}).items():
            pl["units"][key] = pl["units"].get(key, 0) + n
        if not total or e["result"] not in ("win", "loss"):
            continue
        top_cls, top_n = max(e["classes"].items(), key=lambda kv: kv[1])
        style = top_cls if top_n / total >= 0.5 else "Misto"
        styles.setdefault(style, [0, 0])[0 if e["result"] == "win" else 1] += 1

    out_players = []
    for pl in players.values():
        total = sum(pl["classes"].values())
        games = pl["games"] or 1
        top = sorted(pl["units"].items(), key=lambda kv: -kv[1])[:5]
        out_players.append({
            "label": pl["label"], "profile_id": pl["profile_id"], "games": pl["games"],
            "military_per_game": round(total / games, 1),
            "shares": {c: round(100.0 * pl["classes"].get(c, 0) / total, 1) if total else 0
                       for c in ARMY_CLASSES},
            "top_units": [{"key": k, "label": unit_name(k), "per_game": round(n / games, 1)} for k, n in top],
        })
    out_players.sort(key=lambda p: -p["games"])
    style_order = ARMY_CLASSES + ["Misto"]
    return {
        "classes": ARMY_CLASSES,
        "players": out_players,
        "styles": [_wl(s, *styles[s]) for s in style_order if s in styles],
    }


# ---------------------------------------------------------------- recordes

RECORD_METRICS = [
    ("score_total", "Maior pontuação"),
    ("kills", "Mais abates"),
    ("gathered_total", "Mais recursos coletados"),
    ("units_made", "Mais unidades produzidas"),
    ("razed", "Mais construções arrasadas"),
    ("apm", "Maior APM"),
]


def records(conn, f: Filters, top: int = 3) -> list[dict]:
    """Top 3 de cada metrica numa partida so, com link para o detalhe."""
    cte, params = _base_cte(conn, f)
    out = []
    for key, title in RECORD_METRICS:
        rows = conn.execute(f"""{cte}
            SELECT b.game_id, b.started_at, b.map, b.grp_result,
                   COALESCE(p.alias, p.name, CAST(p.profile_id AS TEXT)) AS who,
                   p.profile_id, ps.{key} AS value
            FROM base b
            JOIN game_summaries s ON s.game_id = b.game_id AND s.status = 'ok'
            JOIN game_players gp ON gp.game_id = b.game_id AND gp.team = b.grp_team
            JOIN players p ON p.profile_id = gp.profile_id AND p.tracked = 1
            JOIN player_summaries ps ON ps.game_id = b.game_id AND ps.profile_id = gp.profile_id
            WHERE ps.{key} IS NOT NULL
            ORDER BY ps.{key} DESC LIMIT ?""", params + [top]).fetchall()
        out.append({"key": key, "title": title, "entries": [dict(r) for r in rows]})

    # Recordes de time / de partida.
    raid = conn.execute(f"""{cte}
        SELECT b.game_id, b.started_at, b.map, b.grp_result, NULL AS who, NULL AS profile_id,
               SUM(us.lost) AS value
        FROM base b
        JOIN game_summaries s ON s.game_id = b.game_id AND s.status = 'ok'
        JOIN game_players gp ON gp.game_id = b.game_id AND gp.team <> b.grp_team
        JOIN unit_stats us ON us.game_id = b.game_id AND us.profile_id = gp.profile_id
        WHERE us.unit_key LIKE '%villager%'
        GROUP BY b.game_id
        ORDER BY value DESC LIMIT ?""", params + [top]).fetchall()
    out.append({"key": "villager_raid", "title": "Mais aldeões inimigos mortos", "entries": [dict(r) for r in raid]})

    rating = conn.execute(f"""{cte}
        SELECT b.game_id, b.started_at, b.map, b.grp_result,
               COALESCE(p.alias, p.name, CAST(p.profile_id AS TEXT)) AS who,
               p.profile_id, gp.rating_diff AS value
        FROM base b
        JOIN game_players gp ON gp.game_id = b.game_id AND gp.team = b.grp_team
        JOIN players p ON p.profile_id = gp.profile_id AND p.tracked = 1
        WHERE gp.rating_diff IS NOT NULL
        ORDER BY gp.rating_diff DESC LIMIT ?""", params + [top]).fetchall()
    out.append({"key": "rating_diff", "title": "Maior ganho de rating", "entries": [dict(r) for r in rating]})

    for title, order, cond in (("Partida mais longa", "DESC", "1=1"),
                               ("Vitória mais rápida", "ASC", "b.grp_result = 'win'")):
        rows = conn.execute(f"""{cte}
            SELECT b.game_id, b.started_at, b.map, b.grp_result, NULL AS who, NULL AS profile_id,
                   ROUND(b.duration / 60.0, 1) AS value
            FROM base b WHERE b.duration IS NOT NULL AND b.duration > 0 AND {cond}
            ORDER BY b.duration {order} LIMIT ?""", params + [top]).fetchall()
        out.append({"key": "duration", "title": title, "unit": "min", "entries": [dict(r) for r in rows]})
    return [r for r in out if r["entries"]]


# ---------------------------------------------------------------- pagina do jogador

def player_profile(conn, f: Filters, pid: int, min_games: int = 3) -> dict | None:
    row = conn.execute(
        "SELECT profile_id, COALESCE(alias, name) AS label, name, country FROM players "
        "WHERE profile_id = ? AND tracked = 1", (pid,)).fetchone()
    if row is None:
        return None
    fp = _with_player(f, pid)
    me = next((p for p in stats.by_player(conn, fp) if p["profile_id"] == pid), None)
    cmp = stats.comparison(conn, fp, "avg")
    partners = [p for p in partnerships(conn, f, min_games=1) if pid in p["ids"]]
    for p in partners:
        p["partner"] = p["names"][1] if p["ids"][0] == pid else p["names"][0]
        p["partner_id"] = p["ids"][1] if p["ids"][0] == pid else p["ids"][0]
    return {
        "player": dict(row),
        "summary": stats.summary(conn, fp),
        "individual": me,
        "civs": player_civs(conn, fp, pid),
        "maps": stats.by_map(conn, fp, min_games),
        "partners": partners,
        "comparison": {"groups": cmp["groups"],
                       "row": next((r for r in cmp["rows"] if r["profile_id"] == pid), None),
                       "team_avg": _team_avg(cmp["rows"])},
        "army": army(conn, fp, pid),
        "early_eco": early_eco(conn, fp, pid),
    }


def _team_avg(rows: list[dict]) -> dict:
    """Media simples dos membros, para comparar o jogador com o grupo."""
    out: dict[str, float] = {}
    if not rows:
        return out
    for key in rows[0]["values"]:
        vals = [r["values"][key] for r in rows if r["values"].get(key) is not None]
        out[key] = round(sum(vals) / len(vals), 1) if vals else None
    return out
