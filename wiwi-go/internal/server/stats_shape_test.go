package server

import (
	"encoding/json"
	"strings"
	"testing"
)

// TestStatsOverviewShape guards the contract the console's Dashboard reads:
// every field must exist and be a number, or `x.toFixed()` throws on undefined
// (which is exactly what a Go shape missing requests_per_minute/tps_avg did).
func TestStatsOverviewShape(t *testing.T) {
	app, _ := newTestApp(t)
	w := do(t, app, "GET", "/admin/stats/overview?minutes=60", "")
	if w.Code != 200 {
		t.Fatalf("overview: %d %s", w.Code, w.Body.String())
	}
	var m map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &m); err != nil {
		t.Fatal(err)
	}
	numeric := []string{
		"window_minutes", "generated_at", "requests", "errors", "error_rate",
		"requests_per_minute", "tok_in", "tok_cached", "tok_cache_creation",
		"tok_reasoning", "tok_out", "estimated_requests", "cache_hits",
		"cache_hit_rate", "tps_avg", "tps_p95", "tps_sample_ratio",
		"ttft_p95_ms", "latency_p95_ms", "cost", "cache_savings",
	}
	for _, k := range numeric {
		v, ok := m[k]
		if !ok {
			t.Fatalf("overview missing %q: %v", k, m)
		}
		if _, ok := v.(float64); !ok {
			t.Fatalf("overview %q is not a number: %T", k, v)
		}
	}
}

func TestStatsTimeseriesShape(t *testing.T) {
	app, _ := newTestApp(t)
	w := do(t, app, "GET", "/admin/stats/timeseries?bucket=minute&metric=tokens&minutes=60", "")
	if w.Code != 200 {
		t.Fatalf("timeseries: %d %s", w.Code, w.Body.String())
	}
	var resp struct {
		BucketSeconds int64            `json:"bucket_seconds"`
		Metric        string           `json:"metric"`
		Buckets       []map[string]any `json:"buckets"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	if resp.Metric != "tokens" || resp.BucketSeconds <= 0 {
		t.Fatalf("timeseries header: %+v", resp)
	}
	if len(resp.Buckets) == 0 {
		t.Fatal("timeseries must zero-fill a bounded window")
	}
	for _, k := range []string{"t", "tok_in", "tok_cached", "tok_cache_creation", "tok_reasoning", "tok_out"} {
		if _, ok := resp.Buckets[0][k]; !ok {
			t.Fatalf("token bucket missing %q: %v", k, resp.Buckets[0])
		}
	}

	// The tps metric must also return the shape (zeros), not an error.
	w = do(t, app, "GET", "/admin/stats/timeseries?bucket=minute&metric=tps&minutes=60", "")
	if w.Code != 200 {
		t.Fatalf("tps timeseries: %d %s", w.Code, w.Body.String())
	}
}

func TestAdminProvidersShape(t *testing.T) {
	app, _ := newTestApp(t)
	w := do(t, app, "GET", "/admin/providers", "")
	if w.Code != 200 {
		t.Fatalf("providers: %d %s", w.Code, w.Body.String())
	}
	var resp struct {
		Providers []struct {
			Name         string           `json:"name"`
			ProviderType string           `json:"provider_type"`
			Healthy      bool             `json:"healthy"`
			Keys         []map[string]any `json:"keys"`
		} `json:"providers"`
		AliasToProvider map[string]string `json:"alias_to_provider"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	if len(resp.Providers) != 1 || resp.Providers[0].Name != "p" {
		t.Fatalf("providers: %+v", resp.Providers)
	}
	if resp.Providers[0].ProviderType != "openai" || len(resp.Providers[0].Keys) != 1 {
		t.Fatalf("provider fields: %+v", resp.Providers[0])
	}
	if resp.AliasToProvider == nil {
		t.Fatal("alias_to_provider must be an object, not null")
	}
}

func TestAdminLogsShape(t *testing.T) {
	app, _ := newTestApp(t)
	do(t, app, "POST", "/v1/chat/completions", `{"model":"gpt-test","messages":[{"role":"user","content":"hi"}]}`)
	w := do(t, app, "GET", "/admin/logs/requests?limit=5", "")
	if w.Code != 200 {
		t.Fatalf("logs: %d %s", w.Code, w.Body.String())
	}
	var resp struct {
		Logs []map[string]any `json:"logs"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	if len(resp.Logs) == 0 {
		t.Fatal("expected at least one request log row")
	}
	for _, k := range []string{"ts", "status", "model_group", "provider", "tok_in", "tok_out", "latency_ms", "cost"} {
		if _, ok := resp.Logs[0][k]; !ok {
			t.Fatalf("log row missing %q: %v", k, resp.Logs[0])
		}
	}
}

// TestCreateProviderConsolePayload reproduces the console's Add-provider form,
// which posts {name, provider_type, base_url, label, key} (a single key, not a
// keys[] array). The old Go handler required {provider, keys[]} and rejected it
// with "name, provider and at least one key are required".
func TestCreateProviderConsolePayload(t *testing.T) {
	app, _ := newTestApp(t)
	body := `{"name":"acme","provider_type":"openai","base_url":"https://example.test/v1","label":"default","key":"sk-secret"}`
	w := do(t, app, "POST", "/admin/providers", body)
	if w.Code != 200 {
		t.Fatalf("create provider: %d %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), `"name":"acme"`) {
		t.Fatalf("create response: %s", w.Body.String())
	}
	w = do(t, app, "GET", "/admin/providers", "")
	if !strings.Contains(w.Body.String(), `"name":"acme"`) || !strings.Contains(w.Body.String(), `"provider_type":"openai"`) {
		t.Fatalf("provider missing after create: %s", w.Body.String())
	}
}

// TestCreateProviderDefaultsBaseURL: omitting base_url for a catalog type uses
// that type's default (Python's _default_base_url), so the form can leave it
// blank.
func TestCreateProviderDefaultsBaseURL(t *testing.T) {
	app, _ := newTestApp(t)
	w := do(t, app, "POST", "/admin/providers", `{"name":"oai","provider_type":"openai","key":"sk-x"}`)
	if w.Code != 200 {
		t.Fatalf("create: %d %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "https://api.openai.com/v1") {
		t.Fatalf("default base_url not applied: %s", w.Body.String())
	}
}
