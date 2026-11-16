package server

import (
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestClineOAuthConnectAndStatus(t *testing.T) {
	app, _ := newTestApp(t)
	w := do(t, app, "GET", "/admin/cline/oauth/status", "")
	if !strings.Contains(w.Body.String(), `"connected":false`) {
		t.Fatalf("initial status: %s", w.Body.String())
	}
	payload := `{"accessToken":"acc","refreshToken":"ref","email":"me@example.com"}`
	code := base64.StdEncoding.EncodeToString([]byte(payload))
	w = do(t, app, "POST", "/admin/cline/oauth/connect", `{"code":"`+code+`"}`)
	if w.Code != 200 {
		t.Fatalf("connect: %d %s", w.Code, w.Body.String())
	}
	w = do(t, app, "GET", "/admin/cline/oauth/status", "")
	if !strings.Contains(w.Body.String(), `"connected":true`) || !strings.Contains(w.Body.String(), "me@example.com") {
		t.Fatalf("status after connect: %s", w.Body.String())
	}
	w = do(t, app, "DELETE", "/admin/cline/oauth/disconnect", "")
	if w.Code != 200 {
		t.Fatalf("disconnect: %d", w.Code)
	}
	w = do(t, app, "GET", "/admin/cline/oauth/status", "")
	if !strings.Contains(w.Body.String(), `"connected":false`) {
		t.Fatalf("status after disconnect: %s", w.Body.String())
	}
}

func TestClineSettings(t *testing.T) {
	app, _ := newTestApp(t)
	w := do(t, app, "PUT", "/admin/cline/settings", `{"default_models":["a","b"]}`)
	if w.Code != 200 {
		t.Fatalf("put settings: %d", w.Code)
	}
	w = do(t, app, "GET", "/admin/cline/settings", "")
	if !strings.Contains(w.Body.String(), `"a"`) {
		t.Fatalf("get settings: %s", w.Body.String())
	}
	w = do(t, app, "DELETE", "/admin/cline/settings/default-models/a", "")
	if w.Code != 200 {
		t.Fatalf("delete model: %d", w.Code)
	}
	w = do(t, app, "GET", "/admin/cline/settings", "")
	if strings.Contains(w.Body.String(), `"a"`) {
		t.Fatalf("model not removed: %s", w.Body.String())
	}
}

func TestProviderModels(t *testing.T) {
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{"data": []map[string]any{{"id": "m1"}, {"id": "m2"}}})
	}))
	defer up.Close()
	app, _ := newTestApp(t)
	// create a provider pointing at the model-listing mock
	w := do(t, app, "POST", "/admin/providers",
		`{"name":"mp","provider":"openai","base_url":"`+up.URL+`","keys":[{"label":"k","key":"x"}]}`)
	if w.Code != 200 {
		t.Fatalf("create: %d", w.Code)
	}
	w = do(t, app, "GET", "/admin/providers/mp/models", "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), "m1") {
		t.Fatalf("models: %d %s", w.Code, w.Body.String())
	}
}

func TestHermesDeviceCodeValidation(t *testing.T) {
	app, _ := newTestApp(t)
	w := do(t, app, "POST", "/admin/hermes/oauth/poll", `{}`)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
	}
	w = do(t, app, "GET", "/admin/hermes/oauth/status", "")
	if !strings.Contains(w.Body.String(), `"connected":false`) {
		t.Fatalf("hermes status: %s", w.Body.String())
	}
}
