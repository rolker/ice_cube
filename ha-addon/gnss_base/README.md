# GNSS Base Station app (Home Assistant OS)

Runs a GNSS base receiver from a Home Assistant box. Home Assistant calls add-ons
"apps" from 2026.2 on.

Written for a Quectel LG290P (for example on a SparkFun breakout, read through one port
of its USB serial bridge). It does not serve corrections yet.

- Reads the receiver and splits its stream into checked RTCM3 and NMEA messages
- Saves every byte to a daily file, and compresses finished days
- Records storage growth hourly in `storage-usage.csv`
- Shows status on a page in the Home Assistant sidebar, behind your Home Assistant login
- **Stage 2:** sends commands to the receiver, under the safety rules below; checks it
  against a receiver profile; guides you through setting it up as a base; and decodes
  RTCM (1005/1006 and MSM), so the page still works in base mode, when the receiver
  stops sending NMEA

## Status page

Besides the receiver, data, storage, signal and message status, the page shows:

- **Position**: latitude, longitude and ellipsoidal height (GGA altitude plus geoid
  separation; an empty separation counts as 0), with the receiver's own 1-sigma
  estimates from GST when it sends them. An altitude, separation or height beyond
  ±100 km is taken as corrupt, and that fix has no position.
- **Map**: the current position as a dot, a circle sized to the horizontal 1-sigma
  (from GST), and a faint trail of recent positions. The map fits itself once, on the
  first fix; after you drag it, it stays put until you press **Centre map**. When the
  fix is lost or more than 5 s old, the dot turns grey at the last known position, the
  circle is hidden and a note under the map says so.
- **Drift**: east/north offsets of the last 2 hours (one fix per second, at most 7,200)
  around their median position, coloured by age, with an up-versus-time strip below.
  Scales adjust to the data with a minimum of ±5 cm. Only fixes with quality above 0 are
  kept.

Offsets use a local tangent plane at a reference: latitude and longitude differences
are scaled by the WGS84 radii, and up is the difference in ellipsoidal height. That is
accurate to well under a millimetre within a kilometre, plenty for a base station that
should not move. History is kept in memory only, and starts again if the app restarts.
A fix more than 1 km from the history's origin is held back: if 30 such fixes in a row
agree with each other, all within 50 m of their median, the receiver has moved and the
history starts again there; otherwise they are dropped as outliers and counted
(`outliers_dropped`). Ages use the
system's monotonic clock, so a step in the wall clock does not disturb the history.

The JSON behind the page is `api/status` (current state) and `api/track` (the reference
position and `[age_s, e, n, u, quality]` for each kept fix, oldest first). `api/track`
also returns a `cursor`; `api/track?since=<cursor>` returns only the fixes after it, with
`full` false. If the cursor is not from the current history (the app restarted or the
receiver moved, or the cursor is malformed), all fixes are returned with `full` true.
Points are offsets from the current reference; add `reference_offset` to get offsets from
the history's fixed origin, which stay comparable between calls. `oldest_age_s` is the age
of the oldest fix the app still keeps. The page loads the whole history once, then only
new fixes every 10 s; it drops any fix it kept that is older than the app's oldest, and
loads the whole history again if the computer slept or its clock was set. That merging
is in `gnss_base/static/track.js`, served next to the page.

**Privacy.** Your position appears on this page, which sits behind your Home Assistant
login like any Ingress page. To draw the map, your browser fetches tiles from
OpenStreetMap (`tile.openstreetmap.org`), which tells OpenStreetMap which area you are
viewing. Nothing else leaves your network; if the tiles cannot load, for example offline,
the rest of the page still works. Map data © OpenStreetMap contributors.

The map uses [Leaflet](https://leafletjs.com) 1.9.4 (BSD 2-Clause), bundled unmodified
in `gnss_base/static/vendor/leaflet/`; see `NOTICE` and `LICENSE` there.

## Talking to the receiver (stage 2)

Commands come only from the status page. Home Assistant shows that page only to
administrators unless the app's `panel_admin` setting is changed; leave it as it is.
Commands use the same serial port the app reads, one at a time, in the order they
arrive. The app adds the checksum and matches the reply by command name
(`$<NAME>,OK…` or `$<NAME>,ERROR,<code>`), with a 3 s timeout (10 s for PQTMSAVEPAR).
Error codes are shown as text: 1 invalid parameters, 2 failed execution, 3 unsupported
command. If the port drops, for example while the receiver restarts, a command in flight
fails cleanly.

### Safety rules

The rules are enforced by the app itself, not by the page, and apply to every way a
command can arrive: the console, the guided steps and the profile. Each command is first
normalised (white space removed, upper case, a leading `$`, a checksum and the line end
removed; a checksum you type must match what you typed), and the bytes that are checked
are exactly the bytes that are sent.

- **Queries run at once**: the `,R` forms of the documented Set/Get commands, and
  PQTMVERNO, PQTMUNIQID, PQTMSN and PQTMLSTMSG.
- **Anything else needs a confirmation**: the app answers with a pending id and the exact
  bytes, and sends them only when the page confirms that id, within 60 s, once. Unknown
  commands, including unknown PQTM commands, are treated the same way.
- **Refused outright**, with an explanation:
  - factory reset (PQTMRESTOREPAR);
  - firmware or bootloader commands (the specification lists none; any command whose name
    contains BOOT, FIRMWARE, UPGRADE, FLASH, DOWNLOAD, ERASE and similar words is refused);
  - any PQTMCFGUART or PQTMCFGPROT write that could affect UART1 or the port the app is on.
    That includes the forms without a port index, which change the current port. A write to
    UART2 or UART3, named by index, is allowed (with confirmation) only once the app knows
    its own port from `PQTMCFGUART,R`.

### Command log

Every command sent or refused is appended to `<log_dir>/commands.jsonl`, one JSON object a
line: `utc`, `user` (the Home Assistant user id, name and display name that Ingress passes
in the `X-Remote-User-Id`, `X-Remote-User-Name` and `X-Remote-User-Display-Name` headers;
recorded only, never used to decide anything), `source` (console, startup check, or
"confirmed" with the step it came from), `command` (the exact sentence), `kind`, `replies`, `outcome` (`ok`, `error`,
`timeout`, `blocked`, `not sent` or `cancelled`) and `error`. A command that is `not sent`
is logged too, with the reason: an earlier command in the same batch failed, the rules
changed between staging and confirming, the port closed while a change waited its turn,
an earlier write is still stuck, or the app stopped. A `timeout` is either no reply in
time, or a write to the port that did not finish in time ("may have been sent");
`cancelled` is a write the app stopped during ("may have been sent"). Nothing else is
written until such a write is done; if it is still not done after 5 s, the whole batch
is `not sent`. Replies of the right name but the wrong shape, and late answers to an
earlier command, are kept in `ignored_replies`.
The page shows the last 20. Like the raw logs, this file can contain the base position.

A reply counts only if its shape fits the command: a write is answered by `OK` alone, a
query by `OK` with values that repeat any port or message it named (a
`PQTMCFGMSGRATE` reply may leave the port out), either by `ERROR,<code>`. A command that
timed out is owed its reply for 60 s, or until the receiver restarts: the first later
reply of that name that fits it is taken as its late answer, never as the answer to a
newer command, which then times out rather than succeed falsely. A late answer to the
very same query does count. The next command of the same name also waits 3 s, so most
late replies pass before it is sent. When the port closes, the app forgets which UART it
is on, so UART and protocol writes are refused until `PQTMCFGUART,R` answers again.
Confirming re-checks the whole batch first, and again just before each write: if any
command would now be classified differently, it and the rest are not sent and the change
must be staged again. The confirmation dialog starts on Cancel, and Send works only after
about a second.

### Receiver profile

`gnss_base/profile.toml` lists the settings the base should have; another file can be
given with `--profile`. It holds no coordinates. For each entry the app sends the `,R`
query and shows **match**, **differ**, **unsupported** (error 1 or 3) or **error** (no
reply). The app checks at startup and whenever the receiver reports a restart
(`$PQTMVER`), logs a warning for differences, and never changes anything by itself.
**Apply differences** checks again, then asks you to confirm the write commands for the
settings that differ, followed by PQTMSAVEPAR. Once the confirmed batch has run, whatever
its outcome, the app checks the profile again by itself (the same read-only queries), so
the table shows what the receiver now has, even if the page was closed.

The profile sets:

| setting | value |
|---|---|
| Receiver mode (PQTMCFGRCVRMODE) | 2, base station. Needs a save and a restart. |
| PQTMCFGRTCM | MSM4, no elevation mask, ephemerides output when updated. MSM7 is a commented alternative. |
| RTCM3-1005 | every fix |
| MSM 107X, 108X, 109X, 112X (GPS, GLONASS, Galileo, BeiDou) | every fix, offset 0 |
| RTCM3-1230, 1019, 1020, 1042, 1046 | rate 1 (the only rate the specification allows) |
| PQTMSVINSTATUS | every fix; base mode only |
| PQTMANTENNASTATUS | every fix; the specification says only the LG680P supports it, so an LG290P will likely show unsupported |
| GST | every fix |

Message rates are set for UART1 with the per-port form of PQTMCFGMSGRATE. In base mode
the receiver runs at 1 Hz, so "every fix" is once a second. The specification asks for
PQTMSAVEPAR and a restart after Set commands, so restart the receiver after applying.

### RTCM on the page

- **1005/1006**: the antenna reference point, converted from ECEF to WGS84 latitude,
  longitude and ellipsoidal height, is shown as "Base position (RTCM 1005)" under the
  position, and on the map as a hollow ring, separate from any GGA fix.
- **MSM1 to MSM7** for GPS, GLONASS, Galileo, QZSS, BeiDou and NavIC: the satellite,
  signal and cell masks give the satellites per constellation; MSM4 to MSM7 also give
  each cell's CNR (1 dB-Hz steps in MSM4/5, 1/16 dB-Hz in MSM6/7). The Signals table uses
  GSV for a constellation while GSV is fresh, otherwise MSM, and names the source. Signal
  IDs are named from the RTCM 10403.3 tables (for example GPS L1 C/A, L2C, L5, L1C;
  GLONASS G1, G2; Galileo E1, E5a, E5b; BeiDou B1I, B2I, B3I, B1C, B2a); others show as
  "signal N".

### First base setup

1. Open the page and press **Read** in step 1 (PQTMVERNO) to see the firmware.
2. Check the profile (step 2). Differences are expected on a new receiver.
3. Step 3 switches to base mode: PQTMCFGRCVRMODE,W,2, PQTMSAVEPAR, then PQTMSRR. The
   receiver restarts; NMEA output stops, so the fix and GSV disappear. The page then shows
   signals from MSM.
4. Check the profile again and **Apply differences**; the app then checks the profile
   again by itself. If a setting needs it, restart the receiver (console: `PQTMSRR`,
   with confirmation); the app checks again when the receiver is back.
5. Step 4 starts a survey-in (default 300 fixes, no accuracy limit): PQTMCFGSVIN,W,1,
   PQTMSAVEPAR, then PQTMSRR. The restart is needed: on firmware LG290P03AANR02A02S a
   PQTMCFGSVIN write is answered OK but takes effect only after a save and restart.
   Each command is sent only if the one before it answered OK. Progress shows under the
   step and in the Survey-in card, from PQTMSVINSTATUS: the state in words (Valid flag
   0 not running, 1 in progress, 2 finished), observations (Obs) of the target (CfgDur)
   and mean accuracy.
6. When the survey is finished (Valid=2), step 5 sets fixed mode from the survey-in mean,
   or from coordinates you enter (latitude, longitude, ellipsoidal height, or ECEF). Until
   then, and if PQTMSVINSTATUS is more than 5 s old, the survey-in mean is refused. The
   confirmation shows ECEF and latitude/longitude/height. It sends PQTMCFGSVIN,W,2,
   PQTMSAVEPAR and PQTMSRR, restarting for the same reason as step 4. Keep the
   coordinates out of public repositories.
7. Once the receiver sends 1005, the base position appears on the page.

## Changes

**0.3.1**

- The survey-in (step 4) and fixed-position (step 5) steps now restart the receiver
  (PQTMSRR) after saving: this firmware applies PQTMCFGSVIN only after a restart.
- The survey-in state from PQTMSVINSTATUS is shown in words, with the Valid flag, Obs,
  CfgDur and mean accuracy.
- After a confirmed **Apply differences**, the app checks the profile again by itself;
  the page shows "checking again" until the new result is in.
- Step 5 refuses the survey-in mean until the survey-in has finished (Valid=2), and says
  the state, Obs of CfgDur and mean accuracy; it used to only warn.
- If a check cannot complete (no answer to PQTMVERNO or PQTMCFGUART,R), the profile table
  is greyed and marked stale with the reason and time, instead of looking current.
- A stale survey-in status is no longer shown green when its last state was Finished.
- The profile check holds the port from PQTMVERNO to the last profile query, so a change
  confirmed meanwhile (a restart, say) is sent after the check, not between its queries.
  A profile query that times out ends the check, so a quiet receiver holds the port for
  at most one timeout per step.
- A profile check that does not get an answer to every query (one timed out, or the
  port closed during it) keeps its rows but marks them stale and "incomplete", with
  the reason. An ERROR reply counts as an answer.
- If PQTMCFGUART,R answers ERROR, answers with an OK the app does not understand, or
  does not answer, the app forgets the port it was on, so UART and protocol writes for
  it stay refused, and the check is marked incomplete. The profile is still read after
  an ERROR reply.
- The page says "Check failed at …" or "Incomplete check at …" with the reason, instead
  of "re-check failed" even when there was no earlier check. The time comes from the
  app.

## Install

1. Copy this whole `gnss_base` folder to the `local_apps` share. The older `addons` share
   is the same folder. Keep the LF line endings.
2. Settings → Apps → Install app → ⋮ → **Check for updates**, then install it from the
   local apps. If it doesn't appear, hard-refresh the page and repeat.
3. On the Configuration tab, pick the receiver under `device`.
4. Start the app and open **GNSS Base** from the sidebar.

The app holds the serial port while it runs, so nothing else can read it. It is set to
start manually for now.

## Options

| option | default | meaning |
|---|---|---|
| device | none | Serial port of the receiver. Use the `/dev/serial/by-id/` entry. |
| device_match | USB_Dual_Serial | The app refuses any device whose path lacks this text, which protects a Zigbee stick or other serial device. Set to match your receiver. |
| baud | 460800 | Receiver baud rate |
| log_dir | /share/gnss_base | Where logs go |
| min_free_gb | 10 | Logging pauses when free disk falls below this, and resumes when space returns |

## Logs

`<log_dir>/raw/YYYY-MM-DD.raw` holds that UTC day's bytes exactly as received. Finished days
become `.raw.gz`. If the system clock steps back into a day while that day is being
compressed, the bytes go to `YYYY-MM-DD-late-<n>.raw` instead, compressed in turn once
the day is over; read it after the day's own file. A name already taken by a `.gz` gets a
`-2`, `-3`… suffix, so nothing is overwritten. Logs contain your position, so keep them out of public repositories.
Home Assistant backups that include `/share` will include them too.

## Tests

From this folder, with `aiohttp` and `pyserial` installed (Python 3.11 or later):

    python -m unittest tests.test_app

The page's track merging is tested under Node (`tests/track_test.js`), which the suite
runs if `node` is installed and skips otherwise. Tests use synthetic positions only, and
a simulated receiver on a pseudo-terminal.
