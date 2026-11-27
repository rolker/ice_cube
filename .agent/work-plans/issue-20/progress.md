---
issue: 20
---

# Issue #20 — icecube: read Ice Cube Logger sessions

## Issue Review
**Status**: complete
**When**: 2026-11-27 17:55 -05:00
**By**: Claude Code Agent (claude-sonnet-5-5)

**Issue**: #20

### Scope Assessment

**Well-scoped?** Mostly. One PR is feasible if the reader and the settle re-fit stay as two commits; if the re-fit grows, split it into its own issue (it touches `soundings.py`, the logger's Java port and the test vectors).
**Right repo?** Yes. `icecube/` plus the shared sigma rules in `analysis/qgis/soundings.py`.
**Serves**: the season goal in `docs/roadmap.md`; the project keeps no separate design document, so none cited.
**Design picture?** No change.
**Dependencies**: #15 (in implementation) changes the settle default to 120 s and adds `points.csv` columns (`fix_start`, `float_spell_s`, `suspect_reason`, `fix_young`) and a float-spell rule in `soundings.fix_stretches`; start implementation after #15 merges. #27 and #9 are merged; the reader must exclude voided captures. #24 (robust nodes) is closed, so drop it from this scope.

### Principle Alignment

| Principle | Status | Notes |
|---|---|---|
| Reuse over duplication | Action needed | `analysis/qgis/logger_session.py` and `soundings.logger_source` already read sessions and build per-capture sigma, stretches and passes. The reader should wrap those, not add a third parser. Decide in the plan between a thin `icecube.io` adapter and running `soundings` on a scratch gpkg. |
| One rule, one place (sigma) | Action needed | The settle term lives in `soundings.sigma_terms`, mirrored in the logger's Java port and pinned by `logger_vectors.py`. A re-fit must change the Python rule, regenerate the vectors and update the Java port together, after #15's default lands. |
| Public repo hygiene | Watch | Re-fit numbers must be relative only. |
| Only what's needed | Watch | Robust nodes is done under #24/#29; do not re-scope it here. |

### ADR Applicability

| ADR | Triggered | Notes |
|---|---|---|
| Workspace ADRs | No | Project-level change. |

### Consequences

- Settle-term change: update `soundings.py` docstring and `DEFAULTS`, `logger_vectors.py` and `coverage_vectors.json`, the Java sigma port and tests, and the howto/README text on the 90 s term.
- New reader: add a `[survey]` source option to `icecube.example.toml` and document it in `docs/howto/ice-cube.md`.

### Recommendations

- Start implementation only after #15 merges; build on its `points.csv` columns and float-spell rule.
- Reader reuses `logger_session.py` and the `soundings.py` sigma and stretch rules; carry `fix_age_s`, `fix_jump_m` and `fix_suspect` (and #15's `fix_young`, `float_spell_s`) through, exclude voided captures, and make fix stretches the pass boundaries.
- Re-fit the settle term on the 11-11, 11-15 and 11-21 sessions (flat to about the settle time, then taper), report only relative figures, and ship it with the vectors and Java port in the same PR.
- Treat the settle re-fit as separable: if it grows beyond a small change, split it from the reader.

## Checkpoint
**Status**: complete
**When**: 2026-11-27 17:51 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (mechanism only; owner asked to orchestrate the issues in parallel while he is busy: "Orchestrate the issues while I'm busy and run them in parallel if practical")
**After**: issue-actions
**Decision**: proceed

Host decisions (owner may veto): the reader is a thin icecube.io adapter over logger_session.py and the soundings rules (no third parser); robust nodes are already delivered by #24/#29, noted as done rather than re-scoped; the settle re-fit is planned in this issue, and if it grows beyond a small change the plan says so and the split goes to the owner as a question (a new issue is his). Implementation starts only after #15 merges and builds on it (120 s settle, new points.csv columns, float-spell rule).
