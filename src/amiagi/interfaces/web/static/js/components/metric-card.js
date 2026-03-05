/**
 * <metric-card> Web Component
 *
 * Compact card displaying a single metric value with label and optional trend.
 *
 * Attributes: label, value, unit, trend (up|down|neutral), color
 */
class MetricCard extends HTMLElement {
  static get observedAttributes() {
    return ["label", "value", "unit", "trend", "color"];
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  connectedCallback() {
    this.render();
  }

  attributeChangedCallback() {
    this.render();
  }

  get label() { return this.getAttribute("label") || "Metric"; }
  get value() { return this.getAttribute("value") || "0"; }
  get unit() { return this.getAttribute("unit") || ""; }
  get trend() { return this.getAttribute("trend") || "neutral"; }
  get color() { return this.getAttribute("color") || "var(--accent-primary, #6366f1)"; }

  _trendIcon() {
    const map = { up: "↑", down: "↓", neutral: "→" };
    return map[this.trend] || "→";
  }

  _trendColor() {
    const map = {
      up: "var(--color-success, #4ade80)",
      down: "var(--color-error, #ef4444)",
      neutral: "var(--text-muted, #94a3b8)",
    };
    return map[this.trend] || map.neutral;
  }

  render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        .metric {
          background: var(--glass-bg, rgba(255,255,255,0.06));
          backdrop-filter: blur(var(--glass-blur, 12px));
          border: 1px solid var(--glass-border, rgba(255,255,255,0.08));
          border-radius: var(--radius-lg, 16px);
          padding: var(--space-4, 1rem);
          text-align: center;
          position: relative;
          overflow: hidden;
        }
        .metric::before {
          content: "";
          position: absolute;
          top: 0; left: 0;
          width: 100%; height: 3px;
          background: ${this.color};
        }
        .label {
          font-size: 0.75rem;
          font-weight: 600;
          text-transform: uppercase;
          letter-spacing: 0.06em;
          color: var(--text-muted, #94a3b8);
          margin-bottom: var(--space-1, 0.25rem);
        }
        .value {
          font-size: 1.8rem;
          font-weight: 700;
          color: var(--text-primary, #f1f5f9);
          line-height: 1.2;
          font-variant-numeric: tabular-nums;
        }
        .unit {
          font-size: 0.9rem;
          font-weight: 400;
          color: var(--text-secondary, #cbd5e1);
          margin-left: 2px;
        }
        .trend {
          font-size: 0.8rem;
          font-weight: 600;
          margin-top: var(--space-1, 0.25rem);
          color: ${this._trendColor()};
        }
      </style>
      <div class="metric" part="metric">
        <div class="label">${this.label}</div>
        <div class="value">${this.value}<span class="unit">${this.unit}</span></div>
        <div class="trend">${this._trendIcon()}</div>
      </div>
    `;
  }
}

customElements.define("metric-card", MetricCard);
