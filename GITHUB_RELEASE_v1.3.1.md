# amiagi v1.3.1 — Supervisor Operations

## What's New

This release closes the `/supervisor` UAT stream and turns the web operator surface into a tighter operational console.

### Highlights

- **Supervisor is now the default operator entrypoint**
- **Session metrics finally work in web mode**
  - tokens accrue correctly
  - session cost includes energy cost
  - GPU RAM used and GPU utilization are visible in the topbar
- **Web startup reclaims stale local sessions** before rebinding the dashboard port
- **Live stream continuity improved** with reconnect state, replay, and normalized routing metadata
- **Project Skills added** for project-scoped runtime guidance without rebuilding the main skill catalog

## Key Outcomes

- clearer operator experience in Supervisor
- fewer duplicate controls across topbar, nav, and status bar
- more reliable visibility into runtime spend and activity
- safer repeated local web launches during development and UAT

## Validation

| Check | Result |
|------|--------|
| Web health / startup pack | **13 passed** |
| Supervisor + nav focused UI pack | **38 passed** |
| Focused cost/UI/budget pack | **33 passed** |

## Release Documents

- [Release notes](RELEASE_NOTES_v1.3.1.md)
- [Web interface docs](WEB_INTERFACE.md)

## Upgrade Notes

- open `/supervisor` as the primary operator screen
- expect session cost in web mode to reflect both token/model and energy spend
- if a stale Amiagi web process exists locally, startup now terminates it automatically