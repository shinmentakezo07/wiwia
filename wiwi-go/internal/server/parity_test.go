package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"

	"wiwi-go/internal/config"
	"wiwi-go/internal/core"
	"wiwi-go/internal/ir"
)

func newCountingApp(t *testing.T, mutate func(*config.Config)) (*App, *int32) {
	t.Helper()
	var calls int32
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"choices": []map[string]any{{"message": map[string]any{"content": "ok"}, "finish_reason": "stop"}},
			"usage":   map[string]any{"prompt_tokens": 1, "completion_tokens": 1},
		})
	}))
	t.Cleanup(up.Close)
	cfg := &config.Config{}
	cfg.General.MasterKey = "sk-test"
	cfg.General.DatabaseURL = "sqlite:///" + filepath.Join(t.TempDir(), "p.db")
	cfg.Providers = []config.Provider{{
		Name: "p", ProviderType: "openai", BaseURL: up.URL,
		Keys: []config.KeyDef{{Label: "k", Key: "x", Weight: 1, Enabled: true}},
	}}
	cfg.ModelList = []config.ModelEntry{{ModelName: "gpt-test", Params: config.DeploymentParams{Provider: "p", Model: "gpt-test", Weight: 1}}}
	if mutate != nil {
		mutate(cfg)
	}
	app, err := New(cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(app.Close)
	return app, &calls
}

func TestGlobalRateLimit(t *testing.T) {
	app, _ := newCountingApp(t, func(c *config.Config) {
		one := 1
		c.Router.GlobalRPM = &one
	})
	body := `{"model":"gpt-test","messages":[{"role":"user","content":"hi"}]}`
	if w := do(t, app, "POST", "/v1/chat/completions", body); w.Code != 200 {
		t.Fatalf("first: %d %s", w.Code, w.Body.String())
	}
	if w := do(t, app, "POST", "/v1/chat/completions", body); w.Code != 429 {
		t.Fatalf("expected 429, got %d", w.Code)
	}
}

func TestCacheBypassHeader(t *testing.T) {
	app, calls := newCountingApp(t, func(c *config.Config) {
		c.Cache.Enabled = true
		c.Cache.TTLS = 3600
	})
	body := `{"model":"gpt-test","messages":[{"role":"user","content":"hi"}]}`
	do(t, app, "POST", "/v1/chat/completions", body)
	do(t, app, "POST", "/v1/chat/completions", body) // cache hit
	if n := atomic.LoadInt32(calls); n != 1 {
		t.Fatalf("expected one upstream call for a cache hit, got %d", n)
	}
	w := httptest.NewRecorder()
	req, _ := http.NewRequest("POST", "/v1/chat/completions", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer sk-test")
	req.Header.Set("x-wiwi-no-cache", "true")
	app.Router.ServeHTTP(w, req)
	if n := atomic.LoadInt32(calls); n != 2 {
		t.Fatalf("bypass header did not force an upstream call, got %d", n)
	}
}

func TestUnpricedReject(t *testing.T) {
	app, _ := newCountingApp(t, func(c *config.Config) {
		c.General.UnpricedModelPolicy = "reject"
	})
	w := do(t, app, "POST", "/v1/chat/completions", `{"model":"gpt-test","messages":[{"role":"user","content":"hi"}]}`)
	if w.Code != 503 {
		t.Fatalf("expected 503 for unpriced model, got %d %s", w.Code, w.Body.String())
	}
}

func TestResponseChaining(t *testing.T) {
	app, _ := newCountingApp(t, nil)
	w := do(t, app, "POST", "/v1/responses", `{"model":"gpt-test","input":"hi","store":true}`)
	if w.Code != 200 {
		t.Fatalf("create: %d %s", w.Code, w.Body.String())
	}
	var out map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &out)
	id, _ := out["id"].(string)
	if id == "" {
		t.Fatal("no response id")
	}
	w = do(t, app, "POST", "/v1/responses", `{"model":"gpt-test","input":"again","previous_response_id":"`+id+`"}`)
	if w.Code != 200 {
		t.Fatalf("chained: %d %s", w.Code, w.Body.String())
	}
}

func TestBackupRestoreKeys(t *testing.T) {
	app, _ := newCountingApp(t, nil)
	w := do(t, app, "POST", "/admin/keys/generate", `{"name":"keepme"}`)
	var gen map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &gen)
	plain, _ := gen["key"].(string)
	id, _ := gen["id"].(string)
	w = do(t, app, "GET", "/admin/backup", "")
	var backup map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &backup)
	if backup["keys"] == nil || backup["users"] == nil {
		t.Fatalf("backup missing keys/users: %v", backup)
	}
	do(t, app, "DELETE", "/admin/keys/"+id, "")
	// The deleted key no longer authenticates.
	w2 := httptest.NewRecorder()
	req, _ := http.NewRequest("GET", "/v1/models", nil)
	req.Header.Set("Authorization", "Bearer "+plain)
	app.Router.ServeHTTP(w2, req)
	if w2.Code != 401 {
		t.Fatalf("deleted key still works: %d", w2.Code)
	}
	raw, _ := json.Marshal(backup)
	w = do(t, app, "POST", "/admin/backup/import", string(raw))
	if w.Code != 200 {
		t.Fatalf("import: %d %s", w.Code, w.Body.String())
	}
	w2 = httptest.NewRecorder()
	req, _ = http.NewRequest("GET", "/v1/models", nil)
	req.Header.Set("Authorization", "Bearer "+plain)
	app.Router.ServeHTTP(w2, req)
	if w2.Code != 200 {
		t.Fatalf("restored key does not authenticate: %d %s", w2.Code, w2.Body.String())
	}
}

func TestAliasPooling(t *testing.T) {
	app, _ := newCountingApp(t, func(c *config.Config) {
		c.Providers[0].AliasID = "pool"
	})
	w := do(t, app, "POST", "/v1/chat/completions", `{"model":"pool","messages":[{"role":"user","content":"hi"}]}`)
	if w.Code != 200 {
		t.Fatalf("alias pool: %d %s", w.Code, w.Body.String())
	}
}

func TestRealtimeDisabled(t *testing.T) {
	app, _ := newCountingApp(t, nil)
	w := do(t, app, "GET", "/v1/realtime?model=gpt-test", "")
	if w.Code != http.StatusNotImplemented {
		t.Fatalf("expected 501, got %d", w.Code)
	}
}

func TestRetentionRollup(t *testing.T) {
	app, _ := newCountingApp(t, nil)
	// Insert two rows that are far in the past.
	for i := 0; i < 2; i++ {
		_, _ = app.State.Store.DB().Exec(app.State.Store.Rebind(
			`INSERT INTO request_logs(request_id,ts,key_id,model_group,provider,model_id,status,tok_in,tok_out,tok_cached,tok_reasoning,cost,latency_ms,ttft_ms,error_message,attempts)
			 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`),
			"old"+string(rune('a'+i)), 1, "k", "g", "p", "m", 200, 10, 5, 0, 0, 0.5, 1, 0, "", "[]")
	}
	app.pruneLogs()
	var count int
	_ = app.State.Store.QueryRow(`SELECT COUNT(*) FROM request_rollups`).Scan(&count)
	if count == 0 {
		t.Fatal("expected rollup rows")
	}
}

func TestCacheKeyIncludesAnthropicBeta(t *testing.T) {
	ctx := &core.RequestContext{
		Group: "g",
		IRReq: ir.Request{Model: "claude-x"},
		Auth:  &core.AuthInfo{KeyID: "k"},
	}
	k1 := cacheKeyFor(core.SurfaceMessages, ctx, "interleaved-thinking-2025")
	k2 := cacheKeyFor(core.SurfaceMessages, ctx, "")
	if k1 == k2 {
		t.Fatal("anthropic-beta must be part of the cache key (AUDIT #329)")
	}
	if cacheKeyFor(core.SurfaceMessages, ctx, "interleaved-thinking-2025") != k1 {
		t.Fatal("cache key must be deterministic")
	}
}

func TestHealerStartsWhenEnabled(t *testing.T) {
	app, _ := newCountingApp(t, func(c *config.Config) {
		c.Healer.Enabled = true
	})
	if app.State.Healer == nil {
		t.Fatal("healer.enabled: true must start the healer")
	}
}
