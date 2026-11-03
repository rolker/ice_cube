// The page's copy of the position history, kept up to date from api/track. Plain
// functions without page state, so tests can run them under Node.
//
// After the first load the page asks only for points after `cursor`. Kept points are
// [time on the page's clock in s, e, n, u, quality], offsets from the history's fixed
// origin. The app answers with all points (full) when the cursor is not from its
// current history, for example after it restarted or the receiver moved.

const CLOCK_JUMP_MS = 5000;  // wall and page clocks disagreeing by more: ask for everything
const OLDEST_SLACK_S = 0.5;  // ages are rounded to 0.1 s

// Merge one api/track response `t` into `kept` (null at first) at page time `now`
// in seconds. Returns the new kept state and the track to draw, whose points are
// [age_s, e, n, u, quality] offsets from the current reference. `no_measured_reason`
// (why there is no measured position now, or null) is passed through.
function mergeTrack(kept, t, now) {
  if (!t.reference || !t.points.length && t.full) {
    return {kept: null, track: {window_s: t.window_s, reference: null,
      outliers_dropped: t.outliers_dropped, no_measured_reason: t.no_measured_reason ?? null,
      points: []}};
  }
  const o = t.reference_offset;
  const add = t.points.map(p => [now - p[0], p[1] + o[0], p[2] + o[1], p[3] + o[2], p[4]]);
  let pts = t.full || !kept ? add : kept.points.concat(add);
  // The app's oldest point bounds what is still in the window, even if this page's
  // clock stood still meanwhile, as it can while a computer sleeps.
  let cutoff = now - t.window_s;
  if (t.oldest_age_s != null) cutoff = Math.max(cutoff, now - t.oldest_age_s - OLDEST_SLACK_S);
  let i = 0;
  while (i < pts.length && pts[i][0] < cutoff) i++;
  pts = pts.slice(Math.max(i, pts.length - t.max_points));
  const r = v => Math.round(v * 1e4) / 1e4;
  return {kept: {cursor: t.cursor, points: pts},
    track: {window_s: t.window_s, reference: t.reference, outliers_dropped: t.outliers_dropped,
      no_measured_reason: t.no_measured_reason ?? null,
      points: pts.map(p => [Math.round((now - p[0]) * 10) / 10, r(p[1] - o[0]), r(p[2] - o[1]),
        r(p[3] - o[2]), p[4]])}};
}

// True if the wall clock (Date.now()) and the page clock (performance.now()), both in
// ms, moved apart by more than CLOCK_JUMP_MS since the last poll: the computer slept or
// the clock was set, so the kept ages cannot be trusted.
function clockJumped(last, wall, mono) {
  return last != null && Math.abs((wall - last.wall) - (mono - last.mono)) > CLOCK_JUMP_MS;
}

// Whether the map should centre on the base position now, so a switch from rover to
// base mode brings the base into view once, without fighting the user's panning on
// every refresh. `centredOn` is the base [lat, lon] the map last centred on (null at
// first), `base` the current, fresh base position or null, and `measuredNow` whether a
// current measured fix is shown (then the map follows that instead). Centres again only
// if the base moves more than BASE_MOVED_M. Returns {centre, centredOn}.
const BASE_MOVED_M = 5;
function baseCentring(centredOn, base, measuredNow) {
  if (measuredNow) return {centre: false, centredOn: null};  // next switch centres again
  if (!base || centredOn && distanceM(centredOn, base) <= BASE_MOVED_M) {
    return {centre: false, centredOn};
  }
  return {centre: true, centredOn: base};
}

// Approximate distance in metres between two nearby [lat, lon] points.
function distanceM(a, b) {
  const k = Math.PI / 180 * 6371000;
  const dLon = ((b[1] - a[1] + 540) % 360) - 180;
  return Math.hypot((b[0] - a[0]) * k, dLon * k * Math.cos((a[0] + b[0]) / 2 * Math.PI / 180));
}

if (typeof module !== "undefined") module.exports = {mergeTrack, clockJumped, baseCentring};
