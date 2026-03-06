/**
 * <ab-comparison> — Side-by-side A/B test comparison Web Component.
 *
 * Data is set via `setCampaign(campaign)` where campaign has:
 *   { name, variant_a: { name, scores }, variant_b: { name, scores }, winner }
 *
 * Usage:
 *   <ab-comparison></ab-comparison>
 *   el.setCampaign({ ... })
 */
class AbComparison extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._campaign = null;
  }

  connectedCallback() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; width: 100%; }
        .comparison-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
        .variant-card {
          padding: 1rem; border-radius: 10px;
          background: rgba(15,23,42,.6); border: 1px solid rgba(148,163,184,.12);
        }
        .variant-card.winner { border-color: #22c55e; box-shadow: 0 0 12px rgba(34,197,94,.15); }
        .variant-header {
          display: flex; justify-content: space-between; align-items: center;
          margin-bottom: .75rem;
        }
        .variant-name { font-weight: 700; font-size: 1rem; color: #f1f5f9; }
        .badge-winner {
          background: #22c55e; color: #052e16; font-size: .7rem; font-weight: 700;
          padding: .15rem .5rem; border-radius: 999px;
        }
        .badge-loser {
          background: #475569; color: #cbd5e1; font-size: .7rem; font-weight: 600;
          padding: .15rem .5rem; border-radius: 999px;
        }
        .score-row {
          display: flex; justify-content: space-between; align-items: center;
          padding: .35rem 0; border-bottom: 1px solid rgba(148,163,184,.06);
          font-size: .85rem; color: #cbd5e1;
        }
        .score-row:last-child { border-bottom: none; }
        .score-value { font-weight: 700; font-variant-numeric: tabular-nums; }
        .score-value.better  { color: #4ade80; }
        .score-value.worse   { color: #f87171; }
        .score-value.neutral { color: #94a3b8; }
        .empty-state {
          text-align: center; color: #64748b; padding: 2rem; font-size: .9rem;
        }
        .title { font-size: 1.1rem; font-weight: 700; color: #f1f5f9; margin-bottom: .75rem; }
      </style>
      <div id="container">
        <div class="empty-state">No A/B campaign data loaded</div>
      </div>
    `;
    this._container = this.shadowRoot.getElementById("container");
    if (this._campaign) this._render();
  }

  disconnectedCallback() {}

  /**
   * @param {{name: string, variant_a: {name: string, scores: Object}, variant_b: {name: string, scores: Object}, winner?: string}} campaign
   */
  setCampaign(campaign) {
    this._campaign = campaign;
    if (this._container) this._render();
  }

  _render() {
    const c = this._campaign;
    if (!c) {
      this._container.innerHTML = '<div class="empty-state">No A/B campaign data loaded</div>';
      return;
    }

    const allKeys = new Set([
      ...Object.keys(c.variant_a?.scores || {}),
      ...Object.keys(c.variant_b?.scores || {}),
    ]);

    const renderVariant = (variant, isWinner) => {
      const scores = variant?.scores || {};
      const otherScores = isWinner ? (c.variant_b?.scores || {}) : (c.variant_a?.scores || {});

      return `
        <div class="variant-card ${isWinner ? 'winner' : ''}">
          <div class="variant-header">
            <span class="variant-name">${this._esc(variant?.name || "Variant")}</span>
            <span class="${isWinner ? 'badge-winner' : 'badge-loser'}">${isWinner ? '🏆 Winner' : 'Baseline'}</span>
          </div>
          ${[...allKeys].map((key) => {
            const val = scores[key] ?? 0;
            const otherVal = otherScores[key] ?? 0;
            let cls = "neutral";
            if (val > otherVal) cls = "better";
            else if (val < otherVal) cls = "worse";
            return `<div class="score-row">
              <span>${this._esc(key)}</span>
              <span class="score-value ${cls}">${typeof val === "number" ? val.toFixed(3) : val}</span>
            </div>`;
          }).join("")}
        </div>
      `;
    };

    const aIsWinner = c.winner === "a" || c.winner === c.variant_a?.name;
    this._container.innerHTML = `
      <div class="title">${this._esc(c.name || "A/B Comparison")}</div>
      <div class="comparison-grid">
        ${renderVariant(c.variant_a, aIsWinner)}
        ${renderVariant(c.variant_b, !aIsWinner)}
      </div>
    `;
  }

  _esc(s) {
    if (!s) return "";
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
}

customElements.define("ab-comparison", AbComparison);
