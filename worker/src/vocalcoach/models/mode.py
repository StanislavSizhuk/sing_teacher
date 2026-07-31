"""Analysis mode (spec 2.3, 6.1): `clean` (a cappella, in headphones) or
`mixed` (singing over any accompaniment). The mode a user picked decides
which pitch source a warm-path stage uses (A4 vs A5), which aspects are
scored at all (spec 6.14), and the base confidence ceiling (spec 6.15).

A tiny, dependency-free module on purpose: both `config.py` (weight
profiles) and `pipeline/base.py` (a stage's own `modes` declaration) need
this type, and neither should have to import the other to get it.
"""

from __future__ import annotations

from typing import Literal

Mode = Literal["clean", "mixed"]

ALL_MODES: frozenset[Mode] = frozenset({"clean", "mixed"})
