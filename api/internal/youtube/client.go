package youtube

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"ai-vocal-coach/api/internal/sysproc"
)

// metadataTimeout/downloadTimeout bound every yt-dlp invocation (spec 11.3:
// external binaries always run with a timeout). Metadata is a quick network
// call; Download can take longer for a near-limit clip.
const (
	metadataTimeout = 20 * time.Second
	downloadTimeout = 180 * time.Second
)

// downloadFilename is fixed (never derived from video title/id) so the
// output path is always server-controlled, matching the same rule applied
// to uploaded files (spec 11.3).
const downloadFilename = "audio.wav"

// VideoInfo is the subset of yt-dlp metadata this system needs.
type VideoInfo struct {
	ID              string
	Title           string
	DurationSeconds float64
}

// Client wraps yt-dlp.
type Client struct {
	runner    sysproc.Runner
	ytDlpPath string
}

// NewClient builds a Client bound to the given yt-dlp binary.
func NewClient(runner sysproc.Runner, ytDlpPath string) *Client {
	return &Client{runner: runner, ytDlpPath: ytDlpPath}
}

// CheckBinary fails fast if yt-dlp is not on PATH (spec 12.1: fail fast),
// instead of only discovering it on the first import attempt. Only called
// when FEATURE_YOUTUBE_IMPORT is enabled.
func CheckBinary(ytDlpPath string) error {
	return sysproc.LookPath(ytDlpPath)
}

type ytDlpMetadata struct {
	ID       string  `json:"id"`
	Title    string  `json:"title"`
	Duration float64 `json:"duration"`
}

// Metadata fetches the video's id/title/duration without downloading it, so
// the duration limit can be enforced before spending any bandwidth (FR-12).
func (c *Client) Metadata(ctx context.Context, videoURL string) (VideoInfo, error) {
	ctx, cancel := context.WithTimeout(ctx, metadataTimeout)
	defer cancel()

	out, stderr, err := c.runner.Run(ctx, c.ytDlpPath, []string{
		"--dump-single-json",
		"--no-playlist",
		"--no-warnings",
		"--skip-download",
		videoURL,
	})
	if err != nil {
		return VideoInfo{}, fmt.Errorf("fetch youtube metadata (%s): %w", strings.TrimSpace(string(stderr)), err)
	}

	var meta ytDlpMetadata
	if err := json.Unmarshal(out, &meta); err != nil {
		return VideoInfo{}, fmt.Errorf("parse youtube metadata: %w", err)
	}
	if meta.ID == "" {
		return VideoInfo{}, fmt.Errorf("youtube metadata missing video id")
	}
	return VideoInfo{ID: meta.ID, Title: meta.Title, DurationSeconds: meta.Duration}, nil
}

// Download fetches videoURL's audio track into destDir and returns its
// path. Callers are expected to have already checked Metadata's duration
// (FR-12), so this does no size/duration validation of its own -- that
// happens uniformly for every source in the media package afterward.
func (c *Client) Download(ctx context.Context, videoURL, destDir string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, downloadTimeout)
	defer cancel()

	outputPath := filepath.Join(destDir, downloadFilename)
	_, stderr, err := c.runner.Run(ctx, c.ytDlpPath, []string{
		"-x", "--audio-format", "wav",
		"--no-playlist",
		"--no-warnings",
		"-o", outputPath,
		videoURL,
	})
	if err != nil {
		return "", fmt.Errorf("download youtube audio (%s): %w", strings.TrimSpace(string(stderr)), err)
	}
	if _, statErr := os.Stat(outputPath); statErr != nil {
		return "", fmt.Errorf("downloaded audio not found at %s: %w", outputPath, statErr)
	}
	return outputPath, nil
}
