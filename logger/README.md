# Ice Cube Logger 0.13.0

Records the RTK Express GPS over Bluetooth or USB, runs its own NTRIP client, and logs
phone accelerometer, gravity and gyroscope data, all on one phone clock. The main screen
is a local map with your own GeoJSON layers (no basemap, no network) where you capture
surface, object and reference points with large glove-friendly buttons or the volume
keys, and take photos. Reference points are averaged and checked against a robust
reference from earlier visits (the pole check), with cues for a pole off the mark centre
or moving during the ref. A false-fix guard warns about captures from a young RTK fix and flags
fixes that began with a suspicious jump or right after a long float spell, and a tilt capture finds the pole tip from
several leans when the pole cannot stand plumb (a house corner, a fence post). Auto
capture takes a surface point by itself when the pole is held plumb and still, after a
quick pole calibration each session. A live
coverage overlay shows where the surface still needs captures (red, amber, clear). It checks
the reference frame and base position, and shares a session as a zip. It works in portrait or landscape, and the screen can be locked to how the
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
The first line of the screen (and the last line of the MENU panel) shows the app version,
e.g. "Ice Cube Logger 0.13.0 (build 17)".
- Display: Screen orientation: Auto (follow the system, including its auto-rotate
  toggle), Portrait, Landscape or Reverse landscape. Default Auto. Every screen of the app
  uses it, whatever the system auto-rotate toggle says, so a phone mounted on a tilting
  pole does not flip. It applies at once. See Screen orientation below.
- Caster: host, port, mount point, user, password (shown as dots), Log corrections,
  Connect caster when the GPS connects (0.8.0's "Connect caster at app launch", same
  setting: the caster now starts when a GPS started by Connect or by Start logging when
  the GPS connects first connects, so opening the app without the rover connects
  nothing), Frame (Unknown, ITRF2020, ITRF2014, ITRF2008,
  NAD83(2011), WGS84) and an optional Frame epoch (decimal year). See Frames below.
- GPS link: USB or Bluetooth. For Bluetooth, pick the receiver from the paired devices;
  pair it in Android Bluetooth settings first. Configure receiver (RAM only) is on by
  default for USB and off for Bluetooth, because the RTK Express firmware already sets up
  the Bluetooth port. Start logging when the GPS connects starts a session on its own: for
  USB when Android opens the app on attach, for Bluetooth when the receiver connects after
  the app is opened. With no session open, that wait ends after Wait for the rover (min,
  0.5 to 60, default 2), and the logger stops in the background.
- Survey: Pole height, from ground mark to the antenna reference point: 1.8, 1.7, 1.6, 1.5,
  1.4 m or Other… for a typed value (0 to 5 m, up to 3 decimals). An invalid value is not
  saved and the screen says why. It applies at once (also settable from the map's strip).
  - Ref duration: seconds of RTK fixed epochs averaged for a Ref capture, 10 to 300,
    default 30.
  - Pole check tolerance: cm, 0.5 to 20, default 3. See Refs and the pole check.
  - Pole check cues (0.12.0, see Refs and the pole check): off-centre cue, cm, 0 to 20,
    default 2.0; pole moved cue, cm, 0 to 20, default 1.5. 0 turns a cue off.
  - False-fix guard (see False-fix guard): Fix young limit, s, 0 to 300, default 120 (0
    turns the warning off); Fix jump warning, m, 0.1 to 10, default 1.0; Fix after float
    spell, s, 0 to 300, default 20 (0 turns the rule off). The young limit applies at once,
    the other two from the next fix stretch. Until 0.10 the young limit was "Fix settle
    time" (0 to 60 s, default 10, captures held back); the first start of 0.11.0 moves a
    stored 10 to 120 once, and keeps any other value (a 10 typed in later stays).
  - Tilt capture gates (see Tilt capture): minimum coverage 0 to 360°, default 180; maximum
    RMS 0.1 to 20 cm, default 2; minimum lean 0 to 30°, default 5; maximum lean 5 to 60°,
    default 35; maximum sigma 0.1 to 50 cm, default 5; IMU disagreement 0.5 to 20°,
    default 3; lean scatter 0.2 to 20 cm, default 2. A value out of range is not saved and
    the screen says why. They apply to the next tilt capture.
  - Auto capture (0.13.0, see Auto capture): on or off (default on; it does nothing until
    the pole is calibrated in the session), plumb limit 0.5 to 10°, default 2; hold time
    0.5 to 30 s, default 2; still limit 0.2 to 20 cm, default 2. They apply from the next
    epoch.
  - Volume keys: Mark / Mode (default on): on the map, volume down = MARK, volume up =
    MODE; during a tilt capture down = LEAN, up = finish; hold down about 1 s to start a
    tilt capture. The keys then do not change the volume while the map is in front.
  - Sound and Vibration (default on): the cues listed under Feedback.
  - Show earlier refs (default off): refs from `refs.csv` of other sessions as grey rings.
  - Number captures on the map (default on): labels S12, O13, R14 next to the points.
- Coverage (see Coverage below), each a typed value checked against its range; a value out
  of range is not saved and the screen says why. A change rebuilds the overlay.
  - Sigma target: cm, 0.5 to 50, default 3.0. A node at or below it is clear.
  - Grid spacing: m, 0.1 to 2, default 0.5.
  - Capture radius: m, 0.1 to 5, default 0.75 (icecube's `capture_radius`).
  - Reach: m, 0.1 to 20, default 2.0: nodes this close to a capture are in the surveyed
    area. A reach below the capture radius is accepted and simply hides gaps.
  - Together: capture radius and reach at most 50 grid spacings (so 5 m at 0.1 m, and the
    20 m reach needs a spacing of 0.4 m or more), which keeps one capture to about 10,000
    grid cells; and a capture radius of at least 0.71 grid spacings (half the cell
    diagonal), so every capture feeds a node. While the three break this, none of them is
    saved and the screen says why (a field that does not parse counts with its saved
    value). Values saved by an earlier version are used as follows: one outside its own
    range (e.g. a spacing over 2 m or a radius over 5 m) falls back to its default; then
    a radius or reach over 50 grid spacings is cut to 50, and a radius under 0.71 grid
    spacings is raised to 0.71.
  - Ground sigma: cm, 0 to 10, default 1.5: how well one shot represents the surface
    around it (lawn 1.5 cm; about 0 on ice or a hard surface).
  - Pass wander: cm, 0 to 10, default 1.5: the RTK wander between passes, added to each
    node's sigma before the target test.
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

## Notification
While the logger runs in the background (a session, the GPS or the caster), Android shows
an ongoing notification that says what is running, for example "Waiting for the rover…
(up to 2 min)", "Caster connected, no GPS · session …" or "Logging session …: GPS and
caster". With no session open it has a Stop action that stops the GPS and the caster;
while a session is open it has none, so a stray tap cannot end the session: end it with
REC (which asks first) or MENU > Stop session. Tapping it opens the map. Opening the app
only to look at the map starts nothing, so there is no notification. Swiping the app away
from the recent apps stops the logger when no session is open; a session keeps logging.

## Main screen (map)
The app opens on the map, full screen; USB attach opens it too. The screen stays on.

Status strip along the top (one line in landscape, two rows in portrait). Every item is a
button (at least 48 dp tall):
- Fix chip: RTK FIXED / FLOAT / DGPS / SINGLE / NO FIX (or NO GPS) in the fix colour, with
  the correction age, e.g. `FIXED 1.0s`. While a fix is young it reads `FIX young 47s` (its
  age) in amber; in a suspect fix stretch it reads `FIXED? jump 2.1m` (began with a jump
  over the warning) or `FIXED? after float` (began right after a long float spell) in amber
  until the fix drops. Tap: Details.
- GPS and NTRIP dots: green connected, amber connecting or retrying after a loss, red the
  last attempt failed (still retrying), grey off. Tap to connect, or to disconnect (while
  a session records it asks first).
- `pole 1.800`: tap for 1.4, 1.5, 1.6, 1.7, 1.8 m or Other…; applies at once and is
  logged at that moment.
- Recording: red dot, elapsed time and the number of captures (`0:12:34 · 17 pts`), or
  `REC off`. Tap to start a session, or to stop it (asks first).

Under the strip, only when present: a red bar with warnings (frame mismatch, base moved,
layers don't match base, failed pole check, GPS or caster link lost, GPS silent; tap it
for Details), and for 10 s after a capture, ref or pole check a green (or red) notice
(amber for a pole check cue or doubtful reference, see Refs and the pole check),
e.g. `S12  FIXED  5 ep  ±0.4 cm`; amber with dark text when the capture is flagged, e.g.
`S12  FIXED  5 ep  ±0.4 cm  fix may be false (jumped 2.2 m)`. A notice about a capture has
a second line with its ground position in the map frame (see Map), e.g. `G x 12.345 y -3.210 z -0.452`.
A capture's own notice (a surface or object MARK, a tilt capture's end, a ref's pole-check
notice) has an UNDO button beside it for 8 s from the capture (the row's own time, so a
ref's notice, which comes after the pole check, can show it with less left). A tap flags
the capture as `accidental` (see Flagged captures). A tap pressed down in the first 750 ms
after UNDO appears (a double tap, a press already under way) does nothing but play the error
sound. A newer notice for the same capture (a ref's pole check after its save) keeps UNDO;
one for another capture replaces it.
If the screen was off past the 8 s, UNDO is gone or says `Too late to undo`. Photo notices
(`Photo on S12`, `Photo saved`) never offer UNDO, so a stray photo cannot flag the capture
it landed on; an accidental capture whose photo went to it (a MARK a few seconds after
another) is flagged from Recent captures. The info box says `2 flagged` when a session has
flagged captures.

Under the notice, in Surface mode while Auto capture is on (0.13.0): the AUTO line, large
(56 dp), saying what auto capture is waiting for (see Auto capture): blue `AUTO: calibrate
to enable (tap)` (a tap starts the pole calibration) and, during one, `CALIBRATE 2/3: lean
slowly all round  7 s` (a tap offers to stop it); amber `AUTO: off, fix young 40 s`, `off,
not RTK fixed` or `off, fix may be false`; grey `off during the tilt set` or `off during the
ref`; teal `ready, hold plumb  3.4°`, `ready, hold still` or `holding  1 / 2 s`; green `S12
taken, move to the next point`.

Four large buttons, a column on the right edge in landscape, a row along the bottom in
portrait (each at least 64 dp):
- MODE: Surface → Object → Ref → Surface. It shows the mode name in the mode's colour
  (Surface cyan, Object magenta, Ref dark slate; the same colours mark the points on the
  map). The mode is remembered. Not while a ref is running.
- MARK: captures in the current mode (see Captures). While a ref runs it reads CANCEL REF.
  Long-press: start a tilt capture in the current mode (see Tilt capture). During a tilt
  capture MODE reads `LEAN` with the next lean number and MARK reads `TILT 2/4` until four
  leans, then `FINISH TILT`.
- PHOTO: the system camera (see Photos). Its second line says where a photo would go now:
  `→ S12 (14 s)` (the latest capture not flagged, and its age, within 60 s) or `→ own row`.
- ☰ MENU: a panel from the right: Connect/Disconnect GPS and caster (with their state),
  Start/Stop session, Pole height, Tilt capture (or Cancel tilt capture while one runs),
  Calibrate pole (Recalibrate pole once done, Stop pole calibration while one runs; see Auto capture),
  Recent captures… (see Flagged captures), Layers… (a checkbox per layer file, applied at once, and Import…, see Map layers),
  Coverage overlay (☑ on / ☐ off, remembered, on by default; see Coverage),
  Share last session, Sessions, Details, Settings. Back or a tap outside closes it.

Volume keys (Settings > Survey, default on): volume down = MARK, volume up = MODE while the
map is in front. Volume down acts when released; held about 1 s it starts a tilt capture
instead. During a tilt capture volume down = LEAN and volume up = finish. A held key acts
once. The key presses are consumed, so the media volume
does not change on this screen; set it elsewhere (e.g. on Details) before going out.

Details (fix chip or MENU) is the former main screen: every status line (links, byte and
message counts, caster statistics, heights, frame, map frame with the current ground
position in it, capture count, folder) with the link, session, Settings, Share and Sessions
buttons.

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

### Flagged (void) captures
From 0.9.0 a capture can be flagged as bad (voided): an accidental MARK, a bad pole
placement. Nothing is deleted and numbers are never reused: the row stays in `points.csv`
with `void` = 1 and a `void_reason`, and readers leave it out (see below). Two ways in:
- UNDO beside a capture's notice for 8 s (see Main screen), reason `accidental`.
- MENU > Recent captures…: the last 10 captures (surface, object, ref; photo rows not
  listed), newest first, with label, class, age and, for a flagged one, `FLAGGED:
  <reason>`. Tap one that is not flagged to pick a reason (or type one); tap a flagged one
  to un-flag it. Not while a ref occupation or a tilt set runs. A flagged capture is only
  flagged, never deleted, and its reason is kept. Whether a pole stop at a flagged capture
  is still used depends on the reason, in `soundings.py`: `accidental` frees it, any other
  reason keeps it flagged.
- On the map a flagged capture is a small grey cross with a grey `S12 ✕` label, never its
  class symbol or camera; a photo row (also one a flag made for moved photos) keeps its
  camera.
- Who can be flagged: surface, object and ref rows, including tilt captures (one row
  each; their leans stay in `tilt_obs.csv`. No reader of `tilt_obs.csv` exists yet; one
  written later must drop the leans of a voided `n`).
  Not a photo row, not one already flagged, and not while a ref occupation or a tilt set
  runs (cancel those instead). UNDO only works within 8 s of the capture.
- Reasons: `accidental`, `bad pole placement`, `moved`, `other` (commas and line breaks
  are removed from any text).
- Unflagging clears `void` and `void_reason`. Photos that moved stay where they went.
- Photos on a flagged row move (only the `photo` column changes; the files stay): to the
  nearest earlier capture that is not flagged and not a photo row, however long ago,
  because the user has said this capture was a mistake; with none, to a new `photo` row at
  the flagged row's position. A photo taken after an undo goes to the latest capture that
  is not flagged, within the usual 60 s.
- A flagged ref: its `refs.csv` row is rewritten in place with the same columns (the one
  write to refs.csv that is not an append), in every storage root's refs.csv that holds
  it. When the copies disagree (a rewrite that reached only some of them), a reader takes
  a ref's flag from the newest copy by file time whose row has a void value, and flagged
  when copies of that time disagree; the QGIS importer reads the copies it is given by the
  same rule, so the phone and the gpkg agree. A flagged ref is never a visit for
  the pole check and is not drawn among the earlier refs; its pole-check notice and
  warning are cleared. The rewrite keeps the row's `photo` column as it was appended: refs.csv photo names are never updated after the
  append (not for a photo attached later, not for a move), and `points.csv` is the record
  of which photos belong to which capture.
- Coverage: a flagged capture leaves the overlay (it is rebuilt without it); unflagged, it
  comes back.
- `events.csv` is the audit trail: `flag n=<n> reason=<reason, spaces as _> via=undo|list`,
  `unflag n=<n>`, `photo moved n=<from> to=<to> photos=<files>` (with `own row` when a new
  photo row took them, followed by its `capture` line), and `ref n=<n> voided|unvoided in
  refs.csv (<k> rows)`, or `error: ref n=<n> not voided|unvoided in refs.csv (…)` when no
  refs.csv could be rewritten (later sessions then still read the old flag). A partial
  write is an error too: `error: ref n=<n> voided|unvoided in <k> of <m> refs.csv copies
  holding it (<rows> rows[; <u> refs.csv copies could not be read]); …`, also used when
  every copy holding the row was rewritten but some copy could not be read at all. The
  rewritten copies are then the newest, so readers take their flag; a copy not rewritten
  brings the old flag back only if it is written to (a ref appended) later.

The phone never reopens a session, so `points.csv` is written from the app's state, and
`refs.csv` is the only void state the app reads back (the pole check, the earlier refs).
If `points.csv` and `events.csv` ever disagree, the events are the record.

Readers: the QGIS importer keeps flagged captures and refs with `void` and `void_reason`
columns (rewritten on each import, so an unflag clears them); `soundings.py` keeps them
with flag `void:<reason>` and `use` 0 unless `include_voided` is set, and
`export_labels.py` leaves them out unless `--include-voided`; each says how many. Flag and
filter, as in multibeam processing: nothing is thrown away.

### False-fix guard
In the field data (2026-11-11) several RTK "fixed" stretches were wrong: each began with a
1.3 to 2.9 m position jump at the float-to-fixed transition and then read 0.2 to 2.2 m off
in height (and horizontally suspect) for minutes; ordinary captures in them were bad. A
normal float-to-fix step moves a few centimetres to a few decimetres. So the app follows
the stretches of RTK fixed GGA epochs:
- A stretch starts at an epoch of quality 4 unless it continues the last one: the last
  fixed epoch is at most 2 s older, no float epoch came between, and the two are at most
  0.5 m apart (or 2 m/s times their time difference, if that is more: a step a moving
  pole cannot make; at 4 Hz that is 0.5 m). So a float epoch, a step, or more than 2 s
  since the last fixed epoch (the GPS went quiet, or gave no position, single or DGPS)
  starts a new stretch; from 0.11.0 a dropout of 2 s or less inside a fix (no position,
  single or DGPS epochs) does not, and the stretch's age runs on through it. Every 2 s
  test runs on the GGA time (the receiver's clock, as soundings.py), not on when the phone
  read the sentence, so a whole-epoch dropout of exactly 2 s reads the same on both.
- The jump at stretch start is the 3D distance from the last float or fixed epoch to the
  first fixed one, only if that epoch is at most 2 s older; so it carries over a dropout
  of 2 s or less (no position, single or DGPS) between float and fix. From 0.11.0 it is
  unknown (empty) after longer single or DGPS spells: the first fix of a session (no
  corrections yet) always "jumps" by metres, and that flagged good fixes (2026-11 field
  data, a 2.3 m step). Unknown is neither suspect nor clean; `fix_start` says what came
  before the stretch: `float`, `single` (single or DGPS), `none` (first epoch, or an epoch
  without a position), `fixed-jump` (a step between fixed epochs) or `gap`.
- Each start is logged in `events.csv` as `fix stretch start jump=1.234 m after float 45 s`
  (or `jump=unknown after single`, `(jump between fixed epochs)`, ...) with
  `suspect: jump`, `suspect: float_spell` or `suspect: jump;float_spell` when it is.
- Suspect, for the whole stretch: the jump was over the jump warning (Settings, default
  1.0 m), or (0.11.0) the stretch began right after a float spell at least the float
  spell limit long (Settings, default 20 s), from its first float epoch to the fix. The
  spell carries over a dropout of 2 s or less (no epochs, or non-float ones), so one
  single epoch just before the fix does not hide it; a longer dropout ends it. On
  2026-11-21 a fix after a long float spell jumped only 0.48 m and was false; a fix that
  takes long to come is the one most likely to be wrong. Captures are allowed, but the
  notice is amber, `… fix may be false (jumped 2.2 m)` or `… (after a long float spell)`,
  the cue is a fast warble ending low, and the row has `fix_suspect` 1 and its
  `suspect_reason`. Re-shoot such points after the fix drops and comes back cleanly, ideally
  without a long float spell first, or check them against a known mark.
- Young: for the young limit after a stretch starts (Settings, default 120 s) the fix may
  still be settling by centimetres (2026-11-26: a re-fix read 6 to 9 cm high at 95 to
  113 s). From 0.11.0 this is a warning, not a hold: MARK, tilt leans and refs go ahead,
  the notice is amber (`… fix young: 95 s`; a ref `… fix young during the ref`), the cue is two even middle tones with two long
  pulses (YOUNG), and the row has `fix_young` 1. Until 0.10 the app refused captures for
  the (10 s) settle time instead; field data showed 10 s was far too short, and a refusal
  that long would stall the survey. A ref counts every fixed epoch; it is young if any of them was (its ring adds
  `(fix young)`), and a young ref is never one of the earlier visits of a pole check (it is
  itself checked and shown). A tilt lean is kept and marked; the tilt row is young if any
  used lean was. The fix chip says `FIX young 47s`.
- Every capture row carries `fix_age_s` (seconds from the stretch start to the last
  averaged epoch), `fix_jump_m`, `fix_start`, `float_spell_s`, `suspect_reason` and
  `fix_young`, so later analysis can pick its own thresholds. One average never mixes two
  stretches: fixed epochs from before the latest stretch began are left out.

These thresholds are a starting point from a few afternoons of data (retuning on more
sessions is issue #41). A larger jump threshold gives fewer false alarms; the young limit
covers fixes that are still settling by centimetres, but not ones that stay wrong for
minutes (those need the jump and float spell flags or a check against a known mark).

### Tilt capture
For a point where the pole cannot stand plumb (a house corner, a fence post, a wall): keep
the tip on the point and lean the pole in several directions. Every antenna position is
then the pole height L from the tip, and the tip is the centre of the sphere through them.

How to hold the pole:
1. Choose the mode (Surface, Object or Ref): the point gets that class, with method `tilt`.
2. Put the tip on the point. Long-press MARK, or hold volume down about 1 s, or MENU >
   Tilt capture. Three rising tones; the map shows a compass rose.
3. Lean the pole 15 to 25° toward one direction, keep the tip still on the point, hold it
   still 1 to 2 s and press volume down (or the LEAN button, or long-press MARK). Two short
   beeps: the lean is taken (the last 1 s of fixed epochs and the phone's gravity vector).
4. Repeat toward at least 4 directions spread around the circle (more is better: 6 to 8
   in 45 to 60° steps is ideal). Against a wall you cannot lean into it, but lean along the
   wall both ways and away from it: that still covers 180°. The rose shows each lean as a
   spoke toward its direction (longer for more tilt, the ring is 30°) and a hollow dot
   where the antenna leans now, so you can see the gaps; below it the count, coverage, RMS
   and sigmas once 3 leans give a provisional tip.
5. Press MARK (or volume up) to finish once there are at least 4 leans. If a gate warns, a
   dialog lists the warnings: Save, More leans, or Cancel tilt. Back or MENU > Cancel tilt
   capture drops the set (its leans still go to `tilt_obs.csv`, unused).

A lean is refused (error buzz and a toast) if: there is no RTK fixed average (leans must
be fixed; float is far too noisy for this), the GGA has no height, the
1 s horizontal scatter sqrt(sd_e² + sd_n²) is over the lean scatter gate (default 2 cm:
the pole moved), or, once 3 leans give a provisional tip, its tilt is outside the minimum
and maximum lean (default 5 to 35°; a near-vertical lean adds almost nothing).

The solve (pure Java, `core/TiltSolver`, unit tested): local east/north/up metres about
the first lean; least squares for the tip c with |p_i - c| = L (L = the pole height in
effect when the set started), Gauss-Newton from mean(p) - (0, 0, L). It reports each lean's
residual, the RMS (root mean square of the residuals), each lean's tilt and azimuth (the
direction it leans toward), the coverage (360° minus the largest gap between lean
azimuths) and per-axis standard deviations of the tip from the normal matrix scaled by the
larger of the residual variance (n - 3 degrees of freedom) and (1 cm)². With leans on one
side only, the tip is poorly determined along that side: the first house-corner set
(5 leans, tilts 14 to 19°, coverage 145°) fitted with 1 cm residuals but a 9 cm sigma
north. With 1 cm noise per lean, 4 leans place the tip to about 3 cm and 6 to 8 leans to
about 2 cm (synthetic tests); a still pole's 1 s average is usually better than 1 cm.

IMU check (no calibration needed): the phone is rigid on the pole, so the pole axis is one
fixed direction in the phone's axes, and the angle between it and the phone's gravity
vector at each lean must equal that lean's tilt from the solution. The app fits that axis
to the other leans and reports, per lean, the disagreement in degrees (leave one out). This
works however the pole is twisted about its own axis; comparing the gravity vectors of two
leans directly would not, because a twist changes the angle between them without moving
the pole. A tip that slipped while a lean was read, or a bad fix, shows as a lean that
disagrees (a 15 cm slip toward the lean is about 5°). At finish, leans whose tilt ended up
outside the lean gates are dropped, then, while the largest disagreement is over the IMU
gate (default 3°), the lean whose removal leaves the others most consistent; each only
while at least 4 leans remain. The set is re-solved and a toast says what was dropped
(`Tilt: dropped lean 3 (IMU 5.2°), re-solved with 5`). Spokes of leans over the IMU gate
are red on the rose. Leans without gravity (a phone with no gravity sensor) skip the check.

Gates (Settings > Survey), checked at finish; any that fails asks before saving:
coverage under 180°, RMS over 2 cm, a used lean outside 5 to 35°, a tip sigma over 5 cm,
an IMU disagreement over 3° that could not be dropped, or a lean in a suspect fix stretch.

The point row: `lat`, `lon` and `h_ground_ellips` are the tip; `h_ant_ellips` is the mean
antenna height of the used leans (so here h_ant - pole is not h_ground); `pole_m` is L;
`fix` 4; `n_epochs` the epochs of all used leans; `sd_e_m`, `sd_n_m`, `sd_u_m` the RMS of
the leans' own 1 s scatter; `age_s` the mean correction age; `utc` from the last used
lean; `fix_age_s` the youngest stretch age, `fix_jump_m` the largest jump and `fix_suspect`
1 if any lean was in a suspect stretch; `fix_start` and `float_spell_s` from the youngest
lean's stretch, `suspect_reason` every reason of the used leans, `fix_young` 1 if any used
lean was taken while the fix was young; `method` `tilt` and the `tilt_*` columns (see
points.csv). In Ref mode the tip is also appended to `refs.csv` and the pole check runs on
it. A pole height that is off by d moves the tip up or down by about d, as for a plumb
shot; the antenna phase centre offset along the pole acts the same way.

`events.csv`: `tilt start set=… class=… pole_height=…`, `tilt lean set=… k=… n_epochs=…
sd_h_m=… tilt_deg=… grav=x;y;z young=0|1`, `tilt lean refused set=…: …`, `tilt end set=… n=… leans=…
used=… rms_m=… cover_deg=… sd_m=e;n;u imu_max_deg=… warnings=… dropped …`, `tilt cancelled
by user set=… leans=…`.

### Auto capture
0.13.0 (#35). In Surface mode the logger can capture by itself when the pole is held plumb
and still, so no button press is needed with gloves on. Object and Ref captures stay on
MARK, and MARK works as before in Surface mode too.

Calibrate first, once per session (the phone is never square to the pole, so "plumb" needs
the pole's axis in phone axes; it is kept for the session only, since the phone may sit
differently next time). Tap the blue AUTO line, or MENU > Calibrate pole, with RTK fixed,
then with the tip on one point all the way through:
1. Hold the pole still, 3 s (`CALIBRATE 1/3`). Moving more than the lean scatter (Tilt
   gates, 2 cm), losing the fix, an epoch without a height or gravity, or a gap of more than
   2 s between epochs starts it again with the buzz and a notice naming the cause
   (`Calibrate: moved, hold the pole still again`); no 3 s still within 30 s fails (naming a
   missing GGA height or phone gravity reading when that was the cause).
2. Three beeps: lean slowly all round the tip, as in a tilt set, 10 s (`CALIBRATE 2/3`).
   Every RTK fixed epoch is a sample (antenna position, gravity about it); a swing over
   0.5 m/s is skipped.
3. Three beeps: twist the pole slowly about itself, twice, 8 s (`CALIBRATE 3/3`). The
   phone's gyroscope must show the pole turned at least 90° about its axis (the range of
   the running angle, so back and forth counts); the epochs are otherwise only counted
   (for a later lever-arm calibration, #38).

The still sample and the leans are then solved like a tilt set: the tip, each sample's tilt,
and the axis that best explains the tilts from the gravity vectors (the tilt capture's IMU
fit). Accepted (rising four-tone, `Pole calibrated: 9 leans, 0.4° RMS. Auto capture on`,
or `Auto capture is off in Settings` / `Auto capture works in Surface mode` when it will not
run yet) with at least 4 leans (the still sample not counted), the tip RMS within the tilt
RMS gate (2 cm), leans of 5° or more covering at least the tilt coverage gate (180°), an
RMS fit residual of 1° or less, and a twist of 90° or more (a phone without a gyroscope
cannot pass); otherwise it fails with the reason (the buzz and an amber notice) and auto
capture stays off. No GPS epoch for 5 s (the rover gone) fails it too, so it never hangs. A young or suspect fix is fine here (only the shape of one short spell is used). MARK,
tilt capture and refs are refused while it runs; Back or MENU > Stop pole calibration stops
it. Recalibrating drops the old axis at the start. events.csv gets `pole calibration start`,
`... lean`, `... twist` and `pole axis calibrated residual_deg=.. samples=.. twist_deg=.. axis=x;y;z`
(phone axes: the mounting, not a position) or `pole calibration failed: ..`.

Then, at each GGA epoch, auto capture is armed when all of these hold, else the AUTO line
names the first that does not: Auto capture on, Surface mode, no calibration running, the
pole calibrated, no tilt set, no ref, RTK fixed, the fix not young and not suspect (see
False-fix guard; MARK still captures on a young fix, with its amber warning). Armed, it
captures once the pole has been plumb (within the plumb limit: the phone's gravity over the
last 0.5 s against the pole axis) and still (every antenna position within the still limit
of their mean, horizontally) at every epoch for the hold time (2 s, so 3 epochs at 1 Hz).
A tilt, a move or a gap of more than 2 s between epochs starts the hold again. The capture
is the same as a Surface MARK (the last 1 s average, the same columns, the surface pip,
an undoable notice `S12 auto  FIXED ...`), with `method` `auto`, and events.csv adds
`auto capture n=.. plumb_deg=.. held_s=.. spread_cm=.. epochs=..`.

One capture per stop: every capture at a stop latches it at that capture's tip: an auto
capture (once it is really taken, after its 1 s average), a Surface MARK, a finished
Surface tilt set (its solved tip, so standing the pole up on the tip afterwards takes
nothing) and an accepted calibration (its solved tip). A second capture at a stop that is
still latched leaves the latch on the first one, so undoing a MARK there leaves the stop
taken. Tips, not antennas, are compared: a pole leaning θ puts the antenna pole height ×
sin θ off its tip (6.3 cm at 2° on a 1.8 m pole), and without a heading the phone knows θ
but not which way, so each tip is known within that radius of the antenna (a MARK uses the
gravity of its 1 s average; an auto capture the largest lean of its hold; a solved tip is
exact; no gravity reading means anywhere within the pole height). Nothing more is taken
until the tip has certainly moved more than 3 times the still limit (6 cm at the default)
from the latched tip; then the full hold must pass again. Standing up after a tilt set,
swaying across the plumb cone, or an epoch with no gravity reading never clears it. So
walk to the next point. To re-shoot the same point, UNDO the capture (that clears the
latch) or press MARK.

Cues: the capture plays the surface pip. Auto capture becoming armed or off is cued only
when the fix caused it (lost, young, suspect; a rising or falling pair), after the change
has lasted 1 s, so a one-epoch dropout is silent; mode changes, tilt sets, refs and the
calibration have their own cues, and the plumb and still states never cue. events.csv:
`auto capture armed`, `auto capture off: no_fix`, and the settings at session start
(`auto_capture=on plumb_deg=2.0 hold_s=2.0 still_cm=2.0`).

### tilt_obs.csv
Every lean of every tilt set in the session, so a set can be re-solved later (another pole
height, other gates, a different solver). Appended when a set is finished or cancelled;
header first. Columns:

| column | meaning |
|---|---|
| `set` | tilt set number in this session (1, 2, …) |
| `n` | the points.csv row it produced; empty for a cancelled set |
| `lean` | lean number in the set (1, 2, …), refused leans not counted |
| `elapsed_ns`, `utc` | phone clock when the lean was taken; UTC of its last epoch |
| `lat`, `lon`, `h_ant_ellips` | the lean's 1 s mean antenna position |
| `n_epochs`, `sd_e_m`, `sd_n_m`, `sd_u_m`, `age_s` | as in points.csv, for this lean |
| `fix_age_s`, `fix_jump_m`, `fix_suspect` | the fix stretch of this lean (no 0.11.0 columns here: young is `fix_age_s` under the young limit) |
| `grav_x`, `grav_y`, `grav_z`, `grav_n` | mean phone gravity vector over the same second (m/s², phone axes as in imu.csv) and the number of samples; empty and 0 without a gravity sensor |
| `e_m`, `n_m`, `u_m` | the lean in local ENU metres about the set's first lean |
| `tilt_deg`, `azimuth_deg`, `resid_m`, `imu_deg` | from the last solve: tilt from vertical, direction it leans toward, sphere residual, IMU disagreement |
| `used` | 1 if the lean is in the saved solution |
| `why_dropped` | e.g. `IMU 5.2 deg` or `tilt 3.1 deg`; empty if not dropped |

Feedback (Settings > Survey: Sound, Vibration): each has its own tone and vibration
pattern: mode change 1, 2 or 3 beeps (surface, object, ref); surface capture a short high
pip; object capture a two-tone pip; ref start a long low tone; ref paused a long middle
tone; ref resumed a short one; ref end a rising three-tone; ref cancelled a falling two;
photo a click; pole check OK two rising beeps; pole check failed an alternating alarm;
an amber pole check (a cue or a doubtful reference, 0.12.0) the low error buzz;
a capture in a suspect fix stretch a fast warble ending low; a capture, lean or ref from a
young fix two even middle tones with two long pulses (0.11.0; one that is both suspect and
young plays only the suspect cue, a ref too; a suspect or young ref with an amber pole
check plays only its fix cue, not the error buzz as well); tilt start three rising
tones, each lean two short beeps, tilt saved a rising three-tone; an auto capture the
surface capture pip (0.13.0); auto capture armed again after the fix came back (or grew
old enough) a rising pair, off because of the fix a falling pair; pole calibration start
two low tones, each next phase three middle beeps, accepted a rising four-tone, a failed
calibration or moving during its still phase the low buzz; errors (no fix, no
session, mode change during a ref, a refused lean) a low buzz. Sounds use the media
volume, so silent mode does not mute them.

On the map: surface small cyan dots, object larger magenta squares, ref dark rings,
photo rows a brown camera icon (and a small camera next to a capture that has a photo),
optionally numbered S12, O13, R14. Only this session's captures are drawn; earlier refs
from `refs.csv` as grey rings when Settings > Survey > Show earlier refs is on.

### points.csv
One header line, then one row per capture, in capture order. UTF-8, comma separated, `.`
decimals; an empty field means not available. Text fields are quoted (RFC 4180) only when
they contain a comma or quote; a line break in a text field is written as a space, so each
row is one line. The app rewrites the whole file on each change
(e.g. when a photo is attached or a capture is flagged), so it is always complete; it is
not append-only. `events.csv` is the append-only record.

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
| `fix_age_s` | 0.6: seconds from the start of the RTK fixed stretch to the last averaged epoch (see False-fix guard); empty when not fixed |
| `fix_jump_m` | 0.6: the position jump at that stretch's start, m (3D); empty when not fixed or unknown |
| `fix_suspect` | 0.6: 1 if the stretch is suspect (a jump over the warning threshold; from 0.11.0 also right after a long float spell), else 0 |
| `method` | 0.6: `single` (one 1 s average), `ref` (a ref occupation) or `tilt` (a tilt capture); 0.13.0: `auto` (an auto capture: the same 1 s average as `single`, taken when the pole was held plumb and still); empty for photo rows |
| `tilt_n` | 0.6, tilt only: leans used in the solution |
| `tilt_rms_m` | 0.6, tilt only: RMS of the sphere residuals, m |
| `tilt_cover_deg` | 0.6, tilt only: azimuth coverage of the used leans, degrees |
| `tilt_sd_e_m`, `tilt_sd_n_m`, `tilt_sd_u_m` | 0.6, tilt only: standard deviation of the tip east, north, up, m |
| `void` | from 0.9.0: 1 if the capture is flagged as bad (see Flagged captures), else 0 |
| `void_reason` | from 0.9.0: why it was flagged, empty when not |
| `fix_start` | from 0.11.0: what came before the fix stretch: `float`, `single`, `none`, `fixed-jump` or `gap` (see False-fix guard); empty when not fixed |
| `float_spell_s` | from 0.11.0: seconds of float right before the stretch; empty unless `fix_start` is `float` |
| `suspect_reason` | from 0.11.0: `jump`, `float_spell` or `jump;float_spell`; empty when not suspect |
| `fix_young` | from 0.11.0: 1 if taken while the fix was younger than the young limit (a ref or tilt: any of its epochs or leans), 0 if not; empty when unknown (not fixed) |

The ten 0.6 columns come after `note`, the two 0.9.0 columns after those and the four
0.11.0 columns last, so a reader that takes the first 17 (27, 29) columns by position keeps
working; older rows simply lack the later columns (unknown, never 0), and a row without
`void` is not flagged. The session importer (`analysis/qgis/import_sessions.py`) carries
the 0.6, 0.9.0 and 0.11.0 columns into the gpkg `captures` table (NULL for older sessions), and soundings.py gives a tilt capture
the tilt-capture sigma. It does not read `tilt_obs.csv` yet.

Example (made-up position):
```
n,class,elapsed_ns,utc,lat,lon,h_ant_ellips,pole_m,h_ground_ellips,fix,n_epochs,sd_e_m,sd_n_m,sd_u_m,age_s,photo,note,fix_age_s,fix_jump_m,fix_suspect,method,tilt_n,tilt_rms_m,tilt_cover_deg,tilt_sd_e_m,tilt_sd_n_m,tilt_sd_u_m,void,void_reason,fix_start,float_spell_s,suspect_reason,fix_young
1,surface,81234567890123,2026-11-11T14:03:22.40Z,10.000000123,20.000000456,31.2345,1.800,29.4345,4,5,0.0021,0.0018,0.0040,1.0,,,42.5,0.180,0,single,,,,,,,0,,float,4.5,,1
2,ref,81290123456789,2026-11-11T14:04:31.00Z,10.000010000,20.000020000,31.3011,1.800,29.5011,4,30,0.0030,0.0026,0.0061,1.1,photo_2_20261111T140440Z.jpg,,151.0,0.180,0,ref,,,,,,,0,,float,4.5,,0
3,object,81390123456789,2026-11-11T14:06:12.25Z,10.000020000,20.000030000,31.0520,1.600,29.4791,4,24,0.0040,0.0035,0.0050,1.2,,,260.5,0.180,0,tilt,6,0.0071,250,0.0090,0.0110,0.0060,0,,float,4.5,,0
4,surface,81393123456789,2026-11-11T14:06:15.10Z,10.000020500,20.000030500,31.0610,1.600,29.4610,4,5,0.0022,0.0019,0.0041,1.0,,,263.4,0.180,0,single,,,,,,,1,accidental,float,4.5,,0
5,surface,81420123456789,2026-11-11T14:06:42.00Z,10.000021000,20.000031000,31.0702,1.600,29.4702,4,5,0.0023,0.0020,0.0043,1.0,,,12.0,0.452,1,single,,,,,,,0,,float,38.0,float_spell,1
```

Related events in `events.csv`: `capture <row>`, `capture refused class=<c>: no fix` (until
0.10 also `: fix settling N s`), `fix stretch start jump=…`, the tilt events (see Tilt capture),
`ref start n=… duration_s=… pole_height=… quality=…`, `ref paused …`, `ref resumed …`,
`ref end n=… epochs=… pauses=… young=0|1`, `ref cancelled …`, `pole check n=…: …` (see Refs
and the pole check), `photo file=…
attached to n=…` or `own row n=…`, `photo cancelled: …`; from 0.9.0 `flag n=…`, `unflag
n=…` and `photo moved n=…` (see Flagged captures).

### Refs and the pole check
Every ref is also appended to `refs.csv` in the storage root
(`Documents/IceCubeLogger/refs.csv`, or the app media folder's, like sessions): the
session folder name, then the points.csv columns. It is kept across sessions. At the end
of a ref the app checks its ground height against a reference formed from the earlier
visits to the same mark. This is the one reference rule (0.12.0, #23); other text points here:

- **Visits**: earlier refs in `refs.csv` within 0.30 m horizontally that have a ground
  height, are not flagged (`void` 1) and were not taken from a young fix (`fix_young` 1).
  An older row without these columns counts. A ref found in several `refs.csv` copies
  (storage roots) is one visit (session and n), and the most recent visit is the latest
  session, then the highest n. A visit that itself failed its check still
  counts (refs.csv does not store check results; the median handles one bad visit).
- **Reference**: 1 visit: that visit ("vs 1 earlier visit"). 2 visits: their mean.
  3 or more: the median of their ground heights (with an even count, the mean of the
  middle two), so one bad earlier visit does not fail the next good one.
- **Doubtful reference**: the visits that form it disagree by more than the tolerance:
  with an even count (a pair too) the middle two differ by more; with an odd count of 3
  or more, neither neighbour of the median (in height order) is within it (so one bad
  visit among three is not doubt, but 0, +5, +10 cm is). A failed check against a
  doubtful reference is amber, not red: no earlier visit can be called bad.
- **Check**: |new - reference| at most the tolerance (Settings, default 3 cm) passes. The
  difference to the most recent visit is shown as information when there are two or more
  visits ("; -2.0 cm vs last visit").
- **Off-centre cue**: the horizontal distance from the new ref to the median east/north
  position of the visits (to the one visit, with one) over the off-centre limit
  (default 2 cm): "pole may be off the mark centre (2.5 cm); re-shoot". It uses earlier
  visits only, so it can differ from an offset computed afterwards from all visits, and
  needs no ground height on the new ref.
- **Pole moved cue**: a held ref (Ref mode occupation) whose own `sd_u` is over the pole
  moved limit (default 1.5 cm): "pole moved during the ref (sd 3.1 cm); re-shoot". It
  compares with nothing, so it also shows on a first visit. Not for a tilt ref: its sd_u is
  the RMS of the per-lean scatters, which the tilt gates judge.

The notice:
- red, failed: "pole check: +4.2 cm vs median of 3 visits; -0.5 cm vs last visit", with
  the alarm sound and the same text, with any cue that may explain it, in the warnings
  bar (until the next ref). Usually the
  pole height setting is wrong, the pole sank, or the shot was bad.
- amber, a cue or a doubtful reference: a failed check against a doubtful pair reads
  "pole check: +4.2 cm vs mean of 2 visits that differ by 3.5 cm; shoot again" (neither
  earlier visit can be called bad; a median reads "vs median of 4 visits that disagree
  by 7.8 cm around it"); a passing one names the disagreement too. Amber plays
  the error buzz, not the alarm, and sets no warnings bar.
- green: "pole check OK (±0.8 cm vs median of 3 visits; +0.2 cm vs last visit)", or
  "vs 1 earlier visit", with two rising beeps.
- first visit (or no ground height): "first visit to this mark", no check (the pole
  moved cue can still make it amber; without a ground height the earlier visits within
  0.30 m still give the off-centre cue).

A young or suspect ref (see False-fix guard) shows its fix text in the same notice; the
more severe colour wins (red over amber over green). Cues are appended after the check
text, separated by "; ".

The `pole check n=…` event line: the notice text, then `diff_last_m`, `diff_mean_m`,
`visits`, `last` (session/n), `ok`, and from 0.12.0 `diff_ref_m`, `ref_kind`
(single|pair|median), `within_tol`, `doubtful`, `place_cm`, `sd_u_cm` and `colour`. A
line with no check (first visit, no ground height) has the notice text, then `place_cm`,
`sd_u_cm` and `colour`; `place_cm` and `sd_u_cm` are on every check and no-check line,
empty when not applicable (e.g. `place_cm= sd_u_cm=3.1 colour=amber`). A ref flagged
while its check ran logs `pole check n=…: result not shown (voided)` with no keys. `ok` is false only for a red, failed check (a doubtful reference
outside the tolerance logs `ok=true within_tol=false doubtful=true`), which is what the
QGIS importer colours red; until 0.11 `ok` was the check against the most recent visit.

From 0.6 its rows carry the ten new points.csv columns; the first time 0.6 appends to an
older `refs.csv` it rewrites the header line to the new one (rows unchanged), so the older
rows simply have empty values there. From 0.9.0 the rows also carry `void` and
`void_reason` (same header upgrade); flagging a ref rewrites its row in place (see
Flagged captures), and a row with `void` = 1 is never a visit. From 0.11.0 the rows carry
the four fix columns too (same header upgrade), and a row with `fix_young` = 1 is never a
visit either (an older row without the column is used as before).

`refs.csv` contains positions. It stays on the phone and in Drive; never commit it to
the public repo. An earlier install's `refs.csv` in Documents may not be readable after a
reinstall (Android only lets an app read files it created); the app then writes a new one
in the app media folder.

## Photos
PHOTO opens the phone's camera app (no camera permission needed) to take one photo into
the session's `photos/` folder, named `photo_<n>_<utc>.jpg` (UTC `yyyyMMddTHHmmssZ`).
If the most recent capture that is not flagged was taken at most 60 s before PHOTO was
pressed, the photo is attached to it (its `photo` column; `n` in the file name is that
capture's); otherwise it gets a row of its own, class `photo`, with the averaged position of that moment (empty
when there is no fix). Cancelled in the camera: the empty file is deleted and the
cancellation logged. Photos are drawn as a camera icon and are included in the shared zip.
The number in a file name is a unique, time-ordered label, not a link: after a flag moves a
photo (see Flagged captures) it no longer matches the row that holds it, and files are never
renamed (that would break `events.csv` lines, shares and the importer's lookup). The row
whose `photo` column lists the file is the link; the importer reads it so.

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
- Origin, the first of these that exists (layer files in name order, features in file order):
  1. the first point of a feature with `kind` "origin" in the switched-on layers (the
     project frame: for the Ice Cube yard, lot monument G, from `origin.geojson`);
  2. else the first point of a feature with `kind` "base";
  3. else the first GPS fix of the session;
  4. else (layers only, no fix yet) the first vertex of the first layer, until a fix arrives.
  The info box at the bottom right says which, with satellites and the ground height.
- Map frame: exact ENU about the origin (geodetic -> ECEF -> rotation at the origin, no
  flat-earth step): x east, y north, z up, metres. z is the height above the origin's
  ellipsoidal height less the earth-curvature drop (0.8 mm at 100 m), the same as the
  session importer's z; an origin point without `h` gives x and y only. The frame's name is
  the origin feature's `name` (`G`), else the base's name, else `first fix` / `first vertex`.
- Readout: the info box shows the current ground position (antenna minus pole) as
  `G x 12.345 y -3.210 z -0.452 (ground)`; Details shows the map frame's name and source
  and the same readout; capture notices add the capture's ground position. This is display
  only: `points.csv` keeps lat/lon/h (the importer computes x/y/z in the project frame).
- Live: the position dot is coloured by fix quality (red single, blue DGPS, amber float,
  green fixed, grey other; legend top left, with the capture classes and, while the
  coverage overlay is on, its two colours), with a 2-minute trail and this session's
  captures (see Captures), over the coverage overlay (see Coverage).
- Pinch to zoom, drag to pan (stops following), double tap or Center to follow the
  position again, Fit to show all layers, trail and captures. Scale bar bottom left.
  Labels show feature names and are skipped where they would overlap.

## Coverage
While surveying, the map shows where the surface still needs captures, so gaps are filled
in the field rather than found afterwards. It is a light, live version of icecube's node
estimator (`icecube/nodes.py`, see `docs/howto/ice-cube.md`), switched by MENU > Coverage
overlay and set in Settings > Coverage.

- Grid: nodes every grid spacing (default 0.5 m) in the map frame, anchored at the map
  origin.
- What feeds it: this session's Surface captures with RTK fixed (fix 4) and not
  `fix_suspect`, single, auto (0.13.0) or tilt, with a finite ground height, not flagged (void); not float, object, ref or photo rows. Earlier sessions
  are not loaded. Each capture is fed once as it arrives; the overlay is rebuilt from the
  session's captures when the map origin changes, a new session starts, or a Coverage
  setting changes.
- Node update (nodes.py's median-first rebuild, one pass, no pass bias): a capture at
  height z with sigmas sz, sh feeds every node within the capture radius, at distance d,
  with variance v = sz² + (0.05 (d + sh))² (slope allowance 0.05 m/m). On every feed the
  node is rebuilt from all its captures: the weighted median starts the estimate, the
  others join nearest first, and one that differs from the estimate by more than 3 sigma
  of the difference goes to an outlier bin instead of pulling it. A bin with more captures
  than the main estimate takes over. The node's height and sigma (sqrt(1/Σ(1/v))) are the
  main estimate's. Arrival order does not matter (up to 32 captures a node; past that the
  oldest agreeing ones are merged into one running sum, and a node with a long run of
  outliers keeps its height and turns red instead).
- Plane pass (from 0.10.0, issue #29; nodes.py's `plane_pass`): on a steep bank a node
  holds more relief inside its capture radius than the 0.05 m/m allowance, so its up- and
  down-slope captures split into bins and it stays red. A node red after the rebuild above
  is tried again: a plane is fitted through its 8 neighbours' estimates (each where its
  captures actually sit, its main estimate's weighted centroid; red neighbours at a
  quarter weight; at least 5 usable, 2 of them not red), and if the plane is flat enough to
  trust (residuals within 3 sigma, the gradient pinned to 0.08 m/m) and steeper than the
  allowance by 3 sigma, every capture is moved to the node centre along it and the node
  rebuilt. The result replaces the first one only if the node leaves red (or has fewer
  outliers) and its height agrees with the plane at the node; otherwise the first stands.
  There is no DEM on the phone, so the batch's DEM gate is skipped and the allowance is
  the constant 0.05 m/m everywhere. A node's result depends only on its own captures and
  its 8 neighbours', so after each capture the pass is redone for the nodes the capture
  fed and every node within one grid step of them (about 25 nodes at the defaults, up to
  26), a few times the work of the rebuild alone (a 5000-capture bank rebuilds about 2x
  slower with it on the desktop JVM, about 44 µs a capture). The overlay draws a node by
  its final values: one cleared by the plane is clear or amber like any other (nothing
  new to capture there); its sigma includes the plane's gradient uncertainty.
  - Memory: each stored capture also keeps its offset from the node, sz² and sh (four
    more numbers, and four running sums for the merged ones), about twice the store: a node
    at the 32-capture limit is about 2.4 kB instead of 1.2 kB, so the full 250,000-node
    grid at that limit would be about 600 MB (it was about 290 MB; no session comes near:
    fringe nodes store nothing and most nodes hold a few captures). Two logged sessions
    replayed at the defaults (1,000-1,600 nodes with captures) went from about 0.5-0.7 MB
    to 0.9-1.3 MB.
- Area: nodes within reach (default 2 m) of any capture. Only those are drawn.
- Colours (from the final values, after the plane pass): **red** where the outliers are
  not outvoted 2:1 (outliers > 0 and agreeing captures < 2 × outliers), or where an
  in-area node has no capture within the capture radius but captures on all four sides (a
  gap between passes wider than about 1.5 m at the defaults shows as a red strip);
  **amber** where the node sigma, with the pass wander
  added (sqrt(sigma² + wander²)), is above the target; **clear** at or below it; a faint
  **grey** for the edge of the walked area with no capture yet (reach minus capture radius,
  1.25 m at the defaults: the survey ends there). All are translucent so the layers show
  through.
- With few captures: one capture is amber or clear by its sigma; two that disagree are red
  (1:1, and the lower one is the height); three with one outlier are not red (2:1), and
  agreeing captures clear a red node. Two bad captures that agree outvote one good one:
  the node is clear at the wrong height (young fixes are issue #15's job). Because the
  node follows the set of captures, a new capture can change which ones agree, so a node
  can turn red, clear and red again as captures arrive.
- Per-capture sigmas: the budget of `analysis/qgis/soundings.py` (`sigma_terms`, the same
  constants), core/SoundingSigma:
  sz² = rtk² + age² + settle² + pole² + ground², with rtk = max(1 cm, `sd_u_m`), age =
  1 mm/s beyond 2 s of correction age, settle = 10 cm (1 - fix_age/120 s) for a capture
  taken within 120 s of its fix starting (90 s until 0.10; 8 cm (1 - fix_age/120 s)² before
  the #20 re-fit), pole = pole_m (1 - cos 1°) + 1 cm tip sink,
  ground the Coverage setting; sh = hypot(max(1 cm, hypot(`sd_e_m`, `sd_n_m`)/√2),
  pole_m sin 1°). A single capture takes a 1° bubble tilt, as soundings.py does. A tilt
  capture takes its own `tilt_sd_u_m` and hypot(`tilt_sd_e_m`, `tilt_sd_n_m`)/√2 with
  tilt 0 (the solution already places the tip), keeping tip sink and ground; one missing
  any tilt sd takes the single rule. soundings.py applies the same rule
  (`capture_sigma_inputs`).
  - Differences from soundings.py: no GST input (the logger does not parse GST, so a
    capture whose GST sigma exceeds its 1 s scatter gets a smaller sigma here); the fix
    age is the logger's own count from the GGA stream, while soundings.py recomputes it
    from `gps.ubx`.
- Example at the defaults: one lawn shot (sz about 2.1 cm, sh about 3.3 cm) clears nodes
  within about 0.28 m of it; a node 0.5 m away stays amber. A node needs several
  overlapping captures to clear.
- Still optimistic against a full `icecube run`: the live view has no crossover (pass
  bias) adjustment, only the fixed pass-wander term. Node positions match a run only
  when the run's grid origin is a multiple of the grid spacing.
- Limit: 250,000 nodes (about 6 ha at 0.5 m). Past it no new nodes are added (existing
  ones still update) and the legend reads `coverage: area limit`.
- Drawing: one pixel per node, at most 2048 nodes a side (1 km at 0.5 m, about 205 m at
  0.1 m). A larger area is drawn in a 2048-node window that holds the newest captures: it
  stays put while they are in it and re-centres on them when they leave it, and every
  move repaints the whole window, so nothing outside the window shows and nothing inside
  it is left unpainted.
- Work: the feeding, rebuilding and painting run in order on one background thread
  (`coverage`), which hands finished frames to the map; a rebuild (new session, origin or
  setting) cancels the one in progress. The map hides the overlay from the start of a
  rebuild until its first frame, usually a moment. If that thread hits an error, the
  overlay stops (no more frames), the legend reads `coverage stopped: error` and the error
  is logged (logcat, and the session's events as `coverage stopped: ...`); logging and the
  rest of the app carry on. It stays stopped until the map screen is created again (for
  example after the app is swiped away from the recent apps and opened again).
- Replay: each rebuild with settings or an origin not yet logged for the session writes
  `coverage cell=… radius=… reach=… target=… wander=… ground=… slope=… k_sigma=… cap=…
  outvote=… plane=1 plane_min_neighbours=… plane_min_nin=… plane_min_clear=…
  plane_red_weight=… plane_planarity=… plane_max_sigma_g=… origin_lat=… origin_lon=…
  origin_h=… origin_source="…"` to `events.csv` (the plane settings from 0.10.0), so the
  overlay can be replayed exactly from the session files.

The Java ports are checked against icecube: `python3 -m icecube.logger_vectors --out
logger/app/src/test/resources/coverage_vectors.json` writes test vectors from
`nodes.NodeStore` (through a Python twin of Coverage with its area and status rules),
`nodes.plane_pass` and `soundings.sigma_terms`, and the JVM tests (`CoverageTest`,
`PlanePassTest`, `SoundingSigmaTest`) compare against them, node by node and status by
status after each capture; regenerate when either changes. The plane parts are
`plane_vectors` (9 blocks below the 32-capture limit), `plane_folded_vectors` (2 banks
past it, with merges and drops) and `plane_sequences` (a bank captured one at a time,
the expected status after each capture from the plane pass redone over the whole grid,
so the ring rule is checked, not copied). The drawing
window and the background thread's order and cancellation have their own JVM tests
(`CoverageRasterTest`, `CoverageWorkerTest`).

## Map layers
Layer files are `*.geojson` or `*.json` in `Documents/IceCubeLogger/layers/` (or the app
media folder's `layers/`, same as sessions). MENU > Layers switches them on and off. Android only lets the app read files it
wrote there itself, so import them: MENU > Layers > Import… (also in the "No layer files"
dialog) or Settings > Map > Import layer files…, the same flow. It opens the system file
picker (Google Drive, Downloads, …; where the picker allows it, in the folder last imported
from), checks each file and copies it into `layers/`. A file whose name is already plain
(letters, digits, `.`, `_`, `-`, ending in `.geojson` or `.json`) keeps it, and importing it
again replaces the earlier copy. Any other name is made plain (other characters become `_`,
`.geojson` is added if missing) and gets a short code from the original name, e.g.
`a b.geojson` becomes `a_b-1a2b3c4d.geojson`, so it never replaces a different file's
layer. A file with nothing before the extension (`.geojson`), or whose name differs from a
layer already there only in upper/lower case, is not imported and the report says why.
Each copy is written to a temporary file and renamed over the old one only when complete,
so a failed import leaves the earlier layer as it was. A report lists each file with its
feature count, skipped features or the reason it was not imported. From MENU > Layers the new layers are drawn at once and the Layers dialog comes
back when the report is closed. A bad file shows its error in Settings and is skipped.

Format: a GeoJSON FeatureCollection (RFC 7946; a single Feature or a bare geometry also
works). Coordinates are WGS84 degrees `[lon, lat]` or `[lon, lat, h]`, h ellipsoidal metres
(longitude first). Geometries: Point, MultiPoint, LineString, MultiLineString, Polygon
(first ring outer, the rest holes), MultiPolygon. GeometryCollection is not supported.
A feature with a missing, empty or invalid geometry is skipped and counted.

Properties, all optional:

| property | meaning |
|---|---|
| `name` | label drawn next to the feature |
| `kind` | free text, e.g. `boundary`, `base`, `rink`, `survey`. `origin` (any case) on a Point sets the map origin and frame (see Map; give it `name` and `h`). `base` (any case) on a Point is used for the base check below and is the map origin when no `origin` point is on; give it `h` |
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
session folder (with `photos/`) to the app cache as `IceCubeLogger_<session>.zip` and opens
the Android share sheet with an `application/zip`; pick Google Drive. The share subject and
title, and the provider's display name, are all that file name, so Drive keeps the `.zip`.
The zip holds `<session>/...` and, at its top level next to the session folder, a copy of
`refs.csv` from the storage root (the session's own root, else the first one found; left out
if there is none). JPEG photos (and other already-compressed files) are stored as they are;
logs and text are deflated. From 0.9.0 every entry carries its file's modification time
in UTC as a zip extended timestamp (extra field 0x5455) besides the DOS time, which is the
phone's local time; the importer orders `refs.csv` copies from zips made in other time
zones by it. For an older zip it finds the phone's zone offset from the session's
`events.csv` (its last line's `wall_ms` against its entry time, rounded to 15 min, taken
only when what is left is -2 s to +10 s, the DOS time being truncated to 2 s; an `events.csv` time reset by a copy tool that still
lands that close to a 15 min step gives a wrong offset, which the file cannot show) and
allows 1 h for a DST change between an entry and `events.csv`; without an offset it allows
26 h. Copies whose times, with those allowances, may be in either order are a tie (flagged
wins, named in the report). The importer keeps every copy it has seen (`refs_copies` in the
gpkg) and decides each ref from all of them, so importing the zips together or one at a
time, in any order, gives the same flag.
If the session is still recording the app asks:
Stop and share (stops the session, then zips the complete files), or Share snapshot (keeps
recording; flushes the files and zips each one, `refs.csv` included, up to its length at that
moment, plus a `SNAPSHOT.txt` saying so, in `IceCubeLogger_<session>_snapshot.zip`; the last
chunk of a log may be cut and there is no stop event).
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
- `tilt_obs.csv`: every lean of every tilt capture (see tilt_obs.csv), when there was one
- `points.csv`: the captures (see points.csv); written from the start, header only until
  the first capture
- `photos/`: the session's photos (may carry EXIF GPS)

Outside the session folders, `refs.csv` in the storage root collects every ref (see Refs
and the pole check).

`elapsed_ns` is `SystemClock.elapsedRealtimeNanos()`. Offsets keep counting across link
drops. The app changes receiver settings in RAM only, and only when Configure is on.
A power cycle restores the receiver's saved settings.
