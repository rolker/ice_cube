# Log

A dated journal of the project, a line or a few per step. Public-safe: offsets, sigmas
and percentages only; no absolute coordinates. Times are UTC unless marked local (EST/EDT).
Reference material is in `howto/`, the narrative in `story/`.

## 2026-01 to 2026-03: last season (summary)

- 2026-01-09: first survey of the rink, an outline of its edge: about 27 cm of height
  variation. Rover: ZED-F9P with SW Maps, corrected by the state DOT's CORS network.
- Mid-February: edge variation down to about 13 cm, interior points spanning about 12 cm.
  Gridding by an adapted CUBE, named Ice-Cube (see `story/`).
- 2026-03-02: a last survey, not analysed until November.
- The rink: an irregular two-lobed shape, about 68 m², over about 31 cm of ground relief.

## 2026-10

- 10-18: yard survey, 149 ground and 29 feature points, median spacing 1.2 m over about
  440 m². First plan: a 98 m² liner between the contours, 97 m² of ice, about 13.5 m³.
- 10-26: second survey, 159 shots, NTRIP dropping out. New shots up to 18 cm above the
  first surface, in ±10 cm "bumps". Ordered an LG290P breakout, a multi-band antenna and
  a ground plate for a base station.
- 10-26 evening (EDT): 40-minute test on the deck-corner mark, cycling single-station
  mountpoints. The nearest station (about 25 km) drifted 8 cm in 10 minutes while
  reporting 1 cm; switching stations moved the height by lasting offsets of 6–10 cm;
  range across all stations 18–19 cm. Written up in `howto/rtk-test-2026-10-26.md`.
- Late October: diagnosis of the surveys. No whole-day offset; within-day stretches 6 cm
  low and 7.5 cm high; reported accuracy 1 cm on every shot. Removing an estimated drift
  cut the shot disagreement from 9.3 to 2.5 cm. Plan after cleanup: between the shed and
  the camper, about 11 cm of ground relief inside the liner.

## 2026-11

- 11-02: found the 4 Hz continuous log of the Oct 26 test and re-did the numbers from it
  (the 4 Hz figures are now the ones in the write-up). Antenna up on the deck's northeast
  corner on a temporary mount. Started `gnss_base`, a Home Assistant app for the base
  receiver; stage 1 (read-only logging, status page, 0.2.2) installed that night after
  three review rounds. First draft of the October write-up.
- 11-03 12:17: 0.3.0 (receiver commands, profile check, guided base setup) running.
  12:22: base mode. 12:34–14:04: 90-minute survey-in, claimed 1.2 cm; the 10-minute means
  had wandered 1.3 m. 14:29: fixed mode at the survey mean.
- 11-03: frame decision: the base is ITRF2020 at the epoch of observation; all earlier data
  is the network's NAD83(2011) epoch 2010.00. Published shift at the yard roughly E −0.56,
  N +1.19, U −1.18 m (ours minus network).
- 11-03: RINEX conversion tested (RTKLIB `convbin`, 3.04, MSM4); `rtkrcv` single-point
  spike on a PC: fine, about 3 % of a core.
- 11-04 00:06: 0.4.0 (local NTRIP caster, push to RTK2go) running. The phone could not
  reach anything on the home network, so the rover used the public caster over cellular;
  stream up at 01:00. First rover test: fixed solutions scatter under 1 cm, 65 % fixed
  outdoors, no false fixes indoors; dropouts were on the caster-to-phone side. The same
  deck spot on both correction sources implied the survey-in was about 1.3 m too high.
- 11-04 02:59: first PPP coordinate applied (CSRS-PPP, 11.6 h, ultra-rapid products):
  the survey-in had been N +0.18, U +1.51 m off. Broadcast 1005 matched the new value to
  0.1 mm. OPUS on the same file agreed within 10 cm. Rover data from before 02:59 is
  about 1.5 m high.
- 11-04: 0.4.1 (rover positions logged) and 0.5.0 (live single-point position from
  `rtkrcv` on a copy of the stream) installed. Antenna confirmed as an L1/L2 patch with
  no L5 and no IGS calibration; PPP value used at the phase centre, 9 mm above the ARP.
- 11-04 20:41: rapid-product PPP applied: E −0.6, N −0.5, U +3.3 cm from the ultra-rapid
  one; 95 % sigmas N 1.0, E 0.6, U 3.0 cm. Rover data between 02:59 and 20:41 is about
  3 cm low.
- 11-04 15:49–16:53 EST: walked the yard with the rover on the home base, 360 points,
  98 % fixed. First full 24 h day of raw base data closes at 00:00 on 11-05.
- 11-04: 0.5.1 built (shows the shift before a fixed-mode change; knows when the receiver
  is already in fixed mode). Installed around 01:39 on 11-05.
- 11-05: post-processing of the walk against the base log: real-time fixed heights agree
  to about 3 cm once a fix is more than 90 s old; the first 1–2 minutes after a re-fix
  from a long float can be 10–30 cm off (9 points dropped); two 30 s correction outages
  biased height about 3 cm low. Only 35 % of epochs fixed in post-processing because
  Bluetooth lost 11 % of raw epochs. The Nov 3 log on both sources matched the published
  frame shift within 5 cm. Deck repeat spot adopted as the session check point.
- 11-05: rink placement check with the walk: confirms the October spot between the shed
  and the camper, about 10 cm of relief over 96 m², 13–15 m³ for 10 cm minimum depth.
  Nov heights are 7–8 cm below October's at the same spots; October drift suspected.
- 11-05: siting analysis in a separate cloud session (merged 11-07 as `analysis/` and
  `logger/`), findings as of that day:
  - Frame offset measured at the deck corner, network minus home base: E +0.476,
    N −1.224, U +1.174 m; ITRF2014 to NAD83(2011) predicts +0.56, −1.20, +1.18.
  - Recommended rink: 6 × 11 m, long axis about 334° true, about 3.5 m west of last
    year's, 2 m from both sheds. From the Nov 4 data: 5.3 m³ fill, deepest 12 cm, relief
    7 cm, against 6.9 m³, 24 cm and 18 cm for last season's footprint.
  - Use the home-base data for siting. Network sessions wander: repeat differences grow
    from 1–3 cm within 3 minutes to 7–8 cm after 25 minutes; Nov 4 holds 3–4 cm at all gaps.
  - Oct 26 static test at the deck corner, heights relative to the "near" mountpoint:
    masa −2 to +3 cm, matb +6 cm, mawr −12 cm, mafa −9 cm, an 18 cm spread. October
    ground reads about 10 cm above November; the mountpoint is the likely cause, unconfirmed.
  - Last season's 12.5 cm mean accretion and southeast slope are within the wander.
  - Float only by the house near the porch, not in the rink area (99.5 % fixed on the
    recommended footprint).
  - Bluetooth logging via SW Maps lost 0.8 % of 4 Hz positions and 10.6 % of raw epochs.
  - Still-pole detection (2 s window, 3 cm horizontal, 1.2 cm height SD, RTK fixed)
    matched 353 of 354 saved points with 0.7 cm rms height agreement.
  - Open: an independent static height (OPUS rapid static); test the USB logger app
    against Bluetooth; the antenna move (roof or mast) deferred to next year.
- 11-05: combined RINEX files built from three days of raw logs (36 h and 60 h, 30 s
  epochs, no gaps over 60 s).
- 11-06 01:56: 36 h PPP back, but with ultra-rapid products because the file touched the
  next UTC day: N +0.3, E +0.5, U −1.8 cm from the receiver's coordinate. Trimmed files
  resubmitted; 36 h with rapid products: N +0.2, E +0.6, U +0.7 cm (95 % N 0.6, E 0.4,
  U 2.0 cm). Not applied.
- 11-06: design draft for a continuous reference check (a second `rtkrcv` in RTK mode
  against a CORS stream). The demo5 fork (RTKLIB-EX 2.5.1) built for the spike.
- 11-06 19:42 EST: 60 h PPP with rapid products all three days: N +0.3, E +0.7, U +0.3 cm
  from the receiver's coordinate (95 % N 0.5, E 0.3, U 1.6 cm); 0.1/0.1/−0.3 cm from the
  36 h result.
- 11-07 01:02: 60 h coordinate applied and verified (command, read-back and 1005 agree to
  0.3 mm). Final products expected about 11-19.
- 11-07 01:06–02:06: reference-check spike on a 25 km CORS baseline: demo5 static,
  GLONASS AR off, fixes in 1.8 minutes and holds fixed 81 % of the hour; stock 2.4.3
  fixed for 65 s. Fixed offset (network minus ours) E +0.53, N −1.15, U +1.20 m against
  the expected +0.56, −1.20, +1.18: residual E −3, N +5, U +2 cm, the same in every build
  and run, so it is in the inputs. A 3 h run follows.
- 11-07: repo restructured into tools and story; the siting branch merged; `docs/howto/`,
  `docs/story/` (outlines) and this log added.
