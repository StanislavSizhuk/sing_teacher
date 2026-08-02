"""The language a caller wants generated report text in (ADR-0031). A tiny,
dependency-free module on the same footing as `mode.py`: `pipeline/report.py`
and `models/context.py`/`models/records.py` all need this type without
importing each other to get it.
"""

from __future__ import annotations

from typing import Literal

Locale = Literal["en", "uk"]

ALL_LOCALES: frozenset[Locale] = frozenset({"en", "uk"})

DEFAULT_LOCALE: Locale = "en"
