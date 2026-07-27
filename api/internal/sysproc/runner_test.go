package sysproc_test

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/sysproc"
)

func TestExecRunner_Run_CapturesStdout(t *testing.T) {
	runner := sysproc.NewExecRunner()
	stdout, _, err := runner.Run(context.Background(), "echo", []string{"hello"})
	require.NoError(t, err)
	require.Equal(t, "hello\n", string(stdout))
}

func TestExecRunner_Run_UnknownBinary_ReturnsError(t *testing.T) {
	runner := sysproc.NewExecRunner()
	_, _, err := runner.Run(context.Background(), "definitely-not-a-real-binary-xyz", nil)
	require.Error(t, err)
}

func TestExecRunner_Run_ContextTimeout_KillsProcess(t *testing.T) {
	runner := sysproc.NewExecRunner()
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()

	start := time.Now()
	_, _, err := runner.Run(ctx, "sleep", []string{"5"})
	elapsed := time.Since(start)

	require.Error(t, err)
	require.Less(t, elapsed, 2*time.Second, "context cancellation must kill the process, not wait it out")
}

func TestLookPath_KnownBinary_NoError(t *testing.T) {
	require.NoError(t, sysproc.LookPath("echo"))
}

func TestLookPath_UnknownBinary_Error(t *testing.T) {
	require.Error(t, sysproc.LookPath("definitely-not-a-real-binary-xyz"))
}
