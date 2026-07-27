package media

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"ai-vocal-coach/api/internal/sysproc"
)

// probeTimeout/transcodeTimeout bound every ffprobe/ffmpeg invocation (spec
// 11.3: external binaries always run with a timeout). Inputs are capped at
// MAX_UPLOAD_MB/MAX_AUDIO_SECONDS, so both are generous relative to the
// actual work a compliant file requires.
const (
	probeTimeout     = 20 * time.Second
	transcodeTimeout = 120 * time.Second
)

// ErrNoAudioStream means ffprobe found no audio stream in the container --
// e.g. a video-only MP4, or a corrupt/empty file.
var ErrNoAudioStream = errors.New("no audio stream found")

// Processor validates and canonicalizes audio via ffprobe/ffmpeg, invoked as
// argument lists through sysproc.Runner (spec 11.3).
type Processor struct {
	runner      sysproc.Runner
	ffmpegPath  string
	ffprobePath string
}

// NewProcessor builds a Processor bound to the given binaries.
func NewProcessor(runner sysproc.Runner, ffmpegPath, ffprobePath string) *Processor {
	return &Processor{runner: runner, ffmpegPath: ffmpegPath, ffprobePath: ffprobePath}
}

// CheckBinaries fails fast if ffmpeg/ffprobe are not on PATH, instead of
// only discovering it on the first upload (spec 12.1: fail fast).
func CheckBinaries(ffmpegPath, ffprobePath string) error {
	if err := sysproc.LookPath(ffmpegPath); err != nil {
		return err
	}
	return sysproc.LookPath(ffprobePath)
}

type probeResult struct {
	Streams []struct {
		CodecType string `json:"codec_type"`
	} `json:"streams"`
	Format struct {
		Duration string `json:"duration"`
	} `json:"format"`
}

// Probe returns the audio duration in seconds. It returns ErrNoAudioStream
// if the container has no audio stream, so a video-only or corrupt file is
// rejected before any further processing.
func (p *Processor) Probe(ctx context.Context, path string) (seconds float64, err error) {
	ctx, cancel := context.WithTimeout(ctx, probeTimeout)
	defer cancel()

	out, stderr, err := p.runner.Run(ctx, p.ffprobePath, []string{
		"-v", "error",
		"-show_entries", "stream=codec_type:format=duration",
		"-of", "json",
		path,
	})
	if err != nil {
		return 0, fmt.Errorf("probe audio (%s): %w", strings.TrimSpace(string(stderr)), err)
	}

	var res probeResult
	if err := json.Unmarshal(out, &res); err != nil {
		return 0, fmt.Errorf("parse ffprobe output: %w", err)
	}

	hasAudio := false
	for _, s := range res.Streams {
		if s.CodecType == "audio" {
			hasAudio = true
			break
		}
	}
	if !hasAudio {
		return 0, ErrNoAudioStream
	}

	seconds, err = strconv.ParseFloat(res.Format.Duration, 64)
	if err != nil {
		return 0, fmt.Errorf("parse duration %q: %w", res.Format.Duration, err)
	}
	return seconds, nil
}

// Transcode re-encodes srcPath into a canonical 16-bit PCM WAV at dstPath,
// dropping any non-audio stream (e.g. embedded album art). This is a
// security sanitization step (spec 11.3: destroys malicious container
// constructs) -- independent of the ML worker's own loudness/resample
// preprocessing (spec 6.3 stage 1, built in E3).
func (p *Processor) Transcode(ctx context.Context, srcPath, dstPath string) error {
	ctx, cancel := context.WithTimeout(ctx, transcodeTimeout)
	defer cancel()

	_, stderr, err := p.runner.Run(ctx, p.ffmpegPath, []string{
		"-y",
		"-i", srcPath,
		"-map", "0:a:0",
		"-vn",
		"-acodec", "pcm_s16le",
		dstPath,
	})
	if err != nil {
		return fmt.Errorf("transcode audio (%s): %w", strings.TrimSpace(string(stderr)), err)
	}
	return nil
}
