package httptransport

import (
	"errors"
	"strings"
	"time"

	"ai-vocal-coach/api/internal/domain"
)

// Boundary limits, same rationale as auth's (spec 12.1): reject abusive
// payloads before business logic, not enforce policy.
const (
	maxSongTitleLength  = 200
	maxArtistLength     = 200
	maxYouTubeURLLength = 2048
)

func validateSongTitle(raw string) (string, error) {
	title := strings.TrimSpace(raw)
	if title == "" || len(title) > maxSongTitleLength {
		return "", errors.New("title must be between 1 and 200 characters")
	}
	return title, nil
}

// validateOptionalTitle allows an empty title (YouTube falls back to the
// video's own title) but still bounds its length when one is given.
func validateOptionalTitle(raw string) (string, error) {
	title := strings.TrimSpace(raw)
	if len(title) > maxSongTitleLength {
		return "", errors.New("title must be at most 200 characters")
	}
	return title, nil
}

func validateArtist(raw string) (string, error) {
	artist := strings.TrimSpace(raw)
	if len(artist) > maxArtistLength {
		return "", errors.New("artist must be at most 200 characters")
	}
	return artist, nil
}

func validateYouTubeURLField(raw string) (string, error) {
	url := strings.TrimSpace(raw)
	if url == "" || len(url) > maxYouTubeURLLength {
		return "", errors.New("youtube_url is required")
	}
	return url, nil
}

type songResponse struct {
	ID              string     `json:"id"`
	SourceType      string     `json:"source_type"`
	Title           string     `json:"title"`
	Artist          *string    `json:"artist,omitempty"`
	DurationSec     int        `json:"duration_sec"`
	PrepStatus      string     `json:"prep_status"`
	PrepStage       *string    `json:"prep_stage,omitempty"`
	PrepErrorCode   *string    `json:"prep_error_code,omitempty"`
	LyricsAvailable bool       `json:"lyrics_available"`
	PreparedAt      *time.Time `json:"prepared_at,omitempty"`
	Reused          bool       `json:"reused"`
	CreatedAt       time.Time  `json:"created_at"`
}

func newSongResponse(s *domain.Song, reused bool) songResponse {
	return songResponse{
		ID:              s.ID.String(),
		SourceType:      string(s.SourceType),
		Title:           s.Title,
		Artist:          s.Artist,
		DurationSec:     s.DurationSec,
		PrepStatus:      string(s.PrepStatus),
		PrepStage:       s.PrepStage,
		PrepErrorCode:   s.PrepErrorCode,
		LyricsAvailable: s.LyricsAvailable,
		PreparedAt:      s.PreparedAt,
		Reused:          reused,
		CreatedAt:       s.CreatedAt,
	}
}
