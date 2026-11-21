package server

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"wiwi-go/internal/config"
	"wiwi-go/internal/core"
	"wiwi-go/internal/logging"
)

func TestMetricsRouteRegisteredWhenDisabled(t *testing.T) {
	app, _ := newCountingApp(t, nil) // prometheus_enabled defaults false
	w := do(t, app, "GET", "/metrics", "")
	if w.Code != 404 {
		t.Fatalf("expected 404, got %d", w.Code)
	}
	if !strings.Contains(w.Body.String(), "Prometheus metrics are disabled") {
		t.Fatalf("missing disabled explanation: %q", w.Body.String())
	}
	if rec := doNoAuth(t, app, "GET", "/metrics", ""); rec.Code != 404 {
		t.Fatalf("disabled scrape without a key: expected 404, got %d", rec.Code)
	}
}

func TestMetricsRouteRequiresAdmin(t *testing.T) {
	app, _ := newCountingApp(t, func(c *config.Config) {
		c.Router.PrometheusEnabled = true
	})
	w := do(t, app, "GET", "/metrics", "")
	if w.Code != 200 {
		t.Fatalf("master scrape: %d %s", w.Code, w.Body.String())
	}
	// No bearer at all: the operator surface must not be public.
	if rec := doNoAuth(t, app, "GET", "/metrics", ""); rec.Code != 401 {
		t.Fatalf("unauthenticated scrape: expected 401, got %d", rec.Code)
	}
}

func TestMetricsEmitsPythonParityFamilies(t *testing.T) {
	app, _ := newCountingApp(t, func(c *config.Config) {
		c.Router.PrometheusEnabled = true
	})
	body := `{"model":"gpt-test","messages":[{"role":"user","content":"hi"}]}`
	if w := do(t, app, "POST", "/v1/chat/completions", body); w.Code != 200 {
		t.Fatalf("request: %d %s", w.Code, w.Body.String())
	}
	w := do(t, app, "GET", "/metrics", "")
	if w.Code != 200 {
		t.Fatalf("scrape: %d %s", w.Code, w.Body.String())
	}
	text := w.Body.String()
	for _, want := range []string{
		`wiwi_requests_total 1`,
		`wiwi_tokens_total{kind="input"} 1`,
		`wiwi_tokens_total{kind="output"} 1`,
		`wiwi_requests_by_status{status="200"} 1`,
		`wiwi_requests_by_provider{provider="p"} 1`,
		`# TYPE wiwi_ttft_ms summary`,
		`wiwi_tps_sample_ratio`,
		`wiwi_spend_charge_failures_total 0`,
		`wiwi_realtime_usage_missing_total 0`,
		`wiwi_prompt_cache_hit_rate 0`,
	} {
		if !strings.Contains(text, want) {
			t.Fatalf("missing %q in:\n%s", want, text)
		}
	}

	// Quantiles come from the event window; drive them deterministically so
	// the assertion does not depend on a sub-millisecond local request.
	events := []logging.Event{{Type: "request", Data: map[string]any{
		"latency_ms": 10.0, "ttft_ms": 4.0, "tps": 20.0, "status": 200, "provider": "p",
	}}}
	out := renderMetrics(events, app.State.Totals.Snapshot(), metricLossCounters{})
	for _, want := range []string{
		`wiwi_request_duration_ms{quantile="0.5"} 10.0`,
		`wiwi_request_duration_ms{quantile="0.95"} 10.0`,
		`wiwi_ttft_ms{quantile="0.95"} 4.0`,
		`wiwi_tps{quantile="0.99"} 20.0`,
		`wiwi_tps_sample_ratio 1`,
	} {
		if !strings.Contains(out, want) {
			t.Fatalf("synthetic window missing %q in:\n%s", want, out)
		}
	}
}

func TestRealtimeAdmissionRefusals(t *testing.T) {
	cases := []struct {
		name     string
		mutate   func(*config.Config)
		key      func(*testing.T, *App) (string, bool)
		path     string
		wantCode int
	}{
		{
			name: "unknown model",
			mutate: func(c *config.Config) {
				c.Realtime.Enabled = true
			},
			path:     "/v1/realtime?model=nope",
			wantCode: 404,
		},
		{
			name: "provider without realtime",
			mutate: func(c *config.Config) {
				c.Realtime.Enabled = true
				c.Providers[0].ProviderType = "anthropic"
			},
			path:     "/v1/realtime?model=gpt-test",
			wantCode: 501,
		},
		{
			name: "model allowlist",
			mutate: func(c *config.Config) {
				c.Realtime.Enabled = true
			},
			key: func(t *testing.T, app *App) (string, bool) {
				plain, _, err := app.State.Auth.Generate("restricted", nil, nil, nil,
					[]string{"other-model"}, 0)
				if err != nil {
					t.Fatal(err)
				}
				return plain, false
			},
			path:     "/v1/realtime?model=gpt-test",
			wantCode: 403,
		},
		{
			name: "rate limit",
			mutate: func(c *config.Config) {
				c.Realtime.Enabled = true
			},
			key: func(t *testing.T, app *App) (string, bool) {
				one := 1
				plain, _, err := app.State.Auth.Generate("limited", nil, &one, nil, nil, 0)
				if err != nil {
					t.Fatal(err)
				}
				// Burn the single RPM slot through the normal HTTP path.
				w := doKey(t, app, plain, "POST", "/v1/chat/completions",
					`{"model":"gpt-test","messages":[{"role":"user","content":"hi"}]}`)
				if w.Code != 200 {
					t.Fatalf("prime: %d %s", w.Code, w.Body.String())
				}
				return plain, false
			},
			path:     "/v1/realtime?model=gpt-test",
			wantCode: 429,
		},
		{
			name: "budget exhausted",
			mutate: func(c *config.Config) {
				c.Realtime.Enabled = true
			},
			key: func(t *testing.T, app *App) (string, bool) {
				budget := 0.001
				plain, vk, err := app.State.Auth.Generate("broke", &budget, nil, nil, nil, 0)
				if err != nil {
					t.Fatal(err)
				}
				if err := app.State.Auth.AddSpend(vk.ID, 1); err != nil {
					t.Fatal(err)
				}
				return plain, false
			},
			path:     "/v1/realtime?model=gpt-test",
			wantCode: 402,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			app, _ := newCountingApp(t, tc.mutate)
			key := "sk-test"
			if tc.key != nil {
				var ok bool
				key, ok = tc.key(t, app)
				_ = ok
			}
			w := doKey(t, app, key, "GET", tc.path, "")
			if w.Code != tc.wantCode {
				t.Fatalf("expected %d, got %d: %s", tc.wantCode, w.Code, w.Body.String())
			}
			if tc.wantCode == 429 && w.Header().Get("Retry-After") == "" {
				t.Fatal("rate-limit refusal missing Retry-After")
			}
		})
	}
}

func TestRealtimeSessionBillsAndLogsPeakUsage(t *testing.T) {
	app, _ := newCountingApp(t, nil)
	deps := app.State.Router.Deployments("gpt-test")
	if len(deps) == 0 {
		t.Fatal("no deployment")
	}
	dep := deps[0]
	key, err := dep.PickKey(nil)
	if err != nil {
		t.Fatal(err)
	}
	ctx := &core.RequestContext{
		Surface: core.SurfaceChat, Started: time.Now(), RequestID: "rt-bill",
		Auth:     &core.AuthInfo{KeyID: "master", IsMaster: true},
		Group:    "gpt-test",
		Metadata: map[string]any{},
	}
	app.finishRealtime(ctx, dep, key, 200, nil, 12, 7, ctx.Started)

	if ctx.Usage == nil || ctx.Usage.PromptTokens != 12 || ctx.Usage.CompletionTokens != 7 {
		t.Fatalf("usage not recorded: %+v", ctx.Usage)
	}
	snap := app.State.Totals.Snapshot()
	if snap["prompt_tokens"] != 12 || snap["completion_tokens"] != 7 {
		t.Fatalf("totals: %v", snap)
	}
	var tokIn, tokOut int
	if err := app.State.Store.QueryRow(
		app.State.Store.Rebind(`SELECT tok_in, tok_out FROM request_logs WHERE request_id=?`),
		"rt-bill").Scan(&tokIn, &tokOut); err != nil {
		t.Fatal(err)
	}
	if tokIn != 12 || tokOut != 7 {
		t.Fatalf("log row: in=%d out=%d", tokIn, tokOut)
	}
	found := false
	for _, e := range app.State.Bus.EventsSince(0) {
		if e.Type == "request" && e.Data["request_id"] == "rt-bill" {
			found = true
			if e.Data["tok_in"] != 12 || e.Data["tok_out"] != 7 {
				t.Fatalf("event usage: %+v", e.Data)
			}
		}
	}
	if !found {
		t.Fatal("realtime session published no request event")
	}
	if app.State.RealtimeUsageMissing.Load() != 0 {
		t.Fatal("priced session counted as usage-missing")
	}

	// A session that ends with no usage is counted, and still logged.
	empty := &core.RequestContext{
		Surface: core.SurfaceChat, Started: time.Now(), RequestID: "rt-empty",
		Auth:  &core.AuthInfo{KeyID: "master", IsMaster: true},
		Group: "gpt-test", Metadata: map[string]any{},
	}
	app.finishRealtime(empty, dep, key, 200, nil, 0, 0, empty.Started)
	if app.State.RealtimeUsageMissing.Load() != 1 {
		t.Fatalf("usage-missing counter = %d", app.State.RealtimeUsageMissing.Load())
	}
	if w := do(t, app, "GET", "/health", ""); !strings.Contains(w.Body.String(), `"realtime_usage_missing":1`) {
		t.Fatalf("health counter missing: %s", w.Body.String())
	}
}

func TestParseRealtimeUsageReadsResponseDone(t *testing.T) {
	in, out, ok := parseRealtimeUsage([]byte(
		`{"type":"response.done","response":{"usage":{"input_tokens":21,"output_tokens":9}}}`))
	if !ok || in != 21 || out != 9 {
		t.Fatalf("got in=%d out=%d ok=%v", in, out, ok)
	}
	if _, _, ok := parseRealtimeUsage([]byte(`{"type":"response.created"}`)); ok {
		t.Fatal("frame without usage parsed as a usage sample")
	}
}

func doKey(t *testing.T, app *App, key, method, path, body string) *httptest.ResponseRecorder {
	t.Helper()
	w := httptest.NewRecorder()
	req, _ := http.NewRequest(method, path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+key)
	app.Router.ServeHTTP(w, req)
	return w
}

func doNoAuth(t *testing.T, app *App, method, path, body string) *httptest.ResponseRecorder {
	t.Helper()
	w := httptest.NewRecorder()
	req, _ := http.NewRequest(method, path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	app.Router.ServeHTTP(w, req)
	return w
}
