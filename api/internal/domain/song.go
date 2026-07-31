package domain

import (
	"time"

	"github.com/google/uuid"
)

// SongSourceType is how a song's audio entered the catalog.
type SongSourceType string

// Valid values of SongSourceType (spec 7, migration 00003).
const (
	SongSourceUpload  SongSourceType = "upload"
	SongSourceYouTube SongSourceType = "youtube"
)

// SongPrepStatus is the cold path's lifecycle state (spec 6.2, 10, migration 00010).
type SongPrepStatus string

// Valid values of SongPrepStatus.
const (
	SongPrepPending    SongPrepStatus = "pending"
	SongPrepProcessing SongPrepStatus = "processing"
	SongPrepReady      SongPrepStatus = "ready"
	SongPrepFailed     SongPrepStatus = "failed"
)

// Song is a reference track in the shared catalog. It is deduplicated by
// ContentHash (sha256 of the normalized audio for uploads, or
// "youtube:<video_id>" for YouTube imports) so its vocal stem and lyrics are
// ever processed once no matter how many users submit the same song (spec 6.6).
type Song struct {
	ID          uuid.UUID
	SourceType  SongSourceType
	SourceURL   *string // set for SongSourceYouTube, nil for uploads
	ContentHash string
	Title       string
	Artist      *string
	DurationSec int

	// LyricsJSON is the cold path's transcription output (spec 6.4 stage
	// P3), nil until PrepStatus reaches PrepReady -- and stays nil forever
	// if P3 failed, since it is an optional stage (LyricsAvailable is the
	// authoritative flag, spec FR-18).
	LyricsJSON []byte
	// LyricsAvailable is false when P3 (Whisper) failed or timed out for
	// this song -- transcription is optional and never blocks the cold
	// path (FR-18), so this is the only reliable signal, not "LyricsJSON != nil".
	LyricsAvailable bool
	// ReferencePitch is the packed float32 pitch curve the cold path's P4
	// stage produces, stored as bytea rather than JSON text (spec 7.3) --
	// this API never parses it, only carries it, so the wire format change
	// needs no other code here to change.
	ReferencePitch []byte
	// VocalStemPath is where the cold path's P2 stage (Demucs) wrote the
	// isolated reference vocal, in the song-cache volume (spec 6.6). Nil
	// until PrepStatus reaches PrepReady.
	VocalStemPath *string

	// PrepStatus is the cold path's current state (spec 6.2, 10.1).
	PrepStatus SongPrepStatus
	// PrepStage is the P-stage currently running, nil when not processing (FR-14).
	PrepStage *string
	// PrepErrorCode is set once PrepStatus is PrepFailed (FR-17).
	PrepErrorCode *string
	// PreparedAt is when PrepStatus last reached PrepReady, nil until then.
	PreparedAt *time.Time

	CreatedAt time.Time
}

// ReadyForAnalysis reports whether the reference has finished separation and
// transcription (FR-14): the cold path has reached PrepReady.
func (s *Song) ReadyForAnalysis() bool {
	return s.PrepStatus == SongPrepReady
}
