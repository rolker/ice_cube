// Checks of the page's track merging. Run by tests.test_app, or: node tests/track_test.js
// Synthetic offsets only.
"use strict";
const assert = require("assert");
const path = require("path");
const {mergeTrack, clockJumped, baseCentring, sentText, roverPoints} = require(
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
for (const [name, check] of Object.entries(checks)) {
  try {
    check();
    console.log("ok", name);
  } catch (error) {
    failed++;
    console.log("FAIL", name, error.message);
  }
}
process.exit(failed ? 1 : 0);
