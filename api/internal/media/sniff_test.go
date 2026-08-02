package media_test

import (
	"testing"

	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/media"
)

func TestSniff(t *testing.T) {
	tests := []struct {
		name   string
		data   []byte
		want   media.Format
		wantOK bool
	}{
		{"wav", append([]byte("RIFF____WAVEfmt "), 0), media.FormatWAV, true},
		{"flac", []byte("fLaC\x00\x00\x00\x22"), media.FormatFLAC, true},
		{"ogg", []byte("OggS\x00\x02\x00\x00"), media.FormatOGG, true},
		{"m4a/mp4 ftyp box", []byte("\x00\x00\x00\x20ftypM4A \x00\x00\x02\x00"), media.FormatM4A, true},
		{
			"webm (MediaRecorder's default in Chrome/Firefox, FR-20)",
			[]byte{0x1A, 0x45, 0xDF, 0xA3, 0x01, 0x00, 0x00, 0x00},
			media.FormatWebM, true,
		},
		{"mp3 with id3 tag", []byte("ID3\x04\x00\x00\x00\x00\x00\x00"), media.FormatMP3, true},
		{"mp3 bare frame sync", []byte{0xFF, 0xFB, 0x90, 0x00}, media.FormatMP3, true},
		{"too short", []byte{0x00}, "", false},
		{"unrelated binary data", []byte("%PDF-1.4 definitely not audio"), "", false},
		{"empty", nil, "", false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, ok := media.Sniff(tt.data)
			require.Equal(t, tt.wantOK, ok)
			require.Equal(t, tt.want, got)
		})
	}
}
