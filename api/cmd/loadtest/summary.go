package main

import (
	"fmt"
	"log"
	"net/http"
	"time"
)

// summary tallies a burst's outcomes into the three buckets that matter:
// admitted, correctly rejected as full, and everything else (which should
// never happen and means the run failed).
type summary struct {
	total          int
	accepted       int
	queueFull      int
	transportFails []burstResult
	unexpected     []burstResult
	maxLatency     time.Duration
}

func summarize(results []burstResult) summary {
	s := summary{total: len(results)}
	for _, r := range results {
		if r.err != nil {
			s.transportFails = append(s.transportFails, r)
			continue
		}
		switch {
		case r.StatusCode == http.StatusAccepted:
			s.accepted++
		case r.StatusCode == http.StatusTooManyRequests && r.ProblemCode == "QUEUE_FULL":
			s.queueFull++
		default:
			s.unexpected = append(s.unexpected, r)
		}
		if r.Latency > s.maxLatency {
			s.maxLatency = r.Latency
		}
	}
	return s
}

func (s summary) print() {
	log.Printf("burst results: %d total, %d accepted (202), %d rejected as QUEUE_FULL (429), "+
		"%d transport failures, %d unexpected responses, max latency %s",
		s.total, s.accepted, s.queueFull, len(s.transportFails), len(s.unexpected), s.maxLatency)
	for _, r := range s.transportFails {
		log.Printf("  transport failure: %v", r.err)
	}
	for _, r := range s.unexpected {
		log.Printf("  unexpected response: status=%d code=%s", r.StatusCode, r.ProblemCode)
	}
	if s.queueFull == 0 {
		log.Printf("  note: nothing was rejected as QUEUE_FULL -- raise -concurrency above the " +
			"server's QUEUE_MAX_LENGTH to actually exercise the 429 boundary")
	}
}

// validate is the run's pass/fail gate. It deliberately does not assume a
// specific QUEUE_MAX_LENGTH: the invariant that matters is that every
// request landed in exactly one of the two expected outcomes, and that the
// server answered every single one (no transport failure, the signature of
// a server that went down under load).
func (s summary) validate() error {
	if len(s.transportFails) > 0 {
		return fmt.Errorf("%d/%d requests never got an HTTP response (connection refused/reset/timeout) -- "+
			"the server likely went down under load", len(s.transportFails), s.total)
	}
	if len(s.unexpected) > 0 {
		return fmt.Errorf("%d/%d requests got a response other than 202 or 429 QUEUE_FULL, see log above",
			len(s.unexpected), s.total)
	}
	if s.accepted+s.queueFull != s.total {
		return fmt.Errorf("accounting mismatch: %d accepted + %d queue-full != %d total",
			s.accepted, s.queueFull, s.total)
	}
	if s.accepted == 0 {
		return fmt.Errorf("no request was accepted at all -- queue admission looks broken, not just full")
	}
	return nil
}
