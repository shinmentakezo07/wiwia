package core

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"wiwi-go/internal/config"
	"wiwi-go/internal/cost"
	"wiwi-go/internal/ir"
	"wiwi-go/internal/router"
)

func TestCompleteFailover(t *testing.T) {
	bad := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, `{"error":{"message":"boom"}}`, 500)
	}))
	defer bad.Close()
	good := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"choices": []map[string]any{{"message": map[string]any{"content": "ok"}, "finish_reason": "stop"}},
			"usage":   map[string]any{"prompt_tokens": 1, "completion_tokens": 1},
		})
	}))
	defer good.Close()

	cfg := &config.Config{}
	cfg.General.MasterKey = "sk"
	cfg.Providers = []config.Provider{
		{Name: "bad", ProviderType: "openai", BaseURL: bad.URL, Keys: []config.KeyDef{{Label: "k", Key: "x", Weight: 1, Enabled: true}}},
		{Name: "good", ProviderType: "openai", BaseURL: good.URL, Keys: []config.KeyDef{{Label: "k", Key: "x", Weight: 1, Enabled: true}}},
	}
	cfg.ModelList = []config.ModelEntry{
		{ModelName: "g", Params: config.DeploymentParams{Provider: "bad", Model: "m", Weight: 1}},
		{ModelName: "g", Params: config.DeploymentParams{Provider: "good", Model: "m", Weight: 1}},
	}
	cfg.Router.NumRetries = 1
	cfg.Router.AllowedFails = 1
	cfg.Router.CooldownTime = 60
	cfg.Router.CycleEveryN = 1

	rt := router.New(cfg)
	gw := New(rt, cost.NewEngine())
	ctx := &RequestContext{
		Surface: SurfaceChat, Group: "g",
		IRReq:    ir.Request{Model: "g", Messages: []ir.Message{{Role: ir.RoleUser, Parts: []ir.Part{ir.NewTextPart("hi")}}}},
		Metadata: map[string]any{},
	}
	turn, we := gw.Complete(ctx)
	if we != nil {
		t.Fatalf("expected failover success, got %v", we)
	}
	if turn.Text != "ok" {
		t.Fatalf("text=%q", turn.Text)
	}
	if len(ctx.Attempts) < 1 {
		t.Fatalf("expected attempts recorded")
	}
}
