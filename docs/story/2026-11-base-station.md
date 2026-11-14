# The base station, November 2026

**Outline, not written.** Almost everything here is `[reconstructed]` from the working
notes and journal, so check it before writing it as memory. Numbers are in `../log.md`
and `../howto/base-station.md`.

## Facts

### The app, built in stages
- `gnss_base`, a Home Assistant app, replaced the drafted relay. Stage 1 (read-only
  logging and a status page) was installed on the night of Nov 2. `[reconstructed]`
- Each stage was built by a background coding agent from an approved design, reviewed in
  rounds by a second model and a fresh adversarial reviewer, then copied to the box by
  hand with checksums. Versions 0.2.2 → 0.5.1 in five days. `[reconstructed]`
- The work style: "propose designs and get an OK before building or touching the box";
  implementation and reviews in the background while the conversation continues.
  `[reconstructed]`

### Survey-in optimism, Nov 3
- Base mode at 12:22 UTC; a 90-minute survey-in reported 1.2 cm accuracy. Its 10-minute
  means had wandered by more than a metre. `[reconstructed]`
- Fixed mode at the survey mean, so the receiver stops re-surveying on restart. In fixed
  mode the receiver's own position sentence just repeats the configured value: "useless
  for drift." `[reconstructed]`
- The first rover test through the public caster: fixed solutions scattered under 1 cm,
  but the same deck spot shot on both correction sources implied the survey-in was about
  1.3 m too high. `[reconstructed]`

### PPP
- Raw logs → RINEX with RTKLIB → CSRS-PPP. The first result (11.6 h, ultra-rapid
  products) put the survey-in 1.5 m high and 0.2 m north. Applied at 02:59 UTC Nov 4;
  the base's broadcast position matched to 0.1 mm. `[reconstructed]`
- The decision on antenna phase centre vs reference point: nothing in the chain applies a
  calibration for this antenna, so the PPP value is used as is (9 mm). `[reconstructed]`
- Then rapid products (3 cm up), then 36 h, then 60 h combined: all within a centimetre.
  The 60 h coordinate went in on Nov 7 UTC. Finals in two weeks. `[reconstructed]`
- A frame decision had to be made: ITRF2020 at the epoch of observation for the base;
  the state network is NAD83(2011) epoch 2010.00; the published shift between them at the
  yard is about 1.7 m, and the measured shift agreed within 5 cm. `[reconstructed]`

### The rover on the home base, Nov 4
- A 360-point walk of the yard, 98 % fixed. Post-processing against the base log showed
  real-time heights good to 3 cm once a fix was 90 s old, and the first minute or two
  after a re-fix could be 10–30 cm off. Bluetooth lost 11 % of raw epochs, which is why a
  USB logger app exists in `logger/`. `[reconstructed]`
- The rink placement check confirmed the October spot, with about 10 cm of relief, and
  found October's heights 7–8 cm high. A separate siting analysis (`analysis/`) proposed
  a 6 × 11 m rink about 3.5 m west of last year's, needing 5.3 m³ against 6.9 m³ for last
  year's footprint. `[reconstructed]`

### RTK2go and the phone
- The phone could not reach anything on the home network, so the rover took corrections
  from the public caster over cellular, from a base 30 m away. Correction dropouts of
  about 30 s were on that path. `[reconstructed]`

### The reference check idea, Nov 6–7
- Continuous RTK of the base antenna against a CORS station, in the app, all day: a
  measured answer to "is the coordinate still right" and "what is the frame offset,
  really." The first hour-long spike fixed in 2 minutes with the demo5 fork and matched
  the published transformation within a few centimetres. `[reconstructed]`

## Prompts

- What it was like to supervise agents building something that talks to real hardware
  in your house: what did you insist on, and what went wrong?
- The survey-in's "1.2 cm": what did you feel when PPP said 1.5 m?
- Choosing a frame when you are not a surveyor. How did you decide, and what would you
  tell someone else facing it?
- The irony of a base 30 m away reached through a server somewhere else.
- What does "the coordinate stays the same all season" commit you to, physically?

## Pointers

- `../howto/base-station.md` for the sequence and current state.
- `../log.md` for the dates.
- `../../ha-addon/gnss_base/README.md` for the app.
