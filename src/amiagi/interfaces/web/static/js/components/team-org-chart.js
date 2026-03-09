/**
 * <team-org-chart> — SVG-based org chart visualization for teams.
 * Usage: el.setTeams(teamsArray) → renders tree with supervisor at top, agents below.
 */
class TeamOrgChart extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._teams = [];
  }

  connectedCallback() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        .org-container { overflow-x: auto; padding: 12px; }
        .team-section { margin-bottom: 28px; }
        .team-label { font-size: 14px; font-weight: 700; color: var(--text-primary, #e2e8f0); margin-bottom: 10px; }
        .org-tree { display: flex; flex-direction: column; align-items: center; gap: 8px; }
        .org-node {
          background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.12);
          border-radius: 12px; padding: 10px 18px; cursor: pointer;
          text-align: center; min-width: 140px; transition: border-color 0.15s;
        }
        .org-node:hover { border-color: rgba(99,102,241,0.7); }
        .org-node--lead { border-color: rgba(99,102,241,0.5); background: rgba(99,102,241,0.08); }
        .node-name { font-size: 13px; font-weight: 600; color: #e2e8f0; }
        .node-role { font-size: 11px; color: #94a3b8; margin-top: 2px; }
        .node-model { font-size: 10px; color: #64748b; margin-top: 2px; }
        .connector { width: 2px; height: 18px; background: rgba(255,255,255,0.12); margin: 0 auto; }
        .children { display: flex; gap: 12px; flex-wrap: wrap; justify-content: center; }
        .branch-line { display: flex; justify-content: center; margin-bottom: 4px; }
        .branch-line::before { content: ''; display: block; width: 60%; height: 2px; background: rgba(255,255,255,0.08); margin-top: 8px; }
      </style>
      <div class="org-container"></div>
    `;
  }

  setTeams(teams) {
    this._teams = teams || [];
    this._render();
  }

  _render() {
    const container = this.shadowRoot.querySelector('.org-container');
    if (!this._teams.length) {
      container.innerHTML = '<p style="color:#94a3b8;text-align:center">No teams</p>';
      return;
    }
    container.innerHTML = this._teams.map(team => this._renderTeam(team)).join('');
  }

  _renderTeam(team) {
    const members = team.members || [];
    const lead = members.find(m => m.role === team.lead_agent_id || m.role === 'supervisor' || m.role === 'lead') || members[0];
    const others = members.filter(m => m !== lead);

    const leadNode = lead ? this._nodeHTML(lead, true, team.team_id) : '';
    const childNodes = others.map(m => this._nodeHTML(m, false, team.team_id)).join('');

    return `<div class="team-section">
      <div class="team-label">${this._esc(team.name || team.team_id || '—')}</div>
      <div class="org-tree">
        ${leadNode}
        ${others.length ? '<div class="connector"></div>' : ''}
        ${others.length > 1 ? '<div class="branch-line"></div>' : ''}
        ${others.length ? `<div class="children">${childNodes}</div>` : ''}
      </div>
    </div>`;
  }

  _nodeHTML(member, isLead, teamId) {
    const name = member.name || member.agent_id || member.id || '—';
    const role = member.role || '';
    const model = member.model || '';
    return `<div class="org-node ${isLead ? 'org-node--lead' : ''}"
                 data-agent="${this._esc(name)}" data-team="${this._esc(teamId)}">
      <div class="node-name">${this._esc(name)}</div>
      <div class="node-role">${this._esc(role)}${isLead ? ' ★' : ''}</div>
      ${model ? `<div class="node-model">${this._esc(model)}</div>` : ''}
    </div>`;
  }

  _esc(s) {
    const d = document.createElement('span');
    d.textContent = s || '';
    return d.innerHTML;
  }
}

customElements.define('team-org-chart', TeamOrgChart);
