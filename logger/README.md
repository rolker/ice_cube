# Ice Cube Logger 0.5

Records the RTK Express GPS over Bluetooth or USB, runs its own NTRIP client, and logs
phone accelerometer, gravity and gyroscope data, all on one phone clock. The main screen
is a local map with your own GeoJSON layers (no basemap, no network) where you capture
surface, object and reference points with large glove-friendly buttons or the volume
keys, and take photos. Reference points are averaged and checked against earlier visits
(the pole check). It checks the reference frame and base position, and shares a session
as a zip. It works in portrait or landscape, and the screen can be locked to how the
phone sits on the pole.

Captures are deliberately unlabelled in the field: no pick-lists while surveying. Each
one is a row in `points.csv` with an empty `note` column for a laptop tool to fill in
later (see points.csv below).

## Build
`./gradlew assembleDebug test lint` (needs the Android SDK; set `sdk.dir` in
`local.properties`). The APK is `app/build/outputs/apk/debug/app-debug.apk`. Or open this
folder in Android Studio and press Run.

## Settings
MENU > Settings. Caster and GPS link changes apply at the next Connect; the rest at once.
- Display: Screen orientation: Auto (follow the system, including its auto-rotate
  toggle), Portrait, Landscape or Reverse landscape. Default Auto. Every screen of the app
  uses it, whatever the system auto-rotate toggle says, so a phone mounted on a tilting
  pole does not flip. It applies at once. See Screen orientation below.
- Caster: host, port, mount point, user, password (shown as dots), Log corrections,
  Connect caster at app launch, Frame (Unknown, ITRF2020, ITRF2014, ITRF2008,
  NAD83(2011), WGS84) and an optional Frame epoch (decimal year). See Frames below.
- GPS link: USB or Bluetooth. For Bluetooth, pick the receiver from the paired devices;
  pair it in Android Bluetooth settings first. Configure receiver (RAM only) is on by
  default for USB and off for Bluetooth, because the RTK Express firmware already sets up
  the Bluetooth port. Start logging when the GPS connects starts a session on its own: for
  USB when Android opens the app on attach, for Bluetooth when the receiver connects after
  the app is opened.
- Survey: Pole height, from ground mark to the antenna reference point: 1.8, 1.7, 1.6, 1.5,
  1.4 m or Other… for a typed value (0 to 5 m, up to 3 decimals). An invalid value is not
  saved and the screen says why. It applies at once (also settable from the map's strip).
  - Ref duration: seconds of RTK fixed epochs averaged for a Ref capture, 10 to 300,
    default 30.
  - Pole check tolerance: cm, 0.5 to 20, default 3. See Refs and the pole check.
  - Volume keys: Mark / Mode (default on): on the map, volume down = MARK, volume up =
    MODE. The keys then do not change the volume while the map is in front.
  - Sound and Vibration (default on): the cues listed under Feedback.
  - Show earlier refs (default off): refs from `refs.csv` of other sessions as grey rings.
  - Number captures on the map (default on): labels S12, O13, R14 next to the points.
- Map: the layer files found, each with a checkbox (remembered by file name), a summary
  line (features, vertices, skipped features) or the error for a bad file, Import layer
  files… and Reload layers. See Map layers below.

## Use
1. Close SW Maps first: only one app can hold the receiver's Bluetooth or USB link.
2. For USB, connect to the GPS USB port with an OTG cable, not the ESP32 port, and allow
   USB access.
3. Tap the GPS and NTRIP dots on the map's strip (or MENU > Connect GPS / Connect caster).
   Either one starts a session; so does tapping REC. The caster works without the GPS;
   corrections go to the GPS whenever both are connected, and the GGA from the GPS goes
   to the caster.
4. Pick the mode with MODE, capture with MARK, take photos with PHOTO (see Main screen).
5. Tap REC (it asks first) or MENU > Stop session when finished. If a link drops, the app
   logs it, shows "link lost" in the red bar and reconnects (Bluetooth every 3 to 30 s,
   USB on re-attach, caster every 5 to 60 s).

## Main screen (map)
The app opens on the map, full screen; USB attach opens it too. The screen stays on.

Status strip along the top (one line in landscape, two rows in portrait). Every item is a
button (at least 48 dp tall):
- Fix chip: RTK FIXED / FLOAT / DGPS / SINGLE / NO FIX (or NO GPS) in the fix colour, with
  the correction age, e.g. `FIXED 1.0s`. Tap: Details.
- GPS and NTRIP dots: green connected, amber connecting or retrying after a loss, red the
  last attempt failed (still retrying), grey off. Tap to connect, or to disconnect (while
  a session records it asks first).
- `pole 1.800`: tap for 1.4, 1.5, 1.6, 1.7, 1.8 m or Other…; applies at once and is
  logged at that moment.
- Recording: red dot, elapsed time and the number of captures (`0:12:34 · 17 pts`), or
  `REC off`. Tap to start a session, or to stop it (asks first).

Under the strip, only when present: a red bar with warnings (frame mismatch, base moved,
layers don't match base, failed pole check, GPS or caster link lost, GPS silent; tap it
for Details), and for 10 s after a capture, ref or pole check a green (or red) notice,
e.g. `S12  FIXED  5 ep  ±0.4 cm`.

Four large buttons, a column on the right edge in landscape, a row along the bottom in
portrait (each at least 64 dp):
- MODE: Surface → Object → Ref → Surface. It shows the mode name in the mode's colour
  (Surface cyan, Object magenta, Ref dark slate; the same colours mark the points on the
  map). The mode is remembered. Not while a ref is running.
- MARK: captures in the current mode (see Captures). While a ref runs it reads CANCEL REF.
- PHOTO: the system camera (see Photos).
- ☰ MENU: a panel from the right: Connect/Disconnect GPS and caster (with their state),
  Start/Stop session, Pole height, Layers (a checkbox per layer file, applied at once),
  Share last session, Sessions, Details, Settings. Back or a tap outside closes it.

Volume keys (Settings > Survey, default on): volume down = MARK, volume up = MODE while the
map is in front. A held key acts once. The key presses are consumed, so the media volume
does not change on this screen; set it elsewhere (e.g. on Details) before going out.

Details (fix chip or MENU) is the former main screen: every status line (links, byte and
message counts, caster statistics, heights, frame, capture count, folder) with the link,
session, Settings, Share and Sessions buttons.

## Captures
Each MARK is written at once to `points.csv` in the session folder and as a `capture` event
in `events.csv` (the same row; events.csv turns commas into `;`). Captures are numbered
1, 2, 3, … per session across all classes (photo rows included).
- Surface: averages the GGA epochs of the last 1 s (ending at the latest epoch, which
  must be under 2 s old). Only the best fix quality among them is used: RTK fixed, else
  float, DGPS, single; the `fix` column says which. With no fix at all it is refused with
  an error buzz and a short message.
- Object: the same, class `object`. No label prompt: label it later on the laptop.
- Ref: an occupation. MARK starts it (refused with no fix at all); a ring at the top of
  the map counts down the seconds left. Only RTK fixed epochs are averaged and count
  toward the duration (Settings, default 30 s). If the fix is not fixed for more than 2 s
  (or the GPS goes quiet) the ring turns amber and says PAUSED: not fixed; it resumes with
  the next fixed epoch. MARK again cancels it (logged, nothing written). At the end the row
  is written with the number of epochs and their scatter, and the pole check runs.
  The pole height is the one in effect when the ref started.

Feedback (Settings > Survey: Sound, Vibration): each has its own tone and vibration
pattern: mode change 1, 2 or 3 beeps (surface, object, ref); surface capture a short high
pip; object capture a two-tone pip; ref start a long low tone; ref paused a long middle
tone; ref resumed a short one; ref end a rising three-tone; ref cancelled a falling two;
photo a click; pole check OK two rising beeps; pole check failed an alternating alarm;
errors (no fix, no session, mode change during a ref) a low buzz. Sounds use the media
volume, so silent mode does not mute them.

On the map: surface small cyan dots, object larger magenta squares, ref dark rings,
photo rows a brown camera icon (and a small camera next to a capture that has a photo),
optionally numbered S12, O13, R14. Only this session's captures are drawn; earlier refs
from `refs.csv` as grey rings when Settings > Survey > Show earlier refs is on.

### points.csv
One header line, then one row per capture, in capture order. UTF-8, comma separated, `.`
decimals; an empty field means not available. Text fields are quoted (RFC 4180) only when
they contain a comma, quote or line break. The app rewrites the whole file on each change
(e.g. when a photo is attached), so it is always complete.

| column | meaning |
|---|---|
| `n` | capture number in this session (1, 2, …), shared by all classes |
| `class` | `surface`, `object`, `ref` or `photo` (a photo not attached to a capture) |
| `elapsed_ns` | phone clock (`SystemClock.elapsedRealtimeNanos`, as in the other logs) when MARK or PHOTO was pressed; for a ref, the time of its last epoch |
| `utc` | UTC of the last averaged GGA epoch, `yyyy-MM-ddTHH:mm:ss.ssZ`: the time of day from the GGA, the date from the phone (the day that puts it nearest the phone's clock) |
| `lat`, `lon` | mean position, decimal degrees, 9 decimals (about 0.1 mm), in the frame of the base (see Frames) |
| `h_ant_ellips` | mean ellipsoidal height of the antenna (GGA alt + geoid separation), m |
| `pole_m` | pole height in effect, m |
| `h_ground_ellips` | `h_ant_ellips - pole_m`: ellipsoidal height of the ground point, m |
| `fix` | GGA quality of the averaged epochs: 4 RTK fixed, 5 float, 2 DGPS, 1 single, 0 none |
| `n_epochs` | number of GGA epochs averaged |
| `sd_e_m`, `sd_n_m`, `sd_u_m` | scatter of those epochs: sample standard deviation (n - 1) east, north, up about their mean, m (0 for one epoch) |
| `age_s` | mean correction age of those epochs, s |
| `photo` | photo file name(s) in `photos/`, `;` between several, or empty |
| `note` | always empty from the phone: for the laptop tool |

Example (made-up position):
```
n,class,elapsed_ns,utc,lat,lon,h_ant_ellips,pole_m,h_ground_ellips,fix,n_epochs,sd_e_m,sd_n_m,sd_u_m,age_s,photo,note
1,surface,81234567890123,2026-11-11T14:03:22.40Z,10.000000123,20.000000456,31.2345,1.800,29.4345,4,5,0.0021,0.0018,0.0040,1.0,,
2,ref,81290123456789,2026-11-11T14:04:31.00Z,10.000010000,20.000020000,31.3011,1.800,29.5011,4,30,0.0030,0.0026,0.0061,1.1,photo_2_20261111T140440Z.jpg,
```

Related events in `events.csv`: `capture <row>`, `capture refused class=<c>: no fix`,
`ref start n=… duration_s=… pole_height=… quality=…`, `ref paused …`, `ref resumed …`,
`ref end n=… epochs=… pauses=…`, `ref cancelled …`, `pole check n=…: …`, `photo file=…
attached to n=…` or `own row n=…`, `photo cancelled: …`.

### Refs and the pole check
Every ref is also appended to `refs.csv` in the storage root
(`Documents/IceCubeLogger/refs.csv`, or the app media folder's, like sessions): the
session folder name, then the points.csv columns. It is kept across sessions. At the end
of a ref the app looks for earlier refs within 0.30 m horizontally. If there are any, it
shows the ground height difference to the most recent one and to the mean of all of them:
- |difference to the most recent| over the tolerance (Settings, default 3 cm): a red
  "pole check: +4.2 cm vs last visit" in the warnings bar (until the next ref) and an
  alarm sound. Usually the pole height setting is wrong, or the pole sank.
- otherwise a green "pole check OK (±0.8 cm vs last visit)".
- first visit (or no ground height): "first visit to this mark", no check.

`refs.csv` contains positions. It stays on the phone and in Drive; never commit it to
the public repo. An earlier install's `refs.csv` in Documents may not be readable after a
reinstall (Android only lets an app read files it created); the app then writes a new one
in the app media folder.

## Photos
PHOTO opens the phone's camera app (no camera permission needed) to take one photo into
the session's `photos/` folder, named `photo_<n>_<utc>.jpg` (UTC `yyyyMMddTHHmmssZ`).
If the most recent capture was taken at most 60 s before PHOTO was pressed, the photo is
attached to it (its `photo` column, `n` in the file name is that capture's); otherwise it
gets a row of its own, class `photo`, with the averaged position of that moment (empty
when there is no fix). Cancelled in the camera: the empty file is deleted and the
cancellation logged. Photos are drawn as a camera icon and are included in the shared zip.

Photos can carry EXIF GPS (the camera app may write the phone's location into them). They
belong with the session data in Drive and must never go into the public repo.

## Screen orientation
Lock the screen to the mount (Settings > Display): Landscape when the phone sits with its
top to the left, Reverse landscape when the top is to the right, Portrait when upright.
Every screen works in both orientations:
- Map (main screen): in landscape the strip is one line, the MODE, MARK, PHOTO and MENU
  column runs down the right edge over the full height and the status bar is hidden
  (swipe down to show it). In portrait the strip is two rows and the buttons are a row
  along the bottom. Small Center and Fit buttons sit at the map's top right.
- Details: in landscape, warnings in a red banner across the top, status scrolling on the
  left, buttons in pairs on the right; the title bar is hidden. In portrait the banner is
  at the top, above one scrolling column. Buttons are 56 dp (drawn) for gloves.
- Settings and Sessions scroll and are at most 640 dp wide, centred.

Rotating (or changing the setting) does not restart a screen: logging goes on in the
service, the map keeps its zoom, centre and follow mode, an open menu, share dialog or
ref countdown stays, and typed Settings values stay.

The IMU log is not affected: `imu.csv` is always in the phone's raw sensor axes, whatever
the screen orientation. Remounting the phone (e.g. portrait to landscape on the pole)
changes how those axes sit relative to the pole, so a plumb (tilt) calibration from the
IMU holds only for one mount; redo it after remounting.

## Heights
Details shows, from the latest GGA (under 5 s old) (the map shows the ground height):
- antenna: ellipsoidal height of the antenna = GGA altitude (field 9, above the
  receiver's geoid model) + geoid separation (field 11). If the GGA has no separation the
  app says so and shows no ellipsoidal height.
- ground: antenna minus the pole height.
- pole: the pole height in effect.

Raw logs (`gps.ubx`) are never changed: they always hold antenna positions.
`events.csv` gets `pole_height=1.800` at session start and `pole_height=1.700 changed`
whenever it is changed during a session. Each capture row carries the pole height in
effect (`pole_m`). (The 0.4 "Mark control point" button and its `control` events are
replaced by Ref captures.)

## Map
North up, in metres east and north on a local tangent plane (WGS84 ellipsoid, ENU), with
no basemap and no network.
- Origin: the first point of a feature with `kind` "base" in the switched-on layers; else
  the first GPS fix of the session; else (layers only, no fix yet) the first vertex of the
  first layer, until a fix arrives.
  The info box at the bottom right says which, with satellites and the ground height.
- Live: the position dot is coloured by fix quality (red single, blue DGPS, amber float,
  green fixed, grey other; legend top left, with the capture classes), with a 2-minute
  trail and this session's captures (see Captures).
- Pinch to zoom, drag to pan (stops following), double tap or Center to follow the
  position again, Fit to show all layers, trail and captures. Scale bar bottom left.
  Labels show feature names and are skipped where they would overlap.

## Map layers
Layer files are `*.geojson` or `*.json` in `Documents/IceCubeLogger/layers/` (or the app
media folder's `layers/`, same as sessions). MENU > Layers switches them on and off. Android only lets the app read files it
wrote there itself, so use Settings > Map > Import layer files…: it opens the system file
picker (Google Drive, Downloads, …), checks each file and copies it into `layers/`,
replacing a file of the same name. A bad file shows its error in Settings and is skipped.

Format: a GeoJSON FeatureCollection (RFC 7946; a single Feature or a bare geometry also
works). Coordinates are WGS84 degrees `[lon, lat]` or `[lon, lat, h]`, h ellipsoidal metres
(longitude first). Geometries: Point, MultiPoint, LineString, MultiLineString, Polygon
(first ring outer, the rest holes), MultiPolygon. GeometryCollection is not supported.
A feature with a missing, empty or invalid geometry is skipped and counted.

Properties, all optional:

| property | meaning |
|---|---|
| `name` | label drawn next to the feature |
| `kind` | free text, e.g. `boundary`, `base`, `rink`, `survey`. `base` (any case) on a Point sets the map origin and is used for the base check below; give it `h` |
| `stroke` | line, outline or point colour, `#rrggbb` or `#aarrggbb` (alpha first, Android order) |
| `fill` | polygon fill (or point colour), `#rrggbb` or `#aarrggbb` |

Defaults: points blue (a base point is a red triangle), lines dark grey, polygons green
outline with a light green fill. Example (made-up coordinates):

```json
{"type": "FeatureCollection", "features": [
  {"type": "Feature", "properties": {"name": "Base", "kind": "base"},
   "geometry": {"type": "Point", "coordinates": [0.00010, 0.00020, 31.234]}},
  {"type": "Feature", "properties": {"name": "Rink", "kind": "rink", "stroke": "#1565c0", "fill": "#331565c0"},
   "geometry": {"type": "Polygon", "coordinates": [[[0.0, 0.0], [0.0002, 0.0], [0.0002, 0.0001], [0.0, 0.0001], [0.0, 0.0]]]}}
]}
```

A few thousand vertices in total draw smoothly; files over 20 MB are refused.

## Frames
RTCM 1005/1006 give the base's antenna reference point (ARP) as ECEF X, Y, Z (and, in
1006, the antenna height above the mark) but no datum or epoch (DF021, "ITRF realization
year", is normally 0; if set it is logged). So the frame of your RTK positions is whatever
frame the base coordinates were entered in, and the app has to be told it:
1. Sourcetable: when the caster is started the app fetches the sourcetable (`GET /`, same
   host, port, credentials and NTRIP 1.0 header as the client, on its own thread so it
   never holds up the corrections) and reads the last field (misc) of the mount point's
   STR record, e.g. `ITRF2020 epoch 2026.85`. Recognised names, any case: ITRFyyyy
   (ITRF2020, ITRF2014, ITRF2008, …), NAD83(2011) (and other NAD83 realizations),
   WGS84(Gxxxx) or WGS84; the epoch is a decimal year after `epoch`, `ep`, `@` or on its
   own. Logged as `frame=ITRF2020 epoch=2026.85 source=sourcetable`, or
   `frame source=sourcetable: none (<reason>)`.
2. Setting: Settings > Caster > Frame and Frame epoch, used when the sourcetable names no
   frame. If both name a frame and they differ, the sourcetable is used, a red banner shows
   the mismatch and both are logged.
3. Neither: the screen says "frame unknown". The app never guesses.

The frame in use is logged at session start and whenever it changes, as
`frame=<name> epoch=<e|none> source=setting|sourcetable|none`.

Base position: the first 1005 and the first 1006 of a session are logged as
`base station=<id> type=<1005|1006> ecef=<x>;<y>;<z> h=<antenna height|none>` (events.csv
turns commas into `;`). A later message more than 0.01 m from the first, or with another
station ID, shows a red "base moved" banner and is logged once per change.

Layers against the base: if a layer (switched on first, else any) has a `kind` "base"
Point with `[lon, lat, h]`, it is converted to ECEF and compared with the received ARP
(like with like: the layer point is the ARP, the 1006 antenna height is not applied). The
difference, received minus layer, in metres east/north/up at the layer point: over 0.05 m
horizontally or vertically shows a red banner on the map and Details ("map layers
are not in this base's frame: dE dN dU"), within it a small "layers match base" line.
Either is logged as `layer base check: ...` when it changes. No base layer, no message.

## Share
MENU > Share last session, or Sessions (a list, newest first; tap one), zips the
session folder (with `photos/`) to the app cache and opens the Android share sheet with an
`application/zip`; pick Google Drive. If the session is still recording the app asks:
Stop and share (stops the session, then zips the complete files), or Share snapshot (keeps
recording; flushes the files and zips each one up to its length at that moment, plus a
`SNAPSHOT.txt` saying so; the last chunk of a log may be cut and there is no stop event).
Only the newest zip is kept in the cache.

## Files
Each session is a folder in `Documents/IceCubeLogger/sessions/`, which survives uninstall.
(On Android 10 and older, or if Documents cannot be written, it is
`Android/media/rink.icecube.logger/sessions/` instead, and `events.csv` says so.) The
folder path is on Details.
- `gps.ubx`: every byte from the receiver, unchanged and in read order
- `gps_chunks.csv`: `elapsed_ns,byte_offset,length` for each read, for USB or Bluetooth
- `corrections.rtcm`: every byte from the caster, unchanged (when Log corrections is on)
- `ntrip_chunks.csv`: `elapsed_ns,byte_offset,length` for each caster read
- `imu.csv`: acc, grav and gyro samples (m/s², rad/s) in the phone's raw sensor axes (not
  the screen's); times are sensor event times
- `events.csv`: session start and stop, links connected and lost (with transport and
  device), retries, pole height, captures, refs, pole checks, photos, frame, base position
  and checks, and final counts
- `points.csv`: the captures (see points.csv); written from the start, header only until
  the first capture
- `photos/`: the session's photos (may carry EXIF GPS)

Outside the session folders, `refs.csv` in the storage root collects every ref (see Refs
and the pole check).

`elapsed_ns` is `SystemClock.elapsedRealtimeNanos()`. Offsets keep counting across link
drops. The app changes receiver settings in RAM only, and only when Configure is on.
A power cycle restores the receiver's saved settings.
