---
issue: 35
---

# Issue #35 — Logger: auto capture when the pole is held plumb and still

## Issue Review
**Status**: complete
**When**: 2026-11-27 17:52 -05:00
**By**: Claude Code Agent (claude-sonnet)

**Issue**: #35

### Scope Assessment

**Well-scoped?** Mostly. The owner's answers settle modes, thresholds, per-session calibration and tilt-set interaction. The calibration routine is shared with #38, so it should be built as a standalone, reusable routine here, and the #38 outputs (lever arm, gyro biases) can be added later. It fits one PR if the calibration stores only the pole axis now.
**Right repo?** Yes. It is all in `logger/`.
**Serves**: none stated. The project keeps no goals or design document. Practically it serves gloved winter ice surveys and fewer accidental captures (#27).
**Design picture?** No change. There is no project design document.
**Dependencies**: #15 must merge first (young-fix warning under 120 s and the float-spell suspect rule; shared Survey/MainActivity code). Related: #27, #9 (tilt sets; no tilt code is in `logger/` yet, so "off during a tilt set" needs a hook that may not exist), #38 (shares the calibration routine).

### Principle Alignment

| Principle | Status | Notes |
|---|---|---|
| Public repo rules (CLAUDE.md) | OK | Thresholds and lever-arm defaults are not site data. Keep the owner's rough lever-arm numbers out of committed fixtures if they identify a specific rig. |
| Auto capture must not fire on a bad fix (#15) | Action needed | Define the arming rule against #15. Auto capture should arm only on a RTK-fixed, settled fix that #15 does not flag as young or suspect. A young-fix warning still captures on MARK with the amber cue, but a capture nobody pressed should not be taken on a fix the app itself doubts. |
| Fail visibly | OK | Owner decision: the screen states why auto capture is off (no calibration, tilt set, unsettled fix). Show the unmet condition. |

### ADR Applicability

| ADR | Triggered | Notes |
|---|---|---|
| Workspace ADRs | No | A project-level change in `logger/`. |

### Consequences

- Update `logger/README.md` (auto capture, calibration, settings).
- Add the new settings with their defaults (2 deg, 2 s, 2 cm).
- Pure-logic unit tests for the arming and re-arming state machine (still detection, one capture per stop, re-arm on move or tilt).

### Recommendations

- Start implementation only after #15 merges; branch from main then.
- Auto capture waits for a settled, non-suspect fix: no auto capture while #15 flags the fix as young (under 120 s) or suspect. MARK is unchanged. This is a recommendation for the owner to confirm.
- Build the calibration as a reusable routine with the phases (still, lean about 10 s, two slow twists). Store only the pole axis for #35, and keep the lever-arm and bias outputs for #38 or leave a clear extension point. Do not pre-build #38 pieces.
- Keep the arming logic a pure, testable state machine separate from the sensor and UI code.
- Gloved use: large touch targets, and an audible or haptic cue for calibration progress and for auto-capture armed or off.
- Check how "tilt set in progress" is represented in the code. If #9 has not landed, gate on a flag that #9 will set.

### Actions
- [ ] Define the arming rule against #15. Auto capture should arm only on a RTK-fixed, settled fix that #15 does not flag as young or suspect. A young-fix warning still captures on MARK with the amber cue, but a capture nobody pressed should not be taken on a fix the app itself doubts.
- [ ] Start implementation only after #15 merges; branch from main then.
- [ ] Auto capture waits for a settled, non-suspect fix: no auto capture while #15 flags the fix as young (under 120 s) or suspect. MARK is unchanged. This is a recommendation for the owner to confirm.
- [ ] Build the calibration as a reusable routine with the phases (still, lean about 10 s, two slow twists). Store only the pole axis for #35, and keep the lever-arm and bias outputs for #38 or leave a clear extension point. Do not pre-build #38 pieces.
- [ ] Keep the arming logic a pure, testable state machine separate from the sensor and UI code.
- [ ] Gloved use: large touch targets, and an audible or haptic cue for calibration progress and for auto-capture armed or off.
- [ ] Check how "tilt set in progress" is represented in the code. If #9 has not landed, gate on a flag that #9 will set.

## Checkpoint
**Status**: complete
**When**: 2026-11-27 17:52 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (mechanism of a behaviour the owner decided; owner asked to orchestrate in parallel while busy)
**After**: issue-actions
**Decision**: proceed

Host decisions (owner may veto): auto capture arms only on an RTK-fixed fix that #15 marks neither young (< 120 s) nor suspect; MARK still captures on a young fix with the amber cue; the screen and a cue say which condition is unmet. Calibration as a reusable routine (hold still, lean ~10 s, two slow twists) storing the pole axis now, with an extension point for #38's lever arm and gyro bias. Arming logic a pure, unit-tested state machine. Large touch targets and audible/haptic cues for gloved use. Correction to the review: tilt capture exists in logger/ (core/TiltSet.java, TiltSolver, Survey's tilt mode); auto capture is off while a tilt set is in progress. Implementation after #15 merges.
