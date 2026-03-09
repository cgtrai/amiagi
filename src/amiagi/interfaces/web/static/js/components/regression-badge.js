/**
 * <regression-badge> — Colored delta indicator for evaluation results.
 *
 * Attributes:
 *   delta     — numeric score difference (e.g. -3.2, +7.1)
 *   threshold — minimum absolute delta to consider significant (default 5)
 *
 * Usage:
 *   <regression-badge delta="-8.3" threshold="5"></regression-badge>
 */
class RegressionBadge extends HTMLElement {
  static get observedAttributes() {
    return ["delta", "threshold"];
  }

  connectedCallback() {
    this._render();
  }

  attributeChangedCallback() {
    this._render();
  }

  _render() {
    const delta = parseFloat(this.getAttribute("delta") || "0");
    const threshold = parseFloat(this.getAttribute("threshold") || "5");
    const isRegression = delta < -threshold;
    const isImprovement = delta > threshold;
    const color = isRegression
      ? "var(--glass-danger, #ef4444)"
      : isImprovement
        ? "var(--glass-success, #22c55e)"
        : "var(--glass-text-secondary, #94a3b8)";
    const arrow = isRegression ? "↓" : isImprovement ? "↑" : "→";
    const sign = delta > 0 ? "+" : "";
    this.innerHTML = `<span class="regression-indicator" style="color:${color};font-weight:600;font-size:.85rem">${arrow} ${sign}${delta.toFixed(1)}%</span>`;
  }
}

customElements.define("regression-badge", RegressionBadge);
