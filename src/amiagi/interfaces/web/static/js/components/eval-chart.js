/**
 * <eval-chart> — Canvas-based bar chart for evaluation results.
 *
 * Attributes:
 *   eval-id — evaluation run ID to display
 *
 * Data is set via `setData(results)` where each item is:
 *   { scenario: string, score: number, max: number }
 *
 * Usage:
 *   <eval-chart eval-id="ev_123"></eval-chart>
 *   el.setData([{ scenario: "accuracy", score: 0.85, max: 1.0 }])
 */
class EvalChart extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._data = [];
  }

  static get observedAttributes() {
    return ["eval-id"];
  }

  connectedCallback() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; width: 100%; min-height: 200px; }
        canvas { width: 100%; height: 100%; display: block; }
        .chart-empty {
          display: flex; align-items: center; justify-content: center;
          height: 180px; color: #64748b; font-size: .9rem;
        }
        .chart-header {
          display: flex; justify-content: space-between; align-items: center;
          margin-bottom: .5rem; font-size: .85rem; color: #94a3b8;
        }
        .chart-title { font-weight: 600; color: #f1f5f9; }
      </style>
      <div class="chart-header">
        <span class="chart-title">Evaluation Results</span>
        <span id="summary"></span>
      </div>
      <canvas id="canvas" width="600" height="240"></canvas>
      <div class="chart-empty" id="empty" hidden>No evaluation data</div>
    `;
    this._canvas = this.shadowRoot.getElementById("canvas");
    this._ctx = this._canvas.getContext("2d");
    this._emptyEl = this.shadowRoot.getElementById("empty");
    this._summaryEl = this.shadowRoot.getElementById("summary");

    // EV6 — click handler for bar interaction
    this._canvas.addEventListener("click", (e) => {
      const point = this._hitTest(e.offsetX, e.offsetY);
      if (point) {
        this.dispatchEvent(new CustomEvent("point-click", { detail: point, bubbles: true }));
      }
    });

    this._draw();
  }

  disconnectedCallback() {}

  attributeChangedCallback() {
    if (this._canvas) this._draw();
  }

  /**
   * @param {Array<{scenario: string, score: number, max?: number}>} data
   */
  setData(data) {
    this._data = data || [];
    this._draw();
  }

  _draw() {
    if (!this._ctx) return;
    const data = this._data;
    const canvas = this._canvas;
    const ctx = this._ctx;
    const dpr = window.devicePixelRatio || 1;

    const w = canvas.clientWidth || 600;
    const h = canvas.clientHeight || 240;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    ctx.scale(dpr, dpr);

    ctx.clearRect(0, 0, w, h);

    if (data.length === 0) {
      this._emptyEl.hidden = false;
      canvas.hidden = true;
      this._barRects = [];
      return;
    }
    this._emptyEl.hidden = true;
    canvas.hidden = false;

    // Layout constants
    const PAD_LEFT = 100, PAD_RIGHT = 20, PAD_TOP = 10, PAD_BOTTOM = 10;
    const chartW = w - PAD_LEFT - PAD_RIGHT;
    const barH = Math.min(28, (h - PAD_TOP - PAD_BOTTOM) / data.length - 4);
    const gap = 4;

    // Summary
    const avg = data.reduce((s, d) => s + d.score, 0) / data.length;
    this._summaryEl.textContent = `avg: ${avg.toFixed(3)}`;

    // Store bar rects for hit-testing
    this._barRects = [];

    // Draw bars
    data.forEach((item, i) => {
      const y = PAD_TOP + i * (barH + gap);
      const maxVal = item.max || 1;
      const pct = Math.max(0, Math.min(1, item.score / maxVal));
      const barW = pct * chartW;

      this._barRects.push({ x: PAD_LEFT, y, w: chartW, h: barH, item, index: i });

      // Background track
      ctx.fillStyle = "rgba(71,85,105,.25)";
      ctx.beginPath();
      ctx.roundRect(PAD_LEFT, y, chartW, barH, 4);
      ctx.fill();

      // Score bar
      const color = pct >= 0.8 ? "#22c55e" : pct >= 0.5 ? "#f59e0b" : "#ef4444";
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.roundRect(PAD_LEFT, y, Math.max(barW, 2), barH, 4);
      ctx.fill();

      // Label
      ctx.fillStyle = "#e2e8f0";
      ctx.font = "600 11px system-ui, sans-serif";
      ctx.textAlign = "right";
      ctx.textBaseline = "middle";
      ctx.fillText(
        item.scenario.length > 14 ? item.scenario.slice(0, 13) + "…" : item.scenario,
        PAD_LEFT - 8,
        y + barH / 2,
      );

      // Value on bar
      ctx.fillStyle = "#f8fafc";
      ctx.font = "600 10px system-ui, sans-serif";
      ctx.textAlign = "left";
      ctx.fillText(item.score.toFixed(3), PAD_LEFT + barW + 4, y + barH / 2);
    });
  }

  /**
   * EV6 — hit-test clicked position against stored bar rects.
   * @param {number} x — offsetX from click event
   * @param {number} y — offsetY from click event
   * @returns {object|null} — matched data item or null
   */
  _hitTest(x, y) {
    if (!this._barRects) return null;
    for (const rect of this._barRects) {
      if (x >= rect.x && x <= rect.x + rect.w && y >= rect.y && y <= rect.y + rect.h) {
        return { scenario: rect.item.scenario, score: rect.item.score, index: rect.index, item: rect.item };
      }
    }
    return null;
  }
}

customElements.define("eval-chart", EvalChart);
