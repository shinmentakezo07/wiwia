package router

import (
	"testing"

	"wiwi-go/internal/providers"
)

func TestMaxInflightExcludesFull(t *testing.T) {
	cfg := testConfig()
	one := 1
	cfg.Router.MaxInflight = &one
	r := New(cfg)
	deps := r.Deployments("g")
	if len(deps) != 2 {
		t.Fatal("expected 2 deployments")
	}
	if !r.ReserveSlot(deps[0], "") {
		t.Fatal("reserve failed")
	}
	for i := 0; i < 5; i++ {
		d, err := r.PickDeployment("g", nil)
		if err != nil {
			t.Fatal(err)
		}
		if d == deps[0] {
			t.Fatal("full deployment selected")
		}
	}
	r.ReleaseSlot(deps[0], "")
}

func TestAffinityPin(t *testing.T) {
	cfg := testConfig()
	cfg.Router.SessionAffinity = true
	r := New(cfg)
	target := r.Deployments("g")[1]
	r.RecordAffinity("sess-1", "", target.Name())
	for i := 0; i < 5; i++ {
		d, err := r.PickDeploymentFor("g", nil, "sess-1", "", 0)
		if err != nil {
			t.Fatal(err)
		}
		if d.Name() != target.Name() {
			t.Fatalf("affinity ignored: %s", d.Name())
		}
	}
}

func TestHealthScoredPrefersHealthy(t *testing.T) {
	cfg := testConfig()
	cfg.Router.HealthModel = "scored"
	cfg.Router.HealthEWMAAlpha = 0.5
	r := New(cfg)
	deps := r.Deployments("g")
	// Make deps[0] look sick.
	for i := 0; i < 20; i++ {
		deps[0].OnKeyResult(deps[0].Provider.Keys[0], 500,
			&providers.WiwiError{Status: 500, EType: "api_connection_error", Retryable: true})
	}
	counts := map[string]int{}
	for i := 0; i < 40; i++ {
		d, err := r.PickDeployment("g", nil)
		if err != nil {
			t.Fatal(err)
		}
		counts[d.Name()]++
	}
	if counts[deps[1].Name()] <= counts[deps[0].Name()] {
		t.Fatalf("scored routing did not prefer the healthy deployment: %v", counts)
	}
}
