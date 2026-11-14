# Surveying the yard, October 2026

**Outline, not written.** Built from the author's own draft of 2026-11-02, so nearly all
facts are `[own words]`; `[reconstructed]` marks the few added from later analysis.
The numbers live in `../log.md` and `../howto/rtk-test-2026-10-26.md`.

## Facts

### Why survey a backyard
- Last winter's rink was improvised and "a lot of work because I hadn't planned any of
  it." This year: plan before the ground froze. Where the rink goes, what shape, how much
  water, how tall the boards. All of that comes down to the shape of the ground. `[own words]`
- Same rover, app and correction service as last season; analysis in a Claude chat from
  the SW Maps exports. `[own words]`

### The first survey, Oct 18
- 149 ground points and 29 feature points (shed, camper, swing set, deck, stone wall,
  trees), median spacing about 1.2 m over roughly 440 m². Two near-duplicate pairs agreed
  within 2 cm. `[own words]`
- Deck step came out 5 cm above last season's deck reference, shot 18 cm away; that
  agreement "looked reassuring but proved little." `[own words]`
- Usable area: a flat patch in the west and northwest. East of the deck and camper the
  ground drops about 1.5 m to a stone wall. `[own words]`
- Target 25 % more ice than last year's 68 m². Water in a liner settles level, so the
  volume is set by how far the ground drops below the ice top; a shape that follows the
  contours wins. Finger-sketches on a phone screen. Plan: 98 m² liner, 97 m² of ice,
  about 13.5 m³. `[own words]`
- Liner vs another snow rink: a strong El Niño outlook, which in southern NH tends to mean
  thaws and rain. Liner and boards, $300–600 in materials. `[own words]`

### What seemed wrong, Oct 26
- Moved the swing set between surveys. 159 more shots; NTRIP kept disconnecting. `[own words]`
- New shots up to 18 cm above the first surface, with "bumps" of ±10 cm every couple of
  metres. The plan's water went from 13.5 to about 22 m³. `[own words]`

### Diagnosing the drift
- No whole-day offset (about 2 cm, at the noise). Instead, stretches within each day:
  one run 6 cm low, two runs 7.5 cm high, over the same central patch. "So it was the
  measurements, not the lawn." `[own words]`
- The CSV export has time, fix type, accuracy, DOPs and satellite counts; the GeoJSON does
  not. 304 of 308 shots RTK fixed; reported vertical accuracy about 1 cm on every shot,
  including the bad ones. "The errors followed the clock, not the ground." Averaging longer
  did not help. `[own words]`
- Removing an estimated per-day drift cut the disagreement from 9.3 cm to 2.5 cm. "The
  lumpy lawn was mostly drift." Each cleanup moved the plan (table in the draft: 13.5 →
  14.4 → 11.4 → 9.9 m³). `[own words]`
- Likely cause: the network solution is extrapolating; every station is south, the
  nearest about 25 km away. `[own words]`

### The mountpoint test, evening of Oct 26
- Pole on the deck-corner mark, cycled through single-station mountpoints for 40 minutes.
  Even the nearest station wandered 8 cm within its first run; each switch moved the
  height to a new level that lasted. `[own words]`
- Found on Nov 2 that SW Maps had been logging continuously at 4 Hz. It changed three
  things: the two "pinned" runs on the same station differed by 5 cm 17 minutes apart;
  the jumps were lasting offsets, not transients; and the receiver kept reporting fixed
  for 5–20 s on stale corrections after each switch. `[own words]`
- "The receiver's error estimate is honest about noise and blind to bias." `[own words]`

### What it means at backyard scale
- "A rink doesn't care where it is on the planet. It cares whether one corner is higher
  than another, and whether this week's surface matches last week's." `[own words]`
- Signal: about 13 cm of relief. Noise: 8 cm of drift plus offsets between stations.
  "The measurement error is more than half the signal." `[own words]`
- Workarounds exist (check marks every few minutes, shoot twice, one source, wait after
  reconnects) but are laborious. A laser level may still be used on flood day. `[own words]`

### Building a base station
- Turnkey option backordered, so a breakout board, antenna and ground plate were ordered
  on Oct 26 (about $307). `[own words]`
- An earlier AI session had drafted a relay add-on: "That was its plan, not mine." What was
  wanted: a box that reads everything the receiver says, keeps all of it, and sends the
  rover only what it needs. `gnss_base` started on Nov 2. `[own words]`
- Nov 2: antenna up on the deck's northeast corner, a threaded rod through the ground plate
  tied to a rail post, temporary. 32 satellites on four constellations. `[own words]`
- Later: the Nov 4 survey on the home base confirmed the October spot and found October's
  heights 7–8 cm high, consistent with the drift. `[reconstructed]`

## Prompts

- The moment of looking at the "lumpy lawn" surface: what did you believe first?
- How did finger-sketching on the phone work, and what did the final shape look like?
- The CSV discovery: what made you look at the export format?
- What does a backyard look like when you have a cm-level map of it? Anything surprising
  besides the drift?

## Pointers

- `../howto/rtk-test-2026-10-26.md` for the test numbers.
- `../log.md` for dates.
