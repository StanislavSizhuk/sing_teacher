"""Redis Streams names shared between the consumer (`consumer.py`), the
prep-completion wake-up publisher (`prep_handler.py`), and anything else in
this process that touches either queue (spec 10.1). Must match
`api/internal/queue/producer.go`'s `AnalysesStreamName`/`AnalysesGroupName`/
`SongsPrepStreamName`/`SongsPrepGroupName` exactly -- they name the two
channels the Go API and this worker agree on.
"""

from __future__ import annotations

ANALYSES_STREAM_NAME = "analyses:run"
ANALYSES_GROUP_NAME = "analyses:workers"

SONGS_PREP_STREAM_NAME = "songs:prep"
SONGS_PREP_GROUP_NAME = "songs:prep:workers"
