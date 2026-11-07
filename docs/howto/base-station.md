# RTK base station

Reference for the base in the yard, as of 2026-11-07. The journal is in `../log.md`; the
story is in `../story/`. The app itself is documented in `../../ha-addon/gnss_base/README.md`.

## Why a base

Surveys on the state CORS network showed heights drifting by about 8 cm over minutes while
the rover reported RTK fixed with about 1 cm accuracy, and switching reference stations
produced lasting offsets of several centimetres (`rtk-test-2026-10-26.md`). The yard is
outside the network's edge, about 25 km from the nearest station. A base in the yard
shortens the baseline to tens of metres, so those errors mostly cancel.

What the rink needs is surfaces that are comparable from survey to survey, not absolute
accuracy. An error in the base coordinate shifts every rover point by the same amount and
cancels when surveys are differenced. Moving the antenna or changing the coordinate does
not cancel. So: a mount that does not move, and one coordinate kept for the whole season.

## Hardware

- Receiver: a Quectel LG290P quad-band module on a breakout board, read over its USB serial
  bridge (the bridge shows two ports; the receiver's UART1 is the first).
- Antenna: a multi-band L1/L2 magnetic-mount patch antenna on a ground plate. It receives
  L1 and L2/E5b/B2I, not L5, and has no published calibration. Temporary mount on a deck
  rail post, with a coax extension to the house. A permanent mount is deferred to next year.
- Host: a small ARM single-board computer running Home Assistant OS, with the receiver on USB.
- Rover: a u-blox ZED-F9P receiver (SparkFun RTK Express) with SW Maps on a phone.

## What the app does

`ha-addon/gnss_base` is a Home Assistant app, built in stages:

1. Read-only: logs every byte from the receiver to a daily compressed file, and shows
   fix, signals, storage and a drift plot on a sidebar page.
2. Commands: sends commands to the receiver with confirm and block rules, checks it against
   a receiver profile (never applied automatically), decodes RTCM 1005/1006 and MSM so the
   page works in base mode, and walks through firmware, base mode, survey-in and fixed mode.
3. Corrections out: a small NTRIP caster on the local network, and a push to a public
   caster (RTK2go) for a rover on cellular data. Rover positions reaching the caster are
   logged. A copy of the stream feeds RTKLIB's `rtkrcv` for a live single-point position.
4. (Designed, not built) A reference check: a second `rtkrcv` in RTK mode against a CORS
   stream, showing the base antenna's offset from its configured position all day.

It never writes to the receiver except through the command path, and credentials for the
public caster are options, never in the repo.

## The coordinate: survey-in, then PPP, then fixed mode

1. **Base mode and survey-in.** A 90-minute survey-in claimed 1.2 cm accuracy. It was
   about 1.5 m too high and 0.2 m north: a survey-in is a single-point average, and the
   10-minute means wander by metres. Good enough to serve corrections, not for a coordinate.
2. **Fixed mode at the survey mean**, so the receiver stops re-surveying on every restart.
   In fixed mode GGA reports quality 7 (manual input), which is the configured position,
   not a measurement; the app treats it as such.
3. **PPP.** The daily raw logs (RTCM MSM4, 1 Hz) are converted to RINEX 3.04 at 30 s
   with RTKLIB `convbin`, with the antenna type and marker in the header, and submitted to
   CSRS-PPP in static mode. The frame is ITRF2020 at the epoch of observation. Results so
   far, each applied to the receiver through the app's fixed-mode step:
   - 11.6 h, ultra-rapid products: moved the position by the 1.5 m above.
   - Same day, rapid products: a further 3 cm, mostly up.
   - 36 h and then 60 h combined files, rapid products: within 1 cm of each other and of
     the rapid single day (95 % sigmas about 0.5 cm horizontal, 1.6 cm up). The 60 h
     coordinate is the one in the receiver (2026-11-07). Final products arrive about two
     weeks after the GPS week; one more submission then, and leave it alone.
   - OPUS on the same file agreed with CSRS within 10 cm (OPUS reports NAD83).
   - Trim the RINEX so the last epoch stays inside the UTC day, or CSRS falls back to
     ultra-rapid products for the whole file.
   - The antenna has no IGS calibration, so the PPP result is at the phase centre, about
     9 mm above the antenna reference point. Nothing downstream applies an offset, so the
     coordinate is used as is. Record which, with the frame and epoch, next to every
     coordinate.

Every coordinate that goes in the receiver is logged by the app (`commands.jsonl`), and
the RTCM 1005 in the raw log is checked against it after each change.

## Frames

- Our base: ITRF2020 at the epoch of observation; refresh yearly by PPP or plate velocity.
- Everything shot on the state network before November 2026 (last season, October): the
  network's NAD83(2011) frame, epoch 2010.00.
- The published transformation between them at the yard is roughly E −0.56, N +1.19,
  U −1.18 m (ours minus network). Measured on the same spot with both corrections:
  within 5 cm of that. The siting scripts carry the measured value in their config.
- Data shot on our base before the first PPP coordinate (2026-11-04 02:59 UTC) is about
  1.5 m high and 0.2 m north; between then and 20:41 UTC that day, about 3 cm low.

## What the rover sees

Measured on the deck-corner repeat spot and a 360-point walk of the yard:

- Fixed solutions scatter under 1 cm; 98 % fixed in the open, float under trees and by
  the house, no false fixes indoors.
- Post-processed against the base's raw log, real-time fixed heights agree to about 3 cm
  once a fix is more than 90 s old. The first 1–2 minutes after a re-fix from a long float
  can be 10–30 cm off: drop those points.
- Corrections over the public caster and a phone drop out for about 30 s now and then;
  stale corrections bias height a few centimetres low.
- SW Maps over Bluetooth loses about 11 % of raw (RAWX) epochs. Log raw data on the
  receiver's SD card, or over USB (`logger/`), for post-processing.

## The reference check (next)

The idea: run a second `rtkrcv` inside the app with our receiver as the rover and a CORS
station's stream as the base, and show the resulting offset from our configured position
continuously. An offline spike on a 25 km baseline found that the demo5 fork (RTKLIB-EX)
in static mode with GLONASS AR off fixes in about 2 minutes and holds fixed 81 % of an
hour, where stock RTKLIB 2.4.3 fixed for about a minute. The fixed offset matched the
published frame transformation to within E −3, N +5, U +2 cm, and the residual was the
same in every build and run, so it is in the inputs (antenna models, station coordinate)
rather than the processing. Design in progress; it needs demo5 built for arm64 in the
add-on image.

## Survey practice

- Export CSV from SW Maps: it has time, fix type, accuracy, DOP and satellites for each
  shot. GeoJSON drops all of that. Keep the SWMZ as a backup.
- Re-shoot a fixed mark with a consistent `CHECK` remark at the start, middle and end.
- After any reconnect or re-fix, wait 90 s before trusting a fixed solution.
- Use one correction source for every survey that will be differenced.

## Sources

- [SparkFun LG290P product page](https://www.sparkfun.com/sparkfun-quadband-gnss-rtk-breakout-lg290p-qwiic.html)
- [SparkFun forum: LG290P base station build](https://community.sparkfun.com/t/question-about-lg290p-rtk-breakout-base-station-build/68706)
- [SparkFun PQTM command notes](https://docs.sparkfun.com/SparkFun_RTK_Postcard/pqmt_commands/)
- [Quectel forum: survey-in workflow](https://forums.quectel.com/t/lg290p-correct-workflow-to-survey-in/42964)
- [Quectel forum: base mode behavior](https://forums.quectel.com/t/lg290p-questions-about-its-behaviour-in-base-station-mode/38843)
- [CSRS-PPP](https://webapp.csrs-scrs.nrcan-rncan.gc.ca/geod/tools-outils/ppp.php)
- [RTK2go](http://rtk2go.com/)
