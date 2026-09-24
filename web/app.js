/* Agezada — frontend. Uma aba por vez: cada aba pede só /api/view/<aba>; filtros ficam na URL. */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => [...document.querySelectorAll(sel)];
  const css = (name) => getComputedStyle(document.body).getPropertyValue(name).trim();

  const COLORS = () => ({
    win: css("--win"),
    loss: css("--loss"),
    accent: css("--accent"),
    text: css("--text-primary"),
    muted: css("--text-muted"),
    grid: css("--border"),
    surface: css("--surface-1"),
  });

  // Abaixo disso o winrate aparece apagado (amostra pequena).
  const SMALL_SAMPLE = 10;
  // Linhas visíveis antes do "ver todos".
  const LIST_LIMIT = 12;

  const charts = {};
  const state = { view: "overview", pid: null, offset: 0, limit: 25, cache: new Map(), token: 0, facets: null };

  // ---------- utilidades ----------
  /** Todo texto vindo da API passa por aqui antes de entrar em innerHTML. */
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const pct = (v) => (v === null || v === undefined ? "—" : `${v.toFixed(1)}%`);
  const CIVS = {
    abbasid_dynasty: "Abássida", ayyubids: "Aiúbidas", byzantines: "Bizantinos", chinese: "Chineses",
    delhi_sultanate: "Délhi", english: "Ingleses", french: "Franceses", golden_horde: "Horda Dourada",
    holy_roman_empire: "SIRG", house_of_lancaster: "Lancaster", japanese: "Japoneses",
    jeanne_darc: "Joana d'Arc", jin_dynasty: "Jin", knights_templar: "Templários",
    macedonian_dynasty: "Macedônios", malians: "Malineses", mongols: "Mongóis",
    order_of_the_dragon: "Ord. do Dragão", ottomans: "Otomanos", rus: "Rus",
    sengoku_daimyo: "Sengoku", tughlaq_dynasty: "Tughlaq", zhu_xis_legacy: "Zhu Xi",
  };
  const civLabel = (c) => CIVS[c] || (c || "?").replace(/_/g, " ").replace(/\b\w/g, (m) => m.toUpperCase());
  const kindLabel = (k) => (k || "?").replace("rm_", "Ranqueado ").replace("qm_", "Quick ").replace(/_/g, " ");
  const dateLabel = (iso) => (iso ? new Date(iso).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", year: "2-digit" }) : "—");
  const monthLabel = (ym) => {
    const [y, m] = ym.split("-");
    return new Date(Number(y), Number(m) - 1, 1).toLocaleDateString("pt-BR", { month: "short", year: "2-digit" });
  };
  const numFmt = (v, decimals) => {
    if (v === null || v === undefined) return "—";
    if (decimals !== undefined) return v.toLocaleString("pt-BR", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    return v.toLocaleString("pt-BR", { maximumFractionDigits: 1 });
  };
  const signed = (v, suffix = "") => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${numFmt(v)}${suffix}`);
  const WIN_REASONS = {
    Surrender: "rendição", Conquest: "conquista", Elimination: "eliminação", Religious: "religiosa",
    Annihilation: "aniquilação", Wonder: "maravilha",
  };

  // ---------- ícones ----------
  const flag = (civ) => `<img class="flag" src="/img/civs/${CIVS[civ] ? civ : "unknown"}.png" alt="" width="26" height="14">`;
  const civCell = (civ) => `<span class="ico-label">${flag(civ)}${esc(civLabel(civ))}</span>`;
  /** Não há arte pública dos mapas: selo com as iniciais, cor estável por nome. */
  function mapIcon(name) {
    const s = String(name || "?");
    let h = 0;
    for (const ch of s) h = (h * 31 + ch.codePointAt(0)) % 360;
    const initials = s.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
    return `<span class="map-ico" style="--h:${h}" aria-hidden="true">${esc(initials)}</span>`;
  }
  const mapCell = (name) => `<span class="ico-label">${mapIcon(name)}${esc(name || "—")}</span>`;
  const playerLink = (pid, label) => `<a class="us" href="#/jogador/${Number(pid)}">${esc(label)}</a>`;

  // ---------- filtros <-> URL ----------
  const DEFAULTS = { preset: "tg", min_size: "2", min_games: "3" };

  /** Estado dos filtros como aparece na URL (só o que foge do padrão). */
  function urlParams() {
    const p = new URLSearchParams();
    const set = (k, v) => { if (v && v !== DEFAULTS[k]) p.set(k, v); };
    set("preset", $("#f-preset").value);
    set("season", $("#f-season").value);
    set("map", $("#f-map").value);
    set("min_size", $("#f-minsize").value);
    set("players", $$("#f-players input:checked").map((i) => i.value).join(","));
    set("from", $("#f-from").value);
    set("to", $("#f-to").value);
    set("min_games", $("#f-mingames").value);
    return p;
  }

  /** Parâmetros da API (a data final vai até o fim do dia). */
  function apiParams() {
    const p = urlParams();
    if (p.get("to")) p.set("to", `${p.get("to")}T23:59:59Z`);
    return p;
  }

  function applyUrl() {
    const p = new URLSearchParams(location.search);
    const pick = (sel, key) => {
      const el = $(sel);
      const v = p.get(key) ?? DEFAULTS[key] ?? "";
      if (el.tagName !== "SELECT" || [...el.options].some((o) => o.value === v)) el.value = v;
    };
    pick("#f-preset", "preset");
    pick("#f-season", "season");
    pick("#f-map", "map");
    pick("#f-minsize", "min_size");
    pick("#f-from", "from");
    pick("#f-to", "to");
    pick("#f-mingames", "min_games");
    const chosen = new Set((p.get("players") || "").split(",").filter(Boolean));
    $$("#f-players input").forEach((i) => { i.checked = chosen.has(i.value); });
    if (p.get("from") || p.get("to") || (p.get("min_games") && p.get("min_games") !== DEFAULTS.min_games)) {
      $(".more-filters").open = true;
    }
  }

  function writeUrl() {
    const qs = urlParams().toString();
    history.replaceState(null, "", `${location.pathname}${qs ? `?${qs}` : ""}${location.hash}`);
  }

  async function getJSON(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  }

  async function fetchView(view, extra = {}) {
    const params = apiParams();
    Object.entries(extra).forEach(([k, v]) => params.set(k, v));
    const key = `${view}?${params}`;
    if (!state.cache.has(key)) {
      state.cache.set(key, getJSON(`/api/view/${view}?${params}`).catch((err) => {
        state.cache.delete(key);
        throw err;
      }));
    }
    return state.cache.get(key);
  }

  // ---------- tabelas ----------
  /** <td> numérico com valor cru em data-v (é o que a ordenação usa). */
  const numTd = (v, text, cls = "") => `<td class="num ${cls}" data-v="${v ?? ""}">${text ?? numFmt(v)}</td>`;
  const fewAttrs = (games) => (games < SMALL_SAMPLE ? ' class="few" title="Amostra pequena: menos de 10 partidas"' : "");
  const emptyRow = (cols, text = "Sem dados nesse filtro.") => `<tr class="empty-row"><td colspan="${cols}" class="empty">${text}</td></tr>`;
  const moreBtn = (n) => (n > LIST_LIMIT ? `<button type="button" class="more-btn" data-more>ver todos (${n})</button>` : "");

  function wrCell(rate) {
    if (rate === null || rate === undefined) return '<td data-v="">—</td>';
    const tone = rate >= 50 ? "win" : "loss";
    return `<td class="wr-cell" data-v="${rate}"><div class="wr-in">
      <span class="wrbar"><span class="${tone}" style="width:${rate}%"></span><i></i></span>
      <b>${pct(rate)}</b></div></td>`;
  }

  /**
   * Tabela de winrate padrão: item | partidas | winrate (barra) | V–D | colunas extras.
   * `label(row)` devolve HTML já escapado.
   */
  function wrTable(rows, { head = "Item", label = (r) => esc(r.label), extra = [], limit = LIST_LIMIT } = {}) {
    const clip = rows.length > limit ? " clip" : "";
    const cols = 4 + extra.length;
    const body = rows.map((r) => `
      <tr${fewAttrs(r.games)}>
        <td data-v="${esc(r.sortLabel ?? r.label)}">${label(r)}</td>
        ${numTd(r.games)}
        ${wrCell(r.win_rate)}
        <td class="num muted" data-v="${r.wins}">${r.wins}–${r.losses}</td>
        ${extra.map((c) => c.cell(r)).join("")}
      </tr>`).join("") || emptyRow(cols);
    return `<table class="sortable wr${clip}">
      <thead><tr><th>${head}</th><th class="num">Partidas</th><th>Winrate</th><th class="num">V–D</th>${extra.map((c) => `<th class="num">${c.head}</th>`).join("")}</tr></thead>
      <tbody>${body}</tbody></table>${moreBtn(rows.length)}`;
  }

  function sortTable(th) {
    const table = th.closest("table");
    const tbody = table.tBodies[0];
    const idx = th.cellIndex;
    const desc = th.getAttribute("aria-sort") !== "descending";
    table.querySelectorAll("th[aria-sort]").forEach((h) => h.removeAttribute("aria-sort"));
    th.setAttribute("aria-sort", desc ? "descending" : "ascending");
    const keyOf = (row) => {
      const td = row.cells[idx];
      if (!td) return -Infinity;
      if (td.dataset.v !== undefined) {
        if (td.dataset.v === "") return -Infinity;
        const n = Number(td.dataset.v);
        return Number.isNaN(n) ? td.dataset.v.toLowerCase() : n;
      }
      return td.textContent.trim().toLowerCase();
    };
    const rows = [...tbody.rows].filter((r) => !r.classList.contains("empty-row"));
    rows.sort((a, b) => {
      const ka = keyOf(a);
      const kb = keyOf(b);
      const cmp = typeof ka === "number" && typeof kb === "number" ? ka - kb : String(ka).localeCompare(String(kb), "pt-BR");
      return desc ? -cmp : cmp;
    });
    rows.forEach((r) => tbody.appendChild(r));
  }

  // ---------- gráficos (só a linha do tempo usa Chart.js) ----------
  Chart.defaults.font.family = "ui-sans-serif, system-ui, 'Segoe UI', Roboto, sans-serif";
  Chart.defaults.animation.duration = 250;

  function destroy(id) {
    if (charts[id]) { charts[id].destroy(); delete charts[id]; }
  }

  function emptyState(canvasId, show) {
    const el = document.getElementById(canvasId);
    const box = el.parentElement;
    let msg = box.querySelector(".empty");
    if (show) {
      if (!msg) {
        msg = document.createElement("p");
        msg.className = "empty";
        box.appendChild(msg);
      }
      msg.textContent = "Sem partidas nesse filtro.";
      el.style.display = "none";
    } else {
      if (msg) msg.remove();
      el.style.display = "";
    }
  }

  const tooltipStyle = (c) => ({
    backgroundColor: c.surface, borderColor: c.grid, borderWidth: 1, titleColor: c.text, bodyColor: c.text, padding: 10,
  });

  function timelineChart(rows) {
    destroy("c-timeline");
    const c = COLORS();
    emptyState("c-timeline", !rows.length);
    if (!rows.length) return;
    charts["c-timeline"] = new Chart(document.getElementById("c-timeline"), {
      type: "bar",
      data: {
        labels: rows.map((r) => monthLabel(r.label)),
        datasets: [
          { label: "Vitórias", data: rows.map((r) => r.wins), backgroundColor: c.win, borderRadius: 4, borderSkipped: "start", borderWidth: 2, borderColor: c.surface },
          { label: "Derrotas", data: rows.map((r) => r.losses), backgroundColor: c.loss, borderRadius: 4, borderSkipped: "start", borderWidth: 2, borderColor: c.surface },
        ],
      },
      options: {
        maintainAspectRatio: false,
        scales: {
          x: { stacked: true, grid: { display: false }, border: { display: false }, ticks: { color: c.muted } },
          y: { stacked: true, grid: { color: c.grid, drawTicks: false }, border: { display: false }, ticks: { color: c.muted, precision: 0 }, title: { display: true, text: "partidas", color: c.muted } },
        },
        plugins: {
          legend: { labels: { color: c.text, boxWidth: 12, boxHeight: 12, usePointStyle: true, pointStyle: "rectRounded" } },
          tooltip: { ...tooltipStyle(c), callbacks: { afterBody: (items) => `Winrate do mês: ${pct(rows[items[0].dataIndex].win_rate)}` } },
        },
      },
    });
  }

  function trendChart(rows) {
    destroy("c-trend");
    const c = COLORS();
    emptyState("c-trend", !rows.length);
    if (!rows.length) return;
    charts["c-trend"] = new Chart(document.getElementById("c-trend"), {
      type: "line",
      data: {
        labels: rows.map((r) => monthLabel(r.label)),
        datasets: [{
          label: "Winrate acumulado",
          data: rows.map((r) => r.cumulative_win_rate),
          borderColor: c.accent, backgroundColor: c.accent,
          borderWidth: 2, pointRadius: 4, pointHoverRadius: 6, tension: 0.25,
          pointBorderColor: c.surface, pointBorderWidth: 2,
        }],
      },
      options: {
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: { grid: { display: false }, border: { display: false }, ticks: { color: c.muted } },
          y: { grid: { color: c.grid, drawTicks: false }, border: { display: false },
               ticks: { color: c.muted, callback: (v) => `${v}%` }, suggestedMin: 30, suggestedMax: 70 },
        },
        plugins: {
          legend: { display: false },
          tooltip: { ...tooltipStyle(c), callbacks: { label: (ctx) => `Winrate acumulado: ${pct(ctx.parsed.y)}` } },
        },
      },
    });
  }

  // ---------- Visão geral ----------
  function renderOverview(d) {
    const s = d.summary;
    $("#t-winrate").textContent = pct(s.win_rate);
    $("#t-wl").textContent = `${s.wins} vitórias · ${s.losses} derrotas`;
    $("#t-games").textContent = s.games;
    $("#t-period").textContent = s.first_game ? `${dateLabel(s.first_game)} → ${dateLabel(s.last_game)}` : "—";
    $("#t-streak").textContent = s.current_streak > 0 ? `${s.current_streak}V` : s.current_streak < 0 ? `${-s.current_streak}D` : "—";
    $("#t-streak-best").textContent = `melhor ${s.best_win_streak}V · pior ${s.worst_loss_streak}D`;
    $("#t-duration").textContent = `${s.avg_duration_min} min`;
    $("#t-groupsize").textContent = `média de ${s.avg_group_size} de nós por partida`;

    timelineChart(d.timeline);
    trendChart(d.timeline);
    $("#tbl-timeline").innerHTML = wrTable(d.timeline.map((r) => ({ ...r, sortLabel: r.label })),
      { head: "Mês", label: (r) => esc(monthLabel(r.label)), limit: 999 });

    const mmr = d.mmr_gap;
    $("#ov-mmr").innerHTML = wrTable(mmr.buckets, { head: "Diferença de MMR", limit: 999 });
    $("#ov-mmr-note").textContent = mmr.games
      ? `Nas vitórias o nosso time tinha em média ${signed(mmr.avg_diff_win)} de MMR sobre o inimigo; nas derrotas, ${signed(mmr.avg_diff_loss)}. Base: ${mmr.games} partidas com MMR dos dois lados.`
      : "";
    $("#ov-reasons").innerHTML = wrTable(d.win_reasons, { head: "Fim da partida", limit: 999 });

    const t = d.tilt;
    $("#ov-tilt-lead").textContent = t.sessions
      ? `Resultado da partida seguinte, dentro da mesma sessão (${t.sessions} sessões, ${numFmt(t.games_per_session)} partidas por sessão em média; mais de 1 h de intervalo abre sessão nova).`
      : "Resultado da partida seguinte, dentro da mesma sessão.";
    $("#ov-tilt-after").innerHTML = wrTable(t.after, { head: "Situação", limit: 999 });
    $("#ov-tilt-index").innerHTML = wrTable(t.by_index, { head: "Partida", limit: 999 });

    $("#ov-kind").innerHTML = wrTable(d.by_kind, { head: "Modo", label: (r) => esc(kindLabel(r.label)) });
    $("#ov-duration").innerHTML = wrTable(d.by_duration.map((r, i) => ({ ...r, sortLabel: String(i) })),
      { head: "Duração", limit: 999 });
  }

  // ---------- Jogadores ----------
  function playersTable(rows) {
    const body = rows.map((r) => `
      <tr${fewAttrs(r.games)}>
        <td data-v="${esc(r.label)}">${playerLink(r.profile_id, r.label)}</td>
        ${numTd(r.games)}
        ${wrCell(r.win_rate)}
        <td class="num muted" data-v="${r.wins}">${r.wins}–${r.losses}</td>
        ${numTd(r.avg_rating, r.avg_rating ?? "—")}
        ${numTd(r.rating_delta, signed(r.rating_delta ?? 0), r.rating_delta > 0 ? "pos" : r.rating_delta < 0 ? "neg" : "")}
      </tr>`).join("") || emptyRow(6);
    return `<table class="sortable wr"><thead><tr><th>Jogador</th><th class="num">Partidas</th><th>Winrate</th><th class="num">V–D</th><th class="num">Rating médio</th><th class="num">Δ rating</th></tr></thead><tbody>${body}</tbody></table>`;
  }

  const synergyTd = (v) => numTd(v, signed(v, " pp"), v > 0 ? "pos" : v < 0 ? "neg" : "");

  function renderPlayers(d) {
    $("#pl-players").innerHTML = playersTable(d.by_player);
    $("#pl-pairs").innerHTML = wrTable(d.partnerships, {
      head: "Dupla",
      label: (r) => `${playerLink(r.ids[0], r.names[0])} + ${playerLink(r.ids[1], r.names[1])}`,
      extra: [
        { head: "Esperado", cell: (r) => numTd(r.expected, pct(r.expected), "muted") },
        { head: "Sinergia", cell: (r) => synergyTd(r.synergy) },
      ],
    });
    $("#pl-lineups").innerHTML = wrTable(d.by_lineup, { head: "Formação" });
    fillComparison(d.comparison);
  }

  function fillComparison(cmp) {
    const table = $("#tbl-comparison");
    const cols = cmp.groups.flatMap((g) => g.columns);
    const rows = cmp.rows || [];

    if (!rows.length) {
      table.tHead.innerHTML = "";
      table.tBodies[0].innerHTML = '<tr class="empty-row"><td class="empty">Sem resumo detalhado nesse filtro. Rode <code>python backend/sync.py --summaries</code>.</td></tr>';
    } else {
      // Escala por coluna: a barra mede o valor contra o maior da própria coluna.
      const max = {};
      cols.forEach((c) => { max[c.key] = Math.max(0, ...rows.map((r) => Number(r.values[c.key]) || 0)); });
      const groupRow = `<tr class="groups"><th class="group" colspan="2"></th>${cmp.groups
        .map((g) => `<th class="group" colspan="${g.columns.length}">${esc(g.label)}</th>`).join("")}</tr>`;
      const colRow = `<tr class="cols"><th>Jogador</th><th class="num">Part.</th>${cols
        .map((c) => `<th class="num">${esc(c.label)}</th>`).join("")}</tr>`;
      table.tHead.innerHTML = groupRow + colRow;
      table.tBodies[0].innerHTML = rows.map((r) => {
        const cells = cols.map((c) => {
          const v = r.values[c.key];
          const width = max[c.key] > 0 && v ? Math.max(2, (Number(v) / max[c.key]) * 100) : 0;
          return `<td class="cell tone-${esc(c.tone)}" data-v="${v ?? ""}"><span class="bar" style="width:${width.toFixed(1)}%"></span><span class="val">${numFmt(v, c.decimals)}</span></td>`;
        }).join("");
        return `<tr><td class="name" data-v="${esc(r.label)}">${playerLink(r.profile_id, r.label)}</td><td class="games num" data-v="${r.games}">${r.games}</td>${cells}</tr>`;
      }).join("");
    }

    const cov = cmp.coverage;
    const falta = cov.games - cov.with_summary;
    $("#cmp-note").textContent =
      `${cmp.mode === "sum" ? "Total somado" : "Média por partida"} sobre ${cov.with_summary} de ${cov.games} partidas do filtro.` +
      (falta > 0 ? ` ${falta} ainda sem resumo detalhado (partidas antigas costumam não ter).` : "");
  }

  // ---------- Civs & Mapas ----------
  function renderCivs(d) {
    $("#cv-civ").innerHTML = wrTable(d.by_civ.map((r) => ({ ...r, sortLabel: civLabel(r.label) })),
      { head: "Civ", label: (r) => civCell(r.label) });
    $("#cv-vsciv").innerHTML = wrTable(d.vs_civ.map((r) => ({ ...r, sortLabel: civLabel(r.label) })),
      { head: "Civ inimiga", label: (r) => civCell(r.label) });
    $("#cv-combos").innerHTML = wrTable(d.civ_combos.map((r) => ({ ...r, sortLabel: r.civs.map(civLabel).join(" + ") })), {
      head: "Combinação",
      label: (r) => `<span class="ico-label">${flag(r.civs[0])}${flag(r.civs[1])}${esc(r.civs.map(civLabel).join(" + "))}</span>`,
    });
    $("#cv-maps").innerHTML = wrTable(d.by_map, { head: "Mapa", label: (r) => mapCell(r.label) });
  }

  // ---------- Economia ----------
  const RES = [
    ["food", "Comida"], ["wood", "Madeira"], ["gold", "Ouro"], ["stone", "Pedra"], ["oliveoil", "Azeite"],
  ];

  function stackBar(parts, total) {
    if (!total) return '<span class="muted">—</span>';
    return `<span class="stack" role="img" aria-label="${esc(parts.map((p) => `${p.label} ${Math.round((100 * p.value) / total)}%`).join(", "))}">${parts
      .filter((p) => p.value > 0)
      .map((p) => `<i class="${p.cls}" style="width:${((100 * p.value) / total).toFixed(1)}%" title="${esc(p.label)}: ${numFmt(p.value)} (${Math.round((100 * p.value) / total)}%)"></i>`)
      .join("")}</span>`;
  }

  function tile(label, value, foot, cls = "") {
    return `<div class="tile ${cls}"><div class="tile-label">${esc(label)}</div><div class="tile-value">${esc(value)}</div><div class="tile-foot">${esc(foot)}</div></div>`;
  }

  function earlyTable(e, { withResult = true } = {}) {
    if (!e.player_games) {
      return '<p class="empty">Sem o horário de produção dos aldeões ainda. Rode <code>python backend/sync.py --summaries --redo-all --summaries-limit 999</code> para rebaixar os resumos.</p>';
    }
    const row = (label, r, games) => `
      <tr${games !== undefined ? fewAttrs(games) : ""}>
        <td data-v="${esc(r.label ?? label)}">${label}</td>
        ${games !== undefined ? numTd(games) : "<td></td>"}
        ${numTd(r["5"])}${numTd(r["10"])}${numTd(r["15"])}${numTd(r.lost10, undefined, "neg")}
      </tr>`;
    const body = e.players.map((p) => row(playerLink(p.profile_id, p.label), p, p.games)).join("");
    const foot = withResult
      ? `<tfoot>${row("Média nas vitórias", e.by_result.win)}${row("Média nas derrotas", e.by_result.loss)}</tfoot>` : "";
    return `<table class="sortable"><thead><tr><th>Jogador</th><th class="num">Partidas</th><th class="num">5 min</th><th class="num">10 min</th><th class="num">15 min</th><th class="num" title="aldeões perdidos até os 10 min">Perd. 10'</th></tr></thead><tbody>${body}</tbody>${foot}</table>`;
  }

  function renderEconomy(d) {
    const res = d.resources;
    const fv = d.first_villager;
    const avg = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
    const gathered = avg(res.players.map((p) => p.gathered));
    const spentPct = avg(res.players.map((p) => p.spent_pct).filter((v) => v !== null));
    $("#ec-tiles").innerHTML = [
      tile("Coleta média por jogador", gathered === null ? "—" : numFmt(Math.round(gathered)), "recursos por partida"),
      tile("Aproveitamento", spentPct === null ? "—" : pct(spentPct), "do que coletamos vira gasto"),
      tile("1º aldeão inimigo cai", fv.avg_first_kill_min === null ? "—" : `${numFmt(fv.avg_first_kill_min)} min`, "em média"),
      tile("Nosso 1º aldeão cai", fv.avg_first_loss_min === null ? "—" : `${numFmt(fv.avg_first_loss_min)} min`, "em média"),
    ].join("");

    const body = res.players.map((p) => `
      <tr${fewAttrs(p.games)}>
        <td data-v="${esc(p.label)}">${playerLink(p.profile_id, p.label)}</td>
        ${numTd(p.games)}${numTd(p.gathered)}${numTd(p.spent)}${numTd(p.unspent)}
        ${numTd(p.spent_pct, p.spent_pct === null ? "—" : pct(p.spent_pct))}
        <td>${stackBar(RES.map(([k, label]) => ({ label, value: p.gathered_by[k], cls: `r-${k}` })), p.gathered)}</td>
      </tr>`).join("") || emptyRow(7);
    $("#ec-resources").innerHTML = `<table class="sortable"><thead><tr><th>Jogador</th><th class="num">Partidas</th><th class="num">Coletado</th><th class="num">Gasto</th><th class="num">Não gasto</th><th class="num">% gasto</th><th>Mix de coleta</th></tr></thead><tbody>${body}</tbody></table>
      <div class="legend">${RES.map(([k, label]) => `<span><i class="r-${k}"></i>${label}</span>`).join("")}</div>`;

    const br = res.by_result;
    const brRow = (label, r) => (r ? `<tr><td>${label}</td>${numTd(r.gathered)}${numTd(r.spent)}${numTd(r.per_minute)}</tr>` : "");
    $("#ec-byresult").innerHTML = `<table><thead><tr><th>Resultado</th><th class="num">Coletado</th><th class="num">Gasto</th><th class="num">Coleta por minuto</th></tr></thead><tbody>${
      brRow("Vitórias", br.win)}${brRow("Derrotas", br.loss)}</tbody></table>`;

    $("#ec-early").innerHTML = earlyTable(d.early_eco);
    $("#ec-first-who").innerHTML = wrTable(fv.who, { head: "1º aldeão morto", limit: 999 });
    $("#ec-first-when").innerHTML = wrTable(fv.when, { head: "1º inimigo caiu", limit: 999 });

    fillEcoKills(d.eco_kills);
  }

  function fillEcoKills(eco) {
    const t = eco.totals;
    $("#eco-tiles").innerHTML = [
      tile("Eliminadas pelo grupo", t.eliminated.toLocaleString("pt-BR"), `${numFmt(t.eliminated_per_game)} por partida`),
      tile("Perdidas pelo grupo", t.lost.toLocaleString("pt-BR"), `${numFmt(t.lost_per_game)} por partida`),
      tile("Saldo", `${t.balance > 0 ? "+" : ""}${t.balance.toLocaleString("pt-BR")}`, t.balance >= 0 ? "matamos mais economia" : "perdemos mais economia"),
    ].join("");

    const games = eco.coverage.with_summary || 0;
    $("#eco-units").innerHTML = `<table class="sortable"><thead><tr><th>Unidade</th><th class="num">Eliminadas</th><th class="num">Por partida</th></tr></thead><tbody>${
      (eco.by_unit || []).map((u) => `<tr><td>${esc(u.label)}</td>${numTd(u.total)}${numTd(games ? u.total / games : null)}</tr>`).join("") || emptyRow(3)}</tbody></table>`;

    $("#eco-players").innerHTML = `<table class="sortable"><thead><tr><th>Jogador</th><th class="num">Partidas</th><th class="num">Produzidas</th><th class="num">Perdidas</th><th class="num">Perdidas/partida</th><th class="num">Sobreviveram</th><th>Detalhe</th></tr></thead><tbody>${
      (eco.by_player || []).map((p) => `
        <tr>
          <td data-v="${esc(p.label)}">${playerLink(p.profile_id, p.label)}</td>
          ${numTd(p.games)}${numTd(p.made)}${numTd(p.lost)}${numTd(p.lost_per_game)}
          ${numTd(p.survival, p.survival === null ? "—" : `${numFmt(p.survival)}%`)}
          <td class="detail">${esc(Object.entries(p.by_unit).map(([k, v]) => `${k}: ${v}`).join(" · ") || "—")}</td>
        </tr>`).join("") || emptyRow(7)}</tbody></table>`;

    $("#eco-civs").innerHTML = `<table class="sortable"><thead><tr><th>Civ inimiga</th><th class="num">Partidas</th><th class="num">Eliminadas</th><th class="num">Por partida</th></tr></thead><tbody>${
      (eco.by_enemy_civ || []).map((c) => `
        <tr${fewAttrs(c.games)}><td data-v="${esc(civLabel(c.label))}">${civCell(c.label)}</td>${numTd(c.games)}${numTd(c.total)}${numTd(c.per_game)}</tr>`).join("") || emptyRow(4)}</tbody></table>`;
  }

  // ---------- Combate ----------
  const CLASS_CSS = {
    Infantaria: "k-inf", "À distância": "k-rng", Cavalaria: "k-cav", Cerco: "k-sie", Religioso: "k-rel", Naval: "k-nav",
  };

  function armyLegend(classes) {
    return classes.map((c) => `<span><i class="${CLASS_CSS[c]}"></i>${esc(c)}</span>`).join("");
  }

  function armyRows(players, classes, { link = true } = {}) {
    return players.map((p) => `
      <tr${fewAttrs(p.games)}>
        <td data-v="${esc(p.label)}">${link ? playerLink(p.profile_id, p.label) : esc(p.label)}</td>
        ${numTd(p.games)}${numTd(p.military_per_game)}
        <td class="stack-cell">${stackBar(classes.map((c) => ({ label: c, value: p.shares[c], cls: CLASS_CSS[c] })), 100)}</td>
        <td class="chips">${p.top_units.map((u) => `<span class="chip">${esc(u.label)} <b>${numFmt(u.per_game)}</b></span>`).join("")}</td>
      </tr>`).join("") || emptyRow(5);
  }

  function armyTable(a, opts) {
    return `<table class="sortable"><thead><tr><th>Jogador</th><th class="num">Partidas</th><th class="num">Militares/partida</th><th>Composição</th><th>Mais feitas (por partida)</th></tr></thead><tbody>${armyRows(a.players, a.classes, opts)}</tbody></table>`;
  }

  function renderCombat(d) {
    const a = d.army;
    $("#cb-legend").innerHTML = armyLegend(a.classes);
    $("#cb-players").innerHTML = armyTable(a);
    $("#cb-styles").innerHTML = wrTable(a.styles, { head: "Estilo", limit: 999 });
  }

  // ---------- Recordes ----------
  function recordValue(rec, v) {
    if (rec.key === "rating_diff") return signed(v);
    return `${numFmt(v)}${rec.unit ? ` ${rec.unit}` : ""}`;
  }

  function renderRecords(d) {
    const s = d.summary;
    const streak = `<article class="record static">
        <h3>Maior sequência de vitórias</h3>
        <div class="record-top"><span class="record-value">${s.best_win_streak}V</span></div>
        <p class="record-meta">pior sequência: ${s.worst_loss_streak} derrotas</p>
      </article>`;
    $("#rc-list").innerHTML = d.records.map((rec) => {
      const [first, ...rest] = rec.entries;
      const meta = (e) => `${e.who ? `${esc(e.who)} · ` : ""}${esc(e.map || "?")} · ${dateLabel(e.started_at)} · ${e.grp_result === "win" ? "vitória" : e.grp_result === "loss" ? "derrota" : "—"}`;
      return `<article class="record">
        <h3>${esc(rec.title)}</h3>
        <button type="button" class="record-top" data-game="${Number(first.game_id)}">
          <span class="record-value">${esc(recordValue(rec, first.value))}</span>
          <span class="record-meta">${meta(first)}</span>
        </button>
        ${rest.length ? `<ol start="2">${rest.map((e) => `<li><button type="button" class="link" data-game="${Number(e.game_id)}"><b>${esc(recordValue(rec, e.value))}</b> ${meta(e)}</button></li>`).join("")}</ol>` : ""}
      </article>`;
    }).join("") + streak;
  }

  // ---------- Partidas ----------
  function playerList(side) {
    return side.map((p) => `${p.tracked ? playerLink(p.profile_id, p.name || p.profile_id) : `<span>${esc(p.name || p.profile_id)}</span>`} ${flag(p.civilization)}`).join(" · ");
  }

  function fillGames(payload) {
    $("#tbl-games tbody").innerHTML = payload.games.map((g) => `
      <tr>
        <td>${dateLabel(g.started_at)}</td>
        <td class="res ${g.result === "win" ? "win" : g.result === "loss" ? "loss" : ""}">${g.result === "win" ? "Vitória" : g.result === "loss" ? "Derrota" : "—"}</td>
        <td>${esc(kindLabel(g.kind))}</td>
        <td>${mapCell(g.map)}</td>
        <td class="num">${numFmt(g.duration_min)} min</td>
        <td class="muted">${esc(WIN_REASONS[g.win_reason] || g.win_reason || "—")}</td>
        <td class="players-cell">${playerList(g.allies)}</td>
        <td class="players-cell">${playerList(g.enemies)}</td>
        <td class="row-actions">
          <button type="button" class="link-btn" data-game="${Number(g.game_id)}">detalhes</button>
          ${g.url ? `<a href="${esc(g.url)}" target="_blank" rel="noopener">aoe4world</a>` : ""}
        </td>
      </tr>`).join("") || emptyRow(9, "Sem partidas nesse filtro.");

    const from = payload.total ? state.offset + 1 : 0;
    const to = Math.min(state.offset + state.limit, payload.total);
    $("#pg-info").textContent = `${from}–${to} de ${payload.total}`;
    $("#pg-prev").disabled = state.offset === 0;
    $("#pg-next").disabled = to >= payload.total;
  }

  async function loadGames() {
    const params = apiParams();
    params.set("limit", state.limit);
    params.set("offset", state.offset);
    fillGames(await getJSON(`/api/games?${params}`));
  }

  // ---------- Página do jogador ----------
  function renderPlayer(d) {
    const p = d.player;
    const me = d.individual;
    const s = d.summary;
    const tiles = [
      tile("Winrate", me ? pct(me.win_rate) : "—", me ? `${me.wins} vitórias · ${me.losses} derrotas` : "", "hero"),
      tile("Partidas com o grupo", String(s.games), s.first_game ? `${dateLabel(s.first_game)} → ${dateLabel(s.last_game)}` : ""),
      tile("Rating médio", me && me.avg_rating ? String(me.avg_rating) : "—", me ? `Δ ${signed(me.rating_delta ?? 0)} no período` : ""),
      tile("Sequência atual", s.current_streak > 0 ? `${s.current_streak}V` : s.current_streak < 0 ? `${-s.current_streak}D` : "—", `melhor ${s.best_win_streak}V · pior ${s.worst_loss_streak}D`),
    ].join("");

    const cmpRow = d.comparison.row;
    const cmpCols = d.comparison.groups.flatMap((g) => g.columns.map((c) => ({ ...c, group: g.label })));
    const cmpBody = cmpRow ? cmpCols.map((c) => {
      const mine = cmpRow.values[c.key];
      const avg = d.comparison.team_avg[c.key];
      const diff = mine !== null && mine !== undefined && avg ? Math.round((100 * (mine - avg)) / avg) : null;
      // Em "perdas" e "prédios perdidos", ficar abaixo da média é bom.
      const good = c.tone === "bad" ? diff < 0 : diff > 0;
      return `<tr><td class="muted">${esc(c.group)}</td><td>${esc(c.label)}</td>${numTd(mine, numFmt(mine, c.decimals))}${numTd(avg, numFmt(avg, c.decimals), "muted")}${numTd(diff, diff === null ? "—" : `${diff > 0 ? "+" : ""}${diff}%`, diff ? (good ? "pos" : "neg") : "")}</tr>`;
    }).join("") : emptyRow(5, "Sem resumo detalhado para esse jogador no filtro.");

    const partners = wrTable(d.partners.map((r) => ({ ...r, label: r.partner })), {
      head: "Com",
      label: (r) => playerLink(r.partner_id, r.partner),
      extra: [{ head: "Sinergia", cell: (r) => synergyTd(r.synergy) }],
    });

    $("#pp-body").innerHTML = `
      <div class="player-head">
        <div>
          <a href="#/jogadores" class="back">‹ jogadores</a>
          <h2>${esc(p.label)}</h2>
          <p class="lead">${esc(p.name && p.name !== p.label ? `${p.name} · ` : "")}perfil ${Number(p.profile_id)} · só partidas com o grupo, dentro do filtro</p>
        </div>
        <div class="player-links">
          <button type="button" class="link-btn" data-player-games="${Number(p.profile_id)}">ver partidas</button>
          <a class="link-btn" href="https://aoe4world.com/players/${Number(p.profile_id)}" target="_blank" rel="noopener">aoe4world</a>
        </div>
      </div>
      <div class="tiles">${tiles}</div>
      <div class="grid">
        <section class="card"><h2>Civilizações</h2><div class="table-scroll">${wrTable(d.civs.map((r) => ({ ...r, sortLabel: civLabel(r.label) })), { head: "Civ", label: (r) => civCell(r.label) })}</div></section>
        <section class="card"><h2>Mapas</h2><div class="table-scroll">${wrTable(d.maps, { head: "Mapa", label: (r) => mapCell(r.label) })}</div></section>
        <section class="card"><h2>Parceiros</h2><p class="lead">Winrate com cada um no mesmo time.</p><div class="table-scroll">${partners}</div></section>
        <section class="card"><h2>Contra a média do grupo</h2><p class="lead">Média por partida dele × média dos membros nas mesmas partidas.</p>
          <div class="table-scroll"><table class="sortable compact-table"><thead><tr><th>Grupo</th><th>Métrica</th><th class="num">Ele</th><th class="num">Média</th><th class="num">Dif.</th></tr></thead><tbody>${cmpBody}</tbody></table></div></section>
      </div>
      <section class="card"><h2>Exército</h2><div class="legend">${armyLegend(d.army.classes)}</div><div class="table-scroll">${armyTable(d.army, { link: false })}</div>
        <h3 class="sub-head">Winrate por estilo</h3><div class="table-scroll">${wrTable(d.army.styles, { head: "Estilo", limit: 999 })}</div></section>
      <section class="card"><h2>Economia inicial</h2><div class="table-scroll">${earlyTable(d.early_eco)}</div></section>`;
  }

  // ---------- detalhe de uma partida ----------
  const secLabel = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;

  function lossSpark(perMinute, peak) {
    if (!perMinute || !perMinute.length || !peak) return '<span class="detail">—</span>';
    return `<span class="spark" role="img" aria-label="perdas por minuto">${perMinute
      .map((n, i) => `<i style="height:${n ? Math.max(12, (n / peak) * 100) : 0}%" title="min ${i}: ${n}"></i>`)
      .join("")}</span>`;
  }

  function detailPlayerRow(p, peak) {
    const worst = p.worst_minute && p.worst_minute.count
      ? `min ${p.worst_minute.minute} (${p.worst_minute.count})` : "—";
    const extra = Object.entries(p.eco_by_unit || {})
      .filter(([k]) => !k.startsWith("Aldeões"))
      .map(([k, v]) => `${k}: ${v}`).join(" · ");
    const first = p.lost_at && p.lost_at.length ? ` · 1º aos ${secLabel(p.lost_at[0])}` : "";
    return `
      <tr>
        <td>${p.tracked ? playerLink(p.profile_id, p.name) : esc(p.name)}${p.result === "win" ? " 🏆" : ""}</td>
        <td>${civCell(p.civilization)}</td>
        <td class="num">${p.villagers_made}</td>
        <td class="num strong">${p.villagers_lost}</td>
        <td class="num">${p.loss_pct === null ? "—" : `${numFmt(p.loss_pct)}%`}</td>
        <td class="num">${p.villagers_alive}</td>
        <td class="detail">${esc(`${worst}${first}${extra ? ` · ${extra}` : ""}`)}</td>
        <td>${lossSpark(p.per_minute, peak)}</td>
      </tr>`;
  }

  function detailComparison(teams, groups) {
    const cols = groups.flatMap((g) => g.columns);
    const rows = teams.flatMap((t) => t.players.map((p) => ({ ...p, team: t })));
    const max = {};
    cols.forEach((c) => { max[c.key] = Math.max(0, ...rows.map((r) => Number(r.stats[c.key]) || 0)); });
    const groupRow = `<tr class="groups"><th class="group"></th>${groups
      .map((g) => `<th class="group" colspan="${g.columns.length}">${esc(g.label)}</th>`).join("")}</tr>`;
    const colRow = `<tr class="cols"><th>Jogador</th>${cols.map((c) => `<th class="num">${esc(c.label)}</th>`).join("")}</tr>`;
    const body = rows.map((r) => {
      const cells = cols.map((c) => {
        const v = r.stats[c.key];
        const width = max[c.key] > 0 && v ? Math.max(2, (Number(v) / max[c.key]) * 100) : 0;
        return `<td class="cell tone-${esc(c.tone)}" data-v="${v ?? ""}"><span class="bar" style="width:${width.toFixed(1)}%"></span><span class="val">${numFmt(v)}</span></td>`;
      }).join("");
      return `<tr><td class="name ${r.tracked ? "us" : ""}" data-v="${esc(r.name)}">${esc(r.name)}</td>${cells}</tr>`;
    }).join("");
    return `<table class="matrix sortable"><thead>${groupRow}${colRow}</thead><tbody>${body}</tbody></table>`;
  }

  function renderGameDetail(d) {
    const g = d.game;
    const peak = Math.max(1, ...d.teams.flatMap((t) => t.players.flatMap((p) => p.per_minute || [0])));
    const reason = g.win_reason ? ` · fim por ${WIN_REASONS[g.win_reason] || g.win_reason}` : "";
    const head = `
      <div class="detail-head">
        <h2 id="game-detail-title">Detalhes da partida</h2>
        <p>${mapIcon(g.map)} ${esc(`${g.map || "?"} · ${kindLabel(g.kind)} · ${dateLabel(g.started_at)} · ${numFmt(g.duration_min)} min${reason}`)}
          · <a href="${esc(g.url)}" target="_blank" rel="noopener">abrir no aoe4world</a></p>
      </div>`;

    if (!d.has_summary) {
      return `${head}<p class="empty">Essa partida não tem resumo detalhado na API${d.summary_status === "missing" ? " (partidas antigas costumam não ter)" : " ainda"}.</p>`;
    }

    const teams = d.teams.map((t) => `
      <div class="team-block">
        <h3 class="sub-head">${t.is_ours ? "Nosso time" : "Time adversário"}
          <span class="tag ${t.result === "win" ? "win" : t.result === "loss" ? "loss" : ""}">${t.result === "win" ? "venceu" : t.result === "loss" ? "perdeu" : ""}</span>
          <span class="detail">${Number(t.villagers_lost)} aldeões perdidos de ${Number(t.villagers_made)} produzidos</span>
        </h3>
        <div class="table-scroll">
          <table>
            <thead><tr>
              <th>Jogador</th><th>Civ</th><th class="num">Produzidos</th><th class="num">Perdidos</th>
              <th class="num">% perdido</th><th class="num">Sobraram</th><th>Pior minuto</th><th>Por minuto</th>
            </tr></thead>
            <tbody>${t.players.map((p) => detailPlayerRow(p, peak)).join("")}</tbody>
          </table>
        </div>
      </div>`).join("");

    const ours = d.teams.find((t) => t.is_ours);
    const theirs = d.teams.find((t) => !t.is_ours);
    const saldo = ours && theirs
      ? `<p class="note">Saldo da partida: o time adversário perdeu <strong>${Number(theirs.villagers_lost)}</strong> aldeões, o nosso perdeu <strong>${Number(ours.villagers_lost)}</strong>.
         A API não registra quem deu cada abate — só quem perdeu a unidade e em que minuto.</p>`
      : "";

    return `${head}
      <details class="detail-cmp" open>
        <summary>Comparativo da partida</summary>
        <div class="table-scroll">${detailComparison(d.teams, d.columns)}</div>
      </details>
      <h3 class="sub-head">Aldeões perdidos</h3>
      ${teams}${saldo}`;
  }

  async function openGameDetail(gameId) {
    const dlg = $("#game-detail");
    $("#game-detail-body").innerHTML = '<p class="empty">Carregando…</p>';
    if (!dlg.open) dlg.showModal();
    try {
      $("#game-detail-body").innerHTML = renderGameDetail(await getJSON(`/api/games/${Number(gameId)}`));
    } catch (err) {
      $("#game-detail-body").innerHTML = `<p class="empty">Não deu para carregar: ${esc(err.message)}</p>`;
    }
  }

  // ---------- roteamento ----------
  const ROUTES = {
    "": "overview", "visao-geral": "overview", jogadores: "players", civs: "civs", economia: "economy",
    combate: "combat", recordes: "records", partidas: "games", jogador: "player",
  };
  const RENDER = {
    overview: renderOverview, players: renderPlayers, civs: renderCivs, economy: renderEconomy,
    combat: renderCombat, records: renderRecords, player: renderPlayer,
  };

  function parseRoute() {
    const [slug = "", arg] = location.hash.replace(/^#\/?/, "").split("/");
    const view = ROUTES[slug] || "overview";
    return { view, pid: view === "player" && /^\d+$/.test(arg || "") ? Number(arg) : null };
  }

  async function render() {
    const { view, pid } = parseRoute();
    const token = ++state.token;
    state.view = view;
    state.pid = pid;
    $$(".view").forEach((el) => { el.hidden = el.dataset.view !== view; });
    $$("[data-nav]").forEach((a) => {
      const active = a.dataset.nav === (view === "player" ? "players" : view);
      a.classList.toggle("active", active);
      if (active) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    const section = $(`.view[data-view="${view}"]`);
    const err = $("#view-error");
    err.hidden = true;
    section.setAttribute("aria-busy", "true");
    try {
      if (view === "games") {
        await loadGames();
      } else {
        const extra = {};
        if (view === "players") extra.cmp_mode = $("#cmp-mode").value;
        if (view === "player") {
          if (pid === null) throw new Error("jogador inválido");
          extra.pid = pid;
        }
        const data = await fetchView(view, extra);
        if (token !== state.token) return;       // o usuário já trocou de aba
        RENDER[view](data);
      }
    } catch (e) {
      if (token !== state.token) return;
      err.textContent = `Erro ao carregar: ${e.message}`;
      err.hidden = false;
    } finally {
      section.removeAttribute("aria-busy");
    }
  }

  // ---------- inicialização ----------
  async function boot() {
    const facets = await getJSON("/api/facets");
    state.facets = facets;
    $("#f-players").insertAdjacentHTML("beforeend", facets.players.map((p) => `
      <label><input type="checkbox" value="${Number(p.profile_id)}"> ${esc(p.label)}</label>`).join(""));
    $("#f-season").insertAdjacentHTML("beforeend", facets.seasons.map((s) => `<option value="${Number(s)}">Temporada ${Number(s)}</option>`).join(""));
    $("#f-map").insertAdjacentHTML("beforeend", (facets.maps || []).map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join(""));
    $("#meta-sync").textContent = facets.last_sync
      ? `última sincronização: ${new Date(facets.last_sync).toLocaleString("pt-BR")}`
      : "ainda não sincronizado";
    applyUrl();

    const rerun = () => { state.offset = 0; writeUrl(); render(); };
    ["#f-preset", "#f-season", "#f-map", "#f-minsize", "#f-from", "#f-to", "#f-mingames"]
      .forEach((sel) => $(sel).addEventListener("change", rerun));
    $("#f-players").addEventListener("change", rerun);
    $("#f-reset").addEventListener("click", () => {
      $("#f-preset").value = DEFAULTS.preset;
      $("#f-season").value = "";
      $("#f-map").value = "";
      $("#f-minsize").value = DEFAULTS.min_size;
      $("#f-from").value = "";
      $("#f-to").value = "";
      $("#f-mingames").value = DEFAULTS.min_games;
      $$("#f-players input").forEach((i) => { i.checked = false; });
      rerun();
    });
    $("#cmp-mode").addEventListener("change", () => render());
    $("#pg-prev").addEventListener("click", () => { state.offset = Math.max(0, state.offset - state.limit); loadGames(); });
    $("#pg-next").addEventListener("click", () => { state.offset += state.limit; loadGames(); });
    $("#game-detail-close").addEventListener("click", () => $("#game-detail").close());

    document.addEventListener("click", (ev) => {
      const th = ev.target.closest("table.sortable thead tr:last-child th");
      if (th) { sortTable(th); return; }
      const more = ev.target.closest("[data-more]");
      if (more) {
        const table = more.previousElementSibling;
        const clipped = table.classList.toggle("clip");
        more.textContent = clipped ? `ver todos (${table.tBodies[0].rows.length})` : "ver menos";
        return;
      }
      const game = ev.target.closest("[data-game]");
      if (game) { openGameDetail(game.dataset.game); return; }
      const pg = ev.target.closest("[data-player-games]");
      if (pg) {
        const box = $(`#f-players input[value="${Number(pg.dataset.playerGames)}"]`);
        if (box) box.checked = true;
        state.offset = 0;
        writeUrl();
        location.hash = "#/partidas";
      }
    });
    // Link de jogador dentro do modal: fecha o modal antes de navegar.
    $("#game-detail").addEventListener("click", (ev) => {
      if (ev.target.closest('a[href^="#/"]')) $("#game-detail").close();
    });

    window.addEventListener("hashchange", () => { state.offset = 0; render(); window.scrollTo({ top: 0 }); });
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => render());

    await render();
  }

  boot().catch((err) => { $("#meta-sync").textContent = `erro ao carregar: ${err.message}`; });
})();
