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
	tot.AddRequest(RequestStats{Prompt: 10, Completion: 5, Cost: 0.5})
	tot.AddRequest(RequestStats{Prompt: 1, Completion: 1, Cost: 0.1, IsErr: true})
	s := tot.Snapshot()
	if s["requests"] != 2 || s["errors"] != 1 || s["prompt_tokens"] != 11 {
		t.Fatalf("totals: %v", s)
	}
}
