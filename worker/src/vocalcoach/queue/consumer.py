"""Redis Streams consumer (spec 10.1, ADR-0002): `XREADGROUP` delivery,
`XACK` once a job reaches a terminal outcome, `XAUTOCLAIM`-equivalent
reclaim for a job whose worker died mid-stage, graceful shutdown on
SIGTERM/SIGINT.

`StreamName`/`GroupName` must match `api/internal/queue`'s constants
exactly -- they name the one channel the Go API and this worker agree on.
"""

from __future__ import annotations

import logging
import signal
import socket
from collections.abc import Callable
from types import FrameType
from typing import Any, Protocol, cast

import redis

from vocalcoach.constants import MAX_CLAIM_ATTEMPTS, PENDING_CLAIM_MIN_IDLE

logger = logging.getLogger(__name__)


class JobHandler(Protocol):
    """The narrow slice of `AnalysisJobHandler` the consumer needs -- it
    orchestrates Redis, not analysis lifecycle, so it only calls in
    (spec 12.2's "interfaces declared by the consumer" applied to Python).
    """

    def handle(self, analysis_id: str, should_stop: Callable[[], bool]) -> bool: ...

    def mark_permanently_failed(self, analysis_id: str) -> None: ...


STREAM_NAME = "analyses:queue"
GROUP_NAME = "analyses:workers"
BLOCK_MS = 5000
RECLAIM_BATCH_SIZE = 100

# redis-py's method signatures are shared between the sync and async
# clients and typed accordingly (`Awaitable[Any] | Any`); this worker only
# ever uses the sync `redis.Redis`, so each call site below casts its
# result back to the shape the sync client actually returns.
_StreamEntry = tuple[str, dict[str, str]]
_ReadGroupReply = list[tuple[str, list[_StreamEntry]]]
_PendingEntry = dict[str, Any]


class Consumer:
    """Drives the worker's main loop: one job at a time, in order (spec
    NFR-04's single active worker), forever until asked to stop.
    """

    def __init__(
        self,
        client: redis.Redis,
        handler: JobHandler,
        consumer_name: str | None = None,
    ) -> None:
        self._client = client
        self._handler = handler
        self._consumer_name = consumer_name or f"worker-{socket.gethostname()}"
        self._stopping = False

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self._request_stop)
        signal.signal(signal.SIGINT, self._request_stop)

    def _request_stop(self, signum: int, _frame: FrameType | None) -> None:
        logger.info("shutdown requested", extra={"signal": signum})
        self._stopping = True

    def should_stop(self) -> bool:
        return self._stopping

    def _ensure_group(self) -> None:
        """Idempotent: the Go API also creates this group at its own
        startup (`queue.Producer.EnsureGroup`); whichever process starts
        first wins. Also the recovery path if the group ever goes missing
        mid-run (see `run_forever`) -- re-creating it is safe and cheap
        either way."""
        try:
            self._client.xgroup_create(STREAM_NAME, GROUP_NAME, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def _read_next(self) -> _ReadGroupReply:
        try:
            return cast(
                _ReadGroupReply,
                self._client.xreadgroup(
                    GROUP_NAME, self._consumer_name, {STREAM_NAME: ">"}, count=1, block=BLOCK_MS
                ),
            )
        except redis.ResponseError as exc:
            if "NOGROUP" not in str(exc):
                raise
            # The stream/group disappeared out from under a running worker
            # (observed in practice, cause not pinned down -- Redis
            # eviction under memory pressure is one candidate). A hard
            # crash here previously took the whole process down, silently
            # abandoning whatever job Redis still had pending for it until
            # something else restarted the container.
            logger.warning("consumer group missing, re-creating", extra={"error": str(exc)})
            self._ensure_group()
            return []

    def run_forever(self) -> None:
        self._ensure_group()
        self._reclaim_stuck_jobs()
        while not self._stopping:
            entries = self._read_next()
            if not entries:
                continue
            _stream_name, messages = entries[0]
            for entry_id, fields in messages:
                if self._stopping:
                    # Picked up right before a shutdown signal: leave it
                    # unacked rather than start a fresh job during teardown.
                    return
                self._process_entry(entry_id, fields)

    def _process_entry(self, entry_id: str, fields: dict[str, str]) -> None:
        analysis_id = fields.get("job_id")
        if not analysis_id:
            logger.error("stream entry missing job_id, dropping", extra={"entry_id": entry_id})
            self._client.xack(STREAM_NAME, GROUP_NAME, entry_id)
            return

        logger.info("processing analysis", extra={"analysis_id": analysis_id, "entry_id": entry_id})
        try:
            terminal = self._handler.handle(analysis_id, self.should_stop)
        except Exception:
            logger.exception(
                "job handler crashed, leaving pending for reclaim",
                extra={"analysis_id": analysis_id},
            )
            return
        if terminal:
            self._client.xack(STREAM_NAME, GROUP_NAME, entry_id)

    def _reclaim_stuck_jobs(self) -> None:
        """Runs at startup: anything still pending after
        `PENDING_CLAIM_MIN_IDLE` belonged to a worker that died mid-stage
        (spec 10.1). Claimed jobs resume normally via `stages_json`; a job
        claimed too many times gives up instead of retrying forever.
        """
        pending = cast(
            list[_PendingEntry],
            self._client.xpending_range(
                STREAM_NAME,
                GROUP_NAME,
                min="-",
                max="+",
                count=RECLAIM_BATCH_SIZE,
                idle=PENDING_CLAIM_MIN_IDLE * 1000,
            ),
        )
        for entry in pending:
            entry_id = cast(str, entry["message_id"])
            delivery_count = cast(int, entry["times_delivered"])
            if delivery_count > MAX_CLAIM_ATTEMPTS:
                self._give_up(entry_id)
                continue

            claimed = cast(
                list[_StreamEntry],
                self._client.xclaim(
                    STREAM_NAME,
                    GROUP_NAME,
                    self._consumer_name,
                    PENDING_CLAIM_MIN_IDLE * 1000,
                    [entry_id],
                ),
            )
            for claimed_id, fields in claimed:
                logger.warning(
                    "reclaimed stuck job",
                    extra={"entry_id": claimed_id, "delivery_count": delivery_count},
                )
                self._process_entry(claimed_id, fields)

    def _give_up(self, entry_id: str) -> None:
        messages = cast(list[_StreamEntry], self._client.xrange(STREAM_NAME, entry_id, entry_id))
        if messages:
            _id, fields = messages[0]
            analysis_id = fields.get("job_id")
            if analysis_id:
                logger.error(
                    "giving up on job after max claim attempts",
                    extra={"analysis_id": analysis_id},
                )
                self._handler.mark_permanently_failed(analysis_id)
        self._client.xack(STREAM_NAME, GROUP_NAME, entry_id)
