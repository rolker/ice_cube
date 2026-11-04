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

## Status page

Besides the receiver, data, storage, signal and message status, the page shows:

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
   then, and if PQTMSVINSTATUS is more than 5 s old, the survey-in mean is refused. The
   confirmation shows ECEF and latitude/longitude/height. It sends PQTMCFGSVIN,W,2,
   PQTMSAVEPAR and PQTMSRR, restarting for the same reason as step 4. Keep the
   coordinates out of public repositories.
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
(Ubuntu's `rtklib` package, 2.4.3 b34) on a copy of the receiver's RTCM and shows its
solution on the map and the drift plot, as offsets from the RTCM 1005 base position.

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
- Its console is a pseudo-terminal the app keeps (rtkrcv needs one); there is no telnet
  console.
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
done in under 9 s. Everything stops at once, each part bounded by the time left: rtkrcv
(SIGTERM, SIGKILL after 3 s) and then the solution log, the outputs and then the
connection log, the receiver and the raw log, the status page (1.5 s for open requests).
File work still running at the deadline is abandoned, not waited for. A day being
compressed then keeps its original file and is compressed again after the next start;
the half-written `.gz.partial` is removed then. A log line being written at that moment
may be cut short.

## Changes

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
| measured_snr_mask | 35 | rtkrcv's SNR mask, dB-Hz (0 to 50; 0 = no mask) |

Command-line arguments override options (`--device`, `--baud`, `--caster-mountpoint`,
`--upload-host`, ...; `--no-caster`, and `--caster-host`/`--caster-port` for where the
caster listens). Passwords can be set only in the options. `--data-dir` sets where the
upload keeps `upload-state.json` (default: the options file's folder, `/data` in Home
Assistant) and rtkrcv its working folder `rtkrcv/`. `--no-measured`,
`--measured-elevation-mask` and `--measured-snr-mask` override the measured options;
`--rtkrcv PATH` runs another rtkrcv build (default: `rtkrcv` on the path).

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

## Tests

From this folder, with `aiohttp` and `pyserial` installed (Python 3.11 or later):

    python -m unittest tests.test_app tests.test_outputs tests.test_measured

`tests.test_outputs` covers the hub, the filter, the caster and the upload, against a fake
caster on localhost; nothing connects outside the machine. If RTKLIB's `str2str` is on
the path (or named by the `STR2STR` environment variable), it is used as an independent
NTRIP client of the caster. The page's track merging is tested under Node (`tests/track_test.js`), which the suite
runs if `node` is installed and skips otherwise. Tests use synthetic positions only, and
a simulated receiver on a pseudo-terminal.

`tests.test_measured` covers the rtkrcv runner against a fake rtkrcv
(`tests/fake_rtkrcv.py`), and the whole app with it. If a real `rtkrcv` is on the path
(or named by the `RTKRCV` environment variable), a smoke test starts it with no data: it
must connect to both local servers and exit cleanly on SIGTERM. No real RTCM is used.
