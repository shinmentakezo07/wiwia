package ratelimit

import "testing"

// TestReserveSettleRefund is the AUDIT #70 contract: an admitted request holds
// a TPM slot, settling replaces the estimate with actual usage, and a refund
// removes only the still-estimated slot — never a billed one.
func TestResumeSettleRefund(t *testing.T) {
	l := New()
	// TPM 100: an estimate of 60 fits once, a second 60 does not.
	if ok, _ := l.Reserve("k", "r1", 0, 100, 60); !ok {
		t.Fatal("first reservation should be admitted")
	}
	if ok, ra := l.Reserve("k", "r2", 0, 100, 60); ok {
		t.Fatal("second reservation should be refused: 60+60 > 100")
	} else if ra <= 0 {
		t.Fatalf("refusal must carry a retry horizon, got %v", ra)
	}
	// Settling r1 down to 20 frees room for r2.
	l.Settle("k", "r1", 20)
	if ok, _ := l.Reserve("k", "r2", 0, 100, 60); !ok {
		t.Fatal("after settle 20+60 <= 100 should admit")
	}
	// Refunding r2 (still estimated) frees its tokens again.
	l.Refund("k", "r2")
	if ok, _ := l.Reserve("k", "r3", 0, 100, 20); !ok {
		t.Fatal("refunded estimate should free room")
	}
}

// TestRefundDoesNotReclaimSettled: a completed request keeps its billed tokens
// even if a late failure path calls Refund.
func TestRefundDoesNotReclaimSettled(t *testing.T) {
	l := New()
	l.Reserve("k", "r1", 0, 100, 50)
	l.Settle("k", "r1", 90)
	l.Refund("k", "r1") // must be a no-op: r1's usage is real
	if ok, _ := l.Reserve("k", "r2", 0, 100, 20); ok {
		t.Fatal("settled 90 + 20 > 100 must be refused")
	}
}

// TestRefundIsIdentityMatched: refunding request A never releases request B's
// slot ("newest" fallback would throttle the wrong request).
func TestRefundIsIdentityMatched(t *testing.T) {
	l := New()
	l.Reserve("k", "a", 0, 100, 50)
	l.Reserve("k", "b", 0, 100, 50)
	l.Refund("k", "a")
	if ok, _ := l.Reserve("k", "c", 0, 100, 60); ok {
		t.Fatal("b still holds its 50, so 50+60 > 100 must be refused")
	}
	l.Refund("k", "b")
	if ok, _ := l.Reserve("k", "c", 0, 100, 60); !ok {
		t.Fatal("with both refunded 60 should be admitted")
	}
}

// TestRPMOnly: rpm is enforced independently of tpm.
func TestRPMOnly(t *testing.T) {
	l := New()
	if ok, _ := l.Reserve("k", "1", 2, 0, 0); !ok {
		t.Fatal("first")
	}
	if ok, _ := l.Reserve("k", "2", 2, 0, 0); !ok {
		t.Fatal("second")
	}
	if ok, ra := l.Reserve("k", "3", 2, 0, 0); ok || ra <= 0 {
		t.Fatal("third must be refused with a horizon")
	}
}

// TestSettleForUnknownRequestRecordsUsage: a request whose reservation aged out
// mid-stream still contributes its real usage to the window.
func TestSettleForUnknownRequestRecordsUsage(t *testing.T) {
	l := New()
	l.Settle("k", "ghost", 100)
	if ok, _ := l.Reserve("k", "r", 0, 100, 1); ok {
		t.Fatal("settled 100 should already saturate the tpm window")
	}
}
