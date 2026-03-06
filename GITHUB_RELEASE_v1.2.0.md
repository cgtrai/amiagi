# amiagi v1.2.0 — Web Management Console

## What's New

Complete Web Management Console — all 5 UI sprints delivered, covering every system capability.

### Highlights

- **29 management tools** — every system feature now accessible from the browser
- **Liquid Glass v2** — 3-tier glass design system with shimmer, specular, and chromatic effects
- **Health Dashboard** — real-time system monitoring: Ollama, DB, GPU, agents, disk + VRAM bars
- **Sandbox Admin** — per-agent sandbox management with visual Shell Policy Editor
- **Model Hub** — model list, VRAM usage, pull, benchmark — all from the UI
- **Secret Vault** — Fernet-encrypted secrets with mask/reveal, rotation, access audit
- **Workflow Studio** — DAG visualization with run status and GATE highlights
- **Evaluation Suite** — eval runs, A/B tests, baselines — all with DB persistence + async workers
- **Knowledge Management** — bases, sources, chunking strategies, reindexing pipeline
- **Human-in-the-Loop** — `AskHumanTool` + `ReviewRequestTool` → Operator Inbox
- **Settings** — 12-tab redesign: General, Models, Costs, Cron, Integrations + 7 more
- **19 Web Components** — Shadow DOM components for all major UI features
- **490+ i18n keys** — full Polish + English coverage
- **12 DB migrations** — auto-applied on startup

### Stats

| Metric | v1.1.0 | v1.2.0 |
|--------|--------|--------|
| Tests | 1,902 | **2,543** (+641) |
| Screens | 6 | **17** (+11) |
| Web Components | 5 | **19** (+14) |
| API endpoints | 80+ | **100+** |
| DB migrations | 6 | **12** (+6) |
| i18n keys | 80+ | **490+** |
| CSS tokens | 40 | **71** |
| Component classes | 30 | **70+** |

### Sprint Delivery (all ✅)

| Sprint | Focus | Key Deliverables |
|--------|-------|------------------|
| S1 | Layout + Design System | Command Rail, Status Bar, Detail Drawer, Liquid Glass v2 |
| S2 | Supervisor + Inbox | Mission Control, Live Stream, Approval Cards, Agent Controls |
| S3 | Model Hub + Vault + Budget | Model management, Fernet vault, cost center |
| S4 | Workflows + Evals + Knowledge | DAG Studio, Eval Runner, Knowledge Pipeline |
| S5 | Health + Settings + Sandboxes | Health Dashboard, 12-tab Settings, Shell Policy Editor |

### Install / Upgrade

```bash
pip install -e ".[web]"
amiagi --ui web
```

### Requirements

- Python 3.10+
- PostgreSQL 13+ (or SQLite for development)
- Ollama (for LLM inference)

Full docs: [WEB_INTERFACE.md](WEB_INTERFACE.md) · [SECURITY.md](SECURITY.md) · [RELEASE_NOTES_v1.2.0.md](RELEASE_NOTES_v1.2.0.md)
