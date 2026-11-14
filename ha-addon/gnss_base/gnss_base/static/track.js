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

// Seconds in words, for ages and uptimes: "42 s", "12 min", "3.5 h".
function duration(s) {
  return s < 90 ? Math.round(s) + " s" : s < 5400 ? Math.round(s / 60) + " min"
    : (s / 3600).toFixed(1) + " h";
}
// An age that is compared with a few-second threshold: one decimal under 10 s (the app
// sends 0.1 s), so 4.6 s and 5.4 s do not both read "5 s" either side of the rule.
function age(s) {
  return s < 10 ? (+s).toFixed(1) + " s" : duration(s);
}

// ---- Base station card (0.5.2): one word for the whole chain ----
// From api/status `s` (receiver, base position, MSM, storage and both outputs):
//   Down      no receiver data (not connected, none yet, or none for more than
//             DATA_DOWN_S), or no RTCM 1005/1006 received at all, or none for more than
//             BASE_DOWN_S (the receiver is not sending a base position: not in base
//             mode, a survey-in still running, or just restarted);
//   Degraded  something in the chain is stale (receiver data older than DATA_FRESH_S,
//             the 1005 stale as the app decides, GPS observations (MSM) missing or
//             older than MSM_FRESH_S), RTCM decode errors rising (`decodeErrors`, a
//             riseTrend of rtcm_decode_errors), free space below the floor, logging
//             paused or failing (storage.last_error), an enabled output down (the
//             caster also when its output callback is failing: `casterErrors`, a
//             riseTrend of caster.subscriber_errors.caster), or no output up at all;
//   Serving   none of the above, and at least one output up: the local caster
//             listening (and not failing) or the upload connected.
// A moved 1005 (baseShift) is shown on the card but is not a reason here.
// Returns {word, reasons}: the reasons in the order checked, empty when Serving.
const DATA_FRESH_S = 5, DATA_DOWN_S = 30, MSM_FRESH_S = 5;
const BASE_DOWN_S = 300;  // well past the app's longest stale threshold (120 s)
function baseSummary(s, decodeErrors, casterErrors) {
  const b = s.base_position || {}, c = s.caster || {}, u = s.upload || {}, d = s.storage || {};
  const dataAge = s.data_age_s;
  const down = reason => ({word: "Down", reasons: [reason]});
  if (!s.connected) return down("Receiver not connected" + (s.last_error ? `: ${s.last_error}` : ""));
  if (dataAge == null) return down("No data from the receiver yet");
  if (dataAge > DATA_DOWN_S) return down(`No data from the receiver for ${duration(dataAge)}`);
  if (b.lat == null) return down("No RTCM 1005/1006 received: the receiver is not sending a base position");
  if (b.age_s > BASE_DOWN_S) return down(`No RTCM 1005/1006 for ${duration(b.age_s)}: the receiver is not sending a base position`);
  const reasons = [];
  if (dataAge > DATA_FRESH_S) reasons.push(`No data from the receiver for ${age(dataAge)}`);
  if (b.stale) reasons.push(`RTCM 1005 is stale (${b.age_s == null ? "age unknown" : duration(b.age_s) + " old"})`);
  const gps = (s.msm || {}).GPS;
  if (!gps) reasons.push("No GPS observations (MSM) in the last 10 s");
  else if (gps.age_s > MSM_FRESH_S) reasons.push(`GPS observations are ${age(gps.age_s)} old`);
  if (decodeErrors && decodeErrors.rising) reasons.push(`${plural(decodeErrors.recent, "new RTCM decode error")} in the last minute`);
  if (storageLow(d)) reasons.push("Free space is below the floor");
  if (d.paused) reasons.push("Logging is paused");
  if (d.last_error) reasons.push(`Logging error: ${d.last_error}`);
  const casterUp = casterIsUp(c, casterErrors), uploadUp = u.state === "connected";
  if (c.enabled && !casterUp) reasons.push("Local caster " + casterText(c, casterErrors));
  if (u.enabled && !uploadUp) reasons.push(uploadLabel(u) + " " + uploadText(u));
  if (!casterUp && !uploadUp) reasons.push("No output is up");
  return {word: reasons.length ? "Degraded" : "Serving", reasons};
}

// Free space below the logging floor (storage.free_bytes < min_free_bytes).
function storageLow(d) {
  return d != null && d.free_bytes != null && d.min_free_bytes != null && d.free_bytes < d.min_free_bytes;
}

// Whether a cumulative error count is rising: `state` is the last result (null at
// first), `count` the app's count now and `now` the page time in seconds. Rising while
// the count grew within the last RISE_WINDOW_S; `recent` is by how much it grew in
// that window (0 when not rising). The first count seen is not a rise, and a count
// that fell (the app restarted) starts afresh: nothing from before is kept.
const RISE_WINDOW_S = 60;
function riseTrend(state, count, now) {
  if (count == null) return {count: null, rises: [], rising: false, recent: 0};
  if (!state || state.count == null || count < state.count) return {count, rises: [], rising: false, recent: 0};
  const rises = state.rises.filter(r => now - r.at <= RISE_WINDOW_S);
  if (count > state.count) rises.push({at: now, before: state.count});
  return {count, rises, rising: rises.length > 0, recent: rises.length ? count - rises[0].before : 0};
}
const plural = (n, what) => `${n} ${what}${n === 1 ? "" : "s"}`;

// The receiver's position mode, from PQTMCFGRCVRMODE (receiver.mode) and PQTMCFGSVIN
// read at the last check (receiver.svin). A rover is a rover whatever survey-in
// configuration it keeps (that is inert, and said in brackets). Otherwise "fixed
// position", "survey-in", a base with survey-in off (or survey-in off alone while the
// receiver mode is not known), or unknown with the reason.
function receiverModeText(r) {
  const sv = (r && r.svin) || {};
  const svin = sv.mode === 2 ? "fixed position" : sv.mode === 1 ? "survey-in" : sv.mode === 0 ? "survey-in off" : null;
  if (r && r.mode === "Rover") return "rover" + (svin && sv.mode !== 0 ? ` (${svin} configured, unused)` : "");
  if (sv.mode === 2 || sv.mode === 1) return svin;
  if (sv.mode === 0) return r.mode === "Base station" ? "base, survey-in off"
    : "survey-in off, " + (r.mode ? `receiver mode ${String(r.mode).toLowerCase()}` : "receiver mode not read");
  return "mode unknown" + (sv.reason ? ` (${sv.reason})` : "");
}

// Satellites per system in recent MSM and the age of the newest message among them
// (the Serving rule looks at GPS's own age), as "GPS 11, GLONASS 7, Galileo 9,
// BeiDou 10 sats, 1 s old"; null without MSM.
const MSM_ORDER = ["GPS", "GLONASS", "Galileo", "BeiDou", "QZSS", "NavIC"];
function msmText(msm) {
  const names = Object.keys(msm || {});
  if (!names.length) return null;
  const rank = n => { const i = MSM_ORDER.indexOf(n); return i < 0 ? MSM_ORDER.length : i; };
  names.sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
  const newest = Math.min(...names.map(n => Number(msm[n].age_s)).filter(Number.isFinite));
  return names.map(n => `${n} ${msm[n].satellites ?? "?"}`).join(", ") + " sats"
    + (Number.isFinite(newest) ? `, ${age(newest)} old` : "");
}

// Whether the base position in RTCM 1005 (`ecef`, metres) has moved: from the position
// the receiver was configured with (receiver.svin.ecef, known in fixed mode) when
// available, else from the first 1005 this page saw (`firstSeen`). A difference up to
// BASE_SAME_M (the 1005 resolution is 0.1 mm) counts as the same. Returns {text, moved}.
const BASE_SAME_M = 0.001;
function baseShift(ecef, configured, firstSeen) {
  if (!ecef) return {text: "none received", moved: false};
  const ref = configured || firstSeen;
  if (!ref) return {text: "first seen now", moved: false};
  const d = Math.hypot(ecef[0] - ref[0], ecef[1] - ref[1], ecef[2] - ref[2]);
  if (!Number.isFinite(d)) return {text: "not comparable", moved: false};
  const what = configured ? "the configured position" : "page load";
  if (!(d > BASE_SAME_M)) return {text: configured ? "matches " + what : "unchanged since " + what, moved: false};
  const m = d >= 1 ? d.toFixed(2) + " m" : d >= 0.01 ? (d * 100).toFixed(1) + " cm" : (d * 1000).toFixed(1) + " mm";
  return {text: `moved ${m} from ${what}`, moved: true};
}

// The reasons as one note: each ends without a full stop, so joining adds one each.
function reasonsText(reasons) {
  return reasons.map(r => String(r).replace(/\.+$/, "")).join(". ") + ".";
}

// The outputs in a few words. The upload is labelled RTK2go when that is its host.
// The caster is up when enabled, listening, and its output callback is not failing
// (`errors`: a riseTrend of caster.subscriber_errors.caster, or null to ignore).
function casterIsUp(c, errors) {
  return !!(c && c.enabled && c.listening && !c.error && !(errors && errors.rising));
}
function casterText(c, errors) {
  if (!c || !c.enabled) return "off (caster_enabled)";
  if (c.error) return `not running: ${c.error}`;
  if (!c.listening) return "not listening";
  const n = (c.clients || []).length, text = `listening, ${plural(n, "client")}`;
  return errors && errors.rising ? `${text}, output failing: ${plural(errors.recent, "error")} in the last minute` : text;
}
function uploadLabel(u) {
  return /rtk2go/i.test(String((u && u.host) || "")) ? "RTK2go" : "Upload";
}
function uploadText(u) {
  if (!u || !u.enabled) return "off";
  const why = u.reason ? ` (${u.reason})` : "";
  switch (u.state) {
    case "off": return "off" + (u.last_error ? `: ${u.last_error}` : "");  // settings problem
    case "connected": return "connected" + (u.connected_s != null ? ` for ${duration(u.connected_s)}` : "");
    case "waiting": return "retrying" + (u.retry_in_s != null ? ` in ${duration(u.retry_in_s)}` : "") + why;
    case "connecting": return "connecting";
    case "waiting for RTCM": return "waiting for RTCM";
    case "stopped for today": return "stopped for today" + why;
    case "stopped": return "stopped" + why;
    default: return (u.state || "unknown") + why;
  }
}

if (typeof module !== "undefined") {
  module.exports = {mergeTrack, clockJumped, baseCentring, sentText, roverPoints, runningMean,
    trackLabel, duration, age, reasonsText, baseSummary, storageLow, riseTrend, receiverModeText, msmText,
    baseShift, casterIsUp, casterText, uploadLabel, uploadText};
}
