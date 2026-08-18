/* Squad Stats — frontend. Uma chamada a /api/stats por filtro + /api/games paginado. */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
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

  const charts = {};
  const state = { offset: 0, limit: 25, gamesTotal: 0 };

  // ---------- utilidades ----------
  const pct = (v) => (v === null || v === undefined ? "—" : `${v.toFixed(1)}%`);
  // Nomes curtos: rotulos do eixo Y sao clipados quando passam de ~20 chars.
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
  const clip = (s, n = 20) => (String(s).length > n ? `${String(s).slice(0, n - 1)}…` : String(s));
  const kindLabel = (k) => (k || "?").replace("rm_", "Ranqueado ").replace("qm_", "Quick ").replace(/_/g, " ");
  const dateLabel = (iso) => (iso ? new Date(iso).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", year: "2-digit" }) : "—");
  const monthLabel = (ym) => {
    const [y, m] = ym.split("-");
    return new Date(Number(y), Number(m) - 1, 1).toLocaleDateString("pt-BR", { month: "short", year: "2-digit" });
  };

  function currentFilters() {
    const players = [...document.querySelectorAll("#f-players input:checked")].map((i) => i.value);
    const params = new URLSearchParams();
    params.set("preset", $("#f-preset").value);
    params.set("min_size", $("#f-minsize").value);
    if (players.length) params.set("players", players.join(","));
    if ($("#f-from").value) params.set("from", $("#f-from").value);
    if ($("#f-to").value) params.set("to", `${$("#f-to").value}T23:59:59Z`);
    return params;
  }

  async function getJSON(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  }

  // ---------- plugin: rotulo direto na ponta da barra ----------
  const endLabels = {
    id: "endLabels",
    afterDatasetsDraw(chart, _args, opts) {
      if (!opts || !opts.enabled) return;
      const { ctx } = chart;
      const meta = chart.getDatasetMeta(0);
      const rows = chart.$rows || [];
      ctx.save();
      ctx.font = "600 12px ui-sans-serif, system-ui, sans-serif";
      ctx.fillStyle = COLORS().text;
      ctx.textBaseline = "middle";
      meta.data.forEach((bar, i) => {
        const row = rows[i];
        if (!row) return;
        const text = `${pct(row.win_rate)}  ·  ${row.wins}V ${row.losses}D`;
        ctx.textAlign = "left";
        ctx.fillText(text, bar.x + 8, bar.y);
      });
      ctx.restore();
    },
  };
  Chart.register(endLabels);

  Chart.defaults.font.family = "ui-sans-serif, system-ui, 'Segoe UI', Roboto, sans-serif";
  Chart.defaults.animation.duration = 250;

  function destroy(id) {
    if (charts[id]) { charts[id].destroy(); delete charts[id]; }
  }

  /** Mostra/esconde aviso de "sem dados" sem destruir o canvas. */
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

  /** Barra horizontal de winrate, com rotulo direto (encoding secundario obrigatorio). */
  function winrateBar(canvasId, rows, { labelFmt = (s) => s, max = 12, clipLen = 20 } = {}) {
    destroy(canvasId);
    const c = COLORS();
    const data = rows.slice(0, max);
    const el = document.getElementById(canvasId);
    emptyState(canvasId, !data.length);
    if (!data.length) return;
    charts[canvasId] = new Chart(el, {
      type: "bar",
      data: {
        labels: data.map((r) => labelFmt(r.label)),
        datasets: [{
          label: "Winrate",
          data: data.map((r) => r.win_rate),
          backgroundColor: data.map((r) => (r.win_rate >= 50 ? c.win : c.loss)),
          borderRadius: 4,
          borderSkipped: "start",
          barThickness: 14,
        }],
      },
      options: {
        indexAxis: "y",
        maintainAspectRatio: false,
        layout: { padding: { right: 118 } },
        scales: {
          x: { min: 0, max: 100, grid: { color: c.grid, drawTicks: false }, border: { display: false },
               ticks: { color: c.muted, callback: (v) => `${v}%` } },
          y: { grid: { display: false }, border: { display: false },
               ticks: { color: c.text, font: { size: 12 },
                        callback(_v, i) { return clip(this.getLabelForValue(i), clipLen); } } },
        },
        plugins: {
          legend: { display: false },
          endLabels: { enabled: true },
          tooltip: {
            backgroundColor: c.surface, borderColor: c.grid, borderWidth: 1,
            titleColor: c.text, bodyColor: c.text, padding: 10,
            callbacks: {
              label: (ctx) => {
                const r = data[ctx.dataIndex];
                return [`Winrate ${pct(r.win_rate)}`, `${r.wins} vitórias / ${r.losses} derrotas`, `${r.games} partidas`];
              },
            },
          },
        },
      },
    });
    charts[canvasId].$rows = data;
  }

  function timelineChart(rows) {
    destroy("c-timeline");
    const c = COLORS();
    const el = document.getElementById("c-timeline");
    emptyState("c-timeline", !rows.length);
    if (!rows.length) return;
    charts["c-timeline"] = new Chart(el, {
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
          tooltip: {
            backgroundColor: c.surface, borderColor: c.grid, borderWidth: 1, titleColor: c.text, bodyColor: c.text, padding: 10,
            callbacks: {
              afterBody: (items) => {
                const r = rows[items[0].dataIndex];
                return `Winrate do mês: ${pct(r.win_rate)}`;
              },
            },
          },
        },
      },
    });
  }

  function trendChart(rows) {
    destroy("c-trend");
    const c = COLORS();
    const el = document.getElementById("c-trend");
    emptyState("c-trend", !rows.length);
    if (!rows.length) return;
    charts["c-trend"] = new Chart(el, {
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
          tooltip: {
            backgroundColor: c.surface, borderColor: c.grid, borderWidth: 1, titleColor: c.text, bodyColor: c.text, padding: 10,
            callbacks: { label: (ctx) => `Winrate acumulado: ${pct(ctx.parsed.y)}` },
          },
          annotation: false,
        },
      },
    });
  }

  // ---------- tabelas ----------
  function fillPlayers(rows) {
    const tb = $("#tbl-players tbody");
    tb.innerHTML = rows.map((r) => `
      <tr>
        <td class="us">${r.label}</td>
        <td class="num">${r.games}</td>
        <td class="num">${r.wins}</td>
        <td class="num">${r.losses}</td>
        <td class="num">${pct(r.win_rate)}</td>
        <td class="num">${r.avg_rating ?? "—"}</td>
        <td class="num">${r.rating_delta > 0 ? "+" : ""}${r.rating_delta ?? 0}</td>
      </tr>`).join("") || '<tr><td colspan="7" class="empty">Sem dados.</td></tr>';
  }

  function rawTables(data) {
    const blocks = [
      ["Por formação", data.by_lineup],
      ["Por modo", data.by_kind],
      ["Civs jogadas", data.by_civ],
      ["Civs enfrentadas", data.vs_civ],
      ["Mapas", data.by_map],
      ["Duração", data.by_duration],
      ["Mês a mês", data.timeline.map((r) => ({ ...r, label: monthLabel(r.label) }))],
    ];
    $("#raw-tables").innerHTML = blocks.map(([title, rows]) => `
      <h3>${title}</h3>
      <div class="table-scroll"><table>
        <thead><tr><th>Item</th><th class="num">Partidas</th><th class="num">V</th><th class="num">D</th><th class="num">Winrate</th></tr></thead>
        <tbody>${rows.map((r) => `<tr><td>${r.label}</td><td class="num">${r.games}</td><td class="num">${r.wins}</td><td class="num">${r.losses}</td><td class="num">${pct(r.win_rate)}</td></tr>`).join("")}</tbody>
      </table></div>`).join("");
  }

  function playerList(side) {
    return side.map((p) => `<span class="${p.tracked ? "us" : ""}">${p.name || p.profile_id}</span> <span class="civ">${civLabel(p.civilization)}</span>`).join(" · ");
  }

  function fillGames(payload) {
    state.gamesTotal = payload.total;
    const tb = $("#tbl-games tbody");
    tb.innerHTML = payload.games.map((g) => `
      <tr>
        <td>${dateLabel(g.started_at)}</td>
        <td class="res ${g.result || ""}">${g.result === "win" ? "Vitória" : g.result === "loss" ? "Derrota" : "—"}</td>
        <td>${kindLabel(g.kind)}</td>
        <td>${g.map || "—"}</td>
        <td class="num">${g.duration_min} min</td>
        <td>${playerList(g.allies)}</td>
        <td>${playerList(g.enemies)}</td>
        <td>${g.url ? `<a href="${g.url}" target="_blank" rel="noopener">detalhe</a>` : ""}</td>
      </tr>`).join("") || '<tr><td colspan="8" class="empty">Sem partidas nesse filtro.</td></tr>';

    const from = payload.total ? state.offset + 1 : 0;
    const to = Math.min(state.offset + state.limit, payload.total);
    $("#pg-info").textContent = `${from}–${to} de ${payload.total}`;
    $("#pg-prev").disabled = state.offset === 0;
    $("#pg-next").disabled = to >= payload.total;
  }

  // ---------- carregamento ----------
  async function loadStats() {
    const params = currentFilters();
    const data = await getJSON(`/api/stats?${params}`);
    const s = data.summary;

    $("#t-winrate").textContent = pct(s.win_rate);
    $("#t-wl").textContent = `${s.wins} vitórias · ${s.losses} derrotas`;
    $("#t-games").textContent = s.games;
    $("#t-period").textContent = s.first_game ? `${dateLabel(s.first_game)} → ${dateLabel(s.last_game)}` : "—";
    $("#t-streak").textContent = s.current_streak > 0 ? `${s.current_streak}V` : s.current_streak < 0 ? `${-s.current_streak}D` : "—";
    $("#t-streak-best").textContent = `melhor ${s.best_win_streak}V · pior ${s.worst_loss_streak}D`;
    $("#t-duration").textContent = `${s.avg_duration_min} min`;
    $("#t-groupsize").textContent = `média de ${s.avg_group_size} de nós por partida`;

    timelineChart(data.timeline);
    trendChart(data.timeline);
    winrateBar("c-lineup", data.by_lineup, { max: 8, clipLen: 34 });
    winrateBar("c-kind", data.by_kind, { labelFmt: kindLabel, max: 8 });
    winrateBar("c-civ", data.by_civ, { labelFmt: civLabel, max: 12 });
    winrateBar("c-vsciv", data.vs_civ, { labelFmt: civLabel, max: 12 });
    winrateBar("c-map", data.by_map, { max: 12 });
    winrateBar("c-duration", data.by_duration, { max: 6 });
    fillPlayers(data.by_player);
    rawTables(data);
  }

  async function loadGames() {
    const params = currentFilters();
    params.set("limit", state.limit);
    params.set("offset", state.offset);
    fillGames(await getJSON(`/api/games?${params}`));
  }

  async function refresh() {
    try {
      await Promise.all([loadStats(), loadGames()]);
    } catch (err) {
      $("#meta-sync").textContent = `erro ao carregar: ${err.message}`;
    }
  }

  async function boot() {
    const facets = await getJSON("/api/facets");
    $("#f-players").insertAdjacentHTML("beforeend", facets.players.map((p) => `
      <label><input type="checkbox" value="${p.profile_id}"> ${p.label}</label>`).join(""));
    $("#meta-sync").textContent = facets.last_sync
      ? `última sincronização: ${new Date(facets.last_sync).toLocaleString("pt-BR")}`
      : "ainda não sincronizado";

    const rerun = () => { state.offset = 0; refresh(); };
    ["#f-preset", "#f-minsize", "#f-from", "#f-to"].forEach((sel) => $(sel).addEventListener("change", rerun));
    $("#f-players").addEventListener("change", rerun);
    $("#f-reset").addEventListener("click", () => {
      $("#f-preset").value = "tg";
      $("#f-minsize").value = "2";
      $("#f-from").value = "";
      $("#f-to").value = "";
      document.querySelectorAll("#f-players input").forEach((i) => { i.checked = false; });
      rerun();
    });
    $("#pg-prev").addEventListener("click", () => { state.offset = Math.max(0, state.offset - state.limit); loadGames(); });
    $("#pg-next").addEventListener("click", () => { state.offset += state.limit; loadGames(); });

    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => refresh());

    await refresh();
  }

  boot();
})();
