package router

import (
	"errors"
	"testing"
)

func cappedRouter(t *testing.T, rpm, tpm *int, n int) (*Router, []*Deployment) {
	t.Helper()
	keys := []*ProviderKey{{Label: "k", Secret: "s", Enabled: true, State: "active"}}
	deps := make([]*Deployment, 0, n)
	for i := 0; i < n; i++ {
		deps = append(deps, &Deployment{
			Group:    "g",
			Model:    "m" + string(rune('a'+i)),
			Provider: &ProviderAccount{Name: "p", Type: "openai", Keys: keys},
			Weight:   1,
			RPM:      rpm,
			TPM:      tpm,
		})
	}
	return &Router{groups: map[string][]*Deployment{"g": deps}, strategy: "round-robin"}, deps
}

// TestDeploymentRPMCap: a deployment at its per-minute request cap is skipped,
// and the next pick uses the other deployment.
func TestDeploymentRPMCap(t *testing.T) {
	rpm := 1
	r, _ := cappedRouter(t, &rpm, nil, 2)
	first, err := r.PickDeployment("g", nil)
	if err != nil {
		t.Fatalf("first pick: %v", err)
	}
	first.ReserveDepSlot("r1", 0)
	second, err := r.PickDeployment("g", nil)
	if err != nil {
		t.Fatalf("second pick should use the other deployment: %v", err)
	}
	if second.Name() == first.Name() {
		t.Fatal("a deployment at its rpm cap must not be picked again")
	}
	second.ReserveDepSlot("r2", 0)
}

// TestRateCapIs429WithHorizon: when every healthy deployment is capped the
// error is a retryable 429 class, not a "nothing healthy" 503 (Python
// AUDIT #101) — the two read completely differently to a client and to an
// operator.
func TestRateCapIs429WithHorizon(t *testing.T) {
	rpm := 1
	r, deps := cappedRouter(t, &rpm, nil, 1)
	d, err := r.PickDeployment("g", nil)
	if err != nil {
		t.Fatal(err)
	}
	d.ReserveDepSlot("r1", 0)
	_, err = r.PickDeployment("g", nil)
	if err == nil {
		t.Fatal("expected a cap error")
	}
	var capErr *RateCapError
	if !errors.As(err, &capErr) {
		t.Fatalf("expected *RateCapError, got %T", err)
	}
	if capErr.RetryAfter < 1 || capErr.RetryAfter > 60 {
		t.Fatalf("retry horizon out of range: %d", capErr.RetryAfter)
	}
	_ = deps
}

// TestRefundFreesCap: a refunded slot is reusable, a settled one is not.
func TestRefundFreesCap(t *testing.T) {
	tpm := 100
	r, _ := cappedRouter(t, nil, &tpm, 1)
	d, err := r.PickDeploymentLimited("g", nil, 60)
	if err != nil {
		t.Fatal(err)
	}
	d.ReserveDepSlot("r1", 60)
	if _, err := r.PickDeploymentLimited("g", nil, 60); err == nil {
		t.Fatal("60+60 > 100 must be refused")
	}
	d.RefundDepSlot("r1")
	if _, err := r.PickDeploymentLimited("g", nil, 60); err != nil {
		t.Fatalf("refunded slot should admit: %v", err)
	}
}

// TestSettledSlotNotRefundable: once usage is known the slot is billed, so a
// late refund must not reclaim cap that was actually consumed.
func TestSettledSlotNotRefundable(t *testing.T) {
	tpm := 100
	r, _ := cappedRouter(t, nil, &tpm, 1)
	d, err := r.PickDeployment("g", nil)
	if err != nil {
		t.Fatal(err)
	}
	d.ReserveDepSlot("r1", 50)
	d.SettleDepTokens("r1", 90)
	d.RefundDepSlot("r1")
	if _, err := r.PickDeploymentLimited("g", nil, 20); err == nil {
		t.Fatal("settled 90 + 20 > 100 must still be refused")
	}
}

// TestAffinityToggle: session_affinity=false really disables the pin instead of
// leaving the default TTL in force.
func TestAffinityToggle(t *testing.T) {
	r, deps := cappedRouter(t, nil, nil, 2)
	r.sessionTTL = 10e9
	r.sessionAffinity = false
	r.RecordAffinity("sess", "", deps[0].Name())
	if pin := r.affinityPin("sess", ""); pin != "" {
		t.Fatalf("affinity disabled but pinned to %q", pin)
	}
	r.sessionAffinity = true
	r.RecordAffinity("sess", "", deps[0].Name())
	if pin := r.affinityPin("sess", ""); pin != deps[0].Name() {
		t.Fatalf("affinity enabled but pin is %q", pin)
	}
}
