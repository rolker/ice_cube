"""Ice-Cube: a CUBE-derived surface and ice-growth estimator for a backyard rink.

Pipeline (see docs/howto/ice-cube.md):
  io.read_survey      -> one point table per survey (local x, y, z, sigmas, time)
  passes.segment      -> pass ids (one RTK bias per pass)
  crossover.adjust    -> one vertical bias per pass from overlaps, zero-mean prior
  nodes.grid_surface  -> CUBE-style node estimates (z, sigma, n, inconsistency flag)
  growth.difference   -> surface minus surface with combined sigma, volumes
  report              -> figures in local axes, markdown tables
  cli                 -> `python -m icecube run config.toml`
"""
__version__ = "0.1.0"
