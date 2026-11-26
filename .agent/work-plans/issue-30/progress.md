---
issue: 30
---

# Issue #30 — Logger: notification stays on when only viewing the map (no GPS)

## Implemented
**Status**: complete
**When**: 2026-11-26
**By**: Claude Code (claude-opus-5-5)
**Plan**: `.agent/work-plans/issue-30/plan.md`

Stacked on #28 (feature/issue-24). Commits: plan; `core/ServiceState` with tests and
`Limit.GPS_WAIT_MIN`; LoggerService notification per state with a Stop action, 2 s tick,
bounded wait for the rover, `onTaskRemoved`, caster follows the GPS, autostart no longer
connects the caster; app version in Settings and the MENU panel; README and 0.8.1 (build 11).
Tests: `./gradlew testDebugUnitTest` 162 OK (ServiceStateTest 10), `assembleDebug lintDebug`
0 errors. Not yet checked on a phone.
