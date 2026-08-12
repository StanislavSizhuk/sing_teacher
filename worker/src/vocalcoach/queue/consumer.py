"""Redis Streams consumer (spec 10.1, ADR-0002): `XREADGROUP` delivery,
`XACK` once a job reaches a terminal outcome, `XAUTOCLAIM`-equivalent
reclaim for a job whose worker died mid-stage.

One `Consumer` instance drives one stream. Parameterized by stream/group
name and reclaim threshold (spec 10.3: 15 min for `analyses:run`, 20 min
for `songs:prep`) rather than duplicated per stream (spec 12.1 DRY) --
`queue.scheduler.Scheduler` owns two instances, one per spec 10.1 stream,
and decides between them (spec 10.2's one-ML-slot-with-priority rule).

Stream/group names must match `api/internal/queue`'s constants exactly
(`queue.streams`) -- they name the channels the Go API and this worker agree on.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol, cast

import redis

logger = logging.getLogger(__name__)


class JobHandler(Protocol):
    """The narrow slice of a job handler (`AnalysisJobHandler`/
    `SongPrepJobHandler`) the consumer needs -- it orchestrates Redis, not
    job lifecycle, so it only calls in (spec 12.2's "interfaces declared by
    the consumer" applied to Python).
    """

    def handle(self, job_id: str, should_stop: Callable[[], bool]) -> bool: ...

    def mark_permanently_failed(self, job_id: str) -> None: ...


BLOCK_MS = 5000
RECLAIM_BATCH_SIZE = 100

# redis-py's method signatures are shared between the sync and async
# clients and typed accordingly (`Awaitable[Any] | Any`); this worker only
# ever uses the sync `redis.Redis`, so each call site below casts its
# result back to the shape the sync client actually returns.
_StreamEntry = tuple[str, dict[str, str]]
ReadGroupReply = list[tuple[str, list[_StreamEntry]]]
_PendingEntry = dict[str, Any]


class Consumer:
    """Drives one Redis Streams queue for this worker process: claim,
    process one job at a time (spec NFR-04/NFR-07's single active ML
    slot -- enforced by the caller never running two `Consumer`s
    concurrently, not by anything in this class), ack, reclaim.
    """

    def __init__(
        self,
        client: redis.Redis,
        handler: JobHandler,
        stream_name: str,
        group_name: str,
        reclaim_min_idle_seconds: int,
        max_claim_attempts: int,
        consumer_name: str,
    ) -> None:
        self._client = client
        self._handler = handler
        self._stream_name = stream_name
        self._group_name = group_name
        self._reclaim_min_idle_seconds = reclaim_min_idle_seconds
        self._max_claim_attempts = max_claim_attempts
        self._consumer_name = consumer_name

    def ensure_group(self) -> None:
        """Idempotent: the Go API also creates this group at its own
        startup (`queue.Producer.EnsureGroup`); whichever process starts
        first wins. Also the recovery path if the group ever goes missing
        mid-run (see `read_next`) -- re-creating it is safe and cheap
        either way."""
        try:
            self._client.xgroup_create(self._stream_name, self._group_name, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def read_next(self, *, block_ms: int | None = None) -> ReadGroupReply:
        """Delivers at most one currently-undelivered entry. `block_ms=0`
        (or omitted with `BLOCK_MS`) blocks up to that long when the stream
        is empty; pass `block_ms=1` for an effectively non-blocking check
        (redis-py's blocking API has no true zero-wait mode)."""
        try:
            return cast(
                ReadGroupReply,
                self._client.xreadgroup(
                    self._group_name,
                    self._consumer_name,
                    {self._stream_name: ">"},
                    count=1,
                    block=BLOCK_MS if block_ms is None else block_ms,
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
            logger.warning(
                "consumer group missing, re-creating",
                extra={"stream": self._stream_name, "error": str(exc)},
            )
            self.ensure_group()
            return []

    def process_entry(
        self, entry_id: str, fields: dict[str, str], should_stop: Callable[[], bool]
    ) -> None:
        job_id = fields.get("job_id")
        if not job_id:
            logger.error(
                "stream entry missing job_id, dropping",
                extra={"stream": self._stream_name, "entry_id": entry_id},
            )
            self._ack_and_remove(entry_id)
            return

        logger.info(
            "processing job",
            extra={"stream": self._stream_name, "job_id": job_id, "entry_id": entry_id},
        )
        try:
            terminal = self._handler.handle(job_id, should_stop)
        except Exception:
            logger.exception(
                "job handler crashed, leaving pending for reclaim",
                extra={"stream": self._stream_name, "job_id": job_id},
            )
            return
        if terminal:
            self._ack_and_remove(entry_id)

    def claim_new_entries(self) -> None:
        """Delivers every currently-undelivered entry to this consumer's
        PEL without processing any of them. Redis Streams has no primitive
        to selectively deliver one arbitrary undelivered entry out of FIFO
        order, so priority scheduling (spec 10.2 rule 2) works the other
        way around: claim everything into the PEL up front (cheap --
        `songs:prep` is capped at 20 entries, spec 10.1), then choose which
        *pending* entry to actually process (`pending_entries` +
        `fields_for`/`process_entry`), which Redis does let a caller pick
        freely.
        """
        self._client.xreadgroup(
            self._group_name,
            self._consumer_name,
            {self._stream_name: ">"},
            count=RECLAIM_BATCH_SIZE,
            block=1,
        )

    def pending_entries(self) -> list[_PendingEntry]:
        """This consumer's own currently-pending (claimed, unacked)
        entries, oldest first (Redis returns `XPENDING` ranges in stream ID
        order)."""
        return cast(
            list[_PendingEntry],
            self._client.xpending_range(
                self._stream_name,
                self._group_name,
                min="-",
                max="+",
                count=RECLAIM_BATCH_SIZE,
                consumername=self._consumer_name,
            ),
        )

    def fields_for(self, entry_id: str) -> dict[str, str] | None:
        messages = cast(
            list[_StreamEntry], self._client.xrange(self._stream_name, entry_id, entry_id)
        )
        return messages[0][1] if messages else None

    def reclaim_stuck_jobs(self) -> None:
        """Runs at startup: anything still pending after
        `reclaim_min_idle_seconds` belonged to a worker that died mid-stage
        (spec 10.1, 10.3). Claimed jobs resume normally via their own
        resumability (`stages_json`/`prep_stages_json`); a job claimed too
        many times gives up instead of retrying forever.
        """
        pending = cast(
            list[_PendingEntry],
            self._client.xpending_range(
                self._stream_name,
                self._group_name,
                min="-",
                max="+",
                count=RECLAIM_BATCH_SIZE,
                idle=self._reclaim_min_idle_seconds * 1000,
            ),
        )
        for entry in pending:
            entry_id = cast(str, entry["message_id"])
            delivery_count = cast(int, entry["times_delivered"])
            if delivery_count > self._max_claim_attempts:
                self._give_up(entry_id)
                continue

            claimed = cast(
                list[_StreamEntry],
                self._client.xclaim(
                    self._stream_name,
                    self._group_name,
                    self._consumer_name,
                    self._reclaim_min_idle_seconds * 1000,
                    [entry_id],
                ),
            )
            for claimed_id, fields in claimed:
                logger.warning(
                    "reclaimed stuck job",
                    extra={
                        "stream": self._stream_name,
                        "entry_id": claimed_id,
                        "delivery_count": delivery_count,
                    },
                )
                self.process_entry(claimed_id, fields, lambda: False)

    def _give_up(self, entry_id: str) -> None:
        fields = self.fields_for(entry_id)
        if fields is not None:
            job_id = fields.get("job_id")
            if job_id:
                logger.error(
                    "giving up on job after max claim attempts",
                    extra={"stream": self._stream_name, "job_id": job_id},
                )
                try:
                    self._handler.mark_permanently_failed(job_id)
                except Exception:
                    # Recording the give-up must never block removing the
                    # entry below: this runs inside reclaim_stuck_jobs, at
                    # startup, before the main loop even begins, so letting
                    # anything escape here (a job_id that was never a real
                    # UUID because it reached the stream outside the normal
                    # producer path, or any other DB failure) crashes the
                    # whole process -- restart policy then repeats the exact
                    # same reclaim on the exact same entry forever, which is
                    # the "retrying forever" this method exists to prevent.
                    logger.exception(
                        "failed to record permanent failure, removing entry anyway",
                        extra={"stream": self._stream_name, "job_id": job_id},
                    )
        self._ack_and_remove(entry_id)

    def _ack_and_remove(self, entry_id: str) -> None:
        """A stream entry an outcome was already recorded for (spec 10.1:
        Postgres's status column is the source of truth) must not keep
        counting against `Producer.Length`'s `XLEN`-based queue-full check
        (spec 10, FR-24) forever -- `XACK` alone only clears this consumer
        group's pending-entries list, it does not shrink the stream, so
        without this the queue would eventually read as permanently full
        regardless of how many jobs are actually in flight. Best-effort,
        same as `Producer.Remove`'s own `XDEL`: Postgres already has the
        real outcome by the time this runs.
        """
        self._client.xack(self._stream_name, self._group_name, entry_id)
        self._client.xdel(self._stream_name, entry_id)
