package telemetry

import (
	"testing"

	"wiwi-go/internal/logging"
)

func TestSpanPublishes(t *testing.T) {
	bus := logging.NewBus(16)
	tr := New(true, bus, "wiwi")
	span := tr.Start("wiwi.test", map[string]any{"k": "v"})
	span.End(nil)
	evs := bus.EventsSince(0)
	if len(evs) != 1 || evs[0].Type != "span" || evs[0].Data["span"] != "wiwi.test" {
		t.Fatalf("span event: %+v", evs)
	}
}

func TestDisabledIsNoop(t *testing.T) {
	bus := logging.NewBus(16)
	tr := New(false, bus, "wiwi")
	tr.Start("x", nil).End(nil)
	if len(bus.EventsSince(0)) != 0 {
		t.Fatal("disabled tracer published a span")
	}
}

type fakeErr struct{ msg string }

func (e *fakeErr) Error() string { return e.msg }

func TestTypedNilErrorDoesNotPanic(t *testing.T) {
	bus := logging.NewBus(8)
	tr := New(true, bus, "wiwi")
	var we *fakeErr // typed nil
	span := tr.Start("x", nil)
	span.End(we) // must not panic
	evs := bus.EventsSince(0)
	if len(evs) != 1 {
		t.Fatalf("span not recorded: %+v", evs)
	}
	if _, ok := evs[0].Data["error"]; ok {
		t.Fatal("typed nil must not be recorded as an error")
	}
}
