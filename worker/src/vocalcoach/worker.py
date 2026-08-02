"""Worker process entrypoint: loads config, wires every dependency, and
runs the two-stream priority scheduler until told to stop (spec 10, 18/E3, M2).
"""

from __future__ import annotations

import logging
import socket
import threading
from pathlib import Path

import psycopg
import redis

from vocalcoach.config import Settings, load_settings
from vocalcoach.constants import (
    MAX_CLAIM_ATTEMPTS,
    PENDING_CLAIM_MIN_IDLE,
    SONGS_PREP_PENDING_CLAIM_MIN_IDLE,
)
from vocalcoach.logging_setup import configure_logging
from vocalcoach.models.context import AnalysisContext, SongPrepContext
from vocalcoach.pipeline.base import ParallelGroup, PipelineStage
from vocalcoach.pipeline.registry import ModelRegistry
from vocalcoach.pipeline.runner import PipelineRunner
from vocalcoach.pipeline.stages.aggregate import AggregateStage
from vocalcoach.pipeline.stages.align import AlignStage
from vocalcoach.pipeline.stages.breath import BreathStage
from vocalcoach.pipeline.stages.dynamics import DynamicsStage
from vocalcoach.pipeline.stages.features import FeaturesStage
from vocalcoach.pipeline.stages.key_normalization import KeyNormalizationStage
from vocalcoach.pipeline.stages.pitch import PitchStage
from vocalcoach.pipeline.stages.prep_reference import PrepReferenceStage
from vocalcoach.pipeline.stages.prep_reference_pitch import PrepReferencePitchStage
from vocalcoach.pipeline.stages.preprocess import PreprocessStage
from vocalcoach.pipeline.stages.recording_condition import RecordingConditionStage
from vocalcoach.pipeline.stages.rhythm import RhythmStage
from vocalcoach.pipeline.stages.separate_recording import SeparateRecordingStage
from vocalcoach.pipeline.stages.separate_reference import SeparateReferenceStage
from vocalcoach.pipeline.stages.timbre import TimbreStage
from vocalcoach.pipeline.stages.transcribe import TranscribeStage
from vocalcoach.pipeline.stages.vibrato import VibratoStage
from vocalcoach.queue.consumer import Consumer
from vocalcoach.queue.events import RedisEventPublisher
from vocalcoach.queue.handler import AnalysisJobHandler
from vocalcoach.queue.prep_handler import SongPrepJobHandler
from vocalcoach.queue.scheduler import Scheduler
from vocalcoach.queue.streams import (
    ANALYSES_GROUP_NAME,
    ANALYSES_STREAM_NAME,
    SONGS_PREP_GROUP_NAME,
    SONGS_PREP_STREAM_NAME,
)
from vocalcoach.repositories.postgres import PostgresAnalysisRepository, PostgresSongRepository

logger = logging.getLogger(__name__)

# ffmpeg is resolved via PATH; the runtime image (worker/Dockerfile) installs it (spec 11.3).
FFMPEG_PATH = "ffmpeg"

# A liveness signal for Docker's HEALTHCHECK (spec 5.3): touched on an
# interval independent of job processing, so a multi-minute stage in
# progress is never mistaken for a hung process.
HEARTBEAT_PATH = Path("/tmp/vocalcoach-worker-heartbeat")  # noqa: S108 -- single-tenant container, own /tmp tmpfs
HEARTBEAT_INTERVAL_SECONDS = 10.0


def build_stages(
    settings: Settings, registry: ModelRegistry
) -> list[PipelineStage[AnalysisContext] | ParallelGroup[AnalysisContext]]:
    """Warm path A1-A10 in spec 6.5 order (A6 is the spec 6.9 shared
    feature cache), plus the spec 6.16 recording-condition/reconciliation
    check and aggregation. Only ever run once a song's cold path has
    reached `ready` (spec 6.2, M2) -- Whisper/reference-pitch detection are
    not here, they ran once in the cold path (`build_prep_stages`). Demucs
    is the one exception (ADR-0034): `SeparateRecordingStage` (`mixed`
    only) runs Demucs on the user's own recording here too, since that
    recording is unique per analysis and cannot be cached the way the
    reference's stem is.

    `PitchStage` (A5) runs in both modes now: after ADR-0033 moved F0
    extraction into `align`, the `mixed`-only stage that used to score it
    separately (`MelodyPitchStage`) had become byte-identical to this one,
    so it was deleted rather than kept as a second copy (ADR-0034). Likewise
    `TimbreStage`/`BreathStage` declare `modes={"clean"}` and are simply
    absent from a `mixed` run's flattened stage list, not present-but-null.

    The five aspect stages depend only on `align`/`pitch`'s already-finished
    output, never on each other, so they run as one `ParallelGroup` (spec
    6.10) unless `PIPELINE_PARALLEL_ASPECTS=false` -- kept available as a
    flat, deterministic fallback for tests that need an exact stage order
    (spec 15.3).

    No stage here takes a `SongRepository`: every stage instance is
    pickled across `PipelineRunner`'s spawn-based subprocess boundary
    (runner.py), and a repository holding a live DB connection isn't
    picklable.
    """
    aspect_stages: tuple[PipelineStage[AnalysisContext], ...] = (
        RhythmStage(),
        VibratoStage(),
        DynamicsStage(),
        TimbreStage(),
        BreathStage(),
    )
    aspects: list[PipelineStage[AnalysisContext] | ParallelGroup[AnalysisContext]] = (
        [ParallelGroup(aspect_stages)]
        if settings.pipeline_parallel_aspects
        else list(aspect_stages)
    )

    return [
        PreprocessStage(ffmpeg_path=FFMPEG_PATH),
        SeparateRecordingStage(registry.vocal_separator()),
        FeaturesStage(),
        AlignStage(registry.pitch_detector()),
        PitchStage(),
        KeyNormalizationStage(
            min_semitones=settings.key_shift_min_semitones,
            max_iqr_semitones=settings.key_shift_max_iqr,
            max_semitones=settings.max_key_shift_semitones,
        ),
        *aspects,
        RecordingConditionStage(settings.accompaniment_detect_threshold),
        AggregateStage(
            {
                "clean": settings.scoring_weights_for("clean"),
                "mixed": settings.scoring_weights_for("mixed"),
            },
            settings.scoring_version,
        ),
    ]


def build_prep_stages(
    settings: Settings, registry: ModelRegistry
) -> list[PipelineStage[SongPrepContext]]:
    """Cold path P1-P4 in spec 6.4 order: reference decode/normalize,
    Demucs separation, optional Whisper transcription (FR-18), reference
    pitch curve. Runs once per song, asynchronously, well before any
    analysis waits on it (spec 6.2, 10, M2). Shares `registry` with
    `build_stages` -- the same pitch engine instance detects both sides of
    every comparison (spec 6.6 determinism), and `ModelRegistry` already
    guarantees Demucs/Whisper are never resident together (spec 6.5)
    regardless of which stage set loads them.
    """
    return [
        PrepReferenceStage(ffmpeg_path=FFMPEG_PATH),
        SeparateReferenceStage(registry.vocal_separator()),
        TranscribeStage(registry.transcriber()),
        PrepReferencePitchStage(registry.pitch_detector()),
    ]


def _heartbeat_loop(stop: threading.Event) -> None:
    while not stop.is_set():
        HEARTBEAT_PATH.touch()
        stop.wait(HEARTBEAT_INTERVAL_SECONDS)


def run() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)

    # First `torch` import in the process (spec 6.11): thread env vars are
    # already set by `configure_worker_threads` in __main__.py, before this
    # module -- or anything it imports -- ever ran; torch.set_num_threads
    # covers its own internal thread pool, which is separate from the
    # OMP_NUM_THREADS-driven one the env vars alone control.
    import torch

    torch.set_num_threads(settings.worker_cpu_threads)
    torch.set_num_interop_threads(max(1, settings.worker_cpu_threads // 2) or 1)

    logger.info(
        "starting worker",
        extra={
            "pitch_engine": settings.pitch_engine,
            "app_env": settings.app_env,
            "worker_cpu_threads": settings.worker_cpu_threads,
            "pipeline_parallel_aspects": settings.pipeline_parallel_aspects,
        },
    )

    settings.audio_storage_dir.mkdir(parents=True, exist_ok=True)
    settings.song_stems_dir.mkdir(parents=True, exist_ok=True)
    settings.model_weights_dir.mkdir(parents=True, exist_ok=True)

    pg_conn = psycopg.connect(settings.postgres_dsn())
    redis_client: redis.Redis = redis.Redis.from_url(settings.redis_url(), decode_responses=True)
    redis_client.ping()

    songs = PostgresSongRepository(pg_conn)
    analyses = PostgresAnalysisRepository(pg_conn)
    events = RedisEventPublisher(redis_client)

    registry = ModelRegistry(
        demucs_model=settings.demucs_model,
        whisper_model=settings.whisper_model,
        whisper_compute_type=settings.whisper_compute_type,
        pitch_engine=settings.pitch_engine,
        weights_dir=settings.model_weights_dir,
    )

    warm_runner = PipelineRunner(build_stages(settings, registry), events)
    cold_runner = PipelineRunner(build_prep_stages(settings, registry), events)
    model_versions = {
        "demucs": settings.demucs_model,
        "whisper": settings.whisper_model,
        "pitch_engine": settings.pitch_engine,
    }
    analysis_handler = AnalysisJobHandler(
        warm_runner, analyses, songs, events, settings, model_versions
    )
    prep_handler = SongPrepJobHandler(cold_runner, songs, analyses, events, redis_client, settings)

    consumer_name = f"worker-{socket.gethostname()}"
    analyses_consumer = Consumer(
        redis_client,
        analysis_handler,
        ANALYSES_STREAM_NAME,
        ANALYSES_GROUP_NAME,
        PENDING_CLAIM_MIN_IDLE,
        MAX_CLAIM_ATTEMPTS,
        consumer_name,
    )
    songs_prep_consumer = Consumer(
        redis_client,
        prep_handler,
        SONGS_PREP_STREAM_NAME,
        SONGS_PREP_GROUP_NAME,
        SONGS_PREP_PENDING_CLAIM_MIN_IDLE,
        MAX_CLAIM_ATTEMPTS,
        consumer_name,
    )
    scheduler = Scheduler(analyses_consumer, songs_prep_consumer, analyses)
    scheduler.install_signal_handlers()

    heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(target=_heartbeat_loop, args=(heartbeat_stop,), daemon=True)
    heartbeat_thread.start()

    try:
        logger.info("ready, waiting for jobs")
        scheduler.run_forever()
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=5)
        pg_conn.close()
        redis_client.close()
        logger.info("worker stopped")
