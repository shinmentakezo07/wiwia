package auth

import "testing"

func TestSpendBufferFlush(t *testing.T) {
	svc := newSvc(t)
	_, v, _ := svc.Generate("b", floatPtr(10), nil, nil, nil, 0)
	buf := NewSpendBuffer(0, 100, 1)
	buf.Add(v.ID, 0.2, 0.1)
	buf.Add(v.ID, 0.1, 0.05)
	buf.flush(svc)
	got, _ := svc.Get(v.ID)
	if got.Spend < 0.1499 || got.Spend > 0.1501 {
		t.Fatalf("spend=%v", got.Spend)
	}
	if got.BudgetReserved != 0 {
		t.Fatalf("reserved=%v", got.BudgetReserved)
	}
}

func TestSpendBufferCountsFailedFlush(t *testing.T) {
	svc := newSvc(t)
	_, v, _ := svc.Generate("b", floatPtr(10), nil, nil, nil, 0)
	buf := NewSpendBuffer(0, 100, 1)
	buf.Add(v.ID, 0.2, 0.1)
	// A closed database is the deterministic form of the failure this counter
	// exists for: the batch is lost and the budget cap was not enforced.
	if err := svc.Store().Close(); err != nil {
		t.Fatal(err)
	}
	buf.flush(svc)
	if got := buf.FlushFailures.Load(); got != 1 {
		t.Fatalf("flush failures = %d", got)
	}
}
