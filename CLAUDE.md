# Ice Cube

Backyard ice rink in Epping, NH, tracked with a SparkFun RTK Express (ZED-F9P),
SW Maps, a home RTK base and MassDOT MaCORS. This repository is public: never
commit survey data, logs, absolute coordinates, or generated site-plan pages.
Private values live in `analysis/site_config.json` (git-ignored).

## Layout
- `analysis/`: Python siting and survey-quality scripts. See `analysis/README.md`.
- `logger/`: Ice Cube Logger, an Android (Java) app that logs the GPS over USB with
  its own NTRIP client and phone IMU data. Version 0.1, not yet run on a phone.

## Frames
- Home base after PPP to MassDOT, measured at the deck corner: +0.476 E, −1.224 N,
  +1.174 up. ITRF2014 to NAD83(2011) predicts +0.56, −1.20, +1.18.
- Oct 2026 and last winter's surveys are MassDOT; Nov 3–4 2026 are home base.
- Analysis grid: local metres from the private origin, MassDOT frame.

## Findings as of Nov 5, 2026
- Recommended rink: 6 × 11 m centred at local (50, 60.5), long axis about 334° true,
  2 m from both sheds. From Nov 4 data: 5.3 m³ fill, deepest 12 cm, relief 7 cm,
  against 6.9 m³, 24 cm and 18 cm for last season's footprint.
- Use Nov 4 (home base) for siting. MassDOT sessions wander: repeat differences grow
  from 1–3 cm within 3 min to 7–8 cm after 25 min. Nov 4 holds 3–4 cm at all gaps.
- Oct 26 static test, deck corner: MassDOT mount points spread 18 cm vertically
  (near 16.68, masa 16.66–16.71, matb 16.74, mawr 16.56, mafa 16.59 m). Oct ground
  reads about 10 cm above Nov; the mount point is the likely cause, unconfirmed.
- Last season's 12.5 cm mean accretion and southeast slope are within the wander.
- Float is at the house near the porch, not in the rink area (option A 99.5% fixed).
- Bluetooth logging via SW Maps lost 0.8% of 4 Hz positions and 10.6% of RAWX epochs.
- Still-pole detection (2 s window, 3 cm horizontal, 1.2 cm height SD, RTK fixed)
  matched 353 of 354 clicks, with 0.7 cm rms height agreement.

## Open items
- Run OPUS Rapid Static on a static deck-corner log for an independent height.
- Test the logger app over the GPS USB port and compare RAWX loss with Bluetooth.
- Base antenna move (roof or mast) is deferred to next year.
