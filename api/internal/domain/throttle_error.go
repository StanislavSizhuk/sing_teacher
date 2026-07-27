package domain

import (
	"fmt"
	"time"
)

// ThrottledError wraps a sentinel error with how long the caller should wait
// before trying again, so transport can set a Retry-After header without
// needing to know which throttle produced it.
type ThrottledError struct {
	Err        error
	RetryAfter time.Duration
}

// Error implements the error interface.
func (e *ThrottledError) Error() string {
	return fmt.Sprintf("%s (retry after %s)", e.Err, e.RetryAfter)
}

// Unwrap lets errors.Is/As see through to the wrapped sentinel.
func (e *ThrottledError) Unwrap() error {
	return e.Err
}
