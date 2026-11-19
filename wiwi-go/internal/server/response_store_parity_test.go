package server

import (
	"testing"
	"time"
)

func TestResponseStoreMasterReadAndSweep(t *testing.T) {
	app, _ := newTestApp(t)
	rs := app.State.Responses
	rs.ttl = 1

	if err := rs.Save("r1", "key-a", map[string]any{"ok": true}); err != nil {
		t.Fatal(err)
	}
	if _, ok := rs.Get("r1", "key-b"); ok {
		t.Fatal("another key must not read the row")
	}
	if _, ok := rs.Get("r1", "master"); !ok {
		t.Fatal("master must cross the key boundary")
	}
	if _, ok := rs.Get("r1", "key-a"); !ok {
		t.Fatal("owner must read its row")
	}

	now := time.Now().Unix()
	if removed := rs.Sweep(now); removed != 0 {
		t.Fatalf("premature sweep removed %d rows", removed)
	}
	if removed := rs.Sweep(now + 3); removed != 1 {
		t.Fatalf("sweep removed %d rows, want 1", removed)
	}
	if _, ok := rs.Get("r1", "key-a"); ok {
		t.Fatal("expired row still readable")
	}
}

func TestResponseStoreZeroTTLKeepsForever(t *testing.T) {
	app, _ := newTestApp(t)
	rs := NewResponseStore(app.State.Store, true, 0)
	if err := rs.Save("keep", "k", map[string]any{"x": 1}); err != nil {
		t.Fatal(err)
	}
	if removed := rs.Sweep(time.Now().Unix() + 10_000_000); removed != 0 {
		t.Fatalf("ttl=0 swept %d rows", removed)
	}
	if _, ok := rs.Get("keep", "k"); !ok {
		t.Fatal("ttl=0 row must be kept")
	}
}
