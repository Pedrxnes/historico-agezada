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
        <td class="row-actions">
          <button type="button" class="link-btn" data-game="${g.game_id}">aldeões perdidos</button>
          ${g.url ? `<a href="${g.url}" target="_blank" rel="noopener">aoe4world</a>` : ""}
        </td>
      </tr>`).join("") || '<tr><td colspan="8" class="empty">Sem partidas nesse filtro.</td></tr>';

    const from = payload.total ? state.offset + 1 : 0;
    const to = Math.min(state.offset + state.limit, payload.total);
    $("#pg-info").textContent = `${from}–${to} de ${payload.total}`;
    $("#pg-prev").disabled = state.offset === 0;
    $("#pg-next").disabled = to >= payload.total;
  }

  // ---------- comparativo (matriz jogador x metrica) ----------
  const numFmt = (v, decimals) => {
    if (v === null || v === undefined) return "—";
    if (decimals !== undefined) return v.toLocaleString("pt-BR", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    return v.toLocaleString("pt-BR", { maximumFractionDigits: 1 });
  };

  function fillComparison(cmp) {
    const table = $("#tbl-comparison");
    const cols = cmp.groups.flatMap((g) => g.columns);
    const rows = cmp.rows || [];

    if (!rows.length) {
      table.querySelector("thead").innerHTML = "";
      table.querySelector("tbody").innerHTML = '<tr><td class="empty">Sem resumo detalhado nesse filtro. Rode <code>python backend/sync.py --summaries</code>.</td></tr>';
    } else {
      // Escala por coluna: a barra mede o valor contra o maior da propria coluna.
      const max = {};
      cols.forEach((c) => {
        max[c.key] = Math.max(0, ...rows.map((r) => Number(r.values[c.key]) || 0));
      });

      const groupRow = `<tr class="groups"><th class="group" colspan="2"></th>${cmp.groups
        .map((g) => `<th class="group" colspan="${g.columns.length}">${g.label}</th>`).join("")}</tr>`;
      const colRow = `<tr class="cols"><th>Jogador</th><th class="num">Part.</th>${cols
        .map((c) => `<th class="num">${c.label}</th>`).join("")}</tr>`;
      table.querySelector("thead").innerHTML = groupRow + colRow;

      table.querySelector("tbody").innerHTML = rows.map((r) => {
        const cells = cols.map((c) => {
          const v = r.values[c.key];
          const width = max[c.key] > 0 && v ? Math.max(2, (Number(v) / max[c.key]) * 100) : 0;
          return `<td class="cell tone-${c.tone}"><span class="bar" style="width:${width.toFixed(1)}%"></span><span class="val">${numFmt(v, c.decimals)}</span></td>`;
        }).join("");
        return `<tr><td class="name">${r.label}</td><td class="games num">${r.games}</td>${cells}</tr>`;
      }).join("");
    }

    const cov = cmp.coverage;
    const falta = cov.games - cov.with_summary;
    $("#cmp-note").innerHTML =
      `${cmp.mode === "sum" ? "Total somado" : "Média por partida"} sobre ${cov.with_summary} de ${cov.games} partidas do filtro.` +
      (falta > 0 ? ` ${falta} ainda sem resumo detalhado (partidas antigas costumam não ter).` : "");
  }

  // ---------- unidades economicas eliminadas ----------
  function fillEco(eco) {
    const t = eco.totals;
    $("#eco-tiles").innerHTML = [
      ["Eliminadas pelo grupo", t.eliminated.toLocaleString("pt-BR"), `${numFmt(t.eliminated_per_game)} por partida`],
      ["Perdidas pelo grupo", t.lost.toLocaleString("pt-BR"), `${numFmt(t.lost_per_game)} por partida`],
      ["Saldo", `${t.balance > 0 ? "+" : ""}${t.balance.toLocaleString("pt-BR")}`, t.balance >= 0 ? "matamos mais economia" : "perdemos mais economia"],
    ].map(([label, value, foot]) => `
      <div class="tile">
        <div class="tile-label">${label}</div>
        <div class="tile-value">${value}</div>
        <div class="tile-foot">${foot}</div>
      </div>`).join("");

    const games = eco.coverage.with_summary || 0;
    $("#tbl-eco-units tbody").innerHTML = (eco.by_unit || []).map((u) => `
      <tr>
        <td>${u.label}</td>
        <td class="num">${u.total.toLocaleString("pt-BR")}</td>
        <td class="num">${games ? numFmt(u.total / games) : "—"}</td>
      </tr>`).join("") || '<tr><td colspan="3" class="empty">Sem dados.</td></tr>';

    $("#tbl-eco-players tbody").innerHTML = (eco.by_player || []).map((p) => `
      <tr>
        <td class="us">${p.label}</td>
        <td class="num">${p.games}</td>
        <td class="num">${p.made.toLocaleString("pt-BR")}</td>
        <td class="num">${p.lost.toLocaleString("pt-BR")}</td>
        <td class="num">${numFmt(p.lost_per_game)}</td>
        <td class="num">${p.survival === null ? "—" : `${numFmt(p.survival)}%`}</td>
        <td class="detail">${Object.entries(p.by_unit).map(([k, v]) => `${k}: ${v}`).join(" · ") || "—"}</td>
      </tr>`).join("") || '<tr><td colspan="7" class="empty">Sem dados.</td></tr>';

    $("#tbl-eco-civs tbody").innerHTML = (eco.by_enemy_civ || []).map((c) => `
      <tr>
        <td>${civLabel(c.label)}</td>
        <td class="num">${c.games}</td>
        <td class="num">${c.total.toLocaleString("pt-BR")}</td>
        <td class="num">${numFmt(c.per_game)}</td>
      </tr>`).join("") || '<tr><td colspan="4" class="empty">Sem dados.</td></tr>';
  }

  // ---------- detalhe de uma partida (aldeões perdidos) ----------
  const secLabel = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;

  /** Barrinhas por minuto: mostra quando a economia do jogador caiu. */
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
    return `
      <tr>
        <td class="${p.tracked ? "us" : ""}">${p.name}${p.result === "win" ? " 🏆" : ""}</td>
        <td class="civ">${civLabel(p.civilization)}</td>
        <td class="num">${p.villagers_made}</td>
        <td class="num strong">${p.villagers_lost}</td>
        <td class="num">${p.loss_pct === null ? "—" : `${numFmt(p.loss_pct)}%`}</td>
        <td class="num">${p.villagers_alive}</td>
        <td class="detail">${worst}${extra ? ` · ${extra}` : ""}</td>
        <td>${lossSpark(p.per_minute, peak)}</td>
      </tr>`;
  }

  function detailComparison(teams, groups) {
    const cols = groups.flatMap((g) => g.columns);
    const rows = teams.flatMap((t) => t.players.map((p) => ({ ...p, team: t })));
    const max = {};
    cols.forEach((c) => { max[c.key] = Math.max(0, ...rows.map((r) => Number(r.stats[c.key]) || 0)); });
    const groupRow = `<tr class="groups"><th class="group"></th>${groups
      .map((g) => `<th class="group" colspan="${g.columns.length}">${g.label}</th>`).join("")}</tr>`;
    const colRow = `<tr class="cols"><th>Jogador</th>${cols
      .map((c) => `<th class="num">${c.label}</th>`).join("")}</tr>`;
    const body = rows.map((r) => {
      const cells = cols.map((c) => {
        const v = r.stats[c.key];
        const width = max[c.key] > 0 && v ? Math.max(2, (Number(v) / max[c.key]) * 100) : 0;
        return `<td class="cell tone-${c.tone}"><span class="bar" style="width:${width.toFixed(1)}%"></span><span class="val">${numFmt(v)}</span></td>`;
      }).join("");
      return `<tr><td class="name ${r.tracked ? "us" : ""}">${r.name}</td>${cells}</tr>`;
    }).join("");
    return `<table class="matrix"><thead>${groupRow}${colRow}</thead><tbody>${body}</tbody></table>`;
  }

  function renderGameDetail(d) {
    const g = d.game;
    const peak = Math.max(1, ...d.teams.flatMap((t) => t.players.flatMap((p) => p.per_minute || [0])));
    const head = `
      <div class="detail-head">
        <h2>Aldeões perdidos</h2>
        <p>${g.map || "?"} · ${kindLabel(g.kind)} · ${dateLabel(g.started_at)} · ${g.duration_min} min${g.win_reason ? ` · ${g.win_reason === "Surrender" ? "encerrada por rendição" : g.win_reason}` : ""}</p>
      </div>`;

    if (!d.has_summary) {
      return `${head}<p class="empty">Essa partida não tem resumo detalhado na API${d.summary_status === "missing" ? " (partidas antigas costumam não ter)" : " ainda"}.</p>`;
    }

    const teams = d.teams.map((t) => `
      <div class="team-block">
        <h3 class="sub-head">${t.is_ours ? "Nosso time" : "Time adversário"}
          <span class="tag ${t.result || ""}">${t.result === "win" ? "venceu" : t.result === "loss" ? "perdeu" : ""}</span>
          <span class="detail">${t.villagers_lost} aldeões perdidos de ${t.villagers_made} produzidos</span>
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
      ? `<p class="note">Saldo da partida: o time adversário perdeu <strong>${theirs.villagers_lost}</strong> aldeões, o nosso perdeu <strong>${ours.villagers_lost}</strong>.
         A API não registra quem deu cada abate — só quem perdeu a unidade e em que minuto.</p>`
      : "";

    return `${head}${teams}${saldo}
      <details class="detail-cmp">
        <summary>Comparativo completo da partida</summary>
        <div class="table-scroll">${detailComparison(d.teams, d.columns)}</div>
      </details>`;
  }

  async function openGameDetail(gameId) {
    const dlg = $("#game-detail");
    $("#game-detail-body").innerHTML = '<p class="empty">Carregando…</p>';
    dlg.showModal();
    try {
      const data = await getJSON(`/api/games/${gameId}`);
      $("#game-detail-body").innerHTML = renderGameDetail(data);
    } catch (err) {
      $("#game-detail-body").innerHTML = `<p class="empty">Não deu para carregar: ${err.message}</p>`;
    }
  }

  // ---------- carregamento ----------
  async function loadStats() {
    const params = currentFilters();
    params.set("cmp_mode", $("#cmp-mode").value);
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
    fillComparison(data.comparison);
    fillEco(data.eco_kills);
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
    $("#cmp-mode").addEventListener("change", () => loadStats());
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
    $("#tbl-games tbody").addEventListener("click", (ev) => {
      const btn = ev.target.closest("button[data-game]");
      if (btn) openGameDetail(btn.dataset.game);
    });
    $("#game-detail-close").addEventListener("click", () => $("#game-detail").close());
    $("#pg-prev").addEventListener("click", () => { state.offset = Math.max(0, state.offset - state.limit); loadGames(); });
    $("#pg-next").addEventListener("click", () => { state.offset += state.limit; loadGames(); });

    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => refresh());

    await refresh();
  }

  boot();
})();
