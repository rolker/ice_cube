// Receiver controls: profile, guided base setup, command console and confirmation.
// Uses $, esc, dl and table from the page. All text from the app or the receiver goes
// through esc or textContent. The server applies the block and confirm rules itself;
// this page only shows what it says.
"use strict";

async function post(url, body) {
  let r;
  try {
    r = await fetch(url, {method: "POST", cache: "no-store",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify(body || {})});
  } catch (e) {
    return {error: "Lost contact with the app"};
  }
  let j;
  try { j = await r.json(); } catch (e) { j = {}; }
  if (!r.ok && !j.error) j.error = "The app answered " + r.status;
  return j;
}

function resultText(r) {
  const lines = [`${r.command}  →  ${r.outcome}${r.error ? ": " + r.error : ""}`];
  for (const line of r.replies || []) lines.push("  " + line);
  if (r.note) lines.push("  " + r.note);
  return lines.join("\n");
}

// Show what an API call returned in `out` (a <pre>): an error, a result, results,
// or a pending command, which opens the confirmation.
function show(out, j) {
  if (j.error) { out.textContent = j.error; out.className = "bad"; return; }
  out.className = "note";
  if (j.pending) { out.textContent = "Waiting for your confirmation…"; openConfirm(j, out); return; }
  if (j.outcome === "blocked") { out.textContent = "Refused: " + j.reason; out.className = "bad"; }
  else if (j.result) out.textContent = resultText(j.result);
  else if (j.results) out.textContent = (j.title ? j.title + "\n" : "") + j.results.map(resultText).join("\n") +
    (j.rechecking ? "\nChecking the profile again; the table updates by itself." : "");
  else if (j.message) out.textContent = j.message;
  else if (j.profile) out.textContent = "Profile checked.";
  loadHistory();
}

// ---- confirmation ----
// Focus starts on Cancel, and Send does nothing for ARM_MS after the dialog opens, so
// a double click or a repeated Enter cannot confirm by accident.
const ARM_MS = 1000;
let pending = null, expiry = null, arming = null;
function openConfirm(p, out) {
  const now = performance.now();
  pending = {id: p.pending, out, until: now + p.expires_s * 1000, armed: now + ARM_MS};
  $("ctitle").textContent = p.title;
  $("cbytes").textContent = p.commands.join("\n");
  const d = p.details || {}, rows = [];
  if (d.ecef) {
    rows.push(["ECEF X, Y, Z", d.ecef.map(v => v.toFixed(4)).join(", ") + " m"],
      ["Latitude", d.lat.toFixed(9) + "°"], ["Longitude", d.lon.toFixed(9) + "°"],
      ["Ellipsoidal height", d.height_m.toFixed(4) + " m"], ["From", d.from]);
  }
  if ("shift" in d) {
    // The shift from the base position the receiver now sends (RTCM 1005), so a
    // mistyped digit shows as a large move before anything is sent.
    const s = d.shift;
    // Millimetres, like the warning text (control.py metres()); never "−0.0 mm".
    const mm = v => { const a = Math.abs(v); const t = a >= 1 ? v.toFixed(3) + " m" :
      a >= 0.01 ? (v * 100).toFixed(1) + " cm" : (v * 1000).toFixed(1) + " mm";
      return t.replace(/^-(?=0\.0 mm)/, "").replace("-", "−"); };
    rows.push(["Current base (RTCM 1005)", d.current_base ? d.current_base.map(v => v.toFixed(4)).join(", ") + " m"
      : "unknown: " + d.shift_unknown],
      ["Shift E, N, U", s ? [s.east_m, s.north_m, s.up_m].map(mm).join(", ") : "unknown"],
      ["Shift horizontal, 3D", s ? mm(s.horizontal_m) + ", " + mm(s.total_m) : "unknown"]);
  }
  dl("cdetails", rows);
  $("cwarn").innerHTML = (p.warnings || []).map(w => `<p class="warn">${esc(w)}</p>`).join("");
  $("confirm").classList.add("open");
  $("cyes").disabled = true;
  clearTimeout(arming);
  arming = setTimeout(() => { if (pending) $("cyes").disabled = false; }, ARM_MS);
  $("cno").focus();
  clearInterval(expiry);
  const tickExpiry = () => {
    const left = Math.round((pending.until - performance.now()) / 1000);
    if (left <= 0) { closeConfirm("Not sent: the confirmation expired."); return; }
    $("cexpire").textContent = `This confirmation expires in ${left} s.`;
  };
  tickExpiry();
  expiry = setInterval(tickExpiry, 1000);
}
function closeConfirm(message) {
  clearInterval(expiry);
  clearTimeout(arming);
  $("confirm").classList.remove("open");
  if (pending && message) { pending.out.textContent = message; pending.out.className = "note"; }
  pending = null;
}
$("cno").onclick = () => closeConfirm("Cancelled; nothing was sent.");
$("confirm").addEventListener("keydown", e => { if (e.key === "Escape") closeConfirm("Cancelled; nothing was sent."); });
$("cyes").onclick = async () => {
  if (!pending || performance.now() < pending.armed) return;
  const {id, out} = pending;
  $("cyes").disabled = true;
  closeConfirm();
  out.textContent = "Sending…";
  show(out, await post("api/command/confirm", {id}));
};

// ---- receiver, profile and survey-in ----
const STATUS_CLASS = {match: "good", differ: "warn", unsupported: "note", error: "bad"};
function renderReceiver(s) {
  const r = s.receiver || {}, p = r.profile || {}, c = p.counts || {};
  // A check that failed before reading the profile leaves the table from any earlier
  // check; one that read it only in part shows what it read. Either way it is stale.
  const st = p.stale, at = st ? new Date(st.time * 1000).toLocaleTimeString() : "";
  const counts = p.checked_age_s == null ? "" :
    `${c.match || 0} match, ${c.differ || 0} differ, ${c.unsupported || 0} unsupported, ${c.error || 0} no answer` +
    ` (${Math.round(p.checked_age_s)} s ago)`;
  let prof;
  if (!st) prof = counts || "Not checked yet";
  else if (st.kind === "incomplete") prof = `Incomplete check at ${at} (${st.reason}): ${counts}`;
  else prof = `Check failed at ${at} (${st.reason})` + (counts ? `; showing an earlier check: ${counts}` : "");
  dl("rcv", [["Firmware", r.firmware], ["Mode", r.mode], ["App's UART", r.current_uart],
    ["Profile", prof + (r.checking ? "; checking again…" : "")],
    ["Busy with", r.busy_with || null], ["Command log", r.log_error || "OK"]]);
  $("rcvnote").textContent = r.check_note || "";
  const rows = p.results || [];
  $("prof").classList.toggle("stale", !!st);
  $("prof").innerHTML = rows.length ? "<tr><th>Setting</th><th>Expected</th><th>Receiver</th><th>Status</th></tr>" +
    rows.map(x => `<tr><td class="wrap">${esc(x.label)}${x.note ? `<div class="note">${esc(x.note)}</div>` : ""}</td>` +
      `<td class="mono">${esc(x.expected)}</td><td class="mono wrap">${esc(x.actual)}</td>` +
      `<td class="${STATUS_CLASS[x.status] || ""}">${esc(x.status)}</td></tr>`).join("")
    : `<tr><td class="note">${esc("No profile check yet")}</td></tr>`;
  // The survey-in configuration read at the last check: in fixed mode (PQTMCFGSVIN
  // mode 2) the receiver reports its configured position as the survey-in mean.
  const sv = r.svin || {}, inFixed = sv.mode === 2;
  $("svinnote").textContent = inFixed ? "The receiver is in fixed mode (PQTMCFGSVIN mode 2): " +
      "the mean it reports is its configured position, not a measurement."
    : sv.mode === 1 ? "Receiver in survey-in mode (PQTMCFGSVIN mode 1)."
    : sv.mode === 0 ? "Survey-in disabled on the receiver (PQTMCFGSVIN mode 0)."
    : `Survey-in configuration not known (${sv.reason || "not read yet"}).`;
  $("s-fixednote").textContent = inFixed ? "The receiver is already in fixed mode: the survey-in " +
    "mean is its configured position, not a measurement. Enter the position from PPP or another source." : "";
  const label = inFixed ? "current fixed position (receiver is in fixed mode)" : "from the survey-in mean (once finished)";
  const option = $("s-src").options[0];
  if (option.textContent !== label) option.textContent = label;
  // Not offered in fixed mode (the app refuses it anyway): the choice moves to coordinates.
  option.disabled = inFixed;
  if (inFixed && $("s-src").value === "survey") { $("s-src").value = "llh"; $("s-src").dispatchEvent(new Event("change")); }
  const v = s.survey_in || {}, m = s.survey_mean;
  $("s-progress").textContent = v.state ? `Survey-in: ${v.state} (Valid=${v.valid ?? "—"}), ` +
    `Obs ${v.observations ?? "—"} of CfgDur ${v.target ?? "—"}` +
    (v.accuracy_m != null ? `, mean accuracy ${v.accuracy_m} m` : "") + (v.age_s > 5 ? " (stale)" : "") +
    (m ? `. Mean: ${m.lat.toFixed(9)}°, ${m.lon.toFixed(9)}°, ${m.height_m.toFixed(3)} m` : "") +
    (v.valid === 0 ? ". Not running. A new survey-in starts only after a restart, and only " +
      "in base mode." : "")
    : "No survey-in status (PQTMSVINSTATUS) received.";
}

// ---- history ----
async function loadHistory() {
  try {
    const r = await fetch("api/commands", {cache: "no-store"});
    if (!r.ok) throw new Error(r.status);
    const entries = (await r.json()).entries.slice().reverse();
    table("hist", ["UTC", "User", "Command", "Outcome", "Reply or reason"], entries.map(e => [
      (e.utc || "").replace("T", " ").slice(0, 19), (e.user && (e.user.display_name || e.user.name)) || e.source,
      e.command, e.outcome, e.error || (e.replies || [])[0] || e.note || ""]), "No commands yet");
  } catch (e) { /* the status line already reports lost contact */ }
}

// ---- actions ----
const out = $("stepout");
$("console").addEventListener("submit", async e => {
  e.preventDefault();
  const text = $("cmd").value.trim();
  if (!text) return;
  $("cmdout").textContent = "Sending…";
  show($("cmdout"), await post("api/command", {command: text}));
});
$("s-fw").onclick = async () => { out.textContent = "Reading…"; show(out, await post("api/command", {command: "PQTMVERNO"})); };
const check = async () => { out.textContent = "Checking the profile…"; show(out, await post("api/profile/check")); };
$("s-check").onclick = check;
$("check").onclick = check;
$("apply").onclick = async () => { out.textContent = "Checking the profile first…"; show(out, await post("api/profile/apply")); };
$("s-base").onclick = async () => show(out, await post("api/steps/base-mode"));
$("s-svin").onclick = async () => {
  const count = Number($("s-count").value);
  show(out, await post("api/steps/survey-in", {count: Number.isInteger(count) ? count : $("s-count").value}));
};
$("s-src").onchange = () => { $("s-llh").hidden = $("s-src").value !== "llh"; $("s-ecef").hidden = $("s-src").value !== "ecef"; };
$("s-fixed").onclick = async () => {
  const source = $("s-src").value, body = {source};
  const val = id => $(id).value.trim();
  if (source === "llh") Object.assign(body, {lat: val("s-lat"), lon: val("s-lon"), height: val("s-h")});
  if (source === "ecef") Object.assign(body, {x: val("s-x"), y: val("s-y"), z: val("s-z")});
  show(out, await post("api/steps/fixed", body));
};
loadHistory();
setInterval(loadHistory, 15000);
