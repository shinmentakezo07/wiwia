// Package ratelimit implements per-bucket sliding-window request and token
// limits. A bucket is any caller-chosen key (typically key id + limit kind).
package ratelimit

import (
	"sync"
	"time"
)

// window is the sliding window width; every limit is expressed per minute.
const window = 60 * time.Second

// sweepThreshold bounds the bucket map: past this many live buckets a new
// bucket triggers a sweep of idle ones.
const sweepThreshold = 2048

type event struct {
	at     time.Time
	tokens int
}

// bucket is one sliding window. Its mutex is only ever taken while holding the
// Limiter's map mutex, or without it (never the other way round), so the lock
// order is map -> bucket everywhere.
type bucket struct {
	mu     sync.Mutex
	events []event
}

// Limiter is safe for concurrent use.
type Limiter struct {
	mu      sync.Mutex
	buckets map[string]*bucket
	now     func() time.Time // swappable clock for tests
}

// New returns an empty limiter.
func New() *Limiter {
	return &Limiter{buckets: make(map[string]*bucket), now: time.Now}
}

// Allow admits one request against the bucket's limits and records its token
// estimate. rpm or tpm <= 0 means that dimension is unlimited. When denied,
// retryAfter is the number of seconds until enough of the oldest events leave
// the window for the request to fit.
func (l *Limiter) Allow(name string, rpm, tpm, estTokens int) (ok bool, retryAfter float64) {
	if rpm <= 0 && tpm <= 0 {
		return true, 0
	}
	if estTokens < 0 {
		estTokens = 0
	}
	b := l.bucket(name)
	now := l.now()
	cutoff := now.Add(-window)

	b.mu.Lock()
	defer b.mu.Unlock()

	// Drop events that left the window.
	live := b.events[:0]
	for _, ev := range b.events {
		if ev.at.After(cutoff) {
			live = append(live, ev)
		}
	}
	b.events = live

	if rpm > 0 && len(b.events) >= rpm {
		return false, timeLeft(b.events[0].at, now)
	}
	if tpm > 0 {
		used := 0
		for _, ev := range b.events {
			used += ev.tokens
		}
		if used+estTokens > tpm {
			// A single request larger than the whole budget can never fit;
			// otherwise wait for the oldest events covering the shortfall.
			wait := window
			need := used + estTokens - tpm
			freed := 0
			for _, ev := range b.events {
				freed += ev.tokens
				if freed >= need {
					wait = ev.at.Add(window).Sub(now)
					break
				}
			}
			if wait <= 0 {
				wait = time.Millisecond
			}
			return false, wait.Seconds()
		}
	}

	b.events = append(b.events, event{at: now, tokens: estTokens})
	return true, 0
}

// timeLeft is the time until an event recorded at `at` leaves the window.
func timeLeft(at, now time.Time) float64 {
	d := at.Add(window).Sub(now)
	if d <= 0 {
		return 0
	}
	return d.Seconds()
}

func (l *Limiter) bucket(name string) *bucket {
	l.mu.Lock()
	defer l.mu.Unlock()
	if b, ok := l.buckets[name]; ok {
		return b
	}
	if len(l.buckets) >= sweepThreshold {
		l.sweepLocked()
	}
	b := &bucket{}
	l.buckets[name] = b
	return b
}

// sweepLocked drops buckets with no live events. Callers hold l.mu.
func (l *Limiter) sweepLocked() {
	cutoff := l.now().Add(-window)
	for name, b := range l.buckets {
		b.mu.Lock()
		idle := true
		for _, ev := range b.events {
			if ev.at.After(cutoff) {
				idle = false
				break
			}
		}
		b.mu.Unlock()
		if idle {
			delete(l.buckets, name)
		}
	}
}
