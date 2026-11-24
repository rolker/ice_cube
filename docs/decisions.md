# Decisions

What was decided for this project, why, and where the decided text lives. A decision
can be revised: edit its entry in place and update its date, so this file stays the
current set of decisions with their reasons. The goals live in
[`README.md`](../README.md#goals), the work in [`roadmap.md`](roadmap.md).

## Goals (2026-11-23)

The goals were worked out in a design discussion (issue #19), each in the owner's words
with the smallest edits.

**The summary line.** In the owner's words: learning is the point. The rink is what is
learned on; the record, the tools and the write-ups serve that.

**Goal 1, the rink.** Building the rink comes first: everything else serves it, and the
roadmap's current goal (this season's rink sited, built and tracked) already treats it
as the point. Its size and the liner are open details of each season's build (below).

**Goal 2, the record.** Last season the record started weeks late: photos and GPS data
only after a few weeks, and the Gemini conversations used to care for the rink had to be
recovered after the fact, so what worked could not be reviewed. The record starts with
the first planning in the fall (this season, the October yard survey came before any
ice) and runs through the thaw, which is part of the story. It names the AI
conversations because AI agents will use the record again (goal 4). "In one place" was
left out on purpose: where the data lives is a design question.

**Goal 3, collecting from the phone.** Photos and notes should not wait for the RTK
receiver: the phone's own GPS is enough for them, and only surveys need RTK. Collecting
comes first in the work, because showing data only makes sense once it can be collected.
Today the logger positions photos only from the receiver, and its notes column is left
for a laptop tool.

**Goal 4, AI help with care.** The project started with photos and a forecast going to
an AI on the phone, and a plan for the next 24 to 48 hours coming back. That help is
wanted again, this time with the agents reading the collected data and the forecasts.
"To help me decide how to care for the rink" says what the AI is for.

**Goal 5, the status page.** Family and coworkers should be able to follow the rink, and
anyone can see it: the owner chose public over a page shared with chosen people. Upkeep
has to stay small. This season's story grows out of the entries the owner adds notes and
flavor to, rather than being written separately afterwards.

**Goal 6, write-ups.** Findings and techniques are written up for whoever wants the
nerdy bits, and last season's story is edited and posted once.

## Principles (2026-11-23)

**General-purpose tools where that is cheap.** The logger has already served surveys
beyond the rink. The rule covers the phone app, the base station and the GIS tools alike,
so it is a principle rather than part of goal 3. "Where that is cheap" was kept over
"when possible": the rink comes first.

**Nothing public gives away where the rink is or who lives there.** Local coordinates
alone do not hide a site: an outline, a surface or a footprint can be matched against
public map layers. The detailed rules are in [`CLAUDE.md`](../CLAUDE.md), whose scope now
includes the status page. Open: whether first names of family and friends may appear.

## Decided against (2026-11-23)

**Deploy-your-own packaging** (each user sets up a copy on their own cloud account). No
one the project is for would run a copy: family and coworkers view the results. Keeping
site-specific values out of the code, already a rule, is enough for others to reuse the
tools.

**Scheduled processing.** Processing starts when the owner starts it (the owner's
direction of 2026-11-21, not re-discussed on 2026-11-23).

## Parked and offered

Parked details do not block the goals and can be settled later without a redesign.

- Parked: where the write-ups live, the status page or the repo.
- Parked: recording the phone's IMU with photos taken without RTK, so the rough position
  comes with the direction the camera pointed.
- Offered, not settled: starting the processing from the phone after new data comes in.
- Offered, not settled: some status updates automatic, richer ones with the owner's
  input. An automatic update cannot wait for review, so it would publish only what is
  safe by construction.
- Rink size and the liner are open details of this season's build, not goals: about as
  large as last season, maybe more rectangular; the liner is an option because a milder
  winter was predicted.
