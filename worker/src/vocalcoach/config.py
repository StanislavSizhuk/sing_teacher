"""Worker process configuration, loaded and validated from the environment.

Mirrors `api/internal/config`: every threshold is named config, not a magic
number (spec 12.1), and pydantic aggregates every missing/invalid field into
one error, so a misconfigured deployment fails fast with a clear message
instead of starting silently wrong.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from vocalcoach.models.mode import Mode
from vocalcoach.scoring.weights import MODE_ASPECTS

PitchEngine = Literal["crepe", "pyin"]

# A misconfigured SCORING_WEIGHTS_* that doesn't sum to 1 would silently
# under- or over-weight the overall score; this is the tolerance for
# floating-point roundoff in the .env value, not a scoring parameter.
_WEIGHTS_SUM_TOLERANCE = 1e-6


class ScoringWeights(BaseModel):
    """One mode's per-aspect weights (spec 6.14), parsed from
    `SCORING_WEIGHTS_CLEAN=pitch:0.35,rhythm:0.20,...` or
    `SCORING_WEIGHTS_MIXED=pitch:0.50,...` -- keyed by exactly that mode's
    own `MODE_ASPECTS`, never all six regardless of mode, since `mixed`
    never has a weight to configure for breath/timbre in the first place
    (FR-41: unavailable is `null`, not a zero weight). One source of truth
    shared with `analyses.weights_profile` so old results stay reproducible.
    """

    weights: dict[str, float]

    @classmethod
    def parse(cls, raw: str, mode: Mode) -> ScoringWeights:
        expected = set(MODE_ASPECTS[mode])
        env_name = f"SCORING_WEIGHTS_{mode.upper()}"
        pairs: dict[str, float] = {}
        for item in raw.split(","):
            item = item.strip()
            if not item:
                continue
            if ":" not in item:
                raise ValueError(f"{env_name} entry {item!r} is not 'aspect:weight'")
            name, _, value = item.partition(":")
            name = name.strip()
            if name not in expected:
                raise ValueError(f"{env_name} has an aspect {mode} does not score: {name!r}")
            try:
                pairs[name] = float(value.strip())
            except ValueError as exc:
                raise ValueError(
                    f"{env_name} weight for {name!r} is not a number: {value!r}"
                ) from exc

        missing = expected - pairs.keys()
        if missing:
            raise ValueError(f"{env_name} is missing aspects: {sorted(missing)}")

        total = sum(pairs.values())
        if abs(total - 1.0) > _WEIGHTS_SUM_TOLERANCE:
            raise ValueError(f"{env_name} must sum to 1.0, got {total}")
        return cls(weights=pairs)

    def as_dict(self) -> dict[str, float]:
        return dict(self.weights)


class Settings(BaseSettings):
    """Worker settings, read from the same `.env` the Go API reads (spec
    20.5) -- one file, one source of truth for both processes."""

    model_config = SettingsConfigDict(extra="ignore")

    app_env: Literal["development", "production"] = Field("development", alias="APP_ENV")
    log_level: str = Field("info", alias="LOG_LEVEL")

    # Fixed container paths, not operator config -- shared Docker volumes
    # with the Go API (spec 5.2 "audio-tmp") and the model weights volume
    # (spec 5.4: model weights live on their own volume).
    audio_storage_dir: Path = Path("/data/audio-tmp")
    model_weights_dir: Path = Path("/data/model-weights")
    # Separate from audio-tmp on purpose: audio-tmp is swept by age (FR-43,
    # <=5min after processing), but a separated reference vocal stem is
    # cached for as long as the song row exists (spec 6.6, 7.2) -- putting
    # it in the swept directory would silently defeat the cache.
    song_stems_dir: Path = Path("/data/song-stems")

    max_audio_seconds: int = Field(360, alias="MAX_AUDIO_SECONDS")
    audio_ttl_seconds: int = Field(300, alias="AUDIO_TTL_SECONDS")

    # Normalized to a concrete count by `runtime.threads.configure_worker_threads`
    # before this settings object is ever built (spec 6.11) -- 0 here would
    # only mean "autodetect wasn't applied yet", which should never happen.
    worker_cpu_threads: int = Field(0, alias="WORKER_CPU_THREADS")
    # Spec 6.10/15.3: off only for deterministic tests that need an exact,
    # reproducible stage order -- production always wants this on.
    pipeline_parallel_aspects: bool = Field(True, alias="PIPELINE_PARALLEL_ASPECTS")

    pitch_engine: PitchEngine = Field("crepe", alias="PITCH_ENGINE")
    # "base", not spec 6.2's named "small": ADR-0014, real hardware measured
    # "small" landing on top of TRANSCRIBE_TIMEOUT_SECONDS instead of under it.
    whisper_model: str = Field("base", alias="WHISPER_MODEL")
    # faster-whisper's CTranslate2 quantization (spec 6.6/ADR-0021); int8 is
    # what makes it substantially faster/lighter than openai-whisper's own
    # float32 inference on CPU at comparable accuracy.
    whisper_compute_type: str = Field("int8", alias="WHISPER_COMPUTE_TYPE")
    demucs_model: str = Field("htdemucs", alias="DEMUCS_MODEL")
    scoring_version: str = Field("1.0", alias="SCORING_VERSION")
    # Raw strings, not nested models: pydantic-settings' env source tries to
    # JSON-decode any complex field type before validators run, which the
    # "aspect:weight,..." format is not. _parse_scoring_weights below is
    # where SCORING_WEIGHTS_* actually gets validated (spec 12.1 fail-fast).
    # Two profiles (spec 6.14, 20.5), not one: mixed scores fewer aspects at
    # different weights, not a subset of clean's own.
    scoring_weights_clean_raw: str = Field(..., alias="SCORING_WEIGHTS_CLEAN")
    scoring_weights_mixed_raw: str = Field(..., alias="SCORING_WEIGHTS_MIXED")
    _scoring_weights: dict[Mode, ScoringWeights] = PrivateAttr()

    # spec 6.16, 6.8: named config, not constants, because the spec itself
    # lists these under .env.example (20.5) as operator-tunable thresholds.
    accompaniment_detect_threshold: float = Field(0.15, alias="ACCOMPANIMENT_DETECT_THRESHOLD")
    key_shift_min_semitones: float = Field(0.6, alias="KEY_SHIFT_MIN_SEMITONES")
    key_shift_max_iqr: float = Field(0.5, alias="KEY_SHIFT_MAX_IQR")
    max_key_shift_semitones: float = Field(7.0, alias="MAX_KEY_SHIFT_SEMITONES")

    postgres_host: str = Field("postgres", alias="POSTGRES_HOST")
    postgres_port: int = Field(5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(..., alias="POSTGRES_DB")
    postgres_user: str = Field(..., alias="POSTGRES_USER")
    postgres_password: str = Field(..., alias="POSTGRES_PASSWORD")

    redis_host: str = Field("redis", alias="REDIS_HOST")
    redis_port: int = Field(6379, alias="REDIS_PORT")
    redis_db: int = Field(0, alias="REDIS_DB")
    redis_password: str = Field(..., alias="REDIS_PASSWORD")

    @model_validator(mode="after")
    def _parse_scoring_weights(self) -> Settings:
        self._scoring_weights = {
            "clean": ScoringWeights.parse(self.scoring_weights_clean_raw, "clean"),
            "mixed": ScoringWeights.parse(self.scoring_weights_mixed_raw, "mixed"),
        }
        return self

    def scoring_weights_for(self, mode: Mode) -> ScoringWeights:
        return self._scoring_weights[mode]

    def postgres_dsn(self) -> str:
        return (
            f"host={self.postgres_host} port={self.postgres_port} dbname={self.postgres_db} "
            f"user={self.postgres_user} password={self.postgres_password} sslmode=disable"
        )

    def redis_url(self) -> str:
        return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"


def load_settings() -> Settings:
    """Loads and validates worker configuration from the environment,
    raising `pydantic.ValidationError` (which lists every problem at once)
    if anything is missing or malformed."""
    return Settings()  # type: ignore[call-arg]
