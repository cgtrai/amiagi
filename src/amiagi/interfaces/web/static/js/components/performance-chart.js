/**
 * <performance-chart> — Canvas-based performance chart Web Component.
 *
 * Attributes:
 *   endpoint — URL to fetch data from (default: /api/performance)
 *   interval — auto-refresh interval in seconds (default: 60, 0 = no auto)
 *   metric   — which metric to plot: "latency" | "throughput" | "tokens" (default: latency)
 *
 * Usage:
 *   <performance-chart endpoint="/api/performance" interval="30" metric="latency"></performance-chart>
 */
class PerformanceChart extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._data = [];
    this._timer = null;
  }

  static get observedAttributes() {
    return ["endpoint", "interval", "metric"];
  }

  get endpoint() {
    return this.getAttribute("endpoint") || "/api/performance";
  }

  get interval() {
    return parseInt(this.getAttribute("interval") || "60", 10);
  }

  get metric() {
    return this.getAttribute("metric") || "latency";
  }

  connectedCallback() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; width: 100%; min-height: 200px; }
        canvas { width: 100%; height: 100%; display: block; }
        .chart-header {
          display: flex; justify-content: space-between; align-items: center;
          font-size: .85rem; color: #94a3b8; margin-bottom: .5rem;
        }
        .chart-title { font-weight: 600; color: #f1f5f9; }
        .chart-empty {
          display: flex; align-items: center; justify-content: center;
          height: 180px; color: #64748b; font-size: .9rem;
        }
      </style>
      <div class="chart-header">
        <span class="chart-title">${this._titleFor(this.metric)}</span>
        <span id="summary"></span>
      </div>
      <canvas id="canvas" width="600" height="200"></canvas>
      <div class="chart-empty" id="empty" hidden>No data available</div>
    `;

    this._canvas = this.shadowRoot.getElementById("canvas");
    this._ctx = this._canvas.getContext("2d");
    this._emptyEl = this.shadowRoot.getElementById("empty");
    this._summaryEl = this.shadowRoot.getElementById("summary");

    this.load();
    if (this.interval > 0) {
      this._timer = setInterval(() => this.load(), this.interval * 1000);
    }
  }

  disconnectedCallback() {
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
  }

  attributeChangedCallback() {
    if (this._canvas) this.load();
  }

  async load() {
    try {
      const resp = await fetch(this.endpoint);
      if (!resp.ok) throw new Error(resp.statusText);
      const json = await resp.json();
      this._data = Array.isArray(json) ? json : json.data || json.metrics || [];
      this._render();
    } catch {
      this._data = [];
      this._render();
    }
  }

  _render() {
    const data = this._data;
    if (!data.length) {
      this._canvas.hidden = true;
      this._emptyEl.hidden = false;
      this._summaryEl.textContent = "";
      return;
    }
    this._canvas.hidden = false;
    this._emptyEl.hidden = true;

    const metric = this.metric;
    const values = data.map(d => {
      if (metric === "throughput") return d.throughput ?? d.requests ?? 0;
      if (metric === "tokens") return d.tokens ?? d.token_count ?? 0;
      return d.latency ?? d.duration_ms ?? d.avg_latency ?? 0;
    });

    const max = Math.max(...values, 1);
    const min = Math.min(...values, 0);
    const avg = values.reduce((a, b) => a + b, 0) / values.length;

    this._summaryEl.textContent = `avg: ${avg.toFixed(1)} | max: ${max.toFixed(1)}`;

    const ctx = this._ctx;
    const W = this._canvas.width;
    const H = this._canvas.height;
    const pad = { top: 10, right: 10, bottom: 25, left: 45 };
    const plotW = W - pad.left - pad.right;
    const plotH = H - pad.top - pad.bottom;

    ctx.clearRect(0, 0, W, H);

    // Grid
    ctx.strokeStyle = "rgba(148,163,184,.15)";
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = pad.top + (plotH / 4) * i;
      ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(W - pad.right, y); ctx.stroke();
      ctx.fillStyle = "#64748b";
      ctx.font = "10px system-ui";
      ctx.textAlign = "right";
      ctx.fillText((max - (max - min) * (i / 4)).toFixed(0), pad.left - 5, y + 3);
    }

    // Line
    ctx.strokeStyle = "#6366f1";
    ctx.lineWidth = 2;
    ctx.beginPath();
    values.forEach((v, i) => {
      const x = pad.left + (i / Math.max(values.length - 1, 1)) * plotW;
      const y = pad.top + plotH - ((v - min) / (max - min || 1)) * plotH;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();

    // Area fill
    const lastX = pad.left + plotW;
    ctx.lineTo(lastX, pad.top + plotH);
    ctx.lineTo(pad.left, pad.top + plotH);
    ctx.closePath();
    ctx.fillStyle = "rgba(99,102,241,.12)";
    ctx.fill();

    // Dots
    ctx.fillStyle = "#6366f1";
    values.forEach((v, i) => {
      const x = pad.left + (i / Math.max(values.length - 1, 1)) * plotW;
      const y = pad.top + plotH - ((v - min) / (max - min || 1)) * plotH;
      ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fill();
    });
  }

  _titleFor(metric) {
    if (metric === "throughput") return "Throughput";
    if (metric === "tokens") return "Token Usage";
    return "Response Latency";
  }
}

customElements.define("performance-chart", PerformanceChart);
