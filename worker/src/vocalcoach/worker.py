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
from vocalcoach.pipeline.base import PipelineStage
from vocalcoach.pipeline.registry import ModelRegistry
from vocalcoach.pipeline.runner import PipelineRunner
from vocalcoach.pipeline.stages.aggregate import AggregateStage
from vocalcoach.pipeline.stages.align import AlignStage
from vocalcoach.pipeline.stages.breath import BreathStage
from vocalcoach.pipeline.stages.dynamics import DynamicsStage
from vocalcoach.pipeline.stages.pitch import PitchStage
from vocalcoach.pipeline.stages.preprocess import PreprocessStage
from vocalcoach.pipeline.stages.rhythm import RhythmStage
from vocalcoach.pipeline.stages.separate_reference import SeparateReferenceStage
from vocalcoach.pipeline.stages.timbre import TimbreStage
from vocalcoach.pipeline.stages.transcribe import TranscribeStage
from vocalcoach.pipeline.stages.vibrato import VibratoStage
from vocalcoach.queue.consumer import GROUP_NAME, STREAM_NAME, Consumer
from vocalcoach.queue.events import RedisEventPublisher
from vocalcoach.queue.handler import AnalysisJobHandler
from vocalcoach.repositories.interfaces import SongRepository
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
    settings: Settings, registry: ModelRegistry, songs: SongRepository
) -> list[PipelineStage]:
    """Stages 1-11 in spec 6.2 order."""
    return [
        PreprocessStage(ffmpeg_path=FFMPEG_PATH),
        SeparateReferenceStage(
            registry.vocal_separator(),
            # functools.partial, not a lambda: PipelineRunner sends each
            # stage instance across a spawn-based multiprocessing boundary
            # (runner.py), which pickles it -- pickle cannot serialize a
            # closure over a local variable, only a plain function plus its
            # already-bound arguments.
            stem_path_for_song=functools.partial(song_stem_path, settings.song_stems_dir),
        ),
        TranscribeStage(registry.transcriber(), songs),
        AlignStage(),
        PitchStage(registry.pitch_detector(), songs),
        RhythmStage(),
        VibratoStage(),
        DynamicsStage(),
        TimbreStage(),
        BreathStage(),
        AggregateStage(settings.scoring_weights, settings.scoring_version),
    ]


def ensure_consumer_group(client: redis.Redis) -> None:
    """Idempotent: the Go API also creates this group at its own startup
    (`queue.Producer.EnsureGroup`); whichever process starts first wins."""
    try:
        client.xgroup_create(STREAM_NAME, GROUP_NAME, id="0", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def _heartbeat_loop(stop: threading.Event) -> None:
    while not stop.is_set():
        HEARTBEAT_PATH.touch()
        stop.wait(HEARTBEAT_INTERVAL_SECONDS)


def run() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    logger.info(
        "starting worker",
        extra={"pitch_engine": settings.pitch_engine, "app_env": settings.app_env},
    )

    settings.audio_storage_dir.mkdir(parents=True, exist_ok=True)
    settings.song_stems_dir.mkdir(parents=True, exist_ok=True)
    settings.model_weights_dir.mkdir(parents=True, exist_ok=True)

    pg_conn = psycopg.connect(settings.postgres_dsn())
    redis_client: redis.Redis = redis.Redis.from_url(settings.redis_url(), decode_responses=True)
    redis_client.ping()
    ensure_consumer_group(redis_client)

    songs = PostgresSongRepository(pg_conn)
    analyses = PostgresAnalysisRepository(pg_conn)
    events = RedisEventPublisher(redis_client)

    registry = ModelRegistry(
        demucs_model=settings.demucs_model,
        whisper_model=settings.whisper_model,
        pitch_engine=settings.pitch_engine,
        weights_dir=settings.model_weights_dir,
    )
    stages = build_stages(settings, registry, songs)
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
