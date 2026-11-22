package core

import (
	"math"
	"math/rand"
	"time"
)

// Backoff reproduces Python's wiwi.core.recovery.Backoff exactly:
//
//	min(cap, max(retryAfter, base * 2**attempt)) + uniform(0, jitter)
//
// so retry_after competes with the exponential floor and is capped. The router
// pins base=0.5s, cap=5.0s, jitter=0.25s (Python router.py:1521).
type Backoff struct {
	BaseS   float64
	CapS    float64
	JitterS float64
}

// RetryBackoff is the router's historical retry sleep, extracted verbatim.
var RetryBackoff = Backoff{BaseS: 0.5, CapS: 5.0, JitterS: 0.25}

// Delay returns the sleep before retry number attempt (0-based), honoring an
// upstream Retry-After hint.
func (b Backoff) Delay(attempt int, retryAfter float64) time.Duration {
	if attempt < 0 {
		attempt = 0
	}
	exp := b.BaseS * math.Pow(2, float64(attempt))
	ra := 0.0
	if retryAfter > 0 {
		ra = retryAfter
	}
	d := math.Min(b.CapS, math.Max(ra, exp))
	if b.JitterS > 0 {
		d += rand.Float64() * b.JitterS
	}
	if d <= 0 {
		return 0
	}
	return time.Duration(d * float64(time.Second))
}

// SleepBackoff waits Delay before the next attempt, aborting early when the
// caller goes away. It reports false when the wait was cut short.
func SleepBackoff(parentCtxDone <-chan struct{}, attempt int, retryAfter float64) bool {
	d := RetryBackoff.Delay(attempt, retryAfter)
	if d <= 0 {
		return true
	}
	t := time.NewTimer(d)
	defer t.Stop()
	if parentCtxDone == nil {
		<-t.C
		return true
	}
	select {
	case <-t.C:
		return true
	case <-parentCtxDone:
		return false
	}
}
