(() => {
  const DATA_URL = "predictions.json";
  const fmtPct = (p) => (p == null ? "—" : (p * 100).toFixed(1) + "%");
  const fmtNum = (n, d = 2) => (n == null ? "—" : Number(n).toFixed(d));

  const fmtKickoff = (iso) => {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      weekday: "short", day: "2-digit", month: "short",
      hour: "2-digit", minute: "2-digit"
    });
  };

  const state = { doc: null, filtered: [] };

  function renderModelMeta(model) {
    const m = model || {};
    const h = m.holdout_metrics || {};
    const grid = document.getElementById("model-meta");
    grid.innerHTML = "";
    const cells = [
      ["Método", m.method || "—"],
      ["Reentrenado", m.trained_at ? new Date(m.trained_at).toLocaleString() : "—"],
      ["Log loss (holdout)", h.log_loss != null ? h.log_loss.toFixed(4) : "—"],
      ["Brier (holdout)", h.brier != null ? h.brier.toFixed(4) : "—"],
      ["Accuracy (holdout)", h.accuracy != null ? (h.accuracy * 100).toFixed(1) + "%" : "—"],
      ["Mercado log loss", h.market_log_loss != null ? h.market_log_loss.toFixed(4) : "—"],
    ];
    for (const [k, v] of cells) {
      const item = document.createElement("div");
      item.className = "item";
      item.innerHTML = `<small>${k}</small><strong>${v}</strong>`;
      grid.appendChild(item);
    }
  }

  function buildCompFilter(matches) {
    const sel = document.getElementById("filter-comp");
    const seen = new Map();
    for (const m of matches) seen.set(m.competition.code, m.competition.name);
    for (const [code, name] of seen) {
      const o = document.createElement("option");
      o.value = code; o.textContent = name;
      sel.appendChild(o);
    }
  }

  function applyFilters() {
    const comp = document.getElementById("filter-comp").value;
    const onlyMarket = document.getElementById("filter-market").checked;
    const q = document.getElementById("filter-search").value.trim().toLowerCase();
    state.filtered = (state.doc.matches || []).filter((m) => {
      if (comp && m.competition.code !== comp) return false;
      if (onlyMarket && !m.market_probabilities) return false;
      if (q) {
        const hay = (m.home.name + " " + m.away.name).toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    renderMatches();
  }

  function renderMatches() {
    const cont = document.getElementById("matches");
    cont.innerHTML = "";
    if (!state.filtered.length) {
      cont.innerHTML = "<p>No hay partidos próximos para los filtros seleccionados.</p>";
      return;
    }
    const tpl = document.getElementById("match-card");
    for (const m of state.filtered) {
      const node = tpl.content.cloneNode(true);
      node.querySelector(".comp").textContent = m.competition.name;
      const t = node.querySelector(".kickoff");
      t.textContent = fmtKickoff(m.kickoff_ts_utc);
      t.dateTime = m.kickoff_ts_utc;

      node.querySelector(".team.home .name").textContent = m.home.name || m.home.id;
      node.querySelector(".team.away .name").textContent = m.away.name || m.away.id;
      node.querySelector(".team.home .elo").textContent = "Elo " + fmtNum(m.ratings.elo_home, 0);
      node.querySelector(".team.away .elo").textContent = "Elo " + fmtNum(m.ratings.elo_away, 0);

      const p = m.probabilities;
      const setBar = (sel, val) => {
        const bar = node.querySelector(sel);
        bar.querySelector(".pct").textContent = fmtPct(val);
        bar.querySelector(".fill").style.width = (val * 100).toFixed(1) + "%";
      };
      setBar(".bar.home", p.home);
      setBar(".bar.draw", p.draw);
      setBar(".bar.away", p.away);

      const det = node.querySelector(".details");
      det.querySelector(".xg").textContent = `${fmtNum(m.expected_goals.home, 2)} - ${fmtNum(m.expected_goals.away, 2)}`;
      det.querySelector(".over").textContent = fmtPct(m.derived.p_over_2_5);
      det.querySelector(".btts").textContent = fmtPct(m.derived.p_btts);

      if (m.market_probabilities) {
        const mr = det.querySelector(".market-row");
        mr.classList.remove("hidden");
        mr.querySelector(".market").textContent =
          `${fmtPct(m.market_probabilities.home)} / ${fmtPct(m.market_probabilities.draw)} / ${fmtPct(m.market_probabilities.away)}`;
      }

      const list = det.querySelector(".scoreline-list");
      for (const s of (m.scoreline_top || [])) {
        const li = document.createElement("li");
        li.innerHTML = `<strong>${s.score}</strong><small>${fmtPct(s.p)}</small>`;
        list.appendChild(li);
      }

      cont.appendChild(node);
    }
  }

  async function init() {
    try {
      const r = await fetch(DATA_URL, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      state.doc = await r.json();
    } catch (e) {
      document.getElementById("matches").innerHTML =
        `<p>No se pudo cargar <code>predictions.json</code>: ${e.message}.<br>
         Generá el archivo con <code>python -m src.predict</code> y subilo a <code>web/predictions.json</code>.</p>`;
      document.getElementById("model-meta").textContent = "—";
      return;
    }
    renderModelMeta(state.doc.model);
    document.getElementById("generated-at").textContent =
      state.doc.generated_at_utc ? new Date(state.doc.generated_at_utc).toLocaleString() : "—";
    buildCompFilter(state.doc.matches || []);
    document.getElementById("filter-comp").addEventListener("change", applyFilters);
    document.getElementById("filter-market").addEventListener("change", applyFilters);
    document.getElementById("filter-search").addEventListener("input", applyFilters);
    applyFilters();
  }

  init();
})();
