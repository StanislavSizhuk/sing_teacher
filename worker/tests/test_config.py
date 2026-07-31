from __future__ import annotations

import pytest
from pydantic import ValidationError

from vocalcoach.config import ScoringWeights, load_settings

VALID_CLEAN_WEIGHTS = "pitch:0.35,rhythm:0.20,breath:0.15,dynamics:0.10,vibrato:0.10,timbre:0.10"
VALID_MIXED_WEIGHTS = "pitch:0.50,rhythm:0.30,dynamics:0.10,vibrato:0.10"


def _base_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    env = {
        "POSTGRES_DB": "vocalcoach",
        "POSTGRES_USER": "vocalcoach",
        "POSTGRES_PASSWORD": "pw",
        "REDIS_PASSWORD": "pw",
        "SCORING_WEIGHTS_CLEAN": VALID_CLEAN_WEIGHTS,
        "SCORING_WEIGHTS_MIXED": VALID_MIXED_WEIGHTS,
        **overrides,
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def test_load_settings_applies_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    settings = load_settings()
    assert settings.pitch_engine == "crepe"
    assert settings.whisper_model == "base"
    assert settings.accompaniment_detect_threshold == 0.15
    assert settings.key_shift_min_semitones == 0.6
    assert settings.postgres_dsn() == (
        "host=postgres port=5432 dbname=vocalcoach user=vocalcoach password=pw sslmode=disable"
    )
    assert settings.redis_url() == "redis://:pw@redis:6379/0"


def test_load_settings_exposes_weights_per_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    settings = load_settings()
    assert settings.scoring_weights_for("clean").as_dict()["timbre"] == 0.10
    assert "timbre" not in settings.scoring_weights_for("mixed").as_dict()
    assert settings.scoring_weights_for("mixed").as_dict()["pitch"] == 0.50


def test_load_settings_missing_required_field_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTGRES_DB", raising=False)
    monkeypatch.setenv("POSTGRES_USER", "vocalcoach")
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw")
    monkeypatch.setenv("REDIS_PASSWORD", "pw")
    monkeypatch.setenv("SCORING_WEIGHTS_CLEAN", VALID_CLEAN_WEIGHTS)
    monkeypatch.setenv("SCORING_WEIGHTS_MIXED", VALID_MIXED_WEIGHTS)
    with pytest.raises(ValidationError):
        load_settings()


def test_scoring_weights_parse_valid() -> None:
    weights = ScoringWeights.parse(VALID_CLEAN_WEIGHTS, "clean")
    assert weights.as_dict()["pitch"] == 0.35
    assert weights.as_dict()["timbre"] == 0.10


def test_scoring_weights_parse_mixed_excludes_breath_and_timbre() -> None:
    weights = ScoringWeights.parse(VALID_MIXED_WEIGHTS, "mixed")
    assert weights.as_dict() == {"pitch": 0.50, "rhythm": 0.30, "dynamics": 0.10, "vibrato": 0.10}


@pytest.mark.parametrize(
    ("raw", "mode"),
    [
        ("pitch:0.35,rhythm:0.20,breath:0.15,dynamics:0.10,vibrato:0.10,timbre:0.20", "clean"),
        # missing timbre
        ("pitch:0.35,rhythm:0.20,breath:0.15,dynamics:0.10,vibrato:0.10", "clean"),
        ("pitch:oops,rhythm:0.20,breath:0.15,dynamics:0.10,vibrato:0.10,timbre:0.10", "clean"),
        ("pitch:0.35;rhythm:0.20", "clean"),  # wrong separator entirely
        # a `clean`-shaped profile is invalid for `mixed`: it names aspects
        # mixed does not score (breath/timbre) and is missing none of its own.
        (VALID_CLEAN_WEIGHTS, "mixed"),
    ],
)
def test_scoring_weights_parse_rejects_invalid(raw: str, mode: str) -> None:
    with pytest.raises(ValueError, match=r".+"):
        ScoringWeights.parse(raw, mode)  # type: ignore[arg-type]


def test_load_settings_rejects_malformed_scoring_weights(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch, SCORING_WEIGHTS_CLEAN="pitch:0.9,rhythm:0.9")
    with pytest.raises(ValidationError):
        load_settings()
