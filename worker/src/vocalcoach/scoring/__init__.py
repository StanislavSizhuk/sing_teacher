"""Weight profiles and the confidence model (spec 6.14, 6.15, 12.3: "profiles
and confidence thresholds live in scoring/, not inside a stage"). Pure
functions over already-computed aspect scores/signals -- no DSP, no I/O,
so `AggregateStage` (the only caller) stays a thin adapter between the
pipeline's stage results and this module's domain logic.
"""

from __future__ import annotations
