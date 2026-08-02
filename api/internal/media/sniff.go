// Package media validates and canonicalizes uploaded/downloaded audio:
// magic-byte format detection, duration probing and re-encoding to a
// canonical WAV, all via ffprobe/ffmpeg through sysproc.Runner (spec 11.3).
// It knows nothing about domain sentinel errors -- the service layer that
// calls it decides how each failure maps to one (spec 12.2 layering).
package media

// Format is a supported audio container, identified from its magic bytes --
// never from a filename extension (spec 11.3, FR-10: mp3/wav/m4a/flac/ogg,
// plus webm for FR-20's in-browser recordings).
type Format string

// Supported formats.
const (
	FormatMP3  Format = "mp3"
	FormatWAV  Format = "wav"
	FormatFLAC Format = "flac"
	FormatOGG  Format = "ogg"
	FormatM4A  Format = "m4a"
	// FormatWebM: what MediaRecorder actually produces in the two browser
	// engines FR-20's "record in browser" runs in most often -- Chromium
	// and Gecko both default to audio/webm;codecs=opus (only WebKit/Safari
	// uses audio/mp4, already covered by FormatM4A's ftyp case below).
	// Without this, every in-browser recording on Chrome or Firefox failed
	// Sniff and came back UNSUPPORTED_AUDIO_FORMAT before ever reaching
	// ffmpeg, which decodes it natively without any other change needed.
	FormatWebM Format = "webm"
)

// Sniff identifies an audio format from its leading bytes. It reports false
// when data does not start with any signature this system accepts.
func Sniff(data []byte) (Format, bool) {
	switch {
	case len(data) >= 12 && string(data[0:4]) == "RIFF" && string(data[8:12]) == "WAVE":
		return FormatWAV, true
	case len(data) >= 4 && string(data[0:4]) == "fLaC":
		return FormatFLAC, true
	case len(data) >= 4 && string(data[0:4]) == "OggS":
		return FormatOGG, true
	// EBML header (WebM is a constrained Matroska profile; both share this
	// signature). ffprobe still verifies an audio stream is present
	// (Processor.Probe), same as the M4A/MP4 case below -- Sniff only
	// gates the container, not what's inside it.
	case len(data) >= 4 && data[0] == 0x1A && data[1] == 0x45 && data[2] == 0xDF && data[3] == 0xA3:
		return FormatWebM, true
	// ISO base media file format (M4A/MP4): a 4-byte box size, then the
	// "ftyp" box type at offset 4. ffprobe still verifies an audio stream is
	// actually present (Processor.Probe), so a video-only MP4 is rejected later.
	case len(data) >= 8 && string(data[4:8]) == "ftyp":
		return FormatM4A, true
	case len(data) >= 3 && string(data[0:3]) == "ID3":
		return FormatMP3, true
	// Bare MPEG audio frame sync (11 set bits): no ID3 tag, e.g. a stripped mp3.
	case len(data) >= 2 && data[0] == 0xFF && data[1]&0xE0 == 0xE0:
		return FormatMP3, true
	default:
		return "", false
	}
}
