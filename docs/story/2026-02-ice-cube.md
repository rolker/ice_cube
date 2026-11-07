# The first season's surveys, and Ice-Cube

**Outline, not written.** Facts marked `[own words]` are the author's; `[reconstructed]`
ones are pieced together from notes and should be checked before writing.

## Facts

- "Once I got the GPS, I started surveying the ice surface." `[own words]`
- 2026-01-09: an outline of the rink edge, about 27 cm of height variation around it.
  By mid-February the edge varied by about 13 cm and the interior points spanned about
  12 cm. `[own words, from the October draft]`
- Surveys on 2026-02-14 and 2026-03-02; the March one was never analysed at the time.
  `[reconstructed]`
- Last season's rink traced out as an irregular two-lobed shape of about 68 m² over about
  31 cm of ground relief. `[own words, from the October draft]`
- The technical trigger: "that's when Gemini struggled with creating a surface at times, I
  think because some points got sampled twice and the algorithm it was using didn't handle
  that." `[own words]`
- The half-joke: "I half-jokingly asked why it didn't just use Calder's CUBE, and to my
  surprise, it returned with something like 'that's a good idea' and proceeded to adapt
  CUBE." `[own words]` CUBE (Combined Uncertainty and Bathymetry Estimator) is the
  bathymetric gridding method the author knows from marine work. `[reconstructed]`
- The name was Gemini's: "It then suggested we name it Ice-Cube and I thought that was
  amazing and hilarious." `[own words]`
- The paper: "I also asked for a research paper more as a spoof, but was genuinely
  surprised with the result." `[own words]` Present it as a spoof. The recovered chat
  text, scripts and LaTeX from that season exist privately and are the source for any
  excerpts. `[reconstructed]`
- The consequence: "my experience with discovering the powers of LLMs while helping me
  with this contributed to my eventual decision to jump into agentic coding for work. Well,
  there is probably some overlap with using agents at work and using Gemini on my phone to
  help with the rink." `[own words]`
- Later finding (Nov 2026): last season's 12.5 cm mean accretion and its southeast slope
  are within the correction wander measured in October, so they are not firm. `[reconstructed,
  from the siting analysis]`

## Prompts

- What did the "surface" look like when it worked, and what did a failed one look like?
- Why did duplicate points break it? (A prompt for a one-paragraph explanation of
  gridding with repeated samples, and why CUBE's uncertainty-weighted hypotheses handle it.)
- The moment Gemini said "that's a good idea": what did it actually produce, and did it
  work on the duplicate points?
- The spoof paper: what was the funniest part, and what in it was actually right?
- The bridge to work: what was the first agentic coding task you tried at work, and did
  the rink experience change how you approached it?

## Pointers

- Method details, if any are published later: `../howto/`.
- Ice numbers: `../log.md`.
