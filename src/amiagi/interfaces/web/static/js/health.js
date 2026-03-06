/**
 * Health Dashboard controller — auto-refresh, status cards, VRAM bars.
 * Polls /health/detailed, /api/health/vram, /api/health/connections every 10 s.
 */
(function () {
  "use strict";

  const REFRESH_INTERVAL_MS = 10_000;

  /* ── Helpers ─────────────────────────────────────────── */

  function setText(id, text) {
    const el = document.getElementById(id);
    if (el) {
      const target = el.querySelector(".health-metric-value") || el;
      target.textContent = text;
    }
  }

  function setCard(service, { status, value, detail }) {
    const card = document.querySelector(`.health-card[data-service="${service}"]`);
    if (!card) return;
    card.className = "health-card health-card--" + status;
    const valEl = card.querySelector(".health-card-value");
    const detEl = card.querySelector(".health-card-detail");
    if (valEl) valEl.textContent = value;
    if (detEl) detEl.textContent = detail;
  }

  function formatUptime(seconds) {
    if (!seconds && seconds !== 0) return "—";
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (h > 0) return h + "h " + m + "m";
    return m + "m";
  }

  function pct(used, total) {
    if (!total) return 0;
    return Math.round((used / total) * 100);
  }

  function statusFromPct(p) {
    if (p >= 90) return "error";
    if (p >= 75) return "warn";
    return "ok";
  }

  /* ── Fetch helpers (tolerant — degrade gracefully) ──── */

  async function fetchJSON(url) {
    try {
      const r = await fetch(url);
      if (!r.ok) return null;
      return await r.json();
    } catch {
      return null;
    }
  }

  /* ── Data loaders ──────────────────────────────────────── */

  async function loadDetailed() {
    const d = await fetchJSON("/health/detailed");
    if (!d) return;

    /* Ollama card */
    if (d.ollama) {
      const alive = d.ollama.available;
      setCard("ollama", {
        status: alive ? "ok" : "warn",
        value: alive ? "OK" : "Offline",
        detail: (d.ollama.models || 0) + " models",
      });
    }

    /* DB card */
    if (d.db_pool) {
      const u = pct(d.db_pool.size - d.db_pool.free, d.db_pool.max);
      setCard("database", {
        status: d.status === "degraded" ? "error" : statusFromPct(u),
        value: d.status === "degraded" ? "Degraded" : "OK",
        detail: "Pool: " + d.db_pool.size + "/" + d.db_pool.max,
      });
    } else if (d.db_pool === null) {
      setCard("database", { status: "error", value: "N/A", detail: "No pool" });
    }

    /* Disk card */
    if (d.disk) {
      const u = d.disk.used_pct || 0;
      setCard("disk", {
        status: statusFromPct(u),
        value: u + "% used",
        detail: d.disk.free_gb + " GB free",
      });
    }

    /* System metrics */
    setText("metric-cpu", d.cpu_percent != null ? d.cpu_percent + "%" : "—");
    setText(
      "metric-ram",
      d.ram_rss_mb != null ? d.ram_rss_mb + " MB" : "—"
    );
    setText("metric-uptime", formatUptime(d.uptime_seconds));
    setText("metric-version", d.version || "—");

    /* Agents */
    if (d.agents) {
      const parts = [];
      parts.push(d.agents.total + " total");
      if (d.agents.working) parts.push(d.agents.working + " active");
      if (d.agents.idle) parts.push(d.agents.idle + " idle");
      setText("metric-agents", parts.join(", "));
    }
  }

  async function loadVRAM() {
    const d = await fetchJSON("/api/health/vram");
    if (!d) return;

    /* GPU status card */
    if (d.available && d.total_mb) {
      const usedPct = pct(d.used_mb, d.total_mb);
      setCard("gpu", {
        status: statusFromPct(usedPct),
        value: usedPct + "% VRAM",
        detail:
          Math.round(d.used_mb / 1024 * 10) / 10 +
          "/" +
          Math.round(d.total_mb / 1024 * 10) / 10 +
          " GB",
      });
    } else {
      setCard("gpu", {
        status: d.available ? "ok" : "warn",
        value: d.available ? "OK" : "N/A",
        detail: d.ollama_alive ? "Ollama OK" : "No GPU info",
      });
    }

    /* VRAM allocations list */
    const list = document.getElementById("vram-list");
    if (!list) return;

    const allocs = d.allocations || {};
    const entries = Object.entries(allocs);
    if (entries.length === 0 && !d.total_mb) {
      list.innerHTML = '<p class="health-muted">No VRAM data available</p>';
      return;
    }

    let html = "";
    const totalMb = d.total_mb || 1;
    if (entries.length > 0) {
      for (const [model, mb] of entries) {
        const p = pct(mb, totalMb);
        const high = p >= 75 ? " vram-bar-fill--high" : "";
        html +=
          '<div class="vram-model-row">' +
          '<span class="vram-model-name" title="' + model + '">' + model + "</span>" +
          '<div class="vram-bar-track"><div class="vram-bar-fill' + high + '" style="width:' + p + '%"></div></div>' +
          '<span class="vram-bar-label">' + Math.round(mb) + " / " + Math.round(totalMb) + " MB</span>" +
          "</div>";
      }
    } else if (d.total_mb) {
      const usedPct = pct(d.used_mb || 0, totalMb);
      const high = usedPct >= 75 ? " vram-bar-fill--high" : "";
      html +=
        '<div class="vram-model-row">' +
        '<span class="vram-model-name">Total VRAM</span>' +
        '<div class="vram-bar-track"><div class="vram-bar-fill' + high + '" style="width:' + usedPct + '%"></div></div>' +
        '<span class="vram-bar-label">' +
        Math.round((d.used_mb || 0) / 1024 * 10) / 10 + " / " +
        Math.round(totalMb / 1024 * 10) / 10 + " GB</span>" +
        "</div>";
    }
    list.innerHTML = html;
  }

  async function loadConnections() {
    const d = await fetchJSON("/api/health/connections");
    if (!d) return;

    const grid = document.getElementById("connections-grid");
    if (!grid) return;

    let html = "";

    /* DB pool */
    if (d.db_pool) {
      const label = d.db_pool.type === "sqlite" ? "SQLite" : "PostgreSQL Pool";
      const val =
        d.db_pool.type === "sqlite"
          ? "1 (file)"
          : d.db_pool.size + "/" + d.db_pool.max +
            " (" + (d.db_pool.utilization_pct || 0) + "%)";
      html += connectionItem(label, val);

      setText(
        "metric-db-pool",
        d.db_pool.type === "sqlite"
          ? "SQLite"
          : d.db_pool.size + "/" + d.db_pool.max
      );
    }

    /* WebSocket */
    if (d.websocket_clients != null) {
      html += connectionItem("WebSocket Clients", d.websocket_clients);
      setText("metric-ws-clients", String(d.websocket_clients));
    }

    /* Rate limiter */
    if (d.rate_limiter) {
      const val = d.rate_limiter.active ? "Active" : "Disabled";
      html += connectionItem("Rate Limiter", val);
      setText("metric-rate-limiter", val);
    }

    /* Agent count */
    if (d.agent_count != null) {
      html += connectionItem("Active Agents", d.agent_count);
    }

    /* Uptime */
    if (d.uptime_seconds != null) {
      html += connectionItem("Uptime", formatUptime(d.uptime_seconds));
    }

    grid.innerHTML = html || '<p class="health-muted">No connection data</p>';
  }

  function connectionItem(label, value) {
    return (
      '<div class="connection-item">' +
      '<span class="connection-item-label">' + label + "</span>" +
      '<span class="connection-item-value">' + value + "</span>" +
      "</div>"
    );
  }

  /* ── Refresh cycle ─────────────────────────────────────── */

  async function refreshAll() {
    await Promise.all([loadDetailed(), loadVRAM(), loadConnections()]);
  }

  /* ── Export report (copy JSON to clipboard) ────────────── */

  async function exportReport() {
    try {
      const [detailed, vram, conn] = await Promise.all([
        fetchJSON("/health/detailed"),
        fetchJSON("/api/health/vram"),
        fetchJSON("/api/health/connections"),
      ]);
      const report = {
        timestamp: new Date().toISOString(),
        detailed: detailed,
        vram: vram,
        connections: conn,
      };
      const text = JSON.stringify(report, null, 2);
      await navigator.clipboard.writeText(text);
      if (typeof showToast === "function") {
        showToast("Health report copied to clipboard", "success");
      }
    } catch (e) {
      console.error("Export failed:", e);
      if (typeof showToast === "function") {
        showToast("Export failed", "error");
      }
    }
  }

  /* ── Init ──────────────────────────────────────────────── */

  document.addEventListener("DOMContentLoaded", function () {
    refreshAll();
    setInterval(refreshAll, REFRESH_INTERVAL_MS);

    const btnRefresh = document.getElementById("btn-refresh-now");
    if (btnRefresh) btnRefresh.addEventListener("click", refreshAll);

    const btnExport = document.getElementById("btn-export-health");
    if (btnExport) btnExport.addEventListener("click", exportReport);
  });
})();
