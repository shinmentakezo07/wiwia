package core

import (
	"testing"
	"time"
)

func TestHealerBackoff(t *testing.T) {
	h := NewHealer(nil, 0, 0, 0)
	now := time.Now()
	id := "p/k"
	if !h.due(id, now) {
		t.Fatal("a fresh key is due")
	}
	h.onFailure(id, now)
	if h.due(id, now.Add(time.Second)) {
		t.Fatal("a just-failed key must be backing off")
	}
	first := h.nextProbe[id]
	h.onFailure(id, now)
	if !h.nextProbe[id].After(first) {
		t.Fatalf("second failure should lengthen the backoff: %v -> %v", first, h.nextProbe[id])
	}
	h.onSuccess(id)
	if !h.due(id, now.Add(time.Second)) {
		t.Fatal("success must clear the backoff")
	}
}

func TestHealerBackoffCap(t *testing.T) {
	h := NewHealer(nil, 0, 0, 0)
	h.ProbeBackoffCap = 100 * time.Second
	now := time.Now()
	id := "p/k"
	for i := 0; i < 20; i++ {
		h.onFailure(id, now)
	}
	if d := h.nextProbe[id].Sub(now); d > h.ProbeBackoffCap {
		t.Fatalf("backoff %v exceeded cap %v", d, h.ProbeBackoffCap)
	}
}
