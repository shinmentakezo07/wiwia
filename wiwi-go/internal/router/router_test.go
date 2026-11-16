package router

import (
	"testing"
	"time"

	"wiwi-go/internal/config"
	"wiwi-go/internal/providers"
)

func testConfig() *config.Config {
	cfg := &config.Config{}
	cfg.General.MasterKey = "sk"
	cfg.Providers = []config.Provider{
		{Name: "a", ProviderType: "openai", BaseURL: "http://a", Keys: []config.KeyDef{
			{Label: "a1", Key: "x", Weight: 1, Enabled: true},
			{Label: "a2", Key: "y", Weight: 1, Enabled: true},
		}},
		{Name: "b", ProviderType: "anthropic", BaseURL: "http://b", Keys: []config.KeyDef{
			{Label: "b1", Key: "z", Weight: 1, Enabled: true},
		}},
	}
	cfg.ModelList = []config.ModelEntry{
		{ModelName: "g", Params: config.DeploymentParams{Provider: "a", Model: "m1", Weight: 1}},
		{ModelName: "g", Params: config.DeploymentParams{Provider: "b", Model: "m2", Weight: 1}},
		{ModelName: "solo", Params: config.DeploymentParams{Provider: "a", Model: "m1", Weight: 1}},
	}
	cfg.Router.Fallbacks = map[string][]string{"g": {"solo"}}
	cfg.Router.ModelGroupAlias = map[string]config.AliasEntry{"alias": {Target: "g", ForceMapping: true}}
	cfg.Router.NumRetries = 1
	cfg.Router.AllowedFails = 1
	cfg.Router.CooldownTime = 30
	cfg.Router.KeyMaxConsecutiveFails = 2
	cfg.Router.CycleEveryN = 1
	return cfg
}

func TestResolveAlias(t *testing.T) {
	r := New(testConfig())
	if got := r.ResolveGroup("alias"); got != "g" {
		t.Fatalf("alias -> %q", got)
	}
	if !r.HasGroup("g") || r.HasGroup("nope") {
		t.Fatal("HasGroup wrong")
	}
}

func TestPickDeploymentRoundRobin(t *testing.T) {
	r := New(testConfig())
	seen := map[string]int{}
	for i := 0; i < 20; i++ {
		d, err := r.PickDeployment("g", nil)
		if err != nil {
			t.Fatal(err)
		}
		seen[d.Model]++
	}
	if len(seen) != 2 {
		t.Fatalf("expected both deployments, got %v", seen)
	}
	if seen["m1"] != seen["m2"] {
		t.Fatalf("expected even split, got %v", seen)
	}
}

func TestCooldownExcludes(t *testing.T) {
	r := New(testConfig())
	d, _ := r.PickDeployment("g", nil)
	d.MarkCooling(60)
	// next picks should never return the cooled deployment
	for i := 0; i < 5; i++ {
		p, err := r.PickDeployment("g", nil)
		if err != nil {
			t.Fatal(err)
		}
		if p.Model == d.Model {
			t.Fatalf("cooled deployment selected")
		}
	}
}

func TestFallbacks(t *testing.T) {
	r := New(testConfig())
	if got := r.Fallbacks("g", false); len(got) != 1 || got[0] != "solo" {
		t.Fatalf("fallbacks: %v", got)
	}
}

func TestPickKeyRoundRobin(t *testing.T) {
	r := New(testConfig())
	d := r.Deployments("solo")[0]
	seen := map[string]int{}
	for i := 0; i < 10; i++ {
		k, err := d.PickKey(nil)
		if err != nil {
			t.Fatal(err)
		}
		seen[k.Label]++
	}
	if len(seen) != 2 {
		t.Fatalf("expected both keys, got %v", seen)
	}
}

func TestKeyRetirement(t *testing.T) {
	r := New(testConfig())
	r.ApplyThresholds()
	d := r.Deployments("solo")[0]
	k, _ := d.PickKey(nil)
	// two credential failures retire the key (key_max_consecutive_fails=2)
	for i := 0; i < 2; i++ {
		d.OnKeyResult(k, 401, &providers.WiwiError{Status: 401, EType: "authentication_error", Retryable: true})
	}
	if k.Available(time.Now()) {
		t.Fatalf("expected retired key, state=%s", k.State)
	}
}
