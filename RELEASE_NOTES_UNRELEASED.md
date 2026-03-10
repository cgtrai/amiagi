# amiagi (unreleased)

Changes staged after v1.3.0 and prepared for v1.3.1.

## Release scope

- communication-centric Supervisor with per-agent embedded screens and clearer operator routing
- live runtime session accounting for tokens, cost, and GPU usage in web mode
- stale web-session takeover on startup so a previous local GUI instance does not block the port
- stronger WebSocket continuity with replay, reconnect state, and normalized stream routing
- project-scoped runtime skills and task dossier guidance for executor flows
- safer cold start cleanup of runtime artifacts and working state

## Release notes

- detailed notes: `RELEASE_NOTES_v1.3.1.md`
- GitHub release summary: `GITHUB_RELEASE_v1.3.1.md`

## Validation completed in this cycle

- `tests/test_web_app_health.py -q`
- `tests/test_supervisor_web_ui.py tests/test_notifications_drawer_extended.py tests/test_faza11_responsive.py -q`

## UAT

- focused `/supervisor` UAT scope marked complete for v1.3.1 preparation
- scenarios and closure notes updated in `devdocs/UAT_SCENARIOS_v1.3.1.md`
