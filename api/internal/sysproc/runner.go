// Package sysproc runs external binaries (ffmpeg, ffprobe, yt-dlp) as an
// argument list, never a shell string (spec 11.3, 12.5). Runner is a thin
// interface over os/exec so callers in internal/media and internal/youtube
// can be unit-tested without those binaries installed.
package sysproc

import (
	"bytes"
	"context"
	"fmt"
	"os/exec"
)

// Runner executes name with args and returns its captured stdout/stderr.
// Callers are responsible for bounding ctx with a timeout: cancellation
// kills the process (spec 11.3 -- external binaries always run with a
// timeout).
type Runner interface {
	Run(ctx context.Context, name string, args []string) (stdout, stderr []byte, err error)
}

// ExecRunner is the production Runner.
type ExecRunner struct{}

// NewExecRunner builds the production Runner.
func NewExecRunner() ExecRunner {
	return ExecRunner{}
}

// Run implements Runner by shelling out via exec.CommandContext. args is
// always a fixed, caller-built list -- never interpolated into a shell
// string -- so it carries no injection risk despite looking dynamic to gosec.
func (ExecRunner) Run(ctx context.Context, name string, args []string) ([]byte, []byte, error) {
	cmd := exec.CommandContext(ctx, name, args...) // #nosec G204 -- args is a fixed list, never a shell string (spec 11.3)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	return stdout.Bytes(), stderr.Bytes(), err
}

// LookPath fails fast at startup if a required external binary is missing
// from PATH (spec 12.1: fail fast with a clear message), instead of only
// discovering it on the first upload.
func LookPath(name string) error {
	if _, err := exec.LookPath(name); err != nil {
		return fmt.Errorf("required binary %q not found: %w", name, err)
	}
	return nil
}
