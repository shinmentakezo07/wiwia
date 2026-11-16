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

func newTestApp(t *testing.T) (*App, *httptest.Server) {
	t.Helper()
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		if stream, _ := body["stream"].(bool); stream {
			w.Header().Set("Content-Type", "text/event-stream")
			w.Write([]byte("data: {\"choices\":[{\"delta\":{\"content\":\"hel\"}}]}\n\n"))
			w.Write([]byte("data: {\"choices\":[{\"delta\":{\"content\":\"lo\"}}]}\n\n"))
			w.Write([]byte("data: {\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}],\"usage\":{\"prompt_tokens\":3,\"completion_tokens\":2}}\n\n"))
			w.Write([]byte("data: [DONE]\n\n"))
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"choices": []map[string]any{{
				"message":       map[string]any{"role": "assistant", "content": "hello"},
				"finish_reason": "stop",
			}},
			"usage": map[string]any{"prompt_tokens": 3, "completion_tokens": 2},
		})
	}))

	dbPath := filepath.Join(t.TempDir(), "wiwi.db")
	cfg := &config.Config{}
	cfg.General.MasterKey = "sk-test"
	cfg.General.DatabaseURL = "sqlite:///" + dbPath
	cfg.Providers = []config.Provider{{
		Name: "p", ProviderType: "openai", BaseURL: upstream.URL,
		Keys: []config.KeyDef{{Label: "k", Key: "secret", Weight: 1, Enabled: true}},
	}}
	cfg.ModelList = []config.ModelEntry{{
		ModelName: "gpt-test",
		Params:    config.DeploymentParams{Provider: "p", Model: "gpt-test", Weight: 1},
	}}
	app, err := New(cfg)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	t.Cleanup(func() { _ = app.State.Store.Close(); upstream.Close() })
	return app, upstream
}

func do(t *testing.T, app *App, method, path, body string) *httptest.ResponseRecorder {
	t.Helper()
	w := httptest.NewRecorder()
	req, _ := http.NewRequest(method, path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer sk-test")
	app.Router.ServeHTTP(w, req)
	return w
}

func TestHealthAndModels(t *testing.T) {
	app, _ := newTestApp(t)
	w := do(t, app, "GET", "/health", "")
	if w.Code != 200 {
		t.Fatalf("health %d", w.Code)
	}
	w = do(t, app, "GET", "/v1/models", "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), "gpt-test") {
		t.Fatalf("models %d %s", w.Code, w.Body.String())
	}
}

func TestChatNonStream(t *testing.T) {
	app, _ := newTestApp(t)
	w := do(t, app, "POST", "/v1/chat/completions", `{"model":"gpt-test","messages":[{"role":"user","content":"hi"}]}`)
	if w.Code != 200 {
		t.Fatalf("code %d body %s", w.Code, w.Body.String())
	}
	var out map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &out)
	choices := out["choices"].([]any)
	msg := choices[0].(map[string]any)["message"].(map[string]any)
	if msg["content"] != "hello" {
		t.Fatalf("unexpected content: %v", out)
	}
}

func TestChatStream(t *testing.T) {
	app, _ := newTestApp(t)
	w := do(t, app, "POST", "/v1/chat/completions", `{"model":"gpt-test","messages":[{"role":"user","content":"hi"}],"stream":true}`)
	if w.Code != 200 {
		t.Fatalf("code %d", w.Code)
	}
	body := w.Body.String()
	if !strings.Contains(body, "hel") || !strings.Contains(body, "[DONE]") {
		t.Fatalf("bad stream: %s", body)
	}
}

func TestMessagesSurface(t *testing.T) {
	app, _ := newTestApp(t)
	w := do(t, app, "POST", "/v1/messages", `{"model":"gpt-test","max_tokens":16,"messages":[{"role":"user","content":"hi"}]}`)
	if w.Code != 200 {
		t.Fatalf("code %d body %s", w.Code, w.Body.String())
	}
	var out map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &out)
	if out["type"] != "message" {
		t.Fatalf("unexpected: %v", out)
	}
}

func TestResponsesSurface(t *testing.T) {
	app, _ := newTestApp(t)
	w := do(t, app, "POST", "/v1/responses", `{"model":"gpt-test","input":"hi"}`)
	if w.Code != 200 {
		t.Fatalf("code %d body %s", w.Code, w.Body.String())
	}
	var out map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &out)
	if out["object"] != "response" {
		t.Fatalf("unexpected: %v", out)
	}
}

func TestCompletionsSurface(t *testing.T) {
	app, _ := newTestApp(t)
	w := do(t, app, "POST", "/v1/completions", `{"model":"gpt-test","prompt":"hi"}`)
	if w.Code != 200 {
		t.Fatalf("code %d body %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "hello") {
		t.Fatalf("unexpected: %s", w.Body.String())
	}
}

func TestAuthRequired(t *testing.T) {
	app, _ := newTestApp(t)
	w := httptest.NewRecorder()
	req, _ := http.NewRequest("POST", "/v1/chat/completions", strings.NewReader(`{"model":"gpt-test","messages":[]}`))
	req.Header.Set("Content-Type", "application/json")
	app.Router.ServeHTTP(w, req)
	if w.Code != 401 {
		t.Fatalf("expected 401, got %d", w.Code)
	}
}

func TestAdminKeys(t *testing.T) {
	app, _ := newTestApp(t)
	w := do(t, app, "POST", "/admin/keys/generate", `{"name":"ci","max_budget":5}`)
	if w.Code != 200 {
		t.Fatalf("generate %d %s", w.Code, w.Body.String())
	}
	var out map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &out)
	key, _ := out["key"].(string)
	if !strings.HasPrefix(key, "sk-wiwi-") {
		t.Fatalf("bad key: %v", out)
	}
	// the minted key authenticates on /v1
	w2 := httptest.NewRecorder()
	req, _ := http.NewRequest("GET", "/v1/models", nil)
	req.Header.Set("Authorization", "Bearer "+key)
	app.Router.ServeHTTP(w2, req)
	if w2.Code != 200 {
		t.Fatalf("vkey auth failed: %d %s", w2.Code, w2.Body.String())
	}
	w3 := do(t, app, "GET", "/admin/keys", "")
	if w3.Code != 200 || !strings.Contains(w3.Body.String(), "ci") {
		t.Fatalf("list keys: %d %s", w3.Code, w3.Body.String())
	}
}

func TestLogsAndStats(t *testing.T) {
	app, _ := newTestApp(t)
	do(t, app, "POST", "/v1/chat/completions", `{"model":"gpt-test","messages":[{"role":"user","content":"hi"}]}`)
	w := do(t, app, "GET", "/admin/logs/requests", "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), "gpt-test") {
		t.Fatalf("logs: %d %s", w.Code, w.Body.String())
	}
	w = do(t, app, "GET", "/admin/stats/overview", "")
	if w.Code != 200 {
		t.Fatalf("stats: %d", w.Code)
	}
}
