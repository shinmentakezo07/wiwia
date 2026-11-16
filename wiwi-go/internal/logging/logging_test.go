package logging

import "testing"

func TestBusAndTotals(t *testing.T) {
	b := NewBus(4)
	if id := b.Publish("request", map[string]any{"x": 1}); id != 1 {
		t.Fatalf("id=%d", id)
	}
	b.Publish("request", map[string]any{"x": 2})
	evs := b.EventsSince(1)
	if len(evs) != 1 || evs[0].Data["x"] != 2 {
		t.Fatalf("events: %+v", evs)
	}
	ch, cancel := b.Subscribe(2)
	defer cancel()
	b.Publish("audit", map[string]any{"y": 1})
	select {
	case e := <-ch:
		if e.Type != "audit" {
			t.Fatalf("subscriber got %+v", e)
		}
	default:
		t.Fatal("expected subscriber event")
	}

	tot := NewTotals()
	tot.AddRequest(10, 5, 0.5, false)
	tot.AddRequest(1, 1, 0.1, true)
	s := tot.Snapshot()
	if s["requests"] != 2 || s["errors"] != 1 || s["prompt_tokens"] != 11 {
		t.Fatalf("totals: %v", s)
	}
}
