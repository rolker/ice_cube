# GNSS Base Station app (Home Assistant OS)

Runs a GNSS base receiver from a Home Assistant box. Home Assistant calls add-ons
"apps" from 2026.2 on.

Written for a Quectel LG290P (for example on a SparkFun breakout, read through one port
of its USB serial bridge).

- Reads the receiver and splits its stream into checked RTCM3 and NMEA messages
- Saves every byte to a daily file, and compresses finished days
- Records storage growth hourly in `storage-usage.csv`
- Shows status on a page in the Home Assistant sidebar, behind your Home Assistant login
- **Stage 2:** sends commands to the receiver, under the safety rules below; checks it
  against a receiver profile; guides you through setting it up as a base; and decodes
  RTCM (1005/1006 and MSM), so the page still works in base mode, when the receiver
  stops sending NMEA
- **Stage 3a (0.4.0):** serves the receiver's RTCM corrections to rovers on your network
  (a small NTRIP caster), and can upload them to an NTRIP caster such as RTK2go, so a
  rover on cellular data can use them too
- **Stage 3c (0.5.0):** shows a live measured position in base mode, from RTKLIB's
  `rtkrcv` running on a copy of the receiver's RTCM (single point, metre level)
- **Stage 6 (0.6.0):** checks the base antenna's position continuously against a CORS
  station by RTK (a second `rtkrcv`, static, our receiver as the rover and the
  station's NTRIP stream as the base), centimetre level, as an offset from our RTCM 1005
- **0.7.0:** a second reference check against another station, a GGA uplink for
  "nearest station" mountpoints, each caster's mountpoint list on its card, and a
  comparison of the two stations

## Status page

Besides the receiver, data, storage, signal and message status, the page shows:

- **Base station**, first: **Serving** when the receiver is connected and sending
  (data 5 s old or less), the RTCM 1005/1006 base position is not stale, GPS
  observations (MSM) are 5 s old or less, and at least one output is up (the local
  caster listening, or the upload connected). **Degraded** when any of those is stale,
  RTCM decode errors grew in the last minute (the line says by how many), free space
  is below `min_free_gb`, logging is paused or failing, an enabled output is down (the
  local caster also counts as down while its output callback is failing: hub
  subscriber errors that grew in the last minute), or no output is up. **Down** when the receiver is not connected, has sent nothing, or
  nothing for more than 30 s, or no 1005/1006 has been received at all or for more
  than 5 min (not in base mode, a survey-in still running, or just restarted). When the page loses contact with the app the card
  says "No contact" instead of keeping its last word. The reasons are listed under the
  word. The receiver's mode comes from `PQTMCFGRCVRMODE` and `PQTMCFGSVIN` as read at
  the last profile check: a rover is a rover whatever survey-in setting it keeps. The
  note about rovers on the upload caster is shown only while an upload is set up. The
  corrections line says whether the 1005 position matches the position the receiver was
  configured with (from `PQTMCFGSVIN,R`, in fixed mode) or, when that is not known, the
  first 1005 this page saw; a difference over 1 mm is amber. Storage turns red only below
  the floor. Rovers using RTK2go are not visible to the app; those on the local caster
  are in its card.

- **Position**: latitude, longitude and ellipsoidal height (GGA altitude plus geoid
  separation; an empty separation counts as 0), with the receiver's own 1-sigma
  estimates from GST when it sends them. An altitude, separation or height beyond
  ±100 km is taken as corrupt, and that fix has no position. GGA quality 7 (manual
  input) is what a receiver in fixed base mode reports if GGA is turned on: its
  configured position repeated, not a measurement. The fix panel says "Fixed position,
  not measured", and that position is neither drawn as a fix on the map nor kept for
  the drift plot.
- **Base position (RTCM 1005)**, in the fix panel: the antenna reference point from the
  latest 1005 or 1006, as latitude, longitude and ellipsoidal height (WGS84, converted
  from ECEF), the ECEF X, Y and Z, the station ID, the antenna height (1006 only) and
  its age. It is marked stale, and greyed, when no 1005/1006 has arrived for more than
  10 s, or for 3 times their recent interval if that is longer (at most 120 s), so a
  receiver sending 1005 every 30 s is not stale between messages. In base mode the
  receiver may send no NMEA, so this is the position the page shows.
- **Map**: the current position as a dot, a circle sized to the horizontal 1-sigma
  (from GST), and a faint trail of recent positions. The map fits itself once, on the
  first fix; after you drag it, it stays put until you press **Centre map**. When the
  fix is lost or more than 5 s old, the dot turns grey at the last known position, the
  circle is hidden and a note under the map says so. The base position from RTCM 1005
  is a hollow orange ring (dashed and grey when stale), with a legend under the map.
  With no current measured fix (none, lost or stale, as after switching from rover to
  base mode), the map centres once on a fresh base position, and again only if it moves
  more than 5 m, so it does not undo your panning; **Centre map** also centres on it.
- **Drift**: east/north offsets of the last 2 hours (one fix per second, at most 7,200)
  around their median position, coloured by age, with an up-versus-time strip below.
  Scales adjust to the data with a minimum of ±5 cm. Only measured fixes are kept: GGA
  quality 1 to 6. Quality 0 (no fix), 7 (manual input, the fixed base position) and 8
  (simulator) are not, so the plot never shows a constant as if it were drift. When there
  is no measured position, the plot and the map note say why: the receiver is in base
  mode (a recent 1005/1006, no measured GGA), it reports only its fixed position (GGA
  quality 7), or its GGA has no usable fix. Other RTCM, such as MSM from a rover, does not
  count as base mode. A fix counts as measured only if its position was accepted, not by
  quality alone. A live position computed from the observations is planned.

Offsets use a local tangent plane at a reference: latitude and longitude differences
are scaled by the WGS84 radii, and up is the difference in ellipsoidal height. That is
accurate to well under a millimetre within a kilometre, plenty for a base station that
should not move. History is kept in memory only, and starts again if the app restarts.
A fix more than 1 km from the history's origin is held back: if 30 such fixes in a row
agree with each other, all within 50 m of their median, the receiver has moved and the
history starts again there; otherwise they are dropped as outliers and counted
(`outliers_dropped`). Ages use the system's monotonic clock, so a step in the wall clock
does not disturb the history.

The JSON behind the page is `api/status` (current state) and `api/track` (the reference
position and `[age_s, e, n, u, quality]` for each kept fix, oldest first, and
`no_measured_reason`, a sentence or null, also in `api/status`). `api/track` also
returns a `cursor`; `api/track?since=<cursor>` returns only the fixes after it, with
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

A check that reads the profile ends with `PQTMCFGSVIN,R`, the survey-in configuration
(not a profile row: it holds the site's coordinates). The reply is
`$PQTMCFGSVIN,OK,<Mode>,<CFG_CNT>,<3D_AccLimit>,<ECEF_X>,<ECEF_Y>,<ECEF_Z>[,<Distance>]`
(specification v1.3, 2.3.22): mode 0 disabled, 1 survey-in, 2 fixed. `api/status` has
it as `receiver.svin`: `mode`, `mode_name`, when fixed the configured position as `ecef`,
`lat`, `lon` and `height_m` (all null if the receiver reports zeros or something
unreadable), `reason` when the mode is unknown (an ERROR, no answer, a reply the app does
not understand, not read yet, or not read again since a restart or port loss; a query not
sent because an earlier one timed out leaves the previous reading in place) and
`read_age_s`, seconds since the last read or attempt. An unanswered PQTMCFGSVIN,R leaves
the mode unknown; it does not make the profile table incomplete. Step 5 does not offer
the survey-in mean in fixed mode. The Survey-in card and step 5 say when the receiver is
in fixed mode, because PQTMSVINSTATUS then reports the configured position as the "mean"
(Valid=2), which is not a measurement. The firmware applies a PQTMCFGSVIN write only
after a restart, and every restart starts a check, so what the last check read is the
configuration in force; after a restart or a port loss it is unknown again until that
check reads it, and after a PQTMCFGSVIN write that reached the port (answered OK,
unanswered or failed: it may have been sent; not one answered ERROR) it is unknown until
the app sees the receiver restart (`$PQTMVER`), because a
check before that may read the written values while the receiver still runs the old
ones. A restart the app did not see (the port was down) leaves it unknown until the next
restart.

The profile sets:

| setting | value |
|---|---|
| Receiver mode (PQTMCFGRCVRMODE) | 2, base station. Needs a save and a restart. |
| PQTMCFGRTCM | MSM4, no elevation mask, ephemerides when updated and every 60 s (EPH_Mode 2, EPH_Interval 60). MSM7 is a commented alternative. |
| RTCM3-1005 | every fix |
| MSM 107X, 108X, 109X, 112X (GPS, GLONASS, Galileo, BeiDou) | every fix, offset 0 |
| RTCM3-1230, 1019, 1020, 1042, 1046 | rate 1 (the only rate the specification allows) |
| PQTMSVINSTATUS | every fix; base mode only |
| GST | every fix |

Message rates are set for UART1 with the per-port form of PQTMCFGMSGRATE. In base mode
the receiver runs at 1 Hz, so "every fix" is once a second. The specification asks for
PQTMSAVEPAR and a restart after Set commands, so restart the receiver after applying.

### RTCM on the page

- **1005/1006**: the antenna reference point, converted from ECEF to WGS84 latitude,
  longitude and ellipsoidal height, is shown as "Base position (RTCM 1005)" in the fix
  panel, and on the map as a hollow ring, separate from any GGA fix. In `api/status` it
  is `base_position`, with `age_s`, `stale` and `stale_after_s` (the age at which it is
  stale: 10 s, or 3 times the recent 1005/1006 interval if longer, at most 120 s).
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
   then, and if PQTMSVINSTATUS is more than 5 s old, the survey-in mean is refused. Once
   the receiver is in fixed mode, the "mean" it reports is the configured position, so
   the survey-in mean is refused as a source then too (the option is relabelled): enter
   the new position, from PPP for instance; a survey-sourced batch is refused again at
   confirmation, and just before its first write (once the position is written, the save
   and restart follow), if a check has found fixed mode since,
   and a step 5 batch staged while a base position was shown is refused if the receiver
   has since started sending another position, or its position is no longer known (the
   shift shown would be out of date); a survey-sourced batch is refused if the mean has
   moved or is no longer usable, or if the survey-in configuration it was staged under
   has been read again, lost or found since. The survey-in mean must have arrived since the port last
   opened and the receiver last restarted.
   The confirmation shows ECEF and latitude/longitude/height, the base position the
   receiver now sends in RTCM 1005, and the shift to the new position as east, north, up,
   horizontal and 3D (unknown without a fresh 1005 received since the port last opened
   and the receiver last restarted: the step still works, with a warning). A shift of
   more than 1 m gets a warning to check every digit, since a wrong digit moves every
   rover by that amount.
   It sends PQTMCFGSVIN,W,2, PQTMSAVEPAR and PQTMSRR, restarting for the same reason as
   step 4. Keep the coordinates out of public repositories.
7. Once the receiver sends 1005, the base position appears on the page.

## Corrections out (stage 3a)

The receiver's RTCM goes to two outputs, which share one filter, so a rover gets the same
stream from either:

- **RTCM only**: NMEA and PQTM sentences never leave the app, and only whole frames whose
  CRC checked out are sent.
- **1005/1006 and 1033** (base position, receiver and antenna names) at most once every
  10 s; the first one goes to a new rover at once. The receiver still sends 1005 every
  second into the raw log and the page.
- Everything else as it comes: MSM4 and 1230 every second, ephemerides when they change
  and every 60 s (with the profile's EPH_Mode 2).

Nothing from the network can reach the receiver: the caster and the upload never get
the command or serial code, and anything a rover or caster sends is read and dropped.
Commands still come only from the page, through Home Assistant.

### Local NTRIP caster

An NTRIP v1 caster on port 2101, for a rover on your network, for example SW Maps on a
phone feeding a SparkFun RTK Express over Bluetooth. In SW Maps (or another NTRIP
client):

| field | value |
|---|---|
| Host | the Home Assistant machine's IP address on your network |
| Port | 2101 (or the host port you set in the app's Network section) |
| Mountpoint | `caster_mountpoint`, default `BASE`; asking for the list of mountpoints also works |
| User, password | anything, or none, unless you set `caster_password` |
| NTRIP version | 1 (version 2 is planned) |

- **Sourcetable**: one `STR` record for the mountpoint, with the RTCM message types and
  rates, the constellations, latitude and longitude `0.00` (the stream's 1005 carries
  the position) and, in the last field (misc), the frame our position is in and the
  epoch: `ITRF2020 epoch 2026.85` (from `base_frame` and `base_epoch`, or today's date,
  which is then said; see Frames under Reference check). NTRIP's record has no datum
  field, so that is where it goes. A wrong `base_frame` or `base_epoch` leaves the field
  empty and puts the message on the caster's card; rovers are still served.
- **Login**: off by default, so any login or none is accepted. Set `caster_password`
  (and optionally `caster_user`) to require one; a wrong or missing login gets
  `401 Unauthorized`, and 5 failures from one address in a minute lock it out for 60 s.
  Without a password anyone on your network can read the corrections, which include the
  base position. Do not forward port 2101 on your router.
- **One address for everyone?** Home Assistant normally passes the rover's own address
  through to the app. If Docker's userland proxy is in the path instead (some setups,
  and connections to the host's own address), every LAN client arrives from one internal
  address. Then the login lockout, the limit on unfinished requests and the connection
  log's rate limits apply to all rovers together: one rover with a wrong password could
  lock the others out for a minute. The lockout is kept short (60 s) for that reason.
  The client table shows the address the app sees.
- **Sourcetable**: `GET /` or an unknown mountpoint returns the sourcetable, one `STR`
  line for the mountpoint with latitude and longitude 0.00 (the stream's 1005 has the
  real position).
- **Clients**: at most `caster_max_clients` at once (more get `503`). If a rover cannot
  keep up, frames for it are skipped and counted once 32 kB are waiting, and it is
  dropped if nothing drains for 30 s. Other rovers and the raw log are unaffected. One
  address may have at most 4 requests still unfinished (20 in all), so a client that
  sends its request very slowly cannot keep a rover elsewhere out.
- **What rovers send**: counted and then dropped; it never reaches the receiver. The
  **Sent to us** column shows it, for example `GGA 1 Hz`, or `GGA 1 Hz + 3 other (GPRMC,
  ...)` in amber when anything but GGA arrives. The first 5 lines per connection go to
  `caster.jsonl`. A line that starts with `$` (NMEA, receiver commands) is logged as
  sent, positions included, escaped and cut to at most 200 characters (never inside an
  escape); before the cut, credentials anywhere in the whole line are replaced by `-`:
  an `Authorization` or `Proxy-Authorization` header or a rev1 `SOURCE` and the rest of
  the line, `user:password@` in a URL and password-like query values. Any other line
  (HTTP headers, URLs, binary, the rest of a line over 512 bytes that arrived without a
  line end) is logged only as its first word and its length, such as `Authorization: (42
  bytes)` or `GET (61 bytes)`. The first word is shown only when it is a label (letters,
  digits, `-`, `_`, at most 32 characters) followed by a space or `:`; anything else, such
  as a bare token, a query string or the rest of a split line, shows only the length. At most one sample is a GGA (the first), so a rover's GGA
  does not crowd out its commands or other data. The disconnect line sums up what came
  and carries the last GGA line received (`last_gga_line`, handled like a `$` sample,
  with `last_gga_shortened`), so the log shows where the rover ended up. The log lives
  in `log_dir` on the device (the share), like the raw logs; since it holds rover
  positions, never copy it into a public repository. A rover's latest GGA position also
  shows on the map as a green diamond (grey once its GGA is over 10 s old).
- **Port in use**: if host port 2101 is taken, Home Assistant cannot start the app at
  all. Change the host port in the app's Network section (the container side stays 2101),
  or turn it off there to run without the caster.
- If you prefer RTKLIB, `str2str -in ntrip://:@<ip>:2101/BASE -out file://test.rtcm3` is
  an independent client to check the stream with.

### Upload to RTK2go

Off until both `upload_mountpoint` and `upload_password` are set. The app then connects
to `upload_host:upload_port` (default `rtk2go.com:2101`) as an NTRIP server: it sends
`SOURCE <password> /<mountpoint>` (NTRIP rev1, as RTKLIB and most RTK2go bases do), and
after `ICY 200 OK` writes the filtered RTCM. Rovers then connect to RTK2go as clients.

1. Reserve a mountpoint on rtk2go.com. The reservation fixes the style; this app speaks
   **rev1** (`upload_style: rev1`). If you choose rev2, the app refuses to upload and
   says so: rev2 is planned.
2. Fill in `upload_mountpoint` and `upload_password` and restart the app.
3. The **Upload (RTK2go)** card shows the state, bytes sent and any refusal.

How it keeps to RTK2go's rules:

- **Only while RTCM flows**: it connects only if the receiver sent RTCM in the last 3 s,
  closes the connection if no RTCM has gone out 5 s after the caster's OK, and
  disconnects once if none arrives from the receiver for 8 s, then waits for it to
  return. SNIP drops a source that sends nothing for about 10 s, and repeated
  connect-then-nothing is a ban reason.
- **One connection**: never more than one upload connection, and the old one is closed
  before a new one opens.
- **Backoff**: retries no sooner than 60 s after the previous attempt, doubling to 30 min
  (with up to 10% added), reset after 30 min connected. `ERROR - Bad Password` or
  `Mount Point Taken or Invalid` (perhaps another source still holds it), or an HTTP 401:
  30 min, red on the page. After 3 refusals without an accepted connection in between
  (other failures between them do not reset the count) the upload **stops** and the card
  says what to check (mountpoint and password against the reservation, and that no
  other base still sends to it). A reply that looks like a ban (HTTP `403` or `429`, an
  HTML page, or the words "banned" or "blocked"): 4 h, and it stops after 2 without an
  accepted connection in between.
  Saving changed upload options starts again (after at least 60 s since the last try);
  restarting with the same options allows one more try once the wait is over. At most
  100 attempts per UTC day. A bare `OK` reply (some rev1 casters other than SNIP) is
  accepted like `ICY 200 OK`.
- **Restarts keep the waits**: the next allowed attempt, the refusal and ban counts, and
  the day's attempts and bytes are kept in `upload-state.json` in the app's private
  `/data` folder (not the share), saved after each attempt and, while connected, every
  5 min or 10 MB. A restart within a wait still waits, and a restart after the day's
  400 MB stays stopped until midnight UTC; a changed password, mountpoint, host, port or
  style drops a refusal's wait but not a ban's, which is about the IP address. If the
  clock is behind the last attempt in the file (not yet synced after a boot), only the
  60 s gap is kept. A file that cannot be read or holds anything unexpected (such as
  `NaN`) is ignored with a warning, and the first attempt still waits 60 s. The file
  holds a salted hash of the settings, never the password.
- **Data**: RTCM only, 1005 every 10 s. If a minute averages over 30 kbit/s (RTK2go's
  limit is 35; a base sends about 12) the upload stops for 30 min; at 400 MB in a UTC day
  (RTK2go's limit is 500) it stops until midnight UTC. A stalled upload (64 kB waiting for
  60 s) is closed and retried with the backoff.
- **Password**: from the options only (there is no command-line flag for it). It is
  written to the connection and nowhere else: not to the app log, `caster.jsonl`, the
  command log, the page or `api/status` (which say only whether one is set). Replies that
  echo it have it replaced by `***`. NTRIP rev1 sends it in clear text, as RTK2go's port
  2101 has no encryption; it only protects the mountpoint from being taken.
- Never test against RTK2go: its rules do not allow it. The tests use a fake caster on
  localhost.

RTK2go shows the mountpoint's position (rounded) on its public map and sourcetable, and
anyone can read the full 1005 from the stream. It also sees your IP address.

### Connection log

`<log_dir>/caster.jsonl`, one JSON object a line: `utc`, `event` (`connect`,
`disconnect`, `auth_failed`, `refused`, `sourcetable`, `rover_sent`, `suppressed`;
`upload_connect`, `upload_disconnect`, `upload_error`), `address`, `user_agent`,
`ntrip_version`, `user` (on connect and disconnect only; a failed login logs just the
address), `bytes_sent`, `frames_skipped`, `duration_s`, `reason`, what the client sent
(`bytes_received`, `gga`, `gga_hz`, `nmea_other`, `other`, `types`) and the last GGA
line (`last_gga_line`, null if none, and `last_gga_shortened`) on disconnect, the sample
lines (`n`, `kind`, `type`, `text`, `shortened`) on `rover_sent`, and for the upload
`host`, `port`, `mountpoint`, `kind` and the caster's `reply`. Passwords are never
logged: a sample that does not start with `$` is only its first word and length, and
credentials in a `$` line are replaced by `-` (see above). The `$` samples (at most one
GGA) and `last_gga_line` are otherwise what the rover sent, positions included, so keep
this file on the device and never copy it into a public repository.

So that a busy or hostile network cannot fill the disk:

- each address gets at most 10 sourcetable, refused and failed-login lines a minute, 30
  lines of any kind, and on top of those 30 `disconnect` lines with a budget of their
  own, so a rover that reconnects often still has its last GGA lines logged while one
  address in a connect/close loop cannot take more than 60 of the 200 lines all
  addresses together get. The rest are counted, and one `suppressed` line per address
  (`count`, `events`) follows when the minute ends;
- at 10 MB the file is renamed `caster.jsonl.1` (older ones move to `.2`, `.3`; the
  oldest is deleted), so it never takes more than about 40 MB;
- nothing is written while free space is below `min_free_gb`, like the raw log;
- lines are written by a background task, never on the path that reads and logs the
  receiver; if the disk is slow and 2000 lines are waiting, more are dropped and counted.

The caster card shows any lines held back, dropped or not written.

## Measured position (stage 3c)

In base mode the receiver only repeats the position it was given (GGA quality 7, or no
GGA at all), so the page would have no measured position. The app runs RTKLIB's `rtkrcv`
(RTKLIB-EX 2.5.1, built into the image as `rtkrcv-ex`; see Install) on a copy of the
receiver's RTCM and shows its solution on the map and the drift plot, as offsets from the
RTCM 1005 base position.

What to expect: this is **single point positioning** on L1 (GPS, GLONASS, Galileo,
BeiDou), with broadcast orbits and RTKLIB's default ionosphere model, because RTCM
carries no ionosphere parameters. Expect 1 to 2 m of scatter horizontally, more
vertically, and Up a few metres high. A steady offset is normal. It catches gross
problems (a wrong base position typed in, an antenna knocked over, the receiver tracking
nothing useful), not centimetre drift. Centimetre checks need a rover (RTK) or PPP.

How it runs:

- rtkrcv starts when the first MSM message arrives, and keeps running. A receiver that
  sends no MSM (a rover without RTCM output) never starts it.
- It reads the RTCM from a small server of the app on `127.0.0.1` and sends its solution
  back to another; both use a port the system picks. rtkrcv opens no listening port of
  its own, and nothing from it can reach the receiver.
- It runs without a console (`-nc`): with a console on a pseudo-terminal, RTKLIB-EX does
  not exit on SIGTERM (it waits for its console thread), so it would be killed at every
  stop and lose its orbit cache. A 2.4.3 b34 build (`--rtkrcv /usr/bin/rtkrcv`, which has
  no `-nc`; the app tells the two apart with `--version`) gets a pseudo-terminal the app
  keeps, as before. There is no telnet console either way.
- Its working folder is `<data dir>/rtkrcv/` (`/data/rtkrcv/` in Home Assistant), kept
  across restarts. rtkrcv saves the satellite orbits it knows there (`rtkrcv.nav`) when it
  stops cleanly and loads them when it starts, so after a restart it has a solution at
  once. The app stops it with SIGTERM, and kills it only if it has not exited within 3 s
  (then the next start waits for orbits: about a minute with the profile's EPH_Mode 2,
  up to 2 hours without it). Linux also sends rtkrcv SIGTERM if the app itself dies, so
  it never outlives the app.
- If it exits it is restarted after 5 s, doubling up to 5 minutes; the delay resets after
  10 minutes of running. Once it has received at least 4 GPS ephemerides (so a cold
  start waiting for orbits is not mistaken for a hang), if MSM keeps arriving and no
  solution comes for 5 minutes, it is restarted the same way.
- Settings: single point, L1, elevation mask `measured_elevation_mask` (15 deg), SNR mask
  `measured_snr_mask` (35 dB-Hz; 0 turns it off), RAIM fault exclusion on. The SNR mask
  matters: weak signals make RTKLIB reject many epochs; 38 lowers the Up bias a little at
  the cost of satellites.

The drift plot and the map use the receiver's GGA while it reports a measured fix, and
rtkrcv's solution otherwise, while its latest solution is under a minute old
(`api/track?source=gga` or `?source=measured` forces one).
For rtkrcv the reference is the latest 1005; when 1005 changes (a survey-in, or a new
fixed position), the history is kept and the reference moves. Without a 1005 the
reference is the median, as for GGA. The **Measured position** card shows the state,
the last solution, mean and spread over the last 10 minutes and 2 hours, rtkrcv's CPU and
memory, restarts, and its last output lines when something is wrong.

Solutions are logged to `<log_dir>/measured/YYYY-MM-DD.pos` (see Logs).

Stopping: Home Assistant kills an app 10 s after asking it to stop, so the app aims to be
done in under 9 s. Everything stops at once, each part bounded by the time left: both
rtkrcv (SIGTERM, SIGKILL after 3 s) and then their solution logs, the outputs and then the
connection log, the receiver and the raw log, the status page (1.5 s for open requests).
File work still running at the deadline is abandoned, not waited for. A day being
compressed then keeps its original file and is compressed again after the next start;
the half-written `.gz.partial` is removed then. A log line being written at that moment
may be cut short.

## Reference check (stage 6)

The measured position above is metre level. To check the base antenna's position at
centimetre level, every day, without walking outside, the app can run a second `rtkrcv`
in **RTK static mode** with our receiver as the rover and a CORS station's live RTCM
stream (over NTRIP) as the base. The app itself is the NTRIP client: it logs in to the
caster, shows what the caster answers, and hands the station's stream to rtkrcv over a
local socket, like our receiver's. The solution is the position of our antenna in the
station's frame; the card shows it as an offset from our own RTCM 1005 position.

What it answers: is the coordinate we gave the receiver right, and does it stay right;
and what is the real offset between our frame and the network's frame, measured. What it
does not do: react to a move within seconds (static mode smears a move into a slow drift
until the next session; the Measured position card is the one that catches a move) or
work without internet and a CORS login.

**Options** (all in the app configuration; nothing on the command line carries the
password):

- `reference_enabled`: true to run it. Off by default.
- `reference_host`, `reference_port` (2101), `reference_mountpoint`, `reference_user`,
  `reference_password`: the NTRIP caster and login of your network (MaCORS, a state DOT
  network, ...). Pick a **single-station** mountpoint in RTCM 3 with MSM and 1005/1006,
  within a few tens of kilometres, not a VRS/iMAX/network one (those need a GGA uplink and
  give a synthetic station). The card is empty ("not configured") until host,
  mountpoint and user are set. One login means one connection: the app opens exactly
  one at a time and waits between attempts (below); check your network's terms before
  leaving a single-station connection up for days.
- `reference_frame`: a drop-down: the reference frame the station's coordinates are in,
  as its network documents it: `NAD83(2011)` (MaCORS and the state networks of the United
  States), `ITRF2020`, `ITRF2014` or `ITRF2008`. Unset by default, which means
  **unknown**: the card then shows the raw offset only, with no expected offset, no
  residual and no colour from a residual, and says "station frame unknown: set
  reference_frame". The app never guesses the frame (see Frames below). A name outside
  that list is refused at startup with a message naming the option.
- `base_frame`: a drop-down: the frame of our own 1005 position, `ITRF2020` by default (what a
  CSRS-PPP or OPUS ITRF coordinate is in). It goes into the local caster's sourcetable
  and the expected offset; empty means not declared (no expected offset either). A name
  outside the list stops the reference check with a message naming the option; the
  local caster still runs, announces no frame, and shows the message on its card.
- `base_epoch`: when that coordinate was observed, as the PPP date `YYYY-MM-DD` or a
  decimal year (`2026.85`). It is the epoch the sourcetable announces and the expected
  offset is computed at. Empty (the default) means today's date, which is right only
  for a coordinate kept current (see Frames below); set it when you apply a coordinate.
  Anything else (not a date, or a year outside 1990 to 2100) stops the reference check
  with a message naming the option, like `base_frame`.
- `reference_reset_utc`: the time of day (UTC, `HH:MM`) when rtkrcv is restarted so
  each day is one static solution; `00:00` by default; empty means never.
- `reference_send_gga`: false by default. True turns on the **GGA uplink** (below) for
  this caster, for a "nearest station" mountpoint that is marked `nmea` 1.
- `reference_gga_position`: the position the GGA carries. Empty (the default) means our
  base's own RTCM 1005 position; `lat,lon` in decimal degrees (for example
  `12.5,-45.25`, a made-up value) sends that position instead, to steer a
  nearest-station mountpoint to the station nearest it. Anything else stops this check
  at startup with a message naming the option.
- `reference_expect_station`: optional, unset by default (any station). A station ID,
  0 to 4095, that the station's RTCM 1005/1006 must carry. Another ID shows as **wrong
  station (got N, expected M)** on the card (amber, no residual colour) and on the
  comparison card (no colour), and is logged once per change; meant for a
  nearest-station mountpoint, to notice when it switches to another station. A value
  outside 0 to 4095 stops this check at startup with a message naming the option.
- A second check, `reference2_*`: see "Second reference (0.7.0)" below.
- The masks are the measured position's (`measured_elevation_mask`, `measured_snr_mask`,
  applied to our side; the station's observations are not masked by SNR).

**The NTRIP connection.** The app connects to the caster as soon as the check starts.
Each attempt: TCP connect (15 s); if the mountpoint has not been checked yet, or the
last attempt failed, `GET /` for the caster's **sourcetable** first (advisory: a caster that gives none, or
does not finish it in time, is asked for the stream anyway), to see what the
mountpoint's `STR` record says (format, constellations, whether it wants a GGA
position: a network or VRS stream, `solution` 1, or a format that is not RTCM 3, is
refused, since a single RTCM 3 station is what the check needs; `nmea` 1 is refused too
unless `reference_send_gga` is on, see GGA uplink below; a mountpoint missing from the
table is still asked for, because casters hide private mountpoints from it, and the
caster's answer decides). The sourcetable request carries the login when a user is set
(the same headers as the stream request; some casters, Trimble Pivot among them, list
more mountpoints to a logged-in client), and is made again without it if that gets no
table. The table is read again after a failed attempt and once a day, not on every
reconnect. After a **refused login** on the stream request the next table is asked for
without the login: with a one-connection login that the caster frees with a delay, a
logged-in `GET /` right before the stream request could keep the login busy and get it
refused again. The card's mountpoint list then keeps the last logged-in table (marked
"login (kept ...)"), and the mountpoint is looked for in both; the next table after a
stream that was not refused is asked for with the login again. Then
`GET /<mountpoint>` with the login (HTTP Basic, `Ntrip-Version: Ntrip/2.0`, HTTP/1.0, so a
version 2 caster answers plainly; chunked replies are decoded anyway). The caster's
status line decides: `ICY 200 OK` or `HTTP/1.x 200` streams; `401` (or any line saying
"Unauthorized" or "Bad Password") is **login refused**; `404`, or a sourcetable in
reply (version 1 casters), is **mountpoint not found** (the reason says whether the
table listed it); anything else is shown as it came. The stream is read until the caster closes it or no
byte arrives for 30 s (**no data**). Exactly one connection at a time per login (a CORS login
usually allows one, and a second attempt would get the first refused; a client with no
user has no login to protect, so this guard does not apply to it): attempts run one
after the other, the socket is closed before the next is scheduled, and the waits are
10 s doubling to 10 min, reset after a connection that streamed for a minute; 5 min
after a refused login or a missing or unsuitable mountpoint, so a wrong password is not
hammered against the caster; a minute after an internal error (a bug), which is logged
and shown as the reason rather than stopping the client. The password is used in the
`Authorization` header and nowhere else (see Password below).

**The card ("Reference check (<mountpoint>)")**:

- State, the client's first: *off*, *not configured*, *connecting to the caster*,
  *reading the caster's sourcetable*, *login refused by the caster* (the status line and
  "check reference_user and reference_password"; red), *mountpoint not found* (red),
  *mountpoint not usable* (red, with the STR field that says why), *no data from the
  caster* and *connection failed* (amber, with the error and "retry in ..."); then
  rtkrcv's: *idle* (no MSM from our receiver yet), *starting* (rtkrcv just started, or no
  solution yet), *no corrections* (the station's stream carries no observations, or
  stopped for 30 s), *fixed*, *float*, *single* (corrections arrive but are not used
  yet, usually the first seconds), *stale* (no solution for 30 s while corrections
  arrive), *restarting*, *not available*; *waiting for a position for the GGA* (amber)
  when the GGA uplink is on and our 1005 has not arrived yet (the client looks again
  every 10 s and does not connect before). The caster connection line shows how long it
  has been up, the rate, the bytes and the last byte's age; the sourcetable entry line
  the mountpoint's STR fields; the station line its station ID (from 1005/1006; with `*_expect_station` set, whether it is the expected one) and
  what its RTCM 1033 says (receiver and antenna); with the uplink on, the GGA line how
  many GGA were sent and what position they carry (our 1005, or the option's name,
  not the coordinate).
- **Station changed**: the station ID or position changed (a nearest-station mountpoint
  picked another station, or the station was re-coordinated): old and new ID, the
  distance between the two 1005 positions and when. rtkrcv restarts for a new session,
  the old station's 1033 descriptors are dropped, the headline says "station changed"
  for an hour, and the add-on log has one line for it.
- **Caster mountpoints (N)**, collapsible, under the card: every `STR` record of the
  caster's sourcetable (up to 500): mountpoint, identifier, format, constellations,
  `nmea` flag, solution (single or network) and whether the app would take it with the
  current `send_gga` setting; the configured one is highlighted. The caster's `CAS`
  and `NET` lines follow, as sent. Use it to find a station's own mountpoint.
- Baseline length, from the station's 1005/1006 and our 1005. Corrections age.
- The last solution: satellites, quality, AR ratio, differential age; its offset E/N/U
  from our 1005.
- Mean ± std over the last 10 minutes (of the fixed epochs, or of all epochs while
  nothing is fixed), the **session mean ± std of the fixed epochs**, and the fixed share
  of the session's epochs.
- The station's frame and where it came from ("NAD83(2011) (from reference_frame)", or
  unknown, with the stream's DF021 value when it is not zero), our frame and the epoch,
  the **expected offset** ("computed, station frame minus ours, epoch 2026.85") and the
  **residual** (session mean minus expected), with a colour:
  green within 5 cm horizontal and 10 cm up, amber within 20 cm, red beyond or after 30
  minutes without a fix (whether or not there was one before). Without a declared
  station frame there is no residual: the card says why, and only the no-fix part of
  the rule colours it. A fixed solution can still be a **false fix**
  (the spike that led to this design saw one in kinematic mode): trust a residual that
  holds for hours and across days, not one that just appeared. The previous session's
  residual (and why that session ended) stays on the card, and colours it until the new
  session has a fix of its own, so the daily reset does not look like an outage.
- rtkrcv's CPU and memory, restarts, and its last output lines when something is wrong,
  as on the measured card. RTK with ambiguity resolution costs more than single point;
  check the CPU figure on your device after a day before leaving both instances on.
  With the second reference check there are two RTK instances besides the measured
  one: check both cards' CPU figures (and the device's load) after a day.

**Sessions.** Static mode accumulates one filter for as long as rtkrcv runs, so each run
is one solution: it converges over minutes (the spike fixed at 25 km in 2 minutes and held
81 % of the hour) and improves for hours. rtkrcv is restarted at `reference_reset_utc`
(a planned restart, not counted as a failure), and when corrections resume after a gap of
more than 5 minutes (the held ambiguities are stale by then), and when the station's
own 1005/1006 position changes (another station behind the mountpoint, a
re-coordination: the solution is relative to it). A changed 1005 position on our side
(a new fixed position typed in) restarts the session statistics, not rtkrcv. If rtkrcv
exits or hangs, the same rules as for the measured position apply.

**Settings** (the exact rtkrcv config is `tests/reference_golden.conf`; both inputs are
local sockets the app serves, `inpstr1` our receiver and `inpstr2` the station's stream,
and the file holds no login): static, L1+L2,
broadcast ionosphere and Saastamoinen troposphere (`est-stec` never fixed in the
spike), fix-and-hold with RTKLIB-EX's AR filter and partial ambiguity resolution,
GLONASS ambiguities left float (the station's receiver is another make: `autocal`
blocked every fix), BeiDou resolved, ratio threshold 3, max age 30 s, no antenna model on
either side. The last point matters: our 1005 is the phase-centre position from PPP with
no antenna model, and rtkrcv with no rover model solves for the phase centre, so the two
compare directly. On the station side MaCORS streams RTCM 1033 with antenna
`GPPNULLANTENNA` (observations already reduced to a null antenna), so no ANTEX lookup is
needed; if your network's 1033 names a real antenna, its phase-centre offset (up to a
decimetre in Up) is **not** applied in this version. A non-zero 1006 antenna height is
shown on the card and not applied either: RTCM defines the 1005/1006 position as the
antenna reference point (the height is the ARP above the survey marker, for information),
while rtkrcv adds the height only when it has an antenna model (`ant2-anttype=*`); with
MaCORS it is 0, so nothing is lost. Decide with your network's documentation if yours
streams a non-zero value.

While RTK cannot run (no corrections, or corrections older than 30 s) rtkrcv emits a
single solution each epoch (`out-outsingle=on`), so the card says *no corrections* or
*single* with live numbers, instead of the runner mistaking the silence for a hang.
"Corrections" means the station's observations (MSM): a stream that still sends 1005
and 1033 but no observations counts as none.

**Frames: declared, computed, announced; never guessed.** The solution is in the
station's frame, because rtkrcv takes the base coordinate from its 1005/1006. Our 1005
is in ours. The offset between the two frames at our antenna is what the raw offset
should show, and the app computes it (`gnss_base/datums.py`) from three declared
things: the station's frame (`reference_frame`), our frame (`base_frame`) and the epoch
(`base_epoch`, the coordinate's observation date, or today's date as a decimal year
when it is not set, since the app cannot know when the coordinate was observed). The
epoch matters: an ITRF2020 coordinate moves with the plate, about 2 cm a year in the
eastern United States, and the computed offset moves with the epoch by the same amount,
so the two cancel only at the coordinate's own epoch. Set `base_epoch` when you apply a
coordinate; without it, keep the coordinate current (a yearly PPP, as planned), since a
coordinate a year old then makes the expected offset about 2 cm wrong, which the
residual shows.
The maths: ITRF2020 to ITRF2008 with the IERS parameters (epoch 2015.0), then ITRF2008 to
NAD83(2011) with the NGS 14 parameters of Pearson & Snay 2013 as HTDP uses them (epoch
1997.00); ITRF2014 likewise through IERS parameters; ITRF2020 to ITRF2020 is the
identity. NAD83(2011) coordinates are quoted at epoch 2010.00, and the point's own
NAD83 velocity (millimetres a year in the stable interior of the continent) is not
modelled: expect a few centimetres of that in the residual over the years since 2010,
which HTDP's velocity model would remove. The **sign convention** everywhere is the
station's frame minus ours, the same as the measured offset (solution minus our 1005):
in New England, NAD83(2011) minus ITRF2020 is about east +0.56, north -1.19, up +1.18 m.

Why declared and not read from the stream: RTCM 1005/1006 do have a field for it, DF021
"ITRF realization year" (6 bits, after the station id), but RTCM 10403 says of it "Since
this field is reserved, all bits should be set to zero for now. However, since the value
is subject to change in future versions, decoding should not rely on a zero value", and
no edition defines how a year would be encoded in six bits. Nearly every caster sends 0,
RTKLIB's encoder writes 0, and the LG290P sends 0 with no command to change it (Quectel
protocol specification v1.3: `PQTMCFGRTCM` sets the MSM type and ephemeris output,
`PQTMCFGRSID` the station id; nothing sets DF021). So the app leaves DF021 as the
receiver sends it, and shows a station's non-zero DF021 on the card as a number, not as
a frame. The RTCM messages for datum transformations (1021 to 1027) are not sent by
any network the author has seen either. What is announced instead: the local caster's
sourcetable `STR` record carries `ITRF2020 epoch 2026.85` in its misc field (with
`base_epoch` set; without it `ITRF2020 epoch 2026.85 (today, base_epoch not set)`, so a
reader does not take today's date for the observation epoch).
RTK2go has no field for it in the stream; its sourcetable entry is set on its
registration page, so put the frame and epoch in the mountpoint's description (the
"misc" or "identifier" text) when registering or updating the reservation.

The log header and the card record the station's 1005 ECEF, our 1005 ECEF, both frames,
the epoch and the computed expected offset, so a later reader knows what each number is
in. A residual of a few centimetres confirms three things at once: our coordinate, the
station's, and the transformation; if it is not small the check cannot say which is off.

**The station's stream on disk.** Everything the app's NTRIP client receives from the
station is written as received to `<log_dir>/reference/raw/YYYY-MM-DD.rtcm3` (RTCM3 without any
header, about 1 kB/s), with the receiver raw log's rotation at midnight UTC, compression
of finished days and free-space floor; the files are on the Log files table as
`reference/raw/...`, and the card has a "CORS raw log" line. Nothing is written while
the check is off or not configured. The bytes are the NTRIP client's, so a gap in the
file is a gap in the connection (shown on the card), not an rtkrcv restart.

**Password.** It goes into the `Authorization` header of the stream request and
nowhere else: not rtkrcv's config (which holds only local ports and masks), the add-on
log, `api/status`, the page, the command log or the solution log. The caster's replies
and any error text are scrubbed of it before they are shown. Characters that cannot go
into a request (spaces, non-ASCII, a `:` in the user name) are refused with a message
that names the option, not the value.

NTRIP is HTTP over plain TCP: the login goes to the caster as HTTP Basic in clear, like
every NTRIP client, and the corrections come back unsigned. Use a login made for this
purpose, and treat the check as a comparison with a network you trust, not as a defence
against someone tampering with that network's stream.

Solutions are logged to `<log_dir>/reference/YYYY-MM-DD.llh` (see Logs).

### Second reference (0.7.0)

A second check runs beside the first, against another station, the same way: its own
NTRIP client, its own rtkrcv (RTK static, the same settings), its own card ("Reference
check 2", shown only while it is on), its own session, daily reset and gap restart,
work folder `<data_dir>/reference2` and logs `<log_dir>/reference2/` (same names as
the first's). Its options are flat and spelt like the first's:

- `reference2_enabled` (false), `reference2_host`, `reference2_port` (2101),
  `reference2_mountpoint`, `reference2_user`, `reference2_password` (a password field),
  `reference2_frame` (the same drop-down; unset means unknown), `reference2_reset_utc`
  (`00:00`), `reference2_send_gga` (false), `reference2_gga_position` (empty: our 1005),
  `reference2_expect_station` (unset: any station).
- The masks, `base_frame` and `base_epoch` are shared with the first check.
- Every message names the option it is about (`reference2_frame`, ...). A bad value
  stops that check only.
- The two checks may not share a caster and user: a CORS login usually allows one
  connection, so the second would be refused while the first streams. With the same
  host, port and user on both, the second says so and does not start. Two logins on
  one caster, or two casters, run side by side (the one-connection rule is per login).

The first check's options, card, logs and log lines are as before, so a 0.6
configuration runs unchanged with the second check off.

### GGA uplink (0.7.0)

Some casters choose the station by the client's position: Trimble Pivot's
`SingleStation_Nearest_RTCM_MSM`, for example, is marked `nmea` 1 in the sourcetable and
streams the single station nearest the GGA it receives. With `reference_send_gga` (or
`reference2_send_gga`) on, the app sends an NMEA GGA sentence with the stream request
twice over: as the NTRIP 2 header `Ntrip-GGA: $GPGGA,...*hh` (the whole sentence, for
a caster that reads the request as strict HTTP and picks the station when the request
arrives), and as raw bytes right after the request (NTRIP 1 style, as casters expect
for `nmea` 1 mountpoints); then raw every 10 s while it is connected: `$GPGGA`, quality 1 (the usual
value for a nearest-station or VRS choice), the UTC time from the system clock, a
nominal 12 satellites and HDOP 1.0, and a correct checksum. The `nmea` 1 refusal is
lifted for that check only; a network solution (`solution` 1) and a format that is not
RTCM 3 are still refused, so a VRS stream is not taken for a station. Which station
the caster picked is what its 1005/1006 (station ID, position, baseline) and 1033 say
on the card; a switch is a "station changed" (above).

The position: `*_gga_position` empty means our base's own RTCM 1005 position (the
receiver's current one; the last one the check saw when none is current), with its ellipsoidal height in the
altitude field and a geoid separation of 0; until it has arrived the client waits and
does not connect. `lat,lon` sends that position instead, height 0, to make the caster
pick the station nearest it: take the station's published position (its CORS datasheet
or its network's station list), to a few hundredths of a degree.

**Privacy.** The GGA goes only to that check's caster, on its own connection, and
carries only our base's position or the configured one; with an empty
`*_gga_position` that is the base's position to a few centimetres, so set one if you do
not want the caster to have it. The GGA, and the request with its `Ntrip-GGA` header,
are never written to the add-on log, the page or `api/status`; a GGA sentence in a
caster's reply is replaced by `GGA ***` before the reply is shown or logged (the log says "GGA uplink on" and counts the GGA sent when a connection
ends; the card shows the count and the source, not the coordinate). Like the login, it
travels in clear over plain TCP.

### Reference comparison (0.7.0)

When both checks have a session mean of fixed epochs, from the same 1005 of ours, a
"Reference comparison" card shows, in centimetres east, north and up at our antenna:
reference 1 minus ours and reference 2 minus ours (each with its station's frame), and
reference 2 minus reference 1. The last does not depend on our coordinate at all: it
compares the two stations (their coordinates, frames and the two RTK solutions). It is
judged only when both stations' frames are declared and the same: green within 3 cm
horizontal and 6 cm up, amber within 10 cm, red beyond. Otherwise the card shows the
raw difference and why there is no residual (a frame unknown, or two different frames,
whose transformation the difference then contains); the app does not guess a frame or
transform between the two stations' frames here. Each side also shows its station ID
(the one its session is relative to) and how long ago its last fix was.

It is judged only while it is current: both checks' NTRIP clients streaming, each
check's rtkrcv running with no restart pending, and each check with a fixed epoch in
the last 30 min. Otherwise the headline is **Stale** with why (for example "reference2
is not streaming (login refused)", "reference2's rtkrcv is restarting (a new
session)", as for the few seconds after a "nearest station" mountpoint switched, when
the session mean is still the old station's, or "reference has no fix in the last 30
min"), the numbers stay with each side's last-fix age, and there is
no colour, so a session mean kept after a check dropped does not read as a current
"Stations agree". With `*_expect_station` set and the other station received on either
side, the headline is **Wrong station** ("reference2: wrong station (got N, expected
M)"), also without colour.

## Changes

**0.7.0**

- A second reference check (`reference2_*` options, "Reference check 2" card,
  `<log_dir>/reference2/` logs, `<data_dir>/reference2` work folder), the same code as
  the first (`ReferenceCheck` once per reference). The first check is unchanged. Two
  checks with the same caster and user are refused (one connection per login); the
  NTRIP client's one-connection guard is now per login, and does not apply to a
  client with no user (no login, so no per-login limit).
- GGA uplink for "nearest station" (`nmea` 1) mountpoints: `reference_send_gga`,
  `reference_gga_position` and their `reference2_` twins, off by default. The GGA goes
  in the NTRIP 2 `Ntrip-GGA` request header and as raw bytes after the request, then
  every 10 s; its position is our current 1005 (or the last one seen), or the option's.
  The GGA itself is never logged, and one echoed in a caster's reply is scrubbed.
- `reference_expect_station` and `reference2_expect_station` (optional, 0 to 4095):
  another station ID shows as "wrong station (got N, expected M)" on the check's card
  and the comparison, without colour, logged once per change.
- Station identity on the card: the station ID from 1005/1006 beside the 1033
  descriptors. A changed station ID now restarts rtkrcv too (a changed position did
  already); either is shown as "station changed" and logged.
- Each card lists the caster's mountpoints (collapsible, with whether each would be
  accepted); the sourcetable is now asked for with the login, and again without it if
  that fails, and read again after a failed attempt or a day. After a refused login on
  the stream request it is asked for without the login, keeping the logged-in list.
- "Reference comparison" card: both references minus ours and reference 2 minus
  reference 1, with both station IDs and last-fix ages, judged only in the same
  declared frame (green 3 cm horizontal / 6 cm up, amber 10 cm) and only while both
  checks stream, neither check's rtkrcv is restarting, and each has a fix in the last
  30 min (otherwise "Stale", no colour).
- Check the CPU after a day with both reference checks on.

**0.6.3**

- The two frame options are drop-downs (ITRF2020, ITRF2014, ITRF2008, NAD83(2011));
  `reference_frame` is unset until chosen. No other change.

**0.6.2**

- The app is the NTRIP client for the reference check (`gnss_base/ntripclient.py`):
  sourcetable check, login, the caster's status line on the card ("Login refused by the
  caster (HTTP/1.1 401 Unauthorized)", "Mountpoint not found", "No data from the
  caster", with the retry time), one connection at a time with backoff. rtkrcv reads the
  station's stream from a local socket (`inpstr2-type=tcpcli`); its config holds no
  login, the 0600 config file, the console scrubbing and rtkrcv's log-stream tap are
  gone. 0.6.1 sat in "connecting" with nothing to say when a login was refused.
- Reference frames are declared, computed and announced, never guessed. `reference_frame`
  (empty: unknown) replaces `reference_expected_enu`; the expected offset is computed
  from it, `base_frame` (default `ITRF2020`) and the epoch (`base_epoch`, else today's) with the IERS and NGS
  transformation parameters (`gnss_base/datums.py`, pure Python). Without a declared
  station frame the card shows the raw offset only, no residual and no residual colour.
  The card and the log header say the frames, their sources, the epoch and the computed
  offset. The local caster's sourcetable carries `ITRF2020 epoch YYYY.YY` in its misc
  field. DF021 in 1005/1006 is left as the receiver sends it (0): RTCM reserves the field
  and defines no encoding; a station's non-zero value is shown, not interpreted.
- The CORS stream is logged as received: `<log_dir>/reference/raw/YYYY-MM-DD.rtcm3`, with
  the raw log's rotation, compression and disk floor; listed on the Log files table.
- The page and its scripts are served with `Cache-Control: no-store`, and the script tags
  carry the version (`track.js?v=0.6.2`), so an update is never shown through a cached
  page.
- The page uses the width of a wide screen (cards from 320 px, up to 1700 px in all),
  values wrap instead of being clipped, the Reference check card spans the page, and
  its longest labels are shorter.
- Fixed: a packaging test still expected 0.6.0 and `build.yaml`.

**0.6.1**

- Build fix: the Dockerfile names its Ubuntu base directly. 0.6.0's `build.yaml` was
  rejected by the supervisor (image name without a registry path), which then passed its
  Alpine base, where `apt-get` does not exist. No functional change.

**0.6.0**

- Reference check: a second rtkrcv in RTK static mode against a CORS station (options
  `reference_*`), with its card, its daily log `reference/YYYY-MM-DD.llh`, a daily reset
  and the residual colour rule above.
- The image builds RTKLIB-EX 2.5.1 (rtklibexplorer's RTKLIB, tag `v2.5.1`, commit
  pinned) from source and runs both rtkrcv instances on it as `rtkrcv-ex`; Ubuntu's
  `rtklib` package is no longer installed. `--rtkrcv` defaults to `rtkrcv-ex`.
- RTCM 1033 (receiver and antenna descriptors) is decoded.
**0.5.2**

- A **Base station** card leads the page: one word for the whole chain, Serving,
  Degraded or Down (`baseSummary` in `static/track.js`, tested under Node), with the
  reasons, then a line each for the receiver (connected, bytes/s, app uptime, firmware,
  the mode from `PQTMCFGRCVRMODE` and `PQTMCFGSVIN`), the corrections (1005 age and whether its position
  matches the receiver's configured one, or the first seen on this page), the
  observations (satellites per system in MSM and their age), decode errors, the local
  caster, the upload and storage. The Position fix card moved below Measured position.
  Page only: no change to `api/status`.

**0.5.1**

- Step 5 (fixed position) shows, in the confirmation, the base position the receiver now
  sends in RTCM 1005 and the shift to the new position: east, north, up, horizontal and
  3D (`details.current_base`, `details.shift`, `details.shift_unknown`). A warning states
  the shift in words; over 1 m it says to check every digit. Without a fresh 1005 the
  shift is unknown, the warning says so, and the step still works.
- The profile check now ends with `PQTMCFGSVIN,R` and `api/status` has `receiver.svin`:
  the survey-in mode (0 disabled, 1 survey-in, 2 fixed, or unknown with the reason) and
  the configured position when fixed. The Survey-in card and step 5 say when the
  receiver is in fixed mode, because PQTMSVINSTATUS then reports the configured
  position as the mean (Valid=2), not a measurement. Step 5 refuses "from the survey-in
  mean" in fixed mode (it silently re-sent the current position before), relabels that
  option "current fixed position", and warns instead when the mode could not be read.

**0.5.0**

- Live measured position from `rtkrcv` (see Measured position): new options
  `measured_enabled`, `measured_elevation_mask`, `measured_snr_mask`, command-line
  `--no-measured`, `--measured-elevation-mask`, `--measured-snr-mask` and `--rtkrcv`.
  The image now installs Ubuntu's `rtklib` package.
- The position history is a class shared by the GGA and the measured track;
  `api/track` says which `source` and `reference_kind` it used, and takes `?source=`.
- Daily solution log under `<log_dir>/measured/`, compressed like the raw log.

**0.4.1**

- The first 5 lines a rover sends are logged to `caster.jsonl`; coordinates are no
  longer blanked. A line that starts with `$` is logged as sent, positions and all
  (escaped, cut to 200 characters, rate-limited), with credentials in the whole line
  (`Authorization` and `Proxy-Authorization`, `SOURCE`, `user:password@` in a URL,
  password-like query values) replaced by `-`. Any other line is logged only as its
  first word and length. At most one sample is a GGA (the first), and the disconnect
  line carries the last GGA line (`last_gga_line`, `last_gga_shortened`), so the log
  shows where the rover ended up. Disconnect lines have their own per-address budget. A
  cut sample never ends inside an escape. Keep the log on the device; never copy it into
  a public repository.
- `caster_max_clients` defaults to 16 (was 4); the app now accepts 1 to 16, as the
  options schema does (it accepted up to 64). An existing install keeps its saved value:
  set `caster_max_clients` to 16 in the app's options to get the new default.
- PQTMANTENNASTATUS is no longer in the profile: only the LG680P supports it, and an
  LG290P answers ERROR 1, which showed as a permanent mismatch.

**0.4.0**

- New local NTRIP caster (v1) on port 2101, login optional and off by default, with a
  card on the page listing its clients.
- New upload to an NTRIP caster such as RTK2go (rev1 SOURCE), off until a mountpoint and
  password are set, with backoff and rate guards that follow RTK2go's rules, and a card
  on the page.
- Both send RTCM only, with 1005/1006/1033 thinned to every 10 s.
- New connection log `caster.jsonl`, rate-limited per address, rotated at 10 MB
  (3 kept), not written below the free space floor, and written off the serial path.
- The caster counts what each client sends (GGA, other NMEA by type, anything else),
  logs the first 5 non-GGA lines per connection with coordinates blanked, and sums it up
  on disconnect. The client table has a **Sent to us** column (amber for anything but
  GGA), and rovers that send GGA with a fix show on the map as diamonds.
- The caster allows 4 unfinished requests per address (20 in all), forgets failed
  logins after the minute, and no longer logs the user name typed in a failed login.
- The upload stops after 3 refusals or 2 ban-like replies without an accepted
  connection in between, until the options
  change or the app restarts, keeps its waits and counts across restarts in
  `/data/upload-state.json`, connects only if RTCM arrived in the last 3 s, closes if
  nothing was sent 5 s after the OK, disconnects after 8 s without RTCM, recognises
  ban replies more precisely and accepts a bare `OK`.
- Every logged rover line, NMEA or not, has coordinate-like numbers blanked (NMEA
  latitude and longitude, decimal degrees, ECEF metres); an IPv4 address seen as
  `::ffff:a.b.c.d` counts as that IPv4 address for the per-address limits, lockout and
  log.
- The upload state is saved every 5 min or 10 MB while connected; a restart after the
  day's 400 MB waits for midnight UTC; a state file with `NaN`, infinities or anything
  else unexpected is ignored with a warning (the first attempt still waits 60 s); a
  clock behind the last attempt keeps only the 60 s gap.
- On shutdown, the connection log waits at most 5 s for the disk, and its writer task
  ends even if the app fails to start.
- New option check: `upload_mountpoint` may hold only letters, digits, `_`, `.` and `-`.
- The profile sets `PQTMCFGRTCM` EPH_Mode 2 with EPH_Interval 60: ephemerides also every
  60 s, about 40 B/s more, so a rover has all orbits within a minute. A receiver set up
  with 0.3 shows this row as **differ**; apply it, save and restart, then check that
  1019/1020/1042/1046 now arrive about once a minute per satellite in the RTCM table.
- New options, see below.


**0.3.2**

- The base position from the latest RTCM 1005/1006 is shown in the fix panel: latitude,
  longitude, ellipsoidal height, ECEF, station ID and age, marked stale after 10 s
  without one. It moved there from under the map.
- The map has a legend for the GGA fix and the base position ring; the ring is dashed
  and grey when stale. With no measured fix, **Centre map** centres on the base.
- GGA quality 7 (manual input: the fixed base position repeated) is labelled "Fixed
  position, not measured" and is no longer drawn as a fix or kept for the drift plot;
  nor are quality 0 and 8 (simulator). Qualities 1 to 6 are kept as before. GGA quality
  names now include 3 (PPS), 7 and 8.
- When there is no measured position, the drift plot says why instead of staying blank.
  `api/track` has a new `no_measured_reason` field.
- After a switch from rover to base mode, the map centres on the base position once
  (and again if it moves more than 5 m), instead of staying on the last rover fix.
- Base mode is recognised from a recent 1005/1006 only. A rover sending MSM with GGA
  quality 0 is reported as having no usable fix, not as being in base mode.
- A GGA of quality 1 to 6 counts as measured only if its position was accepted: a
  malformed coordinate or rejected height makes it unmeasured everywhere.
- The map note gives the app's reason for having no measured position, instead of
  saying that a receiver in base mode sends no NMEA while NMEA is arriving.
  `api/status` has `no_measured_reason` too.
- The base position goes stale after 10 s, or 3 times the recent interval between
  1005/1006 messages if longer, at most 120 s (`base_position.stale_after_s`).

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
   is the same folder. Keep the LF line endings. The image build clones RTKLIB-EX from
   GitHub (the Dockerfile fixes the Ubuntu base itself, because the supervisor would pass
   its Alpine base; it checks the tag's commit) and
   compiles `rtkrcv`, which takes a few minutes on the device and needs internet access.
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
| caster_enabled | true | Run the local NTRIP caster |
| caster_mountpoint | BASE | Its mountpoint: 1 to 32 letters, digits or `_` |
| caster_user | empty | With a password: the user name it requires (empty: any user) |
| caster_password | empty | Empty: no login needed. Set: rovers must log in |
| caster_max_clients | 16 | Rovers at once (1 to 16) |
| upload_host | rtk2go.com | NTRIP caster to upload to |
| upload_port | 2101 | Its port |
| upload_mountpoint | empty | Your reserved mountpoint. Empty: no upload |
| upload_password | empty | The mountpoint's password. Empty: no upload |
| upload_style | rev1 | NTRIP style of the reservation. Only rev1 works so far |
| measured_enabled | true | Run rtkrcv for the live measured position |
| measured_elevation_mask | 15 | rtkrcv's elevation mask, degrees (0 to 45) |
| measured_snr_mask | 35 | rtkrcv's SNR mask, dB-Hz (0 to 50; 0 = no mask); both instances |
| reference_enabled | false | Run the reference check (RTK against a CORS station) |
| reference_host | empty | The CORS network's NTRIP caster |
| reference_port | 2101 | Its port |
| reference_mountpoint | empty | A single-station RTCM 3 mountpoint (MSM + 1005/1006) |
| reference_user | empty | Your login |
| reference_password | empty | Its password (used in the request's Authorization header only; not in rtkrcv's config) |
| reference_frame | empty | The station's frame: NAD83(2011), ITRF2020, ITRF2014 or ITRF2008. Empty: unknown, no expected offset or residual |
| reference_reset_utc | 00:00 | Daily restart of the reference rtkrcv (UTC, HH:MM); empty: never |
| reference_send_gga | false | Send a GGA to the caster (for an `nmea` 1 nearest-station mountpoint), in the `Ntrip-GGA` header and right after the request, then every 10 s |
| reference_gga_position | empty | The GGA's position, `lat,lon` in decimal degrees. Empty: our base's 1005 position |
| reference_expect_station | unset | The station ID (0 to 4095) the stream's 1005/1006 must carry; another shows as "wrong station". Unset: any |
| reference2_enabled | false | Run the second reference check |
| reference2_host, reference2_port, reference2_mountpoint, reference2_user, reference2_password | empty, 2101, empty, empty, empty | The second check's caster and login, as for the first |
| reference2_frame | empty | The second station's frame (same list). Empty: unknown |
| reference2_reset_utc | 00:00 | The second rtkrcv's daily restart |
| reference2_send_gga, reference2_gga_position | false, empty | The second check's GGA uplink |
| reference2_expect_station | unset | The second station's expected ID, as for the first |
| base_frame | ITRF2020 | The frame of our own base position: in the sourcetable and the expected offset. Empty: not declared |
| base_epoch | empty | When the base position was observed (the PPP date), `YYYY-MM-DD` or a decimal year: the epoch announced and used for the expected offset. Empty: today's date |

Command-line arguments override options (`--device`, `--baud`, `--caster-mountpoint`,
`--upload-host`, ...; `--no-caster`, and `--caster-host`/`--caster-port` for where the
caster listens). Passwords can be set only in the options. `--data-dir` sets where the
upload keeps `upload-state.json` (default: the options file's folder, `/data` in Home
Assistant) and rtkrcv its working folder `rtkrcv/`. `--no-measured`,
`--measured-elevation-mask` and `--measured-snr-mask` override the measured options;
`--reference`/`--no-reference`, `--reference-host`, `--reference-port`,
`--reference-mountpoint`, `--reference-user`, `--reference-frame`, `--base-frame`,
`--base-epoch` and `--reference-reset-utc` the reference ones (the password is an option only);
`--reference-send-gga`/`--no-reference-send-gga` and `--reference-gga-position` the GGA
uplink; the second check's are spelt the same with `reference2` (`--reference2`,
`--no-reference2`, `--reference2-host`, ..., `--reference2-gga-position`); `--rtkrcv
PATH` runs another rtkrcv build for both instances (default: `rtkrcv-ex` on the path, the
RTKLIB-EX build in the image).

## Logs

`<log_dir>/raw/YYYY-MM-DD.raw` holds that UTC day's bytes exactly as received. Finished days
become `.raw.gz`. If the system clock steps back into a day while that day is being
compressed, the bytes go to `YYYY-MM-DD-late-<n>.raw` instead, compressed in turn once
the day is over; read it after the day's own file. A name already taken by a `.gz` gets a
`-2`, `-3`… suffix, so nothing is overwritten. Logs contain your position, so keep them out of public repositories.
Home Assistant backups that include `/share` will include them too.

`<log_dir>/measured/YYYY-MM-DD.pos` (UTC day of arrival) has one `%` header line (app and
rtkrcv versions, masks, "time GPST") and then rtkrcv's solution lines as it sent them:
GPST date and time, latitude, longitude, ellipsoidal height, quality (5 = single),
satellites, the sigmas, age and ratio. A new `%` line starts each block written with
other settings or another rtkrcv version. About 13 MB a day, 3 to 4 MB compressed; finished
days become `.pos.gz`. They hold positions too, so keep them private. Nothing is written
below `min_free_gb`. They can be made again by replaying the raw log through rtkrcv.

`<log_dir>/reference/YYYY-MM-DD.llh` is the reference check's equivalent, same writer,
same compression and disk floor. Its `%` header names the mountpoint, our 1005 ECEF, the
station's 1005 ECEF (and 1006 antenna height), both frames and where they came from, the
epoch, the computed expected offset and its sign convention, and the masks; a new header
line is written when any of them changes (a new session's rtkrcv version, a new 1005,
the epoch's second decimal). The lines are rtkrcv's: quality 1 is fixed,
2 float, 5 single; the last two fields are the age of the corrections and the AR ratio.
It holds absolute positions in the station's frame: private, like the others.
The second check writes the same files under `<log_dir>/reference2/` (and
`reference2/raw/`), with `reference2_frame` named in its header.

`<log_dir>/reference/raw/YYYY-MM-DD.rtcm3` is the station's stream as the app's NTRIP
client received it
(header-less RTCM3, about 1 kB/s, 100 to 170 MB a day before compression), written with
the receiver raw log's rules: UTC days, `.rtcm3.gz` when finished, `-late-<n>` files,
nothing below `min_free_gb`. Only while the reference check is enabled and configured,
and only while the app's NTRIP client is connected: a gap in the file is a gap in the
connection, shown on the card; rtkrcv's restarts do not touch it. It holds the
station's position (its 1005), which is public, and nothing of ours.

## Tests

From this folder, with `aiohttp` and `pyserial` installed (Python 3.11 or later):

    python -m unittest tests.test_app tests.test_outputs tests.test_measured tests.test_reference tests.test_ntripclient tests.test_datums

`tests.test_outputs` covers the hub, the filter, the caster and the upload, against a fake
caster on localhost; nothing connects outside the machine. If RTKLIB's `str2str` is on
the path (or named by the `STR2STR` environment variable), it is used as an independent
NTRIP client of the caster. The page's track merging is tested under Node (`tests/track_test.js`), which the suite
runs if `node` is installed and skips otherwise. Tests use synthetic positions only, and
a simulated receiver on a pseudo-terminal.

`tests.test_measured` covers the rtkrcv runner against a fake rtkrcv
(`tests/fake_rtkrcv.py`), and the whole app with it. If a real `rtkrcv-ex` or `rtkrcv`
is on the path (or named by the `RTKRCV` environment variable), a smoke test starts it
with no data: it must connect to both local servers and exit cleanly on SIGTERM. No real
RTCM is used.

`tests.test_datums` covers the frame transformations: it reproduces, to the millimetre,
the numbers of the earlier private numpy script at synthetic points, checks the sign of
the New England offset, the identity for ITRF2020, the rotation sign of each convention
and the inverse.

`tests.test_ntripclient` covers the app's NTRIP client against a fake caster on
localhost (`tests/fake_caster.py`): `ICY`, HTTP and chunked streams, 401, 404 and a
sourcetable in reply, the sourcetable check and unsuitable mountpoints, no data, one
connection at a time, the backoff schedule, and that the password appears only in the
`Authorization` header; and (0.7.0) the GGA sentence (format, checksum, time), the
acceptance rules with and without the uplink, a sourcetable of several records with
the "usable here" column, the logged-in sourcetable and its fallback and refresh, the
uplink on the wire (the `Ntrip-GGA` header and the raw sentence), "no position", two
logins at once, two clients with no user at once, the anonymous sourcetable after a refused login, and that neither the
GGA (sent or echoed by a caster) nor the password reaches the log.

`tests.test_reference` covers the reference check: the config's option spellings
(`tests/reference_golden.conf`, with no credential in it), the option checks, the
states, the session statistics and the residual rule, the daily reset, planned
restarts, the station decoder, the raw log, and the whole app with both instances and
the fake caster; and (0.7.0) the `reference2_*`, `*_gga_position` and
`*_expect_station` option checks and the login clash, station changes, a wrong station,
the GGA position from the current 1005, the comparison's numbers, colours, station IDs
and stale rule (also while a check's rtkrcv restarts after a station switch), two checks
running side by side (one refused, one streaming with a GGA), and the whole app with
both. The smoke test with a real binary points the client at a closed local
port: no CORS is ever contacted by the tests.
