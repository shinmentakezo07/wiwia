package cache

import (
	"sync/atomic"
	"testing"
	"time"
)

func TestFlightZeroWaitersBypasses(t *testing.T) {
	f := NewFlightWithLimits(8, 0)
	var calls int32
	started := make(chan struct{})
	release := make(chan struct{})
	fn := func() ([]byte, error) {
		if atomic.AddInt32(&calls, 1) == 1 {
			close(started)
			<-release
		}
		return []byte("x"), nil
	}
	leader := make(chan struct{})
	go func() { _, _ = f.Do("k", fn); close(leader) }()
	<-started

	follower := make(chan struct{})
	go func() { _, _ = f.Do("k", fn); close(follower) }()
	select {
	case <-follower:
	case <-time.After(time.Second):
		t.Fatal("follower waited despite maxWaiters=0")
	}
	close(release)
	<-leader
	if n := atomic.LoadInt32(&calls); n != 2 {
		t.Fatalf("calls = %d, want 2 (leader + bypassed follower)", n)
	}
}

func TestFlightMaxFlightsBypassesOtherKeys(t *testing.T) {
	f := NewFlightWithLimits(1, 8)
	started := make(chan struct{})
	release := make(chan struct{})
	leader := make(chan struct{})
	go func() {
		_, _ = f.Do("a", func() ([]byte, error) {
			close(started)
			<-release
			return []byte("a"), nil
		})
		close(leader)
	}()
	<-started

	done := make(chan struct{})
	go func() { _, _ = f.Do("b", func() ([]byte, error) { return []byte("b"), nil }); close(done) }()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("second key blocked although the flight table was full")
	}
	close(release)
	<-leader
}
