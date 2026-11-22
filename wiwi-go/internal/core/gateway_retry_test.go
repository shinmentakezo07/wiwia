package core

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"wiwi-go/internal/config"
	"wiwi-go/internal/cost"
	"wiwi-go/internal/ir"
	"wiwi-go/internal/router"
	"wiwi-go/internal/streaming"
)

func gatewayFor(t *testing.T, servers map[string]string, failAfterFirst bool) (*Gateway, *config.Config) {
	t.Helper()
	cfg := &config.Config{}
	cfg.General.MasterKey = "sk"
	cfg.Router.NumRetries = 1
	cfg.Router.AllowedFails = 1
	cfg.Router.CooldownTime = 60
	cfg.Router.CycleEveryN = 1
	for name, url := range servers {
		cfg.Providers = append(cfg.Providers, config.Provider{
			Name: name, ProviderType: "openai", BaseURL: url,
			Keys: []config.KeyDef{{Label: "k", Key: "x", Weight: 1, Enabled: true}},
		})
		cfg.ModelList = append(cfg.ModelList, config.ModelEntry{
			ModelName: "g", Params: config.DeploymentParams{Provider: name, Model: "m", Weight: 1},
		})
	}
	_ = failAfterFirst
	rt := router.New(cfg)
	return New(rt, cost.NewEngine()), cfg
}

func streamCtx() *RequestContext {
	return &RequestContext{
		Surface: SurfaceChat, Group: "g", RequestID: "req-1",
		IRReq:    ir.Request{Model: "g", Messages: []ir.Message{{Role: ir.RoleUser, Parts: []ir.Part{ir.NewTextPart("hi")}}}},
		Metadata: map[string]any{},
	}
}

// TestTruncatedStreamIsNotSuccess: a stream that ends without a terminal frame
// is a failure, not a completed answer. Reporting it as success bills a partial
// answer and marks the deployment healthy, so the next request repeats the
// mistake (Python treats an EOF before Finish as retryable).
func TestTruncatedStreamIsNotSuccess(t *testing.T) {
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		// Content, then EOF with no finish_reason and no [DONE].
		_, _ = w.Write([]byte("data: {\"choices\":[{\"delta\":{\"content\":\"partial\"}}]}\n\n"))
	}))
	defer up.Close()

	gw, _ := gatewayFor(t, map[string]string{"p": up.URL}, false)
	ctx := streamCtx()
	var sawError bool
	for d := range gw.Stream(ctx) {
		if se, ok := d.(streaming.StreamError); ok {
			sawError = true
			if se.Status < 500 {
				t.Fatalf("a truncated upstream should surface as a retryable server error, got %d", se.Status)
			}
		}
	}
	if !sawError {
		t.Fatal("a stream truncated before its terminal frame must not report success")
	}
}

// TestStreamRetryAfterCoolsCredential: a 429 carrying Retry-After must reach the
// key, so the retry backs off for the vendor's hint instead of hammering.
func TestStreamRetryAfterCoolsCredential(t *testing.T) {
	var attempts int
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		attempts++
		w.Header().Set("Retry-After", "2")
		w.WriteHeader(http.StatusTooManyRequests)
		_, _ = w.Write([]byte(`{"error":{"message":"slow down","type":"rate_limit_error"}}`))
	}))
	defer up.Close()

	gw, cfg := gatewayFor(t, map[string]string{"p": up.URL}, false)
	cfg.Router.NumRetries = 1
	ctx := streamCtx()
	start := time.Now()
	for range gw.Stream(ctx) {
	}
	if time.Since(start) < 1500*time.Millisecond {
		t.Fatalf("retry after a 429 with Retry-After: 2 must sleep close to 2s, waited %v",
			time.Since(start))
	}
}

// TestCompletePropagatesRetryAfter: the non-streaming path must read the same
// header, so both surfaces honor the vendor's cooling hint.
func TestCompletePropagatesRetryAfter(t *testing.T) {
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Retry-After", "3")
		w.WriteHeader(http.StatusTooManyRequests)
		_, _ = w.Write([]byte(`{"error":{"message":"slow down"}}`))
	}))
	defer up.Close()

	gw, cfg := gatewayFor(t, map[string]string{"p": up.URL}, false)
	cfg.Router.NumRetries = 0
	_, we := gw.Complete(streamCtx())
	if we == nil {
		t.Fatal("expected the 429 to surface")
	}
	if we.RetryAfter != 3 {
		t.Fatalf("expected RetryAfter=3 from the upstream header, got %v", we.RetryAfter)
	}
}

// TestDeploymentCapYields429: a group whose deployments are all at their cap is
// a retryable 429 with a horizon, not the 503 "no healthy deployment" outage.
func TestDeploymentCapYields429(t *testing.T) {
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"choices": []map[string]any{{"message": map[string]any{"content": "ok"}, "finish_reason": "stop"}},
			"usage":   map[string]any{"prompt_tokens": 1, "completion_tokens": 1},
		})
	}))
	defer up.Close()

	gw, _ := gatewayFor(t, map[string]string{"p": up.URL}, false)
	one := 1
	for _, d := range gw.Router.Deployments("g") {
		d.RPM = &one
	}
	if _, we := gw.Complete(streamCtx()); we != nil {
		t.Fatalf("first request should succeed: %v", we)
	}
	_, we := gw.Complete(streamCtx())
	if we == nil {
		t.Fatal("second request should be capped")
	}
	if we.Status != 429 {
		t.Fatalf("a capped group is a 429, got %d (%s)", we.Status, we.EType)
	}
	if !we.Retryable {
		t.Fatal("a capped group must be retryable")
	}
	if we.RetryAfter < 1 {
		t.Fatalf("a capped group must carry a horizon, got %v", we.RetryAfter)
	}
}
