/**
 * <workflow-dag> — SVG-based DAG visualisation for workflow runs.
 *
 * Attributes:
 *   run-id — ID of the workflow run to render
 *
 * Data is set via the `setNodes(nodes)` method where each node has:
 *   { id, label, status, depends_on: [] }
 *
 * Usage:
 *   <workflow-dag run-id="abc123"></workflow-dag>
 *   el.setNodes([...])
 */
class WorkflowDag extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._nodes = [];
    this._nodeMap = {};
    this._positions = {};       // nodeId → {x, y}  (mutable after drag)
    this._dragging = null;      // { nodeId, offsetX, offsetY, g, startX, startY }
    this._NODE_W = 130;
    this._NODE_H = 50;
  }

  static get observedAttributes() {
    return ["run-id"];
  }

  connectedCallback() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; width: 100%; min-height: 280px; overflow: auto; }
        svg { width: 100%; height: 100%; }
        .node-rect { rx: 8; ry: 8; stroke-width: 1.5; cursor: grab; }
        .node-rect.pending   { fill: #1e293b; stroke: #475569; }
        .node-rect.running   { fill: #0c4a6e; stroke: #0ea5e9; }
        .node-rect.completed { fill: #14532d; stroke: #22c55e; }
        .node-rect.failed    { fill: #7f1d1d; stroke: #ef4444; }
        .node-rect.skipped   { fill: #1e1b4b; stroke: #818cf8; }
        .node-rect.waiting   { fill: #422006; stroke: #f59e0b; }
        .node-rect.waiting_approval { fill: #422006; stroke: #f59e0b; }
        :host(.dragging) .node-rect { cursor: grabbing; }
        .node-group { transition: transform .12s ease; }
        .node-group.is-dragging { transition: none; }
        .node-label {
          fill: #e2e8f0; font: 600 11px/1.2 system-ui, sans-serif;
          dominant-baseline: central; text-anchor: middle;
          pointer-events: none; user-select: none;
        }
        .node-status {
          fill: #94a3b8; font: 400 9px/1 system-ui, sans-serif;
          dominant-baseline: central; text-anchor: middle;
          pointer-events: none;
        }
        .edge { stroke: #475569; stroke-width: 1.2; fill: none; marker-end: url(#arrow); }
        .dag-empty {
          display: flex; align-items: center; justify-content: center;
          height: 200px; color: #64748b; font-size: .9rem;
        }
      </style>
      <div class="dag-empty" id="empty">No DAG data — select a workflow run</div>
      <svg id="svg" xmlns="http://www.w3.org/2000/svg" hidden>
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5"
                  markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#475569"/>
          </marker>
        </defs>
      </svg>
    `;
    this._svg = this.shadowRoot.getElementById("svg");
    this._emptyEl = this.shadowRoot.getElementById("empty");

    // ── Drag-and-drop handlers ──────────────────────────────
    this._onPointerMove = this._handlePointerMove.bind(this);
    this._onPointerUp   = this._handlePointerUp.bind(this);
  }

  disconnectedCallback() {
    // Cleanup global listeners just in case
    document.removeEventListener("pointermove", this._onPointerMove);
    document.removeEventListener("pointerup", this._onPointerUp);
  }

  /* ── SVG coordinate helper ─────────────────────────────── */
  _clientToSVG(clientX, clientY) {
    const pt = this._svg.createSVGPoint();
    pt.x = clientX;
    pt.y = clientY;
    const ctm = this._svg.getScreenCTM();
    if (!ctm) return { x: clientX, y: clientY };
    return pt.matrixTransform(ctm.inverse());
  }

  /* ── Drag start (pointerdown on a node <g>) ────────────── */
  _handlePointerDown(e, nodeId, g) {
    e.preventDefault();
    e.stopPropagation();
    const svgPt = this._clientToSVG(e.clientX, e.clientY);
    const pos = this._positions[nodeId];
    this._dragging = {
      nodeId,
      g,
      offsetX: svgPt.x - pos.x,
      offsetY: svgPt.y - pos.y,
      startX: pos.x,
      startY: pos.y,
      moved: false,
    };
    g.classList.add("is-dragging");
    this.classList.add("dragging");
    document.addEventListener("pointermove", this._onPointerMove);
    document.addEventListener("pointerup", this._onPointerUp);
  }

  /* ── Drag move ─────────────────────────────────────────── */
  _handlePointerMove(e) {
    if (!this._dragging) return;
    e.preventDefault();
    const svgPt = this._clientToSVG(e.clientX, e.clientY);
    const nx = svgPt.x - this._dragging.offsetX;
    const ny = svgPt.y - this._dragging.offsetY;
    this._positions[this._dragging.nodeId] = { x: nx, y: ny };
    this._dragging.moved = true;
    this._updateNodeTransform(this._dragging.nodeId);
    this._redrawEdges();
  }

  /* ── Drag end ──────────────────────────────────────────── */
  _handlePointerUp(e) {
    document.removeEventListener("pointermove", this._onPointerMove);
    document.removeEventListener("pointerup", this._onPointerUp);
    if (!this._dragging) return;
    const d = this._dragging;
    d.g.classList.remove("is-dragging");
    this.classList.remove("dragging");
    if (d.moved) {
      this._justDragged = true;
      // Reset flag after current event cycle so click handler sees it
      requestAnimationFrame(() => { this._justDragged = false; });
      this.dispatchEvent(new CustomEvent("node-moved", {
        detail: {
          nodeId: d.nodeId,
          x: this._positions[d.nodeId].x,
          y: this._positions[d.nodeId].y,
        },
      }));
    }
    this._dragging = null;
  }

  /* ── Move a single node <g> via transform ──────────────── */
  _updateNodeTransform(nodeId) {
    const g = this._nodeGroups && this._nodeGroups[nodeId];
    if (!g) return;
    const pos = this._positions[nodeId];
    const orig = this._origPositions[nodeId];
    const dx = pos.x - orig.x;
    const dy = pos.y - orig.y;
    g.setAttribute("transform", `translate(${dx},${dy})`);
  }

  /* ── Redraw all edges (fast — just update line coords) ── */
  _redrawEdges() {
    if (!this._edgeLines) return;
    const W = this._NODE_W, H = this._NODE_H;
    this._edgeLines.forEach(({ line, fromId, toId }) => {
      const from = this._positions[fromId];
      const to   = this._positions[toId];
      if (!from || !to) return;
      line.setAttribute("x1", from.x + W);
      line.setAttribute("y1", from.y + H / 2);
      line.setAttribute("x2", to.x);
      line.setAttribute("y2", to.y + H / 2);
    });
  }

  /**
   * Set DAG nodes and render.
   * @param {Array<{id: string, label: string, status: string, depends_on: string[]}>} nodes
   */
  setNodes(nodes) {
    this._nodes = nodes || [];
    this._nodeMap = {};
    this._nodes.forEach((n) => { this._nodeMap[n.id] = n; });
    this._render();
  }

  /* ── Layout engine (simple layered / topological sort) ── */
  _render() {
    if (!this._svg) return;
    if (this._nodes.length === 0) {
      this._svg.hidden = true;
      this._emptyEl.hidden = false;
      return;
    }
    this._svg.hidden = false;
    this._emptyEl.hidden = true;

    // Topological layers
    const layers = this._topoLayers();
    const NODE_W = this._NODE_W, NODE_H = this._NODE_H, PAD_X = 40, PAD_Y = 30;

    // Compute positions (only if not already dragged)
    const positions = {};
    let maxLayerSize = 0;
    layers.forEach((layer, li) => {
      maxLayerSize = Math.max(maxLayerSize, layer.length);
      layer.forEach((nodeId, ni) => {
        // Keep user-dragged position if it exists
        if (this._positions[nodeId]) {
          positions[nodeId] = this._positions[nodeId];
        } else {
          positions[nodeId] = {
            x: PAD_X + li * (NODE_W + PAD_X),
            y: PAD_Y + ni * (NODE_H + PAD_Y),
          };
        }
      });
    });
    this._positions = positions;
    // Store original (layout) positions for transform-based dragging
    this._origPositions = {};
    layers.forEach((layer, li) => {
      layer.forEach((nodeId, ni) => {
        this._origPositions[nodeId] = {
          x: PAD_X + li * (NODE_W + PAD_X),
          y: PAD_Y + ni * (NODE_H + PAD_Y),
        };
      });
    });

    const svgW = PAD_X + layers.length * (NODE_W + PAD_X);
    const svgH = PAD_Y + maxLayerSize * (NODE_H + PAD_Y);
    this._svg.setAttribute("viewBox", `0 0 ${svgW} ${svgH}`);
    this._svg.style.height = `${Math.max(280, svgH)}px`;

    // Clear previous geometry (keep defs)
    Array.from(this._svg.children).forEach((c) => {
      if (c.tagName !== "defs") c.remove();
    });

    const ns = "http://www.w3.org/2000/svg";
    this._edgeLines = [];
    this._nodeGroups = {};

    // Edges first (behind nodes)
    this._nodes.forEach((node) => {
      (node.depends_on || []).forEach((depId) => {
        if (!positions[depId]) return;
        const from = positions[depId];
        const to = positions[node.id];
        const line = document.createElementNS(ns, "line");
        line.setAttribute("class", "edge");
        line.setAttribute("x1", from.x + NODE_W);
        line.setAttribute("y1", from.y + NODE_H / 2);
        line.setAttribute("x2", to.x);
        line.setAttribute("y2", to.y + NODE_H / 2);
        this._svg.appendChild(line);
        this._edgeLines.push({ line, fromId: depId, toId: node.id });
      });
    });

    // Nodes
    this._nodes.forEach((node) => {
      const orig = this._origPositions[node.id];
      const pos  = positions[node.id];
      if (!orig || !pos) return;

      const g = document.createElementNS(ns, "g");
      g.setAttribute("class", "node-group");
      // Apply drag offset as transform
      const dx = pos.x - orig.x;
      const dy = pos.y - orig.y;
      if (dx || dy) g.setAttribute("transform", `translate(${dx},${dy})`);

      const rect = document.createElementNS(ns, "rect");
      rect.setAttribute("class", `node-rect ${node.status || "pending"}`);
      rect.setAttribute("x", orig.x);
      rect.setAttribute("y", orig.y);
      rect.setAttribute("width", NODE_W);
      rect.setAttribute("height", NODE_H);

      // W2 — GATE highlight
      if (node.type === "gate" || node.type === "GATE") {
        rect.setAttribute("stroke", "#f59e0b");
        rect.setAttribute("stroke-width", "2.5");
      }

      g.appendChild(rect);

      const label = document.createElementNS(ns, "text");
      label.setAttribute("class", "node-label");
      label.setAttribute("x", orig.x + NODE_W / 2);
      label.setAttribute("y", orig.y + NODE_H / 2 - 6);
      label.textContent = node.label || node.id;
      g.appendChild(label);

      const status = document.createElementNS(ns, "text");
      status.setAttribute("class", "node-status");
      status.setAttribute("x", orig.x + NODE_W / 2);
      status.setAttribute("y", orig.y + NODE_H / 2 + 10);
      const statusParts = [node.status || "pending"];
      if (node.type === "gate" || node.type === "GATE") {
        statusParts.push("GATE");
      }
      status.textContent = statusParts.join(" • ");
      g.appendChild(status);

      // W1 — Node progress counter
      if (node.progress) {
        const progText = document.createElementNS(ns, "text");
        progText.setAttribute("x", orig.x + NODE_W / 2);
        progText.setAttribute("y", orig.y + NODE_H - 4);
        progText.textContent = node.progress;
        progText.setAttribute("font-size", "10");
        progText.setAttribute("fill", "#94a3b8");
        progText.setAttribute("text-anchor", "middle");
        progText.setAttribute("pointer-events", "none");
        g.appendChild(progText);
      }

      // Click → emit node-click (only if user didn't drag)
      g.addEventListener("click", (ev) => {
        if (this._justDragged) return;
        this.dispatchEvent(new CustomEvent("node-click", { detail: node }));
      });

      // Pointer-based drag-and-drop
      g.addEventListener("pointerdown", (ev) => this._handlePointerDown(ev, node.id, g));

      this._nodeGroups[node.id] = g;
      this._svg.appendChild(g);
    });

    this._justDragged = false;
  }

  /**
   * Topological sort into layers (Kahn's algorithm).
   */
  _topoLayers() {
    const indeg = {};
    const adj = {};
    this._nodes.forEach((n) => {
      indeg[n.id] = 0;
      adj[n.id] = [];
    });
    this._nodes.forEach((n) => {
      (n.depends_on || []).forEach((d) => {
        if (adj[d]) {
          adj[d].push(n.id);
          indeg[n.id]++;
        }
      });
    });

    const layers = [];
    let queue = Object.keys(indeg).filter((id) => indeg[id] === 0);
    while (queue.length > 0) {
      layers.push([...queue]);
      const next = [];
      queue.forEach((id) => {
        (adj[id] || []).forEach((child) => {
          indeg[child]--;
          if (indeg[child] === 0) next.push(child);
        });
      });
      queue = next;
    }
    return layers;
  }
}

customElements.define("workflow-dag", WorkflowDag);
