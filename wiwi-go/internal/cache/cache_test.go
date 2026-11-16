package cache

import (
	"sync"
	"testing"
	"time"
)

func TestGetSetTTL(t *testing.T) {
	c := New(true, 3600, 2)
	c.Set("a", []byte("1"))
	if b, ok := c.Get("a"); !ok || string(b) != "1" {
		t.Fatalf("get: %q %v", b, ok)
	}
	c.Set("b", []byte("2"))
	c.Set("c", []byte("3")) // evicts oldest at cap 2
	if _, ok := c.Get("a"); ok {
		t.Fatal("expected eviction")
	}
	if !c.Enabled() {
		t.Fatal("should be enabled")
	}
	if New(false, 1, 1).Enabled() {
		t.Fatal("disabled cache reported enabled")
	}
}

func TestFlightCollapses(t *testing.T) {
	f := NewFlight()
	var mu sync.Mutex
	calls := 0
	started := make(chan struct{})
	release := make(chan struct{})
	fn := func() ([]byte, error) {
		mu.Lock()
		calls++
		mu.Unlock()
		close(started)
		<-release
		return []byte("x"), nil
	}
	done := make(chan struct{}, 5)
	go func() { _, _ = f.Do("k", fn); done <- struct{}{} }()
	<-started // leader is now in flight
	for i := 0; i < 4; i++ {
		go func() { _, _ = f.Do("k", fn); done <- struct{}{} }()
	}
	time.Sleep(20 * time.Millisecond) // let joiners register
	close(release)
	for i := 0; i < 5; i++ {
		<-done
	}
	mu.Lock()
	n := calls
	mu.Unlock()
	if n != 1 {
		t.Fatalf("expected one call, got %d", n)
	}
}
