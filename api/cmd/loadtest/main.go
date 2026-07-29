// Command loadtest fires a burst of concurrent analysis submissions at a
// running AI Vocal Coach stack to prove two things a demo can't: the queue
// admits at most its configured cap under real concurrent HTTP load, and
// the server stays up and responsive throughout (spec 18, stage E6's "20
// concurrent tasks don't crash the server"). It talks to the real API over
// HTTP, using mailhog to complete real account verification -- see
// docs/LOAD_TESTING.md for how to run it against docker-compose.dev.yml.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"
)

// referenceSongDuration and recordingDuration are short on purpose: this
// fixture only needs to pass go-api's own ffprobe/magic-byte checks, not
// produce a meaningful ML result (the burst never waits on the worker).
const (
	referenceSongDuration = 3.0
	recordingDuration     = 2.0
	sampleRate            = 22050
)

// loadTestPassword satisfies spec 9.1's policy (>=10 chars, not a common
// password) for every synthetic account this run creates.
const loadTestPassword = "LoadTest-Runner-Password-2026"

func main() {
	var (
		baseURL     = flag.String("base-url", "http://localhost:8080", "go-api base URL (root, not /api/v1)")
		mailhogURL  = flag.String("mailhog-url", "http://localhost:8025", "mailhog base URL, used to read verification codes")
		concurrency = flag.Int("concurrency", 25, "concurrent analysis submissions to fire; must exceed the server's QUEUE_MAX_LENGTH (default 20) to actually exercise the 429 boundary")
		timeout     = flag.Duration("timeout", 3*time.Minute, "overall run timeout")
	)
	flag.Parse()

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(ctx, *timeout)
	defer cancel()

	if err := run(ctx, *baseURL, *mailhogURL, *concurrency); err != nil {
		log.Fatalf("load test failed: %v", err)
	}
	log.Println("load test passed")
}

func run(ctx context.Context, baseURL, mailhogURL string, concurrency int) error {
	if concurrency < 1 {
		return errors.New("-concurrency must be at least 1")
	}
	c := newClient(baseURL)

	log.Println("checking server health before the burst")
	if err := c.checkHealth(ctx, "/healthz"); err != nil {
		return fmt.Errorf("server not healthy before starting: %w", err)
	}
	if err := c.checkHealth(ctx, "/readyz"); err != nil {
		return fmt.Errorf("server not ready before starting: %w", err)
	}

	log.Printf("provisioning %d verified test users via %s", concurrency, mailhogURL)
	tokens, err := provisionUsers(ctx, c, mailhogURL, concurrency)
	if err != nil {
		return fmt.Errorf("provision test users: %w", err)
	}

	log.Println("uploading a shared reference song")
	songID, err := c.addSong(ctx, tokens[0], "Load Test Song", syntheticWAV(referenceSongDuration, sampleRate))
	if err != nil {
		return fmt.Errorf("upload reference song: %w", err)
	}

	log.Printf("firing %d concurrent POST /analyses requests", concurrency)
	recording := syntheticWAV(recordingDuration, sampleRate)
	results := fireBurst(ctx, c, tokens, songID, recording)

	s := summarize(results)
	s.print()

	log.Println("checking server health after the burst")
	healthErr := c.checkHealth(ctx, "/healthz")
	readyErr := c.checkHealth(ctx, "/readyz")

	log.Println("cleaning up: canceling every analysis this run queued")
	cleanupQueued(ctx, c, results)

	if healthErr != nil || readyErr != nil {
		return fmt.Errorf("server unhealthy after the burst (healthz: %v, readyz: %v) -- treat this as a crash under load", healthErr, readyErr)
	}
	return s.validate()
}

// provisionUsers registers, verifies and logs in `count` distinct accounts
// so the burst can fire one request per user -- USER_ANALYSES_PER_HOUR
// (spec NFR-06) would otherwise contaminate the queue-capacity signal with
// per-user rate-limit 429s indistinguishable at a glance from QUEUE_FULL
// ones. Sequential: only the burst itself needs to be concurrent.
func provisionUsers(ctx context.Context, c *client, mailhogURL string, count int) ([]string, error) {
	runID := time.Now().UnixNano()
	tokens := make([]string, count)
	for i := range count {
		email := fmt.Sprintf("loadtest-%d-%d@example.com", runID, i)
		if err := c.register(ctx, email, loadTestPassword, "Load Test User"); err != nil {
			return nil, fmt.Errorf("register user %d: %w", i, err)
		}
		code, err := fetchVerificationCode(ctx, mailhogURL, email)
		if err != nil {
			return nil, fmt.Errorf("fetch verification code for user %d: %w", i, err)
		}
		if err := c.verify(ctx, email, code); err != nil {
			return nil, fmt.Errorf("verify user %d: %w", i, err)
		}
		token, err := c.login(ctx, email, loadTestPassword)
		if err != nil {
			return nil, fmt.Errorf("log in user %d: %w", i, err)
		}
		tokens[i] = token
	}
	return tokens, nil
}

// burstResult pairs one enqueueOutcome with the token that produced it, so
// cleanupQueued can cancel it as its own owner (analyses are owner-scoped,
// spec 11).
type burstResult struct {
	token string
	enqueueOutcome
	err error
}

// fireBurst starts every submission behind one closed channel so goroutines
// release as close to simultaneously as the Go scheduler allows -- the
// closest a single-process HTTP client can get to a genuine concurrent
// burst.
func fireBurst(ctx context.Context, c *client, tokens []string, songID string, recording []byte) []burstResult {
	results := make([]burstResult, len(tokens))
	start := make(chan struct{})
	var wg sync.WaitGroup
	for i, token := range tokens {
		wg.Add(1)
		go func(i int, token string) {
			defer wg.Done()
			<-start
			outcome, err := c.enqueueAnalysis(ctx, token, songID, recording)
			results[i] = burstResult{token: token, enqueueOutcome: outcome, err: err}
		}(i, token)
	}
	close(start)
	wg.Wait()
	return results
}

// cleanupQueued best-effort cancels every accepted analysis. Failures are
// logged, not fatal: in dev compose python-worker may already have claimed
// the earliest jobs by the time this runs, and Cancel correctly rejects
// canceling a job that already left the queued state (FR-25).
func cleanupQueued(ctx context.Context, c *client, results []burstResult) {
	for _, r := range results {
		if r.StatusCode != http.StatusAccepted || r.AnalysisID == "" {
			continue
		}
		if err := c.cancelAnalysis(ctx, r.token, r.AnalysisID); err != nil {
			log.Printf("cleanup: cancel %s: %v", r.AnalysisID, err)
		}
	}
}
