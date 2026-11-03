# GNSS Base Station app (Home Assistant OS)

Runs a GNSS base receiver from a Home Assistant box. Home Assistant calls add-ons
"apps" from 2026.2 on.

**Stage 1 only listens.** The app never writes to the serial port, so it cannot change
the receiver. It does not serve corrections yet.

- Reads the receiver and splits its stream into checked RTCM3 and NMEA messages
- Saves every byte to a daily file, and compresses finished days
- Records storage growth hourly in `storage-usage.csv`
- Shows status on a page in the Home Assistant sidebar, behind your Home Assistant login

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

From this folder, with `aiohttp` and `pyserial` installed:

    python -m unittest tests.test_app

The page's track merging is tested under Node (`tests/track_test.js`), which the suite
runs if `node` is installed and skips otherwise.
