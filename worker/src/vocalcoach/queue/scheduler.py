"""Two-stream priority scheduler (spec 10.2): `analyses:run` is served
first whenever both streams have work; a `songs:prep` entry whose song an
analysis is waiting on jumps that stream's own FIFO order; otherwise
`songs:prep` is plain FIFO. Exactly one job (of either kind) runs at a time
in this process (spec NFR-04/NFR-07) -- the single-threaded loop below *is*
that guarantee, nothing else in this codebase enforces it.
"""

from __future__ import annotations

import logging
import signal
from types import FrameType
from typing import Protocol

from vocalcoach.queue.consumer import Consumer, ReadGroupReply

logger = logging.getLogger(__name__)

# How long an empty tick blocks on analyses:run before re-running the full
# priority evaluation (spec 10.2) -- short enough that a songs:prep job
# waiting behind an idle analyses:run stream still starts promptly.
IDLE_BLOCK_MS = 2000


class WaitingSongsLookup(Protocol):
    """Finds which song's cold path should jump `songs:prep`'s queue
    because an analysis is waiting on it (spec 10.2 rule 2)."""

    def oldest_waiting_song_id(self) -> str | None: ...


class Scheduler:
    """Drives the worker's main loop across both spec 10.1 streams,
    forever until asked to stop.
    """

    def __init__(
        self,
        analyses: Consumer,
        songs_prep: Consumer,
        waiting_songs: WaitingSongsLookup,
    ) -> None:
        self._analyses = analyses
        self._songs_prep = songs_prep
        self._waiting_songs = waiting_songs
        self._stopping = False

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self._request_stop)
        signal.signal(signal.SIGINT, self._request_stop)

    def _request_stop(self, signum: int, _frame: FrameType | None) -> None:
        logger.info("shutdown requested", extra={"signal": signum})
        self._stopping = True

    def should_stop(self) -> bool:
        return self._stopping

    def run_forever(self) -> None:
        self._analyses.ensure_group()
        self._songs_prep.ensure_group()
        self._analyses.reclaim_stuck_jobs()
        self._songs_prep.reclaim_stuck_jobs()

        while not self._stopping:
            if self._tick():
                continue
            # Neither stream had anything ready. Block briefly on
            # analyses:run as an idle-wait wake signal -- if it *does*
            # deliver an entry during the wait, that entry is now in this
            # consumer's PEL regardless (XREADGROUP already delivered it),
            # so it is processed immediately rather than discarded (spec
            # 10.3: an undelivered-then-ignored entry would otherwise sit
            # unprocessed until the 15-minute reclaim sweep). A timeout
            # (no entry) just loops back to a fresh `_tick`.
            entries = self._analyses.read_next(block_ms=IDLE_BLOCK_MS)
            if entries and not self._stopping:
                self._process_first(self._analyses, entries)

    def _tick(self) -> bool:
        """One priority evaluation (spec 10.2). Returns True if it found
        and processed exactly one job, False if neither stream had
        anything ready right now."""
        if self._stopping:
            return False

        entries = self._analyses.read_next(block_ms=1)
        if entries:
            self._process_first(self._analyses, entries)
            return True

        priority_song_id = self._waiting_songs.oldest_waiting_song_id()
        if priority_song_id is not None and self._process_priority_prep(priority_song_id):
            return True

        return self._process_next_prep()

    def _process_priority_prep(self, song_id: str) -> bool:
        """Rule 2 (spec 10.2): the prep job for `song_id` jumps
        `songs:prep`'s own FIFO order because an analysis is waiting on it.
        Redis Streams has no primitive to selectively deliver one arbitrary
        undelivered entry out of order, so this claims every currently
        undelivered entry into this consumer's PEL first (cheap --
        `songs:prep` is capped at 20 entries, spec 10.1), then scans the
        PEL for the one matching `song_id` and processes only that one,
        leaving the rest claimed-but-unprocessed for a later tick
        (`_process_next_prep`/a future priority match).
        """
        self._songs_prep.claim_new_entries()
        for entry in self._songs_prep.pending_entries():
            entry_id = entry["message_id"]
            fields = self._songs_prep.fields_for(entry_id)
            if fields is not None and fields.get("job_id") == song_id:
                self._songs_prep.process_entry(entry_id, fields, self.should_stop)
                return True
        # Not found on the stream: already processing, already ready, or a
        # race with a just-published enqueue -- fall through to plain FIFO
        # rather than spin on a target that isn't there right now.
        return False

    def _process_next_prep(self) -> bool:
        """Rule 3/4 (spec 10.2): plain FIFO once nothing needs to jump the
        line. `pending_entries` returns this consumer's PEL in stream ID
        order, so the first entry is the oldest -- whether it just arrived
        via `claim_new_entries` or was left over from a `_process_priority_prep`
        call earlier this run.
        """
        self._songs_prep.claim_new_entries()
        pending = self._songs_prep.pending_entries()
        if not pending:
            return False
        entry_id = pending[0]["message_id"]
        fields = self._songs_prep.fields_for(entry_id)
        if fields is None:
            return False
        self._songs_prep.process_entry(entry_id, fields, self.should_stop)
        return True

    def _process_first(self, consumer: Consumer, entries: ReadGroupReply) -> None:
        _stream_name, messages = entries[0]
        for entry_id, fields in messages:
            if self._stopping:
                # Picked up right before a shutdown signal: leave it
                # unacked rather than start a fresh job during teardown.
                return
            consumer.process_entry(entry_id, fields, self.should_stop)
