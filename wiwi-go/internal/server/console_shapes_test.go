package server

import (
	"encoding/json"
	"math"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"wiwi-go/internal/ir"
)

// These tests lock the response shapes the web console (web/) reads. They are
// the Go half of the cross-backend contract: the same SPA must run against the
// Python gateway and this port, so an envelope or field rename here is a
// console break, not an internal refactor.

func decodeBody(t *testing.T, body []byte, out any) {
	t.Helper()
	if err := json.Unmarshal(body, out); err != nil {
		t.Fatalf("decode %s: %v", string(body), err)
	}
}

func TestConsoleListEnvelopes(t *testing.T) {
	app, _ := newTestApp(t)

	checks := []struct {
		path   string
		key    string
		object bool
	}{
		{"/admin/keys", "keys", false},
		{"/admin/users", "users", false},
		{"/admin/pricing", "models", false},
		{"/admin/alert-rules", "rules", false},
		{"/admin/logs/proxy", "logs", false},
		{"/admin/logs/audit", "logs", false},
		{"/admin/workbuddy/accounts", "accounts", false},
		{"/admin/cline/settings", "default_models", false},
		{"/public/models", "groups", false},
	}
	for _, c := range checks {
		w := do(t, app, "GET", c.path, "")
		if w.Code != 200 {
			t.Fatalf("%s: %d %s", c.path, w.Code, w.Body.String())
		}
		var m map[string]any
		decodeBody(t, w.Body.Bytes(), &m)
		if _, ok := m[c.key]; !ok {
			t.Fatalf("%s missing %q: %s", c.path, c.key, w.Body.String())
		}
	}

	// /admin/models is the richer envelope.
	w := do(t, app, "GET", "/admin/models", "")
	var models map[string]any
	decodeBody(t, w.Body.Bytes(), &models)
	for _, k := range []string{"groups", "aliases", "provider_aliases", "strategy"} {
		if _, ok := models[k]; !ok {
			t.Fatalf("/admin/models missing %q: %s", k, w.Body.String())
		}
	}

	// The provider catalog is a bare array with the fields the Add-provider
	// form reads.
	w = do(t, app, "GET", "/admin/provider-catalog", "")
	var catalog []map[string]any
	decodeBody(t, w.Body.Bytes(), &catalog)
	if len(catalog) == 0 {
		t.Fatal("provider catalog is empty")
	}
	for _, k := range []string{"provider_type", "label", "default_base_url", "latest_models", "configured", "builtin"} {
		if _, ok := catalog[0][k]; !ok {
			t.Fatalf("catalog entry missing %q: %v", k, catalog[0])
		}
	}
}

func TestConsolePricingIsPerMillion(t *testing.T) {
	app, _ := newTestApp(t)
	w := do(t, app, "PUT", "/admin/pricing/smoke/model",
		`{"input_per_1m":2.5,"output_per_1m":10,"cache_read_per_1m":1.25}`)
	if w.Code != 200 {
		t.Fatalf("put pricing: %d %s", w.Code, w.Body.String())
	}
	var put map[string]any
	decodeBody(t, w.Body.Bytes(), &put)
	if put["model_id"] != "smoke/model" {
		t.Fatalf("model_id: %v", put)
	}
	if got := put["input_per_1m"].(float64); math.Abs(got-2.5) > 1e-9 {
		t.Fatalf("put did not echo per-1M input: %v", put)
	}

	w = do(t, app, "GET", "/admin/pricing", "")
	var list struct {
		Models []map[string]any `json:"models"`
	}
	decodeBody(t, w.Body.Bytes(), &list)
	found := false
	for _, m := range list.Models {
		if m["model_id"] != "smoke/model" {
			continue
		}
		found = true
		if got := m["input_per_1m"].(float64); math.Abs(got-2.5) > 1e-9 {
			t.Fatalf("listed price is not per 1M: %v", m)
		}
	}
	if !found {
		t.Fatalf("custom price missing from list: %s", w.Body.String())
	}

	// The engine bills per token, so a $2.50/1M input must cost 0.0000025 for
	// one prompt token — the regression the per-1M conversion exists for.
	if c, _ := app.State.Cost.Compute("smoke/model", ir.Usage{PromptTokens: 1}); math.Abs(c-0.0000025) > 1e-12 {
		t.Fatalf("engine billed %v for one token at $2.50/1M", c)
	}
}

func TestConsoleDeploymentCrud(t *testing.T) {
	app, _ := newTestApp(t)
	w := do(t, app, "POST", "/admin/model-groups/gpt-test/deployments",
		`{"provider":"p","model_id":"vendor/model","weight":2}`)
	if w.Code != 200 {
		t.Fatalf("add deployment: %d %s", w.Code, w.Body.String())
	}
	var add map[string]any
	decodeBody(t, w.Body.Bytes(), &add)
	dep, ok := add["deployment"].(map[string]any)
	if !ok || dep["model_id"] != "vendor/model" {
		t.Fatalf("add response: %s", w.Body.String())
	}

	w = do(t, app, "GET", "/admin/models", "")
	var models struct {
		Groups []struct {
			Name        string           `json:"name"`
			Deployments []map[string]any `json:"deployments"`
		} `json:"groups"`
	}
	decodeBody(t, w.Body.Bytes(), &models)
	seen := false
	for _, g := range models.Groups {
		for _, d := range g.Deployments {
			if d["provider"] == "p" && d["model_id"] == "vendor/model" {
				seen = true
				for _, k := range []string{"weight", "available", "inflight", "p95_latency_ms", "cooldown_remaining_s"} {
					if _, ok := d[k]; !ok {
						t.Fatalf("deployment missing %q: %v", k, d)
					}
				}
			}
		}
	}
	if !seen {
		t.Fatalf("added deployment not listed: %s", w.Body.String())
	}

	// The console deletes with query params (model ids contain slashes).
	w = do(t, app, "DELETE", "/admin/model-groups/gpt-test/deployments?provider=p&model_id=vendor/model", "")
	if w.Code != 200 {
		t.Fatalf("delete deployment: %d %s", w.Code, w.Body.String())
	}
	var del map[string]any
	decodeBody(t, w.Body.Bytes(), &del)
	if del["deleted"] != true || del["model_id"] != "vendor/model" {
		t.Fatalf("delete response: %s", w.Body.String())
	}
}

func TestConsoleKeyLifecycle(t *testing.T) {
	app, _ := newTestApp(t)
	w := do(t, app, "POST", "/admin/keys/generate", `{"name":"smoke","ttl_seconds":3600}`)
	if w.Code != 200 {
		t.Fatalf("generate: %d %s", w.Code, w.Body.String())
	}
	var gen map[string]any
	decodeBody(t, w.Body.Bytes(), &gen)
	id, _ := gen["id"].(string)
	if id == "" || gen["key"] == "" {
		t.Fatalf("generate response: %s", w.Body.String())
	}

	w = do(t, app, "PATCH", "/admin/keys/"+id, `{"max_budget":5,"expires_at":4102444800}`)
	if w.Code != 200 {
		t.Fatalf("patch: %d %s", w.Code, w.Body.String())
	}
	var patched struct {
		Key map[string]any `json:"key"`
	}
	decodeBody(t, w.Body.Bytes(), &patched)
	if patched.Key["alias"] != "smoke" || patched.Key["max_budget"] != float64(5) {
		t.Fatalf("patch response: %s", w.Body.String())
	}
	if patched.Key["expires_at"] != float64(4102444800) {
		t.Fatalf("expires_at not applied: %v", patched.Key)
	}

	// An explicit null clears the field; an absent key leaves it alone.
	w = do(t, app, "PATCH", "/admin/keys/"+id, `{"max_budget":null,"expires_at":null}`)
	if w.Code != 200 {
		t.Fatalf("clear patch: %d %s", w.Code, w.Body.String())
	}
	decodeBody(t, w.Body.Bytes(), &patched)
	if patched.Key["max_budget"] != nil || patched.Key["expires_at"] != nil {
		t.Fatalf("null did not clear: %v", patched.Key)
	}
	if patched.Key["alias"] != "smoke" {
		t.Fatalf("absent field was cleared: %v", patched.Key)
	}

	w = do(t, app, "POST", "/admin/keys/"+id+"/disable", `{"disabled":true}`)
	var dis map[string]any
	decodeBody(t, w.Body.Bytes(), &dis)
	if dis["disabled"] != true || dis["key_id"] != id {
		t.Fatalf("disable response: %s", w.Body.String())
	}
	w = do(t, app, "POST", "/admin/keys/"+id+"/disable", `{"disabled":false}`)
	decodeBody(t, w.Body.Bytes(), &dis)
	if dis["disabled"] != false {
		t.Fatalf("re-enable response: %s", w.Body.String())
	}

	w = do(t, app, "DELETE", "/admin/keys/"+id, "")
	var del map[string]any
	decodeBody(t, w.Body.Bytes(), &del)
	if del["deleted"] != true {
		t.Fatalf("delete response: %s", w.Body.String())
	}
}

func TestConsoleBackupDocument(t *testing.T) {
	app, _ := newTestApp(t)
	w := do(t, app, "GET", "/admin/backup", "")
	if w.Code != 200 {
		t.Fatalf("backup: %d %s", w.Code, w.Body.String())
	}
	var doc map[string]any
	decodeBody(t, w.Body.Bytes(), &doc)
	if doc["kind"] != "wiwi.backup" {
		t.Fatalf("kind: %v", doc["kind"])
	}
	for _, k := range []string{"version", "sections", "counts", "providers", "keys", "deployments"} {
		if _, ok := doc[k]; !ok {
			t.Fatalf("backup missing %q: %v", k, doc)
		}
	}
	if _, ok := doc["exported_at"].(float64); !ok {
		t.Fatalf("exported_at is not a number: %T", doc["exported_at"])
	}

	// A dry run reports counts and writes nothing.
	body, _ := json.Marshal(doc)
	w = do(t, app, "POST", "/admin/backup/import?dry_run=1", string(body))
	if w.Code != 200 {
		t.Fatalf("dry run: %d %s", w.Code, w.Body.String())
	}
	var plan map[string]any
	decodeBody(t, w.Body.Bytes(), &plan)
	if plan["dry_run"] != true {
		t.Fatalf("dry run response: %s", w.Body.String())
	}
	if _, ok := plan["counts"].(map[string]any); !ok {
		t.Fatalf("dry run missing counts: %s", w.Body.String())
	}
}

func TestConsoleAliasBatch(t *testing.T) {
	app, _ := newTestApp(t)
	w := do(t, app, "POST", "/admin/aliases",
		`{"set":{"shortcut":{"target":"gpt-test","force_mapping":true}},"unset":[]}`)
	if w.Code != 200 {
		t.Fatalf("alias batch: %d %s", w.Code, w.Body.String())
	}
	var resp struct {
		Aliases map[string]any `json:"aliases"`
	}
	decodeBody(t, w.Body.Bytes(), &resp)
	if resp.Aliases["shortcut"] == nil {
		t.Fatalf("alias not applied: %s", w.Body.String())
	}

	// The strategy patch the Models page sends must take effect.
	w = do(t, app, "PATCH", "/admin/model-groups/gpt-test", `{"strategy":"least-busy"}`)
	if w.Code != 200 {
		t.Fatalf("group patch: %d %s", w.Code, w.Body.String())
	}
	if app.State.Router.Strategy() != "least-busy" {
		t.Fatalf("strategy not applied: %s", app.State.Router.Strategy())
	}
}

func TestConsoleSpaFallback(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "index.html"), []byte(`<div id="root"></div>`), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("WIWI_STATIC_DIR", dir)
	app, _ := newTestApp(t)
	// A client-side route must deep-link to the SPA shell.
	w := do(t, app, "GET", "/login", "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), `id="root"`) {
		t.Fatalf("SPA deep link: %d %.120s", w.Code, w.Body.String())
	}
	// A missing asset is a real 404, not the shell.
	w = do(t, app, "GET", "/assets/does-not-exist.js", "")
	if w.Code != 404 {
		t.Fatalf("missing asset must 404, got %d", w.Code)
	}
	// API paths never fall back to the shell.
	w = do(t, app, "GET", "/admin/nope", "")
	if w.Code != 404 || strings.Contains(w.Body.String(), `id="root"`) {
		t.Fatalf("unknown API path must 404 JSON, got %d %.120s", w.Code, w.Body.String())
	}
}

func TestConsoleAuthMeAnonymous(t *testing.T) {
	app, _ := newTestApp(t)
	w := httptest.NewRecorder()
	req, _ := http.NewRequest("GET", "/auth/me", nil)
	app.Router.ServeHTTP(w, req)
	if w.Code != 200 {
		t.Fatalf("anonymous /auth/me must be 200 like Python, got %d", w.Code)
	}
	if !strings.Contains(w.Body.String(), `"user":null`) {
		t.Fatalf("anonymous /auth/me body: %s", w.Body.String())
	}
}
