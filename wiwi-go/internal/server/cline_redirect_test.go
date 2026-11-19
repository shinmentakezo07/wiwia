package server

import (
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
)

func clineCode() string {
	payload := `{"accessToken":"acc-token-123","refreshToken":"ref-token","email":"me@example.com"}`
	return base64.StdEncoding.EncodeToString([]byte(payload))
}

func postClineAutoConnect(t *testing.T, app *App, body string) map[string]any {
	t.Helper()
	w := do(t, app, "POST", "/admin/cline/oauth/auto-connect", body)
	if w.Code != http.StatusOK {
		t.Fatalf("auto-connect: %d %s", w.Code, w.Body.String())
	}
	var out map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func publicCallback(t *testing.T, app *App, code, state string) *httptest.ResponseRecorder {
	t.Helper()
	w := httptest.NewRecorder()
	req := httptest.NewRequest("GET",
		"/cline/oauth/callback?code="+url.QueryEscape(code)+"&state="+url.QueryEscape(state), nil)
	app.Router.ServeHTTP(w, req)
	return w
}

func TestClineRedirectAutoConnectAndCallback(t *testing.T) {
	app, _ := newTestApp(t)
	w := do(t, app, "POST", "/admin/providers",
		`{"name":"cline2","provider":"cline","base_url":"https://api.cline.bot/api/v1","keys":[{"label":"k","key":"x","weight":1,"enabled":true}]}`)
	if w.Code != http.StatusOK {
		t.Fatalf("create provider: %d %s", w.Code, w.Body.String())
	}

	out := postClineAutoConnect(t, app, `{"provider":"cline2","return_path":"/console/oauth"}`)
	state, _ := out["state"].(string)
	if state == "" || out["auth_url"] == "" {
		t.Fatalf("auto-connect response missing state/auth_url: %v", out)
	}

	w = publicCallback(t, app, clineCode(), state)
	if w.Code != http.StatusFound {
		t.Fatalf("callback: %d %s", w.Code, w.Body.String())
	}
	loc := w.Header().Get("Location")
	if !strings.HasPrefix(loc, "/console/oauth?") || !strings.Contains(loc, "cline_connected=1") {
		t.Fatalf("callback redirect = %q", loc)
	}

	w = do(t, app, "GET", "/admin/cline/oauth/status?provider=cline2", "")
	if !strings.Contains(w.Body.String(), `"connected":true`) {
		t.Fatalf("status after callback: %s", w.Body.String())
	}

	// The state token is single-use.
	if w := publicCallback(t, app, clineCode(), state); w.Code != http.StatusBadRequest {
		t.Fatalf("replayed state: %d, want 400", w.Code)
	}
}

func TestClineAutoConnectRejectsOpenRedirect(t *testing.T) {
	app, _ := newTestApp(t)
	w := do(t, app, "POST", "/admin/providers",
		`{"name":"cline2","provider":"cline","base_url":"https://api.cline.bot/api/v1","keys":[{"label":"k","key":"x","weight":1,"enabled":true}]}`)
	if w.Code != http.StatusOK {
		t.Fatalf("create provider: %d", w.Code)
	}
	out := postClineAutoConnect(t, app, `{"provider":"cline2","return_path":"https://evil.example/"}`)
	state, _ := out["state"].(string)
	w = publicCallback(t, app, clineCode(), state)
	loc := w.Header().Get("Location")
	if !strings.HasPrefix(loc, "/console/oauth?") {
		t.Fatalf("return_path was not clamped: %q", loc)
	}
}

func TestClineAutoConnectRequiresKnownProvider(t *testing.T) {
	app, _ := newTestApp(t)
	w := do(t, app, "POST", "/admin/cline/oauth/auto-connect", `{"provider":"missing"}`)
	if w.Code != http.StatusNotFound {
		t.Fatalf("unknown provider: %d, want 404", w.Code)
	}
}
