package core

import (
	"math"
	"testing"
	"time"
)

// TestBackoffMatchesPython pins the recovery.Backoff formula:
// min(cap, max(retry_after, base*2**attempt)) + uniform(0, jitter).
func TestBackoffMatchesPython(t *testing.T) {
	b := Backoff{BaseS: 0.5, CapS: 5.0, JitterS: 0}
	// Exponential floor, uncapped.
	if d := b.Delay(0, 0).Seconds(); d != 0.5 {
		t.Fatalf("attempt 0: %v", d)
	}
	if d := b.Delay(1, 0).Seconds(); d != 1.0 {
		t.Fatalf("attempt 1: %v", d)
	}
	if d := b.Delay(3, 0).Seconds(); d != 4.0 {
		t.Fatalf("attempt 3: %v", d)
	}
	// Capped at 5s.
	if d := b.Delay(6, 0).Seconds(); d != 5.0 {
		t.Fatalf("cap: %v", d)
	}
	// Retry-After wins when larger, and is still capped.
	if d := b.Delay(0, 3).Seconds(); d != 3.0 {
		t.Fatalf("retry-after floor: %v", d)
	}
	if d := b.Delay(0, 60).Seconds(); d != 5.0 {
		t.Fatalf("retry-after cap: %v", d)
	}
}

// TestBackoffJitterStaysBounded: jitter is additive and small, never a source
// of an unbounded sleep.
func TestBackoffJitterStaysBounded(t *testing.T) {
	b := RetryBackoff
	for i := 0; i < 200; i++ {
		d := b.Delay(0, 0).Seconds()
		if d < 0.5 || d > 0.5+b.JitterS {
			t.Fatalf("delay out of range: %v", d)
		}
	}
}

// TestBackoffNegativeRetryAfterIgnored: Python only counts a positive hint.
func TestBackoffNegativeRetryAfterIgnored(t *testing.T) {
	b := Backoff{BaseS: 0.5, CapS: 5.0}
	if d := b.Delay(1, -5).Seconds(); d != 1.0 {
		t.Fatalf("negative retry-after must be ignored, got %v", d)
	}
	_ = math.Abs
}

// TestSleepBackoffAbortsOnCallerGone: a disconnected client must not keep a
// retry sleeping for five seconds.
func TestSleepBackoffAbortsOnCallerGone(t *testing.T) {
	done := make(chan struct{})
	close(done)
	start := time.Now()
	if SleepBackoff(done, 5, 0) {
		t.Fatal("expected the sleep to be cut short")
	}
	if time.Since(start) > time.Second {
		t.Fatal("sleep did not abort promptly")
	}
}
