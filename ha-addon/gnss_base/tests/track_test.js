// Checks of the page's track merging. Run by tests.test_app, or: node tests/track_test.js
// Synthetic offsets only.
"use strict";
const assert = require("assert");
const path = require("path");
const {mergeTrack, clockJumped, baseCentring, sentText, roverPoints, runningMean,
  trackLabel, duration, age, reasonsText, baseSummary, storageLow, riseTrend, receiverModeText, msmText,
  baseShift, casterIsUp, casterText, uploadLabel, uploadText} = require(
  path.join(__dirname, "..", "gnss_base", "static", "track.js"));

const REF = {lat: 0, lon: 0, height_m: 0};

function response(fields) {
  return Object.assign({window_s: 7200, max_points: 7200, reference: REF,
    reference_offset: [0, 0, 0], cursor: "h:1", full: true, outliers_dropped: 0,
    oldest_age_s: null, points: []}, fields);
}

const checks = {
  "concatenates when full is false"() {
    let {kept} = mergeTrack(null, response({cursor: "h:2", oldest_age_s: 2,
      points: [[2, 0.01, 0, 0, 4], [1, 0.02, 0, 0, 4]]}), 1000);
    let track;
    ({kept, track} = mergeTrack(kept, response({cursor: "h:3", full: false, oldest_age_s: 12,
      points: [[1, 0.03, 0, 0, 4]]}), 1010));
    assert.deepStrictEqual(track.points.map(p => p[0]), [12, 11, 1]);
    assert.deepStrictEqual(track.points.map(p => p[1]), [0.01, 0.02, 0.03]);
    assert.strictEqual(kept.cursor, "h:3");
  },

  "resets when full is true"() {
    let {kept} = mergeTrack(null, response({points: [[2, 0.01, 0, 0, 4], [1, 0.02, 0, 0, 4]]}),
      1000);
    let track;
    ({kept, track} = mergeTrack(kept, response({cursor: "g:1", oldest_age_s: 1,
      points: [[1, 0.5, 0, 0, 5]]}), 1010));
    assert.deepStrictEqual(track.points, [[1, 0.5, 0, 0, 5]]);
    assert.strictEqual(kept.cursor, "g:1");
    ({kept, track} = mergeTrack(kept, response({reference: null, reference_offset: null,
      cursor: null}), 1020));
    assert.strictEqual(kept, null);
    assert.deepStrictEqual(track.points, []);
  },

  "prunes to the window and the point limit"() {
    let {kept} = mergeTrack(null, response({window_s: 100, max_points: 3,
      points: [[90, 1, 0, 0, 4], [50, 2, 0, 0, 4], [10, 3, 0, 0, 4]]}), 1000);
    const {track} = mergeTrack(kept, response({window_s: 100, max_points: 3, full: false,
      points: [[5, 4, 0, 0, 4]]}), 1020);
    // The first point is now 110 s old, outside the 100 s window.
    assert.deepStrictEqual(track.points.map(p => p[1]), [2, 3, 4]);
    const {track: limited} = mergeTrack(kept, response({window_s: 100, max_points: 3,
      full: false, points: [[1, 4, 0, 0, 4], [0, 5, 0, 0, 4]]}), 1001);
    assert.deepStrictEqual(limited.points.map(p => p[1]), [3, 4, 5]);
  },

  "drops kept points older than the app's oldest"() {
    // The page clock stood still for an hour while the computer slept: by its own
    // clock nothing is old, but the app has dropped the first two points meanwhile.
    let {kept} = mergeTrack(null, response({points: [[30, 1, 0, 0, 4], [20, 2, 0, 0, 4],
      [10, 3, 0, 0, 4]]}), 1000);
    const {track} = mergeTrack(kept, response({full: false, oldest_age_s: 10,
      points: [[1, 4, 0, 0, 4]]}), 1000);
    assert.deepStrictEqual(track.points.map(p => p[1]), [3, 4]);
  },

  "re-bases kept points when reference_offset changes"() {
    let {kept} = mergeTrack(null, response({reference_offset: [0.01, 0, 0],
      points: [[2, 0, 0.02, 0, 4], [1, 0.02, 0, -0.01, 4]]}), 1000);
    const {track} = mergeTrack(kept, response({reference_offset: [0.02, 0.01, 0.005],
      full: false, points: [[1, 0, 0, 0, 4]]}), 1001);
    // From the fixed origin: (0.01, 0.02, 0), (0.03, 0, -0.01), (0.02, 0.01, 0.005).
    assert.deepStrictEqual(track.points.map(p => p.slice(1, 4)),
      [[-0.01, 0.01, -0.005], [0.01, -0.01, -0.015], [0, 0, 0]]);
  },

  "passes no_measured_reason through, with or without points"() {
    const reason = "No measured position: the receiver is in base mode.";
    const {track: empty} = mergeTrack(null, response({reference: null, reference_offset: null,
      cursor: null, no_measured_reason: reason}), 1000);
    assert.strictEqual(empty.no_measured_reason, reason);
    const {track: kept} = mergeTrack(null, response({no_measured_reason: reason,
      points: [[1, 0.01, 0, 0, 4]]}), 1000);
    assert.strictEqual(kept.no_measured_reason, reason);
    const {track: older} = mergeTrack(null, response({points: [[1, 0.01, 0, 0, 4]]}), 1000);
    assert.strictEqual(older.no_measured_reason, null, "an older app sends no reason");
  },

  "centres on the base once after a switch from rover to base mode"() {
    const base = [0.001, 0.002];
    // A rover with a measured fix: never centre on the base.
    let state = baseCentring(null, base, true);
    assert.deepStrictEqual(state, {centre: false, centredOn: null});
    // The fix goes stale (switched to base mode): centre once ...
    state = baseCentring(state.centredOn, base, false);
    assert.deepStrictEqual(state, {centre: true, centredOn: base});
    // ... and not on every refresh, even if the user pans away.
    state = baseCentring(state.centredOn, [0.001, 0.002], false);
    assert.strictEqual(state.centre, false);
    // A stale base (null here) neither centres nor forgets where it last centred.
    state = baseCentring(state.centredOn, null, false);
    assert.deepStrictEqual(state, {centre: false, centredOn: base});
    // A move of about 1 m is not material; about 11 m is.
    state = baseCentring(state.centredOn, [0.00101, 0.002], false);
    assert.strictEqual(state.centre, false);
    state = baseCentring(state.centredOn, [0.0011, 0.002], false);
    assert.deepStrictEqual(state, {centre: true, centredOn: [0.0011, 0.002]});
    // Back to a measured fix, then base mode again: centre again.
    state = baseCentring(state.centredOn, [0.0011, 0.002], true);
    assert.strictEqual(state.centredOn, null);
    assert.strictEqual(baseCentring(state.centredOn, [0.0011, 0.002], false).centre, true);
  },

  "switching source drops the kept points and passes the source through"() {
    let {kept, track} = mergeTrack(null, response({source: "gga", cursor: "g:2",
      points: [[2, 0.01, 0, 0, 4], [1, 0.02, 0, 0, 4]]}), 1000);
    assert.strictEqual(track.source, "gga");
    // A measured response that (wrongly) says it continues: still not merged with GGA.
    ({kept, track} = mergeTrack(kept, response({source: "measured", reference_kind: "base",
      base_changed_age_s: 30, cursor: "m:1", full: false, points: [[1, 1.5, -2, 3, 5]]}), 1001));
    assert.deepStrictEqual(track.points, [[1, 1.5, -2, 3, 5]]);
    assert.deepStrictEqual([track.source, track.reference_kind, track.base_changed_age_s],
      ["measured", "base", 30]);
    assert.strictEqual(kept.source, "measured");
    // An older app sends no source: GGA, median.
    const {track: old} = mergeTrack(null, response({points: [[1, 0, 0, 0, 4]]}), 1000);
    assert.deepStrictEqual([old.source, old.reference_kind, old.base_changed_age_s],
      ["gga", "median", null]);
    const {track: empty} = mergeTrack(null, response({source: "measured", reference: null,
      reference_offset: null, cursor: null}), 1000);
    assert.strictEqual(empty.source, "measured");
  },

  "a new 1005 moves the measured reference and keeps the points"() {
    // Points stay in the history's frame; the 1005 moved 2 m north between polls.
    let {kept} = mergeTrack(null, response({source: "measured", reference_kind: "base",
      reference_offset: [0, 0, 0], points: [[2, 1, 0, 3, 5], [1, 1, 0, 3, 5]]}), 1000);
    const {track} = mergeTrack(kept, response({source: "measured", reference_kind: "base",
      reference_offset: [0, 2, 0], full: false, points: [[1, -1, -2, 0, 5]]}), 1001);
    assert.deepStrictEqual(track.points.map(p => p.slice(1, 4)),
      [[1, -2, 3], [1, -2, 3], [-1, -2, 0]]);
  },

  "running mean over five minutes"() {
    // One point a second for 10 minutes: east 0 for the first 5, then 1.
    const pts = [];
    for (let age = 600; age >= 1; age--) pts.push([age, age > 300 ? 0 : 1, 2, -age / 100, 5]);
    const mean = runningMean(pts);
    assert.strictEqual(mean.length, pts.length);
    assert.deepStrictEqual(mean[0], [600, 0, 2, -6]);
    assert.strictEqual(mean[299][1], 0);
    const last = mean[mean.length - 1];
    assert.strictEqual(last[0], 1);
    assert.ok(Math.abs(last[1] - 300 / 301) < 1e-9, last);
    assert.ok(Math.abs(last[3] - -1.51) < 1e-9, last);
    assert.deepStrictEqual(runningMean([]), []);
    // A gap longer than the window: the mean starts again.
    assert.deepStrictEqual(runningMean([[1000, 5, 5, 5, 5], [10, 1, 1, 1, 5]])[1], [10, 1, 1, 1]);
  },

  "labels say where the offsets are from"() {
    assert.strictEqual(trackLabel(null).title, "Drift around the median position");
    assert.ok(/RTCM 1005/.test(trackLabel({source: "measured", reference_kind: "base"}).offsets));
    assert.ok(/median/.test(trackLabel({source: "measured", reference_kind: "median"}).offsets));
    assert.ok(/rtkrcv/.test(trackLabel({source: "measured"}).title));
  },

  // ---- 0.5.2: the base station card ----
  "serving only when the whole chain is fresh and an output is up"() {
    const ok = () => ({connected: true, data_age_s: 0.3, last_error: null,
      base_position: {lat: 1, lon: 2, ecef: [1, 2, 3], age_s: 1.2, stale: false},
      msm: {GPS: {satellites: 9, age_s: 0.5}, GLONASS: {satellites: 6, age_s: 0.5}},
      storage: {free_bytes: 50e9, min_free_bytes: 10e9, paused: false},
      caster: {enabled: true, listening: true, error: null, clients: []},
      upload: {enabled: true, state: "connected", host: "rtk2go.com", connected_s: 100}});
    assert.deepStrictEqual(baseSummary(ok(), false), {word: "Serving", reasons: []});
    const degraded = (change, pattern) => {
      const s = ok(); change(s);
      const out = baseSummary(s, false);
      assert.strictEqual(out.word, "Degraded", pattern);
      assert.ok(out.reasons.some(r => pattern.test(r)), `${pattern}: ${out.reasons}`);
    };
    degraded(s => { s.data_age_s = 7; }, /No data from the receiver for 7\.0 s/);
    degraded(s => { s.data_age_s = 5.1; }, /No data from the receiver for 5\.1 s/);
    degraded(s => { s.base_position.stale = true; s.base_position.age_s = 45; }, /1005 is stale \(45 s old\)/);
    degraded(s => { s.base_position.stale = true; delete s.base_position.age_s; }, /1005 is stale \(age unknown\)/);
    degraded(s => { delete s.msm.GPS; }, /No GPS observations/);
    degraded(s => { s.msm = {}; }, /No GPS observations/);
    degraded(s => { s.msm.GPS.age_s = 12; }, /GPS observations are 12 s old/);
    degraded(s => { s.msm.GPS.age_s = 5.4; }, /GPS observations are 5\.4 s old/);
    assert.strictEqual(baseSummary(Object.assign(ok(), {data_age_s: 5}), null).word, "Serving", "5.0 s is still fresh");
    degraded(s => { s.storage.free_bytes = 9e9; }, /below the floor/);
    degraded(s => { s.storage.paused = true; }, /Logging is paused/);
    degraded(s => { s.storage.last_error = "disk: I/O error"; }, /Logging error: disk: I\/O error/);
    const both = ok(); both.storage.free_bytes = 1; both.storage.paused = true; both.storage.last_error = "x";
    assert.strictEqual(baseSummary(both, false).reasons.length, 3, "every storage reason is listed");
    degraded(s => { s.caster.listening = false; }, /Local caster not listening/);
    degraded(s => { s.caster.error = "port in use"; }, /Local caster not running: port in use/);
    degraded(s => { s.upload.state = "waiting"; s.upload.retry_in_s = 240; s.upload.reason = "refused"; },
      /RTK2go retrying in 4 min \(refused\)/);
    degraded(s => { s.upload.state = "off"; s.upload.last_error = "rev2 is not supported"; },
      /RTK2go off: rev2 is not supported/);
    // Both outputs off: not a failure of either, but nothing serves corrections.
    degraded(s => { s.caster.enabled = false; s.upload = {enabled: false, state: "off"}; }, /No output is up/);
    // One output is enough.
    const one = ok(); one.upload = {enabled: false, state: "off"};
    assert.strictEqual(baseSummary(one, false).word, "Serving");
    const other = ok(); other.caster.enabled = false;
    assert.strictEqual(baseSummary(other, false).word, "Serving");
    // Other systems stale or missing do not count; GPS does.
    const noGlo = ok(); noGlo.msm.GLONASS.age_s = 30;
    assert.strictEqual(baseSummary(noGlo, false).word, "Serving");
    assert.deepStrictEqual(baseSummary(ok(), {rising: true, recent: 1}).reasons, ["1 new RTCM decode error in the last minute"]);
    assert.deepStrictEqual(baseSummary(ok(), {rising: true, recent: 3}).reasons, ["3 new RTCM decode errors in the last minute"]);
    assert.strictEqual(baseSummary(ok(), {rising: false, recent: 0}).word, "Serving");
    assert.strictEqual(baseSummary(ok(), null).word, "Serving");
    // The caster's output callback failing (hub subscriber errors rising): the caster is
    // not up, even though it listens; with the upload connected the station is Degraded,
    // and with the upload off nothing serves.
    const failing = {rising: true, recent: 2};
    assert.deepStrictEqual(baseSummary(ok(), null, failing).reasons,
      ["Local caster listening, 0 clients, output failing: 2 errors in the last minute"]);
    const only = ok(); only.upload = {enabled: false, state: "off"};
    assert.deepStrictEqual(baseSummary(only, null, failing).reasons.slice(-1), ["No output is up"]);
    assert.strictEqual(baseSummary(ok(), null, {rising: false, recent: 0}).word, "Serving");
    // Up to BASE_DOWN_S a stale 1005 is Degraded; past it, Down (see the Down checks):
    // the app never clears the last 1005, it only marks it stale.
    const gone = ok(); gone.base_position.stale = true; gone.base_position.age_s = 300;
    assert.strictEqual(baseSummary(gone, null).word, "Degraded");
    gone.base_position.age_s = 300.1;
    assert.strictEqual(baseSummary(gone, null).word, "Down");
    // Several reasons, in the order checked.
    const many = ok(); many.data_age_s = 6; many.base_position.stale = true; many.caster.enabled = false;
    assert.deepStrictEqual(baseSummary(many, false).reasons.map(r => r.split(" ")[0]), ["No", "RTCM"]);
    // Storage and outputs missing from an older app: not reasons on their own.
    const old = ok(); delete old.storage; delete old.caster; delete old.upload;
    assert.deepStrictEqual(baseSummary(old, false).reasons, ["No output is up"]);
  },

  "down without receiver data or a 1005"() {
    const base = {connected: true, data_age_s: 1, base_position: {lat: 1, lon: 2, age_s: 1, stale: false},
      msm: {GPS: {satellites: 9, age_s: 1}}, caster: {enabled: true, listening: true}};
    const down = (change, pattern) => {
      const s = JSON.parse(JSON.stringify(base)); change(s);
      const out = baseSummary(s, false);
      assert.strictEqual(out.word, "Down", pattern);
      assert.strictEqual(out.reasons.length, 1);
      assert.ok(pattern.test(out.reasons[0]), out.reasons[0]);
    };
    down(s => { s.connected = false; s.last_error = "no such device"; }, /not connected: no such device/);
    down(s => { s.connected = false; }, /^Receiver not connected$/);
    down(s => { s.data_age_s = null; }, /No data from the receiver yet/);
    down(s => { s.data_age_s = 31; }, /No data from the receiver for 31 s/);
    down(s => { s.data_age_s = 5400; }, /for 1\.5 h/);
    down(s => { s.base_position = {}; }, /No RTCM 1005\/1006 received/);
    down(s => { delete s.base_position; }, /No RTCM 1005\/1006 received/);
    down(s => { s.base_position.stale = true; s.base_position.age_s = 301; }, /No RTCM 1005\/1006 for 5 min/);
    down(s => { s.base_position.stale = true; s.base_position.age_s = 7200; }, /No RTCM 1005\/1006 for 2\.0 h/);
    // Down comes before any Degraded reason, even with everything else stale.
    down(s => { s.connected = false; s.msm = {}; s.caster.listening = false; }, /not connected/);
    assert.strictEqual(baseSummary({}, false).word, "Down");
  },

  "an error count is rising for a minute after it grows, and says by how many"() {
    const brief = t => [t.rising, t.recent];
    let t = riseTrend(null, 0, 100);
    assert.deepStrictEqual(t, {count: 0, rises: [], rising: false, recent: 0});
    t = riseTrend(t, 0, 110);
    assert.deepStrictEqual(brief(t), [false, 0]);
    t = riseTrend(t, 2, 120);
    assert.deepStrictEqual(t, {count: 2, rises: [{at: 120, before: 0}], rising: true, recent: 2});
    // More within the minute add up from the count before the first rise in the window.
    t = riseTrend(t, 3, 150);
    assert.deepStrictEqual(brief(t), [true, 3]);
    // The first rise leaves the window: only what grew in the last minute counts.
    t = riseTrend(t, 3, 181);
    assert.deepStrictEqual(brief(t), [true, 1]);
    t = riseTrend(t, 3, 211);
    assert.deepStrictEqual(brief(t), [false, 0]);
    // A new rise after a quiet spell counts from the count before it, not from zero.
    t = riseTrend(t, 4, 300);
    assert.deepStrictEqual(brief(t), [true, 1]);
    // The app restarted while rising: the lower count starts afresh, nothing kept.
    t = riseTrend(t, 0, 310);
    assert.deepStrictEqual(t, {count: 0, rises: [], rising: false, recent: 0});
    assert.deepStrictEqual(brief(riseTrend(t, 1, 311)), [true, 1]);
    // The first count seen is not a rise, even if large; a missing count is nothing,
    // and the count after it is a first count again.
    assert.strictEqual(riseTrend(null, 500, 0).rising, false);
    // The first error ever (0 -> 1) is a rise: the page passes 0 for a subscriber the
    // hub does not list yet, not null.
    assert.deepStrictEqual(brief(riseTrend(riseTrend(null, 0, 100), 1, 110)), [true, 1]);
    assert.deepStrictEqual(riseTrend(t, null, 500), {count: null, rises: [], rising: false, recent: 0});
    assert.strictEqual(riseTrend(riseTrend(t, null, 500), 1, 501).rising, false);
  },

  "the receiver's mode in words"() {
    assert.strictEqual(receiverModeText({svin: {mode: 2}}), "fixed position");
    assert.strictEqual(receiverModeText({svin: {mode: 1}, mode: "Base station"}), "survey-in");
    assert.strictEqual(receiverModeText({svin: {mode: 0}, mode: "Rover"}), "rover");
    assert.strictEqual(receiverModeText({svin: {mode: 0}, mode: null}), "survey-in off, receiver mode not read");
    // A rover is a rover, whatever survey-in configuration it keeps.
    assert.strictEqual(receiverModeText({svin: {mode: 2}, mode: "Rover"}), "rover (fixed position configured, unused)");
    assert.strictEqual(receiverModeText({svin: {mode: 1}, mode: "Rover"}), "rover (survey-in configured, unused)");
    assert.strictEqual(receiverModeText({svin: {mode: null, reason: "x"}, mode: "Rover"}), "rover");
    assert.strictEqual(receiverModeText({svin: {mode: 2}, mode: "Base station"}), "fixed position");
    assert.strictEqual(receiverModeText({svin: {mode: 0}, mode: "Unknown (3)"}), "survey-in off, receiver mode unknown (3)");
    assert.strictEqual(receiverModeText({svin: {mode: 0}, mode: "Base station"}), "base, survey-in off");
    assert.strictEqual(receiverModeText({svin: {mode: null, reason: "not read yet"}}), "mode unknown (not read yet)");
    assert.strictEqual(receiverModeText({}), "mode unknown");
    assert.strictEqual(receiverModeText(null), "mode unknown");
  },

  "satellites per system from MSM, GPS first, newest age"() {
    assert.strictEqual(msmText({BeiDou: {satellites: 10, age_s: 1.4}, GPS: {satellites: 11, age_s: 0.6},
      Galileo: {satellites: 9, age_s: 0.6}, GLONASS: {satellites: 7, age_s: 8.9}}),
      "GPS 11, GLONASS 7, Galileo 9, BeiDou 10 sats, 0.6 s old");
    assert.strictEqual(msmText({GPS: {satellites: 3, age_s: 100}}), "GPS 3 sats, 2 min old");
    assert.strictEqual(msmText({Other: {satellites: 1, age_s: 1}, QZSS: {satellites: 2, age_s: 1}}),
      "QZSS 2, Other 1 sats, 1.0 s old");
    assert.strictEqual(msmText({GPS: {satellites: 3}}), "GPS 3 sats");
    assert.strictEqual(msmText({GPS: {age_s: 1}}), "GPS ? sats, 1.0 s old");
    assert.strictEqual(msmText({GPS: {satellites: 3, age_s: 12.4}}), "GPS 3 sats, 12 s old");
    assert.strictEqual(msmText({}), null);
    assert.strictEqual(msmText(undefined), null);
  },

  "a moved 1005 is measured from the configured position, else from page load"() {
    const here = [1000, 2000, 3000];
    assert.deepStrictEqual(baseShift(null, null, null), {text: "none received", moved: false});
    assert.deepStrictEqual(baseShift(here, null, null), {text: "first seen now", moved: false});
    assert.deepStrictEqual(baseShift(here, null, [1000, 2000, 3000.0005]),
      {text: "unchanged since page load", moved: false});
    assert.deepStrictEqual(baseShift(here, [1000.0004, 2000, 3000], [0, 0, 0]),
      {text: "matches the configured position", moved: false});
    assert.deepStrictEqual(baseShift(here, [1000, 2000, 3000.0015], here),
      {text: "moved 1.5 mm from the configured position", moved: true});
    assert.deepStrictEqual(baseShift(here, null, [1000.03, 2000.04, 3000]),
      {text: "moved 5.0 cm from page load", moved: true});
    assert.deepStrictEqual(baseShift(here, null, [1001, 2001, 3001]),
      {text: "moved 1.73 m from page load", moved: true});
    // The configured position wins over page load when both are known.
    assert.strictEqual(baseShift(here, here, [0, 0, 0]).moved, false);
    // Anything that is not a number is not "matches".
    assert.deepStrictEqual(baseShift(here, ["x", 2000, 3000], null), {text: "not comparable", moved: false});
    assert.deepStrictEqual(baseShift([1, 2], here, null), {text: "not comparable", moved: false});
  },

  "the outputs in a few words"() {
    assert.strictEqual(casterText({enabled: false}), "off (caster_enabled)");
    assert.strictEqual(casterText(undefined), "off (caster_enabled)");
    assert.strictEqual(casterText({enabled: true, error: "port in use"}), "not running: port in use");
    assert.strictEqual(casterText({enabled: true, listening: false}), "not listening");
    assert.strictEqual(casterText({enabled: true, listening: true, clients: []}), "listening, 0 clients");
    assert.strictEqual(casterText({enabled: true, listening: true, clients: [{}]}), "listening, 1 client");
    assert.strictEqual(casterText({enabled: true, listening: true, clients: [{}, {}]}), "listening, 2 clients");
    assert.strictEqual(casterText({enabled: true, listening: true, clients: [{}]}, {rising: true, recent: 1}),
      "listening, 1 client, output failing: 1 error in the last minute");
    assert.strictEqual(casterText({enabled: true, listening: true, clients: []}, {rising: false, recent: 0}),
      "listening, 0 clients");
    assert.strictEqual(casterIsUp({enabled: true, listening: true}, null), true);
    assert.strictEqual(casterIsUp({enabled: true, listening: true}, {rising: true, recent: 1}), false);
    assert.strictEqual(casterIsUp({enabled: true, listening: true, error: "x"}, null), false);
    assert.strictEqual(casterIsUp({enabled: true, listening: false}, null), false);
    assert.strictEqual(casterIsUp(undefined, null), false);
    assert.strictEqual(uploadLabel({host: "rtk2go.com"}), "RTK2go");
    assert.strictEqual(uploadLabel({host: "RTK2GO.COM"}), "RTK2go");
    assert.strictEqual(uploadLabel({host: "caster.example.net"}), "Upload");
    assert.strictEqual(uploadLabel({}), "Upload");
    assert.strictEqual(uploadText({enabled: false, state: "off"}), "off");
    assert.strictEqual(uploadText(undefined), "off");
    assert.strictEqual(uploadText({enabled: true, state: "off", last_error: "bad style"}), "off: bad style");
    assert.strictEqual(uploadText({enabled: true, state: "connected", connected_s: 7200}), "connected for 2.0 h");
    assert.strictEqual(uploadText({enabled: true, state: "connected"}), "connected");
    assert.strictEqual(uploadText({enabled: true, state: "waiting", retry_in_s: 30}), "retrying in 30 s");
    assert.strictEqual(uploadText({enabled: true, state: "waiting", reason: "banned"}), "retrying (banned)");
    assert.strictEqual(uploadText({enabled: true, state: "connecting"}), "connecting");
    assert.strictEqual(uploadText({enabled: true, state: "waiting for RTCM"}), "waiting for RTCM");
    assert.strictEqual(uploadText({enabled: true, state: "stopped for today", reason: "400 MB sent"}),
      "stopped for today (400 MB sent)");
    assert.strictEqual(uploadText({enabled: true, state: "stopped"}), "stopped");
    assert.strictEqual(uploadText({enabled: true, state: "odd"}), "odd");
  },

  "storage is low below the floor only, and durations read as before"() {
    assert.strictEqual(storageLow({free_bytes: 9, min_free_bytes: 10}), true);
    assert.strictEqual(storageLow({free_bytes: 10, min_free_bytes: 10}), false);
    assert.strictEqual(storageLow({free_bytes: 5}), false);
    assert.strictEqual(storageLow(undefined), false);
    assert.deepStrictEqual([duration(42.4), duration(89), duration(90), duration(5399), duration(5400), duration(36000)],
      ["42 s", "89 s", "2 min", "90 min", "1.5 h", "10.0 h"]);
    assert.deepStrictEqual([age(0), age(4.6), age(5.4), age(9.96), age(10), age(100)],
      ["0.0 s", "4.6 s", "5.4 s", "10.0 s", "10 s", "2 min"]);
    // Reasons that already end with a full stop do not get a second one.
    assert.strictEqual(reasonsText(["A", "B ends here.", "C..."]), "A. B ends here. C.");
    assert.strictEqual(reasonsText(["only"]), "only.");
  },

  "notices a clock jump"() {
    assert.strictEqual(clockJumped(null, 1e6, 500), false);
    assert.strictEqual(clockJumped({wall: 1e6, mono: 500}, 1e6 + 10000, 10500), false);
    assert.strictEqual(clockJumped({wall: 1e6, mono: 500}, 1e6 + 3600e3, 10500), true);
    assert.strictEqual(clockJumped({wall: 1e6, mono: 500}, 1e6 - 6000, 600), true);
  },

  "says what a rover sent, amber for anything but GGA"() {
    assert.deepStrictEqual(sentText(null), {text: null, warn: false});
    assert.deepStrictEqual(sentText({gga: 0, nmea_other: 0, other: 0}),
      {text: "nothing", warn: false});
    assert.deepStrictEqual(sentText({gga: 60, gga_hz: 1.0, nmea_other: 0, other: 0, types: {}}),
      {text: "GGA 1 Hz", warn: false});
    assert.deepStrictEqual(sentText({gga: 1, gga_hz: null, nmea_other: 0, other: 0}),
      {text: "GGA (1)", warn: false});
    assert.deepStrictEqual(sentText({gga: 10, gga_hz: 0.2, nmea_other: 2, other: 1,
      types: {GPRMC: 1, GNGSA: 1}}), {text: "GGA 0.2 Hz + 3 other (GPRMC, GNGSA, non-NMEA)",
      warn: true});
    assert.deepStrictEqual(sentText({gga: 0, nmea_other: 0, other: 4, types: {}}),
      {text: "4 other (non-NMEA)", warn: true});
    // A rate that is not a number (an old or odd status) is left out, never thrown on.
    for (const hz of ["1.0", "x", {}, [], true, NaN, Infinity]) {
      const out = sentText({gga: 5, gga_hz: hz, nmea_other: 0, other: 0});
      assert.ok(/^GGA( [0-9.]+ Hz| \(5\))$/.test(out.text), `${String(hz)}: ${out.text}`);
    }
    assert.deepStrictEqual(sentText({gga: 5, gga_hz: "2.04"}), {text: "GGA 2 Hz", warn: false});
    assert.deepStrictEqual(sentText({gga: "x", nmea_other: -1, other: null}),
      {text: "nothing", warn: false});
  },

  "rovers on the map: only clients with a position, stale after 10 s"() {
    const pts = roverPoints([
      {address: "192.0.2.1", position: {lat: 1.5, lon: 2.5, quality: 4, age_s: 1}},
      {address: "192.0.2.1", position: {lat: 1.6, lon: 2.6, quality: 1, age_s: 12}},
      {address: "192.0.2.2", position: null},
      {address: "192.0.2.3"},
    ]);
    assert.deepStrictEqual(pts.map(p => [p.key, p.stale]),
      [["192.0.2.1#1", false], ["192.0.2.1#2", true]]);
    assert.deepStrictEqual(roverPoints(undefined), []);
  },
};

let failed = 0;
const total = Object.keys(checks).length;
for (const [name, check] of Object.entries(checks)) {
  try {
    check();
    console.log("ok", name);
  } catch (error) {
    failed++;
    console.log("FAIL", name, error.message);
  }
}
console.log(`passed ${total - failed} of ${total} checks`);
process.exit(failed ? 1 : 0);
