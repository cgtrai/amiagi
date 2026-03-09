# amiagi v1.3.0 — UAT Readiness

## What's New

This release closes the final repair work before UAT and hardens the operator-facing web console.

### Highlights

- **Plan 02 closed** — final operator and workflow gaps resolved
- **2868 tests** in the collected suite
- **Critical regression gate passed** — 193 key operator tests green
- **Model catalog semantics fixed**
  - local models come from **Ollama**
  - commercial models are **user-defined only**
  - configurable providers: **OpenAI / Anthropic / Google**
- **Release-safe defaults restored** for permissions configuration

### Key Outcomes

- Better operator feedback in Teams, Settings, Model Hub, Agents, Evaluations, and Knowledge
- Safer frontend/backend contracts for workflow and management actions
- No hardcoded default cloud model list replacing local runtime inventory
- Documentation refreshed for the current release state

### Validation

| Check | Result |
|------|--------|
| Critical operator regression pack | **193 passed** |
| Focused model regression pack | **58 passed** |
| Full collected suite size | **2868 tests** |

### Release Documents

- [Release notes](RELEASE_NOTES_v1.3.0.md)
- [Web interface docs](WEB_INTERFACE.md)

### Upgrade Notes

- Ensure Ollama is available on target machines for local model discovery
- Configure commercial providers only with explicit user credentials and model names
- Review permission policy before enabling broader execution in production environments
