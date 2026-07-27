package youtube_test

import (
	"testing"

	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/youtube"
)

func TestValidateURL(t *testing.T) {
	tests := []struct {
		name    string
		raw     string
		wantErr bool
	}{
		{"standard watch url", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", false},
		{"bare domain", "https://youtube.com/watch?v=dQw4w9WgXcQ", false},
		{"short url", "https://youtu.be/dQw4w9WgXcQ", false},
		{"mobile subdomain", "https://m.youtube.com/watch?v=dQw4w9WgXcQ", false},
		{"music subdomain", "https://music.youtube.com/watch?v=dQw4w9WgXcQ", false},
		{"http rejected", "http://www.youtube.com/watch?v=dQw4w9WgXcQ", true},
		{"lookalike suffix domain rejected", "https://youtube.com.evil.example/watch?v=x", true},
		{"unrelated host rejected", "https://vimeo.com/12345", true},
		{"userinfo trick rejected", "https://youtube.com@evil.example/x", true},
		{"not a url", "not a url at all", true},
		{"empty", "", true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			_, err := youtube.ValidateURL(tt.raw)
			if tt.wantErr {
				require.ErrorIs(t, err, youtube.ErrInvalidURL)
			} else {
				require.NoError(t, err)
			}
		})
	}
}
