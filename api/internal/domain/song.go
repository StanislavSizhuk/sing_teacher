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

	// LyricsJSON and ReferencePitchJSON are ML pipeline output (spec 6.3
	// stages 3 and 5), populated once the worker exists (stage E3). They are
	// nil for every song created in this stage.
	LyricsJSON         []byte
	ReferencePitchJSON []byte

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
