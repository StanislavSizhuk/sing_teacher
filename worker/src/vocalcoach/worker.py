"""Worker process entrypoint: loads config, wires every dependency, and
runs the Redis Streams consumer loop until told to stop (spec 10, 18/E3).
"""

from __future__ import annotations

import functools
import logging
import threading
from pathlib import Path

import psycopg
import redis

from vocalcoach.audio.paths import song_stem_path
from vocalcoach.config import Settings, load_settings
from vocalcoach.logging_setup import configure_logging
from vocalcoach.pipeline.base import ParallelGroup, PipelineStage
from vocalcoach.pipeline.registry import ModelRegistry
from vocalcoach.pipeline.runner import PipelineRunner
from vocalcoach.pipeline.stages.aggregate import AggregateStage
from vocalcoach.pipeline.stages.align import AlignStage
from vocalcoach.pipeline.stages.breath import BreathStage
from vocalcoach.pipeline.stages.dynamics import DynamicsStage
from vocalcoach.pipeline.stages.features import FeaturesStage
from vocalcoach.pipeline.stages.pitch import PitchStage
from vocalcoach.pipeline.stages.preprocess import PreprocessStage
from vocalcoach.pipeline.stages.recording_condition import RecordingConditionStage
from vocalcoach.pipeline.stages.rhythm import RhythmStage
from vocalcoach.pipeline.stages.separate_reference import SeparateReferenceStage
from vocalcoach.pipeline.stages.timbre import TimbreStage
from vocalcoach.pipeline.stages.transcribe import TranscribeStage
from vocalcoach.pipeline.stages.vibrato import VibratoStage
from vocalcoach.queue.consumer import Consumer
from vocalcoach.queue.events import RedisEventPublisher
from vocalcoach.queue.handler import AnalysisJobHandler
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
) -> list[PipelineStage | ParallelGroup]:
    """Stages 1-9 in spec 6.2 order (stage 3 is the spec 6.9 shared feature
    cache), plus the spec 6.9 recording-condition check (12) and
    aggregation (13).

    The five aspect stages depend only on `align`/`pitch`'s already-finished
    output, never on each other, so they run as one `ParallelGroup` (spec
    6.10) unless `PIPELINE_PARALLEL_ASPECTS=false` -- kept available as a
    flat, deterministic fallback for tests that need an exact stage order
    (spec 15.3).

    No stage here takes a `SongRepository`: every stage instance is
    pickled across `PipelineRunner`'s spawn-based subprocess boundary
    (runner.py), and a repository holding a live DB connection isn't
    picklable. `AnalysisJobHandler` persists transcribe's/pitch's song-cache
    writes itself, from the parent process, once the pipeline finishes.
    """
    aspect_stages: tuple[PipelineStage, ...] = (
        RhythmStage(),
        VibratoStage(),
        DynamicsStage(),
        TimbreStage(),
        BreathStage(),
    )
    aspects: list[PipelineStage | ParallelGroup] = (
        [ParallelGroup(aspect_stages)]
        if settings.pipeline_parallel_aspects
        else list(aspect_stages)
    )

    return [
        PreprocessStage(ffmpeg_path=FFMPEG_PATH),
        SeparateReferenceStage(
            registry.vocal_separator(),
            # functools.partial, not a lambda: same pickling constraint --
            # pickle cannot serialize a closure over a local variable, only
            # a plain function plus its already-bound arguments.
            stem_path_for_song=functools.partial(song_stem_path, settings.song_stems_dir),
        ),
        FeaturesStage(),
        TranscribeStage(registry.transcriber()),
        AlignStage(),
        PitchStage(registry.pitch_detector()),
        *aspects,
        RecordingConditionStage(),
        AggregateStage(settings.scoring_weights, settings.scoring_version),
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
    stages = build_stages(settings, registry)
    runner = PipelineRunner(stages, analyses, events)
    model_versions = {
        "demucs": settings.demucs_model,
        "whisper": settings.whisper_model,
        "pitch_engine": settings.pitch_engine,
    }
    handler = AnalysisJobHandler(runner, analyses, songs, events, settings, model_versions)
    consumer = Consumer(redis_client, handler)
    consumer.install_signal_handlers()

    heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(target=_heartbeat_loop, args=(heartbeat_stop,), daemon=True)
    heartbeat_thread.start()

    try:
        logger.info("ready, waiting for jobs")
        consumer.run_forever()
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=5)
        pg_conn.close()
        redis_client.close()
        logger.info("worker stopped")
