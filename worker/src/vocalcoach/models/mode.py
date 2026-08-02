"""Analysis mode (spec 2.3, 6.1): `clean` (a cappella, in headphones) or
`mixed` (singing over any accompaniment). `mixed` is no longer just a
different pitch *source* -- ADR-0034 gives it its own extra pipeline step
(`SeparateRecordingStage`, Demucs on the user's own recording) before A4/A5
ever run, on top of which aspects are scored at all (spec 6.14) and the
base confidence ceiling (spec 6.15).

A tiny, dependency-free module on purpose: both `config.py` (weight
profiles) and `pipeline/base.py` (a stage's own `modes` declaration) need
this type, and neither should have to import the other to get it.
"""

from __future__ import annotations

from typing import Literal

Mode = Literal["clean", "mixed"]

ALL_MODES: frozenset[Mode] = frozenset({"clean", "mixed"})
