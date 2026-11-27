# Plan: Logger: pole check against a robust reference, with placement and steadiness cues

## Issue

https://github.com/rolker/ice_cube/issues/23

## Context

2026-11-21: four red pole-check warnings, one needed. `PoleCheck` (`logger/.../core/PoleCheck.java`) fails when |new - most recent visit| exceeds the tolerance, so one bad visit fails the next good one (128 after 127, 329 after a high ref 4). The pole being off the mark centre (2.5 and 4.6 cm horizontally) and the pole moving (sd_u 3.1 cm) go unreported.
Scope is the Issue Review and newest Checkpoint (host decisions, owner may veto): two PRs. **PR A** (detailed here): robust reference, placement and steadiness cues, the shared reference rule in the README. **PR B** (outline): the "validated by a known point" mark and board-top check points as a mark kind chosen in Ref mode.
Base: #15 must merge first. This plan builds on #15's plan (read-only): `PoleCheck.matches` also skips earlier refs with `fix_young` = 1 (and still skips voided rows, #27); `PointRow`/refs.csv gain `fix_start`, `float_spell_s`, `suspect_reason`, `fix_young` after `void_reason` (29 -> 33 columns); settle limit becomes the young limit (120 s). PR A adds no row columns, so it does not collide with that schema. Names below (`fixYoung`) follow #15's plan; check them against the merged code before step 1.

## Approach: PR A (robust reference, placement and steadiness cues)

The one shared reference rule (written once, in `logger/README.md` "Refs and the pole check"; #15's text points to it):
- Reference visits = `PoleCheck.matches(now, earlier)`: within 0.30 m, with a ground height, not voided, not young. Formed from these only.
- 1 visit: compare with it, notice says "single-visit reference" (amber-leaning info, check still runs).
- 2 visits: reference = their mean; if they differ from each other by more than the tolerance, a "earlier visits disagree" cue (the reference is doubtful; no red fail from a doubtful reference, see step 2).
- 3 or more: reference = median of the earlier ground heights.
- Tolerance (setting, default 3 cm) applies to |new - reference|. The difference to the most recent visit is kept as information.

1. **Reference in `PoleCheck`** — add `reference` (m), `refKind` (`SINGLE`, `PAIR`, `MEDIAN`), `diffRef` (new minus reference), `pairSpread` (|a - b| for two visits, NaN otherwise). `ok = |diffRef| <= tolerance` (keeping the 1e-9 boundary slack). Keep `diffLast`, `last`, `visits`; `diffMean` is dropped from the decision but kept as a field for the event line (tuning against past sessions).
2. **Doubtful reference** — with two disagreeing visits, a failed check is reported amber ("pole check: +4.2 cm vs mean of 2 visits that differ by 3.5 cm; shoot again") instead of red with the alarm, because neither side can be called bad. Open question 1 asks whether to keep this.
3. **Placement cue** — new `PoleCheck.placementOffset`: horizontal distance from the new ref's position to the median east/north position of the reference visits (local frame via `LocalFrame`, as `matches` does). Over `PLACE_CM` (2 cm, setting) adds "pole may be off the mark centre (2.5 cm); re-shoot" to the notice and the event line. With 1 earlier visit the offset is to that visit.
4. **Steadiness cue** — the new ref's own `sdU` over `STEADY_CM` (1.5 cm, setting) adds "pole moved during the ref (sd 3.1 cm); re-shoot". Not a comparison with other visits, so it also fires on a first visit; `check` returns a result with no reference in that case (see step 5).
5. **API shape** — `PoleCheck.check` currently returns null with no ground height or no earlier match, and `Survey.poleCheck` then writes "first visit" text. Steadiness must still show on a first visit, so add a separate pure `PoleCheck.Cues` (placement offset or NaN, steadiness sd or NaN, flags) built in `Survey.poleCheck` beside the check; `describe()` composes the text, and the notice is red (failed check, not doubtful), amber (cues or doubtful reference only), or green.
6. **Settings** — `Limit.PLACE_CM` (0-20, default 2, 0 = off) and `Limit.STEADY_CM` (0-20, default 1.5, 0 = off), shown in `SettingsActivity` under the pole check tolerance, `Settings` getters, `config`-style help text. Pole tolerance setting unchanged.
7. **Event line and logging** — `pole check n=...` event keeps `diff_last_m`, `diff_mean_m` and adds `diff_ref_m`, `ref_kind`, `place_cm`, `sd_u_cm`, so private past-session replays can retune thresholds (relative numbers only in the repo). Old refs.csv rows read unchanged (no new columns).
8. **Tuning against 2026-11-11, -15, -21** — a one-off offline replay of those private sessions' refs.csv through the new rule (script run from an agent, not committed, counts only): confirm 72 and 127 warn, 128 and 329 do not, count changes elsewhere. Only relative numbers go into the PR text and `docs/log.md`.
9. **Docs and version** — README "Refs and the pole check" rewritten (rule above, cues, settings list entry, cue/notice colours; the old "most recent / mean of all" text goes), `logger/README.md` settings list, `build.gradle` version bump (minor, versionCode +1) after #15's, `docs/log.md` entry.

## Tests (JVM, `PoleCheckTest`; synthetic local-frame refs, relative metres)

- Bad middle visit (127 pattern): visits 0, 0, +7 cm, then a good one at 0 does not fail (median), and the +7 visit itself fails against a 3-visit reference.
- 1 visit: compared, `refKind` SINGLE; 2 visits: mean, disagreement over tolerance sets the doubtful flag, amber not red; 3 visits: median, an outlier among them does not move the reference.
- Voided and young (`fixYoung` = 1) earlier refs are not in the reference (mirrors #15's tests); an older row with unknown young still counts.
- `diffLast` still reported alongside `diffRef`.
- Placement: offset 2.5 cm cues, 1.9 cm does not, 0 disables; steadiness: sd_u 3.1 cm cues, 0.9 cm does not, fires on a first visit.
- Boundary: |diff| exactly at tolerance still ok.
- `LimitTest` for the two new limits. `describe()` strings for each notice colour.

## Outline: PR B (after A; validated mark and board-top check points)

1. **Validated by a known point** — a fix stretch (#15's `FixGuard.Stretch`) gets "validated" when a later ref or check point inside that stretch passed the reference rule above, clearing the doubt about the stretch while a failed check marks the ref only. Stored as a new points.csv/refs.csv column appended after #15's, header-upgrade path as for `void`, `analysis/` and `icecube/` readers (`logger_session.py`, `import_sessions.py`, `SCHEMA_VERSION`) read it as unknown when absent. Replaces #15's follow-up mention; defined once in the README next to the reference rule.
2. **Mark kind** — a `kind` chosen when a mark is first shot in Ref mode: "fixed ref" (default) or "check point (may move)", stored with the mark in refs.csv (new column, older rows = fixed ref). A mark's kind is fixed after the first shot (open question 3).
3. **Check points** — compared only against their own earlier visits (same robust-median rule over visits of the same kind), plus a drift cue against the fixed refs (change of the check point relative to the fixed refs measured in the same stretch); never used as references for fixed refs (`matches` gains a kind filter). Frost heave makes a board top a moving point, so it is not a reference.
4. Tests, README, version, log as in PR A. Detail and the exact validated-flag semantics are planned after PR A merges.

## Files to Change (PR A)

| File | Change |
|------|--------|
| `logger/app/src/main/java/rink/icecube/logger/core/PoleCheck.java` | robust reference, ref kind, doubtful flag, placement and steadiness helpers, `describe()` |
| `logger/.../core/Limit.java`, `Settings.java`, `SettingsActivity.java` | `PLACE_CM`, `STEADY_CM` |
| `logger/.../Survey.java` | build cues in `poleCheck`, notice colour, extended event line, first-visit steadiness |
| `logger/app/src/test/.../core/{PoleCheckTest,LimitTest}.java` | cases above |
| `logger/README.md`, `logger/app/build.gradle`, `docs/log.md` | the shared rule, settings, version, journal |

## Prior Art

#15's `PoleCheck.matches` (voided/young skip) is reused as the reference set. Median of repeated measurements is the standard robust location estimate; a median of two is the mean, so the 2-visit rule is explicit. The field session analysis in the issue supplies the thresholds (2 cm, 1.5 cm), to be checked in step 8. Nothing else new is built.

## Serves

Survey-readiness for cm-level ice accumulation: trustworthy pole checks before the winter ice survey. Project has no goals or design document: none.

## Design Picture

No change to the design picture (project has no design document). The reference rule is a documented behaviour decision, written once in `logger/README.md`.

## Principles Self-Check

| Principle | Consideration |
|---|---|
| Public repo, no coordinates | Tests use synthetic offsets; PR text and log carry relative numbers only; replay of private sessions stays offline. |
| Small steps | Two PRs; PR A adds no row columns and no processing-side change. |
| A change includes its consequences | README text and `PoleCheckTest` that assume "most recent / mean of all" are rewritten in PR A. |
| Keep one current design | One reference rule, in the README only; #15's text points to it. |
| Only what's needed | Board tops and the validated mark wait for PR B. |
| Older data stays readable | No schema change in PR A; old refs.csv rows used as before. |

## ADR Compliance

| ADR | Triggered | How addressed |
|---|---|---|
| none (project has no `docs/decisions/`) | No | n/a |

## Consequences

| If we change... | Also update... | Included? |
|---|---|---|
| pole check decision rule | README section, `PoleCheckTest`, notice colours/cues in `Survey` | Yes |
| new limits | `Limit`, `Settings`, `SettingsActivity`, README settings list | Yes |
| `matches` (#15) | stays the one reference-set filter; PR B adds a kind filter | Yes (A) / outline (B) |
| refs.csv columns | PR B only | PR B |

## Open Questions

1. Doubtful two-visit reference: amber (shoot again) instead of a red alarm when the two earlier visits disagree by more than the tolerance. Confirm, or fail red as for a single visit.
2. Placement and steadiness limits as settings (2 cm, 1.5 cm, 0 = off) versus fixed constants; plan assumes settings, matching the tolerance.
3. Can a mark's kind change after its first shot (a board top first shot as a fixed ref by mistake)? Plan assumes no in PR B; correction means voiding and re-shooting.
4. Should an earlier visit that itself failed its check be left out of the reference? Plan says no (refs.csv does not store check results; the median handles one bad visit).
5. Implementation waits for #15 to merge; column and field names (`fixYoung`) are taken from #15's plan and must be re-checked against merged code.

## Estimated Scope

Two PRs. PR A: about 7 files plus tests, single PR. PR B: outline only, planned in detail after A.
