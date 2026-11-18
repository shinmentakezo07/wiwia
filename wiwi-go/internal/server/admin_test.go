package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"wiwi-go/internal/config"
)

func mockUpstream(t *testing.T) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"choices": []map[string]any{{"message": map[string]any{"content": "ok"}, "finish_reason": "stop"}},
			"usage":   map[string]any{"prompt_tokens": 1, "completion_tokens": 1},
		})
	}))
}

func TestAdminProviderCRUDAndOverlay(t *testing.T) {
	up := mockUpstream(t)
	defer up.Close()
	dbPath := filepath.Join(t.TempDir(), "overlay.db")

	cfg := &config.Config{}
	cfg.General.MasterKey = "sk-test"
	cfg.General.DatabaseURL = "sqlite:///" + dbPath
	app, err := New(cfg)
	if err != nil {
		t.Fatal(err)
	}

	w := do(t, app, "POST", "/admin/providers",
		`{"name":"p1","provider":"openai","base_url":"`+up.URL+`","keys":[{"label":"k","key":"secret"}]}`)
	if w.Code != 200 {
		t.Fatalf("create provider: %d %s", w.Code, w.Body.String())
	}
	w = do(t, app, "POST", "/admin/model-groups/gpt-admin/deployments", `{"provider":"p1","model":"m"}`)
	if w.Code != 200 {
		t.Fatalf("add deployment: %d %s", w.Code, w.Body.String())
	}
	w = do(t, app, "POST", "/v1/chat/completions", `{"model":"gpt-admin","messages":[{"role":"user","content":"hi"}]}`)
	if w.Code != 200 {
		t.Fatalf("chat after mutation: %d %s", w.Code, w.Body.String())
	}
	app.Close()

	// A fresh process with an empty YAML must load the DB overlay.
	cfg2 := &config.Config{}
	cfg2.General.MasterKey = "sk-test"
	cfg2.General.DatabaseURL = "sqlite:///" + dbPath
	app2, err := New(cfg2)
	if err != nil {
		t.Fatal(err)
	}
	defer app2.Close()
	w = do(t, app2, "POST", "/v1/chat/completions", `{"model":"gpt-admin","messages":[{"role":"user","content":"hi"}]}`)
	if w.Code != 200 {
		t.Fatalf("overlay chat: %d %s", w.Code, w.Body.String())
	}
	w = do(t, app2, "GET", "/admin/providers", "")
	if !strings.Contains(w.Body.String(), "p1") {
		t.Fatalf("overlay provider missing: %s", w.Body.String())
	}
}

func TestAlertRulesFire(t *testing.T) {
	app, _ := newTestApp(t)
	w := do(t, app, "PUT", "/admin/alert-rules", `[{"metric":"requests","threshold":0}]`)
	if w.Code != 200 {
		t.Fatalf("put rules: %d", w.Code)
	}
	do(t, app, "POST", "/v1/chat/completions", `{"model":"gpt-test","messages":[{"role":"user","content":"hi"}]}`)
	w = do(t, app, "GET", "/admin/logs/proxy?since=0", "")
	if !strings.Contains(w.Body.String(), "alert fired") {
		t.Fatalf("expected alert event, got %s", w.Body.String())
	}
}

func TestBackupImportPrices(t *testing.T) {
	app, _ := newTestApp(t)
	w := do(t, app, "POST", "/admin/backup/import", `{"prices":{"mymodel":{"Prompt":0.001,"Completion":0.002}}}`)
	if w.Code != 200 {
		t.Fatalf("import: %d %s", w.Code, w.Body.String())
	}
	w = do(t, app, "GET", "/admin/pricing", "")
	if !strings.Contains(w.Body.String(), "mymodel") {
		t.Fatalf("price missing: %s", w.Body.String())
	}
}
