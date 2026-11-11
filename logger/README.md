# Ice Cube Logger 0.3

Records the RTK Express GPS over Bluetooth or USB, runs its own NTRIP client, and logs
phone accelerometer, gravity and gyroscope data, all on one phone clock. It shows antenna
and ground heights, a local map with your own GeoJSON layers (no basemap, no network),
checks the reference frame and base position, and shares a session as a zip.

## Build
`./gradlew assembleDebug test lint` (needs the Android SDK; set `sdk.dir` in
`local.properties`). The APK is `app/build/outputs/apk/debug/app-debug.apk`. Or open this
folder in Android Studio and press Run.

## Settings
Press Settings on the main screen. Changes apply at the next Connect.
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
  saved and the screen says why. It applies at once.
- Map: the layer files found, each with a checkbox (remembered by file name), a summary
  line (features, vertices, skipped features) or the error for a bad file, Import layer
  files… and Reload layers. See Map layers below.

## Use
1. Close SW Maps first: only one app can hold the receiver's Bluetooth or USB link.
2. For USB, connect to the GPS USB port with an OTG cable, not the ESP32 port, and allow
   USB access.
3. Press Connect GPS and/or Connect caster. Either one starts a session. The caster works
   without the GPS; corrections go to the GPS whenever both are connected, and the GGA
   from the GPS goes to the caster.
4. Mark control point (main screen or map) writes a numbered mark (C1, C2, …) to the
   session with the pole height in effect, and puts it on the map if there is a fix.
5. Press Stop session when finished. If a link drops, the app logs it and reconnects
   (Bluetooth every 3 to 30 s, USB on re-attach, caster every 5 to 60 s).

## Heights
The main screen shows, from the latest GGA (under 5 s old):
- antenna: ellipsoidal height of the antenna = GGA altitude (field 9, above the
  receiver's geoid model) + geoid separation (field 11). If the GGA has no separation the
  app says so and shows no ellipsoidal height.
- ground: antenna minus the pole height.
- pole: the pole height in effect.

Raw logs (`gps.ubx`) are never changed: they always hold antenna positions.
`events.csv` gets `pole_height=1.800` at session start and `pole_height=1.700 changed`
whenever it is changed during a session, and each control mark is
`control n=<n> pole_height=<m> gga_quality=<q|none>`.

## Map
Press Map on the main screen. It is north up, in metres east and north on a local tangent
plane (WGS84 ellipsoid, ENU), with no basemap and no network. The screen stays on.
- Origin: the first point of a feature with `kind` "base" in the switched-on layers; else
  the first GPS fix of the session; else (layers only, no fix yet) the first vertex of the
  first layer, until a fix arrives. The line above the map says which.
- Live: the position dot is coloured by fix quality (red single, blue DGPS, amber float,
  green fixed, grey other; legend top left), with a 2-minute trail and this session's
  control marks (purple squares, C1, C2, …).
- Pinch to zoom, drag to pan (stops following), double tap or Center to follow the
  position again, Fit to show all layers, trail and marks. Scale bar bottom left.
  Labels show feature names and are skipped where they would overlap.

## Map layers
Layer files are `*.geojson` or `*.json` in `Documents/IceCubeLogger/layers/` (or the app
media folder's `layers/`, same as sessions). Android only lets the app read files it
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
horizontally or vertically shows a red banner on the main screen and the map ("map layers
are not in this base's frame: dE dN dU"), within it a small "layers match base" line.
Either is logged as `layer base check: ...` when it changes. No base layer, no message.

## Share
Share last session (main screen) or Sessions (a list, newest first; tap one) zips the
session folder to the app cache and opens the Android share sheet with an
`application/zip`; pick Google Drive. If the session is still recording the app asks:
Stop and share (stops the session, then zips the complete files), or Share snapshot (keeps
recording; flushes the files and zips each one up to its length at that moment, plus a
`SNAPSHOT.txt` saying so; the last chunk of a log may be cut and there is no stop event).
Only the newest zip is kept in the cache.

## Files
Each session is a folder in `Documents/IceCubeLogger/sessions/`, which survives uninstall.
(On Android 10 and older, or if Documents cannot be written, it is
`Android/media/rink.icecube.logger/sessions/` instead, and `events.csv` says so.) The
folder path is on the main screen.
- `gps.ubx`: every byte from the receiver, unchanged and in read order
- `gps_chunks.csv`: `elapsed_ns,byte_offset,length` for each read, for USB or Bluetooth
- `corrections.rtcm`: every byte from the caster, unchanged (when Log corrections is on)
- `ntrip_chunks.csv`: `elapsed_ns,byte_offset,length` for each caster read
- `imu.csv`: acc, grav and gyro samples (m/s², rad/s); times are sensor event times
- `events.csv`: session start and stop, links connected and lost (with transport and
  device), retries, pole height, control marks, frame, base position and checks, and final
  counts

`elapsed_ns` is `SystemClock.elapsedRealtimeNanos()`. Offsets keep counting across link
drops. The app changes receiver settings in RAM only, and only when Configure is on.
A power cycle restores the receiver's saved settings.
