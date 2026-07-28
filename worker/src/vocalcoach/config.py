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

PitchEngine = Literal["crepe", "pyin"]

# Order matches spec 6.4's aggregation table; every stage/weights table in
# this codebase iterates aspects in this order so logs and reports agree.
ASPECTS: tuple[str, ...] = ("pitch", "rhythm", "breath", "dynamics", "vibrato", "timbre")

# A misconfigured SCORING_WEIGHTS that doesn't sum to 1 would silently
# under- or over-weight the overall score; this is the tolerance for
# floating-point roundoff in the .env value, not a scoring parameter.
_WEIGHTS_SUM_TOLERANCE = 1e-6


class ScoringWeights(BaseModel):
    """Per-aspect weights for the overall score (spec 6.4), parsed from
    `SCORING_WEIGHTS=pitch:0.35,rhythm:0.20,...` -- one source of truth
    shared with `analyses.scoring_version` so old results stay reproducible."""

    pitch: float
    rhythm: float
    breath: float
    dynamics: float
    vibrato: float
    timbre: float

    @classmethod
    def parse(cls, raw: str) -> ScoringWeights:
        pairs: dict[str, float] = {}
        for item in raw.split(","):
            item = item.strip()
            if not item:
                continue
            if ":" not in item:
                raise ValueError(f"SCORING_WEIGHTS entry {item!r} is not 'aspect:weight'")
            name, _, value = item.partition(":")
            name = name.strip()
            if name not in ASPECTS:
                raise ValueError(f"SCORING_WEIGHTS has unknown aspect {name!r}")
            try:
                pairs[name] = float(value.strip())
            except ValueError as exc:
                raise ValueError(
                    f"SCORING_WEIGHTS weight for {name!r} is not a number: {value!r}"
                ) from exc

        missing = set(ASPECTS) - pairs.keys()
        if missing:
            raise ValueError(f"SCORING_WEIGHTS is missing aspects: {sorted(missing)}")

        weights = cls(**pairs)
        total = sum(pairs.values())
        if abs(total - 1.0) > _WEIGHTS_SUM_TOLERANCE:
            raise ValueError(f"SCORING_WEIGHTS must sum to 1.0, got {total}")
        return weights

    def as_dict(self) -> dict[str, float]:
        return {aspect: getattr(self, aspect) for aspect in ASPECTS}


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

    pitch_engine: PitchEngine = Field("crepe", alias="PITCH_ENGINE")
    whisper_model: str = Field("small", alias="WHISPER_MODEL")
    demucs_model: str = Field("htdemucs", alias="DEMUCS_MODEL")
    scoring_version: str = Field("1.0", alias="SCORING_VERSION")
    # Raw string, not a nested model: pydantic-settings' env source tries to
    # JSON-decode any complex field type before validators run, which the
    # "aspect:weight,..." format is not. _parse_scoring_weights below is
    # where SCORING_WEIGHTS actually gets validated (spec 12.1 fail-fast).
    scoring_weights_raw: str = Field(..., alias="SCORING_WEIGHTS")
    _scoring_weights: ScoringWeights = PrivateAttr()

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
        self._scoring_weights = ScoringWeights.parse(self.scoring_weights_raw)
        return self

    @property
    def scoring_weights(self) -> ScoringWeights:
        return self._scoring_weights

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
