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
// (why there is no measured position now, or null) is passed through, and so are
// `source` ("gga" or "measured"), `reference_kind` ("median" or "base", the RTCM 1005)
// and `base_changed_age_s`. Kept points are dropped when the source changes.
function mergeTrack(kept, t, now) {
  const about = {source: t.source ?? "gga", reference_kind: t.reference_kind ?? "median",
    base_changed_age_s: t.base_changed_age_s ?? null};
  if (!t.reference || !t.points.length && t.full) {
    return {kept: null, track: Object.assign({window_s: t.window_s, reference: null,
      outliers_dropped: t.outliers_dropped, no_measured_reason: t.no_measured_reason ?? null,
      points: []}, about)};
  }
  const o = t.reference_offset;
  const add = t.points.map(p => [now - p[0], p[1] + o[0], p[2] + o[1], p[3] + o[2], p[4]]);
  const same = kept && kept.source === about.source;
  let pts = t.full || !same ? add : kept.points.concat(add);
  // The app's oldest point bounds what is still in the window, even if this page's
  // clock stood still meanwhile, as it can while a computer sleeps.
  let cutoff = now - t.window_s;
  if (t.oldest_age_s != null) cutoff = Math.max(cutoff, now - t.oldest_age_s - OLDEST_SLACK_S);
  let i = 0;
  while (i < pts.length && pts[i][0] < cutoff) i++;
  pts = pts.slice(Math.max(i, pts.length - t.max_points));
  const r = v => Math.round(v * 1e4) / 1e4;
  return {kept: {cursor: t.cursor, points: pts, source: about.source},
    track: Object.assign({window_s: t.window_s, reference: t.reference,
      outliers_dropped: t.outliers_dropped, no_measured_reason: t.no_measured_reason ?? null,
      points: pts.map(p => [Math.round((now - p[0]) * 10) / 10, r(p[1] - o[0]), r(p[2] - o[1]),
        r(p[3] - o[2]), p[4]])}, about)};
}

// The running mean of a track's points over the `windowS` seconds up to each point:
// [age_s, e, n, u] per point, oldest first like the points. 1 Hz single point scatter
// of a metre or two hides a sustained shift of a few decimetres; the mean shows it.
const RUNNING_MEAN_S = 300;
function runningMean(points, windowS = RUNNING_MEAN_S) {
  const out = [];
  let start = 0, se = 0, sn = 0, su = 0;
  for (let i = 0; i < points.length; i++) {
    const p = points[i];
    se += p[1]; sn += p[2]; su += p[3];
    while (points[start][0] - p[0] > windowS) {
      const q = points[start++];
      se -= q[1]; sn -= q[2]; su -= q[3];
    }
    const k = i - start + 1;
    out.push([p[0], se / k, sn / k, su / k]);
  }
  return out;
}

// What the drift plot shows, for its heading and text: {title, offsets}.
function trackLabel(track) {
  if (track && track.source === "measured") {
    return {title: "Drift: rtkrcv single point",
      offsets: track.reference_kind === "base" ? "Offsets are from the RTCM 1005 base position."
        : "Offsets are from the median position (no RTCM 1005 yet)."};
  }
  return {title: "Drift around the median position", offsets: "Offsets are from the median position."};
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

// What a caster client sent us, for the clients table: {text, warn}. `sent` is the
// app's per-client summary (gga, gga_hz, nmea_other, other, types). Anything other than
// GGA is worth a look (warn), since a rover normally sends GGA only, or nothing.
function sentText(sent) {
  if (!sent) return {text: null, warn: false};
  const count = (v) => (Number.isFinite(Number(v)) && Number(v) > 0 ? Number(v) : 0);
  const gga = count(sent.gga), nmea = count(sent.nmea_other), other = count(sent.other);
  if (!gga && !nmea && !other) return {text: "nothing", warn: false};
  const rate = sent.gga_hz == null ? NaN : Number(sent.gga_hz);
  const hz = Number.isFinite(rate) ? ` ${+rate.toFixed(1)} Hz` : "";
  const parts = gga ? [`GGA${hz || (gga === 1 ? " (1)" : ` (${gga})`)}`] : [];
  if (nmea + other) {
    const names = Object.keys(sent.types || {}).slice(0, 3);
    if (other) names.push("non-NMEA");
    parts.push(`${nmea + other} other` + (names.length ? ` (${names.join(", ")})` : ""));
  }
  return {text: parts.join(" + "), warn: nmea + other > 0};
}

// The rovers to draw on the map: caster clients whose latest GGA has a position.
// Returns [{key, lat, lon, quality, age_s, stale, address}]; stale after `maxAge` s.
const ROVER_STALE_S = 10;
function roverPoints(clients, maxAge = ROVER_STALE_S) {
  const out = [], seen = {};
  for (const c of clients || []) {
    const p = c.position;
    if (!p || typeof p.lat !== "number" || typeof p.lon !== "number") continue;
    const n = seen[c.address] = (seen[c.address] || 0) + 1;
    out.push({key: `${c.address}#${n}`, lat: p.lat, lon: p.lon, quality: p.quality,
      age_s: p.age_s, stale: p.age_s == null || p.age_s > maxAge, address: c.address});
  }
  return out;
}

if (typeof module !== "undefined") {
  module.exports = {mergeTrack, clockJumped, baseCentring, sentText, roverPoints, runningMean,
    trackLabel};
}
