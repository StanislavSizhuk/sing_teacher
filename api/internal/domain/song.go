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

	// LyricsJSON is ML pipeline output (spec 6.3 stage 3), populated once the
	// worker exists (stage E3). Nil for every song created in this stage.
	LyricsJSON []byte
	// ReferencePitch is the packed float32 pitch curve the worker produces
	// (spec 6.3 stage 5), stored as bytea rather than JSON text (spec 7.3) --
	// this API never parses it, only carries it, so the wire format change
	// needs no other code here to change.
	ReferencePitch []byte

	// VocalStemProcessed flips true once Demucs+Whisper have run for this
	// song (spec 6.6 cache flag). Nothing sets it yet -- the worker lands in E3.
	VocalStemProcessed bool

	CreatedAt time.Time
}

// ReadyForAnalysis reports whether the reference has finished separation and
// transcription (FR-14). It is always false until the E3 worker exists.
func (s *Song) ReadyForAnalysis() bool {
	return s.VocalStemProcessed
}
